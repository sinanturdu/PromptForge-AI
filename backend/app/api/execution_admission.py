"""Unwired, process-local admission reservations; no claims, tasks, DB or provider I/O."""

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from threading import Lock
from typing import Final, Literal
from uuid import UUID

from pydantic import ValidationError
from starlette.types import Scope

from app.api.workflow_security import execution_request_is_local
from app.core.config import Settings
from app.core.errors import AppError
from app.core.execution_environment import (
    ExecutionRuntimeQualification,
    PreparedLocalExecutionServer,
)
from app.llm.errors import LLMError
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT
from app.modules.prompts.workflow_api_schemas import WorkflowAPIModel, WorkflowStartRequest
from app.modules.prompts.workflow_read_contracts import WorkflowReadback

# Local deployment limits, not changes to the optimizer/gateway domain policy.
MAX_CONCURRENT_WORKFLOWS: Final = 1
MAX_WAITING_WORKFLOWS: Final = 0
MAX_CONCURRENT_PROVIDER_CALLS: Final = 1
MAX_AGENT_OPERATIONS: Final = 2 * MAX_EVALUATION_ROUNDS_HARD_LIMIT
MAX_ATTEMPTS_PER_OPERATION: Final = 4
MAX_ATTEMPTS_PER_WORKFLOW: Final = MAX_AGENT_OPERATIONS * MAX_ATTEMPTS_PER_OPERATION
EXECUTION_DEADLINE_SECONDS: Final = 300
MAX_GATEWAY_DEADLINE_SECONDS: Final = 90
MAX_REQUEST_TIMEOUT_SECONDS: Final = 20
LEASE_SECONDS: Final = 120
SHUTDOWN_GRACE_SECONDS: Final = 30


class AdmissionRequest(WorkflowAPIModel):
    prompt_request_id: UUID
    workflow_run_id: UUID
    start: WorkflowStartRequest


class AdmissionOutcome(StrEnum):
    FEATURE_DISABLED = "feature_disabled"
    SECURITY_DENIED = "security_denied"
    ENVIRONMENT_UNVERIFIED = "environment_unverified"
    INVALID_REQUEST = "invalid_request"
    WORKFLOW_NOT_FOUND = "workflow_not_found"
    WORKFLOW_NOT_ELIGIBLE = "workflow_not_eligible"
    ALREADY_RUNNING = "already_running"
    ALREADY_COMPLETED = "already_completed"
    UNRESOLVED_OPERATION = "unresolved_operation"
    DUPLICATE_ADMISSION = "duplicate_admission"
    CAPACITY_EXHAUSTED = "capacity_exhausted"
    RUNTIME_NOT_QUALIFIED = "runtime_not_qualified"
    PERSISTENCE_UNAVAILABLE = "persistence_unavailable"
    SHUTTING_DOWN = "shutting_down"
    PERMITTED = "admission_permitted"


