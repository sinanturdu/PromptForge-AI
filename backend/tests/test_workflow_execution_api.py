"""Offline HTTP submission through a real owned listener and migrated disposable DB."""

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, NoReturn
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from sqlalchemy import event, update

import app.main as application
from app.core.config import Settings, build_sqlite_url
from app.core.execution_environment import (
    PreparedLocalExecutionServer,
    prepare_local_execution_server,
)
from app.core.execution_startup import (
    ApplicationExecutionOwner,
    ExecutionModels,
    prepare_execution_startup,
)
from app.core.execution_supervisor import WorkflowExecutionSupervisor
from app.db.base import utc_now
from app.db.write_transactions import write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import ModelSettings
from app.models import PromptRequest, WorkflowRun
from app.modules.prompts.persistence_contracts import OperationBindingStorage
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import (
    OperationIdentity,
    PersistenceError,
    WorkflowCreation,
)
from workflow_read_fixtures import (
    PRIVATE,
    RAW,
    SECRET_ID,
    Environment,
    PausingFake,
    dump,
    script,
)
from workflow_read_fixtures import db as db


class HTTPSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKFLOW_API_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_EXECUTION_ENABLED", raising=False)


def protected(response: httpx.Response, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["cross-origin-resource-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert {"Origin", "Sec-Fetch-Site"} <= set(response.headers["vary"].split(", "))
    value: dict[str, Any] = response.json()
    if status >= 400:
        assert response.headers["content-type"].startswith("application/problem+json")
        assert value["request_id"] == response.headers["x-request-id"]
    for secret in (RAW, PRIVATE, SECRET_ID, '"execution_token":', "client_idempotency_key_digest"):
        assert secret not in response.text
    return value


@dataclass
class HTTPHarness:
    db: Environment
    app: FastAPI
    transport: PreparedLocalExecutionServer
    client: httpx.AsyncClient
    provider: PausingFake
    owner: ApplicationExecutionOwner
    supervisor: WorkflowExecutionSupervisor
    persistence: WorkflowPersistenceService

    async def create(
        self, *, key: str = "http-creation-1", parent: UUID | None = None, rounds: int = 3
    ) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/prompt-requests/{parent or self.db.source.id}/runs",
            json=dict(client_idempotency_key=key, max_evaluation_rounds=rounds),
        )

    async def workflow(self, **options: Any) -> str:
        return str(protected(await self.create(**options), 201)["workflow"]["id"])

    async def start(self, workflow: str, *, revision: int = 0) -> httpx.Response:
        return await self.client.post(
            f"/api/v1/runs/{workflow}/execution", json=dict(expected_revision=revision)
        )

    async def other_parent(self) -> UUID:
        async with write_transaction(self.db.writer) as session:
            source = PromptRequest(raw_prompt=RAW)
            session.add(source)
            await session.flush()
            return source.id


@pytest.fixture
async def http_env(db: Environment, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[HTTPHarness]:
    provider = PausingFake(script(["bad", "critical", "qualified"]), block_at=1)
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    app = application.create_app(
        HTTPSettings(
            database_url=build_sqlite_url(db.path),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
            llm_max_retries=0,
            llm_json_repair_attempts=0,
        ),
        execution_models=ExecutionModels(
            architect=ModelSettings(model="fake"), critic=ModelSettings(model="fake")
        ),
    )
    with prepare_local_execution_server(app, port=0) as original:
        prepared = prepare_execution_startup(original)
        server, config = prepared.server, prepared.server.config
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[prepared.socket])
        owner: ApplicationExecutionOwner = app.state.workflow_execution_owner
        supervisor, persistence = owner.supervisor, owner.persistence
        assert supervisor is not None and persistence is not None and owner._writer is not None
        assert prepared.is_qualified() and provider.calls == 0
        # Prove provider dispatch does not borrow a request/transaction resource.
        checked_out: set[int] = set()

        @event.listens_for(owner._writer.sync_engine, "checkout")
        def checkout(connection: Any, *_args: Any) -> None:
            checked_out.add(id(connection))

        @event.listens_for(owner._writer.sync_engine, "checkin")
        def checkin(connection: Any, *_args: Any) -> None:
            checked_out.discard(id(connection))

        generate = provider.generate

        async def outside_transaction(request: Any) -> Any:
            assert not checked_out and db.read_connections == 0
            return await generate(request)

        monkeypatch.setattr(provider, "generate", outside_transaction)
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{prepared.socket.getsockname()[1]}",
                trust_env=False,
                timeout=20,
            ) as client:
                yield HTTPHarness(
                    db, app, prepared, client, provider, owner, supervisor, persistence
                )
        finally:
            provider.release.set()
            await supervisor.wait_idle()
            await server.shutdown(sockets=[prepared.socket])
    assert provider.closed and owner.persistence is None


