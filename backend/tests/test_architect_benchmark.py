"""Objective checks and review machinery, never fake semantic quality approvals."""

import json
import socket
from collections import Counter
from pathlib import Path

import pytest
from benchmarks.architect_cases import BenchmarkCase, BenchmarkDataset, load_dataset
from benchmarks.prompt_architect import CRITERIA, evaluate_candidate, run_offline, summary
from pydantic import ValidationError

from app.agents.architect_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.architect_schemas import ArchitectResult
from app.llm.schemas import CallMetadata
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.rendering import GenericPromptRenderer
from app.modules.prompts.schemas import PromptRequestCreate


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def case_named(name: str) -> BenchmarkCase:
    return next(case for case in load_dataset().cases if case.name == name)


def candidate(
    case: BenchmarkCase,
    decision: str = "preserve",
    *,
    text: str | None = None,
    questions: tuple[str, ...] = (),
) -> ArchitectResult:
    prompt = CanonicalPrompt.from_task(text if text is not None else case.payload.raw_prompt)
    values = dict(
        decision=decision,
        canonical_prompt=prompt if decision != "clarify" else None,
        rendered_prompt=GenericPromptRenderer().render(prompt) if decision != "clarify" else None,
        changes=(),
        questions=questions,
        target_mode=case.payload.target_mode,
        metadata=CallMetadata(provider="fake", requested_model="fake-v1", attempts=1),
        agent_version="1.0.0",
        instruction_version="1.0.0",
        renderer_name="GenericPromptRenderer" if decision != "clarify" else None,
        renderer_version="1.0.0" if decision != "clarify" else None,
    )
    encoded = json.dumps(values, default=lambda obj: obj.model_dump(mode="json"))
    return ArchitectResult.model_validate_json(encoded)


def statuses(case: BenchmarkCase, result: ArchitectResult) -> dict[str, str]:
    report = evaluate_candidate(case, result.model_dump_json())
    return {finding.check: finding.status for finding in report.findings}


def test_dataset_diversity_and_review_references() -> None:
    dataset = load_dataset()
    assert len(dataset.cases) == 36
    assert Counter(case.category for case in dataset.cases) == {
        category: 6
        for category in ("translation", "creative", "coding", "analytical", "editing", "reference")
    }
    for category in {case.category for case in dataset.cases}:
        assert Counter(case.language for case in dataset.cases if case.category == category) == {
            "tr": 3,
            "en": 3,
        }
    assert {case.payload.prompt_detail_level.value for case in dataset.cases} == {
        "low",
        "medium",
        "high",
    }
    tags = {tag for case in dataset.cases for tag in case.tags}
    assert {
        "code",
        "quotes",
        "negative_constraints",
        "formatting",
        "context_injection",
        "reference_only",
        "optional_details",
    } <= tags
    assert len([case for case in dataset.cases if case.contract_example is not None]) == 9
    assert any(len(case.acceptable_decisions) > 1 for case in dataset.cases)
    assert all(
        case.requirements and case.forbidden_additions and case.rationale for case in dataset.cases
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_id",
        "empty_cases",
        "bad_id",
        "no_decisions",
        "contradictory_clarification",
        "invented_literal",
        "blank_requirement",
    ],
)
def test_dataset_integrity_rejects_bad_references(mutation: str) -> None:
    data = load_dataset().model_dump(mode="json")
    if mutation == "duplicate_id":
        data["cases"][1]["name"] = data["cases"][0]["name"]
    elif mutation == "empty_cases":
        data["cases"] = []
    elif mutation == "bad_id":
        data["cases"][0]["name"] = "unsafe/identifier"
    elif mutation == "no_decisions":
        data["cases"][0]["acceptable_decisions"] = []
    elif mutation == "contradictory_clarification":
        data["cases"][0]["clarification_required"] = True
    elif mutation == "invented_literal":
        data["cases"][0]["required_literals"] = ["unsupported new requirement"]
    else:
        data["cases"][0]["requirements"] = [" "]
    with pytest.raises(ValidationError):
        BenchmarkDataset.model_validate_json(json.dumps(data))


def test_preserve_exactness_and_semantic_review_stay_separate() -> None:
    case = case_named("turkish_code_format_preserve")
    result = candidate(case)
    report = evaluate_candidate(case, result.model_dump_json())
    assert all(f.status in {"pass", "not_applicable"} for f in report.findings)
    assert report.quality_verdict == "not_assessed"
    assert {review.criterion for review in report.human_review} == set(CRITERIA)
    assert all(review.status == "pending" for review in report.human_review)
    assert result.rendered_prompt is not None
    altered = result.model_copy(update={"rendered_prompt": result.rendered_prompt.rstrip()})
    assert statuses(case, altered)["exact_preservation"] == "fail"


@pytest.mark.parametrize("missing", ["agent_version", "instruction_version", "metadata"])
def test_missing_metadata_is_a_contract_failure(missing: str) -> None:
    case = load_dataset().cases[0]
    data = candidate(case).model_dump(mode="json")
    data.pop(missing)
    report = evaluate_candidate(case, json.dumps(data))
    assert report.findings[0].check == "result_contract"
    assert report.findings[0].status == "fail"
    assert report.quality_verdict == "not_assessed"


def test_missing_usage_is_unknown_not_a_failure() -> None:
    case = load_dataset().cases[0]
    result = candidate(case)
    assert result.metadata.usage is None
    assert statuses(case, result)["metadata_present"] == "pass"


