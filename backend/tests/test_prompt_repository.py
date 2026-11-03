"""Tests for PromptRequestRepository, PromptRequestService, and schema alignment."""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from alembic import command
from sqlalchemy import String
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import build_sqlite_url
from app.db.migrations import build_alembic_config
from app.db.session import DatabaseSessionManager
from app.models.enums import (
    PromptDetailLevel,
    ReasoningPreference,
    ResponseDetailPreference,
    TargetMode,
)
from app.models.prompt_request import PromptRequest
from app.modules.prompts.repository import PromptRequestRepository, escape_like_term
from app.modules.prompts.schemas import (
    MAX_OUTPUT_LANGUAGE_LENGTH,
    MAX_SEARCH_QUERY_LENGTH,
    MAX_TASK_PROFILE_LENGTH,
    PromptRequestCreate,
)
from app.modules.prompts.service import (
    PromptRequestNotFoundError,
    PromptRequestService,
    normalize_search_query,
)


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


def test_escape_like_term_and_normalize_search_query_unit_rules() -> None:
    assert escape_like_term("React") == "React"
    assert escape_like_term("100% coverage") == r"100\% coverage"
    assert escape_like_term("task_profile_v1") == r"task\_profile\_v1"
    assert escape_like_term(r"C:\Users\sinan\app") == r"C:\\Users\\sinan\\app"
    assert escape_like_term(r"50%_\mix") == r"50\%\_\\mix"

    with pytest.raises(ValueError, match="single character"):
        escape_like_term("test", escape_char="!!")

    assert normalize_search_query(None) is None
    assert normalize_search_query("") is None
    assert normalize_search_query("   \t\n  ") is None
    assert normalize_search_query("  FastAPI  ") == "FastAPI"
    assert normalize_search_query("x" * MAX_SEARCH_QUERY_LENGTH) == "x" * MAX_SEARCH_QUERY_LENGTH

    with pytest.raises(ValueError, match="at most 200"):
        normalize_search_query("x" * (MAX_SEARCH_QUERY_LENGTH + 1))


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
async def test_repository_filtered_count_search_and_literal_wildcards(
    migrated_sqlite_url: str,
) -> None:
    manager = DatabaseSessionManager(migrated_sqlite_url)
    try:
        t1 = datetime(2026, 8, 1, 9, 0, 0, tzinfo=UTC)
        t2 = datetime(2026, 8, 1, 10, 0, 0, tzinfo=UTC)
        t3 = datetime(2026, 8, 1, 11, 0, 0, tzinfo=UTC)
        t4 = datetime(2026, 8, 1, 12, 0, 0, tzinfo=UTC)

        async with manager.session() as session:
            repo = PromptRequestRepository(session)
            await repo.create(
                PromptRequest(
                    raw_prompt="Achieve 100% test reliability on FastAPI endpoints.",
                    user_context=None,
                    target_mode=TargetMode.ANTHROPIC.value,
                    created_at=t1,
                )
            )
            await repo.create(
                PromptRequest(
                    raw_prompt="Achieve 1000 req/s throughput on FastAPI endpoints.",
                    user_context="Schema uses task_profile column.",
                    target_mode=TargetMode.OPENAI.value,
                    created_at=t2,
                )
            )
            await repo.create(
                PromptRequest(
                    raw_prompt="Configure taskXprofile parser.",
                    user_context=r"Workspace root is C:\Projects\PromptForge.",
                    target_mode=TargetMode.ANTHROPIC.value,
                    created_at=t3,
                )
            )
            await repo.create(
                PromptRequest(
                    raw_prompt="Workspace root is C:ProjectsPromptForge without slashes.",
                    user_context=None,
                    target_mode=TargetMode.GOOGLE.value,
                    created_at=t4,
                )
            )
            await session.commit()

        async with manager.session() as read_session:
            read_repo = PromptRequestRepository(read_session)
            assert await read_repo.count_all() == 4

            # Literal % must match only "100%" and never "1000".
            pct_rows = await read_repo.list_paginated(offset=0, limit=10, search_query="100%")
            assert await read_repo.count_filtered(search_query="100%") == 1
            assert len(pct_rows) == 1
            assert "100%" in pct_rows[0].raw_prompt

            # Literal _ must match only "task_profile" in user_context and never "taskXprofile".
            underscore_rows = await read_repo.list_paginated(
                offset=0,
                limit=10,
                search_query="task_profile",
            )
            assert await read_repo.count_filtered(search_query="task_profile") == 1
            assert len(underscore_rows) == 1
            assert underscore_rows[0].user_context == "Schema uses task_profile column."

            # Literal backslash must match only the record containing C:\Projects\PromptForge.
            backslash_rows = await read_repo.list_paginated(
                offset=0,
                limit=10,
                search_query=r"C:\Projects",
            )
            assert await read_repo.count_filtered(search_query=r"C:\Projects") == 1
            assert len(backslash_rows) == 1
            assert backslash_rows[0].target_mode == TargetMode.ANTHROPIC.value

            # Combined keyword + target_mode applies AND between filter dimensions.
            combined_count = await read_repo.count_filtered(
                search_query="fastapi",
                target_mode=TargetMode.ANTHROPIC,
            )
            combined_rows = await read_repo.list_paginated(
                offset=0,
                limit=10,
                search_query="fastapi",
                target_mode=TargetMode.ANTHROPIC,
            )
            assert combined_count == 1
            assert len(combined_rows) == 1
            assert combined_rows[0].target_mode == TargetMode.ANTHROPIC.value
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_service_rolls_back_on_commit_error_and_handles_not_found(
    migrated_sqlite_url: str,
    default_db_guard: Callable[[], None],
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
            with pytest.raises(ValueError, match="at most 200"):
                await verify_service.list_prompt_requests(
                    page=1,
                    page_size=10,
                    q="q" * (MAX_SEARCH_QUERY_LENGTH + 1),
                )
    finally:
        await manager.dispose()

    default_db_guard()
