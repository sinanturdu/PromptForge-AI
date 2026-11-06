"""Preparation only: synthetic readback, real memory probes and loopback HTTP handoff.

No optimizer, workflow runner, provider dispatch or application database is used.
"""

import asyncio
import os
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Barrier
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic_settings import SettingsConfigDict
from starlette.types import Scope

from app.api import execution_admission as admission
from app.api.workflow_security import execution_request_is_local
from app.core.config import Settings
from app.core.execution_environment import (
    ExecutionRuntimeQualification,
    PreparedLocalExecutionServer,
    prepare_local_execution_server,
    qualify_execution_runtime,
)
from app.modules.prompts.workflow_api_schemas import WorkflowStartRequest
from app.modules.prompts.workflow_persistence_contracts import WorkflowConfiguration
from app.modules.prompts.workflow_read_contracts import (
    IntegrityFinding,
    OperationSummary,
    WorkflowReadback,
    WorkflowSummary,
)

NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)
PRIVATE = "PRIVATE_ADMISSION_CANARY"


class AdmissionSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def clean_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKFLOW_API_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_EXECUTION_ENABLED", raising=False)


def pending() -> WorkflowReadback:
    return WorkflowReadback(
        workflow=WorkflowSummary(
            id=uuid4(),
            prompt_request_id=uuid4(),
            status="PENDING",
            state_revision=0,
            current_round=0,
            completed_rounds=0,
            max_evaluation_rounds=3,
            created_at=NOW,
            started_at=None,
            finished_at=None,
            lease_expires_at=None,
            claimed=False,
            source_digest="a" * 64,
            configuration=WorkflowConfiguration(schema_version="1.0.0", timeout_seconds=300),
            stored_best_version_id=None,
            stored_final_score=None,
            stored_qualified=None,
            termination_reason=None,
            error_code=None,
            clarification_question_count=0,
        ),
        operations=(),
        versions=(),
        evaluations=(),
        selected=None,
        history_valid=True,
        records_truncated=False,
        findings=(),
    )


@dataclass
class Environment:
    transport: PreparedLocalExecutionServer
    runtime: ExecutionRuntimeQualification
    settings: AdmissionSettings
    scope: Scope
    workflow: WorkflowReadback
    request: admission.AdmissionRequest

    def admit(
        self, gate: admission.ExecutionAdmissionGate, **overrides: Any
    ) -> admission.AdmissionResult:
        values: dict[str, Any] = dict(
            settings=self.settings,
            scope=self.scope,
            workflow=self.workflow,
            now=NOW,
            transport=self.transport,
            runtime=self.runtime,
        )
        values.update(overrides)
        request = values.pop("request", self.request)
        return gate.admit(request, **values)


@pytest.fixture
async def env() -> AsyncIterator[Environment]:
    app = FastAPI()
    runtime = await qualify_execution_runtime()
    with prepare_local_execution_server(app, port=0) as transport:
        # Only a bare HTTP server is started to verify actual socket handoff. No
        # application lifespan/provider/database or workflow execution is attached.
        config, server = transport.server.config, transport.server
        assert not transport.is_qualified()
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[transport.socket])
        try:
            assert transport.is_qualified()
            port = transport.socket.getsockname()[1]
            workflow = pending()
            yield Environment(
                transport,
                runtime,
                AdmissionSettings(workflow_api_enabled=True, workflow_execution_enabled=True),
                dict(
                    type="http",
                    method="POST",
                    scheme="http",
                    app=app,
                    path=f"/api/v1/runs/{workflow.workflow.id}/execution",
                    client=("127.0.0.1", 50000),
                    server=("0.0.0.0", 1),
                    headers=[
                        (b"host", f"127.0.0.1:{port}".encode()),
                        (b"content-type", b"application/json"),
                    ],
                ),
                workflow,
                admission.AdmissionRequest(
                    prompt_request_id=workflow.workflow.prompt_request_id,
                    workflow_run_id=workflow.workflow.id,
                    start=WorkflowStartRequest(expected_revision=0),
                ),
            )
        finally:
            await server.shutdown(sockets=[transport.socket])
            assert not transport.is_qualified()
    assert transport.socket.fileno() == -1


