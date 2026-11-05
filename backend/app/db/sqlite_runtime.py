"""Capability policy for the documented SQLite WAL-reset defect, not API admission."""

import re
from typing import Literal

SQLiteWALStatus = Literal["fixed", "affected", "unverified"]
_VERSION = re.compile(r"3\.(?:0|[1-9][0-9]{0,2})\.(?:0|[1-9][0-9]{0,2})")


class SQLiteRuntimeSafetyError(RuntimeError):
    """The connected library cannot safely enable concurrent durable WAL writes."""


def sqlite_wal_status(version: object) -> SQLiteWALStatus:
    """Classify SELECT sqlite_version(), including the two documented fixed backports.

    Unknown formats/major versions fail closed. Distributor-specific patches without
    a recognized version need separate review; package/CLI versions are not evidence.
    """
    if not isinstance(version, str) or not _VERSION.fullmatch(version):
        return "unverified"
    parsed = tuple(int(part) for part in version.split("."))
    if parsed in {(3, 44, 6), (3, 50, 7)} or parsed >= (3, 51, 3):
        return "fixed"
    if parsed >= (3, 7, 0):
        return "affected"
    return "unverified"


def require_safe_sqlite_wal(version: object) -> None:
    """Refuse unverified WAL capability with a fixed, secret-free diagnostic."""
    if sqlite_wal_status(version) != "fixed":
        raise SQLiteRuntimeSafetyError(
            "Durable concurrent SQLite WAL writes require SQLite 3.51.3+ or a verified "
            "fixed backport (3.44.6 or 3.50.7). Verify SELECT sqlite_version() through "
            "the Python database connection after an approved runtime upgrade. "
            "Existing PromptRequest APIs do not require this capability."
        )
