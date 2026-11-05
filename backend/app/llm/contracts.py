"""Adapters make one asynchronous generation attempt and own no transactions."""

from typing import Protocol

from app.llm.schemas import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    async def generate(self, request: LLMRequest) -> LLMResponse:
        """Make one attempt; raise a normalized LLMError on provider failure."""
        ...

    async def aclose(self) -> None:
        """Release adapter resources; the creator is responsible for closing."""
        ...
