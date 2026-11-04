"""Validated wire contracts. Dumps and structured validation errors are NOT log-safe."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
TokenCount = Annotated[int, Field(ge=0)]


class LLMModel(BaseModel):
    """Immutable fields, strict inputs and input-free ordinary validation messages.

    Nested JSON dictionaries are not deeply immutable. Never log model_dump(),
    ValidationError.errors() or .json(): these can still include private inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, hide_input_in_errors=True)


class ModelSettings(LLMModel):
    """Application bounds, not a claim about any provider's capabilities."""

    model: Identifier
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)


class LLMMessage(LLMModel):
    role: Literal["system", "user", "assistant"]
    content: Annotated[str, StringConstraints(min_length=1, pattern=r"\S")] = Field(repr=False)


class LLMRequest(LLMModel):
    settings: ModelSettings
    messages: tuple[LLMMessage, ...] = Field(min_length=1, repr=False)
    response_schema: dict[str, JsonValue] | None = Field(default=None, repr=False)
    request_id: Identifier | None = Field(default=None, repr=False)


class TokenUsage(LLMModel):
    """Unknown counts remain None; reasoning/cached counts may be subsets."""

    input_tokens: TokenCount | None = None
    output_tokens: TokenCount | None = None
    total_tokens: TokenCount | None = None
    cached_input_tokens: TokenCount | None = None
    reasoning_tokens: TokenCount | None = None


class LLMResponse(LLMModel):
    provider: Identifier
    resolved_model: Identifier | None = None
    text: str | None = Field(default=None, repr=False)
    finish_reason: Literal["complete", "length", "refusal", "other"]
    usage: TokenUsage | None = None
    upstream_request_id: Identifier | None = Field(default=None, repr=False)


class CallMetadata(LLMModel):
    """Content-free metadata; identifiers must still be treated as untrusted strings."""

    provider: Identifier
    resolved_model: Identifier | None = None
    usage: TokenUsage | None = None
    upstream_request_id: Identifier | None = Field(default=None, repr=False)
    request_id: Identifier | None = Field(default=None, repr=False)
    attempts: int = Field(default=1, ge=1)
    repair_attempted: bool = False


class StructuredResult[T: BaseModel](LLMModel):
    value: T = Field(repr=False)
    metadata: CallMetadata
