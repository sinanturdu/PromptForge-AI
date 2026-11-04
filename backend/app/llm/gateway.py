"""One operation budget for transport retries, schema repair and elapsed time."""

import asyncio
import math
import random
import re
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.core.logging import get_logger
from app.llm.contracts import LLMProvider
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import (
    CallMetadata,
    GatewayPolicy,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    StructuredResult,
    TokenUsage,
)

logger = get_logger("llm")


@dataclass
class _Operation:
    started: float
    deadline: float
    attempts: int = 0
    retries: int = 0
    repair: bool = False
    provider: str | None = None
    resolved_model: str | None = None
    upstream_request_id: str | None = None
    usages: list[TokenUsage | None] = field(default_factory=list)


def _usage(usages: list[TokenUsage | None]) -> TokenUsage | None:
    totals: dict[str, int | None] = {}
    for key in TokenUsage.model_fields:
        known = [
            getattr(usage, key)
            for usage in usages
            if usage is not None and getattr(usage, key) is not None
        ]
        totals[key] = sum(known) if known else None
    return (
        TokenUsage.model_validate(totals) if any(v is not None for v in totals.values()) else None
    )


def _safe_identifier(value: str | None) -> str | None:
    return value if value and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}", value) else None


class LLMGateway:
    """Borrows a single-attempt provider. All mutable budgets are local to an operation."""

    def __init__(
        self,
        provider: LLMProvider,
        *,
        policy: GatewayPolicy | None = None,
        provider_name: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._provider = provider
        self._policy = GatewayPolicy.model_validate((policy or GatewayPolicy()).model_dump())
        self._provider_name = provider_name
        self._clock, self._sleep, self._jitter = clock, sleep, jitter

    def _start(self) -> _Operation:
        now = self._clock()
        return _Operation(
            now, now + self._policy.call_deadline_seconds, provider=self._provider_name
        )

    def _metadata(self, op: _Operation, request: LLMRequest) -> CallMetadata:
        complete = bool(op.usages) and all(
            usage is not None and all(v is not None for v in usage.model_dump().values())
            for usage in op.usages
        )
        return CallMetadata(
            provider=op.provider,
            requested_model=request.settings.model,
            resolved_model=op.resolved_model,
            request_id=request.request_id,
            upstream_request_id=op.upstream_request_id,
            usage=_usage(op.usages),
            usage_complete=complete,
            attempts=op.attempts,
            retries=op.retries,
            repair_attempted=op.repair,
            duration_ms=max(0, self._clock() - op.started) * 1000,
        )

    def _finish(
        self,
        op: _Operation,
        request: LLMRequest,
        code: str | None = None,
    ) -> CallMetadata:
        metadata = self._metadata(op, request)
        logger.info(
            "LLM operation completed",
            extra={
                "context": {
                    "provider": _safe_identifier(metadata.provider),
                    "model": _safe_identifier(metadata.resolved_model or metadata.requested_model),
                    "attempts": metadata.attempts,
                    "retries": metadata.retries,
                    "repair_attempted": metadata.repair_attempted,
                    "duration_ms": metadata.duration_ms,
                    "usage": metadata.usage.model_dump() if metadata.usage is not None else None,
                    "usage_complete": metadata.usage_complete,
                    "error_code": code,
                }
            },
        )
        return metadata

    def _remaining(self, op: _Operation) -> float:
        remaining = op.deadline - self._clock()
        if remaining <= 0:
            raise LLMError(LLMErrorCode.DEADLINE_EXCEEDED)
        return remaining

    @staticmethod
    def _retryable(error: LLMError) -> bool:
        if error.code in {LLMErrorCode.TIMEOUT, LLMErrorCode.RATE_LIMIT}:
            return True
        return error.code == LLMErrorCode.PROVIDER_UNAVAILABLE and (
            error.metadata.http_status is None or error.metadata.http_status in {500, 502, 503, 504}
        )

    async def _call(
        self,
        request: LLMRequest,
        op: _Operation,
        *,
        repair: bool = False,
    ) -> LLMResponse:
        while True:
            timeout = min(self._policy.request_timeout_seconds, self._remaining(op))
            if op.attempts >= 4:
                raise LLMError(LLMErrorCode.PROVIDER_UNAVAILABLE)
            started = self._clock()
            op.attempts += 1
            op.repair = op.repair or repair
            response: LLMResponse | None = None
            failure: LLMError | None = None
            retryable = True
            try:
                # Same-task cancellation: no detached/background generation tasks.
                async with asyncio.timeout(timeout):
                    response = await self._provider.generate(request)
            except LLMError as exc:
                failure = LLMError(exc.code, metadata=exc.metadata)
            except TimeoutError:
                failure = LLMError(LLMErrorCode.TIMEOUT)
            except Exception:
                failure = LLMError(LLMErrorCode.PROVIDER_UNAVAILABLE)
                retryable = False
            op.usages.append(response.usage if response is not None else None)
            if response is not None:
                op.provider, op.resolved_model = response.provider, response.resolved_model
                op.upstream_request_id = response.upstream_request_id
            self._remaining(op)
            if self._clock() - started >= timeout:
                failure = LLMError(LLMErrorCode.TIMEOUT)
            if failure is None:
                assert response is not None
                if response.finish_reason == "refusal":
                    raise LLMError(LLMErrorCode.REFUSAL)
                if response.finish_reason == "length":
                    raise LLMError(LLMErrorCode.TRUNCATION)
                if response.finish_reason != "complete":
                    raise LLMError(LLMErrorCode.PROVIDER_UNAVAILABLE)
                if response.text is None or not response.text.strip():
                    raise LLMError(LLMErrorCode.MISSING_TEXT)
                return response
            if (
                not retryable
                or not self._retryable(failure)
                or op.retries >= self._policy.max_retries
            ):
                raise failure
            ceiling = min(2.0, 0.5 * 2**op.retries)
            sample = self._jitter()
            if not math.isfinite(sample) or not 0 <= sample <= 1:
                sample = 0.5
            delay = ceiling * (0.5 + sample * 0.5)
            suggested = failure.metadata.retry_after_seconds
            if suggested is not None and math.isfinite(suggested) and suggested >= 0:
                delay = max(delay, suggested)
            # Never shorten Retry-After to squeeze another call into the deadline.
            if delay >= self._remaining(op):
                raise failure
            wait_expired = False
            try:
                async with asyncio.timeout(self._remaining(op)):
                    await self._sleep(delay)
            except TimeoutError:
                wait_expired = True
            self._remaining(op)
            if wait_expired:
                raise LLMError(LLMErrorCode.DEADLINE_EXCEEDED)
            op.retries += 1

    @staticmethod
    def _validate[T: BaseModel](text: str, output_type: type[T]) -> tuple[T | None, str]:
        value = None
        feedback = "Return a JSON object matching the required schema."
        try:
            value = output_type.model_validate_json(text, strict=True)
        except ValidationError as exc:
            # Fixed error categories only: no raw values, paths or validator messages.
            allowed = {
                "json_invalid",
                "missing",
                "extra_forbidden",
                "int_type",
                "string_type",
                "bool_type",
                "float_type",
                "list_type",
                "dict_type",
                "literal_error",
            }
            categories = sorted(
                {
                    e["type"]
                    for e in exc.errors(
                        include_input=False, include_context=False, include_url=False
                    )
                }
                & allowed
            )
            if categories:
                feedback += " Validation categories: " + ", ".join(categories[:5]) + "."
        except ValueError:
            pass
        return value, feedback

    async def _generate_json[T: BaseModel](
        self,
        request: LLMRequest,
        output_type: type[T],
        op: _Operation,
    ) -> StructuredResult[T]:
        schema = None
        with suppress(Exception):
            schema = output_type.model_json_schema()
        self._remaining(op)
        if schema is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        current = request.model_copy(update={"response_schema": schema}, deep=True)
        for repair in range(self._policy.json_repair_attempts + 1):
            response = await self._call(current, op, repair=repair > 0)
            assert response.text is not None
            value, feedback = self._validate(response.text, output_type)
            self._remaining(op)
            if value is not None:
                return StructuredResult[T](value=value, metadata=self._finish(op, request))
            if repair == self._policy.json_repair_attempts:
                break
            current = current.model_copy(
                update={
                    "messages": (
                        *request.messages,
                        LLMMessage(role="assistant", content=response.text),
                        LLMMessage(
                            role="user",
                            content=(
                                "Correct only the JSON/schema defects in the preceding response. "
                                "Preserve the original task intent and output schema; "
                                "do not add a new task. "
                                "Return only corrected JSON. " + feedback
                            ),
                        ),
                    )
                },
                deep=True,
            )
        raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)

    async def generate(self, request: LLMRequest) -> LLMResponse:
        op = self._start()
        failure: LLMError | None = None
        try:
            result = await self._call(request, op)
            return result.model_copy(update={"call_metadata": self._finish(op, request)})
        except LLMError as exc:
            failure = LLMError(
                exc.code,
                metadata=exc.metadata.model_copy(
                    update={"call": self._finish(op, request, exc.code)}
                ),
            )
        except asyncio.CancelledError:
            self._finish(op, request, "cancelled")
            raise
        assert failure is not None
        raise failure

    async def generate_json[T: BaseModel](
        self,
        request: LLMRequest,
        output_type: type[T],
    ) -> StructuredResult[T]:
        op = self._start()
        failure: LLMError | None = None
        try:
            return await self._generate_json(request, output_type, op)
        except LLMError as exc:
            failure = LLMError(
                exc.code,
                metadata=exc.metadata.model_copy(
                    update={"call": self._finish(op, request, exc.code)}
                ),
            )
        except asyncio.CancelledError:
            self._finish(op, request, "cancelled")
            raise
        assert failure is not None
        raise failure
