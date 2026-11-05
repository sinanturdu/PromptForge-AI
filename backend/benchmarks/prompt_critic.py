"""Deterministic fake Critic probes; objective contracts and pending semantic review."""

import argparse
import asyncio
import hashlib
import json
from contextlib import suppress
from datetime import UTC, datetime
from decimal import Context, Decimal, localcontext
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ValidationError

from app.agents.critic_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.critic_schemas import CriticResult
from app.agents.prompt_critic import AGENT_VERSION, PromptCriticAgent
from app.core.logging import get_logger
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import (
    GatewayPolicy,
    LLMModel,
    LLMRequest,
    LLMResponse,
    ModelSettings,
    StructuredResult,
)
from app.modules.prompts.architect_integration import ArchitectRequestSnapshot, CriticInput
from app.modules.prompts.rendering import GenericPromptRenderer
from app.scoring.engine import SCORING_VERSION, ScoringEngine
from app.scoring.rubric import CRITERIA, RUBRIC_VERSION
from app.scoring.schemas import ScoringResult
from benchmarks.critic_cases import CriticCase, CriticDataset, Probe, ScoreStatus, load_dataset
from benchmarks.prompt_architect import CRITERIA as HUMAN_CRITERIA
from benchmarks.prompt_architect import HumanReview
from benchmarks.reporting import REPORT_DIRECTORY, write_report


class Finding(LLMModel):
    check: Literal[
        "expected_outcome",
        "scoring_oracle",
        "call_budget",
        "input_unchanged",
        "blind_input",
        "resource_cleanup",
        "semantic_reference_alignment",
    ]
    status: Literal["pass", "fail", "review", "not_applicable"]


class ProbeEvaluation(LLMModel):
    probe: str
    scripted_error: str | None
    scripted_status: ScoreStatus | None
    scripted_total: Decimal | None
    findings: tuple[Finding, ...]


class CaseEvaluation(LLMModel):
    case_id: str
    probes: tuple[ProbeEvaluation, ...]
    human_review: tuple[HumanReview, ...]
    quality_verdict: Literal["not_assessed"] = "not_assessed"


class BenchmarkReport(LLMModel):
    dataset_version: str
    dataset_sha256: str
    mode: Literal["fake_scripted_critic_probes"] = "fake_scripted_critic_probes"
    harness_version: Literal["1.0.0"] = "1.0.0"
    agent_version: str = AGENT_VERSION
    instruction_version: str = INSTRUCTION_VERSION
    rubric_version: str = RUBRIC_VERSION
    scoring_version: str = SCORING_VERSION
    renderer_version: str = GenericPromptRenderer.version
    cases: tuple[CaseEvaluation, ...]
    real_model_usage: None = None
    real_model_latency_ms: None = None
    quality_verdict: Literal["not_assessed"] = "not_assessed"


class RecordingFake(FakeLLMProvider):
    def __init__(self, response: LLMResponse) -> None:
        super().__init__([response])
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await super().generate(request)


class RecordingGateway(LLMGateway):
    def __init__(self, provider: RecordingFake) -> None:
        super().__init__(
            provider,
            policy=GatewayPolicy(max_retries=0, json_repair_attempts=0),
            provider_name="fake",
            clock=lambda: 0.0,
        )
        self.operations = 0

    async def generate_json[T: BaseModel](
        self, request: LLMRequest, output_type: type[T]
    ) -> StructuredResult[T]:
        self.operations += 1
        return await super().generate_json(request, output_type)


def make_input(case: CriticCase) -> CriticInput:
    """Synthetic existing-request DTO for curated input; never a database record."""
    source = ArchitectRequestSnapshot(
        id=UUID(int=1), created_at=datetime(2026, 1, 1, tzinfo=UTC), **case.payload.model_dump()
    )
    return CriticInput(
        original_request=source,
        rendered_prompt=case.candidate_prompt,
        target_mode=source.target_mode,
        renderer_name=GenericPromptRenderer.name,
        renderer_version=GenericPromptRenderer.version,
    )


