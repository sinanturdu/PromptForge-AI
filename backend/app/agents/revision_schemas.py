"""Revision-only contracts: bound feedback in, candidate/unchanged/questions out."""

import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator, model_validator

from app.agents.architect_schemas import ArchitectOutput
from app.agents.critic_schemas import MAX_ISSUES, CriticIssue, CriticResult
from app.llm.schemas import CallMetadata, LLMModel
from app.modules.prompts.architect_contracts import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.critic_validation import validate_candidate_evaluation


def _snapshot_data(data: Any) -> Any:
    if isinstance(data, BaseModel):
        data = data.model_dump(warnings=False)
    if isinstance(data, dict):
        return {
            key: value.model_dump(warnings=False) if isinstance(value, BaseModel) else value
            for key, value in data.items()
        }
    return data


class ProtectedSourceSpan(LLMModel):
    """Explicit caller-supplied character offsets, not automatically inferred rules."""

    model_config = ConfigDict(revalidate_instances="always")
    source: Literal["raw_prompt", "user_context"]
    kind: Literal["version", "quotation", "code", "identifier", "literal", "formatting"]
    start: int
    end: int

    @model_validator(mode="after")
    def validate_offsets(self) -> Self:
        if self.start < 0 or self.end <= self.start:
            raise ValueError("Protected spans need ordered nonnegative character offsets.")
        return self

    def text(self, source: ArchitectRequestSnapshot) -> str:
        content = source.raw_prompt if self.source == "raw_prompt" else source.user_context
        if content is None or self.end > len(content):
            raise ValueError("Protected span is outside the supplied source.")
        if self.source == "raw_prompt" and self.start == 0 and self.end == len(content):
            raise ValueError("Protect specific source material rather than the entire raw prompt.")
        text = content[self.start : self.end]
        if not text.strip():
            raise ValueError("Protected source material must be nonblank.")
        return text


class RevisionFinding(LLMModel):
    """One existing supported issue, with a local key; no duplicated scoring fields."""

    model_config = ConfigDict(revalidate_instances="always")
    issue_key: str
    issue: CriticIssue = Field(repr=False)

    @model_validator(mode="after")
    def validate_finding(self) -> Self:
        if re.fullmatch(r"issue_[0-9]{3}", self.issue_key) is None:
            raise ValueError("Revision issue keys must be locally assigned identifiers.")
        if self.issue.status != "supported" or self.issue.severity is None:
            raise ValueError("Only supported issues with severity can guide revision.")
        return self


class _RevisionBinding(LLMModel):
    model_config = ConfigDict(revalidate_instances="always")
    baseline: CandidateReady = Field(repr=False)
    context: ArchitectRunContext
    next_evaluation_round: int
    protected_spans: tuple[ProtectedSourceSpan, ...] = Field(repr=False)

    @model_validator(mode="before")
    @classmethod
    def snapshot_nested(cls, data: Any, info: ValidationInfo) -> Any:
        if info.mode != "json" or not isinstance(data, dict):
            return _snapshot_data(data)
        # Like EvaluatedCandidate, explicitly preserve strict JSON-mode parsing
        # for nested UUID/datetime/enum models before returning Python values.
        values = dict(data)
        for key, model in (
            ("baseline", CandidateReady),
            ("context", ArchitectRunContext),
            ("evaluation", CriticResult),
            ("candidate", CandidateReady),
            ("metadata", CallMetadata),
        ):
            if key in values:
                values[key] = model.model_validate_json(json.dumps(values[key]))
        if isinstance(values.get("protected_spans"), list):
            values["protected_spans"] = tuple(
                ProtectedSourceSpan.model_validate_json(json.dumps(span))
                for span in values["protected_spans"]
            )
        for key in ("eligible_issue_keys", "addressed_issue_keys", "questions"):
            if isinstance(values.get(key), list):
                values[key] = tuple(values[key])
        return values

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        previous = self.baseline.context
        if self.context.prompt_request_id != self.baseline.original_request.id or (
            self.context.workflow_run_id != previous.workflow_run_id
            or self.context.workflow_prompt_request_id != previous.workflow_prompt_request_id
        ):
            raise ValueError("Revision context must retain original request and workflow ancestry.")
        if (
            self.context.agent_run_id is not None
            and self.context.agent_run_id == previous.agent_run_id
        ):
            raise ValueError("A revision cannot reuse the baseline creation AgentRun reference.")
        if not 2 <= self.next_evaluation_round <= 3:
            raise ValueError("Proposed revision evaluation round must be two or three.")
        if len(self.protected_spans) > 32 or len(set(self.protected_spans)) != len(
            self.protected_spans
        ):
            raise ValueError("At most thirty-two unique protected spans are allowed.")
        for span in self.protected_spans:
            span.text(self.baseline.original_request)
        # Several adjacent spans must not collectively lock the entire task either.
        raw = self.baseline.original_request.raw_prompt
        cursor = 0
        unprotected: list[str] = []
        for span in sorted(
            (span for span in self.protected_spans if span.source == "raw_prompt"),
            key=lambda span: span.start,
        ):
            if span.start > cursor:
                unprotected.append(raw[cursor : span.start])
            cursor = max(cursor, span.end)
        unprotected.append(raw[cursor:])
        if not "".join(unprotected).strip():
            raise ValueError("Protected spans cannot collectively lock the entire raw prompt.")
        return self


