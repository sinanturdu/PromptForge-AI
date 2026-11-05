"""Versioned, explicit storage projections; no sessions, queries or optimizer hooks.

Full dumps remain private. Structural DTO validation never proves database ownership.
"""

import hashlib
import json
from contextlib import suppress
from datetime import UTC, datetime
from typing import Annotated, Any, Final, Literal, Self
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

from app.agents.architect_schemas import ArchitectOutput
from app.agents.critic_schemas import CriticOutput, CriticResult, resolve_unassessable
from app.agents.revision_schemas import ProtectedSourceSpan, RevisionOutput, RevisionResult
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import CallMetadata, LLMModel
from app.models.enums import (
    PromptDetailLevel,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
)
from app.modules.prompts.architect_contracts import ArchitectRequestSnapshot, CandidateReady
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimizer_contracts import (
    OptimizationOperation,
    OptimizationOutcome,
    TerminationReason,
    failure_reason,
)
from app.modules.prompts.rendering import GenericPromptRenderer
from app.modules.prompts.schemas import PromptRequestRead
from app.scoring.engine import ScoringEngine
from app.scoring.schemas import GateResults, ScoringResult

STORAGE_VERSION: Final = "1.0.0"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
SafeErrorCode = LLMErrorCode | Literal["interrupted", "persistence_failure", "internal_error"]


def _safe_snapshot[T: BaseModel](model: type[T], value: BaseModel) -> T:
    validated = None
    with suppress(ValidationError, LLMError):
        validated = model.model_validate(value.model_dump(warnings=False))
    if validated is None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return validated


