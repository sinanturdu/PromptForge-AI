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
from collections.abc import Sequence
from contextlib import closing
from importlib.machinery import (
    EXTENSION_SUFFIXES,
    BuiltinImporter,
    ExtensionFileLoader,
    ModuleSpec,
)
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType
from typing import cast

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import build_sqlite_url
from app.db.verify_ci_runtime import verify_ci_runtime
from app.db.write_transactions import create_write_engine, write_transaction


def sqlite_extension_path(
    module: ModuleType, builtin_names: Sequence[str]
) -> Path | None:
    """Return a verified extension path, or None for a confirmed built-in only."""
    spec = module.__spec__
    location = getattr(module, "__file__", None)
    if (
        module.__name__ != "_sqlite3"
        or not isinstance(spec, ModuleSpec)
        or spec.name != "_sqlite3"
    ):
        raise RuntimeError("Unverified SQLite module import metadata.")
    if location is None:
        if (
            spec.origin == "built-in"
            and cast(object, spec.loader) is BuiltinImporter
            and not spec.has_location
            and "_sqlite3" in builtin_names
        ):
            return None
        raise RuntimeError("SQLite module without a file is not a verified built-in.")
    if (
        not isinstance(location, str)
        or not location
        or not isinstance(spec.origin, str)
        or not spec.has_location
        or not isinstance(spec.loader, ExtensionFileLoader)
        or "_sqlite3" in builtin_names
    ):
        raise RuntimeError("Unverified SQLite extension import metadata.")
    try:
        extension = Path(location).resolve(strict=True)
        origin = Path(spec.origin).resolve(strict=True)
        loader_path = Path(spec.loader.path).resolve(strict=True)
    except (OSError, ValueError) as error:
        raise RuntimeError("Cannot resolve SQLite extension file.") from error
    if (
        not extension.is_file()
        or extension != origin
        or extension != loader_path
        or spec.loader.name != "_sqlite3"
        or not any(extension.name.endswith(suffix) for suffix in EXTENSION_SUFFIXES)
    ):
        raise RuntimeError(
            "Invalid SQLite extension file or conflicting import metadata."
        )
    return extension


async def inspect_sqlite_linkage() -> None:
    extension = sqlite_extension_path(_sqlite3, sys.builtin_module_names)
    if extension is None:
        print(
            "SQLite module: verified Python built-in; no separate extension file "
            "exists for ldd inspection. External library linkage was not inspected.",
            flush=True,
        )
        return
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
    await inspect_sqlite_linkage()

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
