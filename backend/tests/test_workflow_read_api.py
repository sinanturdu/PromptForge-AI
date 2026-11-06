"""Real read-only HTTP integration and adversarial local-boundary checks."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal, NoReturn
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy import event, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

import app.main as application
from app.core.config import Settings, build_sqlite_url
from app.db import session as ordinary_db
from app.db import write_transactions
from app.db.read_transactions import create_read_engine
from app.db.write_transactions import write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.providers.fake import FakeLLMProvider
from app.models import PromptRequest, WorkflowRun
from app.modules.prompts.workflow_persistence_contracts import (
    PersistenceError,
    WorkflowConfiguration,
)
from app.modules.prompts.workflow_readback import WorkflowReadbackService
from workflow_read_fixtures import (
    NOTE,
    PRIVATE,
    QUESTION,
    RAW,
    SECRET_ID,
    Environment,
    PausingFake,
    completed,
    dump,
    execute,
    interrupted,
    new_workflow,
)
from workflow_read_fixtures import db as db

SUFFIXES = ("", "/history", "/history/content", "/result", "/recovery")
OLD_PATHS = {"/api/v1/health", "/api/v1/prompt-requests", "/api/v1/prompt-requests/{id}"}
READ_PATHS = {
    "/api/v1/runs/{id}",
    "/api/v1/prompt-requests/{id}/runs",
    "/api/v1/runs/{id}/history",
    "/api/v1/runs/{id}/history/content",
    "/api/v1/runs/{id}/result",
    "/api/v1/runs/{id}/recovery",
}


class ReadAPISettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKFLOW_API_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_EXECUTION_ENABLED", raising=False)


@contextmanager
def local_client(
    path: Path,
    *,
    enabled: bool = True,
    execution: bool = False,
    peer: str = "127.0.0.1",
    base_url: str = "http://127.0.0.1",
    prefix: str = "/api/v1",
) -> Iterator[TestClient]:
    app = application.create_app(
        ReadAPISettings(
            database_url=build_sqlite_url(path),
            workflow_api_enabled=enabled,
            workflow_execution_enabled=execution,
            api_v1_prefix=prefix,
        )
    )
    # Test transport injects a socket peer explicitly. No production bypass exists.
    with TestClient(app, base_url=base_url, client=(peer, 50000)) as client:
        yield client


def protected(response: Any, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert {"Origin", "Sec-Fetch-Site"} <= set(response.headers["vary"].split(", "))
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "access-control-allow-origin" not in response.headers
    body: dict[str, Any] = response.json()
    if status >= 400:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert body["request_id"] == response.headers["x-request-id"]
        assert "input" not in body and "ctx" not in body
    return body


def test_default_off_has_no_routes_engine_or_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = Mock(side_effect=AssertionError("Disabled feature must not create a reader"))
    monkeypatch.setattr(application, "create_read_engine", factory)
    path = tmp_path / "absent" / "default.db"
    with local_client(path, enabled=False) as client:
        assert set(client.get("/api/v1/openapi.json").json()["paths"]) == OLD_PATHS
        for suffix in SUFFIXES:
            protected(client.get(f"/api/v1/runs/{uuid4()}{suffix}"), 404)
        protected(client.get(f"/api/v1/prompt-requests/{uuid4()}/runs"), 404)
        assert isinstance(client.app, FastAPI)
        assert not hasattr(client.app.state, "workflow_read_engine")
    factory.assert_not_called()
    assert not path.parent.exists()


@pytest.mark.parametrize("execution", [False, True])
def test_activation_registers_gets_and_gated_posts_without_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, execution: bool
) -> None:
    provider = FakeLLMProvider()
    writer = Mock(side_effect=AssertionError("Read API must not create a writer"))
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    monkeypatch.setattr(write_transactions, "create_write_engine", writer)
    path = tmp_path / "absent" / "read.db"
    with local_client(path, execution=execution) as client:
        schema = client.get("/api/v1/openapi.json").json()
        assert set(schema["paths"]) == OLD_PATHS | READ_PATHS | (
            {"/api/v1/runs/{id}/execution"} if execution else set()
        )
        for route in READ_PATHS:
            assert set(schema["paths"][route]) == (
                {"get", "post"} if execution and route.endswith("/runs") else {"get"}
            )
            operation = schema["paths"][route]["get"]
            assert "application/problem+json" in operation["responses"]["422"]["content"]
            assert "HTTPValidationError" not in json.dumps(operation)
        for path_part in (f"/runs/{uuid4()}/execution", f"/prompt-requests/{uuid4()}/runs"):
            response = client.post("/api/v1" + path_part, json={})
            protected(
                response, 503 if execution else 404 if path_part.endswith("execution") else 405
            )
        assert provider.calls == 0
        assert not path.parent.exists()
    assert provider.closed
    writer.assert_not_called()


@pytest.mark.parametrize(
    "peer,base_url",
    [
        ("127.0.0.1", "http://127.0.0.1"),
        ("127.0.0.2", "http://localhost:8000"),
        ("::1", "http://[::1]:8000"),
        ("::ffff:127.0.0.1", "http://localhost"),
        ("127.0.0.1", "https://localhost:8443"),
    ],
)
def test_verified_loopback_and_matching_origin_are_allowed(
    tmp_path: Path, peer: str, base_url: str
) -> None:
    with local_client(tmp_path / "absent.db", peer=peer, base_url=base_url) as client:
        body = protected(
            client.get(
                f"/api/v1/runs/{uuid4()}",
                headers={"Origin": base_url, "Sec-Fetch-Site": "same-origin"},
            ),
            503,
        )
        assert body["error_code"] == "workflow_unavailable"


@pytest.mark.parametrize(
    "peer,headers",
    [
        ("192.0.2.10", {}),
        ("::2", {}),
        ("::ffff:192.0.2.10", {}),
        ("localhost", {}),
        ("testclient", {}),
        ("127.0.0.1%fake", {}),
        ("127.0.0.1", {"Host": "evil.example"}),
        ("127.0.0.1", {"Host": "localhost.evil.example"}),
        ("127.0.0.1", {"Host": "127.1"}),
        ("127.0.0.1", {"Host": "2130706433"}),
        ("127.0.0.1", {"Host": "localhost:0"}),
        ("127.0.0.1", {"Host": "localhost:65536"}),
        ("127.0.0.1", {"Host": "user@localhost"}),
        ("127.0.0.1", {"Host": "[::1%lo]"}),
        ("127.0.0.1", {"Forwarded": "for=127.0.0.1"}),
        ("127.0.0.1", {"X-Forwarded-For": "127.0.0.1"}),
        ("127.0.0.1", {"X-Forwarded-Host": "localhost"}),
        ("127.0.0.1", {"X-Forwarded-Proto": "http"}),
        ("127.0.0.1", {"X-Forwarded-Port": "8000"}),
        ("127.0.0.1", {"X-Forwarded-Unknown": ""}),
        ("127.0.0.1", {"X-Real-IP": "127.0.0.1"}),
        ("127.0.0.1", {"Origin": "https://evil.example"}),
        ("127.0.0.1", {"Origin": "null"}),
        ("127.0.0.1", {"Origin": "https://127.0.0.1"}),
        ("127.0.0.1", {"Origin": "http://localhost"}),
        ("127.0.0.1", {"Origin": "http://127.0.0.1:8000"}),
        ("127.0.0.1", {"Origin": "http://127.0.0.1/path"}),
        ("127.0.0.1", {"Sec-Fetch-Site": "cross-site"}),
        ("127.0.0.1", {"Sec-Fetch-Site": "same-site"}),
        ("127.0.0.1", {"Sec-Fetch-Site": "unknown"}),
        ("192.0.2.10", {"Host": "localhost", "Origin": "http://localhost"}),
    ],
)
def test_untrusted_requests_are_rejected_before_validation_or_database_access(
    tmp_path: Path, peer: str, headers: dict[str, str]
) -> None:
    path = tmp_path / "not-created" / "db.sqlite"
    with local_client(path, peer=peer) as client:
        body = protected(client.get("/api/v1/runs/not-a-uuid", headers=headers), 403)
        assert body["error_code"] == "workflow_access_denied"
    assert not path.parent.exists()


@pytest.mark.parametrize(
    "headers",
    [
        [("Host", "127.0.0.1"), ("Host", "localhost")],
        [("Origin", "http://127.0.0.1"), ("Origin", "http://evil.example")],
        [("Sec-Fetch-Site", "same-origin"), ("Sec-Fetch-Site", "cross-site")],
    ],
)
def test_duplicate_security_headers_fail_closed(
    tmp_path: Path, headers: list[tuple[str, str]]
) -> None:
    with local_client(tmp_path / "absent.db") as client:
        protected(client.get(f"/api/v1/runs/{uuid4()}", headers=headers), 403)


def test_missing_socket_identity_is_denied(tmp_path: Path) -> None:
    app = application.create_app(
        ReadAPISettings(database_url=build_sqlite_url(tmp_path / "db"), workflow_api_enabled=True)
    )

    async def missing_peer(scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            scope["client"] = None
        await app(scope, receive, send)

    with TestClient(missing_peer, base_url="http://localhost") as client:
        protected(client.get(f"/api/v1/runs/{uuid4()}"), 403)


@pytest.mark.anyio
async def test_scope_not_found_validation_and_existing_api_compatibility(db: Environment) -> None:
    state = await new_workflow(db)
    async with write_transaction(db.writer) as session:
        other = PromptRequest(raw_prompt="OTHER_PARENT_PRIVATE_CANARY")
        session.add(other)
        await session.flush()
    before = dump(db)
    with local_client(db.path) as client:
        for suffix in SUFFIXES:
            missing = protected(client.get(f"/api/v1/runs/{uuid4()}{suffix}"), 404)
            mismatch = protected(
                client.get(
                    f"/api/v1/runs/{state.id}{suffix}", params={"prompt_request_id": str(other.id)}
                ),
                404,
            )
            assert missing["error_code"] == mismatch["error_code"] == "workflow_not_found"
            assert missing["detail"] == mismatch["detail"]
            protected(client.get(f"/api/v1/runs/bad{suffix}"), 422)
            protected(
                client.get(f"/api/v1/runs/{state.id}{suffix}", params={"prompt_request_id": "bad"}),
                422,
            )
        assert (
            protected(client.get(f"/api/v1/prompt-requests/{uuid4()}/runs"), 404)["error_code"]
            == "prompt_request_not_found"
        )
        assert protected(client.get(f"/api/v1/runs/{state.id}"), 200)["status"] == "PENDING"
        assert (
            protected(client.get(f"/api/v1/runs/{state.id}/result"), 409)["error_code"]
            == "workflow_not_terminal"
        )
        assert dump(db) == before
        # Existing routes remain accessible under their original policy and payloads.
        response = client.get(
            f"/api/v1/prompt-requests/{db.source.id}",
            headers={"Host": "testserver", "Origin": "https://other.example"},
        )
        assert response.status_code == 200 and response.json()["raw_prompt"] == RAW
        assert "cache-control" not in response.headers
        created = client.post(
            "/api/v1/prompt-requests", json={"raw_prompt": "API backwards compatibility"}
        )
        assert created.status_code == 201
        assert client.get(created.headers["location"]).json() == created.json()


@pytest.mark.anyio
async def test_empty_and_bounded_stable_pagination(db: Environment) -> None:
    with local_client(db.path) as client:
        assert protected(client.get(f"/api/v1/prompt-requests/{db.source.id}/runs"), 200) == {
            "items": [],
            "next_cursor": None,
        }
    identities = []
    for _ in range(3):
        identities.append((await completed(db, ["qualified"])).terminal.workflow.id)
    async with write_transaction(db.writer) as session:
        other = PromptRequest(raw_prompt="OTHER_PARENT_PRIVATE_CANARY")
        session.add(other)
        await session.flush()
    other_workflow = await db.persistence.create_workflow(
        other.id, WorkflowConfiguration(schema_version="1.0.0")
    )
    before = dump(db)
    with local_client(db.path) as client:
        path = f"/api/v1/prompt-requests/{db.source.id}/runs"
        page1 = protected(client.get(path, params={"limit": 2}), 200)
        cursor = page1["next_cursor"]
        page2 = protected(
            client.get(
                path,
                params={
                    "limit": 2,
                    "before_created_at": cursor["created_at"],
                    "before_workflow_run_id": cursor["workflow_run_id"],
                },
            ),
            200,
        )
        entries = page1["items"] + page2["items"]
        assert [UUID(v["id"]) for v in entries] == sorted(identities, reverse=True)
        assert all(
            v["prompt_request_id"] == str(db.source.id) and v["integrity_checked"] is False
            for v in entries
        )
        assert page2["next_cursor"] is None
        end = protected(
            client.get(
                path,
                params={
                    "before_created_at": entries[-1]["created_at"],
                    "before_workflow_run_id": entries[-1]["id"],
                },
            ),
            200,
        )
        assert end == {"items": [], "next_cursor": None}
        other_page = protected(client.get(f"/api/v1/prompt-requests/{other.id}/runs"), 200)
        assert [v["id"] for v in other_page["items"]] == [str(other_workflow.workflow.id)]
        foreign_cursor_page = protected(
            client.get(
                f"/api/v1/prompt-requests/{other.id}/runs",
                params={
                    "before_created_at": cursor["created_at"],
                    "before_workflow_run_id": cursor["workflow_run_id"],
                },
            ),
            200,
        )
        assert all(v["prompt_request_id"] == str(other.id) for v in foreign_cursor_page["items"])
        for params in (
            {"limit": 0},
            {"limit": 51},
            {"limit": "bad"},
            {"before_workflow_run_id": str(uuid4())},
            {"before_created_at": "2026-11-06T12:00:00"},
            {"before_created_at": "bad", "before_workflow_run_id": str(uuid4())},
            {"before_created_at": entries[0]["created_at"], "before_workflow_run_id": "bad"},
            {"unknown": SECRET_ID},
        ):
            response = client.get(path, params=params)
            assert protected(response, 422)["error_code"] == "validation_error"
            assert SECRET_ID not in response.text
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize("call", [1, 2, 3])
async def test_active_states_keep_unresolved_telemetry_unknown(db: Environment, call: int) -> None:
    state = await interrupted(db, call)
    before = dump(db)
    with local_client(db.path) as client:
        progress = protected(client.get(f"/api/v1/runs/{state.id}"), 200)
        assert progress["status"] == {1: "GENERATING", 2: "EVALUATING", 3: "REVISING"}[call]
        metadata = protected(client.get(f"/api/v1/runs/{state.id}/history"), 200)
        assert metadata["operations"][-1]["telemetry"] is None
        assert metadata["operations"][-1]["unresolved"]
        assert (
            protected(client.get(f"/api/v1/runs/{state.id}/result"), 409)["error_code"]
            == "workflow_not_terminal"
        )
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize("profile", ["qualified", "critical", "unassessable", "clarify", "failed"])
async def test_terminal_content_quality_and_metadata_privacy(db: Environment, profile: str) -> None:
    if profile == "failed":
        provider = PausingFake([LLMErrorCode.PROVIDER_UNAVAILABLE])
        try:
            result = await execute(db, await new_workflow(db), provider)
        finally:
            await provider.aclose()
    else:
        result = await completed(
            db,
            [] if profile == "clarify" else [profile],
            limit=1,
            decision="clarify" if profile == "clarify" else None,
        )
    state = result.terminal.workflow
    before = dump(db)
    with local_client(db.path) as client:
        progress = protected(client.get(f"/api/v1/runs/{state.id}"), 200)
        metadata = protected(client.get(f"/api/v1/runs/{state.id}/history"), 200)
        content = protected(client.get(f"/api/v1/runs/{state.id}/history/content"), 200)
        final = protected(client.get(f"/api/v1/runs/{state.id}/result"), 200)
        recovery = protected(client.get(f"/api/v1/runs/{state.id}/recovery"), 200)
        assert progress["status"] == state.status
        assert (
            content["source"]["raw_prompt"] == RAW and content["source"]["user_context"] == PRIVATE
        )
        assert set(content) == {
            "metadata",
            "source",
            "versions",
            "evaluations",
            "clarification_questions",
        }
        assert "operation_results" not in content
        for value in (progress, metadata, recovery):
            wire = json.dumps(value)
            for private in (
                PRIVATE,
                NOTE,
                QUESTION,
                SECRET_ID,
                "PRIVATE_CRITIC",
                "PRIVATE_APPROVED",
                "raw_prompt",
                "user_context",
                "canonical_prompt",
                "rendered_prompt",
                "source_digest",
                "execution_token",
                "client_idempotency_key_digest",
                str(state.execution_token),
            ):
                assert private not in wire
        for value in (content, final):
            wire = json.dumps(value)
            for private in (
                SECRET_ID,
                str(state.execution_token),
                "operation_results",
                "upstream_request_id",
                "configured_provider",
                "configured_model",
            ):
                assert private not in wire
        if profile in {"clarify", "failed"}:
            assert (
                final["candidate"] is None
                and final["evaluation"] is None
                and final["assessment"] is None
            )
            assert final["clarification_questions"] == ([QUESTION] if profile == "clarify" else [])
        else:
            assert final["candidate"]["rendered_prompt"] == RAW
            assert (
                final["workflow"]["selected"]["quality_status"]
                == {
                    "qualified": "qualified",
                    "critical": "not_qualified",
                    "unassessable": "unassessable",
                }[profile]
            )
            assert final["workflow"]["status"] == "COMPLETED"
            if profile == "unassessable":
                assert final["evaluation"]["stored_total_score"] is None
                assert final["workflow"]["selected"]["total_score"] is None
    assert dump(db) == before


@pytest.mark.anyio
async def test_integrity_conflict_keeps_diagnostic_records_without_trusted_result(
    db: Environment,
) -> None:
    result = await completed(db, ["qualified"])
    state = result.terminal.workflow
    async with write_transaction(db.writer) as session:
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == state.id).values(final_version_id=None)
        )
    before = dump(db)
    with local_client(db.path) as client:
        metadata = protected(client.get(f"/api/v1/runs/{state.id}/history"), 200)
        assert metadata["workflow"]["findings"] and metadata["workflow"]["selected"] is None
        assert len(metadata["evaluations"]) == 1
        content = protected(client.get(f"/api/v1/runs/{state.id}/history/content"), 200)
        assert content["versions"][0]["rendered_prompt"] == RAW
        response = client.get(f"/api/v1/runs/{state.id}/result")
        assert protected(response, 409)["error_code"] == "workflow_integrity_inconsistent"
        assert "candidate" not in response.text and PRIVATE not in response.text
        recovery = protected(client.get(f"/api/v1/runs/{state.id}/recovery"), 200)["assessment"]
        assert recovery["disposition"] == "integrity_investigation_required"
        assert recovery["selected"] is None and recovery["committed"] == []
        assert not recovery["committed_result_reconciliation_available"]
        assert (
            not recovery["automatic_provider_replay_safe"] and not recovery["authorizes_mutation"]
        )
    assert dump(db) == before


@pytest.mark.parametrize(
    "kind,status,code",
    [
        ("not_found", 404, "workflow_not_found"),
        ("conflict", 409, "workflow_conflict"),
        ("invalid", 409, "workflow_integrity_inconsistent"),
        ("unavailable", 503, "workflow_unavailable"),
        ("sql", 503, "workflow_unavailable"),
        ("unexpected", 500, "internal_server_error"),
    ],
)
def test_safe_failure_mapping_and_headers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    kind: Literal["not_found", "conflict", "invalid", "unavailable", "sql", "unexpected"],
    status: int,
    code: str,
) -> None:
    async def broken(*_args: Any, **_kwargs: Any) -> NoReturn:
        if kind == "sql":
            raise OperationalError(SECRET_ID, {"raw_prompt": PRIVATE}, RuntimeError(SECRET_ID))
        if kind == "unexpected":
            raise RuntimeError(SECRET_ID)
        raise PersistenceError(kind)

    monkeypatch.setattr(WorkflowReadbackService, "read", broken)
    with local_client(tmp_path / "absent.db") as client:
        response = client.get(f"/api/v1/runs/{uuid4()}")
        assert protected(response, status)["error_code"] == code
        for private in (SECRET_ID, PRIVATE, "OperationalError", "Traceback"):
            assert private not in response.text and private not in caplog.text


def test_absent_database_stays_absent_for_every_endpoint(tmp_path: Path) -> None:
    path = tmp_path / "absent" / "database.db"
    with local_client(path) as client:
        for suffix in SUFFIXES:
            assert (
                protected(client.get(f"/api/v1/runs/{uuid4()}{suffix}"), 503)["error_code"]
                == "workflow_unavailable"
            )
        protected(client.get(f"/api/v1/prompt-requests/{uuid4()}/runs"), 503)
    assert not path.parent.exists()


@pytest.mark.anyio
async def test_one_owned_read_engine_no_writes_or_provider_calls_and_shutdown(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await completed(db, ["qualified"])
    before = dump(db)
    statements: list[str] = []
    readers: list[AsyncEngine] = []
    disposed: list[AsyncEngine] = []
    active: set[int] = set()
    real_dispose = AsyncEngine.dispose
    real_ordinary_factory = ordinary_db.create_db_engine
    provider = FakeLLMProvider()
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    writer = Mock(side_effect=AssertionError("No writer during inspection"))
    monkeypatch.setattr(write_transactions, "create_write_engine", writer)

    def read_factory(url: str) -> AsyncEngine:
        reader = create_read_engine(url)
        readers.append(reader)

        @event.listens_for(reader.sync_engine, "before_cursor_execute")
        def trace(_conn: Any, _cursor: Any, statement: str, *_args: Any) -> None:
            statements.append(statement)

        @event.listens_for(reader.sync_engine, "checkout")
        def checkout(connection: Any, *_args: Any) -> None:
            active.add(id(connection))

        @event.listens_for(reader.sync_engine, "checkin")
        def checkin(connection: Any, *_args: Any) -> None:
            active.discard(id(connection))

        return reader

    def ordinary_factory(*args: Any, **kwargs: Any) -> AsyncEngine:
        engine = real_ordinary_factory(*args, **kwargs)

        @event.listens_for(engine.sync_engine, "connect")
        def forbid(*_args: Any) -> NoReturn:
            raise AssertionError("Workflow reads must not connect through the ordinary API engine")

        return engine

    async def dispose(engine: AsyncEngine, close: bool = True) -> None:
        disposed.append(engine)
        await real_dispose(engine, close=close)

    monkeypatch.setattr(application, "create_read_engine", read_factory)
    monkeypatch.setattr(ordinary_db, "create_db_engine", ordinary_factory)
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    with local_client(db.path) as client:
        for suffix in SUFFIXES:
            protected(client.get(f"/api/v1/runs/{result.terminal.workflow.id}{suffix}"), 200)
            assert not active
        protected(client.get(f"/api/v1/prompt-requests/{db.source.id}/runs"), 200)
        assert not active and len(readers) == 1 and provider.calls == 0
        assert isinstance(client.app, FastAPI)
        state = client.app.state
    assert provider.closed and disposed.count(readers[0]) == 1
    assert not hasattr(state, "workflow_read_engine") and not hasattr(state, "workflow_readback")
    assert statements and any(s == "BEGIN" for s in statements)
    assert all(s.lstrip().upper().startswith(("SELECT", "BEGIN")) for s in statements)
    assert all(
        "BEGIN IMMEDIATE" not in s.upper() and "PRAGMA JOURNAL_MODE" not in s.upper()
        for s in statements
    )
    writer.assert_not_called()
    assert dump(db) == before


def test_read_engine_disposed_on_partial_startup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    disposed: list[AsyncEngine] = []
    real_dispose = AsyncEngine.dispose
    readers: list[AsyncEngine] = []

    def reader(url: str) -> AsyncEngine:
        engine = create_read_engine(url)
        readers.append(engine)
        return engine

    def broken_provider(_settings: Settings) -> NoReturn:
        raise RuntimeError("synthetic provider construction failure")

    async def dispose(engine: AsyncEngine, close: bool = True) -> None:
        disposed.append(engine)
        await real_dispose(engine, close=close)

    monkeypatch.setattr(application, "create_read_engine", reader)
    monkeypatch.setattr(application, "create_provider", broken_provider)
    monkeypatch.setattr(AsyncEngine, "dispose", dispose)
    with (
        pytest.raises(RuntimeError, match="synthetic provider construction failure"),
        local_client(tmp_path / "not-created.db"),
    ):
        pytest.fail("Startup should fail")
    assert len(readers) == 1 and disposed.count(readers[0]) == 1
    assert not (tmp_path / "not-created.db").exists()


def test_custom_prefix_has_same_security_and_cache_boundary(tmp_path: Path) -> None:
    with local_client(tmp_path / "db", prefix="/custom/v1") as client:
        protected(client.get("/custom/v1/runs/bad"), 422)
        protected(client.get("/custom/v1/runs/bad", headers={"Host": "evil.example"}), 403)


@pytest.mark.parametrize("peer,status", [("192.0.2.10", 403), ("127.0.0.1", 422)])
def test_asgi_root_path_cannot_bypass_guard(tmp_path: Path, peer: str, status: int) -> None:
    app = application.create_app(
        ReadAPISettings(
            database_url=build_sqlite_url(tmp_path / "absent.db"), workflow_api_enabled=True
        )
    )
    with TestClient(
        app, base_url="http://localhost", client=(peer, 50000), root_path="/mounted"
    ) as client:
        protected(client.get("/mounted/api/v1/runs/bad"), status)


@pytest.mark.parametrize("raw_host", [None, b"\xff"])
def test_missing_or_non_ascii_host_fails_closed(tmp_path: Path, raw_host: bytes | None) -> None:
    app = application.create_app(
        ReadAPISettings(
            database_url=build_sqlite_url(tmp_path / "absent.db"), workflow_api_enabled=True
        )
    )

    async def malformed_host(scope: Any, receive: Any, send: Any) -> None:
        # TestClient supplies Host automatically; inject the actual wire edge case.
        if scope["type"] == "http":
            scope["headers"] = [
                (key, value) for key, value in scope["headers"] if key.lower() != b"host"
            ]
            if raw_host is not None:
                scope["headers"].append((b"host", raw_host))
        await app(scope, receive, send)

    with TestClient(
        malformed_host, base_url="http://localhost", client=("127.0.0.1", 50000)
    ) as client:
        protected(client.get("/api/v1/runs/bad"), 403)


@pytest.mark.parametrize("data", [b"", b"not-a-database PRIVATE_STORAGE_CANARY"])
def test_unusable_existing_database_is_not_initialized(tmp_path: Path, data: bytes) -> None:
    path = tmp_path / "unusable.db"
    path.write_bytes(data)
    timestamp = path.stat().st_mtime_ns
    with local_client(path) as client:
        response = client.get(f"/api/v1/runs/{uuid4()}")
        assert protected(response, 503)["error_code"] == "workflow_unavailable"
        assert "PRIVATE_STORAGE_CANARY" not in response.text
    assert path.read_bytes() == data and path.stat().st_mtime_ns == timestamp
