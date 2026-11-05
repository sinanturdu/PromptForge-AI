"""Deterministic operation budgets, privacy, cancellation and lifecycle checks."""

import asyncio
import io
import json
import traceback
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings
from app.core.logging import configure_logging
from app.llm.errors import LLMError, LLMErrorCode, LLMErrorMetadata
from app.llm.gateway import LLMGateway
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import (
    GatewayPolicy,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    ModelSettings,
    TokenUsage,
)
from app.main import create_app

PRIVATE = "PRIVATE_PROMPT_AND_OUTPUT_SENTINEL"
SECRET = "synthetic-private-credential-sentinel"


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int


class Clock:
    def __init__(self) -> None:
        self.now = 0.0
        self.waits: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.waits.append(delay)
        self.now += delay


Step = LLMResponse | LLMError | Callable[[LLMRequest], Awaitable[LLMResponse]]


class Scripted:
    def __init__(self, *steps: Step) -> None:
        self.steps = deque(steps)
        self.requests: list[LLMRequest] = []
        self.closes = 0

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        assert self.steps, "Unexpected extra provider call"
        step = self.steps.popleft()
        if isinstance(step, LLMError):
            raise step
        if isinstance(step, LLMResponse):
            return step
        return await step(request)

    async def aclose(self) -> None:
        self.closes += 1


def response(text: str = '{"answer":42}', **fields: Any) -> LLMResponse:
    return LLMResponse(provider="fake", text=text, finish_reason="complete", **fields)


def error(code: LLMErrorCode = LLMErrorCode.TIMEOUT, **metadata: Any) -> LLMError:
    return LLMError(code, metadata=LLMErrorMetadata(**metadata))


def gateway(provider: Scripted, clock: Clock, **policy: Any) -> LLMGateway:
    return LLMGateway(
        provider,
        policy=GatewayPolicy(**policy),
        clock=clock,
        sleep=clock.sleep,
        jitter=lambda: 0.0,
        provider_name="fake",
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def request_model() -> LLMRequest:
    return LLMRequest(
        settings=ModelSettings(model="fake-v1"),
        messages=(LLMMessage(role="user", content=PRIVATE),),
    )


@pytest.mark.anyio
async def test_success_metadata(request_model: LLMRequest) -> None:
    provider, clock = Scripted(response()), Clock()
    result = await gateway(provider, clock).generate(request_model)
    assert result.call_metadata is not None
    assert result.call_metadata.attempts == 1
    assert result.call_metadata.retries == 0
    assert not result.call_metadata.repair_attempted
    assert result.call_metadata.duration_ms == 0
    assert result.call_metadata.usage is None
    assert not result.call_metadata.usage_complete
    assert provider.closes == 0  # Gateway borrows the provider.
    assert not clock.waits


@pytest.mark.anyio
@pytest.mark.parametrize(
    "failure",
    [
        error(),
        error(LLMErrorCode.RATE_LIMIT),
        error(LLMErrorCode.PROVIDER_UNAVAILABLE),
        error(LLMErrorCode.PROVIDER_UNAVAILABLE, http_status=500),
        error(LLMErrorCode.PROVIDER_UNAVAILABLE, http_status=502),
        error(LLMErrorCode.PROVIDER_UNAVAILABLE, http_status=503),
        error(LLMErrorCode.PROVIDER_UNAVAILABLE, http_status=504),
    ],
)
async def test_retryable_failure_then_success(failure: LLMError, request_model: LLMRequest) -> None:
    provider, clock = Scripted(failure, response()), Clock()
    result = await gateway(provider, clock).generate_json(request_model, Output)
    assert result.value.answer == 42
    assert result.metadata.attempts == 2
    assert result.metadata.retries == 1
    assert result.metadata.duration_ms == 250
    assert clock.waits == [0.25]


@pytest.mark.anyio
async def test_retry_exhaustion(request_model: LLMRequest) -> None:
    provider, clock = Scripted(error(), error(), error(), response()), Clock()
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock).generate(request_model)
    assert caught.value.code == LLMErrorCode.TIMEOUT
    assert caught.value.metadata.call is not None
    assert caught.value.metadata.call.attempts == 3
    assert caught.value.metadata.call.retries == 2
    assert clock.waits == [0.25, 0.5]
    assert len(provider.requests) == 3


