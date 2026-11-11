"""Reconstruct bounded durable history using existing M5D contracts and policies."""

from dataclasses import dataclass

from app.agents.critic_schemas import CriticResult
from app.agents.revision_instructions import REVISION_VERSION
from app.agents.revision_schemas import (
    RevisedCandidate,
    RevisionClarification,
    RevisionResult,
    UnchangedBaseline,
)
from app.models import AgentRun, Evaluation, PromptVersion, WorkflowRun
from app.modules.prompts.architect_contracts import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.optimization_contracts import EvaluationHistory, EvaluationRound
from app.modules.prompts.optimization_progress import (
    check_candidate_progress,
    check_revision_progress,
)
from app.modules.prompts.persistence_contracts import (
    AssessmentStorage,
    CandidateStorage,
    EvaluationStorage,
    OperationResultStorage,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.workflow_persistence_contracts import (
    OperationParameters,
    PersistenceError,
    WorkflowConfiguration,
)
from app.modules.prompts.workflow_provenance import (
    observed_metadata,
    operation_binding,
    revision_binding,
)
from app.modules.prompts.workflow_repository import AgentRunRepository, WorkflowRunRepository


@dataclass(frozen=True)
class DurableHistory:
    evaluations: tuple[Evaluation, ...]
    versions: tuple[PromptVersion, ...]
    history: EvaluationHistory | None

    def best(self) -> tuple[Evaluation, PromptVersion, EvaluatedCandidate] | None:
        if self.history is None:
            return None
        index = select_best_candidate(self.history).round_number - 1
        return self.evaluations[index], self.versions[index], self.history.rounds[index].evaluated


def _context(row: WorkflowRun, agent: AgentRun) -> ArchitectRunContext:
    return ArchitectRunContext(
        prompt_request_id=row.prompt_request_id,
        workflow_run_id=row.id,
        workflow_prompt_request_id=row.prompt_request_id,
        agent_run_id=agent.id,
        agent_workflow_run_id=row.id,
    )


def staged_candidate(
    row: WorkflowRun, source: ArchitectRequestSnapshot, agent: AgentRun
) -> CandidateReady:
    binding = operation_binding(agent)
    stored = OperationResultStorage.from_json_value(agent.result_data)
    if (
        agent.workflow_run_id != row.id
        or agent.status != "COMPLETED"
        or agent.finished_at is None
        or agent.result_kind != "candidate"
        or binding.operation not in {"create", "revise"}
        or stored.candidate is None
    ):
        raise PersistenceError("conflict")
    value = stored.candidate
    if binding.operation == "create":
        WorkflowConfiguration.from_json_value(row.config_snapshot).validate_initial_renderer(value)
    candidate = CandidateReady.model_validate(
        dict(
            original_request=source,
            context=_context(row, agent),
            metadata=observed_metadata(agent),
            agent_version=agent.agent_version,
            instruction_version=agent.instruction_version,
            decision=value.decision,
            canonical_prompt=value.canonical_prompt,
            rendered_prompt=value.rendered_prompt,
            changes=value.changes,
            renderer_name=value.renderer_name,
            renderer_version=value.renderer_version,
        )
    )
    if CandidateStorage.from_candidate(candidate) != value:
        raise PersistenceError("conflict")
    return candidate


def staged_revision(
    row: WorkflowRun,
    source: ArchitectRequestSnapshot,
    agent: AgentRun,
    durable: DurableHistory,
    config: WorkflowConfiguration,
) -> RevisionResult:
    binding = operation_binding(agent)
    stored = OperationResultStorage.from_json_value(agent.result_data)
    if (
        binding.operation != "revise"
        or durable.history is None
        or binding.protected_spans != config.protected_spans
    ):
        raise PersistenceError("conflict")
    index = next(
        (
            i
            for i, value in enumerate(durable.evaluations)
            if value.id == binding.baseline_evaluation_id
        ),
        None,
    )
    if index is None or durable.history.rounds[index].round_number >= binding.evaluation_round:
        raise PersistenceError("conflict")
    baseline = durable.history.rounds[index].evaluated
    payload = make_revision_input(
        baseline,
        next_evaluation_round=binding.evaluation_round,
        protected_spans=binding.protected_spans,
        context=_context(row, agent),
    )
    if revision_binding(payload, durable.evaluations[index].id) != binding:
        raise PersistenceError("conflict")
    common = dict(
        baseline=baseline.candidate,
        context=payload.context,
        next_evaluation_round=binding.evaluation_round,
        protected_spans=binding.protected_spans,
        baseline_input_fingerprint=baseline.critic.input_fingerprint,
        eligible_issue_keys=binding.eligible_issue_keys,
        metadata=observed_metadata(agent),
        agent_version=agent.agent_version,
        instruction_version=agent.instruction_version,
        revision_version=REVISION_VERSION,
    )
    if stored.kind == "candidate":
        return RevisedCandidate.model_validate(
            dict(
                **common,
                candidate=staged_candidate(row, source, agent),
                addressed_issue_keys=stored.addressed_issue_keys,
            )
        )
    if agent.status != "COMPLETED" or agent.finished_at is None or agent.result_kind != stored.kind:
        raise PersistenceError("conflict")
    if stored.kind == "unchanged":
        return UnchangedBaseline.model_validate(dict(**common, reason=stored.unchanged_reason))
    if stored.kind == "clarification":
        return RevisionClarification.model_validate(dict(**common, questions=stored.questions))
    raise PersistenceError("conflict")


async def load_history(
    workflows: WorkflowRunRepository,
    agents: AgentRunRepository,
    row: WorkflowRun,
    source: ArchitectRequestSnapshot,
    config: WorkflowConfiguration,
    *,
    count: int | None = None,
) -> DurableHistory:
    evaluations = tuple(await workflows.evaluations(row.id))
    expected = row.completed_rounds if count is None else count
    versions = await workflows.versions(row.id)
    if (
        len(evaluations) != expected
        or expected > row.max_evaluation_rounds
        or tuple(value.evaluation_round for value in evaluations) != tuple(range(1, expected + 1))
        or {value.id for value in versions} != {value.prompt_version_id for value in evaluations}
    ):
        raise PersistenceError("conflict")
    by_id = {value.id: value for value in versions}
    rounds: tuple[EvaluationRound, ...] = ()
    ordered: tuple[PromptVersion, ...] = ()
    for record in evaluations:
        version = by_id[record.prompt_version_id]
        if version.agent_run_id is None:
            raise PersistenceError("invalid")
        creator = await agents.get_for_workflow(version.agent_run_id, row.id)
        critic = await agents.get_for_workflow(record.critic_agent_run_id, row.id)
        if creator is None or critic is None:
            raise PersistenceError("invalid")
        candidate = staged_candidate(row, source, creator)
        binding = operation_binding(creator)
        critic_binding = operation_binding(critic)
        params = OperationParameters.from_json_value(critic.parameters)
        critic_result = OperationResultStorage.from_json_value(critic.result_data)
        if (
            version.prompt_request_id != source.id
            or version.evaluation_round != record.evaluation_round
            or binding.evaluation_round != record.evaluation_round
            or critic_binding.operation != "evaluate"
            or critic_binding.evaluation_round != record.evaluation_round
            or critic.status != "COMPLETED"
            or critic.finished_at is None
            or critic.result_kind != "evaluation"
            or critic_result.kind != "evaluation"
            or critic_result.evaluation_id != record.id
            or params.candidate_agent_run_id != creator.id
            or params.input_fingerprint != record.input_fingerprint
            or version.canonical_prompt != candidate.canonical_prompt.model_dump(mode="json")
            or version.rendered_prompt != candidate.rendered_prompt
            or version.target_mode != source.target_mode.value
            or version.renderer_name != candidate.renderer_name
            or version.renderer_version != candidate.renderer_version
            or version.change_summary != list(candidate.changes)
            or version.source
            != {"create": "engineer_create", "revise": "engineer_revise"}[binding.operation]
            or any(
                span.text(source) not in candidate.rendered_prompt
                for span in config.protected_spans
            )
        ):
            raise PersistenceError("invalid")
        output = CriticResult.model_validate(
            dict(
                output=AssessmentStorage.from_json_value(record.assessment).assessment,
                input_fingerprint=record.input_fingerprint,
                evaluated_target_mode=source.target_mode,
                evaluated_renderer_name=record.evaluated_renderer_name,
                evaluated_renderer_version=record.evaluated_renderer_version,
                agent_version=critic.agent_version,
                instruction_version=critic.instruction_version,
                rubric_version=record.rubric_version,
                metadata=observed_metadata(critic),
            )
        )
        evaluated = EvaluatedCandidate.model_validate(dict(candidate=candidate, critic=output))
        projection = EvaluationStorage.from_evaluated(
            evaluated,
            workflow_run_id=row.id,
            prompt_version_id=version.id,
            critic_agent_run_id=critic.id,
            evaluation_round=record.evaluation_round,
            completed_at=record.completed_at,
        ).record_values()
        if any(getattr(record, key) != value for key, value in projection.items()):
            raise PersistenceError("invalid")
        baseline_round = None
        revision = None
        if binding.operation == "revise":
            previous = DurableHistory(
                evaluations[: len(rounds)], ordered, EvaluationHistory(rounds=rounds)
            )
            if previous.history is None:
                raise PersistenceError("invalid")
            revision_value = staged_revision(row, source, creator, previous, config)
            if (
                not isinstance(revision_value, RevisedCandidate)
                or check_candidate_progress(previous.history, revision_value).action != "evaluate"
            ):
                raise PersistenceError("invalid")
            revision = revision_value
            baseline_round = next(
                value.evaluation_round
                for value in previous.evaluations
                if value.id == binding.baseline_evaluation_id
            )
            parent = previous.versions[baseline_round - 1]
            if version.parent_version_id != parent.id:
                raise PersistenceError("invalid")
        elif record.evaluation_round != 1 or version.parent_version_id is not None:
            raise PersistenceError("invalid")
        rounds = (
            *rounds,
            EvaluationRound(
                round_number=record.evaluation_round,
                evaluated=evaluated,
                baseline_round_number=baseline_round,
                revision=revision,
            ),
        )
        ordered = (*ordered, version)
    return DurableHistory(
        evaluations, ordered, EvaluationHistory(rounds=rounds) if rounds else None
    )


def validate_best(row: WorkflowRun, durable: DurableHistory) -> None:
    count = len(durable.evaluations)
    if (
        row.current_round not in {count, count + 1}
        or row.current_round > row.max_evaluation_rounds
        or (row.status == "GENERATING" and (count != 0 or row.current_round != 1))
        or (row.status == "REVISING" and (count == 0 or row.current_round != count + 1))
    ):
        raise PersistenceError("conflict")
    best = durable.best()
    expected = (
        (None, None, None)
        if best is None
        else (best[1].id, best[2].scoring.total_score, best[2].scoring.passed)
    )
    if (row.final_version_id, row.final_score, row.qualified) != expected:
        raise PersistenceError("conflict")


async def stopping_reason(
    durable: DurableHistory,
    config: WorkflowConfiguration,
    agents: AgentRunRepository,
    row: WorkflowRun,
) -> str | None:
    if durable.history is None:
        return None
    operations = await agents.for_workflow(row.id)
    attempted = tuple(
        value.revision_attempt_key for value in operations if value.revision_attempt_key is not None
    )
    decision = check_revision_progress(
        durable.history,
        attempted_revision_keys=attempted,
        protected_spans=config.protected_spans,
    )
    if decision.reason == "qualified":
        return "quality_passed"
    if len(durable.evaluations) >= config.max_evaluation_rounds:
        return "evaluation_limit"
    return decision.reason if decision.action == "stop" else None
