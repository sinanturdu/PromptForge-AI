"""Independent, opt-in readback and exact persistence reconciliation; no dispatch."""

from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from functools import wraps
from re import fullmatch
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.read_transactions import read_snapshot
from app.llm.errors import LLMError
from app.modules.prompts.candidate_selection import SELECTION_POLICY_VERSION
from app.modules.prompts.persistence_contracts import CallMetadataStorage
from app.modules.prompts.workflow_history import staged_candidate
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    PersistenceError,
    WorkflowFinalization,
    validated,
)
from app.modules.prompts.workflow_provenance import operation_binding
from app.modules.prompts.workflow_read_contracts import (
    EvaluationSummary,
    OperationContent,
    OperationExpectation,
    OperationSummary,
    PersistenceReconciliation,
    SelectedEvaluation,
    VersionSummary,
    WorkflowContentReadback,
    WorkflowCursor,
    WorkflowListEntry,
    WorkflowPage,
    WorkflowReadback,
    WorkflowSummary,
)
from app.modules.prompts.workflow_read_repository import WorkflowReadRepository
from app.modules.prompts.workflow_read_validation import InspectedGraph, inspect_graph

ReconciliationStatus = Literal[
    "exact_committed",
    "committed_identity_only",
    "reserved_unresolved",
    "absent",
    "conflict",
    "integrity_inconsistent",
]


