"""Integration tests for `/api/v1/prompt-requests` REST API endpoints."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import DEFAULT_SQLITE_DB_PATH, Settings, build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.session import DatabaseSessionManager
from app.main import create_app
from app.models.prompt_request import PromptRequest
from app.modules.prompts.schemas import MAX_RAW_PROMPT_LENGTH


class IsolatedApiSettings(Settings):
    """Test settings ignoring any local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


@pytest.fixture
def anyio_backend() -> str:
    """Run async verification helpers on asyncio."""
    return "asyncio"


@pytest.fixture
def migrated_db_url(tmp_path: Path) -> str:
    """Create and migrate a temporary SQLite database for API tests."""
    db_path = tmp_path / "api_tests" / "prompt_api.db"
    db_url = build_sqlite_url(db_path)
    command.upgrade(build_alembic_config(db_url), "head")
    return db_url


@pytest.fixture
def api_client(migrated_db_url: str) -> Iterator[TestClient]:
    """Provide a TestClient bound to an isolated migrated SQLite database."""
    settings = IsolatedApiSettings(database_url=migrated_db_url)
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


@pytest.mark.anyio
async def test_create_retrieve_and_verify_database_persistence(
    api_client: TestClient,
    migrated_db_url: str,
) -> None:
    payload = {
        "raw_prompt": (
            "Build a professional project management application using React and FastAPI."
        ),
        "user_context": "The application should support multiple projects, tasks and teams.",
        "output_language": "tr",
        "target_mode": "anthropic",
        "task_profile": "software_development",
        "prompt_detail_level": "high",
        "response_detail_preference": "comprehensive",
        "reasoning_preference": "high",
    }

    create_resp = api_client.post("/api/v1/prompt-requests", json=payload)
    assert create_resp.status_code == 201

    created = create_resp.json()
    created_id = UUID(created["id"])
    expected_location = f"/api/v1/prompt-requests/{created_id}"
    assert create_resp.headers["Location"] == expected_location
    assert created["raw_prompt"] == payload["raw_prompt"]
    assert created["user_context"] == payload["user_context"]
    assert created["output_language"] == "tr"
    assert created["target_mode"] == "anthropic"
    assert created["task_profile"] == "software_development"
    assert created["prompt_detail_level"] == "high"
    assert created["response_detail_preference"] == "comprehensive"
    assert created["reasoning_preference"] == "high"
    parsed_created_at = datetime.fromisoformat(created["created_at"])
    assert parsed_created_at.tzinfo is not None

    # Retrieve via the returned Location header URL.
    get_resp = api_client.get(expected_location)
    assert get_resp.status_code == 200
    assert get_resp.json() == created

    # Verify direct SQLite persistence independently of the FastAPI app instance.
    manager = DatabaseSessionManager(migrated_db_url)
    try:
        async with manager.session() as session:
            persisted = await session.get(PromptRequest, created_id)
            assert persisted is not None
            assert persisted.raw_prompt == payload["raw_prompt"]
            assert persisted.target_mode == "anthropic"
            assert persisted.created_at.tzinfo == UTC
    finally:
        await manager.dispose()


def test_default_preferences_optional_normalization_and_max_length_boundary(
    api_client: TestClient,
) -> None:
    minimal_resp = api_client.post(
        "/api/v1/prompt-requests",
        json={"raw_prompt": "Explain event-driven architecture trade-offs."},
    )
    assert minimal_resp.status_code == 201
    minimal_body = minimal_resp.json()
    assert minimal_body["user_context"] is None
    assert minimal_body["output_language"] is None
    assert minimal_body["target_mode"] == "generic"
    assert minimal_body["task_profile"] is None
    assert minimal_body["prompt_detail_level"] == "medium"
    assert minimal_body["response_detail_preference"] == "balanced"
    assert minimal_body["reasoning_preference"] == "auto"

    # Blank optional strings normalize cleanly to None; exact 20,000-char prompt succeeds.
    boundary_prompt = "P" * MAX_RAW_PROMPT_LENGTH
    boundary_resp = api_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": boundary_prompt,
            "user_context": "   \n  ",
            "output_language": "  ",
            "task_profile": "\t ",
        },
    )
    assert boundary_resp.status_code == 201
    boundary_body = boundary_resp.json()
    assert len(boundary_body["raw_prompt"]) == MAX_RAW_PROMPT_LENGTH
    assert boundary_body["user_context"] is None
    assert boundary_body["output_language"] is None
    assert boundary_body["task_profile"] is None


def test_get_missing_prompt_request_returns_404_without_internal_db_details(
    api_client: TestClient,
) -> None:
    missing_id = uuid4()
    resp = api_client.get(f"/api/v1/prompt-requests/{missing_id}")

    assert resp.status_code == 404
    body = resp.json()
    assert body == {"detail": "Prompt request not found."}
    assert "sqlite" not in str(body).lower()
    assert "prompt_requests" not in str(body).lower()


