"""Offline real-coordinator integration against temporary migrated WAL databases."""

import asyncio
import json
import sqlite3
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.core.config import build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.sqlite_runtime import require_safe_sqlite_wal
from app.db.write_transactions import create_write_engine, write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMRequest, LLMResponse, ModelSettings
from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimizer import optimize_prompt
from app.modules.prompts.persistent_optimizer import (
    PersistentExecutionIdentity,
    PersistentOptimizationResult,
    execute_persisted_optimizer,
)
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    EvaluationReceipt,
    OperationReceipt,
    PersistenceError,
    WorkflowConfiguration,
    WorkflowFence,
    WorkflowFinalization,
    WorkflowState,
)
from app.modules.prompts.workflow_repository import (
    AgentRunRepository,
    WorkflowRunRepository,
)
from app.scoring.rubric import CRITERIA

RAW = '  Explain Python 3.12. Keep "Merhaba".\n```python\ndef f():\n    return 1\n```\n'
PRIVATE = "PRIVATE_PERSISTENT_EXECUTION_SENTINEL"
NOTE = "PRIVATE_ARCHITECT_NOTE"
QUESTION = "Which conflicting source is authoritative?"
NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Environment:
    path: Path
    engine: AsyncEngine
    service: WorkflowPersistenceService
    source_id: UUID
    clock: Clock
    connections: int = 0


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Environment]:
    require_safe_sqlite_wal(sqlite3.sqlite_version)
    path = tmp_path / "execution.db"
    command.upgrade(build_alembic_config(build_sqlite_url(path)), "head")
    engine = create_write_engine(build_sqlite_url(path))
    clock = Clock()
    try:
        async with write_transaction(engine) as session:
            assert await session.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await session.scalar(text("PRAGMA synchronous")) == 2
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            source = PromptRequest(raw_prompt=RAW, user_context=PRIVATE)
            session.add(source)
            await session.flush()
            source_id = source.id
        environment = Environment(
            path, engine, WorkflowPersistenceService(engine, clock=clock), source_id, clock
        )

        @event.listens_for(engine.sync_engine, "checkout")
        def checkout(*_args: Any) -> None:
            environment.connections += 1

        @event.listens_for(engine.sync_engine, "checkin")
        def checkin(*_args: Any) -> None:
            environment.connections -= 1

        yield environment
        assert environment.connections == 0
    finally:
        await engine.dispose()


def wire(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(provider="fake", finish_reason="complete", text=json.dumps(data))


def author(decision: str = "preserve", *, draft: int = 1, revision: bool = False) -> LLMResponse:
    data: dict[str, Any] = dict(
        decision=decision,
        canonical_prompt=CanonicalPrompt.from_task(RAW + f"\nRevision {draft}.").model_dump(
            mode="json"
        )
        if decision == "rewrite"
        else None,
        changes=[NOTE] if decision == "rewrite" else [],
        questions=[QUESTION] if decision == "clarify" else [],
    )
    if revision:
        data["addressed_issue_keys"] = ["issue_001"] if decision == "rewrite" else []
    return wire(data)


def judge(profile: str = "qualified") -> LLMResponse:
    evidence = [dict(source="raw_prompt", quote="Python 3.12")]
    criteria = {
        key: dict(score=10, rationale="Offline fixture.", evidence=evidence)
        for key, _, _ in CRITERIA
    }
    issues = []
    if profile not in {"qualified", "no_feedback"}:
        criteria["constraints_and_scope"]["score"] = 3 if profile == "bad" else 6
        issues.append(
            dict(
                criterion="constraints_and_scope",
                code="missing_requirement",
                status="supported",
                severity="major" if profile == "unassessable" else "critical",
                description="Fixture requirement missing.",
                recommendation="Restore the explicit requirement.",
                evidence=evidence,
            )
        )
    if profile in {"unassessable", "no_feedback"}:
        criteria["clarity"] = dict(score=None, rationale="Unknown fixture detail.", evidence=[])
    return wire(dict(criteria=criteria, issues=issues, summary="Offline, not semantic proof."))


class RecordingFake(FakeLLMProvider):
    def __init__(self, db: Environment, script: list[LLMResponse | LLMErrorCode]) -> None:
        super().__init__(script)
        self.db = db
        self.requests: list[LLMRequest] = []
        self.on_call: Callable[[int], Awaitable[None]] | None = None
        self.phases: list[tuple[str, int, str]] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        assert self.db.connections == 0  # All service connections actually closed.
        # An independent writer can acquire BEGIN IMMEDIATE during every provider call.
        probe = create_write_engine(build_sqlite_url(self.db.path))
        try:
            async with write_transaction(probe) as session:
                operation = await session.scalar(
                    select(AgentRun)
                    .join(WorkflowRun, WorkflowRun.id == AgentRun.workflow_run_id)
                    .where(
                        AgentRun.status == "RUNNING",
                        WorkflowRun.prompt_request_id == self.db.source_id,
                    )
                )
                assert operation is not None
                workflow = await session.get(WorkflowRun, operation.workflow_run_id)
                assert workflow is not None and workflow.execution_token is not None
                self.phases.append(
                    (workflow.status, workflow.completed_rounds, operation.operation)
                )
                assert operation.evaluation_round == workflow.completed_rounds + 1
                if operation.operation == "evaluate":
                    creators = (
                        await session.scalars(
                            select(AgentRun).where(
                                AgentRun.workflow_run_id == workflow.id,
                                AgentRun.evaluation_round == operation.evaluation_round,
                                AgentRun.operation != "evaluate",
                            )
                        )
                    ).all()
                    assert len(creators) == 1 and creators[0].status == "COMPLETED"
                    assert creators[0].result_kind == "candidate"
                    payload = json.loads(request.messages[-1].content)
                    assert set(payload) == {
                        "original_request",
                        "rendered_prompt",
                        "target_mode",
                        "renderer_name",
                        "renderer_version",
                    }
                    assert NOTE not in request.messages[-1].content
        finally:
            await probe.dispose()
        if self.on_call is not None:
            await self.on_call(len(self.requests))
        return await super().generate(request)


def agents(provider: FakeLLMProvider) -> tuple[PromptArchitectAgent, PromptCriticAgent]:
    gateway = LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))
    return (
        PromptArchitectAgent(gateway, ModelSettings(model="fake")),
        PromptCriticAgent(gateway, ModelSettings(model="fake")),
    )


