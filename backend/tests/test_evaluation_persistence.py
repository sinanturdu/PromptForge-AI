"""Atomic completion/finalization against real temporary migrated WAL databases."""

import asyncio
import json
import sqlite3
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite
import pytest
from alembic import command
from sqlalchemy import event, select, text, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_schemas import RevisedCandidate
from app.core.config import build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.sqlite_runtime import require_safe_sqlite_wal
from app.db.write_transactions import create_write_engine, write_transaction
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMResponse, ModelSettings
from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun
from app.modules.prompts.architect_contracts import (
    ArchitectRunContext,
    CandidateReady,
    make_critic_input,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate
from app.modules.prompts.architect_integration import create_architect_handoff
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.persistence_contracts import (
    OperationBindingStorage,
    TerminalDetailsStorage,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    EvaluationReceipt,
    OperationIdentity,
    OperationReceipt,
    PersistenceError,
    WorkflowConfiguration,
    WorkflowFinalization,
    WorkflowState,
)
from app.modules.prompts.workflow_provenance import revision_binding
from app.modules.prompts.workflow_repository import (
    AgentRunRepository,
    EvaluationRepository,
    WorkflowRunRepository,
)
from app.scoring.rubric import CRITERIA

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
RAW = '  Explain Python 3.12. Keep "Merhaba".\n```python\ndef f():\n    return 1\n```\n'
PRIVATE = "PRIVATE_EVALUATION_SENTINEL"


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
    source: PromptRequestRead
    clock: Clock


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Environment]:
    with sqlite3.connect(":memory:") as connection:
        require_safe_sqlite_wal(connection.execute("SELECT sqlite_version()").fetchone()[0])
    path = tmp_path / "evaluations.db"
    command.upgrade(build_alembic_config(build_sqlite_url(path)), "head")
    engine = create_write_engine(build_sqlite_url(path))
    clock = Clock()
    try:
        async with write_transaction(engine) as session:
            assert await session.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await session.scalar(text("PRAGMA synchronous")) == 2
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            source = PromptRequest(raw_prompt=RAW, user_context=PRIVATE, created_at=NOW)
            session.add(source)
            await session.flush()
            payload = PromptRequestRead.model_validate(source)
        yield Environment(
            path, engine, WorkflowPersistenceService(engine, clock=clock), payload, clock
        )
    finally:
        await engine.dispose()


def response(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(provider="fake", finish_reason="complete", text=json.dumps(data))


def gateway(provider: FakeLLMProvider) -> LLMGateway:
    return LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))


def identity(operation: str, number: int) -> OperationIdentity:
    return OperationIdentity(
        operation_key=f"{operation}:{number}",
        agent_name="PromptCriticAgent" if operation == "evaluate" else "PromptArchitectAgent",
        agent_version="1.0.0",
        instruction_version="1.0.0",
    )


def context(value: OperationReceipt) -> ArchitectRunContext:
    return ArchitectRunContext(
        prompt_request_id=value.workflow.prompt_request_id,
        workflow_run_id=value.workflow.id,
        workflow_prompt_request_id=value.workflow.prompt_request_id,
        agent_run_id=value.agent.id,
        agent_workflow_run_id=value.workflow.id,
    )


async def start(db: Environment, limit: int = 3) -> WorkflowState:
    value = await db.service.create_workflow(
        db.source.id, WorkflowConfiguration(schema_version="1.0.0", max_evaluation_rounds=limit)
    )
    return await db.service.claim_workflow(
        db.source.id, value.workflow.id, expected_revision=0, lease_seconds=300
    )


@dataclass
class Staged:
    receipt: OperationReceipt
    candidate: CandidateReady | None


