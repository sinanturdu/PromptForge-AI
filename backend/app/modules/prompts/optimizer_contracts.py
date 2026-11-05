"""Validated in-memory execution results; no persisted workflow status or records."""

import json
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

from app.agents.architect_schemas import ArchitectOutput
from app.llm.errors import LLMErrorCode
from app.llm.schemas import LLMModel
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.optimization_contracts import CandidateSelection, EvaluationHistory

OptimizationOperation = Literal[
    "input_validation",
    "architect_create",
    "architect_revise",
    "critic_evaluate",
    "validation",
    "selection",
    "progress",
]
TerminationReason = Literal[
    "quality_passed",
    "evaluation_limit",
    "no_actionable_feedback",
    "unchanged_baseline",
    "already_evaluated",
    "repeated_revision_attempt",
    "clarification_required",
    "provider_failure",
    "validation_failure",
    "deadline_exceeded",
]


def failure_reason(code: LLMErrorCode) -> TerminationReason:
    if code == LLMErrorCode.DEADLINE_EXCEEDED:
        return "deadline_exceeded"
    if code in {
        LLMErrorCode.INVALID_REQUEST,
        LLMErrorCode.INVALID_STRUCTURED_OUTPUT,
        LLMErrorCode.INVALID_RESPONSE,
    }:
        return "validation_failure"
    return "provider_failure"


class OptimizationCounts(LLMModel):
    """Started agent operations, not gateway attempts or completed evaluations."""

    model_config = ConfigDict(revalidate_instances="always")
    architect_create: int
    architect_revise: int
    critic_evaluate: int

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if not (
            0 <= self.architect_create <= 1
            and 0 <= self.architect_revise <= 2
            and 0 <= self.critic_evaluate <= MAX_EVALUATION_ROUNDS_HARD_LIMIT
        ):
            raise ValueError("Optimization dispatch counts exceed the hard bounds.")
        return self


class OptimizationOutcome(LLMModel):
    """Execution, selected quality and stopping reason are independent dimensions.

    Full dumps remain sensitive. A retained selection after failure is not approval.
    """

    model_config = ConfigDict(revalidate_instances="always")
    execution_status: Literal["completed", "clarification_required", "failed"]
    termination_reason: TerminationReason
    history: EvaluationHistory | None = Field(repr=False)
    selection: CandidateSelection | None = Field(repr=False)
    quality_status: Literal["qualified", "not_qualified", "unassessable"] | None
    clarification_questions: tuple[str, ...] = Field(repr=False)
    failed_operation: OptimizationOperation | None
    error_code: LLMErrorCode | None
    counts: OptimizationCounts
    max_evaluation_rounds: int
    optimizer_version: Literal["1.0.0"]
    progress_policy_version: Literal["1.0.0"]

    @field_validator("history", "selection", "counts", mode="wrap")
    @classmethod
    def snapshot_nested(
        cls, value: Any, _handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> Any:
        if value is None and info.field_name != "counts":
            return None
        models: dict[str, type[BaseModel]] = {
            "history": EvaluationHistory,
            "selection": CandidateSelection,
            "counts": OptimizationCounts,
        }
        model = models[str(info.field_name)]
        if isinstance(value, BaseModel):
            value = value.model_dump(
                mode="json" if info.mode == "json" else "python", warnings=False
            )
        return (
            model.model_validate_json(json.dumps(value))
            if info.mode == "json"
            else model.model_validate(value)
        )

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if not 1 <= self.max_evaluation_rounds <= MAX_EVALUATION_ROUNDS_HARD_LIMIT:
            raise ValueError("Optimization evaluation limit must be one through three.")
        completed = len(self.history.rounds) if self.history is not None else 0
        if not (
            completed
            <= self.counts.critic_evaluate
            <= min(completed + 1, self.max_evaluation_rounds)
            and max(0, completed - 1)
            <= self.counts.architect_revise
            <= min(completed, self.max_evaluation_rounds - 1)
            and self.counts.critic_evaluate
            <= self.counts.architect_create + self.counts.architect_revise
        ):
            raise ValueError("Started operations and completed history disagree.")
        if self.history is None:
            if (
                self.selection is not None
                or self.quality_status is not None
                or self.counts.architect_revise
            ):
                raise ValueError("No completed history means no selection, quality or revision.")
        else:
            if self.counts.architect_create != 1 or self.selection != select_best_candidate(
                self.history
            ):
                raise ValueError("Selection must reference the best existing completed evaluation.")
            selected = self.history.rounds[self.selection.round_number - 1]
            if self.quality_status != selected.evaluated.scoring.status:
                raise ValueError("Quality status must come from the selected evaluation.")
            if any(item.revision is None for item in self.history.rounds[1:]):
                raise ValueError(
                    "Orchestrated revisions require full available revision provenance."
                )
        failure_reasons = {"provider_failure", "validation_failure", "deadline_exceeded"}
        if self.execution_status == "failed":
            if (
                self.termination_reason not in failure_reasons
                or self.failed_operation is None
                or self.error_code is None
                or self.termination_reason != failure_reason(self.error_code)
            ):
                raise ValueError("Failed execution requires a safe failure category and operation.")
        elif (
            self.failed_operation is not None
            or self.error_code is not None
            or self.termination_reason in failure_reasons
        ):
            raise ValueError("Only failed execution carries failure diagnostics.")
        if self.execution_status == "clarification_required":
            if (
                self.termination_reason != "clarification_required"
                or self.counts.architect_create != 1
                or self.counts.critic_evaluate != completed
                or self.counts.architect_revise != completed
            ):
                raise ValueError("Clarification execution and reason must agree.")
            ArchitectOutput(
                decision="clarify",
                canonical_prompt=None,
                changes=(),
                questions=self.clarification_questions,
            )
        elif self.clarification_questions or self.termination_reason == "clarification_required":
            raise ValueError("Only clarification outcomes contain questions.")
        if self.execution_status == "completed":
            if self.history is None or self.counts.critic_evaluate != completed:
                raise ValueError("Completed execution requires completed evaluations only.")
            extra_revision = self.termination_reason in {"unchanged_baseline", "already_evaluated"}
            if self.counts.architect_revise != completed - 1 + int(extra_revision):
                raise ValueError("Termination reason and revision accounting disagree.")
            if (self.termination_reason == "quality_passed") != (
                self.quality_status == "qualified"
            ):
                raise ValueError("Quality stopping must use the actual qualification result.")
            if (
                self.termination_reason == "evaluation_limit"
                and completed != self.max_evaluation_rounds
            ):
                raise ValueError("Evaluation-limit termination requires the configured limit.")
        return self
