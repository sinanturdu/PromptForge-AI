"""Structured JSON logging, request-context correlation, and sensitive-data redaction."""

import json
import logging
import re
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any, Final, TextIO

APP_LOGGER_NAME: Final[str] = "promptforge"
REQUEST_ID_HEADER: Final[str] = "X-Request-ID"
REDACTED_VALUE: Final[str] = "[REDACTED]"

# Payload/prompt keys that must never have their contents emitted into application logs.
OMITTED_LOG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "raw_prompt",
        "user_context",
        "rendered_prompt",
        "canonical_prompt",
        "request_body",
        "response_body",
        "body",
    }
)

# Credential/secret/header keys whose values must always be redacted if passed in extras.
SENSITIVE_LOG_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "proxy_authorization",
        "cookie",
        "set_cookie",
        "api_key",
        "apikey",
        "x_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "gemini_api_key",
        "google_api_key",
        "access_token",
        "refresh_token",
        "id_token",
        "token",
        "secret",
        "client_secret",
        "password",
        "passwd",
        "pwd",
        "database_url",
        "dsn",
        "connection_string",
        "credentials",
    }
)

_OPTIONAL_STRUCTURED_FIELDS: Final[tuple[str, ...]] = (
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_code",
    "workflow_run_id",
    "agent_name",
    "exception_type",
)

_URL_CREDENTIALS_RE: Final[re.Pattern[str]] = re.compile(
    r"([a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:)([^@\s]+)(@)"
)
_AUTH_SCHEME_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+",
    re.IGNORECASE,
)
_KEY_VALUE_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret)"
    r"(\s*[=:]\s*)([^\s,;\"']+)"
)
_API_KEY_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"\b(?:sk|rk|pk)-[A-Za-z0-9_-]{10,}\b")

_request_id_ctx_var: ContextVar[str | None] = ContextVar("promptforge_request_id", default=None)


def get_request_id() -> str | None:
    """Return the active request ID from the current async execution context, if any."""
    return _request_id_ctx_var.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind a request ID to the current async execution context and return its reset token."""
    return _request_id_ctx_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the request ID context variable using its previously issued token."""
    _request_id_ctx_var.reset(token)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def sanitize_log_text(text: str) -> str:
    """Scrub URL credentials, auth tokens, and inline secrets from a log message."""
    sanitized = _URL_CREDENTIALS_RE.sub(rf"\1{REDACTED_VALUE}\3", text)
    sanitized = _AUTH_SCHEME_RE.sub(rf"\1 {REDACTED_VALUE}", sanitized)
    sanitized = _KEY_VALUE_SECRET_RE.sub(rf"\1\2{REDACTED_VALUE}", sanitized)
    sanitized = _API_KEY_PREFIX_RE.sub(REDACTED_VALUE, sanitized)
    return sanitized


def sanitize_log_value(value: Any, *, key: str | None = None) -> Any:
    """Recursively sanitize structured log values, redacting secrets and dropping prompts."""
    if key is not None:
        normalized_key = _normalize_key(key)
        if normalized_key in OMITTED_LOG_KEYS or normalized_key in SENSITIVE_LOG_KEYS:
            return REDACTED_VALUE

    if isinstance(value, str):
        return sanitize_log_text(value)

    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for item_key, item_val in value.items():
            str_key = str(item_key)
            normalized_item_key = _normalize_key(str_key)
            if normalized_item_key in OMITTED_LOG_KEYS:
                continue
            if normalized_item_key in SENSITIVE_LOG_KEYS:
                cleaned[str_key] = REDACTED_VALUE
            else:
                cleaned[str_key] = sanitize_log_value(item_val, key=str_key)
        return cleaned

    if isinstance(value, (list, tuple)):
        return [sanitize_log_value(item) for item in value]

    return value


class StructuredJsonFormatter(logging.Formatter):
    """Format Python LogRecords as single-line UTC JSON objects with redaction."""

    def format(self, record: logging.LogRecord) -> str:
        created_utc = datetime.fromtimestamp(record.created, tz=UTC)
        explicit_request_id = getattr(record, "request_id", None)
        resolved_request_id = (
            str(explicit_request_id) if explicit_request_id is not None else get_request_id()
        )

        payload: dict[str, Any] = {
            "timestamp": created_utc.isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": sanitize_log_text(record.getMessage()),
            "request_id": resolved_request_id,
        }

        for field_name in _OPTIONAL_STRUCTURED_FIELDS:
            field_value = getattr(record, field_name, None)
            if field_value is not None:
                payload[field_name] = sanitize_log_value(field_value, key=field_name)

        extra_context = getattr(record, "context", None)
        if isinstance(extra_context, dict):
            sanitized_ctx = sanitize_log_value(extra_context)
            if sanitized_ctx:
                payload["context"] = sanitized_ctx

        # Redact any explicitly passed sensitive extra attributes if attached to LogRecord.
        for attr_name in SENSITIVE_LOG_KEYS:
            if hasattr(record, attr_name):
                payload[attr_name] = REDACTED_VALUE

        return json.dumps(payload, ensure_ascii=False, default=str)


class _PromptForgeJsonStreamHandler(logging.StreamHandler[TextIO]):
    """Managed JSON StreamHandler that resolves current sys.stdout when no fixed stream is set."""

    def __init__(self, explicit_stream: TextIO | None = None) -> None:
        self._explicit_stream = explicit_stream
        super().__init__(explicit_stream if explicit_stream is not None else sys.stdout)

    def emit(self, record: logging.LogRecord) -> None:
        if self._explicit_stream is None:
            self.stream = sys.stdout
        super().emit(record)


def configure_logging(
    level: str = "INFO",
    *,
    stream: TextIO | None = None,
) -> logging.Logger:
    """Configure idempotent structured JSON logging for the `promptforge` logger namespace."""
    numeric_level = getattr(logging, level.strip().upper(), None)
    if not isinstance(numeric_level, int):
        raise ValueError(f"Unsupported log level: {level!r}.")

    app_logger = logging.getLogger(APP_LOGGER_NAME)
    app_logger.setLevel(numeric_level)
    app_logger.propagate = False

    # Replace existing managed handlers instead of accumulating duplicates across create_app().
    for existing_handler in list(app_logger.handlers):
        if isinstance(existing_handler, _PromptForgeJsonStreamHandler):
            app_logger.removeHandler(existing_handler)

    handler = _PromptForgeJsonStreamHandler(explicit_stream=stream)
    handler.setLevel(numeric_level)
    handler.setFormatter(StructuredJsonFormatter())
    app_logger.addHandler(handler)
    return app_logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a child logger within the `promptforge` structured logging namespace."""
    if not name or name == APP_LOGGER_NAME:
        return logging.getLogger(APP_LOGGER_NAME)
    if name.startswith(f"{APP_LOGGER_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{APP_LOGGER_NAME}.{name}")
