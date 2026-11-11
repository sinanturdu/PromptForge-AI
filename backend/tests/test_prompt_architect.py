"""Offline contract fixtures; these do not establish real-model semantic quality."""

import asyncio
import io
import json
import traceback
import warnings
from typing import Any

import pytest
from benchmarks.architect_cases import BenchmarkCase, load_dataset
from pydantic import BaseModel

from app.agents.architect_instructions import INSTRUCTION_VERSION, SYSTEM_INSTRUCTION
from app.agents.architect_schemas import ArchitectOutput
from app.agents.prompt_architect import PromptArchitectAgent
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers._openai_schema import validate_schema
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import (
    GatewayPolicy,
    LLMRequest,
    LLMResponse,
    ModelSettings,
    StructuredResult,
    TokenUsage,
)
from app.models.enums import PromptDetailLevel, TargetMode
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.schemas import PromptRequestCreate

CASES = tuple(case for case in load_dataset().cases if case.contract_example is not None)
PRIVATE = "SENSITIVE_PROMPT_SENTINEL"
SECRET = "synthetic-credential-sentinel"


def canonical(**updates: Any) -> dict[str, Any]:
    return {
        **CanonicalPrompt.from_task("Improve the supplied task.").model_dump(mode="json"),
        **updates,
    }


def wire(decision: str = "preserve", **updates: Any) -> str:
    return json.dumps(
        {"decision": decision, "canonical_prompt": None, "changes": [], "questions": [], **updates},
        ensure_ascii=False,
    )


def scripted(text: str, **updates: Any) -> LLMResponse:
    return LLMResponse(provider="fake", finish_reason="complete", text=text, **updates)


class RecordingFake(FakeLLMProvider):
    def __init__(self, script: list[LLMResponse | LLMErrorCode]) -> None:
        super().__init__(script)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await super().generate(request)


class RecordingGateway(LLMGateway):
    def __init__(self, provider: RecordingFake, *, repair: int = 0) -> None:
        super().__init__(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=repair))
        self.operations = 0

    async def generate_json[T: BaseModel](
        self,
        request: LLMRequest,
        output_type: type[T],
    ) -> StructuredResult[T]:
        self.operations += 1
        return await super().generate_json(request, output_type)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
@pytest.mark.parametrize("level", list(PromptDetailLevel))
async def test_representative_contract_cases(case: BenchmarkCase, level: PromptDetailLevel) -> None:
    payload = case.payload.model_copy(update={"prompt_detail_level": level}, deep=True)
    before = payload.model_dump()
    example = case.contract_example
    assert example is not None
    text = example.output.model_dump_json()
    provider = RecordingFake([scripted(text)])
    gateway = RecordingGateway(provider)
    agent = PromptArchitectAgent(gateway, ModelSettings(model="fake-v1"))
    try:
        result = await agent.create(payload, request_id="request-fixture")
    finally:
        await provider.aclose()
    assert gateway.operations == provider.calls == 1
    assert result.decision == example.output.decision
    assert result.instruction_version == INSTRUCTION_VERSION
    assert result.metadata.request_id == "request-fixture"
    assert payload.model_dump() == before
    sent = json.loads(provider.requests[0].messages[1].content)["request"]
    assert sent == payload.model_dump(mode="json")
    assert sent["prompt_detail_level"] == level.value
    assert provider.requests[0].messages[0].content == SYSTEM_INSTRUCTION
    if result.decision == "preserve":
        assert result.rendered_prompt == payload.raw_prompt
        assert result.canonical_prompt == CanonicalPrompt.from_task(payload.raw_prompt)
        assert result.changes == result.questions == ()
    elif result.decision == "rewrite":
        assert result.rendered_prompt is not None and result.questions == ()
        for required in example.required_fragments:
            assert required in result.rendered_prompt
        for forbidden in example.forbidden_fragments:
            assert forbidden not in result.rendered_prompt
        assert result.changes == example.output.changes
        assert not any(note in result.rendered_prompt for note in result.changes)
    else:
        assert result.rendered_prompt is None and result.canonical_prompt is None
        assert result.questions == example.output.questions
        assert result.changes == () and result.renderer_name is None