async def stage(
    db: Environment,
    state: WorkflowState,
    baseline: tuple[EvaluationCompletion, EvaluationReceipt] | None = None,
    *,
    decision: str = "rewrite",
    rendered: str | None = None,
) -> Staged:
    number = state.completed_rounds + 1
    if baseline is None:
        bound = OperationBindingStorage(
            schema_version="1.0.0", operation="create", evaluation_round=1
        )
        reservation = await db.service.reserve_operation(
            state.fence(), identity("create", 1), bound
        )
        data: dict[str, Any] = dict(
            decision="clarify" if decision == "clarify" else "preserve",
            canonical_prompt=None,
            changes=[],
            questions=["Which conflicting outcome has priority?"] if decision == "clarify" else [],
        )
        provider = FakeLLMProvider([response(data)])
        try:
            architect = PromptArchitectAgent(gateway(provider), ModelSettings(model="fake"))
            outcome = await create_architect_handoff(
                architect, db.source, context=context(reservation)
            )
        finally:
            await provider.aclose()
        receipt = await db.service.complete_architect(
            reservation.workflow.fence(), reservation.agent.id, outcome
        )
        return Staged(receipt, outcome if isinstance(outcome, CandidateReady) else None)
    previous, committed = baseline
    payload = make_revision_input(previous.evaluated, next_evaluation_round=number)
    bound = revision_binding(payload, committed.evaluation_id)
    reservation = await db.service.reserve_operation(
        state.fence(), identity("revise", number), bound, revision_input=payload
    )
    execution = make_revision_input(
        previous.evaluated, next_evaluation_round=number, context=context(reservation)
    )
    data = dict(
        decision=decision,
        canonical_prompt=CanonicalPrompt.from_task(
            rendered if rendered is not None else RAW + f"\nUse example {number}."
        ).model_dump(mode="json")
        if decision == "rewrite"
        else None,
        changes=["Clarified the requested example."] if decision == "rewrite" else [],
        questions=["Which outcome takes priority?"] if decision == "clarify" else [],
        addressed_issue_keys=["issue_001"] if decision == "rewrite" else [],
    )
    provider = FakeLLMProvider([response(data)])
    try:
        architect = PromptArchitectAgent(gateway(provider), ModelSettings(model="fake"))
        revised = await architect.revise(execution)
    finally:
        await provider.aclose()
    receipt = await db.service.complete_architect(
        reservation.workflow.fence(), reservation.agent.id, revised
    )
    return Staged(receipt, revised.candidate if isinstance(revised, RevisedCandidate) else None)


async def assess(
    db: Environment, staged: Staged, profile: str = "critical"
) -> EvaluationCompletion:
    assert staged.candidate is not None
    number = staged.receipt.workflow.current_round
    op = await db.service.reserve_operation(
        staged.receipt.workflow.fence(),
        identity("evaluate", number),
        OperationBindingStorage(
            schema_version="1.0.0", operation="evaluate", evaluation_round=number
        ),
    )
    evidence = [dict(source="raw_prompt", quote="Python 3.12")]
    criteria = {
        key: dict(score=10, rationale="Offline fixture.", evidence=evidence)
        for key, _, _ in CRITERIA
    }
    issues = []
    if profile not in {"qualified", "no_feedback"}:
        criteria["constraints_and_scope"]["score"] = 3 if profile == "worse" else 6
        issues.append(
            dict(
                criterion="constraints_and_scope",
                code="missing_requirement",
                status="supported",
                severity="major" if profile == "unassessable" else "critical",
                description="A fixture requirement is missing.",
                recommendation="Restore the explicit requirement.",
                evidence=evidence,
            )
        )
    if profile in {"unassessable", "no_feedback"}:
        criteria["clarity"] = dict(score=None, rationale="Insufficient evidence.", evidence=[])
    provider = FakeLLMProvider(
        [
            response(
                dict(
                    criteria=criteria,
                    issues=issues,
                    summary="Offline fixture, not semantic quality proof.",
                )
            )
        ]
    )
    try:
        critic = PromptCriticAgent(gateway(provider), ModelSettings(model="fake"))
        evaluated = EvaluatedCandidate.model_validate(
            dict(
                candidate=staged.candidate,
                critic=await critic.evaluate(make_critic_input(staged.candidate)),
            )
        )
    finally:
        await provider.aclose()
    return EvaluationCompletion(
        fence=op.workflow.fence(),
        architect_agent_run_id=staged.receipt.agent.id,
        critic_agent_run_id=op.agent.id,
        critic_operation_key=f"evaluate:{number}",
        evaluation_round=number,
        evaluated=evaluated,
    )


async def round_one(
    db: Environment, profile: str = "critical", limit: int = 3
) -> tuple[EvaluationCompletion, EvaluationReceipt]:
    command = await assess(db, await stage(db, await start(db, limit)), profile)
    return command, await db.service.complete_evaluation(command)


def finish(
    state: WorkflowState,
    reason: str,
    *,
    status: str = "COMPLETED",
    agent_id: UUID | None = None,
    **details: Any,
) -> WorkflowFinalization:
    return WorkflowFinalization(
        fence=state.fence(),
        agent_run_id=agent_id,
        details=TerminalDetailsStorage.model_validate(
            dict(
                schema_version="1.0.0",
                execution_status=status,
                termination_reason=reason,
                **details,
            )
        ),
    )


def dump(db: Environment) -> str:
    with sqlite3.connect(db.path) as connection:
        return "\n".join(connection.iterdump())


async def row(db: Environment, workflow_id: UUID) -> WorkflowRun:
    async with write_transaction(db.engine) as session:
        value = await session.get(WorkflowRun, workflow_id)
        assert value is not None
        return value


