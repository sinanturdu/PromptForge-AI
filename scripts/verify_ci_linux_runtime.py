"""CI-only Linux diagnostics plus the unchanged application runtime/WAL gates.

No library substitution or provider composition. The only disk database is an owned,
temporary capability probe; the development database and env files are never opened.
"""

import _sqlite3
import asyncio
import os
import platform
import sqlite3
import struct
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import build_sqlite_url
from app.db.verify_ci_runtime import verify_ci_runtime
from app.db.write_transactions import create_write_engine, write_transaction


async def verify() -> None:
    if (
        sys.platform != "linux"
        or platform.machine() != "x86_64"
        or struct.calcsize("P") != 8
    ):
        raise RuntimeError(
            "This CI gate requires Linux x86_64 and a 64-bit interpreter."
        )
    if os.environ.get("LD_LIBRARY_PATH") or os.environ.get("LD_PRELOAD"):
        raise RuntimeError(
            "CI qualification refuses external library-loading overrides."
        )
    print(
        f"CI platform: {sys.platform}; architecture: {platform.machine()}; pointer bits: 64"
    )
    # Exact Python + both real connected SQLite version policies.
    await verify_ci_runtime()

    with closing(sqlite3.connect(":memory:")) as connection:
        options = {row[0] for row in connection.execute("PRAGMA compile_options")}
    if sqlite3.threadsafety == 0 or options & {
        "OMIT_WAL",
        "OMIT_FOREIGN_KEY",
        "OMIT_TRIGGER",
    }:
        raise RuntimeError(
            "SQLite lacks the thread/WAL/foreign-key capabilities needed by CI."
        )
    print(f"SQLite DB-API threadsafety: {sqlite3.threadsafety}")
    print(
        "SQLite thread compile options:",
        sorted(v for v in options if v.startswith("THREADSAFE=")),
    )
    extension = Path(_sqlite3.__file__).resolve()
    print(f"SQLite extension: {extension}", flush=True)
    linked = await asyncio.create_subprocess_exec(
        "ldd",
        str(extension),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    output, _ = await linked.communicate()
    if linked.returncode != 0:
        raise RuntimeError("Cannot inspect Linux SQLite extension linkage.")
    print("SQLite extension dependencies:\n" + output.decode("utf-8"), flush=True)

    # Exercise the real dedicated writer; never use a replacement driver or fake version.
    with TemporaryDirectory(prefix="promptforge-ci-runtime-") as directory:
        engine = create_write_engine(
            build_sqlite_url(Path(directory) / "probe.sqlite3")
        )
        try:
            async with write_transaction(engine) as session:
                journal = (
                    await session.execute(text("PRAGMA journal_mode"))
                ).scalar_one()
                synchronous = (
                    await session.execute(text("PRAGMA synchronous"))
                ).scalar_one()
                foreign_keys = (
                    await session.execute(text("PRAGMA foreign_keys"))
                ).scalar_one()
                if (
                    str(journal).lower() != "wal"
                    or synchronous != 2
                    or foreign_keys != 1
                ):
                    raise RuntimeError("Disposable WAL/FULL/foreign-key probe failed.")
        finally:
            await engine.dispose()
    print(
        "Linux CI runtime and disposable WAL/FULL/foreign-key capability probe passed."
    )


if __name__ == "__main__":
    asyncio.run(verify())
