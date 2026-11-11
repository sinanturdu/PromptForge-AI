"""Check local Markdown targets/fragments in tracked and unignored new docs.

Standard-library helper for this repository's inline/reference links. Fenced examples
are ignored; external URLs are not fetched. This is not a general Markdown linter.
"""

import re
import subprocess
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def prose(path: Path) -> str:
    lines = []
    fence = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        matched = re.match(r"^\s*(`{3,}|~{3,})", line)
        if matched:
            marker = matched[1][0]
            if not fence:
                fence = marker
            elif marker == fence:
                fence = ""
        elif not fence:
            lines.append(line)
    return "\n".join(lines)


def anchors(path: Path) -> set[str]:
    counts: dict[str, int] = {}
    result = set()
    for line in prose(path).splitlines():
        matched = re.match(r"^#{1,6}\s+(.+?)\s*#*\s*$", line)
        if not matched:
            continue
        title = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", matched[1])
        slug = re.sub(r"[^\w -]", "", title.lower()).replace(" ", "-")
        number = counts.get(slug, 0)
        result.add(slug + (f"-{number}" if number else ""))
        counts[slug] = number + 1
    return result


def main() -> int:
    paths = (
        subprocess.check_output(
            ["git", "ls-files", "-c", "-o", "--exclude-standard", "-z"], cwd=ROOT
        )
        .decode("utf-8")
        .split("\0")
    )
    documents = sorted({ROOT / name for name in paths if name.endswith(".md")})
    errors = []
    checked = 0
    for document in documents:
        text = prose(document)
        links = re.findall(r"!?\[[^\]\n]+\]\(\s*(<[^>]+>|[^\s)]+)", text)
        links += re.findall(r"^\s*\[[^\]]+\]:\s*(<[^>]+>|\S+)", text, re.MULTILINE)
        for raw in links:
            url = urlsplit(raw.strip("<>"))
            if url.scheme or url.netloc:
                continue
            target = (
                (document.parent / unquote(url.path)).resolve()
                if url.path
                else document
            )
            checked += 1
            if not target.exists():
                errors.append(f"{document.relative_to(ROOT)}: missing target {raw}")
            elif (
                url.fragment
                and target.suffix == ".md"
                and unquote(url.fragment) not in anchors(target)
            ):
                errors.append(f"{document.relative_to(ROOT)}: missing heading {raw}")
    for error in errors:
        print(error)
    print(
        f"Checked {checked} local links in {len(documents)} Markdown files; {len(errors)} errors."
    )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