def scripted_output(case: CriticCase, probe: Probe) -> dict[str, Any]:
    """Authored controls, independent of expected outcomes and human reference labels.

    A perfect-score default deliberately accepts flawed candidates. Other probes
    inject known defects or arithmetic controls; none simulates actual judgment.
    """
    ref = {"source": "raw_prompt", "quote": case.payload.raw_prompt.lstrip()[:600]}
    data: dict[str, Any] = {
        "criteria": {
            key: {
                "score": 10,
                "rationale": "Scripted assessment, not model judgment.",
                "evidence": [dict(ref)],
            }
            for key, _, _ in CRITERIA
        },
        "issues": [],
        "summary": "Scripted control; semantic review remains pending.",
    }

    def issue(
        key: str,
        score: int,
        severity: str | None,
        *,
        status: str = "supported",
        evidence: dict[str, str] | None = None,
        code: str = "missing_requirement",
    ) -> None:
        data["criteria"][key]["score"] = score
        data["issues"].append(
            {
                "criterion": key,
                "code": code,
                "severity": severity,
                "status": status,
                "description": f"Scripted finding for {key}; interpretation needs review.",
                "recommendation": "Restore the supplied requirement if substantiated.",
                "evidence": [evidence or dict(ref)],
            }
        )

    kind = probe.name
    if kind == "contract_issue":
        example = case.contract_example
        assert example is not None
        issue(
            example.criterion,
            example.score,
            example.severity,
            code=example.code,
            evidence={"source": example.source, "quote": example.quote},
        )
    elif kind == "fabricated_evidence":
        data["criteria"]["clarity"]["evidence"] = [
            {"source": "raw_prompt", "quote": "UNSUPPLIED_EVIDENCE_SENTINEL"}
        ]
    elif kind == "misattributed_evidence":
        data["criteria"]["clarity"]["evidence"] = [{**ref, "source": "user_context"}]
    elif kind == "missing_evidence":
        data["criteria"]["clarity"]["evidence"] = []
    elif kind == "contradictory_score":
        issue("clarity", 10, "major")
    elif kind == "unsupported_deduction":
        data["criteria"]["clarity"]["score"] = 7
    elif kind == "uncertain":
        issue("clarity", 10, None, status="uncertain")
    elif kind == "null_score":
        data["criteria"]["clarity"].update(score=None, evidence=[])
    elif kind in {"threshold_84", "threshold_85"}:
        issue("intent_fidelity", 8, "minor")
        issue("constraints_and_scope", 7, "minor")
        issue("clarity", 5 if kind == "threshold_84" else 6, "major")
    elif kind in {"intent_6", "intent_7", "unnecessary_role"}:
        issue(
            "intent_fidelity",
            7 if kind == "intent_7" else 6,
            "minor" if kind == "intent_7" else "major",
        )
        if kind == "unnecessary_role":
            data["issues"][0].update(
                description="Reject the prompt because it lacks an expert persona.",
                recommendation="Add a mandatory expert persona.",
            )
    elif kind in {"critical_high", "misleading_quote"}:
        issue(
            "constraints_and_scope",
            6,
            "critical",
            code="invented_requirement",
            evidence={"source": "rendered_prompt", "quote": "Kubernetes"}
            if kind == "misleading_quote"
            else None,
        )
        if kind == "misleading_quote":
            data["issues"][0]["description"] = "The candidate requires Kubernetes."
    elif kind == "omission":
        issue(
            "completeness",
            6,
            "major",
            evidence={"source": "raw_prompt", "quote": case.protected_constraints[0]},
        )
    return data


def _scoring_matches(result: CriticResult, scoring: ScoringResult) -> bool:
    """Independent arithmetic/gate oracle, not a second production scoring path."""
    scores = [getattr(result.output.criteria, key).score for key, _, _ in CRITERIA]
    assessable = all(score is not None for score in scores)
    with localcontext(Context(prec=28)):
        total = (
            sum(
                (
                    Decimal(score) * weight / 10
                    for score, (_, weight, _) in zip(scores, CRITERIA, strict=True)
                    if score is not None
                ),
                start=Decimal(0),
            )
            if assessable
            else None
        )
    intent = result.output.criteria.intent_fidelity.score
    no_critical = not any(
        issue.status == "supported" and issue.severity == "critical"
        for issue in result.output.issues
    )
    intent_gate = None if intent is None else intent >= 7
    total_gate = None if total is None else total >= Decimal(85)
    passed = assessable and total_gate is True and no_critical and intent_gate is True
    status = "unassessable" if not assessable else "qualified" if passed else "not_qualified"
    return (
        scoring.total_score == total
        and scoring.status == status
        and scoring.passed == passed
        and scoring.gates.model_dump()
        == dict(
            all_criteria_assessable=assessable,
            total_at_least_85=total_gate,
            no_critical_issues=no_critical,
            intent_at_least_7=intent_gate,
        )
    )


