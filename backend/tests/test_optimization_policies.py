"""Deterministic completed-history/ranking/progress tests, not model-quality evidence."""

import io
import json
import socket
import traceback
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_schemas import (
    ProtectedSourceSpan,
    RevisedCandidate,
    RevisionClarification,
    UnchangedBaseline,
)
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMResponse, ModelSettings
from app.models.enums import PromptDetailLevel, TargetMode
from app.modules.prompts.architect_contracts import (
    ArchitectRunContext,
    make_critic_input,
)
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate, run_architect_critic_pass
from app.modules.prompts.candidate_selection import SELECTION_POLICY_VERSION, select_best_candidate
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimization_contracts import (
    CandidateSelection,
    EvaluationHistory,
    EvaluationRound,
    ProgressDecision,
)
from app.modules.prompts.optimization_progress import (
    PROGRESS_POLICY_VERSION,
    check_candidate_progress,
    check_revision_progress,
    revision_attempt_key,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead
from app.scoring.rubric import CRITERIA

RAW = "Explain Python 3.12 code unchanged. Work offline only."
PRIVATE = "PRIVATE_OPTIMIZATION_POLICY_SENTINEL"
REQUEST_ID = UUID(int=1)
S82 = dict(intent_fidelity=8, constraints_and_scope=6, clarity=6, structure=8)
S75 = dict(intent_fidelity=8, constraints_and_scope=5, completeness=6, clarity=6)
S80 = dict(intent_fidelity=8, constraints_and_scope=6, completeness=6, structure=8)
S85 = dict(intent_fidelity=8, constraints_and_scope=7, clarity=6)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def response(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        provider="fake", finish_reason="complete", text=json.dumps(data, ensure_ascii=False)
    )


def gateway(provider: FakeLLMProvider) -> LLMGateway:
    return LLMGateway(
        provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0), clock=lambda: 0.0
    )


def critic_data(
    scores: Mapping[str, int | None] | None = None,
    *,
    raw: str = RAW,
    critical: bool = False,
    uncertain: bool = False,
) -> dict[str, Any]:
    values: dict[str, int | None] = {key: 10 for key, _, _ in CRITERIA}
    values.update(scores or {})
    if critical:
        values["constraints_and_scope"] = min(values["constraints_and_scope"] or 6, 6)
    if uncertain:
        values["clarity"] = None
    ref = dict(source="raw_prompt", quote=raw.lstrip()[:300])
    criteria: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for key, _, _ in CRITERIA:
        score = values[key]
        criteria[key] = dict(
            score=score,
            rationale="Scripted contract assessment, not a real judgment.",
            evidence=[ref] if score is not None else [],
        )
        if score is not None and score < 9:
            issues.append(
                dict(
                    criterion=key,
                    code="missing_requirement",
                    status="supported",
                    severity="critical"
                    if critical and key == "constraints_and_scope"
                    else "major"
                    if score <= 6
                    else "minor",
                    description=f"Scripted supported finding for {key}.",
                    recommendation="Restore the supplied requirement where substantiated.",
                    evidence=[ref],
                )
            )
    if uncertain:
        issues.append(
            dict(
                criterion="clarity",
                code="insufficient_specificity",
                status="uncertain",
                severity=None,
                description="An external detail is unresolved.",
                recommendation="Check available evidence without inventing details.",
                evidence=[],
            )
        )
    return dict(criteria=criteria, issues=issues, summary="Scripted software fixture.")


