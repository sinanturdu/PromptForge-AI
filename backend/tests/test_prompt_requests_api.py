"""Integration tests for `/api/v1/prompt-requests` REST API endpoints."""

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.session import DatabaseSessionManager
from app.main import create_app
from app.models.enums import TargetMode
from app.models.prompt_request import PromptRequest
from app.modules.prompts.schemas import MAX_RAW_PROMPT_LENGTH, MAX_SEARCH_QUERY_LENGTH


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
    target_path = f"/api/v1/prompt-requests/{missing_id}"
    resp = api_client.get(target_path)

    assert resp.status_code == 404
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == "about:blank"
    assert body["title"] == "Not Found"
    assert body["status"] == 404
    assert body["detail"] == "Prompt request not found."
    assert body["instance"] == target_path
    assert body["error_code"] == "prompt_request_not_found"
    assert body["request_id"] == resp.headers["X-Request-ID"]
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
    default_db_guard: Callable[[], None],
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

    default_db_guard()


@pytest.mark.anyio
async def test_search_and_target_mode_filtering_pagination_ordering_and_immutability(
    api_client: TestClient,
    migrated_db_url: str,
    default_db_guard: Callable[[], None],
) -> None:
    id_gen_1 = UUID("20000000-0000-0000-0000-000000000001")
    id_oai_1 = UUID("20000000-0000-0000-0000-000000000002")
    id_ant_tie_low = UUID("20000000-0000-0000-0000-000000000003")
    id_ant_tie_high = UUID("20000000-0000-0000-0000-000000000004")
    id_ant_ctx = UUID("20000000-0000-0000-0000-000000000005")
    id_goo_1 = UUID("20000000-0000-0000-0000-000000000006")
    id_ant_newest = UUID("20000000-0000-0000-0000-000000000007")
    id_oai_2 = UUID("20000000-0000-0000-0000-000000000008")

    t_1 = datetime(2026, 9, 1, 8, 0, 0, tzinfo=UTC)
    t_2 = datetime(2026, 9, 1, 9, 0, 0, tzinfo=UTC)
    t_tie = datetime(2026, 9, 1, 10, 0, 0, tzinfo=UTC)
    t_4 = datetime(2026, 9, 1, 11, 0, 0, tzinfo=UTC)
    t_5 = datetime(2026, 9, 1, 12, 0, 0, tzinfo=UTC)
    t_6 = datetime(2026, 9, 1, 13, 0, 0, tzinfo=UTC)
    t_7 = datetime(2026, 9, 1, 14, 0, 0, tzinfo=UTC)

    seeded_rows = [
        PromptRequest(
            id=id_gen_1,
            raw_prompt="Design a modular event bus for distributed observability.",
            user_context=None,
            target_mode=TargetMode.GENERIC.value,
            created_at=t_1,
        ),
        PromptRequest(
            id=id_oai_1,
            raw_prompt="Build a React dashboard with chart widgets.",
            user_context="TypeScript strict frontend.",
            target_mode=TargetMode.OPENAI.value,
            created_at=t_2,
        ),
        PromptRequest(
            id=id_ant_tie_low,
            raw_prompt="Implement a React state synchronization hook.",
            user_context=None,
            target_mode=TargetMode.ANTHROPIC.value,
            created_at=t_tie,
        ),
        PromptRequest(
            id=id_ant_tie_high,
            raw_prompt="Optimize REACT router query parameter handling.",
            user_context="Must support deep-linking.",
            target_mode=TargetMode.ANTHROPIC.value,
            created_at=t_tie,
        ),
        PromptRequest(
            id=id_ant_ctx,
            raw_prompt="Write unit tests for URL state helpers.",
            user_context="Frontend stack uses react 19 and TanStack Query.",
            target_mode=TargetMode.ANTHROPIC.value,
            created_at=t_4,
        ),
        PromptRequest(
            id=id_goo_1,
            raw_prompt="Summarize OpenTelemetry observability collector architecture.",
            user_context="Kubernetes deployment context.",
            target_mode=TargetMode.GOOGLE.value,
            created_at=t_5,
        ),
        PromptRequest(
            id=id_ant_newest,
            raw_prompt="Audit React component accessibility and keyboard focus.",
            user_context=None,
            target_mode=TargetMode.ANTHROPIC.value,
            created_at=t_6,
        ),
        PromptRequest(
            id=id_oai_2,
            raw_prompt="Draft API gateway rate-limiting specification.",
            user_context="UniqueContextKeywordForLookup in backend microservices.",
            target_mode=TargetMode.OPENAI.value,
            created_at=t_7,
        ),
    ]

    manager = DatabaseSessionManager(migrated_db_url)
    try:
        async with manager.session() as session:
            session.add_all(seeded_rows)
            await session.commit()

        async with manager.session() as snapshot_session:
            before_snapshot = [
                (row.id, row.raw_prompt, row.user_context, row.target_mode, row.created_at)
                for row in (
                    await snapshot_session.execute(
                        select(PromptRequest).order_by(PromptRequest.id.asc())
                    )
                )
                .scalars()
                .all()
            ]
    finally:
        await manager.dispose()

    # 1. Backward-compatible unfiltered call preserves exact envelope and total count.
    unfiltered = api_client.get("/api/v1/prompt-requests", params={"page": 1, "page_size": 10})
    assert unfiltered.status_code == 200
    unfiltered_body = unfiltered.json()
    assert set(unfiltered_body.keys()) == {"items", "total", "page", "page_size"}
    assert unfiltered_body["total"] == 8
    assert unfiltered_body["page"] == 1
    assert unfiltered_body["page_size"] == 10
    assert len(unfiltered_body["items"]) == 8

    # 2. Empty and whitespace-only q behave identically to no search filter.
    for blank_q in ("", "   ", "\t  \n"):
        blank_resp = api_client.get(
            "/api/v1/prompt-requests",
            params={"page": 1, "page_size": 10, "q": blank_q},
        )
        assert blank_resp.status_code == 200
        assert blank_resp.json() == unfiltered_body

    # 3. Search by raw_prompt, search by user_context (OR logic), partial & case-insensitive match.
    ctx_only_resp = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": "uniquecontextkeywordforlookup"},
    )
    assert ctx_only_resp.status_code == 200
    assert ctx_only_resp.json()["total"] == 1
    assert [item["id"] for item in ctx_only_resp.json()["items"]] == [str(id_oai_2)]

    partial_resp = api_client.get("/api/v1/prompt-requests", params={"q": "  OBSERV  "})
    assert partial_resp.status_code == 200
    assert partial_resp.json()["total"] == 2
    assert [item["id"] for item in partial_resp.json()["items"]] == [
        str(id_goo_1),
        str(id_gen_1),
    ]

    # Case-insensitive + whitespace-trimmed search across BOTH raw_prompt and user_context.
    for react_variant in ("react", "REACT", "ReAcT", "   react   "):
        react_resp = api_client.get("/api/v1/prompt-requests", params={"q": react_variant})
        assert react_resp.status_code == 200
        react_body = react_resp.json()
        assert react_body["total"] == 5
        assert [item["id"] for item in react_body["items"]] == [
            str(id_ant_newest),
            str(id_ant_ctx),
            str(id_ant_tie_high),
            str(id_ant_tie_low),
            str(id_oai_1),
        ]

    # 4. Individual target_mode filtering for generic, openai, anthropic, and google.
    expected_by_mode = {
        "generic": [str(id_gen_1)],
        "openai": [str(id_oai_2), str(id_oai_1)],
        "anthropic": [
            str(id_ant_newest),
            str(id_ant_ctx),
            str(id_ant_tie_high),
            str(id_ant_tie_low),
        ],
        "google": [str(id_goo_1)],
    }
    for mode_value, expected_mode_ids in expected_by_mode.items():
        mode_resp = api_client.get(
            "/api/v1/prompt-requests",
            params={"target_mode": mode_value, "page": 1, "page_size": 10},
        )
        assert mode_resp.status_code == 200
        mode_json = mode_resp.json()
        assert mode_json["total"] == len(expected_mode_ids)
        assert [item["id"] for item in mode_json["items"]] == expected_mode_ids

    # 5. Combined keyword + target_mode with multi-page filtered pagination and tie-breaker order.
    expected_filtered_order = [
        str(id_ant_newest),
        str(id_ant_ctx),
        str(id_ant_tie_high),
        str(id_ant_tie_low),
    ]
    fp1 = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": "react", "target_mode": "anthropic", "page": 1, "page_size": 2},
    )
    fp2 = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": "react", "target_mode": "anthropic", "page": 2, "page_size": 2},
    )
    fp3 = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": "react", "target_mode": "anthropic", "page": 3, "page_size": 2},
    )
    assert fp1.status_code == 200
    assert fp2.status_code == 200
    assert fp3.status_code == 200

    assert fp1.json()["total"] == 4
    assert fp2.json()["total"] == 4
    assert fp3.json()["total"] == 4
    assert fp3.json()["items"] == []
    assert [
        item["id"] for item in (*fp1.json()["items"], *fp2.json()["items"])
    ] == expected_filtered_order

    # Zero-match search returns empty items and total=0 without errors, including SQL-like strings.
    zero_resp = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": "'; DROP TABLE prompt_requests; --", "target_mode": "google"},
    )
    assert zero_resp.status_code == 200
    assert zero_resp.json() == {
        "items": [],
        "total": 0,
        "page": 1,
        "page_size": 20,
    }

    # 6. Verify stored database rows were never mutated by any search or filter query.
    verify_manager = DatabaseSessionManager(migrated_db_url)
    try:
        async with verify_manager.session() as verify_session:
            after_snapshot = [
                (row.id, row.raw_prompt, row.user_context, row.target_mode, row.created_at)
                for row in (
                    await verify_session.execute(
                        select(PromptRequest).order_by(PromptRequest.id.asc())
                    )
                )
                .scalars()
                .all()
            ]
    finally:
        await verify_manager.dispose()

    assert after_snapshot == before_snapshot

    # 7. Existing POST creation and GET-by-ID continue working seamlessly alongside filters.
    post_resp = api_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": "Create a Gemini prompt for log anomaly analysis.",
            "target_mode": "google",
        },
    )
    assert post_resp.status_code == 201
    created_id = post_resp.json()["id"]

    get_by_id_resp = api_client.get(f"/api/v1/prompt-requests/{created_id}")
    assert get_by_id_resp.status_code == 200
    assert get_by_id_resp.json()["id"] == created_id

    updated_google = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": "anomaly", "target_mode": "google"},
    )
    assert updated_google.status_code == 200
    assert updated_google.json()["total"] == 1
    assert updated_google.json()["items"][0]["id"] == created_id

    default_db_guard()


