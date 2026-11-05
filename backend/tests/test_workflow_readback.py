"""Readback, privacy, graph validation and coherent independent read snapshots."""

import asyncio
import hashlib
import traceback
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite
import pytest
from pydantic import ValidationError
from sqlalchemy import delete, event, text, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import build_sqlite_url
from app.db.read_transactions import create_read_engine, read_snapshot
from app.db.write_transactions import write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.providers.fake import FakeLLMProvider
from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun
from app.modules.prompts.workflow_persistence_contracts import PersistenceError
from app.modules.prompts.workflow_read_repository import WorkflowReadRepository
from workflow_read_fixtures import (
    NOTE,
    PRIVATE,
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


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profiles,limit",
    [
        (["qualified"], 3),
        (["critical"], 1),
        (["unassessable"], 1),
        (["critical", "qualified"], 3),
        (["bad", "critical", "qualified"], 3),
        (["critical", "bad"], 3),
    ],
)
async def test_completed_history_readback(db: Environment, profiles: list[str], limit: int) -> None:
    result = await completed(db, profiles, limit=limit)
    before = dump(db)
    summary = await db.readback.read(result.terminal.workflow.id)
    assert summary.findings == () and summary.history_valid
    assert summary.workflow.completed_rounds == len(profiles)
    assert (
        summary.selected is not None
        and summary.selected.prompt_version_id == result.terminal.best_version_id
    )
    assert [value.evaluation_round for value in summary.evaluations] == list(
        range(1, len(profiles) + 1)
    )
    assert len(summary.operations) == 2 * len(profiles)
    assert [(value.evaluation_round, value.operation) for value in summary.operations] == [
        item
        for number in range(1, len(profiles) + 1)
        for item in ((number, "create" if number == 1 else "revise"), (number, "evaluate"))
    ]
    text = summary.model_dump_json()
    for private in (
        RAW,
        PRIVATE,
        NOTE,
        SECRET_ID,
        "PRIVATE_CRITIC",
        "PRIVATE_APPROVED_FEEDBACK",
        str(result.terminal.workflow.execution_token),
    ):
        assert private not in text and private not in repr(summary)
    content = await db.readback.read_content(result.terminal.workflow.id)
    assert content.source is not None and content.source.raw_prompt == RAW
    assert content.history == result.outcome.history
    assert content.history is not None and len(content.history.rounds) == len(profiles)
    assert dump(db) == before and db.read_connections == 0


@pytest.mark.anyio
async def test_pending_empty_workflow(db: Environment) -> None:
    state = await new_workflow(db)
    result = await db.readback.read(state.id)
    assert result.findings == () and result.history_valid
    assert not result.operations and not result.versions and not result.evaluations
    assert result.selected is None and not result.workflow.claimed


@pytest.mark.anyio
@pytest.mark.parametrize("call", [1, 2, 3, 4])
async def test_unresolved_and_staged_operations_are_not_completed_evaluations(
    db: Environment, call: int
) -> None:
    state = await interrupted(db, call)
    result = await db.readback.read(state.id)
    assert result.findings == () and result.history_valid
    assert result.workflow.completed_rounds == (call - 1) // 2
    assert len(result.evaluations) == (call - 1) // 2
    assert len(result.operations) == call
    last = result.operations[-1]
    assert last.status == "RUNNING" and last.unresolved and last.telemetry is None
    assert last.result_kind is None and last.finished_at is None
    if call % 2 == 0:
        assert result.operations[-2].staged_unevaluated_candidate
    assert not any(value.critic_agent_run_id == last.id for value in result.evaluations)


@pytest.mark.anyio
@pytest.mark.parametrize("count,decision", [(0, "clarify"), (1, "clarify"), (1, "preserve")])
async def test_clarification_and_unchanged_have_no_extra_versions(
    db: Environment, count: int, decision: str
) -> None:
    result = await completed(db, ["critical"] * count, decision=decision)
    summary = await db.readback.read(result.terminal.workflow.id)
    assert summary.findings == () and len(summary.versions) == count
    assert len(summary.evaluations) == count and len(summary.operations) == 2 * count + 1
    assert summary.operations[-1].result_kind == (
        "clarification" if decision == "clarify" else "unchanged"
    )
    assert summary.workflow.clarification_question_count == int(decision == "clarify")
    assert "PRIVATE_READBACK_CLARIFICATION" not in summary.model_dump_json()
    content = await db.readback.read_content(result.terminal.workflow.id)
    assert content.terminal == result.terminal.terminal


