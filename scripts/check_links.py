"""Fail on broken relative links in Markdown files (documentation check for make check / CI)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def markdown_files(root: Path) -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "*.md"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [root / name for name in output.stdout.decode().split("\0") if name]


def broken_links(root: Path, files: list[Path]) -> list[str]:
    problems: list[str] = []
    for path in files:
        if "legacy" in path.parts or "archive" in path.parts or not path.is_file():
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            for target in LINK.findall(line):
                if target.startswith(SKIP_PREFIXES):
                    continue
                relative = target.split("#", 1)[0]
                if relative and not (path.parent / relative).exists():
                    problems.append(f"{path.relative_to(root)}:{line_number}: broken link {target}")
    return problems


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    problems = broken_links(root, markdown_files(root))
    for problem in problems:
        print(problem, file=sys.stderr)
    if problems:
        return 1
    print("link check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
