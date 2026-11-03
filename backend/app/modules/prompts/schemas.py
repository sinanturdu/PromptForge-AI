"""Pydantic v2 request and response schemas for prompt request management."""

from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    PromptDetailLevel,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
)

MAX_RAW_PROMPT_LENGTH: Final[int] = 20_000
MAX_USER_CONTEXT_LENGTH: Final[int] = 20_000
MAX_OUTPUT_LANGUAGE_LENGTH: Final[int] = 32
MAX_TASK_PROFILE_LENGTH: Final[int] = 64

DEFAULT_PAGE: Final[int] = 1
DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100
MAX_SEARCH_QUERY_LENGTH: Final[int] = 200


class PromptRequestCreate(BaseModel):
    """Payload for submitting and persisting a new user prompt request."""

    model_config = ConfigDict(extra="forbid")

    raw_prompt: str = Field(
        ...,
        min_length=1,
        max_length=MAX_RAW_PROMPT_LENGTH,
        description="Raw user prompt text to be optimized (1..20,000 characters).",
    )
    user_context: str | None = Field(
        default=None,
        max_length=MAX_USER_CONTEXT_LENGTH,
        description="Optional background or project context accompanying the raw prompt.",
    )
    output_language: str | None = Field(
        default=None,
        max_length=MAX_OUTPUT_LANGUAGE_LENGTH,
        description="Optional target output language code or label (e.g., 'tr', 'en').",
    )
    target_mode: TargetMode = Field(
        default=TargetMode.GENERIC,
        description="Target AI family mode for prompt compilation.",
    )
    task_profile: str | None = Field(
        default=None,
        max_length=MAX_TASK_PROFILE_LENGTH,
        description="Optional task category profile (e.g., 'software_development').",
    )
    prompt_detail_level: PromptDetailLevel = Field(
        default=PromptDetailLevel.MEDIUM,
        description="Desired detail level of the generated prompt.",
    )
    response_detail_preference: ResponseDetailPreference = Field(
        default=ResponseDetailPreference.BALANCED,
        description="Desired verbosity of responses produced by the downstream model.",
    )
    reasoning_preference: ReasoningPreference = Field(
        default=ReasoningPreference.AUTO,
        description="Desired reasoning depth guidance embedded in the prompt.",
    )

    @field_validator("raw_prompt")
    @classmethod
    def validate_raw_prompt_not_blank(cls, value: str) -> str:
        """Reject whitespace-only prompts while preserving meaningful formatting."""
        if not value.strip():
            raise ValueError("raw_prompt must not be empty or whitespace-only.")
        return value

    @field_validator("user_context")
    @classmethod
    def normalize_user_context(cls, value: str | None) -> str | None:
        """Convert blank optional context strings to None while keeping multi-line text."""
        if value is None:
            return None
        if not value.strip():
            return None
        return value

    @field_validator("output_language", "task_profile")
    @classmethod
    def normalize_optional_metadata_str(cls, value: str | None) -> str | None:
        """Trim surrounding whitespace on optional metadata tags and normalize blanks to None."""
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return stripped


class PromptRequestRead(BaseModel):
    """Persisted prompt request representation returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    raw_prompt: str
    user_context: str | None
    output_language: str | None
    target_mode: TargetMode
    task_profile: str | None
    prompt_detail_level: PromptDetailLevel
    response_detail_preference: ResponseDetailPreference
    reasoning_preference: ReasoningPreference
    created_at: datetime


class PromptRequestListResponse(BaseModel):
    """Paginated response envelope for saved prompt requests."""

    items: list[PromptRequestRead]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