@pytest.mark.parametrize(
    "invalid_payload",
    [
        {},
        {"raw_prompt": ""},
        {"raw_prompt": "     "},
        {"raw_prompt": "\n\t  \r\n"},
        {"raw_prompt": "x" * (MAX_RAW_PROMPT_LENGTH + 1)},
        {"raw_prompt": "Valid prompt", "target_mode": "unsupported_model"},
        {"raw_prompt": "Valid prompt", "target_mode": ""},
        {"raw_prompt": "Valid prompt", "prompt_detail_level": "ultra"},
        {"raw_prompt": "Valid prompt", "response_detail_preference": "verbose"},
        {"raw_prompt": "Valid prompt", "reasoning_preference": "maximum"},
        {"raw_prompt": "Valid prompt", "output_language": "x" * 33},
        {"raw_prompt": "Valid prompt", "task_profile": "x" * 65},
        {"raw_prompt": "Valid prompt", "unknown_extra_field": "rejected"},
    ],
)
def test_create_rejects_invalid_prompts_preferences_and_unknown_fields(
    api_client: TestClient,
    invalid_payload: dict[str, object],
) -> None:
    resp = api_client.post("/api/v1/prompt-requests", json=invalid_payload)
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "malformed_id",
    [
        "not-a-uuid",
        "12345",
        "00000000-0000-0000-0000-00000000000Z",
    ],
)
def test_get_rejects_malformed_uuid_values(
    api_client: TestClient,
    malformed_id: str,
) -> None:
    resp = api_client.get(f"/api/v1/prompt-requests/{malformed_id}")
    assert resp.status_code == 422


@pytest.mark.parametrize(
    "query_params",
    [
        {"page": 0},
        {"page": -2},
        {"page_size": 0},
        {"page_size": 101},
        {"page": "abc"},
    ],
)
def test_list_rejects_invalid_pagination_parameters(
    api_client: TestClient,
    query_params: dict[str, int | str],
) -> None:
    resp = api_client.get("/api/v1/prompt-requests", params=query_params)
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_paginated_listing_and_deterministic_ordering(
    api_client: TestClient,
    migrated_db_url: str,
) -> None:
    empty_resp = api_client.get("/api/v1/prompt-requests")
    assert empty_resp.status_code == 200
    assert empty_resp.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
    }

    # Seed records with controlled timestamps (including tied timestamps) to test ordering.
    t_old = datetime(2026, 7, 1, 8, 0, 0, tzinfo=UTC)
    t_tie = datetime(2026, 7, 1, 9, 0, 0, tzinfo=UTC)
    t_new = datetime(2026, 7, 1, 10, 0, 0, tzinfo=UTC)

    id_1 = UUID("10000000-0000-0000-0000-000000000001")
    id_2 = UUID("10000000-0000-0000-0000-000000000002")
    id_3 = UUID("10000000-0000-0000-0000-000000000003")
    id_old = UUID("10000000-0000-0000-0000-000000000099")
    id_new = UUID("10000000-0000-0000-0000-000000000000")

    manager = DatabaseSessionManager(migrated_db_url)
    try:
        async with manager.session() as session:
            session.add_all(
                [
                    PromptRequest(id=id_1, raw_prompt="Tie 1", created_at=t_tie),
                    PromptRequest(id=id_old, raw_prompt="Oldest", created_at=t_old),
                    PromptRequest(id=id_3, raw_prompt="Tie 3", created_at=t_tie),
                    PromptRequest(id=id_new, raw_prompt="Newest", created_at=t_new),
                    PromptRequest(id=id_2, raw_prompt="Tie 2", created_at=t_tie),
                ]
            )
            await session.commit()
    finally:
        await manager.dispose()

    expected_ids = [str(uid) for uid in (id_new, id_3, id_2, id_1, id_old)]

    page1 = api_client.get("/api/v1/prompt-requests", params={"page": 1, "page_size": 2})
    page2 = api_client.get("/api/v1/prompt-requests", params={"page": 2, "page_size": 2})
    page3 = api_client.get("/api/v1/prompt-requests", params={"page": 3, "page_size": 2})
    page4 = api_client.get("/api/v1/prompt-requests", params={"page": 4, "page_size": 2})

    assert page1.status_code == 200
    assert page2.status_code == 200
    assert page3.status_code == 200
    assert page4.status_code == 200

    p1_json = page1.json()
    p2_json = page2.json()
    p3_json = page3.json()
    p4_json = page4.json()

    assert p1_json["total"] == 5
    assert p1_json["page"] == 1
    assert p1_json["page_size"] == 2
    assert len(p1_json["items"]) == 2
    assert len(p2_json["items"]) == 2
    assert len(p3_json["items"]) == 1
    assert p4_json["items"] == []
    assert p4_json["total"] == 5

    combined_ids = [
        item["id"] for item in (*p1_json["items"], *p2_json["items"], *p3_json["items"])
    ]
    assert combined_ids == expected_ids


def test_transaction_rollback_on_api_write_failure_and_health_preserved(
    api_client: TestClient,
) -> None:
    original_rollback = AsyncSession.rollback
    with (
        patch.object(
            AsyncSession,
            "commit",
            autospec=True,
            side_effect=SQLAlchemyError("simulated write failure"),
        ),
        patch.object(
            AsyncSession,
            "rollback",
            autospec=True,
            side_effect=original_rollback,
        ) as rollback_spy,
    ):
        failed_resp = api_client.post(
            "/api/v1/prompt-requests",
            json={"raw_prompt": "Rollback check prompt."},
        )

    assert failed_resp.status_code == 500
    assert "simulated write failure" not in failed_resp.text
    assert rollback_spy.await_count >= 1

    # Confirm nothing was persisted and subsequent reads/writes still work cleanly.
    list_resp = api_client.get("/api/v1/prompt-requests")
    assert list_resp.status_code == 200
    assert list_resp.json()["total"] == 0

    health_resp = api_client.get("/api/v1/health")
    assert health_resp.status_code == 200
    assert health_resp.json() == {"status": "ok", "service": "PromptForge AI"}

    openapi_resp = api_client.get("/api/v1/openapi.json")
    assert openapi_resp.status_code == 200
    paths = openapi_resp.json()["paths"]
    assert "/api/v1/health" in paths
    assert "/api/v1/prompt-requests" in paths
    assert "/api/v1/prompt-requests/{id}" in paths

    assert not DEFAULT_SQLITE_DB_PATH.exists()
