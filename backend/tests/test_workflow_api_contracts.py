"""Offline contract/privacy tests; workflow reads are gated and execution stays inactive."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import aiosqlite
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError
from pydantic_settings import SettingsConfigDict
from sqlalchemy import update

import app.main as application
from app.api.error_handlers import register_exception_handlers
from app.core.config import Settings, build_sqlite_url
from app.db import write_transactions
from app.db.write_transactions import write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.providers.fake import FakeLLMProvider
from app.models import WorkflowRun
from app.models.enums import WorkflowRunStatus
from app.modules.prompts import workflow_api_schemas as api
from app.modules.prompts.workflow_persistence_contracts import WorkflowConfiguration
from app.modules.prompts.workflow_read_contracts import ReadTelemetry
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
    script,
)
from workflow_read_fixtures import db as db


class IsolatedWorkflowSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_workflow_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKFLOW_API_ENABLED", raising=False)
    monkeypatch.delenv("WORKFLOW_EXECUTION_ENABLED", raising=False)


def test_default_off_and_explicit_env_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = IsolatedWorkflowSettings()
    assert not settings.workflow_api_enabled and not settings.workflow_execution_enabled
    monkeypatch.setenv("WORKFLOW_API_ENABLED", "true")
    assert IsolatedWorkflowSettings().workflow_api_enabled
    assert not IsolatedWorkflowSettings().workflow_execution_enabled
    monkeypatch.setenv("WORKFLOW_EXECUTION_ENABLED", "TRUE")
    assert IsolatedWorkflowSettings().workflow_execution_enabled
    monkeypatch.setenv("WORKFLOW_API_ENABLED", "false")
    with pytest.raises(ValidationError, match="requires explicit workflow API activation"):
        IsolatedWorkflowSettings()


@pytest.mark.parametrize("flag", ["workflow_api_enabled", "workflow_execution_enabled"])
@pytest.mark.parametrize("invalid", ["yes", "1", "false ", "invalid", 1, None])
def test_ambiguous_feature_values_are_rejected(flag: str, invalid: Any) -> None:
    with pytest.raises(ValidationError, match="must be true or false"):
        IsolatedWorkflowSettings(**{flag: invalid})


@pytest.mark.parametrize(
    "api_enabled,execution_enabled", [(False, False), (True, False), (True, True)]
)
def test_flags_gate_read_routes_without_writers_or_database_connections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_enabled: bool, execution_enabled: bool
) -> None:
    database = tmp_path / "not-provisioned" / "api.db"
    writer = Mock(side_effect=AssertionError("Workflow writer must stay inactive"))
    connect = Mock(side_effect=AssertionError("Startup/health must not open a database"))
    monkeypatch.setattr(write_transactions, "create_write_engine", writer)
    monkeypatch.setattr(aiosqlite, "connect", connect)
    provider = FakeLLMProvider()
    monkeypatch.setattr(application, "create_provider", lambda _settings: provider)
    app = application.create_app(
        IsolatedWorkflowSettings(
            database_url=build_sqlite_url(database),
            workflow_api_enabled=api_enabled,
            workflow_execution_enabled=execution_enabled,
        )
    )
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/v1/health").status_code == 200
        paths = client.get("/api/v1/openapi.json").json()["paths"]
        expected = {
            "/api/v1/health",
            "/api/v1/prompt-requests",
            "/api/v1/prompt-requests/{id}",
        }
        if api_enabled:
            expected |= {
                "/api/v1/runs/{id}",
                "/api/v1/prompt-requests/{id}/runs",
                "/api/v1/runs/{id}/history",
                "/api/v1/runs/{id}/history/content",
                "/api/v1/runs/{id}/result",
                "/api/v1/runs/{id}/recovery",
            }
        if execution_enabled:
            expected.add("/api/v1/runs/{id}/execution")
        assert set(paths) == expected
        if api_enabled:
            posts = {path for path, item in paths.items() if "runs" in path and "post" in item}
            assert posts == (
                {"/api/v1/prompt-requests/{id}/runs", "/api/v1/runs/{id}/execution"}
                if execution_enabled
                else set()
            )
        for method, path in [
            ("POST", f"/api/v1/prompt-requests/{uuid4()}/runs"),
            ("POST", f"/api/v1/runs/{uuid4()}/execution"),
        ]:
            response = client.request(method, path, json={})
            assert response.status_code in ({503} if execution_enabled else {404, 405})
            assert response.headers["content-type"].startswith("application/problem+json")
        if not api_enabled:
            assert client.get(f"/api/v1/runs/{uuid4()}").status_code == 404
        assert provider.calls == 0
    assert provider.closed
    writer.assert_not_called()
    connect.assert_not_called()
    assert not database.parent.exists()


@pytest.mark.parametrize("status", list(WorkflowRunStatus))
def test_only_actual_workflow_statuses_are_serialized(status: WorkflowRunStatus) -> None:
    result = api.WorkflowIdentityResponse.model_validate(
        dict(id=str(uuid4()), prompt_request_id=str(uuid4()), status=status.value)
    )
    assert result.model_dump(mode="json")["status"] == status.value


@pytest.mark.parametrize(
    "model,payload",
    [
        (
            api.WorkflowIdentityResponse,
            dict(id="bad", prompt_request_id=str(uuid4()), status="PENDING"),
        ),
        (
            api.WorkflowIdentityResponse,
            dict(id=str(uuid4()), prompt_request_id="bad", status="PENDING"),
        ),
        (
            api.WorkflowIdentityResponse,
            dict(id=str(uuid4()), prompt_request_id=str(uuid4()), status="RUNNING"),
        ),
        (api.WorkflowCreateRequest, {}),
        *[
            (api.WorkflowCreateRequest, dict(client_idempotency_key=value))
            for value in ("", " ", "a b", "a\nb", "x" * 257, 123)
        ],
        *[
            (
                api.WorkflowCreateRequest,
                dict(client_idempotency_key="request-1", max_evaluation_rounds=value),
            )
            for value in (0, 4, True, "3")
        ],
        (api.WorkflowCreateRequest, dict(client_idempotency_key="key", provider="openai")),
        (api.WorkflowStartRequest, {}),
        *[(api.WorkflowStartRequest, dict(expected_revision=value)) for value in (-1, True, "0")],
        (api.WorkflowStartRequest, dict(expected_revision=0, execution_token=str(uuid4()))),
        (api.WorkflowHistoryQuery, dict(limit=51)),
        (api.WorkflowHistoryQuery, dict(before_workflow_run_id=str(uuid4()))),
        (api.WorkflowHistoryQuery, dict(before_created_at="2026-11-06T12:00:00")),
    ],
)
def test_malformed_identifiers_and_payloads_are_rejected(
    model: type[BaseModel], payload: dict[str, Any]
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_idempotency_and_cursor_contracts_match_domain() -> None:
    key = "request:v1_" + "x" * 245
    payload = api.WorkflowCreateRequest(client_idempotency_key=key)
    assert payload.client_idempotency_key == key and len(key) == 256
    config = WorkflowConfiguration(
        schema_version="1.0.0", max_evaluation_rounds=payload.max_evaluation_rounds
    )
    assert config.max_evaluation_rounds == 3
    assert api.WorkflowStartRequest(expected_revision=0).expected_revision == 0
    query = api.WorkflowHistoryQuery.model_validate(
        dict(
            limit="20",
            before_created_at="2026-11-06T12:00:00Z",
            before_workflow_run_id=str(uuid4()),
        )
    )
    cursor = query.to_cursor()
    assert cursor is not None and cursor.created_at.tzinfo is not None
    assert cursor.workflow_run_id == query.before_workflow_run_id
    assert api.WorkflowHistoryQuery().to_cursor() is None
    assert api.WorkflowHistoryPageResponse(items=(), next_cursor=None).model_dump(
        mode="json"
    ) == dict(items=[], next_cursor=None)


@pytest.mark.anyio
@pytest.mark.parametrize("profile", ["qualified", "critical", "unassessable"])
async def test_metadata_content_result_and_null_score_boundaries(
    db: Environment, profile: str
) -> None:
    result = await completed(db, [profile], limit=1)
    before = dump(db)
    value = await db.readback.read_content(result.terminal.workflow.id)
    metadata = api.WorkflowMetadataHistoryResponse.from_readback(value.summary)
    content = api.WorkflowContentHistoryResponse.from_readback(value)
    final = api.WorkflowResultResponse.from_readback(value)
    assert final.workflow.status == WorkflowRunStatus.COMPLETED
    assert final.workflow.selected is not None and final.candidate is not None
    assert (
        final.workflow.selected.quality_status
        == {"qualified": "qualified", "critical": "not_qualified", "unassessable": "unassessable"}[
            profile
        ]
    )
    assert final.evaluation is not None and final.assessment is not None
    assert final.candidate.rendered_prompt == RAW
    assert content.source is not None and content.source.raw_prompt == RAW
    assert content.versions[0].canonical_prompt == final.candidate.canonical_prompt
    assert content.versions[0].rendered_prompt == RAW
    if profile == "unassessable":
        assert metadata.workflow.stored_final_score is None
        assert (
            metadata.workflow.selected is not None
            and metadata.workflow.selected.total_score is None
        )
        assert metadata.evaluations[0].stored_total_score is None
        assert json.loads(final.model_dump_json())["evaluation"]["stored_total_score"] is None
    for private in (
        RAW,
        PRIVATE,
        NOTE,
        QUESTION,
        SECRET_ID,
        "PRIVATE_CRITIC",
        "PRIVATE_APPROVED",
        str(result.terminal.workflow.execution_token),
    ):
        assert private not in metadata.model_dump_json()
        assert private not in repr(content) and private not in repr(final)
    for model in (metadata, content, final):
        wire = model.model_dump_json()
        assert type(model).model_validate_json(wire) == model
        for private in (
            SECRET_ID,
            "execution_token",
            "source_digest",
            "config_snapshot",
            "configured_provider",
            "configured_model",
        ):
            assert private not in wire
    metadata_schema = json.dumps(api.WorkflowMetadataHistoryResponse.model_json_schema())
    for forbidden in (
        "raw_prompt",
        "canonical_prompt",
        "rendered_prompt",
        "rationale",
        "questions",
    ):
        assert forbidden not in metadata_schema
    assert dump(db) == before


@pytest.mark.anyio
async def test_pending_telemetry_and_result_not_ready(db: Environment) -> None:
    state = await new_workflow(db)
    content = await db.readback.read_content(state.id)
    progress = api.WorkflowProgressResponse.from_readback(content.summary)
    assert progress.selected is None and progress.stored_final_score is None
    assert progress.completed_rounds == 0 and progress.max_evaluation_rounds == 3
    with pytest.raises(ValueError, match="requires a terminal execution"):
        api.WorkflowResultResponse.from_readback(content)
    assert "telemetry" not in api.WorkflowIdentityResponse.model_fields


@pytest.mark.anyio
async def test_unknown_operation_telemetry_remains_null(db: Environment) -> None:
    state = await interrupted(db, 1)
    before = dump(db)
    metadata = api.WorkflowMetadataHistoryResponse.from_readback(await db.readback.read(state.id))
    assert metadata.operations[0].unresolved and metadata.operations[0].telemetry is None
    assert metadata.evaluations == () and metadata.workflow.selected is None
    assert json.loads(metadata.model_dump_json())["operations"][0]["telemetry"] is None
    assert dump(db) == before


@pytest.mark.anyio
async def test_failed_execution_can_retain_unqualified_candidate(db: Environment) -> None:
    provider = PausingFake([*script(["critical"]), LLMErrorCode.PROVIDER_UNAVAILABLE])
    try:
        result = await execute(db, await new_workflow(db), provider)
    finally:
        await provider.aclose()
    final = api.WorkflowResultResponse.from_readback(
        await db.readback.read_content(result.terminal.workflow.id)
    )
    assert final.workflow.status == WorkflowRunStatus.FAILED
    assert final.workflow.termination_reason == "provider_failure"
    assert final.workflow.selected is not None and not final.workflow.selected.qualified
    assert final.candidate is not None and final.candidate.rendered_prompt == RAW


@pytest.mark.anyio
async def test_result_matches_selected_evaluation_in_multiple_rounds(db: Environment) -> None:
    result = await completed(db, ["critical", "bad"])
    content = await db.readback.read_content(result.terminal.workflow.id)
    history = api.WorkflowContentHistoryResponse.from_readback(content)
    final = api.WorkflowResultResponse.from_readback(content)
    assert len(history.versions) == len(history.evaluations) == 2
    assert final.workflow.selected is not None and final.workflow.selected.evaluation_round == 1
    assert final.candidate is not None and final.evaluation is not None
    assert final.candidate.version.id == result.terminal.best_version_id
    assert final.candidate.version.id == final.evaluation.prompt_version_id
    assert final.assessment == history.evaluations[0].assessment
    assert final.candidate.rendered_prompt == RAW
    assert history.versions[1].change_summary == (NOTE,)


def test_known_telemetry_excludes_private_provider_identifiers() -> None:
    telemetry = api.WorkflowTelemetryResponse.from_readback(
        ReadTelemetry(
            provider=SECRET_ID,
            requested_model=SECRET_ID,
            resolved_model=SECRET_ID,
            usage=None,
            attempts=1,
            retries=0,
            repair_attempted=False,
            duration_ms=10,
            usage_complete=False,
        )
    )
    assert telemetry.usage is None and not telemetry.usage_complete
    assert telemetry.attempts == 1 and telemetry.duration_ms == 10
    assert SECRET_ID not in telemetry.model_dump_json()


@pytest.mark.anyio
async def test_clarification_has_questions_without_fabricated_candidate(db: Environment) -> None:
    result = await completed(db, [], decision="clarify")
    final = api.WorkflowResultResponse.from_readback(
        await db.readback.read_content(result.terminal.workflow.id)
    )
    assert final.workflow.status == WorkflowRunStatus.CLARIFICATION_REQUIRED
    assert final.candidate is None and final.evaluation is None and final.assessment is None
    assert final.workflow.selected is None and final.clarification_questions == (QUESTION,)
    assert QUESTION not in repr(final)


@pytest.mark.anyio
async def test_integrity_findings_withhold_trusted_result_without_concealing_records(
    db: Environment,
) -> None:
    result = await completed(db, ["qualified"])
    state = result.terminal.workflow
    async with write_transaction(db.writer) as session:
        await session.execute(
            update(WorkflowRun).where(WorkflowRun.id == state.id).values(final_version_id=None)
        )
    before = dump(db)
    content = await db.readback.read_content(state.id)
    final = api.WorkflowResultResponse.from_readback(content)
    history = api.WorkflowContentHistoryResponse.from_readback(content)
    assert final.workflow.findings and final.workflow.selected is None
    assert final.candidate is None and final.evaluation is None and final.assessment is None
    assert len(history.metadata.evaluations) == len(history.versions) == 1
    recovery = api.WorkflowRecoveryResponse(assessment=await db.recovery.assess(state.id))
    assert recovery.assessment.disposition == "integrity_investigation_required"
    assert not recovery.assessment.committed_result_reconciliation_available
    assert not recovery.assessment.authorizes_mutation
    assert not recovery.assessment.automatic_provider_replay_safe
    assert dump(db) == before


def test_contracts_integrate_with_openapi_without_activating_production_routes() -> None:
    # A throwaway schema-only app exercises FastAPI's actual schema composition.
    app = FastAPI()
    register_exception_handlers(app)

    @app.post("/contract", response_model=api.WorkflowResultResponse)
    def contract(payload: api.WorkflowCreateRequest) -> None:
        raise AssertionError("Only validation and OpenAPI generation are exercised")

    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
        assert "WorkflowCreateRequest" in schema["components"]["schemas"]
        assert "WorkflowResultResponse" in schema["components"]["schemas"]
        response = client.post(
            "/contract", json=dict(client_idempotency_key=SECRET_ID, provider="private-secret")
        )
        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/problem+json")
        assert response.json()["error_code"] == "validation_error"
        assert SECRET_ID not in response.text and "private-secret" not in response.text
    for model in (
        api.WorkflowCreateRequest,
        api.WorkflowStartRequest,
        api.WorkflowHistoryQuery,
        api.WorkflowCreateResponse,
        api.WorkflowStartResponse,
        api.WorkflowProgressResponse,
        api.WorkflowHistoryPageResponse,
        api.WorkflowMetadataHistoryResponse,
        api.WorkflowContentHistoryResponse,
        api.WorkflowResultResponse,
        api.WorkflowRecoveryResponse,
        api.WorkflowProblemDetails,
    ):
        wire_schema = json.dumps(model.model_json_schema(mode="serialization"))
        for forbidden in (
            "execution_token",
            "operation_key",
            "client_idempotency_key_digest",
            "upstream_request_id",
            "openai_api_key",
            "configured_provider",
            "requested_model",
            "resolved_model",
            "parameters",
            "traceback",
        ):
            assert forbidden not in wire_schema
