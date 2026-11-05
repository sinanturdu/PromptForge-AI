"""Safe internal errors, independent of HTTP and provider SDK exception objects."""

from enum import StrEnum

from pydantic import Field

from app.llm.schemas import CallMetadata, Identifier, LLMModel


class LLMErrorCode(StrEnum):
    INVALID_CONFIGURATION = "invalid_configuration"
    MISSING_CREDENTIALS = "missing_credentials"
    AUTHENTICATION = "authentication"
    PERMISSION = "permission"
    QUOTA_EXHAUSTED = "quota_exhausted"
    RATE_LIMIT = "rate_limit"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    INVALID_REQUEST = "invalid_request"
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    REFUSAL = "refusal"
    TRUNCATION = "truncation"
    MISSING_TEXT = "missing_text"
    SCRIPT_EXHAUSTED = "script_exhausted"
    PROVIDER_CLOSED = "provider_closed"
    INVALID_RESPONSE = "invalid_response"
    DEADLINE_EXCEEDED = "deadline_exceeded"


_MESSAGES: dict[LLMErrorCode, str] = {
    LLMErrorCode.INVALID_CONFIGURATION: "LLM configuration is unsupported.",
    LLMErrorCode.MISSING_CREDENTIALS: "Required LLM credentials are missing.",
    LLMErrorCode.AUTHENTICATION: "LLM authentication failed.",
    LLMErrorCode.PERMISSION: "LLM access was denied.",
    LLMErrorCode.QUOTA_EXHAUSTED: "LLM quota is exhausted.",
    LLMErrorCode.RATE_LIMIT: "LLM rate limit was reached.",
    LLMErrorCode.TIMEOUT: "LLM request timed out.",
    LLMErrorCode.PROVIDER_UNAVAILABLE: "LLM provider is unavailable.",
    LLMErrorCode.INVALID_REQUEST: "LLM request is unsupported.",
    LLMErrorCode.INVALID_STRUCTURED_OUTPUT: "LLM output does not match the required JSON schema.",
    LLMErrorCode.REFUSAL: "LLM provider refused the request.",
    LLMErrorCode.TRUNCATION: "LLM output was truncated.",
    LLMErrorCode.MISSING_TEXT: "LLM response contains no usable text.",
    LLMErrorCode.SCRIPT_EXHAUSTED: "Fake LLM response script is exhausted.",
    LLMErrorCode.PROVIDER_CLOSED: "LLM provider is closed.",
    LLMErrorCode.INVALID_RESPONSE: "LLM provider returned an invalid response.",
    LLMErrorCode.DEADLINE_EXCEEDED: "LLM operation deadline was exceeded.",
}


class LLMErrorMetadata(LLMModel):
    """Content-free fields for future retry handling; no raw bodies or exceptions."""

    upstream_request_id: Identifier | None = Field(default=None, repr=False)
    http_status: int | None = Field(default=None, ge=100, le=599)
    retry_after_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    call: CallMetadata | None = None


class LLMError(Exception):
    """Validated category, fixed message and content-free metadata only."""

    def __init__(self, code: LLMErrorCode, *, metadata: LLMErrorMetadata | None = None) -> None:
        # Reject untyped callers safely without interpolating their input.
        if not isinstance(code, LLMErrorCode):
            code = LLMErrorCode.INVALID_CONFIGURATION
        self.code = code
        self.metadata = metadata if metadata is not None else LLMErrorMetadata()
        super().__init__(_MESSAGES[code])