@pytest.mark.anyio
@pytest.mark.parametrize("api,execution", [(False, False), (True, False)])
async def test_feature_gates_default_off(env: Environment, api: bool, execution: bool) -> None:
    result = env.admit(
        admission.ExecutionAdmissionGate(),
        settings=AdmissionSettings(workflow_api_enabled=api, workflow_execution_enabled=execution),
    )
    assert result.decision.outcome == admission.AdmissionOutcome.FEATURE_DISABLED
    assert result.reservation is None
    assert not AdmissionSettings().workflow_execution_enabled


@pytest.mark.anyio
async def test_first_duplicate_capacity_and_owner_release(env: Environment) -> None:
    gate = admission.ExecutionAdmissionGate()
    first = env.admit(gate)
    assert first.decision.outcome == admission.AdmissionOutcome.PERMITTED
    assert first.reservation is not None and first.decision.to_error() is None
    assert not first.decision.durable_claim and not first.decision.authorizes_provider_call
    assert env.admit(gate).decision.outcome == admission.AdmissionOutcome.DUPLICATE_ADMISSION
    other = pending()
    request = admission.AdmissionRequest(
        prompt_request_id=other.workflow.prompt_request_id,
        workflow_run_id=other.workflow.id,
        start=WorkflowStartRequest(expected_revision=0),
    )
    full = env.admit(gate, workflow=other, request=request)
    assert full.decision.outcome == admission.AdmissionOutcome.CAPACITY_EXHAUSTED
    error = full.decision.to_error()
    assert error is not None and error.status_code == 429 and error.headers == {"Retry-After": "2"}
    assert not gate.release(admission.AdmissionReservation(env.request))
    assert gate.release(first.reservation)
    second = env.admit(gate)
    assert second.reservation is not None
    assert not gate.release(first.reservation)
    assert gate.release(second.reservation)


@pytest.mark.anyio
async def test_concurrent_duplicates_reserve_exactly_one_slot(env: Environment) -> None:
    gate, barrier = admission.ExecutionAdmissionGate(), Barrier(8)

    def attempt(_number: int) -> admission.AdmissionResult:
        barrier.wait(timeout=5)
        return env.admit(gate)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(attempt, range(8)))
    assert sum(r.decision.outcome == admission.AdmissionOutcome.PERMITTED for r in results) == 1
    assert (
        sum(r.decision.outcome == admission.AdmissionOutcome.DUPLICATE_ADMISSION for r in results)
        == 7
    )
    assert all(not r.decision.authorizes_provider_call for r in results)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invalid",
    [
        None,
        {},
        {"start": {}},
        {"workflow_run_id": "bad"},
        {"start": {"expected_revision": -1}},
        {"start": {"expected_revision": True}},
        {"start": {"expected_revision": "0"}},
        {"source_digest": PRIVATE},
    ],
)
async def test_invalid_requests_are_sanitized(env: Environment, invalid: object) -> None:
    value: Any = invalid
    if isinstance(invalid, dict):
        value = dict(env.request.model_dump())
        value.update(invalid)
        if not invalid:
            value = {}
    result = env.admit(admission.ExecutionAdmissionGate(), request=value)
    assert result.decision.outcome == admission.AdmissionOutcome.INVALID_REQUEST
    assert PRIVATE not in repr(result) and PRIVATE not in result.decision.model_dump_json()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "status,expected",
    [
        ("COMPLETED", admission.AdmissionOutcome.ALREADY_COMPLETED),
        ("FAILED", admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE),
        ("CLARIFICATION_REQUIRED", admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE),
        ("RUNNING", admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE),
    ],
)
async def test_terminal_or_unsupported_states_do_not_restart(
    env: Environment, status: str, expected: admission.AdmissionOutcome
) -> None:
    workflow = env.workflow.model_copy(
        update={"workflow": env.workflow.workflow.model_copy(update={"status": status})}
    )
    assert (
        env.admit(admission.ExecutionAdmissionGate(), workflow=workflow).decision.outcome
        == expected
    )


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["GENERATING", "EVALUATING", "REVISING"])
@pytest.mark.parametrize("seconds", [30, 0, -1])
async def test_active_and_expired_leases_never_reclaim(
    env: Environment, status: str, seconds: int
) -> None:
    workflow = env.workflow.model_copy(
        update={
            "workflow": env.workflow.workflow.model_copy(
                update={
                    "status": status,
                    "claimed": True,
                    "lease_expires_at": NOW + timedelta(seconds=seconds),
                }
            )
        }
    )
    result = env.admit(admission.ExecutionAdmissionGate(), workflow=workflow)
    expected = (
        admission.AdmissionOutcome.ALREADY_RUNNING
        if seconds > 0
        else admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
    )
    assert result.decision.outcome == expected and result.reservation is None


