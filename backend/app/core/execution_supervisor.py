"""One owned execution task, no queue; persisted history remains the source of truth."""

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import AsyncExitStack
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Literal

from pydantic import ValidationError
from starlette.types import Scope

from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.api.execution_admission import (
    EXECUTION_DEADLINE_SECONDS,
    LEASE_SECONDS,
    MAX_ATTEMPTS_PER_WORKFLOW,
    SHUTDOWN_GRACE_SECONDS,
    AdmissionDecision,
    AdmissionOutcome,
    AdmissionRequest,
    AdmissionReservation,
    ExecutionAdmissionGate,
)
from app.api.workflow_security import execution_request_is_local
from app.core.config import Settings
from app.core.execution_environment import (
    ExecutionRuntimeQualification,
    PreparedLocalExecutionServer,
)
from app.db.base import utc_now
from app.llm.contracts import LLMProvider
from app.llm.gateway import LLMGateway
from app.llm.schemas import GatewayPolicy, LLMRequest, LLMResponse, ModelSettings
from app.modules.prompts.persistent_optimizer import (
    PersistentExecutionIdentity,
    execute_persisted_optimizer,
)
from app.modules.prompts.workflow_api_schemas import WorkflowAPIModel
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import PersistenceError, WorkflowState
from app.modules.prompts.workflow_readback import WorkflowReadbackService

LEASE_SAFETY_MARGIN_SECONDS = 15
CANCELLATION_CLEANUP_SECONDS = 10

CompletionCode = Literal[
    "durable_terminal_recorded",
    "persistence_conflict",
    "persistence_unavailable",
    "unexpected_error",
    "cancelled_unknown",
    "lease_stop_unknown",
    "deadline_stop_unknown",
    "environment_stop_unknown",
    "budget_stop_unknown",
]
Phase = Literal["accepting", "draining", "cancelling", "stuck", "stopped"]


class ExecutionCompletion(WorkflowAPIModel):
    """Operational observation, never a workflow status or an external-call verdict."""

    code: CompletionCode


class SubmissionResult(WorkflowAPIModel):
    accepted: bool
    decision: AdmissionDecision


class ShutdownResult(WorkflowAPIModel):
    stopped: bool
    phase: Phase


