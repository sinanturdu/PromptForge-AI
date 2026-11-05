"""Real isolated DELETE-journal transactions; never stress affected SQLite WAL builds."""

import asyncio
import sqlite3
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import aiosqlite
import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError, InvalidRequestError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import build_sqlite_url
from app.db.base import Base
from app.db.session import DatabaseSessionManager
from app.db.sqlite_runtime import (
    SQLiteRuntimeSafetyError,
    require_safe_sqlite_wal,
    sqlite_wal_status,
)
from app.db.write_transactions import create_write_engine, write_transaction
from app.models import PromptRequest
from app.modules.prompts.repository import PromptRequestRepository


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def database(tmp_path: Path) -> Path:
    path = tmp_path / "transactions.db"
    # Default SQLite DELETE mode, no application WAL hook or migrations involved.
    engine = create_engine("sqlite:///" + path.as_posix())
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        connection.executescript(
            "CREATE TABLE items (id INTEGER PRIMARY KEY, label TEXT NOT NULL);"
            "INSERT INTO items VALUES (1, 'original');"
            "CREATE TABLE deferred_children (id INTEGER PRIMARY KEY, parent_id INTEGER "
            "REFERENCES items(id) DEFERRABLE INITIALLY DEFERRED);"
        )
    return path


@pytest.fixture
async def engine(database: Path) -> AsyncIterator[AsyncEngine]:
    writer = create_write_engine(build_sqlite_url(database), sqlite_journal_mode="DELETE")
    try:
        yield writer
    finally:
        await writer.dispose()


def rows(path: Path) -> list[tuple[int, str]]:
    with sqlite3.connect(path) as connection:
        return connection.execute("SELECT id, label FROM items ORDER BY id").fetchall()


def capture_connections(engine: AsyncEngine) -> list[aiosqlite.Connection]:
    connections: list[aiosqlite.Connection] = []

    @event.listens_for(engine.sync_engine, "connect", insert=True)
    def connected(connection: Any, _record: Any) -> None:
        connections.append(connection.driver_connection)

    return connections


async def assert_closed(connections: list[aiosqlite.Connection]) -> None:
    assert connections
    for connection in connections:
        with pytest.raises(ValueError, match="no active connection"):
            await connection.execute("SELECT 1")


def trace_begin(engine: AsyncEngine, started: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    async def install(connection: aiosqlite.Connection) -> None:
        def trace(statement: str) -> None:
            if statement == "BEGIN IMMEDIATE":
                loop.call_soon_threadsafe(started.set)

        await connection.set_trace_callback(trace)

    @event.listens_for(engine.sync_engine, "connect")
    def connected(connection: Any, _record: Any) -> None:
        connection.run_async(install)


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("3.51.3", "fixed"),
        ("3.51.4", "fixed"),
        ("3.52.0", "fixed"),
        ("3.53.4", "fixed"),
        ("3.44.6", "fixed"),
        ("3.50.7", "fixed"),
        ("3.44.5", "affected"),
        ("3.45.3", "affected"),
        ("3.50.4", "affected"),
        ("3.50.6", "affected"),
        ("3.51.2", "affected"),
        ("3.7.0", "affected"),
        ("3.6.0", "unverified"),
        ("4.0.0", "unverified"),
        ("3.51.3-custom", "unverified"),
        ("3.050.7", "unverified"),
        ("3.50.007", "unverified"),
        ("3.51.3\n", "unverified"),
        (None, "unverified"),
        ((3, 51, 3), "unverified"),
        ("PRIVATE_SECRET", "unverified"),
    ],
)
def test_runtime_policy_is_backport_aware_and_fail_closed(version: object, expected: str) -> None:
    assert sqlite_wal_status(version) == expected
    if expected == "fixed":
        require_safe_sqlite_wal(version)
    else:
        with pytest.raises(SQLiteRuntimeSafetyError, match=r"3\.51\.3") as error:
            require_safe_sqlite_wal(version)
        assert "PRIVATE_SECRET" not in str(error.value)


@pytest.mark.parametrize("timeout", [0, -1, 30_001, True, 1.5])
def test_timeout_is_bounded(timeout: Any, database: Path) -> None:
    with pytest.raises(ValueError, match="busy_timeout_ms"):
        create_write_engine(build_sqlite_url(database), busy_timeout_ms=timeout)