@pytest.mark.anyio
@pytest.mark.parametrize("count", [0, 1, 2])
async def test_later_failure_retains_completed_history(db: Environment, count: int) -> None:
    provider = PausingFake(
        [*script(["bad", "critical"][:count]), LLMErrorCode.PROVIDER_UNAVAILABLE]
    )
    state = await new_workflow(db)
    try:
        result = await execute(db, state, provider)
    finally:
        await provider.aclose()
    summary = await db.readback.read(state.id)
    assert summary.findings == () and summary.workflow.status == "FAILED"
    assert len(summary.evaluations) == count and summary.operations[-1].status == "FAILED"
    assert summary.selected is None if count == 0 else summary.selected is not None
    assert result.terminal.best_version_id == (
        None if summary.selected is None else summary.selected.prompt_version_id
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "progress",
        "selected",
        "source",
        "fingerprint",
        "render",
        "canonical",
        "assessment",
        "rubric",
        "scoring",
        "metadata",
        "terminal_reason",
        "finished",
        "missing_evaluation",
    ],
)
async def test_inconsistent_persisted_data_produces_typed_findings_without_repairs(
    db: Environment, mutation: str, caplog: pytest.LogCaptureFixture
) -> None:
    result = await completed(db, ["qualified"])
    reference = result.evaluations[0]
    async with write_transaction(db.writer) as session:
        workflow = await session.get(WorkflowRun, result.terminal.workflow.id)
        version = await session.get(PromptVersion, reference.prompt_version_id)
        evaluation = await session.get(Evaluation, reference.evaluation_id)
        assert workflow is not None and version is not None and evaluation is not None
        if mutation == "progress":
            workflow.completed_rounds = 0
        elif mutation == "selected":
            workflow.final_version_id = None
        elif mutation == "source":
            source = PromptRequest(raw_prompt="Other request")
            session.add(source)
            await session.flush()
            workflow.prompt_request_id = source.id
        elif mutation == "fingerprint":
            evaluation.input_fingerprint = "f" * 64
        elif mutation == "render":
            version.rendered_prompt = PRIVATE
        elif mutation == "canonical":
            version.canonical_prompt = {"private": PRIVATE}
        elif mutation == "assessment":
            evaluation.assessment = {"private": PRIVATE}
        elif mutation == "rubric":
            evaluation.rubric_version = "unsupported"
        elif mutation == "scoring":
            evaluation.scoring_version = "unsupported"
        elif mutation == "metadata":
            await session.execute(
                update(AgentRun)
                .where(AgentRun.id == evaluation.critic_agent_run_id)
                .values(attempt_count=2, error_message=PRIVATE)
            )
        elif mutation == "terminal_reason":
            workflow.stop_reason = "evaluation_limit"
        elif mutation == "finished":
            workflow.finished_at = None
        else:
            await session.delete(evaluation)
    before = dump(db)
    summary = await db.readback.read(result.terminal.workflow.id)
    assert summary.findings and not summary.records_truncated
    assert dump(db) == before
    serialized = summary.model_dump_json()
    assert PRIVATE not in serialized and PRIVATE not in repr(summary) and PRIVATE not in caplog.text


@pytest.mark.anyio
async def test_cross_request_parent_and_creator_are_detected(db: Environment) -> None:
    first = await completed(db, ["critical", "qualified"])
    async with write_transaction(db.writer) as session:
        other_source = PromptRequest(raw_prompt="Other source")
        session.add(other_source)
        await session.flush()
        other = PromptVersion(
            prompt_request_id=other_source.id,
            version_number=1,
            source="user_edit",
            target_mode="generic",
            renderer_name="GenericPromptRenderer",
            renderer_version="1.0.0",
            canonical_prompt={"task": "Other"},
            rendered_prompt="Other",
        )
        session.add(other)
        await session.flush()
        await session.execute(
            update(PromptVersion)
            .where(PromptVersion.id == first.evaluations[-1].prompt_version_id)
            .values(parent_version_id=other.id)
        )
    summary = await db.readback.read(first.terminal.workflow.id)
    assert any(value.code == "version_lineage" for value in summary.findings)
    assert not summary.history_valid


