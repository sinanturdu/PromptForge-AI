"""Opt-in durable WorkflowRun/AgentRun actions; no provider awaits or execution loop."""

import hashlib
import sqlite3
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.revision_schemas import (
    RevisedCandidate,
    RevisionClarification,
    RevisionInput,
    RevisionResult,
    UnchangedBaseline,
)
from app.db.base import utc_now
from app.db.sqlite_runtime import SQLiteRuntimeSafetyError
from app.db.write_transactions import write_transaction
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import CallMetadata
from app.models import AgentRun, Evaluation, PromptVersion, WorkflowRun
from app.modules.prompts.architect_contracts import (
    ArchitectHandoff,
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    CriticInput,
    critic_input_fingerprint,
)
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimization_progress import check_candidate_progress
from app.modules.prompts.optimizer_contracts import OptimizationOperation, failure_reason
from app.modules.prompts.persistence_contracts import (
    STORAGE_VERSION,
    CallMetadataStorage,
    CandidateStorage,
    EvaluationStorage,
    OperationBindingStorage,
    OperationResultStorage,
    RequestPreferenceSnapshot,
    SafeErrorCode,
    TerminalDetailsStorage,
    request_content_digest,
)
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_history import (
    DurableHistory,
    load_history,
    staged_candidate,
    staged_revision,
    stopping_reason,
    validate_best,
)
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    EvaluationReceipt,
    FinalizationReceipt,
    OperationIdentity,
    OperationParameters,
    OperationReceipt,
    OperationState,
    PersistenceError,
    WorkflowConfiguration,
    WorkflowCreation,
    WorkflowFence,
    WorkflowFinalization,
    WorkflowState,
    validated,
)
from app.modules.prompts.workflow_provenance import (
    bound_source,
    checked_revision,
    observed_metadata,
    operation_binding,
    revision_binding,
)
from app.modules.prompts.workflow_repository import (
    AgentRunRepository,
    EvaluationRepository,
    WorkflowRunRepository,
)

_PHASE = {"create": "GENERATING", "revise": "REVISING", "evaluate": "EVALUATING"}
_OPERATION: dict[str, OptimizationOperation] = {
    "create": "architect_create",
    "revise": "architect_revise",
    "evaluate": "critic_evaluate",
}


