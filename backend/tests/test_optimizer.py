"""Offline execution/accounting tests; scripted scores are not semantic evaluation."""

import asyncio
import io
import json
import socket
import traceback
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.architect_schemas import ArchitectResult
from app.agents.critic_schemas import CriticResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_schemas import (
    ProtectedSourceSpan,
    RevisedCandidate,
    RevisionInput,
    RevisionResult,
)
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMRequest, LLMResponse, ModelSettings, StructuredResult
from app.models.enums import PromptDetailLevel, TargetMode
from app.modules.prompts import optimizer
from app.modules.prompts.architect_contracts import ArchitectRunContext, CriticInput
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimizer import optimize_prompt
from app.modules.prompts.optimizer_contracts import OptimizationOutcome
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead
from app.scoring.engine import ScoringEngine
from app.scoring.rubric import CRITERIA
from app.scoring.schemas import ScoringResult

RAW = (
    '  Python 3.12 kodunu açıkla. Work offline. Keep "quotes" and code:\n'  # noqa: RUF001
    "```python\ndef f():\n    return 1\n```\n"
)
PRIVATE = "PRIVATE_OPTIMIZER_SENTINEL"
NOTE = "PRIVATE_ARCHITECT_NOTES_SENTINEL"
QUESTION = "Which supplied source is authoritative?"
REQUEST_ID, WORKFLOW_ID, AGENT_ID = (UUID(int=index) for index in range(1, 4))
S75 = dict(intent_fidelity=8, constraints_and_scope=5, completeness=6, clarity=6)
S80 = dict(intent_fidelity=8, constraints_and_scope=6, completeness=6, structure=8)
S82 = dict(intent_fidelity=8, constraints_and_scope=6, clarity=6, structure=8)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def source() -> PromptRequestRead:
    payload = PromptRequestCreate(
        raw_prompt=RAW,
        user_context=PRIVATE,
        output_language="tr",
        target_mode=TargetMode.GOOGLE,
        prompt_detail_level=PromptDetailLevel.LOW,
    )
    return PromptRequestRead(
        id=REQUEST_ID, created_at=datetime(2026, 1, 1, tzinfo=UTC), **payload.model_dump()
    )


def context() -> ArchitectRunContext:
    return ArchitectRunContext(
        prompt_request_id=REQUEST_ID,
        workflow_run_id=WORKFLOW_ID,
        workflow_prompt_request_id=REQUEST_ID,
        agent_run_id=AGENT_ID,
        agent_workflow_run_id=WORKFLOW_ID,
    )


def protection() -> tuple[ProtectedSourceSpan, ...]:
    return (
        ProtectedSourceSpan(
            source="raw_prompt", kind="version", start=RAW.index("3.12"), end=RAW.index("3.12") + 4
        ),
    )


def text(index: int) -> str:
    return RAW + f"\nDraft {index}."


def author_wire(
    decision: str = "rewrite", *, draft: str | None = None, revision: bool = False
) -> LLMResponse:
    value: dict[str, Any] = dict(
        decision=decision,
        canonical_prompt=CanonicalPrompt.from_task(draft or text(1)).model_dump(mode="json")
        if decision == "rewrite"
        else None,
        changes=[NOTE] if decision == "rewrite" else [],
        questions=[QUESTION] if decision == "clarify" else [],
    )
    if revision:
        value["addressed_issue_keys"] = ["issue_001"] if decision == "rewrite" else []
    return response(value)


def response(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        provider="fake", finish_reason="complete", text=json.dumps(data, ensure_ascii=False)
    )


def critic_wire(
    scores: Mapping[str, int | None] | None = None,
    *,
    critical: bool = False,
    uncertain: bool = False,
    tag: str = "first",
) -> LLMResponse:
    values: dict[str, int | None] = {key: 10 for key, _, _ in CRITERIA}
    values.update(scores or {})
    if critical:
        values["constraints_and_scope"] = 6
    if uncertain:
        values["clarity"] = None
    ref = dict(source="raw_prompt", quote=RAW.strip())
    criteria: dict[str, Any] = {}
    issues: list[dict[str, Any]] = []
    for key, _, _ in CRITERIA:
        score = values[key]
        criteria[key] = dict(
            score=score,
            rationale="Scripted assessment.",
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
                    description=f"{tag}: supplied finding for {key}.",
                    recommendation=f"{tag}: restore the supplied constraint.",
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
                description="Unresolved external detail.",
                recommendation="Do not invent facts.",
                evidence=[],
            )
        )
    return response(
        dict(criteria=criteria, issues=issues, summary="Scripted, not a real quality judgment.")
    )


class RecordingFake(FakeLLMProvider):
    def __init__(
        self, script: list[LLMResponse | LLMErrorCode], block_at: int | None = None
    ) -> None:
        super().__init__(script)
        self.requests: list[LLMRequest] = []
        self.block_at = block_at
        self.entered = asyncio.Event()
        self.cleaned_up = False

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        value = await super().generate(request)
        if len(self.requests) == self.block_at:
            self.entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                self.cleaned_up = True
        return value


