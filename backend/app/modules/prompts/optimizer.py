"""Sequential, bounded, opt-in prompt optimization. Caller owns all dependencies."""

import asyncio
import math
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Final

from pydantic import ValidationError

from app.agents.architect_instructions import INSTRUCTION_VERSION as CREATE_INSTRUCTION_VERSION
from app.agents.architect_schemas import ArchitectResult
from app.agents.critic_schemas import CriticResult
from app.agents.prompt_architect import AGENT_VERSION, PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_schemas import (
    ProtectedSourceSpan,
    RevisedCandidate,
    RevisionClarification,
    RevisionInput,
    RevisionResult,
    UnchangedBaseline,
)
from app.llm.errors import LLMError, LLMErrorCode
from app.models.enums import MAX_EVALUATION_ROUNDS_HARD_LIMIT
from app.modules.prompts.architect_contracts import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    ClarificationRequired,
    CriticInput,
    make_critic_input,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate, run_architect_critic_pass
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.optimization_contracts import EvaluationHistory, EvaluationRound
from app.modules.prompts.optimization_progress import (
    PROGRESS_POLICY_VERSION,
    check_candidate_progress,
    check_revision_progress,
    revision_attempt_key,
)
from app.modules.prompts.optimizer_contracts import (
    OptimizationCounts,
    OptimizationOperation,
    OptimizationOutcome,
    TerminationReason,
    failure_reason,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead

OPTIMIZER_VERSION: Final = "1.0.0"


@dataclass
class _Run:
    limit: int
    deadline: float | None
    clock: Callable[[], float]
    history: EvaluationHistory | None = None
    operation: OptimizationOperation = "input_validation"
    creates: int = 0
    revisions: int = 0
    critics: int = 0

    def check_deadline(self) -> None:
        if self.deadline is not None and self.clock() >= self.deadline:
            raise LLMError(LLMErrorCode.DEADLINE_EXCEEDED)

    def begin(self, operation: OptimizationOperation) -> None:
        self.operation = operation
        self.check_deadline()
        if operation == "architect_create" and self.creates == 0:
            self.creates += 1
        elif operation == "architect_revise" and self.revisions < self.limit - 1:
            self.revisions += 1
        elif operation == "critic_evaluate" and self.critics < self.limit:
            self.critics += 1
        else:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)


class _ArchitectDispatch(PromptArchitectAgent):
    """Instrument borrowed operations hidden inside the existing initial-pass boundary."""

    def __init__(
        self,
        agent: PromptArchitectAgent,
        run: _Run,
        source: ArchitectRequestSnapshot,
        spans: tuple[ProtectedSourceSpan, ...],
    ) -> None:
        self.agent, self.run, self.source, self.spans = agent, run, source, spans

    async def create(
        self, payload: PromptRequestCreate, *, request_id: str | None = None
    ) -> ArchitectResult:
        self.run.begin("architect_create")
        result = await self.agent.create(payload, request_id=request_id)
        self.run.check_deadline()
        if (
            not isinstance(result, ArchitectResult)
            or result.agent_version != AGENT_VERSION
            or result.instruction_version != CREATE_INSTRUCTION_VERSION
        ):
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        if result.decision == "rewrite" and (
            not isinstance(result.rendered_prompt, str)
            or any(span.text(self.source) not in result.rendered_prompt for span in self.spans)
        ):
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        return result

    async def revise(
        self, revision_input: RevisionInput, *, request_id: str | None = None
    ) -> RevisionResult:
        self.run.begin("architect_revise")
        result = await self.agent.revise(revision_input, request_id=request_id)
        self.run.check_deadline()
        return result


class _CriticDispatch(PromptCriticAgent):
    def __init__(self, agent: PromptCriticAgent, run: _Run) -> None:
        self.agent, self.run = agent, run

    async def evaluate(
        self, critic_input: CriticInput, *, request_id: str | None = None
    ) -> CriticResult:
        self.run.begin("critic_evaluate")
        result = await self.agent.evaluate(critic_input, request_id=request_id)
        self.run.check_deadline()
        return result


