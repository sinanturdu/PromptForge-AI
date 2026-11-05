"""Pure arithmetic/gates on a revalidated Critic result, never model self-approval."""

from contextlib import suppress
from decimal import Context, Decimal, localcontext
from typing import Final

from pydantic import ValidationError

from app.agents.critic_schemas import CriticResult
from app.llm.errors import LLMError, LLMErrorCode
from app.scoring.rubric import CRITERIA, MIN_INTENT_FIDELITY, MIN_TOTAL_SCORE, RUBRIC_VERSION
from app.scoring.schemas import GateResults, ScoringResult

SCORING_VERSION: Final = "1.0.0"


class ScoringEngine:
    """Qualified means reported judgments pass rubric gates, not verified task success."""

    @staticmethod
    def score(critic_result: CriticResult) -> ScoringResult:
        result = None
        if isinstance(critic_result, CriticResult):
            with suppress(ValidationError):
                result = CriticResult.model_validate(critic_result.model_dump(warnings=False))
        if result is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        scores: tuple[tuple[int | None, int], ...] = tuple(
            (getattr(result.output.criteria, key).score, weight) for key, weight, _ in CRITERIA
        )
        assessable = all(score is not None for score, _ in scores)
        total = None
        if assessable:
            # Independent of a caller's global Decimal precision/rounding settings.
            with localcontext(Context(prec=28)):
                total = sum(
                    (
                        Decimal(score) * Decimal(weight) / Decimal(10)
                        for score, weight in scores
                        if score is not None
                    ),
                    start=Decimal(0),
                )
        intent = result.output.criteria.intent_fidelity.score
        gates = GateResults(
            all_criteria_assessable=assessable,
            total_at_least_85=None if total is None else total >= MIN_TOTAL_SCORE,
            no_critical_issues=not any(
                issue.status == "supported" and issue.severity == "critical"
                for issue in result.output.issues
            ),
            intent_at_least_7=None if intent is None else intent >= MIN_INTENT_FIDELITY,
        )
        passed = assessable and (
            gates.total_at_least_85 is True
            and gates.no_critical_issues
            and gates.intent_at_least_7 is True
        )
        return ScoringResult(
            status="unassessable" if not assessable else "qualified" if passed else "not_qualified",
            total_score=total,
            passed=passed,
            gates=gates,
            rubric_version=RUBRIC_VERSION,
            scoring_version=SCORING_VERSION,
        )