async def evaluated(
    index: int = 1,
    scores: Mapping[str, int | None] | None = None,
    *,
    critical: bool = False,
    uncertain: bool = False,
    text: str | None = None,
    raw: str = RAW,
    request_id: UUID = REQUEST_ID,
    target: TargetMode = TargetMode.GOOGLE,
    detail: PromptDetailLevel = PromptDetailLevel.MEDIUM,
    context_text: str = PRIVATE,
    workflow: UUID | None = None,
    agent_id: UUID | None = None,
    preserve: bool = False,
) -> EvaluatedCandidate:
    payload = PromptRequestCreate(
        raw_prompt=raw, user_context=context_text, target_mode=target, prompt_detail_level=detail
    )
    source = PromptRequestRead(
        id=request_id, created_at=datetime(2026, 1, 1, tzinfo=UTC), **payload.model_dump()
    )
    canonical = CanonicalPrompt.from_task(
        raw if preserve else text if text is not None else raw + f"\n\nCandidate {index}."
    )
    author: dict[str, Any] = dict(
        decision="preserve" if preserve else "rewrite",
        canonical_prompt=None if preserve else canonical.model_dump(mode="json"),
        changes=[],
        questions=[],
    )
    provider = FakeLLMProvider(
        [
            response(author),
            response(critic_data(scores, raw=raw, critical=critical, uncertain=uncertain)),
        ]
    )
    shared = gateway(provider)
    binding = ArchitectRunContext(
        prompt_request_id=request_id,
        workflow_run_id=workflow,
        workflow_prompt_request_id=request_id if workflow else None,
        agent_run_id=agent_id,
        agent_workflow_run_id=workflow if agent_id else None,
    )
    try:
        result = await run_architect_critic_pass(
            PromptArchitectAgent(shared, ModelSettings(model="fake-a")),
            PromptCriticAgent(shared, ModelSettings(model="fake-c")),
            source,
            context=binding,
        )
        assert isinstance(result, EvaluatedCandidate) and provider.calls == 2
        return result
    finally:
        await provider.aclose()


def history(*values: EvaluatedCandidate) -> EvaluationHistory:
    return EvaluationHistory(
        rounds=tuple(
            EvaluationRound(
                round_number=index, evaluated=value, baseline_round_number=None if index == 1 else 1
            )
            for index, value in enumerate(values, start=1)
        )
    )


async def revision_outcome(
    base: EvaluatedCandidate,
    decision: str = "rewrite",
    *,
    next_round: int = 2,
    text: str = RAW + "\n\nRevised candidate.",
) -> RevisedCandidate | UnchangedBaseline | RevisionClarification:
    payload = make_revision_input(base, next_evaluation_round=next_round)
    data = dict(
        decision=decision,
        canonical_prompt=CanonicalPrompt.from_task(text).model_dump(mode="json")
        if decision == "rewrite"
        else None,
        changes=["Addressed a supplied finding."] if decision == "rewrite" else [],
        questions=["Which source should be used?"] if decision == "clarify" else [],
        addressed_issue_keys=[payload.feedback[0].issue_key] if decision == "rewrite" else [],
    )
    provider = FakeLLMProvider([response(data)])
    try:
        return await PromptArchitectAgent(gateway(provider), ModelSettings(model="fake-r")).revise(
            payload
        )
    finally:
        await provider.aclose()