@pytest.mark.anyio
@pytest.mark.parametrize(
    "profile,reason,quality",
    [
        ("qualified", "quality_passed", "qualified"),
        ("critical", "evaluation_limit", "not_qualified"),
        ("no_feedback", "evaluation_limit", "unassessable"),
    ],
)
async def test_first_round_and_terminal_quality_dimensions(
    db: Environment, profile: str, reason: str, quality: str
) -> None:
    command, receipt = await round_one(db, profile, limit=1)
    assert receipt.workflow.completed_rounds == 1 and receipt.workflow.current_round == 1
    assert receipt.quality_status == quality
    async with write_transaction(db.engine) as session:
        version = await session.get(PromptVersion, receipt.prompt_version_id)
        evaluation = await session.get(Evaluation, receipt.evaluation_id)
        critic = await session.get(AgentRun, command.critic_agent_run_id)
        assert version is not None and evaluation is not None and critic is not None
        assert version.rendered_prompt == RAW and version.parent_version_id is None
        assert version.version_number == 1 and critic.status == "COMPLETED"
        assert evaluation.total_score == command.evaluated.scoring.total_score
        assert len(evaluation.assessment["assessment"]["criteria"]) == 9
    terminal = await db.service.finalize_workflow(finish(receipt.workflow, reason))
    assert terminal.workflow.status == "COMPLETED" and terminal.quality_status == quality
    assert terminal.best_version_id == receipt.prompt_version_id


@pytest.mark.anyio
async def test_three_rounds_progress_and_parent_lineage(db: Environment) -> None:
    first, one = await round_one(db, "worse")
    second = await assess(db, await stage(db, one.workflow, (first, one)), "critical")
    two = await db.service.complete_evaluation(second)
    third = await assess(db, await stage(db, two.workflow, (second, two)), "qualified")
    three = await db.service.complete_evaluation(third)
    assert three.workflow.completed_rounds == three.workflow.current_round == 3
    assert three.best_version_id == three.prompt_version_id
    async with write_transaction(db.engine) as session:
        versions = list(
            (
                await session.scalars(select(PromptVersion).order_by(PromptVersion.version_number))
            ).all()
        )
        assert [value.version_number for value in versions] == [1, 2, 3]
        assert [value.parent_version_id for value in versions] == [
            None,
            one.prompt_version_id,
            two.prompt_version_id,
        ]
    terminal = await db.service.finalize_workflow(finish(three.workflow, "quality_passed"))
    assert terminal.workflow.status == "COMPLETED"


@pytest.mark.anyio
@pytest.mark.parametrize("profile", ["worse", "critical"])
async def test_earlier_best_and_ties_stop_repeated_revision(db: Environment, profile: str) -> None:
    first, one = await round_one(db)
    second = await assess(db, await stage(db, one.workflow, (first, one)), profile)
    two = await db.service.complete_evaluation(second)
    assert two.best_version_id == one.prompt_version_id
    assert two.workflow.current_round == two.workflow.completed_rounds == 2
    terminal = await db.service.finalize_workflow(finish(two.workflow, "repeated_revision_attempt"))
    assert terminal.best_version_id == one.prompt_version_id


@pytest.mark.anyio
async def test_null_score_selection_uses_existing_policy(db: Environment) -> None:
    first, one = await round_one(db)
    second = await assess(db, await stage(db, one.workflow, (first, one)), "unassessable")
    two = await db.service.complete_evaluation(second)
    assert two.best_version_id == two.prompt_version_id and two.quality_status == "unassessable"
    stored = await row(db, two.workflow.id)
    assert stored.final_score is None and stored.qualified is False


@pytest.mark.anyio
async def test_completion_replay_and_expired_reconciliation_are_read_only(db: Environment) -> None:
    command, receipt = await round_one(db)
    db.clock.now += timedelta(seconds=301)
    before = dump(db)
    replay = await db.service.complete_evaluation(command)
    assert replay.replayed and replay.evaluation_id == receipt.evaluation_id
    assert dump(db) == before
    with pytest.raises(PersistenceError):
        await db.service.finalize_workflow(
            finish(receipt.workflow, "interrupted", status="FAILED", error_code="interrupted")
        )
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "decision,reason,status",
    [
        ("preserve", "unchanged_baseline", "COMPLETED"),
        ("clarify", "clarification_required", "CLARIFICATION_REQUIRED"),
    ],
)
async def test_early_revision_outcomes_create_no_new_versions(
    db: Environment, decision: str, reason: str, status: str
) -> None:
    first, one = await round_one(db)
    staged = await stage(db, one.workflow, (first, one), decision=decision)
    arguments = dict(questions=("Which outcome takes priority?",)) if decision == "clarify" else {}
    command = finish(
        staged.receipt.workflow,
        reason,
        status=status,
        agent_id=staged.receipt.agent.id,
        **arguments,
    )
    terminal = await db.service.finalize_workflow(command)
    assert terminal.workflow.status == status and terminal.best_version_id == one.prompt_version_id
    before = dump(db)
    assert (await db.service.finalize_workflow(command)).replayed
    assert dump(db) == before
    async with write_transaction(db.engine) as session:
        assert len((await session.scalars(select(PromptVersion))).all()) == 1
        assert len((await session.scalars(select(Evaluation))).all()) == 1


