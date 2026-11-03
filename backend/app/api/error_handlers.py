"""Centralized RFC 9457 Problem Details exception handlers for FastAPI."""

from collections.abc import Mapping, Sequence
from typing import Any, Final
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import (
    DEFAULT_PROBLEM_TYPE,
    PROBLEM_JSON_MEDIA_TYPE,
    AppError,
    ProblemDetails,
    ValidationErrorItem,
    get_status_title,
)
from app.core.logging import (
    REQUEST_ID_HEADER,
    get_logger,
    get_request_id,
    sanitize_log_text,
)

error_logger = get_logger("errors")

_LOCATION_PREFIXES: Final[frozenset[str]] = frozenset({"body", "query", "path", "header", "cookie"})
_SAFE_ERROR_HEADERS: Final[frozenset[str]] = frozenset(
    {
        "allow",
        "cache-control",
        "location",
        "retry-after",
        "vary",
        "www-authenticate",
    }
)
_HTTP_ERROR_CODES: Final[dict[int, str]] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "request_body_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
}


class ProblemJSONResponse(JSONResponse):
    """JSONResponse configured with the RFC 9457 `application/problem+json` media type."""

    media_type = PROBLEM_JSON_MEDIA_TYPE


def resolve_request_id(request: Request) -> str:
    """Resolve the active server-generated request ID from ContextVar or ASGI request state."""
    ctx_request_id = get_request_id()
    if ctx_request_id:
        return ctx_request_id

    state_request_id: object = getattr(request.state, "request_id", None)
    if isinstance(state_request_id, str) and state_request_id:
        return state_request_id

    generated = str(uuid4())
    request.state.request_id = generated
    return generated


def _filter_safe_headers(
    raw_headers: Mapping[str, str] | None,
    *,
    request_id: str,
) -> dict[str, str]:
    headers: dict[str, str] = {}
    if raw_headers:
        for key, value in raw_headers.items():
            if key.lower() in _SAFE_ERROR_HEADERS:
                headers[key] = value
    headers[REQUEST_ID_HEADER] = request_id
    return headers


def build_problem_response(
    request: Request,
    *,
    status_code: int,
    error_code: str,
    detail: str,
    title: str | None = None,
    problem_type: str = DEFAULT_PROBLEM_TYPE,
    headers: Mapping[str, str] | None = None,
    errors: list[ValidationErrorItem] | None = None,
) -> ProblemJSONResponse:
    """Construct a standardized RFC 9457 Problem Details HTTP response."""
    request_id = resolve_request_id(request)
    request.state.error_code = error_code

    problem = ProblemDetails(
        type=problem_type,
        title=title if title is not None else get_status_title(status_code),
        status=status_code,
        detail=detail,
        instance=request.url.path,
        request_id=request_id,
        error_code=error_code,
        errors=errors,
    )
    response_headers = _filter_safe_headers(headers, request_id=request_id)
    return ProblemJSONResponse(
        status_code=status_code,
        content=problem.model_dump(mode="json", exclude_none=True),
        headers=response_headers,
    )


def _format_error_field(loc: Sequence[Any]) -> str:
    if not loc:
        return "request"
    parts = [str(part) for part in loc]
    if len(parts) > 1 and parts[0] in _LOCATION_PREFIXES:
        parts = parts[1:]
    return ".".join(parts)


def _format_validation_message(error_code: str, raw_msg: str, ctx: Mapping[str, Any]) -> str:
    if error_code == "missing":
        return "Field is required."
    if error_code == "extra_forbidden":
        return "Unknown field is not permitted."
    if error_code == "json_invalid":
        return "Malformed JSON request body."
    if error_code == "uuid_parsing":
        return "Input must be a valid UUID."
    if error_code == "string_type":
        return "Input must be a valid string."
    if error_code in {"int_parsing", "int_type"}:
        return "Input must be a valid integer."
    if error_code in {"bool_parsing", "bool_type"}:
        return "Input must be a valid boolean."
    if error_code in {"dict_type", "model_attributes_type", "model_type"}:
        return "Request payload must be a JSON object."

    if error_code == "string_too_long":
        max_len = ctx.get("max_length")
        if isinstance(max_len, int):
            return f"Input exceeds maximum length of {max_len} characters."
        return "Input exceeds the allowed length."

    if error_code == "string_too_short":
        min_len = ctx.get("min_length")
        if isinstance(min_len, int):
            return f"Input must contain at least {min_len} character(s)."
        return "Input is shorter than the minimum allowed length."

    if error_code == "greater_than_equal":
        bound = ctx.get("ge")
        if isinstance(bound, (int, float)):
            return f"Input must be greater than or equal to {bound}."
        return "Input is below the minimum allowed value."

    if error_code == "greater_than":
        bound = ctx.get("gt")
        if isinstance(bound, (int, float)):
            return f"Input must be greater than {bound}."
        return "Input is below the minimum allowed value."

    if error_code == "less_than_equal":
        bound = ctx.get("le")
        if isinstance(bound, (int, float)):
            return f"Input must be less than or equal to {bound}."
        return "Input exceeds the maximum allowed value."

    if error_code == "less_than":
        bound = ctx.get("lt")
        if isinstance(bound, (int, float)):
            return f"Input must be less than {bound}."
        return "Input exceeds the maximum allowed value."

    if error_code in {"enum", "literal_error"}:
        expected = ctx.get("expected")
        if isinstance(expected, str) and expected:
            return f"Input must be one of: {expected}."
        return "Input is not a supported option."

    if error_code == "value_error":
        cleaned = raw_msg.removeprefix("Value error, ").strip()
        if cleaned:
            return sanitize_log_text(cleaned)
        return "Invalid input value."

    return "Invalid input value."