async def workflow(db: Environment, limit: int = 3, timeout: float | None = None) -> WorkflowState:
    receipt = await db.service.create_workflow(
        db.source_id,
        WorkflowConfiguration(
            schema_version="1.0.0", max_evaluation_rounds=limit, timeout_seconds=timeout
        ),
    )
    return receipt.workflow


async def execute(
    db: Environment, state: WorkflowState, provider: FakeLLMProvider
) -> PersistentOptimizationResult:
    architect, critic = agents(provider)
    return await asyncio.wait_for(
        execute_persisted_optimizer(
            db.service,
            architect,
            critic,
            prompt_request_id=db.source_id,
            workflow_run_id=state.id,
            identity=PersistentExecutionIdentity(
                architect_provider="fake",
                architect_settings=ModelSettings(model="fake"),
                critic_provider="fake",
                critic_settings=ModelSettings(model="fake"),
            ),
            lease_seconds=30,
        ),
        15,
    )


def dump(db: Environment) -> str:
    with sqlite3.connect(db.path) as connection:
        return "\n".join(connection.iterdump())


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profiles,limit,reason,quality",
    [
        (["qualified"], 3, "quality_passed", "qualified"),
        (["critical", "qualified"], 3, "quality_passed", "qualified"),
        (["bad", "critical", "qualified"], 3, "quality_passed", "qualified"),
        (["bad", "critical", "critical"], 3, "evaluation_limit", "not_qualified"),
        (["critical"], 1, "evaluation_limit", "not_qualified"),
        (["unassessable"], 1, "evaluation_limit", "unassessable"),
        (["no_feedback"], 3, "no_actionable_feedback", "unassessable"),
        (["critical", "bad"], 3, "repeated_revision_attempt", "not_qualified"),
        (["critical", "critical"], 3, "repeated_revision_attempt", "not_qualified"),
    ],
)
async def test_real_optimizer_commits_rounds_and_preserves_policies(
    db: Environment, profiles: list[str], limit: int, reason: str, quality: str
) -> None:
    script: list[LLMResponse | LLMErrorCode] = []
    for number, profile in enumerate(profiles, 1):
        script.extend(
            [
                author("preserve" if number == 1 else "rewrite", draft=number, revision=number > 1),
                judge(profile),
            ]
        )
    provider = RecordingFake(db, script)
    state = await workflow(db, limit)
    result = await execute(db, state, provider)
    assert not provider.closed  # Caller retains ownership.
    assert result.outcome.termination_reason == reason
    assert result.outcome.quality_status == quality
    assert result.outcome.history is not None
    assert result.terminal.workflow.status == "COMPLETED"
    assert result.terminal.workflow.completed_rounds == len(profiles)
    assert provider.calls == 2 * len(profiles)
    assert len(result.evaluations) == len(profiles)
    async with write_transaction(db.engine) as session:
        versions = (
            await session.scalars(select(PromptVersion).order_by(PromptVersion.evaluation_round))
        ).all()
        evaluations = (
            await session.scalars(select(Evaluation).order_by(Evaluation.evaluation_round))
        ).all()
        operations = (await session.scalars(select(AgentRun))).all()
        assert len(versions) == len(evaluations) == len(profiles)
        assert [value.version_number for value in versions] == list(range(1, len(profiles) + 1))
        assert versions[0].rendered_prompt == RAW
        assert all(value.status == "COMPLETED" for value in operations)
        assert all(
            value.input_tokens is None and value.output_tokens is None for value in operations
        )
        assert all(
            value.attempt_count == 1 and value.repair_attempted is False for value in operations
        )
        for number, reference in enumerate(result.evaluations):
            assert reference.prompt_version_id == versions[number].id
            assert reference.evaluation_id == evaluations[number].id
            assert len(evaluations[number].assessment["assessment"]["criteria"]) == 9
            if number:
                assert versions[number].parent_version_id == versions[number - 1].id
        best = result.outcome.selection
        assert best is not None
        assert result.terminal.best_version_id == versions[best.round_number - 1].id
        assert result.terminal.best_evaluation_id == evaluations[best.round_number - 1].id
        if reason == "repeated_revision_attempt":
            assert best.round_number == 1
        if quality == "unassessable":
            assert evaluations[0].total_score is None
    assert db.connections == 0