@pytest.mark.parametrize("url", ["sqlite+aiosqlite:///:memory:", "sqlite:///file.db"])
def test_sqlite_write_engine_rejects_unsupported_urls(url: str) -> None:
    with pytest.raises(ValueError, match="file-based sqlite"):
        create_write_engine(url)


@pytest.mark.anyio
async def test_opt_in_engine_is_lazy_and_checks_connected_version_before_wal(
    database: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.db.write_transactions as writes

    with sqlite3.connect(database) as connection:
        actual_version = connection.execute("SELECT sqlite_version()").fetchone()[0]
    before = database.read_bytes()
    observed: list[object] = []

    def verify(version: object) -> None:
        observed.append(version)
        # A deterministic refusal tests that no journal change precedes admission,
        # even when this test is later run on an actually fixed runtime.
        require_safe_sqlite_wal("3.45.3")

    monkeypatch.setattr(writes, "require_safe_sqlite_wal", verify)
    monkeypatch.setattr(sqlite3, "sqlite_version", "3.51.3")
    writer = create_write_engine(build_sqlite_url(database))
    connections = capture_connections(writer)
    try:
        assert observed == [] and database.read_bytes() == before
        with pytest.raises(SQLiteRuntimeSafetyError):
            async with write_transaction(writer):
                pytest.fail("Rejected WAL capability must not yield a session")
        assert observed == [actual_version]
        assert database.read_bytes() == before
        assert not database.with_name(database.name + "-wal").exists()
        await assert_closed(connections)
    finally:
        await writer.dispose()


@pytest.mark.anyio
async def test_actual_runtime_wal_capability_without_concurrent_writes(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        version = connection.execute("SELECT sqlite_version()").fetchone()[0]
    writer = create_write_engine(build_sqlite_url(database))
    try:
        if sqlite_wal_status(version) != "fixed":
            with pytest.raises(SQLiteRuntimeSafetyError):
                async with write_transaction(writer):
                    pytest.fail("Affected or unknown SQLite must not enter WAL writes")
            with sqlite3.connect(database) as connection:
                assert connection.execute("PRAGMA journal_mode").fetchone() == ("delete",)
        else:
            async with write_transaction(writer) as session:
                assert (await session.scalar(text("PRAGMA journal_mode"))) == "wal"
                assert (await session.scalar(text("PRAGMA synchronous"))) == 2
                assert (await session.scalar(text("PRAGMA foreign_keys"))) == 1
    finally:
        await writer.dispose()


@pytest.mark.anyio
async def test_explicit_begin_settings_orm_commit_and_permanent_close(
    engine: AsyncEngine, database: Path
) -> None:
    connections = capture_connections(engine)
    statements: list[str] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def statement(_conn: Any, _cursor: Any, sql: str, *_args: Any) -> None:
        statements.append(sql)

    async with write_transaction(engine) as session:
        assert statements == ["BEGIN IMMEDIATE"]  # Reserved before any repository/read.
        assert session.in_transaction() and not session.sync_session.autobegin
        assert connections[0].in_transaction  # Physical SQLite transaction, not just ORM state.
        assert connections[0].isolation_level is None
        assert (await session.scalar(text("PRAGMA journal_mode"))) == "delete"
        assert (await session.scalar(text("PRAGMA synchronous"))) == 2
        assert (await session.scalar(text("PRAGMA foreign_keys"))) == 1
        assert (await session.scalar(text("PRAGMA busy_timeout"))) == 1_000
        assert await session.scalar(text("SELECT sqlite_version()"))
        repository = PromptRequestRepository(session)
        await repository.create(PromptRequest(raw_prompt="First offline request"))
        await repository.create(PromptRequest(raw_prompt="Second offline request"))
        # A flush is not a commit: an independent connection sees neither row.
        with sqlite3.connect(database) as observer:
            assert observer.execute("SELECT count(*) FROM prompt_requests").fetchone() == (0,)
    assert not session.in_transaction()
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        await session.begin()
    await assert_closed(connections)
    with sqlite3.connect(database) as observer:
        assert observer.execute("SELECT count(*) FROM prompt_requests").fetchone() == (2,)


@pytest.mark.anyio
async def test_exception_rolls_back_all_statements_and_orm_flushes(
    engine: AsyncEngine, database: Path
) -> None:
    connections = capture_connections(engine)
    with pytest.raises(RuntimeError, match="Injected failure"):
        async with write_transaction(engine) as session:
            await session.execute(text("UPDATE items SET label='changed' WHERE id=1"))
            await PromptRequestRepository(session).create(PromptRequest(raw_prompt="Uncommitted"))
            await session.execute(text("INSERT INTO items VALUES (2, 'not committed')"))
            raise RuntimeError("Injected failure")
    assert rows(database) == [(1, "original")]
    with sqlite3.connect(database) as observer:
        assert observer.execute("SELECT count(*) FROM prompt_requests").fetchone() == (0,)
    await assert_closed(connections)


@pytest.mark.anyio
async def test_deferred_fk_commit_failure_rolls_back_everything(
    engine: AsyncEngine, database: Path
) -> None:
    connections = capture_connections(engine)
    with pytest.raises(IntegrityError):
        async with write_transaction(engine) as session:
            await session.execute(text("UPDATE items SET label='changed' WHERE id=1"))
            await session.execute(text("INSERT INTO deferred_children VALUES (1, 999)"))
            assert (await session.scalar(text("SELECT count(*) FROM deferred_children"))) == 1
    assert rows(database) == [(1, "original")]
    assert not session.in_transaction()
    with pytest.raises(InvalidRequestError, match="permanently closed"):
        await session.begin()
    await assert_closed(connections)


@pytest.mark.anyio
async def test_busy_commit_leaves_no_partial_writes(database: Path) -> None:
    writer = create_write_engine(
        build_sqlite_url(database), sqlite_journal_mode="DELETE", busy_timeout_ms=80
    )
    connections = capture_connections(writer)
    reader = sqlite3.connect(database)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM items").fetchall()  # Hold a real shared read lock.
        with pytest.raises(OperationalError) as error:
            async with write_transaction(writer) as session:
                await session.execute(text("UPDATE items SET label='changed' WHERE id=1"))
                await session.execute(text("INSERT INTO items VALUES (2, 'partial')"))
        assert getattr(error.value.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
        reader.rollback()
        assert rows(database) == [(1, "original")]
        with pytest.raises(InvalidRequestError, match="permanently closed"):
            await session.begin()
        await assert_closed(connections)
    finally:
        reader.close()
        await writer.dispose()


@pytest.mark.anyio
async def test_competing_writers_read_after_reservation_and_commit_in_order(
    engine: AsyncEngine, database: Path
) -> None:
    second = create_write_engine(build_sqlite_url(database), sqlite_journal_mode="DELETE")
    started = asyncio.Event()
    trace_begin(second, started)
    connections = capture_connections(second)

    async def contender() -> None:
        async with write_transaction(second) as session:
            assert (await session.scalar(text("SELECT label FROM items WHERE id=1"))) == "first"
            await session.execute(text("UPDATE items SET label='second' WHERE id=1"))

    try:
        async with write_transaction(engine) as session:
            await session.execute(text("UPDATE items SET label='first' WHERE id=1"))
            task = asyncio.create_task(contender())
            await asyncio.wait_for(started.wait(), timeout=5)
            assert not task.done()
        await asyncio.wait_for(task, timeout=5)
        assert rows(database) == [(1, "second")]
        await assert_closed(connections)
    finally:
        await second.dispose()


@pytest.mark.anyio
async def test_busy_writer_timeout_is_bounded_and_connection_closes(
    engine: AsyncEngine, database: Path
) -> None:
    second = create_write_engine(
        build_sqlite_url(database), sqlite_journal_mode="DELETE", busy_timeout_ms=80
    )
    connections = capture_connections(second)

    async def contend() -> None:
        async with write_transaction(second):
            pytest.fail("Second writer must not acquire a held reservation")

    try:
        async with write_transaction(engine):
            with pytest.raises(OperationalError) as error:
                await asyncio.wait_for(contend(), timeout=5)
            assert getattr(error.value.orig, "sqlite_errorcode", None) == sqlite3.SQLITE_BUSY
        await assert_closed(connections)
        assert rows(database) == [(1, "original")]
    finally:
        await second.dispose()


@pytest.mark.anyio
async def test_cancel_waiting_writer_closes_before_cancellation_returns(
    engine: AsyncEngine, database: Path
) -> None:
    second = create_write_engine(
        build_sqlite_url(database), sqlite_journal_mode="DELETE", busy_timeout_ms=100
    )
    connections = capture_connections(second)
    started = asyncio.Event()
    trace_begin(second, started)

    async def contend() -> None:
        async with write_transaction(second):
            pytest.fail("Cancelled waiting writer must not enter the body")

    try:
        async with write_transaction(engine):
            task = asyncio.create_task(contend())
            await asyncio.wait_for(started.wait(), timeout=5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, timeout=5)
            await assert_closed(connections)
        async with write_transaction(second) as session:
            assert (await session.scalar(text("SELECT label FROM items WHERE id=1"))) == "original"
    finally:
        await second.dispose()


@pytest.mark.anyio
async def test_cancel_body_rolls_back_and_closes(engine: AsyncEngine, database: Path) -> None:
    connections = capture_connections(engine)
    staged = asyncio.Event()

    async def cancelled_operation() -> None:
        async with write_transaction(engine) as session:
            await session.execute(text("UPDATE items SET label='cancelled' WHERE id=1"))
            staged.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(cancelled_operation())
    await asyncio.wait_for(staged.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)
    assert rows(database) == [(1, "original")]
    await assert_closed(connections)


@pytest.mark.anyio
async def test_released_savepoint_is_still_rolled_back_by_outer_transaction(
    engine: AsyncEngine, database: Path
) -> None:
    with pytest.raises(RuntimeError, match="outer failure"):
        async with write_transaction(engine) as session:
            async with session.begin_nested():
                await session.execute(text("UPDATE items SET label='savepoint' WHERE id=1"))
            raise RuntimeError("outer failure")
    assert rows(database) == [(1, "original")]


@pytest.mark.anyio
async def test_transactional_ddl_is_rolled_back(engine: AsyncEngine, database: Path) -> None:
    with pytest.raises(RuntimeError, match="DDL failure"):
        async with write_transaction(engine) as session:
            await session.execute(text("CREATE TABLE transient_ddl (id INTEGER PRIMARY KEY)"))
            raise RuntimeError("DDL failure")
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute(
                "SELECT name FROM sqlite_master WHERE name='transient_ddl'"
            ).fetchall()
            == []
        )


@pytest.mark.anyio
async def test_nested_root_begin_is_rejected_and_savepoint_failure_is_local(
    engine: AsyncEngine, database: Path
) -> None:
    async with write_transaction(engine) as session:
        with pytest.raises(InvalidRequestError, match="already begun"):
            await session.begin()
        await session.execute(text("UPDATE items SET label='outer' WHERE id=1"))
        with pytest.raises(RuntimeError, match="inner failure"):
            async with session.begin_nested():
                await session.execute(text("UPDATE items SET label='inner' WHERE id=1"))
                raise RuntimeError("inner failure")
    assert rows(database) == [(1, "outer")]


@pytest.mark.anyio
async def test_pragma_settings_cannot_be_changed_inside_write_transaction(
    engine: AsyncEngine,
) -> None:
    with pytest.raises(OperationalError, match="Safety level may not be changed"):
        async with write_transaction(engine) as session:
            await session.execute(text("PRAGMA synchronous=NORMAL"))
    async with write_transaction(engine) as session:
        await session.execute(text("PRAGMA foreign_keys=OFF"))  # SQLite ignores this inside BEGIN.
        assert (await session.scalar(text("PRAGMA foreign_keys"))) == 1


@pytest.mark.anyio
async def test_existing_api_sessions_keep_legacy_read_and_no_auto_commit(database: Path) -> None:
    manager = DatabaseSessionManager(build_sqlite_url(database))
    statements: list[str] = []

    @event.listens_for(manager.engine.sync_engine, "before_cursor_execute")
    def statement(_conn: Any, _cursor: Any, sql: str, *_args: Any) -> None:
        statements.append(sql)

    try:
        async with manager.session() as session:
            assert session.sync_session.autobegin
            assert (await session.scalar(text("SELECT 1"))) == 1
            assert "BEGIN IMMEDIATE" not in statements
            assert (await session.scalar(text("PRAGMA synchronous"))) == 1
            # Sequential legacy WAL use only; no concurrent WAL workload here.
            await session.execute(text("UPDATE items SET label='not committed' WHERE id=1"))
        assert rows(database) == [(1, "original")]
        with pytest.raises(ValueError, match="dedicated"):
            async with write_transaction(manager.engine):
                pytest.fail("Legacy engines must not be used by the new boundary")
    finally:
        await manager.dispose()
