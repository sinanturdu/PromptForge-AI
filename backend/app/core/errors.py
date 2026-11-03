"""Application error hierarchy and RFC 9457 Problem Details models."""

from collections.abc import Mapping
from http import HTTPStatus
from typing import Final

from pydantic import BaseModel, Field

PROBLEM_JSON_MEDIA_TYPE: Final[str] = "application/problem+json"
DEFAULT_PROBLEM_TYPE: Final[str] = "about:blank"

_RFC9110_STATUS_TITLES: Final[dict[int, str]] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    413: "Content Too Large",
    415: "Unsupported Media Type",
    422: "Unprocessable Content",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def get_status_title(status_code: int) -> str:
    """Return the standard RFC 9110 human-readable title for an HTTP status code."""
    if status_code in _RFC9110_STATUS_TITLES:
        return _RFC9110_STATUS_TITLES[status_code]
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "Application Error"


class ValidationErrorItem(BaseModel):
    """Sanitized field-level validation failure descriptor."""

    field: str = Field(description="Request field or parameter path that failed validation.")
    code: str = Field(description="Machine-readable validation failure code.")
    message: str = Field(description="Human-readable validation failure explanation.")


class ProblemDetails(BaseModel):
    """RFC 9457 Problem Details error response schema."""

    type: str = Field(
        default=DEFAULT_PROBLEM_TYPE,
        description="URI reference identifying the problem type.",
    )
    title: str = Field(description="Short, human-readable summary of the problem type.")
    status: int = Field(description="HTTP status code generated for this problem occurrence.")
    detail: str = Field(description="Human-readable explanation specific to this occurrence.")
    instance: str = Field(description="Request URI path where the problem occurred.")
    request_id: str = Field(description="Server-generated UUID identifying the HTTP request.")
    error_code: str = Field(description="Stable machine-readable application error code.")
    errors: list[ValidationErrorItem] | None = Field(
        default=None,
        description="Sanitized field-level validation errors when status is 422.",
    )


class AppError(Exception):
    """Base class for structured domain and application errors."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        detail: str,
        title: str | None = None,
        problem_type: str = DEFAULT_PROBLEM_TYPE,
        headers: Mapping[str, str] | None = None,
        errors: list[ValidationErrorItem] | None = None,
    ) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.error_code = error_code
        self.detail = detail
        self.title = title if title is not None else get_status_title(status_code)
        self.problem_type = problem_type
        self.headers = dict(headers) if headers is not None else None
        self.errors = errors


class BadRequestError(AppError):
    """Raised when an HTTP request is syntactically invalid or malformed (400)."""

    def __init__(
        self,
        detail: str = "Malformed request.",
        *,
        error_code: str = "bad_request",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=400,
            error_code=error_code,
            detail=detail,
            headers=headers,
        )


class NotFoundError(AppError):
    """Raised when a requested domain resource cannot be found (404)."""

    def __init__(
        self,
        detail: str = "Requested resource was not found.",
        *,
        error_code: str = "resource_not_found",
        headers: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(
            status_code=404,
            error_code=error_code,
            detail=detail,
            headers=headers,
        )


class RequestBodyTooLargeError(AppError):
    """Raised when an incoming HTTP request body exceeds MAX_REQUEST_BODY_BYTES (413)."""

    def __init__(self, *, max_body_bytes: int) -> None:
        self.max_body_bytes = max_body_bytes
        super().__init__(
            status_code=413,
            error_code="request_body_too_large",
            detail=(f"Request body exceeds the maximum allowed size of {max_body_bytes} bytes."),
        )


class DomainValidationError(AppError):
    """Raised when a domain rule rejects semantically invalid input (422)."""

    def __init__(
        self,
        detail: str = "Domain validation failed.",
        *,
        error_code: str = "domain_validation_error",
        errors: list[ValidationErrorItem] | None = None,
    ) -> None:
        super().__init__(
            status_code=422,
            error_code=error_code,
            detail=detail,
            errors=errors,
        )


class InternalApplicationError(AppError):
    """Raised when an internal subsystem fails unexpectedly (500)."""

    def __init__(
        self,
        detail: str = "An unexpected internal error occurred.",
        *,
        error_code: str = "internal_server_error",
    ) -> None:
        super().__init__(
            status_code=500,
            error_code=error_code,
            detail=detail,
        )
