"""Fail if tracked files contain likely secrets or forbidden local files.

Standard library only, so it runs identically in `make check` and in CI.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

FORBIDDEN_FILES = re.compile(r"(^|/)(\.env(\..+)?|.+\.(pem|key|db|sqlite|db-wal|db-shm))$")
ALLOWED_FILES = {".env.example"}
PATTERNS: dict[str, re.Pattern[str]] = {
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "github_token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "alpaca_key_id": re.compile(r"\b(PK|AK)[A-Z0-9]{18}\b"),
    "generic_assignment": re.compile(
        r"(?i)\b(api[_-]?key|api[_-]?secret|secret[_-]?key|password|token)\b\s*[:=]\s*['\"]?[A-Za-z0-9/+_\-]{20,}"
    ),
}
SKIP_SUFFIXES = {".csv", ".ipynb", ".png", ".svg"}


def tracked_files(root: Path) -> list[str]:
    """Tracked plus untracked, non-ignored files, so `make check` also covers files not yet added."""
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    return [item for item in output.stdout.decode().split("\0") if item]


def scan(root: Path, files: list[str]) -> list[str]:
    findings: list[str] = []
    for name in files:
        if FORBIDDEN_FILES.search(name) and Path(name).name not in ALLOWED_FILES:
            findings.append(f"{name}: forbidden file type must not be tracked")
            continue
        path = root / name
        if path.suffix in SKIP_SUFFIXES or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line) and "secret-scan: allow" not in line:
                    findings.append(f"{name}:{line_number}: possible {label}")
    return findings


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    findings = scan(root, tracked_files(root))
    for finding in findings:
        print(finding, file=sys.stderr)
    if findings:
        print(f"secret scan failed: {len(findings)} finding(s)", file=sys.stderr)
        return 1
    print("secret scan passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
