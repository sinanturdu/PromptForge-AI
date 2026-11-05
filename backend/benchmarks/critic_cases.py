"""Versioned Critic scenarios, human references and separate scripted software probes."""

import re
from decimal import Decimal
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from app.agents.critic_schemas import EvidenceSource, IssueCode, Severity
from app.llm.schemas import LLMModel
from app.modules.prompts.schemas import PromptRequestCreate
from app.scoring.rubric import CriterionKey
from benchmarks.architect_cases import Category as TaskType

DATASET_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "critic_cases.json"
ScriptKind = Literal[
    "perfect",
    "contract_issue",
    "fabricated_evidence",
    "misattributed_evidence",
    "missing_evidence",
    "contradictory_score",
    "unsupported_deduction",
    "uncertain",
    "null_score",
    "threshold_84",
    "threshold_85",
    "intent_6",
    "intent_7",
    "critical_high",
    "invalid_input",
    "misleading_quote",
    "unnecessary_role",
    "omission",
]
ScoreStatus = Literal["qualified", "not_qualified", "unassessable"]


class ContractExample(LLMModel):
    """The original thirteen M5C-2 fixtures, retained as authored contract scripts."""

    name: str
    raw: str = Field(repr=False)
    candidate: str = Field(repr=False)
    criterion: CriterionKey
    code: IssueCode
    severity: Severity
    score: int
    source: EvidenceSource
    quote: str = Field(repr=False)

    @model_validator(mode="after")
    def validate_example(self) -> Self:
        if not 0 <= self.score <= (8 if self.severity == "minor" else 6):
            raise ValueError("Contract example score contradicts severity.")
        source = self.raw if self.source == "raw_prompt" else self.candidate
        if (
            self.source not in {"raw_prompt", "rendered_prompt"}
            or not self.quote.strip()
            or self.quote not in source
        ):
            raise ValueError("Contract example must cite supplied source text.")
        return self


class ObjectiveExpectation(LLMModel):
    error: Literal["invalid_request", "invalid_structured_output"] | None
    status: ScoreStatus | None
    total: Decimal | None

    @model_validator(mode="after")
    def validate_outcome(self) -> Self:
        if self.error is not None:
            if self.status is not None or self.total is not None:
                raise ValueError("Rejected probes have no scoring outcome.")
        elif self.status is None or ((self.status == "unassessable") != (self.total is None)):
            raise ValueError("Evaluated probes need consistent status and score availability.")
        if self.total is not None and (not self.total.is_finite() or not 0 <= self.total <= 100):
            raise ValueError("Expected total must be finite and within zero to one hundred.")
        return self


class Probe(LLMModel):
    name: ScriptKind
    expected: ObjectiveExpectation


class CriticCase(LLMModel):
    name: str
    category: Literal[
        "strong", "intent_preservation", "overprescription", "injection", "evidence_scoring"
    ]
    language: Literal["tr", "en"]
    task_type: TaskType
    payload: PromptRequestCreate = Field(repr=False)
    candidate_prompt: str = Field(repr=False)
    intended_behavior: str = Field(repr=False)
    protected_constraints: tuple[str, ...] = Field(repr=False)
    review_expectation: Literal["strong", "defective", "disputed"]
    human_review_considerations: tuple[str, ...] = Field(repr=False)
    rationale: str = Field(repr=False)
    probes: tuple[Probe, ...]
    contract_example: ContractExample | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_case(self) -> Self:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,79}", self.name) is None:
            raise ValueError("Case IDs must be safe stable lowercase identifiers.")
        if not self.candidate_prompt.strip() or len(self.candidate_prompt) > 40_000:
            raise ValueError("Candidate must be nonblank and bounded.")
        if not self.intended_behavior.strip() or not self.rationale.strip():
            raise ValueError("Cases need intended behavior and rationale.")
        for values in (self.protected_constraints, self.human_review_considerations):
            if (
                not values
                or len(set(values)) != len(values)
                or any(not text.strip() for text in values)
            ):
                raise ValueError("Review references must be nonempty, unique and nonblank.")
        source = self.payload.raw_prompt + "\n" + (self.payload.user_context or "")
        if any(text not in source for text in self.protected_constraints):
            raise ValueError("Protected reference constraints must come from supplied source text.")
        if not self.probes or len({probe.name for probe in self.probes}) != len(self.probes):
            raise ValueError("Cases need unique named probes.")
        if (
            any(probe.name == "contract_issue" for probe in self.probes)
            and self.contract_example is None
        ):
            raise ValueError("Contract issue probes need an authored example.")
        if self.contract_example is not None and (
            self.contract_example.name != self.name
            or self.contract_example.raw != self.payload.raw_prompt
            or self.contract_example.candidate != self.candidate_prompt
        ):
            raise ValueError("Legacy contract example must match its scenario identity and text.")
        return self


class CriticDataset(LLMModel):
    version: Literal["1.0.0"]
    cases: tuple[CriticCase, ...] = Field(repr=False)

    @model_validator(mode="after")
    def validate_cases(self) -> Self:
        if not self.cases or len({case.name for case in self.cases}) != len(self.cases):
            raise ValueError("Dataset case identifiers must be nonempty and unique.")
        return self


def load_dataset(path: Path = DATASET_PATH) -> CriticDataset:
    return CriticDataset.model_validate_json(path.read_text(encoding="utf-8"))
