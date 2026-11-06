"""REST contracts and explicit read projections; no I/O or dispatch.

Content responses are sensitive even though ordinary reprs omit their text. Never
log dumps or structured validation errors. Internal persistence DTOs are not API DTOs.
"""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.agents.critic_schemas import CriticOutput
from app.core.errors import ProblemDetails
from app.llm.schemas import TokenUsage
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT, AgentRunStatus, WorkflowRunStatus
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimizer_contracts import TerminationReason
from app.modules.prompts.persistence_contracts import SafeErrorCode
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_read_contracts import (
    EvaluationSummary,
    IntegrityFinding,
    OperationSummary,
    ReadTelemetry,
    RecoveryAssessment,
    SelectedEvaluation,
    VersionSummary,
    WorkflowContentReadback,
    WorkflowCursor,
    WorkflowPage,
    WorkflowReadback,
)

ClientIdempotencyKey = Annotated[
    str,
    StringConstraints(
        strict=True, min_length=1, max_length=256, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"
    ),
]
RoundCount = Annotated[int, Field(strict=True, ge=0, le=MAX_EVALUATION_ROUNDS_HARD_LIMIT)]
RoundLimit = Annotated[int, Field(strict=True, ge=1, le=MAX_EVALUATION_ROUNDS_HARD_LIMIT)]
TerminalReason = TerminationReason | Literal["interrupted", "persistence_failure", "internal_error"]


class WorkflowAPIModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)


class WorkflowCreateRequest(WorkflowAPIModel):
    """Scoped to an existing PromptRequest by the future route; no provider overrides."""

    client_idempotency_key: ClientIdempotencyKey = Field(repr=False)
    max_evaluation_rounds: RoundLimit = MAX_EVALUATION_ROUNDS_HARD_LIMIT


class WorkflowStartRequest(WorkflowAPIModel):
    """Expected public revision only; never accept an execution token or lease."""

    expected_revision: int = Field(strict=True, ge=0)


class WorkflowHistoryQuery(WorkflowAPIModel):
    limit: int = Field(default=20, ge=1, le=50)
    before_created_at: AwareDatetime | None = None
    before_workflow_run_id: UUID | None = None

    @model_validator(mode="after")
    def paired_cursor(self) -> Self:
        if (self.before_created_at is None) != (self.before_workflow_run_id is None):
            raise ValueError("Both workflow cursor fields must be supplied together.")
        return self

    def to_cursor(self) -> WorkflowCursor | None:
        if self.before_created_at is None or self.before_workflow_run_id is None:
            return None
        return WorkflowCursor(
            created_at=self.before_created_at, workflow_run_id=self.before_workflow_run_id
        )


class WorkflowIdentityResponse(WorkflowAPIModel):
    id: UUID
    prompt_request_id: UUID
    status: WorkflowRunStatus


class WorkflowCreateResponse(WorkflowAPIModel):
    workflow: WorkflowIdentityResponse
    replayed: bool


class WorkflowStartResponse(WorkflowAPIModel):
    """Admission acknowledgment, not a claim that any provider call completed."""

    workflow: WorkflowIdentityResponse
    accepted: Literal[True] = True


class WorkflowProgressResponse(WorkflowIdentityResponse):
    state_revision: int = Field(ge=0)
    current_round: RoundCount
    completed_rounds: RoundCount
    max_evaluation_rounds: RoundLimit
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    stored_best_version_id: UUID | None
    stored_final_score: Decimal | None
    stored_qualified: bool | None
    termination_reason: TerminalReason | None
    error_code: SafeErrorCode | None
    clarification_question_count: int = Field(ge=0)
    selected: SelectedEvaluation | None
    history_valid: bool
    records_truncated: bool
    findings: tuple[IntegrityFinding, ...]

    @classmethod
    def from_readback(cls, value: WorkflowReadback) -> Self:
        # Explicit field allowlist: configuration, content digests and claim details
        # must not leak when an internal projection gains fields in the future.
        row = value.workflow
        return cls.model_validate(
            dict(
                id=row.id,
                prompt_request_id=row.prompt_request_id,
                status=row.status,
                state_revision=row.state_revision,
                current_round=row.current_round,
                completed_rounds=row.completed_rounds,
                max_evaluation_rounds=row.max_evaluation_rounds,
                created_at=row.created_at,
                started_at=row.started_at,
                finished_at=row.finished_at,
                stored_best_version_id=row.stored_best_version_id,
                stored_final_score=row.stored_final_score,
                stored_qualified=row.stored_qualified,
                termination_reason=row.termination_reason,
                error_code=row.error_code,
                clarification_question_count=row.clarification_question_count,
                selected=value.selected
                if value.history_valid and not value.findings and not value.records_truncated
                else None,
                history_valid=value.history_valid,
                records_truncated=value.records_truncated,
                findings=value.findings,
            )
        )


