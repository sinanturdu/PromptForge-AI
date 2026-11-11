"""Strict Critic wire contracts and a separate, versioned application result.

Bounds live in validators rather than unsupported strict-schema keywords.
Explicit dumps/validation details contain private content and are never log-safe.
"""

import re
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from app.llm.schemas import CallMetadata, LLMModel
from app.models.enums import TargetMode
from app.modules.prompts.rendering import resolve_renderer
from app.scoring.rubric import CRITERIA, CriterionKey

EvidenceSource = Literal[
    "raw_prompt",
    "user_context",
    "output_language",
    "task_profile",
    "prompt_detail_level",
    "response_detail_preference",
    "reasoning_preference",
    "target_mode",
    "rendered_prompt",
]
IssueCode = Literal[
    "intent_shift",
    "missing_requirement",
    "invented_requirement",
    "contradiction",
    "unsupported_context",
    "unnecessary_complexity",
    "insufficient_specificity",
    "task_mismatch",
    "format_mismatch",
]
Severity = Literal["critical", "major", "minor"]
MAX_TEXT_CHARS = 600
MAX_EVIDENCE = 3
MAX_ISSUES = 12


class EvidenceRef(LLMModel):
    source: EvidenceSource
    quote: str = Field(repr=False)

    @field_validator("quote")
    @classmethod
    def validate_quote(cls, value: str) -> str:
        if not value.strip() or len(value) > MAX_TEXT_CHARS:
            raise ValueError("Evidence must be nonblank and at most 600 characters.")
        return value


class CriterionAssessment(LLMModel):
    score: int | None
    rationale: str = Field(repr=False)
    evidence: tuple[EvidenceRef, ...] = Field(repr=False)

    @field_validator("score")
    @classmethod
    def validate_score(cls, value: int | None) -> int | None:
        if value is not None and not 0 <= value <= 10:
            raise ValueError("Criterion scores must be between zero and ten or null.")
        return value

    @field_validator("rationale")
    @classmethod
    def validate_rationale(cls, value: str) -> str:
        if not value.strip() or len(value) > MAX_TEXT_CHARS:
            raise ValueError("Rationale must be nonblank and at most 600 characters.")
        return value

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if len(self.evidence) > MAX_EVIDENCE or len(set(self.evidence)) != len(self.evidence):
            raise ValueError("Assessment evidence must be unique and contain at most three items.")
        if self.score is not None and not self.evidence:
            raise ValueError("An assessed criterion requires supporting evidence.")
        return self


class CriterionScores(LLMModel):
    intent_fidelity: CriterionAssessment
    clarity: CriterionAssessment
    completeness: CriterionAssessment
    structure: CriterionAssessment
    context_and_role: CriterionAssessment
    constraints_and_scope: CriterionAssessment
    output_format: CriterionAssessment
    specificity: CriterionAssessment
    consistency: CriterionAssessment


class CriticIssue(LLMModel):
    criterion: CriterionKey
    code: IssueCode
    status: Literal["supported", "uncertain"]
    severity: Severity | None
    description: str = Field(repr=False)
    recommendation: str = Field(repr=False)
    evidence: tuple[EvidenceRef, ...] = Field(repr=False)

    @field_validator("description", "recommendation")
    @classmethod
    def validate_text(cls, value: str) -> str:
        if not value.strip() or len(value) > MAX_TEXT_CHARS:
            raise ValueError("Issue text must be nonblank and at most 600 characters.")
        return value

    @model_validator(mode="after")
    def validate_support(self) -> Self:
        if len(self.evidence) > MAX_EVIDENCE or len(set(self.evidence)) != len(self.evidence):
            raise ValueError("Issue evidence must be unique and contain at most three items.")
        if self.status == "supported" and (self.severity is None or not self.evidence):
            raise ValueError("Supported issues require severity and evidence.")
        if self.status == "uncertain" and self.severity is not None:
            raise ValueError("Uncertain issues cannot assert a severity.")
        return self


class CriticOutput(LLMModel):
    criteria: CriterionScores = Field(repr=False)
    issues: tuple[CriticIssue, ...] = Field(repr=False)
    summary: str = Field(repr=False)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        if not value.strip() or len(value) > 800:
            raise ValueError("Summary must be nonblank and at most 800 characters.")
        return value

    @model_validator(mode="after")
    def validate_issues(self) -> Self:
        if len(self.issues) > MAX_ISSUES:
            raise ValueError("Critic output permits at most twelve issues.")
        seen: set[tuple[IssueCode, str, tuple[EvidenceRef, ...]]] = set()
        for issue in self.issues:
            identity = (issue.code, issue.description, issue.evidence)
            if identity in seen:
                raise ValueError("Duplicate issues cannot be counted under multiple criteria.")
            seen.add(identity)
            score = getattr(self.criteria, issue.criterion).score
            if issue.status == "supported" and score is not None:
                ceiling = 8 if issue.severity == "minor" else 6
                if score > ceiling:
                    raise ValueError("Issue severity conflicts with its primary criterion score.")
        return self


def resolve_unassessable(output: CriticOutput) -> CriticOutput:
    """Conservatively remove unsupported certainty, without inventing issues/calls.

    A reported uncertain issue affects its primary criterion. Optional speculative
    improvements should not be reported as issues in the first place.
    """
    updates: dict[str, CriterionAssessment] = {}
    for key, _, _ in CRITERIA:
        assessment: CriterionAssessment = getattr(output.criteria, key)
        related = tuple(issue for issue in output.issues if issue.criterion == key)
        reason = None
        if any(issue.status == "uncertain" for issue in related):
            reason = "Unassessable: an unresolved issue affects this criterion."
        elif assessment.score is not None and assessment.score < 9:
            supported = tuple(issue for issue in related if issue.status == "supported")
            if not supported or (
                assessment.score <= 6
                and not any(issue.severity in {"major", "critical"} for issue in supported)
            ):
                reason = "Unassessable: no supported finding justifies the reported deduction."
        if reason is not None and assessment.score is not None:
            updates[key] = assessment.model_copy(update={"score": None, "rationale": reason})
    if not updates:
        return output
    return output.model_copy(update={"criteria": output.criteria.model_copy(update=updates)})


class CriticResult(LLMModel):
    """Trusted application handoff, not proof of semantic quality or authenticity."""

    output: CriticOutput = Field(repr=False)
    input_fingerprint: str = Field(repr=False)
    evaluated_target_mode: TargetMode
    evaluated_renderer_name: str
    evaluated_renderer_version: str
    agent_version: Literal["1.0.0"]
    instruction_version: Literal["1.0.0"]
    rubric_version: Literal["2.0.0"]
    metadata: CallMetadata = Field(repr=False)

    @field_validator("input_fingerprint")
    @classmethod
    def validate_fingerprint(cls, value: str) -> str:
        if re.fullmatch(r"[0-9a-f]{64}", value) is None:
            raise ValueError("Evaluation fingerprint must be a SHA-256 hexadecimal digest.")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        resolve_renderer(
            self.evaluated_renderer_name,
            self.evaluated_renderer_version,
            target_mode=self.evaluated_target_mode,
        )
        if resolve_unassessable(self.output) != self.output:
            raise ValueError("Unsupported certainty must be resolved before result handoff.")
        if (
            self.metadata.attempts < 1
            or self.metadata.provider is None
            or self.metadata.requested_model is None
        ):
            raise ValueError("Critic result requires executed-operation metadata.")
        return self
