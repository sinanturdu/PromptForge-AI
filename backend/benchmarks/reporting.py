"""Shared fixed-location report writing; no arbitrary CLI output destinations."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal

REPORT_DIRECTORY = Path(__file__).resolve().parents[1] / "benchmark-reports"
ReportName = Literal["prompt-architect-offline.json", "prompt-critic-offline.json"]


def write_report(directory: Path, name: ReportName, content: str) -> None:
    """Write atomically, rejecting redirects and never overwriting a hardlink target."""
    if name not in {"prompt-architect-offline.json", "prompt-critic-offline.json"}:
        raise ValueError("Benchmark report name is unsupported.")
    if directory.resolve() != directory.absolute() or directory.is_symlink():
        raise ValueError("Benchmark report directory must not be redirected.")
    destination = directory / name
    if destination.is_symlink():
        raise ValueError("Benchmark report file must not be redirected.")
    directory.mkdir(exist_ok=True)
    temporary = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=directory,
            prefix=".benchmark-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(content + "\n")
        os.replace(temporary, destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
