"""Tests for Milestone 3C-2 RFC 9457 Problem Details error handling and OpenAPI schemas."""

from collections.abc import Callable, Iterator
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, build_sqlite_url
from app.core.errors import (
    PROBLEM_JSON_MEDIA_TYPE,
    BadRequestError,
    DomainValidationError,
    InternalApplicationError,
    ValidationErrorItem,
)
from app.core.logging import REQUEST_ID_HEADER
from app.db.migrations import build_alembic_config
from app.main import create_app
from app.modules.prompts.schemas import MAX_RAW_PROMPT_LENGTH


class IsolatedErrorSettings(Settings):
    """Test settings ignoring any local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


@pytest.fixture
def migrated_db_url(tmp_path: Path) -> str:
    """Create and migrate a temporary SQLite database for error-handling tests."""
    db_path = tmp_path / "err_tests" / "errors.db"
    db_url = build_sqlite_url(db_path)
    command.upgrade(build_alembic_config(db_url), "head")
    return db_url


@pytest.fixture
def error_test_client(migrated_db_url: str) -> Iterator[TestClient]:
    """Provide a TestClient with test routes exercising domain and unexpected errors."""
    settings = IsolatedErrorSettings(database_url=migrated_db_url)
    app = create_app(settings=settings)

    @app.get("/_test/bad-request")
    async def trigger_bad_request() -> None:
        raise BadRequestError("Unsupported query combination.", error_code="invalid_combination")

    @app.get("/_test/domain-validation")
    async def trigger_domain_validation() -> None:
        raise DomainValidationError(
            "Task profile conflicts with target mode.",
            errors=[
                ValidationErrorItem(
                    field="task_profile",
                    code="incompatible_profile",
                    message="Selected profile cannot be paired with this mode.",
                )
            ],
        )

    @app.get("/_test/internal-app-error")
    async def trigger_internal_app_error() -> None:
        raise InternalApplicationError()

    @app.get("/_test/unhandled-runtime-error")
    async def trigger_runtime_error() -> None:
        raise RuntimeError(
            "Traceback secret: C:/secrets/private_key.pem password=SuperSecretInternal"
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def _assert_problem_envelope(
    resp_json: dict[str, object],
    *,
    expected_status: int,
    expected_title: str,
    expected_instance: str,
    expected_request_id: str,
    expected_error_code: str,
) -> None:
    assert resp_json["type"] == "about:blank"
    assert resp_json["title"] == expected_title
    assert resp_json["status"] == expected_status
    assert resp_json["instance"] == expected_instance
    assert resp_json["request_id"] == expected_request_id
    assert resp_json["error_code"] == expected_error_code
    assert isinstance(resp_json["detail"], str)
    assert len(resp_json["detail"]) > 0


def test_not_found_unknown_route_and_method_not_allowed_problem_details(
    error_test_client: TestClient,
) -> None:
    # 1. Missing domain record -> 404 Problem Details.
    missing_id = uuid4()
    missing_path = f"/api/v1/prompt-requests/{missing_id}"
    missing_resp = error_test_client.get(missing_path)
    assert missing_resp.status_code == 404
    assert missing_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    _assert_problem_envelope(
        missing_resp.json(),
        expected_status=404,
        expected_title="Not Found",
        expected_instance=missing_path,
        expected_request_id=missing_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="prompt_request_not_found",
    )
    assert missing_resp.json()["detail"] == "Prompt request not found."

    # 2. Unknown route -> 404 Problem Details.
    unknown_resp = error_test_client.get("/api/v1/non-existent-route")
    assert unknown_resp.status_code == 404
    assert unknown_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    _assert_problem_envelope(
        unknown_resp.json(),
        expected_status=404,
        expected_title="Not Found",
        expected_instance="/api/v1/non-existent-route",
        expected_request_id=unknown_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="not_found",
    )

    # 3. Unsupported HTTP method -> 405 Problem Details preserving safe `Allow` header.
    method_resp = error_test_client.delete("/api/v1/health")
    assert method_resp.status_code == 405
    assert method_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    assert "GET" in method_resp.headers.get("Allow", "")
    _assert_problem_envelope(
        method_resp.json(),
        expected_status=405,
        expected_title="Method Not Allowed",
        expected_instance="/api/v1/health",
        expected_request_id=method_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="method_not_allowed",
    )


def test_validation_errors_are_sanitized_and_never_echo_raw_prompts_or_inputs(
    error_test_client: TestClient,
) -> None:
    # 1. Malformed UUID path parameter -> 422 Problem Details without echoing raw value.
    bad_uuid_value = "malformed-secret-uuid-999"
    uuid_path = f"/api/v1/prompt-requests/{bad_uuid_value}"
    uuid_resp = error_test_client.get(uuid_path)
    assert uuid_resp.status_code == 422
    assert uuid_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    uuid_body = uuid_resp.json()
    _assert_problem_envelope(
        uuid_body,
        expected_status=422,
        expected_title="Unprocessable Content",
        expected_instance=uuid_path,
        expected_request_id=uuid_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="validation_error",
    )
    assert uuid_body["errors"] == [
        {
            "field": "id",
            "code": "uuid_parsing",
            "message": "Input must be a valid UUID.",
        }
    ]
    assert bad_uuid_value not in str(uuid_body["errors"])

    # 2. Multiple simultaneous body validation errors -> all reported, zero raw input echoed.
    sensitive_prompt_marker = "CONFIDENTIAL_PROMPT_BODY_MARKER_"
    oversized_prompt = sensitive_prompt_marker + ("Z" * (MAX_RAW_PROMPT_LENGTH + 50))
    invalid_resp = error_test_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": oversized_prompt,
            "target_mode": "invalid_vendor_secret_name",
            "unexpected_secret_field": "TOP_SECRET_EXTRA_VALUE",
        },
    )
    assert invalid_resp.status_code == 422
    assert invalid_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    invalid_body = invalid_resp.json()
    _assert_problem_envelope(
        invalid_body,
        expected_status=422,
        expected_title="Unprocessable Content",
        expected_instance="/api/v1/prompt-requests",
        expected_request_id=invalid_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="validation_error",
    )

    errors_by_field = {item["field"]: item for item in invalid_body["errors"]}
    assert set(errors_by_field.keys()) == {
        "raw_prompt",
        "target_mode",
        "unexpected_secret_field",
    }
    assert errors_by_field["raw_prompt"]["code"] == "string_too_long"
    assert errors_by_field["target_mode"]["code"] == "enum"
    assert errors_by_field["unexpected_secret_field"]["code"] == "extra_forbidden"

    serialized_response = invalid_resp.text
    assert sensitive_prompt_marker not in serialized_response
    assert "invalid_vendor_secret_name" not in serialized_response
    assert "TOP_SECRET_EXTRA_VALUE" not in serialized_response
    assert '"input"' not in serialized_response
    assert '"ctx"' not in serialized_response

    # 3. Malformed JSON syntax -> 422 Problem Details without parser internals.
    malformed_json_resp = error_test_client.post(
        "/api/v1/prompt-requests",
        content=b'{"raw_prompt": "unterminated',
        headers={"Content-Type": "application/json"},
    )
    assert malformed_json_resp.status_code == 422
    assert malformed_json_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    malformed_errors = malformed_json_resp.json()["errors"]
    assert malformed_errors[0]["code"] == "json_invalid"
    assert malformed_errors[0]["message"] == "Malformed JSON request body."
    assert "unterminated" not in malformed_json_resp.text


def test_app_error_hierarchy_and_sanitized_500_for_unexpected_and_db_failures(
    error_test_client: TestClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 1. BadRequestError -> 400 Problem Details.
    bad_req = error_test_client.get("/_test/bad-request")
    assert bad_req.status_code == 400
    assert bad_req.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    _assert_problem_envelope(
        bad_req.json(),
        expected_status=400,
        expected_title="Bad Request",
        expected_instance="/_test/bad-request",
        expected_request_id=bad_req.headers[REQUEST_ID_HEADER],
        expected_error_code="invalid_combination",
    )

    # 2. DomainValidationError -> 422 Problem Details with structured errors list.
    dom_val = error_test_client.get("/_test/domain-validation")
    assert dom_val.status_code == 422
    assert dom_val.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    assert dom_val.json()["error_code"] == "domain_validation_error"
    assert len(dom_val.json()["errors"]) == 1

    # 3. InternalApplicationError -> 500 Problem Details.
    internal_err = error_test_client.get("/_test/internal-app-error")
    assert internal_err.status_code == 500
    assert internal_err.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    assert internal_err.json()["error_code"] == "internal_server_error"

    # 4. Unexpected RuntimeError -> sanitized 500 without stack trace or secrets.
    capsys.readouterr()
    runtime_resp = error_test_client.get("/_test/unhandled-runtime-error")
    runtime_logs = capsys.readouterr().out
    assert runtime_resp.status_code == 500
    assert runtime_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    _assert_problem_envelope(
        runtime_resp.json(),
        expected_status=500,
        expected_title="Internal Server Error",
        expected_instance="/_test/unhandled-runtime-error",
        expected_request_id=runtime_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="internal_server_error",
    )
    assert runtime_resp.json()["detail"] == "An unexpected internal error occurred."
    assert "private_key.pem" not in runtime_resp.text
    assert "SuperSecretInternal" not in runtime_resp.text
    assert "Traceback" not in runtime_resp.text
    assert "SuperSecretInternal" not in runtime_logs

    # 5. Database OperationalError -> transaction rolled back, SQL & credentials never exposed.
    original_rollback = AsyncSession.rollback
    sensitive_db_err = OperationalError(
        "INSERT INTO prompt_requests (raw_prompt) VALUES ('secret')",
        {"raw_prompt": "secret"},
        Exception("postgresql+asyncpg://db_admin:DbMasterSecret99@10.0.0.12:5432/prod"),
    )
    with (
        patch.object(
            AsyncSession,
            "commit",
            autospec=True,
            side_effect=sensitive_db_err,
        ),
        patch.object(
            AsyncSession,
            "rollback",
            autospec=True,
            side_effect=original_rollback,
        ) as rollback_spy,
    ):
        capsys.readouterr()
        db_resp = error_test_client.post(
            "/api/v1/prompt-requests",
            json={"raw_prompt": "Database error sanitization check."},
        )
        db_logs = capsys.readouterr().out

    assert db_resp.status_code == 500
    assert db_resp.headers["content-type"].startswith(PROBLEM_JSON_MEDIA_TYPE)
    _assert_problem_envelope(
        db_resp.json(),
        expected_status=500,
        expected_title="Internal Server Error",
        expected_instance="/api/v1/prompt-requests",
        expected_request_id=db_resp.headers[REQUEST_ID_HEADER],
        expected_error_code="internal_server_error",
    )
    assert rollback_spy.await_count >= 1
    for forbidden in ("INSERT INTO", "prompt_requests", "DbMasterSecret99", "10.0.0.12"):
        assert forbidden not in db_resp.text
    assert "DbMasterSecret99" not in db_logs


def test_openapi_schema_and_docs_accurately_represent_problem_details(
    error_test_client: TestClient,
    default_db_guard: Callable[[], None],
) -> None:
    docs_resp = error_test_client.get("/docs")
    assert docs_resp.status_code == 200
    assert "Swagger UI" in docs_resp.text

    openapi_resp = error_test_client.get("/api/v1/openapi.json")
    assert openapi_resp.status_code == 200
    schema = openapi_resp.json()

    component_schemas = schema["components"]["schemas"]
    assert "ProblemDetails" in component_schemas
    assert "ValidationErrorItem" in component_schemas
    assert "HTTPValidationError" not in component_schemas

    post_responses = schema["paths"]["/api/v1/prompt-requests"]["post"]["responses"]
    for status_key in ("400", "413", "422", "500"):
        assert PROBLEM_JSON_MEDIA_TYPE in post_responses[status_key]["content"]

    get_by_id_responses = schema["paths"]["/api/v1/prompt-requests/{id}"]["get"]["responses"]
    for status_key in ("404", "422", "500"):
        assert PROBLEM_JSON_MEDIA_TYPE in get_by_id_responses[status_key]["content"]

    default_db_guard()