class WorkflowListItemResponse(WorkflowIdentityResponse):
    created_at: datetime
    finished_at: datetime | None
    current_round: RoundCount
    completed_rounds: RoundCount
    integrity_checked: Literal[False] = False


class WorkflowHistoryPageResponse(WorkflowAPIModel):
    items: tuple[WorkflowListItemResponse, ...]
    next_cursor: WorkflowCursor | None

    @classmethod
    def from_readback(cls, value: WorkflowPage) -> Self:
        return cls(
            items=tuple(
                WorkflowListItemResponse(
                    id=item.id,
                    prompt_request_id=item.prompt_request_id,
                    status=WorkflowRunStatus(item.status),
                    created_at=item.created_at,
                    finished_at=item.finished_at,
                    current_round=item.current_round,
                    completed_rounds=item.completed_rounds,
                )
                for item in value.items
            ),
            next_cursor=value.next_cursor,
        )


class WorkflowTelemetryResponse(WorkflowAPIModel):
    """Observed counts only; omit provider/model/request identifiers entirely."""

    usage: TokenUsage | None
    attempts: int = Field(ge=0)
    retries: int = Field(ge=0)
    repair_attempted: bool
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    usage_complete: bool

    @classmethod
    def from_readback(cls, value: ReadTelemetry) -> Self:
        return cls(
            usage=value.usage,
            attempts=value.attempts,
            retries=value.retries,
            repair_attempted=value.repair_attempted,
            duration_ms=value.duration_ms,
            usage_complete=value.usage_complete,
        )


class WorkflowOperationResponse(WorkflowAPIModel):
    id: UUID
    workflow_run_id: UUID
    operation: Literal["create", "revise", "evaluate"]
    evaluation_round: int | None
    status: AgentRunStatus
    started_at: datetime
    finished_at: datetime | None
    result_kind: Literal["candidate", "clarification", "unchanged", "evaluation", "failed"] | None
    telemetry: WorkflowTelemetryResponse | None
    error_code: SafeErrorCode | None
    incomplete: bool
    unresolved: bool
    staged_unevaluated_candidate: bool

    @classmethod
    def from_readback(cls, value: OperationSummary) -> Self:
        return cls.model_validate(
            dict(
                id=value.id,
                workflow_run_id=value.workflow_run_id,
                operation=value.operation,
                evaluation_round=value.evaluation_round,
                status=value.status,
                started_at=value.started_at,
                finished_at=value.finished_at,
                result_kind=value.result_kind,
                telemetry=None
                if value.telemetry is None
                else WorkflowTelemetryResponse.from_readback(value.telemetry),
                error_code=value.error_code,
                incomplete=value.incomplete,
                unresolved=value.unresolved,
                staged_unevaluated_candidate=value.staged_unevaluated_candidate,
            )
        )


class WorkflowMetadataHistoryResponse(WorkflowAPIModel):
    workflow: WorkflowProgressResponse
    operations: tuple[WorkflowOperationResponse, ...]
    versions: tuple[VersionSummary, ...]
    evaluations: tuple[EvaluationSummary, ...]

    @classmethod
    def from_readback(cls, value: WorkflowReadback) -> Self:
        return cls(
            workflow=WorkflowProgressResponse.from_readback(value),
            operations=tuple(WorkflowOperationResponse.from_readback(v) for v in value.operations),
            versions=value.versions,
            evaluations=value.evaluations,
        )


class WorkflowVersionContentResponse(WorkflowAPIModel):
    version: VersionSummary
    canonical_prompt: CanonicalPrompt = Field(repr=False)
    rendered_prompt: str = Field(repr=False)
    change_summary: tuple[str, ...] = Field(repr=False)


class WorkflowEvaluationContentResponse(WorkflowAPIModel):
    evaluation_id: UUID
    assessment: CriticOutput = Field(repr=False)


