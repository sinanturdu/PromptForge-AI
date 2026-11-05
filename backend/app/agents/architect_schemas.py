"""Architect wire output and separate application result; never log content dumps."""

from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from app.llm.schemas import CallMetadata, LLMModel
from app.models.enums import TargetMode
from app.modules.prompts.canonical import CanonicalPrompt

ArchitectDecision = Literal["preserve", "rewrite", "clarify"]


class ArchitectOutput(LLMModel):
    """Wire keys have no defaults so strict structured output can require them all."""

    decision: ArchitectDecision
    canonical_prompt: CanonicalPrompt | None = Field(repr=False)
    changes: tuple[str, ...] = Field(repr=False)
    questions: tuple[str, ...] = Field(repr=False)

    @field_validator("changes", "questions")
    @classmethod
    def validate_notes(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not text.strip() or len(text) > 1000 for text in value):
            raise ValueError("Architect notes must be nonblank and at most 1000 characters each.")
        return value

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if len(self.changes) > 8 or len(self.questions) > 3:
            raise ValueError("Architect output exceeds the note limits.")
        if self.decision == "preserve" and (
            self.canonical_prompt is not None or self.changes or self.questions
        ):
            raise ValueError("Preserve requires null canonical content and empty notes.")
        if self.decision == "rewrite" and (self.canonical_prompt is None or self.questions):
            raise ValueError("Rewrite requires canonical content and no clarification questions.")
        if self.decision == "clarify" and (
            self.canonical_prompt is not None or self.changes or not self.questions
        ):
            raise ValueError("Clarify requires one to three questions and no candidate or changes.")
        return self


class ArchitectResult(LLMModel):
    """Copy rendered_prompt only. Other fields are explanatory or execution metadata."""

    decision: ArchitectDecision
    canonical_prompt: CanonicalPrompt | None = Field(repr=False)
    rendered_prompt: str | None = Field(repr=False)
    changes: tuple[str, ...] = Field(repr=False)
    questions: tuple[str, ...] = Field(repr=False)
    target_mode: TargetMode
    metadata: CallMetadata
    agent_version: str
    instruction_version: str
    renderer_name: str | None
    renderer_version: str | None
