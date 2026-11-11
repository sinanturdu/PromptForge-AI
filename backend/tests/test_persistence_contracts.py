"""Offline persistence projections from real validated M5D contracts, not quality proof."""

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

from app.agents.prompt_architect import PromptArchitectAgent
from app.agents.prompt_critic import PromptCriticAgent
from app.agents.revision_schemas import ProtectedSourceSpan, RevisedCandidate
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import CallMetadata, GatewayPolicy, LLMResponse, ModelSettings
from app.modules.prompts.architect_contracts import ArchitectRunContext
from app.modules.prompts.architect_critic_pass import EvaluatedCandidate, run_architect_critic_pass
from app.modules.prompts.candidate_selection import select_best_candidate
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.optimization_contracts import EvaluationHistory, EvaluationRound
from app.modules.prompts.optimizer_contracts import OptimizationCounts, OptimizationOutcome
from app.modules.prompts.persistence_contracts import (
    AssessmentStorage,
    CallMetadataStorage,
    CandidateStorage,
    EvaluationStorage,
    GateStorage,
    OperationBindingStorage,
    OperationResultStorage,
    RequestPreferenceSnapshot,
    ScoringStorage,
    TerminalDetailsStorage,
    request_content_digest,
)
from app.modules.prompts.revision_integration import make_revision_input
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead
from app.scoring.rubric import CRITERIA

RAW = '  Explain Python 3.12. Keep "Merhaba".\n```python\ndef f():\n    return 1\n```\n'
PRIVATE = "PRIVATE_PERSISTENCE_SENTINEL"
NOW = datetime(2026, 11, 5, tzinfo=UTC)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def source() -> PromptRequestRead:
    payload = PromptRequestCreate(raw_prompt=RAW, user_context=PRIVATE, output_language="tr")
    return PromptRequestRead(id=UUID(int=1), created_at=NOW, **payload.model_dump())


def response(value: dict[str, Any]) -> LLMResponse:
    return LLMResponse(
        provider="fake", finish_reason="complete", text=json.dumps(value, ensure_ascii=False)
    )


def gateway(provider: FakeLLMProvider) -> LLMGateway:
    return LLMGateway(
        provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0), clock=lambda: 0.0
    )


async def sample(status: str = "qualified") -> EvaluatedCandidate:
    ref = dict(source="raw_prompt", quote=RAW)
    scores = {
        key: dict(score=10, rationale="Scripted assessment.", evidence=[ref])
        for key, _, _ in CRITERIA
    }
    issues: list[dict[str, Any]] = []
    if status == "unassessable":
        scores["clarity"] = dict(score=None, rationale="Missing assessment evidence.", evidence=[])
    elif status == "not_qualified":
        scores["constraints_and_scope"]["score"] = 6
        issues.append(
            dict(
                criterion="constraints_and_scope",
                code="missing_requirement",
                status="supported",
                severity="critical",
                description="A scripted supported constraint failure.",
                recommendation="Restore the stated constraint.",
                evidence=[ref],
            )
        )
    provider = FakeLLMProvider(
        [
            response(dict(decision="preserve", canonical_prompt=None, changes=[], questions=[])),
            response(
                dict(criteria=scores, issues=issues, summary="Scripted fixture, not model quality.")
            ),
        ]
    )
    shared = gateway(provider)
    try:
        result = await run_architect_critic_pass(
            PromptArchitectAgent(shared, ModelSettings(model="fake-a")),
            PromptCriticAgent(shared, ModelSettings(model="fake-c")),
            source(),
            context=ArchitectRunContext(
                prompt_request_id=UUID(int=1),
                workflow_run_id=UUID(int=2),
                workflow_prompt_request_id=UUID(int=1),
            ),
        )
        assert isinstance(result, EvaluatedCandidate) and provider.calls == 2
        return result
    finally:
        await provider.aclose()


def storage(value: EvaluatedCandidate) -> EvaluationStorage:
    return EvaluationStorage.from_evaluated(
        value,
        workflow_run_id=UUID(int=2),
        prompt_version_id=UUID(int=5),
        critic_agent_run_id=UUID(int=4),
        evaluation_round=1,
        completed_at=NOW,
    )


def test_legacy_generic_candidate_json_is_readable_without_conversion() -> None:
    legacy = {
        "schema_version": "1.0.0",
        "prompt_request_id": str(UUID(int=1)),
        "decision": "preserve",
        "canonical_prompt": CanonicalPrompt.from_task(RAW).model_dump(mode="json"),
        "rendered_prompt": RAW,
        "changes": [],
        "target_mode": "google",
        "renderer_name": "GenericPromptRenderer",
        "renderer_version": "1.0.0",
    }
    stored = CandidateStorage.from_json_value(legacy)
    assert stored.rendered_prompt == RAW
    assert stored.model_dump(mode="json") == legacy