class RecordingGateway(LLMGateway):
    def __init__(self, provider: FakeLLMProvider, *, retries: int = 0, repair: int = 0) -> None:
        super().__init__(
            provider,
            policy=GatewayPolicy(max_retries=retries, json_repair_attempts=repair),
            clock=lambda: 0.0,
            sleep=self.no_wait,
            jitter=lambda: 0.0,
        )
        self.operations: list[str] = []

    @staticmethod
    async def no_wait(delay: float) -> None:
        pass

    async def generate_json[T: BaseModel](
        self, request: LLMRequest, output_type: type[T]
    ) -> StructuredResult[T]:
        self.operations.append(output_type.__name__)
        return await super().generate_json(request, output_type)


class RecordingArchitect(PromptArchitectAgent):
    def __init__(self, gateway: LLMGateway) -> None:
        super().__init__(gateway, ModelSettings(model="fake-a"))
        self.creates: list[PromptRequestCreate] = []
        self.revisions: list[RevisionInput] = []

    async def create(
        self,
        payload: PromptRequestCreate,
        *,
        request_id: str | None = None,
        legacy_generic: bool = False,
    ) -> ArchitectResult:
        self.creates.append(payload.model_copy(deep=True))
        return await super().create(payload, request_id=request_id, legacy_generic=legacy_generic)

    async def revise(
        self, revision_input: RevisionInput, *, request_id: str | None = None
    ) -> RevisionResult:
        self.revisions.append(revision_input)
        return await super().revise(revision_input, request_id=request_id)


class RecordingCritic(PromptCriticAgent):
    def __init__(self, gateway: LLMGateway) -> None:
        super().__init__(gateway, ModelSettings(model="fake-c"))
        self.inputs: list[CriticInput] = []

    async def evaluate(
        self, critic_input: CriticInput, *, request_id: str | None = None
    ) -> CriticResult:
        self.inputs.append(critic_input)
        return await super().evaluate(critic_input, request_id=request_id)


@dataclass
class Rig:
    author: RecordingArchitect
    critic: RecordingCritic
    provider: RecordingFake
    gateway: RecordingGateway


@asynccontextmanager
async def setup(
    script: list[LLMResponse | LLMErrorCode],
    *,
    block_at: int | None = None,
    retries: int = 0,
    repair: int = 0,
) -> AsyncIterator[Rig]:
    provider = RecordingFake(script, block_at)
    gateway = RecordingGateway(provider, retries=retries, repair=repair)
    try:
        yield Rig(RecordingArchitect(gateway), RecordingCritic(gateway), provider, gateway)
    finally:
        await provider.aclose()


def rounds(outcome: OptimizationOutcome) -> int:
    return len(outcome.history.rounds) if outcome.history is not None else 0


def assert_counts(
    rig: Rig, outcome: OptimizationOutcome, creates: int, revisions: int, critics: int
) -> None:
    assert outcome.counts.model_dump() == dict(
        architect_create=creates, architect_revise=revisions, critic_evaluate=critics
    )
    assert (len(rig.author.creates), len(rig.author.revisions), len(rig.critic.inputs)) == (
        creates,
        revisions,
        critics,
    )
    assert len(rig.gateway.operations) == creates + revisions + critics
    assert not rig.provider.closed


@pytest.mark.anyio
@pytest.mark.parametrize("qualified_round", [1, 2, 3])
async def test_qualification_stops_after_exact_gateway_operations(
    qualified_round: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    script: list[LLMResponse | LLMErrorCode] = [
        author_wire(),
        critic_wire() if qualified_round == 1 else critic_wire(S75),
    ]
    for number in range(2, qualified_round + 1):
        script.extend(
            [
                author_wire(draft=text(number), revision=True),
                critic_wire() if number == qualified_round else critic_wire(S80),
            ]
        )
    calculations: list[str] = []
    original_score = ScoringEngine.score

    def counted(value: CriticResult) -> ScoringResult:
        calculations.append(value.input_fingerprint)
        return original_score(value)

    monkeypatch.setattr(ScoringEngine, "score", counted)
    async with setup(script) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source(), context=context())
        calculation_count = len(calculations)
        assert (
            outcome.execution_status == "completed"
            and outcome.termination_reason == "quality_passed"
        )
        assert outcome.quality_status == "qualified" and rounds(outcome) == qualified_round
        assert_counts(rig, outcome, 1, qualified_round - 1, qualified_round)
        assert rig.provider.calls == 2 * qualified_round
        assert outcome.history is not None and outcome.selection == select_best_candidate(
            outcome.history
        )
        assert outcome.selection.round_number == qualified_round
        assert len(set(calculations)) == qualified_round
        # Revalidations verify existing arithmetic; these are not extra evaluations.
        assert calculation_count == {1: 7, 2: 22, 3: 43}[qualified_round]
        for number, completed in enumerate(outcome.history.rounds, start=1):
            assert completed.round_number == number
            if number > 1:
                assert completed.revision is not None
                assert completed.revision.candidate == completed.evaluated.candidate
                assert completed.baseline_round_number == number - 1


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [1, 2, 3])
async def test_configured_limit_is_a_maximum_without_qualification(limit: int) -> None:
    script: list[LLMResponse | LLMErrorCode] = [author_wire(), critic_wire(S75)]
    for number, scores in [(2, S80), (3, S82)]:
        script.extend(
            [author_wire(draft=text(number), revision=True), critic_wire(scores, tag=str(number))]
        )
    async with setup(script) as rig:
        outcome = await optimize_prompt(
            rig.author, rig.critic, source(), max_evaluation_rounds=limit
        )
        assert outcome.execution_status == "completed" and outcome.quality_status == "not_qualified"
        assert outcome.termination_reason == "evaluation_limit" and rounds(outcome) == limit
        assert_counts(rig, outcome, 1, limit - 1, limit)
        assert rig.provider.calls == 2 * limit


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, 4, -1, True, False, 1.0, "3", None])
async def test_invalid_evaluation_limits_are_rejected_before_dispatch(limit: Any) -> None:
    async with setup([]) as rig:
        with pytest.raises(LLMError) as caught:
            await optimize_prompt(rig.author, rig.critic, source(), max_evaluation_rounds=limit)
        assert caught.value.code == LLMErrorCode.INVALID_REQUEST
        assert rig.provider.calls == 0 and rig.gateway.operations == []


