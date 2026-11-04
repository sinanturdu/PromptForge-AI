"""Deterministic explicit scripts, not genuine model-generated content."""

from collections import deque
from collections.abc import Iterable

from app.llm.errors import LLMError, LLMErrorCode
from app.llm.schemas import LLMRequest, LLMResponse


class FakeLLMProvider:
    """One script item per call. No network, keys, recorded prompts or global state."""

    def __init__(self, script: Iterable[LLMResponse | LLMErrorCode] = ()) -> None:
        self._script: deque[LLMResponse | LLMErrorCode] = deque()
        for item in script:
            if isinstance(item, LLMResponse) and item.provider == "fake":
                self._script.append(item.model_copy(deep=True))
            elif isinstance(item, LLMErrorCode):
                self._script.append(item)
            else:
                raise LLMError(LLMErrorCode.INVALID_CONFIGURATION)
        self._closed = False
        self._calls = 0

    @property
    def calls(self) -> int:
        return self._calls

    @property
    def closed(self) -> bool:
        return self._closed

    async def generate(self, request: LLMRequest) -> LLMResponse:
        self._calls += 1
        if self._closed:
            raise LLMError(LLMErrorCode.PROVIDER_CLOSED)
        if not self._script:
            raise LLMError(LLMErrorCode.SCRIPT_EXHAUSTED)
        item = self._script.popleft()
        if isinstance(item, LLMErrorCode):
            raise LLMError(item)
        return item

    async def aclose(self) -> None:
        self._closed = True
        self._script.clear()
