"""Content-free scoring outcomes; unassessable is distinct from a failed score."""

from decimal import Decimal
from typing import Literal, Self

from pydantic import model_validator

from app.llm.schemas import LLMModel
from app.scoring.rubric import MIN_TOTAL_SCORE


class GateResults(LLMModel):
    all_criteria_assessable: bool
    total_at_least_85: bool | None
    no_critical_issues: bool
    intent_at_least_7: bool | None


class ScoringResult(LLMModel):
    status: Literal["qualified", "not_qualified", "unassessable"]
    total_score: Decimal | None
    passed: bool
    gates: GateResults
    rubric_version: Literal["2.0.0"]
    scoring_version: Literal["1.0.0"]

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.total_score is not None and (
            not self.total_score.is_finite() or not 0 <= self.total_score <= 100
        ):
            raise ValueError("Total must be finite and between zero and one hundred.")
        if self.gates.all_criteria_assessable != (self.total_score is not None):
            raise ValueError("Only fully assessable evaluations have a total.")
        expected_total_gate = (
            None if self.total_score is None else self.total_score >= MIN_TOTAL_SCORE
        )
        if self.gates.total_at_least_85 != expected_total_gate or (
            self.gates.all_criteria_assessable and self.gates.intent_at_least_7 is None
        ):
            raise ValueError("Gate results disagree with score availability or threshold.")
        passed = (
            self.gates.all_criteria_assessable
            and self.gates.total_at_least_85 is True
            and self.gates.no_critical_issues
            and self.gates.intent_at_least_7 is True
        )
        expected_status = (
            "unassessable"
            if self.total_score is None
            else "qualified"
            if passed
            else "not_qualified"
        )
        if self.passed != passed or self.status != expected_status:
            raise ValueError("Scoring status must agree with all qualification gates.")
        return self
