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

CODEX_AGENT_ROOT = Path("/run/codex-agent")


def read_file(root: Path, name: str, limit: int) -> bytes:
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


def prepare_home(provider: str, auth: Path, home: Path) -> dict[str, str]:
    """Copy only the provider's credential file into a fresh, private home."""
    credential = {"codex": ".codex/auth.json", "claude": ".claude/.credentials.json"}[provider]
    value = read_file(auth, credential, 1024 * 1024)
    target = home / credential
    target.parent.mkdir(parents=True, mode=0o700)
    with target.open("xb") as handle:
        os.fchmod(handle.fileno(), 0o600)
        handle.write(value)
    env = {**os.environ, "HOME": str(home)}
    env["CODEX_HOME"] = str(home / ".codex")
    env["CLAUDE_CONFIG_DIR"] = str(home / ".claude")
    return env


def subscription_confirmed(provider: str, env: dict[str, str]) -> bool:
    args = ["codex", "login", "status"] if provider == "codex" else ["claude", "auth", "status"]
    result = subprocess.run(args, env=env, capture_output=True, text=True, check=False)
    if result.returncode:
        return False
    if provider == "codex":
        return "logged in using chatgpt" in (result.stdout + result.stderr).lower()
    status = json.loads(result.stdout)
    return status.get("loggedIn") is True and status.get("authMethod") == "claude.ai"


def main() -> int:
    provider, maximum, names = sys.argv[1], int(sys.argv[2]), json.loads(sys.argv[3])
    home = CODEX_AGENT_ROOT / "home" if provider == "codex" else Path("/tmp/agent-home")
    try:
        home.mkdir(mode=0o700)
        env = prepare_home(provider, Path("/auth"), home)
        if not subscription_confirmed(provider, env):
            return 1
    except (OSError, ValueError):
        return 1
    workspace = Path("/work")
    if names is not None:
        workspace = Path("/tmp/dev")
        shutil.copytree("/work", workspace)
    logfile = Path("/tmp/agent-events")
    with logfile.open("wb") as output:
        proc = subprocess.Popen(
            sys.argv[4:],
            cwd=workspace,
            env=env,
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
        for name in names or []:
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
