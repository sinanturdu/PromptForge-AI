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
from app.models import AgentRun, WorkflowRun
from app.modules.prompts.architect_contracts import (
    ArchitectHandoff,
    ArchitectRequestSnapshot,
    CandidateReady,
    ClarificationRequired,
    CriticInput,
    critic_input_fingerprint,
)
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimizer_contracts import OptimizationOperation, failure_reason
from app.modules.prompts.persistence_contracts import (
    STORAGE_VERSION,
    CallMetadataStorage,
    CandidateStorage,
    OperationBindingStorage,
    OperationResultStorage,
    RequestPreferenceSnapshot,
    SafeErrorCode,
    TerminalDetailsStorage,
    request_content_digest,
)
from app.modules.prompts.schemas import PromptRequestRead
from app.modules.prompts.workflow_persistence_contracts import (
    OperationIdentity,
    OperationParameters,
    OperationReceipt,
    OperationState,
    PersistenceError,
    WorkflowConfiguration,
    WorkflowCreation,
    WorkflowFence,
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
from app.modules.prompts.workflow_repository import AgentRunRepository, WorkflowRunRepository

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