def sanitize_validation_errors(exc: RequestValidationError) -> list[ValidationErrorItem]:
    """Convert FastAPI/Pydantic validation errors into sanitized items without raw inputs."""
    items: list[ValidationErrorItem] = []
    for raw_error in exc.errors():
        loc = raw_error.get("loc", ())
        code = str(raw_error.get("type", "validation_error"))
        raw_msg = str(raw_error.get("msg", ""))
        raw_ctx = raw_error.get("ctx")
        ctx: Mapping[str, Any] = raw_ctx if isinstance(raw_ctx, Mapping) else {}

        items.append(
            ValidationErrorItem(
                field=_format_error_field(loc),
                code=code,
                message=_format_validation_message(code, raw_msg, ctx),
            )
        )
    return items


async def app_error_handler(request: Request, exc: Exception) -> ProblemJSONResponse:
    """Format known domain/application AppError instances as Problem Details."""
    if not isinstance(exc, AppError):
        return await unhandled_exception_handler(request, exc)

    if exc.status_code >= 500:
        request_id = resolve_request_id(request)
        error_logger.error(
            "Internal application error raised",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": exc.status_code,
                "error_code": exc.error_code,
                "exception_type": type(exc).__name__,
            },
        )

    return build_problem_response(
        request,
        status_code=exc.status_code,
        error_code=exc.error_code,
        title=exc.title,
        detail=exc.detail,
        problem_type=exc.problem_type,
        headers=exc.headers,
        errors=exc.errors,
    )


async def validation_exception_handler(
    request: Request,
    exc: Exception,
) -> ProblemJSONResponse:
    """Format FastAPI RequestValidationError as HTTP 422 Problem Details."""
    if not isinstance(exc, RequestValidationError):
        return await unhandled_exception_handler(request, exc)

    sanitized_items = sanitize_validation_errors(exc)
    return build_problem_response(
        request,
        status_code=422,
        error_code="validation_error",
        detail="Request validation failed.",
        errors=sanitized_items,
    )


async def http_exception_handler(
    request: Request,
    exc: Exception,
) -> ProblemJSONResponse:
    """Format Starlette/FastAPI HTTPExceptions (including unknown routes) as Problem Details."""
    if not isinstance(exc, StarletteHTTPException):
        return await unhandled_exception_handler(request, exc)

    # FastAPI wraps exceptions raised while reading streamed request bodies into HTTPException(400).
    if isinstance(exc.__cause__, AppError):
        return await app_error_handler(request, exc.__cause__)

    status_code = exc.status_code
    if status_code >= 500:
        request_id = resolve_request_id(request)
        error_logger.error(
            "HTTP 5xx exception raised",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": status_code,
                "error_code": "internal_server_error",
                "exception_type": type(exc).__name__,
            },
        )
        return build_problem_response(
            request,
            status_code=status_code,
            error_code="internal_server_error",
            detail="An unexpected internal error occurred.",
            headers=exc.headers,
        )

    error_code = _HTTP_ERROR_CODES.get(status_code, f"http_{status_code}")
    if status_code == 404:
        detail = (
            exc.detail
            if isinstance(exc.detail, str) and exc.detail.strip() != "Not Found"
            else "The requested endpoint or resource was not found."
        )
    elif status_code == 405:
        detail = "HTTP method is not allowed for this endpoint."
    elif isinstance(exc.detail, str) and exc.detail.strip():
        detail = sanitize_log_text(exc.detail.strip())
    else:
        detail = get_status_title(status_code)

    return build_problem_response(
        request,
        status_code=status_code,
        error_code=error_code,
        detail=detail,
        headers=exc.headers,
    )


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> ProblemJSONResponse:
    """Log sanitized server error metadata and return a generic HTTP 500 Problem Details body."""
    request_id = resolve_request_id(request)
    is_db_error = isinstance(exc, SQLAlchemyError)
    log_message = (
        "Unhandled database error during request execution"
        if is_db_error
        else "Unhandled exception during request execution"
    )
    error_logger.error(
        log_message,
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": 500,
            "error_code": "internal_server_error",
            "exception_type": type(exc).__name__,
        },
    )
    return build_problem_response(
        request,
        status_code=500,
        error_code="internal_server_error",
        detail="An unexpected internal error occurred.",
    )


def register_exception_handlers(application: FastAPI) -> None:
    """Attach centralized Problem Details exception handlers to a FastAPI application."""
    application.add_exception_handler(AppError, app_error_handler)
    application.add_exception_handler(RequestValidationError, validation_exception_handler)
    application.add_exception_handler(StarletteHTTPException, http_exception_handler)
    application.add_exception_handler(SQLAlchemyError, unhandled_exception_handler)
    application.add_exception_handler(Exception, unhandled_exception_handler)