@pytest.mark.anyio
async def test_create_replay_conflict_and_server_owned_configuration(http_env: HTTPHarness) -> None:
    env = http_env
    first = await env.create()
    value = protected(first, 201)
    workflow = value["workflow"]
    assert set(value) == {"workflow", "replayed"} and not value["replayed"]
    assert set(workflow) == {"id", "prompt_request_id", "status"}
    assert workflow["status"] == "PENDING" and workflow["prompt_request_id"] == str(
        env.db.source.id
    )
    assert first.headers["location"] == f"/api/v1/runs/{workflow['id']}"
    before = dump(env.db)
    replay = await env.create()
    assert protected(replay, 200) == dict(workflow=workflow, replayed=True)
    assert replay.headers["location"] == first.headers["location"]
    assert dump(env.db) == before and not env.provider.requests
    protected(await env.create(rounds=2), 409)
    protected(await env.create(key="another-key"), 409)
    assert dump(env.db) == before
    summary = await env.db.readback.read(UUID(workflow["id"]))
    config = summary.workflow.configuration
    assert config is not None and config.timeout_seconds == 300
    assert config.max_evaluation_rounds == 3 and config.protected_spans == ()
    assert not summary.operations


@pytest.mark.anyio
async def test_concurrent_identical_creation_is_one_record(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = http_env
    original = env.persistence.create_workflow
    barrier, count = asyncio.Event(), 0

    async def create(*args: Any, **kwargs: Any) -> WorkflowCreation:
        nonlocal count
        count += 1
        if count == 4:
            barrier.set()
        await barrier.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(env.persistence, "create_workflow", create)
    responses = await asyncio.gather(*(env.create() for _ in range(4)))
    assert sorted(r.status_code for r in responses) == [200, 200, 200, 201]
    ids = {protected(r, r.status_code)["workflow"]["id"] for r in responses}
    assert len(ids) == 1
    assert len((await env.db.readback.list_workflows(env.db.source.id)).items) == 1
    assert env.provider.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("after_commit", [False, True])
async def test_lost_creation_acknowledgment_preserves_idempotent_retry(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch, after_commit: bool
) -> None:
    env = http_env
    original = env.persistence.create_workflow
    committed: list[WorkflowCreation] = []

    async def lost(*args: Any, **kwargs: Any) -> WorkflowCreation:
        if after_commit:
            committed.append(await original(*args, **kwargs))
        raise PersistenceError("unavailable")

    before = dump(env.db)
    monkeypatch.setattr(env.persistence, "create_workflow", lost)
    protected(await env.create(), 503)
    if not after_commit:
        assert dump(env.db) == before
    monkeypatch.setattr(env.persistence, "create_workflow", original)
    replay = protected(await env.create(), 200 if after_commit else 201)
    if after_commit:
        assert replay["workflow"]["id"] == str(committed[0].workflow.id)
        assert replay["replayed"]
    assert len((await env.db.readback.list_workflows(env.db.source.id)).items) == 1
    assert not (await env.db.readback.read(UUID(replay["workflow"]["id"]))).operations
    assert env.provider.calls == 0


@pytest.mark.anyio
async def test_create_start_poll_and_three_round_result(http_env: HTTPHarness) -> None:
    env = http_env
    workflow = await env.workflow()
    started = await env.start(workflow)
    receipt = protected(started, 202)
    assert receipt == dict(
        workflow=dict(id=workflow, prompt_request_id=str(env.db.source.id), status="PENDING"),
        accepted=True,
    )
    assert started.headers["location"] == f"/api/v1/runs/{workflow}"
    await asyncio.wait_for(env.provider.entered.wait(), 5)
    running = protected(await env.client.get(started.headers["location"]), 200)
    assert running["status"] == "GENERATING" and running["stored_final_score"] is None
    assert running["stored_qualified"] is None and running["selected"] is None
    assert env.provider.calls == 0 and len(env.provider.requests) == 1  # paused before fake return
    env.provider.release.set()
    await env.supervisor.wait_idle()
    done = protected(await env.client.get(started.headers["location"]), 200)
    assert done["status"] == "COMPLETED" and done["completed_rounds"] == 3
    assert done["selected"]["quality_status"] == "qualified"
    result = await env.client.get(f"/api/v1/runs/{workflow}/result")
    assert result.status_code == 200 and result.headers["cache-control"] == "no-store"
    assert result.json()["candidate"] is not None and len(env.provider.requests) == 6
    before = dump(env.db)
    protected(await env.start(workflow), 409)
    replay = protected(await env.create(), 200)
    assert replay["replayed"] and replay["workflow"]["status"] == "COMPLETED"
    assert dump(env.db) == before and len(env.provider.requests) == 6


@pytest.mark.anyio
async def test_duplicate_execution_and_capacity(http_env: HTTPHarness) -> None:
    env = http_env
    workflow = await env.workflow()
    second = await env.workflow(parent=await env.other_parent())
    responses = await asyncio.gather(*(env.start(workflow) for _ in range(5)))
    assert sorted(r.status_code for r in responses) == [202, 409, 409, 409, 409]
    for response in responses:
        protected(response, response.status_code)
    await asyncio.wait_for(env.provider.entered.wait(), 5)
    assert len(env.provider.requests) == 1
    saturated = protected(await env.start(second), 429)
    assert saturated["error_code"] == "workflow_capacity_exceeded"
    assert len(env.provider.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["claim_workflow", "execution_snapshot"])
async def test_fence_failure_prevents_dispatch(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    env = http_env
    workflow = await env.workflow()

    async def conflict(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise PersistenceError("conflict")

    monkeypatch.setattr(env.persistence, stage, conflict)
    protected(await env.start(workflow), 202)
    completed = await env.supervisor.wait_idle()
    assert completed is not None and completed.code == "persistence_conflict"
    assert not env.provider.requests
    stored = protected(await env.client.get(f"/api/v1/runs/{workflow}"), 200)
    assert stored["status"] == ("PENDING" if stage == "claim_workflow" else "GENERATING")
    if stage == "execution_snapshot":
        protected(await env.start(workflow), 409)


@pytest.mark.anyio
async def test_disconnect_after_acceptance_leaves_supervisor_owner(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = http_env
    workflow = await env.workflow()
    accepted, submit = asyncio.Event(), env.supervisor.submit

    async def observe(*args: Any, **kwargs: Any) -> Any:
        result = await submit(*args, **kwargs)
        if result.accepted:
            accepted.set()
        return result

    monkeypatch.setattr(env.supervisor, "submit", observe)
    port = env.transport.socket.getsockname()[1]
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        body = b'{"expected_revision":0}'
        writer.write(
            f"POST /api/v1/runs/{workflow}/execution HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        await asyncio.wait_for(accepted.wait(), 5)
    finally:
        writer.close()
        await writer.wait_closed()
    await asyncio.wait_for(env.provider.entered.wait(), 5)
    assert env.supervisor._active is not None and not env.supervisor._active.done()
    protected(await env.start(workflow), 409)
    env.provider.release.set()
    await env.supervisor.wait_idle()
    result = protected(await env.client.get(f"/api/v1/runs/{workflow}"), 200)
    assert result["status"] == "COMPLETED" and len(env.provider.requests) == 6


@pytest.mark.anyio
@pytest.mark.parametrize("case", ["failed", "expired", "unresolved", "revision", "scope"])
async def test_ineligible_requests_do_not_dispatch(http_env: HTTPHarness, case: str) -> None:
    env = http_env
    workflow = await env.workflow()
    workflow_id = UUID(workflow)
    if case in {"failed", "expired", "unresolved"}:
        claimed = await env.persistence.claim_workflow(
            env.db.source.id, workflow_id, expected_revision=0, lease_seconds=120
        )
        if case == "unresolved":
            await env.persistence.reserve_operation(
                claimed.fence(),
                identity=OperationIdentity(
                    operation_key="create:1",
                    agent_name="PromptArchitectAgent",
                    agent_version="1.0.0",
                    instruction_version="1.0.0",
                ),
                binding=OperationBindingStorage(
                    schema_version="1.0.0", operation="create", evaluation_round=1
                ),
            )
        else:
            async with write_transaction(env.db.writer) as session:
                await session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == workflow_id)
                    .values(
                        status="FAILED" if case == "failed" else "GENERATING",
                        lease_expires_at=utc_now() - timedelta(seconds=1),
                    )
                )
    before = dump(env.db)
    if case == "scope":
        response = await env.client.post(
            f"/api/v1/runs/{workflow}/execution?prompt_request_id={uuid4()}",
            json=dict(expected_revision=0),
        )
    else:
        response = await env.start(workflow, revision=1 if case == "revision" else 0)
    protected(response, 404 if case == "scope" else 409)
    assert dump(env.db) == before and not env.provider.requests


@pytest.mark.anyio
async def test_provider_failure_is_durable_and_cannot_restart(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = http_env
    failing = PausingFake([LLMErrorCode.AUTHENTICATION])
    monkeypatch.setattr(env.provider, "generate", failing.generate)
    workflow = await env.workflow()
    protected(await env.start(workflow), 202)
    await env.supervisor.wait_idle()
    result = protected(await env.client.get(f"/api/v1/runs/{workflow}"), 200)
    assert result["status"] == "FAILED" and result["selected"] is None
    before = dump(env.db)
    protected(await env.start(workflow), 409)
    assert dump(env.db) == before and failing.calls == 1


@pytest.mark.anyio
async def test_shutdown_rejects_both_mutations(http_env: HTTPHarness) -> None:
    workflow = await http_env.workflow()
    http_env.supervisor.begin_shutdown()
    for response in [await http_env.create(), await http_env.start(workflow)]:
        assert protected(response, 503)["error_code"] == "workflow_unavailable"
    assert not http_env.provider.requests


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["create", "start"])
@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Origin": "http://evil.example"},
        {"Origin": "null"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Forwarded": "for=127.0.0.1"},
        {"X-Forwarded-For": "127.0.0.1"},
        {"X-Real-IP": "127.0.0.1"},
        {"Content-Type": "text/plain"},
    ],
)
async def test_untrusted_requests_are_denied(
    http_env: HTTPHarness, path: str, headers: dict[str, str]
) -> None:
    env = http_env
    route = (
        f"/prompt-requests/{env.db.source.id}/runs"
        if path == "create"
        else f"/runs/{uuid4()}/execution"
    )
    before = dump(env.db)
    protected(await env.client.post("/api/v1" + route, json={}, headers=headers), 403)
    assert dump(env.db) == before and not env.provider.requests


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["create", "start"])
async def test_forged_peer_cannot_bypass_security(http_env: HTTPHarness, path: str) -> None:
    env = http_env
    route = (
        f"/prompt-requests/{env.db.source.id}/runs"
        if path == "create"
        else f"/runs/{uuid4()}/execution"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, client=("192.0.2.1", 50000)),
        base_url="http://127.0.0.1",
    ) as client:
        protected(await client.post("/api/v1" + route, json={}, headers={"Host": "localhost"}), 403)
    assert not env.provider.requests


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path,body",
    [
        ("create", {}),
        ("create", {"client_idempotency_key": "a", "max_evaluation_rounds": 4}),
        ("create", {"client_idempotency_key": "a", "max_evaluation_rounds": True}),
        ("create", {"client_idempotency_key": "a", "protected_spans": [PRIVATE]}),
        ("create", {"client_idempotency_key": "a", "provider": SECRET_ID}),
        ("create", {"client_idempotency_key": " "}),
        ("start", {}),
        ("start", {"expected_revision": "0"}),
        ("start", {"expected_revision": -1}),
        ("start", {"expected_revision": True}),
        ("start", {"expected_revision": 0, "execution_token": SECRET_ID}),
    ],
)
async def test_invalid_bodies_are_sanitized(
    http_env: HTTPHarness, path: str, body: dict[str, Any]
) -> None:
    env = http_env
    route = (
        f"/prompt-requests/{env.db.source.id}/runs"
        if path == "create"
        else f"/runs/{uuid4()}/execution"
    )
    response = await env.client.post("/api/v1" + route, json=body)
    # The field name is intentionally reported, never its private value.
    value = protected(response, 422)
    assert value["error_code"] == "validation_error" and not env.provider.requests


