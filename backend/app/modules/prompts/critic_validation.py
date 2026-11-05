"""Shared candidate/evaluation binding and evidence attribution, without agent calls."""

from app.agents.critic_schemas import CriticOutput, CriticResult, EvidenceRef
from app.llm.errors import LLMError, LLMErrorCode
from app.modules.prompts.architect_contracts import (
    CandidateReady,
    CriticInput,
    critic_input_fingerprint,
    make_critic_input,
)
from app.scoring.rubric import CRITERIA


def _evidence_matches(evidence: EvidenceRef, payload: CriticInput) -> bool:
    # Source is a validated Literal whitelist, never an arbitrary path/URL to fetch.
    source = (
        payload.rendered_prompt
        if evidence.source == "rendered_prompt"
        else getattr(payload.original_request, evidence.source)
    )
    return isinstance(source, str) and evidence.quote in source


def validate_critic_evidence(output: CriticOutput, payload: CriticInput) -> None:
    """Check attribution on revalidated models; no semantic approval or repair call."""
    evidence = [ref for key, _, _ in CRITERIA for ref in getattr(output.criteria, key).evidence]
    evidence.extend(ref for issue in output.issues for ref in issue.evidence)
    if not all(_evidence_matches(ref, payload) for ref in evidence):
        # No repair operation: attribution needs this input, not more invented evidence.
        raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)


def validate_candidate_evaluation(candidate: CandidateReady, critic: CriticResult) -> CriticInput:
    """Bind revalidated models to exact source/text/renderer, without scoring again."""
    payload = make_critic_input(candidate)
    if (
        critic.evaluated_target_mode != payload.target_mode
        or critic.evaluated_renderer_name != payload.renderer_name
        or critic.evaluated_renderer_version != payload.renderer_version
        or critic.input_fingerprint != critic_input_fingerprint(payload)
    ):
        raise ValueError("Critic evaluation does not match the candidate input.")
    validate_critic_evidence(critic.output, payload)
    return payload
