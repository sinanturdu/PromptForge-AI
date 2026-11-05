"""Sparse CanonicalPrompt content from ADR-011; no required visible sections."""

from typing import Self

from pydantic import Field, field_validator, model_validator

from app.llm.schemas import LLMModel
from app.modules.prompts.schemas import MAX_RAW_PROMPT_LENGTH, MAX_USER_CONTEXT_LENGTH

MAX_CANONICAL_CHARS = MAX_RAW_PROMPT_LENGTH + MAX_USER_CONTEXT_LENGTH
MAX_CANONICAL_ITEMS = 128


class CanonicalPrompt(LLMModel):
    """Required wire keys; unused content is null/empty. Never log dumps/errors().

    Items contain complete copy-ready wording, including negative wording for
    prohibitions. Tuple collections are immutable and serialize as JSON arrays.
    Local validators impose bounds without unsupported JSON Schema keywords.
    """

    task: str = Field(repr=False)
    role: str | None = Field(repr=False)
    context: str | None = Field(repr=False)
    input_data: str | None = Field(repr=False)
    output_format: str | None = Field(repr=False)
    requirements: tuple[str, ...] = Field(repr=False)
    constraints: tuple[str, ...] = Field(repr=False)
    prohibitions: tuple[str, ...] = Field(repr=False)
    examples: tuple[str, ...] = Field(repr=False)
    variables: tuple[str, ...] = Field(repr=False)
    assumptions: tuple[str, ...] = Field(repr=False)

    @field_validator("task", "role", "context", "input_data", "output_format")
    @classmethod
    def validate_text(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or len(value) > MAX_CANONICAL_CHARS):
            raise ValueError("Canonical text must be nonblank and within the content limit.")
        return value

    @field_validator(
        "requirements", "constraints", "prohibitions", "examples", "variables", "assumptions"
    )
    @classmethod
    def validate_items(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_CANONICAL_ITEMS or any(
            not item.strip() or len(item) > MAX_CANONICAL_CHARS for item in value
        ):
            raise ValueError("Canonical items must be nonblank and within the content limits.")
        return value

    @model_validator(mode="after")
    def validate_total_size(self) -> Self:
        texts = (
            self.task,
            self.role,
            self.context,
            self.input_data,
            self.output_format,
            *self.requirements,
            *self.constraints,
            *self.prohibitions,
            *self.examples,
            *self.variables,
            *self.assumptions,
        )
        if sum(len(text) for text in texts if text is not None) > MAX_CANONICAL_CHARS:
            raise ValueError("Canonical content exceeds the content limit.")
        return self

    @classmethod
    def from_task(cls, task: str) -> Self:
        """Represent a preserved original without extracting or duplicating its content."""
        return cls(
            task=task,
            role=None,
            context=None,
            input_data=None,
            output_format=None,
            requirements=(),
            constraints=(),
            prohibitions=(),
            examples=(),
            variables=(),
            assumptions=(),
        )
