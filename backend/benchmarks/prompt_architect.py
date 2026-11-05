"""Reproducible fake-preserve baseline and content-free, review-oriented findings."""

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from app.agents.architect_instructions import INSTRUCTION_VERSION
from app.agents.architect_schemas import ArchitectResult
from app.agents.prompt_architect import AGENT_VERSION, PromptArchitectAgent
from app.core.logging import get_logger
from app.llm.errors import LLMError
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import GatewayPolicy, LLMModel, LLMResponse, ModelSettings
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
from benchmarks.architect_cases import BenchmarkCase, BenchmarkDataset, load_dataset

Criterion = Literal[
    "intent_fidelity",
    "requirement_preservation",
    "outcome_clarity",
    "relevant_context",
    "no_invented_requirements",
    "appropriate_complexity",
    "decision_suitability",
    "task_suitability",
]
CRITERIA: tuple[Criterion, ...] = (
    "intent_fidelity",
    "requirement_preservation",
    "outcome_clarity",
    "relevant_context",
    "no_invented_requirements",
    "appropriate_complexity",
    "decision_suitability",
    "task_suitability",
)
REPORT_DIRECTORY = Path(__file__).resolve().parents[1] / "benchmark-reports"


class Finding(LLMModel):
    check: Literal[
        "result_contract",
        "metadata_present",
        "output_boundaries",
        "exact_preservation",
        "decision_reference_alignment",
        "required_literals",
        "forbidden_literals",
    ]
    status: Literal["pass", "fail", "review", "not_applicable"]
    severity: Literal["critical", "major", "minor"] | None = None
    rule_indices: tuple[int, ...] = ()


class HumanReview(LLMModel):
    criterion: Criterion
    status: Literal["pending"] = "pending"


class CaseEvaluation(LLMModel):
    case_id: str
    findings: tuple[Finding, ...]
    human_review: tuple[HumanReview, ...]
    quality_verdict: Literal["not_assessed"] = "not_assessed"


class BenchmarkReport(LLMModel):
    dataset_version: str
    dataset_sha256: str
    mode: Literal["fake_preserve_baseline"] = "fake_preserve_baseline"
    harness_version: Literal["1.0.0"] = "1.0.0"
    agent_version: str = AGENT_VERSION
    instruction_version: str = INSTRUCTION_VERSION
    renderer_version: str = GenericPromptRenderer.version
    cases: tuple[CaseEvaluation, ...]
    # Fake usage/latency are not measurements of a real model.
    real_model_usage: None = None
    real_model_latency_ms: None = None
    quality_verdict: Literal["not_assessed"] = "not_assessed"


def evaluate_candidate(case: BenchmarkCase, candidate_json: str) -> CaseEvaluation:
    """Assess observable facts only. No semantic scores or reference-text equality."""
    pending = tuple(HumanReview(criterion=criterion) for criterion in CRITERIA)
    result = None
    invalid_metadata = False
    try:
        result = ArchitectResult.model_validate_json(candidate_json)
    except ValidationError as exc:
        invalid_metadata = any(
            error["loc"]
            and error["loc"][0]
            in {
                "metadata",
                "agent_version",
                "instruction_version",
                "renderer_name",
                "renderer_version",
            }
            for error in exc.errors(include_input=False, include_context=False, include_url=False)
        )
    if result is None:
        failures = [Finding(check="result_contract", status="fail", severity="major")]
        if invalid_metadata:
            failures.append(Finding(check="metadata_present", status="fail", severity="major"))
        return CaseEvaluation(
            case_id=case.name,
            findings=tuple(failures),
            human_review=pending,
        )
    findings = [Finding(check="result_contract", status="pass")]
    metadata_ok = bool(
        result.agent_version.strip()
        and result.instruction_version.strip()
        and result.metadata.provider
        and result.metadata.requested_model
        and result.metadata.attempts >= 1
    )
    if result.decision == "clarify":
        metadata_ok = (
            metadata_ok and result.renderer_name is None and result.renderer_version is None
        )
        boundaries = (
            result.rendered_prompt is None
            and result.canonical_prompt is None
            and not result.changes
            and 1 <= len(result.questions) <= 3
            and all(question.strip() for question in result.questions)
        )
    else:
        metadata_ok = metadata_ok and bool(
            result.renderer_name
            and result.renderer_name.strip()
            and result.renderer_version
            and result.renderer_version.strip()
        )
        boundaries = bool(
            result.rendered_prompt
            and result.rendered_prompt.strip()
            and result.canonical_prompt is not None
            and not result.questions
        )
        if boundaries and result.decision == "rewrite":
            assert result.canonical_prompt is not None
            boundaries = result.rendered_prompt == GenericPromptRenderer().render(
                result.canonical_prompt
            )
        if result.decision == "preserve":
            boundaries = (
                boundaries
                and not result.changes
                and (result.canonical_prompt == CanonicalPrompt.from_task(case.payload.raw_prompt))
            )
    boundaries = boundaries and result.target_mode == case.payload.target_mode
    boundaries = (
        boundaries
        and len(result.changes) <= 8
        and len(result.questions) <= 3
        and all(note.strip() and len(note) <= 1000 for note in (*result.changes, *result.questions))
    )
    findings.extend(
        (
            Finding(
                check="metadata_present",
                status="pass" if metadata_ok else "fail",
                severity=None if metadata_ok else "major",
            ),
            Finding(
                check="output_boundaries",
                status="pass" if boundaries else "fail",
                severity=None if boundaries else "major",
            ),
        )
    )
    if result.decision == "preserve":
        exact = result.rendered_prompt == case.payload.raw_prompt
        findings.append(
            Finding(
                check="exact_preservation",
                status="pass" if exact else "fail",
                severity=None if exact else "major",
            )
        )
    else:
        findings.append(Finding(check="exact_preservation", status="not_applicable"))
    aligned = result.decision in case.acceptable_decisions
    findings.append(
        Finding(
            check="decision_reference_alignment",
            status="pass" if aligned else "fail",
            severity=None if aligned else "major",
        )
    )
    text = result.rendered_prompt
    if text is None:
        findings.extend(
            Finding(check=check, status="not_applicable")
            for check in ("required_literals", "forbidden_literals")
        )
    else:
        missing = tuple(
            index for index, literal in enumerate(case.required_literals) if literal not in text
        )
        flagged = tuple(
            index
            for index, literal in enumerate(case.forbidden_literals)
            if literal.casefold() in text.casefold()
        )
        findings.append(
            Finding(
                check="required_literals",
                status="not_applicable"
                if not case.required_literals
                else "fail"
                if missing
                else "pass",
                severity="critical" if missing else None,
                rule_indices=missing,
            )
        )
        findings.append(
            Finding(
                check="forbidden_literals",
                status="not_applicable"
                if not case.forbidden_literals
                else "review"
                if flagged
                else "pass",
                severity="major" if flagged else None,
                rule_indices=flagged,
            )
        )
    return CaseEvaluation(case_id=case.name, findings=tuple(findings), human_review=pending)


