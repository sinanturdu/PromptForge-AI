"""Offline LLM contract, composition, single-attempt and privacy regression tests."""

import asyncio
import socket
import traceback
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings
from app.llm.composition import create_provider
from app.llm.contracts import LLMProvider
from app.llm.errors import LLMError, LLMErrorCode
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

SENTINEL = "PRIVATE_PROMPT_SECRET_SENTINEL"


def single_attempt_gateway(provider: LLMProvider) -> LLMGateway:
    """Retain adapter/contract tests for explicitly disabled reliability features."""
    return LLMGateway(provider, policy=GatewayPolicy(max_retries=0, json_repair_attempts=0))


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def request_model() -> LLMRequest:
    return LLMRequest(
        settings=ModelSettings(model="fake-v1"),
        messages=(LLMMessage(role="user", content=SENTINEL),),
        request_id="local-1",
    )


def response(**overrides: Any) -> LLMResponse:
    return LLMResponse.model_validate(
        {"provider": "fake", "finish_reason": "complete", "text": '{"answer":42}', **overrides}
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"model": ""},
        {"model": "   "},
        {"model": 123},
        {"temperature": -0.1},
        {"temperature": 2.1},
        {"temperature": float("nan")},
        {"temperature": "1"},
        {"max_output_tokens": 0},
        {"max_output_tokens": 8193},
        {"max_output_tokens": True},
        {"max_output_tokens": "4"},
        {"unexpected": 1},
    ],
)
def test_invalid_settings(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ModelSettings.model_validate({"model": "fake-v1", **fields})


def test_settings_bounds_and_immutability() -> None:
    assert ModelSettings(model="fake-v1").temperature is None
    for temperature in (0.0, 2.0):
        assert ModelSettings(model="fake-v1", temperature=temperature).temperature == temperature
    settings = ModelSettings(model="fake-v1", max_output_tokens=8192)
    with pytest.raises(ValidationError):
        settings.model = "changed"


@pytest.mark.parametrize(
    "fields",
    [{"content": ""}, {"content": " \n"}, {"content": 12}, {"role": "tool"}, {"x": 1}],
)
def test_invalid_messages(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        LLMMessage.model_validate({"role": "user", "content": SENTINEL, **fields})


def test_request_validation_and_privacy(request_model: LLMRequest) -> None:
    assert SENTINEL not in repr(request_model)
    assert SENTINEL not in repr(request_model.messages[0])
    assert SENTINEL not in repr(response(text=SENTINEL))
    assert request_model.messages[0].content == SENTINEL
    for messages in ((), [], ["bad"]):
        with pytest.raises(ValidationError):
            LLMRequest.model_validate({"settings": request_model.settings, "messages": messages})
    with pytest.raises(ValidationError) as caught:
        LLMMessage.model_validate({"role": SENTINEL, "content": SENTINEL})
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in repr(caught.value)


@pytest.mark.parametrize("name", list(TokenUsage.model_fields))
@pytest.mark.parametrize("value", [-1, True, "2", 1.5])
def test_invalid_usage(name: str, value: object) -> None:
    with pytest.raises(ValidationError):
        TokenUsage.model_validate({name: value})


def test_usage_unknown_and_explicit_zero() -> None:
    assert all(value is None for value in TokenUsage().model_dump().values())
    usage = TokenUsage(input_tokens=0, output_tokens=2, cached_input_tokens=0, reasoning_tokens=1)
    assert usage.input_tokens == 0
    assert usage.total_tokens is None


@pytest.mark.anyio
async def test_fake_order_isolation_exhaustion_and_cleanup(request_model: LLMRequest) -> None:
    script = [response(text="first"), response(text="second")]
    first = FakeLLMProvider(script)
    second = FakeLLMProvider(script)
    script.clear()
    assert (await first.generate(request_model)).text == "first"
    assert (await first.generate(request_model)).text == "second"
    with pytest.raises(LLMError) as caught:
        await first.generate(request_model)
    assert caught.value.code == LLMErrorCode.SCRIPT_EXHAUSTED
    assert (await second.generate(request_model)).text == "first"
    await second.aclose()
    await second.aclose()
    assert second.closed
    with pytest.raises(LLMError) as caught:
        await second.generate(request_model)
    assert caught.value.code == LLMErrorCode.PROVIDER_CLOSED
    await first.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize("code", list(LLMErrorCode))
async def test_scripted_failure_single_attempt(
    code: LLMErrorCode, request_model: LLMRequest
) -> None:
    provider = FakeLLMProvider([code, response()])
    with pytest.raises(LLMError) as caught:
        await single_attempt_gateway(provider).generate_json(request_model, Output)
    assert caught.value.code == code
    assert provider.calls == 1
    assert SENTINEL not in str(caught.value)
    assert SENTINEL not in repr(caught.value)
    await provider.aclose()


@pytest.mark.anyio
async def test_structured_success_schema_metadata_and_borrowed_lifetime(
    request_model: LLMRequest,
) -> None:
    class InspectingFake(FakeLLMProvider):
        async def generate(self, request: LLMRequest) -> LLMResponse:
            assert request.response_schema == Output.model_json_schema()
            return await super().generate(request)

    usage = TokenUsage(input_tokens=2, output_tokens=3, total_tokens=5)
    provider = InspectingFake([response(usage=usage, upstream_request_id="upstream-1")])
    result = await single_attempt_gateway(provider).generate_json(request_model, Output)
    assert result.value == Output(answer=42)
    assert result.metadata.usage == usage
    assert result.metadata.request_id == "local-1"
    assert result.metadata.upstream_request_id == "upstream-1"
    assert result.metadata.provider == "fake"
    assert result.metadata.attempts == 1
    assert not result.metadata.repair_attempted
    assert request_model.response_schema is None
    assert provider.calls == 1
    assert not provider.closed
    await provider.aclose()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("fields", "code"),
    [
        ({"text": "bad-json"}, LLMErrorCode.INVALID_STRUCTURED_OUTPUT),
        ({"text": '{"answer":"42"}'}, LLMErrorCode.INVALID_STRUCTURED_OUTPUT),
        ({"text": "{}"}, LLMErrorCode.INVALID_STRUCTURED_OUTPUT),
        ({"text": '{"answer":42,"extra":1}'}, LLMErrorCode.INVALID_STRUCTURED_OUTPUT),
        ({"text": None}, LLMErrorCode.MISSING_TEXT),
        ({"text": " \n"}, LLMErrorCode.MISSING_TEXT),
        ({"finish_reason": "refusal"}, LLMErrorCode.REFUSAL),
        ({"finish_reason": "length"}, LLMErrorCode.TRUNCATION),
        ({"finish_reason": "other"}, LLMErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_gateway_rejection_without_retry(
    fields: dict[str, Any], code: LLMErrorCode, request_model: LLMRequest
) -> None:
    provider = FakeLLMProvider([response(**fields), response()])
    with pytest.raises(LLMError) as caught:
        await single_attempt_gateway(provider).generate_json(request_model, Output)
    assert caught.value.code == code
    assert provider.calls == 1
    await provider.aclose()


@pytest.mark.anyio
async def test_privacy_and_offline_operation(
    request_model: LLMRequest,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        pytest.fail("LLM fake attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)
    provider = FakeLLMProvider([response(text=SENTINEL), response()])
    gateway = single_attempt_gateway(provider)
    with pytest.raises(LLMError) as caught:
        await gateway.generate_json(request_model, Output)
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))
    assert caught.value.__context__ is None
    await gateway.generate_json(request_model, Output)
    captured = capsys.readouterr()
    assert SENTINEL not in caplog.text + captured.out + captured.err
    await provider.aclose()


@pytest.mark.anyio
async def test_unexpected_errors_are_safe_and_cancellation_propagates(
    request_model: LLMRequest,
) -> None:
    class BrokenProvider:
        def __init__(self, error: BaseException) -> None:
            self.error = error

        async def generate(self, request: LLMRequest) -> LLMResponse:
            raise self.error

        async def aclose(self) -> None:
            pass

    with pytest.raises(LLMError) as caught:
        await single_attempt_gateway(BrokenProvider(RuntimeError(SENTINEL))).generate(request_model)
    assert caught.value.code == LLMErrorCode.PROVIDER_UNAVAILABLE
    assert caught.value.__context__ is None
    assert SENTINEL not in "".join(traceback.format_exception(caught.value))
    with pytest.raises(asyncio.CancelledError):
        await single_attempt_gateway(BrokenProvider(asyncio.CancelledError())).generate(
            request_model
        )


@pytest.mark.anyio
async def test_default_composition_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    settings = IsolatedSettings()
    assert settings.llm_provider == "fake"
    provider = create_provider(settings)
    assert isinstance(provider, FakeLLMProvider)
    await provider.aclose()


@pytest.mark.parametrize("selection", ["anthropic", "gemini", "unknown", SENTINEL])
def test_unsupported_configuration_is_safe(selection: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", selection)
    with pytest.raises(ValidationError) as caught:
        IsolatedSettings()
    assert "fake" in str(caught.value)
    assert SENTINEL not in str(caught.value)
    # Defensive factory check even if a caller bypasses Settings validation.
    with pytest.raises(LLMError) as normalized:
        create_provider(Settings.model_construct(llm_provider=selection))
    assert normalized.value.code == LLMErrorCode.INVALID_CONFIGURATION


def test_fake_cannot_impersonate_real_provider() -> None:
    with pytest.raises(LLMError) as caught:
        FakeLLMProvider([response(provider="openai")])
    assert caught.value.code == LLMErrorCode.INVALID_CONFIGURATION


@pytest.mark.anyio
async def test_plain_text_generation(request_model: LLMRequest) -> None:
    provider = FakeLLMProvider([response(text="Explicit fake output")])
    result = await single_attempt_gateway(provider).generate(request_model)
    assert result.text == "Explicit fake output"
    assert provider.calls == 1
    await provider.aclose()


@pytest.mark.anyio
async def test_unsupported_output_schema_fails_before_generation(request_model: LLMRequest) -> None:
    class UnsupportedOutput(BaseModel):
        model_config = ConfigDict(arbitrary_types_allowed=True)
        unsupported: socket.socket

    provider = FakeLLMProvider([response()])
    with pytest.raises(LLMError) as caught:
        await single_attempt_gateway(provider).generate_json(request_model, UnsupportedOutput)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert caught.value.__context__ is None
    assert provider.calls == 0
    await provider.aclose()


@pytest.mark.parametrize(
    "fields",
    [{"provider": " "}, {"finish_reason": "unknown"}, {"text": 1}, {"usage": {"total_tokens": -1}}],
)
def test_invalid_responses(fields: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        response(**fields)