@pytest.mark.anyio
async def test_clarification_before_any_evaluation_has_no_quality(db: Environment) -> None:
    staged = await stage(db, await start(db), decision="clarify")
    terminal = await db.service.finalize_workflow(
        finish(
            staged.receipt.workflow,
            "clarification_required",
            status="CLARIFICATION_REQUIRED",
            agent_id=staged.receipt.agent.id,
            questions=("Which conflicting outcome has priority?",),
        )
    )
    assert terminal.best_version_id is None and terminal.quality_status is None
    stored = await row(db, terminal.workflow.id)
    assert stored.qualified is None and stored.final_score is None and stored.completed_rounds == 0


@pytest.mark.anyio
@pytest.mark.parametrize("completed", [0, 1, 2])
async def test_failure_retains_only_actual_completed_history(
    db: Environment, completed: int
) -> None:
    if completed == 0:
        state = await start(db)
        best_id = None
    else:
        previous, receipt = await round_one(db, "worse")
        if completed == 2:
            next_command = await assess(
                db, await stage(db, receipt.workflow, (previous, receipt)), "critical"
            )
            receipt = await db.service.complete_evaluation(next_command)
        state, best_id = receipt.workflow, receipt.best_version_id
    terminal = await db.service.finalize_workflow(
        finish(state, "interrupted", status="FAILED", error_code="interrupted")
    )
    assert terminal.workflow.status == "FAILED" and terminal.workflow.completed_rounds == completed
    assert terminal.best_version_id == best_id


@pytest.mark.anyio
async def test_no_feedback_stopping_reason_is_validated(db: Environment) -> None:
    _, receipt = await round_one(db, "no_feedback")
    with pytest.raises(PersistenceError):
        await db.service.finalize_workflow(finish(receipt.workflow, "quality_passed"))
    result = await db.service.finalize_workflow(finish(receipt.workflow, "no_actionable_feedback"))
    assert result.quality_status == "unassessable"


