"""One shared, validated reference dataset and explicitly scripted contract examples."""

import re
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from app.agents.architect_schemas import ArchitectDecision, ArchitectOutput
from app.llm.schemas import LLMModel
from app.modules.prompts.schemas import PromptRequestCreate

DATASET_PATH = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "architect_cases.json"
Category = Literal["translation", "creative", "coding", "analytical", "editing", "reference"]


class ContractExample(LLMModel):
    """An authored software fixture, never a unique quality reference answer."""

    output: ArchitectOutput = Field(repr=False)
    required_fragments: tuple[str, ...] = Field(default=(), repr=False)
    forbidden_fragments: tuple[str, ...] = Field(default=(), repr=False)


class BenchmarkCase(LLMModel):
    name: str
    category: Category
    language: Literal["tr", "en"]
    tags: tuple[str, ...]
    payload: PromptRequestCreate = Field(repr=False)
    acceptable_decisions: tuple[ArchitectDecision, ...]
    requirements: tuple[str, ...] = Field(repr=False)
    forbidden_additions: tuple[str, ...] = Field(repr=False)
    required_literals: tuple[str, ...] = Field(default=(), repr=False)
    forbidden_literals: tuple[str, ...] = Field(default=(), repr=False)
    clarification_required: bool
    rationale: str = Field(repr=False)
    contract_example: ContractExample | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def validate_reference(self) -> Self:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,79}", self.name):
            raise ValueError("Case identifiers must use safe lowercase identifiers.")
        if not self.acceptable_decisions or len(set(self.acceptable_decisions)) != len(
            self.acceptable_decisions
        ):
            raise ValueError("Acceptable decisions must be nonempty and unique.")
        if self.clarification_required != (self.acceptable_decisions == ("clarify",)):
            raise ValueError("Blocking clarification must match the acceptable decision set.")
        if not self.clarification_required and "clarify" in self.acceptable_decisions:
            raise ValueError("Optional information must not require clarification.")
        if (
            not self.requirements
            or not self.forbidden_additions
            or not self.tags
            or not self.rationale.strip()
        ):
            raise ValueError(
                "Each case needs requirements, forbidden additions, tags and rationale."
            )
        for values in (
            self.requirements,
            self.forbidden_additions,
            self.required_literals,
            self.forbidden_literals,
            self.tags,
        ):
            if any(not value.strip() for value in values) or len(set(values)) != len(values):
                raise ValueError("Reference lists must contain unique nonblank entries.")
        source = self.payload.raw_prompt + "\n" + (self.payload.user_context or "")
        if any(literal not in source for literal in self.required_literals):
            raise ValueError("Required literals must be grounded in the supplied input.")
        if (
            self.contract_example is not None
            and self.contract_example.output.decision not in self.acceptable_decisions
        ):
            raise ValueError("Scripted examples must use an acceptable reference decision.")
        return self


class BenchmarkDataset(LLMModel):
    version: Literal["1.0.0"]
    cases: tuple[BenchmarkCase, ...] = Field(repr=False)

    @model_validator(mode="after")
    def validate_cases(self) -> Self:
        if not self.cases or len({case.name for case in self.cases}) != len(self.cases):
            raise ValueError("Dataset cases must be nonempty and have unique identifiers.")
        return self


def load_dataset(path: Path = DATASET_PATH) -> BenchmarkDataset:
    """Read afresh; callers never share mutable request DTOs between runs."""
    return BenchmarkDataset.model_validate_json(path.read_text(encoding="utf-8"))
