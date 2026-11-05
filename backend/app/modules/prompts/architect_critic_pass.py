"""One opt-in Architect -> blind Critic -> scoring pass, without persistence/loops."""

import json
from contextlib import suppress
from typing import Any, Literal

from pydantic import ConfigDict, Field, ValidationError, ValidationInfo, model_validator

from app.agents.critic_schemas import CriticResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent, validate_critic_evidence
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import LLMModel
from app.modules.prompts.architect_integration import (
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    create_architect_handoff,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.schemas import PromptRequestRead
from app.scoring.engine import SCORING_VERSION, ScoringEngine
from app.scoring.schemas import ScoringResult


class EvaluatedCandidate(LLMModel):
    """Execution succeeded; qualification lives separately in scoring.status.

    Validation binds/revalidates candidate and Critic, then scores exactly once.
    Scoring is derived when omitted, or checked against the derivation when supplied
    (e.g. revalidating a serialized outcome). No model or provider is called here.
    """

    model_config = ConfigDict(revalidate_instances="always")
    status: Literal["evaluated"] = "evaluated"
    candidate: CandidateReady = Field(repr=False)
    critic: CriticResult = Field(repr=False)
    scoring: ScoringResult = Field(repr=False)

    @model_validator(mode="before")
    @classmethod
    def bind_and_score(cls, data: Any, info: ValidationInfo) -> Any:
        if isinstance(data, cls):
            data = data.model_dump(warnings=False)
        if not isinstance(data, dict) or "candidate" not in data or "critic" not in data:
            return data
        values = dict(data)

        def revalidate[T: LLMModel](model: type[T], value: Any) -> T:
            # Preserve strict JSON-mode UUID/Decimal/enum parsing on round trips.
            # Dump nested model instances because model_copy can bypass validation.
            if isinstance(value, LLMModel):
                value = value.model_dump(
                    mode="json" if info.mode == "json" else "python", warnings=False
                )
            if info.mode == "json":
                return model.model_validate_json(json.dumps(value))
            return model.model_validate(value)

        candidate = revalidate(CandidateReady, values["candidate"])
        critic = revalidate(CriticResult, values["critic"])
        critic_input = make_critic_input(candidate)
        if (
            critic.evaluated_target_mode != critic_input.target_mode
            or critic.evaluated_renderer_name != critic_input.renderer_name
            or critic.evaluated_renderer_version != critic_input.renderer_version
            or critic.input_fingerprint != critic_input_fingerprint(critic_input)
        ):
            raise ValueError("Critic evaluation does not match the candidate input.")
        validate_critic_evidence(critic.output, critic_input)
        supplied = revalidate(ScoringResult, values["scoring"]) if "scoring" in values else None
        calculated = ScoringEngine.score(critic)
        if not isinstance(calculated, ScoringResult):
            raise ValueError("Scorer must return a scoring result.")
        scoring = revalidate(ScoringResult, calculated)
        if (
            scoring.rubric_version != critic.rubric_version
            or scoring.scoring_version != SCORING_VERSION
        ):
            raise ValueError("Evaluation and scoring policies disagree.")
        if supplied is not None and supplied != scoring:
            raise ValueError("Supplied scoring disagrees with the bound Critic evaluation.")
        values.update(candidate=candidate, critic=critic, scoring=scoring)
        return values


SinglePassOutcome = ClarificationRequired | EvaluatedCandidate


async def run_architect_critic_pass(
    architect: PromptArchitectAgent,
    critic: PromptCriticAgent,
    prompt_request: PromptRequestRead,
    *,
    context: ArchitectRunContext | None = None,
    request_id: str | None = None,
) -> SinglePassOutcome:
    """Reuse the handoff once; clarify immediately or evaluate and score once.

    Caller retains provider ownership. Errors/cancellation propagate without a
    partial outcome, pass retries, records, final-version selection or logging.
    """
    handoff = await create_architect_handoff(
        architect, prompt_request, context=context, request_id=request_id
    )
    if isinstance(handoff, ClarificationRequired):
        # Revalidate nested fields before returning even if a dependency was tampered.
        clarification = None
        with suppress(ValidationError):
            clarification = ClarificationRequired.model_validate(handoff.model_dump(warnings=False))
        if clarification is None:
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        return clarification
    critic_input = make_critic_input(handoff)
    evaluation = await critic.evaluate(critic_input, request_id=request_id)
    outcome = None
    with suppress(ValidationError):
        outcome = EvaluatedCandidate.model_validate({"candidate": handoff, "critic": evaluation})
    if outcome is None:
        raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
    return outcome