def test_preferences_reference_and_digest_do_not_copy_original_content() -> None:
    request = source()
    before = request.model_dump_json()
    value = RequestPreferenceSnapshot.from_request(request)
    assert value.prompt_request_id == request.id and value.source_created_at == request.created_at
    assert {"raw_prompt", "user_context"}.isdisjoint(value.model_dump())
    assert PRIVATE not in value.model_dump_json() and RAW not in repr(value)
    assert RequestPreferenceSnapshot.model_validate_json(value.model_dump_json()) == value
    digest = request_content_digest(request)
    assert digest == request_content_digest(request) and len(digest) == 64
    assert request_content_digest(request.model_copy(update={"raw_prompt": RAW + " "})) != digest
    assert (
        request_content_digest(request.model_copy(update={"user_context": PRIVATE + "\n"}))
        != digest
    )
    assert request.model_dump_json() == before


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["qualified", "not_qualified", "unassessable"])
async def test_full_assessment_score_gate_and_record_projections(status: str) -> None:
    evaluated = await sample(status)
    value = storage(evaluated)
    before = evaluated.model_dump_json()
    fields = value.record_values()
    assert (
        len(
            AssessmentStorage.from_json_value(fields["assessment"]).assessment.criteria.model_dump()
        )
        == 9
    )
    assert GateStorage.from_json_value(fields["gate_results"]).gates == evaluated.scoring.gates
    assert (
        ScoringStorage.model_validate_json(
            ScoringStorage(schema_version="1.0.0", scoring=evaluated.scoring).model_dump_json()
        ).scoring
        == evaluated.scoring
    )
    assert (
        fields["quality_status"] == status
        and fields["total_score"] == evaluated.scoring.total_score
    )
    assert {
        "original_request",
        "raw_prompt",
        "user_context",
        "metadata",
        "history",
        "critic",
    }.isdisjoint(fields)
    assert (
        fields["assessment"]["assessment"]["criteria"]["intent_fidelity"]["evidence"][0]["quote"]
        == RAW
    )
    assert EvaluationStorage.model_validate_json(value.model_dump_json()) == value
    assert evaluated.model_dump_json() == before
    if status == "unassessable":
        assert fields["total_score"] is None and fields["passed"] is False
        assert fields["gate_results"]["gates"]["total_at_least_85"] is None


@pytest.mark.anyio
async def test_candidate_projection_preserves_exact_quotes_indentation_and_protection() -> None:
    baseline = await sample("not_qualified")
    protected = ProtectedSourceSpan(
        source="raw_prompt", kind="code", start=RAW.index("```"), end=RAW.rindex("```") + 3
    )
    payload = make_revision_input(baseline, next_evaluation_round=2, protected_spans=(protected,))
    canonical = CanonicalPrompt.from_task(RAW + "\nUse the stated constraints.")
    provider = FakeLLMProvider(
        [
            response(
                dict(
                    decision="rewrite",
                    canonical_prompt=canonical.model_dump(mode="json"),
                    changes=["Restored the supplied constraint."],
                    questions=[],
                    addressed_issue_keys=["issue_001"],
                )
            )
        ]
    )
    try:
        revised = await PromptArchitectAgent(
            gateway(provider), ModelSettings(model="fake-r")
        ).revise(payload)
        assert isinstance(revised, RevisedCandidate)
        result = OperationResultStorage.from_revision(revised)
        assert result.kind == "candidate" and result.candidate is not None
        assert (
            protected.text(baseline.candidate.original_request) in result.candidate.rendered_prompt
        )
        assert result.candidate.rendered_prompt == revised.candidate.rendered_prompt
        assert "original_request" not in result.candidate.model_dump()
        assert OperationResultStorage.model_validate_json(result.model_dump_json()) == result
    finally:
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["preserve", "clarify"])
async def test_revision_without_candidate_stores_no_version_content(decision: str) -> None:
    baseline = await sample("not_qualified")
    provider = FakeLLMProvider(
        [
            response(
                dict(
                    decision=decision,
                    canonical_prompt=None,
                    changes=[],
                    questions=["Which source is authoritative?"] if decision == "clarify" else [],
                    addressed_issue_keys=[],
                )
            )
        ]
    )
    try:
        revised = await PromptArchitectAgent(
            gateway(provider), ModelSettings(model="fake-r")
        ).revise(make_revision_input(baseline, next_evaluation_round=2))
        result = OperationResultStorage.from_revision(revised)
        assert result.kind == ("unchanged" if decision == "preserve" else "clarification")
        assert result.candidate is None and result.evaluation_id is None
    finally:
        await provider.aclose()


