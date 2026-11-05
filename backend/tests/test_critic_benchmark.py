"""Adversarial software controls; no claims about genuine Critic judgment accuracy."""

import io
import json
import os
import socket
import traceback
from collections import Counter
from decimal import ROUND_HALF_UP, Decimal, localcontext
from pathlib import Path
from typing import Any

import pytest
from benchmarks.critic_cases import CriticCase, CriticDataset, Probe, load_dataset
from benchmarks.prompt_critic import (
    RecordingFake,
    RecordingGateway,
    make_input,
    run_offline,
    scripted_output,
    summary,
)
from benchmarks.reporting import write_report
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.critic_schemas import CriticResult
from app.agents.prompt_critic import PromptCriticAgent
from app.core.logging import configure_logging, get_logger
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import LLMRequest, LLMResponse, ModelSettings
from app.modules.prompts.schemas import PromptRequestCreate
from app.scoring.engine import ScoringEngine
from app.scoring.rubric import CRITERIA

PRIVATE = "PRIVATE_BENCHMARK_PROMPT_SENTINEL"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def named(name: str) -> CriticCase:
    return next(case for case in load_dataset().cases if case.name == name)


async def evaluate(case: CriticCase, probe: Probe | None = None) -> CriticResult:
    provider = RecordingFake(
        LLMResponse(
            provider="fake",
            finish_reason="complete",
            text=json.dumps(scripted_output(case, probe or case.probes[0]), ensure_ascii=False),
        )
    )
    gateway = RecordingGateway(provider)
    try:
        return await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(
            make_input(case)
        )
    finally:
        await provider.aclose()


def test_dataset_distribution_and_original_thirteen_examples_preserved() -> None:
    dataset = load_dataset()
    assert dataset.version == "1.0.0" and len(dataset.cases) == 36
    assert Counter(case.language for case in dataset.cases) == {"en": 18, "tr": 18}
    assert Counter(case.task_type for case in dataset.cases) == dict.fromkeys(
        ["coding", "creative", "translation", "editing", "analytical", "reference"], 6
    )
    for task in {case.task_type for case in dataset.cases}:
        assert Counter(case.language for case in dataset.cases if case.task_type == task) == {
            "en": 3,
            "tr": 3,
        }
    assert len({case.name for case in dataset.cases}) == 36
    assert sum(case.contract_example is not None for case in dataset.cases) == 13
    assert {case.review_expectation for case in dataset.cases} == {
        "strong",
        "defective",
        "disputed",
    }
    assert {case.category for case in dataset.cases} == {
        "strong",
        "intent_preservation",
        "overprescription",
        "injection",
        "evidence_scoring",
    }
    assert sum(len(case.probes) for case in dataset.cases) == 43
    assert dict((key, weight) for key, weight, _ in CRITERIA) == dict(
        intent_fidelity=25,
        clarity=10,
        completeness=15,
        structure=5,
        context_and_role=5,
        constraints_and_scope=20,
        output_format=5,
        specificity=10,
        consistency=5,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_id",
        "unsafe_id",
        "empty_candidate",
        "missing_reference",
        "duplicate_probe",
        "missing_example",
        "wrong_example_text",
        "contradictory_expectation",
        "nonfinite_total",
    ],
)
def test_dataset_rejects_inconsistent_curated_references(mutation: str) -> None:
    data = load_dataset().model_dump(mode="json")
    first = data["cases"][0]
    if mutation == "duplicate_id":
        data["cases"].append(first)
    elif mutation == "unsafe_id":
        first["name"] = PRIVATE
    elif mutation == "empty_candidate":
        first["candidate_prompt"] = " "
    elif mutation == "missing_reference":
        first["human_review_considerations"] = []
    elif mutation == "duplicate_probe":
        first["probes"] *= 2
    elif mutation == "missing_example":
        first["contract_example"] = None
    elif mutation == "wrong_example_text":
        first["contract_example"]["raw"] = PRIVATE
    elif mutation == "contradictory_expectation":
        first["probes"][0]["expected"]["status"] = "unassessable"
    else:
        first["probes"][0]["expected"]["total"] = "NaN"
    with pytest.raises(ValidationError):
        CriticDataset.model_validate_json(json.dumps(data))