@pytest.mark.anyio
@pytest.mark.parametrize("timeout", [0, -1, True, False, float("nan"), float("inf"), "1", 10**1000])
async def test_invalid_deadlines_are_rejected(timeout: Any) -> None:
    async with setup([]) as rig:
        with pytest.raises(LLMError) as caught:
            await optimize_prompt(rig.author, rig.critic, source(), timeout_seconds=timeout)
        assert caught.value.code == LLMErrorCode.INVALID_REQUEST and rig.provider.calls == 0


@pytest.mark.anyio
@pytest.mark.parametrize("critical", [False, True])
async def test_earlier_best_survives_deterioration_and_repeated_attempt_stops(
    critical: bool,
) -> None:
    async with setup(
        [
            author_wire(),
            critic_wire(S82),
            author_wire(draft=text(2), revision=True),
            critic_wire(critical=True) if critical else critic_wire(S75),
        ]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert outcome.history is not None and outcome.selection is not None
        assert rounds(outcome) == 2 and outcome.selection.round_number == 1
        assert outcome.termination_reason == "repeated_revision_attempt"
        assert outcome.history.rounds[0].evaluated.candidate.rendered_prompt == text(1)
        assert_counts(rig, outcome, 1, 1, 2)
        assert rig.provider.calls == 4


@pytest.mark.anyio
async def test_each_revision_uses_selected_baselines_own_feedback() -> None:
    async with setup(
        [
            author_wire(),
            critic_wire(S75, tag="one"),
            author_wire(draft=text(2), revision=True),
            critic_wire(S80, tag="two"),
            author_wire(draft=text(3), revision=True),
            critic_wire(),
        ]
    ) as rig:
        outcome = await optimize_prompt(
            rig.author, rig.critic, source(), context=context(), protected_spans=protection()
        )
        assert rounds(outcome) == 3 and outcome.history is not None
        for index, payload in enumerate(rig.author.revisions):
            baseline = outcome.history.rounds[index].evaluated
            assert payload.baseline == baseline.candidate and payload.evaluation == baseline.critic
            assert (
                payload.next_evaluation_round == index + 2 and payload.context.agent_run_id is None
            )
            assert payload.protected_spans == protection()
            assert all(
                finding.issue.recommendation.startswith(("one", "two")[index])
                for finding in payload.feedback
            )


@pytest.mark.anyio
async def test_known_zero_is_not_conflated_with_unavailable_total() -> None:
    zeros: dict[str, int] = {key: 0 for key, _, _ in CRITERIA}
    async with setup(
        [
            author_wire(),
            critic_wire(dict(clarity=None, intent_fidelity=8)),
            author_wire(draft=text(2), revision=True),
            critic_wire(zeros),
        ]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source(), max_evaluation_rounds=2)
        assert outcome.history is not None and outcome.selection is not None
        assert outcome.history.rounds[0].evaluated.scoring.total_score is None
        assert outcome.history.rounds[1].evaluated.scoring.total_score == Decimal(0)
        assert outcome.selection.round_number == 2 and outcome.quality_status == "not_qualified"


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["preserve", "rewrite"])
async def test_creation_decisions_can_both_lead_to_justified_revision(decision: str) -> None:
    async with setup(
        [
            author_wire(decision),
            critic_wire(S75),
            author_wire(draft=text(2), revision=True),
            critic_wire(),
        ]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert rounds(outcome) == 2 and outcome.history is not None
        assert outcome.history.rounds[0].evaluated.candidate.decision == decision
        if decision == "preserve":
            assert outcome.history.rounds[0].evaluated.candidate.rendered_prompt == RAW
        assert_counts(rig, outcome, 1, 1, 2)


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["preserve", "identical", "clarify"])
async def test_revision_without_new_candidate_retains_one_completed_round(decision: str) -> None:
    revised = author_wire(
        "rewrite" if decision == "identical" else decision,
        draft=text(1) if decision == "identical" else None,
        revision=True,
    )
    async with setup([author_wire(), critic_wire(S75), revised]) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert rounds(outcome) == 1 and outcome.quality_status == "not_qualified"
        assert_counts(rig, outcome, 1, 1, 1)
        assert rig.provider.calls == 3
        if decision == "clarify":
            assert (
                outcome.execution_status == "clarification_required"
                and outcome.clarification_questions == (QUESTION,)
            )
        else:
            assert (
                outcome.execution_status == "completed"
                and outcome.termination_reason == "unchanged_baseline"
            )
            assert outcome.clarification_questions == ()


