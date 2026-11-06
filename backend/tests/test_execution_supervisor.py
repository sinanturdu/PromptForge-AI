"""Real fenced coordinator and deterministic supervision; only fake providers/temp DBs."""

import asyncio
import os
import signal
import subprocess
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any, NoReturn
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.types import Scope

import app.main as application
from app.api.execution_admission import AdmissionOutcome, AdmissionRequest
from app.core.config import BACKEND_DIR, Settings, build_sqlite_url
from app.core.execution_environment import (
    PreparedLocalExecutionServer,
    prepare_local_execution_server,
    qualify_execution_runtime,
)
from app.core.execution_startup import (
    ApplicationExecutionOwner,
    ExecutionModels,
    prepare_execution_startup,
)
from app.core.execution_supervisor import (
    CANCELLATION_CLEANUP_SECONDS,
    ExecutionCompletion,
    ExecutionGuard,
    WorkflowExecutionSupervisor,
)
from app.db import write_transactions
from app.db.base import utc_now
from app.db.write_transactions import write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import LLMRequest, LLMResponse, ModelSettings
from app.models import PromptRequest
from app.modules.prompts.workflow_api_schemas import WorkflowStartRequest
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    EvaluationReceipt,
    PersistenceError,
    WorkflowConfiguration,
    WorkflowState,
)
from workflow_read_fixtures import PRIVATE, RAW, SECRET_ID, Environment, dump, script
from workflow_read_fixtures import db as db


class SupervisorSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKFLOW_API_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_EXECUTION_ENABLED", raising=False)


class ControlledFake(FakeLLMProvider):
    def __init__(
        self,
        values: list[LLMResponse | LLMErrorCode],
        block_at: int | None = None,
        suppress_cancel: bool = False,
    ) -> None:
        super().__init__(values)
        self.requests: list[LLMRequest] = []
        self.block_at, self.suppress_cancel = block_at, suppress_cancel
        self.entered, self.release, self.cancelled = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        self.in_flight = 0
        self.max_in_flight = 0
        self.check_dispatch: Callable[[], None] = lambda: None

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.check_dispatch()
        self.requests.append(request)
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if len(self.requests) == self.block_at:
                self.entered.set()
                while not self.release.is_set():
                    try:
                        await self.release.wait()
                    except asyncio.CancelledError:
                        self.cancelled.set()
                        if not self.suppress_cancel:
                            raise
            return await super().generate(request)
        finally:
            self.in_flight -= 1

    async def aclose(self) -> None:
        assert self.in_flight == 0
        await super().aclose()


@dataclass
class Harness:
    db: Environment
    transport: PreparedLocalExecutionServer
    scope: Scope
    supervisors: list[WorkflowExecutionSupervisor] = field(default_factory=list)
    providers: list[ControlledFake] = field(default_factory=list)
    open_writers: set[int] = field(default_factory=set)

    async def state(self, *, limit: int = 3, other: bool = False) -> WorkflowState:
        parent = self.db.source.id
        if other:
            async with write_transaction(self.db.writer) as session:
                source = PromptRequest(raw_prompt=RAW, user_context=PRIVATE)
                session.add(source)
                await session.flush()
                parent = source.id
        return (
            await self.db.persistence.create_workflow(
                parent,
                WorkflowConfiguration(
                    schema_version="1.0.0", max_evaluation_rounds=limit, timeout_seconds=300
                ),
            )
        ).workflow

    async def supervisor(
        self, provider: ControlledFake, **options: Any
    ) -> WorkflowExecutionSupervisor:
        settings = SupervisorSettings(
            database_url=build_sqlite_url(self.db.path),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
            llm_max_retries=0,
            llm_json_repair_attempts=0,
        )
        runtime = await qualify_execution_runtime()

        def no_transaction() -> None:
            assert not self.open_writers and self.db.read_connections == 0

        provider.check_dispatch = no_transaction
        supervisor = WorkflowExecutionSupervisor(
            settings=settings,
            transport=self.transport,
            runtime=runtime,
            readback=self.db.readback,
            persistence=self.db.persistence,
            provider=provider,
            architect_settings=ModelSettings(model="fake"),
            critic_settings=ModelSettings(model="fake"),
            utc_clock=self.db.clock,
            **options,
        )
        self.supervisors.append(supervisor)
        self.providers.append(provider)
        return supervisor

    async def submit(self, supervisor: WorkflowExecutionSupervisor, state: WorkflowState) -> Any:
        return await supervisor.submit(
            AdmissionRequest(
                prompt_request_id=state.prompt_request_id,
                workflow_run_id=state.id,
                start=WorkflowStartRequest(expected_revision=0),
            ),
            scope=self.scope,
        )


