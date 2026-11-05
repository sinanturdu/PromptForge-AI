"""Offline service integration on temporary migrated WAL/FULL databases."""

import asyncio
import json
import sqlite3
import traceback
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import aiosqlite
import pytest
from alembic import command
from sqlalchemy import event, text, update
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
from app.llm.schemas import CallMetadata, LLMResponse, ModelSettings
from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun
from app.modules.prompts.architect_contracts import (
    ArchitectHandoff,
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    make_critic_input,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate
from app.modules.prompts.architect_integration import create_architect_handoff
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.persistence_contracts import (
    CallMetadataStorage,
    EvaluationStorage,
    OperationBindingStorage,
    OperationResultStorage,
    RequestPreferenceSnapshot,
    request_content_digest,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import (
    OperationIdentity,
    OperationReceipt,
    PersistenceError,
    WorkflowConfiguration,
    WorkflowState,
)
from app.modules.prompts.workflow_provenance import revision_binding
from app.modules.prompts.workflow_repository import AgentRunRepository, WorkflowRunRepository
from app.scoring.rubric import CRITERIA

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
RAW = '  Explain Python 3.12. Keep "Merhaba".\n```python\ndef f():\n    return 1\n```\n'
PRIVATE = "PRIVATE_WORKFLOW_SENTINEL"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Harness:
    path: Path
    engine: AsyncEngine
    service: WorkflowPersistenceService
    source: PromptRequestRead
    clock: Clock


@pytest.fixture
async def harness(tmp_path: Path) -> AsyncIterator[Harness]:
    # Fail explicitly rather than silently qualifying the affected legacy runtime.
    with sqlite3.connect(":memory:") as connection:
        require_safe_sqlite_wal(connection.execute("SELECT sqlite_version()").fetchone()[0])
    path = tmp_path / "persistence.db"
    url = build_sqlite_url(path)
    command.upgrade(build_alembic_config(url), "head")
    engine = create_write_engine(url)
    clock = Clock()
    try:
        async with write_transaction(engine) as session:
            assert await session.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await session.scalar(text("PRAGMA synchronous")) == 2
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            row = PromptRequest(raw_prompt=RAW, user_context=PRIVATE, created_at=NOW)
            session.add(row)
            await session.flush()
            source = PromptRequestRead.model_validate(row)
        yield Harness(path, engine, WorkflowPersistenceService(engine, clock=clock), source, clock)
    finally:
        await engine.dispose()


def config(**changes: Any) -> WorkflowConfiguration:
    return WorkflowConfiguration(schema_version="1.0.0", **changes)


def identity(operation: str = "create", key: str | None = None) -> OperationIdentity:
    return OperationIdentity(
        operation_key=key or operation + ":1",
        agent_name="PromptCriticAgent" if operation == "evaluate" else "PromptArchitectAgent",
        agent_version="1.0.0",
        instruction_version="1.0.0",
    )


def binding(operation: str = "create", number: int = 1) -> OperationBindingStorage:
    return OperationBindingStorage.model_validate(
        dict(schema_version="1.0.0", operation=operation, evaluation_round=number)
    )


def context(receipt: OperationReceipt) -> ArchitectRunContext:
    return ArchitectRunContext(
        prompt_request_id=receipt.workflow.prompt_request_id,
        workflow_run_id=receipt.workflow.id,
        workflow_prompt_request_id=receipt.workflow.prompt_request_id,
        agent_run_id=receipt.agent.id,
        agent_workflow_run_id=receipt.workflow.id,
    )


def response(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(provider="fake", finish_reason="complete", text=json.dumps(data))


async def claim(h: Harness, **options: Any) -> WorkflowState:
    created = await h.service.create_workflow(h.source.id, config(**options))
    return await h.service.claim_workflow(
        h.source.id, created.workflow.id, expected_revision=0, lease_seconds=300
    )


async def reserve(h: Harness) -> OperationReceipt:
    state = await claim(h)
    return await h.service.reserve_operation(state.fence(), identity(), binding())


async def handoff(
    h: Harness, receipt: OperationReceipt, *, clarify: bool = False
) -> ArchitectHandoff:
    data = dict(
        decision="clarify" if clarify else "preserve",
        canonical_prompt=None,
        changes=[],
        questions=["Which incompatible goal takes priority?"] if clarify else [],
    )
    provider = FakeLLMProvider([response(data)])
    try:
        agent = PromptArchitectAgent(LLMGateway(provider), ModelSettings(model="fake-model"))
        return await create_architect_handoff(agent, h.source, context=context(receipt))
    finally:
        await provider.aclose()


def counts(h: Harness) -> tuple[int, int, int, int]:
    with sqlite3.connect(h.path) as connection:
        return tuple(
            connection.execute("SELECT count(*) FROM " + name).fetchone()[0]
            for name in ("workflow_runs", "agent_runs", "prompt_versions", "evaluations")
        )


async def stored_workflow(h: Harness, identity: UUID) -> WorkflowRun:
    async with write_transaction(h.engine) as session:
        row = await WorkflowRunRepository(session).get_by_id(identity)
        assert row is not None
        return row


@pytest.mark.anyio
async def test_creation_binds_exact_source_preferences_digest_and_configuration(
    harness: Harness,
) -> None:
    h = harness
    value = await h.service.create_workflow(
        h.source.id, config(max_evaluation_rounds=2), client_idempotency_key=PRIVATE
    )
    row = await stored_workflow(h, value.workflow.id)
    assert (
        row.status == "PENDING"
        and row.current_round == row.completed_rounds == row.state_revision == 0
    )
    assert row.execution_token is None and row.lease_expires_at is None
    assert row.request_content_digest == request_content_digest(h.source)
    assert RequestPreferenceSnapshot.from_json_value(
        row.request_snapshot
    ) == RequestPreferenceSnapshot.from_request(h.source)
    assert WorkflowConfiguration.from_json_value(row.config_snapshot) == config(
        max_evaluation_rounds=2
    )
    assert PRIVATE not in json.dumps(row.request_snapshot) and RAW not in json.dumps(
        row.request_snapshot
    )
    assert (
        row.client_idempotency_key_digest != PRIVATE
        and len(row.client_idempotency_key_digest or "") == 64
    )
    assert counts(h) == (1, 0, 0, 0)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        dict(max_evaluation_rounds=0),
        dict(max_evaluation_rounds=4),
        dict(max_evaluation_rounds=True),
        dict(timeout_seconds=-1.0),
        dict(timeout_seconds=float("inf")),
        dict(schema_version="9"),
    ],
)
async def test_invalid_configuration_copy_cannot_create_workflow(
    harness: Harness, changes: dict[str, Any]
) -> None:
    with pytest.raises(PersistenceError) as error:
        await harness.service.create_workflow(
            harness.source.id, config().model_copy(update=changes)
        )
    assert error.value.code == "invalid" and counts(harness) == (0, 0, 0, 0)


@pytest.mark.anyio
async def test_missing_and_invalid_stored_source_are_rejected(harness: Harness) -> None:
    with pytest.raises(PersistenceError) as error:
        await harness.service.create_workflow(uuid4(), config())
    assert error.value.code == "not_found"
    async with write_transaction(harness.engine) as session:
        await session.execute(
            update(PromptRequest)
            .where(PromptRequest.id == harness.source.id)
            .values(raw_prompt="x" * 20_001)
        )
    with pytest.raises(PersistenceError) as error:
        await harness.service.create_workflow(harness.source.id, config())
    assert error.value.code == "invalid" and counts(harness) == (0, 0, 0, 0)


@pytest.mark.anyio
async def test_creation_idempotency_compares_configuration_and_source(harness: Harness) -> None:
    h = harness
    first = await h.service.create_workflow(h.source.id, config(), client_idempotency_key=PRIVATE)
    retried = await h.service.create_workflow(h.source.id, config(), client_idempotency_key=PRIVATE)
    assert retried.replayed and retried.workflow == first.workflow
    with pytest.raises(PersistenceError) as error:
        await h.service.create_workflow(
            h.source.id, config(max_evaluation_rounds=2), client_idempotency_key=PRIVATE
        )
    assert error.value.code == "conflict"
    for key in (None, "different-key"):
        with pytest.raises(PersistenceError) as error:
            await h.service.create_workflow(h.source.id, config(), client_idempotency_key=key)
        assert error.value.code == "conflict" and error.value.__context__ is None
    assert counts(h) == (1, 0, 0, 0)


@pytest.mark.anyio
async def test_source_tampering_blocks_idempotency_claim_and_operations(harness: Harness) -> None:
    h = harness
    created = await h.service.create_workflow(h.source.id, config(), client_idempotency_key=PRIVATE)
    async with write_transaction(h.engine) as session:
        await session.execute(
            update(PromptRequest)
            .where(PromptRequest.id == h.source.id)
            .values(raw_prompt=RAW + " changed")
        )
    for action in (
        h.service.create_workflow(h.source.id, config(), client_idempotency_key=PRIVATE),
        h.service.claim_workflow(
            h.source.id, created.workflow.id, expected_revision=0, lease_seconds=300
        ),
    ):
        with pytest.raises(PersistenceError) as error:
            await action
        assert error.value.code == "conflict"


@pytest.mark.anyio
async def test_independent_concurrent_creation_and_claim_have_one_winner(harness: Harness) -> None:
    h = harness
    second = create_write_engine(build_sqlite_url(h.path))
    other = WorkflowPersistenceService(second, clock=h.clock)
    barrier = asyncio.Event()

    async def create(service: WorkflowPersistenceService) -> Any:
        await barrier.wait()
        return await service.create_workflow(h.source.id, config())

    try:
        tasks = [asyncio.create_task(create(service)) for service in (h.service, other)]
        barrier.set()
        outcomes = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        assert sum(not isinstance(value, BaseException) for value in outcomes) == 1
        creation_errors = [value for value in outcomes if isinstance(value, PersistenceError)]
        assert len(creation_errors) == 1 and creation_errors[0].code == "conflict"
        assert counts(h) == (1, 0, 0, 0)
        created = next(value for value in outcomes if not isinstance(value, BaseException))
        barrier.clear()

        async def execute_claim(service: WorkflowPersistenceService) -> WorkflowState:
            await barrier.wait()
            return await service.claim_workflow(
                h.source.id, created.workflow.id, expected_revision=0, lease_seconds=300
            )

        tasks_claim = [
            asyncio.create_task(execute_claim(service)) for service in (h.service, other)
        ]
        barrier.set()
        results = await asyncio.wait_for(asyncio.gather(*tasks_claim, return_exceptions=True), 5)
        assert sum(isinstance(value, WorkflowState) for value in results) == 1
        errors = [value for value in results if isinstance(value, PersistenceError)]
        assert len(errors) == 1 and errors[0].code == "conflict"
        row = await stored_workflow(h, created.workflow.id)
        assert (
            row.state_revision == 1
            and row.status == "GENERATING"
            and row.execution_token is not None
        )
    finally:
        await second.dispose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation", ["token", "revision", "completed", "expired", "phase", "round"]
)
async def test_reservation_rejects_stale_owners_and_wrong_phase(
    harness: Harness, mutation: str
) -> None:
    h = harness
    state = await claim(h)
    fence = state.fence()
    if mutation == "token":
        fence = fence.model_copy(update=dict(execution_token=uuid4()))
    elif mutation == "revision":
        fence = fence.model_copy(update=dict(state_revision=0))
    elif mutation == "completed":
        fence = fence.model_copy(update=dict(completed_rounds=1))
    elif mutation == "expired":
        h.clock.now += timedelta(seconds=301)
    else:
        async with write_transaction(h.engine) as session:
            await session.execute(
                update(WorkflowRun)
                .where(WorkflowRun.id == state.id)
                .values(
                    **(dict(status="REVISING") if mutation == "phase" else dict(current_round=2))
                )
            )
    with pytest.raises(PersistenceError) as error:
        await h.service.reserve_operation(fence, identity(), binding())
    assert error.value.code == "conflict" and counts(h) == (1, 0, 0, 0)


