"""Real committed workflow fixtures; providers are used only during fixture creation."""

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.core.config import build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.read_transactions import create_read_engine
from app.db.sqlite_runtime import require_safe_sqlite_wal
from app.db.write_transactions import create_write_engine, write_transaction
from app.llm.errors import LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMRequest, LLMResponse, ModelSettings
from app.models import PromptRequest
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.persistent_optimizer import (
    PersistentExecutionIdentity,
    PersistentOptimizationResult,
    execute_persisted_optimizer,
)
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    EvaluationReceipt,
    OperationReceipt,
    WorkflowConfiguration,
    WorkflowFinalization,
    WorkflowState,
)
from app.modules.prompts.workflow_readback import WorkflowReadbackService
from app.modules.prompts.workflow_recovery import WorkflowRecoveryAssessmentService
from app.scoring.rubric import CRITERIA

RAW = '  Explain Python 3.12. Keep "Merhaba".\n```python\ndef f():\n    return 1\n```\n'
PRIVATE = "PRIVATE_READBACK_CONTEXT_SENTINEL"
NOTE = "PRIVATE_READBACK_ARCHITECT_NOTE"
QUESTION = "PRIVATE_READBACK_CLARIFICATION_QUESTION"
SECRET_ID = "PRIVATE_PROVIDER_REQUEST_IDENTIFIER"
NOW = datetime(2026, 11, 6, 12, tzinfo=UTC)


@dataclass
class Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Environment:
    path: Path
    writer: AsyncEngine
    reader: AsyncEngine
    persistence: WorkflowPersistenceService
    readback: WorkflowReadbackService
    recovery: WorkflowRecoveryAssessmentService
    clock: Clock
    source: PromptRequest
    evaluations: list[EvaluationCompletion] = field(default_factory=list)
    finalizations: list[WorkflowFinalization] = field(default_factory=list)
    reservations: list[OperationReceipt] = field(default_factory=list)
    read_connections: int = 0


@pytest.fixture
async def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Environment]:
    require_safe_sqlite_wal(sqlite3.sqlite_version)
    path = tmp_path / "readback ş + space.db"
    command.upgrade(build_alembic_config(build_sqlite_url(path)), "head")
    writer = create_write_engine(build_sqlite_url(path))
    reader = create_read_engine(build_sqlite_url(path))
    clock = Clock()
    persistence = WorkflowPersistenceService(writer, clock=clock)
    readback = WorkflowReadbackService(reader)
    try:
        async with write_transaction(writer) as session:
            assert await session.scalar(text("PRAGMA journal_mode")) == "wal"
            assert await session.scalar(text("PRAGMA synchronous")) == 2
            assert await session.scalar(text("PRAGMA foreign_keys")) == 1
            source = PromptRequest(raw_prompt=RAW, user_context=PRIVATE, created_at=NOW)
            session.add(source)
            await session.flush()
        env = Environment(
            path,
            writer,
            reader,
            persistence,
            readback,
            WorkflowRecoveryAssessmentService(readback, clock=clock),
            clock,
            source,
        )
        original_eval, original_final, original_reserve = (
            persistence.complete_evaluation,
            persistence.finalize_workflow,
            persistence.reserve_operation,
        )

        async def complete(value: EvaluationCompletion) -> EvaluationReceipt:
            env.evaluations.append(value)
            return await original_eval(value)

        async def finalize(value: WorkflowFinalization) -> Any:
            env.finalizations.append(value)
            return await original_final(value)

        async def reserve(*args: Any, **kwargs: Any) -> OperationReceipt:
            result = await original_reserve(*args, **kwargs)
            env.reservations.append(result)
            return result

        monkeypatch.setattr(persistence, "complete_evaluation", complete)
        monkeypatch.setattr(persistence, "finalize_workflow", finalize)
        monkeypatch.setattr(persistence, "reserve_operation", reserve)

        @event.listens_for(reader.sync_engine, "checkout")
        def checkout(*_args: Any) -> None:
            env.read_connections += 1

        @event.listens_for(reader.sync_engine, "checkin")
        def checkin(*_args: Any) -> None:
            env.read_connections -= 1

        yield env
        assert env.read_connections == 0
    finally:
        await reader.dispose()
        await writer.dispose()


