"""One non-streaming Responses request; no SDK types escape this adapter."""

import logging
import math
import re
from contextlib import suppress
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from json import JSONDecodeError
from typing import Literal

import openai
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, omit
from openai.types.responses import Response, ResponseInputParam, ResponseTextConfigParam
from pydantic import SecretStr, ValidationError

from app.llm.errors import LLMError, LLMErrorCode, LLMErrorMetadata
from app.llm.providers._openai_schema import validate_schema
from app.llm.schemas import LLMRequest, LLMResponse, TokenUsage


def _request_id(value: object) -> str | None:
    # Request IDs are opaque metadata, not arbitrary header/body text or credentials.
    if isinstance(value, str) and re.fullmatch(r"req_[A-Za-z0-9_-]{1,200}", value):
        return value
    return None


def _retry_delay(exc: openai.APIStatusError) -> float | None:
    headers = exc.response.headers
    value: float | None = None
    try:
        if "retry-after-ms" in headers:
            value = float(headers["retry-after-ms"]) / 1000
        elif "retry-after" in headers:
            try:
                value = float(headers["retry-after"])
            except ValueError:
                date = parsedate_to_datetime(headers["retry-after"])
                if date.tzinfo is not None:
                    value = max(0, (date - datetime.now(UTC)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None
    return value if value is not None and math.isfinite(value) and value >= 0 else None


def _status_error(exc: openai.APIStatusError) -> LLMError:
    status = exc.status_code
    code = LLMErrorCode.PROVIDER_UNAVAILABLE
    if status == 401:
        code = LLMErrorCode.AUTHENTICATION
    elif status == 403:
        code = LLMErrorCode.PERMISSION
    elif status in {402, 429}:
        body = exc.body
        # SDK error bodies may be either the inner error or the full envelope.
        if isinstance(body, dict) and isinstance(body.get("error"), dict):
            body = body["error"]
        quota_codes = {"insufficient_quota", "billing_hard_limit_reached", "billing_not_active"}
        is_quota = isinstance(body, dict) and any(
            isinstance(body.get(key), str) and body[key] in quota_codes for key in ("code", "type")
        )
        code = (
            LLMErrorCode.QUOTA_EXHAUSTED if status == 402 or is_quota else LLMErrorCode.RATE_LIMIT
        )
    elif status == 408:
        code = LLMErrorCode.TIMEOUT
    elif status in {400, 404, 409, 422}:
        code = LLMErrorCode.INVALID_REQUEST
    return LLMError(
        code,
        metadata=LLMErrorMetadata(
            upstream_request_id=_request_id(exc.request_id),
            http_status=status if 100 <= status <= 599 else None,
            retry_after_seconds=_retry_delay(exc),
        ),
    )


def _translate(raw: Response) -> LLMResponse:
    # The SDK normally constructs models permissively. Revalidate the actual
    # envelope, including discriminated output items, before accessing them.
    if not isinstance(raw, Response):
        raise LLMError(LLMErrorCode.INVALID_RESPONSE)
    metadata = LLMErrorMetadata(upstream_request_id=_request_id(getattr(raw, "_request_id", None)))
    validated: Response | None = None
    with suppress(ValidationError):
        validated = Response.model_validate(raw.model_dump(warnings=False), strict=True)
    if validated is None:
        raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
    response = validated
    if response.status not in {"completed", "incomplete"} or response.error is not None:
        raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
    refusal = False
    texts: list[str] = []
    for item in response.output:
        if item.type == "message":
            if response.status == "completed" and item.status != "completed":
                raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
            for content in item.content:
                if content.type == "refusal":
                    refusal = True
                elif content.type == "output_text":
                    texts.append(content.text)
        elif item.type != "reasoning":
            # No tools or other output modalities were requested.
            raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
    finish: Literal["complete", "length", "refusal", "other"]
    if response.status == "incomplete":
        reason = response.incomplete_details.reason if response.incomplete_details else None
        if reason == "max_output_tokens":
            finish = "length"
        elif reason == "content_filter":
            finish = "refusal"
        else:
            raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
    elif response.incomplete_details is not None:
        raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
    else:
        finish = "refusal" if refusal else "complete"
    usage = response.usage
    translated: LLMResponse | None = None
    with suppress(ValidationError):
        translated = LLMResponse(
            provider="openai",
            resolved_model=response.model,
            text="".join(texts) if texts else None,
            finish_reason=finish,
            upstream_request_id=metadata.upstream_request_id,
            usage=None
            if usage is None
            else TokenUsage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                total_tokens=usage.total_tokens,
                cached_input_tokens=usage.input_tokens_details.cached_tokens,
                reasoning_tokens=usage.output_tokens_details.reasoning_tokens,
            ),
        )
    if translated is None:
        raise LLMError(LLMErrorCode.INVALID_RESPONSE, metadata=metadata)
    return translated


class OpenAILLMProvider:
    """Own locally created clients; borrow injected clients unless ownership is transferred.

    Injected clients must disable retries. Their creator configures their transport.
    The optional temperature allowlist is an explicit capability declaration for
    exact model IDs verified by the caller; unknown IDs never get temperature.
    """

    def __init__(
        self,
        *,
        api_key: SecretStr | None = None,
        client: AsyncOpenAI | None = None,
        owns_client: bool = False,
        temperature_models: frozenset[str] = frozenset(),
        timeout_seconds: float = 20.0,
    ) -> None:
        if not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 300:
            raise LLMError(LLMErrorCode.INVALID_CONFIGURATION)
        if client is not None and client.max_retries != 0:
            raise LLMError(LLMErrorCode.INVALID_CONFIGURATION)
        if client is None:
            if api_key is None or not api_key.get_secret_value().strip():
                raise LLMError(LLMErrorCode.MISSING_CREDENTIALS)
            if any(character.isspace() for character in api_key.get_secret_value()):
                raise LLMError(LLMErrorCode.INVALID_CONFIGURATION)
            # Pin the official endpoint instead of inheriting OPENAI_BASE_URL.
            client = AsyncOpenAI(
                api_key=api_key.get_secret_value(),
                base_url="https://api.openai.com/v1",
                max_retries=0,
                timeout=timeout_seconds,
                http_client=DefaultAsyncHttpxClient(follow_redirects=False),
            )
            owns_client = True
        # SDK DEBUG logs include request content. Do not inherit OPENAI_LOG=debug.
        logging.getLogger("openai").setLevel(logging.WARNING)
        self._client = client
        self._owns_client = owns_client
        self._temperature_models = temperature_models
        self._closed = False

    async def generate(self, request: LLMRequest) -> LLMResponse:
        if self._closed:
            raise LLMError(LLMErrorCode.PROVIDER_CLOSED)
        if self._client.max_retries != 0:
            raise LLMError(LLMErrorCode.INVALID_CONFIGURATION)
        temperature = request.settings.temperature
        if temperature is not None and request.settings.model not in self._temperature_models:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        text: ResponseTextConfigParam = {}
        if request.response_schema is not None:
            validate_schema(request.response_schema)
            text = {
                "format": {
                    "type": "json_schema",
                    "name": "promptforge_output",
                    "strict": True,
                    "schema": dict(request.response_schema),
                }
            }
        messages: ResponseInputParam = [
            {"role": message.role, "content": message.content, "type": "message"}
            for message in request.messages
        ]
        result: Response | None = None
        failure: LLMError | None = None
        try:
            result = await self._client.responses.create(
                model=request.settings.model,
                input=messages,
                max_output_tokens=request.settings.max_output_tokens,
                temperature=temperature if temperature is not None else omit,
                text=text if text else omit,
                store=False,
                stream=False,
            )
        except openai.APITimeoutError:
            failure = LLMError(LLMErrorCode.TIMEOUT)
        except openai.APIConnectionError:
            failure = LLMError(LLMErrorCode.PROVIDER_UNAVAILABLE)
        except openai.APIResponseValidationError as exc:
            failure = LLMError(
                LLMErrorCode.INVALID_RESPONSE,
                metadata=LLMErrorMetadata(
                    upstream_request_id=_request_id(exc.response.headers.get("x-request-id")),
                    http_status=exc.status_code,
                ),
            )
        except JSONDecodeError:
            failure = LLMError(LLMErrorCode.INVALID_RESPONSE)
        except openai.APIStatusError as exc:
            failure = _status_error(exc)
        # Outside handlers: raw SDK exceptions must not survive as __context__.
        if failure is not None:
            raise failure
        assert result is not None
        return _translate(result)

    async def aclose(self) -> None:
        if not self._closed:
            if self._owns_client:
                await self._client.close()
            self._closed = True
