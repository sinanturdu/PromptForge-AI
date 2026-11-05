"""Pure ADR-007 ordering; nullable values are compared without invented scores."""

from decimal import Decimal
from typing import Final

from app.modules.prompts.optimization_contracts import (
    CandidateSelection,
    EvaluationHistory,
    EvaluationRound,
    history_snapshot,
)

SELECTION_POLICY_VERSION: Final = "1.0.0"


def _compare_known(left: Decimal | int | None, right: Decimal | int | None) -> int:
    if left is None:
        return 0 if right is None else -1
    if right is None:
        return 1
    return (left > right) - (left < right)


def _better(left: EvaluationRound, right: EvaluationRound) -> bool:
    a, b = left.evaluated, right.evaluated
    for a_flag, b_flag in (
        (a.scoring.passed, b.scoring.passed),
        (a.scoring.gates.no_critical_issues, b.scoring.gates.no_critical_issues),
    ):
        if a_flag != b_flag:
            return a_flag
    for a_score, b_score in (
        (a.scoring.total_score, b.scoring.total_score),
        (
            a.critic.output.criteria.intent_fidelity.score,
            b.critic.output.criteria.intent_fidelity.score,
        ),
    ):
        comparison = _compare_known(a_score, b_score)
        if comparison:
            return comparison > 0
    return left.round_number < right.round_number


def select_best_candidate(history: EvaluationHistory) -> CandidateSelection:
    validated = history_snapshot(history)
    best = validated.rounds[0]
    for candidate in validated.rounds[1:]:
        if _better(candidate, best):
            best = candidate
    return CandidateSelection(
        round_number=best.round_number,
        input_fingerprint=best.evaluated.critic.input_fingerprint,
        selection_policy_version=SELECTION_POLICY_VERSION,
    )