class WorkflowContentHistoryResponse(WorkflowAPIModel):
    metadata: WorkflowMetadataHistoryResponse
    source: PromptRequestRead | None = Field(repr=False)
    versions: tuple[WorkflowVersionContentResponse, ...] = Field(repr=False)
    evaluations: tuple[WorkflowEvaluationContentResponse, ...] = Field(repr=False)
    clarification_questions: tuple[str, ...] = Field(repr=False)

    @classmethod
    def from_readback(cls, value: WorkflowContentReadback) -> Self:
        versions, evaluations = [], []
        # Only the existing validated history supplies candidate/assessment content.
        # Ordinary metadata still shows actual stored records with integrity findings.
        if value.history is not None:
            for version, evaluation, completed in zip(
                value.summary.versions, value.summary.evaluations, value.history.rounds, strict=True
            ):
                candidate = completed.evaluated.candidate
                versions.append(
                    WorkflowVersionContentResponse(
                        version=version,
                        canonical_prompt=candidate.canonical_prompt,
                        rendered_prompt=candidate.rendered_prompt,
                        change_summary=candidate.changes,
                    )
                )
                evaluations.append(
                    WorkflowEvaluationContentResponse(
                        evaluation_id=evaluation.id, assessment=completed.evaluated.critic.output
                    )
                )
        return cls(
            metadata=WorkflowMetadataHistoryResponse.from_readback(value.summary),
            source=None
            if value.source is None
            else PromptRequestRead.model_validate(value.source.model_dump()),
            versions=tuple(versions),
            evaluations=tuple(evaluations),
            clarification_questions=() if value.terminal is None else value.terminal.questions,
        )


class WorkflowResultResponse(WorkflowAPIModel):
    """Terminal execution may retain a candidate without being successful or qualified."""

    workflow: WorkflowProgressResponse
    candidate: WorkflowVersionContentResponse | None = Field(repr=False)
    evaluation: EvaluationSummary | None
    assessment: CriticOutput | None = Field(repr=False)
    clarification_questions: tuple[str, ...] = Field(repr=False)

    @classmethod
    def from_readback(cls, value: WorkflowContentReadback) -> Self:
        content = WorkflowContentHistoryResponse.from_readback(value)
        workflow = content.metadata.workflow
        if workflow.status not in {
            WorkflowRunStatus.COMPLETED,
            WorkflowRunStatus.FAILED,
            WorkflowRunStatus.CLARIFICATION_REQUIRED,
        }:
            raise ValueError("Workflow result requires a terminal execution.")
        selected = workflow.selected
        candidate = next(
            (
                v
                for v in content.versions
                if selected and v.version.id == selected.prompt_version_id
            ),
            None,
        )
        evaluation = next(
            (
                v
                for v in content.metadata.evaluations
                if selected and v.id == selected.evaluation_id
            ),
            None,
        )
        assessment = next(
            (
                v.assessment
                for v in content.evaluations
                if selected and v.evaluation_id == selected.evaluation_id
            ),
            None,
        )
        return cls(
            workflow=workflow,
            candidate=candidate,
            evaluation=evaluation,
            assessment=assessment,
            clarification_questions=content.clarification_questions,
        )


class WorkflowRecoveryResponse(WorkflowAPIModel):
    """Internal-development diagnostics only; this safe DTO grants no mutation authority."""

    assessment: RecoveryAssessment

    @classmethod
    def from_readback(cls, value: RecoveryAssessment) -> Self:
        return cls(
            assessment=value.model_copy(update={"selected": None}) if value.findings else value
        )


WorkflowAPIErrorCode = Literal[
    "workflow_execution_environment_unsafe",
    "workflow_access_denied",
    "validation_error",
    "prompt_request_not_found",
    "workflow_not_found",
    "workflow_api_disabled",
    "workflow_execution_disabled",
    "workflow_conflict",
    "workflow_not_terminal",
    "workflow_integrity_inconsistent",
    "workflow_capacity_exceeded",
    "workflow_runtime_unavailable",
    "workflow_unavailable",
    "internal_server_error",
]


class WorkflowProblemDetails(ProblemDetails):
    """Future error vocabulary; existing exception handlers remain unchanged."""

    error_code: WorkflowAPIErrorCode
