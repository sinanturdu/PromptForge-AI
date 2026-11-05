"""Offline identity binding, candidate eligibility and blind evaluation projections."""

import asyncio
import io
import json
import traceback
import warnings
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.architect_schemas import ArchitectResult
from app.agents.prompt_architect import PromptArchitectAgent
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import CallMetadata, GatewayPolicy, LLMRequest, LLMResponse, ModelSettings
from app.models.enums import TargetMode
from app.modules.prompts.architect_integration import (
    ArchitectRunContext,
    CandidateReady,
    ClarificationRequired,
    create_architect_handoff,
    make_critic_input,
)
from app.modules.prompts.canonical import CanonicalPrompt
from app.modules.prompts.schemas import PromptRequestCreate, PromptRequestRead

REQUEST_ID = UUID("00000000-0000-0000-0000-000000000001")
WORKFLOW_ID = UUID("00000000-0000-0000-0000-000000000002")
AGENT_ID = UUID("00000000-0000-0000-0000-000000000003")
OTHER_ID = UUID("00000000-0000-0000-0000-000000000004")
PRIVATE = "PRIVATE_REQUEST_CONTENT_SENTINEL"
NOTE = "PRIVATE_ARCHITECT_NOTE_SENTINEL"


def source() -> PromptRequestRead:
    payload = PromptRequestCreate(
        raw_prompt="  Explain this Python 3.12 function without editing it.\n\n"
        "```python\ndef f(x):\n    return x * 2\n```\n",
        user_context=PRIVATE,
        target_mode=TargetMode.GOOGLE,
    )
    return PromptRequestRead(
        id=REQUEST_ID, created_at=datetime(2026, 1, 1, tzinfo=UTC), **payload.model_dump()
    )


def run_context() -> ArchitectRunContext:
    return ArchitectRunContext(
        prompt_request_id=REQUEST_ID,
        workflow_run_id=WORKFLOW_ID,
        workflow_prompt_request_id=REQUEST_ID,
        agent_run_id=AGENT_ID,
        agent_workflow_run_id=WORKFLOW_ID,
    )


class RecordingFake(FakeLLMProvider):
    def __init__(self, script: list[LLMResponse | LLMErrorCode]) -> None:
        super().__init__(script)
        self.requests: list[LLMRequest] = []

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        return await super().generate(request)


def agent_for(decision: str = "preserve") -> tuple[PromptArchitectAgent, RecordingFake]:
    canonical = CanonicalPrompt.from_task("Explain the supplied function without editing it.")
    output = dict(
        decision=decision,
        canonical_prompt=(canonical.model_dump(mode="json") if decision == "rewrite" else None),
        changes=[NOTE] if decision == "rewrite" else [],
        questions=["Which source function should be examined?"] if decision == "clarify" else [],
    )
    provider = RecordingFake(
        [LLMResponse(provider="fake", finish_reason="complete", text=json.dumps(output))]
    )
    gateway = LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))
    return PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")), provider


class ReturningArchitect(PromptArchitectAgent):
    """Typed bad-result/failure double; no provider or database implementation."""

    def __init__(
        self, result: ArchitectResult | None = None, failure: Exception | None = None
    ) -> None:
        self.result, self.failure = result, failure
        self.calls = 0

    async def create(
        self, payload: PromptRequestCreate, *, request_id: str | None = None
    ) -> ArchitectResult:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        assert self.result is not None
        return self.result


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
@pytest.mark.parametrize("decision", ["preserve", "rewrite", "clarify"])
async def test_explicit_handoff_outcomes(decision: str) -> None:
    request = source()
    before = request.model_dump()
    agent, provider = agent_for(decision)
    try:
        outcome = await create_architect_handoff(
            agent, request, context=run_context(), request_id="correlation-1"
        )
        assert outcome.context == run_context()
        assert outcome.original_request.model_dump() == before
        assert outcome.metadata.request_id == "correlation-1"
        assert outcome.metadata.attempts == 1 and outcome.metadata.usage is None
        assert provider.calls == 1 and not provider.closed
        assert json.loads(provider.requests[0].messages[1].content)["request"] == (
            request.model_dump(mode="json", exclude={"id", "created_at"})
        )
        if isinstance(outcome, CandidateReady):
            assert outcome.status == "candidate_ready" and outcome.decision == decision
            critic = make_critic_input(outcome)
            assert critic.rendered_prompt == outcome.rendered_prompt
            assert critic.original_request.id == REQUEST_ID
            assert critic.target_mode == TargetMode.GOOGLE
            assert set(critic.model_dump()) == {
                "original_request",
                "rendered_prompt",
                "target_mode",
                "renderer_name",
                "renderer_version",
            }
            assert NOTE not in critic.model_dump_json()
            assert "metadata" not in type(critic).model_fields
            if decision == "preserve":
                assert outcome.rendered_prompt == request.raw_prompt
                assert outcome.canonical_prompt == CanonicalPrompt.from_task(request.raw_prompt)
                assert outcome.changes == ()
            else:
                assert outcome.changes == (NOTE,) and NOTE not in outcome.rendered_prompt
        else:
            assert isinstance(outcome, ClarificationRequired)
            assert outcome.status == "clarification_required" and outcome.questions
            assert not hasattr(outcome, "canonical_prompt") and not hasattr(
                outcome, "rendered_prompt"
            )
            assert not hasattr(outcome, "version_number")
            with pytest.raises(LLMError) as caught:
                make_critic_input(outcome)  # type: ignore[arg-type]
            assert caught.value.code == LLMErrorCode.INVALID_REQUEST
        assert request.model_dump() == before
    finally:
        await provider.aclose()