def _inputs(
    prompt_request: PromptRequestRead,
    context: ArchitectRunContext | None,
    protected_spans: tuple[ProtectedSourceSpan, ...],
) -> tuple[ArchitectRequestSnapshot, ArchitectRunContext, tuple[ProtectedSourceSpan, ...]]:
    if (
        not isinstance(prompt_request, PromptRequestRead)
        or not isinstance(protected_spans, tuple)
        or (context is not None and not isinstance(context, ArchitectRunContext))
    ):
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    source = ArchitectRequestSnapshot.model_validate(prompt_request.model_dump(warnings=False))
    binding = (
        ArchitectRunContext(prompt_request_id=source.id)
        if context is None
        else ArchitectRunContext.model_validate(context.model_dump(warnings=False))
    )
    if binding.prompt_request_id != source.id or len(protected_spans) > 32:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    if any(not isinstance(span, ProtectedSourceSpan) for span in protected_spans):
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    spans = tuple(
        ProtectedSourceSpan.model_validate(span.model_dump(warnings=False))
        for span in protected_spans
    )
    valid = False
    with suppress(ValueError):
        for span in spans:
            span.text(source)
        valid = len(set(spans)) == len(spans)
    if not valid:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return source, binding, spans


def _revision(value: RevisionResult, payload: RevisionInput) -> RevisionResult:
    validated = None
    if isinstance(value, (RevisedCandidate, UnchangedBaseline, RevisionClarification)):
        with suppress(ValidationError):
            validated = type(value).model_validate(value.model_dump(warnings=False))
    if validated is None or (
        validated.baseline != payload.baseline
        or validated.context != payload.context
        or validated.protected_spans != payload.protected_spans
        or validated.next_evaluation_round != payload.next_evaluation_round
        or validated.eligible_issue_keys != tuple(finding.issue_key for finding in payload.feedback)
    ):
        raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
    return validated


def _outcome(
    run: _Run,
    status: str,
    reason: str,
    *,
    questions: tuple[str, ...] = (),
    error_code: LLMErrorCode | None = None,
) -> OptimizationOutcome:
    selection = select_best_candidate(run.history) if run.history is not None else None
    quality = (
        run.history.rounds[selection.round_number - 1].evaluated.scoring.status
        if run.history is not None and selection is not None
        else None
    )
    return OptimizationOutcome.model_validate(
        dict(
            execution_status=status,
            termination_reason=reason,
            history=run.history,
            selection=selection,
            quality_status=quality,
            clarification_questions=questions,
            failed_operation=run.operation if status == "failed" else None,
            error_code=error_code,
            counts=OptimizationCounts(
                architect_create=run.creates,
                architect_revise=run.revisions,
                critic_evaluate=run.critics,
            ),
            max_evaluation_rounds=run.limit,
            optimizer_version=OPTIMIZER_VERSION,
            progress_policy_version=PROGRESS_POLICY_VERSION,
        )
    )


