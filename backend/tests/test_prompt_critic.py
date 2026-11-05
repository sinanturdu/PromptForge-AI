"""Scripted judgments verify contracts/arithmetic, never real-model semantic quality."""

import asyncio
import copy
import hashlib
import io
import json
import traceback
import warnings
from datetime import UTC, datetime
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import BaseModel, ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.architect_schemas import ArchitectDecision, ArchitectOutput
from app.agents.critic_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.critic_schemas import (
    CriterionAssessment,
    CriterionScores,
    CriticOutput,
    CriticResult,
    resolve_unassessable,
)
from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import AGENT_VERSION, MAX_RENDERED_CHARS, PromptCriticAgent
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers._openai_schema import validate_schema
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import (
    CallMetadata,
    GatewayPolicy,
    LLMRequest,
    LLMResponse,
    ModelSettings,
    StructuredResult,
)
from app.models.enums import PromptDetailLevel, TargetMode
from app.modules.prompts.architect_integration import (
    ArchitectRequestSnapshot,
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    CriticInput,
    create_architect_handoff,
    make_critic_input,
)
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead
from app.scoring.engine import SCORING_VERSION, ScoringEngine
from app.scoring.rubric import CRITERIA, RUBRIC_VERSION
from app.scoring.schemas import ScoringResult

PRIVATE = "PRIVATE_CRITIC_CONTENT_SENTINEL"
NOTE = "PRIVATE_ARCHITECT_EXPLANATION_SENTINEL"
REQUEST_ID = UUID("00000000-0000-0000-0000-000000000031")
CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent / "fixtures" / "critic_cases.json").read_text(encoding="utf-8")
)


def blind(raw: str = "Translate 'hello' into Turkish.", **updates: Any) -> CriticInput:
    payload = PromptRequestCreate(raw_prompt=raw, user_context=PRIVATE)
    source = ArchitectRequestSnapshot(
        id=REQUEST_ID, created_at=datetime(2026, 1, 1, tzinfo=UTC), **payload.model_dump()
    )
    return CriticInput(
        original_request=source,
        rendered_prompt=raw,
        target_mode=source.target_mode,
        renderer_name=GenericPromptRenderer.name,
        renderer_version=GenericPromptRenderer.version,
    ).model_copy(update=updates)


def evidence(source: str = "raw_prompt", quote: str = "Translate") -> dict[str, str]:
    return {"source": source, "quote": quote}


def wire(quote: str = "Translate") -> dict[str, Any]:
    return {
        "criteria": {
            key: {
                "score": 10,
                "rationale": "The supplied request is satisfied.",
                "evidence": [evidence(quote=quote)],
            }
            for key, _, _ in CRITERIA
        },
        "issues": [],
        "summary": "The supplied candidate meets the stated request.",
    }


def finding(
    data: dict[str, Any],
    key: str,
    score: int | None,
    severity: str | None = "minor",
    *,
    status: str = "supported",
    ref: dict[str, str] | None = None,
    code: str = "missing_requirement",
    description: str | None = None,
) -> None:
    data["criteria"][key]["score"] = score
    data["issues"].append(
        {
            "criterion": key,
            "code": code,
            "status": status,
            "severity": severity,
            "description": description or f"The supplied {key} requirement is not satisfied.",
            "recommendation": "Restore the requirement supported by the supplied source.",
            "evidence": [ref or evidence()],
        }
    )


def result_from(data: dict[str, Any]) -> CriticResult:
    return CriticResult(
        output=resolve_unassessable(CriticOutput.model_validate_json(json.dumps(data))),
        input_fingerprint="a" * 64,
        evaluated_target_mode=TargetMode.GENERIC,
        evaluated_renderer_name=GenericPromptRenderer.name,
        evaluated_renderer_version=GenericPromptRenderer.version,
        agent_version=AGENT_VERSION,
        instruction_version=INSTRUCTION_VERSION,
        rubric_version=RUBRIC_VERSION,
        metadata=CallMetadata(provider="fake", requested_model="fake-critic-v1"),
    )


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


