"""SQLAlchemy 2.0 async repository for PromptRequest persistence and queries."""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.prompt_request import PromptRequest


class PromptRequestRepository:
    """Data-access layer for storing and querying PromptRequest records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, prompt_request: PromptRequest) -> PromptRequest:
        """Stage and flush a new PromptRequest within the active session transaction."""
        self._session.add(prompt_request)
        await self._session.flush()
        return prompt_request

    async def get_by_id(self, prompt_request_id: UUID) -> PromptRequest | None:
        """Fetch a single PromptRequest by its UUID primary key, or None if missing."""
        statement = select(PromptRequest).where(PromptRequest.id == prompt_request_id)
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def list_paginated(self, *, offset: int, limit: int) -> list[PromptRequest]:
        """Fetch a deterministic page of PromptRequest rows ordered newest first."""
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0.")
        if limit < 1:
            raise ValueError("limit must be at least 1.")

        statement = (
            select(PromptRequest)
            .order_by(PromptRequest.created_at.desc(), PromptRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def count_all(self) -> int:
        """Return the total count of persisted PromptRequest rows."""
        statement = select(func.count()).select_from(PromptRequest)
        result = await self._session.execute(statement)
        return int(result.scalar_one())