@pytest.mark.anyio
@pytest.mark.parametrize("field", ["version", "critic", "duplicate_round", "delete_version"])
async def test_constraints_reject_invalid_relationships_without_bypass(
    db: Environment, field: str
) -> None:
    first = await completed(
        db, ["critical", "qualified"] if field == "duplicate_round" else ["qualified"]
    )
    second = await completed(db, ["qualified"])
    before = dump(db)
    with pytest.raises(IntegrityError):
        async with write_transaction(db.writer) as session:
            if field == "version":
                await session.execute(
                    update(Evaluation)
                    .where(Evaluation.id == first.evaluations[0].evaluation_id)
                    .values(prompt_version_id=second.evaluations[0].prompt_version_id)
                )
            elif field == "critic":
                second_eval = await session.get(Evaluation, second.evaluations[0].evaluation_id)
                assert second_eval is not None
                await session.execute(
                    update(Evaluation)
                    .where(Evaluation.id == first.evaluations[0].evaluation_id)
                    .values(critic_agent_run_id=second_eval.critic_agent_run_id)
                )
            elif field == "duplicate_round":
                await session.execute(
                    update(Evaluation)
                    .where(Evaluation.id == first.evaluations[1].evaluation_id)
                    .values(evaluation_round=1)
                )
            else:
                await session.execute(
                    delete(PromptVersion).where(
                        PromptVersion.id == first.evaluations[0].prompt_version_id
                    )
                )
    assert dump(db) == before
    assert not (await db.readback.read(first.terminal.workflow.id)).findings