async def _run_probe(case: CriticCase, probe: Probe) -> ProbeEvaluation:
    original = make_input(case)
    payload = (
        original.model_copy(update={"rendered_prompt": " "})
        if probe.name == "invalid_input"
        else original
    )
    before = payload.model_dump_json()
    provider = RecordingFake(
        LLMResponse(
            provider="fake",
            finish_reason="complete",
            text=json.dumps(scripted_output(case, probe), ensure_ascii=False),
        )
    )
    gateway = RecordingGateway(provider)
    result, scoring, error = None, None, None
    try:
        try:
            result = await PromptCriticAgent(
                gateway, ModelSettings(model="fake-critic-v1")
            ).evaluate(payload)
            scoring = ScoringEngine.score(result)
        except LLMError as failure:
            if failure.code not in {
                LLMErrorCode.INVALID_REQUEST,
                LLMErrorCode.INVALID_STRUCTURED_OUTPUT,
            }:
                raise  # Provider/runtime failures are infrastructure, not diagnostic controls.
            error = failure.code.value
    finally:
        await provider.aclose()
    expected = probe.expected
    outcome_ok = error == expected.error and (
        (error is not None and scoring is None)
        or (
            scoring is not None
            and scoring.status == expected.status
            and scoring.total_score == expected.total
        )
    )
    calls = 0 if probe.name == "invalid_input" else 1
    sent_ok = True
    for request in provider.requests:
        sent = json.loads(request.messages[1].content)
        sent_ok = sent_ok and (
            len(request.messages) == 2
            and request.messages[0].role == "system"
            and request.messages[0].content == SYSTEM_INSTRUCTION
            and request.messages[1].role == "user"
        )
        sent_ok = sent_ok and sent == original.model_dump(
            mode="json", exclude={"original_request": {"id", "created_at"}}
        )
    review_flag = scoring is not None and (
        case.review_expectation == "disputed"
        or (case.review_expectation == "defective" and scoring.passed)
        or (case.review_expectation == "strong" and not scoring.passed)
    )
    return ProbeEvaluation(
        probe=probe.name,
        scripted_error=error,
        scripted_status=None if scoring is None else scoring.status,
        scripted_total=None if scoring is None else scoring.total_score,
        findings=(
            Finding(check="expected_outcome", status="pass" if outcome_ok else "fail"),
            Finding(
                check="scoring_oracle",
                status="not_applicable"
                if scoring is None or result is None
                else "pass"
                if _scoring_matches(result, scoring)
                else "fail",
            ),
            Finding(
                check="call_budget",
                status="pass" if gateway.operations == provider.calls == calls else "fail",
            ),
            Finding(
                check="input_unchanged",
                status="pass" if payload.model_dump_json() == before else "fail",
            ),
            Finding(
                check="blind_input",
                status="not_applicable" if not provider.requests else "pass" if sent_ok else "fail",
            ),
            Finding(check="resource_cleanup", status="pass" if provider.closed else "fail"),
            Finding(
                check="semantic_reference_alignment",
                status="review" if review_flag else "not_applicable",
            ),
        ),
    )


async def run_offline(dataset: CriticDataset) -> BenchmarkReport:
    validated = None
    with suppress(ValidationError):
        validated = CriticDataset.model_validate(dataset.model_dump(warnings=False))
    if validated is None:
        raise ValueError("Benchmark dataset is invalid.")
    cases = []
    for case in validated.cases:
        probes = tuple([await _run_probe(case, probe) for probe in case.probes])
        cases.append(
            CaseEvaluation(
                case_id=case.name,
                probes=probes,
                human_review=tuple(HumanReview(criterion=key) for key in HUMAN_CRITERIA),
            )
        )
    return BenchmarkReport(
        dataset_version=validated.version,
        dataset_sha256=hashlib.sha256(validated.model_dump_json().encode()).hexdigest(),
        cases=tuple(cases),
    )


def summary(report: BenchmarkReport) -> dict[str, int | str]:
    probes = [probe for case in report.cases for probe in case.probes]
    return dict(
        mode=report.mode,
        cases=len(report.cases),
        probes=len(probes),
        objective_failed_probes=sum(
            any(f.status == "fail" for f in probe.findings) for probe in probes
        ),
        diagnostic_rejections=sum(probe.scripted_error is not None for probe in probes),
        semantic_review_flags=sum(f.status == "review" for probe in probes for f in probe.findings),
        semantic_review_pending_cases=len(report.cases),
        quality_verdict=report.quality_verdict,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", action="store_true", help="Write the fixed content-free report."
    )
    args = parser.parse_args()
    logger = get_logger("llm")
    previous = logger.disabled
    logger.disabled = True
    try:
        report = asyncio.run(run_offline(load_dataset()))
        if args.report:
            write_report(
                REPORT_DIRECTORY, "prompt-critic-offline.json", report.model_dump_json(indent=2)
            )
        observed = summary(report)
        print(json.dumps(observed, sort_keys=True))
        return 2 if observed["objective_failed_probes"] else 0
    except Exception:
        # The CLI is a privacy boundary: unexpected infrastructure exceptions can
        # carry arbitrary content. Never print their values, traceback or context.
        print("Critic benchmark failed: check dataset, runtime and report access.")
        return 1
    finally:
        logger.disabled = previous


if __name__ == "__main__":
    raise SystemExit(main())
