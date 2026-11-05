"""Small persistence commands/receipts, not another optimizer domain model."""

from contextlib import suppress
from datetime import datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.agents.revision_schemas import ProtectedSourceSpan
from app.llm.errors import LLMError
from app.llm.schemas import LLMModel, ModelSettings
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate
from app.modules.prompts.persistence_contracts import (
    CallMetadataStorage,
    Digest,
    OperationResultStorage,
    PersistenceModel,
    TerminalDetailsStorage,
)

Version = Annotated[str, Field(min_length=1, max_length=32)]
OperationKey = Annotated[str, Field(min_length=1, max_length=64)]


class PersistenceError(RuntimeError):
    """Fixed diagnostics; never attach a database exception or private payload."""

    def __init__(self, code: Literal["not_found", "conflict", "invalid", "unavailable"]) -> None:
        self.code = code
        super().__init__(
            {
                "not_found": "Requested persistence record was not found in its owning scope.",
                "conflict": "Persistence state or operation identity conflicts with this request.",
                "invalid": "Persistence input or stored provenance is invalid.",
                "unavailable": "Persistence operation could not be completed.",
            }[code]
        )


def validated[T: BaseModel](model: type[T], value: BaseModel) -> T:
    result = None
    with suppress(ValueError, TypeError, LLMError):
        if isinstance(value, model):
            result = model.model_validate(value.model_dump(warnings=False))
    if result is None:
        raise PersistenceError("invalid")
    return result


class WorkflowConfiguration(PersistenceModel):
    max_evaluation_rounds: int = Field(default=3, ge=1, le=3)
    timeout_seconds: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    protected_spans: tuple[ProtectedSourceSpan, ...] = Field(default=(), max_length=32, repr=False)
    optimizer_version: Literal["1.0.0"] = "1.0.0"
    progress_policy_version: Literal["1.0.0"] = "1.0.0"


class WorkflowFence(LLMModel):
    """Loaded ownership and expected state, never an authentication credential."""

    prompt_request_id: UUID
    workflow_run_id: UUID
    execution_token: UUID = Field(repr=False)
    state_revision: int = Field(ge=0)
    completed_rounds: int = Field(ge=0, le=3)


class WorkflowState(LLMModel):
    id: UUID
    prompt_request_id: UUID
    status: str
    state_revision: int
    current_round: int
    completed_rounds: int
    max_evaluation_rounds: int
    execution_token: UUID | None = Field(repr=False)
    lease_expires_at: datetime | None

    def fence(self) -> WorkflowFence:
        if self.execution_token is None:
            raise PersistenceError("conflict")
        return WorkflowFence(
            prompt_request_id=self.prompt_request_id,
            workflow_run_id=self.id,
            execution_token=self.execution_token,
            state_revision=self.state_revision,
            completed_rounds=self.completed_rounds,
        )


class WorkflowCreation(LLMModel):
    workflow: WorkflowState
    replayed: bool


class OperationIdentity(LLMModel):
    model_config = ConfigDict(revalidate_instances="always")
    operation_key: OperationKey = Field(repr=False)
    agent_name: Literal["PromptArchitectAgent", "PromptCriticAgent"]
    agent_version: Version
    instruction_version: Version
    provider: str | None = Field(default=None, max_length=64, repr=False)
    settings: ModelSettings | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def configured_model_fits_column(self) -> Self:
        if self.settings is not None and len(self.settings.model) > 128:
            raise ValueError("Configured model identifier exceeds the persistence limit.")
        return self

    @field_validator("operation_key", "agent_version", "instruction_version", "provider")
    @classmethod
    def nonblank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("Operation identifiers must be nonblank.")
        return value


class OperationParameters(PersistenceModel):
    """Requested settings and blind Critic candidate identity; no provider response."""

    settings: ModelSettings | None = Field(default=None, repr=False)
    candidate_agent_run_id: UUID | None = None
    input_fingerprint: Digest | None = Field(default=None, repr=False)


class OperationState(LLMModel):
    id: UUID
    workflow_run_id: UUID
    operation: Literal["create", "revise", "evaluate"]
    status: str
    evaluation_round: int
    result: OperationResultStorage | None = Field(repr=False)
    metadata: CallMetadataStorage | None = Field(repr=False)


class OperationReceipt(LLMModel):
    workflow: WorkflowState
    agent: OperationState
    replayed: bool


class EvaluationCompletion(LLMModel):
    """One exact evaluated candidate; identity is the already reserved Critic operation."""

    fence: WorkflowFence = Field(repr=False)
    architect_agent_run_id: UUID
    critic_agent_run_id: UUID
    critic_operation_key: OperationKey = Field(repr=False)
    evaluation_round: int = Field(ge=1, le=3)
    evaluated: EvaluatedCandidate = Field(repr=False)


class EvaluationReceipt(LLMModel):
    workflow: WorkflowState
    prompt_version_id: UUID
    evaluation_id: UUID
    evaluation_round: int
    best_version_id: UUID
    best_evaluation_id: UUID
    quality_status: Literal["qualified", "not_qualified", "unassessable"]
    replayed: bool


class WorkflowFinalization(LLMModel):
    fence: WorkflowFence = Field(repr=False)
    details: TerminalDetailsStorage = Field(repr=False)
    agent_run_id: UUID | None = None


class FinalizationReceipt(LLMModel):
    workflow: WorkflowState
    best_version_id: UUID | None
    best_evaluation_id: UUID | None
    quality_status: Literal["qualified", "not_qualified", "unassessable"] | None
    terminal: TerminalDetailsStorage = Field(repr=False)
    replayed: bool
