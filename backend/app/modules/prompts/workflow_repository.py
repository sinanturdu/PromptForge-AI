"""Scoped WorkflowRun/AgentRun SQL; only services own commit and rollback."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun
from app.modules.prompts.workflow_persistence_contracts import WorkflowFence


class WorkflowRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, identity: UUID) -> WorkflowRun | None:
        return await self.session.get(WorkflowRun, identity)

    async def get_for_request(self, identity: UUID, request_id: UUID) -> WorkflowRun | None:
        return await self.session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.id == identity, WorkflowRun.prompt_request_id == request_id
            )
        )

    async def get_idempotent(self, request_id: UUID, digest: str) -> WorkflowRun | None:
        return await self.session.scalar(
            select(WorkflowRun).where(
                WorkflowRun.prompt_request_id == request_id,
                WorkflowRun.client_idempotency_key_digest == digest,
            )
        )

    async def get_source(self, identity: UUID) -> PromptRequest | None:
        return await self.session.get(PromptRequest, identity)

    async def insert(self, row: WorkflowRun) -> None:
        self.session.add(row)
        await self.session.flush()

    async def claim(
        self, row: WorkflowRun, revision: int, token: UUID, now: datetime, expires: datetime
    ) -> bool:
        identity = await self.session.scalar(
            update(WorkflowRun)
            .where(
                WorkflowRun.id == row.id,
                WorkflowRun.prompt_request_id == row.prompt_request_id,
                WorkflowRun.status == "PENDING",
                WorkflowRun.state_revision == revision,
                WorkflowRun.execution_token.is_(None),
                WorkflowRun.lease_expires_at.is_(None),
                WorkflowRun.current_round == 0,
                WorkflowRun.completed_rounds == 0,
            )
            .values(
                status="GENERATING",
                current_round=1,
                state_revision=WorkflowRun.state_revision + 1,
                execution_token=token,
                lease_expires_at=expires,
                started_at=now,
            )
            .returning(WorkflowRun.id)
            .execution_options(synchronize_session=False)
        )
        return identity is not None

    async def advance(
        self,
        fence: WorkflowFence,
        phase: str,
        next_phase: str,
        now: datetime,
        terminal: dict[str, Any] | None = None,
    ) -> bool:
        statement = (
            update(WorkflowRun)
            .where(
                WorkflowRun.id == fence.workflow_run_id,
                WorkflowRun.prompt_request_id == fence.prompt_request_id,
                WorkflowRun.execution_token == fence.execution_token,
                WorkflowRun.lease_expires_at > now,
                WorkflowRun.status == phase,
                WorkflowRun.state_revision == fence.state_revision,
                WorkflowRun.completed_rounds == fence.completed_rounds,
            )
            .values(status=next_phase, state_revision=WorkflowRun.state_revision + 1)
        )
        if terminal is not None:
            statement = statement.values(
                terminal_details=terminal,
                finished_at=now,
                stop_reason=terminal["termination_reason"],
                error_code=terminal["error_code"],
            )
        identity = await self.session.scalar(
            statement.returning(WorkflowRun.id).execution_options(synchronize_session=False)
        )
        return identity is not None

    async def refresh(self, row: WorkflowRun) -> None:
        await self.session.refresh(row)

    async def get_evaluation(self, identity: UUID, workflow_id: UUID) -> Evaluation | None:
        return await self.session.scalar(
            select(Evaluation).where(
                Evaluation.id == identity, Evaluation.workflow_run_id == workflow_id
            )
        )

    async def get_version(self, identity: UUID, workflow_id: UUID) -> PromptVersion | None:
        return await self.session.scalar(
            select(PromptVersion).where(
                PromptVersion.id == identity, PromptVersion.workflow_run_id == workflow_id
            )
        )

    async def evaluations(self, workflow_id: UUID) -> list[Evaluation]:
        return list(
            (
                await self.session.scalars(
                    select(Evaluation)
                    .where(Evaluation.workflow_run_id == workflow_id)
                    .order_by(Evaluation.evaluation_round)
                )
            ).all()
        )

    async def versions(self, workflow_id: UUID) -> list[PromptVersion]:
        return list(
            (
                await self.session.scalars(
                    select(PromptVersion).where(PromptVersion.workflow_run_id == workflow_id)
                )
            ).all()
        )

    async def record_progress(
        self,
        fence: WorkflowFence,
        phase: str,
        now: datetime,
        *,
        completed: int,
        current: int,
        next_phase: str,
        best_version_id: UUID | None,
        score: Decimal | None,
        qualified: bool | None,
        terminal: dict[str, Any] | None = None,
    ) -> bool:
        statement = (
            update(WorkflowRun)
            .where(
                WorkflowRun.id == fence.workflow_run_id,
                WorkflowRun.prompt_request_id == fence.prompt_request_id,
                WorkflowRun.execution_token == fence.execution_token,
                WorkflowRun.lease_expires_at > now,
                WorkflowRun.status == phase,
                WorkflowRun.state_revision == fence.state_revision,
                WorkflowRun.completed_rounds == fence.completed_rounds,
            )
            .values(
                completed_rounds=completed,
                current_round=current,
                status=next_phase,
                state_revision=WorkflowRun.state_revision + 1,
                final_version_id=best_version_id,
                final_score=score,
                qualified=qualified,
            )
        )
        if terminal is not None:
            statement = statement.values(
                terminal_details=terminal,
                stop_reason=terminal["termination_reason"],
                error_code=terminal["error_code"],
                finished_at=now,
            )
        return (
            await self.session.scalar(
                statement.returning(WorkflowRun.id).execution_options(synchronize_session=False)
            )
            is not None
        )


class EvaluationRepository:
    """Version/Evaluation SQL only; callers validate and own the atomic transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_critic(self, workflow_id: UUID, critic_id: UUID) -> Evaluation | None:
        return await self.session.scalar(
            select(Evaluation).where(
                Evaluation.workflow_run_id == workflow_id,
                Evaluation.critic_agent_run_id == critic_id,
            )
        )

    async def next_version_number(self, request_id: UUID) -> int:
        maximum = await self.session.scalar(
            select(func.max(PromptVersion.version_number)).where(
                PromptVersion.prompt_request_id == request_id
            )
        )
        return 1 if maximum is None else maximum + 1

    async def insert_version(self, row: PromptVersion) -> None:
        self.session.add(row)
        await self.session.flush()

    async def insert_evaluation(self, row: Evaluation) -> None:
        self.session.add(row)
        await self.session.flush()


class AgentRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, identity: UUID) -> AgentRun | None:
        return await self.session.get(AgentRun, identity)

    async def get_for_workflow(self, identity: UUID, workflow_id: UUID) -> AgentRun | None:
        return await self.session.scalar(
            select(AgentRun).where(AgentRun.id == identity, AgentRun.workflow_run_id == workflow_id)
        )

    async def get_operation(self, workflow_id: UUID, key: str) -> AgentRun | None:
        return await self.session.scalar(
            select(AgentRun).where(
                AgentRun.workflow_run_id == workflow_id, AgentRun.operation_key == key
            )
        )

    async def get_revision(self, workflow_id: UUID, key: str) -> AgentRun | None:
        return await self.session.scalar(
            select(AgentRun).where(
                AgentRun.workflow_run_id == workflow_id, AgentRun.revision_attempt_key == key
            )
        )

    async def for_round(self, workflow_id: UUID, number: int) -> list[AgentRun]:
        return list(
            (
                await self.session.scalars(
                    select(AgentRun).where(
                        AgentRun.workflow_run_id == workflow_id, AgentRun.evaluation_round == number
                    )
                )
            ).all()
        )

    async def for_workflow(self, workflow_id: UUID) -> list[AgentRun]:
        return list(
            (
                await self.session.scalars(
                    select(AgentRun).where(AgentRun.workflow_run_id == workflow_id)
                )
            ).all()
        )

    async def insert(self, row: AgentRun) -> None:
        self.session.add(row)
        await self.session.flush()

    async def finish(
        self,
        row: AgentRun,
        result: dict[str, Any],
        metadata: dict[str, Any] | None,
        now: datetime,
    ) -> bool:
        observed = metadata["metadata"] if metadata is not None else None
        usage = observed["usage"] if observed is not None else None
        identity = await self.session.scalar(
            update(AgentRun)
            .where(
                AgentRun.id == row.id,
                AgentRun.workflow_run_id == row.workflow_run_id,
                AgentRun.status == "RUNNING",
            )
            .values(
                status="FAILED" if result["kind"] == "failed" else "COMPLETED",
                result_kind=result["kind"],
                result_data=result,
                call_metadata=metadata,
                error_code=result["error_code"],
                finished_at=now,
                attempt_count=None if observed is None else observed["attempts"],
                repair_attempted=None if observed is None else observed["repair_attempted"],
                latency_ms=None if observed is None else int(observed["duration_ms"]),
                input_tokens=None if usage is None else usage["input_tokens"],
                output_tokens=None if usage is None else usage["output_tokens"],
            )
            .returning(AgentRun.id)
            .execution_options(synchronize_session=False)
        )
        return identity is not None

    async def refresh(self, row: AgentRun) -> None:
        await self.session.refresh(row)
