"""Tests for Milestone 3C-2 structured JSON logging and sensitive data protection."""

import io
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings, build_sqlite_url
from app.core.logging import (
    APP_LOGGER_NAME,
    REDACTED_VALUE,
    _PromptForgeJsonStreamHandler,
    configure_logging,
    get_logger,
    reset_request_id,
    set_request_id,
)
from app.db.migrations import build_alembic_config
from app.main import create_app


class IsolatedLoggingSettings(Settings):
    """Test settings ignoring any local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


def _parse_json_lines(raw_output: str) -> list[dict[str, object]]:
    lines = [line.strip() for line in raw_output.splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_structured_json_schema_utc_timestamp_and_optional_fields() -> None:
    buffer = io.StringIO()
    configure_logging(level="DEBUG", stream=buffer)
    logger = get_logger("unit")

    # Outside a request context, request_id is None and optional fields are omitted.
    logger.info("Service check completed")

    token = set_request_id("550e8400-e29b-41d4-a716-446655440000")
    try:
        logger.warning(
            "Workflow step warning",
            extra={
                "method": "POST",
                "path": "/api/v1/prompt-requests",
                "status_code": 422,
                "duration_ms": 12.345,
                "error_code": "validation_error",
                "workflow_run_id": "run-123",
                "agent_name": "prompt_engineer",
            },
        )
    finally:
        reset_request_id(token)

    entries = _parse_json_lines(buffer.getvalue())
    assert len(entries) == 2

    first = entries[0]
    assert set(first.keys()) == {"timestamp", "level", "logger", "message", "request_id"}
    first_ts = datetime.fromisoformat(str(first["timestamp"]))
    assert first_ts.tzinfo == UTC
    assert first["level"] == "INFO"
    assert first["logger"] == "promptforge.unit"
    assert first["message"] == "Service check completed"
    assert first["request_id"] is None

    second = entries[1]
    second_ts = datetime.fromisoformat(str(second["timestamp"]))
    assert second_ts.tzinfo == UTC
    assert second["level"] == "WARNING"
    assert second["request_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert second["method"] == "POST"
    assert second["path"] == "/api/v1/prompt-requests"
    assert second["status_code"] == 422
    assert second["duration_ms"] == 12.345
    assert second["error_code"] == "validation_error"
    assert second["workflow_run_id"] == "run-123"
    assert second["agent_name"] == "prompt_engineer"


def test_settings_log_level_normalization_and_validation() -> None:
    lower_settings = IsolatedLoggingSettings.model_validate({"log_level": "debug"})
    assert lower_settings.log_level == "DEBUG"

    with pytest.raises(ValidationError):
        IsolatedLoggingSettings.model_validate({"log_level": "TRACE"})

    with pytest.raises(ValidationError):
        IsolatedLoggingSettings(max_request_body_bytes=0)


def test_repeated_create_app_does_not_accumulate_duplicate_handlers(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db_url = build_sqlite_url(tmp_path / "no_dup_logs.db")
    settings = IsolatedLoggingSettings(database_url=db_url, log_level="INFO")

    app = create_app(settings=settings)
    for _ in range(4):
        app = create_app(settings=settings)

    root_app_logger = logging.getLogger(APP_LOGGER_NAME)
    managed_handlers = [
        handler
        for handler in root_app_logger.handlers
        if isinstance(handler, _PromptForgeJsonStreamHandler)
    ]
    assert len(managed_handlers) == 1

    # Clear any prior stdout output before firing a single HTTP request.
    capsys.readouterr()

    with TestClient(app) as client:
        capsys.readouterr()
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        captured = capsys.readouterr().out

    entries = _parse_json_lines(captured)
    http_entries = [entry for entry in entries if entry.get("logger") == "promptforge.http"]
    assert len(http_entries) == 1
    assert http_entries[0]["request_id"] == resp.headers["X-Request-ID"]
    assert UUID(str(http_entries[0]["request_id"])).version == 4


def test_sensitive_fields_credentials_tokens_and_prompts_are_redacted() -> None:
    buffer = io.StringIO()
    configure_logging(level="INFO", stream=buffer)
    logger = get_logger("security")

    logger.error(
        "Failed connecting to postgresql+asyncpg://admin:UltraSecretDbPass@db.local:5432/app "
        "with Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.SuperSecretToken "
        "and api_key=sk-live-9876543210abcdefSECRET",
        extra={
            "api_key": "raw-api-key-value",
            "password": "PlaintextPassword99",
            "access_token": "tok_secret_value",
            "context": {
                "safe_key": "allowed_metadata",
                "raw_prompt": "CONFIDENTIAL_RAW_PROMPT_TEXT",
                "user_context": "CONFIDENTIAL_USER_CONTEXT_TEXT",
                "authorization": "Bearer nested-secret-jwt",
                "database_url": "postgresql+asyncpg://user:NestedDbSecret@host:5432/db",
            },
        },
    )

    raw_output = buffer.getvalue()
    for forbidden_secret in (
        "UltraSecretDbPass",
        "eyJhbGciOiJIUzI1NiJ9.SuperSecretToken",
        "sk-live-9876543210abcdefSECRET",
        "raw-api-key-value",
        "PlaintextPassword99",
        "tok_secret_value",
        "CONFIDENTIAL_RAW_PROMPT_TEXT",
        "CONFIDENTIAL_USER_CONTEXT_TEXT",
        "nested-secret-jwt",
        "NestedDbSecret",
    ):
        assert forbidden_secret not in raw_output

    entries = _parse_json_lines(raw_output)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["api_key"] == REDACTED_VALUE
    assert entry["password"] == REDACTED_VALUE
    assert entry["access_token"] == REDACTED_VALUE

    nested_ctx = entry["context"]
    assert isinstance(nested_ctx, dict)
    assert nested_ctx["safe_key"] == "allowed_metadata"
    assert "raw_prompt" not in nested_ctx
    assert "user_context" not in nested_ctx
    assert nested_ctx["authorization"] == REDACTED_VALUE
    assert nested_ctx["database_url"] == REDACTED_VALUE


def test_http_request_logs_never_include_raw_prompt_context_headers_or_query_string(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    default_db_guard: Callable[[], None],
) -> None:
    db_file = tmp_path / "log_privacy.db"
    db_url = build_sqlite_url(db_file)
    command.upgrade(build_alembic_config(db_url), "head")

    settings = IsolatedLoggingSettings(database_url=db_url, log_level="INFO")
    app = create_app(settings=settings)

    secret_prompt = "NEVER_LOG_THIS_RAW_PROMPT_991122"
    secret_context = "NEVER_LOG_THIS_USER_CONTEXT_334455"
    secret_query_token = "QUERY_TOKEN_SECRET_778899"
    secret_header_token = "HEADER_BEARER_SECRET_556677"

    with TestClient(app) as client:
        capsys.readouterr()
        resp = client.post(
            f"/api/v1/prompt-requests?token={secret_query_token}&api_key=query_key_111",
            headers={
                "Authorization": f"Bearer {secret_header_token}",
                "X-API-Key": "custom_header_api_key_222",
            },
            json={
                "raw_prompt": secret_prompt,
                "user_context": secret_context,
                "target_mode": "openai",
            },
        )
        assert resp.status_code == 201
        logged_stdout = capsys.readouterr().out

    for sensitive_marker in (
        secret_prompt,
        secret_context,
        secret_query_token,
        "query_key_111",
        secret_header_token,
        "custom_header_api_key_222",
    ):
        assert sensitive_marker not in logged_stdout

    entries = _parse_json_lines(logged_stdout)
    assert len(entries) == 1
    http_entry = entries[0]
    assert http_entry["logger"] == "promptforge.http"
    assert http_entry["method"] == "POST"
    assert http_entry["path"] == "/api/v1/prompt-requests"
    assert http_entry["status_code"] == 201
    assert isinstance(http_entry["duration_ms"], (int, float))
    assert http_entry["request_id"] == resp.headers["X-Request-ID"]
    default_db_guard()
