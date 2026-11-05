"""Bounded snapshot queries and in-memory adapters for existing history validators."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentRun, Evaluation, PromptRequest, PromptVersion, WorkflowRun
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT
from app.modules.prompts.workflow_read_contracts import WorkflowCursor
from app.modules.prompts.workflow_repository import AgentRunRepository, WorkflowRunRepository


@dataclass(frozen=True)
class WorkflowGraph:
    workflow: WorkflowRun
    source: PromptRequest | None
    operations: tuple[AgentRun, ...]
    versions: tuple[PromptVersion, ...]
    evaluations: tuple[Evaluation, ...]

    @property
    def truncated(self) -> bool:
        limit = MAX_EVALUATION_ROUNDS_HARD_LIMIT
        return (
            len(self.operations) > 2 * limit
            or len(self.versions) > limit
            or len(self.evaluations) > limit
        )


class WorkflowReadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def graph(
        self, workflow_id: UUID, request_id: UUID | None = None
    ) -> WorkflowGraph | None:
        statement = select(WorkflowRun).where(WorkflowRun.id == workflow_id)
        if request_id is not None:
            statement = statement.where(WorkflowRun.prompt_request_id == request_id)
        row = await self.session.scalar(statement)
        if row is None:
            return None
        source = await self.session.get(PromptRequest, row.prompt_request_id)
        operations = tuple(
            (
                await self.session.scalars(
                    select(AgentRun)
                    .where(AgentRun.workflow_run_id == row.id)
                    .order_by(
                        AgentRun.evaluation_round.asc().nulls_last(),
                        case(
                            (AgentRun.operation == "create", 0),
                            (AgentRun.operation == "revise", 1),
                            (AgentRun.operation == "evaluate", 2),
                            else_=3,
                        ),
                        AgentRun.started_at,
                        AgentRun.id,
                    )
                    .limit(2 * MAX_EVALUATION_ROUNDS_HARD_LIMIT + 1)
                )
            ).all()
        )
        versions = tuple(
            (
                await self.session.scalars(
                    select(PromptVersion)
                    .where(PromptVersion.workflow_run_id == row.id)
                    .order_by(
                        PromptVersion.evaluation_round.asc().nulls_last(),
                        PromptVersion.version_number,
                        PromptVersion.id,
                    )
                    .limit(MAX_EVALUATION_ROUNDS_HARD_LIMIT + 1)
                )
            ).all()
        )
        evaluations = tuple(
            (
                await self.session.scalars(
                    select(Evaluation)
                    .where(Evaluation.workflow_run_id == row.id)
                    .order_by(Evaluation.evaluation_round, Evaluation.id)
                    .limit(MAX_EVALUATION_ROUNDS_HARD_LIMIT + 1)
                )
            ).all()
        )
        return WorkflowGraph(row, source, operations, versions, evaluations)

    async def workflows(
        self, request_id: UUID, limit: int, cursor: WorkflowCursor | None
    ) -> list[WorkflowRun]:
        statement = select(WorkflowRun).where(WorkflowRun.prompt_request_id == request_id)
        if cursor is not None:
            statement = statement.where(
                or_(
                    WorkflowRun.created_at < cursor.created_at,
                    and_(
                        WorkflowRun.created_at == cursor.created_at,
                        WorkflowRun.id < cursor.workflow_run_id,
                    ),
                )
            )
        return list(
            (
                await self.session.scalars(
                    statement.order_by(WorkflowRun.created_at.desc(), WorkflowRun.id.desc()).limit(
                        limit + 1
                    )
                )
            ).all()
        )


class GraphWorkflows(WorkflowRunRepository):
    """Only validation reads are implemented; these adapters never receive a session."""

    def __init__(self, graph: WorkflowGraph) -> None:
        self.graph = graph

    async def get_source(self, identity: UUID) -> PromptRequest | None:
        source = self.graph.source
        return source if source is not None and source.id == identity else None

    async def evaluations(self, workflow_id: UUID) -> list[Evaluation]:
        return [value for value in self.graph.evaluations if value.workflow_run_id == workflow_id]

    async def versions(self, workflow_id: UUID) -> list[PromptVersion]:
        return [value for value in self.graph.versions if value.workflow_run_id == workflow_id]

    async def get_evaluation(self, identity: UUID, workflow_id: UUID) -> Evaluation | None:
        return next(
            (
                value
                for value in self.graph.evaluations
                if value.id == identity and value.workflow_run_id == workflow_id
            ),
            None,
        )

    async def get_version(self, identity: UUID, workflow_id: UUID) -> PromptVersion | None:
        return next(
            (
                value
                for value in self.graph.versions
                if value.id == identity and value.workflow_run_id == workflow_id
            ),
            None,
        )


class GraphAgents(AgentRunRepository):
    def __init__(self, graph: WorkflowGraph) -> None:
        self.graph = graph

    async def get_for_workflow(self, identity: UUID, workflow_id: UUID) -> AgentRun | None:
        return next(
            (
                value
                for value in self.graph.operations
                if value.id == identity and value.workflow_run_id == workflow_id
            ),
            None,
        )

    async def for_workflow(self, workflow_id: UUID) -> list[AgentRun]:
        return [value for value in self.graph.operations if value.workflow_run_id == workflow_id]
