"""Tests for PromptRequestRepository, PromptRequestService, and schema alignment."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import String
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import DEFAULT_SQLITE_DB_PATH, build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.session import DatabaseSessionManager
from app.models.enums import (
    PromptDetailLevel,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
)
from app.models.prompt_request import PromptRequest
from app.modules.prompts.repository import PromptRequestRepository
from app.modules.prompts.schemas import (
    MAX_OUTPUT_LANGUAGE_LENGTH,
    MAX_TASK_PROFILE_LENGTH,
    PromptRequestCreate,
)
from app.modules.prompts.service import PromptRequestNotFoundError, PromptRequestService


@pytest.fixture
def anyio_backend() -> str:
    """Run async repository and service tests on the asyncio backend."""
    return "asyncio"


@pytest.fixture
def migrated_sqlite_url(tmp_path: Path) -> str:
    """Provision an isolated temporary SQLite database migrated to Alembic head."""
    db_file = tmp_path / "repo_tests" / "prompt_repo.db"
    db_url = build_sqlite_url(db_file)
    command.upgrade(build_alembic_config(db_url), "head")
    return db_url


def test_pydantic_defaults_and_lengths_match_prompt_request_orm_model() -> None:
    payload = PromptRequestCreate(raw_prompt="Design a modular FastAPI service.")
    columns = PromptRequest.__table__.columns

    assert payload.target_mode.value == columns["target_mode"].default.arg
    assert payload.prompt_detail_level.value == columns["prompt_detail_level"].default.arg
    assert (
        payload.response_detail_preference.value
        == columns["response_detail_preference"].default.arg
    )
    assert payload.reasoning_preference.value == columns["reasoning_preference"].default.arg

    output_lang_type = columns["output_language"].type
    task_profile_type = columns["task_profile"].type
    assert isinstance(output_lang_type, String)
    assert isinstance(task_profile_type, String)
    assert output_lang_type.length == MAX_OUTPUT_LANGUAGE_LENGTH
    assert task_profile_type.length == MAX_TASK_PROFILE_LENGTH


@pytest.mark.anyio
async def test_repository_create_get_by_id_and_count_all(migrated_sqlite_url: str) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.session() as write_session:
            repo = PromptRequestRepository(write_session)
            assert await repo.count_all() == 0

            entity = PromptRequest(
                raw_prompt="Build a CLI task runner in Python.",
                user_context="Must support Windows and Linux.",
                output_language="en",
                target_mode=TargetMode.OPENAI.value,
                task_profile="new_project",
                prompt_detail_level=PromptDetailLevel.HIGH.value,
                response_detail_preference=ResponseDetailPreference.COMPREHENSIVE.value,
                reasoning_preference=ReasoningPreference.MEDIUM.value,
            )
            created = await repo.create(entity)
            created_id = created.id
            await write_session.commit()

        async with manager.session() as read_session:
            read_repo = PromptRequestRepository(read_session)
            assert await read_repo.count_all() == 1

            loaded = await read_repo.get_by_id(created_id)
            assert loaded is not None
            assert loaded.id == created_id
            assert loaded.raw_prompt == "Build a CLI task runner in Python."
            assert loaded.user_context == "Must support Windows and Linux."
            assert loaded.output_language == "en"
            assert loaded.target_mode == "openai"
            assert loaded.task_profile == "new_project"
            assert loaded.prompt_detail_level == "high"
            assert loaded.response_detail_preference == "comprehensive"
            assert loaded.reasoning_preference == "medium"
            assert loaded.created_at.tzinfo == UTC

            assert await read_repo.get_by_id(uuid4()) is None
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_repository_paginated_listing_and_deterministic_id_tie_breaker(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        shared_ts = datetime(2026, 6, 15, 10, 30, 0, tzinfo=UTC)
        older_ts = datetime(2026, 6, 15, 9, 0, 0, tzinfo=UTC)
        newest_ts = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)

        id_low = UUID("00000000-0000-0000-0000-000000000001")
        id_mid = UUID("00000000-0000-0000-0000-000000000002")
        id_high = UUID("00000000-0000-0000-0000-000000000003")
        id_older = UUID("00000000-0000-0000-0000-000000000010")
        id_newest = UUID("00000000-0000-0000-0000-000000000000")

        async with manager.session() as session:
            repo = PromptRequestRepository(session)
            for item_id, label, ts in (
                (id_mid, "tie-mid", shared_ts),
                (id_older, "older", older_ts),
                (id_high, "tie-high", shared_ts),
                (id_newest, "newest", newest_ts),
                (id_low, "tie-low", shared_ts),
            ):
                await repo.create(
                    PromptRequest(
                        id=item_id,
                        raw_prompt=f"Prompt {label}",
                        created_at=ts,
                    )
                )
            await session.commit()

        expected_order = [id_newest, id_high, id_mid, id_low, id_older]

        async with manager.session() as read_session:
            read_repo = PromptRequestRepository(read_session)
            assert await read_repo.count_all() == 5

            page_1 = await read_repo.list_paginated(offset=0, limit=2)
            page_2 = await read_repo.list_paginated(offset=2, limit=2)
            page_3 = await read_repo.list_paginated(offset=4, limit=2)

            observed_ids = [row.id for row in (*page_1, *page_2, *page_3)]
            assert observed_ids == expected_order

            with pytest.raises(ValueError, match="offset"):
                await read_repo.list_paginated(offset=-1, limit=10)
            with pytest.raises(ValueError, match="limit"):
                await read_repo.list_paginated(offset=0, limit=0)
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_service_rolls_back_on_commit_error_and_handles_not_found(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        async with manager.session() as session:
            service = PromptRequestService(session)

            original_rollback = AsyncSession.rollback
            with (
                patch.object(
                    session,
                    "commit",
                    side_effect=SQLAlchemyError("simulated commit failure"),
                ),
                patch.object(
                    AsyncSession,
                    "rollback",
                    autospec=True,
                    side_effect=original_rollback,
                ) as rollback_spy,
                pytest.raises(SQLAlchemyError, match="simulated commit failure"),
            ):
                await service.create_prompt_request(
                    PromptRequestCreate(raw_prompt="Should not remain persisted on failure.")
                )

            assert rollback_spy.await_count == 1

        async with manager.session() as verify_session:
            verify_service = PromptRequestService(verify_session)
            listing = await verify_service.list_prompt_requests(page=1, page_size=20)
            assert listing.total == 0
            assert listing.items == []

            missing_id = uuid4()
            with pytest.raises(PromptRequestNotFoundError) as exc_info:
                await verify_service.get_prompt_request(missing_id)
            assert exc_info.value.prompt_request_id == missing_id

            with pytest.raises(ValueError, match="page"):
                await verify_service.list_prompt_requests(page=0, page_size=10)
            with pytest.raises(ValueError, match="page_size"):
                await verify_service.list_prompt_requests(page=1, page_size=101)
    finally:
        await manager.dispose()

    assert not DEFAULT_SQLITE_DB_PATH.exists()