@pytest.mark.anyio
@pytest.mark.parametrize(
    "code",
    [
        code
        for code in LLMErrorCode
        if code
        not in {
            LLMErrorCode.TIMEOUT,
            LLMErrorCode.RATE_LIMIT,
            LLMErrorCode.PROVIDER_UNAVAILABLE,
        }
    ],
)
async def test_nonretryable_errors(code: LLMErrorCode, request_model: LLMRequest) -> None:
    provider, clock = Scripted(error(code), response()), Clock()
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock).generate_json(request_model, Output)
    assert caught.value.code == code
    assert len(provider.requests) == 1
    assert not clock.waits


@pytest.mark.anyio
@pytest.mark.parametrize("status", [400, 403, 501, 505])
async def test_nontransient_unavailability(status: int, request_model: LLMRequest) -> None:
    provider, clock = (
        Scripted(error(LLMErrorCode.PROVIDER_UNAVAILABLE, http_status=status)),
        Clock(),
    )
    with pytest.raises(LLMError):
        await gateway(provider, clock).generate(request_model)
    assert len(provider.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(("delay", "expected_waits"), [(2.0, [2.0]), (0.0, [0.25]), (91.0, [])])
async def test_provider_delay(
    delay: float,
    expected_waits: list[float],
    request_model: LLMRequest,
) -> None:
    provider, clock = (
        Scripted(error(LLMErrorCode.RATE_LIMIT, retry_after_seconds=delay), response()),
        Clock(),
    )
    if expected_waits:
        await gateway(provider, clock).generate(request_model)
    else:
        with pytest.raises(LLMError) as caught:
            await gateway(provider, clock).generate(request_model)
        assert caught.value.code == LLMErrorCode.RATE_LIMIT
        assert caught.value.metadata.retry_after_seconds == delay
        assert len(provider.requests) == 1
    assert clock.waits == expected_waits


@pytest.mark.anyio
@pytest.mark.parametrize("delay", [-1.0, float("nan"), float("inf")])
async def test_invalid_provider_delay_ignored(delay: float, request_model: LLMRequest) -> None:
    # Defensive boundary even for an adapter that bypassed metadata validation.
    invalid = LLMErrorMetadata.model_construct(retry_after_seconds=delay)
    provider, clock = (
        Scripted(LLMError(LLMErrorCode.RATE_LIMIT, metadata=invalid), response()),
        Clock(),
    )
    await gateway(provider, clock).generate(request_model)
    assert clock.waits == [0.25]


@pytest.mark.anyio
@pytest.mark.parametrize("sample", [0.0, 0.5, 1.0])
async def test_jitter_bounds(sample: float, request_model: LLMRequest) -> None:
    provider, clock = Scripted(error(), error(), response()), Clock()
    await LLMGateway(provider, clock=clock, sleep=clock.sleep, jitter=lambda: sample).generate(
        request_model
    )
    assert clock.waits == [0.25 + sample * 0.25, 0.5 + sample * 0.5]


@pytest.mark.anyio
@pytest.mark.parametrize("bad_text", ["bad-json", '{"answer":"wrong"}'])
async def test_repair_preserves_task_and_schema(bad_text: str, request_model: LLMRequest) -> None:
    provider, clock = Scripted(response(bad_text), response()), Clock()
    result = await gateway(provider, clock).generate_json(request_model, Output)
    assert result.value.answer == 42
    assert result.metadata.repair_attempted
    assert result.metadata.attempts == 2
    original, repaired = provider.requests
    assert original.response_schema == repaired.response_schema == Output.model_json_schema()
    assert repaired.settings == request_model.settings
    assert repaired.messages[:-2] == request_model.messages
    assert repaired.messages[-2].content == bad_text
    assert "Validation categories:" in repaired.messages[-1].content
    assert "Preserve the original task intent" in repaired.messages[-1].content
    assert not clock.waits


@pytest.mark.anyio
async def test_failed_repair_is_final(request_model: LLMRequest) -> None:
    provider, clock = Scripted(response("bad"), response("still bad"), response()), Clock()
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock).generate_json(request_model, Output)
    assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
    assert caught.value.metadata.call is not None and caught.value.metadata.call.repair_attempted
    assert len(provider.requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("before_repair", [0, 1, 2])
async def test_shared_budget_hard_four_calls(before_repair: int, request_model: LLMRequest) -> None:
    steps: list[Step] = [
        *[error() for _ in range(before_repair)],
        response("bad"),
        *[error() for _ in range(2 - before_repair)],
        response(),
    ]
    provider, clock = Scripted(*steps), Clock()
    result = await gateway(provider, clock).generate_json(request_model, Output)
    assert len(provider.requests) == result.metadata.attempts == 4
    assert result.metadata.retries == 2
    assert result.metadata.repair_attempted


@pytest.mark.anyio
async def test_budget_not_reset_for_repair(request_model: LLMRequest) -> None:
    provider, clock = Scripted(error(), response("bad"), error(), error(), response()), Clock()
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock).generate_json(request_model, Output)
    assert caught.value.code == LLMErrorCode.TIMEOUT
    assert len(provider.requests) == 4


