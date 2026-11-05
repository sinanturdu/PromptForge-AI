"""Opt-in short write transactions; never used by current API sessions or migrations."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from sqlalchemy import event
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import create_session_factory, is_file_based_sqlite_url, is_sqlite_url
from app.db.sqlite_runtime import require_safe_sqlite_wal

_WRITE_ENGINE = "promptforge_explicit_write_engine"


def create_write_engine(
    database_url: str,
    *,
    sqlite_journal_mode: Literal["WAL", "DELETE"] = "WAL",
    busy_timeout_ms: int = 1_000,
) -> AsyncEngine:
    """Create a dedicated lazy engine, without opening or migrating a database.

    WAL is gated by the actual connected runtime. DELETE is the explicit non-WAL
    transaction-test mode, not an automatic fallback for workflow activation.
    Database parent directories must already exist. Never pass the development URL
    without separate approval to provision its journal mode. No retries are added.
    """
    if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30_000:
        raise ValueError("Write busy_timeout_ms must be an integer between 1 and 30000.")
    if sqlite_journal_mode not in {"WAL", "DELETE"}:
        raise ValueError("Write journal mode must be WAL or DELETE.")
    url = make_url(database_url)
    sqlite = is_sqlite_url(url)
    if sqlite and (url.drivername != "sqlite+aiosqlite" or not is_file_based_sqlite_url(url)):
        raise ValueError("SQLite writes require a file-based sqlite+aiosqlite URL.")
    if not sqlite and url.get_backend_name() != "postgresql":
        raise ValueError("Write transactions support SQLite or PostgreSQL.")

    engine = create_async_engine(
        url,
        connect_args={"timeout": busy_timeout_ms / 1_000} if sqlite else {},
        poolclass=NullPool,
        hide_parameters=True,
        execution_options={_WRITE_ENGINE: True},
    )
    if sqlite:
        _configure_sqlite_writes(engine, sqlite_journal_mode, busy_timeout_ms)
    return engine


def _configure_sqlite_writes(
    engine: AsyncEngine, journal_mode: Literal["WAL", "DELETE"], busy_timeout_ms: int
) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def configure(connection: Any, _record: Any) -> None:
        # SQLAlchemy's supported sqlite3/aiosqlite BEGIN-event recipe. Do not also
        # set driver autocommit=False or SQLAlchemy isolation_level=AUTOCOMMIT.
        connection.isolation_level = None
        cursor = connection.cursor()
        try:
            cursor.execute("SELECT sqlite_version()")
            version = cursor.fetchone()[0]
            if journal_mode == "WAL":
                require_safe_sqlite_wal(version)
            # New connection, no BEGIN yet: journal/synchronous/FK PRAGMAs are legal.
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            cursor.execute(f"PRAGMA journal_mode={journal_mode}")
            if str(cursor.fetchone()[0]).upper() != journal_mode:
                raise RuntimeError("SQLite could not establish the requested write journal mode.")
            cursor.execute("PRAGMA synchronous=FULL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA synchronous")
            synchronous = cursor.fetchone()[0]
            cursor.execute("PRAGMA foreign_keys")
            if synchronous != 2 or cursor.fetchone()[0] != 1:
                raise RuntimeError("SQLite could not establish the required write safety settings.")
        finally:
            cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection: Connection) -> None:
        # Reserve the one SQLite writer before the first read, avoiding a later
        # read-snapshot upgrade. This engine must not serve ordinary API reads.
        connection.exec_driver_sql("BEGIN IMMEDIATE")


@asynccontextmanager
async def write_transaction(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Service-owned atomic unit: eager BEGIN, commit/rollback, then permanent close.

    Repositories only add/execute/flush; do not call commit/rollback in the body.
    Use session.begin_nested() for an intentional SAVEPOINT, not another root UoW.
    Exit this context before any provider/network await. Engine disposal belongs to
    its composition owner, not to a repository or each individual transaction.
    """
    if not engine.get_execution_options().get(_WRITE_ENGINE):
        raise ValueError("write_transaction requires a dedicated create_write_engine engine.")
    factory = create_session_factory(engine)
    # Keep close in a separate outer context: even a failed commit/transaction exit
    # must permanently close the session. SQLAlchemy shields asynchronous close.
    async with factory(autobegin=False, close_resets_only=False) as session, session.begin():
        await session.connection()  # Take the writer reservation before yielding.
        yield session