@pytest.mark.anyio
async def test_unresolved_operation_and_integrity_findings_take_precedence(
    env: Environment,
) -> None:
    operation = OperationSummary(
        id=uuid4(),
        workflow_run_id=env.workflow.workflow.id,
        operation="create",
        evaluation_round=1,
        status="RUNNING",
        agent_name=PRIVATE,
        agent_version="1",
        instruction_version="1",
        configured_provider=PRIVATE,
        configured_model=PRIVATE,
        started_at=NOW,
        finished_at=None,
        result_kind=None,
        baseline_evaluation_id=None,
        telemetry=None,
        error_code=None,
        incomplete=True,
        unresolved=True,
        staged_unevaluated_candidate=False,
    )
    workflow = env.workflow.model_copy(update={"operations": (operation,)})
    result = env.admit(admission.ExecutionAdmissionGate(), workflow=workflow)
    assert result.decision.outcome == admission.AdmissionOutcome.UNRESOLVED_OPERATION
    assert PRIVATE not in repr(result)
    broken = workflow.model_copy(
        update={"findings": (IntegrityFinding(code="workflow_state", scope="workflow"),)}
    )
    assert (
        env.admit(admission.ExecutionAdmissionGate(), workflow=broken).decision.outcome
        == admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "change",
    [
        {"claimed": True},
        {"state_revision": 1},
        {"current_round": 1},
        {"completed_rounds": 1},
        {"configuration": None},
        {"max_evaluation_rounds": 4},
    ],
)
async def test_pending_must_match_initial_claim_requirements(
    env: Environment, change: dict[str, Any]
) -> None:
    workflow = env.workflow.model_copy(
        update={"workflow": env.workflow.workflow.model_copy(update=change)}
    )
    assert (
        env.admit(admission.ExecutionAdmissionGate(), workflow=workflow).decision.outcome
        == admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
    )


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [None, 301.0, -1.0, float("nan")])
async def test_stored_deadline_must_be_bounded(env: Environment, timeout: float | None) -> None:
    config = WorkflowConfiguration(schema_version="1.0.0").model_copy(
        update={"timeout_seconds": timeout}
    )
    workflow = env.workflow.model_copy(
        update={"workflow": env.workflow.workflow.model_copy(update={"configuration": config})}
    )
    assert (
        env.admit(admission.ExecutionAdmissionGate(), workflow=workflow).decision.outcome
        == admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
    )


@pytest.mark.anyio
async def test_missing_scoped_or_unavailable_workflow(env: Environment) -> None:
    gate = admission.ExecutionAdmissionGate()
    assert (
        env.admit(gate, workflow=None).decision.outcome
        == admission.AdmissionOutcome.WORKFLOW_NOT_FOUND
    )
    assert (
        env.admit(gate, persistence_available=False).decision.outcome
        == admission.AdmissionOutcome.PERSISTENCE_UNAVAILABLE
    )
    request = env.request.model_copy(update={"prompt_request_id": uuid4()})
    assert (
        env.admit(gate, request=request).decision.outcome
        == admission.AdmissionOutcome.WORKFLOW_NOT_FOUND
    )


@pytest.mark.anyio
async def test_shutdown_and_fork_fail_closed(
    env: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = admission.ExecutionAdmissionGate()
    first = env.admit(gate)
    gate.close_admission()
    assert env.admit(gate).decision.outcome == admission.AdmissionOutcome.SHUTTING_DOWN
    assert first.reservation is not None and gate.release(first.reservation)
    assert env.admit(gate).decision.outcome == admission.AdmissionOutcome.SHUTTING_DOWN
    other = admission.ExecutionAdmissionGate()
    pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: pid + 1)
    assert env.admit(other).decision.outcome == admission.AdmissionOutcome.ENVIRONMENT_UNVERIFIED


