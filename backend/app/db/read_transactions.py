"""Opt-in SQLite read snapshots; no journal changes, migrations or writer reservation."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.engine import URL, Connection, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from app.db.session import create_session_factory, is_file_based_sqlite_url

_READ_ENGINE = "promptforge_read_snapshot_engine"


def create_read_engine(database_url: str, *, busy_timeout_ms: int = 1_000) -> AsyncEngine:
    """Lazy existing-file, read-only engine; caller owns disposal.

    Only the SQLite snapshot path is qualified here. Reject other dialects rather
    than claiming repeatable reads at their default isolation. URI options are
    assembled here, never accepted from an untrusted caller or copied from a writer.
    """
    url = make_url(database_url)
    if (
        url.drivername != "sqlite+aiosqlite"
        or not is_file_based_sqlite_url(url)
        or url.database is None
        or url.database.startswith("file:")
        or url.query
        or url.host is not None
        or url.username is not None
        or url.password is not None
        or url.port is not None
    ):
        raise ValueError("Read snapshots require a plain file-based sqlite+aiosqlite URL.")
    if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30_000:
        raise ValueError("Read busy_timeout_ms must be an integer between 1 and 30000.")
    readonly = URL.create(
        "sqlite+aiosqlite",
        database=Path(url.database).resolve().as_uri(),
        query={"mode": "ro", "uri": "true"},
    )
    engine = create_async_engine(
        readonly,
        connect_args={"timeout": busy_timeout_ms / 1_000},
        poolclass=NullPool,
        hide_parameters=True,
        execution_options={_READ_ENGINE: True},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def configure(connection: Any, _record: Any) -> None:
        connection.isolation_level = None
        cursor = connection.cursor()
        try:
            cursor.execute("PRAGMA query_only=ON")
        finally:
            cursor.close()

    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection: Connection) -> None:
        # Explicit driver BEGIN is essential: legacy sqlite3 SELECT alone does not
        # pin a multi-statement snapshot. Never use BEGIN IMMEDIATE for this engine.
        connection.exec_driver_sql("BEGIN")

    return engine


@asynccontextmanager
async def read_snapshot(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """One connection/snapshot from first SELECT through rollback and permanent close."""
    if not engine.get_execution_options().get(_READ_ENGINE):
        raise ValueError("read_snapshot requires a dedicated create_read_engine engine.")
    factory = create_session_factory(engine)
    async with factory(autobegin=False, close_resets_only=False) as session, session.begin():
        try:
            await session.connection()
            yield session
        finally:
            await session.rollback()