@pytest.mark.anyio
async def test_invalid_json_ids_and_missing_records(http_env: HTTPHarness) -> None:
    env = http_env
    for path in [f"/prompt-requests/{env.db.source.id}/runs", f"/runs/{uuid4()}/execution"]:
        protected(
            await env.client.post(
                "/api/v1" + path,
                content=b'{"private":',
                headers={"Content-Type": "application/json"},
            ),
            422,
        )
    for path, body in [
        ("/prompt-requests/not-a-uuid/runs", {"client_idempotency_key": "a"}),
        ("/runs/not-a-uuid/execution", {"expected_revision": 0}),
    ]:
        protected(await env.client.post("/api/v1" + path, json=body), 422)
    protected(await env.create(parent=uuid4()), 404)
    protected(await env.start(str(uuid4())), 404)
    assert not env.provider.requests


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error", [PersistenceError("unavailable"), RuntimeError(PRIVATE + SECRET_ID)]
)
async def test_persistence_and_unexpected_failures_are_sanitized(
    http_env: HTTPHarness,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    error: Exception,
) -> None:
    async def fail(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise error

    monkeypatch.setattr(http_env.persistence, "create_workflow", fail)
    protected(await http_env.create(), 503 if isinstance(error, PersistenceError) else 500)
    assert PRIVATE not in caplog.text and SECRET_ID not in caplog.text
    assert not http_env.provider.requests


@pytest.mark.anyio
async def test_lost_listener_qualification_fails_both_mutations(http_env: HTTPHarness) -> None:
    env = http_env
    workflow = await env.workflow()
    env.transport.server.config.proxy_headers = True
    try:
        for response in [await env.create(), await env.start(workflow)]:
            protected(response, 503)
        assert not env.provider.requests
    finally:
        env.transport.server.config.proxy_headers = False


@pytest.mark.anyio
async def test_openapi_lists_only_two_posts_and_safe_contracts(http_env: HTTPHarness) -> None:
    schema = (await http_env.client.get("/api/v1/openapi.json")).json()
    posts = {
        path: methods["post"]
        for path, methods in schema["paths"].items()
        if "runs" in path and "post" in methods
    }
    assert set(posts) == {"/api/v1/prompt-requests/{id}/runs", "/api/v1/runs/{id}/execution"}
    for operation in posts.values():
        for code in ("403", "404", "409", "422", "429", "503", "500"):
            assert "application/problem+json" in operation["responses"][code]["content"]
    assert "200" in posts["/api/v1/prompt-requests/{id}/runs"]["responses"]
    assert "201" in posts["/api/v1/prompt-requests/{id}/runs"]["responses"]
    assert "202" in posts["/api/v1/runs/{id}/execution"]["responses"]
    assert (
        "client_idempotency_key"
        in schema["components"]["schemas"]["WorkflowCreateRequest"]["required"]
    )
    assert "execution_token" not in json.dumps(schema)


@pytest.mark.anyio
async def test_cancelled_request_after_acceptance_does_not_cancel_worker(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = http_env
    workflow = await env.workflow()
    accepted, submit = asyncio.Event(), env.supervisor.submit
    hold = asyncio.Event()

    async def observe(*args: Any, **kwargs: Any) -> Any:
        result = await submit(*args, **kwargs)
        accepted.set()
        await hold.wait()  # simulate cancellation before HTTP receipt delivery
        return result

    monkeypatch.setattr(env.supervisor, "submit", observe)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=env.app, client=("127.0.0.1", 50000)),
        base_url="http://127.0.0.1",
    ) as client:
        request = asyncio.create_task(
            client.post(f"/api/v1/runs/{workflow}/execution", json={"expected_revision": 0})
        )
        await asyncio.wait_for(accepted.wait(), 5)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
    await asyncio.wait_for(env.provider.entered.wait(), 5)
    env.provider.release.set()
    await env.supervisor.wait_idle()
    assert len(env.provider.requests) == 6
    assert (await env.db.readback.read(UUID(workflow))).workflow.status == "COMPLETED"


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["not_qualified", "unassessable", "clarify"])
async def test_terminal_outcomes_remain_distinct(
    http_env: HTTPHarness, monkeypatch: pytest.MonkeyPatch, decision: str
) -> None:
    from workflow_read_fixtures import architect

    env = http_env
    fake = FakeLLMProvider(
        [architect("clarify")]
        if decision == "clarify"
        else script(["bad" if decision == "not_qualified" else "unassessable"])
    )
    monkeypatch.setattr(env.provider, "generate", fake.generate)
    workflow = await env.workflow(rounds=1)
    protected(await env.start(workflow), 202)
    await env.supervisor.wait_idle()
    progress = protected(await env.client.get(f"/api/v1/runs/{workflow}"), 200)
    assert progress["status"] == (
        "CLARIFICATION_REQUIRED" if decision == "clarify" else "COMPLETED"
    )
    if decision == "clarify":
        assert progress["selected"] is None and progress["stored_final_score"] is None
        assert progress["clarification_question_count"] == 1
    else:
        assert progress["selected"]["quality_status"] == decision
    result = await env.client.get(f"/api/v1/runs/{workflow}/result")
    assert result.status_code == 200
    if decision == "clarify":
        assert result.json()["candidate"] is None and result.json()["evaluation"] is None
    before = dump(env.db)
    protected(await env.start(workflow), 409)
    assert dump(env.db) == before