@pytest.mark.anyio
async def test_every_curated_probe_uses_actual_agent_and_scorer_without_network_or_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline Critic benchmark attempted a forbidden side effect")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(Engine, "connect", forbidden)
    for method in ("add", "execute", "flush", "commit"):
        monkeypatch.setattr(AsyncSession, method, forbidden)
    monkeypatch.setattr("app.llm.composition.create_provider", forbidden)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", PRIVATE)
    dataset = load_dataset()
    before = dataset.model_dump_json()
    first, second = await run_offline(dataset), await run_offline(dataset)
    assert first.model_dump_json() == second.model_dump_json()
    assert dataset.model_dump_json() == before
    observed = summary(first)
    assert observed == dict(
        mode="fake_scripted_critic_probes",
        cases=36,
        probes=43,
        objective_failed_probes=0,
        diagnostic_rejections=5,
        semantic_review_flags=20,
        semantic_review_pending_cases=36,
        quality_verdict="not_assessed",
    )
    assert first.real_model_usage is first.real_model_latency_ms is None
    assert first.agent_version == first.instruction_version == first.scoring_version == "1.0.0"
    assert (
        first.dataset_version == first.harness_version == "1.0.0"
        and first.rubric_version == "2.0.0"
    )
    assert all(
        case.quality_verdict == "not_assessed"
        and all(review.status == "pending" for review in case.human_review)
        for case in first.cases
    )


@pytest.mark.anyio
async def test_human_reference_labels_do_not_change_fake_provider_responses() -> None:
    case = named("scope_expansion_tr")
    original = await run_offline(CriticDataset(version="1.0.0", cases=(case,)))
    alternate = case.model_copy(
        update={"review_expectation": "strong", "intended_behavior": PRIVATE, "rationale": PRIVATE}
    )
    changed = await run_offline(CriticDataset(version="1.0.0", cases=(alternate,)))
    assert scripted_output(case, case.probes[0]) == scripted_output(alternate, alternate.probes[0])
    before, after = original.cases[0].probes[0], changed.cases[0].probes[0]
    assert before.scripted_total == after.scripted_total == Decimal(100)
    assert before.scripted_status == after.scripted_status == "qualified"
    assert before.findings[-1].status == "review" and after.findings[-1].status == "not_applicable"
    assert changed.quality_verdict == "not_assessed"


@pytest.mark.anyio
async def test_literal_evidence_can_support_a_semantically_wrong_interpretation() -> None:
    case = named("misleading_negated_quote_en")
    result = await evaluate(case)
    assert result.output.issues[0].evidence[0].quote in case.candidate_prompt
    assert "Do not use Kubernetes" in case.candidate_prompt
    assert ScoringEngine.score(result).status == "not_qualified"
    report = await run_offline(CriticDataset(version="1.0.0", cases=(case,)))
    assert all(f.status != "fail" for f in report.cases[0].probes[0].findings)
    assert report.cases[0].probes[0].findings[-1].status == "review"
    assert report.quality_verdict == "not_assessed"


@pytest.mark.anyio
async def test_omission_requires_only_an_original_source_quote() -> None:
    result = await evaluate(named("omission_source_only_en"))
    issue = result.output.issues[0]
    assert issue.evidence and all(ref.source == "raw_prompt" for ref in issue.evidence)
    assert issue.code == "missing_requirement"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation",
    [
        "score",
        "quote_type",
        "source",
        "severity",
        "uncertainty",
        "metadata",
        "missing_finding",
        "unknown_criterion",
    ],
)
async def test_nested_model_copy_tampering_is_revalidated_before_scoring(mutation: str) -> None:
    valid = await evaluate(named("missing_offline_constraint"))
    output = valid.output
    if mutation in {"score", "quote_type", "source", "missing_finding"}:
        assessment = output.criteria.clarity
        if mutation == "score":
            assessment = assessment.model_copy(update={"score": True})
        elif mutation == "missing_finding":
            assessment = assessment.model_copy(update={"score": 7})
        else:
            ref = assessment.evidence[0].model_copy(
                update={"quote": {PRIVATE: PRIVATE}}
                if mutation == "quote_type"
                else {"source": PRIVATE}
            )
            assessment = assessment.model_copy(update={"evidence": (ref,)})
        output = output.model_copy(
            update={"criteria": output.criteria.model_copy(update={"clarity": assessment})}
        )
    elif mutation == "metadata":
        valid = valid.model_copy(
            update={"metadata": valid.metadata.model_copy(update={"attempts": PRIVATE})}
        )
    else:
        updates = (
            {"severity": "minor"}
            if mutation == "severity"
            else {"status": "uncertain"}
            if mutation == "uncertainty"
            else {"criterion": PRIVATE}
        )
        # Raising a major/critical score to 9 makes the changed minor severity invalid too.
        if mutation == "severity":
            assessment = output.criteria.constraints_and_scope.model_copy(update={"score": 9})
            output = output.model_copy(
                update={
                    "criteria": output.criteria.model_copy(
                        update={"constraints_and_scope": assessment}
                    )
                }
            )
        output = output.model_copy(
            update={"issues": (output.issues[0].model_copy(update=updates),)}
        )
    tampered = valid.model_copy(update={"output": output})
    with pytest.raises(LLMError) as caught:
        ScoringEngine.score(tampered)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert caught.value.__context__ is None
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))


