"""Tests for Milestone 3C-2 Request ID tracing and HTTP body size limit middlewares."""

import asyncio
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from alembic import command
from fastapi import Request
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings, build_sqlite_url
from app.core.logging import REQUEST_ID_HEADER, get_request_id
from app.db.migrations import build_alembic_config
from app.main import create_app
from app.modules.prompts.schemas import MAX_RAW_PROMPT_LENGTH, MAX_USER_CONTEXT_LENGTH


class IsolatedMiddlewareSettings(Settings):
    """Test settings ignoring any local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


@pytest.fixture
def anyio_backend() -> str:
    """Run async HTTP concurrency and streaming tests on asyncio."""
    return "asyncio"


@pytest.fixture
def migrated_db_url(tmp_path: Path) -> str:
    """Create and migrate an isolated SQLite database for middleware tests."""
    db_path = tmp_path / "mw_tests" / "middleware.db"
    db_url = build_sqlite_url(db_path)
    command.upgrade(build_alembic_config(db_url), "head")
    return db_url


def test_request_id_present_on_success_and_error_responses_and_ignores_client_header(
    migrated_db_url: str,
) -> None:
    settings = IsolatedMiddlewareSettings(database_url=migrated_db_url)
    app = create_app(settings=settings)

    @app.get("/_test/boom")
    async def boom_endpoint() -> dict[str, str]:
        raise RuntimeError("unexpected boom")

    seen_ids: set[str] = set()
    with TestClient(app, raise_server_exceptions=False) as client:
        # 1. Health endpoint preserves exact body and adds server-generated X-Request-ID.
        forged_id = "client-supplied-untrusted-id"
        health_resp = client.get("/api/v1/health", headers={REQUEST_ID_HEADER: forged_id})
        assert health_resp.status_code == 200
        assert health_resp.json() == {"status": "ok", "service": "PromptForge AI"}
        health_rid = health_resp.headers[REQUEST_ID_HEADER]
        assert health_rid != forged_id
        assert UUID(health_rid).version == 4
        seen_ids.add(health_rid)

        # 2. Successful POST and GET endpoints receive distinct UUIDs.
        post_resp = client.post(
            "/api/v1/prompt-requests",
            json={"raw_prompt": "Verify request tracing header."},
        )
        assert post_resp.status_code == 201
        post_rid = post_resp.headers[REQUEST_ID_HEADER]
        assert UUID(post_rid).version == 4
        assert post_rid not in seen_ids
        seen_ids.add(post_rid)

        # 3. 422 validation error includes matching X-Request-ID header and body field.
        val_resp = client.post("/api/v1/prompt-requests", json={"raw_prompt": "   "})
        assert val_resp.status_code == 422
        val_rid = val_resp.headers[REQUEST_ID_HEADER]
        assert UUID(val_rid).version == 4
        assert val_resp.json()["request_id"] == val_rid
        assert val_rid not in seen_ids
        seen_ids.add(val_rid)

        # 4. 404 unknown route includes matching X-Request-ID header and body field.
        nf_resp = client.get("/api/v1/unknown-path")
        assert nf_resp.status_code == 404
        nf_rid = nf_resp.headers[REQUEST_ID_HEADER]
        assert UUID(nf_rid).version == 4
        assert nf_resp.json()["request_id"] == nf_rid
        assert nf_rid not in seen_ids
        seen_ids.add(nf_rid)

        # 5. 500 unhandled server error includes matching X-Request-ID header and body field.
        err_resp = client.get("/_test/boom")
        assert err_resp.status_code == 500
        err_rid = err_resp.headers[REQUEST_ID_HEADER]
        assert UUID(err_rid).version == 4
        assert err_resp.json()["request_id"] == err_rid
        assert err_rid not in seen_ids

    # ContextVar must be cleaned up outside active requests.
    assert get_request_id() is None


@pytest.mark.anyio
async def test_concurrent_requests_maintain_strict_request_id_isolation(
    migrated_db_url: str,
) -> None:
    settings = IsolatedMiddlewareSettings(database_url=migrated_db_url)
    app = create_app(settings=settings)

    @app.get("/_test/concurrent/{slot}")
    async def concurrent_probe(slot: int, request: Request) -> dict[str, str | int | None]:
        before_sleep = get_request_id()
        # Stagger wake-up order so tasks interleave across the event loop.
        await asyncio.sleep(0.005 * ((12 - slot) % 5))
        after_sleep = get_request_id()
        state_id: str = request.state.request_id
        return {
            "slot": slot,
            "before": before_sleep,
            "after": after_sleep,
            "state": state_id,
        }

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        responses = await asyncio.gather(
            *(client.get(f"/_test/concurrent/{idx}") for idx in range(12))
        )

    returned_ids: set[str] = set()
    for idx, resp in enumerate(responses):
        assert resp.status_code == 200
        header_rid = resp.headers[REQUEST_ID_HEADER]
        assert UUID(header_rid).version == 4
        body = resp.json()
        assert body["slot"] == idx
        assert body["before"] == header_rid
        assert body["after"] == header_rid
        assert body["state"] == header_rid
        returned_ids.add(header_rid)

    assert len(returned_ids) == 12
    assert get_request_id() is None


@pytest.mark.anyio
async def test_body_size_limit_enforces_content_length_and_stops_chunked_streams_early(
    migrated_db_url: str,
) -> None:
    small_limit = 1024
    settings = IsolatedMiddlewareSettings(
        database_url=migrated_db_url,
        max_request_body_bytes=small_limit,
    )
    app = create_app(settings=settings)

    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
    ):
        # 1. Valid request within the 1024-byte limit succeeds and preserves Location header.
        valid_resp = await client.post(
            "/api/v1/prompt-requests",
            json={"raw_prompt": "Short valid prompt under 1 KiB."},
        )
        assert valid_resp.status_code == 201
        assert valid_resp.headers["Location"].startswith("/api/v1/prompt-requests/")

        # 2. Declared Content-Length exceeding 1024 bytes is rejected with 413 Problem Details.
        oversized_resp = await client.post(
            "/api/v1/prompt-requests",
            json={"raw_prompt": "A" * 1500},
        )
        assert oversized_resp.status_code == 413
        assert oversized_resp.headers["content-type"].startswith("application/problem+json")
        oversized_body = oversized_resp.json()
        assert oversized_body["status"] == 413
        assert oversized_body["title"] == "Content Too Large"
        assert oversized_body["error_code"] == "request_body_too_large"
        assert oversized_body["instance"] == "/api/v1/prompt-requests"
        assert oversized_body["request_id"] == oversized_resp.headers[REQUEST_ID_HEADER]

        # 3. Chunked stream WITHOUT Content-Length aborts immediately once limit is crossed,
        # without consuming or buffering the remaining chunks into memory.
        emitted_chunks = 0

        async def large_chunk_stream() -> AsyncIterator[bytes]:
            nonlocal emitted_chunks
            yield b'{"raw_prompt":"'
            for _ in range(50):
                emitted_chunks += 1
                yield b"X" * 512
            yield b'"}'

        stream_resp = await client.post(
            "/api/v1/prompt-requests",
            content=large_chunk_stream(),
            headers={"Content-Type": "application/json"},
        )
        assert stream_resp.status_code == 413
        assert stream_resp.headers["content-type"].startswith("application/problem+json")
        assert stream_resp.json()["error_code"] == "request_body_too_large"
        assert stream_resp.json()["request_id"] == stream_resp.headers[REQUEST_ID_HEADER]
        # 3 chunks of 512 bytes (> 1024 bytes) triggers immediate stop well before 50 chunks!
        assert emitted_chunks <= 4

        # 4. Malformed Content-Length header returns standardized 400 Problem Details.
        bad_cl_resp = await client.post(
            "/api/v1/prompt-requests",
            content=b'{"raw_prompt":"hello"}',
            headers={
                "Content-Type": "application/json",
                "Content-Length": "not-an-integer",
            },
        )
        assert bad_cl_resp.status_code == 400
        assert bad_cl_resp.headers["content-type"].startswith("application/problem+json")
        assert bad_cl_resp.json()["error_code"] == "invalid_content_length"

        # 5. Spoofed small Content-Length with oversized actual body still returns 413.
        async def spoofed_stream() -> AsyncIterator[bytes]:
            yield b'{"raw_prompt":"' + (b"S" * 1300) + b'"}'

        spoofed_resp = await client.post(
            "/api/v1/prompt-requests",
            content=spoofed_stream(),
            headers={"Content-Type": "application/json", "Content-Length": "10"},
        )
        assert spoofed_resp.status_code == 413
        assert spoofed_resp.headers["content-type"].startswith("application/problem+json")
        assert spoofed_resp.json()["error_code"] == "request_body_too_large"


def test_default_body_limit_supports_maximum_multibyte_utf8_prompt_and_context(
    migrated_db_url: str,
    default_db_guard: Callable[[], None],
) -> None:
    settings = IsolatedMiddlewareSettings(database_url=migrated_db_url)
    app = create_app(settings=settings)

    # 20,000 multi-byte Turkish UTF-8 chars in BOTH raw_prompt and user_context (~80+ KB JSON).
    multibyte_unit = "ğüşiöçĞÜŞİÖÇ"
    max_utf8_prompt = (multibyte_unit * ((MAX_RAW_PROMPT_LENGTH // len(multibyte_unit)) + 1))[
        :MAX_RAW_PROMPT_LENGTH
    ]
    max_utf8_context = (multibyte_unit * ((MAX_USER_CONTEXT_LENGTH // len(multibyte_unit)) + 1))[
        :MAX_USER_CONTEXT_LENGTH
    ]

    with TestClient(app) as client:
        resp = client.post(
            "/api/v1/prompt-requests",
            json={
                "raw_prompt": max_utf8_prompt,
                "user_context": max_utf8_context,
                "output_language": "tr",
                "target_mode": "anthropic",
                "task_profile": "architecture_design",
                "prompt_detail_level": "high",
                "response_detail_preference": "comprehensive",
                "reasoning_preference": "high",
            },
        )
        assert resp.status_code == 201
        assert len(resp.json()["raw_prompt"]) == MAX_RAW_PROMPT_LENGTH
        assert len(resp.json()["user_context"]) == MAX_USER_CONTEXT_LENGTH

    default_db_guard()
