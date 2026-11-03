"""Tests for Milestone 3A asynchronous SQLite & SQLAlchemy database foundation."""

from pathlib import Path
from typing import Annotated
from unittest.mock import patch

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_settings import SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import (
    BACKEND_DIR,
    DEFAULT_SQLITE_DB_PATH,
    Settings,
    build_sqlite_url,
)
from app.db.session import (
    DatabaseConnectionError,
    DatabaseSessionManager,
    get_db_session,
    is_file_based_sqlite_url,
    is_in_memory_sqlite_url,
    is_sqlite_url,
)
from app.main import create_app


class IsolatedSettings(Settings):
    """Test settings subclass that ignores any developer-local `.env` file."""

    model_config = SettingsConfigDict(
        env_file=None,
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )


@pytest.fixture
def anyio_backend() -> str:
    """Run async database tests exclusively on the asyncio backend."""
    return "asyncio"


def test_default_database_url_is_valid_and_cwd_independent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    settings = IsolatedSettings()
    parsed = make_url(settings.database_url)

    assert parsed.drivername == "sqlite+aiosqlite"
    assert parsed.database is not None
    assert Path(parsed.database) == DEFAULT_SQLITE_DB_PATH
    assert Path(parsed.database).parent == BACKEND_DIR / "data"
    assert settings.app_name == "PromptForge AI"
    assert settings.app_version == "0.0.1"
    assert settings.api_v1_prefix == "/api/v1"


def test_database_url_supports_env_overrides_and_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pg_url = "postgresql+asyncpg://postgres:secret@localhost:5432/promptforge"
    monkeypatch.setenv("DATABASE_URL", pg_url)
    pg_settings = IsolatedSettings()
    assert pg_settings.database_url == pg_url
    assert not is_sqlite_url(make_url(pg_settings.database_url))

    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    mem_settings = IsolatedSettings()
    assert is_in_memory_sqlite_url(make_url(mem_settings.database_url))
    assert not is_file_based_sqlite_url(make_url(mem_settings.database_url))

    monkeypatch.delenv("DATABASE_URL", raising=False)
    for invalid_url in (
        "",
        "   ",
        "://not-a-valid-url",
        "sqlite:///sync_driver.db",
        "postgresql://user:pass@localhost:5432/sync_pg",
    ):
        with pytest.raises(ValidationError):
            IsolatedSettings(database_url=invalid_url)


