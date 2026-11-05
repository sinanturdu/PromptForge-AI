"""Offline CI preflight checks using actual, unmodified SQLite drivers."""

import subprocess
import sys
from pathlib import Path

import pytest

from app.db import verify_ci_runtime as preflight
from app.db.sqlite_runtime import require_safe_sqlite_wal


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_both_connected_versions_are_checked_and_reported(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    checked: list[object] = []

    def record(version: object) -> None:
        require_safe_sqlite_wal(version)
        checked.append(version)

    monkeypatch.setattr(preflight, "require_safe_sqlite_wal", record)
    await preflight.verify_ci_runtime()
    output = capsys.readouterr().out
    assert len(checked) == 2
    assert f"sqlite3 connected SQLite: {checked[0]}" in output
    assert f"aiosqlite connected SQLite: {checked[1]}" in output
    assert "Python: 3.12.16" in output and sys.executable in output
    assert "qualification passed" in output


@pytest.mark.anyio
@pytest.mark.parametrize("version", [(3, 12, 15), (3, 13, 0)])
async def test_wrong_python_version_fails_explicitly(
    monkeypatch: pytest.MonkeyPatch, version: tuple[int, int, int]
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(sys, "version_info", version)
        with pytest.raises(RuntimeError, match=r"CI requires Python 3\.12\.16"):
            await preflight.verify_ci_runtime()


def test_module_entry_point_qualifies_the_current_environment() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "app.db.verify_ci_runtime"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "qualification passed" in result.stdout