def response(data: dict[str, Any]) -> LLMResponse:
    return LLMResponse(provider="fake", finish_reason="complete", text=json.dumps(data))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "raw",
    [
        "  Translate 'hello' into Turkish. Return only the translation.\n",
        "  'hello' sözcüğünü Türkçeye çevir. Yaln\u0131zca çeviriyi döndür.\n",
    ],
)
@pytest.mark.parametrize("decision", ["preserve", "rewrite"])
async def test_real_architect_handoffs_are_blind_and_eligible(
    raw: str, decision: ArchitectDecision
) -> None:
    payload = PromptRequestCreate(raw_prompt=raw, user_context=PRIVATE)
    source = PromptRequestRead(
        id=REQUEST_ID, created_at=datetime(2026, 1, 1, tzinfo=UTC), **payload.model_dump()
    )
    canonical = CanonicalPrompt.from_task(raw + "\n")
    architect_output = ArchitectOutput(
        decision=decision,
        canonical_prompt=canonical if decision == "rewrite" else None,
        changes=(NOTE,) if decision == "rewrite" else (),
        questions=(),
    )
    provider = RecordingFake(
        [
            LLMResponse(
                provider="fake", finish_reason="complete", text=architect_output.model_dump_json()
            ),
            response(wire(raw.strip())),
        ]
    )
    gateway = RecordingGateway(provider)
    settings = ModelSettings(model="fake-critic-v1")
    before = source.model_dump()
    try:
        handoff = await create_architect_handoff(PromptArchitectAgent(gateway, settings), source)
        assert isinstance(handoff, CandidateReady)
        candidate = make_critic_input(handoff)
        critic_before = candidate.model_dump()
        result = await PromptCriticAgent(gateway, settings).evaluate(
            candidate, request_id="critic-1"
        )
        assert gateway.operations == provider.calls == 2
        assert not provider.closed
    finally:
        await provider.aclose()
    assert source.model_dump() == before
    assert candidate.model_dump() == critic_before
    if decision == "preserve":
        assert candidate.rendered_prompt == raw
    sent = json.loads(provider.requests[1].messages[1].content)
    assert sent["original_request"] == payload.model_dump(mode="json")
    assert sent["rendered_prompt"] == candidate.rendered_prompt
    assert set(sent) == {
        "original_request",
        "rendered_prompt",
        "target_mode",
        "renderer_name",
        "renderer_version",
    }
    assert NOTE not in provider.requests[1].messages[1].content
    assert str(REQUEST_ID) not in provider.requests[1].messages[1].content
    assert "created_at" not in provider.requests[1].messages[1].content
    assert "critic-1" not in provider.requests[1].messages[1].content
    assert provider.requests[1].messages[0].content == SYSTEM_INSTRUCTION
    assert result.metadata.request_id == "critic-1"
    assert result.metadata.requested_model == settings.model
    assert result.agent_version == result.instruction_version == "1.0.0"
    assert result.rubric_version == "2.0.0"
    expected = hashlib.sha256(
        json.dumps(candidate.model_dump(mode="json"), sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert result.input_fingerprint == expected
    assert ScoringEngine.score(result).status == "qualified"


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
async def test_scripted_adversarial_judgments_remain_grounded(case: dict[str, Any]) -> None:
    payload = blind(case["raw"], rendered_prompt=case["candidate"])
    data = wire(case["raw"])
    finding(
        data,
        case["criterion"],
        case["score"],
        case["severity"],
        ref=evidence(case["source"], case["quote"]),
        code=case["code"],
    )
    provider = RecordingFake([response(data)])
    gateway = RecordingGateway(provider)
    try:
        result = await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(payload)
        assert provider.calls == gateway.operations == 1
    finally:
        await provider.aclose()
    issue = result.output.issues[0]
    assert issue.code == case["code"] and issue.severity == case["severity"]
    assert issue.evidence[0].quote == case["quote"]
    scoring = ScoringEngine.score(result)
    assert scoring.gates.no_critical_issues == (case["severity"] != "critical")
    if case["severity"] == "critical":
        assert not scoring.passed


def test_critic_schema_is_strict_and_compatible_with_existing_preflight() -> None:
    schema = CriticOutput.model_json_schema()
    validate_schema(schema)
    assert set(CriterionScores.model_fields) == {key for key, _, _ in CRITERIA}
    assert set(schema["required"]) == {"criteria", "issues", "summary"}
    assert "total_score" not in schema["properties"] and "passed" not in schema["properties"]
    for model in (CriticOutput, CriterionScores, CriterionAssessment):
        assert all(field.is_required() for field in model.model_fields.values())
    assert len(CRITERIA) == 9 and sum(weight for _, weight, _ in CRITERIA) == 100


@pytest.mark.parametrize("score", [-1, 11, True, 7.5, "10"])
def test_invalid_score_types_and_ranges(score: object) -> None:
    data = wire()
    data["criteria"]["clarity"]["score"] = score
    with pytest.raises(ValidationError):
        CriticOutput.model_validate_json(json.dumps(data))


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_criterion",
        "extra_criterion",
        "extra_total",
        "blank_rationale",
        "long_rationale",
        "missing_evidence",
        "blank_quote",
        "long_quote",
        "unknown_source",
        "too_many_evidence",
        "duplicate_evidence",
        "blank_summary",
        "long_summary",
        "unknown_issue",
        "unknown_criterion",
        "supported_without_severity",
        "supported_without_evidence",
        "uncertain_with_severity",
        "blank_description",
        "long_recommendation",
        "too_many_issues",
        "duplicate_issue",
        "major_perfect_score",
        "minor_score_nine",
        "missing_wire_field",
    ],
)
def test_malformed_wire_contracts(mutation: str) -> None:
    data = wire()
    assessment = data["criteria"]["clarity"]
    if mutation == "missing_criterion":
        del data["criteria"]["clarity"]
    elif mutation == "extra_criterion":
        data["criteria"]["other"] = assessment
    elif mutation == "extra_total":
        data["total_score"] = 100
    elif mutation == "blank_rationale":
        assessment["rationale"] = " "
    elif mutation == "long_rationale":
        assessment["rationale"] = "x" * 601
    elif mutation == "missing_evidence":
        assessment["evidence"] = []
    elif mutation == "blank_quote":
        assessment["evidence"][0]["quote"] = " "
    elif mutation == "long_quote":
        assessment["evidence"][0]["quote"] = "x" * 601
    elif mutation == "unknown_source":
        assessment["evidence"][0]["source"] = "https://example.com"
    elif mutation == "too_many_evidence":
        assessment["evidence"] = [evidence(quote=str(i)) for i in range(4)]
    elif mutation == "duplicate_evidence":
        assessment["evidence"] *= 2
    elif mutation == "blank_summary":
        data["summary"] = " "
    elif mutation == "long_summary":
        data["summary"] = "x" * 801
    else:
        finding(data, "clarity", 6, "major")
        issue = data["issues"][0]
        if mutation == "unknown_issue":
            issue["code"] = "other"
        elif mutation == "unknown_criterion":
            issue["criterion"] = "other"
        elif mutation == "supported_without_severity":
            issue["severity"] = None
        elif mutation == "supported_without_evidence":
            issue["evidence"] = []
        elif mutation == "uncertain_with_severity":
            issue["status"] = "uncertain"
        elif mutation == "blank_description":
            issue["description"] = " "
        elif mutation == "long_recommendation":
            issue["recommendation"] = "x" * 601
        elif mutation == "too_many_issues":
            data["issues"] = [dict(issue, description=str(i)) for i in range(13)]
        elif mutation == "duplicate_issue":
            data["issues"].append(dict(issue, criterion="completeness"))
        elif mutation == "major_perfect_score":
            assessment["score"] = 10
        elif mutation == "minor_score_nine":
            issue["severity"], assessment["score"] = "minor", 9
        elif mutation == "missing_wire_field":
            del issue["severity"]
    with pytest.raises(ValidationError):
        CriticOutput.model_validate_json(json.dumps(data))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "ref",
    [
        evidence(quote="fabricated quotation"),
        evidence("user_context", "Translate"),
        evidence("output_language", "en"),
        evidence(quote="translate"),
    ],
)
async def test_fabricated_or_misattributed_evidence_never_gets_semantic_repair(
    ref: dict[str, str],
) -> None:
    data = wire()
    data["criteria"]["clarity"]["evidence"] = [ref]
    provider = RecordingFake([response(data), response(wire())])
    gateway = RecordingGateway(provider, repair=1)
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(blind())
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert provider.calls == gateway.operations == 1
        assert caught.value.__context__ is None
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_whitelisted_preferences_and_issue_evidence_are_checked() -> None:
    data = wire()
    for (key, _, _), source, quote in zip(
        CRITERIA,
        [
            "raw_prompt",
            "user_context",
            "output_language",
            "task_profile",
            "prompt_detail_level",
            "response_detail_preference",
            "reasoning_preference",
            "target_mode",
            "rendered_prompt",
        ],
        [
            "Translate",
            PRIVATE,
            "tr",
            "translation",
            "medium",
            "balanced",
            "auto",
            "generic",
            "Translate",
        ],
        strict=True,
    ):
        data["criteria"][key]["evidence"] = [evidence(source, quote)]
    payload = blind()
    payload = payload.model_copy(
        update={
            "original_request": payload.original_request.model_copy(
                update={"output_language": "tr", "task_profile": "translation"}
            )
        }
    )
    provider = RecordingFake([response(data)])
    try:
        result = await PromptCriticAgent(
            RecordingGateway(provider), ModelSettings(model="fake-v1")
        ).evaluate(payload)
        assert ScoringEngine.score(result).passed
    finally:
        await provider.aclose()
    finding(data, "clarity", 8, ref=evidence(quote="fabricated issue evidence"))
    provider = RecordingFake([response(data)])
    try:
        with pytest.raises(LLMError):
            await PromptCriticAgent(
                RecordingGateway(provider), ModelSettings(model="fake-v1")
            ).evaluate(payload)
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "mode",
    ["explicit_null", "unsupported_deduction", "minor_cannot_justify_severe", "uncertain_issue"],
)
def test_uncertainty_never_invents_scores_or_renormalizes(mode: str) -> None:
    data = wire()
    if mode == "explicit_null":
        data["criteria"]["clarity"].update(score=None, evidence=[])
    elif mode == "unsupported_deduction":
        data["criteria"]["clarity"]["score"] = 8
    elif mode == "minor_cannot_justify_severe":
        finding(data, "clarity", 3)
    else:
        finding(data, "clarity", 10, None, status="uncertain")
    before = copy.deepcopy(data)
    result = result_from(data)
    scoring = ScoringEngine.score(result)
    assert data == before
    assert result.output.criteria.clarity.score is None
    assert scoring.status == "unassessable" and scoring.total_score is None
    assert not scoring.passed and scoring.gates.total_at_least_85 is None
    assert not scoring.gates.all_criteria_assessable
    assert scoring.gates.intent_at_least_7 is True