@pytest.mark.anyio
async def test_async_engine_connects_lazily_to_sqlite_and_enables_wal_for_files(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "nested_data" / "test_connect.db"
    db_url = build_sqlite_url(db_file)

    manager = DatabaseSessionManager(db_url)
    try:
        assert not db_file.exists()

        await manager.verify_connection()
        assert db_file.exists()

        async with manager.engine.connect() as connection:
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            assert str(journal_mode).lower() == "wal"
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_in_memory_sqlite_does_not_apply_wal_mode() -> None:
    manager = DatabaseSessionManager("sqlite+aiosqlite:///:memory:")
    try:
        async with manager.engine.connect() as connection:
            journal_mode = (await connection.execute(text("PRAGMA journal_mode"))).scalar_one()
            foreign_keys = (await connection.execute(text("PRAGMA foreign_keys"))).scalar_one()

            assert str(journal_mode).lower() == "memory"
            assert foreign_keys == 1
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_connection_failure_raises_database_connection_error(
    tmp_path: Path,
) -> None:
    directory_as_db_path = tmp_path / "existing_directory"
    directory_as_db_path.mkdir()
    invalid_sqlite_url = build_sqlite_url(directory_as_db_path)

    manager = DatabaseSessionManager(invalid_sqlite_url, connect_timeout=1.0)
    try:
        with pytest.raises(DatabaseConnectionError):
            await manager.verify_connection()
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_async_session_executes_select_one_and_closes_cleanly(
    tmp_path: Path,
) -> None:
    db_url = build_sqlite_url(tmp_path / "session_select.db")
    manager = DatabaseSessionManager(db_url)
    try:
        captured_session: AsyncSession | None = None
        original_close = AsyncSession.close
        with patch.object(
            AsyncSession,
            "close",
            autospec=True,
            side_effect=original_close,
        ) as close_spy:
            async with manager.session() as session:
                captured_session = session
                result = await session.execute(text("SELECT 1"))
                assert result.scalar_one() == 1
                assert session.in_transaction()

            assert close_spy.await_count == 1

        assert captured_session is not None
        assert not captured_session.in_transaction()
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_session_rolls_back_and_closes_when_exception_occurs(
    tmp_path: Path,
) -> None:
    db_url = build_sqlite_url(tmp_path / "rollback.db")
    manager = DatabaseSessionManager(db_url)
    try:
        async with manager.session() as setup_session:
            await setup_session.execute(
                text("CREATE TABLE test_items (id INTEGER PRIMARY KEY, label TEXT NOT NULL)")
            )
            await setup_session.commit()

        failed_session: AsyncSession | None = None
        with pytest.raises(RuntimeError, match="simulated failure"):
            async with manager.session() as session:
                failed_session = session
                await session.execute(
                    text("INSERT INTO test_items (label) VALUES ('should_rollback')")
                )
                assert session.in_transaction()
                raise RuntimeError("simulated failure")

        assert failed_session is not None
        assert not failed_session.in_transaction()

        async with manager.session() as check_session:
            row_count = (
                await check_session.execute(text("SELECT COUNT(*) FROM test_items"))
            ).scalar_one()
            assert row_count == 0
    finally:
        await manager.dispose()


@pytest.mark.anyio
async def test_sqlite_foreign_key_enforcement_is_enabled(tmp_path: Path) -> None:
    db_url = build_sqlite_url(tmp_path / "foreign_keys.db")
    manager = DatabaseSessionManager(db_url)
    try:
        async with manager.session() as session:
            pragma_value = (await session.execute(text("PRAGMA foreign_keys"))).scalar_one()
            assert pragma_value == 1

            await session.execute(text("CREATE TABLE parent_nodes (id INTEGER PRIMARY KEY)"))
            await session.execute(
                text(
                    "CREATE TABLE child_nodes ("
                    "id INTEGER PRIMARY KEY, "
                    "parent_id INTEGER NOT NULL REFERENCES parent_nodes(id)"
                    ")"
                )
            )
            await session.commit()

        with pytest.raises(IntegrityError):
            async with manager.session() as session:
                await session.execute(text("INSERT INTO child_nodes (parent_id) VALUES (999)"))
                await session.commit()
    finally:
        await manager.dispose()


def test_fastapi_lifespan_session_dependency_and_health_independence(
    tmp_path: Path,
) -> None:
    db_file = tmp_path / "fastapi_lifespan.db"
    settings = IsolatedSettings(database_url=build_sqlite_url(db_file))
    application = create_app(settings=settings)

    @application.get("/_test/db-ping")
    async def db_ping(
        session: Annotated[AsyncSession, Depends(get_db_session)],
    ) -> dict[str, int]:
        value = (await session.execute(text("SELECT 1"))).scalar_one()
        return {"result": int(value)}

    original_dispose = DatabaseSessionManager.dispose
    with (
        patch.object(
            DatabaseSessionManager,
            "dispose",
            autospec=True,
            side_effect=original_dispose,
        ) as dispose_spy,
        TestClient(application) as client,
    ):
        assert not db_file.exists()

        health_response = client.get("/api/v1/health")
        assert health_response.status_code == 200
        assert health_response.json() == {"status": "ok", "service": "PromptForge AI"}
        assert not db_file.exists()

        ping_response = client.get("/_test/db-ping")
        assert ping_response.status_code == 200
        assert ping_response.json() == {"result": 1}
        assert db_file.exists()

    assert dispose_spy.await_count == 1