@pytest.mark.anyio
async def test_operation_reservation_idempotency_budget_and_unknown_facts(harness: Harness) -> None:
    h = harness
    state = await claim(h)
    first = await h.service.reserve_operation(state.fence(), identity(), binding())
    replay = await h.service.reserve_operation(state.fence(), identity(), binding())
    assert replay.replayed and replay.agent == first.agent and first.workflow.state_revision == 2
    with pytest.raises(PersistenceError):
        await h.service.reserve_operation(
            first.workflow.fence(), identity(key="another-create"), binding()
        )
    with pytest.raises(PersistenceError):
        await h.service.reserve_operation(
            first.workflow.fence(), identity().model_copy(update=dict(agent_version="2")), binding()
        )
    async with write_transaction(h.engine) as session:
        agent = await AgentRunRepository(session).get_by_id(first.agent.id)
        assert agent is not None
        assert all(
            value is None
            for value in (
                agent.provider,
                agent.model,
                agent.attempt_count,
                agent.repair_attempted,
                agent.call_metadata,
            )
        )
    assert counts(h) == (1, 1, 0, 0)


@pytest.mark.anyio
async def test_completed_candidate_exact_preservation_replay_and_critic_reservation(
    harness: Harness,
) -> None:
    h = harness
    operation = await reserve(h)
    value = await handoff(h, operation)
    first = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, value
    )
    assert first.agent.result is not None and first.agent.result.candidate is not None
    assert first.agent.result.candidate.rendered_prompt == RAW
    assert first.workflow.status == "EVALUATING" and first.workflow.completed_rounds == 0
    replay = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, value
    )
    assert replay.replayed and replay.agent == first.agent
    changed = value.model_copy(update=dict(instruction_version="2"))
    with pytest.raises(PersistenceError):
        await h.service.complete_architect(first.workflow.fence(), operation.agent.id, changed)
    critic = await h.service.reserve_operation(
        first.workflow.fence(), identity("evaluate"), binding("evaluate")
    )
    assert critic.agent.operation == "evaluate" and critic.agent.status == "RUNNING"
    with pytest.raises(PersistenceError):
        await h.service.complete_architect(critic.workflow.fence(), critic.agent.id, value)
    assert counts(h) == (1, 2, 0, 0)


