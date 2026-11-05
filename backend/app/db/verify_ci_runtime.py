"""Explicit CI preflight; never imported by application startup or a database factory."""

import asyncio
import sqlite3
import sys
from contextlib import closing
from typing import Final

import aiosqlite

from app.db.sqlite_runtime import require_safe_sqlite_wal

EXPECTED_PYTHON: Final = (3, 12, 16)


async def verify_ci_runtime() -> None:
    """Qualify real in-memory connections without opening application databases."""
    version = ".".join(str(part) for part in sys.version_info[:3])
    print(f"Python: {version} ({sys.executable})", flush=True)
    if sys.version_info[:3] != EXPECTED_PYTHON:
        raise RuntimeError("CI requires Python 3.12.16; refusing another interpreter.")

    with closing(sqlite3.connect(":memory:")) as connection:
        row = connection.execute("SELECT sqlite_version()").fetchone()
        sqlite_version = None if row is None else row[0]
    async with (
        aiosqlite.connect(":memory:") as async_connection,
        async_connection.execute("SELECT sqlite_version()") as cursor,
    ):
        async_row = await cursor.fetchone()
        async_version = None if async_row is None else async_row[0]

    # Report both observed libraries before applying either fail-closed check.
    print(f"sqlite3 connected SQLite: {sqlite_version}", flush=True)
    print(f"aiosqlite connected SQLite: {async_version}", flush=True)
    require_safe_sqlite_wal(sqlite_version)
    require_safe_sqlite_wal(async_version)
    print("Python and SQLite WAL runtime qualification passed.", flush=True)


if __name__ == "__main__":
    asyncio.run(verify_ci_runtime())
