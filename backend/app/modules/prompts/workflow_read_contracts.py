"""Immutable internal read projections; diagnostics never contain execution tokens."""

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.llm.schemas import Identifier, LLMModel, TokenUsage
from app.modules.prompts.architect_contracts import ArchitectRequestSnapshot
from app.modules.prompts.optimization_contracts import EvaluationHistory
from app.modules.prompts.persistence_contracts import (
    CallMetadataStorage,
    Digest,
    OperationBindingStorage,
    OperationResultStorage,
    SafeErrorCode,
    TerminalDetailsStorage,
)
from app.modules.prompts.workflow_persistence_contracts import (
    OperationIdentity,
    OperationParameters,
    WorkflowConfiguration,
    WorkflowFence,
)
from app.scoring.schemas import GateResults

QualityStatus = Literal["qualified", "not_qualified", "unassessable"]


IntegrityCode = Literal[
    "source_binding",
    "history_size",
    "operation_provenance",
    "operation_state",
    "operation_telemetry",
    "version_lineage",
    "evaluation_provenance",
    "history_validation",
    "progress_selection",
    "workflow_state",
    "terminal_state",
]
IntegrityScope = Literal[
    "workflow", "source", "operation", "version", "evaluation", "history", "terminal"
]


class IntegrityFinding(LLMModel):
    code: IntegrityCode
    scope: IntegrityScope
    record_id: UUID | None = None


class ReadTelemetry(LLMModel):
    provider: Identifier | None = Field(repr=False)
    requested_model: Identifier | None = Field(repr=False)
    resolved_model: Identifier | None = Field(repr=False)
    usage: TokenUsage | None
    attempts: int
    retries: int
    repair_attempted: bool
    duration_ms: float
    usage_complete: bool


class WorkflowSummary(LLMModel):
    id: UUID
    prompt_request_id: UUID
    status: str
    state_revision: int
    current_round: int
    completed_rounds: int
    max_evaluation_rounds: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    lease_expires_at: datetime | None
    claimed: bool
    source_digest: Digest | None = Field(repr=False)
    configuration: WorkflowConfiguration | None = Field(repr=False)
    stored_best_version_id: UUID | None
    stored_final_score: Decimal | None
    stored_qualified: bool | None
    termination_reason: str | None
    error_code: SafeErrorCode | None
    clarification_question_count: int


class OperationSummary(LLMModel):
    id: UUID
    workflow_run_id: UUID
    operation: str
    evaluation_round: int | None
    status: str
    agent_name: str = Field(repr=False)
    agent_version: str = Field(repr=False)
    instruction_version: str = Field(repr=False)
    configured_provider: str | None = Field(repr=False)
    configured_model: str | None = Field(repr=False)
    started_at: datetime
    finished_at: datetime | None
    result_kind: str | None
    baseline_evaluation_id: UUID | None
    telemetry: ReadTelemetry | None
    error_code: SafeErrorCode | None
    incomplete: bool
    unresolved: bool
    staged_unevaluated_candidate: bool


class VersionSummary(LLMModel):
    id: UUID
    prompt_request_id: UUID
    workflow_run_id: UUID | None
    agent_run_id: UUID | None
    parent_version_id: UUID | None
    version_number: int
    evaluation_round: int | None
    source: str
    target_mode: str
    renderer_name: str
    renderer_version: str
    created_at: datetime


class EvaluationSummary(LLMModel):
    id: UUID
    prompt_version_id: UUID
    critic_agent_run_id: UUID
    evaluation_round: int
    completed_at: datetime
    rubric_version: str
    scoring_version: str
    stored_total_score: Decimal | None
    quality_status: QualityStatus | None
    gates: GateResults | None


class SelectedEvaluation(LLMModel):
    evaluation_id: UUID
    prompt_version_id: UUID
    evaluation_round: int
    total_score: Decimal | None
    qualified: bool
    quality_status: QualityStatus
    computed_selection_policy_version: str