@pytest.mark.anyio
@pytest.mark.parametrize("target", list(TargetMode))
@pytest.mark.parametrize("decision", ["preserve", "rewrite"])
@pytest.mark.parametrize("legacy_generic", [False, True])
async def test_target_selects_renderer_without_selecting_provider(
    target: TargetMode, decision: str, legacy_generic: bool
) -> None:
    raw = "  Translate 'hi' into Turkish. Return only the translation.\n"
    task = "  Translate 'hi' into Turkish.\n"
    constraint = "Return only the translation."
    response = (
        wire()
        if decision == "preserve"
        else wire("rewrite", canonical_prompt=canonical(task=task, constraints=[constraint]))
    )
    provider = RecordingFake([scripted(response)])
    gateway = RecordingGateway(provider)
    settings = ModelSettings(model="explicit-internal-model", max_output_tokens=512)
    result = await PromptArchitectAgent(gateway, settings).create(
        PromptRequestCreate(raw_prompt=raw, target_mode=target), legacy_generic=legacy_generic
    )
    pilot = target is TargetMode.OPENAI and not legacy_generic
    rewritten = (
        "## Task\n\n" + task + "\n\n## Constraints\n\n" + constraint
        if pilot
        else task + "\n\n" + constraint
    )
    assert result.decision == decision
    assert result.rendered_prompt == (raw if decision == "preserve" else rewritten)
    assert result.target_mode == target
    assert result.metadata.provider == "fake"
    assert gateway.operations == provider.calls == 1
    assert provider.requests[0].settings == settings
    assert result.renderer_name == ("OpenAIPromptRenderer" if pilot else "GenericPromptRenderer")
    assert result.renderer_version == "1.0.0"
    await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "output",
    [
        wire("preserve", changes=["Unwanted explanation"]),
        wire("preserve", questions=["Optional detail?"]),
        wire("preserve", canonical_prompt=canonical()),
        wire("rewrite"),
        wire("rewrite", canonical_prompt=canonical(), questions=["Why?"]),
        wire("clarify"),
        wire("clarify", questions=["  "]),
        wire("clarify", questions=["Which?"], changes=["Not a prompt"]),
        wire("clarify", questions=["Which?"], canonical_prompt=canonical()),
        wire("clarify", questions=["A?", "B?", "C?", "D?"]),
        wire("rewrite", canonical_prompt=canonical(task=" ")),
        wire("rewrite", canonical_prompt=canonical(constraints=[""])),
        wire("rewrite", canonical_prompt=canonical(), changes=["change"] * 9),
        wire("clarify", questions=["x" * 1001]),
        wire("unexpected"),
        "malformed JSON",
    ],
)
async def test_invalid_decisions_and_responses_fail_safely(output: str) -> None:
    provider = RecordingFake([scripted(output)])
    gateway = RecordingGateway(provider)
    with pytest.raises(LLMError) as caught:
        await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(
            PromptRequestCreate(raw_prompt=PRIVATE)
        )
    assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
    assert caught.value.__context__ is None
    assert gateway.operations == provider.calls == 1
    await provider.aclose()


@pytest.mark.anyio
async def test_gateway_repair_is_one_agent_operation() -> None:
    provider = RecordingFake([scripted(wire("rewrite")), scripted(wire())])
    gateway = RecordingGateway(provider, repair=1)
    payload = PromptRequestCreate(raw_prompt="A clear original prompt.")
    result = await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(payload)
    assert result.rendered_prompt == payload.raw_prompt
    assert gateway.operations == 1 and provider.calls == 2
    assert result.metadata.repair_attempted and result.metadata.attempts == 2
    await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code",
    [
        LLMErrorCode.AUTHENTICATION,
        LLMErrorCode.TIMEOUT,
        LLMErrorCode.QUOTA_EXHAUSTED,
        LLMErrorCode.DEADLINE_EXCEEDED,
    ],
)
async def test_gateway_errors_propagate_without_agent_retry(code: LLMErrorCode) -> None:
    provider = RecordingFake([code])
    gateway = RecordingGateway(provider)
    with pytest.raises(LLMError) as caught:
        await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(
            PromptRequestCreate(raw_prompt=PRIVATE)
        )
    assert caught.value.code == code and caught.value.metadata.call is not None
    assert gateway.operations == provider.calls == 1
    await provider.aclose()


