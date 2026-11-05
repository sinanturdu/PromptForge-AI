"""In-memory prompt creation through one injected gateway operation."""

import json
from contextlib import suppress

from pydantic import ValidationError

from app.agents.architect_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.architect_schemas import ArchitectOutput, ArchitectResult
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.schemas import LLMMessage, LLMRequest, ModelSettings
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
from app.modules.prompts.schemas import PromptRequestCreate

AGENT_VERSION = "1.0.0"


class PromptArchitectAgent:
    """Borrow the gateway. No database, provider ownership, scoring or retry loop."""

    def __init__(self, gateway: LLMGateway, model_settings: ModelSettings) -> None:
        self._gateway = gateway
        self._model_settings = model_settings
        self._renderer = GenericPromptRenderer()

    @staticmethod
    def _snapshot(payload: PromptRequestCreate) -> PromptRequestCreate:
        # The API DTO is mutable. Snapshot and revalidate before any await, without
        # exposing a validation error's private input values or exception context.
        snapshot = None
        with suppress(ValidationError):
            snapshot = PromptRequestCreate.model_validate(payload.model_dump(warnings=False))
        if snapshot is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        return snapshot

    async def create(
        self,
        payload: PromptRequestCreate,
        *,
        request_id: str | None = None,
    ) -> ArchitectResult:
        snapshot = self._snapshot(payload)
        # This is the input assembly seam for future curated textual evidence.
        # It currently accepts only the existing request DTO; no REA placeholder.
        request = LLMRequest(
            settings=self._model_settings,
            request_id=request_id,
            messages=(
                LLMMessage(role="system", content=SYSTEM_INSTRUCTION),
                LLMMessage(
                    role="user",
                    content=json.dumps(
                        {"request": snapshot.model_dump(mode="json")},
                        ensure_ascii=False,
                    ),
                ),
            ),
        )
        generated = await self._gateway.generate_json(request, ArchitectOutput)
        output = generated.value
        canonical = output.canonical_prompt
        rendered = None
        if output.decision == "preserve":
            canonical = CanonicalPrompt.from_task(snapshot.raw_prompt)
            rendered = snapshot.raw_prompt
        elif output.decision == "rewrite":
            assert canonical is not None  # ArchitectOutput enforces this before return.
            rendered = self._renderer.render(canonical)
        return ArchitectResult(
            decision=output.decision,
            canonical_prompt=canonical,
            rendered_prompt=rendered,
            changes=output.changes,
            questions=output.questions,
            target_mode=snapshot.target_mode,
            metadata=generated.metadata,
            agent_version=AGENT_VERSION,
            instruction_version=INSTRUCTION_VERSION,
            renderer_name=self._renderer.name if rendered is not None else None,
            renderer_version=self._renderer.version if rendered is not None else None,
        )