class WorkflowReadback(LLMModel):
    workflow: WorkflowSummary
    operations: tuple[OperationSummary, ...]
    versions: tuple[VersionSummary, ...]
    evaluations: tuple[EvaluationSummary, ...]
    selected: SelectedEvaluation | None
    history_valid: bool
    records_truncated: bool
    findings: tuple[IntegrityFinding, ...]


class OperationContent(LLMModel):
    agent_run_id: UUID
    result: OperationResultStorage = Field(repr=False)


class WorkflowContentReadback(LLMModel):
    summary: WorkflowReadback
    source: ArchitectRequestSnapshot | None = Field(repr=False)
    history: EvaluationHistory | None = Field(repr=False)
    operation_results: tuple[OperationContent, ...] = Field(repr=False)
    terminal: TerminalDetailsStorage | None = Field(repr=False)


class WorkflowCursor(LLMModel):
    created_at: datetime
    workflow_run_id: UUID

    @field_validator("created_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Workflow cursors require an aware timestamp.")
        return value


class WorkflowListEntry(LLMModel):
    id: UUID
    prompt_request_id: UUID
    status: str
    created_at: datetime
    finished_at: datetime | None
    current_round: int
    completed_rounds: int
    integrity_checked: Literal[False] = False


class WorkflowPage(LLMModel):
    items: tuple[WorkflowListEntry, ...]
    next_cursor: WorkflowCursor | None


class OperationExpectation(LLMModel):
    fence: WorkflowFence = Field(repr=False)
    agent_run_id: UUID
    identity: OperationIdentity = Field(repr=False)
    binding: OperationBindingStorage = Field(repr=False)
    parameters: OperationParameters = Field(repr=False)
    result: OperationResultStorage | None = Field(default=None, repr=False)
    metadata: CallMetadataStorage | None = Field(default=None, repr=False)


class PersistenceReconciliation(LLMModel):
    status: Literal[
        "exact_committed",
        "committed_identity_only",
        "reserved_unresolved",
        "absent",
        "conflict",
        "integrity_inconsistent",
    ]
    workflow_run_id: UUID
    agent_run_id: UUID | None
    evaluation_id: UUID | None
    prompt_version_id: UUID | None
    completed_rounds: int | None
    progress_advanced: bool | None
    selected: SelectedEvaluation | None
    terminal_status: str | None
    caller_acknowledgment: Literal["unknown"] = "unknown"
    authorizes_mutation: Literal[False] = False


class CommittedProof(LLMModel):
    kind: Literal["operation_result", "evaluation", "terminal"]
    record_id: UUID
    prompt_version_id: UUID | None = None


class RecoveryAssessment(LLMModel):
    workflow_run_id: UUID
    prompt_request_id: UUID
    observed_at: datetime
    persisted_status: str
    completed_rounds: int
    lease_expires_at: datetime | None
    lease_expired: bool | None
    conditions: tuple[
        Literal[
            "pending",
            "confirmed_completed",
            "active_with_valid_lease",
            "expired_lease",
            "unresolved_operation",
            "committed_results_available",
            "terminal_failed",
            "terminal_clarification",
            "integrity_inconsistency",
        ],
        ...,
    ]
    disposition: Literal[
        "no_action_required",
        "currently_active_do_not_interfere",
        "inspect_operation_outcome",
        "reconcile_committed_identity",
        "manual_review_required",
        "integrity_investigation_required",
    ]
    committed: tuple[CommittedProof, ...]
    unresolved_operation_ids: tuple[UUID, ...]
    selected: SelectedEvaluation | None
    findings: tuple[IntegrityFinding, ...]
    committed_result_reconciliation_available: bool
    caller_acknowledgment: Literal["unknown"] = "unknown"
    external_operation_outcome: Literal["unknown"] = "unknown"
    former_worker_stopped: Literal["unknown"] = "unknown"
    automatic_provider_replay_safe: Literal[False] = False
    authorizes_mutation: Literal[False] = False