async def evaluate_revision(
    revision: RevisedCandidate, scores: Mapping[str, int | None] | None = None
) -> EvaluatedCandidate:
    provider = FakeLLMProvider([response(critic_data(scores))])
    try:
        critic = await PromptCriticAgent(gateway(provider), ModelSettings(model="fake-c")).evaluate(
            make_critic_input(revision.candidate)
        )
        return EvaluatedCandidate.model_validate(dict(candidate=revision.candidate, critic=critic))
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario,expected",
    [
        ("one", 1),
        ("qualified_first", 1),
        ("qualified_later", 2),
        ("qualified_over_critical", 1),
        ("no_critical_over_score", 1),
        ("earlier_better", 1),
        ("intent_tie_break", 2),
        ("earlier_tie_break", 1),
        ("known_total", 2),
        ("all_unassessable", 1),
        ("known_intent", 2),
        ("nullable_intent_tie", 1),
        ("higher_nullable_intent", 2),
        ("unassessable_without_critical", 1),
    ],
)
async def test_deterministic_selection_uses_actual_validated_evaluations(
    scenario: str, expected: int
) -> None:
    if scenario == "one":
        values = [await evaluated(1, S82)]
    elif scenario == "qualified_first":
        values = [await evaluated(1), await evaluated(2, S82)]
    elif scenario == "qualified_later":
        values = [await evaluated(1, S82), await evaluated(2)]
    elif scenario == "qualified_over_critical":
        values = [await evaluated(1, S85), await evaluated(2, critical=True)]
    elif scenario == "no_critical_over_score":
        values = [await evaluated(1, S82), await evaluated(2, critical=True)]
    elif scenario == "earlier_better":
        values = [await evaluated(1, S82), await evaluated(2, S75), await evaluated(3, S80)]
    elif scenario == "intent_tie_break":
        values = [
            await evaluated(1, S82),
            await evaluated(2, dict(constraints_and_scope=4, completeness=6)),
        ]
    elif scenario == "earlier_tie_break":
        values = [await evaluated(1, S82), await evaluated(2, S82)]
    elif scenario == "known_total":
        values = [await evaluated(1, dict(clarity=None)), await evaluated(2, S82)]
    elif scenario == "all_unassessable":
        values = [await evaluated(1, dict(clarity=None)), await evaluated(2, dict(clarity=None))]
    elif scenario == "known_intent":
        values = [
            await evaluated(1, dict(intent_fidelity=None)),
            await evaluated(2, dict(clarity=None)),
        ]
    elif scenario == "nullable_intent_tie":
        values = [
            await evaluated(1, dict(intent_fidelity=None)),
            await evaluated(2, dict(intent_fidelity=None)),
        ]
    elif scenario == "higher_nullable_intent":
        values = [
            await evaluated(1, dict(clarity=None, intent_fidelity=9)),
            await evaluated(2, dict(clarity=None)),
        ]
    else:
        values = [await evaluated(1, dict(clarity=None)), await evaluated(2, critical=True)]
    data = history(*values)
    before = data.model_dump_json()
    first, second = select_best_candidate(data), select_best_candidate(data)
    assert first == second and first.round_number == expected
    assert first.input_fingerprint == values[expected - 1].critic.input_fingerprint
    assert first.selection_policy_version == SELECTION_POLICY_VERSION == "1.0.0"
    assert data.model_dump_json() == before
    assert not {"score", "total_score", "candidate", "passed"} & set(type(first).model_fields)
    if scenario == "earlier_better":
        assert [value.scoring.total_score for value in values] == [
            Decimal(82),
            Decimal(75),
            Decimal(80),
        ]
    if "unassessable" in scenario or scenario in {
        "known_intent",
        "nullable_intent_tie",
        "higher_nullable_intent",
    }:
        assert values[0].scoring.total_score is None and not values[0].scoring.passed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_round",
        "skipped_round",
        "fourth",
        "empty",
        "list",
        "missing_baseline",
        "self_baseline",
        "future_baseline",
        "wrong_request",
        "preferences",
        "context",
        "target",
        "renderer",
        "workflow",
        "fingerprint",
        "evidence",
        "score",
        "same_candidate",
        "same_rendering",
        "reused_agent",
        "nested_round",
        "nested_score",
    ],
)
async def test_history_rejects_invalid_identity_ancestry_or_nested_data(mutation: str) -> None:
    first, second = await evaluated(1, S82), await evaluated(2, S80)
    rounds = [
        EvaluationRound(round_number=1, evaluated=first),
        EvaluationRound(round_number=2, evaluated=second, baseline_round_number=1),
    ]
    if mutation == "duplicate_round":
        rounds[1] = rounds[1].model_copy(update={"round_number": 1, "baseline_round_number": None})
    elif mutation == "skipped_round":
        rounds[1] = rounds[1].model_copy(update={"round_number": 3})
    elif mutation == "fourth":
        rounds += [
            rounds[1].model_copy(update={"round_number": 3}),
            rounds[1].model_copy(update={"round_number": 4}),
        ]
    elif mutation in {"missing_baseline", "self_baseline", "future_baseline"}:
        rounds[1] = rounds[1].model_copy(
            update={
                "baseline_round_number": None
                if mutation == "missing_baseline"
                else 2
                if mutation == "self_baseline"
                else 3
            }
        )
    elif mutation == "wrong_request":
        second = await evaluated(2, S80, request_id=UUID(int=9))
    elif mutation == "preferences":
        second = await evaluated(2, S80, detail=PromptDetailLevel.HIGH)
    elif mutation == "context":
        second = await evaluated(2, S80, context_text="Different source context")
    elif mutation == "target":
        second = await evaluated(2, S80, target=TargetMode.OPENAI)
    elif mutation == "workflow":
        second = await evaluated(2, S80, workflow=UUID(int=9))
    elif mutation == "renderer":
        second = second.model_copy(
            update={"candidate": second.candidate.model_copy(update={"renderer_version": "999"})}
        )
    elif mutation == "fingerprint":
        second = second.model_copy(
            update={"critic": second.critic.model_copy(update={"input_fingerprint": "f" * 64})}
        )
    elif mutation == "score":
        second = second.model_copy(
            update={"scoring": second.scoring.model_copy(update={"total_score": Decimal(81)})}
        )
    elif mutation in {"nested_score", "evidence"}:
        assessment = second.critic.output.criteria.clarity
        updates = (
            {"score": True}
            if mutation == "nested_score"
            else {"evidence": (assessment.evidence[0].model_copy(update={"quote": PRIVATE}),)}
        )
        second = second.model_copy(
            update={
                "critic": second.critic.model_copy(
                    update={
                        "output": second.critic.output.model_copy(
                            update={
                                "criteria": second.critic.output.criteria.model_copy(
                                    update={"clarity": assessment.model_copy(update=updates)}
                                )
                            }
                        )
                    }
                )
            }
        )
    elif mutation in {"same_candidate", "same_rendering"}:
        second = await evaluated(2, S80, text=first.candidate.rendered_prompt)
        if mutation == "same_rendering":
            canonical = CanonicalPrompt.from_task(RAW).model_copy(
                update={"context": "Candidate 1."}
            )
            second = second.model_copy(
                update={
                    "candidate": second.candidate.model_copy(update={"canonical_prompt": canonical})
                }
            )
    elif mutation == "reused_agent":
        first = await evaluated(1, S82, workflow=UUID(int=2), agent_id=UUID(int=7))
        second = await evaluated(2, S80, workflow=UUID(int=2), agent_id=UUID(int=7))
        rounds[0] = EvaluationRound(round_number=1, evaluated=first)
    elif mutation == "nested_round":
        rounds[1] = rounds[1].model_copy(update={"round_number": True})
    if mutation not in {
        "duplicate_round",
        "skipped_round",
        "fourth",
        "missing_baseline",
        "self_baseline",
        "future_baseline",
        "nested_round",
    }:
        rounds[1] = rounds[1].model_copy(update={"evaluated": second})
    values: Any = () if mutation == "empty" else rounds if mutation == "list" else tuple(rounds)
    with pytest.raises((ValidationError, LLMError)):
        EvaluationHistory(rounds=values)


