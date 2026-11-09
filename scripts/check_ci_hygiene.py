"""Read-only repository hygiene checks; no application imports or network access.

Checks tracked/unignored new filenames and current operator documentation/examples.
This is a small preventive gate, not an exhaustive secret or Git-history scanner.
"""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENERATED_PARTS = {
    ".venv",
    "node_modules",
    "dist",
    "playwright-report",
    "test-results",
    "blob-report",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "tmp",
    "temp",
    "logs",
    "coverage",
    "htmlcov",
    "build",
    "venv",
    "dist-ssr",
    ".vite",
    ".playwright",
    "secrets",
}
PRIVATE_SUFFIX = re.compile(
    r"\.(?:db|sqlite|sqlite3)(?:-(?:wal|shm|journal))?$", re.IGNORECASE
)
SECRET_FILE = re.compile(r"(?:credentials.*|service-account.*)\.json$", re.IGNORECASE)
PERSONAL_PATH = re.compile(
    r"[A-Z]:[\\/]Users[\\/]|/(?:home|Users)/[^/\s]+/", re.IGNORECASE
)
SECRET_VALUE = re.compile(
    r"sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"
)
PRIVATE_SNAPSHOT = re.compile(
    r"LastWriteTimeUtc\s+(?:ticks\s+)?`?\d{12,}|"
    r"Development\s+(?:DB|database)\s+observation:\s*\d+\s+bytes",
    re.IGNORECASE,
)


def filename_problem(name: str) -> bool:
    path = Path(name)
    return (
        bool(set(path.parts) & GENERATED_PARTS)
        or name.startswith("backend/data/")
        or (path.name.startswith(".env") and path.name != ".env.example")
        or path.name.endswith(".env")
        or bool(PRIVATE_SUFFIX.search(path.name))
        or bool(SECRET_FILE.fullmatch(path.name))
        or path.suffix.lower() in {".pem", ".key", ".p12", ".pfx", ".log"}
    )


def is_operator_document(name: str) -> bool:
    return (
        name
        in {
            "README.md",
            "backend/README.md",
            "frontend/README.md",
            "docs/ci-qualification.md",
        }
        or (name.startswith("docs/local-mvp-") and name.endswith(".md"))
        or name.endswith("/.env.example")
    )


def main() -> int:
    names = (
        subprocess.check_output(
            ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode("utf-8")
        .split("\0")
    )
    errors = []
    documents = 0
    for name in sorted(set(filter(None, names))):
        if filename_problem(name):
            errors.append(
                f"{name!r}: private/generated file must not be version-controlled"
            )
        if not is_operator_document(name):
            continue
        documents += 1
        content = (ROOT / name).read_text(encoding="utf-8")
        for pattern, category in (
            (PERSONAL_PATH, "absolute personal path"),
            (SECRET_VALUE, "credential/token marker"),
            (PRIVATE_SNAPSHOT, "literal private database snapshot metadata"),
        ):
            if pattern.search(content):
                # Never echo the matched value into CI logs.
                errors.append(f"{name!r}: {category}")
    for error in errors:
        print(error)
    print(
        f"Repository filenames and {documents} operator documents checked; {len(errors)} errors."
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
