"""Pure next-step advice and revision-attempt identity; never executes an agent."""

import hashlib
import json
import re
from contextlib import suppress
from typing import Final

from pydantic import ValidationError

from app.agents.revision_instructions import INSTRUCTION_VERSION, REVISION_VERSION
from app.agents.revision_schemas import (
    ProtectedSourceSpan,
    RevisedCandidate,
    RevisionClarification,
    RevisionInput,
    UnchangedBaseline,
)
from app.llm.errors import LLMError, LLMErrorCode
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT
from app.modules.prompts.architect_contracts import (
    CandidateReady,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.optimization_contracts import (
    EvaluationHistory,
    EvaluationRound,
    ProgressDecision,
    history_snapshot,
    validate_candidate_identity,
    validate_revision_provenance,
)
from app.modules.prompts.revision_integration import make_revision_input

PROGRESS_POLICY_VERSION: Final = "1.0.0"


def revision_attempt_key(payload: RevisionInput) -> str:
    """Canonicalize actual feedback/protection; exclude round numbers and run telemetry."""
    validated = None
    if isinstance(payload, RevisionInput):
        with suppress(ValidationError, LLMError):
            validated = RevisionInput.model_validate(payload.model_dump(warnings=False))
    if validated is None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    feedback = []
    for finding in validated.feedback:
        item = finding.issue.model_dump(mode="json", exclude={"status"})
        item["evidence"] = sorted(item["evidence"], key=lambda ref: (ref["source"], ref["quote"]))
        # Local issue positions are labels, not the content of a revision request.
        feedback.append(item)
    data = dict(
        baseline_input_fingerprint=validated.evaluation.input_fingerprint,
        feedback=sorted(
            feedback, key=lambda item: json.dumps(item, sort_keys=True, ensure_ascii=False)
        ),
        protected_spans=[
            span.model_dump(mode="json")
            for span in sorted(
                validated.protected_spans,
                key=lambda span: (span.source, span.start, span.end, span.kind),
            )
        ],
        revision_policy_version=REVISION_VERSION,
        instruction_version=INSTRUCTION_VERSION,
    )
    return hashlib.sha256(
        json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _decision(
    baseline: EvaluationRound,
    reason: str,
    *,
    action: str = "stop",
    next_round: int | None = None,
    attempt_key: str | None = None,
) -> ProgressDecision:
    return ProgressDecision.model_validate(
        dict(
            action=action,
            reason=reason,
            baseline_round_number=baseline.round_number,
            next_round_number=next_round,
            revision_attempt_key=attempt_key,
            progress_policy_version=PROGRESS_POLICY_VERSION,
        )
    )


def _baseline(history: EvaluationHistory) -> EvaluationRound:
    selection = select_best_candidate(history)
    return history.rounds[selection.round_number - 1]


def _early_stop(history: EvaluationHistory, baseline: EvaluationRound) -> ProgressDecision | None:
    if baseline.evaluated.scoring.passed:
        return _decision(baseline, "qualified")
    if len(history.rounds) >= MAX_EVALUATION_ROUNDS_HARD_LIMIT:
        return _decision(baseline, "evaluation_limit")
    return None


def _has_feedback(baseline: EvaluationRound) -> bool:
    # Existing supported-issue validation requires severity, actionable text and evidence.
    return any(issue.status == "supported" for issue in baseline.evaluated.critic.output.issues)


def check_revision_progress(
    history: EvaluationHistory,
    *,
    attempted_revision_keys: tuple[str, ...] = (),
    protected_spans: tuple[ProtectedSourceSpan, ...] = (),
) -> ProgressDecision:
    validated = history_snapshot(history)
    if not isinstance(attempted_revision_keys, tuple) or any(
        not isinstance(key, str) or re.fullmatch(r"[0-9a-f]{64}", key) is None
        for key in attempted_revision_keys
    ):
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    baseline = _baseline(validated)
    stop = _early_stop(validated, baseline)
    if stop is not None:
        return stop
    if not _has_feedback(baseline):
        return _decision(baseline, "no_actionable_feedback")
    payload = make_revision_input(
        baseline.evaluated,
        next_evaluation_round=len(validated.rounds) + 1,
        protected_spans=protected_spans,
    )
    key = revision_attempt_key(payload)
    if key in attempted_revision_keys:
        return _decision(baseline, "repeated_revision_attempt", attempt_key=key)
    return _decision(
        baseline,
        "revision_justified",
        action="revise",
        next_round=len(validated.rounds) + 1,
        attempt_key=key,
    )


def check_candidate_progress(
    history: EvaluationHistory,
    proposed: CandidateReady | RevisedCandidate | UnchangedBaseline | RevisionClarification,
) -> ProgressDecision:
    validated = history_snapshot(history)
    baseline = _baseline(validated)
    stop = _early_stop(validated, baseline)
    if stop is not None:
        return stop  # No proposed input is consumed or authorized after a terminal gate.
    result = None
    with suppress(ValueError, LLMError):
        if isinstance(proposed, (RevisedCandidate, UnchangedBaseline, RevisionClarification)):
            value = type(proposed).model_validate(proposed.model_dump(warnings=False))
            if (
                value.baseline != baseline.evaluated.candidate
                or value.next_evaluation_round != len(validated.rounds) + 1
            ):
                raise ValueError(
                    "Revision outcome does not match the selected baseline/next round."
                )
            validate_revision_provenance(baseline.evaluated, value, len(validated.rounds) + 1)
            if isinstance(value, UnchangedBaseline):
                return _decision(baseline, "unchanged_baseline")
            if isinstance(value, RevisionClarification):
                return _decision(baseline, "clarification_required")
            candidate = value.candidate
        elif isinstance(proposed, CandidateReady):
            candidate = CandidateReady.model_validate(proposed.model_dump(warnings=False))
        else:
            raise ValueError("Proposed input is not a candidate or revision outcome.")
        validate_candidate_identity(validated.rounds[0].evaluated.candidate, candidate)
        fingerprint = critic_input_fingerprint(make_critic_input(candidate))
        if fingerprint in {item.evaluated.critic.input_fingerprint for item in validated.rounds}:
            return _decision(baseline, "already_evaluated")
        if candidate.decision != "rewrite":
            raise ValueError("A new proposed revision candidate must be a rewrite.")
        used_ids = {item.evaluated.candidate.context.agent_run_id for item in validated.rounds}
        if (
            candidate.context.agent_run_id is not None
            and candidate.context.agent_run_id in used_ids
        ):
            raise ValueError("Proposed candidate reuses an earlier Architect AgentRun ID.")
        if not _has_feedback(baseline):
            return _decision(baseline, "no_actionable_feedback")
        result = _decision(
            baseline, "new_candidate", action="evaluate", next_round=len(validated.rounds) + 1
        )
    # Shared provenance validation can raise ValueError in addition to Pydantic errors.
    if result is None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return result