@pytest.mark.anyio
async def test_clarification_is_terminal_without_candidate_or_evaluation(harness: Harness) -> None:
    h = harness
    operation = await reserve(h)
    value = await handoff(h, operation, clarify=True)
    assert isinstance(value, ClarificationRequired)
    result = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, value
    )
    assert result.workflow.status == "CLARIFICATION_REQUIRED" and result.agent.status == "COMPLETED"
    assert result.agent.result is not None and result.agent.result.candidate is None
    assert result.agent.result.questions == value.questions
    assert counts(h) == (1, 1, 0, 0)
    replay = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, value
    )
    assert replay.replayed
    later = await h.service.create_workflow(h.source.id, config())
    assert later.workflow.id != result.workflow.id


@pytest.mark.anyio
async def test_failure_diagnostics_and_unknown_telemetry_are_truthful(harness: Harness) -> None:
    h = harness
    operation = await reserve(h)
    result = await h.service.fail_operation(
        operation.workflow.fence(), operation.agent.id, error_code=LLMErrorCode.TIMEOUT
    )
    assert (
        result.workflow.status == "FAILED"
        and result.agent.status == "FAILED"
        and result.agent.metadata is None
    )
    replay = await h.service.fail_operation(
        operation.workflow.fence(), operation.agent.id, error_code=LLMErrorCode.TIMEOUT
    )
    assert replay.replayed
    with pytest.raises(PersistenceError):
        await h.service.fail_operation(
            result.workflow.fence(), operation.agent.id, error_code="internal_error"
        )
    row = await stored_workflow(h, result.workflow.id)
    assert row.final_version_id is None and row.final_score is None and row.qualified is None
    assert row.error_message is None and row.error_code == "timeout"