@pytest.mark.anyio
async def test_no_synthetic_run_or_version_identifiers() -> None:
    agent, provider = agent_for()
    outcome = await create_architect_handoff(agent, source())
    assert outcome.context == ArchitectRunContext(prompt_request_id=REQUEST_ID)
    assert not hasattr(outcome, "id") and not hasattr(outcome, "version_number")
    await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {
            "prompt_request_id": OTHER_ID,
            "workflow_run_id": None,
            "workflow_prompt_request_id": None,
            "agent_run_id": None,
            "agent_workflow_run_id": None,
        },
        {"workflow_prompt_request_id": OTHER_ID},
        {"agent_workflow_run_id": OTHER_ID},
        {"workflow_run_id": None, "workflow_prompt_request_id": None},
        {"agent_run_id": None},
        {"workflow_prompt_request_id": None},
    ],
)
async def test_cross_request_and_run_links_rejected_before_generation(
    updates: dict[str, Any],
) -> None:
    binding = run_context().model_copy(update=updates)
    agent, provider = agent_for()
    with pytest.raises(LLMError) as caught:
        await create_architect_handoff(agent, source(), context=binding)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert provider.calls == 0 and caught.value.__context__ is None
    await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "updates",
    [
        {"rendered_prompt": PRIVATE},
        {"canonical_prompt": CanonicalPrompt.from_task(PRIVATE)},
        {"target_mode": TargetMode.OPENAI},
        {"changes": (NOTE,)},
        {"questions": (PRIVATE,)},
        {"renderer_name": "UntrustedRenderer"},
        {"renderer_version": "9.0.0"},
        {"canonical_prompt": None},
        {"metadata": CallMetadata(provider="fake", requested_model="fake-v1", attempts=0)},
        {"metadata": CallMetadata(requested_model="fake-v1")},
        {"agent_version": ""},
        {"instruction_version": PRIVATE * 4},
    ],
)
async def test_inconsistent_candidate_results_rejected(updates: dict[str, Any]) -> None:
    actual, provider = agent_for()
    request = source()
    result = await actual.create(
        PromptRequestCreate.model_validate(request.model_dump(exclude={"id", "created_at"}))
    )
    bad = ReturningArchitect(result.model_copy(update=updates))
    with pytest.raises(LLMError) as caught:
        await create_architect_handoff(bad, request)
    assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
    assert caught.value.__context__ is None and bad.calls == 1
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    await provider.aclose()


@pytest.mark.anyio
async def test_rewrite_canonical_render_mismatch_rejected() -> None:
    actual, provider = agent_for("rewrite")
    result = await actual.create(PromptRequestCreate(raw_prompt="Explain the supplied function."))
    bad = ReturningArchitect(result.model_copy(update={"rendered_prompt": "A different candidate"}))
    with pytest.raises(LLMError):
        await create_architect_handoff(bad, source())
    await provider.aclose()


