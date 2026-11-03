"""SQLAlchemy 2.0 asynchronous engine and session lifecycle management."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class DatabaseConnectionError(RuntimeError):
    """Raised when establishing or verifying a database connection fails."""


def is_sqlite_url(url: URL) -> bool:
    """Return True when the SQLAlchemy URL targets the SQLite dialect."""
    return url.get_backend_name() == "sqlite"


def is_in_memory_sqlite_url(url: URL) -> bool:
    """Return True when the SQLite URL points to a transient in-memory database."""
    if not is_sqlite_url(url):
        return False

    database = url.database
    if not database or database == ":memory:":
        return True

    if url.query.get("mode") == "memory":
        return True

    return database.startswith("file:") and (
        "mode=memory" in database or database.startswith("file::memory:")
    )


def is_file_based_sqlite_url(url: URL) -> bool:
    """Return True when the SQLite URL points to a persistent file on disk."""
    return is_sqlite_url(url) and not is_in_memory_sqlite_url(url)


def _configure_sqlite_engine(
    engine: AsyncEngine,
    url: URL,
    *,
    connect_timeout: float,
) -> None:
    """Attach SQLite-only connection hooks without affecting other database dialects."""
    file_based = is_file_based_sqlite_url(url)
    busy_timeout_ms = max(1, int(connect_timeout * 1000))
    database_path = url.database

    if file_based and database_path and not database_path.startswith("file:"):
        target_file = Path(database_path)

        @event.listens_for(engine.sync_engine, "do_connect")
        def _ensure_sqlite_parent_directory(
            _dialect: Any,
            _conn_rec: Any,
            _cargs: Any,
            _cparams: Any,
        ) -> None:
            target_file.parent.mkdir(parents=True, exist_ok=True)

    @event.listens_for(engine.sync_engine, "connect")
    def _apply_sqlite_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
            if file_based:
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
        finally:
            cursor.close()


def create_db_engine(
    database_url: str,
    *,
    connect_timeout: float = 30.0,
    echo: bool = False,
) -> AsyncEngine:
    """Create a lazy SQLAlchemy AsyncEngine without opening an immediate connection."""
    parsed_url = make_url(database_url)
    connect_args: dict[str, Any] = {}

    if is_sqlite_url(parsed_url):
        connect_args["timeout"] = connect_timeout

    engine = create_async_engine(
        database_url,
        echo=echo,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if is_sqlite_url(parsed_url):
        _configure_sqlite_engine(engine, parsed_url, connect_timeout=connect_timeout)

    return engine


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create an async session factory bound to the given AsyncEngine."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )


async def verify_database_connection(engine: AsyncEngine) -> None:
    """Verify database reachability by executing a minimal `SELECT 1` probe."""
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            if result.scalar_one() != 1:
                raise DatabaseConnectionError("Unexpected response from database probe query.")
    except DatabaseConnectionError:
        raise
    except (SQLAlchemyError, OSError) as exc:
        raise DatabaseConnectionError("Failed to establish a database connection.") from exc


class DatabaseSessionManager:
    """Owns an AsyncEngine and async_sessionmaker for an application or test scope."""

    def __init__(
        self,
        database_url: str,
        *,
        connect_timeout: float = 30.0,
        echo: bool = False,
    ) -> None:
        self._engine = create_db_engine(
            database_url,
            connect_timeout=connect_timeout,
            echo=echo,
        )
        self._session_factory = create_session_factory(self._engine)

    @property
    def engine(self) -> AsyncEngine:
        """Return the underlying SQLAlchemy AsyncEngine."""
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]:
        """Return the configured async_sessionmaker."""
        return self._session_factory

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Provide a managed AsyncSession that rolls back on error and always closes."""
        session = self._session_factory()
        try:
            yield session
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()

    async def verify_connection(self) -> None:
        """Verify that the managed engine can execute a probe query."""
        await verify_database_connection(self._engine)

    async def dispose(self) -> None:
        """Dispose pooled connections held by the underlying AsyncEngine."""
        await self._engine.dispose()


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped AsyncSession."""
    db_manager: DatabaseSessionManager | None = getattr(request.app.state, "db_manager", None)
    if db_manager is None:
        raise DatabaseConnectionError(
            "DatabaseSessionManager is not initialized on application state."
        )

    async with db_manager.session() as session:
        yield session

