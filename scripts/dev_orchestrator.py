"""Persistent, single-worker development orchestration for Trading_Ai."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from coding_runner import FAMILIES, MANUAL_TASKS, RunnerError, eligible, labels, lock, task_id, validate_root
from dev_orchestrator_backend import Containers, Pending, Quota, Repository, safe_path

ACTIVE = ("claim", "develop", "review", "merge", "finalize")
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]*")
IMAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]+@sha256:[a-f0-9]{64}")
DEPENDENCIES = re.compile(r"^\s*(?:Depends on|Abhängigkeiten)\s*:\s*(.*?)\s*$", re.I | re.M)


def config_file(path: Path) -> dict[str, Any]:
    # PyYAML is already pinned for development and CI. Keep the trading runtime dependency-free.
    try:
        import yaml
    except ImportError as exc:
        raise RunnerError("install the pinned PyYAML from requirements-dev.txt") from exc
    try:
        config = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise RunnerError("invalid YAML configuration") from exc
    for name, key in (("TRADING_DEV_STATE", "state_directory"), ("TRADING_DEV_IMAGE", "image")):
        if name in os.environ:
            config[key] = os.environ[name]
    for name, cap in {
        "poll_seconds": 3600,
        "timeout_seconds": 3600,
        "cooldown_seconds": 86400 * 7,
        "max_age_seconds": 86400 * 30,
        "max_tasks": 1000,
        "max_corrections": 3,
        "max_quota_pauses": 20,
        "output_bytes": 16_000_000,
        "diff_bytes": 500_000,
        "file_bytes": 32_000_000,
        "pids": 1024,
        "cpus": 16,
    }.items():
        value = config[name]
        if type(value) is not int or not (0 if name == "max_corrections" else 1) <= value <= cap:
            raise RunnerError(f"invalid limit {name}")
    if config["repository"] != "BasKjell/Trading_Ai":
        raise RunnerError("unexpected repository")
    if not isinstance(config["execution_enabled"], bool) or not isinstance(config["merge_enabled"], bool):
        raise RunnerError("execution_enabled and merge_enabled must be booleans")
    if not re.fullmatch(r"[1-9][0-9]*[mg]", config["memory"]) or not re.fullmatch(
        r"[1-9][0-9]*[mg]", config["scratch"]
    ):
        raise RunnerError("memory and scratch must be specified in m or g")
    if config["author"] not in FAMILIES:
        raise RunnerError("default author must be Codex or Claude")
    for provider in FAMILIES:
        settings = config[provider]
        if any(not SAFE_NAME.fullmatch(settings[key]) for key in ("auth_volume", "network", "model")):
            raise RunnerError("invalid provider settings")
        if not re.fullmatch(r"http://[A-Za-z0-9_.-]+:[0-9]{1,5}", settings["proxy"]):
            raise RunnerError("provider requires a restricted forward proxy")
        if settings["network"] in {"none", "host", "bridge", "default"}:
            raise RunnerError("provider requires its own restricted Docker network")
    for key in ("auth_volume", "network"):
        if config["codex"][key] == config["claude"][key]:
            raise RunnerError(f"providers must use separate {key} values")
    if (ollama := config.get("ollama")) and (
        not SAFE_NAME.fullmatch(ollama["model"])
        or not SAFE_NAME.fullmatch(ollama["network"])
        or ollama["network"]
        in {"none", "host", "bridge", "default", config["codex"]["network"], config["claude"]["network"]}
        or not re.fullmatch(r"https?://[A-Za-z0-9_.-]+:[0-9]{1,5}", ollama["host"])
    ):
        raise RunnerError("invalid Ollama network, URL or model")
    if (opencode := config.get("opencode")) is not None:
        used_networks = {config[provider]["network"] for provider in FAMILIES}
        if ollama:
            used_networks.add(ollama["network"])
        if (
            not isinstance(opencode, dict)
            or not SAFE_NAME.fullmatch(opencode["model"])
            or opencode["model"].lower().endswith(("-cloud", ":cloud"))
            or not SAFE_NAME.fullmatch(opencode["network"])
            or opencode["network"] in used_networks | {"none", "host", "bridge", "default"}
            or not re.fullmatch(r"http://[A-Za-z0-9_-]+:[0-9]{1,5}", opencode["host"])
        ):
            raise RunnerError("invalid OpenCode network, local Ollama URL or model")
    for name, scope in config["tasks"].items():
        paths = scope["paths"]
        deps = scope["depends_on"]
        if (
            task_id(name) != name
            or name in MANUAL_TASKS
            or scope["class"] != "U"
            or not isinstance(paths, list)
            or not paths
            or len(paths) > 30
            or not all(isinstance(item, str) and safe_path(item) for item in paths)
            or len(set(paths)) != len(paths)
            or not isinstance(deps, list)
            or not all(type(number) is int and number > 0 for number in deps)
            or type(scope.get("priority", 100)) is not int
            or (scope.get("ollama") and not ollama)
            or type(scope.get("opencode", False)) is not bool
            or (scope.get("opencode") and (not opencode or scope.get("ollama")))
        ):
            raise RunnerError("invalid task scope or dependency")
    return config


class Store:
    def __init__(self, path: Path) -> None:
        self.db = sqlite3.connect(path, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS tasks (
                issue INTEGER PRIMARY KEY, task TEXT UNIQUE NOT NULL, author TEXT NOT NULL,
                reviewer TEXT NOT NULL, branch TEXT UNIQUE NOT NULL, phase TEXT NOT NULL,
                created REAL NOT NULL, next_at REAL NOT NULL DEFAULT 0, corrections INTEGER NOT NULL DEFAULT 0,
                quota_pauses INTEGER NOT NULL DEFAULT 0, pr INTEGER, head TEXT, review_id INTEGER,
                findings TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, stamp REAL NOT NULL, issue INTEGER, phase TEXT NOT NULL, detail TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cooldown (provider TEXT PRIMARY KEY, until REAL NOT NULL);
        """)

    def close(self) -> None:
        self.db.close()

    def setting(self, name: str, default: str = "") -> str:
        row = self.db.execute("SELECT value FROM settings WHERE name=?", (name,)).fetchone()
        return row["value"] if row else default

    def set(self, name: str, value: str) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (name, value))

    def owner(self) -> str:
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO settings VALUES ('owner', ?)", (uuid.uuid4().hex,))
        return self.setting("owner")

    def tasks(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.db.execute("SELECT * FROM tasks ORDER BY created, issue")]

    def event(self, issue: int, phase: str, detail: str = "") -> None:
        self.db.execute(
            "INSERT INTO events(stamp, issue, phase, detail) VALUES (?, ?, ?, ?)", (time.time(), issue, phase, detail)
        )
        self.db.execute("DELETE FROM events WHERE id <= (SELECT MAX(id) FROM events) - 2000")

    def record_progress(self, issue: int, phase: str) -> None:
        with self.db:
            self.event(issue, phase)

    def enqueue(self, issue: int, task: str, author: str, scope: dict[str, Any]) -> None:
        reviewers = [provider for provider in FAMILIES if provider != author]
        reviewer = min(reviewers, key=self.cooldown_until)
        with self.db:
            self.db.execute(
                "INSERT INTO tasks(issue, task, author, reviewer, branch, phase, created, scope) "
                "VALUES (?, ?, ?, ?, ?, 'claim', ?, ?)",
                (
                    issue,
                    task,
                    author,
                    reviewer,
                    f"agent/{task}-orchestrator-{issue}",
                    time.time(),
                    json.dumps(scope, sort_keys=True),
                ),
            )
            self.event(issue, "claim")

    def transition(self, issue: int, phase: str, *, reason: str = "", **fields: Any) -> None:
        updates = {"phase": phase, "reason": reason, "next_at": 0, **fields}
        # Columns are chosen only by the controller, never by payloads from agents or GitHub.
        columns = ", ".join(f"{name}=?" for name in updates)
        with self.db:
            self.db.execute(f"UPDATE tasks SET {columns} WHERE issue=?", (*updates.values(), issue))
            self.event(issue, phase, reason)

    def pause_provider(self, task: dict[str, Any], provider: str, until: float) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO cooldown VALUES (?, ?)", (provider, until))
            self.db.execute(
                "UPDATE tasks SET phase=?, next_at=?, quota_pauses=quota_pauses+1, reason=? WHERE issue=?",
                (task["phase"], until, f"{provider} usage limit", task["issue"]),
            )
            self.event(task["issue"], task["phase"], f"{provider} usage limit")

    def cooldown_until(self, provider: str) -> float:
        row = self.db.execute("SELECT until FROM cooldown WHERE provider=?", (provider,)).fetchone()
        return float(row["until"]) if row else 0

    def recover(self) -> None:
        for task in self.tasks():
            if task["phase"].startswith("running:"):
                self.transition(task["issue"], "blocked", reason=f"interrupted during {task['phase'][8:]}; inspect")

    def status(self) -> dict[str, Any]:
        tasks = [{key: value for key, value in row.items() if key not in {"findings", "scope"}} for row in self.tasks()]
        return {
            "control": self.setting("control", "ready"),
            "tasks": tasks,
            "provider_cooldowns": [dict(row) for row in self.db.execute("SELECT * FROM cooldown")],
            "recent_events": [dict(row) for row in self.db.execute("SELECT * FROM events ORDER BY id DESC LIMIT 20")],
        }


