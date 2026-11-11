"""In-memory request/run binding; shared data types retain their original import paths."""

from contextlib import suppress

from pydantic import ValidationError

from app.agents.architect_schemas import ArchitectResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.llm.errors import LLMError, LLMErrorCode
from app.modules.prompts.architect_contracts import (
    ArchitectHandoff,
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    CriticInput,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.rendering import renderer_for_target
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead

__all__ = [
    "ArchitectHandoff",
    "ArchitectRequestSnapshot",
    "ArchitectRunContext",
    "CandidateReady",
    "ClarificationRequired",
    "CriticInput",
    "create_architect_handoff",
    "critic_input_fingerprint",
    "make_critic_input",
]


async def create_architect_handoff(
    agent: PromptArchitectAgent,
    prompt_request: PromptRequestRead,
    *,
    context: ArchitectRunContext | None = None,
    request_id: str | None = None,
    legacy_generic: bool = False,
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
    renderer = renderer_for_target(source.target_mode, legacy_generic=legacy_generic)
    result = await agent.create(payload, request_id=request_id, legacy_generic=legacy_generic)
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
                and validated.renderer_name == renderer.name
                and validated.renderer_version == renderer.version
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