class AdmissionDecision(WorkflowAPIModel):
    outcome: AdmissionOutcome
    durable_claim: Literal[False] = False
    authorizes_provider_call: Literal[False] = False

    def to_error(self) -> AppError | None:
        if self.outcome == AdmissionOutcome.PERMITTED:
            return None
        code, status, detail = {
            AdmissionOutcome.FEATURE_DISABLED: (
                "workflow_execution_disabled",
                403,
                "Workflow execution is disabled.",
            ),
            AdmissionOutcome.SECURITY_DENIED: (
                "workflow_access_denied",
                403,
                "Workflow execution requires a direct same-origin loopback JSON request.",
            ),
            AdmissionOutcome.ENVIRONMENT_UNVERIFIED: (
                "workflow_execution_environment_unsafe",
                503,
                "Workflow execution transport is not qualified.",
            ),
            AdmissionOutcome.INVALID_REQUEST: (
                "validation_error",
                422,
                "Execution request is invalid.",
            ),
            AdmissionOutcome.WORKFLOW_NOT_FOUND: ("workflow_not_found", 404, "Workflow not found."),
            AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE: (
                "workflow_conflict",
                409,
                "Workflow is not eligible for a new execution.",
            ),
            AdmissionOutcome.ALREADY_RUNNING: (
                "workflow_conflict",
                409,
                "Workflow is already active.",
            ),
            AdmissionOutcome.ALREADY_COMPLETED: (
                "workflow_conflict",
                409,
                "Workflow is already completed.",
            ),
            AdmissionOutcome.UNRESOLVED_OPERATION: (
                "workflow_conflict",
                409,
                "A prior operation requires investigation before any new execution.",
            ),
            AdmissionOutcome.DUPLICATE_ADMISSION: (
                "workflow_conflict",
                409,
                "Workflow already has a local admission reservation.",
            ),
            AdmissionOutcome.CAPACITY_EXHAUSTED: (
                "workflow_capacity_exceeded",
                429,
                "Local workflow capacity is exhausted.",
            ),
            AdmissionOutcome.RUNTIME_NOT_QUALIFIED: (
                "workflow_runtime_unavailable",
                503,
                "Persistent execution runtime is not qualified.",
            ),
            AdmissionOutcome.PERSISTENCE_UNAVAILABLE: (
                "workflow_unavailable",
                503,
                "Workflow inspection is temporarily unavailable.",
            ),
            AdmissionOutcome.SHUTTING_DOWN: (
                "workflow_unavailable",
                503,
                "Workflow admission is closed for shutdown.",
            ),
        }[self.outcome]
        return AppError(
            status_code=status,
            error_code=code,
            detail=detail,
            headers={"Retry-After": "2"}
            if self.outcome == AdmissionOutcome.CAPACITY_EXHAUSTED
            else None,
        )


@dataclass(frozen=True, eq=False, repr=False)
class AdmissionReservation:
    """Identity-checked local slot handle. Never a fencing token or an API response."""

    request: AdmissionRequest


@dataclass(frozen=True)
class AdmissionResult:
    decision: AdmissionDecision
    reservation: AdmissionReservation | None = field(default=None, repr=False)


