"""Shared pytest fixtures and persistent local database safety guards."""

import hashlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.core.config import DEFAULT_SQLITE_DB_PATH


@dataclass(frozen=True)
class _FileStateSnapshot:
    exists: bool
    size: int | None
    mtime_ns: int | None
    sha256: str | None


def _snapshot_file(path: Path) -> _FileStateSnapshot:
    if not path.exists():
        return _FileStateSnapshot(exists=False, size=None, mtime_ns=None, sha256=None)
    stat_result = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return _FileStateSnapshot(
        exists=True,
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        sha256=digest,
    )


class DefaultDatabaseGuard:
    """Guarantees tests never create, delete, or mutate the local development SQLite database."""

    def __init__(self, db_path: Path = DEFAULT_SQLITE_DB_PATH) -> None:
        self._tracked_paths: tuple[Path, ...] = (
            db_path,
            db_path.with_name(f"{db_path.name}-wal"),
            db_path.with_name(f"{db_path.name}-shm"),
        )
        self._baseline: dict[Path, _FileStateSnapshot] = {
            path: _snapshot_file(path) for path in self._tracked_paths
        }

    def assert_unchanged(self) -> None:
        """Verify the default local database and WAL/SHM sidecars match their pre-test state."""
        for path, before in self._baseline.items():
            after = _snapshot_file(path)
            if not before.exists:
                assert not after.exists, f"Test unexpectedly created persistent DB file: {path}"
            else:
                assert after.exists, f"Test unexpectedly deleted persistent DB file: {path}"
                assert after == before, f"Test unexpectedly modified persistent DB file: {path}"


@pytest.fixture(autouse=True)
def default_db_guard() -> Iterator[Callable[[], None]]:
    """Automatically verify persistent local DB isolation around every backend test."""
    guard = DefaultDatabaseGuard()
    yield guard.assert_unchanged
    guard.assert_unchanged()
