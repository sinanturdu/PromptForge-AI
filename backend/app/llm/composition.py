"""Explicit composition; the caller owns and closes the returned provider."""

from collections.abc import Iterable

from app.core.config import Settings
from app.llm.contracts import LLMProvider
from app.llm.errors import LLMError, LLMErrorCode
from app.llm.providers.fake import FakeLLMProvider
from app.llm.schemas import LLMResponse


def create_provider(
    settings: Settings, *, script: Iterable[LLMResponse | LLMErrorCode] = ()
) -> LLMProvider:
    if settings.llm_provider == "fake":
        return FakeLLMProvider(script)
    if settings.llm_provider == "openai":
        # Real SDK types and imports stay behind the explicit provider selection.
        from app.llm.providers.openai import OpenAILLMProvider

        return OpenAILLMProvider(api_key=settings.openai_api_key)
    raise LLMError(LLMErrorCode.INVALID_CONFIGURATION)
