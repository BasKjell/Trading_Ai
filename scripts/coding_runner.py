"""Bounded local Codex/Claude dispatcher. No SDK, API-key fallback, or automatic merge.

Run with Python >=3.12 on macOS/Linux. See docs/CODING_RUNNER.md.
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import tomllib
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FAMILIES = ("codex", "claude")
MANUAL_TASKS = {"T-005", "T-006", "T-008", "T-011", "T-012", "T-013", "T-014", "T-015", "T-016"}
BLOCK_LABELS = {"blocked", "manual-only", "needs-independent-review", "effort:high", "research"}
CLAIM = "<!-- coding-runner claim "
RESULT = "RUNNER_RESULT "
LIMIT_CODES = {"rate_limit_error", "rate_limit_exceeded", "usage_limit_reached", "insufficient_quota"}


class RunnerError(RuntimeError):
    pass


def command(args: list[str], cwd: Path, *, timeout: int = 60) -> str:
    """Never use a shell, and never include command output in persisted error messages."""
    try:
        run = subprocess.run(args, cwd=cwd, env=clean_environment(), capture_output=True,
                             text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RunnerError(f"{Path(args[0]).name}: {type(exc).__name__}") from exc
    if run.returncode:
        raise RunnerError(f"{Path(args[0]).name}: exit {run.returncode}; inspect locally")
    return run.stdout


class GitHub:
    def __init__(self, root: Path, repo: str) -> None:
        self.root, self.repo = root, repo

    def api(self, endpoint: str, body: str | None = None) -> Any:
        args = ["gh", "api", f"repos/{self.repo}/{endpoint}"]
        if body is None:
            args += ["--paginate", "--slurp"]
        else:
            args += ["--method", "POST", "-f", f"body={body}"]
        raw = json.loads(command(args, self.root))
        if body is None:
            # --slurp wraps every page, including single-object responses.
            if not raw:
                raise RunnerError("GitHub returned no response")
            if isinstance(raw[0], list):
                return [row for page in raw for row in page]
            if "check_runs" in raw[0]:
                return {"check_runs": [row for page in raw for row in page["check_runs"]]}
            return raw[0]
        return raw


def family(body: str) -> str | None:
    """Provenance comes from explicit Agent metadata / review prefix, never GitHub login."""
    match = re.search(r"(?:Agent(?:/Modell/Denkstufe)?\s*:\s*|^)(Codex|Claude(?: Code)?)\b", body, re.I)
    return match.group(1).lower().split()[0] if match else None


def task_id(title: str) -> str | None:
    matches = set(re.findall(r"\bT-\d{3}\b", title))
    return next(iter(matches)) if len(matches) == 1 else None


def labels(item: dict[str, Any]) -> set[str]:
    return {row["name"] for row in item.get("labels", [])}


def eligible(item: dict[str, Any]) -> bool:
    tags = labels(item)
    return {"auto-ready", "effort:medium"} <= tags and not tags & BLOCK_LABELS


@dataclass(frozen=True)
class Job:
    kind: str
    number: int
    provider: str
    task: str
    branch: str
    sha: str
    base: str
    revision: str = ""

    @property
    def key(self) -> str:
        # An unassigned issue is one job, regardless of which provider wins it.
        payload = (f"implement:{self.number}" if self.kind == "implement" else
                   f"{self.kind}:{self.number}:{self.provider}:{self.sha}:{self.base}:{self.revision}")
        return hashlib.sha256(payload.encode()).hexdigest()[:24]


def plan(gh: GitHub, providers: list[str], resumable: dict[int, str] | None = None) -> list[Job]:
    resumable = resumable or {}
    jobs: list[Job] = []
    prs = gh.api("pulls?state=open&per_page=100")
    for pr in prs:
        if pr.get("draft") or not eligible(pr):
            continue
        task = task_id(pr["title"])
        owner = family(pr.get("body") or "")
        if not task or task in MANUAL_TASKS or owner is None:
            continue
        number, sha, base = pr["number"], pr["head"]["sha"], pr["base"]["ref"]
        # Never check out a fork branch under the identity of this repository.
        if (pr["head"].get("repo") or {}).get("full_name") != gh.repo:
            continue
        reviews = gh.api(f"pulls/{number}/reviews?per_page=100")
        current = [r for r in reviews if r.get("commit_id") == sha and family(r.get("body") or "")]
        foreign = [r for r in current if family(r.get("body") or "") != owner]
        latest = foreign[-1] if foreign else None
        if latest and (
            latest.get("state") == "CHANGES_REQUESTED" or "ÄNDERUNGEN NÖTIG" in (latest.get("body") or "")
        ):
            if owner in providers:
                jobs.append(Job("fix", number, owner, task, pr["head"]["ref"], sha, base, str(latest["id"])))
            continue
        for provider in providers:
            if provider != owner and not any(family(r.get("body") or "") == provider for r in current):
                jobs.append(Job("review", number, provider, task, "", sha, base))
                break
    for issue in gh.api("issues?state=open&per_page=100"):
        if "pull_request" in issue or not eligible(issue) or "work:coding" not in labels(issue):
            continue
        task = task_id(issue["title"])
        if not task or task in MANUAL_TASKS:
            continue
        assigned = labels(issue) & {"agent:codex", "agent:claude"}
        if len(assigned) > 1:
            continue
        candidates = [p for p in providers if not assigned or f"agent:{p}" in assigned]
        if not candidates:
            continue
        # A pre-existing manual claim is not abandoned just because time passed.
        if "done" in labels(issue) or (
            labels(issue) & {"claimed", "in_progress", "review"} and issue["number"] not in resumable
        ):
            continue
        if issue["number"] in resumable:
            candidates = [p for p in candidates if p == resumable[issue["number"]]]
        for provider in candidates:
            jobs.append(Job("implement", issue["number"], provider, task, f"agent/{task}-auto-{provider}", "", "main"))
    priority = {"fix": 0, "review": 1, "implement": 2}
    return sorted(jobs, key=lambda j: (priority[j.kind], j.number, providers.index(j.provider)))


class State:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.path = directory / "state.json"
        self.data: dict[str, Any] = {"owner": uuid.uuid4().hex, "jobs": {}, "cooldown": {}, "paused": {}}

    def load(self) -> None:
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        for row in self.data["jobs"].values():
            if row["status"] == "running":
                row.update(status="blocked", reason="interrupted run; inspect worktree and explicitly retry")
                self.data["paused"].update(dict.fromkeys(FAMILIES, "interrupted run; check for orphan workers"))

    def save(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", dir=self.directory, delete=False) as handle:
            json.dump(self.data, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, self.path)

    def ready(self, job: Job, now: float) -> bool:
        if self.data["paused"].get(job.provider) or self.data["cooldown"].get(job.provider, 0) > now:
            return False
        row = self.data["jobs"].get(job.key, {})
        return row.get("provider", job.provider) == job.provider and row.get("status") in (None, "retry", "rate_limited")


@contextlib.contextmanager
def lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "runner.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RunnerError("another runner owns this state directory") from exc
        yield


def claim(gh: GitHub, state: State, job: Job) -> bool:
    """First server-confirmed claim wins across hosts. Claims never expire automatically."""
    prefix = f"{CLAIM}key={job.key} "
    mine = f"{prefix}owner={state.data['owner']} -->"
    endpoint = f"issues/{job.number}/comments?per_page=100"
    comments = gh.api(endpoint)
    matches = [c for c in comments if prefix in (c.get("body") or "")]
    manual = [c for c in comments if "claimed by" in (c.get("body") or "").lower() and CLAIM not in c["body"]]
    if job.kind == "implement" and manual:
        return False
    if not matches:
        gh.api(f"issues/{job.number}/comments", mine + f"\nclaimed by runner/{job.provider} for {job.kind} {job.task}")
        matches = [c for c in gh.api(endpoint) if prefix in (c.get("body") or "")]
    return bool(matches) and mine in min(matches, key=lambda c: c["id"])["body"]


def clean_environment() -> dict[str, str]:
    # CLI subscription login is required. Never pass broker or API credentials to a worker.
    blocked = ("ALPACA", "PAPER_BROKER", "LIVE_BROKER", "OPENAI_API", "CODEX_API", "ANTHROPIC_API")
    env = {k: v for k, v in os.environ.items() if not k.startswith(blocked)}
    env["LIVE_TRADING_ENABLED"] = "false"
    env["PAPER_TRADING_ONLY"] = "true"
    return env


def worker_command(provider: str, settings: dict[str, Any]) -> list[str]:
    binary, model = settings["binary"], settings["model"]
    if provider == "codex":
        return [binary, "--ask-for-approval", "never", "exec", "--sandbox", "workspace-write", "--model", model,
                "-c", 'model_reasoning_effort="medium"', "--json", "-"]
    return [binary, "-p", "--model", model, "--effort", "medium", "--output-format", "json",
            "--permission-mode", "acceptEdits", "--allowedTools",
            "Read,Edit,Write,Glob,Grep,Bash(git *),Bash(gh *),Bash(make *),Bash(python3 *)"]


def subscription_login(provider: str, settings: dict[str, Any], cwd: Path) -> None:
    binary = settings["binary"]
    if not shutil.which(binary):
        raise RunnerError(f"{provider}: binary missing")
    if provider == "codex":
        # Some Codex releases write login status to stderr; check both streams without logging it.
        run = subprocess.run([binary, "login", "status"], cwd=cwd, env=clean_environment(),
                             capture_output=True, text=True, timeout=20)
        if run.returncode or "chatgpt" not in (run.stdout + run.stderr).lower():
            raise RunnerError("codex: ChatGPT subscription login not confirmed")
    else:
        auth = json.loads(command([binary, "auth", "status"], cwd))
        if not auth.get("loggedIn") or auth.get("authMethod") not in {"claude.ai", "oauth_token"}:
            raise RunnerError("claude: subscription login not confirmed")


def outcome(text: str, returncode: int) -> str:
    """Read only terminal worker messages / errors, not arbitrary tool output containing 'limit'."""
    terminal: list[str] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        error = event.get("error")
        if event.get("type") == "error" or event.get("is_error") or error:
            encoded = json.dumps(error or event).lower()
            if any(code in encoded for code in LIMIT_CODES) or "usage limit" in encoded or "rate limit" in encoded:
                return "rate_limited"
        if event.get("type") == "result" and not event.get("is_error"):
            terminal.append(str(event.get("result", "")))
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            terminal.append(str(item.get("text", "")))
    if returncode:
        if re.search(r"(?:you.ve hit your usage limit|usage limit reached|rate_limit_exceeded)", text, re.I):
            return "rate_limited"
        return "blocked"
    for line in "\n".join(terminal).splitlines()[::-1]:
        if line.startswith(RESULT):
            try:
                status = json.loads(line[len(RESULT):])["status"]
                return "verify" if status == "done" else "blocked"
            except (ValueError, KeyError, TypeError):
                break
    return "blocked"


def run_worker(args: list[str], cwd: Path, prompt: str, timeout: int, max_output: int = 2_000_000) -> str:
    """Bound runtime/output. Kill the whole process group; never persist raw model/tool output."""
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(args, cwd=cwd, env=clean_environment(), stdin=subprocess.PIPE,
                                stdout=output, stderr=output, start_new_session=True)
        try:
            assert proc.stdin is not None
            proc.stdin.write(prompt.encode())
            proc.stdin.close()
            deadline = time.monotonic() + timeout
            while proc.poll() is None:
                if time.monotonic() >= deadline or os.fstat(output.fileno()).st_size > max_output:
                    return "blocked"
                time.sleep(0.2)
            output.seek(0)
            return outcome(output.read(max_output).decode(errors="replace"), proc.returncode)
        finally:
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


def prepare(root: Path, directory: Path, job: Job, row: dict[str, Any]) -> Path:
    if job.branch and (job.branch.startswith("-") or command(["git", "check-ref-format", "--branch", job.branch], root).strip() != job.branch):
        raise RunnerError("invalid branch name")
    path = directory / "worktrees" / job.key
    path.parent.mkdir(exist_ok=True)
    if row.get("worktree"):
        if path.resolve() != Path(row["worktree"]).resolve() or not path.is_dir():
            raise RunnerError("managed worktree missing; no automatic recreation")
        expected = job.sha if job.kind == "review" else job.branch
        actual = command(["git", "rev-parse", "HEAD"] if job.kind == "review" else
                         ["git", "branch", "--show-current"], path).strip()
        if expected != actual:
            raise RunnerError("managed worktree changed branch/revision")
        return path
    if path.exists():
        raise RunnerError("unmanaged worktree exists; inspect manually")
    command(["git", "fetch", "origin"], root)
    if job.kind == "review":
        command(["git", "worktree", "add", "--detach", str(path), job.sha], root)
    elif job.kind == "fix":
        if job.branch in {"main", "master"}:
            raise RunnerError("refusing to edit protected branch")
        remote = f"refs/remotes/origin/{job.branch}"
        if command(["git", "rev-parse", remote], root).strip() != job.sha:
            raise RunnerError("PR head changed; replan")
        command(["git", "worktree", "add", "-b", job.branch, str(path), remote], root)
    else:
        command(["git", "worktree", "add", "-b", job.branch, str(path), "origin/main"], root)
    return path


def prompt_for(job: Job) -> str:
    return f"""Arbeite als {job.provider}, Modell laut CLI, Denkstufe Medium. Auftrag: {job.kind},