@pytest.mark.anyio
async def test_readback_is_immutable_and_no_provider_is_consulted(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    from alembic import command

    from app.agents.prompt_architect import PromptArchitectAgent
    from app.agents.prompt_critic import PromptCriticAgent

    result = await completed(db, ["qualified"])

    def forbidden(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Inspection must not dispatch or migrate")

    monkeypatch.setattr(FakeLLMProvider, "generate", forbidden)
    monkeypatch.setattr(PromptArchitectAgent, "create", forbidden)
    monkeypatch.setattr(PromptArchitectAgent, "revise", forbidden)
    monkeypatch.setattr(PromptCriticAgent, "evaluate", forbidden)
    monkeypatch.setattr(command, "upgrade", forbidden)
    before = dump(db)
    summary = await db.readback.read(result.terminal.workflow.id)
    with pytest.raises(ValidationError):
        summary.workflow.completed_rounds = 2
    content = await db.readback.read_content(result.terminal.workflow.id)
    assert content.source is not None
    with pytest.raises(ValidationError):
        content.source.raw_prompt = "changed"
    await db.recovery.assess(result.terminal.workflow.id)
    await db.readback.list_operations(result.terminal.workflow.id)
    assert dump(db) == before


@pytest.mark.anyio
async def test_scoped_reads_pagination_and_tie_breakers(db: Environment) -> None:
    ids = [(await completed(db, ["qualified"])).terminal.workflow.id for _ in range(3)]
    # Fixed fixture clock makes all creation timestamps equal: UUID is the tie-breaker.
    first = await db.readback.list_workflows(db.source.id, limit=2)
    second = await db.readback.list_workflows(db.source.id, limit=2, cursor=first.next_cursor)
    assert [value.id for value in (*first.items, *second.items)] == sorted(ids, reverse=True)
    assert len(first.items) == 2 and len(second.items) == 1 and second.next_cursor is None
    assert all(not value.integrity_checked for value in first.items)
    assert (await db.readback.list_workflows(uuid4())).items == ()
    for workflow_id, request_id in ((uuid4(), None), (ids[0], uuid4())):
        with pytest.raises(PersistenceError) as error:
            await db.readback.read(workflow_id, prompt_request_id=request_id)
        assert error.value.code == "not_found"
    for limit in (0, 51, True):
        with pytest.raises(PersistenceError):
            await db.readback.list_workflows(db.source.id, limit=limit)


@pytest.mark.anyio
@pytest.mark.parametrize("count", [1, 3])
async def test_bounded_history_uses_constant_number_of_queries(db: Environment, count: int) -> None:
    result = await completed(db, ["qualified"] if count == 1 else ["bad", "critical", "qualified"])
    statements: list[str] = []

    @event.listens_for(db.reader.sync_engine, "before_cursor_execute")
    def statement(_conn: Any, _cursor: Any, sql: str, *_args: Any) -> None:
        statements.append(sql)

    summary = await db.readback.read(result.terminal.workflow.id)
    assert not summary.findings and len(summary.evaluations) == count
    assert sum(value.lstrip().upper().startswith("SELECT") for value in statements) == 5
    assert statements[0] == "BEGIN"
    assert not any("BEGIN IMMEDIATE" in value or "journal_mode=" in value for value in statements)


@pytest.mark.anyio
async def test_read_boundary_cannot_write_or_create_a_database(
    db: Environment, tmp_path: Path
) -> None:
    state = await new_workflow(db)
    before = dump(db)
    stat = db.path.stat()
    digest = hashlib.sha256(db.path.read_bytes()).hexdigest()
    for disable_guard in (False, True):
        with pytest.raises(OperationalError):
            async with read_snapshot(db.reader) as session:
                if disable_guard:
                    await session.execute(text("PRAGMA query_only=OFF"))
                await session.execute(
                    update(WorkflowRun).where(WorkflowRun.id == state.id).values(state_revision=9)
                )
    assert dump(db) == before
    assert (
        db.path.stat().st_mtime_ns == stat.st_mtime_ns
        and hashlib.sha256(db.path.read_bytes()).hexdigest() == digest
    )
    missing = tmp_path / "missing" / "absent.db"
    reader = create_read_engine(build_sqlite_url(missing))
    try:
        with pytest.raises(OperationalError):
            async with read_snapshot(reader):
                pytest.fail("Missing database must not be opened")
        assert not missing.exists() and not missing.parent.exists()
    finally:
        await reader.dispose()
    with pytest.raises(ValueError):
        async with read_snapshot(db.writer):
            pytest.fail("Writer engine is not a read snapshot engine")


@pytest.mark.anyio
async def test_coherent_snapshot_across_concurrent_evaluation_commit(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await new_workflow(db)
    provider = PausingFake(script(["critical", "qualified"]), block_at=4)
    execution = asyncio.create_task(execute(db, state, provider))
    await asyncio.wait_for(provider.entered.wait(), 10)
    original = AsyncSession.scalar
    header_read, release = asyncio.Event(), asyncio.Event()

    async def scalar(self: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        value = await original(self, *args, **kwargs)
        if self.bind is db.reader and not header_read.is_set():
            header_read.set()
            await release.wait()
        return value

    monkeypatch.setattr(AsyncSession, "scalar", scalar)
    read = asyncio.create_task(db.readback.read(state.id))
    try:
        await asyncio.wait_for(header_read.wait(), 5)
        assert db.read_connections == 1
        provider.release.set()
        committed = await asyncio.wait_for(execution, 10)
        assert committed.terminal.workflow.completed_rounds == 2
    finally:
        release.set()
    old = await asyncio.wait_for(read, 5)
    assert old.findings == () and old.workflow.completed_rounds == len(old.evaluations) == 1
    assert old.operations[-1].status == "RUNNING" and old.workflow.status == "EVALUATING"
    fresh = await db.readback.read(state.id)
    assert fresh.findings == () and fresh.workflow.completed_rounds == len(fresh.evaluations) == 2
    await provider.aclose()


@pytest.mark.anyio
async def test_cancelled_read_rolls_back_and_closes_physical_connection(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    state = await new_workflow(db)
    connections: list[aiosqlite.Connection] = []
    entered = asyncio.Event()
    original = AsyncSession.scalar

    @event.listens_for(db.reader.sync_engine, "connect")
    def connected(connection: Any, _record: Any) -> None:
        connections.append(connection.driver_connection)

    async def pause(self: AsyncSession, *args: Any, **kwargs: Any) -> Any:
        result = await original(self, *args, **kwargs)
        if self.bind is db.reader:
            entered.set()
            await asyncio.Event().wait()
        return result

    monkeypatch.setattr(AsyncSession, "scalar", pause)
    before = dump(db)
    task = asyncio.create_task(db.readback.read(state.id))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert db.read_connections == 0 and dump(db) == before and connections
    for connection in connections:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")


@pytest.mark.anyio
async def test_database_error_diagnostics_do_not_leak_private_exception_context(
    db: Environment, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    state = await new_workflow(db)

    async def failure(*_args: Any, **_kwargs: Any) -> Any:
        raise SQLAlchemyError(PRIVATE)

    monkeypatch.setattr(WorkflowReadRepository, "graph", failure)
    with pytest.raises(PersistenceError) as error:
        await db.readback.read(state.id)
    assert error.value.code == "unavailable" and error.value.__context__ is None
    assert (
        PRIVATE not in "".join(traceback.format_exception(error.value))
        and PRIVATE not in caplog.text
    )
    assert db.read_connections == 0


@pytest.mark.anyio
async def test_foreign_creator_is_not_authorized_by_valid_uuid(db: Environment) -> None:
    from app.modules.prompts.persistence_contracts import OperationBindingStorage
    from app.modules.prompts.workflow_persistence_contracts import OperationIdentity

    result = await completed(db, ["qualified"])
    pending = await new_workflow(db)
    claim = await db.persistence.claim_workflow(
        db.source.id, pending.id, expected_revision=0, lease_seconds=30
    )
    foreign = await db.persistence.reserve_operation(
        claim.fence(),
        OperationIdentity(
            operation_key="create:1",
            agent_name="PromptArchitectAgent",
            agent_version="1.0.0",
            instruction_version="1.0.0",
        ),
        OperationBindingStorage(schema_version="1.0.0", operation="create", evaluation_round=1),
    )
    async with write_transaction(db.writer) as session:
        await session.execute(
            update(PromptVersion)
            .where(PromptVersion.id == result.evaluations[0].prompt_version_id)
            .values(agent_run_id=foreign.agent.id)
        )
    read = await db.readback.read(result.terminal.workflow.id)
    assert any(value.code == "version_lineage" for value in read.findings)
    assert not read.history_valid


@pytest.mark.anyio
async def test_selected_version_requires_own_completed_evaluation(db: Environment) -> None:
    from app.modules.prompts.canonical import CanonicalPrompt

    result = await completed(db, ["qualified"])
    async with write_transaction(db.writer) as session:
        manual = PromptVersion(
            prompt_request_id=db.source.id,
            version_number=99,
            source="user_edit",
            target_mode="generic",
            renderer_name="GenericPromptRenderer",
            renderer_version="1.0.0",
            canonical_prompt=CanonicalPrompt.from_task("Manual").model_dump(mode="json"),
            rendered_prompt="Manual",
        )
        session.add(manual)
        await session.flush()
        manual_id = manual.id
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == result.terminal.workflow.id)
            .values(final_version_id=manual_id)
        )
    read = await db.readback.read(result.terminal.workflow.id)
    assert read.workflow.stored_best_version_id == manual_id
    assert any(value.code == "progress_selection" for value in read.findings)
    assert (
        read.selected is not None
        and read.selected.prompt_version_id == result.terminal.best_version_id
    )


@pytest.mark.anyio
async def test_broken_baseline_and_nonconsecutive_rounds_are_not_renumbered(
    db: Environment,
) -> None:
    first = await completed(db, ["critical", "qualified"])
    other = await completed(db, ["qualified"])
    async with write_transaction(db.writer) as session:
        version = await session.get(PromptVersion, first.evaluations[1].prompt_version_id)
        assert version is not None
        creator = await session.get(AgentRun, version.agent_run_id)
        assert creator is not None and creator.binding_data is not None
        creator.baseline_evaluation_id = other.evaluations[0].evaluation_id
        creator.binding_data = dict(
            creator.binding_data, baseline_evaluation_id=str(other.evaluations[0].evaluation_id)
        )
        await session.execute(
            update(Evaluation)
            .where(Evaluation.id == first.evaluations[1].evaluation_id)
            .values(evaluation_round=3)
        )
    read = await db.readback.read(first.terminal.workflow.id)
    assert read.findings and not read.history_valid
    assert [value.evaluation_round for value in read.evaluations] == [1, 3]
    assert [value.evaluation_round for value in read.versions] == [1, 2]


@pytest.mark.anyio
async def test_unresolved_candidate_reference_in_json_is_an_integrity_finding(
    db: Environment,
) -> None:
    result = await completed(db, ["qualified"])
    async with write_transaction(db.writer) as session:
        evaluation = await session.get(Evaluation, result.evaluations[0].evaluation_id)
        assert evaluation is not None
        critic = await session.get(AgentRun, evaluation.critic_agent_run_id)
        assert critic is not None and critic.parameters is not None
        critic.parameters = dict(critic.parameters, candidate_agent_run_id=str(uuid4()))
    read = await db.readback.read(result.terminal.workflow.id)
    assert any(value.code == "operation_provenance" for value in read.findings)


@pytest.mark.anyio
async def test_unknown_failure_telemetry_stays_unknown(db: Environment) -> None:
    from app.modules.prompts.persistence_contracts import (
        OperationBindingStorage,
        TerminalDetailsStorage,
    )
    from app.modules.prompts.workflow_persistence_contracts import (
        OperationIdentity,
        WorkflowFinalization,
    )

    state = await new_workflow(db)
    claim = await db.persistence.claim_workflow(
        db.source.id, state.id, expected_revision=0, lease_seconds=30
    )
    reserved = await db.persistence.reserve_operation(
        claim.fence(),
        OperationIdentity(
            operation_key="create:1",
            agent_name="PromptArchitectAgent",
            agent_version="1.0.0",
            instruction_version="1.0.0",
        ),
        OperationBindingStorage(schema_version="1.0.0", operation="create", evaluation_round=1),
    )
    await db.persistence.finalize_workflow(
        WorkflowFinalization(
            fence=reserved.workflow.fence(),
            agent_run_id=reserved.agent.id,
            details=TerminalDetailsStorage(
                schema_version="1.0.0",
                execution_status="FAILED",
                termination_reason="provider_failure",
                failed_operation="architect_create",
                error_code=LLMErrorCode.PROVIDER_UNAVAILABLE,
            ),
        )
    )
    read = await db.readback.read(state.id)
    assert read.findings == () and read.operations[0].status == "FAILED"
    assert read.operations[0].telemetry is None and read.selected is None


@pytest.mark.anyio
async def test_oversized_legacy_graph_is_bounded_and_flagged(db: Environment) -> None:
    from app.modules.prompts.persistence_contracts import OperationBindingStorage
    from app.modules.prompts.workflow_persistence_contracts import OperationParameters

    result = await completed(db, ["qualified"])
    async with write_transaction(db.writer) as session:
        for number in range(5):
            session.add(
                AgentRun(
                    workflow_run_id=result.terminal.workflow.id,
                    agent_name="PromptArchitectAgent",
                    agent_version="1.0.0",
                    instruction_version="1.0.0",
                    operation="create",
                    operation_key=f"legacy_duplicate_{number}",
                    evaluation_round=1,
                    status="RUNNING",
                    parameters=OperationParameters(schema_version="1.0.0").model_dump(mode="json"),
                    binding_data=OperationBindingStorage(
                        schema_version="1.0.0", operation="create", evaluation_round=1
                    ).model_dump(mode="json"),
                )
            )
    read = await db.readback.read(result.terminal.workflow.id)
    assert read.records_truncated and not read.history_valid and len(read.operations) == 7
    assert any(value.code == "history_size" for value in read.findings)
    assert (
        await db.recovery.assess(result.terminal.workflow.id)
    ).disposition == "integrity_investigation_required"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite+aiosqlite:///:memory:",
        "sqlite+aiosqlite:///file:example.db?mode=rw&uri=true",
        "sqlite+aiosqlite:///example.db?immutable=1",
        "postgresql+asyncpg://localhost/example",
        "sqlite+aiosqlite://unexpected-host/example.db",
    ],
)
def test_unqualified_or_unsafe_snapshot_url_is_rejected(url: str) -> None:
    with pytest.raises(ValueError):
        create_read_engine(url)