@pytest.mark.anyio
@pytest.mark.parametrize("completed,decision", [(0, "clarify"), (1, "clarify"), (1, "preserve")])
async def test_early_architect_outcomes_never_fabricate_evaluations(
    db: Environment, completed: int, decision: str
) -> None:
    script: list[LLMResponse | LLMErrorCode] = []
    if completed:
        script.extend([author(), judge("critical")])
    script.append(author(decision, revision=bool(completed)))
    provider = RecordingFake(db, script)
    result = await execute(db, await workflow(db), provider)
    assert result.terminal.workflow.completed_rounds == completed
    assert len(result.evaluations) == completed and provider.calls == 2 * completed + 1
    assert result.terminal.workflow.status == (
        "CLARIFICATION_REQUIRED" if decision == "clarify" else "COMPLETED"
    )
    assert result.outcome.termination_reason == (
        "clarification_required" if decision == "clarify" else "unchanged_baseline"
    )
    if not completed:
        assert result.terminal.best_version_id is None and result.terminal.quality_status is None


@pytest.mark.anyio
@pytest.mark.parametrize("position", [0, 1, 2, 3, 4, 5])
@pytest.mark.parametrize("invalid", [False, True])
async def test_expected_provider_or_validation_failures_retain_committed_history(
    db: Environment, position: int, invalid: bool, caplog: pytest.LogCaptureFixture
) -> None:
    script: list[LLMResponse | LLMErrorCode] = [
        author(),
        judge("bad"),
        author("rewrite", draft=2, revision=True),
        judge("critical"),
        author("rewrite", draft=3, revision=True),
        judge(),
    ]
    script[position] = wire({"private": PRIVATE}) if invalid else LLMErrorCode.PROVIDER_UNAVAILABLE
    provider = RecordingFake(db, script)
    result = await execute(db, await workflow(db), provider)
    assert result.outcome.execution_status == "failed"
    assert result.terminal.workflow.status == "FAILED"
    assert result.terminal.workflow.completed_rounds == position // 2
    assert len(result.evaluations) == position // 2
    assert provider.calls == position + 1
    assert PRIVATE not in repr(result) and PRIVATE not in caplog.text
    async with write_transaction(db.engine) as session:
        operations = (await session.scalars(select(AgentRun))).all()
        assert sum(value.status == "FAILED" for value in operations) == 1
        failed = next(value for value in operations if value.status == "FAILED")
        assert failed.error_message is None
        assert not any(value.status == "RUNNING" for value in operations)
        if invalid:
            assert result.outcome.termination_reason == "validation_failure"
        else:
            assert result.outcome.termination_reason == "provider_failure"


@pytest.mark.anyio
async def test_rejected_and_repeated_claims_never_dispatch(db: Environment) -> None:
    state = await workflow(db)
    provider = RecordingFake(db, [author(), judge()])
    first = await execute(db, state, provider)
    before = dump(db)
    with pytest.raises(PersistenceError) as error:
        await execute(db, state, provider)
    assert error.value.code == "conflict" and provider.calls == 2
    assert dump(db) == before and first.terminal.workflow.status == "COMPLETED"


