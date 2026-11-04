"""Actual SDK serialization and error parsing over a fully offline HTTP transport."""

import asyncio
import json
import logging
import traceback
import warnings
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, SecretStr
from pydantic_settings import SettingsConfigDict

from app.core.config import Settings
from app.llm.composition import create_provider
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.gateway import LLMGateway
from app.llm.providers.openai import OpenAILLMProvider
from app.llm.schemas import LLMMessage, LLMRequest, ModelSettings

PRIVATE = "PRIVATE_PROMPT_OUTPUT_SENTINEL"
SECRET = "synthetic-offline-key-sentinel"


class IsolatedSettings(Settings):
    model_config = SettingsConfigDict(env_file=None)


class Output(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: int


def payload(**overrides: Any) -> dict[str, Any]:
    return {
        "id": "resp_offline",
        "object": "response",
        "created_at": 0,
        "model": "explicit-model",
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "id": "msg_offline",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": '{"answer":42}', "annotations": []}],
            }
        ],
        "usage": None,
        **overrides,
    }


class Harness:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body: Any = payload()
        self.headers: dict[str, str] = {"x-request-id": "req_offline_1"}
        self.error: BaseException | None = None
        self.transport = httpx.MockTransport(self.handle)
        self.http = httpx.AsyncClient(transport=self.transport)
        self.client = AsyncOpenAI(api_key=SECRET, http_client=self.http, max_retries=0)
        self.provider = OpenAILLMProvider(
            client=self.client, owns_client=True, temperature_models=frozenset({"explicit-model"})
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url == "https://api.openai.com/v1/responses"
        self.requests.append(json.loads(request.content))
        if self.error is not None:
            raise self.error
        return httpx.Response(self.status, json=self.body, headers=self.headers)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def request_model() -> LLMRequest:
    return LLMRequest(
        settings=ModelSettings(model="explicit-model", max_output_tokens=321),
        messages=tuple(
            LLMMessage(role=role, content=f"{role}: {PRIVATE}")
            for role in ("system", "user", "assistant")
        ),
    )


@pytest.fixture
async def harness() -> AsyncIterator[Harness]:
    harness = Harness()
    try:
        yield harness
    finally:
        await harness.provider.aclose()


@pytest.mark.anyio
async def test_sdk_request_and_text_translation(
    harness: Harness, request_model: LLMRequest
) -> None:
    result = await harness.provider.generate(request_model)
    assert result.text == '{"answer":42}'
    assert result.finish_reason == "complete"
    assert result.provider == "openai"
    assert result.resolved_model == "explicit-model"
    assert result.upstream_request_id == "req_offline_1"
    assert result.usage is None
    assert harness.requests == [
        {
            "model": "explicit-model",
            "max_output_tokens": 321,
            "store": False,
            "stream": False,
            "input": [
                {"role": message.role, "type": "message", "content": message.content}
                for message in request_model.messages
            ],
        }
    ]


@pytest.mark.anyio
async def test_structured_output_and_local_validation(
    harness: Harness, request_model: LLMRequest
) -> None:
    gateway = LLMGateway(harness.provider)
    result = await gateway.generate_json(request_model, Output)
    assert result.value.answer == 42
    assert harness.requests[0]["text"] == {
        "format": {
            "type": "json_schema",
            "name": "promptforge_output",
            "strict": True,
            "schema": Output.model_json_schema(),
        }
    }
    assert request_model.response_schema is None
    assert len(harness.requests) == 1
    # The transport/provider's schema claim never replaces gateway validation.
    harness.body["output"][0]["content"][0]["text"] = '{"answer":"wrong"}'
    with pytest.raises(LLMError) as caught:
        await gateway.generate_json(request_model, Output)
    assert caught.value.code == LLMErrorCode.INVALID_STRUCTURED_OUTPUT
    assert len(harness.requests) == 2


@pytest.mark.anyio
async def test_temperature_explicit_support(harness: Harness, request_model: LLMRequest) -> None:
    request_model = request_model.model_copy(
        update={"settings": ModelSettings(model="explicit-model", temperature=0.0)}
    )
    await harness.provider.generate(request_model)
    assert harness.requests[0]["temperature"] == 0.0
    request_model = request_model.model_copy(
        update={"settings": ModelSettings(model="unverified-model", temperature=1.0)}
    )
    with pytest.raises(LLMError) as caught:
        await harness.provider.generate(request_model)
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert len(harness.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"type": "object", "properties": {}, "required": []},
        {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": [],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"x": {"type": "string", "minLength": 2}},
            "required": ["x"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "anyOf": [{"type": "string"}],
        },
        {
            "type": "object",
            "properties": {"x": {"$ref": "https://example.com/schema"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"x": {"$ref": "#/$defs/missing"}},
            "required": ["x"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
            "default": {},
        },
    ],
)
async def test_unsupported_schema_never_sent(
    schema: dict[str, Any],
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    with pytest.raises(LLMError) as caught:
        await harness.provider.generate(
            request_model.model_copy(update={"response_schema": schema})
        )
    assert caught.value.code == LLMErrorCode.INVALID_REQUEST
    assert not harness.requests


@pytest.mark.anyio
async def test_nested_nullable_and_definitions_unchanged(
    harness: Harness, request_model: LLMRequest
) -> None:
    class Nested(BaseModel):
        model_config = ConfigDict(extra="forbid")
        label: str | None

    class NestedOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")
        items: list[Nested]

    schema = NestedOutput.model_json_schema()
    await harness.provider.generate(request_model.model_copy(update={"response_schema": schema}))
    assert harness.requests[0]["text"]["format"]["schema"] == schema


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("overrides", "finish"),
    [
        ({"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"}}, "length"),
        ({"status": "incomplete", "incomplete_details": {"reason": "content_filter"}}, "refusal"),
        (
            {
                "output": [
                    {
                        "id": "msg_refusal",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [{"type": "refusal", "refusal": PRIVATE}],
                    }
                ]
            },
            "refusal",
        ),
        ({"output": []}, "complete"),
    ],
)
async def test_finish_reasons(
    overrides: dict[str, Any],
    finish: str,
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    harness.body = payload(**overrides)
    result = await harness.provider.generate(request_model)
    assert result.finish_reason == finish
    assert len(harness.requests) == 1
    if "output" in overrides:
        assert result.text is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "queued"},
        {"status": "failed"},
        {"status": "unexpected"},
        {"status": None},
        {"status": "incomplete"},
        {"output": "malformed"},
        {"model": None},
        {"output": [{"type": "unknown", "content": PRIVATE}]},
        {"incomplete_details": {"reason": "max_output_tokens"}},
        {"usage": {"input_tokens": "wrong"}},
    ],
)
async def test_bad_envelopes(
    overrides: dict[str, Any],
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    harness.body = payload(**overrides)
    with pytest.raises(LLMError) as caught:
        await harness.provider.generate(request_model)
    assert caught.value.code == LLMErrorCode.INVALID_RESPONSE
    assert caught.value.__context__ is None
    assert len(harness.requests) == 1


@pytest.mark.anyio
async def test_usage_is_not_double_counted(harness: Harness, request_model: LLMRequest) -> None:
    harness.body = payload(
        usage={
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
            "input_tokens_details": {"cached_tokens": 5, "cache_write_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 8},
        }
    )
    result = await harness.provider.generate(request_model)
    assert result.usage is not None
    assert result.usage.model_dump() == {
        "input_tokens": 10,
        "output_tokens": 20,
        "total_tokens": 30,
        "cached_input_tokens": 5,
        "reasoning_tokens": 8,
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body_code", "expected"),
    [
        (401, "invalid_api_key", LLMErrorCode.AUTHENTICATION),
        (403, "denied", LLMErrorCode.PERMISSION),
        (402, "billing", LLMErrorCode.QUOTA_EXHAUSTED),
        (429, "insufficient_quota", LLMErrorCode.QUOTA_EXHAUSTED),
        (429, "billing_hard_limit_reached", LLMErrorCode.QUOTA_EXHAUSTED),
        (429, "rate_limit_exceeded", LLMErrorCode.RATE_LIMIT),
        (408, "timeout", LLMErrorCode.TIMEOUT),
        (400, "invalid", LLMErrorCode.INVALID_REQUEST),
        (404, "missing_model", LLMErrorCode.INVALID_REQUEST),
        (422, "invalid", LLMErrorCode.INVALID_REQUEST),
        (500, "server", LLMErrorCode.PROVIDER_UNAVAILABLE),
        (503, "unavailable", LLMErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_sdk_status_errors_safe_and_no_retry(
    status: int,
    body_code: str,
    expected: LLMErrorCode,
    harness: Harness,
    request_model: LLMRequest,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    harness.status = status
    harness.body = {"error": {"code": body_code, "message": f"{PRIVATE} {SECRET}"}}
    harness.headers["retry-after"] = "2.5"
    with pytest.raises(LLMError) as caught:
        await LLMGateway(harness.provider).generate(request_model)
    error = caught.value
    assert error.code == expected
    assert error.metadata.http_status == status
    assert error.metadata.upstream_request_id == "req_offline_1"
    assert error.metadata.retry_after_seconds == 2.5
    assert error.__context__ is None
    assert len(harness.requests) == 1
    visible = repr(error) + str(error) + repr(error.metadata) + caplog.text
    visible += "".join(traceback.format_exception(error))
    assert PRIVATE not in visible and SECRET not in visible


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error_type", "expected"),
    [
        (httpx.ReadTimeout, LLMErrorCode.TIMEOUT),
        (httpx.ConnectError, LLMErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
async def test_transport_failures(
    error_type: type[httpx.RequestError],
    expected: LLMErrorCode,
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    harness.error = error_type(PRIVATE)
    with pytest.raises(LLMError) as caught:
        await harness.provider.generate(request_model)
    assert caught.value.code == expected
    assert caught.value.__context__ is None
    assert len(harness.requests) == 1


@pytest.mark.anyio
async def test_cancellation_and_programming_errors_propagate(
    harness: Harness, request_model: LLMRequest
) -> None:
    harness.error = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await harness.provider.generate(request_model)
    harness.error = TypeError("programming mistake")

    # The installed SDK wraps transport exceptions as connection errors; adapter
    # programming errors outside SDK transport must still propagate.
    def broken_schema(schema: object) -> None:
        raise TypeError("programming mistake")

    from unittest.mock import patch

    with (
        patch("app.llm.providers.openai.validate_schema", broken_schema),
        pytest.raises(TypeError),
    ):
        await harness.provider.generate(request_model.model_copy(update={"response_schema": {}}))


@pytest.mark.parametrize("key", [None, SecretStr(""), SecretStr("   ")])
def test_missing_credentials(key: SecretStr | None) -> None:
    with pytest.raises(LLMError) as caught:
        OpenAILLMProvider(api_key=key)
    assert caught.value.code == LLMErrorCode.MISSING_CREDENTIALS


@pytest.mark.anyio
async def test_owned_and_borrowed_cleanup(harness: Harness, request_model: LLMRequest) -> None:
    borrowed = OpenAILLMProvider(client=harness.client)
    await borrowed.aclose()
    await borrowed.aclose()
    assert not harness.client.is_closed()
    with pytest.raises(LLMError) as caught:
        await borrowed.generate(request_model)
    assert caught.value.code == LLMErrorCode.PROVIDER_CLOSED
    await harness.provider.aclose()
    await harness.provider.aclose()
    assert harness.client.is_closed()


@pytest.mark.anyio
async def test_reject_injected_retrying_client(harness: Harness) -> None:
    harness.client.max_retries = 2
    with pytest.raises(LLMError) as caught:
        OpenAILLMProvider(client=harness.client)
    assert caught.value.code == LLMErrorCode.INVALID_CONFIGURATION
    assert not harness.client.is_closed()


@pytest.mark.anyio
async def test_composition_and_constructor_options(
    harness: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.llm.providers.openai as adapter

    options: dict[str, Any] = {}

    def offline_client(**kwargs: Any) -> AsyncOpenAI:
        options.update(kwargs)
        return harness.client

    monkeypatch.setattr(adapter, "AsyncOpenAI", offline_client)
    settings = IsolatedSettings(llm_provider="openai", openai_api_key=SecretStr(SECRET))
    assert SECRET not in repr(settings)
    assert SECRET not in settings.model_dump_json()
    provider = create_provider(settings)
    assert isinstance(provider, OpenAILLMProvider)
    assert options["max_retries"] == 0
    assert options["base_url"] == "https://api.openai.com/v1"
    assert options["timeout"] == 20.0
    await provider.aclose()


def test_openai_selection_without_key_fails() -> None:
    settings = IsolatedSettings(llm_provider="openai")
    with pytest.raises(LLMError) as caught:
        create_provider(settings)
    assert caught.value.code == LLMErrorCode.MISSING_CREDENTIALS


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("headers", "delay"),
    [
        ({"retry-after-ms": "1250"}, 1.25),
        ({"retry-after": "nan"}, None),
        ({"retry-after": "-1"}, None),
        ({"retry-after": "invalid"}, None),
        ({"retry-after": "Mon, 01 Jan 1990 00:00:00 GMT"}, 0.0),
    ],
)
async def test_retry_metadata_parsing_without_waiting(
    headers: dict[str, str],
    delay: float | None,
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    harness.status = 429
    harness.body = {"error": {"code": "rate_limit_exceeded"}}
    harness.headers.update(headers)
    with pytest.raises(LLMError) as caught:
        await harness.provider.generate(request_model)
    assert caught.value.metadata.retry_after_seconds == delay
    assert len(harness.requests) == 1


@pytest.mark.anyio
async def test_bad_envelope_has_no_content_in_warnings(
    harness: Harness,
    request_model: LLMRequest,
    capsys: pytest.CaptureFixture[str],
) -> None:
    harness.body = payload(output=[{"type": "unknown", "content": PRIVATE}])
    with warnings.catch_warnings(record=True) as captured, pytest.raises(LLMError) as caught:
        warnings.simplefilter("always")
        await harness.provider.generate(request_model)
    assert not captured
    assert caught.value.__context__ is None
    output = capsys.readouterr()
    assert PRIVATE not in output.out + output.err


@pytest.mark.anyio
@pytest.mark.parametrize("text", [None, "", " \n"])
async def test_missing_text_gateway_failure(
    text: str | None,
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    if text is None:
        harness.body = payload(output=[])
    else:
        harness.body["output"][0]["content"][0]["text"] = text
    with pytest.raises(LLMError) as caught:
        await LLMGateway(harness.provider).generate(request_model)
    assert caught.value.code == LLMErrorCode.MISSING_TEXT
    assert len(harness.requests) == 1


@pytest.mark.anyio
@pytest.mark.parametrize("body", [{}, [], None, "not-an-envelope"])
async def test_malformed_top_level(
    body: Any,
    harness: Harness,
    request_model: LLMRequest,
) -> None:
    harness.body = body
    with pytest.raises(LLMError) as caught:
        await harness.provider.generate(request_model)
    assert caught.value.code == LLMErrorCode.INVALID_RESPONSE
    assert len(harness.requests) == 1


@pytest.mark.anyio
async def test_local_client_creation_cleanup_and_no_ambient_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://untrusted.invalid/v1")
    provider = OpenAILLMProvider(api_key=SecretStr(SECRET))
    # Construction/close alone never makes a network request (external sockets are blocked).
    await provider.aclose()
    await provider.aclose()


@pytest.mark.anyio
async def test_unsafe_upstream_id_dropped(harness: Harness, request_model: LLMRequest) -> None:
    harness.headers["x-request-id"] = SECRET
    result = await harness.provider.generate(request_model)
    assert result.upstream_request_id is None
