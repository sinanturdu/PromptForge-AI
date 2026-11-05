"""In-memory prompt creation through one injected gateway operation."""

import json
from contextlib import suppress
from typing import Final

from pydantic import ValidationError

from app.agents.architect_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.architect_schemas import ArchitectOutput, ArchitectResult
from app.agents.revision_instructions import (
    INSTRUCTION_VERSION as REVISION_INSTRUCTION_VERSION,
)
from app.agents.revision_instructions import REVISION_VERSION
from app.agents.revision_instructions import SYSTEM_INSTRUCTION as REVISION_INSTRUCTION
from app.agents.revision_schemas import (
    RevisedCandidate,
    RevisionClarification,
    RevisionInput,
    RevisionOutput,
    RevisionResult,
    UnchangedBaseline,
    revision_request_data,
)
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.schemas import CallMetadata, LLMMessage, LLMRequest, ModelSettings
from app.modules.prompts.architect_contracts import (
    CandidateReady,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
from app.modules.prompts.schemas import PromptRequestCreate

AGENT_VERSION: Final = "1.0.0"


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

    async def revise(
        self, revision_input: RevisionInput, *, request_id: str | None = None
    ) -> RevisionResult:
        """One supported revision; no Critic call, score, record, loop or ownership."""
        snapshot = None
        if isinstance(revision_input, RevisionInput):
            with suppress(ValidationError, LLMError):
                snapshot = RevisionInput.model_validate(revision_input.model_dump(warnings=False))
        if snapshot is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        request = None
        with suppress(ValidationError):
            request = LLMRequest(
                settings=ModelSettings.model_validate(
                    self._model_settings.model_dump(warnings=False)
                ),
                request_id=request_id,
                messages=(
                    LLMMessage(role="system", content=REVISION_INSTRUCTION),
                    LLMMessage(
                        role="user",
                        content=json.dumps(revision_request_data(snapshot), ensure_ascii=False),
                    ),
                ),
            )
        if request is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        generated = await self._gateway.generate_json(request, RevisionOutput)
        output = None
        if isinstance(generated.value, RevisionOutput):
            with suppress(ValidationError):
                output = RevisionOutput.model_validate(generated.value.model_dump(warnings=False))
        if output is None or not isinstance(generated.metadata, CallMetadata):
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        eligible = tuple(finding.issue_key for finding in snapshot.feedback)
        if not set(output.addressed_issue_keys) <= set(eligible):
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        result: RevisionResult | None = None
        with suppress(ValidationError):
            metadata = CallMetadata.model_validate(generated.metadata.model_dump(warnings=False))
            common = dict(
                baseline=snapshot.baseline,
                context=snapshot.context,
                next_evaluation_round=snapshot.next_evaluation_round,
                protected_spans=snapshot.protected_spans,
                baseline_input_fingerprint=critic_input_fingerprint(
                    make_critic_input(snapshot.baseline)
                ),
                eligible_issue_keys=eligible,
                metadata=metadata,
                agent_version=AGENT_VERSION,
                instruction_version=REVISION_INSTRUCTION_VERSION,
                revision_version=REVISION_VERSION,
            )
            if output.decision == "preserve":
                result = UnchangedBaseline.model_validate(dict(reason="preserve", **common))
            elif output.decision == "clarify":
                result = RevisionClarification.model_validate(
                    dict(questions=output.questions, **common)
                )
            else:
                assert output.canonical_prompt is not None  # Existing wire validator.
                rendered = self._renderer.render(output.canonical_prompt)
                if rendered == snapshot.baseline.rendered_prompt:
                    # Discard claimed progress/changes; retain actual operation telemetry.
                    result = UnchangedBaseline.model_validate(
                        dict(reason="identical_rewrite", **common)
                    )
                else:
                    candidate = CandidateReady(
                        original_request=snapshot.baseline.original_request,
                        context=snapshot.context,
                        metadata=metadata,
                        agent_version=AGENT_VERSION,
                        instruction_version=REVISION_INSTRUCTION_VERSION,
                        decision="rewrite",
                        canonical_prompt=output.canonical_prompt,
                        rendered_prompt=rendered,
                        changes=output.changes,
                        renderer_name=self._renderer.name,
                        renderer_version=self._renderer.version,
                    )
                    result = RevisedCandidate.model_validate(
                        dict(
                            candidate=candidate,
                            addressed_issue_keys=output.addressed_issue_keys,
                            **common,
                        )
                    )
        if result is None:
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        return result