@dataclass
class Baseline:
    workflow: WorkflowState
    evaluation_id: UUID
    version_id: UUID
    evaluated: EvaluatedCandidate


async def seed_completed_baseline(h: Harness) -> Baseline:
    """Fixture only: simulate the atomic completion that production M5E-3C will own."""
    operation = await reserve(h)
    candidate = await handoff(h, operation)
    assert isinstance(candidate, CandidateReady)
    done = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, candidate
    )
    critic_op = await h.service.reserve_operation(
        done.workflow.fence(), identity("evaluate"), binding("evaluate")
    )
    evidence = [dict(source="raw_prompt", quote="Python 3.12")]
    criteria = {
        key: dict(score=10, rationale="Offline fixture.", evidence=evidence)
        for key, _, _ in CRITERIA
    }
    criteria["constraints_and_scope"]["score"] = 6
    provider = FakeLLMProvider(
        [
            response(
                dict(
                    criteria=criteria,
                    issues=[
                        dict(
                            criterion="constraints_and_scope",
                            code="missing_requirement",
                            status="supported",
                            severity="critical",
                            description="A fixture requirement is missing.",
                            recommendation="Restore the explicit requirement.",
                            evidence=evidence,
                        )
                    ],
                    summary="Offline fixture, not semantic quality proof.",
                )
            )
        ]
    )
    try:
        critic_agent = PromptCriticAgent(LLMGateway(provider), ModelSettings(model="fake-model"))
        critic = await critic_agent.evaluate(make_critic_input(candidate))
    finally:
        await provider.aclose()
    evaluated = EvaluatedCandidate.model_validate(dict(candidate=candidate, critic=critic))
    async with write_transaction(h.engine) as session:
        version = PromptVersion(
            prompt_request_id=h.source.id,
            workflow_run_id=done.workflow.id,
            agent_run_id=operation.agent.id,
            version_number=1,
            evaluation_round=1,
            source="engineer_create",
            target_mode=h.source.target_mode,
            renderer_name=candidate.renderer_name,
            renderer_version=candidate.renderer_version,
            canonical_prompt=candidate.canonical_prompt.model_dump(mode="json"),
            rendered_prompt=candidate.rendered_prompt,
            change_summary=list(candidate.changes),
        )
        session.add(version)
        await session.flush()
        evaluation = Evaluation(
            **EvaluationStorage.from_evaluated(
                evaluated,
                workflow_run_id=done.workflow.id,
                prompt_version_id=version.id,
                critic_agent_run_id=critic_op.agent.id,
                evaluation_round=1,
                completed_at=NOW,
            ).record_values()
        )
        session.add(evaluation)
        await session.flush()
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == critic_op.agent.id)
            .values(
                status="COMPLETED",
                result_kind="evaluation",
                result_data=OperationResultStorage(
                    schema_version="1.0.0", kind="evaluation", evaluation_id=evaluation.id
                ).model_dump(mode="json"),
                call_metadata=CallMetadataStorage.from_metadata(critic.metadata).model_dump(
                    mode="json"
                ),
                attempt_count=critic.metadata.attempts,
                repair_attempted=critic.metadata.repair_attempted,
                finished_at=NOW,
            )
        )
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == done.workflow.id)
            .values(
                status="REVISING",
                current_round=2,
                completed_rounds=1,
                state_revision=WorkflowRun.state_revision + 1,
            )
        )
        workflow = critic_op.workflow.model_copy(
            update=dict(
                status="REVISING",
                current_round=2,
                completed_rounds=1,
                state_revision=critic_op.workflow.state_revision + 1,
            )
        )
        return Baseline(workflow, evaluation.id, version.id, evaluated)


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["preserve", "clarify", "rewrite"])
async def test_revision_reservation_and_outcomes_keep_history_separate(
    harness: Harness, decision: str
) -> None:
    h = harness
    baseline = await seed_completed_baseline(h)
    payload = make_revision_input(baseline.evaluated, next_evaluation_round=2)
    bound = revision_binding(payload, baseline.evaluation_id)
    operation = await h.service.reserve_operation(
        baseline.workflow.fence(), identity("revise"), bound, revision_input=payload
    )
    replay = await h.service.reserve_operation(
        baseline.workflow.fence(), identity("revise"), bound, revision_input=payload
    )
    assert replay.replayed and replay.agent.id == operation.agent.id
    with pytest.raises(PersistenceError):
        await h.service.reserve_operation(
            operation.workflow.fence(),
            identity("revise", "duplicate-key"),
            bound,
            revision_input=payload,
        )
    execution = make_revision_input(
        baseline.evaluated, next_evaluation_round=2, context=context(operation)
    )
    provider = FakeLLMProvider(
        [
            response(
                dict(
                    decision=decision,
                    canonical_prompt=CanonicalPrompt.from_task(
                        "Explain Python 3.12 with one useful example."
                    ).model_dump(mode="json")
                    if decision == "rewrite"
                    else None,
                    changes=["Addressed explicit requirement."] if decision == "rewrite" else [],
                    questions=["Which requirement has priority?"] if decision == "clarify" else [],
                    addressed_issue_keys=["issue_001"] if decision == "rewrite" else [],
                )
            )
        ]
    )
    try:
        architect = PromptArchitectAgent(LLMGateway(provider), ModelSettings(model="fake-model"))
        outcome = await architect.revise(execution)
    finally:
        await provider.aclose()
    result = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, outcome
    )
    assert result.agent.result is not None
    assert (
        result.agent.result.kind
        == {"preserve": "unchanged", "clarify": "clarification", "rewrite": "candidate"}[decision]
    )
    assert counts(h) == (1, 3, 1, 1)
    assert result.workflow.completed_rounds == 1
    replayed = await h.service.complete_architect(
        operation.workflow.fence(), operation.agent.id, outcome
    )
    assert replayed.replayed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    ["baseline", "feedback", "attempt", "keys", "version", "evidence", "creator", "critic"],
)
async def test_revision_rejects_wrong_or_malformed_provenance(
    harness: Harness, mutation: str
) -> None:
    h = harness
    baseline = await seed_completed_baseline(h)
    payload = make_revision_input(baseline.evaluated, next_evaluation_round=2)
    bound = revision_binding(payload, baseline.evaluation_id)
    if mutation in {"baseline", "feedback", "attempt", "keys"}:
        mutations: dict[str, dict[str, Any]] = {
            "baseline": dict(baseline_evaluation_id=uuid4()),
            "feedback": dict(feedback_projection_digest="0" * 64),
            "attempt": dict(revision_attempt_key="1" * 64),
            "keys": dict(eligible_issue_keys=("issue_002",)),
        }
        bound = bound.model_copy(update=mutations[mutation])
    else:
        async with write_transaction(h.engine) as session:
            if mutation == "version":
                await session.execute(
                    update(PromptVersion)
                    .where(PromptVersion.id == baseline.version_id)
                    .values(rendered_prompt="wrong")
                )
            elif mutation == "evidence":
                row = await session.get(Evaluation, baseline.evaluation_id)
                assert row is not None
                data = dict(row.assessment)
                data["schema_version"] = "unsupported"
                row.assessment = data
            else:
                evaluation_row = await session.get(Evaluation, baseline.evaluation_id)
                assert evaluation_row is not None
                target_id = (
                    baseline.evaluated.candidate.context.agent_run_id
                    if mutation == "creator"
                    else evaluation_row.critic_agent_run_id
                )
                assert target_id is not None
                await session.execute(
                    update(AgentRun).where(AgentRun.id == target_id).values(status="RUNNING")
                )
    with pytest.raises(PersistenceError):
        await h.service.reserve_operation(
            baseline.workflow.fence(), identity("revise"), bound, revision_input=payload
        )
    assert counts(h) == (1, 2, 1, 1)


