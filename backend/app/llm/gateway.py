"""Single-attempt generation and local validation; retries/repair belong to M5A-4."""

from contextlib import suppress

from pydantic import BaseModel, ValidationError

from app.llm.contracts import LLMProvider
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import CallMetadata, LLMRequest, LLMResponse, StructuredResult


class LLMGateway:
    """Borrows a provider. Its creator, not this gateway, must call provider.aclose()."""

    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    async def generate(self, request: LLMRequest) -> LLMResponse:
        response: LLMResponse | None = None
        failure = LLMErrorCode.PROVIDER_UNAVAILABLE
        try:
            response = await self._provider.generate(request)
        except LLMError as exc:
            failure = exc.code
        except Exception:
            # Cancellation (BaseException) must propagate without being converted.
            pass
        if response is None:
            # Raise outside the handler so even __context__ retains no raw exception.
            raise LLMError(failure)
        if response.finish_reason == "refusal":
            raise LLMError(LLMErrorCode.REFUSAL)
        if response.finish_reason == "length":
            raise LLMError(LLMErrorCode.TRUNCATION)
        if response.finish_reason != "complete":
            raise LLMError(LLMErrorCode.PROVIDER_UNAVAILABLE)
        if response.text is None or not response.text.strip():
            raise LLMError(LLMErrorCode.MISSING_TEXT)
        return response

    async def generate_json[T: BaseModel](
        self, request: LLMRequest, output_type: type[T]
    ) -> StructuredResult[T]:
        schema = None
        with suppress(Exception):
            schema = output_type.model_json_schema()
        if schema is None:
            raise LLMError(LLMErrorCode.INVALID_REQUEST)
        # The supplied output model is authoritative; never mutate the caller's request.
        structured_request = request.model_copy(update={"response_schema": schema}, deep=True)
        response = await self.generate(structured_request)
        assert response.text is not None  # guaranteed by generate()
        value = None
        with suppress(ValidationError, ValueError):
            value = output_type.model_validate_json(response.text, strict=True)
        if value is None:
            raise LLMError(LLMErrorCode.INVALID_STRUCTURED_OUTPUT)
        return StructuredResult[T](
            value=value,
            metadata=CallMetadata(
                provider=response.provider,
                resolved_model=response.resolved_model,
                usage=response.usage,
                upstream_request_id=response.upstream_request_id,
                request_id=request.request_id,
            ),
        )