@pytest.mark.anyio
@pytest.mark.parametrize(
    "runtime",
    [
        None,
        ExecutionRuntimeQualification((3, 12, 7), "3.53.1", "3.53.1"),
        ExecutionRuntimeQualification((3, 12, 16), "3.45.3", "3.53.1"),
        ExecutionRuntimeQualification((3, 12, 16), "3.53.1", "3.45.3"),
        ExecutionRuntimeQualification((3, 12, 16), None, "3.53.1"),
        ExecutionRuntimeQualification((3, 12, 16), "3.53.1", "unverified"),
    ],
)
async def test_runtime_unknown_or_unsafe_denies(
    env: Environment, runtime: ExecutionRuntimeQualification | None
) -> None:
    assert (
        env.admit(admission.ExecutionAdmissionGate(), runtime=runtime).decision.outcome
        == admission.AdmissionOutcome.RUNTIME_NOT_QUALIFIED
    )


@pytest.mark.anyio
async def test_real_connected_runtime_probe() -> None:
    result = await qualify_execution_runtime()
    assert result.is_qualified() and result.python_version == (3, 12, 16)
    assert result.sqlite3_version is not None and result.aiosqlite_version is not None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "key,value",
    [
        ("workers", 2),
        ("reload", True),
        ("proxy_headers", True),
        ("forwarded_allow_ips", "*"),
        ("host", "0.0.0.0"),
        ("root_path", "/proxy"),
    ],
)
async def test_unsafe_server_configuration_invalidates_qualification(
    env: Environment, key: str, value: object
) -> None:
    config = env.transport.server.config
    previous = getattr(config, key)
    try:
        setattr(config, key, value)
        assert (
            env.admit(admission.ExecutionAdmissionGate()).decision.outcome
            == admission.AdmissionOutcome.ENVIRONMENT_UNVERIFIED
        )
    finally:
        setattr(config, key, previous)


@pytest.mark.anyio
async def test_asgi_listener_claim_never_qualifies_transport(env: Environment) -> None:
    scope = dict(env.scope, server=("127.0.0.1", env.transport.socket.getsockname()[1]))
    assert (
        env.admit(admission.ExecutionAdmissionGate(), transport=None, scope=scope).decision.outcome
        == admission.AdmissionOutcome.ENVIRONMENT_UNVERIFIED
    )
    with prepare_local_execution_server(env.transport.application, port=0) as unstarted:
        assert not unstarted.is_qualified()
        assert (
            env.admit(admission.ExecutionAdmissionGate(), transport=unstarted).decision.outcome
            == admission.AdmissionOutcome.ENVIRONMENT_UNVERIFIED
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "peer,headers",
    [
        (None, []),
        (("testclient", 1), []),
        (("192.0.2.1", 1), []),
        (("::ffff:192.0.2.1", 1), []),
        (("127.0.0.1", 1), [(b"host", b"evil.example")]),
        (("127.0.0.1", 1), [(b"origin", b"https://evil.example")]),
        (("127.0.0.1", 1), [(b"forwarded", b"for=127.0.0.1")]),
        (("127.0.0.1", 1), [(b"x-forwarded-for", b"127.0.0.1")]),
        (("127.0.0.1", 1), [(b"sec-fetch-site", b"cross-site")]),
    ],
)
async def test_request_security_denial(
    env: Environment, peer: Any, headers: list[tuple[bytes, bytes]]
) -> None:
    names = {k for k, _ in headers}
    scope = dict(
        env.scope,
        client=peer,
        headers=[(k, v) for k, v in env.scope["headers"] if k not in names] + headers,
    )
    assert (
        env.admit(admission.ExecutionAdmissionGate(), scope=scope).decision.outcome
        == admission.AdmissionOutcome.SECURITY_DENIED
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "content_type",
    [
        b"text/plain",
        b"application/x-www-form-urlencoded",
        b"multipart/form-data",
        b"application/json, text/plain",
    ],
)
async def test_execution_requires_json(env: Environment, content_type: bytes) -> None:
    scope = dict(
        env.scope,
        headers=[(k, v) for k, v in env.scope["headers"] if k != b"content-type"]
        + [(b"content-type", content_type)],
    )
    assert not execution_request_is_local(scope)
    assert (
        env.admit(admission.ExecutionAdmissionGate(), scope=scope).decision.outcome
        == admission.AdmissionOutcome.SECURITY_DENIED
    )