@pytest.mark.parametrize(
    "kind", ["candidate", "unchanged", "clarification", "evaluation", "failed"]
)
def test_operation_result_rejects_missing_classified_payload(kind: Any) -> None:
    with pytest.raises(ValidationError):
        OperationResultStorage(schema_version="1.0.0", kind=kind)


def test_unknown_metadata_stays_null_and_observations_require_explicit_counts() -> None:
    unknown = CallMetadataStorage.from_metadata(None)
    assert unknown.model_dump() == dict(schema_version="1.0.0", metadata=None)
    known = CallMetadata(
        provider="fake",
        requested_model="fake-model",
        resolved_model="fake-resolved",
        attempts=2,
        retries=1,
    )
    observed = CallMetadataStorage.from_metadata(known)
    assert observed.metadata is not None and observed.metadata.attempts == 2
    assert CallMetadataStorage.model_validate_json(observed.model_dump_json()) == observed
    invalid_values: list[dict[str, Any]] = [
        dict(metadata={}),
        dict(metadata={"api_key": PRIVATE}),
        dict(api_key=PRIVATE, metadata=None),
    ]
    for invalid in invalid_values:
        with pytest.raises(ValidationError) as caught:
            CallMetadataStorage.model_validate(dict(schema_version="1.0.0", **invalid))
        assert PRIVATE not in str(caught.value)


@pytest.mark.parametrize(
    "changes",
    [
        dict(attempts=0, retries=1),
        dict(attempts=1, repair_attempted=True),
        dict(attempts=True),
        dict(retries=3),
    ],
)
def test_inconsistent_metadata_is_rejected(changes: dict[str, Any]) -> None:
    value = CallMetadata(provider="fake", requested_model="fake", attempts=1, retries=0)
    with pytest.raises(ValidationError):
        CallMetadataStorage.from_metadata(value.model_copy(update=changes))


def test_revision_binding_is_versioned_bounded_and_not_public_idempotency() -> None:
    value = OperationBindingStorage(
        schema_version="1.0.0",
        operation="revise",
        evaluation_round=2,
        baseline_evaluation_id=UUID(int=6),
        revision_attempt_key="a" * 64,
        feedback_projection_digest="b" * 64,
        eligible_issue_keys=("issue_001",),
    )
    assert OperationBindingStorage.model_validate_json(value.model_dump_json()) == value
    assert "a" * 64 not in repr(value)
    updates: list[dict[str, Any]] = [
        dict(evaluation_round=1),
        dict(evaluation_round=True),
        dict(baseline_evaluation_id=None),
        dict(eligible_issue_keys=()),
        dict(feedback_projection_digest=PRIVATE),
        dict(operation="evaluate"),
    ]
    for fields in updates:
        with pytest.raises(ValidationError):
            OperationBindingStorage.model_validate(value.model_copy(update=fields))


@pytest.mark.parametrize("version", ["2.0.0", "", 1, None])
def test_unknown_storage_versions_fail(version: Any) -> None:
    models: list[tuple[type[BaseModel], dict[str, Any]]] = [
        (CallMetadataStorage, dict(metadata=None)),
        (OperationBindingStorage, dict(operation="create", evaluation_round=1)),
        (
            TerminalDetailsStorage,
            dict(
                execution_status="FAILED",
                termination_reason="interrupted",
                error_code="interrupted",
            ),
        ),
    ]
    for model, fields in models:
        with pytest.raises(ValidationError):
            model.model_validate(dict(schema_version=version, **fields))


@pytest.mark.anyio
@pytest.mark.parametrize("mutation", ["score", "evidence", "fingerprint", "canonical"])
async def test_domain_factory_revalidates_nested_model_copy_tampering(mutation: str) -> None:
    value = await sample()
    if mutation == "score":
        value = value.model_copy(
            update={"scoring": value.scoring.model_copy(update={"total_score": Decimal(99)})}
        )
    elif mutation == "fingerprint":
        value = value.model_copy(
            update={"critic": value.critic.model_copy(update={"input_fingerprint": "f" * 64})}
        )
    elif mutation == "canonical":
        value = value.model_copy(
            update={"candidate": value.candidate.model_copy(update={"rendered_prompt": PRIVATE})}
        )
    else:
        criterion = value.critic.output.criteria.clarity
        invalid = criterion.evidence[0].model_copy(update={"quote": PRIVATE})
        criteria = value.critic.output.criteria.model_copy(
            update={"clarity": criterion.model_copy(update={"evidence": (invalid,)})}
        )
        value = value.model_copy(
            update={
                "critic": value.critic.model_copy(
                    update={"output": value.critic.output.model_copy(update={"criteria": criteria})}
                )
            }
        )
    with pytest.raises(LLMError) as caught:
        storage(value)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST and caught.value.__context__ is None
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.anyio
async def test_standalone_storage_rejects_scoring_or_assessment_inconsistency() -> None:
    value = storage(await sample())
    with pytest.raises(ValidationError):
        EvaluationStorage.model_validate(
            value.model_copy(
                update={"scoring": value.scoring.model_copy(update={"total_score": Decimal(99)})}
            )
        )
    assessment = value.critic.output
    mutations: list[dict[str, Any]] = [
        dict(schema_version="2.0.0"),
        dict(assessment=assessment.model_copy(update={"summary": ""})),
    ]
    for mutation in mutations:
        with pytest.raises(ValidationError):
            AssessmentStorage.model_validate(
                {"schema_version": "1.0.0", "assessment": assessment, **mutation}
            )