def _action[**P, T](
    method: Callable[P, Coroutine[Any, Any, T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Translate after transaction exit, outside contextlib's exception handling."""

    @wraps(method)
    async def call(*args: P.args, **kwargs: P.kwargs) -> T:
        failure = None
        try:
            return await method(*args, **kwargs)
        except PersistenceError as error:
            failure = PersistenceError(error.code)
        except IntegrityError as error:
            unique = (
                getattr(error.orig, "sqlite_errorcode", None)
                in {sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY}
                or getattr(error.orig, "sqlstate", None) == "23505"
                or getattr(error.orig, "pgcode", None) == "23505"
            )
            failure = PersistenceError("conflict" if unique else "unavailable")
        except (SQLAlchemyError, SQLiteRuntimeSafetyError):
            failure = PersistenceError("unavailable")
        except (ValidationError, LLMError, ValueError, TypeError):
            failure = PersistenceError("invalid")
        raise failure

    return call


def _workflow(row: WorkflowRun) -> WorkflowState:
    return WorkflowState(
        id=row.id,
        prompt_request_id=row.prompt_request_id,
        status=row.status,
        state_revision=row.state_revision,
        current_round=row.current_round,
        completed_rounds=row.completed_rounds,
        max_evaluation_rounds=row.max_evaluation_rounds,
        execution_token=row.execution_token,
        lease_expires_at=row.lease_expires_at,
    )


def _receipt(row: WorkflowRun, agent: AgentRun, replayed: bool) -> OperationReceipt:
    result = (
        None
        if agent.result_data is None
        else OperationResultStorage.from_json_value(agent.result_data)
    )
    if (
        (agent.status in {"COMPLETED", "FAILED"}) != (result is not None)
        or (result is not None and (agent.result_kind != result.kind or agent.finished_at is None))
        or (result is not None and (agent.status == "FAILED") != (result.kind == "failed"))
    ):
        raise PersistenceError("invalid")
    return OperationReceipt(
        workflow=_workflow(row),
        replayed=replayed,
        agent=OperationState.model_validate(
            dict(
                id=agent.id,
                workflow_run_id=agent.workflow_run_id,
                operation=agent.operation,
                status=agent.status,
                evaluation_round=agent.evaluation_round,
                result=result,
                metadata=None
                if agent.call_metadata is None
                else CallMetadataStorage.from_json_value(agent.call_metadata),
            )
        ),
    )


class WorkflowPersistenceService:
    """Caller owns the dedicated engine; each action owns a fresh transaction/session."""

    def __init__(self, engine: AsyncEngine, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._engine = engine
        self._clock = clock

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise PersistenceError("invalid")
        return value.astimezone(UTC)

    @_action
    async def create_workflow(
        self,
        prompt_request_id: UUID,
        configuration: WorkflowConfiguration,
        *,
        client_idempotency_key: str | None = None,
    ) -> WorkflowCreation:
        config = validated(WorkflowConfiguration, configuration)
        if not isinstance(prompt_request_id, UUID) or (
            client_idempotency_key is not None
            and (
                not isinstance(client_idempotency_key, str)
                or not client_idempotency_key.strip()
                or len(client_idempotency_key) > 256
            )
        ):
            raise PersistenceError("invalid")
        digest = (
            None
            if client_idempotency_key is None
            else hashlib.sha256(client_idempotency_key.encode("utf-8")).hexdigest()
        )
        async with write_transaction(self._engine) as session:
            repository = WorkflowRunRepository(session)
            original = await repository.get_source(prompt_request_id)
            if original is None:
                raise PersistenceError("not_found")
            source = ArchitectRequestSnapshot.model_validate(
                PromptRequestRead.model_validate(original).model_dump()
            )
            if len(set(config.protected_spans)) != len(config.protected_spans):
                raise PersistenceError("invalid")
            for span in config.protected_spans:
                span.text(source)
            preferences = RequestPreferenceSnapshot.from_request(source)
            content_digest = request_content_digest(source)
            if digest is not None:
                previous = await repository.get_idempotent(prompt_request_id, digest)
                if previous is not None:
                    old_source, old_config = await bound_source(repository, previous)
                    if old_source != source or old_config != config:
                        raise PersistenceError("conflict")
                    return WorkflowCreation(workflow=_workflow(previous), replayed=True)
            row = WorkflowRun(
                prompt_request_id=prompt_request_id,
                status="PENDING",
                max_evaluation_rounds=config.max_evaluation_rounds,
                current_round=0,
                completed_rounds=0,
                config_snapshot=config.model_dump(mode="json"),
                request_snapshot=preferences.model_dump(mode="json"),
                request_content_digest=content_digest,
                client_idempotency_key_digest=digest,
                state_revision=0,
                created_at=self._now(),
            )
            await repository.insert(row)
            return WorkflowCreation(workflow=_workflow(row), replayed=False)

    @_action
    async def claim_workflow(
        self,
        prompt_request_id: UUID,
        workflow_run_id: UUID,
        *,
        expected_revision: int,
        lease_seconds: int,
    ) -> WorkflowState:
        if (
            not isinstance(prompt_request_id, UUID)
            or not isinstance(workflow_run_id, UUID)
            or type(expected_revision) is not int
            or expected_revision < 0
            or type(lease_seconds) is not int
            or not 1 <= lease_seconds <= 900
        ):
            raise PersistenceError("invalid")
        async with write_transaction(self._engine) as session:
            repository = WorkflowRunRepository(session)
            row = await repository.get_for_request(workflow_run_id, prompt_request_id)
            if row is None:
                raise PersistenceError("not_found")
            await bound_source(repository, row)
            now = self._now()
            if not await repository.claim(
                row, expected_revision, uuid4(), now, now + timedelta(seconds=lease_seconds)
            ):
                raise PersistenceError("conflict")
            await repository.refresh(row)
            return _workflow(row)

    async def _owned(
        self,
        repository: WorkflowRunRepository,
        fence: WorkflowFence,
    ) -> tuple[WorkflowRun, ArchitectRequestSnapshot, WorkflowConfiguration]:
        row = await repository.get_for_request(fence.workflow_run_id, fence.prompt_request_id)
        if row is None:
            raise PersistenceError("not_found")
        if (
            row.execution_token != fence.execution_token
            or row.lease_expires_at is None
            or row.lease_expires_at <= self._now()
        ):
            raise PersistenceError("conflict")
        source, config = await bound_source(repository, row)
        return row, source, config

    async def _round(
        self,
        repository: WorkflowRunRepository,
        row: WorkflowRun,
        fence: WorkflowFence,
        binding: OperationBindingStorage,
    ) -> None:
        history = await repository.evaluations(row.id)
        if (
            row.status != _PHASE[binding.operation]
            or row.state_revision != fence.state_revision
            or row.completed_rounds != fence.completed_rounds
            or row.current_round != row.completed_rounds + 1
            or binding.evaluation_round != row.current_round
            or row.current_round > row.max_evaluation_rounds
            or [value.evaluation_round for value in history]
            != list(range(1, row.completed_rounds + 1))
        ):
            raise PersistenceError("conflict")

    async def _parameters(
        self,
        agents: AgentRunRepository,
        row: WorkflowRun,
        source: ArchitectRequestSnapshot,
        binding: OperationBindingStorage,
        identity: OperationIdentity,
    ) -> OperationParameters:
        if binding.operation != "evaluate":
            return OperationParameters(schema_version=STORAGE_VERSION, settings=identity.settings)
        creators = [
            value
            for value in await agents.for_round(row.id, binding.evaluation_round)
            if value.operation in {"create", "revise"}
        ]
        if (
            len(creators) != 1
            or creators[0].status != "COMPLETED"
            or creators[0].result_kind != "candidate"
        ):
            raise PersistenceError("conflict")
        creator = creators[0]
        operation_binding(creator)
        observed_metadata(creator)
        result = OperationResultStorage.from_json_value(creator.result_data)
        if result.candidate is None:
            raise PersistenceError("invalid")
        self._candidate_source(result.candidate, source)
        blind = CriticInput(
            original_request=source,
            rendered_prompt=result.candidate.rendered_prompt,
            target_mode=source.target_mode,
            renderer_name=result.candidate.renderer_name,
            renderer_version=result.candidate.renderer_version,
        )
        return OperationParameters(
            schema_version=STORAGE_VERSION,
            settings=identity.settings,
            candidate_agent_run_id=creator.id,
            input_fingerprint=critic_input_fingerprint(blind),
        )

    @staticmethod
    def _candidate_source(candidate: CandidateStorage, source: ArchitectRequestSnapshot) -> None:
        if (
            candidate.prompt_request_id != source.id
            or candidate.target_mode != source.target_mode
            or (
                candidate.decision == "preserve"
                and (
                    candidate.rendered_prompt != source.raw_prompt
                    or candidate.canonical_prompt != CanonicalPrompt.from_task(source.raw_prompt)
                )
            )
        ):
            raise PersistenceError("conflict")

    @_action
    async def reserve_operation(
        self,
        fence: WorkflowFence,
        identity: OperationIdentity,
        binding: OperationBindingStorage,
        *,
        revision_input: RevisionInput | None = None,
    ) -> OperationReceipt:
        fence = validated(WorkflowFence, fence)
        identity = validated(OperationIdentity, identity)
        binding = validated(OperationBindingStorage, binding)
        async with write_transaction(self._engine) as session:
            workflows, agents = WorkflowRunRepository(session), AgentRunRepository(session)
            row, source, config = await self._owned(workflows, fence)
            expected_name = (
                "PromptCriticAgent" if binding.operation == "evaluate" else "PromptArchitectAgent"
            )
            if identity.agent_name != expected_name:
                raise PersistenceError("invalid")
            parameters = await self._parameters(agents, row, source, binding, identity)
            if binding.operation == "revise":
                if revision_input is None or binding.baseline_evaluation_id is None:
                    raise PersistenceError("invalid")
                revision = validated(RevisionInput, revision_input)
                if (
                    revision_binding(revision, binding.baseline_evaluation_id) != binding
                    or revision.protected_spans != config.protected_spans
                ):
                    raise PersistenceError("conflict")
                actual = await checked_revision(
                    workflows, agents, row, source, binding, revision.baseline, revision.context
                )
                if actual != revision:
                    raise PersistenceError("conflict")
            elif revision_input is not None or binding.protected_spans:
                raise PersistenceError("invalid")
            previous = await agents.get_operation(row.id, identity.operation_key)
            if previous is not None:
                if (
                    previous.agent_name != identity.agent_name
                    or previous.agent_version != identity.agent_version
                    or previous.instruction_version != identity.instruction_version
                    or previous.provider != identity.provider
                    or previous.model
                    != (None if identity.settings is None else identity.settings.model)
                    or operation_binding(previous) != binding
                    or OperationParameters.from_json_value(previous.parameters) != parameters
                ):
                    raise PersistenceError("conflict")
                return _receipt(row, previous, True)
            await self._round(workflows, row, fence, binding)
            existing = await agents.for_round(row.id, binding.evaluation_round)
            if any(
                (value.operation == "evaluate") == (binding.operation == "evaluate")
                for value in existing
            ):
                raise PersistenceError("conflict")
            if (
                binding.revision_attempt_key is not None
                and await agents.get_revision(row.id, binding.revision_attempt_key) is not None
            ):
                raise PersistenceError("conflict")
            if not await workflows.advance(fence, row.status, row.status, self._now()):
                raise PersistenceError("conflict")
            agent = AgentRun(
                workflow_run_id=row.id,
                operation_key=identity.operation_key,
                agent_name=identity.agent_name,
                agent_version=identity.agent_version,
                instruction_version=identity.instruction_version,
                operation=binding.operation,
                evaluation_round=binding.evaluation_round,
                status="RUNNING",
                started_at=self._now(),
                provider=identity.provider,
                model=None if identity.settings is None else identity.settings.model,
                parameters=parameters.model_dump(mode="json"),
                binding_data=binding.model_dump(mode="json"),
                revision_attempt_key=binding.revision_attempt_key,
                baseline_evaluation_id=binding.baseline_evaluation_id,
                feedback_projection_digest=binding.feedback_projection_digest,
            )
            await agents.insert(agent)
            await workflows.refresh(row)
            return _receipt(row, agent, False)

    @_action
    async def complete_architect(
        self,
        fence: WorkflowFence,
        agent_run_id: UUID,
        outcome: ArchitectHandoff | RevisionResult,
    ) -> OperationReceipt:
        fence = validated(WorkflowFence, fence)
        if not isinstance(agent_run_id, UUID):
            raise PersistenceError("invalid")
        async with write_transaction(self._engine) as session:
            workflows, agents = WorkflowRunRepository(session), AgentRunRepository(session)
            row, source, config = await self._owned(workflows, fence)
            agent = await agents.get_for_workflow(agent_run_id, row.id)
            if agent is None:
                raise PersistenceError("not_found")
            binding = operation_binding(agent)
            if agent.operation != binding.operation or binding.operation == "evaluate":
                raise PersistenceError("conflict")
            value: ArchitectHandoff | RevisionResult
            if binding.operation == "create" and isinstance(
                outcome, (CandidateReady, ClarificationRequired)
            ):
                value = validated(type(outcome), outcome)
                if value.original_request != source:
                    raise PersistenceError("conflict")
                result = OperationResultStorage(
                    schema_version=STORAGE_VERSION,
                    kind="candidate" if isinstance(value, CandidateReady) else "clarification",
                    candidate=CandidateStorage.from_candidate(value)
                    if isinstance(value, CandidateReady)
                    else None,
                    questions=value.questions if isinstance(value, ClarificationRequired) else (),
                )
            elif binding.operation == "revise" and isinstance(
                outcome, (RevisedCandidate, UnchangedBaseline, RevisionClarification)
            ):
                revised = validated(type(outcome), outcome)
                payload = await checked_revision(
                    workflows, agents, row, source, binding, revised.baseline, revised.context
                )
                if revised.eligible_issue_keys != tuple(
                    finding.issue_key for finding in payload.feedback
                ):
                    raise PersistenceError("conflict")
                result = OperationResultStorage.from_revision(revised)
                value = revised
            else:
                raise PersistenceError("invalid")
            context = value.context
            if (
                context.agent_run_id != agent.id
                or context.agent_workflow_run_id != row.id
                or context.workflow_run_id != row.id
                or context.prompt_request_id != source.id
                or value.agent_version != agent.agent_version
                or value.instruction_version != agent.instruction_version
            ):
                raise PersistenceError("conflict")
            if result.candidate is not None:
                self._candidate_source(result.candidate, source)
                if any(
                    span.text(source) not in result.candidate.rendered_prompt
                    for span in config.protected_spans
                ):
                    raise PersistenceError("conflict")
            metadata = CallMetadataStorage.from_metadata(value.metadata)
            return await self._finish(
                workflows, agents, row, agent, fence, binding, result, metadata
            )

    @_action
    async def fail_operation(
        self,
        fence: WorkflowFence,
        agent_run_id: UUID,
        *,
        error_code: SafeErrorCode,
        metadata: CallMetadata | None = None,
    ) -> OperationReceipt:
        fence = validated(WorkflowFence, fence)
        if not isinstance(agent_run_id, UUID):
            raise PersistenceError("invalid")
        async with write_transaction(self._engine) as session:
            workflows, agents = WorkflowRunRepository(session), AgentRunRepository(session)
            row, _source, _config = await self._owned(workflows, fence)
            agent = await agents.get_for_workflow(agent_run_id, row.id)
            if agent is None:
                raise PersistenceError("not_found")
            binding = operation_binding(agent)
            if agent.operation != binding.operation:
                raise PersistenceError("invalid")
            result = OperationResultStorage(
                schema_version=STORAGE_VERSION, kind="failed", error_code=error_code
            )
            observed = (
                None
                if metadata is None
                else CallMetadataStorage.from_metadata(validated(CallMetadata, metadata))
            )
            return await self._finish(
                workflows, agents, row, agent, fence, binding, result, observed
            )

    async def _finish(
        self,
        workflows: WorkflowRunRepository,
        agents: AgentRunRepository,
        row: WorkflowRun,
        agent: AgentRun,
        fence: WorkflowFence,
        binding: OperationBindingStorage,
        result: OperationResultStorage,
        metadata: CallMetadataStorage | None,
    ) -> OperationReceipt:
        data = result.model_dump(mode="json")
        observed = None if metadata is None else metadata.model_dump(mode="json")
        if (
            metadata is not None
            and metadata.metadata is not None
            and (
                (
                    agent.provider is not None
                    and metadata.metadata.provider is not None
                    and agent.provider != metadata.metadata.provider
                )
                or (
                    agent.model is not None
                    and metadata.metadata.requested_model is not None
                    and agent.model != metadata.metadata.requested_model
                )
            )
        ):
            raise PersistenceError("conflict")
        if agent.status in {"COMPLETED", "FAILED"}:
            if agent.result_data != data or agent.call_metadata != observed:
                raise PersistenceError("conflict")
            return _receipt(row, agent, True)
        if agent.status != "RUNNING":
            raise PersistenceError("conflict")
        await self._round(workflows, row, fence, binding)
        phase = row.status
        terminal = None
        if result.kind == "failed":
            phase = "FAILED"
            code = result.error_code
            reason = failure_reason(code) if isinstance(code, LLMErrorCode) else code
            terminal = TerminalDetailsStorage.model_validate(
                dict(
                    schema_version=STORAGE_VERSION,
                    execution_status=phase,
                    termination_reason=reason,
                    failed_operation=_OPERATION[binding.operation],
                    error_code=code,
                )
            ).model_dump(mode="json")
        elif result.kind == "clarification":
            phase = "CLARIFICATION_REQUIRED"
            terminal = TerminalDetailsStorage(
                schema_version=STORAGE_VERSION,
                execution_status="CLARIFICATION_REQUIRED",
                termination_reason="clarification_required",
                questions=result.questions,
            ).model_dump(mode="json")
        elif result.kind == "candidate":
            phase = "EVALUATING"
        elif result.kind != "unchanged":
            raise PersistenceError("invalid")
        now = self._now()
        if not await workflows.advance(fence, row.status, phase, now, terminal):
            raise PersistenceError("conflict")
        if not await agents.finish(agent, data, observed, now):
            raise PersistenceError("conflict")
        await workflows.refresh(row)
        await agents.refresh(agent)
        return _receipt(row, agent, False)

    async def _completion_scope(
        self,
        workflows: WorkflowRunRepository,
        fence: WorkflowFence,
    ) -> tuple[WorkflowRun, ArchitectRequestSnapshot, WorkflowConfiguration]:
        """A matching historical token permits exact reconciliation, not a new write."""
        row = await workflows.get_for_request(fence.workflow_run_id, fence.prompt_request_id)
        if row is None:
            raise PersistenceError("not_found")
        if row.execution_token != fence.execution_token:
            raise PersistenceError("conflict")
        source, config = await bound_source(workflows, row)
        return row, source, config

    @staticmethod
    def _evaluation_receipt(
        row: WorkflowRun,
        durable: DurableHistory,
        record: Evaluation,
        replayed: bool,
    ) -> EvaluationReceipt:
        best = durable.best()
        if best is None:
            raise PersistenceError("invalid")
        return EvaluationReceipt(
            workflow=_workflow(row),
            prompt_version_id=record.prompt_version_id,
            evaluation_id=record.id,
            evaluation_round=record.evaluation_round,
            best_version_id=best[1].id,
            best_evaluation_id=best[0].id,
            quality_status=best[2].scoring.status,
            replayed=replayed,
        )

    @_action
    async def complete_evaluation(self, command: EvaluationCompletion) -> EvaluationReceipt:
        command = validated(EvaluationCompletion, command)
        async with write_transaction(self._engine) as session:
            workflows, agents = WorkflowRunRepository(session), AgentRunRepository(session)
            evaluations = EvaluationRepository(session)
            row, source, config = await self._completion_scope(workflows, command.fence)
            durable = await load_history(workflows, agents, row, source, config)
            validate_best(row, durable)
            creator = await agents.get_for_workflow(command.architect_agent_run_id, row.id)
            critic = await agents.get_for_workflow(command.critic_agent_run_id, row.id)
            if creator is None or critic is None:
                raise PersistenceError("not_found")
            binding = operation_binding(critic)
            parameters = OperationParameters.from_json_value(critic.parameters)
            candidate = staged_candidate(row, source, creator)
            assessed = command.evaluated
            if (
                binding.operation != "evaluate"
                or binding.evaluation_round != command.evaluation_round
                or critic.operation_key != command.critic_operation_key
                or creator.evaluation_round != command.evaluation_round
                or parameters.candidate_agent_run_id != creator.id
                or parameters.input_fingerprint != assessed.critic.input_fingerprint
                or candidate != assessed.candidate
                or critic.agent_version != assessed.critic.agent_version
                or critic.instruction_version != assessed.critic.instruction_version
                or (
                    critic.provider is not None
                    and critic.provider != assessed.critic.metadata.provider
                )
                or (
                    critic.model is not None
                    and critic.model != assessed.critic.metadata.requested_model
                )
                or any(
                    span.text(source) not in candidate.rendered_prompt
                    for span in config.protected_spans
                )
            ):
                raise PersistenceError("conflict")
            previous = await evaluations.for_critic(row.id, critic.id)
            if previous is not None:
                if durable.history is None or critic.status != "COMPLETED":
                    raise PersistenceError("conflict")
                stored = durable.history.rounds[previous.evaluation_round - 1].evaluated
                if previous.evaluation_round != command.evaluation_round or stored != assessed:
                    raise PersistenceError("conflict")
                return self._evaluation_receipt(row, durable, previous, True)
            if (
                critic.status != "RUNNING"
                or row.lease_expires_at is None
                or row.lease_expires_at <= self._now()
            ):
                raise PersistenceError("conflict")
            await self._round(workflows, row, command.fence, binding)
            parent_id = None
            creator_binding = operation_binding(creator)
            if creator_binding.operation == "revise":
                revised = staged_revision(row, source, creator, durable, config)
                if (
                    durable.history is None
                    or check_candidate_progress(durable.history, revised).action != "evaluate"
                ):
                    raise PersistenceError("conflict")
                parent_record = next(
                    (
                        value
                        for value in durable.evaluations
                        if value.id == creator_binding.baseline_evaluation_id
                    ),
                    None,
                )
                if parent_record is None:
                    raise PersistenceError("conflict")
                parent_id = parent_record.prompt_version_id
            elif (
                creator_binding.operation != "create"
                or command.evaluation_round != 1
                or durable.history is not None
            ):
                raise PersistenceError("conflict")
            version = PromptVersion(
                prompt_request_id=source.id,
                workflow_run_id=row.id,
                agent_run_id=creator.id,
                parent_version_id=parent_id,
                version_number=await evaluations.next_version_number(source.id),
                evaluation_round=command.evaluation_round,
                source="engineer_create"
                if creator_binding.operation == "create"
                else "engineer_revise",
                target_mode=source.target_mode.value,
                renderer_name=candidate.renderer_name,
                renderer_version=candidate.renderer_version,
                canonical_prompt=candidate.canonical_prompt.model_dump(mode="json"),
                rendered_prompt=candidate.rendered_prompt,
                change_summary=list(candidate.changes),
                created_at=self._now(),
            )
            await evaluations.insert_version(version)
            now = self._now()
            record = Evaluation(
                **EvaluationStorage.from_evaluated(
                    assessed,
                    workflow_run_id=row.id,
                    prompt_version_id=version.id,
                    critic_agent_run_id=critic.id,
                    evaluation_round=command.evaluation_round,
                    completed_at=now,
                ).record_values(),
                created_at=now,
            )
            await evaluations.insert_evaluation(record)
            result = OperationResultStorage(
                schema_version=STORAGE_VERSION,
                kind="evaluation",
                evaluation_id=record.id,
            )
            if not await agents.finish(
                critic,
                result.model_dump(mode="json"),
                CallMetadataStorage.from_metadata(assessed.critic.metadata).model_dump(mode="json"),
                now,
            ):
                raise PersistenceError("conflict")
            await agents.refresh(critic)
            complete = await load_history(
                workflows,
                agents,
                row,
                source,
                config,
                count=row.completed_rounds + 1,
            )
            best = complete.best()
            if best is None:
                raise PersistenceError("invalid")
            stop = await stopping_reason(complete, config, agents, row)
            count = len(complete.evaluations)
            if not await workflows.record_progress(
                command.fence,
                "EVALUATING",
                self._now(),
                completed=count,
                current=count if stop is not None else count + 1,
                next_phase="EVALUATING" if stop is not None else "REVISING",
                best_version_id=best[1].id,
                score=best[2].scoring.total_score,
                qualified=best[2].scoring.passed,
            ):
                raise PersistenceError("conflict")
            await workflows.refresh(row)
            return self._evaluation_receipt(row, complete, record, False)

    async def _validate_terminal(
        self,
        command: WorkflowFinalization,
        row: WorkflowRun,
        source: ArchitectRequestSnapshot,
        config: WorkflowConfiguration,
        durable: DurableHistory,
        agents: AgentRunRepository,
        workflows: WorkflowRunRepository,
    ) -> AgentRun | None:
        details = command.details
        operations = await agents.for_workflow(row.id)
        running = [value for value in operations if value.status == "RUNNING"]
        agent = None
        if command.agent_run_id is not None:
            agent = await agents.get_for_workflow(command.agent_run_id, row.id)
            if agent is None:
                raise PersistenceError("not_found")
            binding = operation_binding(agent)
            if binding.evaluation_round != row.current_round:
                raise PersistenceError("conflict")
        if details.execution_status == "FAILED":
            if any(value.id != command.agent_run_id for value in running):
                raise PersistenceError("conflict")
            if agent is not None:
                if details.failed_operation != _OPERATION[agent.operation]:
                    raise PersistenceError("conflict")
                if agent.status == "FAILED":
                    result = OperationResultStorage.from_json_value(agent.result_data)
                    if result.kind != "failed" or result.error_code != details.error_code:
                        raise PersistenceError("conflict")
                elif agent.status == "RUNNING":
                    await self._round(workflows, row, command.fence, operation_binding(agent))
                else:
                    raise PersistenceError("conflict")
            return agent
        if running:
            raise PersistenceError("conflict")
        if details.execution_status == "CLARIFICATION_REQUIRED":
            if agent is None or agent.status != "COMPLETED" or agent.result_kind != "clarification":
                raise PersistenceError("conflict")
            result = OperationResultStorage.from_json_value(agent.result_data)
            if result.questions != details.questions:
                raise PersistenceError("conflict")
            if agent.operation == "create":
                if durable.history is not None:
                    raise PersistenceError("conflict")
                ClarificationRequired.model_validate(
                    dict(
                        original_request=source,
                        context=ArchitectRunContext(
                            prompt_request_id=source.id,
                            workflow_run_id=row.id,
                            workflow_prompt_request_id=source.id,
                            agent_run_id=agent.id,
                            agent_workflow_run_id=row.id,
                        ),
                        metadata=observed_metadata(agent),
                        agent_version=agent.agent_version,
                        instruction_version=agent.instruction_version,
                        questions=result.questions,
                    )
                )
            else:
                proposed = staged_revision(row, source, agent, durable, config)
                if (
                    durable.history is None
                    or check_candidate_progress(durable.history, proposed).reason
                    != "clarification_required"
                ):
                    raise PersistenceError("conflict")
            return agent
        if durable.history is None:
            raise PersistenceError("conflict")
        if agent is not None:
            proposed = staged_revision(row, source, agent, durable, config)
            decision = check_candidate_progress(durable.history, proposed)
            if decision.action != "stop" or decision.reason != details.termination_reason:
                raise PersistenceError("conflict")
        elif (
            row.current_round > row.completed_rounds
            or await stopping_reason(durable, config, agents, row) != details.termination_reason
        ):
            raise PersistenceError("conflict")
        return agent

    @_action
    async def finalize_workflow(self, command: WorkflowFinalization) -> FinalizationReceipt:
        command = validated(WorkflowFinalization, command)
        async with write_transaction(self._engine) as session:
            workflows, agents = WorkflowRunRepository(session), AgentRunRepository(session)
            row, source, config = await self._completion_scope(workflows, command.fence)
            durable = await load_history(workflows, agents, row, source, config)
            validate_best(row, durable)
            agent = await self._validate_terminal(
                command, row, source, config, durable, agents, workflows
            )
            details = command.details.model_dump(mode="json")
            replayed = row.status in {"COMPLETED", "FAILED", "CLARIFICATION_REQUIRED"}
            if replayed:
                if (
                    row.status != command.details.execution_status
                    or row.finished_at is None
                    or row.stop_reason != command.details.termination_reason
                    or row.error_code != command.details.error_code
                    or TerminalDetailsStorage.from_json_value(row.terminal_details)
                    != command.details
                ):
                    raise PersistenceError("conflict")
            else:
                if (
                    row.status not in {"GENERATING", "REVISING", "EVALUATING"}
                    or row.lease_expires_at is None
                    or row.lease_expires_at <= self._now()
                    or row.state_revision != command.fence.state_revision
                    or row.completed_rounds != command.fence.completed_rounds
                ):
                    raise PersistenceError("conflict")
                best = durable.best()
                now = self._now()
                if (
                    command.details.execution_status == "FAILED"
                    and agent is not None
                    and agent.status == "RUNNING"
                ):
                    result = OperationResultStorage(
                        schema_version=STORAGE_VERSION,
                        kind="failed",
                        error_code=command.details.error_code,
                    )
                    if not await agents.finish(agent, result.model_dump(mode="json"), None, now):
                        raise PersistenceError("conflict")
                if not await workflows.record_progress(
                    command.fence,
                    row.status,
                    now,
                    completed=len(durable.evaluations),
                    current=row.current_round,
                    next_phase=command.details.execution_status,
                    best_version_id=None if best is None else best[1].id,
                    score=None if best is None else best[2].scoring.total_score,
                    qualified=None if best is None else best[2].scoring.passed,
                    terminal=details,
                ):
                    raise PersistenceError("conflict")
                await workflows.refresh(row)
            best = durable.best()
            return FinalizationReceipt(
                workflow=_workflow(row),
                best_version_id=None if best is None else best[1].id,
                best_evaluation_id=None if best is None else best[0].id,
                quality_status=None if best is None else best[2].scoring.status,
                terminal=command.details,
                replayed=replayed,
            )