@pytest.mark.anyio
async def test_failure_finalizes_running_critic_atomically(db: Environment) -> None:
    staged = await stage(db, await start(db))
    command = await assess(db, staged)
    terminal = await db.service.finalize_workflow(
        WorkflowFinalization(
            fence=command.fence,
            agent_run_id=command.critic_agent_run_id,
            details=TerminalDetailsStorage(
                schema_version="1.0.0",
                execution_status="FAILED",
                termination_reason="interrupted",
                failed_operation="critic_evaluate",
                error_code="interrupted",
            ),
        )
    )
    assert terminal.workflow.completed_rounds == 0 and terminal.best_version_id is None
    async with write_transaction(db.engine) as session:
        critic = await session.get(AgentRun, command.critic_agent_run_id)
        assert critic is not None and critic.status == "FAILED" and critic.call_metadata is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "token",
        "revision",
        "completed",
        "expired",
        "round",
        "critic",
        "creator",
        "key",
        "fingerprint",
        "scoring",
        "phase",
        "source",
        "progress",
        "stage",
    ],
)
async def test_rejects_conflicting_or_tampered_completion(db: Environment, mutation: str) -> None:
    command = await assess(db, await stage(db, await start(db)))
    if mutation == "token":
        command = command.model_copy(
            update=dict(fence=command.fence.model_copy(update=dict(execution_token=uuid4())))
        )
    elif mutation == "revision":
        command = command.model_copy(
            update=dict(fence=command.fence.model_copy(update=dict(state_revision=0)))
        )
    elif mutation == "completed":
        command = command.model_copy(
            update=dict(fence=command.fence.model_copy(update=dict(completed_rounds=1)))
        )
    elif mutation == "expired":
        db.clock.now += timedelta(seconds=301)
    elif mutation == "round":
        command = command.model_copy(update=dict(evaluation_round=2))
    elif mutation == "critic":
        command = command.model_copy(
            update=dict(critic_agent_run_id=command.architect_agent_run_id)
        )
    elif mutation == "creator":
        command = command.model_copy(update=dict(architect_agent_run_id=uuid4()))
    elif mutation == "key":
        command = command.model_copy(update=dict(critic_operation_key="different-operation"))
    elif mutation == "fingerprint":
        critic = command.evaluated.critic.model_copy(update=dict(input_fingerprint="0" * 64))
        command = command.model_copy(
            update=dict(evaluated=command.evaluated.model_copy(update=dict(critic=critic)))
        )
    elif mutation == "scoring":
        score = command.evaluated.scoring.model_copy(update=dict(passed=True))
        command = command.model_copy(
            update=dict(evaluated=command.evaluated.model_copy(update=dict(scoring=score)))
        )
    else:
        async with write_transaction(db.engine) as session:
            if mutation == "phase":
                await session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == command.fence.workflow_run_id)
                    .values(status="REVISING")
                )
            elif mutation == "progress":
                await session.execute(
                    update(WorkflowRun)
                    .where(WorkflowRun.id == command.fence.workflow_run_id)
                    .values(completed_rounds=1)
                )
            elif mutation == "source":
                await session.execute(
                    update(PromptRequest)
                    .where(PromptRequest.id == db.source.id)
                    .values(user_context=PRIVATE + "changed")
                )
            else:
                agent = await session.get(AgentRun, command.architect_agent_run_id)
                assert agent is not None and agent.result_data is not None
                value = json.loads(json.dumps(agent.result_data))
                value["candidate"]["rendered_prompt"] = "corrupt staged content"
                agent.result_data = value
    before = dump(db)
    with pytest.raises(PersistenceError):
        await db.service.complete_evaluation(command)
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "render",
        "canonical",
        "score",
        "gate",
        "evidence",
        "renderer",
        "schema",
        "selection",
        "rubric",
        "scoring_version",
    ],
)
async def test_persisted_history_is_revalidated_on_replay(db: Environment, mutation: str) -> None:
    command, receipt = await round_one(db)
    async with write_transaction(db.engine) as session:
        version = await session.get(PromptVersion, receipt.prompt_version_id)
        evaluation = await session.get(Evaluation, receipt.evaluation_id)
        assert version is not None and evaluation is not None
        if mutation == "render":
            version.rendered_prompt = "changed"
        elif mutation == "canonical":
            version.canonical_prompt = CanonicalPrompt.from_task("changed").model_dump(mode="json")
        elif mutation == "score":
            evaluation.total_score = (
                evaluation.total_score - 1 if evaluation.total_score is not None else None
            )
        elif mutation == "gate":
            value = json.loads(json.dumps(evaluation.gate_results))
            value["gates"]["no_critical_issues"] = True
            evaluation.gate_results = value
        elif mutation == "evidence":
            value = json.loads(json.dumps(evaluation.assessment))
            value["assessment"]["criteria"]["clarity"]["evidence"][0]["quote"] = PRIVATE
            evaluation.assessment = value
        elif mutation == "renderer":
            evaluation.evaluated_renderer_version = "unsupported"
        elif mutation == "schema":
            evaluation.assessment_schema_version = "unsupported"
        elif mutation == "rubric":
            evaluation.rubric_version = "unsupported"
        elif mutation == "scoring_version":
            evaluation.scoring_version = "unsupported"
        else:
            await session.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == receipt.workflow.id)
                .values(final_version_id=None)
            )
    before = dump(db)
    with pytest.raises(PersistenceError):
        await db.service.complete_evaluation(command)
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize("second_round", [False, True])
@pytest.mark.parametrize(
    "point", ["version", "evaluation", "critic", "progress", "selection", "commit"]
)
async def test_atomic_completion_rolls_back_every_stage(
    db: Environment,
    monkeypatch: pytest.MonkeyPatch,
    point: str,
    second_round: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    if second_round:
        first, prior = await round_one(db, "worse")
        command = await assess(db, await stage(db, prior.workflow, (first, prior)))
    else:
        command = await assess(db, await stage(db, await start(db)))
    before = dump(db)
    original_version = EvaluationRepository.insert_version
    original_evaluation = EvaluationRepository.insert_evaluation
    original_progress = WorkflowRunRepository.record_progress
    inserted = False

    async def insert_version(self: EvaluationRepository, value: PromptVersion) -> None:
        await original_version(self, value)
        if point == "version":
            raise SQLAlchemyError(PRIVATE)

    async def insert_evaluation(self: EvaluationRepository, value: Evaluation) -> None:
        nonlocal inserted
        await original_evaluation(self, value)
        inserted = True
        if point == "evaluation":
            raise SQLAlchemyError(PRIVATE)

    async def fail_critic(*_args: Any, **_kwargs: Any) -> bool:
        raise SQLAlchemyError(PRIVATE)

    async def progress(self: WorkflowRunRepository, *args: Any, **kwargs: Any) -> bool:
        await original_progress(self, *args, **kwargs)
        raise SQLAlchemyError(PRIVATE)

    import app.modules.prompts.workflow_history as history

    original_select = select_best_candidate

    def selection(*args: Any, **kwargs: Any) -> Any:
        if inserted:
            raise SQLAlchemyError(PRIVATE)
        return original_select(*args, **kwargs)

    def commit(_connection: Any) -> None:
        raise SQLAlchemyError(PRIVATE)

    monkeypatch.setattr(EvaluationRepository, "insert_version", insert_version)
    monkeypatch.setattr(EvaluationRepository, "insert_evaluation", insert_evaluation)
    if point == "critic":
        monkeypatch.setattr(AgentRunRepository, "finish", fail_critic)
    if point == "progress":
        monkeypatch.setattr(WorkflowRunRepository, "record_progress", progress)
    if point == "selection":
        monkeypatch.setattr(history, "select_best_candidate", selection)
    if point == "commit":
        event.listen(db.engine.sync_engine, "commit", commit)
    try:
        with pytest.raises(PersistenceError) as error:
            await db.service.complete_evaluation(command)
        assert error.value.code == "unavailable" and error.value.__context__ is None
        assert PRIVATE not in "".join(traceback.format_exception(error.value))
        assert PRIVATE not in caplog.text and dump(db) == before
    finally:
        if point == "commit":
            event.remove(db.engine.sync_engine, "commit", commit)


@pytest.mark.anyio
@pytest.mark.parametrize("conflicting", [False, True])
async def test_concurrent_completions_have_one_atomic_winner(
    db: Environment, conflicting: bool
) -> None:
    command = await assess(db, await stage(db, await start(db)))
    other_command = command
    if conflicting:
        output = command.evaluated.critic.output.model_copy(
            update=dict(summary="A different valid assessment summary.")
        )
        critic = command.evaluated.critic.model_copy(update=dict(output=output))
        other_command = command.model_copy(
            update=dict(evaluated=command.evaluated.model_copy(update=dict(critic=critic)))
        )
    other_engine = create_write_engine(build_sqlite_url(db.path))
    other = WorkflowPersistenceService(other_engine, clock=db.clock)
    ready = asyncio.Event()
    physical: list[Any] = []

    def connected(connection: Any, _record: Any) -> None:
        physical.append(connection)

    event.listen(db.engine.sync_engine, "connect", connected)
    event.listen(other_engine.sync_engine, "connect", connected)

    async def complete(
        service: WorkflowPersistenceService, payload: EvaluationCompletion
    ) -> EvaluationReceipt:
        await ready.wait()
        return await service.complete_evaluation(payload)

    try:
        tasks = [
            asyncio.create_task(complete(db.service, command)),
            asyncio.create_task(complete(other, other_command)),
        ]
        ready.set()
        values = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 10)
        successes = [value for value in values if isinstance(value, EvaluationReceipt)]
        assert len(successes) == (1 if conflicting else 2)
        assert sum(not value.replayed for value in successes) == 1
        assert len(physical) == 2 and physical[0] is not physical[1]
        async with write_transaction(db.engine) as session:
            assert len((await session.scalars(select(Evaluation))).all()) == 1
            assert len((await session.scalars(select(PromptVersion))).all()) == 1
    finally:
        event.remove(db.engine.sync_engine, "connect", connected)
        await other_engine.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize("cancelled", [False, True])