class PersistenceModel(LLMModel):
    model_config = ConfigDict(revalidate_instances="always")
    schema_version: Literal["1.0.0"]

    @field_validator("*", mode="wrap")
    @classmethod
    def snapshot_fields(
        cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Any:
        if info.mode == "json":
            # Keep the full constrained field schema in strict JSON mode. Validator
            # hooks receive decoded values rather than native JSON tokens in 2.14.
            field = cls.model_fields[str(info.field_name)]
            return TypeAdapter(field.rebuild_annotation()).validate_json(
                json.dumps(value), strict=True
            )
        if isinstance(value, BaseModel):
            value = value.model_dump(warnings=False)
        return handler(value)

    @classmethod
    def from_json_value(cls, value: Any) -> Self:
        """Read a database JSON value in strict JSON mode, not coercive Python mode."""
        validated = None
        with suppress(ValueError, TypeError, LLMError):
            validated = cls.model_validate_json(json.dumps(value, ensure_ascii=False))
        if validated is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        return validated


class RequestPreferenceSnapshot(PersistenceModel):
    """Source reference/preferences only. Exact raw text and context stay in PromptRequest."""

    prompt_request_id: UUID
    source_created_at: datetime
    output_language: str | None = Field(repr=False, max_length=32)
    target_mode: TargetMode
    task_profile: str | None = Field(repr=False, max_length=64)
    prompt_detail_level: PromptDetailLevel
    response_detail_preference: ResponseDetailPreference
    reasoning_preference: ReasoningPreference

    @model_validator(mode="after")
    def validate_preferences(self) -> Self:
        if self.source_created_at.tzinfo is None or any(
            value is not None and not value.strip()
            for value in (self.output_language, self.task_profile)
        ):
            raise ValueError(
                "Preference snapshots require aware source time and nonblank metadata."
            )
        return self

    @classmethod
    def from_request(cls, value: PromptRequestRead) -> Self:
        source = _safe_snapshot(ArchitectRequestSnapshot, value)
        return cls(
            schema_version=STORAGE_VERSION,
            prompt_request_id=source.id,
            source_created_at=source.created_at,
            output_language=source.output_language,
            target_mode=source.target_mode,
            task_profile=source.task_profile,
            prompt_detail_level=source.prompt_detail_level,
            response_detail_preference=source.response_detail_preference,
            reasoning_preference=source.reasoning_preference,
        )


def request_content_digest(value: PromptRequestRead) -> str:
    """Hash exact source and preferences under v1; never normalize or log the digest."""
    source = _safe_snapshot(ArchitectRequestSnapshot, value)
    # Explicit allowlist prevents future DTO additions silently changing digest semantics.
    values = source.model_dump(
        mode="json",
        include={
            "id",
            "created_at",
            "raw_prompt",
            "user_context",
            "output_language",
            "target_mode",
            "task_profile",
            "prompt_detail_level",
            "response_detail_preference",
            "reasoning_preference",
        },
    )
    return hashlib.sha256(
        json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


class AssessmentStorage(PersistenceModel):
    assessment: CriticOutput = Field(repr=False)

    @model_validator(mode="after")
    def validate_resolved(self) -> Self:
        if resolve_unassessable(self.assessment) != self.assessment:
            raise ValueError("Stored assessments must retain resolved uncertainty.")
        return self


class GateStorage(PersistenceModel):
    gates: GateResults


class ScoringStorage(PersistenceModel):
    scoring: ScoringResult


class ObservedCallMetadata(CallMetadata):
    """Observation fields are required rather than synthesized from gateway defaults."""

    attempts: int = Field(ge=0, le=4)
    retries: int = Field(ge=0, le=2)
    repair_attempted: bool
    duration_ms: float = Field(ge=0, allow_inf_nan=False)
    usage_complete: bool

    @model_validator(mode="after")
    def validate_attempts(self) -> Self:
        if self.retries > max(0, self.attempts - 1) or (
            self.repair_attempted and self.attempts < 2
        ):
            raise ValueError("Observed retry/repair counts must agree with actual attempts.")
        return self


class CallMetadataStorage(PersistenceModel):
    """None means unknown. An absent observation never becomes a zero-attempt call."""

    metadata: ObservedCallMetadata | None = Field(repr=False)

    @classmethod
    def from_metadata(cls, value: CallMetadata | None) -> Self:
        return cls.model_validate(
            dict(
                schema_version=STORAGE_VERSION,
                metadata=None if value is None else value.model_dump(warnings=False),
            )
        )


class CandidateStorage(PersistenceModel):
    """Bounded accepted candidate content, without another copy of source/context."""

    prompt_request_id: UUID
    decision: Literal["preserve", "rewrite"]
    canonical_prompt: CanonicalPrompt = Field(repr=False)
    rendered_prompt: str = Field(repr=False)
    changes: tuple[str, ...] = Field(repr=False)
    target_mode: TargetMode
    renderer_name: Literal["GenericPromptRenderer"]
    renderer_version: Literal["1.0.0"]

    @model_validator(mode="after")
    def validate_rendering(self) -> Self:
        if self.rendered_prompt != GenericPromptRenderer().render(self.canonical_prompt):
            raise ValueError("Stored canonical and rendered prompt must agree exactly.")
        ArchitectOutput(
            decision=self.decision,
            canonical_prompt=self.canonical_prompt if self.decision == "rewrite" else None,
            changes=self.changes,
            questions=(),
        )
        return self

    @classmethod
    def from_candidate(cls, value: CandidateReady) -> Self:
        candidate = _safe_snapshot(CandidateReady, value)
        return cls.model_validate(
            dict(
                schema_version=STORAGE_VERSION,
                prompt_request_id=candidate.original_request.id,
                decision=candidate.decision,
                canonical_prompt=candidate.canonical_prompt,
                rendered_prompt=candidate.rendered_prompt,
                changes=candidate.changes,
                target_mode=candidate.original_request.target_mode,
                renderer_name=candidate.renderer_name,
                renderer_version=candidate.renderer_version,
            )
        )


class EvaluationStorage(PersistenceModel):
    """Validate arithmetic against existing Critic contracts; service still binds actual rows."""

    workflow_run_id: UUID
    prompt_version_id: UUID
    critic_agent_run_id: UUID
    evaluation_round: int = Field(ge=1, le=3)
    critic: CriticResult = Field(repr=False)
    scoring: ScoringResult
    completed_at: datetime

    @model_validator(mode="after")
    def validate_scoring(self) -> Self:
        if self.completed_at.tzinfo is None:
            raise ValueError("Evaluation completion needs an aware timestamp.")
        if ScoringEngine.score(self.critic) != self.scoring:
            raise ValueError("Stored scoring disagrees with the existing scoring engine.")
        return self

    @classmethod
    def from_evaluated(
        cls,
        value: EvaluatedCandidate,
        *,
        workflow_run_id: UUID,
        prompt_version_id: UUID,
        critic_agent_run_id: UUID,
        evaluation_round: int,
        completed_at: datetime,
    ) -> Self:
        evaluated = _safe_snapshot(EvaluatedCandidate, value)
        if evaluated.candidate.context.workflow_run_id not in (None, workflow_run_id):
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        return cls(
            schema_version=STORAGE_VERSION,
            workflow_run_id=workflow_run_id,
            prompt_version_id=prompt_version_id,
            critic_agent_run_id=critic_agent_run_id,
            evaluation_round=evaluation_round,
            critic=evaluated.critic,
            scoring=evaluated.scoring,
            completed_at=completed_at,
        )

    def record_values(self) -> dict[str, Any]:
        """Explicit row allowlist: no original request, history or provider response."""
        value = _safe_snapshot(EvaluationStorage, self)
        return dict(
            workflow_run_id=value.workflow_run_id,
            prompt_version_id=value.prompt_version_id,
            critic_agent_run_id=value.critic_agent_run_id,
            evaluation_round=value.evaluation_round,
            input_fingerprint=value.critic.input_fingerprint,
            evaluated_target_mode=value.critic.evaluated_target_mode.value,
            evaluated_renderer_name=value.critic.evaluated_renderer_name,
            evaluated_renderer_version=value.critic.evaluated_renderer_version,
            rubric_version=value.critic.rubric_version,
            scoring_version=value.scoring.scoring_version,
            assessment_schema_version=value.schema_version,
            assessment=AssessmentStorage(
                schema_version=STORAGE_VERSION, assessment=value.critic.output
            ).model_dump(mode="json"),
            quality_status=value.scoring.status,
            total_score=value.scoring.total_score,
            passed=value.scoring.passed,
            gate_results=GateStorage(
                schema_version=STORAGE_VERSION, gates=value.scoring.gates
            ).model_dump(mode="json"),
            completed_at=value.completed_at.astimezone(UTC),
        )


class OperationBindingStorage(PersistenceModel):
    operation: Literal["create", "revise", "evaluate"]
    evaluation_round: int = Field(ge=1, le=3)
    baseline_evaluation_id: UUID | None = None
    revision_attempt_key: Digest | None = Field(default=None, repr=False)
    feedback_projection_digest: Digest | None = Field(default=None, repr=False)
    protected_spans: tuple[ProtectedSourceSpan, ...] = Field(default=(), max_length=32, repr=False)
    eligible_issue_keys: tuple[str, ...] = Field(default=(), repr=False)

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        RevisionOutput.validate_keys(self.eligible_issue_keys)
        if self.operation == "revise":
            if (
                self.evaluation_round == 1
                or self.baseline_evaluation_id is None
                or self.revision_attempt_key is None
                or self.feedback_projection_digest is None
                or not self.eligible_issue_keys
            ):
                raise ValueError(
                    "Revision storage requires its actual baseline and feedback binding."
                )
        elif (
            self.baseline_evaluation_id is not None
            or self.revision_attempt_key is not None
            or self.feedback_projection_digest is not None
            or self.eligible_issue_keys
        ):
            raise ValueError("Only revisions carry baseline feedback bindings.")
        if self.operation == "create" and self.evaluation_round != 1:
            raise ValueError("Initial creation belongs to round one.")
        if len(set(self.protected_spans)) != len(self.protected_spans):
            raise ValueError("Stored protected spans must be unique.")
        return self


class OperationResultStorage(PersistenceModel):
    kind: Literal["candidate", "unchanged", "clarification", "evaluation", "failed"]
    candidate: CandidateStorage | None = Field(default=None, repr=False)
    questions: tuple[str, ...] = Field(default=(), repr=False)
    unchanged_reason: Literal["preserve", "identical_rewrite"] | None = None
    addressed_issue_keys: tuple[str, ...] = Field(default=(), repr=False)
    evaluation_id: UUID | None = None
    error_code: SafeErrorCode | None = None

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        RevisionOutput.validate_keys(self.addressed_issue_keys)
        if (
            (self.kind == "candidate") != (self.candidate is not None)
            or (self.kind == "unchanged") != (self.unchanged_reason is not None)
            or (self.kind == "evaluation") != (self.evaluation_id is not None)
            or (self.kind == "failed") != (self.error_code is not None)
        ):
            raise ValueError("Operation classification and stored payload disagree.")
        if self.kind == "clarification":
            ArchitectOutput(
                decision="clarify", canonical_prompt=None, changes=(), questions=self.questions
            )
        elif self.questions:
            raise ValueError("Only clarification operations contain questions.")
        if self.addressed_issue_keys and self.kind != "candidate":
            raise ValueError("Only candidates can claim addressed findings.")
        if (
            self.addressed_issue_keys
            and self.candidate is not None
            and self.candidate.decision == "preserve"
        ):
            raise ValueError("Preserved original candidates do not claim addressed findings.")
        return self

    @classmethod
    def from_revision(cls, value: RevisionResult) -> Self:
        from app.agents.revision_schemas import RevisedCandidate, RevisionClarification

        revision = _safe_snapshot(type(value), value)
        if isinstance(revision, RevisedCandidate):
            return cls(
                schema_version=STORAGE_VERSION,
                kind="candidate",
                candidate=CandidateStorage.from_candidate(revision.candidate),
                addressed_issue_keys=revision.addressed_issue_keys,
            )
        if isinstance(revision, RevisionClarification):
            return cls(
                schema_version=STORAGE_VERSION, kind="clarification", questions=revision.questions
            )
        return cls(
            schema_version=STORAGE_VERSION, kind="unchanged", unchanged_reason=revision.reason
        )


class TerminalDetailsStorage(PersistenceModel):
    execution_status: Literal["COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"]
    termination_reason: (
        TerminationReason | Literal["interrupted", "persistence_failure", "internal_error"]
    )
    questions: tuple[str, ...] = Field(default=(), repr=False)
    failed_operation: OptimizationOperation | None = None
    error_code: SafeErrorCode | None = None

    @model_validator(mode="after")
    def validate_terminal(self) -> Self:
        if self.execution_status == "FAILED":
            expected = (
                failure_reason(self.error_code)
                if isinstance(self.error_code, LLMErrorCode)
                else self.error_code
            )
            if self.error_code is None or self.termination_reason != expected:
                raise ValueError("Failed terminal storage requires a matching safe classification.")
        elif (
            self.error_code is not None
            or self.failed_operation is not None
            or self.termination_reason
            in {
                "provider_failure",
                "validation_failure",
                "deadline_exceeded",
                "interrupted",
                "persistence_failure",
                "internal_error",
            }
        ):
            raise ValueError("Only failed outcomes contain failure classification.")
        if self.execution_status == "CLARIFICATION_REQUIRED":
            if self.termination_reason != "clarification_required":
                raise ValueError("Clarification status and reason must agree.")
            ArchitectOutput(
                decision="clarify", canonical_prompt=None, changes=(), questions=self.questions
            )
        elif self.questions or self.termination_reason == "clarification_required":
            raise ValueError("Only clarification outcomes contain questions.")
        return self

    @classmethod
    def from_outcome(cls, value: OptimizationOutcome) -> Self:
        outcome = _safe_snapshot(OptimizationOutcome, value)
        return cls.model_validate(
            dict(
                schema_version=STORAGE_VERSION,
                execution_status=outcome.execution_status.upper(),
                termination_reason=outcome.termination_reason,
                questions=outcome.clarification_questions,
                failed_operation=outcome.failed_operation,
                error_code=outcome.error_code,
            )
        )