@pytest.mark.anyio
async def test_deadline_covers_repair_and_validation(request_model: LLMRequest) -> None:
    clock = Clock()

    async def slow(request: LLMRequest) -> LLMResponse:
        clock.now += 1.1
        return response("bad")

    provider = Scripted(slow, response())
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock, call_deadline_seconds=1.0).generate_json(
            request_model, Output
        )
    assert caught.value.code == LLMErrorCode.DEADLINE_EXCEEDED
    assert len(provider.requests) == 1


@pytest.mark.anyio
async def test_backoff_cannot_overrun_deadline(request_model: LLMRequest) -> None:
    clock = Clock()

    async def oversleep(delay: float) -> None:
        clock.now = 100

    provider = Scripted(error(), response())
    with pytest.raises(LLMError) as caught:
        await LLMGateway(provider, clock=clock, sleep=oversleep).generate(request_model)
    assert caught.value.code == LLMErrorCode.DEADLINE_EXCEEDED
    assert len(provider.requests) == 1


@pytest.mark.anyio
async def test_timeout_cancels_provider_without_orphan(
    request_model: LLMRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    exited = False

    async def blocked(request: LLMRequest) -> LLMResponse:
        nonlocal exited
        try:
            await asyncio.Event().wait()
            raise AssertionError("Unreachable")
        finally:
            exited = True

    actual_timeout = asyncio.timeout
    # Trigger Python 3.12's real cancellation path at the next await, without a timed sleep.
    monkeypatch.setattr(asyncio, "timeout", lambda delay: actual_timeout(0))
    provider, clock = Scripted(blocked), Clock()
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock, max_retries=0).generate(request_model)
    assert caught.value.code == LLMErrorCode.TIMEOUT
    assert exited and len(provider.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("during_backoff", [False, True])
async def test_external_cancellation(during_backoff: bool, request_model: LLMRequest) -> None:
    entered, exited = asyncio.Event(), asyncio.Event()

    async def block() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            exited.set()

    async def blocked(request: LLMRequest) -> LLMResponse:
        await block()
        raise AssertionError("Unreachable")

    async def sleep(delay: float) -> None:
        await block()

    provider = Scripted(error() if during_backoff else blocked, response())
    task = asyncio.create_task(LLMGateway(provider, sleep=sleep).generate(request_model))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.done() and exited.is_set()
    assert len(provider.requests) == 1


@pytest.mark.anyio
async def test_usage_across_repair_and_unknown_attempts(request_model: LLMRequest) -> None:
    full = TokenUsage(
        input_tokens=2, output_tokens=3, total_tokens=5, cached_input_tokens=1, reasoning_tokens=1
    )
    provider, clock = Scripted(response("bad", usage=full), error(), response(usage=full)), Clock()
    result = await gateway(provider, clock).generate_json(request_model, Output)
    assert result.metadata.usage == TokenUsage(
        input_tokens=4, output_tokens=6, total_tokens=10, cached_input_tokens=2, reasoning_tokens=2
    )
    assert not result.metadata.usage_complete  # Failed request may have incurred unknown usage.
    provider = Scripted(response("bad", usage=full), response(usage=full))
    result = await gateway(provider, clock).generate_json(request_model, Output)
    assert result.metadata.usage_complete


@pytest.mark.anyio
async def test_content_never_enters_diagnostics(request_model: LLMRequest) -> None:
    stream = io.StringIO()
    configure_logging(level="INFO", stream=stream)
    provider, clock = Scripted(response(PRIVATE + SECRET), response(PRIVATE)), Clock()
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock).generate_json(request_model, Output)
    visible = stream.getvalue() + repr(caught.value) + repr(caught.value.metadata)
    visible += "".join(traceback.format_exception(caught.value))
    assert PRIVATE not in visible and SECRET not in visible
    assert caught.value.__context__ is None
    diagnostics = json.loads(stream.getvalue().splitlines()[-1])["context"]
    assert diagnostics["attempts"] == 2 and diagnostics["repair_attempted"]
    assert diagnostics["error_code"] == "invalid_structured_output"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("llm_request_timeout_seconds", 0),
        ("llm_request_timeout_seconds", 301),
        ("llm_call_deadline_seconds", 0),
        ("llm_call_deadline_seconds", 901),
        ("llm_request_timeout_seconds", float("inf")),
        ("llm_call_deadline_seconds", float("nan")),
        ("llm_max_retries", -1),
        ("llm_max_retries", 3),
        ("llm_json_repair_attempts", -1),
        ("llm_json_repair_attempts", 2),
    ],
)
def test_invalid_configuration(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        IsolatedSettings.model_validate({name: value})


def test_lifespan_owns_one_provider_and_health_never_generates() -> None:
    provider = Scripted()
    app = create_app(IsolatedSettings(database_url="sqlite+aiosqlite:///:memory:"))
    with (
        patch("app.main.create_provider", return_value=provider) as factory,
        TestClient(app) as client,
    ):
        assert isinstance(app.state.llm_gateway, LLMGateway)
        assert client.get("/api/v1/health").status_code == 200
        assert client.get("/api/v1/health").status_code == 200
        assert factory.call_count == 1 and provider.closes == 0
    assert provider.closes == 1 and not provider.requests


def test_lifespan_partial_initialization_cleanup() -> None:
    provider = Scripted()
    with (
        patch("app.main.create_provider", return_value=provider),
        patch("app.main.LLMGateway", side_effect=RuntimeError("gateway startup failed")),
        patch("app.main.DatabaseSessionManager.dispose") as dispose,
        pytest.raises(RuntimeError),
        TestClient(create_app(IsolatedSettings())),
    ):
        pass
    assert provider.closes == 1 and dispose.await_count == 1


def test_lifespan_provider_construction_failure_disposes_database() -> None:
    with (
        patch("app.main.create_provider", side_effect=LLMError(LLMErrorCode.MISSING_CREDENTIALS)),
        patch("app.main.DatabaseSessionManager.dispose") as dispose,
        pytest.raises(LLMError),
        TestClient(create_app(IsolatedSettings())),
    ):
        pass
    assert dispose.await_count == 1


def test_default_fake_lifespan() -> None:
    with TestClient(create_app(IsolatedSettings())) as client:
        assert client.get("/api/v1/health").json()["status"] == "ok"
    assert IsolatedSettings().llm_provider == "fake"


@pytest.mark.anyio
async def test_fake_provider_remains_usable_without_credentials(request_model: LLMRequest) -> None:
    provider = FakeLLMProvider([response()])
    result = await LLMGateway(provider).generate_json(request_model, Output)
    assert result.value.answer == 42
    await provider.aclose()


@pytest.mark.anyio
async def test_repair_uses_remaining_deadline(request_model: LLMRequest) -> None:
    clock = Clock()

    async def slow_invalid(request: LLMRequest) -> LLMResponse:
        clock.now += 0.6
        return response("bad")

    async def slow_valid(request: LLMRequest) -> LLMResponse:
        clock.now += 0.6
        return response()

    provider = Scripted(slow_invalid, slow_valid, response())
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock, call_deadline_seconds=1.0).generate_json(
            request_model, Output
        )
    assert caught.value.code == LLMErrorCode.DEADLINE_EXCEEDED
    assert len(provider.requests) == 2
    assert caught.value.metadata.call is not None and caught.value.metadata.call.repair_attempted


