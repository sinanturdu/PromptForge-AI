"""Completed evaluation history and small selection/progress values; no execution."""

import json
import re
from contextlib import suppress
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

from app.agents.revision_schemas import RevisedCandidate, RevisionInput, RevisionResult
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import LLMModel
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT
from app.modules.prompts.architect_contracts import ArchitectRequestSnapshot, CandidateReady
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate


def _revalidate[T: BaseModel](model: type[T], value: Any, mode: str) -> T:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json" if mode == "json" else "python", warnings=False)
    return (
        model.model_validate_json(json.dumps(value))
        if mode == "json"
        else model.model_validate(value)
    )


def validate_revision_provenance(
    baseline: EvaluatedCandidate, revision: RevisionResult, next_round: int
) -> None:
    """Check available revision provenance without a Critic call or selection claim."""
    if revision.baseline != baseline.candidate or revision.next_evaluation_round != next_round:
        raise ValueError("Revision does not reference the declared completed baseline/round.")
    payload = RevisionInput(
        baseline=baseline.candidate,
        evaluation=baseline.critic,
        context=revision.context,
        next_evaluation_round=next_round,
        protected_spans=revision.protected_spans,
    )
    if revision.eligible_issue_keys != tuple(finding.issue_key for finding in payload.feedback):
        raise ValueError("Revision feedback keys do not belong to its baseline evaluation.")