@pytest.mark.anyio
async def test_agent_returns_unassessable_without_fabricating_an_issue() -> None:
    data = wire()
    data["criteria"]["clarity"]["score"] = 7
    provider = RecordingFake([response(data)])
    try:
        result = await PromptCriticAgent(
            RecordingGateway(provider), ModelSettings(model="fake-v1")
        ).evaluate(blind())
    finally:
        await provider.aclose()
    assert result.output.criteria.clarity.score is None and result.output.issues == ()
    assert ScoringEngine.score(result).status == "unassessable"
    assert provider.calls == 1


@pytest.mark.parametrize(
    ("clarity", "expected", "status"), [(5, "84", "not_qualified"), (6, "85", "qualified")]
)
def test_exact_total_threshold(clarity: int, expected: str, status: str) -> None:
    data = wire()
    finding(data, "intent_fidelity", 8)
    finding(data, "constraints_and_scope", 7)
    finding(data, "clarity", clarity, "major")
    result = result_from(data)
    before = result.model_dump()
    scoring = ScoringEngine.score(result)
    assert scoring.total_score == Decimal(expected) and scoring.status == status
    assert scoring.passed == (status == "qualified")
    assert result.model_dump() == before


@pytest.mark.parametrize(
    ("intent", "severity", "total", "passed"),
    [(6, "major", "90", False), (7, "minor", "92.5", True)],
)
def test_intent_floor_independently_blocks_high_total(
    intent: int, severity: str, total: str, passed: bool
) -> None:
    data = wire()
    finding(data, "intent_fidelity", intent, severity)
    scoring = ScoringEngine.score(result_from(data))
    assert scoring.total_score == Decimal(total)
    assert scoring.passed == scoring.gates.intent_at_least_7 == passed


