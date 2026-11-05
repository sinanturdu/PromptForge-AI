"""Validate a loaded read snapshot using existing provenance, scoring and policies."""

from dataclasses import dataclass
from uuid import UUID

from app.llm.errors import LLMError
from app.models import AgentRun
from app.modules.prompts.architect_contracts import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    ClarificationRequired,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.optimization_progress import (
    check_candidate_progress,
    check_revision_progress,
)
from app.modules.prompts.persistence_contracts import (
    CallMetadataStorage,
    OperationResultStorage,
    TerminalDetailsStorage,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.workflow_history import (
    DurableHistory,
    load_history,
    staged_candidate,
    staged_revision,
    stopping_reason,
    validate_best,
)
from app.modules.prompts.workflow_persistence_contracts import (
    OperationIdentity,
    OperationParameters,
    PersistenceError,
    WorkflowConfiguration,
)
from app.modules.prompts.workflow_provenance import (
    bound_source,
    observed_metadata,
    operation_binding,
    revision_binding,
)
from app.modules.prompts.workflow_read_contracts import (
    IntegrityCode,
    IntegrityFinding,
    IntegrityScope,
    ReadTelemetry,
)
from app.modules.prompts.workflow_read_repository import GraphAgents, GraphWorkflows, WorkflowGraph

_INVALID = (ValueError, TypeError, KeyError, LLMError, PersistenceError)
_TERMINAL = {"COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"}


@dataclass(frozen=True)
class InspectedGraph:
    graph: WorkflowGraph
    source: ArchitectRequestSnapshot | None
    config: WorkflowConfiguration | None
    durable: DurableHistory | None
    results: dict[UUID, OperationResultStorage]
    telemetry: dict[UUID, ReadTelemetry]
    terminal: TerminalDetailsStorage | None
    findings: tuple[IntegrityFinding, ...]


def _context(graph: WorkflowGraph, agent: AgentRun) -> ArchitectRunContext:
    row = graph.workflow
    return ArchitectRunContext(
        prompt_request_id=row.prompt_request_id,
        workflow_run_id=row.id,
        workflow_prompt_request_id=row.prompt_request_id,
        agent_run_id=agent.id,
        agent_workflow_run_id=row.id,
    )


def _telemetry(agent: AgentRun) -> ReadTelemetry | None:
    stored = (
        None
        if agent.call_metadata is None
        else CallMetadataStorage.from_json_value(agent.call_metadata).metadata
    )
    usage = None if stored is None else stored.usage
    expected = (
        None if stored is None else stored.attempts,
        None if stored is None else stored.repair_attempted,
        None if stored is None else int(stored.duration_ms),
        None if usage is None else usage.input_tokens,
        None if usage is None else usage.output_tokens,
    )
    if (
        agent.attempt_count,
        agent.repair_attempted,
        agent.latency_ms,
        agent.input_tokens,
        agent.output_tokens,
    ) != expected:
        raise PersistenceError("invalid")
    if stored is None:
        return None
    if (
        agent.provider is not None
        and stored.provider is not None
        and agent.provider != stored.provider
    ) or (
        agent.model is not None
        and stored.requested_model is not None
        and agent.model != stored.requested_model
    ):
        raise PersistenceError("invalid")
    return ReadTelemetry.model_validate(
        stored.model_dump(exclude={"request_id", "upstream_request_id"})
    )


async def inspect_graph(graph: WorkflowGraph) -> InspectedGraph:
    row = graph.workflow
    workflows, agents = GraphWorkflows(graph), GraphAgents(graph)
    findings: set[tuple[IntegrityCode, IntegrityScope, UUID | None]] = set()

    def add(code: IntegrityCode, scope: IntegrityScope, record: UUID | None = None) -> None:
        findings.add((code, scope, record))

    source = None
    config = None
    try:
        source, config = await bound_source(workflows, row)
    except _INVALID:
        add("source_binding", "source", row.prompt_request_id)
    durable = None
    if graph.truncated:
        add("history_size", "history", row.id)
    elif source is not None and config is not None:
        try:
            durable = await load_history(workflows, agents, row, source, config)
        except _INVALID:
            add("history_validation", "history", row.id)
    if durable is not None:
        try:
            validate_best(row, durable)
        except _INVALID:
            add("progress_selection", "workflow", row.id)
    operations = {value.id: value for value in graph.operations}
    versions = {value.id: value for value in graph.versions}
    evaluations = {value.id: value for value in graph.evaluations}
    for version in graph.versions:
        creator = operations.get(version.agent_run_id) if version.agent_run_id is not None else None
        if (
            version.prompt_request_id != row.prompt_request_id
            or creator is None
            or creator.evaluation_round != version.evaluation_round
            or (version.parent_version_id is not None and version.parent_version_id not in versions)
        ):
            add("version_lineage", "version", version.id)
    for evaluation in graph.evaluations:
        if (
            evaluation.prompt_version_id not in versions
            or evaluation.critic_agent_run_id not in operations
        ):
            add("evaluation_provenance", "evaluation", evaluation.id)
    results: dict[UUID, OperationResultStorage] = {}
    telemetry: dict[UUID, ReadTelemetry] = {}
    seen: set[tuple[int | None, str]] = set()
    for agent in graph.operations:
        role = "critic" if agent.operation == "evaluate" else "architect"
        if (agent.evaluation_round, role) in seen:
            add("operation_state", "operation", agent.id)
        seen.add((agent.evaluation_round, role))
        try:
            binding = operation_binding(agent)
            parameters = OperationParameters.from_json_value(agent.parameters)
            OperationIdentity.model_validate(
                dict(
                    operation_key=agent.operation_key,
                    agent_name=agent.agent_name,
                    agent_version=agent.agent_version,
                    instruction_version=agent.instruction_version,
                    provider=agent.provider,
                    settings=parameters.settings,
                )
            )
            if (
                not 1 <= binding.evaluation_round <= row.max_evaluation_rounds
                or binding.evaluation_round > row.current_round
                or (binding.operation == "revise")
                != (binding.evaluation_round > 1 and role == "architect")
            ):
                raise PersistenceError("invalid")
            if source is None or config is None:
                raise PersistenceError("invalid")
            if agent.operation == "evaluate":
                creator = (
                    operations.get(parameters.candidate_agent_run_id)
                    if parameters.candidate_agent_run_id is not None
                    else None
                )
                if creator is None or creator.evaluation_round != binding.evaluation_round:
                    raise PersistenceError("invalid")
                candidate = staged_candidate(row, source, creator)
                if parameters.input_fingerprint != critic_input_fingerprint(
                    make_critic_input(candidate)
                ):
                    raise PersistenceError("invalid")
            if binding.operation == "revise" and binding.evaluation_round > row.completed_rounds:
                if durable is None or durable.history is None:
                    raise PersistenceError("invalid")
                baseline = next(
                    (
                        value
                        for value in durable.evaluations
                        if value.id == binding.baseline_evaluation_id
                    ),
                    None,
                )
                if baseline is None:
                    raise PersistenceError("invalid")
                payload = make_revision_input(
                    durable.history.rounds[baseline.evaluation_round - 1].evaluated,
                    next_evaluation_round=binding.evaluation_round,
                    protected_spans=config.protected_spans,
                    context=_context(graph, agent),
                )
                if revision_binding(payload, baseline.id) != binding:
                    raise PersistenceError("invalid")
                previous_keys = tuple(
                    value.revision_attempt_key
                    for value in graph.operations
                    if value.id != agent.id and value.revision_attempt_key is not None
                )
                progress = check_revision_progress(
                    durable.history,
                    attempted_revision_keys=previous_keys,
                    protected_spans=config.protected_spans,
                )
                if (
                    progress.action != "revise"
                    or progress.baseline_round_number != baseline.evaluation_round
                ):
                    raise PersistenceError("invalid")
            if agent.status in {"COMPLETED", "FAILED"}:
                result = OperationResultStorage.from_json_value(agent.result_data)
                if (
                    agent.finished_at is None
                    or agent.result_kind != result.kind
                    or agent.error_code != result.error_code
                    or (agent.status == "FAILED") != (result.kind == "failed")
                ):
                    raise PersistenceError("invalid")
                if agent.operation == "evaluate" and agent.status == "COMPLETED":
                    if (
                        result.kind != "evaluation"
                        or result.evaluation_id not in evaluations
                        or evaluations[result.evaluation_id].critic_agent_run_id != agent.id
                    ):
                        raise PersistenceError("invalid")
                elif agent.status == "COMPLETED":
                    if agent.operation == "create":
                        if result.kind == "candidate":
                            staged_candidate(row, source, agent)
                        elif result.kind == "clarification":
                            ClarificationRequired(
                                original_request=source,
                                context=_context(graph, agent),
                                metadata=observed_metadata(agent),
                                agent_version=agent.agent_version,
                                instruction_version=agent.instruction_version,
                                questions=result.questions,
                            )
                        else:
                            raise PersistenceError("invalid")
                    elif binding.evaluation_round > row.completed_rounds:
                        assert durable is not None
                        staged_revision(row, source, agent, durable, config)
                results[agent.id] = result
            elif (
                agent.status not in {"RUNNING", "PENDING"}
                or agent.finished_at is not None
                or agent.result_data is not None
                or agent.result_kind is not None
                or agent.error_code is not None
            ):
                raise PersistenceError("invalid")
            if binding.evaluation_round <= row.completed_rounds and agent.status != "COMPLETED":
                raise PersistenceError("invalid")
        except _INVALID:
            add("operation_provenance", "operation", agent.id)
        try:
            value = _telemetry(agent)
            if value is not None:
                if agent.status in {"RUNNING", "PENDING"}:
                    raise PersistenceError("invalid")
                telemetry[agent.id] = value
        except _INVALID:
            add("operation_telemetry", "operation", agent.id)
    terminal = None
    try:
        active = row.status in {"GENERATING", "REVISING", "EVALUATING"}
        if row.status == "PENDING":
            if (
                row.execution_token is not None
                or row.lease_expires_at is not None
                or row.started_at is not None
                or row.current_round != 0
                or row.completed_rounds != 0
                or graph.operations
                or graph.evaluations
                or graph.versions
            ):
                raise PersistenceError("invalid")
        elif active or row.status in _TERMINAL:
            if (
                row.execution_token is None
                or row.lease_expires_at is None
                or row.started_at is None
            ):
                raise PersistenceError("invalid")
        else:
            raise PersistenceError("invalid")
        if row.status not in _TERMINAL:
            if any(
                value is not None
                for value in (
                    row.finished_at,
                    row.terminal_details,
                    row.stop_reason,
                    row.error_code,
                )
            ) or any(value.status == "FAILED" for value in graph.operations):
                raise PersistenceError("invalid")
            if row.status == "GENERATING" and any(
                value.operation != "create" or value.result_kind == "candidate"
                for value in graph.operations
            ):
                raise PersistenceError("invalid")
            if row.status == "REVISING" and any(
                value.evaluation_round == row.current_round
                and (value.operation == "evaluate" or value.result_kind == "candidate")
                for value in graph.operations
            ):
                raise PersistenceError("invalid")
            if row.status == "EVALUATING":
                if row.current_round == row.completed_rounds:
                    if (
                        durable is None
                        or config is None
                        or await stopping_reason(durable, config, agents, row) is None
                    ):
                        raise PersistenceError("invalid")
                elif not any(
                    value.evaluation_round == row.current_round
                    and value.result_kind == "candidate"
                    and value.status == "COMPLETED"
                    for value in graph.operations
                ):
                    raise PersistenceError("invalid")
        else:
            terminal = TerminalDetailsStorage.from_json_value(row.terminal_details)
            if (
                row.finished_at is None
                or terminal.execution_status != row.status
                or terminal.termination_reason != row.stop_reason
                or terminal.error_code != row.error_code
                or any(value.status in {"RUNNING", "PENDING"} for value in graph.operations)
            ):
                raise PersistenceError("invalid")
            if durable is None or source is None or config is None:
                raise PersistenceError("invalid")
            current = [
                value for value in graph.operations if value.evaluation_round == row.current_round
            ]
            if row.status == "COMPLETED":
                if durable.history is None:
                    raise PersistenceError("invalid")
                if row.current_round == row.completed_rounds:
                    if (
                        await stopping_reason(durable, config, agents, row)
                        != terminal.termination_reason
                    ):
                        raise PersistenceError("invalid")
                else:
                    proposed = [
                        value
                        for value in current
                        if value.operation == "revise" and value.status == "COMPLETED"
                    ]
                    if (
                        len(proposed) != 1
                        or check_candidate_progress(
                            durable.history,
                            staged_revision(row, source, proposed[0], durable, config),
                        ).reason
                        != terminal.termination_reason
                    ):
                        raise PersistenceError("invalid")
            elif row.status == "CLARIFICATION_REQUIRED":
                questions = [
                    value
                    for value in current
                    if value.id in results
                    and results[value.id].kind == "clarification"
                    and results[value.id].questions == terminal.questions
                ]
                if len(questions) != 1 or (row.completed_rounds == 0) != (
                    questions[0].operation == "create"
                ):
                    raise PersistenceError("invalid")
            else:
                failed = [value for value in current if value.status == "FAILED"]
                roles = {
                    "create": "architect_create",
                    "revise": "architect_revise",
                    "evaluate": "critic_evaluate",
                }
                if any(
                    value.error_code != terminal.error_code
                    or roles.get(value.operation) != terminal.failed_operation
                    for value in failed
                ):
                    raise PersistenceError("invalid")
    except _INVALID:
        add(
            "terminal_state" if row.status in _TERMINAL else "workflow_state",
            "terminal" if row.status in _TERMINAL else "workflow",
            row.id,
        )
    return InspectedGraph(
        graph,
        source,
        config,
        durable,
        results,
        telemetry,
        terminal,
        tuple(
            IntegrityFinding(code=code, scope=scope, record_id=record)
            for code, scope, record in sorted(
                findings, key=lambda value: (value[0], value[1], str(value[2]))
            )
        ),
    )