class ExecutionGuard:
    """Observe confirmed leases; a timer notices stalls without issuing any DB renewal."""

    def __init__(
        self,
        *,
        environment_valid: Callable[[], bool],
        utc_clock: Callable[[], datetime] = utc_now,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._clock = clock or self._loop.time
        self._utc_clock, self._environment_valid = utc_clock, environment_valid
        self._deadline = self._clock() + EXECUTION_DEADLINE_SECONDS
        self._lease_deadline: float | None = None
        self._state: WorkflowState | None = None
        self._task: asyncio.Task[ExecutionCompletion] | None = None
        self._timer: asyncio.TimerHandle | None = None
        self.reason: CompletionCode | None = None
        self.dispatch_closed = False
        self.attempts = 0
        self.provider_active = False
        self.persistence: WorkflowPersistenceService | None = None
        self.on_stop: Callable[[], None] = lambda: None

    def start(self, task: asyncio.Task[ExecutionCompletion]) -> None:
        self._task = task
        self._tick()

    def close(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._task = None

    def stop(self, reason: CompletionCode) -> None:
        if self.reason is None:
            self.reason = reason
            self.dispatch_closed = True
            self.on_stop()
            if self._timer is not None:
                self._timer.cancel()
            if self._task is not None and not self._task.done():
                self._task.cancel()

    def _poll(self) -> None:
        if self.reason is not None:
            return
        if self._clock() >= self._deadline:
            self.stop("deadline_stop_unknown")
        elif not self.dispatch_closed and not self._environment_valid():
            self.stop("environment_stop_unknown")
        elif self._state is not None and (
            self._state.lease_expires_at is None
            or (self._state.lease_expires_at - self._utc_clock()).total_seconds()
            <= LEASE_SAFETY_MARGIN_SECONDS
            or (self._lease_deadline is not None and self._clock() >= self._lease_deadline)
        ):
            self.stop("lease_stop_unknown")

    def _tick(self) -> None:
        self._poll()
        if self.reason is None:
            remaining = self._deadline - self._clock()
            if self._lease_deadline is not None:
                remaining = min(remaining, self._lease_deadline - self._clock())
            # Also notice forward wall-clock changes during an awaited operation.
            self._timer = self._loop.call_later(max(0, min(1.0, remaining)), self._tick)

    def require(self, *, provider: bool = False) -> None:
        self._poll()
        if provider and (self.dispatch_closed or self._state is None):
            self.stop("cancelled_unknown")
        if self.reason is not None:
            # BaseException cancellation bypasses gateway retry/failure conversion.
            raise asyncio.CancelledError("Supervised execution authority ended.")

    def observe(self, state: WorkflowState) -> None:
        self.require()

        previous = self._state
        if previous is not None and (
            previous.execution_token != state.execution_token
            or state.state_revision < previous.state_revision
            or state.id != previous.id
        ):
            self.stop("lease_stop_unknown")
        if state.execution_token is None or state.lease_expires_at is None:
            self.stop("lease_stop_unknown")
        else:
            remaining = min(
                LEASE_SECONDS, (state.lease_expires_at - self._utc_clock()).total_seconds()
            )
            deadline = self._clock() + remaining - LEASE_SAFETY_MARGIN_SECONDS
            self._lease_deadline = (
                min(self._lease_deadline, deadline)
                if (
                    previous is not None
                    and previous.lease_expires_at == state.lease_expires_at
                    and self._lease_deadline is not None
                )
                else deadline
            )
            self._state = state
        self.require()

    async def before_provider(self) -> None:
        """Freshly verify the exact existing fence before every actual attempt/retry."""
        self.require(provider=True)
        if self._state is None or self.persistence is None:
            self.stop("lease_stop_unknown")
        else:
            try:
                snapshot = await self.persistence.execution_snapshot(self._state.fence())
                if snapshot.workflow != self._state:
                    self.stop("lease_stop_unknown")
            except PersistenceError as error:
                self.stop(
                    "lease_stop_unknown" if error.code == "conflict" else "persistence_unavailable"
                )
            except Exception:
                self.stop("persistence_unavailable")
        self.require(provider=True)


_CURRENT_GUARD: ContextVar[ExecutionGuard | None] = ContextVar("execution_guard", default=None)


class SupervisedProvider:
    """Borrow the lifespan provider; enforce actual local starts without changing retries."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, request: LLMRequest) -> LLMResponse:
        guard = _CURRENT_GUARD.get()
        if guard is None:
            raise asyncio.CancelledError("Supervised dispatch requires claimed ownership.")
        await guard.before_provider()
        if guard.provider_active or guard.attempts >= MAX_ATTEMPTS_PER_WORKFLOW:
            guard.stop("budget_stop_unknown")
            guard.require()
        guard.attempts += 1
        guard.provider_active = True
        try:
            result = await self._provider.generate(request)
        finally:
            guard.provider_active = False
        guard.require()
        return result

    async def aclose(self) -> None:
        """The borrowed provider is closed by its application owner, never by this view."""


async def _wait_task(task: asyncio.Task[ExecutionCompletion], seconds: float) -> bool:
    done, _pending = await asyncio.wait({task}, timeout=seconds)
    return bool(done)


class WorkflowExecutionSupervisor:
    """Loop-owned task supervision, one reservation, zero queue, bounded diagnostics."""

    def __init__(
        self,
        *,
        settings: Settings,
        transport: PreparedLocalExecutionServer,
        runtime: ExecutionRuntimeQualification,
        readback: WorkflowReadbackService,
        persistence: WorkflowPersistenceService,
        provider: LLMProvider,
        architect_settings: ModelSettings,
        critic_settings: ModelSettings,
        utc_clock: Callable[[], datetime] = utc_now,
        guard_factory: Callable[[], ExecutionGuard] | None = None,
        waiter: Callable[[asyncio.Task[ExecutionCompletion], float], Awaitable[bool]] = _wait_task,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        self._settings, self._transport, self._runtime = settings, transport, runtime
        self._readback, self._persistence = readback, persistence
        gateway = LLMGateway(
            SupervisedProvider(provider),
            provider_name=settings.llm_provider,
            policy=GatewayPolicy(
                request_timeout_seconds=settings.llm_request_timeout_seconds,
                call_deadline_seconds=settings.llm_call_deadline_seconds,
                max_retries=settings.llm_max_retries,
                json_repair_attempts=settings.llm_json_repair_attempts,
            ),
        )
        self._architect = PromptArchitectAgent(gateway, architect_settings)
        self._critic = PromptCriticAgent(gateway, critic_settings)
        self._identity = PersistentExecutionIdentity(
            architect_provider=settings.llm_provider,
            architect_settings=architect_settings,
            critic_provider=settings.llm_provider,
            critic_settings=critic_settings,
        )
        self._clock, self._waiter = utc_clock, waiter
        self._guard_factory = guard_factory or (
            lambda: ExecutionGuard(
                environment_valid=lambda: transport.is_qualified() and runtime.is_qualified(),
                utc_clock=utc_clock,
            )
        )
        self._gate = ExecutionAdmissionGate()
        self.phase: Phase = "accepting"
        self._active: asyncio.Task[ExecutionCompletion] | None = None
        self._guard: ExecutionGuard | None = None
        self._shutdown_task: asyncio.Task[ShutdownResult] | None = None
        self._retained_resources: AsyncExitStack | None = None
        self._idle = asyncio.Event()
        self._idle.set()
        self.last_completion: ExecutionCompletion | None = None

    def check_environment(self, scope: Scope) -> AdmissionOutcome | None:
        """Request-time authority check, also required for HTTP workflow creation."""
        if asyncio.get_running_loop() is not self._loop:
            return AdmissionOutcome.ENVIRONMENT_UNVERIFIED
        if self.phase != "accepting":
            return AdmissionOutcome.SHUTTING_DOWN
        if not self._settings.workflow_api_enabled or not self._settings.workflow_execution_enabled:
            return AdmissionOutcome.FEATURE_DISABLED
        if not execution_request_is_local(scope):
            return AdmissionOutcome.SECURITY_DENIED
        if (
            scope.get("app") is not self._transport.application
            or not self._transport.is_qualified()
        ):
            return AdmissionOutcome.ENVIRONMENT_UNVERIFIED
        if not self._runtime.is_qualified():
            return AdmissionOutcome.RUNTIME_NOT_QUALIFIED
        return None

    async def submit(self, request: AdmissionRequest, *, scope: Scope) -> SubmissionResult:
        if asyncio.get_running_loop() is not self._loop:
            return self._rejected(AdmissionOutcome.ENVIRONMENT_UNVERIFIED)
        failure = self.check_environment(scope)
        if failure is not None:
            return self._rejected(failure)
        if not isinstance(request, AdmissionRequest):
            return self._rejected(AdmissionOutcome.INVALID_REQUEST)
        try:
            command = AdmissionRequest.model_validate(request.model_dump())
        except (ValueError, TypeError, ValidationError):
            return self._rejected(AdmissionOutcome.INVALID_REQUEST)
        try:
            summary = await self._readback.read(
                command.workflow_run_id, prompt_request_id=command.prompt_request_id
            )
        except PersistenceError as error:
            return self._rejected(
                AdmissionOutcome.WORKFLOW_NOT_FOUND
                if error.code == "not_found"
                else AdmissionOutcome.PERSISTENCE_UNAVAILABLE
            )
        except Exception:
            return self._rejected(AdmissionOutcome.PERSISTENCE_UNAVAILABLE)
        failure = self.check_environment(scope)
        if failure is not None:
            return self._rejected(failure)
        admitted = self._gate.admit(
            command,
            settings=self._settings,
            scope=scope,
            workflow=summary,
            now=self._clock(),
            transport=self._transport,
            runtime=self._runtime,
        )
        reservation = admitted.reservation
        if reservation is None:
            return SubmissionResult(accepted=False, decision=admitted.decision)
        # No await between the atomic reservation, strong task ownership and callback.
        operation: Coroutine[Any, Any, ExecutionCompletion] | None = None
        try:
            guard = self._guard_factory()
            guard.persistence = self._persistence
            guard.on_stop = self._close_admission
            operation = self._execute(reservation, guard)
            task = self._loop.create_task(operation, name="workflow-execution")
        except Exception:
            if operation is not None:
                operation.close()
            self._gate.release(reservation)
            return self._rejected(AdmissionOutcome.PERSISTENCE_UNAVAILABLE)
        self._active, self._guard = task, guard
        self._idle.clear()
        task.add_done_callback(lambda completed: self._finished(completed, reservation, guard))
        return SubmissionResult(accepted=True, decision=admitted.decision)

    @staticmethod
    def _rejected(outcome: AdmissionOutcome) -> SubmissionResult:
        return SubmissionResult(accepted=False, decision=AdmissionDecision(outcome=outcome))

    async def _execute(
        self, reservation: AdmissionReservation, guard: ExecutionGuard
    ) -> ExecutionCompletion:
        current = asyncio.current_task()
        assert current is not None
        token = _CURRENT_GUARD.set(guard)
        guard.start(current)
        try:
            if self.phase != "accepting":
                guard.stop("cancelled_unknown")
            guard.require()
            command = reservation.request
            await execute_persisted_optimizer(
                self._persistence,
                self._architect,
                self._critic,
                prompt_request_id=command.prompt_request_id,
                workflow_run_id=command.workflow_run_id,
                identity=self._identity,
                expected_revision=command.start.expected_revision,
                lease_seconds=LEASE_SECONDS,
                on_state=guard.observe,
            )
            guard.require()
            return ExecutionCompletion(code="durable_terminal_recorded")
        except asyncio.CancelledError:
            return ExecutionCompletion(code=guard.reason or "cancelled_unknown")
        except PersistenceError as error:
            return ExecutionCompletion(
                code="persistence_conflict"
                if error.code in {"conflict", "invalid", "not_found"}
                else "persistence_unavailable"
            )
        except Exception:
            return ExecutionCompletion(code="unexpected_error")
        finally:
            guard.close()
            _CURRENT_GUARD.reset(token)

    def _finished(
        self,
        task: asyncio.Task[ExecutionCompletion],
        reservation: AdmissionReservation,
        guard: ExecutionGuard,
    ) -> None:
        guard.close()
        try:
            completion = task.result()
        except asyncio.CancelledError:
            completion = ExecutionCompletion(code=guard.reason or "cancelled_unknown")
        except BaseException:
            completion = ExecutionCompletion(code="unexpected_error")
        self._gate.release(reservation)
        if self._active is task:
            self.last_completion = completion
            self._active, self._guard = None, None
            self._idle.set()

    async def wait_idle(self) -> ExecutionCompletion | None:
        """Caller cancellation never cancels the owned execution task."""
        await self._idle.wait()
        return self.last_completion

    def retain_resources(self, resources: AsyncExitStack) -> None:
        """Fail-closed ownership when an uncooperative task prevents safe disposal."""
        self._retained_resources = resources

    async def shutdown(self) -> ShutdownResult:
        self.begin_shutdown()
        assert self._shutdown_task is not None
        return await asyncio.shield(self._shutdown_task)

    def begin_shutdown(self) -> None:
        """Close dispatch synchronously, before the HTTP owner closes its listener."""
        self._close_admission()
        if self._shutdown_task is None or (self.phase == "stuck" and self._active is None):
            self._shutdown_task = self._loop.create_task(self._shutdown(), name="workflow-shutdown")
            self._shutdown_task.add_done_callback(self._collect_shutdown)

    def _close_admission(self) -> None:
        if self.phase == "accepting":
            self.phase = "draining"
            self._gate.close_admission()
            if self._guard is not None:
                self._guard.dispatch_closed = True

    @staticmethod
    def _collect_shutdown(task: asyncio.Task[ShutdownResult]) -> None:
        if not task.cancelled():
            task.exception()  # Always retrieve; callers still receive the exception on await.

    async def _shutdown(self) -> ShutdownResult:
        task = self._active
        if (
            task is not None
            and not task.done()
            and not await self._waiter(task, SHUTDOWN_GRACE_SECONDS)
        ):
            self.phase = "cancelling"
            if self._guard is not None:
                self._guard.stop("cancelled_unknown")
            if not await self._waiter(task, CANCELLATION_CLEANUP_SECONDS):
                self.phase = "stuck"
                return ShutdownResult(stopped=False, phase=self.phase)
        await self._idle.wait()
        if self._retained_resources is not None:
            await self._retained_resources.aclose()
            self._retained_resources = None
        self.phase = "stopped"
        return ShutdownResult(stopped=True, phase=self.phase)