def dependency_numbers(body: str, configured: list[int]) -> set[int] | None:
    deps = set(configured)
    for entry in DEPENDENCIES.findall(body):
        if entry.strip().lower() in {"none", "keine", "-"}:
            continue
        if not re.fullmatch(r"#[0-9]+(?:[, ]+#[0-9]+)*", entry.strip()):
            return None
        deps.update(int(number) for number in re.findall(r"#([0-9]+)", entry))
    return deps


def satisfied(repo: Repository, issue: dict[str, Any], scope: dict[str, Any]) -> bool:
    deps = dependency_numbers(issue.get("body") or "", scope["depends_on"])
    if deps is None or issue["number"] in deps:
        return False
    native = repo.api(f"issues/{issue['number']}/dependencies/blocked_by?per_page=100")
    if any(
        row.get("repository_url") != f"https://api.github.com/repos/{repo.repo}" and row["state"] != "closed"
        for row in native
    ):
        return False
    deps.update(
        row["number"] for row in native if row.get("repository_url") == f"https://api.github.com/repos/{repo.repo}"
    )
    return all(
        "pull_request" not in row and row["state"] == "closed" and "done" in labels(row)
        for row in (repo.api(f"issues/{number}") for number in sorted(deps))
    )


def choose(
    repo: Repository, config: dict[str, Any], known: list[dict[str, Any]], *, local_only: bool = False
) -> tuple | None:
    existing = {row["issue"] for row in known}
    tasks = {row["task"] for row in known}
    tasks.update(task_id(pr["title"]) for pr in repo.api("pulls?state=open&per_page=100"))
    roadmap = (repo.root / "docs/STATUS.md").read_text()
    section = re.search(r"^## Nächste Ziele[^\n]*\n(.*?)(?=^## |\Z)", roadmap, flags=re.M | re.S)
    order = list(dict.fromkeys(re.findall(r"\bT-\d{3}\b", section[1]))) if section else []
    candidates = []
    for issue in repo.api("issues?state=open&per_page=100"):
        tags = labels(issue)
        task = task_id(issue["title"])
        if (
            "pull_request" in issue
            or task not in config["tasks"]
            or task in tasks
            or issue["number"] in existing
            or not eligible(issue)
            or not {"backlog", "work:coding"} <= tags
            or tags & {"claimed", "in_progress", "review", "done"}
            or issue.get("assignees")
        ):
            continue
        scope = config["tasks"][task]
        owners = [provider for provider in (*FAMILIES, "opencode") if f"agent:{provider}" in tags]
        local = scope.get("opencode") is True and owners == ["opencode"]
        if (
            len(owners) > 1
            or (scope.get("opencode") is True) != local
            or ("opencode" in owners and not local)
            or (local_only and not local)
            or not satisfied(repo, issue, scope)
        ):
            continue
        priority = (scope.get("priority", 100), order.index(task) if task in order else len(order), issue["number"])
        candidates.append((priority, issue["number"], task, owners[0] if owners else config["author"], scope))
    return min(candidates)[1:] if candidates else None


