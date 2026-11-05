"""Offline single-pass execution/binding tests, not real-model quality evaluation."""

import asyncio
import hashlib
import io
import json
import socket
import traceback
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.architect_schemas import ArchitectDecision
from app.agents.critic_instructions import SYSTEM_INSTRUCTION as CRITIC_INSTRUCTION
from app.agents.critic_schemas import CriticResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMRequest, LLMResponse, ModelSettings, StructuredResult
from app.models.enums import PromptDetailLevel, TargetMode
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate, run_architect_critic_pass
from app.modules.prompts.architect_integration import (
    ArchitectRunContext,
    ClarificationRequired,
    CriticInput,
    critic_input_fingerprint,
    make_critic_input,
)
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead
from app.scoring.engine import ScoringEngine
from app.scoring.rubric import CRITERIA
from app.scoring.schemas import ScoringResult

PRIVATE = "PRIVATE_SINGLE_PASS_SENTINEL"
NOTE = "PRIVATE_ARCHITECT_EXPLANATION_SENTINEL"
QUESTION = "Which source should be translated?"
REQUEST_ID, WORKFLOW_ID, AGENT_ID, OTHER_ID = (UUID(int=value) for value in range(1, 5))


def source(
    raw: str = "  Translate 'hello' into Turkish. Return only the translation.\n",
) -> PromptRequestRead:
    payload = PromptRequestCreate(
        raw_prompt=raw,
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


def response(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        provider="fake", finish_reason="complete", text=json.dumps(data, ensure_ascii=False)
    )


def architect_wire(decision: ArchitectDecision, raw: str) -> dict[str, Any]:
    return dict(
        decision=decision,
        canonical_prompt=CanonicalPrompt.from_task(raw + "\n").model_dump(mode="json")
        if decision == "rewrite"
        else None,
        changes=[NOTE] if decision == "rewrite" else [],
        questions=[QUESTION] if decision == "clarify" else [],
    )


def critic_wire(raw: str, status: str = "qualified") -> dict[str, Any]:
    ref = dict(source="raw_prompt", quote=raw.strip())
    data: dict[str, Any] = dict(
        criteria={
            key: dict(score=10, rationale="Scripted supported assessment.", evidence=[ref])
            for key, _, _ in CRITERIA
        },
        issues=[],
        summary="Scripted evaluation; not proof of model quality.",
    )
    if status == "unassessable":
        data["criteria"]["clarity"].update(score=None, evidence=[])
    elif status == "not_qualified":
        data["criteria"]["constraints_and_scope"]["score"] = 6
        data["issues"] = [
            dict(
                criterion="constraints_and_scope",
                code="missing_requirement",
                status="supported",
                severity="critical",
                description="A scripted hard-constraint failure.",
                recommendation="Restore the explicit source constraint.",
                evidence=[ref],
            )
        ]
    return data


class RecordingFake(FakeLLMProvider):
    def __init__(self, script: list[LLMResponse | LLMErrorCode]) -> None:
        super().__init__(script)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await super().generate(request)


class RecordingGateway(LLMGateway):
    def __init__(self, provider: FakeLLMProvider, *, repair: int = 0, retries: int = 0) -> None:
        super().__init__(
            provider,
            policy=GatewayPolicy(max_retries=retries, json_repair_attempts=repair),
            clock=lambda: 0.0,
            sleep=self.no_wait,
        )
        self.operations = 0

    @staticmethod
    async def no_wait(delay: float) -> None:
        pass

    async def generate_json[T: BaseModel](
        self, request: LLMRequest, output_type: type[T]
    ) -> StructuredResult[T]:
        self.operations += 1
        return await super().generate_json(request, output_type)


def agents(
    request: PromptRequestRead, decision: ArchitectDecision = "preserve", status: str = "qualified"
) -> tuple[PromptArchitectAgent, PromptCriticAgent, RecordingFake, RecordingGateway]:
    provider = RecordingFake(
        [
            response(architect_wire(decision, request.raw_prompt)),
            response(critic_wire(request.raw_prompt, status)),
        ]
    )
    gateway = RecordingGateway(provider)
    return (
        PromptArchitectAgent(gateway, ModelSettings(model="fake-architect-v1")),
        PromptCriticAgent(gateway, ModelSettings(model="fake-critic-v1")),
        provider,
        gateway,
    )


class ReturningCritic(PromptCriticAgent):
    def __init__(self, result: CriticResult) -> None:
        self.result = result
        self.calls = 0

    async def evaluate(
        self, critic_input: CriticInput, *, request_id: str | None = None
    ) -> CriticResult:
        self.calls += 1
        return self.result


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "decision,status",
    [
        ("preserve", "qualified"),
        ("rewrite", "qualified"),
        ("rewrite", "not_qualified"),
        ("preserve", "not_qualified"),
        ("preserve", "unassessable"),
    ],
)
@pytest.mark.parametrize(
    "raw",
    [
        "  Translate 'hello' into Turkish. Return only the translation.\n",
        "  'hello' ifadesini Türkçeye çevir; sadece çeviriyi döndür.\n",
    ],
)
async def test_complete_single_pass_paths_and_one_scoring_operation(
    decision: ArchitectDecision, status: str, raw: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = source(raw)
    before = request.model_dump()
    architect, critic, provider, gateway = agents(request, decision, status)
    original_score = ScoringEngine.score
    scores: list[CriticResult] = []

    def counted(result: CriticResult) -> ScoringResult:
        scores.append(result)
        return original_score(result)

    monkeypatch.setattr(ScoringEngine, "score", counted)
    try:
        outcome = await run_architect_critic_pass(
            architect, critic, request, context=context(), request_id="pass-1"
        )
        assert isinstance(outcome, EvaluatedCandidate)
        assert outcome.status == "evaluated" and outcome.scoring.status == status
        assert len(scores) == 1 and scores[0] == outcome.critic
        assert gateway.operations == provider.calls == 2 and not provider.closed
    finally:
        await provider.aclose()
    assert request.model_dump() == before
    candidate = outcome.candidate
    assert candidate.context == context() and candidate.original_request.id == REQUEST_ID
    assert candidate.original_request.raw_prompt == raw
    assert candidate.rendered_prompt == GenericPromptRenderer().render(candidate.canonical_prompt)
    if decision == "preserve":
        assert candidate.rendered_prompt == raw and candidate.changes == ()
    else:
        assert candidate.changes == (NOTE,)
    sent = json.loads(provider.requests[1].messages[1].content)
    expected = make_critic_input(candidate).model_dump(
        mode="json", exclude={"original_request": {"id", "created_at"}}
    )
    assert sent == expected
    assert sent["original_request"]["output_language"] == "tr"
    assert sent["original_request"]["prompt_detail_level"] == "low"
    assert sent["target_mode"] == TargetMode.GOOGLE.value
    assert NOTE not in provider.requests[1].messages[1].content
    assert "decision" not in sent and "metadata" not in sent and "scoring" not in sent
    assert len(provider.requests[1].messages) == 2
    assert provider.requests[1].messages[0].content == CRITIC_INSTRUCTION
    assert provider.requests[0].settings.model == "fake-architect-v1"
    assert provider.requests[1].settings.model == "fake-critic-v1"
    assert outcome.critic.input_fingerprint == critic_input_fingerprint(
        make_critic_input(candidate)
    )
    assert outcome.critic.evaluated_target_mode == candidate.original_request.target_mode
    assert outcome.critic.rubric_version == outcome.scoring.rubric_version == "2.0.0"
    assert candidate.metadata.request_id == outcome.critic.metadata.request_id == "pass-1"
    if status == "qualified":
        assert outcome.scoring.total_score == Decimal(100)
    elif status == "not_qualified":
        assert outcome.scoring.total_score == Decimal(92)
    else:
        assert outcome.scoring.total_score is None and not outcome.scoring.passed


@pytest.mark.anyio
async def test_clarification_returns_existing_shape_without_critic_or_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source()
    architect, critic, provider, gateway = agents(request, "clarify")

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Clarification called Critic/scoring")

    monkeypatch.setattr(critic, "evaluate", forbidden)
    monkeypatch.setattr(ScoringEngine, "score", forbidden)
    try:
        outcome = await run_architect_critic_pass(architect, critic, request, context=context())
        assert isinstance(outcome, ClarificationRequired)
        assert outcome.questions == (QUESTION,)
        assert outcome.original_request.id == REQUEST_ID and outcome.context == context()
        assert gateway.operations == provider.calls == 1
        assert not {"candidate", "critic", "scoring", "version_id", "rendered_prompt"} & set(
            type(outcome).model_fields
        )
        assert not provider.closed
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {"workflow_prompt_request_id": OTHER_ID},
        {"agent_workflow_run_id": OTHER_ID},
        {"prompt_request_id": OTHER_ID},
        {"workflow_run_id": None},
    ],
)
async def test_corrupted_ancestry_fails_before_either_provider(updates: dict[str, Any]) -> None:
    request = source()
    architect, critic, provider, gateway = agents(request)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(
                architect, critic, request, context=context().model_copy(update=updates)
            )
        assert (
            caught.value.code == LLMErrorCode.INVALID_REQUEST and caught.value.__context__ is None
        )
        assert gateway.operations == provider.calls == 0
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_invalid_architect_input_fails_before_any_provider() -> None:
    request = source()
    architect, critic, provider, gateway = agents(request)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(
                architect, critic, request.model_copy(update={"raw_prompt": " "})
            )
        assert caught.value.code == LLMErrorCode.INVALID_REQUEST and gateway.operations == 0
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["architect", "critic"])
@pytest.mark.parametrize("code", [LLMErrorCode.TIMEOUT, LLMErrorCode.AUTHENTICATION])
async def test_gateway_failures_propagate_without_partial_outcome_or_pass_retry(
    stage: str, code: LLMErrorCode
) -> None:
    request = source()
    script: list[LLMResponse | LLMErrorCode] = (
        [code]
        if stage == "architect"
        else [response(architect_wire("preserve", request.raw_prompt)), code]
    )
    provider = RecordingFake(script)
    gateway = RecordingGateway(provider)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(
                PromptArchitectAgent(gateway, ModelSettings(model="fake-a")),
                PromptCriticAgent(gateway, ModelSettings(model="fake-c")),
                request,
            )
        assert caught.value.code == code
        assert gateway.operations == provider.calls == (1 if stage == "architect" else 2)
        assert not provider.closed
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_fabricated_evidence_fails_without_a_scoring_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source()
    data = critic_wire(request.raw_prompt)
    data["criteria"]["clarity"]["evidence"][0] = dict(source="raw_prompt", quote="fabricated")
    provider = RecordingFake(
        [response(architect_wire("preserve", request.raw_prompt)), response(data)]
    )
    gateway = RecordingGateway(provider)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid Critic evidence reached scoring")

    monkeypatch.setattr(ScoringEngine, "score", forbidden)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(
                PromptArchitectAgent(gateway, ModelSettings(model="fake-a")),
                PromptCriticAgent(gateway, ModelSettings(model="fake-c")),
                request,
            )
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert gateway.operations == provider.calls == 2
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "fingerprint",
        "target",
        "renderer",
        "version",
        "nested_score",
        "nested_evidence",
        "nested_metadata",
    ],
)
async def test_tampered_critic_handoff_rejected_before_scoring(
    mutation: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = source()
    architect, critic, provider, _ = agents(request)
    try:
        original = await run_architect_critic_pass(architect, critic, request)
        assert isinstance(original, EvaluatedCandidate)
    finally:
        await provider.aclose()
    evaluation = original.critic
    if mutation == "fingerprint":
        evaluation = evaluation.model_copy(update={"input_fingerprint": "b" * 64})
    elif mutation == "target":
        evaluation = evaluation.model_copy(update={"evaluated_target_mode": TargetMode.OPENAI})
    elif mutation == "renderer":
        evaluation = evaluation.model_copy(update={"evaluated_renderer_version": "999"})
    elif mutation == "version":
        evaluation = evaluation.model_copy(update={"rubric_version": "999"})
    elif mutation == "nested_metadata":
        evaluation = evaluation.model_copy(
            update={"metadata": evaluation.metadata.model_copy(update={"attempts": PRIVATE})}
        )
    else:
        assessment = evaluation.output.criteria.clarity
        if mutation == "nested_score":
            assessment = assessment.model_copy(update={"score": 11})
        else:
            assessment = assessment.model_copy(
                update={"evidence": (assessment.evidence[0].model_copy(update={"quote": PRIVATE}),)}
            )
        evaluation = evaluation.model_copy(
            update={
                "output": evaluation.output.model_copy(
                    update={
                        "criteria": evaluation.output.criteria.model_copy(
                            update={"clarity": assessment}
                        )
                    }
                )
            }
        )
    replay, _, replay_provider, replay_gateway = agents(request)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid Critic handoff reached scoring")

    monkeypatch.setattr(ScoringEngine, "score", forbidden)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(replay, ReturningCritic(evaluation), request)
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert caught.value.__context__ is None
        assert replay_gateway.operations == replay_provider.calls == 1
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    finally:
        await replay_provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [{"gates": None}, {"status": "qualified", "total_score": None}, {"rubric_version": "999"}],
)
async def test_invalid_scorer_output_is_rejected(
    updates: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    request = source()
    architect, critic, provider, gateway = agents(request)
    original = ScoringEngine.score
    calls = 0

    def malformed(result: CriticResult) -> ScoringResult:
        nonlocal calls
        calls += 1
        return original(result).model_copy(update=updates)

    monkeypatch.setattr(ScoringEngine, "score", malformed)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(architect, critic, request)
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert caught.value.__context__ is None and calls == 1
        assert gateway.operations == provider.calls == 2
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_scorer_failure_propagates_without_a_returned_outcome(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source()
    architect, critic, provider, gateway = agents(request)
    failure = LLMError(LLMErrorCode.INVALID_REQUEST)

    def fail(result: CriticResult) -> ScoringResult:
        raise failure

    monkeypatch.setattr(ScoringEngine, "score", fail)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(architect, critic, request)
        assert caught.value is failure and gateway.operations == provider.calls == 2
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("stage", ["architect", "critic"])
async def test_cancellation_propagates_and_providers_remain_borrowed(stage: str) -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()
    request = source()

    class WaitingFake(RecordingFake):
        async def generate(self, value: LLMRequest) -> LLMResponse:
            self.requests.append(value)
            if stage == "architect" or len(self.requests) == 2:
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            # Consume without recording a second time.
            return await FakeLLMProvider.generate(self, value)

    provider = WaitingFake([response(architect_wire("preserve", request.raw_prompt))])
    gateway = RecordingGateway(provider)
    task = asyncio.create_task(
        run_architect_critic_pass(
            PromptArchitectAgent(gateway, ModelSettings(model="fake-a")),
            PromptCriticAgent(gateway, ModelSettings(model="fake-c")),
            request,
        )
    )
    await entered.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and not provider.closed
        assert gateway.operations == (1 if stage == "architect" else 2)
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "stage,kind",
    [(stage, kind) for stage in ("architect", "critic") for kind in ("retry", "repair")],
)
async def test_gateway_retry_or_repair_does_not_repeat_the_pass(stage: str, kind: str) -> None:
    request = source()
    initial: LLMResponse | LLMErrorCode = (
        LLMErrorCode.TIMEOUT
        if kind == "retry"
        else LLMResponse(provider="fake", finish_reason="complete", text="bad JSON")
    )
    script: list[LLMResponse | LLMErrorCode] = [
        response(architect_wire("preserve", request.raw_prompt)),
        response(critic_wire(request.raw_prompt)),
    ]
    script.insert(0 if stage == "architect" else 1, initial)
    provider = RecordingFake(script)
    gateway = RecordingGateway(provider, repair=1, retries=1)
    try:
        outcome = await run_architect_critic_pass(
            PromptArchitectAgent(gateway, ModelSettings(model="fake-a")),
            PromptCriticAgent(gateway, ModelSettings(model="fake-c")),
            request,
        )
        assert isinstance(outcome, EvaluatedCandidate)
        assert gateway.operations == 2 and provider.calls == 3
        metadata = outcome.candidate.metadata if stage == "architect" else outcome.critic.metadata
        assert metadata.attempts == 2 and metadata.repair_attempted == (kind == "repair")
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_no_database_live_provider_or_unexpected_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Single pass attempted forbidden side effects")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    monkeypatch.setattr("app.llm.composition.create_provider", forbidden)
    for name in ("add", "execute", "flush", "commit", "delete"):
        monkeypatch.setattr(AsyncSession, name, forbidden)
    request = source()
    architect, critic, provider, gateway = agents(request)
    try:
        outcome = await run_architect_critic_pass(architect, critic, request)
        assert isinstance(outcome, EvaluatedCandidate) and gateway.operations == 2
        assert outcome.candidate.context.workflow_run_id is None
        assert outcome.candidate.context.agent_run_id is None
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_result_revalidation_checks_nested_binding_and_supplied_scoring() -> None:
    request = source()
    architect, critic, provider, _ = agents(request)
    try:
        result = await run_architect_critic_pass(architect, critic, request)
        assert isinstance(result, EvaluatedCandidate)
    finally:
        await provider.aclose()
    assert EvaluatedCandidate.model_validate(result) == result
    assert EvaluatedCandidate.model_validate_json(result.model_dump_json()) == result
    before = result.model_dump()
    for tampered in [
        result.model_copy(
            update={"scoring": result.scoring.model_copy(update={"total_score": Decimal(99)})}
        ),
        result.model_copy(
            update={
                "candidate": result.candidate.model_copy(
                    update={
                        "original_request": result.candidate.original_request.model_copy(
                            update={"id": OTHER_ID}
                        )
                    }
                )
            }
        ),
        result.model_copy(
            update={"candidate": result.candidate.model_copy(update={"rendered_prompt": PRIVATE})}
        ),
    ]:
        with pytest.raises(ValidationError):
            EvaluatedCandidate.model_validate(tampered)
    assert result.model_dump() == before


@pytest.mark.anyio
async def test_fingerprint_uses_unchanged_algorithm_and_binds_each_input_dimension() -> None:
    request = source()
    architect, critic, provider, _ = agents(request)
    try:
        outcome = await run_architect_critic_pass(architect, critic, request)
        assert isinstance(outcome, EvaluatedCandidate)
    finally:
        await provider.aclose()
    payload = make_critic_input(outcome.candidate)
    expected = hashlib.sha256(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    assert expected == critic_input_fingerprint(payload) == outcome.critic.input_fingerprint
    assert (
        critic_input_fingerprint(
            payload.model_copy(update={"rendered_prompt": payload.rendered_prompt + " "})
        )
        != expected
    )
    for key, value in [
        ("id", OTHER_ID),
        ("output_language", "en"),
        ("user_context", "changed"),
        ("prompt_detail_level", PromptDetailLevel.HIGH),
    ]:
        altered = payload.model_copy(
            update={"original_request": payload.original_request.model_copy(update={key: value})}
        )
        assert critic_input_fingerprint(altered) != expected


@pytest.mark.anyio
async def test_sensitive_content_is_absent_from_outcome_repr_logs_and_safe_errors() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    request = source(PRIVATE)
    architect, critic, provider, _ = agents(request, "rewrite")
    try:
        outcome = await run_architect_critic_pass(architect, critic, request)
        assert isinstance(outcome, EvaluatedCandidate)
    finally:
        await provider.aclose()
    assert PRIVATE not in repr(outcome) + stream.getvalue()
    assert NOTE not in repr(outcome) + stream.getvalue()
    assert PRIVATE in outcome.model_dump_json()  # Explicit dumps remain private.
    replay, _, replay_provider, _ = agents(request)
    bad = outcome.critic.model_copy(update={"input_fingerprint": PRIVATE})
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(replay, ReturningCritic(bad), request)
        assert caught.value.__context__ is None
        assert PRIVATE not in "".join(traceback.format_exception(caught.value)) + stream.getvalue()
    finally:
        await replay_provider.aclose()


@pytest.mark.anyio
async def test_separate_injected_gateways_keep_histories_and_telemetry_independent() -> None:
    request = source()
    architect_provider = RecordingFake([response(architect_wire("preserve", request.raw_prompt))])
    critic_provider = RecordingFake([response(critic_wire(request.raw_prompt))])
    architect_gateway, critic_gateway = (
        RecordingGateway(architect_provider),
        RecordingGateway(critic_provider),
    )
    try:
        outcome = await run_architect_critic_pass(
            PromptArchitectAgent(architect_gateway, ModelSettings(model="fake-architect-v1")),
            PromptCriticAgent(critic_gateway, ModelSettings(model="fake-critic-v1")),
            request,
        )
        assert isinstance(outcome, EvaluatedCandidate)
        assert architect_gateway.operations == architect_provider.calls == 1
        assert critic_gateway.operations == critic_provider.calls == 1
        assert outcome.candidate.metadata.requested_model == "fake-architect-v1"
        assert outcome.critic.metadata.requested_model == "fake-critic-v1"
        assert len(critic_provider.requests[0].messages) == 2
        assert not architect_provider.closed and not critic_provider.closed
    finally:
        await architect_provider.aclose()
        await critic_provider.aclose()


@pytest.mark.anyio
async def test_source_mutation_during_critic_wait_cannot_relabel_the_evaluation() -> None:
    request = source()
    before = request.model_dump()
    architect, critic, provider, gateway = agents(request)
    entered, release = asyncio.Event(), asyncio.Event()

    class PausingCritic(PromptCriticAgent):
        async def evaluate(
            self, payload: CriticInput, *, request_id: str | None = None
        ) -> CriticResult:
            entered.set()
            await release.wait()
            return await critic.evaluate(payload, request_id=request_id)

    task = asyncio.create_task(
        run_architect_critic_pass(
            architect,
            PausingCritic(gateway, ModelSettings(model="unused")),
            request,
            context=context(),
        )
    )
    try:
        await entered.wait()
        request.id, request.raw_prompt = OTHER_ID, PRIVATE
        release.set()
        outcome = await task
        assert isinstance(outcome, EvaluatedCandidate)
        assert outcome.candidate.original_request.id == before["id"]
        assert outcome.candidate.original_request.raw_prompt == before["raw_prompt"]
        assert outcome.candidate.context.prompt_request_id == REQUEST_ID
        assert gateway.operations == provider.calls == 2
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await provider.aclose()


@pytest.mark.anyio
async def test_malformed_architect_response_never_reaches_critic_or_scoring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = source()
    malformed = architect_wire("preserve", request.raw_prompt)
    malformed["changes"] = [NOTE]
    provider = RecordingFake([response(malformed)])
    gateway = RecordingGateway(provider)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Invalid Architect response reached Critic/scoring")

    critic = PromptCriticAgent(gateway, ModelSettings(model="fake-c"))
    monkeypatch.setattr(critic, "evaluate", forbidden)
    monkeypatch.setattr(ScoringEngine, "score", forbidden)
    try:
        with pytest.raises(LLMError) as caught:
            await run_architect_critic_pass(
                PromptArchitectAgent(gateway, ModelSettings(model="fake-a")), critic, request
            )
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert gateway.operations == provider.calls == 1
        assert caught.value.__context__ is None
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_clarification_contract_cannot_be_revalidated_as_evaluated_candidate() -> None:
    request = source()
    architect, critic, provider, _ = agents(request, "clarify")
    try:
        outcome = await run_architect_critic_pass(architect, critic, request)
        assert isinstance(outcome, ClarificationRequired)
    finally:
        await provider.aclose()
    with pytest.raises(ValidationError):
        EvaluatedCandidate.model_validate(outcome.model_dump())