async def run_offline(dataset: BenchmarkDataset) -> BenchmarkReport:
    """Reference-independent preserve-only control, not simulated model quality.

    No provider selection/configuration or SDK imports. The fixed fake response is
    intentionally unaware of expected decisions and contract_example candidates.
    """
    evaluations: list[CaseEvaluation] = []
    response = LLMResponse(
        provider="fake",
        finish_reason="complete",
        text=('{"decision":"preserve","canonical_prompt":null,"changes":[],"questions":[]}'),
    )
    for case in dataset.cases:
        provider = FakeLLMProvider([response])
        gateway = LLMGateway(
            provider,
            policy=GatewayPolicy(max_retries=0, json_repair_attempts=0),
            provider_name="fake",
            clock=lambda: 0.0,
        )
        try:
            result = await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(
                case.payload
            )
            evaluations.append(evaluate_candidate(case, result.model_dump_json()))
        finally:
            await provider.aclose()
    digest = hashlib.sha256(dataset.model_dump_json().encode("utf-8")).hexdigest()
    return BenchmarkReport(
        dataset_version=dataset.version, dataset_sha256=digest, cases=tuple(evaluations)
    )


def summary(report: BenchmarkReport) -> dict[str, int | str]:
    counts = Counter(finding.status for case in report.cases for finding in case.findings)
    return {
        "mode": report.mode,
        "cases": len(report.cases),
        "objective_failed_cases": sum(
            any(f.status == "fail" for f in case.findings) for case in report.cases
        ),
        "literal_review_flags": counts["review"],
        "semantic_review_pending_cases": len(report.cases),
        "quality_verdict": report.quality_verdict,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true", help="Write a content-free JSON report.")
    args = parser.parse_args()
    # CLI diagnostics contain only the safe summary below, never agent payloads.
    gateway_logger = get_logger("llm")
    previous_gateway_disabled = gateway_logger.disabled
    gateway_logger.disabled = True
    try:
        report = asyncio.run(run_offline(load_dataset()))
    except (OSError, ValueError, LLMError):
        print("Benchmark failed: check dataset integrity and file access.")
        return 1
    finally:
        gateway_logger.disabled = previous_gateway_disabled
    if args.report:
        # Fixed generated-only location; never accept arbitrary prompt/.env output paths.
        directory = REPORT_DIRECTORY.resolve()
        if directory != REPORT_DIRECTORY.absolute() or directory.is_symlink():
            print("Benchmark report directory must not be redirected.")
            return 1
        destination = directory / "prompt-architect-offline.json"
        if destination.is_symlink():
            print("Benchmark report file must not be redirected.")
            return 1
        try:
            directory.mkdir(exist_ok=True)
            destination.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
        except OSError:
            print("Benchmark report could not be written.")
            return 1
    print(json.dumps(summary(report), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
