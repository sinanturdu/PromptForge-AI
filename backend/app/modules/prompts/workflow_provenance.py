"""Loaded-row source/revision checks; no dispatch, history selection or record writes."""

import hashlib
import json
from uuid import UUID

from app.agents.critic_schemas import CriticResult
from app.agents.revision_schemas import RevisionInput
from app.llm.schemas import CallMetadata
from app.models import AgentRun, WorkflowRun
from app.modules.prompts.architect_contracts import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate
from app.modules.prompts.optimization_progress import revision_attempt_key
from app.modules.prompts.persistence_contracts import (
    STORAGE_VERSION,
    AssessmentStorage,
    CallMetadataStorage,
    CandidateStorage,
    GateStorage,
    OperationBindingStorage,
    OperationResultStorage,
    RequestPreferenceSnapshot,
    request_content_digest,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_persistence_contracts import (
    OperationParameters,
    PersistenceError,
    WorkflowConfiguration,
    validated,
)
from app.modules.prompts.workflow_repository import AgentRunRepository, WorkflowRunRepository


def operation_binding(agent: AgentRun) -> OperationBindingStorage:
    value = OperationBindingStorage.from_json_value(agent.binding_data)
    parameters = OperationParameters.from_json_value(agent.parameters)
    expected_name = "PromptCriticAgent" if value.operation == "evaluate" else "PromptArchitectAgent"
    if (
        agent.operation != value.operation
        or agent.evaluation_round != value.evaluation_round
        or agent.agent_name != expected_name
        or agent.revision_attempt_key != value.revision_attempt_key
        or agent.baseline_evaluation_id != value.baseline_evaluation_id
        or agent.feedback_projection_digest != value.feedback_projection_digest
        or agent.model != (None if parameters.settings is None else parameters.settings.model)
        or (value.operation == "evaluate") != (parameters.candidate_agent_run_id is not None)
        or (value.operation == "evaluate") != (parameters.input_fingerprint is not None)
    ):
        raise PersistenceError("invalid")
    return value


async def bound_source(
    repository: WorkflowRunRepository,
    workflow: WorkflowRun,
) -> tuple[ArchitectRequestSnapshot, WorkflowConfiguration]:
    row = await repository.get_source(workflow.prompt_request_id)
    if row is None:
        raise PersistenceError("not_found")
    source = ArchitectRequestSnapshot.model_validate(
        PromptRequestRead.model_validate(row).model_dump()
    )
    snapshot = RequestPreferenceSnapshot.from_json_value(workflow.request_snapshot)
    config = WorkflowConfiguration.from_json_value(workflow.config_snapshot)
    if (
        snapshot != RequestPreferenceSnapshot.from_request(source)
        or workflow.request_content_digest != request_content_digest(source)
        or workflow.max_evaluation_rounds != config.max_evaluation_rounds
    ):
        raise PersistenceError("conflict")
    for span in config.protected_spans:
        span.text(source)
    return source, config


def revision_binding(
    payload: RevisionInput, baseline_evaluation_id: UUID
) -> OperationBindingStorage:
    """Version-one approved feedback projection: exact supported finding keys/content."""
    value = validated(RevisionInput, payload)
    projection = dict(
        schema_version=STORAGE_VERSION,
        feedback=[finding.model_dump(mode="json") for finding in value.feedback],
    )
    digest = hashlib.sha256(
        json.dumps(projection, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return OperationBindingStorage(
        schema_version=STORAGE_VERSION,
        operation="revise",
        evaluation_round=value.next_evaluation_round,
        baseline_evaluation_id=baseline_evaluation_id,
        revision_attempt_key=revision_attempt_key(value),
        feedback_projection_digest=digest,
        protected_spans=value.protected_spans,
        eligible_issue_keys=tuple(finding.issue_key for finding in value.feedback),
    )


def observed_metadata(row: AgentRun) -> CallMetadata:
    stored = CallMetadataStorage.from_json_value(row.call_metadata)
    if stored.metadata is None:
        raise PersistenceError("invalid")
    value = CallMetadata.model_validate(stored.metadata.model_dump())
    if (
        (row.provider is not None and row.provider != value.provider)
        or (row.model is not None and row.model != value.requested_model)
        or row.attempt_count != value.attempts
        or row.repair_attempted != value.repair_attempted
    ):
        raise PersistenceError("invalid")
    return value


async def checked_revision(
    workflows: WorkflowRunRepository,
    agents: AgentRunRepository,
    workflow: WorkflowRun,
    source: ArchitectRequestSnapshot,
    binding: OperationBindingStorage,
    baseline: CandidateReady,
    context: ArchitectRunContext,
) -> RevisionInput:
    if binding.baseline_evaluation_id is None:
        raise PersistenceError("invalid")
    evaluation = await workflows.get_evaluation(binding.baseline_evaluation_id, workflow.id)
    if evaluation is None:
        raise PersistenceError("conflict")
    version = await workflows.get_version(evaluation.prompt_version_id, workflow.id)
    critic = await agents.get_for_workflow(evaluation.critic_agent_run_id, workflow.id)
    creator = (
        await agents.get_for_workflow(version.agent_run_id, workflow.id)
        if version is not None and version.agent_run_id is not None
        else None
    )
    if version is None or critic is None or creator is None:
        raise PersistenceError("invalid")
    creator_binding = operation_binding(creator)
    critic_binding = operation_binding(critic)
    if (
        creator_binding.evaluation_round != evaluation.evaluation_round
        or critic_binding.evaluation_round != evaluation.evaluation_round
    ):
        raise PersistenceError("invalid")
    if creator_binding.operation == "create":
        if version.parent_version_id is not None:
            raise PersistenceError("invalid")
    else:
        if creator_binding.baseline_evaluation_id is None:
            raise PersistenceError("invalid")
        parent = await workflows.get_evaluation(creator_binding.baseline_evaluation_id, workflow.id)
        if (
            parent is None
            or parent.prompt_version_id != version.parent_version_id
            or parent.evaluation_round >= evaluation.evaluation_round
        ):
            raise PersistenceError("invalid")
    if (
        version.prompt_request_id != source.id
        or version.evaluation_round != evaluation.evaluation_round
        or not 1 <= evaluation.evaluation_round <= workflow.completed_rounds
        or not evaluation.evaluation_round
        < binding.evaluation_round
        <= workflow.max_evaluation_rounds
        or critic.status != "COMPLETED"
        or critic.operation != "evaluate"
        or critic.agent_name != "PromptCriticAgent"
        or critic.result_kind != "evaluation"
        or critic.evaluation_round != evaluation.evaluation_round
        or creator.status != "COMPLETED"
        or creator.operation not in {"create", "revise"}
        or creator.agent_name != "PromptArchitectAgent"
        or creator.result_kind != "candidate"
        or creator.evaluation_round != evaluation.evaluation_round
        or version.source
        != {"create": "engineer_create", "revise": "engineer_revise"}[creator.operation]
        or baseline.original_request != source
        or baseline.context
        != ArchitectRunContext(
            prompt_request_id=source.id,
            workflow_run_id=workflow.id,
            workflow_prompt_request_id=source.id,
            agent_run_id=creator.id,
            agent_workflow_run_id=workflow.id,
        )
        or baseline.agent_version != creator.agent_version
        or baseline.instruction_version != creator.instruction_version
        or baseline.metadata != observed_metadata(creator)
    ):
        raise PersistenceError("conflict")
    candidate = CandidateStorage.from_candidate(baseline)
    result = OperationResultStorage.from_json_value(creator.result_data)
    critic_result = OperationResultStorage.from_json_value(critic.result_data)
    parameters = OperationParameters.from_json_value(critic.parameters)
    if (
        result.kind != "candidate"
        or result.candidate != candidate
        or critic_result.kind != "evaluation"
        or critic_result.evaluation_id != evaluation.id
        or parameters.candidate_agent_run_id != creator.id
        or parameters.input_fingerprint != evaluation.input_fingerprint
        or version.canonical_prompt != candidate.canonical_prompt.model_dump(mode="json")
        or version.rendered_prompt != candidate.rendered_prompt
        or version.target_mode != candidate.target_mode.value
        or version.renderer_name != candidate.renderer_name
        or version.renderer_version != candidate.renderer_version
    ):
        raise PersistenceError("invalid")
    assessment = AssessmentStorage.from_json_value(evaluation.assessment)
    critic_output = CriticResult.model_validate(
        dict(
            output=assessment.assessment,
            input_fingerprint=evaluation.input_fingerprint,
            evaluated_target_mode=candidate.target_mode,
            evaluated_renderer_name=evaluation.evaluated_renderer_name,
            evaluated_renderer_version=evaluation.evaluated_renderer_version,
            agent_version=critic.agent_version,
            instruction_version=critic.instruction_version,
            rubric_version=evaluation.rubric_version,
            metadata=observed_metadata(critic),
        )
    )
    evaluated = EvaluatedCandidate.model_validate(dict(candidate=baseline, critic=critic_output))
    score = evaluated.scoring
    if (
        evaluation.evaluated_target_mode != candidate.target_mode.value
        or evaluation.assessment_schema_version != assessment.schema_version
        or evaluation.scoring_version != score.scoring_version
        or evaluation.quality_status != score.status
        or evaluation.total_score != score.total_score
        or evaluation.passed != score.passed
        or GateStorage.from_json_value(evaluation.gate_results).gates != score.gates
    ):
        raise PersistenceError("invalid")
    payload = make_revision_input(
        evaluated,
        next_evaluation_round=binding.evaluation_round,
        protected_spans=binding.protected_spans,
        context=context,
    )
    if revision_binding(payload, evaluation.id) != binding:
        raise PersistenceError("conflict")
    return payload