@pytest.mark.anyio
async def test_two_claimants_allow_exactly_one_execution(db: Environment) -> None:
    state = await workflow(db)
    other = create_write_engine(build_sqlite_url(db.path))
    second = Environment(
        db.path, other, WorkflowPersistenceService(other, clock=db.clock), db.source_id, db.clock
    )
    first_provider = RecordingFake(db, [author(), judge()])
    second_provider = RecordingFake(second, [author(), judge()])
    start = asyncio.Event()

    async def contender(
        environment: Environment, provider: RecordingFake
    ) -> PersistentOptimizationResult:
        await start.wait()
        return await execute(environment, state, provider)

    tasks = [
        asyncio.create_task(contender(db, first_provider)),
        asyncio.create_task(contender(second, second_provider)),
    ]
    start.set()
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 15)
        assert sum(isinstance(value, PersistentOptimizationResult) for value in results) == 1
        assert sum(isinstance(value, PersistenceError) for value in results) == 1
        assert first_provider.calls + second_provider.calls == 2
    finally:
        await other.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("call", [1, 2, 3, 4])
async def test_expired_or_replaced_owner_stops_after_provider_without_new_writes(
    db: Environment, call: int
) -> None:
    provider = RecordingFake(
        db, [author(), judge("critical"), author("rewrite", revision=True), judge()]
    )

    async def expire(number: int) -> None:
        if number == call:
            db.clock.now += timedelta(seconds=31)

    provider.on_call = expire
    state = await workflow(db)
    with pytest.raises(PersistenceError) as error:
        await execute(db, state, provider)
    assert error.value.code == "conflict" and provider.calls == call
    async with write_transaction(db.engine) as session:
        row = await session.get(WorkflowRun, state.id)
        assert row is not None and row.completed_rounds == (call - 1) // 2
        assert row.status not in {"COMPLETED", "FAILED"}


@pytest.mark.anyio
@pytest.mark.parametrize("call", [1, 2, 3, 4])
async def test_external_cancellation_keeps_durable_history_and_does_not_replay(
    db: Environment, call: int
) -> None:
    provider = RecordingFake(
        db, [author(), judge("critical"), author("rewrite", revision=True), judge()]
    )
    entered = asyncio.Event()
    exited = asyncio.Event()

    async def block(number: int) -> None:
        if number == call:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()

    provider.on_call = block
    state = await workflow(db)
    task = asyncio.create_task(execute(db, state, provider))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert exited.is_set() and len(provider.requests) == call
    assert db.connections == 0
    async with write_transaction(db.engine) as session:
        row = await session.get(WorkflowRun, state.id)
        assert row is not None and row.completed_rounds == (call - 1) // 2
        assert row.status not in {"COMPLETED", "FAILED"}
    with pytest.raises(PersistenceError):
        await execute(db, state, provider)
    assert len(provider.requests) == call