def _read_action[**P, T](
    method: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    @wraps(method)
    async def call(*args: P.args, **kwargs: P.kwargs) -> T:
        failure = None
        try:
            return await method(*args, **kwargs)
        except PersistenceError as error:
            failure = PersistenceError(error.code)
        except SQLAlchemyError:
            failure = PersistenceError("unavailable")
        except (ValueError, TypeError, LLMError):
            failure = PersistenceError("invalid")
        # Translate after snapshot rollback/close and outside private exception context.
        raise failure

    return call


def _ids(workflow_id: UUID, request_id: UUID | None) -> None:
    if not isinstance(workflow_id, UUID) or (
        request_id is not None and not isinstance(request_id, UUID)
    ):
        raise PersistenceError("invalid")


def _summary(checked: InspectedGraph) -> WorkflowReadback:
    graph, durable = checked.graph, checked.durable
    row = graph.workflow
    best = None if durable is None else durable.best()
    selected = (
        None
        if best is None
        else SelectedEvaluation(
            evaluation_id=best[0].id,
            prompt_version_id=best[1].id,
            evaluation_round=best[0].evaluation_round,
            total_score=best[2].scoring.total_score,
            qualified=best[2].scoring.passed,
            quality_status=best[2].scoring.status,
            computed_selection_policy_version=SELECTION_POLICY_VERSION,
        )
    )
    scored = (
        {}
        if durable is None or durable.history is None
        else {
            value.id: completed.evaluated.scoring
            for value, completed in zip(durable.evaluations, durable.history.rounds, strict=True)
        }
    )
    evaluated_creators = {value.agent_run_id for value in graph.versions}
    terminal = checked.terminal
    return WorkflowReadback(
        workflow=WorkflowSummary(
            id=row.id,
            prompt_request_id=row.prompt_request_id,
            status=row.status,
            state_revision=row.state_revision,
            current_round=row.current_round,
            completed_rounds=row.completed_rounds,
            max_evaluation_rounds=row.max_evaluation_rounds,
            created_at=row.created_at,
            started_at=row.started_at,
            finished_at=row.finished_at,
            lease_expires_at=row.lease_expires_at,
            claimed=row.execution_token is not None,
            source_digest=row.request_content_digest
            if row.request_content_digest is not None
            and fullmatch(r"[0-9a-f]{64}", row.request_content_digest)
            else None,
            configuration=checked.config,
            stored_best_version_id=row.final_version_id,
            stored_final_score=row.final_score,
            stored_qualified=row.qualified,
            termination_reason=None if terminal is None else terminal.termination_reason,
            error_code=None if terminal is None else terminal.error_code,
            clarification_question_count=0 if terminal is None else len(terminal.questions),
        ),
        operations=tuple(
            OperationSummary(
                id=value.id,
                workflow_run_id=value.workflow_run_id,
                operation=value.operation,
                evaluation_round=value.evaluation_round,
                status=value.status,
                agent_name=value.agent_name,
                agent_version=value.agent_version,
                instruction_version=value.instruction_version,
                configured_provider=value.provider,
                configured_model=value.model,
                started_at=value.started_at,
                finished_at=value.finished_at,
                result_kind=value.result_kind,
                baseline_evaluation_id=value.baseline_evaluation_id,
                telemetry=checked.telemetry.get(value.id),
                error_code=checked.results[value.id].error_code
                if value.id in checked.results
                else None,
                incomplete=value.status in {"PENDING", "RUNNING"},
                unresolved=value.status == "RUNNING",
                staged_unevaluated_candidate=value.id in checked.results
                and checked.results[value.id].kind == "candidate"
                and value.id not in evaluated_creators,
            )
            for value in graph.operations
        ),
        versions=tuple(
            VersionSummary.model_validate(
                {key: getattr(value, key) for key in VersionSummary.model_fields}
            )
            for value in graph.versions
        ),
        evaluations=tuple(
            EvaluationSummary(
                id=value.id,
                prompt_version_id=value.prompt_version_id,
                critic_agent_run_id=value.critic_agent_run_id,
                evaluation_round=value.evaluation_round,
                completed_at=value.completed_at,
                rubric_version=value.rubric_version,
                scoring_version=value.scoring_version,
                stored_total_score=value.total_score,
                quality_status=scored[value.id].status if value.id in scored else None,
                gates=scored[value.id].gates if value.id in scored else None,
            )
            for value in graph.evaluations
        ),
        selected=selected,
        history_valid=durable is not None,
        records_truncated=graph.truncated,
        findings=checked.findings,
    )


def _receipt(
    workflow_id: UUID,
    checked: InspectedGraph | None,
    status: ReconciliationStatus,
    *,
    agent_id: UUID | None = None,
    evaluation_id: UUID | None = None,
    version_id: UUID | None = None,
    previous_count: int | None = None,
) -> PersistenceReconciliation:
    summary = None if checked is None else _summary(checked)
    count = None if summary is None else summary.workflow.completed_rounds
    return PersistenceReconciliation(
        status=status,
        workflow_run_id=workflow_id,
        agent_run_id=agent_id,
        evaluation_id=evaluation_id,
        prompt_version_id=version_id,
        completed_rounds=count,
        progress_advanced=None
        if count is None or previous_count is None
        else count > previous_count,
        selected=None if summary is None else summary.selected,
        terminal_status=None
        if summary is None
        or summary.findings
        or summary.workflow.status not in {"COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"}
        else summary.workflow.status,
    )


class WorkflowReadbackService:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    @asynccontextmanager
    async def _inspect(
        self, workflow_id: UUID, request_id: UUID | None
    ) -> AsyncIterator[InspectedGraph | None]:
        _ids(workflow_id, request_id)
        async with read_snapshot(self._engine) as session:
            graph = await WorkflowReadRepository(session).graph(workflow_id, request_id)
            yield None if graph is None else await inspect_graph(graph)

    @_read_action
    async def read(
        self, workflow_id: UUID, *, prompt_request_id: UUID | None = None
    ) -> WorkflowReadback:
        async with self._inspect(workflow_id, prompt_request_id) as checked:
            if checked is None:
                raise PersistenceError("not_found")
            return _summary(checked)

    @_read_action
    async def read_content(
        self, workflow_id: UUID, *, prompt_request_id: UUID | None = None
    ) -> WorkflowContentReadback:
        async with self._inspect(workflow_id, prompt_request_id) as checked:
            if checked is None:
                raise PersistenceError("not_found")
            return WorkflowContentReadback(
                summary=_summary(checked),
                source=checked.source,
                history=None if checked.durable is None else checked.durable.history,
                operation_results=tuple(
                    OperationContent(agent_run_id=value.id, result=checked.results[value.id])
                    for value in checked.graph.operations
                    if value.id in checked.results
                ),
                terminal=checked.terminal,
            )

    @_read_action
    async def list_workflows(
        self, prompt_request_id: UUID, *, limit: int = 20, cursor: WorkflowCursor | None = None
    ) -> WorkflowPage:
        _ids(prompt_request_id, None)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise PersistenceError("invalid")
        if cursor is not None:
            cursor = validated(WorkflowCursor, cursor)
        async with read_snapshot(self._engine) as session:
            rows = await WorkflowReadRepository(session).workflows(prompt_request_id, limit, cursor)
            items = tuple(
                WorkflowListEntry.model_validate(
                    {
                        key: getattr(value, key)
                        for key in WorkflowListEntry.model_fields
                        if key != "integrity_checked"
                    }
                )
                for value in rows[:limit]
            )
            last = items[-1] if items else None
            return WorkflowPage(
                items=items,
                next_cursor=WorkflowCursor(created_at=last.created_at, workflow_run_id=last.id)
                if len(rows) > limit and last is not None
                else None,
            )

    async def list_operations(
        self, workflow_id: UUID, *, prompt_request_id: UUID | None = None
    ) -> tuple[OperationSummary, ...]:
        return (await self.read(workflow_id, prompt_request_id=prompt_request_id)).operations

    @_read_action
    async def reconcile_operation(
        self, expectation: OperationExpectation
    ) -> PersistenceReconciliation:
        command = validated(OperationExpectation, expectation)
        fence = command.fence
        async with self._inspect(fence.workflow_run_id, fence.prompt_request_id) as checked:
            if checked is None:
                return _receipt(fence.workflow_run_id, None, "absent")
            if checked.findings:
                return _receipt(fence.workflow_run_id, checked, "integrity_inconsistent")
            agent = next(
                (value for value in checked.graph.operations if value.id == command.agent_run_id),
                None,
            )
            if agent is None:
                return _receipt(fence.workflow_run_id, checked, "absent")
            identity = command.identity
            actual_metadata = (
                None
                if agent.call_metadata is None
                else CallMetadataStorage.from_json_value(agent.call_metadata)
            )
            if (
                checked.graph.workflow.execution_token != fence.execution_token
                or fence.state_revision > checked.graph.workflow.state_revision
                or fence.completed_rounds != command.binding.evaluation_round - 1
                or agent.operation_key != identity.operation_key
                or agent.agent_name != identity.agent_name
                or agent.agent_version != identity.agent_version
                or agent.instruction_version != identity.instruction_version
                or agent.provider != identity.provider
                or agent.model != (None if identity.settings is None else identity.settings.model)
                or identity.settings != command.parameters.settings
                or operation_binding(agent) != command.binding
                or agent.parameters != command.parameters.model_dump(mode="json")
            ):
                return _receipt(fence.workflow_run_id, checked, "conflict")
            if agent.status in {"RUNNING", "PENDING"}:
                return _receipt(
                    fence.workflow_run_id,
                    checked,
                    "reserved_unresolved",
                    agent_id=agent.id,
                    previous_count=fence.completed_rounds,
                )
            if command.result is None:
                return _receipt(
                    fence.workflow_run_id,
                    checked,
                    "committed_identity_only",
                    agent_id=agent.id,
                    previous_count=fence.completed_rounds,
                )
            if (
                checked.results.get(agent.id) != command.result
                or actual_metadata != command.metadata
            ):
                return _receipt(fence.workflow_run_id, checked, "conflict")
            result = checked.results[agent.id]
            evaluation = next(
                (value for value in checked.graph.evaluations if value.id == result.evaluation_id),
                None,
            )
            version = next(
                (value for value in checked.graph.versions if value.agent_run_id == agent.id), None
            )
            return _receipt(
                fence.workflow_run_id,
                checked,
                "exact_committed",
                agent_id=agent.id,
                evaluation_id=None if evaluation is None else evaluation.id,
                version_id=evaluation.prompt_version_id
                if evaluation is not None
                else None
                if version is None
                else version.id,
                previous_count=fence.completed_rounds,
            )

    @_read_action
    async def reconcile_evaluation(
        self, completion: EvaluationCompletion
    ) -> PersistenceReconciliation:
        command = validated(EvaluationCompletion, completion)
        fence = command.fence
        async with self._inspect(fence.workflow_run_id, fence.prompt_request_id) as checked:
            if checked is None:
                return _receipt(fence.workflow_run_id, None, "absent")
            if checked.findings:
                return _receipt(fence.workflow_run_id, checked, "integrity_inconsistent")
            critic = next(
                (
                    value
                    for value in checked.graph.operations
                    if value.id == command.critic_agent_run_id
                ),
                None,
            )
            if critic is None:
                return _receipt(fence.workflow_run_id, checked, "absent")
            row = checked.graph.workflow
            parameters = command.evaluated.critic
            if (
                row.execution_token != fence.execution_token
                or fence.state_revision > row.state_revision
                or fence.completed_rounds != command.evaluation_round - 1
                or critic.operation_key != command.critic_operation_key
                or critic.operation != "evaluate"
                or critic.evaluation_round != command.evaluation_round
                or critic.agent_version != parameters.agent_version
                or critic.instruction_version != parameters.instruction_version
                or (critic.provider is not None and critic.provider != parameters.metadata.provider)
                or (
                    critic.model is not None and critic.model != parameters.metadata.requested_model
                )
            ):
                return _receipt(fence.workflow_run_id, checked, "conflict")
            record = next(
                (
                    value
                    for value in checked.graph.evaluations
                    if value.critic_agent_run_id == critic.id
                ),
                None,
            )
            if record is None:
                creator = next(
                    (
                        value
                        for value in checked.graph.operations
                        if value.id == command.architect_agent_run_id
                    ),
                    None,
                )
                if (
                    checked.source is None
                    or creator is None
                    or staged_candidate(row, checked.source, creator) != command.evaluated.candidate
                ):
                    return _receipt(fence.workflow_run_id, checked, "conflict")
                return _receipt(
                    fence.workflow_run_id,
                    checked,
                    "reserved_unresolved",
                    agent_id=critic.id,
                    previous_count=fence.completed_rounds,
                )
            durable = checked.durable
            if (
                durable is None
                or durable.history is None
                or command.evaluated
                != durable.history.rounds[record.evaluation_round - 1].evaluated
                or command.architect_agent_run_id
                != command.evaluated.candidate.context.agent_run_id
                or record.evaluation_round != command.evaluation_round
            ):
                return _receipt(fence.workflow_run_id, checked, "conflict")
            return _receipt(
                fence.workflow_run_id,
                checked,
                "exact_committed",
                agent_id=critic.id,
                evaluation_id=record.id,
                version_id=record.prompt_version_id,
                previous_count=fence.completed_rounds,
            )

    @_read_action
    async def reconcile_terminal(
        self, finalization: WorkflowFinalization
    ) -> PersistenceReconciliation:
        command = validated(WorkflowFinalization, finalization)
        fence = command.fence
        async with self._inspect(fence.workflow_run_id, fence.prompt_request_id) as checked:
            if checked is None:
                return _receipt(fence.workflow_run_id, None, "absent")
            if checked.findings:
                return _receipt(fence.workflow_run_id, checked, "integrity_inconsistent")
            row = checked.graph.workflow
            if (
                row.execution_token != fence.execution_token
                or fence.state_revision > row.state_revision
                or row.completed_rounds != fence.completed_rounds
            ):
                return _receipt(fence.workflow_run_id, checked, "conflict")
            if checked.terminal is None:
                return _receipt(fence.workflow_run_id, checked, "absent")
            if checked.terminal != command.details:
                return _receipt(fence.workflow_run_id, checked, "conflict")
            if command.agent_run_id is not None:
                agent = next(
                    (
                        value
                        for value in checked.graph.operations
                        if value.id == command.agent_run_id
                        and value.evaluation_round == row.current_round
                    ),
                    None,
                )
                if agent is None:
                    return _receipt(fence.workflow_run_id, checked, "conflict")
                result = checked.results.get(agent.id)
                if (
                    result is None
                    or (row.status == "FAILED" and result.kind != "failed")
                    or (
                        row.status == "CLARIFICATION_REQUIRED"
                        and (
                            result.kind != "clarification"
                            or result.questions != command.details.questions
                        )
                    )
                    or (row.status == "COMPLETED" and agent.operation != "revise")
                ):
                    return _receipt(fence.workflow_run_id, checked, "conflict")
            return _receipt(
                fence.workflow_run_id,
                checked,
                "exact_committed",
                agent_id=command.agent_run_id,
                previous_count=fence.completed_rounds,
            )
