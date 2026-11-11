"""Explicit claimed execution through the existing M5D loop and M5E services."""

from collections.abc import Awaitable, Callable
from uuid import UUID

from pydantic import Field, ValidationError

from app.agents.architect_instructions import INSTRUCTION_VERSION as CREATE_INSTRUCTION
from app.agents.critic_instructions import INSTRUCTION_VERSION as CRITIC_INSTRUCTION
from app.agents.critic_schemas import CriticResult
from app.agents.prompt_architect import AGENT_VERSION as ARCHITECT_VERSION
from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import AGENT_VERSION as CRITIC_VERSION
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_instructions import INSTRUCTION_VERSION as REVISE_INSTRUCTION
from app.agents.revision_schemas import RevisionInput, RevisionResult
from app.llm.errors import LLMError
from app.llm.schemas import CallMetadata, LLMModel, ModelSettings
from app.modules.prompts.architect_contracts import (
    ArchitectHandoff,
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    make_critic_input,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate, SinglePassOutcome
from app.modules.prompts.architect_integration import create_architect_handoff
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.optimization_contracts import EvaluationHistory
from app.modules.prompts.optimizer import optimize_prompt
from app.modules.prompts.optimizer_contracts import OptimizationOperation, OptimizationOutcome
from app.modules.prompts.persistence_contracts import (
    STORAGE_VERSION,
    OperationBindingStorage,
    TerminalDetailsStorage,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.workflow_persistence import WorkflowPersistenceService
from app.modules.prompts.workflow_persistence_contracts import (
    EvaluationCompletion,
    FinalizationReceipt,
    OperationIdentity,
    OperationReceipt,
    PersistedEvaluationReference,
    PersistenceError,
    WorkflowExecutionSnapshot,
    WorkflowFinalization,
    WorkflowState,
    validated,
)
from app.modules.prompts.workflow_provenance import revision_binding


class PersistentExecutionIdentity(LLMModel):
    """Known requested configuration, not invented observed provider telemetry."""

    architect_provider: str | None = Field(default=None, repr=False, max_length=64)
    architect_settings: ModelSettings | None = Field(default=None, repr=False)
    critic_provider: str | None = Field(default=None, repr=False, max_length=64)
    critic_settings: ModelSettings | None = Field(default=None, repr=False)


class PersistentOptimizationResult(LLMModel):
    outcome: OptimizationOutcome = Field(repr=False)
    terminal: FinalizationReceipt = Field(repr=False)
    evaluations: tuple[PersistedEvaluationReference, ...] = Field(max_length=3)


class _PersistentExecution:
    """One invocation's I/O state. No ORM/session access, policy loop or resumption."""

    def __init__(
        self,
        persistence: WorkflowPersistenceService,
        snapshot: WorkflowExecutionSnapshot,
        identity: PersistentExecutionIdentity,
        lease_seconds: int,
        on_state: Callable[[WorkflowState], None] | None = None,
    ) -> None:
        self.persistence, self.snapshot, self.identity = persistence, snapshot, identity
        self.state = snapshot.workflow
        self.lease_seconds = lease_seconds
        self.active: OperationReceipt | None = None
        self.last_architect_id: UUID | None = None
        self.failure_metadata: CallMetadata | None = None
        self.uncertain = False
        self._on_state = on_state

    def _set_state(self, state: WorkflowState) -> None:
        self.state = state
        if self._on_state is not None:
            self._on_state(state)

    async def _capture[T](self, operation: Awaitable[T]) -> T:
        try:
            return await operation
        except LLMError as error:
            self.failure_metadata = error.metadata.call
            raise

    async def _complete[T](self, operation: Callable[[], Awaitable[T]]) -> T:
        """One bounded persistence-only reconciliation; never replays dispatch."""
        self.uncertain = True
        try:
            result = await operation()
        except PersistenceError as error:
            if error.code != "unavailable":
                raise
            result = await operation()
        self.uncertain = False
        return result

    async def _reserve(
        self, binding: OperationBindingStorage, payload: RevisionInput | None = None
    ) -> OperationReceipt:
        self._set_state(
            await self.persistence.renew_claim(self.state.fence(), lease_seconds=self.lease_seconds)
        )
        critic = binding.operation == "evaluate"
        identity = OperationIdentity(
            operation_key=f"{binding.operation}:{binding.evaluation_round}",
            agent_name="PromptCriticAgent" if critic else "PromptArchitectAgent",
            agent_version=CRITIC_VERSION if critic else ARCHITECT_VERSION,
            instruction_version=CRITIC_INSTRUCTION
            if critic
            else CREATE_INSTRUCTION
            if binding.operation == "create"
            else REVISE_INSTRUCTION,
            provider=self.identity.critic_provider if critic else self.identity.architect_provider,
            settings=self.identity.critic_settings if critic else self.identity.architect_settings,
        )
        receipt = await self.persistence.reserve_operation(
            self.state.fence(), identity, binding, revision_input=payload
        )
        self._set_state(receipt.workflow)
        if receipt.replayed:
            # Even RUNNING may mean an earlier provider call happened. M5E-5 owns it.
            raise PersistenceError("conflict")
        self.active = receipt
        self.failure_metadata = None
        return receipt

    def _context(self, receipt: OperationReceipt) -> ArchitectRunContext:
        return ArchitectRunContext(
            prompt_request_id=self.state.prompt_request_id,
            workflow_run_id=self.state.id,
            workflow_prompt_request_id=self.state.prompt_request_id,
            agent_run_id=receipt.agent.id,
            agent_workflow_run_id=self.state.id,
        )

    async def _stage(self, handoff: ArchitectHandoff | RevisionResult) -> None:
        active = self.active
        if active is None:
            raise PersistenceError("conflict")
        fence = self.state.fence()
        receipt = await self._complete(
            lambda: self.persistence.complete_architect(
                fence, active.agent.id, handoff, defer_clarification=True
            )
        )
        self._set_state(receipt.workflow)
        self.last_architect_id = active.agent.id
        self.active = None

    async def initial(
        self,
        architect: PromptArchitectAgent,
        critic: PromptCriticAgent,
        source: ArchitectRequestSnapshot,
        context: ArchitectRunContext,
        request_id: str | None,
    ) -> SinglePassOutcome:
        if source != self.snapshot.source or context.workflow_run_id != self.state.id:
            raise PersistenceError("invalid")
        reservation = await self._reserve(
            OperationBindingStorage(
                schema_version=STORAGE_VERSION, operation="create", evaluation_round=1
            )
        )
        handoff = await self._capture(
            create_architect_handoff(
                architect, source, context=self._context(reservation), request_id=request_id
            )
        )
        await self._stage(handoff)
        if isinstance(handoff, ClarificationRequired):
            return handoff
        evaluation = await self.evaluate(critic, handoff, request_id)
        return EvaluatedCandidate.model_validate(dict(candidate=handoff, critic=evaluation))

    async def revise(
        self, architect: PromptArchitectAgent, payload: RevisionInput, request_id: str | None
    ) -> tuple[RevisionInput, RevisionResult]:
        history = self.snapshot.history
        if history is None:
            raise PersistenceError("conflict")
        baseline = next(
            (
                value
                for value in history.rounds
                if value.evaluated.candidate == payload.baseline
                and value.evaluated.critic == payload.evaluation
            ),
            None,
        )
        if baseline is None:
            raise PersistenceError("conflict")
        reference = self.snapshot.evaluations[baseline.round_number - 1]
        reservation = await self._reserve(
            revision_binding(payload, reference.evaluation_id), payload
        )
        bound = make_revision_input(
            baseline.evaluated,
            next_evaluation_round=payload.next_evaluation_round,
            protected_spans=payload.protected_spans,
            context=self._context(reservation),
        )
        result = await self._capture(architect.revise(bound, request_id=request_id))
        return bound, result

    async def stage_revision(self, revised: RevisionResult) -> None:
        await self._stage(revised)

    async def evaluate(
        self, critic: PromptCriticAgent, candidate: CandidateReady, request_id: str | None
    ) -> CriticResult:
        await self._reserve(
            OperationBindingStorage(
                schema_version=STORAGE_VERSION,
                operation="evaluate",
                evaluation_round=self.state.completed_rounds + 1,
            )
        )
        return await self._capture(
            critic.evaluate(make_critic_input(candidate), request_id=request_id)
        )

    async def publish(self, history: EvaluationHistory) -> EvaluationHistory:
        active = self.active
        completed = history.rounds[-1]
        creator = completed.evaluated.candidate.context.agent_run_id
        if active is None or active.agent.operation != "evaluate" or creator is None:
            raise PersistenceError("conflict")
        command = EvaluationCompletion(
            fence=self.state.fence(),
            architect_agent_run_id=creator,
            critic_agent_run_id=active.agent.id,
            critic_operation_key=f"evaluate:{completed.round_number}",
            evaluation_round=completed.round_number,
            evaluated=completed.evaluated,
        )
        receipt = await self._complete(lambda: self.persistence.complete_evaluation(command))
        self._set_state(receipt.workflow)
        self.active = None
        self.snapshot = await self.persistence.execution_snapshot(self.state.fence())
        if self.snapshot.history != history:
            raise PersistenceError("invalid")
        assert self.snapshot.history is not None
        return self.snapshot.history

    async def finish(self, outcome: OptimizationOutcome) -> PersistentOptimizationResult:
        if self.uncertain:
            # A deadline may have cancelled a write at commit. Do not guess a status.
            raise PersistenceError("unavailable")
        self.snapshot = await self.persistence.execution_snapshot(self.state.fence())
        if outcome.history != self.snapshot.history:
            # A deadline can expire after commit but before M5D publishes history.
            # Durable completed evaluations must survive that failed execution.
            if outcome.execution_status != "failed":
                raise PersistenceError("conflict")
            history = self.snapshot.history
            selection = None if history is None else select_best_candidate(history)
            data = outcome.model_dump()
            data.update(
                history=history,
                selection=selection,
                quality_status=None
                if history is None or selection is None
                else history.rounds[selection.round_number - 1].evaluated.scoring.status,
            )
            outcome = OptimizationOutcome.model_validate(data)
        details = TerminalDetailsStorage.from_outcome(outcome)
        agent_id = None
        if outcome.execution_status == "failed" and self.active is not None:
            agent_id = self.active.agent.id
            operations: dict[str, OptimizationOperation] = {
                "create": "architect_create",
                "revise": "architect_revise",
                "evaluate": "critic_evaluate",
            }
            details = TerminalDetailsStorage.model_validate(
                dict(details.model_dump(), failed_operation=operations[self.active.agent.operation])
            )
            if self.failure_metadata is not None:
                fence = self.state.fence()
                failed_id = self.active.agent.id
                error_code = outcome.error_code
                assert error_code is not None
                receipt = await self._complete(
                    lambda: self.persistence.fail_operation(
                        fence,
                        failed_id,
                        error_code=error_code,
                        metadata=self.failure_metadata,
                    )
                )
                self._set_state(receipt.workflow)
        elif outcome.execution_status == "clarification_required" or outcome.termination_reason in {
            "unchanged_baseline",
            "already_evaluated",
        }:
            agent_id = self.last_architect_id
        command = WorkflowFinalization(
            fence=self.state.fence(), details=details, agent_run_id=agent_id
        )
        terminal = await self._complete(lambda: self.persistence.finalize_workflow(command))
        return PersistentOptimizationResult(
            outcome=outcome, terminal=terminal, evaluations=self.snapshot.evaluations
        )


async def execute_persisted_optimizer(
    persistence: WorkflowPersistenceService,
    architect: PromptArchitectAgent,
    critic: PromptCriticAgent,
    *,
    prompt_request_id: UUID,
    workflow_run_id: UUID,
    identity: PersistentExecutionIdentity,
    expected_revision: int = 0,
    lease_seconds: int = 300,
    request_id: str | None = None,
    on_state: Callable[[WorkflowState], None] | None = None,
) -> PersistentOptimizationResult:
    """Explicit new claim only. No workflow creation, implicit resume or provider ownership."""
    try:
        identity = validated(PersistentExecutionIdentity, identity)
        state: WorkflowState = await persistence.claim_workflow(
            prompt_request_id,
            workflow_run_id,
            expected_revision=expected_revision,
            lease_seconds=lease_seconds,
        )
        if on_state is not None:
            on_state(state)
        snapshot = await persistence.execution_snapshot(state.fence())
        execution = _PersistentExecution(persistence, snapshot, identity, lease_seconds, on_state)
        config = snapshot.configuration
        outcome = await optimize_prompt(
            architect,
            critic,
            snapshot.source,
            max_evaluation_rounds=config.max_evaluation_rounds,
            protected_spans=config.protected_spans,
            timeout_seconds=config.timeout_seconds,
            context=ArchitectRunContext(
                prompt_request_id=prompt_request_id,
                workflow_run_id=workflow_run_id,
                workflow_prompt_request_id=prompt_request_id,
            ),
            request_id=request_id,
            execution=execution,
        )
        return await execution.finish(outcome)
    except (ValidationError, ValueError, TypeError):
        pass
    raise PersistenceError("invalid")
