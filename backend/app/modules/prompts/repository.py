"""SQLAlchemy 2.0 async repository for PromptRequest persistence and queries."""

from typing import Final
from uuid import UUID

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import TargetMode
from app.models.prompt_request import PromptRequest

LIKE_ESCAPE_CHAR: Final[str] = "\\"


def escape_like_term(value: str, *, escape_char: str = LIKE_ESCAPE_CHAR) -> str:
    """Escape literal backslashes and SQL LIKE wildcards (% and _) in user search input."""
    if len(escape_char) != 1:
        raise ValueError("escape_char must be a single character.")
    return (
        value.replace(escape_char, escape_char + escape_char)
        .replace("%", escape_char + "%")
        .replace("_", escape_char + "_")
    )


class PromptRequestRepository:
    """Data-access layer for storing and querying PromptRequest records."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _build_filter_conditions(
        *,
        search_query: str | None = None,
        target_mode: TargetMode | None = None,
    ) -> list[ColumnElement[bool]]:
        """Build shared SQLAlchemy WHERE predicates for list and count queries."""
        conditions: list[ColumnElement[bool]] = []
        if target_mode is not None:
            conditions.append(PromptRequest.target_mode == target_mode.value)

        if search_query is not None:
            normalized = search_query.strip()
            if normalized:
                escaped_pattern = f"%{escape_like_term(normalized)}%"
                conditions.append(
                    or_(
                        PromptRequest.raw_prompt.ilike(escaped_pattern, escape=LIKE_ESCAPE_CHAR),
                        and_(
                            PromptRequest.user_context.is_not(None),
                            PromptRequest.user_context.ilike(
                                escaped_pattern,
                                escape=LIKE_ESCAPE_CHAR,
                            ),
                        ),
                    )
                )

        return conditions

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

    async def list_paginated(
        self,
        *,
        offset: int,
        limit: int,
        search_query: str | None = None,
        target_mode: TargetMode | None = None,
    ) -> list[PromptRequest]:
        """Fetch a deterministic page of filtered PromptRequest rows ordered newest first."""
        if offset < 0:
            raise ValueError("offset must be greater than or equal to 0.")
        if limit < 1:
            raise ValueError("limit must be at least 1.")

        conditions = self._build_filter_conditions(
            search_query=search_query,
            target_mode=target_mode,
        )
        statement = select(PromptRequest)
        if conditions:
            statement = statement.where(*conditions)

        statement = (
            statement.order_by(PromptRequest.created_at.desc(), PromptRequest.id.desc())
            .offset(offset)
            .limit(limit)
        )
        result = await self._session.execute(statement)
        return list(result.scalars().all())

    async def count_filtered(
        self,
        *,
        search_query: str | None = None,
        target_mode: TargetMode | None = None,
    ) -> int:
        """Return the count of persisted PromptRequest rows matching the given filters."""
        conditions = self._build_filter_conditions(
            search_query=search_query,
            target_mode=target_mode,
        )
        statement = select(func.count()).select_from(PromptRequest)
        if conditions:
            statement = statement.where(*conditions)
        result = await self._session.execute(statement)
        return int(result.scalar_one())

    async def count_all(self) -> int:
        """Return the total count of persisted PromptRequest rows."""
        return await self.count_filtered()