@pytest.mark.anyio
async def test_clarification_cannot_smuggle_candidate_content() -> None:
    actual, provider = agent_for("clarify")
    result = await actual.create(PromptRequestCreate(raw_prompt="Missing source"))
    for updates in (
        {"rendered_prompt": PRIVATE},
        {"canonical_prompt": CanonicalPrompt.from_task(PRIVATE)},
        {"changes": (NOTE,)},
        {"questions": ()},
        {"renderer_name": "GenericPromptRenderer"},
    ):
        with pytest.raises(LLMError) as caught:
            await create_architect_handoff(
                ReturningArchitect(result.model_copy(update=updates)), source()
            )
        assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
    await provider.aclose()


@pytest.mark.anyio
async def test_critic_projection_revalidates_candidate_and_keeps_no_private_notes() -> None:
    agent, provider = agent_for("rewrite")
    outcome = await create_architect_handoff(agent, source())
    assert isinstance(outcome, CandidateReady)
    assert make_critic_input(outcome).renderer_version == outcome.renderer_version
    with pytest.raises(LLMError):
        make_critic_input(outcome.model_copy(update={"rendered_prompt": PRIVATE}))
    with pytest.raises(ValidationError):
        outcome.original_request.raw_prompt = "Mutated"
    assert PRIVATE not in repr(make_critic_input(outcome))
    assert NOTE not in repr(outcome)
    await provider.aclose()


@pytest.mark.anyio
async def test_no_unintended_database_activity(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("Architect integration attempted database activity")

    monkeypatch.setattr(Engine, "connect", forbidden)
    for method in ("execute", "add", "flush", "commit"):
        monkeypatch.setattr(AsyncSession, method, forbidden)
    for decision in ("preserve", "rewrite", "clarify"):
        agent, provider = agent_for(decision)
        await create_architect_handoff(agent, source(), context=run_context())
        await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code",
    [LLMErrorCode.TIMEOUT, LLMErrorCode.AUTHENTICATION, LLMErrorCode.INVALID_STRUCTURED_OUTPUT],
)
async def test_gateway_failures_propagate_without_handoff(code: LLMErrorCode) -> None:
    provider = RecordingFake([code])
    gateway = LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))
    with pytest.raises(LLMError) as caught:
        await create_architect_handoff(
            PromptArchitectAgent(gateway, ModelSettings(model="fake-v1")), source()
        )
    assert caught.value.code == code and caught.value.metadata.call is not None
    assert provider.calls == 1 and not provider.closed
    await provider.aclose()


@pytest.mark.anyio
async def test_architect_failure_is_not_wrapped_or_replaced() -> None:
    failure = RuntimeError("controlled Architect failure")
    agent = ReturningArchitect(failure=failure)
    with pytest.raises(RuntimeError) as caught:
        await create_architect_handoff(agent, source())
    assert caught.value is failure and agent.calls == 1


@pytest.mark.anyio
async def test_source_snapshot_is_stable_during_generation() -> None:
    entered, release = asyncio.Event(), asyncio.Event()

    class PausingArchitect(PromptArchitectAgent):
        async def create(
            self, payload: PromptRequestCreate, *, request_id: str | None = None
        ) -> ArchitectResult:
            entered.set()
            await release.wait()
            return await super().create(payload, request_id=request_id)

    original = source()
    _, provider = agent_for()
    gateway = LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))
    pausing = PausingArchitect(gateway, ModelSettings(model="fake-v1"))
    task = asyncio.create_task(create_architect_handoff(pausing, original))
    await entered.wait()
    original.raw_prompt = "New external value"
    original.id = OTHER_ID
    release.set()
    outcome = await task
    assert isinstance(outcome, CandidateReady)
    assert outcome.original_request.id == REQUEST_ID
    assert outcome.rendered_prompt == source().raw_prompt
    assert original.raw_prompt == "New external value"
    await provider.aclose()


@pytest.mark.anyio
async def test_diagnostics_and_invalid_source_never_expose_content() -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    agent, provider = agent_for("rewrite")
    outcome = await create_architect_handoff(agent, source())
    assert PRIVATE not in str(outcome) + stream.getvalue()
    assert NOTE not in str(outcome) + stream.getvalue()
    bad_source = source().model_copy(update={"raw_prompt": [PRIVATE]})
    with warnings.catch_warnings(record=True) as captured, pytest.raises(LLMError) as caught:
        warnings.simplefilter("always")
        await create_architect_handoff(agent, bad_source)
    assert not captured and caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert PRIVATE not in "".join(traceback.format_exception(caught.value))
    await provider.aclose()