@pytest.mark.anyio
async def test_same_origin_json_and_ambiguous_content_types(http_env: HTTPHarness) -> None:
    env = http_env
    create_path = f"/api/v1/prompt-requests/{env.db.source.id}/runs"
    start_path = f"/api/v1/runs/{uuid4()}/execution"
    for path in [create_path, start_path]:
        for headers in [
            [],
            [("Content-Type", "application/x-www-form-urlencoded")],
            [("Content-Type", "application/json"), ("Content-Type", "application/json")],
        ]:
            protected(await env.client.post(path, content=b"{}", headers=headers), 403)
    response = await env.client.post(
        create_path,
        json={"client_idempotency_key": "same-origin"},
        headers={
            "Origin": str(env.client.base_url).rstrip("/"),
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    workflow = protected(response, 201)["workflow"]["id"]
    assert not env.provider.requests
    protected(await env.start(workflow), 202)
    await asyncio.wait_for(env.provider.entered.wait(), 5)


@pytest.mark.anyio
async def test_unstarted_preparation_and_forged_asgi_address_are_not_authority(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeLLMProvider()
    monkeypatch.setattr(application, "create_provider", lambda _settings: fake)
    app = application.create_app(
        HTTPSettings(
            database_url=build_sqlite_url(db.path),
            workflow_api_enabled=True,
            workflow_execution_enabled=True,
        ),
        execution_models=ExecutionModels(
            architect=ModelSettings(model="fake"), critic=ModelSettings(model="fake")
        ),
    )
    with (
        prepare_local_execution_server(app, port=0) as prepared,
        TestClient(app, base_url="http://localhost", client=("127.0.0.1", 50000)) as client,
    ):
        assert not prepared.is_qualified()
        owner: ApplicationExecutionOwner = app.state.workflow_execution_owner
        assert owner.supervisor is None and owner.persistence is None and owner._writer is None
        for path, body in [
            (f"/api/v1/prompt-requests/{db.source.id}/runs", {"client_idempotency_key": "k"}),
            (f"/api/v1/runs/{uuid4()}/execution", {"expected_revision": 0}),
        ]:
            protected(client.post(path, json=body), 503)
        assert owner._writer is None and fake.calls == 0