@pytest.mark.anyio
async def test_first_round_and_history_are_immutable_and_json_roundtrip() -> None:
    value = await evaluated(1, S82)
    for updates in [dict(round_number=0), dict(round_number=4), dict(baseline_round_number=1)]:
        with pytest.raises(ValidationError):
            EvaluationRound.model_validate({"round_number": 1, "evaluated": value, **updates})
    data = history(value)
    assert EvaluationHistory.model_validate_json(data.model_dump_json()) == data
    assert EvaluationHistory.model_validate(data) == data
    assert data.original_request == value.candidate.original_request
    with pytest.raises(ValidationError):
        data.rounds = ()
    with pytest.raises(ValidationError):
        data.original_request.raw_prompt = "Changed"


@pytest.mark.anyio
async def test_real_revision_provenance_binds_baseline_candidate_and_feedback_keys() -> None:
    first = await evaluated(1, S82)
    revision = await revision_outcome(first)
    assert isinstance(revision, RevisedCandidate)
    second = await evaluate_revision(revision, S80)
    complete = EvaluationRound(
        round_number=2, evaluated=second, baseline_round_number=1, revision=revision
    )
    data = EvaluationHistory(rounds=(EvaluationRound(round_number=1, evaluated=first), complete))
    assert data.rounds[1].revision == revision
    assert EvaluationHistory.model_validate_json(data.model_dump_json()) == data
    for changed in [
        revision.model_copy(update={"next_evaluation_round": 3}),
        revision.model_copy(
            update={"eligible_issue_keys": ("issue_999",), "addressed_issue_keys": ("issue_999",)}
        ),
    ]:
        with pytest.raises((ValidationError, LLMError)):
            EvaluationHistory(
                rounds=(data.rounds[0], complete.model_copy(update={"revision": changed}))
            )
    other = await evaluated(3, S75)
    with pytest.raises((ValidationError, LLMError)):
        EvaluationHistory(rounds=(data.rounds[0], complete.model_copy(update={"evaluated": other})))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "scenario,reason",
    [
        ("supported", "revision_justified"),
        ("uncertain", "no_actionable_feedback"),
        ("no_issues", "no_actionable_feedback"),
        ("qualified", "qualified"),
        ("limit", "evaluation_limit"),
    ],
)
async def test_revision_progress_is_explicit_and_bounded(scenario: str, reason: str) -> None:
    if scenario == "qualified":
        data = history(await evaluated())
    elif scenario == "uncertain":
        data = history(await evaluated(uncertain=True))
    elif scenario == "no_issues":
        data = history(await evaluated(scores=dict(clarity=None)))
    elif scenario == "limit":
        data = history(await evaluated(1, S82), await evaluated(2, S75), await evaluated(3, S80))
    else:
        data = history(await evaluated(1, S82))
    result = check_revision_progress(data)
    assert (
        result.reason == reason
        and result.progress_policy_version == PROGRESS_POLICY_VERSION == "1.0.0"
    )
    assert result.baseline_round_number == 1
    if scenario == "supported":
        assert result.action == "revise" and result.next_round_number == 2
    else:
        assert result.action == "stop" and result.next_round_number is None