def test_critical_issue_blocks_despite_high_total() -> None:
    data = wire()
    finding(data, "constraints_and_scope", 6, "critical")
    scoring = ScoringEngine.score(result_from(data))
    assert scoring.total_score == Decimal("92")
    assert scoring.gates.total_at_least_85 and scoring.gates.intent_at_least_7
    assert not scoring.gates.no_critical_issues and not scoring.passed
    assert scoring.status == "not_qualified"


def test_decimal_arithmetic_is_exact_and_independent_of_callers_precision() -> None:
    data = wire()
    data["criteria"]["consistency"]["score"] = 9
    with localcontext() as context:
        context.prec = 1
        context.Emax = 0
        scoring = ScoringEngine.score(result_from(data))
    assert scoring.total_score == Decimal("99.5")
    assert scoring.rubric_version == RUBRIC_VERSION and scoring.scoring_version == SCORING_VERSION


@pytest.mark.parametrize("key,weight,_definition", CRITERIA)
def test_each_fixed_weight_contributes_exactly(key: str, weight: int, _definition: str) -> None:
    data = wire()
    finding(data, key, 0, "major")
    assert ScoringEngine.score(result_from(data)).total_score == Decimal(100 - weight)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {"rendered_prompt": " "},
        {"rendered_prompt": "x" * (MAX_RENDERED_CHARS + 1)},
        {"rendered_prompt": 42},
        {"target_mode": TargetMode.OPENAI},
        {"renderer_name": PRIVATE},
        {"renderer_version": "999"},
        {"original_request": None},
    ],
)
async def test_tampered_blind_input_rejected_before_provider(updates: dict[str, Any]) -> None:
    provider = RecordingFake([response(wire())])
    gateway = RecordingGateway(provider)
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(
                blind(**updates)
            )
        assert caught.value.code == LLMErrorCode.INVALID_REQUEST
        assert caught.value.__context__ is None
        assert gateway.operations == provider.calls == 0
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_clarification_and_tampered_candidate_cannot_enter_critic() -> None:
    source = blind().original_request
    handoff = ClarificationRequired(
        original_request=source,
        context=ArchitectRunContext(prompt_request_id=source.id),
        metadata=CallMetadata(provider="fake", requested_model="fake-v1"),
        agent_version="1.0.0",
        instruction_version="1.0.0",
        questions=("Which input?",),
    )
    provider = RecordingFake([response(wire())])
    gateway = RecordingGateway(provider)
    try:
        with pytest.raises(LLMError):
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(handoff)  # type: ignore[arg-type]
        candidate = CandidateReady(
            original_request=source,
            context=handoff.context,
            metadata=handoff.metadata,
            agent_version="1.0.0",
            instruction_version="1.0.0",
            decision="preserve",
            canonical_prompt=CanonicalPrompt.from_task(source.raw_prompt),
            rendered_prompt=source.raw_prompt,
            changes=(),
            renderer_name=GenericPromptRenderer.name,
            renderer_version=GenericPromptRenderer.version,
        )
        with pytest.raises(LLMError):
            make_critic_input(candidate.model_copy(update={"rendered_prompt": PRIVATE}))
        assert gateway.operations == provider.calls == 0
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "updates",
    [
        {"rubric_version": "unknown"},
        {"input_fingerprint": PRIVATE},
        {"evaluated_renderer_version": "999"},
        {"metadata": None},
    ],
)
def test_scorer_deeply_revalidates_application_result(updates: dict[str, Any]) -> None:
    result = result_from(wire()).model_copy(update=updates)
    with pytest.raises(LLMError) as caught:
        ScoringEngine.score(result)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert caught.value.__context__ is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code",
    [
        LLMErrorCode.TIMEOUT,
        LLMErrorCode.AUTHENTICATION,
        LLMErrorCode.REFUSAL,
        LLMErrorCode.TRUNCATION,
    ],
)
async def test_gateway_errors_propagate_without_fallback(code: LLMErrorCode) -> None:
    provider = RecordingFake([code])
    gateway = RecordingGateway(provider)
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(blind())
        assert caught.value.code == code and caught.value.metadata.call is not None
        assert provider.calls == gateway.operations == 1 and not provider.closed
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("first", ["malformed_json", "transport_timeout"])
async def test_gateway_repair_and_transport_retry_stay_one_agent_operation(first: str) -> None:
    initial: LLMResponse | LLMErrorCode = (
        LLMResponse(provider="fake", finish_reason="complete", text="not JSON")
        if first == "malformed_json"
        else LLMErrorCode.TIMEOUT
    )
    provider = RecordingFake([initial, response(wire())])
    gateway = RecordingGateway(provider, repair=1, retries=1)
    try:
        result = await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(blind())
        assert gateway.operations == 1 and provider.calls == 2
        assert result.metadata.attempts == 2
        assert result.metadata.repair_attempted == (first == "malformed_json")
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_cancellation_propagates_without_closing_borrowed_provider() -> None:
    started = asyncio.Event()
    cancelled = asyncio.Event()

    class WaitingFake(RecordingFake):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("Unreachable")

    provider = WaitingFake([])
    gateway = RecordingGateway(provider)
    task = asyncio.create_task(
        PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(blind())
    )
    await started.wait()
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cancelled.is_set() and gateway.operations == 1 and not provider.closed
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_no_database_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Critic/scoring attempted database activity")

    monkeypatch.setattr(Engine, "connect", forbidden)
    for method in ("execute", "add", "flush", "commit"):
        monkeypatch.setattr(AsyncSession, method, forbidden)
    provider = RecordingFake([response(wire())])
    try:
        result = await PromptCriticAgent(
            RecordingGateway(provider), ModelSettings(model="fake-v1")
        ).evaluate(blind())
        assert ScoringEngine.score(result).passed
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_private_content_stays_out_of_reprs_logs_and_errors() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    payload = blind(PRIVATE)
    data = wire(PRIVATE)
    for assessment in data["criteria"].values():
        assessment["rationale"] = PRIVATE
    data["summary"] = PRIVATE
    provider = RecordingFake([response(data), response(wire(PRIVATE))])
    gateway = RecordingGateway(provider)
    try:
        result = await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(payload)
        representations = [
            repr(payload),
            repr(result),
            repr(result.output),
            repr(result.output.criteria),
            repr(ScoringEngine.score(result)),
        ]
        bad = copy.deepcopy(data)
        bad["criteria"]["clarity"]["evidence"] = [evidence(quote=PRIVATE + "fabricated")]
        other = RecordingFake([response(bad)])
        try:
            with pytest.raises(LLMError) as caught:
                await PromptCriticAgent(
                    RecordingGateway(other), ModelSettings(model="fake-v1")
                ).evaluate(payload)
            representations += [
                str(caught.value),
                repr(caught.value),
                "".join(traceback.format_exception(caught.value)),
            ]
            assert caught.value.__context__ is None
        finally:
            await other.aclose()
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            with pytest.raises(LLMError):
                await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(
                    payload.model_copy(update={"rendered_prompt": {PRIVATE: PRIVATE}})
                )
        assert not recorded
        assert all(PRIVATE not in text for text in representations)
        assert PRIVATE not in stream.getvalue()
        # Explicit dumps are intentionally private; no claim of automatic redaction.
        assert PRIVATE in result.model_dump_json()
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("target", list(TargetMode))
@pytest.mark.parametrize("detail", list(PromptDetailLevel))
async def test_concise_prompt_can_pass_for_every_target_and_detail(
    target: TargetMode, detail: PromptDetailLevel
) -> None:
    payload = blind()
    payload = payload.model_copy(
        update={
            "target_mode": target,
            "original_request": payload.original_request.model_copy(
                update={
                    "target_mode": target,
                    "prompt_detail_level": detail,
                }
            ),
        }
    )
    provider = RecordingFake([response(wire())])
    try:
        result = await PromptCriticAgent(
            RecordingGateway(provider), ModelSettings(model="fake-v1")
        ).evaluate(payload)
        assert result.evaluated_target_mode == target
        assert ScoringEngine.score(result).total_score == Decimal(100)
        sent = json.loads(provider.requests[0].messages[1].content)
        assert sent["original_request"]["prompt_detail_level"] == detail.value
        assert sent["rendered_prompt"] == payload.rendered_prompt
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "updates",
    [
        {"total_score": Decimal("NaN")},
        {"total_score": Decimal("101")},
        {"status": "not_qualified"},
        {"passed": False},
        {"total_score": None},
        {
            "gates": {
                "all_criteria_assessable": True,
                "total_at_least_85": False,
                "no_critical_issues": True,
                "intent_at_least_7": True,
            }
        },
        {
            "gates": {
                "all_criteria_assessable": True,
                "total_at_least_85": True,
                "no_critical_issues": True,
                "intent_at_least_7": None,
            }
        },
    ],
)
def test_scoring_result_contract_rejects_inconsistent_decisions(updates: dict[str, Any]) -> None:
    valid = ScoringEngine.score(result_from(wire())).model_dump()
    with pytest.raises(ValidationError):
        ScoringResult.model_validate({**valid, **updates})


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["output", "metadata", "nested_metadata"])
async def test_forged_gateway_handoffs_fail_safely(mutation: str) -> None:
    class ForgingGateway(RecordingGateway):
        async def generate_json[T: BaseModel](
            self, request: LLMRequest, output_type: type[T]
        ) -> StructuredResult[T]:
            generated = await super().generate_json(request, output_type)
            if mutation == "output":
                return generated.model_copy(update={"value": None})
            if mutation == "metadata":
                return generated.model_copy(update={"metadata": None})
            return generated.model_copy(
                update={"metadata": generated.metadata.model_copy(update={"attempts": PRIVATE})}
            )

    provider = RecordingFake([response(wire())])
    gateway = ForgingGateway(provider)
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(blind())
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
        assert caught.value.__context__ is None
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
        assert gateway.operations == provider.calls == 1
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_input_snapshot_cannot_be_relabelled_during_provider_wait() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class PausingFake(RecordingFake):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            entered.set()
            await release.wait()
            return await super().generate(request)

    payload = blind()
    before = payload.model_dump(mode="json")
    provider = PausingFake([response(wire())])
    gateway = RecordingGateway(provider)
    task = asyncio.create_task(
        PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(payload)
    )
    try:
        await entered.wait()
        # Simulate a caller deliberately bypassing a frozen DTO after the snapshot.
        object.__setattr__(payload.original_request, "raw_prompt", PRIVATE)
        object.__setattr__(payload, "rendered_prompt", PRIVATE)
        release.set()
        result = await task
        expected = hashlib.sha256(
            json.dumps(before, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        assert result.input_fingerprint == expected
        assert (
            json.loads(provider.requests[0].messages[1].content)["rendered_prompt"]
            == before["rendered_prompt"]
        )
        assert gateway.operations == provider.calls == 1
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        await provider.aclose()


@pytest.mark.anyio
async def test_uncertain_external_claim_is_unassessable_without_a_fabricated_quote() -> None:
    data = wire()
    finding(data, "context_and_role", None, None, status="uncertain")
    data["issues"][0]["evidence"] = []
    data["criteria"]["context_and_role"]["evidence"] = []
    provider = RecordingFake([response(data)])
    try:
        result = await PromptCriticAgent(
            RecordingGateway(provider), ModelSettings(model="fake-v1")
        ).evaluate(blind())
        assert result.output.issues[0].severity is None
        assert result.output.issues[0].evidence == ()
        assert ScoringEngine.score(result).status == "unassessable"
        assert provider.calls == 1
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_tampered_settings_fail_before_provider_access() -> None:
    provider = RecordingFake([response(wire())])
    gateway = RecordingGateway(provider)
    settings = ModelSettings(model="fake-v1").model_copy(update={"max_output_tokens": PRIVATE})
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, settings).evaluate(blind())
        assert caught.value.code == LLMErrorCode.INVALID_REQUEST
        assert caught.value.__context__ is None
        assert provider.calls == gateway.operations == 0
    finally:
        await provider.aclose()