class RevisionInput(_RevisionBinding):
    """Local provenance includes the evaluation; provider projection excludes it."""

    evaluation: CriticResult = Field(repr=False)

    @model_validator(mode="after")
    def validate_feedback(self) -> Self:
        validate_candidate_evaluation(self.baseline, self.evaluation)
        if not self.feedback:
            raise ValueError("Revision requires at least one supported actionable finding.")
        return self

    @property
    def feedback(self) -> tuple[RevisionFinding, ...]:
        # Preserve original issue positions even when uncertain issues are filtered.
        return tuple(
            RevisionFinding(issue_key=f"issue_{index + 1:03d}", issue=issue)
            for index, issue in enumerate(self.evaluation.output.issues)
            if issue.status == "supported"
        )


class RevisionOutput(ArchitectOutput):
    """Reuse creation wire conventions; preserve here refers to the selected baseline."""

    addressed_issue_keys: tuple[str, ...] = Field(repr=False)

    @field_validator("addressed_issue_keys")
    @classmethod
    def validate_keys(cls, keys: tuple[str, ...]) -> tuple[str, ...]:
        if (
            len(keys) > MAX_ISSUES
            or len(set(keys)) != len(keys)
            or any(re.fullmatch(r"issue_[0-9]{3}", key) is None for key in keys)
        ):
            raise ValueError("Addressed issue keys must be unique local identifiers.")
        return keys

    @model_validator(mode="after")
    def validate_claims(self) -> Self:
        if (self.decision == "rewrite") != bool(self.addressed_issue_keys):
            raise ValueError("Only a rewrite must claim one or more supplied issue keys.")
        return self


class _RevisionOutcome(_RevisionBinding):
    operation: Literal["revise"] = "revise"
    baseline_input_fingerprint: str = Field(repr=False)
    eligible_issue_keys: tuple[str, ...] = Field(repr=False)
    metadata: CallMetadata = Field(repr=False)
    agent_version: Literal["1.0.0"]
    instruction_version: Literal["1.0.0"]
    revision_version: Literal["1.0.0"]

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        if self.baseline_input_fingerprint != critic_input_fingerprint(
            make_critic_input(self.baseline)
        ):
            raise ValueError("Revision baseline fingerprint does not match its source.")
        RevisionOutput.validate_keys(self.eligible_issue_keys)
        if not self.eligible_issue_keys:
            raise ValueError("Revision outcomes require eligible feedback keys.")
        if (
            self.metadata.attempts < 1
            or self.metadata.provider is None
            or self.metadata.requested_model is None
        ):
            raise ValueError("Revision outcomes require executed-operation metadata.")
        return self


class RevisedCandidate(_RevisionOutcome):
    status: Literal["revised_candidate"] = "revised_candidate"
    candidate: CandidateReady = Field(repr=False)
    addressed_issue_keys: tuple[str, ...] = Field(repr=False)

    @model_validator(mode="after")
    def validate_revision(self) -> Self:
        candidate = self.candidate
        RevisionOutput.validate_keys(self.addressed_issue_keys)
        if not self.addressed_issue_keys or not set(self.addressed_issue_keys) <= set(
            self.eligible_issue_keys
        ):
            raise ValueError("Claimed fixes must reference supplied eligible findings.")
        if (
            candidate.decision != "rewrite"
            or candidate.rendered_prompt == self.baseline.rendered_prompt
        ):
            raise ValueError("A revised candidate requires a changed rewrite.")
        if (
            candidate.original_request != self.baseline.original_request
            or candidate.context != self.context
        ):
            raise ValueError(
                "Revised candidate must retain its original request and revision context."
            )
        if (
            candidate.metadata != self.metadata
            or candidate.agent_version != self.agent_version
            or candidate.instruction_version != self.instruction_version
        ):
            raise ValueError("Candidate and revision execution metadata disagree.")
        if any(
            span.text(self.baseline.original_request) not in candidate.rendered_prompt
            for span in self.protected_spans
        ):
            raise ValueError("Revised prompt lost protected source material.")
        return self


class UnchangedBaseline(_RevisionOutcome):
    status: Literal["unchanged_baseline"] = "unchanged_baseline"
    reason: Literal["preserve", "identical_rewrite"]

    @property
    def rendered_prompt(self) -> str:
        return self.baseline.rendered_prompt


class RevisionClarification(_RevisionOutcome):
    status: Literal["clarification_required"] = "clarification_required"
    questions: tuple[str, ...] = Field(repr=False)

    @model_validator(mode="after")
    def validate_questions(self) -> Self:
        ArchitectOutput(
            decision="clarify", canonical_prompt=None, changes=(), questions=self.questions
        )
        return self


RevisionResult = RevisedCandidate | UnchangedBaseline | RevisionClarification


def revision_request_data(payload: RevisionInput) -> dict[str, Any]:
    """Explicit allowlist; never serialize the full local input/evaluation to a model."""
    return {
        "original_request": payload.baseline.original_request.model_dump(
            mode="json", exclude={"id", "created_at"}
        ),
        "baseline_prompt": payload.baseline.rendered_prompt,
        "feedback": [
            dict(
                issue_key=finding.issue_key,
                **finding.issue.model_dump(mode="json", exclude={"status"}),
            )
            for finding in payload.feedback
        ],
        "protected_spans": [
            dict(
                source=span.source,
                kind=span.kind,
                text=span.text(payload.baseline.original_request),
            )
            for span in payload.protected_spans
        ],
    }