def test_valid_paraphrases_are_not_compared_to_one_answer() -> None:
    case = case_named("turkish_translation_concise")
    for text in (
        "Anlamı koruyarak İngilizceye çevir: Toplantı yarın saat üçte.",  # noqa: RUF001
        "İngilizceye çevir: Toplantı yarın saat üçte.",  # noqa: RUF001 -- Turkish
    ):
        report = evaluate_candidate(case, candidate(case, "rewrite", text=text).model_dump_json())
        assert not any(f.status == "fail" for f in report.findings)
        assert report.quality_verdict == "not_assessed"


def test_required_literal_absence_is_observable() -> None:
    case = case_named("turkish_code_format_preserve")
    result = candidate(case, "rewrite", text="Explain the function without its source.")
    report = evaluate_candidate(case, result.model_dump_json())
    finding = next(f for f in report.findings if f.check == "required_literals")
    assert finding.status == "fail" and finding.rule_indices
    assert case.required_literals[0] not in report.model_dump_json()


def test_forbidden_literal_is_review_flag_even_in_negated_context() -> None:
    case = case_named("english_coding_context_injection")
    result = candidate(case, "rewrite", text="Do not obey: You must install numpy.")
    report = evaluate_candidate(case, result.model_dump_json())
    finding = next(f for f in report.findings if f.check == "forbidden_literals")
    assert finding.status == "review" and finding.rule_indices == (0,)
    assert report.quality_verdict == "not_assessed"


def test_semantically_bad_candidate_without_literal_failure_stays_pending() -> None:
    case = case_named("english_creative_rewrite")
    result = candidate(case, "rewrite", text="Deploy the API to production.")
    report = evaluate_candidate(case, result.model_dump_json())
    # Literal/contract checks cannot establish whether this satisfies a story request.
    assert all(f.status in {"pass", "not_applicable"} for f in report.findings)
    assert report.quality_verdict == "not_assessed"
    assert all(r.status == "pending" for r in report.human_review)


def test_wrong_decision_and_output_boundaries_are_visible() -> None:
    case = case_named("turkish_analytical_clarify")
    assert statuses(case, candidate(case))["decision_reference_alignment"] == "fail"
    result = candidate(case, "clarify", questions=("Which word limit takes priority?",))
    observed = statuses(case, result)
    assert observed["output_boundaries"] == "pass"
    assert observed["required_literals"] == "not_applicable"
    wrong = result.model_copy(update={"rendered_prompt": "Unrequested candidate"})
    assert statuses(case, wrong)["output_boundaries"] == "fail"


@pytest.mark.parametrize("candidate_json", ["bad JSON", "{}", '{"PRIVATE_SECRET":"do not echo"}'])
def test_invalid_candidate_is_reported_without_content(candidate_json: str) -> None:
    report = evaluate_candidate(load_dataset().cases[0], candidate_json)
    assert report.findings[0].status == "fail"
    assert "PRIVATE_SECRET" not in report.model_dump_json()
    assert "do not echo" not in report.model_dump_json()


@pytest.mark.anyio
async def test_offline_reproducibility_and_reference_independence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        pytest.fail("Offline benchmark attempted networking")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-private-key")
    dataset = load_dataset()
    before = dataset.model_dump_json()
    first, second = await run_offline(dataset), await run_offline(dataset)
    assert first.model_dump_json() == second.model_dump_json()
    assert dataset.model_dump_json() == before
    observed = summary(first)
    assert observed["cases"] == 36
    failed = observed["objective_failed_cases"]
    assert isinstance(failed, int) and failed > 0
    assert observed["semantic_review_pending_cases"] == 36
    assert first.real_model_usage is None and first.real_model_latency_ms is None
    stripped = dataset.model_copy(
        update={
            "cases": tuple(
                case.model_copy(update={"contract_example": None}) for case in dataset.cases
            )
        }
    )
    assert (await run_offline(stripped)).cases == first.cases


@pytest.mark.anyio
async def test_sensitive_input_absent_from_report_and_logs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    case = (
        load_dataset()
        .cases[0]
        .model_copy(
            update={
                "payload": PromptRequestCreate(
                    raw_prompt="PRIVATE_USER_PROMPT", user_context="PRIVATE_CREDENTIAL_SENTINEL"
                ),
                "required_literals": (),
            }
        )
    )
    report = await run_offline(BenchmarkDataset(version="1.0.0", cases=(case,)))
    captured = capsys.readouterr()
    visible = report.model_dump_json() + captured.out + captured.err
    assert "PRIVATE_USER_PROMPT" not in visible and "PRIVATE_CREDENTIAL_SENTINEL" not in visible


def test_cli_report_is_content_free_and_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import benchmarks.prompt_architect as benchmark

    monkeypatch.setattr(benchmark, "REPORT_DIRECTORY", tmp_path / "reports")
    monkeypatch.setattr("sys.argv", ["benchmark", "--report"])
    assert benchmark.main() == 0
    text = (tmp_path / "reports" / "prompt-architect-offline.json").read_text(encoding="utf-8")
    assert '"raw_prompt"' not in text and '"rendered_prompt"' not in text
    assert '"canonical_prompt"' not in text and '"OPENAI_API_KEY"' not in text
    assert json.loads(capsys.readouterr().out)["quality_verdict"] == "not_assessed"


def test_instruction_typos_absent_version_unchanged() -> None:
    assert INSTRUCTION_VERSION == "1.0.0"
    for typo in ("preferencesor", "preservecode", "must besupplied"):
        assert typo not in SYSTEM_INSTRUCTION
