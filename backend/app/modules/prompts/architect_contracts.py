"""Shared Architect/handoff data contracts; no agent implementation imports."""

import hashlib
import json
from contextlib import suppress
from typing import Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agents.architect_schemas import ArchitectOutput
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import CallMetadata, LLMModel
from app.models.enums import TargetMode
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import resolve_renderer
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead


class ArchitectRequestSnapshot(PromptRequestRead):
    """Existing read DTO made immutable/private for handoff, not a new database record."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        hide_input_in_errors=True,
        from_attributes=False,
    )
    raw_prompt: str = Field(repr=False)
    user_context: str | None = Field(repr=False)
    output_language: str | None = Field(repr=False)
    task_profile: str | None = Field(repr=False)

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        PromptRequestCreate.model_validate(self.model_dump(exclude={"id", "created_at"}))
        return self


class ArchitectRunContext(LLMModel):
    """Caller-supplied existing IDs and their loaded parent IDs; never allocate IDs.

    These are relationship assertions, not proof of database existence/authorization.
    The future caller must load and verify records inside its own transaction.
    """

    prompt_request_id: UUID
    workflow_run_id: UUID | None = None
    workflow_prompt_request_id: UUID | None = None
    agent_run_id: UUID | None = None
    agent_workflow_run_id: UUID | None = None

    @model_validator(mode="after")
    def validate_ancestry(self) -> Self:
        if self.workflow_run_id is None:
            if any(
                value is not None
                for value in (
                    self.workflow_prompt_request_id,
                    self.agent_run_id,
                    self.agent_workflow_run_id,
                )
            ):
                raise ValueError("Agent references require an existing workflow reference.")
        elif self.workflow_prompt_request_id != self.prompt_request_id:
            raise ValueError("Workflow reference belongs to a different prompt request.")
        if self.agent_run_id is not None and self.agent_workflow_run_id != self.workflow_run_id:
            raise ValueError("Agent reference belongs to a different workflow.")
        if self.agent_run_id is None and self.agent_workflow_run_id is not None:
            raise ValueError("Agent parent reference requires an agent reference.")
        return self


class _ArchitectHandoff(LLMModel):
    original_request: ArchitectRequestSnapshot = Field(repr=False)
    context: ArchitectRunContext
    metadata: CallMetadata = Field(repr=False)
    agent_version: str = Field(repr=False)
    instruction_version: str = Field(repr=False)

    @field_validator("agent_version", "instruction_version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        if not value.strip() or len(value) > 32:
            raise ValueError("Architect versions must be nonblank and at most 32 characters.")
        return value

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.context.prompt_request_id != self.original_request.id:
            raise ValueError("Handoff request identity does not match its source.")
        if (
            self.metadata.attempts < 1
            or self.metadata.provider is None
            or self.metadata.requested_model is None
        ):
            raise ValueError("Handoff requires metadata from an executed Architect operation.")
        return self


class CandidateReady(_ArchitectHandoff):
    """A candidate eligible for later evaluation, not a persisted or approved version."""

    status: Literal["candidate_ready"] = "candidate_ready"
    decision: Literal["preserve", "rewrite"]
    canonical_prompt: CanonicalPrompt = Field(repr=False)
    rendered_prompt: str = Field(repr=False)
    changes: tuple[str, ...] = Field(repr=False)
    renderer_name: str
    renderer_version: str

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        renderer = resolve_renderer(
            self.renderer_name, self.renderer_version, target_mode=self.original_request.target_mode
        )
        if self.rendered_prompt != renderer.render(self.canonical_prompt):
            raise ValueError("Candidate canonical and rendered content disagree.")
        if self.decision == "preserve" and (
            self.rendered_prompt != self.original_request.raw_prompt
            or self.canonical_prompt != CanonicalPrompt.from_task(self.original_request.raw_prompt)
        ):
            raise ValueError("Preserved candidate must equal the original request exactly.")
        ArchitectOutput(
            decision=self.decision,
            canonical_prompt=self.canonical_prompt if self.decision == "rewrite" else None,
            changes=self.changes,
            questions=(),
        )
        return self


class ClarificationRequired(_ArchitectHandoff):
    """No candidate/version fields: callers must resolve blocking questions first."""

    status: Literal["clarification_required"] = "clarification_required"
    questions: tuple[str, ...] = Field(repr=False)

    @model_validator(mode="after")
    def validate_questions(self) -> Self:
        ArchitectOutput(
            decision="clarify", canonical_prompt=None, changes=(), questions=self.questions
        )
        return self


ArchitectHandoff = CandidateReady | ClarificationRequired


class CriticInput(LLMModel):
    """Blind input projection, not a Critic implementation or quality assertion."""

    original_request: ArchitectRequestSnapshot = Field(repr=False)
    rendered_prompt: str = Field(repr=False)
    target_mode: TargetMode
    renderer_name: str
    renderer_version: str


def critic_input_fingerprint(payload: CriticInput) -> str:
    """Bind an already validated snapshot; not authentication or a log identifier.

    Preserve the M5C-2 algorithm exactly, including existing identity/timestamp,
    preferences, exact rendered text and target/renderer identity.
    """
    return hashlib.sha256(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()


def make_critic_input(candidate: CandidateReady) -> CriticInput:
    """Admit only a revalidated candidate and exclude Architect notes/telemetry."""
    validated = None
    if isinstance(candidate, CandidateReady):
        with suppress(ValidationError):
            validated = CandidateReady.model_validate(candidate.model_dump(warnings=False))
    if validated is None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return CriticInput(
        original_request=validated.original_request,
        rendered_prompt=validated.rendered_prompt,
        target_mode=validated.original_request.target_mode,
        renderer_name=validated.renderer_name,
        renderer_version=validated.renderer_version,
    )