@pytest.mark.anyio
async def test_metadata_is_forwarded_and_content_stays_out_of_diagnostics() -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    usage = TokenUsage(input_tokens=10, output_tokens=20, total_tokens=30)
    provider = RecordingFake(
        [
            scripted(
                wire("rewrite", canonical_prompt=canonical(task=PRIVATE), changes=[SECRET]),
                usage=usage,
                resolved_model="fake-v1",
                upstream_request_id="req_fake",
            )
        ]
    )
    gateway = RecordingGateway(provider)
    result = await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(
        PromptRequestCreate(raw_prompt=PRIVATE, user_context=SECRET)
    )
    assert result.rendered_prompt == PRIVATE and result.changes == (SECRET,)
    assert result.metadata.usage == usage and result.metadata.upstream_request_id == "req_fake"
    visible = repr(result) + str(result) + repr(result.canonical_prompt) + stream.getvalue()
    assert PRIVATE not in visible and SECRET not in visible
    assert result.metadata.attempts == 1 and not provider.closed
    await provider.aclose()


@pytest.mark.anyio
async def test_invalid_mutated_input_never_leaks_or_calls_gateway() -> None:
    payload = PromptRequestCreate(raw_prompt=PRIVATE)
    payload.raw_prompt = [SECRET]  # type: ignore[assignment]
    provider = RecordingFake([])
    gateway = RecordingGateway(provider)
    with warnings.catch_warnings(record=True) as captured, pytest.raises(LLMError) as caught:
        warnings.simplefilter("always")
        await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(payload)
    assert not captured
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert PRIVATE not in str(caught.value) and SECRET not in str(caught.value)
    assert caught.value.__context__ is None
    assert gateway.operations == provider.calls == 0
    await provider.aclose()


@pytest.mark.anyio
async def test_private_validation_failure_has_no_raw_exception_context() -> None:
    provider = RecordingFake([scripted(wire("clarify", questions=[SECRET + "x" * 1001]))])
    gateway = RecordingGateway(provider)
    with pytest.raises(LLMError) as caught:
        await PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(
            PromptRequestCreate(raw_prompt=PRIVATE)
        )
    visible = "".join(traceback.format_exception(caught.value))
    assert SECRET not in visible and PRIVATE not in visible
    assert caught.value.__context__ is None
    await provider.aclose()


@pytest.mark.anyio
async def test_cancellation_propagates_without_closing_borrowed_resources() -> None:
    entered = asyncio.Event()

    class BlockingFake(RecordingFake):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("Unreachable")

    provider = BlockingFake([])
    gateway = RecordingGateway(provider)
    payload = PromptRequestCreate(raw_prompt="Original")
    task = asyncio.create_task(
        PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(payload)
    )
    await entered.wait()
    payload.raw_prompt = "Changed externally"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done() and gateway.operations == 1
    assert not provider.closed
    await provider.aclose()


def test_wire_schema_is_accepted_by_existing_openai_preflight() -> None:
    schema = ArchitectOutput.model_json_schema()
    validate_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


@pytest.mark.anyio
async def test_input_snapshot_survives_external_mutation_during_await() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class PausingFake(RecordingFake):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            entered.set()
            await release.wait()
            return await super().generate(request)

    provider = PausingFake([scripted(wire())])
    gateway = RecordingGateway(provider)
    payload = PromptRequestCreate(raw_prompt="Original prompt.\n", user_context="Original context.")
    task = asyncio.create_task(
        PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")).create(payload)
    )
    await entered.wait()
    payload.raw_prompt = "New external value."
    payload.user_context = "New context."
    release.set()
    result = await task
    assert result.rendered_prompt == "Original prompt.\n"
    sent = json.loads(provider.requests[0].messages[1].content)["request"]
    assert (
        sent["raw_prompt"] == "Original prompt.\n" and sent["user_context"] == "Original context."
    )
    assert payload.raw_prompt == "New external value."
    assert payload.user_context == "New context."
    await provider.aclose()