def test_search_literal_like_wildcards_and_backslashes_via_http_api(
    api_client: TestClient,
) -> None:
    pct_create = api_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": "Ensure 100% deterministic score calculation.",
            "target_mode": "generic",
        },
    )
    non_pct_create = api_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": "Ensure 1000 deterministic score samples.",
            "user_context": "Field name is task_profile in schema.",
            "target_mode": "openai",
        },
    )
    non_underscore_create = api_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": "Field name is taskXprofile in legacy payload.",
            "user_context": r"Local path C:\Users\dev\PromptForge-AI\backend",
            "target_mode": "anthropic",
        },
    )
    non_backslash_create = api_client.post(
        "/api/v1/prompt-requests",
        json={
            "raw_prompt": "Local path C:UsersdevPromptForge-AIbackend without separators.",
            "target_mode": "google",
        },
    )

    assert pct_create.status_code == 201
    assert non_pct_create.status_code == 201
    assert non_underscore_create.status_code == 201
    assert non_backslash_create.status_code == 201

    pct_resp = api_client.get("/api/v1/prompt-requests", params={"q": "100%"})
    assert pct_resp.status_code == 200
    assert pct_resp.json()["total"] == 1
    assert pct_resp.json()["items"][0]["id"] == pct_create.json()["id"]

    # Standalone "%" must NOT match all 4 records unless literal "%" exists in text.
    only_pct_resp = api_client.get("/api/v1/prompt-requests", params={"q": "%"})
    assert only_pct_resp.status_code == 200
    assert only_pct_resp.json()["total"] == 1
    assert only_pct_resp.json()["items"][0]["id"] == pct_create.json()["id"]

    underscore_resp = api_client.get("/api/v1/prompt-requests", params={"q": "task_profile"})
    assert underscore_resp.status_code == 200
    assert underscore_resp.json()["total"] == 1
    assert underscore_resp.json()["items"][0]["id"] == non_pct_create.json()["id"]

    backslash_resp = api_client.get(
        "/api/v1/prompt-requests",
        params={"q": r"C:\Users\dev"},
    )
    assert backslash_resp.status_code == 200
    assert backslash_resp.json()["total"] == 1
    assert backslash_resp.json()["items"][0]["id"] == non_underscore_create.json()["id"]