@pytest.mark.anyio
@pytest.mark.parametrize(
    "mutation", ["raw_type", "raw_blank", "context_type", "detail", "identity"]
)
async def test_nested_blind_input_tampering_fails_before_a_gateway_operation(mutation: str) -> None:
    case = named("concise_translation_en")
    payload = make_input(case)
    updates = (
        {"raw_prompt": 5}
        if mutation == "raw_type"
        else {"raw_prompt": " "}
        if mutation == "raw_blank"
        else {"user_context": {PRIVATE: PRIVATE}}
        if mutation == "context_type"
        else {"prompt_detail_level": PRIVATE}
        if mutation == "detail"
        else {"id": PRIVATE}
    )
    payload = payload.model_copy(
        update={"original_request": payload.original_request.model_copy(update=updates)}
    )
    provider = RecordingFake(LLMResponse(provider="fake", finish_reason="complete", text="{}"))
    gateway = RecordingGateway(provider)
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(payload)
        assert caught.value.code == LLMErrorCode.INVALID_REQUEST
        assert caught.value.__context__ is None and gateway.operations == provider.calls == 0
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_rounding_cannot_turn_a_reported_84_point_5_into_a_pass() -> None:
    case = named("score_boundary_84_tr")
    data = scripted_output(case, case.probes[0])
    # A clarity increase adds one point; a small structure-score decrease
    # subtracts half a point, giving exactly 84.5 under the unchanged rubric.
    data["criteria"]["clarity"]["score"] = 6
    data["criteria"]["structure"]["score"] = 9
    provider = RecordingFake(
        LLMResponse(provider="fake", finish_reason="complete", text=json.dumps(data))
    )
    try:
        result = await PromptCriticAgent(
            RecordingGateway(provider), ModelSettings(model="fake-v1")
        ).evaluate(make_input(case))
    finally:
        await provider.aclose()
    with localcontext() as context:
        context.prec = 1
        score = ScoringEngine.score(result)
    assert score.total_score == Decimal("84.5")
    assert score.total_score.quantize(Decimal(1), rounding=ROUND_HALF_UP) == Decimal(85)
    assert not score.passed and not score.gates.total_at_least_85


@pytest.mark.anyio
async def test_unexpected_provider_exceptions_are_content_free_and_not_retried() -> None:
    class FailingFake(RecordingFake):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise RuntimeError(PRIVATE)

    provider = FailingFake(LLMResponse(provider="fake", finish_reason="complete", text="{}"))
    gateway = RecordingGateway(provider)
    try:
        with pytest.raises(LLMError) as caught:
            await PromptCriticAgent(gateway, ModelSettings(model="fake-v1")).evaluate(
                make_input(named("concise_translation_en"))
            )
        assert gateway.operations == 1
        assert caught.value.code == LLMErrorCode.PROVIDER_UNAVAILABLE
        assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_private_content_absent_from_report_reprs_and_logs() -> None:
    stream = io.StringIO()
    configure_logging(stream=stream)
    case = named("concise_translation_en").model_copy(
        update={
            "payload": PromptRequestCreate(raw_prompt=PRIVATE, user_context=PRIVATE),
            "candidate_prompt": PRIVATE,
            "protected_constraints": (PRIVATE,),
            "intended_behavior": PRIVATE,
            "rationale": PRIVATE,
            "human_review_considerations": (PRIVATE,),
        }
    )
    before = case.model_dump_json()
    report = await run_offline(CriticDataset(version="1.0.0", cases=(case,)))
    assert case.model_dump_json() == before
    assert PRIVATE not in report.model_dump_json() + repr(report) + repr(case) + stream.getvalue()
    assert (
        '"raw_prompt"' not in report.model_dump_json() and '"quote"' not in report.model_dump_json()
    )


