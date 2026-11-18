"""Trusted image entry point for returning bounded coding artifacts from a tmpfs."""

from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
from contextlib import suppress
from pathlib import Path


def read_file(root: Path, name: str, limit: int) -> bytes | None:
    path = root
    for part in name.split("/")[:-1]:
        path /= part
        if not stat.S_ISDIR(path.lstat().st_mode):
            raise ValueError("unsafe artifact parent")
    path /= name.split("/")[-1]
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("unsafe artifact")
        value = handle.read(limit + 1)
        if len(value) > limit:
            raise ValueError("oversized artifact")
        return value


def main() -> int:
    maximum, names = int(sys.argv[1]), json.loads(sys.argv[2])
    workspace = Path("/tmp/dev")
    shutil.copytree("/work", workspace)
    logfile = Path("/tmp/agent-events")
    with logfile.open("wb") as output:
        proc = subprocess.Popen(
            sys.argv[3:],
            cwd=workspace,
            stdout=output,
            stderr=output,
            start_new_session=True,
        )
        try:
            code = proc.wait()
        finally:
            with suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
    with logfile.open("rb") as output:
        events = output.read(maximum + 1)
    remaining = maximum - len(events)
    if remaining < 0:
        return 1
    files = {}
    if code == 0:
        for name in names:
            try:
                value = read_file(workspace, name, remaining)
            except FileNotFoundError:
                value = None
            if value is not None:
                remaining -= len(value)
            files[name] = base64.b64encode(value).decode() if value is not None else None
    print(json.dumps({"code": code, "events": events.decode(errors="replace"), "files": files}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