GitHub Nummer {job.number}, Task {job.task}, geprüfte Ausgangsrevision {job.sha or 'origin/main'}.
Lies AGENTS.md, PROJECT_SPEC, STATUS, ARCHITECTURE, Issue/PR samt allen Kommentaren und Handoff.
Nur diese freigegebene Aufgabe bearbeiten. Bei komplexer/risikorelevanter Aufgabe oder nach zwei
Fehlversuchen: Handoff schreiben, needs-human melden; kein Modell-/Effortwechsel.
implement/fix: Tests und make check, Handoff, changelog.d, auf genau {job.branch} committen/pushen,
PR erstellen/aktualisieren, CI prüfen. Agent-Feld und Co-Authored-By korrekt setzen.
review: nur unabhängig reviewen, make check ausführen, COMMENT-Review mit Head-SHA und Zielbranch
{job.base} abgeben. Keine Implementierungsänderungen während eines Reviews.
Keine anderen Aufgaben, keine Forschung/Ideenexperimente, kein Merge, kein Force-Push, keine
echten Orders, keine Datenkäufe, keine Risikolimit-Erhöhung. Keine Geheimnisse aus .env laden.
Der Runner-Claim gehört dieser Sitzung. Bei Pause Übergabe unter docs/handoffs/{job.task}.md.
Letzte Antwort enthält genau eine Zeile: RUNNER_RESULT {{"status":"done"}} oder
RUNNER_RESULT {{"status":"needs-human"}}. done nur nach tatsächlicher Fertigstellung.
"""


def verify(gh: GitHub, job: Job, worktree: Path) -> bool:
    """A process exit or confident final answer is never accepted as proof of completion."""
    if command(["git", "status", "--porcelain"], worktree).strip():
        return False
    command(["make", "check"], worktree, timeout=1800)
    if job.kind == "review":
        pr = gh.api(f"pulls/{job.number}")
        if pr["head"]["sha"] != job.sha or pr["base"]["ref"] != job.base:
            return False
        return any(r.get("commit_id") == job.sha and family(r.get("body") or "") == job.provider
                   and f"Zielbranch: {job.base}" in (r.get("body") or "")
                   and "Empfehlung:" in (r.get("body") or "")
                   for r in gh.api(f"pulls/{job.number}/reviews?per_page=100"))
    prs = gh.api("pulls?state=open&per_page=100")
    head = command(["git", "rev-parse", "HEAD"], worktree).strip()
    matches = [p for p in prs if not p.get("draft") and p["head"]["ref"] == job.branch and p["head"]["sha"] == head
               and (p["head"].get("repo") or {}).get("full_name") == gh.repo and family(p.get("body") or "") == job.provider]
    if not matches or (job.kind == "fix" and head == job.sha):
        return False
    checks = gh.api(f"commits/{head}/check-runs")["check_runs"]
    statuses = gh.api(f"commits/{head}/status")
    status_ok = statuses.get("total_count", 0) == 0 or statuses["state"] == "success"
    return bool(checks) and all(c.get("conclusion") == "success" for c in checks) and status_ok


def validate_root(root: Path, repo: str) -> None:
    if command(["git", "rev-parse", "--show-toplevel"], root).strip() != str(root):
        raise RunnerError("--repo-root must be the repository root")
    remote = command(["git", "remote", "get-url", "origin"], root).strip().removesuffix(".git")
    if remote not in {f"https://github.com/{repo}", f"git@github.com:{repo}", f"ssh://git@github.com/{repo}"}:
        raise RunnerError("origin does not match configured GitHub repository")
    if command(["git", "status", "--porcelain"], root).strip():
        raise RunnerError("control checkout must be clean; existing changes are preserved")
    for path in ("AGENTS.md", "docs/PROJECT_SPEC.md", "docs/STATUS.md", "docs/ARCHITECTURE.md"):
        if not (root / path).is_file():
            raise RunnerError("project rules missing; integrate prerequisite documentation first")


def cycle(config: dict[str, Any], root: Path, state: State, gh: GitHub, *, dry_run: bool) -> bool:
    providers = [p for p in FAMILIES if config[p].get("enabled", True)]
    resumable = {r["number"]: r["provider"] for r in state.data["jobs"].values()
                 if r.get("kind") == "implement" and r["status"] in {"retry", "rate_limited"}}
    jobs = plan(gh, providers, resumable)
    if dry_run:
        print(json.dumps([{"key": j.key, "kind": j.kind, "number": j.number, "provider": j.provider} for j in jobs]))
        return False
    for job in jobs:
        if not state.ready(job, time.time()):
            continue
        try:
            subscription_login(job.provider, config[job.provider], root)
        except (RunnerError, ValueError, subprocess.TimeoutExpired):
            state.data["paused"][job.provider] = "CLI/authentication unavailable; doctor then resume-provider"
            state.save()
            continue
        row = state.data["jobs"].setdefault(job.key, {"status": "retry", "provider": job.provider,
                                                    "number": job.number, "kind": job.kind})
        try:
            if not claim(gh, state, job):
                row.update(status="blocked", reason="claim belongs to another session")
                state.save()
                continue
            if job.kind == "implement":
                command(["gh", "issue", "edit", str(job.number), "--repo", gh.repo, "--add-label", "claimed"], root)
            worktree = prepare(root, state.directory, job, row)
            row.update(status="running", worktree=str(worktree), started_at=time.time())
            state.save()
            result = run_worker(worker_command(job.provider, config[job.provider]), worktree, prompt_for(job),
                                int(config["runner"]["timeout_seconds"]))
            if result == "verify":
                result = "completed" if verify(gh, job, worktree) else "blocked"
            if result == "rate_limited":
                state.data["cooldown"][job.provider] = time.time() + config["runner"]["cooldown_seconds"]
            row.update(status=result, finished_at=time.time())
        except (RunnerError, OSError, ValueError, subprocess.TimeoutExpired):
            row.update(status="blocked", reason="operation failed; inspect worktree/CLI/GitHub locally")
        state.save()
        print(json.dumps({"job": job.key, "provider": job.provider, "status": row["status"]}))
        return True
    print("idle: no eligible runnable job")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["doctor", "once", "watch", "status", "retry", "resume-provider"])
    parser.add_argument("target", nargs="?")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config/coding-runner.toml"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    root = args.repo_root.resolve()
    config_path = args.config if args.config.is_absolute() else root / args.config
    try:
        config = tomllib.loads(config_path.read_text())
        directory = (root / config["runner"]["state_directory"]).resolve()
        with lock(directory):
            state = State(directory)
            state.load()
            if args.action == "status":
                print(json.dumps(state.data, indent=2))
                return 0
            if args.action in {"retry", "resume-provider"}:
                if args.action == "retry":
                    state.data["jobs"][args.target]["status"] = "retry"
                else:
                    if args.target not in FAMILIES:
                        raise RunnerError("target must be codex or claude")
                    state.data["paused"].pop(args.target, None)
                    state.data["cooldown"].pop(args.target, None)
                state.save()
                return 0
            validate_root(root, config["runner"]["repository"])
            gh = GitHub(root, config["runner"]["repository"])
            command(["gh", "auth", "status"], root)
            if args.action == "doctor":
                for provider in FAMILIES:
                    if config[provider].get("enabled", True):
                        subscription_login(provider, config[provider], root)
                command(["make", "check"], root, timeout=1800)
                print("doctor: CLI subscription logins, clean origin checkout and make check OK")
                return 0
            while True:
                if (directory / "STOP").exists() and not args.dry_run:
                    print("runner paused by STOP file")
                    return 0
                cycle(config, root, state, gh, dry_run=args.dry_run)
                if args.action != "watch" or args.dry_run or (directory / "STOP").exists():
                    return 0
                time.sleep(max(60, config["runner"]["poll_seconds"]))
    except (RunnerError, OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(f"runner stopped: {type(exc).__name__}; check doctor/configuration locally", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("runner interrupted; inspect managed worktree before retry", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
