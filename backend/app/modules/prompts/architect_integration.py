"""In-memory request/run binding and validated Architect handoffs, without persistence."""

from contextlib import suppress
from typing import Literal, Self
from uuid import UUID

from pydantic import ConfigDict, Field, ValidationError, field_validator, model_validator

from app.agents.architect_schemas import ArchitectOutput, ArchitectResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import CallMetadata, LLMModel
from app.models.enums import TargetMode
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
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
        renderer = GenericPromptRenderer()
        if self.renderer_name != renderer.name or self.renderer_version != renderer.version:
            raise ValueError("Candidate renderer is unsupported.")
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


async def create_architect_handoff(
    agent: PromptArchitectAgent,
    prompt_request: PromptRequestRead,
    *,
    context: ArchitectRunContext | None = None,
    request_id: str | None = None,
) -> ArchitectHandoff:
    """Snapshot/bind existing identity, call once, and validate downstream semantics.

    No session, queries, records, retries or resource ownership. Agent/gateway
    failures and external cancellation propagate without fallback candidates.
    """
    source = None
    binding = None
    with suppress(ValidationError):
        source = ArchitectRequestSnapshot.model_validate(prompt_request.model_dump(warnings=False))
        binding = (
            ArchitectRunContext(prompt_request_id=source.id)
            if context is None
            else ArchitectRunContext.model_validate(context.model_dump(warnings=False))
        )
    if source is None or binding is None or binding.prompt_request_id != source.id:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    payload = PromptRequestCreate.model_validate(source.model_dump(exclude={"id", "created_at"}))
    # Future curated REA evidence belongs at input assembly before this call;
    # original_request remains the unchanged source of intent for blind evaluation.
    result = await agent.create(payload, request_id=request_id)
    validated = None
    handoff: ArchitectHandoff | None = None
    with suppress(ValidationError):
        validated = ArchitectResult.model_validate(result.model_dump(warnings=False))
        if validated.target_mode == source.target_mode:
            if validated.decision == "clarify":
                if (
                    validated.canonical_prompt is None
                    and validated.rendered_prompt is None
                    and not validated.changes
                    and validated.renderer_name is None
                    and validated.renderer_version is None
                ):
                    handoff = ClarificationRequired(
                        original_request=source,
                        context=binding,
                        metadata=validated.metadata,
                        agent_version=validated.agent_version,
                        instruction_version=validated.instruction_version,
                        questions=validated.questions,
                    )
            elif (
                validated.canonical_prompt is not None
                and validated.rendered_prompt is not None
                and not validated.questions
                and validated.renderer_name is not None
                and validated.renderer_version is not None
            ):
                handoff = CandidateReady(
                    original_request=source,
                    context=binding,
                    metadata=validated.metadata,
                    agent_version=validated.agent_version,
                    instruction_version=validated.instruction_version,
                    decision=validated.decision,
                    canonical_prompt=validated.canonical_prompt,
                    rendered_prompt=validated.rendered_prompt,
                    changes=validated.changes,
                    renderer_name=validated.renderer_name,
                    renderer_version=validated.renderer_version,
                )
    if handoff is None:
        raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
    return handoff


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