def test_cli_reproducible_report_is_ignored_and_explains_pending_review(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.prompt_critic as cli

    monkeypatch.setattr(cli, "REPORT_DIRECTORY", tmp_path / "reports")
    monkeypatch.setattr("sys.argv", ["benchmark", "--report"])
    assert cli.main() == 0
    artifact = tmp_path / "reports" / "prompt-critic-offline.json"
    first = artifact.read_bytes()
    assert cli.main() == 0 and first == artifact.read_bytes()
    assert not list(artifact.parent.glob(".benchmark-*.tmp"))
    assert all(
        json.loads(line)["quality_verdict"] == "not_assessed"
        for line in capsys.readouterr().out.splitlines()
    )
    ignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text()
    assert "backend/benchmark-reports/" in ignore


def test_cli_uses_exit_two_for_objective_regressions(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.prompt_critic as cli

    dataset = load_dataset()
    case = named("concise_translation_en")
    expected = case.probes[0].expected.model_copy(update={"total": Decimal(99)})
    probe = case.probes[0].model_copy(update={"expected": expected})
    changed = dataset.model_copy(update={"cases": (case.model_copy(update={"probes": (probe,)}),)})
    monkeypatch.setattr(cli, "load_dataset", lambda: changed)
    monkeypatch.setattr("sys.argv", ["benchmark"])
    assert cli.main() == 2
    assert json.loads(capsys.readouterr().out)["objective_failed_probes"] == 1


@pytest.mark.parametrize("error_type", [RuntimeError, ValueError, OSError])
def test_cli_infrastructure_failures_and_logs_are_safe(
    error_type: type[Exception], monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.prompt_critic as cli

    def fail() -> CriticDataset:
        raise error_type(PRIVATE)

    logger = get_logger("llm")
    previous = logger.disabled
    monkeypatch.setattr(cli, "load_dataset", fail)
    monkeypatch.setattr("sys.argv", ["benchmark"])
    assert cli.main() == 1 and logger.disabled == previous
    captured = capsys.readouterr()
    assert PRIVATE not in captured.out + captured.err
    assert "failed" in captured.out and not captured.err


def test_shared_report_writer_preserves_hardlinked_sources(tmp_path: Path) -> None:
    source = tmp_path / "private.txt"
    source.write_text(PRIVATE)
    directory = tmp_path / "reports"
    directory.mkdir()
    destination = directory / "prompt-critic-offline.json"
    os.link(source, destination)
    write_report(directory, "prompt-critic-offline.json", "{}")
    assert source.read_text() == PRIVATE and destination.read_text() == "{}\n"
    assert not list(directory.glob(".benchmark-*.tmp"))


def test_redirected_report_destination_is_rejected_without_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "reports"
    original_resolve = Path.resolve

    def redirect(path: Path, *args: Any, **kwargs: Any) -> Path:
        return (
            tmp_path / "private" if path == directory else original_resolve(path, *args, **kwargs)
        )

    monkeypatch.setattr(Path, "resolve", redirect)
    with pytest.raises(ValueError):
        write_report(directory, "prompt-critic-offline.json", "{}")
    assert not directory.exists() and not (tmp_path / "private").exists()


def test_report_writer_cleans_temporary_file_after_failed_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*args: object, **kwargs: object) -> None:
        raise OSError(PRIVATE)

    monkeypatch.setattr("benchmarks.reporting.os.replace", fail)
    with pytest.raises(OSError):
        write_report(tmp_path, "prompt-critic-offline.json", "{}")
    assert not list(tmp_path.iterdir())


def test_report_writer_rejects_arbitrary_filename(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        write_report(tmp_path, "../private.txt", "{}")  # type: ignore[arg-type]
    assert not list(tmp_path.iterdir())


def test_cli_reports_provider_failures_as_infrastructure_not_expected_diagnostics(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.prompt_critic as cli

    async def fail(self: RecordingFake, request: LLMRequest) -> LLMResponse:
        raise LLMError(LLMErrorCode.SCRIPT_EXHAUSTED)

    monkeypatch.setattr(RecordingFake, "generate", fail)
    monkeypatch.setattr("sys.argv", ["benchmark"])
    assert cli.main() == 1
    visible = capsys.readouterr()
    assert "failed" in visible.out and not visible.err


def test_protected_reference_cannot_be_invented() -> None:
    data = named("concise_translation_en").model_dump(mode="json")
    data["protected_constraints"] = [PRIVATE]
    with pytest.raises(ValidationError):
        CriticCase.model_validate_json(json.dumps(data))
