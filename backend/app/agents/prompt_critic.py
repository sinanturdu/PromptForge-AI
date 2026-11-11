"""One blind, database-free evaluation using an injected gateway."""

import json
from contextlib import suppress
from typing import Final

from pydantic import ValidationError

from app.agents.critic_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.critic_schemas import (
    CriticOutput,
    CriticResult,
    resolve_unassessable,
)
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.schemas import CallMetadata, LLMMessage, LLMRequest, ModelSettings
from app.modules.prompts.architect_integration import CriticInput, critic_input_fingerprint
from app.modules.prompts.critic_validation import (
    validate_critic_evidence as validate_critic_evidence,
)
from app.modules.prompts.rendering import MAX_RENDERED_CHARS as MAX_RENDERED_CHARS
from app.modules.prompts.rendering import resolve_renderer
from app.scoring.rubric import RUBRIC_VERSION

AGENT_VERSION: Final = "1.0.0"


def _snapshot(payload: CriticInput) -> CriticInput:
    snapshot = None
    if isinstance(payload, CriticInput):
        with suppress(ValueError):
            value = CriticInput.model_validate(payload.model_dump(warnings=False))
            renderer = resolve_renderer(
                value.renderer_name, value.renderer_version, target_mode=value.target_mode
            )
            if len(value.rendered_prompt) <= renderer.max_rendered_chars:
                snapshot = value
    if (
        snapshot is None
        or snapshot.target_mode != snapshot.original_request.target_mode
        or not snapshot.rendered_prompt.strip()
    ):
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return snapshot


class PromptCriticAgent:
    """Borrow resources; no agent retry loop, database, rewriting or score arithmetic."""

    def __init__(self, gateway: LLMGateway, model_settings: ModelSettings) -> None:
        self._gateway = gateway
        self._model_settings = model_settings

    async def evaluate(
        self, critic_input: CriticInput, *, request_id: str | None = None
    ) -> CriticResult:
        snapshot = _snapshot(critic_input)
        # Keep unchanged intent separate from any future provenance-bearing evidence
        # assembled here. No REA placeholders, fetching or extra input fields today.
        data = snapshot.model_dump(mode="json", exclude={"original_request": {"id", "created_at"}})
        request = None
        with suppress(ValidationError):
            request = LLMRequest(
                settings=ModelSettings.model_validate(
                    self._model_settings.model_dump(warnings=False)
                ),
                request_id=request_id,
                messages=(
                    LLMMessage(role="system", content=SYSTEM_INSTRUCTION),
                    LLMMessage(role="user", content=json.dumps(data, ensure_ascii=False)),
                ),
            )
        if request is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        generated = await self._gateway.generate_json(request, CriticOutput)
        output = None
        if isinstance(generated.value, CriticOutput):
            with suppress(ValidationError):
                output = CriticOutput.model_validate(generated.value.model_dump(warnings=False))
        if output is None or not isinstance(generated.metadata, CallMetadata):
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        validate_critic_evidence(output, snapshot)
        # Fingerprint binds the local request snapshot, exact candidate and renderer.
        # It is neither authentication nor a safe diagnostic identifier.
        fingerprint = critic_input_fingerprint(snapshot)
        result = None
        with suppress(ValidationError):
            result = CriticResult(
                output=resolve_unassessable(output),
                input_fingerprint=fingerprint,
                evaluated_target_mode=snapshot.target_mode,
                evaluated_renderer_name=snapshot.renderer_name,
                evaluated_renderer_version=snapshot.renderer_version,
                agent_version=AGENT_VERSION,
                instruction_version=INSTRUCTION_VERSION,
                rubric_version=RUBRIC_VERSION,
                metadata=CallMetadata.model_validate(generated.metadata.model_dump(warnings=False)),
            )
        if result is None:
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        return result