@pytest.mark.anyio
async def test_timeout_then_success_has_no_detached_work(request_model: LLMRequest) -> None:
    clock = Clock()

    async def late(request: LLMRequest) -> LLMResponse:
        clock.now += 2
        return response()

    provider = Scripted(late, response())
    result = await gateway(provider, clock, request_timeout_seconds=1.0).generate(request_model)
    assert result.call_metadata is not None and result.call_metadata.attempts == 2
    assert clock.waits == [0.25]


@pytest.mark.anyio
async def test_deadline_cancels_active_call(
    request_model: LLMRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock, exited = Clock(), asyncio.Event()
    actual_timeout = asyncio.timeout
    timeouts: list[float] = []

    def expire(delay: float) -> asyncio.Timeout:
        timeouts.append(delay)
        return actual_timeout(0)

    monkeypatch.setattr(asyncio, "timeout", expire)

    async def blocked(request: LLMRequest) -> LLMResponse:
        clock.now = 1.0
        try:
            await asyncio.Event().wait()
            raise AssertionError("Unreachable")
        finally:
            exited.set()

    provider = Scripted(blocked, response())
    with pytest.raises(LLMError) as caught:
        await gateway(provider, clock, call_deadline_seconds=1.0).generate(request_model)
    assert caught.value.code == LLMErrorCode.DEADLINE_EXCEEDED
    assert exited.is_set() and timeouts == [1.0] and len(provider.requests) == 1


@pytest.mark.anyio
async def test_deadline_during_backoff_cancels_wait(
    request_model: LLMRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    actual_timeout = asyncio.timeout
    contexts = 0

    def expire_wait(delay: float) -> asyncio.Timeout:
        nonlocal contexts
        contexts += 1
        return actual_timeout(0 if contexts == 2 else delay)

    monkeypatch.setattr(asyncio, "timeout", expire_wait)
    exited = False

    async def blocked_sleep(delay: float) -> None:
        nonlocal exited
        try:
            await asyncio.Event().wait()
        finally:
            exited = True

    provider, clock = Scripted(error(), response()), Clock()
    with pytest.raises(LLMError) as caught:
        await LLMGateway(provider, clock=clock, sleep=blocked_sleep).generate(request_model)
    assert caught.value.code == LLMErrorCode.DEADLINE_EXCEEDED
    assert exited and len(provider.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("kind", ["refusal", "length"])
async def test_no_repair_for_noncomplete_response(
    kind: Literal["refusal", "length"],
    request_model: LLMRequest,
) -> None:
    provider, clock = (
        Scripted(LLMResponse(provider="fake", finish_reason=kind), response()),
        Clock(),
    )
    with pytest.raises(LLMError):
        await gateway(provider, clock).generate_json(request_model, Output)
    assert len(provider.requests) == 1


@pytest.mark.anyio
async def test_concurrent_operations_have_independent_budgets(request_model: LLMRequest) -> None:
    calls: dict[str, int] = {}

    class Provider:
        async def generate(self, request: LLMRequest) -> LLMResponse:
            key = request.request_id or ""
            calls[key] = calls.get(key, 0) + 1
            if calls[key] == 1:
                raise error()
            return response()

        async def aclose(self) -> None:
            pass

    async def no_wait(delay: float) -> None:
        await asyncio.sleep(0)

    shared = LLMGateway(Provider(), sleep=no_wait, jitter=lambda: 0)
    results = await asyncio.gather(
        *(
            shared.generate_json(request_model.model_copy(update={"request_id": key}), Output)
            for key in ("one", "two")
        )
    )
    assert all(result.metadata.attempts == 2 for result in results)
    assert calls == {"one": 2, "two": 2}


def test_shutdown_failure_still_disposes_database() -> None:
    class BrokenClose(Scripted):
        async def aclose(self) -> None:
            self.closes += 1
            raise RuntimeError("close failed")

    provider = BrokenClose()
    with (
        patch("app.main.create_provider", return_value=provider),
        patch("app.main.DatabaseSessionManager.dispose") as dispose,
        pytest.raises(RuntimeError),
        TestClient(create_app(IsolatedSettings())),
    ):
        pass
    assert provider.closes == 1 and dispose.await_count == 1