async def _execute(
    run: _Run,
    architect: PromptArchitectAgent,
    critic: PromptCriticAgent,
    source: ArchitectRequestSnapshot,
    context: ArchitectRunContext,
    spans: tuple[ProtectedSourceSpan, ...],
    request_id: str | None,
) -> OptimizationOutcome:
    author = _ArchitectDispatch(architect, run, source, spans)
    judge = _CriticDispatch(critic, run)
    initial = await run_architect_critic_pass(
        author, judge, source, context=context, request_id=request_id
    )
    run.check_deadline()
    if isinstance(initial, ClarificationRequired):
        return _outcome(
            run, "clarification_required", "clarification_required", questions=initial.questions
        )
    run.operation = "validation"
    completed = EvaluationRound(round_number=1, evaluated=initial)
    history = EvaluationHistory(rounds=(completed,))
    run.check_deadline()
    run.history = history  # Publish only after the whole append validates within budget.
    attempted: tuple[str, ...] = ()
    while True:
        run.operation = "selection"
        selection = select_best_candidate(history)
        baseline = history.rounds[selection.round_number - 1]
        if baseline.evaluated.scoring.passed:
            return _outcome(run, "completed", "quality_passed")
        if len(history.rounds) >= run.limit:
            return _outcome(run, "completed", "evaluation_limit")
        run.operation = "progress"
        progress = check_revision_progress(
            history, attempted_revision_keys=attempted, protected_spans=spans
        )
        if progress.action == "stop":
            return _outcome(run, "completed", progress.reason)
        payload = make_revision_input(
            baseline.evaluated, next_evaluation_round=len(history.rounds) + 1, protected_spans=spans
        )
        key = revision_attempt_key(payload)
        if (
            progress.action != "revise"
            or progress.baseline_round_number != baseline.round_number
            or progress.next_round_number != payload.next_evaluation_round
            or progress.revision_attempt_key != key
        ):
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        run.check_deadline()
        attempted = (*attempted, key)  # Record before dispatch; never replay the run on failure.
        result = await author.revise(payload, request_id=request_id)
        run.operation = "validation"
        revised = _revision(result, payload)
        run.operation = "progress"
        candidate_progress = check_candidate_progress(history, revised)
        if candidate_progress.action == "stop":
            if isinstance(revised, RevisionClarification):
                return _outcome(
                    run,
                    "clarification_required",
                    "clarification_required",
                    questions=revised.questions,
                )
            return _outcome(run, "completed", candidate_progress.reason)
        if (
            not isinstance(revised, RevisedCandidate)
            or candidate_progress.action != "evaluate"
            or candidate_progress.baseline_round_number != baseline.round_number
            or candidate_progress.next_round_number != len(history.rounds) + 1
        ):
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        evaluation = await judge.evaluate(
            make_critic_input(revised.candidate), request_id=request_id
        )
        run.operation = "validation"
        evaluated = EvaluatedCandidate.model_validate(
            dict(candidate=revised.candidate, critic=evaluation)
        )
        completed = EvaluationRound(
            round_number=len(history.rounds) + 1,
            evaluated=evaluated,
            baseline_round_number=baseline.round_number,
            revision=revised,
        )
        history = EvaluationHistory(rounds=(*history.rounds, completed))
        run.check_deadline()
        run.history = history


async def optimize_prompt(
    architect: PromptArchitectAgent,
    critic: PromptCriticAgent,
    prompt_request: PromptRequestRead,
    *,
    max_evaluation_rounds: int = MAX_EVALUATION_ROUNDS_HARD_LIMIT,
    protected_spans: tuple[ProtectedSourceSpan, ...] = (),
    context: ArchitectRunContext | None = None,
    request_id: str | None = None,
    timeout_seconds: float | None = None,
) -> OptimizationOutcome:
    """Run once in the current task; invalid limits/deadlines raise safe INVALID_REQUEST.

    Expected execution failures return retained validated history. External cancellation
    and unexpected programming errors propagate. Providers/gateways remain caller-owned.
    """
    if (
        type(max_evaluation_rounds) is not int
        or not 1 <= max_evaluation_rounds <= MAX_EVALUATION_ROUNDS_HARD_LIMIT
    ):
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    duration: float | None = None
    if timeout_seconds is not None:
        if type(timeout_seconds) not in (int, float):
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        with suppress(OverflowError):
            duration = float(timeout_seconds)
        if duration is None or not math.isfinite(duration) or duration <= 0:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
    loop = asyncio.get_running_loop()
    deadline = None if duration is None else loop.time() + duration
    if deadline is not None and not math.isfinite(deadline):
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    run = _Run(max_evaluation_rounds, deadline, loop.time)
    failure: tuple[TerminationReason, LLMErrorCode] | None = None
    timer = asyncio.timeout_at(deadline)
    try:
        async with timer:
            source, binding, spans = _inputs(prompt_request, context, protected_spans)
            result = await _execute(run, architect, critic, source, binding, spans, request_id)
            run.check_deadline()
            return result
    except LLMError as exc:
        failure = failure_reason(exc.code), exc.code
    except ValidationError:
        failure = (
            "validation_failure",
            (
                LLMErrorCode.INVALID_REQUEST
                if run.operation == "input_validation"
                else LLMErrorCode.INVALID_STRUCTURED_OUTPUT
            ),
        )
    except TimeoutError:
        if not timer.expired():
            raise
        failure = "deadline_exceeded", LLMErrorCode.DEADLINE_EXCEEDED
    # No private exception object or chained validation context enters the outcome.
    assert failure is not None
    return _outcome(run, "failed", failure[0], error_code=failure[1])