@pytest.fixture
async def harness(db: Environment) -> AsyncIterator[Harness]:
    app = FastAPI()
    with prepare_local_execution_server(app, port=0) as prepared:
        server, config = prepared.server, prepared.server.config
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[prepared.socket])
        port = prepared.socket.getsockname()[1]
        value = Harness(
            db,
            prepared,
            dict(
                type="http",
                app=app,
                method="POST",
                scheme="http",
                path="/internal",
                client=("127.0.0.1", 50000),
                headers=[
                    (b"host", f"127.0.0.1:{port}".encode()),
                    (b"content-type", b"application/json"),
                ],
            ),
        )

        @event.listens_for(db.writer.sync_engine, "checkout")
        def checkout(connection: Any, *_args: Any) -> None:
            value.open_writers.add(id(connection))

        @event.listens_for(db.writer.sync_engine, "checkin")
        def checkin(connection: Any, *_args: Any) -> None:
            value.open_writers.discard(id(connection))

        try:
            yield value
        finally:
            for provider in value.providers:
                provider.release.set()
            for supervisor in value.supervisors:
                assert (await supervisor.shutdown()).stopped
            for provider in value.providers:
                await provider.aclose()
            await server.shutdown(sockets=[prepared.socket])


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profiles", [["qualified"], ["bad", "critical", "qualified"], ["unassessable"]]
)
async def test_real_optimizer_and_exact_reservation_cleanup(
    harness: Harness, profiles: list[str]
) -> None:
    provider = ControlledFake(script(profiles))
    supervisor = await harness.supervisor(provider)
    state = await harness.state(limit=len(profiles))
    assert provider.calls == 0
    assert (await harness.submit(supervisor, state)).accepted
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "durable_terminal_recorded"
    summary = await harness.db.readback.read(state.id)
    assert summary.workflow.status == "COMPLETED" and not summary.findings
    assert summary.workflow.completed_rounds == len(profiles)
    assert len(provider.requests) == 2 * len(profiles) and provider.max_in_flight == 1
    before = dump(harness.db)
    duplicate = await harness.submit(supervisor, state)
    assert (
        not duplicate.accepted and duplicate.decision.outcome == AdmissionOutcome.ALREADY_COMPLETED
    )
    assert dump(harness.db) == before