@pytest.mark.anyio
async def test_initial_clarification_has_no_history_selection_or_quality() -> None:
    async with setup([author_wire("clarify")]) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert outcome.execution_status == "clarification_required" and rounds(outcome) == 0
        assert (
            outcome.history is None and outcome.selection is None and outcome.quality_status is None
        )
        assert outcome.clarification_questions == (QUESTION,)
        assert_counts(rig, outcome, 1, 0, 0)


@pytest.mark.anyio
@pytest.mark.parametrize("uncertain", [True, False])
async def test_unassessable_without_supported_feedback_stops(uncertain: bool) -> None:
    async with setup(
        [
            author_wire(),
            critic_wire(uncertain=True) if uncertain else critic_wire(dict(clarity=None)),
        ]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert outcome.execution_status == "completed" and outcome.quality_status == "unassessable"
        assert outcome.termination_reason == "no_actionable_feedback" and rounds(outcome) == 1
        assert_counts(rig, outcome, 1, 0, 1)


@pytest.mark.anyio
async def test_supported_critical_finding_justifies_revision() -> None:
    async with setup(
        [
            author_wire(),
            critic_wire(critical=True),
            author_wire(draft=text(2), revision=True),
            critic_wire(),
        ]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert outcome.termination_reason == "quality_passed" and rounds(outcome) == 2
        assert rig.author.revisions[0].feedback[0].issue.severity == "critical"


@pytest.mark.anyio
@pytest.mark.parametrize("alternate_canonical", [False, True])
async def test_repeated_earlier_rendering_stops_before_third_critic(
    alternate_canonical: bool,
) -> None:
    repeated = author_wire(draft=text(1), revision=True)
    if alternate_canonical:
        assert repeated.text is not None
        data = json.loads(repeated.text)
        canonical = CanonicalPrompt.from_task(RAW.rstrip("\n")).model_copy(
            update={"context": "Draft 1."}
        )
        data["canonical_prompt"] = canonical.model_dump(mode="json")
        repeated = response(data)
    async with setup(
        [
            author_wire(),
            critic_wire(S75),
            author_wire(draft=text(2), revision=True),
            critic_wire(S80),
            repeated,
        ]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert rounds(outcome) == 2 and outcome.termination_reason == "already_evaluated"
        assert outcome.selection is not None and outcome.selection.round_number == 2
        assert_counts(rig, outcome, 1, 2, 2)
        assert rig.provider.calls == 5


@pytest.mark.anyio
@pytest.mark.parametrize("failure_at", [1, 2, 3, 4, 5, 6])
async def test_provider_failures_retain_only_prior_complete_evaluations(failure_at: int) -> None:
    script: list[LLMResponse | LLMErrorCode] = [
        author_wire(),
        critic_wire(S75),
        author_wire(draft=text(2), revision=True),
        critic_wire(S80),
        author_wire(draft=text(3), revision=True),
        critic_wire(),
    ]
    script[failure_at - 1] = LLMErrorCode.AUTHENTICATION
    async with setup(script) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert (
            outcome.execution_status == "failed"
            and outcome.termination_reason == "provider_failure"
        )
        assert outcome.error_code == LLMErrorCode.AUTHENTICATION
        expected_rounds = (failure_at - 1) // 2
        assert rounds(outcome) == expected_rounds
        expected_operation = (
            "architect_create"
            if failure_at == 1
            else "critic_evaluate"
            if failure_at % 2 == 0
            else "architect_revise"
        )
        assert outcome.failed_operation == expected_operation
        assert rig.provider.calls == failure_at and len(rig.gateway.operations) == failure_at
        if expected_rounds:
            assert (
                outcome.selection is not None and outcome.selection.round_number == expected_rounds
            )
            assert outcome.quality_status == "not_qualified"
        else:
            assert outcome.selection is None and outcome.quality_status is None


@pytest.mark.anyio
@pytest.mark.parametrize("round_number", [1, 2, 3])
@pytest.mark.parametrize("kind", ["evidence", "fingerprint", "scoring"])
async def test_invalid_completed_evaluation_never_enters_history(
    round_number: int, kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script: list[LLMResponse | LLMErrorCode] = [author_wire(), critic_wire(S75)]
    for number in [2, 3]:
        script.extend(
            [
                author_wire(draft=text(number), revision=True),
                critic_wire(S80) if number == 2 else critic_wire(),
            ]
        )
    if kind == "evidence":
        item = script[2 * round_number - 1]
        assert isinstance(item, LLMResponse) and item.text is not None
        data = json.loads(item.text)
        data["criteria"]["clarity"]["evidence"] = [dict(source="raw_prompt", quote=PRIVATE)]
        script[2 * round_number - 1] = response(data)
    async with setup(script) as rig:
        if kind == "fingerprint":
            original_evaluate = rig.critic.evaluate

            async def bad_fingerprint(
                payload: CriticInput, *, request_id: str | None = None
            ) -> CriticResult:
                result = await original_evaluate(payload, request_id=request_id)
                return (
                    result.model_copy(update={"input_fingerprint": "f" * 64})
                    if len(rig.critic.inputs) == round_number
                    else result
                )

            monkeypatch.setattr(rig.critic, "evaluate", bad_fingerprint)
        elif kind == "scoring":
            original_score = ScoringEngine.score
            seen: set[str] = set()
            target_fingerprint: str | None = None

            def invalid_score(value: CriticResult) -> ScoringResult:
                nonlocal target_fingerprint
                seen.add(value.input_fingerprint)
                if len(seen) == round_number and target_fingerprint is None:
                    target_fingerprint = value.input_fingerprint
                result = original_score(value)
                return (
                    result.model_copy(update={"total_score": Decimal(101)})
                    if value.input_fingerprint == target_fingerprint
                    else result
                )

            monkeypatch.setattr(ScoringEngine, "score", invalid_score)
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert (
            outcome.execution_status == "failed"
            and outcome.termination_reason == "validation_failure"
        )
        assert outcome.error_code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert rounds(outcome) == round_number - 1 and rig.provider.calls == round_number * 2
        assert outcome.counts.critic_evaluate == round_number
        assert PRIVATE not in repr(outcome)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "bare_candidate",
        "round",
        "baseline",
        "context",
        "protection",
        "eligible_keys",
        "addressed_keys",
        "policy",
        "candidate",
    ],
)
async def test_orchestrated_revision_requires_exact_available_provenance(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with setup(
        [author_wire(), critic_wire(S75), author_wire(draft=text(2), revision=True)]
    ) as rig:
        original = rig.author.revise

        async def tampered(
            payload: RevisionInput, *, request_id: str | None = None
        ) -> RevisionResult:
            result = await original(payload, request_id=request_id)
            assert isinstance(result, RevisedCandidate)
            if mutation == "bare_candidate":
                return cast(RevisionResult, cast(object, result.candidate))
            mutations: dict[str, dict[str, Any]] = {
                "round": {"next_evaluation_round": 3},
                "baseline": {
                    "baseline": result.baseline.model_copy(update={"changes": (PRIVATE,)})
                },
                "context": {
                    "context": result.context.model_copy(update={"prompt_request_id": UUID(int=99)})
                },
                "protection": {"protected_spans": ()},
                "eligible_keys": {
                    "eligible_issue_keys": ("issue_999",),
                    "addressed_issue_keys": ("issue_999",),
                },
                "addressed_keys": {"addressed_issue_keys": ()},
                "policy": {"revision_version": PRIVATE},
                "candidate": {
                    "candidate": result.candidate.model_copy(update={"rendered_prompt": PRIVATE})
                },
            }
            return result.model_copy(update=mutations[mutation])

        monkeypatch.setattr(rig.author, "revise", tampered)
        outcome = await optimize_prompt(
            rig.author, rig.critic, source(), context=context(), protected_spans=protection()
        )
        assert (
            outcome.execution_status == "failed"
            and outcome.termination_reason == "validation_failure"
        )
        assert (
            rounds(outcome) == 1
            and outcome.selection is not None
            and outcome.selection.round_number == 1
        )
        assert rig.provider.calls == 3 and len(rig.critic.inputs) == 1
        assert PRIVATE not in repr(outcome)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kind", ["protected_revision", "unknown_keys", "protected_initial", "initial_policy"]
)
async def test_real_agent_contract_violations_fail_before_next_critic(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script: list[LLMResponse | LLMErrorCode] = [
        author_wire(),
        critic_wire(S75),
        author_wire(draft=text(2), revision=True),
    ]
    if kind == "protected_revision":
        script[-1] = author_wire(draft="Explain the code without the version.", revision=True)
    elif kind == "unknown_keys":
        item = script[-1]
        assert isinstance(item, LLMResponse) and item.text is not None
        wire = json.loads(item.text)
        wire["addressed_issue_keys"] = ["issue_999"]
        script[-1] = response(wire)
    elif kind == "protected_initial":
        script[0] = author_wire(draft="Explain code without its explicit version.")
    async with setup(script) as rig:
        if kind == "initial_policy":
            original = rig.author.create

            async def invalid(
                payload: PromptRequestCreate,
                *,
                request_id: str | None = None,
                legacy_generic: bool = False,
            ) -> ArchitectResult:
                return (
                    await original(payload, request_id=request_id, legacy_generic=legacy_generic)
                ).model_copy(update={"instruction_version": PRIVATE})

            monkeypatch.setattr(rig.author, "create", invalid)
        outcome = await optimize_prompt(
            rig.author, rig.critic, source(), protected_spans=protection()
        )
        expected = 0 if kind in {"protected_initial", "initial_policy"} else 1
        assert (
            outcome.execution_status == "failed"
            and outcome.termination_reason == "validation_failure"
        )
        assert rounds(outcome) == expected and len(rig.critic.inputs) == expected
        assert rig.provider.calls == (1 if expected == 0 else 3)


@pytest.mark.anyio
@pytest.mark.parametrize("block_at", [1, 3, 4, 6])
async def test_overall_deadline_cancels_in_flight_operation_and_retains_history(
    block_at: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    timers: list[asyncio.Timeout] = []
    original_timeout = asyncio.timeout_at

    def recorded_timeout(deadline: float | None) -> asyncio.Timeout:
        timer = original_timeout(deadline)
        timers.append(timer)
        return timer

    monkeypatch.setattr(asyncio, "timeout_at", recorded_timeout)
    script: list[LLMResponse | LLMErrorCode] = [
        author_wire(),
        critic_wire(S75),
        author_wire(draft=text(2), revision=True),
        critic_wire(S80),
        author_wire(draft=text(3), revision=True),
        critic_wire(),
    ]
    async with setup(script, block_at=block_at) as rig:
        task = asyncio.create_task(
            optimize_prompt(rig.author, rig.critic, source(), timeout_seconds=30)
        )
        try:
            async with asyncio.timeout(5):
                await rig.provider.entered.wait()
            # Deterministically expire the one real workflow timer at the blocked dispatch.
            assert len(timers) == 1
            timers[0].reschedule(asyncio.get_running_loop().time())
            outcome = await task
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
        assert (
            outcome.execution_status == "failed"
            and outcome.termination_reason == "deadline_exceeded"
        )
        assert outcome.error_code == LLMErrorCode.DEADLINE_EXCEEDED
        assert rounds(outcome) == (block_at - 1) // 2
        assert (
            rig.provider.calls == block_at and rig.provider.cleaned_up and not rig.provider.closed
        )
        assert task.done()


@pytest.mark.anyio
@pytest.mark.parametrize("operation", ["architect_create", "architect_revise", "critic_evaluate"])
async def test_expired_monotonic_budget_prevents_dispatch(operation: Any) -> None:
    run = optimizer._Run(limit=3, deadline=10.0, clock=lambda: 10.0)
    with pytest.raises(LLMError) as caught:
        run.begin(operation)
    assert caught.value.code == LLMErrorCode.DEADLINE_EXCEEDED
    assert run.creates == run.revisions == run.critics == 0


@pytest.mark.anyio
@pytest.mark.parametrize("block_at", [1, 3, 4])
async def test_external_cancellation_propagates_unchanged_without_detached_work(
    block_at: int,
) -> None:
    script: list[LLMResponse | LLMErrorCode] = [
        author_wire(),
        critic_wire(S75),
        author_wire(draft=text(2), revision=True),
        critic_wire(),
    ]
    async with setup(script, block_at=block_at) as rig:
        previous = asyncio.all_tasks()
        task = asyncio.create_task(
            optimize_prompt(rig.author, rig.critic, source(), timeout_seconds=30)
        )
        try:
            async with asyncio.timeout(5):
                await rig.provider.entered.wait()
            task.cancel("external cancellation")
            with pytest.raises(asyncio.CancelledError) as caught:
                await task
            assert caught.value.args == ("external cancellation",)
            assert rig.provider.cleaned_up and not rig.provider.closed
            assert rig.provider.calls == block_at
            assert not (asyncio.all_tasks() - previous)
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task


@pytest.mark.anyio
@pytest.mark.parametrize("exception", [RuntimeError, ValueError, TimeoutError])
async def test_programming_errors_are_not_disguised_as_quality_or_provider_failures(
    exception: type[Exception], monkeypatch: pytest.MonkeyPatch
) -> None:
    failure = exception("Unexpected test-double failure")
    async with setup([]) as rig:

        async def broken(
            payload: PromptRequestCreate,
            *,
            request_id: str | None = None,
            legacy_generic: bool = False,
        ) -> ArchitectResult:
            raise failure

        monkeypatch.setattr(rig.author, "create", broken)
        with pytest.raises(exception) as caught:
            await optimize_prompt(rig.author, rig.critic, source())
        assert caught.value is failure and not rig.provider.closed


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["retry", "repair", "retry_exhausted", "repair_exhausted"])
async def test_gateway_attempts_do_not_add_workflow_operations_or_evaluations(kind: str) -> None:
    malformed = LLMResponse(provider="fake", finish_reason="complete", text="not JSON")
    script: list[LLMResponse | LLMErrorCode] = [author_wire(), critic_wire()]
    if kind == "retry":
        script.insert(0, LLMErrorCode.RATE_LIMIT)
    elif kind == "repair":
        script.insert(0, malformed)
    elif kind == "retry_exhausted":
        script = [LLMErrorCode.RATE_LIMIT, LLMErrorCode.RATE_LIMIT]
    else:
        script = [malformed, malformed]
    async with setup(script, retries=1, repair=1) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        success = kind in {"retry", "repair"}
        assert rounds(outcome) == (1 if success else 0)
        assert_counts(rig, outcome, 1, 0, 1 if success else 0)
        assert rig.provider.calls == (3 if success else 2)
        assert outcome.execution_status == ("completed" if success else "failed")
        if success:
            assert outcome.history is not None
            metadata = outcome.history.rounds[0].evaluated.candidate.metadata
            assert metadata.attempts == 2
            assert metadata.retries == (1 if kind == "retry" else 0)
            assert metadata.repair_attempted == (kind == "repair")


@pytest.mark.anyio
async def test_critic_is_blind_and_revision_receives_only_supported_feedback_projection() -> None:
    async with setup(
        [author_wire(), critic_wire(S75), author_wire(draft=text(2), revision=True), critic_wire()]
    ) as rig:
        outcome = await optimize_prompt(
            rig.author,
            rig.critic,
            source(),
            context=context(),
            protected_spans=protection(),
            request_id="optimizer-test",
        )
        assert outcome.history is not None
        for request in rig.provider.requests:
            data = json.loads(request.messages[1].content)
            assert request.request_id == "optimizer-test"
            if request.settings.model == "fake-c":
                assert set(data) == {
                    "original_request",
                    "rendered_prompt",
                    "target_mode",
                    "renderer_name",
                    "renderer_version",
                }
                assert (
                    "id" not in data["original_request"]
                    and "created_at" not in data["original_request"]
                )
                assert NOTE not in request.messages[1].content
                assert data["original_request"]["raw_prompt"] == RAW
                assert data["original_request"]["prompt_detail_level"] == "low"
            elif "feedback" in data:
                assert set(data) == {
                    "original_request",
                    "baseline_prompt",
                    "feedback",
                    "protected_spans",
                }
                assert all(
                    set(finding)
                    == {
                        "issue_key",
                        "criterion",
                        "code",
                        "severity",
                        "description",
                        "recommendation",
                        "evidence",
                    }
                    for finding in data["feedback"]
                )
                assert NOTE not in request.messages[1].content
        assert [item.rendered_prompt for item in rig.critic.inputs] == [text(1), text(2)]


@pytest.mark.anyio
async def test_full_run_has_no_database_or_network_activity_and_does_not_mutate_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request, binding, spans = source(), context(), protection()
    before = (
        request.model_dump_json(),
        binding.model_dump_json(),
        tuple(span.model_dump_json() for span in spans),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Optimizer accessed a database or network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    for name in ("execute", "connection", "add", "flush", "commit", "delete"):
        monkeypatch.setattr(AsyncSession, name, forbidden)
    async with setup(
        [author_wire(), critic_wire(S75), author_wire(draft=text(2), revision=True), critic_wire()]
    ) as rig:
        result = await optimize_prompt(
            rig.author, rig.critic, request, context=binding, protected_spans=spans
        )
        assert result.termination_reason == "quality_passed"
        assert before == (
            request.model_dump_json(),
            binding.model_dump_json(),
            tuple(span.model_dump_json() for span in spans),
        )


@pytest.mark.anyio
async def test_mutable_caller_request_is_pinned_before_first_await(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source()
    async with setup([author_wire("preserve"), critic_wire()]) as rig:
        original = rig.author.create

        async def mutate_caller(
            payload: PromptRequestCreate,
            *,
            request_id: str | None = None,
            legacy_generic: bool = False,
        ) -> ArchitectResult:
            request.raw_prompt = PRIVATE + " changed by test caller"
            return await original(payload, request_id=request_id, legacy_generic=legacy_generic)

        monkeypatch.setattr(rig.author, "create", mutate_caller)
        outcome = await optimize_prompt(rig.author, rig.critic, request)
        assert outcome.history is not None and outcome.history.original_request.raw_prompt == RAW
        assert rig.critic.inputs[0].original_request.raw_prompt == RAW


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["source", "context", "span", "span_type"])
async def test_invalid_inputs_return_safe_validation_failure_before_agent_dispatch(
    kind: str,
) -> None:
    request, binding, spans = source(), context(), protection()
    if kind == "source":
        request = request.model_copy(update={"raw_prompt": False})
    elif kind == "context":
        binding = binding.model_copy(update={"prompt_request_id": UUID(int=99)})
    elif kind == "span":
        spans = (spans[0].model_copy(update={"end": 9999}),)
    else:
        spans = cast(tuple[ProtectedSourceSpan, ...], cast(object, (PRIVATE,)))
    async with setup([]) as rig:
        outcome = await optimize_prompt(
            rig.author, rig.critic, request, context=binding, protected_spans=spans
        )
        assert (
            outcome.execution_status == "failed"
            and outcome.termination_reason == "validation_failure"
        )
        assert (
            outcome.failed_operation == "input_validation"
            and outcome.error_code == LLMErrorCode.INVALID_REQUEST
        )
        assert (
            outcome.history is None
            and rig.provider.calls == 0
            and outcome.counts.architect_create == 0
        )
        assert PRIVATE not in repr(outcome)


@pytest.mark.anyio
async def test_borrowed_gateway_and_provider_stay_usable_until_caller_closes_them() -> None:
    async with setup([author_wire(), critic_wire(), author_wire("preserve")]) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert outcome.termination_reason == "quality_passed" and not rig.provider.closed
        result = await rig.author.create(PromptRequestCreate(raw_prompt=RAW))
        assert result.rendered_prompt == RAW and rig.provider.calls == 3
    assert rig.provider.closed


@pytest.mark.anyio
async def test_safe_diagnostics_hide_prompts_evidence_questions_and_fingerprints() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    async with setup(
        [author_wire(), critic_wire(S75), author_wire("clarify", revision=True)]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source(), context=context())
        assert outcome.history is not None and outcome.selection is not None
        output = repr(outcome) + stream.getvalue()
        for private in [PRIVATE, RAW, NOTE, QUESTION, outcome.selection.input_fingerprint]:
            assert private not in output
        assert PRIVATE in outcome.model_dump_json()  # Explicit serialization is restricted.
        assert OptimizationOutcome.model_validate_json(outcome.model_dump_json()) == outcome
        invalid = outcome.model_copy(update={"quality_status": PRIVATE})
        with pytest.raises(ValidationError) as caught:
            OptimizationOutcome.model_validate(invalid)
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.anyio
async def test_identical_fake_scripts_produce_identical_outcomes() -> None:
    results: list[OptimizationOutcome] = []
    for _ in range(2):
        async with setup(
            [
                author_wire(),
                critic_wire(S75),
                author_wire(draft=text(2), revision=True),
                critic_wire(),
            ]
        ) as rig:
            results.append(
                await optimize_prompt(rig.author, rig.critic, source(), context=context())
            )
    assert results[0] == results[1]


@pytest.mark.anyio
@pytest.mark.parametrize("gate", ["intent", "critical", "assessability"])
async def test_numeric_score_cannot_bypass_any_quality_gate(gate: str) -> None:
    evaluation = (
        critic_wire(dict(intent_fidelity=6))
        if gate == "intent"
        else critic_wire(critical=True)
        if gate == "critical"
        else critic_wire(dict(intent_fidelity=8, clarity=None))
    )
    async with setup([author_wire(), evaluation, author_wire("preserve", revision=True)]) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert (
            outcome.execution_status == "completed"
            and outcome.termination_reason == "unchanged_baseline"
        )
        assert outcome.quality_status != "qualified" and rounds(outcome) == 1
        assert outcome.history is not None
        score = outcome.history.rounds[0].evaluated.scoring
        assert score.total_score is None or score.total_score >= 85
        assert not score.passed


@pytest.mark.anyio
async def test_unchanged_revision_counts_include_only_one_new_score_and_existing_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calculations: list[str] = []
    original = ScoringEngine.score

    def counted(value: CriticResult) -> ScoringResult:
        calculations.append(value.input_fingerprint)
        return original(value)

    monkeypatch.setattr(ScoringEngine, "score", counted)
    async with setup(
        [author_wire(), critic_wire(S75), author_wire("preserve", revision=True)]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert rounds(outcome) == 1 and len(set(calculations)) == 1 and len(calculations) == 13
        assert_counts(rig, outcome, 1, 1, 1)
        assert rig.provider.calls == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "quality",
        "selection",
        "missing_revision",
        "counts",
        "counts_bool",
        "fingerprint",
        "policy",
        "limit",
        "questions",
        "failure_code",
    ],
)
async def test_outcome_revalidates_nested_state_and_separates_execution_from_quality(
    mutation: str,
) -> None:
    async with setup(
        [author_wire(), critic_wire(S75), author_wire(draft=text(2), revision=True), critic_wire()]
    ) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert outcome.history is not None and outcome.selection is not None
        updates: dict[str, Any] = {}
        if mutation == "quality":
            updates["quality_status"] = "not_qualified"
        elif mutation == "selection":
            updates["selection"] = outcome.selection.model_copy(update={"round_number": 1})
        elif mutation == "missing_revision":
            updates["history"] = outcome.history.model_copy(
                update={
                    "rounds": (
                        outcome.history.rounds[0],
                        outcome.history.rounds[1].model_copy(update={"revision": None}),
                    )
                }
            )
        elif mutation in {"counts", "counts_bool"}:
            updates["counts"] = outcome.counts.model_copy(
                update={"critic_evaluate": 1 if mutation == "counts" else True}
            )
        elif mutation == "fingerprint":
            updates["selection"] = outcome.selection.model_copy(
                update={"input_fingerprint": "f" * 64}
            )
        elif mutation == "policy":
            updates["progress_policy_version"] = PRIVATE
        elif mutation == "limit":
            updates["max_evaluation_rounds"] = True
        elif mutation == "questions":
            updates["clarification_questions"] = (PRIVATE,)
        else:
            updates.update(
                execution_status="failed",
                termination_reason="deadline_exceeded",
                failed_operation="validation",
                error_code=LLMErrorCode.AUTHENTICATION,
            )
        with pytest.raises(ValidationError) as caught:
            OptimizationOutcome.model_validate(outcome.model_copy(update=updates))
        assert PRIVATE not in str(caught.value)
        with pytest.raises(ValidationError):
            outcome.execution_status = "failed"


@pytest.mark.anyio
@pytest.mark.parametrize("state", ["completed", "failed", "clarification"])
async def test_outcome_json_round_trip_handles_history_or_its_absence(state: str) -> None:
    script: list[LLMResponse | LLMErrorCode] = (
        [author_wire("clarify")]
        if state == "clarification"
        else [author_wire(), critic_wire(S75), LLMErrorCode.AUTHENTICATION]
        if state == "failed"
        else [
            author_wire(),
            critic_wire(S75),
            author_wire(draft=text(2), revision=True),
            critic_wire(),
        ]
    )
    async with setup(script) as rig:
        outcome = await optimize_prompt(rig.author, rig.critic, source())
        assert OptimizationOutcome.model_validate_json(outcome.model_dump_json()) == outcome


@pytest.mark.anyio
async def test_defensive_dispatch_counter_cannot_start_a_fourth_critic() -> None:
    run = optimizer._Run(limit=3, deadline=None, clock=lambda: 0.0, critics=3)
    with pytest.raises(LLMError) as caught:
        run.begin("critic_evaluate")
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST and run.critics == 3