@pytest.mark.anyio
async def test_revision_attempt_key_blocks_equivalent_attempts_without_round_or_id_leakage() -> (
    None
):
    first = await evaluated(1, S82)
    data = history(first)
    payload = make_revision_input(first, next_evaluation_round=2)
    original = payload.model_dump_json()
    key = revision_attempt_key(payload)
    assert (
        key == revision_attempt_key(payload)
        and len(key) == 64
        and payload.model_dump_json() == original
    )
    assert revision_attempt_key(payload.model_copy(update={"next_evaluation_round": 3})) == key
    reversed_output = first.critic.output.model_copy(
        update={"issues": tuple(reversed(first.critic.output.issues))}
    )
    alternative = first.model_copy(
        update={"critic": first.critic.model_copy(update={"output": reversed_output})}
    )
    assert revision_attempt_key(make_revision_input(alternative, next_evaluation_round=2)) == key
    changed_issue = first.critic.output.issues[0].model_copy(
        update={"recommendation": "Use a different supplied fix."}
    )
    changed_output = first.critic.output.model_copy(
        update={"issues": (changed_issue, *first.critic.output.issues[1:])}
    )
    altered = first.model_copy(
        update={"critic": first.critic.model_copy(update={"output": changed_output})}
    )
    assert revision_attempt_key(make_revision_input(altered, next_evaluation_round=2)) != key
    protection = ProtectedSourceSpan(
        source="raw_prompt", kind="version", start=RAW.index("3.12"), end=RAW.index("3.12") + 4
    )
    assert (
        revision_attempt_key(
            make_revision_input(first, next_evaluation_round=2, protected_spans=(protection,))
        )
        != key
    )
    stop = check_revision_progress(data, attempted_revision_keys=(key,))
    assert (
        stop.reason == "repeated_revision_attempt"
        and stop.action == "stop"
        and stop.next_round_number is None
    )
    assert key not in repr(stop)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "decision,reason",
    [
        ("preserve", "unchanged_baseline"),
        ("clarify", "clarification_required"),
        ("rewrite", "new_candidate"),
        ("identical", "unchanged_baseline"),
    ],
)
async def test_actual_revision_outcomes_produce_pure_progress_decisions(
    decision: str, reason: str
) -> None:
    first = await evaluated(1, S82)
    value = await revision_outcome(
        first,
        "rewrite" if decision == "identical" else decision,
        text=first.candidate.rendered_prompt
        if decision == "identical"
        else RAW + "\n\nNew revised text.",
    )
    result = check_candidate_progress(history(first), value)
    assert result.reason == reason
    assert result.action == ("evaluate" if decision == "rewrite" else "stop")
    assert result.next_round_number == (2 if decision == "rewrite" else None)