class EvaluationRound(LLMModel):
    """One complete bound evaluation, never a dispatch, clarification or new version."""

    model_config = ConfigDict(revalidate_instances="always")
    round_number: int
    evaluated: EvaluatedCandidate = Field(repr=False)
    baseline_round_number: int | None = None
    revision: RevisedCandidate | None = Field(default=None, repr=False)

    @field_validator("evaluated", mode="wrap")
    @classmethod
    def validate_evaluated(
        cls, value: Any, _handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> EvaluatedCandidate:
        # Rebuild in the original validation mode, retaining the normal model serializer.
        return _revalidate(EvaluatedCandidate, value, info.mode)

    @field_validator("revision", mode="wrap")
    @classmethod
    def validate_revision(
        cls, value: Any, _handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> RevisedCandidate | None:
        return None if value is None else _revalidate(RevisedCandidate, value, info.mode)

    @model_validator(mode="after")
    def validate_round(self) -> Self:
        if not 1 <= self.round_number <= MAX_EVALUATION_ROUNDS_HARD_LIMIT:
            raise ValueError("Completed round number must be between one and three.")
        if self.round_number == 1:
            if self.baseline_round_number is not None or self.revision is not None:
                raise ValueError("The first evaluation cannot have revision ancestry.")
        elif (
            self.baseline_round_number is None
            or not 1 <= self.baseline_round_number < self.round_number
        ):
            raise ValueError("A revision must reference an earlier completed round.")
        elif self.evaluated.candidate.decision != "rewrite":
            raise ValueError("Later evaluations require changed rewrite candidates.")
        if self.revision is not None and (
            self.revision.next_evaluation_round != self.round_number
            or self.revision.candidate != self.evaluated.candidate
        ):
            raise ValueError("Revision and completed evaluation candidate disagree.")
        return self


class EvaluationHistory(LLMModel):
    """One to three immutable completed rounds under one pinned source and policies."""

    model_config = ConfigDict(revalidate_instances="always")
    rounds: tuple[EvaluationRound, ...] = Field(repr=False)

    @field_validator("rounds", mode="wrap")
    @classmethod
    def validate_rounds(
        cls, value: Any, _handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> tuple[EvaluationRound, ...]:
        expected_type = list if info.mode == "json" else tuple
        if (
            not isinstance(value, expected_type)
            or not 1 <= len(value) <= MAX_EVALUATION_ROUNDS_HARD_LIMIT
        ):
            raise ValueError("History requires one to three ordered completed rounds.")
        return tuple(_revalidate(EvaluationRound, item, info.mode) for item in value)

    @model_validator(mode="after")
    def validate_history(self) -> Self:
        if tuple(item.round_number for item in self.rounds) != tuple(
            range(1, len(self.rounds) + 1)
        ):
            raise ValueError("Completed round numbers must be consecutive, unique and ordered.")
        first = self.rounds[0].evaluated
        fingerprints: set[str] = set()
        agent_ids: set[object] = set()
        for item in self.rounds:
            evaluated = item.evaluated
            validate_candidate_identity(first.candidate, evaluated.candidate)
            if (
                evaluated.critic.rubric_version != first.critic.rubric_version
                or evaluated.scoring.scoring_version != first.scoring.scoring_version
                or evaluated.critic.agent_version != first.critic.agent_version
                or evaluated.critic.instruction_version != first.critic.instruction_version
            ):
                raise ValueError("History evaluation policies must remain pinned.")
            fingerprint = evaluated.critic.input_fingerprint
            if fingerprint in fingerprints:
                raise ValueError("The same exact candidate cannot be evaluated twice in history.")
            fingerprints.add(fingerprint)
            agent_id = evaluated.candidate.context.agent_run_id
            if agent_id is not None:
                if agent_id in agent_ids:
                    raise ValueError("Completed rounds cannot reuse an Architect AgentRun ID.")
                agent_ids.add(agent_id)
            if item.baseline_round_number is not None:
                baseline = self.rounds[item.baseline_round_number - 1].evaluated
                if item.revision is not None:
                    validate_revision_provenance(baseline, item.revision, item.round_number)
        return self

    @property
    def original_request(self) -> ArchitectRequestSnapshot:
        return self.rounds[0].evaluated.candidate.original_request


def validate_candidate_identity(reference: CandidateReady, candidate: CandidateReady) -> None:
    """Local source/workflow/renderer consistency, not database existence/authorization."""
    if candidate.original_request != reference.original_request or (
        candidate.context.workflow_run_id != reference.context.workflow_run_id
        or candidate.context.workflow_prompt_request_id
        != reference.context.workflow_prompt_request_id
        or candidate.renderer_name != reference.renderer_name
        or candidate.renderer_version != reference.renderer_version
    ):
        raise ValueError("Candidate does not match the pinned source, workflow or renderer.")


def history_snapshot(history: EvaluationHistory) -> EvaluationHistory:
    """Privacy boundary for public pure helpers, including nested model_copy tampering."""
    validated = None
    if isinstance(history, EvaluationHistory):
        with suppress(ValidationError, LLMError):
            validated = EvaluationHistory.model_validate(history.model_dump(warnings=False))
    if validated is None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return validated


class CandidateSelection(LLMModel):
    """A reference within the supplied history; no prompt, score or approval created."""

    round_number: int
    input_fingerprint: str = Field(repr=False)
    selection_policy_version: Literal["1.0.0"]

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if (
            not 1 <= self.round_number <= MAX_EVALUATION_ROUNDS_HARD_LIMIT
            or re.fullmatch(r"[0-9a-f]{64}", self.input_fingerprint) is None
        ):
            raise ValueError("Selection requires a valid completed-round reference.")
        return self


class ProgressDecision(LLMModel):
    """Advice for a future controller, never permission to mark an evaluation complete."""

    action: Literal["revise", "evaluate", "stop"]
    reason: Literal[
        "revision_justified",
        "new_candidate",
        "qualified",
        "evaluation_limit",
        "no_actionable_feedback",
        "repeated_revision_attempt",
        "unchanged_baseline",
        "already_evaluated",
        "clarification_required",
    ]
    baseline_round_number: int
    next_round_number: int | None
    revision_attempt_key: str | None = Field(repr=False)
    progress_policy_version: Literal["1.0.0"]

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if not 1 <= self.baseline_round_number <= MAX_EVALUATION_ROUNDS_HARD_LIMIT:
            raise ValueError("Progress must reference a completed baseline.")
        if (
            self.revision_attempt_key is not None
            and re.fullmatch(r"[0-9a-f]{64}", self.revision_attempt_key) is None
        ):
            raise ValueError("Revision attempt key must be a SHA-256 digest.")
        if (self.action == "revise" or self.reason == "repeated_revision_attempt") != (
            self.revision_attempt_key is not None
        ):
            raise ValueError("Only a revision or repeated-attempt stop requires an attempt key.")
        if self.action == "stop":
            if self.next_round_number is not None or self.reason in {
                "revision_justified",
                "new_candidate",
            }:
                raise ValueError("Stopping cannot authorize another evaluation.")
        elif (
            self.next_round_number is None
            or not self.baseline_round_number
            < self.next_round_number
            <= MAX_EVALUATION_ROUNDS_HARD_LIMIT
            or self.reason != ("revision_justified" if self.action == "revise" else "new_candidate")
        ):
            raise ValueError("Progress action, reason and next-round reference must agree.")
        return self