@pytest.mark.anyio
async def test_failure_after_history_preserves_progress_and_selection(harness: Harness) -> None:
    h = harness
    baseline = await seed_completed_baseline(h)
    async with write_transaction(h.engine) as session:
        await session.execute(
            update(WorkflowRun)
            .where(WorkflowRun.id == baseline.workflow.id)
            .values(
                final_version_id=baseline.version_id,
                final_score=baseline.evaluated.scoring.total_score,
                qualified=False,
            )
        )
    payload = make_revision_input(baseline.evaluated, next_evaluation_round=2)
    operation = await h.service.reserve_operation(
        baseline.workflow.fence(),
        identity("revise"),
        revision_binding(payload, baseline.evaluation_id),
        revision_input=payload,
    )
    await h.service.fail_operation(
        operation.workflow.fence(), operation.agent.id, error_code="interrupted"
    )
    row = await stored_workflow(h, baseline.workflow.id)
    assert row.status == "FAILED" and row.final_version_id == baseline.version_id
    assert row.completed_rounds == 1 and row.current_round == 2
    assert row.final_score == baseline.evaluated.scoring.total_score and row.qualified is False
    assert counts(h) == (1, 3, 1, 1)


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["workflow", "reservation", "completion"])
async def test_injected_write_failure_rolls_back_and_sanitizes(
    harness: Harness, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    h = harness
    operation = None if stage == "workflow" else await reserve(h)
    before = counts(h)
    row_before = None if operation is None else await stored_workflow(h, operation.workflow.id)
    comparison_id = None if operation is None else operation.workflow.id

    async def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise SQLAlchemyError(PRIVATE)

    action: Awaitable[Any]
    if stage == "workflow":
        original = WorkflowRunRepository.insert

        async def partial(self: WorkflowRunRepository, row: WorkflowRun) -> None:
            await original(self, row)
            await fail()

        monkeypatch.setattr(WorkflowRunRepository, "insert", partial)
        action = h.service.create_workflow(h.source.id, config())
    elif stage == "reservation":
        assert operation is not None
        # Use a newly created second source for this independent reservation path.
        async with write_transaction(h.engine) as session:
            source = PromptRequest(raw_prompt="Other", created_at=NOW)
            session.add(source)
            await session.flush()
            source_id = source.id
        created = await h.service.create_workflow(source_id, config())
        state = await h.service.claim_workflow(
            source_id, created.workflow.id, expected_revision=0, lease_seconds=300
        )
        before = counts(h)
        row_before = await stored_workflow(h, state.id)
        comparison_id = state.id
        original_agent_insert = AgentRunRepository.insert

        async def partial_agent(self: AgentRunRepository, row: AgentRun) -> None:
            await original_agent_insert(self, row)
            await fail()

        monkeypatch.setattr(AgentRunRepository, "insert", partial_agent)
        action = h.service.reserve_operation(state.fence(), identity(), binding())
    else:
        assert operation is not None
        value = await handoff(h, operation)
        original_finish = AgentRunRepository.finish

        async def partial_finish(self: AgentRunRepository, *args: Any, **kwargs: Any) -> bool:
            await original_finish(self, *args, **kwargs)
            await fail()
            return True

        monkeypatch.setattr(AgentRunRepository, "finish", partial_finish)
        action = h.service.complete_architect(operation.workflow.fence(), operation.agent.id, value)
    with pytest.raises(PersistenceError) as error:
        await action
    assert error.value.code == "unavailable" and error.value.__context__ is None
    assert PRIVATE not in "".join(traceback.format_exception(error.value))
    assert counts(h) == before
    if comparison_id is not None and row_before is not None:
        after = await stored_workflow(h, comparison_id)
        assert (
            after.state_revision == row_before.state_revision and after.status == row_before.status
        )
    if stage == "completion" and operation is not None:
        async with write_transaction(h.engine) as session:
            actor = await session.get(AgentRun, operation.agent.id)
            assert actor is not None and actor.status == "RUNNING" and actor.result_data is None


@pytest.mark.anyio
async def test_completion_rejects_expired_owner_and_wrong_operation_scope(harness: Harness) -> None:
    h = harness
    operation = await reserve(h)
    value = await handoff(h, operation)
    for fence, agent_id in [
        (
            operation.workflow.fence().model_copy(update=dict(execution_token=uuid4())),
            operation.agent.id,
        ),
        (operation.workflow.fence(), uuid4()),
    ]:
        with pytest.raises(PersistenceError):
            await h.service.complete_architect(fence, agent_id, value)
    h.clock.now += timedelta(seconds=301)
    with pytest.raises(PersistenceError):
        await h.service.complete_architect(operation.workflow.fence(), operation.agent.id, value)
    assert counts(h) == (1, 1, 0, 0)


@pytest.mark.anyio
async def test_service_closes_connections_before_return_and_on_cancellation(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = harness
    connections: list[aiosqlite.Connection] = []

    @event.listens_for(h.engine.sync_engine, "connect")
    def connected(connection: Any, _record: Any) -> None:
        connections.append(connection.driver_connection)

    state = await claim(h)
    assert connections
    for connection in connections:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")
    flushed = asyncio.Event()
    original = AgentRunRepository.insert

    async def wait(self: AgentRunRepository, row: AgentRun) -> None:
        await original(self, row)
        flushed.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(AgentRunRepository, "insert", wait)
    task = asyncio.create_task(h.service.reserve_operation(state.fence(), identity(), binding()))
    await asyncio.wait_for(flushed.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 5)
    assert counts(h) == (1, 0, 0, 0)
    for connection in connections:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")


@pytest.mark.anyio
@pytest.mark.parametrize("same_key", [True, False])
async def test_independent_operation_reservation_never_authorizes_two_dispatches(
    harness: Harness, same_key: bool
) -> None:
    h = harness
    state = await claim(h)
    second = create_write_engine(build_sqlite_url(h.path))
    other = WorkflowPersistenceService(second, clock=h.clock)
    barrier = asyncio.Event()

    async def start(service: WorkflowPersistenceService, key: str) -> OperationReceipt:
        await barrier.wait()
        return await service.reserve_operation(state.fence(), identity(key=key), binding())

    try:
        tasks = [
            asyncio.create_task(start(h.service, "logical-create")),
            asyncio.create_task(start(other, "logical-create" if same_key else "different-create")),
        ]
        barrier.set()
        values = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 5)
        receipts = [value for value in values if isinstance(value, OperationReceipt)]
        assert sum(not receipt.replayed for receipt in receipts) == 1
        assert len(receipts) == (2 if same_key else 1)
        assert counts(h) == (1, 1, 0, 0)
    finally:
        await second.dispose()


@pytest.mark.anyio
async def test_known_configuration_does_not_invent_unobserved_execution_facts(
    harness: Harness,
) -> None:
    h = harness
    state = await claim(h)
    spec = identity().model_copy(
        update=dict(provider="fake", settings=ModelSettings(model="configured-model"))
    )
    operation = await h.service.reserve_operation(state.fence(), spec, binding())
    observed = CallMetadata(
        provider=None,
        requested_model=None,
        attempts=0,
        retries=0,
        repair_attempted=False,
        duration_ms=0.0,
        usage_complete=False,
    )
    # Even a model_copy extra must not escape the explicit metadata allowlist.
    observed = observed.model_copy(update=dict(api_key=PRIVATE))
    finished = await h.service.fail_operation(
        operation.workflow.fence(),
        operation.agent.id,
        error_code="internal_error",
        metadata=observed,
    )
    assert finished.agent.metadata is not None and finished.agent.metadata.metadata is not None
    assert finished.agent.metadata.metadata.provider is None
    assert PRIVATE not in finished.agent.metadata.model_dump_json()
    async with write_transaction(h.engine) as session:
        row = await session.get(AgentRun, operation.agent.id)
        assert row is not None
        assert row.provider == "fake" and row.model == "configured-model"
        assert row.attempt_count == 0 and row.repair_attempted is False
        assert row.input_tokens is row.output_tokens is None


@pytest.mark.anyio
async def test_existing_baseline_from_another_workflow_is_rejected(harness: Harness) -> None:
    h = harness
    first = await seed_completed_baseline(h)
    async with write_transaction(h.engine) as session:
        row = PromptRequest(raw_prompt=RAW, user_context=PRIVATE, created_at=NOW)
        session.add(row)
        await session.flush()
        source = PromptRequestRead.model_validate(row)
    other = Harness(h.path, h.engine, h.service, source, h.clock)
    foreign = await seed_completed_baseline(other)
    payload = make_revision_input(first.evaluated, next_evaluation_round=2)
    with pytest.raises(PersistenceError) as error:
        await h.service.reserve_operation(
            first.workflow.fence(),
            identity("revise"),
            revision_binding(payload, foreign.evaluation_id),
            revision_input=payload,
        )
    assert error.value.code == "conflict" and counts(h) == (2, 4, 2, 2)
    foreign_agent_id = foreign.evaluated.candidate.context.agent_run_id
    assert foreign_agent_id is not None
    with pytest.raises(PersistenceError) as error:
        await h.service.fail_operation(
            first.workflow.fence(), foreign_agent_id, error_code="interrupted"
        )
    assert error.value.code == "not_found"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "field", ["agent_name", "evaluation_round", "operation", "feedback_projection_digest"]
)
async def test_inconsistent_agent_columns_cannot_be_completed(harness: Harness, field: str) -> None:
    h = harness
    operation = await reserve(h)
    value = await handoff(h, operation)
    mutations = dict(
        agent_name="PromptCriticAgent",
        evaluation_round=2,
        operation="evaluate",
        feedback_projection_digest="0" * 64,
    )
    async with write_transaction(h.engine) as session:
        await session.execute(
            update(AgentRun)
            .where(AgentRun.id == operation.agent.id)
            .values(**{field: mutations[field]})
        )
    with pytest.raises(PersistenceError) as error:
        await h.service.complete_architect(operation.workflow.fence(), operation.agent.id, value)
    assert error.value.code == "invalid" and counts(h) == (1, 1, 0, 0)