@pytest.mark.anyio
async def test_duplicate_guard_uses_exact_rendering_without_whitespace_normalization() -> None:
    first = await evaluated(1, S82)
    data = history(first)
    repeated = first.candidate.model_copy(
        update={
            "canonical_prompt": CanonicalPrompt.from_task(RAW).model_copy(
                update={"context": "Candidate 1."}
            )
        }
    )
    assert check_candidate_progress(data, repeated).reason == "already_evaluated"
    spaced = CanonicalPrompt.from_task(first.candidate.rendered_prompt + " ")
    changed = first.candidate.model_copy(
        update={"canonical_prompt": spaced, "rendered_prompt": spaced.task}
    )
    assert check_candidate_progress(data, changed).reason == "new_candidate"
    full = history(first, await evaluated(2, S75), await evaluated(3, S80))
    stopped = check_candidate_progress(full, changed)
    assert stopped.reason == "evaluation_limit" and stopped.next_round_number is None


@pytest.mark.anyio
async def test_progress_uses_best_baseline_and_rejects_unrelated_revision_provenance() -> None:
    first, second = await evaluated(1, S82), await evaluated(2, S75)
    data = history(first, second)
    assert check_revision_progress(data).baseline_round_number == 1
    assert check_revision_progress(data).next_round_number == 3
    unrelated = await revision_outcome(second, next_round=3)
    with pytest.raises(LLMError) as caught:
        check_candidate_progress(data, unrelated)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST and caught.value.__context__ is None


@pytest.mark.anyio
async def test_pure_helpers_have_no_provider_sql_network_or_input_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = await evaluated(1, S82)
    data = history(first)
    proposed = (await evaluated(2, S80)).candidate
    payload = make_revision_input(first, next_evaluation_round=2)
    before = data.model_dump_json(), proposed.model_dump_json(), payload.model_dump_json()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Pure policy helper invoked execution or external state")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    for name in ("add", "execute", "flush", "commit", "delete"):
        monkeypatch.setattr(AsyncSession, name, forbidden)
    monkeypatch.setattr(LLMGateway, "generate_json", forbidden)
    monkeypatch.setattr(FakeLLMProvider, "generate", forbidden)
    monkeypatch.setattr(PromptArchitectAgent, "create", forbidden)
    monkeypatch.setattr(PromptArchitectAgent, "revise", forbidden)
    monkeypatch.setattr(PromptCriticAgent, "evaluate", forbidden)
    monkeypatch.setattr("app.llm.composition.create_provider", forbidden)
    assert select_best_candidate(data).round_number == 1
    assert check_revision_progress(data).action == "revise"
    assert check_candidate_progress(data, proposed).action == "evaluate"
    assert len(revision_attempt_key(payload)) == 64
    assert before == (data.model_dump_json(), proposed.model_dump_json(), payload.model_dump_json())