@pytest.mark.anyio
async def test_safe_terminal_projection_excludes_history_and_keeps_quality_separate() -> None:
    evaluated = await sample()
    history = EvaluationHistory(rounds=(EvaluationRound(round_number=1, evaluated=evaluated),))
    outcome = OptimizationOutcome(
        execution_status="completed",
        termination_reason="quality_passed",
        history=history,
        selection=select_best_candidate(history),
        quality_status="qualified",
        clarification_questions=(),
        failed_operation=None,
        error_code=None,
        counts=OptimizationCounts(architect_create=1, architect_revise=0, critic_evaluate=1),
        max_evaluation_rounds=3,
        optimizer_version="1.0.0",
        progress_policy_version="1.0.0",
    )
    projected = TerminalDetailsStorage.from_outcome(outcome)
    assert projected.execution_status == "COMPLETED"
    assert {"history", "selection", "quality_status", "counts"}.isdisjoint(projected.model_dump())
    assert PRIVATE not in projected.model_dump_json()
    assert TerminalDetailsStorage.model_validate_json(projected.model_dump_json()) == projected


@pytest.mark.parametrize(
    "fields",
    [
        dict(execution_status="FAILED", termination_reason="interrupted"),
        dict(
            execution_status="FAILED",
            termination_reason="deadline_exceeded",
            error_code="interrupted",
        ),
        dict(
            execution_status="CLARIFICATION_REQUIRED", termination_reason="clarification_required"
        ),
        dict(
            execution_status="COMPLETED",
            termination_reason="provider_failure",
            error_code=LLMErrorCode.AUTHENTICATION,
        ),
    ],
)
def test_terminal_status_rejects_inconsistent_or_missing_data(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TerminalDetailsStorage.model_validate(dict(schema_version="1.0.0", **fields))


@pytest.mark.anyio
async def test_projection_helpers_are_offline_pure_and_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evaluated = await sample()
    before = evaluated.model_dump_json()

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Persistence projection invoked I/O or an agent")

    monkeypatch.setattr(Engine, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(LLMGateway, "generate_json", forbidden)
    monkeypatch.setattr(PromptArchitectAgent, "create", forbidden)
    monkeypatch.setattr(PromptArchitectAgent, "revise", forbidden)
    monkeypatch.setattr(PromptCriticAgent, "evaluate", forbidden)
    value = storage(evaluated)
    candidate = CandidateStorage.from_candidate(evaluated.candidate)
    assert candidate.rendered_prompt == RAW and PRIVATE not in repr(value) + repr(candidate)
    assert value.record_values()["total_score"] == evaluated.scoring.total_score
    assert evaluated.model_dump_json() == before


@pytest.mark.parametrize(
    "value",
    [
        {"metadata": None},
        {"schema_version": "2.0.0", "metadata": None},
        {"schema_version": "1.0.0", PRIVATE: PRIVATE},
        [PRIVATE],
    ],
)
def test_json_reader_requires_version_and_hides_private_paths(value: Any) -> None:
    with pytest.raises(LLMError) as caught:
        CallMetadataStorage.from_json_value(value)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST and caught.value.__context__ is None
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


def test_json_constraints_and_frozen_preferences() -> None:
    fields = dict(schema_version="1.0.0", operation="evaluate", evaluation_round=4)
    with pytest.raises(ValidationError):
        OperationBindingStorage.model_validate_json(json.dumps(fields))
    fields["evaluation_round"] = True
    with pytest.raises(ValidationError):
        OperationBindingStorage.model_validate_json(json.dumps(fields))
    snapshot = RequestPreferenceSnapshot.from_request(source())
    with pytest.raises(ValidationError):
        snapshot.output_language = "en"