def wire(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        provider="fake",
        text=json.dumps(data),
        finish_reason="complete",
        upstream_request_id=SECRET_ID,
    )


def architect(decision: str = "preserve", *, number: int = 1) -> LLMResponse:
    value: dict[str, Any] = dict(
        decision=decision,
        canonical_prompt=CanonicalPrompt.from_task(RAW + f"\nRevision {number}.").model_dump(
            mode="json"
        )
        if decision == "rewrite"
        else None,
        changes=[NOTE] if decision == "rewrite" else [],
        questions=[QUESTION] if decision == "clarify" else [],
    )
    if number > 1:
        value["addressed_issue_keys"] = ["issue_001"] if decision == "rewrite" else []
    return wire(value)


def critic(profile: str) -> LLMResponse:
    evidence = [dict(source="raw_prompt", quote="Python 3.12")]
    criteria = {
        key: dict(score=10, rationale="PRIVATE_CRITIC_RATIONALE", evidence=evidence)
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
                description="PRIVATE_CRITIC_FINDING",
                recommendation="PRIVATE_APPROVED_FEEDBACK",
                evidence=evidence,
            )
        )
    if profile in {"unassessable", "no_feedback"}:
        criteria["clarity"] = dict(score=None, rationale="Unknown external detail.", evidence=[])
    return wire(dict(criteria=criteria, issues=issues, summary="PRIVATE_CRITIC_SUMMARY"))


def script(profiles: list[str], *, decision: str | None = None) -> list[LLMResponse | LLMErrorCode]:
    values: list[LLMResponse | LLMErrorCode] = []
    for number, profile in enumerate(profiles, 1):
        values.extend(
            [architect("preserve" if number == 1 else "rewrite", number=number), critic(profile)]
        )
    if decision is not None:
        values.append(architect(decision, number=len(profiles) + 1))
    return values


class PausingFake(FakeLLMProvider):
    def __init__(
        self, values: list[LLMResponse | LLMErrorCode], block_at: int | None = None
    ) -> None:
        super().__init__(values)
        self.block_at, self.entered, self.release = block_at, asyncio.Event(), asyncio.Event()
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) == self.block_at:
            self.entered.set()
            await self.release.wait()
        return await super().generate(request)


async def new_workflow(db: Environment, limit: int = 3) -> WorkflowState:
    return (
        await db.persistence.create_workflow(
            db.source.id, WorkflowConfiguration(schema_version="1.0.0", max_evaluation_rounds=limit)
        )
    ).workflow


async def execute(
    db: Environment, state: WorkflowState, provider: FakeLLMProvider
) -> PersistentOptimizationResult:
    gateway = LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))
    return await asyncio.wait_for(
        execute_persisted_optimizer(
            db.persistence,
            PromptArchitectAgent(gateway, ModelSettings(model="fake")),
            PromptCriticAgent(gateway, ModelSettings(model="fake")),
            prompt_request_id=db.source.id,
            workflow_run_id=state.id,
            identity=PersistentExecutionIdentity(),
            lease_seconds=30,
        ),
        15,
    )


async def completed(
    db: Environment, profiles: list[str], *, limit: int = 3, decision: str | None = None
) -> PersistentOptimizationResult:
    provider = PausingFake(script(profiles, decision=decision))
    try:
        return await execute(db, await new_workflow(db, limit), provider)
    finally:
        await provider.aclose()


def dump(db: Environment) -> str:
    with sqlite3.connect(db.path) as connection:
        return "\n".join(connection.iterdump())


async def interrupted(db: Environment, call: int) -> WorkflowState:
    state = await new_workflow(db)
    provider = PausingFake(script(["bad", "critical", "qualified"]), block_at=call)
    task = asyncio.create_task(execute(db, state, provider))
    try:
        await asyncio.wait_for(provider.entered.wait(), 10)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        await provider.aclose()
    return state
