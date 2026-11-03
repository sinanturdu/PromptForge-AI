"""Application service coordinating PromptRequest business rules and transactions."""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.enums import TargetMode
from app.models.prompt_request import PromptRequest
from app.modules.prompts.repository import PromptRequestRepository
from app.modules.prompts.schemas import (
    DEFAULT_PAGE,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    MAX_SEARCH_QUERY_LENGTH,
    PromptRequestCreate,
    PromptRequestListResponse,
    PromptRequestRead,
)


def normalize_search_query(raw_query: str | None) -> str | None:
    """Normalize optional search input by trimming whitespace and collapsing blanks to None."""
    if raw_query is None:
        return None
    if len(raw_query) > MAX_SEARCH_QUERY_LENGTH:
        raise ValueError(f"q must be at most {MAX_SEARCH_QUERY_LENGTH} characters.")
    stripped = raw_query.strip()
    if not stripped:
        return None
    return stripped


class PromptRequestNotFoundError(NotFoundError):
    """Raised when a requested PromptRequest record does not exist."""

    def __init__(self, prompt_request_id: UUID) -> None:
        self.prompt_request_id = prompt_request_id
        super().__init__(
            detail="Prompt request not found.",
            error_code="prompt_request_not_found",
        )


class PromptRequestService:
    """Manages validation, transaction boundaries, and persistence for prompt requests."""

    def __init__(
        self,
        session: AsyncSession,
        repository: PromptRequestRepository | None = None,
    ) -> None:
        self._session = session
        self._repository = (
            repository if repository is not None else PromptRequestRepository(session)
        )

    async def create_prompt_request(self, payload: PromptRequestCreate) -> PromptRequestRead:
        """Create and commit a new PromptRequest, rolling back on any persistence error."""
        prompt_request = PromptRequest(
            raw_prompt=payload.raw_prompt,
            user_context=payload.user_context,
            output_language=payload.output_language,
            target_mode=payload.target_mode.value,
            task_profile=payload.task_profile,
            prompt_detail_level=payload.prompt_detail_level.value,
            response_detail_preference=payload.response_detail_preference.value,
            reasoning_preference=payload.reasoning_preference.value,
        )
        try:
            persisted = await self._repository.create(prompt_request)
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            raise

        return PromptRequestRead.model_validate(persisted)

    async def get_prompt_request(self, prompt_request_id: UUID) -> PromptRequestRead:
        """Retrieve a single PromptRequest by UUID or raise PromptRequestNotFoundError."""
        record = await self._repository.get_by_id(prompt_request_id)
        if record is None:
            raise PromptRequestNotFoundError(prompt_request_id)
        return PromptRequestRead.model_validate(record)

    async def list_prompt_requests(
        self,
        *,
        page: int = DEFAULT_PAGE,
        page_size: int = DEFAULT_PAGE_SIZE,
        q: str | None = None,
        target_mode: TargetMode | None = None,
    ) -> PromptRequestListResponse:
        """Return a paginated, optionally filtered list of PromptRequest records newest first."""
        if page < 1:
            raise ValueError("page must be at least 1.")
        if page_size < 1 or page_size > MAX_PAGE_SIZE:
            raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}.")

        normalized_query = normalize_search_query(q)
        offset = (page - 1) * page_size
        total = await self._repository.count_filtered(
            search_query=normalized_query,
            target_mode=target_mode,
        )
        records = await self._repository.list_paginated(
            offset=offset,
            limit=page_size,
            search_query=normalized_query,
            target_mode=target_mode,
        )
        return PromptRequestListResponse(
            items=[PromptRequestRead.model_validate(record) for record in records],
            total=total,
            page=page,
            page_size=page_size,
        )