@pytest.mark.anyio
@pytest.mark.parametrize("critic", [False, True])
async def test_duplicate_reservation_is_not_permission_to_call_provider(
    db: Environment, monkeypatch: pytest.MonkeyPatch, critic: bool
) -> None:
    original = db.service.reserve_operation

    async def duplicate(*args: Any, **kwargs: Any) -> OperationReceipt:
        receipt = await original(*args, **kwargs)
        if (receipt.agent.operation == "evaluate") == critic:
            return await original(*args, **kwargs)
        return receipt

    monkeypatch.setattr(db.service, "reserve_operation", duplicate)
    provider = RecordingFake(db, [author(), judge()])
    with pytest.raises(PersistenceError) as error:
        await execute(db, await workflow(db), provider)
    assert error.value.code == "conflict" and provider.calls == int(critic)


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["architect", "evaluation", "terminal"])
async def test_lost_commit_acknowledgment_reconciles_without_provider_replay(
    db: Environment, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    calls = 0
    if point == "architect":
        name = "complete_architect"
    elif point == "evaluation":
        name = "complete_evaluation"
    else:
        name = "finalize_workflow"
    original = getattr(db.service, name)

    async def lose_ack(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        result = await original(*args, **kwargs)
        if calls == 1:
            raise PersistenceError("unavailable")
        assert result.replayed
        return result

    monkeypatch.setattr(db.service, name, lose_ack)
    provider = RecordingFake(db, [author(), judge()])
    result = await execute(db, await workflow(db), provider)
    assert calls == 2 and provider.calls == 2
    assert result.terminal.workflow.completed_rounds == 1
    assert len(result.evaluations) == 1 and result.terminal.workflow.status == "COMPLETED"


@pytest.mark.anyio
async def test_exact_and_conflicting_completion_replays_do_not_dispatch(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = db.service.complete_evaluation
    verified = False

    async def replay(command: EvaluationCompletion) -> EvaluationReceipt:
        nonlocal verified
        first = await original(command)
        before = dump(db)
        second = await original(command)
        assert second.replayed and second.evaluation_id == first.evaluation_id
        assert second.prompt_version_id == first.prompt_version_id and dump(db) == before
        conflicting = command.model_copy(update={"critic_operation_key": "other-operation"})
        with pytest.raises(PersistenceError):
            await original(conflicting)
        assert dump(db) == before
        verified = True
        return second

    monkeypatch.setattr(db.service, "complete_evaluation", replay)
    provider = RecordingFake(db, [author(), judge()])
    result = await execute(db, await workflow(db), provider)
    assert verified and provider.calls == 2 and result.terminal.workflow.completed_rounds == 1


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["architect", "evaluation", "terminal"])
async def test_persistence_failure_stops_without_claiming_success_or_more_calls(
    db: Environment,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    original_finish = AgentRunRepository.finish
    original_progress = WorkflowRunRepository.record_progress

    async def fail_finish(self: AgentRunRepository, *args: Any, **kwargs: Any) -> bool:
        await original_finish(self, *args, **kwargs)
        if point == "architect":
            raise SQLAlchemyError(PRIVATE)
        return True

    async def fail_progress(self: WorkflowRunRepository, *args: Any, **kwargs: Any) -> bool:
        changed = await original_progress(self, *args, **kwargs)
        if (kwargs.get("terminal") is not None) == (point == "terminal"):
            raise SQLAlchemyError(PRIVATE)
        return changed

    monkeypatch.setattr(AgentRunRepository, "finish", fail_finish)
    if point != "architect":
        monkeypatch.setattr(WorkflowRunRepository, "record_progress", fail_progress)
    provider = RecordingFake(db, [author(), judge()])
    state = await workflow(db)
    with pytest.raises(PersistenceError) as error:
        await execute(db, state, provider)
    assert error.value.code == "unavailable"
    assert PRIVATE not in "".join(traceback.format_exception(error.value))
    assert PRIVATE not in caplog.text
    assert provider.calls == (1 if point == "architect" else 2)
    async with write_transaction(db.engine) as session:
        stored = await session.get(WorkflowRun, state.id)
        assert stored is not None and stored.status != "COMPLETED"
        assert stored.completed_rounds == int(point == "terminal")
        assert len((await session.scalars(select(Evaluation))).all()) == int(point == "terminal")
        assert len((await session.scalars(select(PromptVersion))).all()) == int(point == "terminal")


@pytest.mark.anyio
@pytest.mark.parametrize("point", ["architect", "evaluation", "terminal"])
async def test_cancellation_during_persistence_rolls_back_and_closes(
    db: Environment, monkeypatch: pytest.MonkeyPatch, point: str
) -> None:
    entered = asyncio.Event()
    before: str | None = None
    original_finish = AgentRunRepository.finish
    original_progress = WorkflowRunRepository.record_progress

    async def finish(self: AgentRunRepository, *args: Any, **kwargs: Any) -> bool:
        nonlocal before
        if point == "architect":
            before = dump(db)
        changed = await original_finish(self, *args, **kwargs)
        if point == "architect":
            entered.set()
            await asyncio.Event().wait()
        return changed

    async def progress(self: WorkflowRunRepository, *args: Any, **kwargs: Any) -> bool:
        nonlocal before
        match = (kwargs.get("terminal") is not None) == (point == "terminal")
        if match:
            before = dump(db)
        changed = await original_progress(self, *args, **kwargs)
        if match:
            entered.set()
            await asyncio.Event().wait()
        return changed

    monkeypatch.setattr(AgentRunRepository, "finish", finish)
    if point != "architect":
        monkeypatch.setattr(WorkflowRunRepository, "record_progress", progress)
    provider = RecordingFake(db, [author(), judge()])
    state = await workflow(db)
    task = asyncio.create_task(execute(db, state, provider))
    await asyncio.wait_for(entered.wait(), 10)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert before is not None and dump(db) == before
    assert db.connections == 0 and provider.calls == (1 if point == "architect" else 2)


@pytest.mark.anyio
async def test_wrong_token_after_provider_call_cannot_store_result(db: Environment) -> None:
    provider = RecordingFake(db, [author(), judge()])

    async def replace_token(number: int) -> None:
        assert number == 1
        async with write_transaction(db.engine) as session:
            await session.execute(update(WorkflowRun).values(execution_token=uuid4()))

    provider.on_call = replace_token
    with pytest.raises(PersistenceError) as error:
        await execute(db, await workflow(db), provider)
    assert error.value.code == "conflict" and provider.calls == 1
    async with write_transaction(db.engine) as session:
        assert not (await session.scalars(select(PromptVersion))).all()


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["token", "revision", "count", "expired", "bound"])
async def test_renewal_cannot_revive_or_change_ownership(db: Environment, mutation: str) -> None:
    state = await workflow(db)
    claimed = await db.service.claim_workflow(
        db.source_id, state.id, expected_revision=0, lease_seconds=30
    )
    db.clock.now += timedelta(seconds=10)
    fence = claimed.fence()
    seconds = 30
    if mutation == "token":
        fence = fence.model_copy(update={"execution_token": uuid4()})
    elif mutation == "revision":
        fence = fence.model_copy(update={"state_revision": fence.state_revision + 1})
    elif mutation == "count":
        fence = fence.model_copy(update={"completed_rounds": 1})
    elif mutation == "expired":
        db.clock.now += timedelta(seconds=21)
    else:
        seconds = 901
    before = dump(db)
    with pytest.raises(PersistenceError):
        await db.service.renew_claim(fence, lease_seconds=seconds)
    assert dump(db) == before


@pytest.mark.anyio
async def test_explicit_renewal_extends_live_lease_and_refuses_terminal_state(
    db: Environment,
) -> None:
    state = await workflow(db)
    claimed = await db.service.claim_workflow(
        db.source_id, state.id, expected_revision=0, lease_seconds=30
    )
    db.clock.now += timedelta(seconds=10)
    renewed = await db.service.renew_claim(claimed.fence(), lease_seconds=30)
    assert renewed.execution_token == claimed.execution_token
    assert renewed.state_revision == claimed.state_revision + 1
    assert renewed.lease_expires_at == NOW + timedelta(seconds=40)
    shorter = await db.service.renew_claim(renewed.fence(), lease_seconds=1)
    assert shorter.lease_expires_at == renewed.lease_expires_at
    await db.service.finalize_workflow(
        WorkflowFinalization.model_validate(
            dict(
                fence=shorter.fence(),
                details=dict(
                    schema_version="1.0.0",
                    execution_status="FAILED",
                    termination_reason="internal_error",
                    failed_operation="input_validation",
                    error_code="internal_error",
                ),
            )
        )
    )
    snapshot = await db.service.execution_snapshot(
        shorter.fence().model_copy(update={"state_revision": shorter.state_revision + 1})
    )
    with pytest.raises(PersistenceError):
        await db.service.renew_claim(snapshot.workflow.fence(), lease_seconds=30)


@pytest.mark.anyio
@pytest.mark.parametrize("call", [1, 4])
async def test_stored_deadline_cancels_provider_and_finalizes_failed_history(
    db: Environment, call: int
) -> None:
    provider = RecordingFake(
        db, [author(), judge("critical"), author("rewrite", revision=True), judge()]
    )
    entered = asyncio.Event()

    async def block(number: int) -> None:
        if number == call:
            entered.set()
            await asyncio.Event().wait()

    provider.on_call = block
    result = await execute(db, await workflow(db, timeout=2.0), provider)
    assert entered.is_set() and result.outcome.termination_reason == "deadline_exceeded"
    assert result.terminal.workflow.status == "FAILED"
    assert result.terminal.workflow.completed_rounds == (call - 1) // 2
    assert len(provider.requests) == call


@pytest.mark.anyio
async def test_deadline_after_commit_retains_committed_history(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = db.service.execution_snapshot
    calls = 0
    committed = asyncio.Event()

    async def delayed(fence: WorkflowFence) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            assert fence.completed_rounds == 1
            committed.set()
            await asyncio.Event().wait()
        return await original(fence)

    monkeypatch.setattr(db.service, "execution_snapshot", delayed)
    provider = RecordingFake(db, [author(), judge()])
    result = await execute(db, await workflow(db, timeout=2.0), provider)
    assert committed.is_set() and result.outcome.termination_reason == "deadline_exceeded"
    assert result.terminal.workflow.status == "FAILED"
    assert result.outcome.history is not None and len(result.outcome.history.rounds) == 1
    assert result.terminal.workflow.completed_rounds == 1 and len(result.evaluations) == 1


@pytest.mark.anyio
async def test_already_evaluated_revision_stops_before_another_critic(db: Environment) -> None:
    duplicate = wire(
        dict(
            decision="rewrite",
            canonical_prompt=CanonicalPrompt.from_task(RAW).model_dump(mode="json"),
            changes=[NOTE],
            questions=[],
            addressed_issue_keys=["issue_001"],
        )
    )
    provider = RecordingFake(
        db,
        [
            author(),
            judge("bad"),
            author("rewrite", draft=2, revision=True),
            judge("critical"),
            duplicate,
        ],
    )
    result = await execute(db, await workflow(db), provider)
    assert result.outcome.termination_reason == "already_evaluated"
    assert result.terminal.workflow.completed_rounds == 2 and provider.calls == 5


@pytest.mark.anyio
async def test_nonpersistent_optimizer_does_not_write_workflow_records(db: Environment) -> None:
    state = await workflow(db)
    async with write_transaction(db.engine) as session:
        row = await session.get(PromptRequest, db.source_id)
        assert row is not None
        source = PromptRequestRead.model_validate(row)
    provider = FakeLLMProvider([author(), judge()])
    architect, critic = agents(provider)
    before = dump(db)
    result = await asyncio.wait_for(optimize_prompt(architect, critic, source), 5)
    assert result.execution_status == "completed" and provider.calls == 2
    assert dump(db) == before
    async with write_transaction(db.engine) as session:
        workflow_row = await session.get(WorkflowRun, state.id)
        assert workflow_row is not None and workflow_row.status == "PENDING"


@pytest.mark.anyio
@pytest.mark.parametrize("revision", [False, True])
async def test_provenance_rejection_fails_actual_reserved_operation(
    db: Environment, monkeypatch: pytest.MonkeyPatch, revision: bool
) -> None:
    from app.agents.critic_schemas import CriticResult
    from app.agents.revision_schemas import RevisionInput, RevisionResult
    from app.modules.prompts.architect_contracts import CriticInput

    original_evaluate = PromptCriticAgent.evaluate
    original_revise = PromptArchitectAgent.revise

    async def bad_evaluate(
        self: PromptCriticAgent, payload: CriticInput, *, request_id: str | None = None
    ) -> CriticResult:
        result = await original_evaluate(self, payload, request_id=request_id)
        return result.model_copy(update={"input_fingerprint": "f" * 64})

    async def bad_revision(
        self: PromptArchitectAgent, payload: RevisionInput, *, request_id: str | None = None
    ) -> RevisionResult:
        result = await original_revise(self, payload, request_id=request_id)
        return result.model_copy(
            update={"context": result.context.model_copy(update={"agent_run_id": uuid4()})}
        )

    monkeypatch.setattr(
        PromptArchitectAgent if revision else PromptCriticAgent,
        "revise" if revision else "evaluate",
        bad_revision if revision else bad_evaluate,
    )
    provider = RecordingFake(
        db, [author(), judge("critical"), author("rewrite", revision=True), judge()]
    )
    result = await execute(db, await workflow(db), provider)
    assert result.outcome.execution_status == "failed"
    assert result.outcome.termination_reason == "validation_failure"
    assert result.terminal.workflow.completed_rounds == int(revision)
    assert provider.calls == (3 if revision else 2)
    assert result.terminal.terminal.failed_operation == (
        "architect_revise" if revision else "critic_evaluate"
    )


@pytest.mark.anyio
async def test_clarification_is_staged_before_explicit_terminalization(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = db.service.finalize_workflow
    inspected = False

    async def inspect(command: WorkflowFinalization) -> Any:
        nonlocal inspected
        async with write_transaction(db.engine) as session:
            row = await session.get(WorkflowRun, command.fence.workflow_run_id)
            assert row is not None and row.status == "GENERATING" and row.finished_at is None
            operation = await session.get(AgentRun, command.agent_run_id)
            assert operation is not None and operation.result_kind == "clarification"
            assert operation.status == "COMPLETED"
        inspected = True
        return await original(command)

    monkeypatch.setattr(db.service, "finalize_workflow", inspect)
    result = await execute(db, await workflow(db), RecordingFake(db, [author("clarify")]))
    assert inspected and result.terminal.workflow.status == "CLARIFICATION_REQUIRED"


@pytest.mark.anyio
async def test_deadline_during_clarification_commit_acknowledgment_stops_safely(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = db.service.complete_architect
    staged = asyncio.Event()

    async def after_stage(*args: Any, **kwargs: Any) -> OperationReceipt:
        result = await original(*args, **kwargs)
        assert result.workflow.status == "GENERATING"
        staged.set()
        await asyncio.Event().wait()
        return result

    monkeypatch.setattr(db.service, "complete_architect", after_stage)
    provider = RecordingFake(db, [author("clarify")])
    # Cancellation at an unacknowledged staging boundary cannot safely claim a
    # terminal result. The caller receives an explicit infrastructure error.
    with pytest.raises(PersistenceError) as error:
        await execute(db, await workflow(db, timeout=2.0), provider)
    assert staged.is_set() and error.value.code == "unavailable"
    assert provider.calls == 1
    async with write_transaction(db.engine) as session:
        row = await session.scalar(select(WorkflowRun))
        assert row is not None and row.status == "GENERATING" and row.completed_rounds == 0
        assert row.finished_at is None


@pytest.mark.anyio
async def test_internal_snapshot_rejects_stale_fence_and_is_detached(db: Environment) -> None:
    from pydantic import ValidationError

    state = await workflow(db)
    claim = await db.service.claim_workflow(
        db.source_id, state.id, expected_revision=0, lease_seconds=30
    )
    snapshot = await db.service.execution_snapshot(claim.fence())
    assert snapshot.source.raw_prompt == RAW and snapshot.source.user_context == PRIVATE
    assert snapshot.configuration.max_evaluation_rounds == 3
    assert snapshot.history is None and snapshot.evaluations == () and db.connections == 0
    with pytest.raises(ValidationError):
        snapshot.configuration.max_evaluation_rounds = 1
    renewed = await db.service.renew_claim(claim.fence(), lease_seconds=30)
    before = dump(db)
    for fence in (
        claim.fence(),
        renewed.fence().model_copy(update={"execution_token": uuid4()}),
        renewed.fence().model_copy(update={"prompt_request_id": uuid4()}),
    ):
        with pytest.raises(PersistenceError):
            await db.service.execution_snapshot(fence)
    assert dump(db) == before


@pytest.mark.anyio
async def test_stored_protected_spans_are_enforced_before_critic_dispatch(db: Environment) -> None:
    from app.agents.revision_schemas import ProtectedSourceSpan

    state = (
        await db.service.create_workflow(
            db.source_id,
            WorkflowConfiguration(
                schema_version="1.0.0",
                max_evaluation_rounds=1,
                protected_spans=(
                    ProtectedSourceSpan(
                        source="raw_prompt",
                        kind="version",
                        start=RAW.index("3.12"),
                        end=RAW.index("3.12") + 4,
                    ),
                ),
            ),
        )
    ).workflow
    response = wire(
        dict(
            decision="rewrite",
            canonical_prompt=CanonicalPrompt.from_task("Explain another language.").model_dump(
                mode="json"
            ),
            changes=[NOTE],
            questions=[],
        )
    )
    provider = RecordingFake(db, [response, judge()])
    result = await execute(db, state, provider)
    assert result.outcome.termination_reason == "validation_failure" and provider.calls == 1
    assert result.terminal.workflow.completed_rounds == 0
    assert result.outcome.max_evaluation_rounds == 1


@pytest.mark.anyio
async def test_successive_workflows_allocate_request_versions_on_independent_engines(
    db: Environment,
) -> None:
    first = await execute(db, await workflow(db), RecordingFake(db, [author(), judge()]))
    other = create_write_engine(build_sqlite_url(db.path))
    second = Environment(
        db.path, other, WorkflowPersistenceService(other, clock=db.clock), db.source_id, db.clock
    )
    try:
        result = await execute(
            second, await workflow(second), RecordingFake(second, [author(), judge()])
        )
        async with write_transaction(other) as session:
            rows = (
                await session.scalars(select(PromptVersion).order_by(PromptVersion.version_number))
            ).all()
            assert [value.version_number for value in rows] == [1, 2]
            assert rows[0].workflow_run_id == first.terminal.workflow.id
            assert rows[1].workflow_run_id == result.terminal.workflow.id
            assert first.terminal.best_version_id != result.terminal.best_version_id
    finally:
        await other.dispose()


@pytest.mark.anyio
async def test_concurrent_independent_workflows_keep_version_ownership(db: Environment) -> None:
    async with write_transaction(db.engine) as session:
        source = PromptRequest(raw_prompt=RAW, user_context=PRIVATE)
        session.add(source)
        await session.flush()
        source_id = source.id
    other = create_write_engine(build_sqlite_url(db.path))
    second = Environment(
        db.path, other, WorkflowPersistenceService(other, clock=db.clock), source_id, db.clock
    )
    states = (await workflow(db), await workflow(second))
    start = asyncio.Event()

    async def run(environment: Environment, state: WorkflowState) -> PersistentOptimizationResult:
        await start.wait()
        return await execute(environment, state, RecordingFake(environment, [author(), judge()]))

    tasks = [asyncio.create_task(run(db, states[0])), asyncio.create_task(run(second, states[1]))]
    start.set()
    try:
        results = await asyncio.wait_for(asyncio.gather(*tasks), 15)
        async with write_transaction(db.engine) as session:
            rows = (await session.scalars(select(PromptVersion))).all()
            assert len(rows) == 2
            assert {value.prompt_request_id for value in rows} == {db.source_id, source_id}
            assert all(value.version_number == 1 for value in rows)
            assert {value.id for value in rows} == {
                value.terminal.best_version_id for value in results
            }
    finally:
        await other.dispose()