@pytest.mark.anyio
async def test_claimed_source_change_and_unapproved_metadata_fail_safely(
    harness: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    h = harness
    operation = await reserve(h)
    bad_code: Any = PRIVATE
    with pytest.raises(PersistenceError) as error:
        await h.service.fail_operation(
            operation.workflow.fence(), operation.agent.id, error_code=bad_code
        )
    assert error.value.code == "invalid" and error.value.__context__ is None
    bad_metadata: Any = dict(api_key=PRIVATE)
    with pytest.raises(PersistenceError):
        await h.service.fail_operation(
            operation.workflow.fence(),
            operation.agent.id,
            error_code="internal_error",
            metadata=bad_metadata,
        )
    async with write_transaction(h.engine) as session:
        await session.execute(
            update(PromptRequest)
            .where(PromptRequest.id == h.source.id)
            .values(user_context=PRIVATE + "changed")
        )
    with pytest.raises(PersistenceError) as error:
        await h.service.fail_operation(
            operation.workflow.fence(), operation.agent.id, error_code="interrupted"
        )
    assert error.value.code == "conflict"
    assert PRIVATE not in caplog.text and PRIVATE not in str(error.value)
    assert counts(h) == (1, 1, 0, 0)


@pytest.mark.anyio
@pytest.mark.parametrize("seconds", [0, -1, 901, True, 1.5])
async def test_claim_lease_requires_explicit_bounded_integer(
    harness: Harness, seconds: Any
) -> None:
    h = harness
    created = await h.service.create_workflow(h.source.id, config())
    with pytest.raises(PersistenceError) as error:
        await h.service.claim_workflow(
            h.source.id, created.workflow.id, expected_revision=0, lease_seconds=seconds
        )
    assert error.value.code == "invalid"
    row = await stored_workflow(h, created.workflow.id)
    assert row.status == "PENDING" and row.state_revision == 0 and row.execution_token is None