def cycle(repo: Repository, db: Store, config: dict[str, Any]) -> bool:
    tasks = db.tasks()
    if any(row["phase"] == "blocked" for row in tasks):
        # A blocked task can have a live claim or partially published artifacts. Hold the queue.
        return False
    now = time.time()
    active = [row for row in tasks if row["phase"] in ACTIVE]
    task = next(
        (
            row
            for row in active
            if row["created"] + config["max_age_seconds"] <= now
            or (
                row["next_at"] <= now
                and (row["phase"] != "review" or db.cooldown_until(row["reviewer"]) <= now)
                and (row["phase"] != "develop" or db.cooldown_until(row["author"]) <= now)
            )
        ),
        None,
    )
    if task is None:
        can_prepare = any(
            row["phase"] in {"develop", "review"}
            and db.cooldown_until(row["author"] if row["phase"] == "develop" else row["reviewer"]) > now
            for row in active
        )
        if (
            len(tasks) < config["max_tasks"]
            and (not active or can_prepare)
            and (chosen := choose(repo, config, tasks, local_only=bool(active)))
        ):
            db.enqueue(*chosen)
            return True
        return False
    phase, issue = task["phase"], task["issue"]
    if task["created"] + config["max_age_seconds"] <= now and phase != "finalize":
        db.transition(issue, "blocked", reason="task time budget exhausted")
        return True
    scope = config["tasks"].get(task["task"])
    if scope is None or json.dumps(scope, sort_keys=True) != task["scope"]:
        db.transition(issue, "blocked", reason="authorized task scope changed")
        return True
    db.transition(issue, f"running:{phase}")
    try:
        if phase != "finalize":
            metadata = repo.api(f"issues/{issue}")
            if not repo.authorized(task) or not satisfied(repo, metadata, scope):
                raise RunnerError("issue authorization or dependencies changed")
        if phase == "claim":
            repo.claim(task, db.owner())
            db.transition(issue, "develop")
        elif phase == "develop":
            pr, head = repo.develop(task, metadata, scope)
            db.transition(issue, "review", pr=pr, head=head)
        elif phase == "review":
            approved, findings, review_id = repo.review(task, scope)
            if approved:
                auto_merge = (
                    task["author"] != "opencode" and config["merge_enabled"] and repo.authorized(task, merge=True)
                )
                next_phase = "merge" if auto_merge else "manual"
                db.transition(issue, next_phase, review_id=review_id, findings="")
            elif findings == task["findings"] and task["corrections"]:
                db.transition(
                    issue,
                    "blocked",
                    reason="same blocking finding remains after correction",
                    review_id=review_id,
                    findings=findings,
                )
            elif task["corrections"] >= config["max_corrections"]:
                db.transition(
                    issue, "blocked", reason="correction limit reached", review_id=review_id, findings=findings
                )
            else:
                db.transition(
                    issue,
                    "develop",
                    corrections=task["corrections"] + 1,
                    review_id=review_id,
                    findings=findings,
                )
        elif phase == "merge":
            if not config["merge_enabled"]:
                db.transition(issue, "manual", reason="automatic merge disabled")
            else:
                repo.merge(task, scope, config["required_check"])
                db.transition(issue, "finalize")
        else:
            repo.finalize(task)
            db.transition(issue, "done")
    except Pending:
        db.transition(issue, phase, next_at=now + config["poll_seconds"], reason="waiting for exact-head CI")
    except Quota as exc:
        if task["quota_pauses"] >= config["max_quota_pauses"]:
            db.transition(issue, "blocked", reason="provider retry budget exhausted")
        else:
            db.pause_provider(task, str(exc), time.time() + config["cooldown_seconds"])
    except (RunnerError, ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        reason = str(exc) if isinstance(exc, RunnerError) else f"{type(exc).__name__}; inspect {phase} locally"
        db.transition(issue, "blocked", reason=reason)
    return True


def service(repo: Repository, db: Store, config: dict[str, Any]) -> int:
    db.recover()
    while (control := db.setting("control")) != "stopped":
        if control != "paused":
            try:
                if cycle(repo, db, config):
                    continue
            except RunnerError:
                # Discovery is read-only and can retry after a transient GitHub failure.
                print("Discovery unavailable; retrying after the poll interval.", file=sys.stderr, flush=True)
        # Short waits let status/pause/stop stay responsive without interrupting a mutation.
        for _ in range(config["poll_seconds"]):
            time.sleep(1)
            if db.setting("control") != control:
                break
    return 0


def interrupt(_signum: int, _frame: Any) -> None:
    raise KeyboardInterrupt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "pause", "resume", "status", "plan", "resolve"])
    parser.add_argument("issue", nargs="?", type=int)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config/dev-orchestrator.yaml"))
    parser.add_argument("--execute", action="store_true", help="explicitly arm container and GitHub operations")
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, interrupt)
    try:
        root = args.repo_root.resolve()
        config = config_file(root / args.config)
        directory = Path(config["state_directory"]).expanduser().resolve()
        if (
            not Path(config["state_directory"]).expanduser().is_absolute()
            or directory.is_relative_to(root)
            or "," in str(directory)
        ):
            raise RunnerError("state_directory must be absolute and outside the control checkout")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.stat().st_mode & 0o077 or directory.stat().st_uid != os.getuid():
            raise RunnerError("state directory must be private and owned by the service user")
        with contextlib.closing(Store(directory / "orchestrator.sqlite")) as db:
            if args.action == "status":
                print(json.dumps(db.status(), indent=2))
                return 0
            if args.action in {"stop", "pause", "resume"}:
                previous = db.setting("control")
                db.set("control", {"stop": "stopped", "pause": "paused", "resume": "ready"}[args.action])
                if args.action == "resume" and previous == "stopped":
                    print("Ready for the next start.")
                return 0
            validate_root(root, config["repository"])
            repo = Repository(root, directory, config["repository"], Containers(root, config))
            repo.progress = db.record_progress
            if args.action == "plan":
                candidate = choose(repo, config, db.tasks())
                print(json.dumps({"next": list(candidate[:3]) if candidate else None}))
                return 0
            if args.action == "resolve":
                with lock(directory):
                    task = next((row for row in db.tasks() if row["issue"] == args.issue), None)
                    if task is None or task["phase"] != "blocked":
                        raise RunnerError("resolve requires a blocked issue number")
                    issue = repo.api(f"issues/{args.issue}")
                    open_pr = any(
                        pr["head"]["ref"] == task["branch"] for pr in repo.api("pulls?state=open&per_page=100")
                    )
                    if issue["state"] != "closed" or open_pr:
                        raise RunnerError("close the issue and reconcile any branch/PR before resolving")
                    db.transition(args.issue, "resolved", reason="operator reconciled GitHub artifacts")
                return 0
            if not args.execute or not config["execution_enabled"] or not IMAGE.fullmatch(config["image"]):
                raise RunnerError("execution requires an approved immutable image, configuration and start --execute")
            if os.getuid() == 0:
                raise RunnerError("run controller as a dedicated non-root user")
            if any(path.name != ".env.example" for path in root.glob(".env*")) or any(
                path.name != ".gitkeep"
                for folder in ("state", "research_registry")
                for path in (root / folder).glob("*")
            ):
                raise RunnerError("execution requires a dedicated clone without credentials, broker state or registry")
            with lock(directory):
                if db.setting("control") == "stopped":
                    return 0
                repo.containers.preflight(db.owner())
                return service(repo, db, config)
    except KeyboardInterrupt:
        print("Stopped at an interruption boundary. Check status before resuming.", file=sys.stderr)
        return 130
    except (RunnerError, ValueError, OSError, KeyError, TypeError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, RunnerError) else type(exc).__name__
        print(f"orchestrator: {message}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
