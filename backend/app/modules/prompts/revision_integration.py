"""Construct revision input from one completed evaluation; no selection or workflow."""

from contextlib import suppress

from pydantic import ValidationError

from app.agents.revision_schemas import ProtectedSourceSpan, RevisionInput
from app.llm.errors import LLMError, LLMErrorCode
from app.modules.prompts.architect_contracts import ArchitectRunContext
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate


def make_revision_input(
    evaluated: EvaluatedCandidate,
    *,
    next_evaluation_round: int,
    protected_spans: tuple[ProtectedSourceSpan, ...] = (),
    context: ArchitectRunContext | None = None,
) -> RevisionInput:
    """Revalidate existing evaluation, retain its own feedback and bind a new operation.

    Default context retains request/workflow ancestry but clears the baseline's
    AgentRun ID. Caller-supplied IDs remain assertions, not database authorization.
    """
    payload = None
    if isinstance(evaluated, EvaluatedCandidate):
        with suppress(ValidationError, LLMError):
            validated = EvaluatedCandidate.model_validate(evaluated.model_dump(warnings=False))
            previous = validated.candidate.context
            binding = (
                context
                if context is not None
                else ArchitectRunContext(
                    prompt_request_id=previous.prompt_request_id,
                    workflow_run_id=previous.workflow_run_id,
                    workflow_prompt_request_id=previous.workflow_prompt_request_id,
                )
            )
            payload = RevisionInput(
                baseline=validated.candidate,
                evaluation=validated.critic,
                context=binding,
                next_evaluation_round=next_evaluation_round,
                protected_spans=protected_spans,
            )
    if payload is None:
        raise LLMError(LLMErrorCode.INVALID_REQUEST)
    return payload