async def test_ambiguous_commit_acknowledgment_reconciles_without_new_writes(
    db: Environment, monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    command = await assess(db, await stage(db, await start(db)))
    import app.modules.prompts.workflow_persistence as service_module

    original = write_transaction

    @asynccontextmanager
    async def ambiguous(engine: AsyncEngine) -> AsyncIterator[Any]:
        async with original(engine) as session:
            yield session
        if cancelled:
            raise asyncio.CancelledError()
        raise SQLAlchemyError(PRIVATE)

    monkeypatch.setattr(service_module, "write_transaction", ambiguous)
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            await db.service.complete_evaluation(command)
    else:
        with pytest.raises(PersistenceError) as error:
            await db.service.complete_evaluation(command)
        assert error.value.code == "unavailable" and error.value.__context__ is None
    monkeypatch.setattr(service_module, "write_transaction", original)
    db.clock.now += timedelta(seconds=301)
    before = dump(db)
    replay = await db.service.complete_evaluation(command)
    assert replay.replayed and replay.workflow.completed_rounds == 1
    assert dump(db) == before


@pytest.mark.anyio
async def test_cancellation_after_version_flush_rolls_back_and_closes(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = await assess(db, await stage(db, await start(db)))
    original = EvaluationRepository.insert_version
    flushed = asyncio.Event()
    connections: list[aiosqlite.Connection] = []

    @event.listens_for(db.engine.sync_engine, "connect")
    def connected(connection: Any, _record: Any) -> None:
        connections.append(connection.driver_connection)

    async def pause(self: EvaluationRepository, version: PromptVersion) -> None:
        await original(self, version)
        flushed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(EvaluationRepository, "insert_version", pause)
    before = dump(db)
    task = asyncio.create_task(db.service.complete_evaluation(command))
    await asyncio.wait_for(flushed.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert dump(db) == before and connections
    for connection in connections:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")


@pytest.mark.anyio
async def test_terminal_failure_rolls_back_and_conflicting_terminal_is_rejected(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, receipt = await round_one(db, "qualified")
    command = finish(receipt.workflow, "quality_passed")
    before = dump(db)
    original = WorkflowRunRepository.record_progress

    async def fail(self: WorkflowRunRepository, *args: Any, **kwargs: Any) -> bool:
        await original(self, *args, **kwargs)
        raise SQLAlchemyError(PRIVATE)

    monkeypatch.setattr(WorkflowRunRepository, "record_progress", fail)
    with pytest.raises(PersistenceError):
        await db.service.finalize_workflow(command)
    assert dump(db) == before

    monkeypatch.setattr(WorkflowRunRepository, "record_progress", original)
    terminal = await db.service.finalize_workflow(command)
    assert terminal.workflow.status == "COMPLETED"
    db.clock.now += timedelta(seconds=301)
    before = dump(db)
    assert (await db.service.finalize_workflow(command)).replayed
    with pytest.raises(PersistenceError):
        await db.service.finalize_workflow(
            finish(terminal.workflow, "interrupted", status="FAILED", error_code="interrupted")
        )
    assert dump(db) == before


async def other_source(db: Environment) -> Environment:
    async with write_transaction(db.engine) as session:
        source = PromptRequest(raw_prompt=RAW, user_context=PRIVATE, created_at=NOW)
        session.add(source)
        await session.flush()
        payload = PromptRequestRead.model_validate(source)
    return Environment(db.path, db.engine, db.service, payload, db.clock)


@pytest.mark.anyio
@pytest.mark.parametrize("which", ["creator", "critic"])
async def test_cross_workflow_operation_ownership(db: Environment, which: str) -> None:
    first = await assess(db, await stage(db, await start(db)))
    other = await other_source(db)
    second = await assess(other, await stage(other, await start(other)))
    command = first.model_copy(
        update={
            "architect_agent_run_id"
            if which == "creator"
            else "critic_agent_run_id": second.architect_agent_run_id
            if which == "creator"
            else second.critic_agent_run_id
        }
    )
    before = dump(db)
    with pytest.raises(PersistenceError):
        await db.service.complete_evaluation(command)
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize("which", ["parent", "baseline", "selected"])
async def test_cross_request_parent_baseline_or_selection_is_rejected(
    db: Environment, which: str
) -> None:
    first, one = await round_one(db, "worse")
    command = await assess(db, await stage(db, one.workflow, (first, one)))
    two = await db.service.complete_evaluation(command)
    other = await other_source(db)
    _, foreign = await round_one(other, "qualified", limit=1)
    async with write_transaction(db.engine) as session:
        if which == "parent":
            await session.execute(
                update(PromptVersion)
                .where(PromptVersion.id == two.prompt_version_id)
                .values(parent_version_id=foreign.prompt_version_id)
            )
        elif which == "selected":
            await session.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == two.workflow.id)
                .values(final_version_id=foreign.prompt_version_id)
            )
        else:
            creator = await session.get(AgentRun, command.architect_agent_run_id)
            assert creator is not None and creator.binding_data is not None
            value = json.loads(json.dumps(creator.binding_data))
            value["baseline_evaluation_id"] = str(foreign.evaluation_id)
            creator.binding_data = value
            creator.baseline_evaluation_id = foreign.evaluation_id
    before = dump(db)
    with pytest.raises(PersistenceError):
        await db.service.complete_evaluation(command)
    assert dump(db) == before


@pytest.mark.anyio
@pytest.mark.parametrize("reserved_critic", [False, True])
async def test_duplicate_fingerprint_never_creates_another_evaluation(
    db: Environment, reserved_critic: bool
) -> None:
    first, one = await round_one(db, "worse")
    second = await assess(db, await stage(db, one.workflow, (first, one)))
    two = await db.service.complete_evaluation(second)
    duplicate = await stage(db, two.workflow, (second, two), rendered=RAW)
    if reserved_critic:
        command = await assess(db, duplicate)
        assert (
            command.evaluated.critic.input_fingerprint == first.evaluated.critic.input_fingerprint
        )
        before = dump(db)
        with pytest.raises(PersistenceError):
            await db.service.complete_evaluation(command)
        assert dump(db) == before
    else:
        result = await db.service.finalize_workflow(
            finish(
                duplicate.receipt.workflow, "already_evaluated", agent_id=duplicate.receipt.agent.id
            )
        )
        assert (
            result.workflow.status == "COMPLETED"
            and result.best_version_id == two.prompt_version_id
        )
    async with write_transaction(db.engine) as session:
        assert len((await session.scalars(select(PromptVersion))).all()) == 2
        assert len((await session.scalars(select(Evaluation))).all()) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("same_request", [False, True])
async def test_concurrent_workflows_use_request_scoped_version_numbers(
    db: Environment, same_request: bool
) -> None:
    if same_request:
        first, committed = await round_one(db, "qualified", limit=1)
        await db.service.finalize_workflow(finish(committed.workflow, "quality_passed"))
        # The active-workflow index permits only one writer workflow per source.
        # Reconciliation of the terminal run may race a new run's allocation.
        second = await assess(db, await stage(db, await start(db, 1)), "qualified")
    else:
        first = await assess(db, await stage(db, await start(db, 1)), "qualified")
        other = await other_source(db)
        second = await assess(other, await stage(other, await start(other, 1)), "qualified")
    engine = create_write_engine(build_sqlite_url(db.path))
    service = WorkflowPersistenceService(engine, clock=db.clock)
    ready = asyncio.Event()

    async def complete(
        owner: WorkflowPersistenceService, command: EvaluationCompletion
    ) -> EvaluationReceipt:
        await ready.wait()
        return await owner.complete_evaluation(command)

    try:
        tasks = [
            asyncio.create_task(complete(db.service, first)),
            asyncio.create_task(complete(service, second)),
        ]
        ready.set()
        receipts = await asyncio.wait_for(asyncio.gather(*tasks), 10)
        assert sum(not receipt.replayed for receipt in receipts) == (1 if same_request else 2)
        async with write_transaction(db.engine) as session:
            versions = list((await session.scalars(select(PromptVersion))).all())
            assert sorted(value.version_number for value in versions) == (
                [1, 2] if same_request else [1, 1]
            )
    finally:
        await engine.dispose()


@pytest.mark.anyio
async def test_version_collision_only_retries_persistence_with_the_same_result(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = await assess(db, await stage(db, await start(db)))
    async with write_transaction(db.engine) as session:
        session.add(
            PromptVersion(
                prompt_request_id=db.source.id,
                version_number=1,
                source="user_edit",
                target_mode="generic",
                renderer_name="GenericPromptRenderer",
                renderer_version="1.0.0",
                canonical_prompt=CanonicalPrompt.from_task(RAW).model_dump(mode="json"),
                rendered_prompt=RAW,
            )
        )
        await session.flush()
    original = EvaluationRepository.next_version_number

    async def stale(_self: EvaluationRepository, _request_id: UUID) -> int:
        return 1

    monkeypatch.setattr(EvaluationRepository, "next_version_number", stale)
    before = dump(db)
    with pytest.raises(PersistenceError) as error:
        await db.service.complete_evaluation(command)
    assert error.value.code == "conflict" and dump(db) == before
    monkeypatch.setattr(EvaluationRepository, "next_version_number", original)
    receipt = await db.service.complete_evaluation(command)
    async with write_transaction(db.engine) as session:
        version = await session.get(PromptVersion, receipt.prompt_version_id)
        assert version is not None and version.version_number == 2


@pytest.mark.anyio
async def test_terminal_scalar_details_must_agree_on_reconciliation(db: Environment) -> None:
    _, completed = await round_one(db, "qualified", limit=1)
    command = finish(completed.workflow, "quality_passed")
    await db.service.finalize_workflow(command)
    async with write_transaction(db.engine) as session:
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == completed.workflow.id)
            .values(stop_reason="evaluation_limit")
        )
    before = dump(db)
    with pytest.raises(PersistenceError):
        await db.service.finalize_workflow(command)
    assert dump(db) == before


@pytest.mark.anyio
async def test_completion_is_invisible_to_other_connections_until_commit(
    db: Environment, monkeypatch: pytest.MonkeyPatch
) -> None:
    command = await assess(db, await stage(db, await start(db)))
    original = WorkflowRunRepository.record_progress
    updated = asyncio.Event()
    release = asyncio.Event()

    async def pause(self: WorkflowRunRepository, *args: Any, **kwargs: Any) -> bool:
        changed = await original(self, *args, **kwargs)
        updated.set()
        await release.wait()
        return changed

    monkeypatch.setattr(WorkflowRunRepository, "record_progress", pause)
    before = dump(db)
    task = asyncio.create_task(db.service.complete_evaluation(command))
    try:
        await asyncio.wait_for(updated.wait(), 5)
        # A separate WAL reader sees the old version/Evaluation/AgentRun/progress
        # together, even after all their writes have executed in the writer.
        assert dump(db) == before
    finally:
        release.set()
    result = await asyncio.wait_for(task, 5)
    assert result.workflow.completed_rounds == 1 and dump(db) != before