@pytest.mark.anyio
async def test_concurrent_duplicates_and_capacity_never_dispatch_twice(harness: Harness) -> None:
    provider = ControlledFake(script(["qualified"]), block_at=1)
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    results = await asyncio.gather(*(harness.submit(supervisor, state) for _ in range(6)))
    assert sum(r.accepted for r in results) == 1
    await asyncio.wait_for(provider.entered.wait(), 5)
    assert len(provider.requests) == 1
    other = await harness.state(other=True)
    denied = await harness.submit(supervisor, other)
    assert not denied.accepted and denied.decision.outcome == AdmissionOutcome.CAPACITY_EXHAUSTED
    provider.release.set()
    await supervisor.wait_idle()
    assert len(provider.requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("action", ["claim_workflow", "renew_claim"])
async def test_failed_fenced_claim_or_renewal_prevents_provider_dispatch(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    provider = ControlledFake(script(["qualified"]))
    supervisor = await harness.supervisor(provider)
    state = await harness.state()

    async def conflict(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise PersistenceError("conflict")

    monkeypatch.setattr(harness.db.persistence, action, conflict)
    assert (await harness.submit(supervisor, state)).accepted
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "persistence_conflict"
    assert not provider.requests


@pytest.mark.anyio
async def test_provider_failure_is_durable_execution_failure(harness: Harness) -> None:
    provider = ControlledFake([LLMErrorCode.PROVIDER_UNAVAILABLE])
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    assert (await harness.submit(supervisor, state)).accepted
    await supervisor.wait_idle()
    summary = await harness.db.readback.read(state.id)
    assert summary.workflow.status == "FAILED" and summary.workflow.completed_rounds == 0
    before = len(provider.requests)
    assert not (await harness.submit(supervisor, state)).accepted
    assert len(provider.requests) == before


@pytest.mark.anyio
async def test_waiter_cancellation_does_not_cancel_execution(harness: Harness) -> None:
    provider = ControlledFake(script(["qualified"]), block_at=1)
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    await harness.submit(supervisor, state)
    await asyncio.wait_for(provider.entered.wait(), 5)
    waiter = asyncio.create_task(supervisor.wait_idle())
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not provider.cancelled.is_set()
    provider.release.set()
    await supervisor.wait_idle()
    assert (await harness.db.readback.read(state.id)).workflow.status == "COMPLETED"


@pytest.mark.anyio
async def test_lease_guard_notices_overrun_and_disallows_late_response(harness: Harness) -> None:
    guards: list[ExecutionGuard] = []
    monotonic = [0.0]

    def factory() -> ExecutionGuard:
        guard = ExecutionGuard(
            environment_valid=harness.transport.is_qualified,
            utc_clock=harness.db.clock,
            clock=lambda: monotonic[0],
        )
        guards.append(guard)
        return guard

    provider = ControlledFake(script(["qualified"]), block_at=1, suppress_cancel=True)
    supervisor = await harness.supervisor(provider, guard_factory=factory)
    state = await harness.state()
    await harness.submit(supervisor, state)
    await asyncio.wait_for(provider.entered.wait(), 5)
    harness.db.clock.now += timedelta(seconds=106)
    monotonic[0] = 106
    guards[0]._poll()  # Drive the same timer callback deterministically, with no sleep.
    await asyncio.wait_for(provider.cancelled.wait(), 5)
    assert (
        supervisor.phase == "draining"
    )  # Admission closes; the slot stays occupied until stopped.
    assert not (await harness.submit(supervisor, state)).accepted
    provider.release.set()
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "lease_stop_unknown"
    assert len(provider.requests) == 1
    summary = await harness.db.readback.read(state.id)
    assert summary.workflow.status == "GENERATING" and summary.operations[0].unresolved
    harness.db.clock.now += timedelta(seconds=15)
    assert not (await harness.submit(supervisor, state)).accepted


@pytest.mark.anyio
async def test_global_deadline_covers_claim_loading_and_cleanup(harness: Harness) -> None:
    guards: list[ExecutionGuard] = []
    elapsed = [0.0]

    def factory() -> ExecutionGuard:
        guard = ExecutionGuard(
            environment_valid=harness.transport.is_qualified,
            utc_clock=harness.db.clock,
            clock=lambda: elapsed[0],
        )
        guards.append(guard)
        return guard

    provider = ControlledFake(script(["qualified"]), block_at=1)
    supervisor = await harness.supervisor(provider, guard_factory=factory)
    state = await harness.state()
    await harness.submit(supervisor, state)
    await asyncio.wait_for(provider.entered.wait(), 5)
    elapsed[0] = 300
    guards[0]._poll()
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "deadline_stop_unknown"
    assert (await harness.db.readback.read(state.id)).workflow.completed_rounds == 0


@pytest.mark.anyio
async def test_graceful_last_call_can_complete_and_shutdown_is_idempotent(harness: Harness) -> None:
    provider = ControlledFake(script(["qualified"]), block_at=2)
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    await harness.submit(supervisor, state)
    await asyncio.wait_for(provider.entered.wait(), 5)
    supervisor.begin_shutdown()
    assert not (await harness.submit(supervisor, state)).accepted
    provider.release.set()
    first, second = await asyncio.gather(supervisor.shutdown(), supervisor.shutdown())
    assert first == second and first.stopped and supervisor.phase == "stopped"
    assert (await harness.db.readback.read(state.id)).workflow.status == "COMPLETED"


@pytest.mark.anyio
async def test_shutdown_timeout_cancels_and_preserves_unknown_operation(harness: Harness) -> None:
    timeouts: list[float] = []

    async def waiter(task: asyncio.Task[ExecutionCompletion], seconds: float) -> bool:
        timeouts.append(seconds)
        if seconds == 30:
            return False
        await asyncio.wait({task}, timeout=5)
        return task.done()

    provider = ControlledFake(script(["qualified"]), block_at=1)
    supervisor = await harness.supervisor(provider, waiter=waiter)
    state = await harness.state()
    await harness.submit(supervisor, state)
    await asyncio.wait_for(provider.entered.wait(), 5)
    assert (await supervisor.shutdown()).stopped
    assert timeouts == [30, CANCELLATION_CLEANUP_SECONDS]
    summary = await harness.db.readback.read(state.id)
    assert summary.operations[0].unresolved and summary.workflow.status == "GENERATING"
    assert (
        supervisor.last_completion is not None
        and supervisor.last_completion.code == "cancelled_unknown"
    )


@pytest.mark.anyio
async def test_uncooperative_cancel_retains_resources_until_task_stops(harness: Harness) -> None:
    async def immediate(_task: asyncio.Task[ExecutionCompletion], _seconds: float) -> bool:
        return False

    provider = ControlledFake(script(["qualified"]), block_at=1, suppress_cancel=True)
    supervisor = await harness.supervisor(provider, waiter=immediate)
    state = await harness.state()
    await harness.submit(supervisor, state)
    await asyncio.wait_for(provider.entered.wait(), 5)
    report = await supervisor.shutdown()
    assert not report.stopped and report.phase == "stuck"
    closed: list[str] = []
    resources = AsyncExitStack()
    resources.callback(closed.append, "closed")
    supervisor.retain_resources(resources)
    assert not closed and not (await harness.submit(supervisor, state)).accepted
    provider.release.set()
    await supervisor.wait_idle()
    assert (await supervisor.shutdown()).stopped and closed == ["closed"]


@pytest.mark.anyio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_persistence_failure_preserves_actual_evidence_without_replay(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    provider = ControlledFake(script(["qualified"]))
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    original = harness.db.persistence.complete_evaluation

    async def unavailable(command: EvaluationCompletion) -> EvaluationReceipt:
        if after_commit:
            await original(command)
        raise PersistenceError("unavailable")

    monkeypatch.setattr(harness.db.persistence, "complete_evaluation", unavailable)
    await harness.submit(supervisor, state)
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "persistence_unavailable"
    summary = await harness.db.readback.read(state.id)
    assert len(summary.evaluations) == int(after_commit)
    assert len(provider.requests) == 2 and not (await harness.submit(supervisor, state)).accepted


@pytest.mark.anyio
async def test_exception_collection_and_factory_failure_release_slot(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    provider = ControlledFake(script(["qualified"]))

    def broken_guard() -> NoReturn:
        raise RuntimeError(SECRET_ID)

    supervisor = await harness.supervisor(provider, guard_factory=broken_guard)
    state = await harness.state()
    first = await harness.submit(supervisor, state)
    assert not first.accepted and not provider.requests
    assert SECRET_ID not in repr(first) and PRIVATE not in caplog.text
    supervisor._guard_factory = lambda: ExecutionGuard(
        environment_valid=harness.transport.is_qualified, utc_clock=harness.db.clock
    )

    async def broken(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise RuntimeError(SECRET_ID)

    monkeypatch.setattr(harness.db.persistence, "claim_workflow", broken)
    assert (await harness.submit(supervisor, state)).accepted
    outcome = await supervisor.wait_idle()
    assert outcome is not None and outcome.code == "unexpected_error"
    assert SECRET_ID not in repr(outcome) and PRIVATE not in caplog.text
    assert not provider.requests


@pytest.mark.parametrize("api,execution", [(False, False), (True, False), (True, True)])
def test_ordinary_startup_never_activates_supervisor_or_writer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api: bool, execution: bool
) -> None:
    provider = ControlledFake([])
    writer = Mock(side_effect=AssertionError("No ordinary execution writer"))
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    monkeypatch.setattr(write_transactions, "create_write_engine", writer)
    app = application.create_app(
        SupervisorSettings(
            database_url=build_sqlite_url(tmp_path / "absent.db"),
            workflow_api_enabled=api,
            workflow_execution_enabled=execution,
        )
    )
    with TestClient(app) as client:
        assert not hasattr(app.state, "workflow_execution_owner")
        assert client.get("/api/v1/health").status_code == 200
        paths = client.get("/api/v1/openapi.json").json()["paths"]
        assert all(set(value) == {"get"} for path, value in paths.items() if "runs" in path)
        assert provider.requests == []
    writer.assert_not_called()


@pytest.mark.anyio
async def test_real_startup_activation_and_disposal_order(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.clock.now = utc_now()
    state = (
        await db.persistence.create_workflow(
            db.source.id, WorkflowConfiguration(schema_version="1.0.0", timeout_seconds=300)
        )
    ).workflow
    provider = ControlledFake(script(["qualified"]), block_at=2)
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    app = application.create_app(
        SupervisorSettings(
            database_url=build_sqlite_url(db.path),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
            llm_max_retries=0,
            llm_json_repair_attempts=0,
        ),
        execution_models=ExecutionModels(
            architect=ModelSettings(model="fake"), critic=ModelSettings(model="fake")
        ),
    )
    order: list[str] = []
    original_dispose, original_close = AsyncEngine.dispose, provider.aclose
    writer: list[AsyncEngine] = []

    async def dispose(engine: AsyncEngine, close: bool = True) -> None:
        if engine in writer:
            assert provider.in_flight == 0
            order.append("writer")
        await original_dispose(engine, close=close)

    async def close_provider() -> None:
        assert provider.in_flight == 0
        order.append("provider")
        await original_close()

    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    monkeypatch.setattr(provider, "aclose", close_provider)
    with prepare_local_execution_server(app, port=0) as original:
        prepared = prepare_execution_startup(original)
        server, config = prepared.server, prepared.server.config
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[prepared.socket])
        try:
            owner: ApplicationExecutionOwner = app.state.workflow_execution_owner
            supervisor = owner.supervisor
            assert supervisor is not None and owner._writer is not None and provider.requests == []
            writer.append(owner._writer)
            port = prepared.socket.getsockname()[1]
            scope: Scope = dict(
                type="http",
                app=app,
                method="POST",
                scheme="http",
                path="/internal",
                client=("127.0.0.1", 50000),
                headers=[
                    (b"host", f"127.0.0.1:{port}".encode()),
                    (b"content-type", b"application/json"),
                ],
            )
            accepted = await supervisor.submit(
                AdmissionRequest(
                    prompt_request_id=state.prompt_request_id,
                    workflow_run_id=state.id,
                    start=WorkflowStartRequest(expected_revision=0),
                ),
                scope=scope,
            )
            assert accepted.accepted
            await asyncio.wait_for(provider.entered.wait(), 5)
            server.handle_exit(signal.SIGTERM, None)
            assert supervisor.phase == "draining"
            provider.release.set()
            await supervisor.wait_idle()
        finally:
            provider.release.set()
            await server.shutdown(sockets=[prepared.socket])
    assert order == ["writer", "provider"] and provider.closed
    assert (await db.readback.read(state.id)).workflow.status == "COMPLETED"


@pytest.mark.anyio
async def test_changed_fence_before_provider_attempt_prevents_dispatch(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ControlledFake(script(["qualified"]))
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    original = harness.db.persistence.execution_snapshot
    calls = 0

    async def changed(fence: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise PersistenceError("conflict")
        return await original(fence)

    monkeypatch.setattr(harness.db.persistence, "execution_snapshot", changed)
    await harness.submit(supervisor, state)
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "lease_stop_unknown"
    assert not provider.requests and supervisor.phase == "draining"
    assert (await harness.db.readback.read(state.id)).operations[0].unresolved


@pytest.mark.anyio
async def test_old_callback_cannot_release_or_clear_new_task(harness: Harness) -> None:
    provider = ControlledFake([*script(["qualified"]), *script(["qualified"])])
    supervisor = await harness.supervisor(provider)
    first = await harness.state()
    await harness.submit(supervisor, first)
    old_task, old_guard = supervisor._active, supervisor._guard
    reservation = supervisor._gate._reservation
    assert old_task is not None and old_guard is not None and reservation is not None
    await supervisor.wait_idle()
    provider.block_at = 3
    second = await harness.state()
    await harness.submit(supervisor, second)
    await asyncio.wait_for(provider.entered.wait(), 5)
    new_task = supervisor._active
    supervisor._finished(old_task, reservation, old_guard)
    assert supervisor._active is new_task and not supervisor._idle.is_set()
    assert not (await harness.submit(supervisor, second)).accepted
    provider.release.set()
    await supervisor.wait_idle()
    assert len(provider.requests) == 4


@pytest.mark.anyio
async def test_submission_racing_shutdown_never_reserves(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ControlledFake(script(["qualified"]))
    supervisor = await harness.supervisor(provider)
    state = await harness.state()
    entered, release = asyncio.Event(), asyncio.Event()
    original = harness.db.readback.read

    async def paused(*args: Any, **kwargs: Any) -> Any:
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result

    monkeypatch.setattr(harness.db.readback, "read", paused)
    submission = asyncio.create_task(harness.submit(supervisor, state))
    await asyncio.wait_for(entered.wait(), 5)
    await supervisor.shutdown()
    release.set()
    result = await submission
    assert not result.accepted and result.decision.outcome == AdmissionOutcome.SHUTTING_DOWN
    assert not provider.requests and supervisor._gate._reservation is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "option,value", [("workers", 2), ("reload", True), ("proxy_headers", True)]
)
async def test_explicit_startup_rejects_unsafe_server_configuration(
    db: Environment, monkeypatch: pytest.MonkeyPatch, option: str, value: object
) -> None:
    provider = ControlledFake([])
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    app = application.create_app(
        SupervisorSettings(
            database_url=build_sqlite_url(db.path),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
        ),
        execution_models=ExecutionModels(
            architect=ModelSettings(model="fake"), critic=ModelSettings(model="fake")
        ),
    )
    with prepare_local_execution_server(app, port=0) as original:
        prepared = prepare_execution_startup(original)
        server, config = prepared.server, prepared.server.config
        setattr(config, option, value)
        config.load()
        server.lifespan = config.lifespan_class(config)
        with pytest.raises(RuntimeError, match="listener handoff"):
            await server.startup(sockets=[prepared.socket])
    assert not provider.requests and provider.closed


@pytest.mark.anyio
async def test_full_lifespan_retains_real_resources_if_task_will_not_stop(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    db.clock.now = utc_now()
    state = (
        await db.persistence.create_workflow(
            db.source.id, WorkflowConfiguration(schema_version="1.0.0", timeout_seconds=300)
        )
    ).workflow
    provider = ControlledFake(script(["qualified"]), block_at=1, suppress_cancel=True)
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    app = application.create_app(
        SupervisorSettings(
            database_url=build_sqlite_url(db.path),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
            llm_max_retries=0,
            llm_json_repair_attempts=0,
        ),
        execution_models=ExecutionModels(
            architect=ModelSettings(model="fake"), critic=ModelSettings(model="fake")
        ),
    )

    async def timeout(_task: asyncio.Task[ExecutionCompletion], _seconds: float) -> bool:
        return False

    with prepare_local_execution_server(app, port=0) as original:
        prepared = prepare_execution_startup(original)
        server, config = prepared.server, prepared.server.config
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[prepared.socket])
        owner: ApplicationExecutionOwner = app.state.workflow_execution_owner
        supervisor = owner.supervisor
        assert supervisor is not None
        supervisor._waiter = timeout
        port = prepared.socket.getsockname()[1]
        scope: Scope = dict(
            type="http",
            app=app,
            method="POST",
            scheme="http",
            path="/internal",
            client=("127.0.0.1", 50000),
            headers=[
                (b"host", f"127.0.0.1:{port}".encode()),
                (b"content-type", b"application/json"),
            ],
        )
        await supervisor.submit(
            AdmissionRequest(
                prompt_request_id=state.prompt_request_id,
                workflow_run_id=state.id,
                start=WorkflowStartRequest(expected_revision=0),
            ),
            scope=scope,
        )
        await asyncio.wait_for(provider.entered.wait(), 5)
        await server.shutdown(sockets=[prepared.socket])
        try:
            assert supervisor.phase == "stuck" and not provider.closed
            assert supervisor._retained_resources is not None and provider.in_flight == 1
        finally:
            provider.release.set()
            await supervisor.wait_idle()
            assert await owner.shutdown()
    assert provider.closed


@pytest.mark.anyio
async def test_confirmed_between_operation_renewal_extends_guard_deadline(harness: Harness) -> None:
    guards: list[ExecutionGuard] = []
    elapsed = [0.0]

    def factory() -> ExecutionGuard:
        guard = ExecutionGuard(
            environment_valid=harness.transport.is_qualified,
            utc_clock=harness.db.clock,
            clock=lambda: elapsed[0],
        )
        guards.append(guard)
        return guard

    provider = ControlledFake(script(["qualified"]))
    supervisor = await harness.supervisor(provider, guard_factory=factory)
    assert_no_transaction = provider.check_dispatch
    observed: list[float | None] = []

    def clock_advance() -> None:
        assert_no_transaction()
        observed.append(guards[0]._lease_deadline)
        if len(observed) == 1:
            elapsed[0] = 30
            harness.db.clock.now += timedelta(seconds=30)

    provider.check_dispatch = clock_advance
    state = await harness.state()
    await harness.submit(supervisor, state)
    result = await supervisor.wait_idle()
    assert result is not None and result.code == "durable_terminal_recorded"
    assert observed == [105.0, 135.0]


def test_module_entrypoint_uses_canonical_ownership_types(tmp_path: Path) -> None:
    # Execute the actual -m path, replacing only the blocking server.run call.
    # The integration test above independently verifies real startup/handoff.
    source = """
import runpy
import sys
import app.core.execution_startup as startup

def inspect_run(server, sockets=None):
    assert type(server) is startup._ExecutionUvicornServer
    assert isinstance(server.config.app.state.execution_models, startup.ExecutionModels)
    assert sockets == [server.preparation.socket]
    print("CANONICAL_EXECUTION_STARTUP")

startup._ExecutionUvicornServer.run = inspect_run
sys.argv = ["app.core.execution_startup", "--database", sys.argv[1],
            "--architect-model", "fake", "--critic-model", "fake", "--port", "0"]
runpy.run_module("app.core.execution_startup", run_name="__main__")
"""
    environment = dict(
        os.environ,
        WORKFLOW_API_ENABLED="true",
        WORKFLOW_EXECUTION_ENABLED="true",
        LLM_PROVIDER="fake",
    )
    result = subprocess.run(
        [sys.executable, "-c", source, str(tmp_path / "not-opened.db")],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "CANONICAL_EXECUTION_STARTUP" in result.stdout
    assert not (tmp_path / "not-opened.db").exists()