@pytest.mark.anyio
async def test_safe_errors_and_normal_representations_exclude_content_and_fingerprints() -> None:
    data = history(await evaluated(1, S82, raw=RAW + PRIVATE))
    stream = io.StringIO()
    configure_logging(stream=stream)
    selection, decision = select_best_candidate(data), check_revision_progress(data)
    hidden = [PRIVATE, selection.input_fingerprint, decision.revision_attempt_key]
    assert all(
        value is not None
        and value not in repr(data) + repr(selection) + repr(decision) + stream.getvalue()
        for value in hidden
    )
    tampered = data.model_copy(
        update={"rounds": (data.rounds[0].model_copy(update={"round_number": PRIVATE}),)}
    )
    for helper in (select_best_candidate, check_revision_progress):
        with pytest.raises(LLMError) as caught:
            helper(tampered)
        assert (
            caught.value.code == LLMErrorCode.INVALID_REQUEST and caught.value.__context__ is None
        )
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    assert PRIVATE in data.model_dump_json()  # Explicit full serialization stays restricted.


@pytest.mark.parametrize(
    "values",
    [
        dict(
            action="evaluate", reason="new_candidate", baseline_round_number=3, next_round_number=4
        ),
        dict(
            action="stop", reason="new_candidate", baseline_round_number=1, next_round_number=None
        ),
        dict(
            action="revise",
            reason="revision_justified",
            baseline_round_number=1,
            next_round_number=None,
        ),
    ],
)
def test_progress_contract_cannot_authorize_invalid_or_fourth_round(values: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ProgressDecision.model_validate(
            dict(revision_attempt_key=None, progress_policy_version="1.0.0", **values)
        )


def test_selection_contract_never_contains_scores_or_private_content() -> None:
    with pytest.raises(ValidationError):
        CandidateSelection(
            round_number=4, input_fingerprint="a" * 64, selection_policy_version="1.0.0"
        )


@pytest.mark.anyio
async def test_preserved_original_is_a_completed_candidate_with_exact_formatting() -> None:
    raw = 'Çevir: "Merhaba". Preserve this code exactly:\n```python\ndef f():\n    return 1\n```\n'
    first = await evaluated(raw=raw, scores=S82, preserve=True)
    data = history(first)
    assert first.candidate.decision == "preserve" and first.candidate.rendered_prompt == raw
    assert data.original_request.raw_prompt == raw
    assert select_best_candidate(data).round_number == 1
    assert check_candidate_progress(data, first.candidate).reason == "already_evaluated"
    assert data.rounds[0].evaluated.candidate.rendered_prompt == raw


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["generated", "clarification", "failed"])
async def test_history_excludes_noncompleted_results(kind: str) -> None:
    first = await evaluated(1, S82)
    invalid: Any = (
        first.candidate
        if kind == "generated"
        else dict(status="clarification_required", questions=[PRIVATE])
        if kind == "clarification"
        else dict(status="failed", candidate=first.candidate)
    )
    with pytest.raises(ValidationError) as caught:
        EvaluationRound(round_number=1, evaluated=invalid)
    assert PRIVATE not in str(caught.value)


@pytest.mark.anyio
async def test_third_round_can_revise_earlier_best_with_available_provenance() -> None:
    first, second = await evaluated(1, S82), await evaluated(2, S75)
    previous = history(first, second)
    revised = await revision_outcome(first, next_round=3)
    assert isinstance(revised, RevisedCandidate)
    progress = check_candidate_progress(previous, revised)
    assert progress.action == "evaluate" and progress.next_round_number == 3
    third = EvaluationRound(
        round_number=3,
        evaluated=await evaluate_revision(revised, S80),
        baseline_round_number=1,
        revision=revised,
    )
    data = EvaluationHistory(rounds=(*previous.rounds, third))
    assert select_best_candidate(data).round_number == 1
    with pytest.raises(ValidationError):
        EvaluationHistory(
            rounds=(*previous.rounds, third.model_copy(update={"baseline_round_number": 2}))
        )


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["preserve", "clarify", "rewrite"])
async def test_all_revision_outcomes_must_retain_available_feedback_provenance(
    decision: str,
) -> None:
    first = await evaluated(1, S82)
    outcome = await revision_outcome(first, decision)
    updates: dict[str, Any] = {"eligible_issue_keys": ("issue_999",)}
    if isinstance(outcome, RevisedCandidate):
        updates["addressed_issue_keys"] = ("issue_999",)
    with pytest.raises(LLMError) as caught:
        check_candidate_progress(history(first), outcome.model_copy(update=updates))
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert caught.value.__context__ is None


