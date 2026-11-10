"""Simulated import modes; real host runtime/DB checks, not Linux qualification."""

import importlib.util
from importlib.abc import Loader
from importlib.machinery import EXTENSION_SUFFIXES, BuiltinImporter, ExtensionFileLoader, ModuleSpec
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db import verify_ci_runtime as preflight


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def helper() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/verify_ci_linux_runtime.py"
    spec = importlib.util.spec_from_file_location("ci_linux_helper", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def builtin() -> ModuleType:
    module = ModuleType("_sqlite3")
    module.__spec__ = ModuleSpec("_sqlite3", cast(Loader, BuiltinImporter), origin="built-in")
    return module


def extension(directory: Path) -> ModuleType:
    # Test-only file and loader metadata: no import or external library is loaded.
    path = directory / ("_sqlite3" + EXTENSION_SUFFIXES[0])
    path.touch()
    module = ModuleType("_sqlite3")
    module.__file__ = str(path)
    module.__spec__ = importlib.util.spec_from_file_location("_sqlite3", path)
    assert module.__spec__ is not None
    assert isinstance(module.__spec__.loader, ExtensionFileLoader)
    return module


def test_verified_builtin_has_no_extension_file(helper: ModuleType) -> None:
    assert helper.sqlite_extension_path(builtin(), ("_sqlite3",)) is None


def test_filebacked_extension_is_resolved(helper: ModuleType, tmp_path: Path) -> None:
    module = extension(tmp_path)
    assert module.__file__ is not None
    assert helper.sqlite_extension_path(module, ()) == Path(module.__file__).resolve()


@pytest.mark.parametrize(
    "defect",
    [
        "no-spec",
        "malformed-spec",
        "origin",
        "loader",
        "location",
        "name",
        "not-listed",
        "module-name",
    ],
)
def test_unverified_builtin_is_rejected(helper: ModuleType, defect: str) -> None:
    module = builtin()
    assert module.__spec__ is not None
    names: tuple[str, ...] = ("_sqlite3",)
    if defect == "no-spec":
        module.__spec__ = None
    elif defect == "malformed-spec":
        module.__spec__ = cast(ModuleSpec, object())
    elif defect == "origin":
        module.__spec__.origin = "unknown"
    elif defect == "loader":
        module.__spec__.loader = None
    elif defect == "location":
        module.__spec__.has_location = True
    elif defect == "name":
        module.__spec__.name = "other"
    elif defect == "module-name":
        module.__name__ = "other"
    else:
        names = ()
    with pytest.raises(RuntimeError, match="SQLite"):
        helper.sqlite_extension_path(module, names)


@pytest.mark.parametrize("defect", ["missing", "origin", "loader", "listed", "empty"])
def test_unverified_extension_is_rejected(helper: ModuleType, defect: str, tmp_path: Path) -> None:
    module = extension(tmp_path)
    assert module.__spec__ is not None
    assert module.__file__ is not None
    if defect == "missing":
        module.__file__ += ".missing"
    elif defect == "origin":
        module.__spec__.origin = "built-in"
    elif defect == "loader":
        module.__spec__.loader = cast(Loader, BuiltinImporter)
    elif defect == "empty":
        module.__file__ = ""
    with pytest.raises(RuntimeError, match="SQLite"):
        helper.sqlite_extension_path(module, ("_sqlite3",) if defect == "listed" else ())


@pytest.mark.anyio
async def test_linkage_failure_is_not_ignored(
    helper: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(helper, "_sqlite3", extension(tmp_path))
    monkeypatch.setattr(helper, "sys", SimpleNamespace(builtin_module_names=()))
    process = SimpleNamespace(returncode=1, communicate=AsyncMock(return_value=(b"", b"error")))
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(helper.asyncio, "create_subprocess_exec", launch)
    with pytest.raises(RuntimeError, match="Cannot inspect Linux SQLite extension linkage"):
        await helper.inspect_sqlite_linkage()
    assert launch.await_args is not None
    location = extension(tmp_path).__file__
    assert location is not None
    assert launch.await_args.args[:2] == ("ldd", str(Path(location).resolve()))


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["builtin", "extension"])
@pytest.mark.parametrize("failure", ["none", "runtime", "probe"])
async def test_modes_retain_real_runtime_and_database_checks(
    helper: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    failure: str,
    tmp_path: Path,
) -> None:
    # Only platform/import metadata and ldd are simulated. Both actual drivers,
    # compatibility policy, writer and capability SQL remain real and unmodified.
    monkeypatch.setattr(helper, "_sqlite3", builtin() if mode == "builtin" else extension(tmp_path))
    monkeypatch.setattr(
        helper,
        "sys",
        SimpleNamespace(
            platform="linux", builtin_module_names=("_sqlite3",) if mode == "builtin" else ()
        ),
    )
    monkeypatch.setattr(helper.platform, "machine", lambda: "x86_64")
    monkeypatch.delenv("LD_LIBRARY_PATH", raising=False)
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    process = SimpleNamespace(
        returncode=0, communicate=AsyncMock(return_value=(b"test linkage", b""))
    )
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(helper.asyncio, "create_subprocess_exec", launch)
    runtime = AsyncMock(wraps=helper.verify_ci_runtime)
    monkeypatch.setattr(helper, "verify_ci_runtime", runtime)
    paths: list[Path] = []
    statements: list[str] = []
    disposed: list[bool] = []
    writer = helper.create_write_engine

    def record_sql(*args: Any) -> None:
        statements.append(str(args[2]))

    def record_writer(url: str) -> AsyncEngine:
        engine: AsyncEngine = writer(url)
        assert engine.url.database is not None
        paths.append(Path(engine.url.database))
        event.listen(engine.sync_engine, "before_cursor_execute", record_sql)
        event.listen(engine.sync_engine, "engine_disposed", lambda _: disposed.append(True))
        if failure == "probe":
            # Deliberately misconfigure this disposable connection to prove the
            # real probe refuses it and still disposes/removes its resources.

            def disable_foreign_keys(connection: Any, record: Any) -> None:
                cursor = connection.cursor()
                try:
                    cursor.execute("PRAGMA foreign_keys=OFF")
                finally:
                    cursor.close()

            event.listen(engine.sync_engine, "connect", disable_foreign_keys)
        return engine

    monkeypatch.setattr(helper, "create_write_engine", record_writer)
    if failure == "runtime":
        monkeypatch.setattr(preflight, "EXPECTED_PYTHON", (0, 0, 0))
        with pytest.raises(RuntimeError, match="CI requires Python"):
            await helper.verify()
        assert not paths and not statements
        launch.assert_not_awaited()
    else:
        if failure == "probe":
            with pytest.raises(RuntimeError, match="Disposable WAL/FULL/foreign-key probe failed"):
                await helper.verify()
        else:
            await helper.verify()
        assert disposed == [True]
        assert len(paths) == 1 and not paths[0].parent.exists()
        assert {"PRAGMA journal_mode", "PRAGMA synchronous", "PRAGMA foreign_keys"} <= set(
            statements
        )
        assert launch.await_count == (0 if mode == "builtin" else 1)
        output = capsys.readouterr().out
        assert ("capability probe passed" in output) == (failure == "none")
        if mode == "builtin":
            assert "External library linkage was not inspected" in output
    runtime.assert_awaited_once()
