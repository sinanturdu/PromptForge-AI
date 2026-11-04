"""Safe internal errors, independent of HTTP and provider SDK exception objects."""

from enum import StrEnum


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


_MESSAGES: dict[LLMErrorCode, str] = {
    LLMErrorCode.INVALID_CONFIGURATION: "Unsupported LLM configuration; select the fake provider.",
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
}


class LLMError(Exception):
    """Only a validated category and fixed message; never accept raw exception text."""

    def __init__(self, code: LLMErrorCode) -> None:
        # Reject untyped callers safely without interpolating their input.
        if not isinstance(code, LLMErrorCode):
            code = LLMErrorCode.INVALID_CONFIGURATION
        self.code = code
        super().__init__(_MESSAGES[code])