@pytest.mark.parametrize("host", ["0.0.0.0", "::", "localhost", "192.0.2.1", PRIVATE])
def test_preparation_never_binds_unverified_address(host: str) -> None:
    with (
        pytest.raises(ValueError, match="explicit loopback"),
        prepare_local_execution_server(FastAPI(), host=host, port=0),
    ):
        pytest.fail("Unsafe address was accepted")


def test_occupied_listener_fails_and_prepared_socket_closes() -> None:
    with prepare_local_execution_server(FastAPI(), port=0) as prepared:
        port = prepared.socket.getsockname()[1]
        with pytest.raises(OSError), prepare_local_execution_server(FastAPI(), port=port):
            pytest.fail("Occupied socket was reused")
    assert prepared.socket.fileno() == -1 and not prepared.is_qualified()


@pytest.mark.parametrize("outcome", list(admission.AdmissionOutcome))
def test_error_contracts_are_fixed_and_private_free(outcome: admission.AdmissionOutcome) -> None:
    decision = admission.AdmissionDecision(outcome=outcome)
    error = decision.to_error()
    assert not decision.durable_claim and not decision.authorizes_provider_call
    if outcome == admission.AdmissionOutcome.PERMITTED:
        assert error is None
    else:
        assert error is not None and error.status_code in {403, 404, 409, 422, 429, 503}
        assert error.__context__ is None
        for private in (PRIVATE, "execution_token", "source_digest", "provider"):
            assert private not in str(error)


def test_resource_bounds_are_explicit_and_derive_from_existing_round_cap() -> None:
    assert admission.MAX_CONCURRENT_WORKFLOWS == admission.MAX_CONCURRENT_PROVIDER_CALLS == 1
    assert admission.MAX_WAITING_WORKFLOWS == 0
    assert admission.MAX_AGENT_OPERATIONS == 6 and admission.MAX_ATTEMPTS_PER_WORKFLOW == 24
    assert admission.LEASE_SECONDS > admission.MAX_GATEWAY_DEADLINE_SECONDS
    assert admission.EXECUTION_DEADLINE_SECONDS == 300 and admission.SHUTDOWN_GRACE_SECONDS == 30


@pytest.mark.anyio
async def test_admission_itself_creates_no_tasks_or_mutations(env: Environment) -> None:
    tasks = set(asyncio.all_tasks())
    before = env.workflow.model_dump_json()
    request = env.request.model_dump_json()
    result = env.admit(admission.ExecutionAdmissionGate())
    assert result.decision.outcome == admission.AdmissionOutcome.PERMITTED
    assert set(asyncio.all_tasks()) == tasks
    assert env.workflow.model_dump_json() == before and env.request.model_dump_json() == request


@pytest.mark.anyio
async def test_ipv6_listener_qualifies_only_after_real_handoff() -> None:
    app = FastAPI()
    with prepare_local_execution_server(app, host="::1", port=0) as transport:
        assert not transport.is_qualified()
        server, config = transport.server, transport.server.config
        config.load()
        server.lifespan = config.lifespan_class(config)
        await server.startup(sockets=[transport.socket])
        try:
            assert transport.is_qualified()
            port = transport.socket.getsockname()[1]
            scope: Scope = dict(
                type="http",
                method="POST",
                scheme="http",
                app=app,
                client=("::1", 50000),
                server=("0.0.0.0", 1),
                path="/unused",
                headers=[
                    (b"host", f"[::1]:{port}".encode()),
                    (b"origin", f"http://[::1]:{port}".encode()),
                    (b"content-type", b"application/json"),
                ],
            )
            assert execution_request_is_local(scope)
            scope["client"] = ("::ffff:127.0.0.1", 50000)
            assert execution_request_is_local(scope)
        finally:
            await server.shutdown(sockets=[transport.socket])
        assert not transport.is_qualified()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "settings", [{"llm_call_deadline_seconds": 91.0}, {"llm_request_timeout_seconds": 21.0}]
)
async def test_gateway_settings_cannot_exceed_local_budget(
    env: Environment, settings: dict[str, float]
) -> None:
    config = AdmissionSettings.model_validate(
        dict(workflow_api_enabled=True, workflow_execution_enabled=True, **settings)
    )
    assert (
        env.admit(admission.ExecutionAdmissionGate(), settings=config).decision.outcome
        == admission.AdmissionOutcome.WORKFLOW_NOT_ELIGIBLE
    )