@pytest.mark.anyio
async def test_attempt_key_ignores_telemetry_and_evidence_order_but_not_baseline() -> None:
    first = await evaluated(1, S82, workflow=UUID(int=2), agent_id=UUID(int=3))
    issue = first.critic.output.issues[0]
    refs = (issue.evidence[0], issue.evidence[0].model_copy(update={"quote": "Python 3.12"}))
    output = first.critic.output.model_copy(
        update={
            "issues": (issue.model_copy(update={"evidence": refs}), *first.critic.output.issues[1:])
        }
    )
    evaluation = first.model_copy(
        update={"critic": first.critic.model_copy(update={"output": output})}
    )
    payload = make_revision_input(evaluation, next_evaluation_round=2)
    changed_context = payload.context.model_copy(
        update={"agent_run_id": UUID(int=9), "agent_workflow_run_id": UUID(int=2)}
    )
    assert revision_attempt_key(
        payload.model_copy(update={"context": changed_context})
    ) == revision_attempt_key(payload)
    reordered = output.model_copy(
        update={
            "issues": (
                issue.model_copy(update={"evidence": tuple(reversed(refs))}),
                *output.issues[1:],
            )
        }
    )
    same = evaluation.model_copy(
        update={"critic": evaluation.critic.model_copy(update={"output": reordered})}
    )
    assert revision_attempt_key(
        make_revision_input(same, next_evaluation_round=3)
    ) == revision_attempt_key(payload)
    other = make_revision_input(await evaluated(2, S82), next_evaluation_round=2)
    assert revision_attempt_key(other) != revision_attempt_key(payload)


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["candidate", "history", "registry", "attempt_input"])
async def test_public_progress_failures_are_safe_even_with_private_nested_input(kind: str) -> None:
    data = history(await evaluated(1, S82))
    candidate = (await evaluated(2, S80)).candidate
    with pytest.raises(LLMError) as caught:
        if kind == "candidate":
            check_candidate_progress(
                data, candidate.model_copy(update={"rendered_prompt": PRIVATE})
            )
        elif kind == "history":
            check_candidate_progress(data.model_copy(update={"rounds": PRIVATE}), candidate)
        elif kind == "registry":
            check_revision_progress(data, attempted_revision_keys=(PRIVATE,))
        else:
            payload = make_revision_input(data.rounds[0].evaluated, next_evaluation_round=2)
            revision_attempt_key(payload.model_copy(update={"next_evaluation_round": PRIVATE}))
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST and caught.value.__context__ is None
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.anyio
async def test_no_actionable_feedback_prevents_new_candidate_evaluation() -> None:
    first = await evaluated(uncertain=True)
    candidate = (await evaluated(2, S82)).candidate
    result = check_candidate_progress(history(first), candidate)
    assert result.action == "stop" and result.reason == "no_actionable_feedback"
    assert result.next_round_number is None


@pytest.mark.parametrize(
    "action,reason,key",
    [
        ("revise", "revision_justified", None),
        ("evaluate", "new_candidate", "a" * 64),
        ("stop", "repeated_revision_attempt", None),
        ("stop", "qualified", "a" * 64),
    ],
)
def test_progress_attempt_key_has_explicit_meaning(
    action: str, reason: str, key: str | None
) -> None:
    with pytest.raises(ValidationError):
        ProgressDecision.model_validate(
            dict(
                action=action,
                reason=reason,
                revision_attempt_key=key,
                baseline_round_number=1,
                next_round_number=None if action == "stop" else 2,
                progress_policy_version="1.0.0",
            )
        )