class ExecutionAdmissionGate:
    """One instance per future application process; no waiting queue or task creation."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._process_id = os.getpid()
        self._closed = False
        self._reservation: AdmissionReservation | None = None

    def close_admission(self) -> None:
        with self._lock:
            self._closed = True

    def release(self, reservation: AdmissionReservation) -> bool:
        """Owner-only capacity release, not workflow completion or permission to replay."""
        with self._lock:
            if (
                self._process_id != os.getpid()
                or self._reservation is None
                or self._reservation is not reservation
            ):
                return False
            self._reservation = None
            return True

    def admit(
        self,
        request: object,
        *,
        settings: Settings,
        scope: Scope,
        workflow: WorkflowReadback | None,
        now: datetime,
        transport: PreparedLocalExecutionServer | None = None,
        runtime: ExecutionRuntimeQualification | None = None,
        persistence_available: bool = True,
    ) -> AdmissionResult:
        """Classify fresh trusted readback and atomically reserve at most one local slot."""
        with self._lock:
            if not settings.workflow_api_enabled or not settings.workflow_execution_enabled:
                return AdmissionResult(AdmissionDecision(outcome=AdmissionOutcome.FEATURE_DISABLED))
            if not execution_request_is_local(scope):
                return AdmissionResult(AdmissionDecision(outcome=AdmissionOutcome.SECURITY_DENIED))
            try:
                if not isinstance(request, (AdmissionRequest, dict)):
                    raise ValueError("Invalid admission input.")
                command = AdmissionRequest.model_validate(
                    request.model_dump() if isinstance(request, AdmissionRequest) else request
                )
            except (ValidationError, TypeError, ValueError):
                return AdmissionResult(AdmissionDecision(outcome=AdmissionOutcome.INVALID_REQUEST))
            outcome = self._evaluate(
                command, settings, scope, workflow, now, transport, runtime, persistence_available
            )
            if outcome != AdmissionOutcome.PERMITTED:
                return AdmissionResult(AdmissionDecision(outcome=outcome))
            if self._reservation is not None:
                outcome = (
                    AdmissionOutcome.DUPLICATE_ADMISSION
                    if (
                        self._reservation.request.workflow_run_id == command.workflow_run_id
                        and self._reservation.request.prompt_request_id == command.prompt_request_id
                    )
                    else AdmissionOutcome.CAPACITY_EXHAUSTED
                )
                return AdmissionResult(AdmissionDecision(outcome=outcome))
            self._reservation = AdmissionReservation(command)
            return AdmissionResult(AdmissionDecision(outcome=outcome), self._reservation)

    def _evaluate(
        self,
        command: AdmissionRequest,
        settings: Settings,
        scope: Scope,
        workflow: WorkflowReadback | None,
        now: datetime,
        transport: PreparedLocalExecutionServer | None,
        runtime: ExecutionRuntimeQualification | None,
        persistence_available: bool,
    ) -> AdmissionOutcome:
        if self._closed:
            return AdmissionOutcome.SHUTTING_DOWN
        if (
            self._process_id != os.getpid()
            or transport is None
            or scope.get("app") is not transport.application
            or not transport.is_qualified()
        ):
            return AdmissionOutcome.ENVIRONMENT_UNVERIFIED
        if runtime is None or not runtime.is_qualified():
            return AdmissionOutcome.RUNTIME_NOT_QUALIFIED
        if not persistence_available:
            return AdmissionOutcome.PERSISTENCE_UNAVAILABLE
        if workflow is None:
            return AdmissionOutcome.WORKFLOW_NOT_FOUND
        try:
            workflow = WorkflowReadback.model_validate(workflow.model_dump())
        except (ValidationError, ValueError, TypeError, LLMError):
            return AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
        row = workflow.workflow
        if row.id != command.workflow_run_id or row.prompt_request_id != command.prompt_request_id:
            return AdmissionOutcome.WORKFLOW_NOT_FOUND
        if workflow.findings or workflow.records_truncated or not workflow.history_valid:
            return AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
        if any(value.unresolved or value.incomplete for value in workflow.operations):
            return AdmissionOutcome.UNRESOLVED_OPERATION
        if row.status == "COMPLETED":
            return AdmissionOutcome.ALREADY_COMPLETED
        if not isinstance(now, datetime) or now.tzinfo is None:
            return AdmissionOutcome.INVALID_REQUEST
        if row.status in {"GENERATING", "EVALUATING", "REVISING"}:
            return (
                AdmissionOutcome.ALREADY_RUNNING
                if (
                    row.lease_expires_at is not None
                    and row.lease_expires_at.tzinfo is not None
                    and row.lease_expires_at > now
                )
                else AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
            )
        config = row.configuration
        if (
            row.status != "PENDING"
            or row.claimed
            or row.lease_expires_at is not None
            or row.current_round != 0
            or row.completed_rounds != 0
            or row.state_revision != 0
            or command.start.expected_revision != row.state_revision
            or workflow.operations
            or workflow.versions
            or workflow.evaluations
            or workflow.selected
            or config is None
            or config.max_evaluation_rounds != row.max_evaluation_rounds
            or not 1 <= row.max_evaluation_rounds <= MAX_EVALUATION_ROUNDS_HARD_LIMIT
            or config.timeout_seconds is None
            or config.timeout_seconds > EXECUTION_DEADLINE_SECONDS
            or settings.llm_request_timeout_seconds > MAX_REQUEST_TIMEOUT_SECONDS
            or settings.llm_call_deadline_seconds > MAX_GATEWAY_DEADLINE_SECONDS
        ):
            return AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
        return AdmissionOutcome.PERMITTED