@pytest.mark.parametrize(
    "invalid_target_mode",
    [
        "unsupported",
        "all",
        "",
        "OPENAI",
        "claude",
    ],
)
def test_list_rejects_invalid_target_mode_with_safe_problem_details(
    api_client: TestClient,
    invalid_target_mode: str,
) -> None:
    resp = api_client.get(
        "/api/v1/prompt-requests",
        params={"target_mode": invalid_target_mode},
    )
    assert resp.status_code == 422
    assert resp.headers["content-type"].startswith("application/problem+json")

    body = resp.json()
    assert body["status"] == 422
    assert body["error_code"] == "validation_error"
    assert body["request_id"] == resp.headers["X-Request-ID"]
    assert any(err["field"] == "target_mode" for err in body["errors"])
    assert "sqlite" not in resp.text.lower()
    assert "traceback" not in resp.text.lower()


def test_list_search_query_max_length_boundary_and_openapi_documentation(
    api_client: TestClient,
) -> None:
    # Exact 200-character query is valid and returns HTTP 200.
    exact_max_q = "k" * MAX_SEARCH_QUERY_LENGTH
    ok_resp = api_client.get("/api/v1/prompt-requests", params={"q": exact_max_q})
    assert ok_resp.status_code == 200
    assert ok_resp.json()["total"] == 0

    # 201-character query is rejected with HTTP 422 Problem Details without echoing raw input.
    oversized_marker = "Z" * (MAX_SEARCH_QUERY_LENGTH + 1)
    too_long_resp = api_client.get("/api/v1/prompt-requests", params={"q": oversized_marker})
    assert too_long_resp.status_code == 422
    assert too_long_resp.headers["content-type"].startswith("application/problem+json")

    body = too_long_resp.json()
    assert body["status"] == 422
    assert body["error_code"] == "validation_error"
    assert body["request_id"] == too_long_resp.headers["X-Request-ID"]
    assert any(err["field"] == "q" and err["code"] == "string_too_long" for err in body["errors"])
    assert oversized_marker not in too_long_resp.text
    assert "sqlite" not in too_long_resp.text.lower()
    assert "select " not in too_long_resp.text.lower()

    # Verify OpenAPI schema documents both `q` and `target_mode` query parameters on GET list.
    openapi_resp = api_client.get("/api/v1/openapi.json")
    assert openapi_resp.status_code == 200
    list_op = openapi_resp.json()["paths"]["/api/v1/prompt-requests"]["get"]
    params_by_name = {param["name"]: param for param in list_op["parameters"]}

    assert {"page", "page_size", "q", "target_mode"}.issubset(params_by_name.keys())
    assert params_by_name["q"]["in"] == "query"
    assert params_by_name["q"]["required"] is False
    assert str(MAX_SEARCH_QUERY_LENGTH) in str(params_by_name["q"]["schema"])
    assert params_by_name["target_mode"]["in"] == "query"
    assert params_by_name["target_mode"]["required"] is False
