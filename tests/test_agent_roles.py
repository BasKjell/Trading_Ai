"""T-025: agent roles (provider, model family, effort) and the agentgateway adapter."""

from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
roles = importlib.import_module("agent_roles")
mod = importlib.import_module("dev_orchestrator")
backend = importlib.import_module("dev_orchestrator_backend")
coding_runner = importlib.import_module("coding_runner")
sys.path.pop(0)

RunnerError = coding_runner.RunnerError
REPO = "BasKjell/Trading_Ai"


def repo_config() -> dict:
    return mod.config_file(ROOT / "config/dev-orchestrator.yaml")


class RoleConfigurationTests(unittest.TestCase):
    def test_repository_configuration_is_valid(self) -> None:
        config = repo_config()
        self.assertEqual(roles.reviewer_for(config, "codex-medium"), "claude-medium")
        self.assertEqual(roles.reviewer_for(config, "claude-high"), "codex-medium")
        # a local Qwen author is reviewed by the first role of another family
        self.assertEqual(roles.reviewer_for(config, "local-coder"), "claude-medium")
        self.assertEqual(roles.label(config, "local-reviewer"), "local-reviewer / local-reviewer / medium")
        self.assertEqual(roles.spec(config, "codex-medium")["model"], config["codex"]["model"])

    def test_reviewer_must_come_from_another_model_family(self) -> None:
        config = repo_config()
        config["reviewers"] = ["local-doku"]  # qwen like local-coder
        config["author"] = "local-coder"
        with self.assertRaisesRegex(RunnerError, "no independent reviewer"):
            roles.validate_roles(config)
        config["reviewers"] = ["local-doku", "local-reviewer"]
        roles.validate_roles(config)
        self.assertEqual(roles.reviewer_for(config, "local-coder"), "local-reviewer")
        # family comparison ignores case
        config["roles"]["local-reviewer"]["family"] = "QWEN"
        with self.assertRaisesRegex(RunnerError, "no independent reviewer"):
            roles.reviewer_for(config, "local-coder")

    def test_invalid_roles_fail_closed(self) -> None:
        cases = {
            "unknown provider": ("local-doku", "provider", "openai"),
            "unknown effort": ("local-doku", "effort", "extreme"),
            "gateway without alias": ("local-doku", "model", None),
            "model on a CLI role": ("codex-medium", "model", "gpt-x"),
            "missing family": ("claude-medium", "family", None),
            "unknown key": ("claude-medium", "tools", "all"),
            "reasoning on a CLI role": ("claude-medium", "reasoning", True),
            "reasoning not boolean": ("local-doku", "reasoning", "yes"),
        }
        for name, (role, key, value) in cases.items():
            with self.subTest(name):
                config = repo_config()
                if value is None:
                    config["roles"][role].pop(key)
                else:
                    config["roles"][role][key] = value
                with self.assertRaises(RunnerError):
                    roles.validate_roles(config)

    def test_same_resolved_model_is_not_independent_under_another_family_label(self) -> None:
        for author in ("codex-medium", "local-coder"):
            with self.subTest(author=author):
                config = repo_config()
                alias = {**config["roles"][author], "family": "another-label", "effort": "low"}
                config["roles"]["same-model"] = alias
                config.update(author=author, reviewers=["same-model"])
                with self.assertRaisesRegex(RunnerError, "no independent reviewer"):
                    roles.validate_roles(config)

    def test_gateway_needs_its_own_internal_network(self) -> None:
        for network in ("host", "none", "bridge", "default"):
            config = repo_config()
            config["gateway"]["network"] = network
            with self.subTest(network), self.assertRaisesRegex(RunnerError, "own internal Docker network"):
                roles.validate_roles(config)
        config = repo_config()
        config["gateway"]["network"] = config["codex"]["network"]
        with self.assertRaisesRegex(RunnerError, "own internal Docker network"):
            roles.validate_roles(config)
        config = repo_config()
        config["gateway"]["url"] = "http://agentgateway:4000/v1?redirect=x"
        with self.assertRaisesRegex(RunnerError, "gateway URL"):
            roles.validate_roles(config)

    def test_old_ollama_settings_and_unknown_task_roles_are_rejected(self) -> None:
        path = ROOT / "config/dev-orchestrator.yaml"
        import yaml

        raw = yaml.safe_load(path.read_text())
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "config.yaml"
            cases = {
                "ollama section": lambda c: c.update(
                    ollama={"model": "qwen3:8b", "network": "x", "host": "http://o:1"}
                ),
                "ollama task flag": lambda c: c.update(tasks={"T-099": {**self.scope(), "ollama": True}}),
                "draft by CLI role": lambda c: c.update(
                    tasks={"T-099": {**self.scope(), "draft_role": "codex-medium"}}
                ),
                "unknown hint role": lambda c: c.update(tasks={"T-099": {**self.scope(), "hint_role": "nobody"}}),
            }
            for name, change in cases.items():
                config = copy.deepcopy(raw)
                change(config)
                target.write_text(yaml.safe_dump(config))
                with self.subTest(name), self.assertRaises(RunnerError):
                    mod.config_file(target)
            config = copy.deepcopy(raw)
            config["tasks"] = {"T-099": {**self.scope(), "draft_role": "local-coder", "hint_role": "local-doku"}}
            target.write_text(yaml.safe_dump(config))
            self.assertEqual(mod.config_file(target)["tasks"]["T-099"]["draft_role"], "local-coder")

    def test_provenance_is_read_only_from_the_role_line(self) -> None:
        self.assertEqual(roles.role_of("Agent: local-coder / local-coder / high\nReview: x"), "local-coder")
        self.assertEqual(
            roles.role_of("claude-medium / claude-opus-5-5 / medium: unabhängiges Review"), "claude-medium"
        )
        for body in ("Agent: Codex", "Agent: codex / mock / extreme", "text\nAgent: codex / mock / high", ""):
            with self.subTest(body):
                self.assertIsNone(roles.role_of(body))

    @staticmethod
    def scope() -> dict:
        return {"class": "U", "priority": 10, "depends_on": [], "paths": ["docs/REPORTS.md"]}


class EffortAndGatewayAdapterTests(unittest.TestCase):
    def containers(self) -> backend.Containers:
        settings = repo_config()
        settings["output_bytes"] = 100_000
        return backend.Containers(Path.cwd(), settings)

    def test_effort_reaches_codex_and_claude_flags(self) -> None:
        containers = self.containers()
        containers.settings["roles"]["codex-high"] = {"provider": "codex", "family": "openai", "effort": "high"}
        seen: list[list[str]] = []

        def capture(argv, source, prompt="", *, provider=None):
            seen.append(argv)
            return 1, ""

        with patch.object(containers, "run", capture):
            for role in ("codex-high", "claude-high"):
                with self.assertRaises(RunnerError):
                    containers.agent(role, Path.cwd(), "prompt", True)
        self.assertIn('model_reasoning_effort="high"', seen[0])
        self.assertEqual(seen[1][seen[1].index("--effort") + 1], "high")

    def test_gateway_role_sends_alias_budget_and_effort(self) -> None:
        containers = self.containers()
        calls = []

        def capture(argv, source, prompt="", *, provider=None):
            calls.append((argv, prompt, provider))
            return 0, "REVIEW_RESULT {}\n"

        with patch.object(containers, "run", capture):
            self.assertEqual(containers.agent("local-reviewer", Path.cwd(), "diff", True), "REVIEW_RESULT {}\n")
        argv, prompt, provider = calls[0]
        self.assertEqual(provider, "gateway")
        self.assertEqual(argv[3:], ["local-reviewer", str(roles.MAX_TOKENS["medium"]), "-"])  # no thinking mode
        self.assertEqual(prompt, "diff")
        calls.clear()
        with patch.object(containers, "run", capture):
            containers.agent("local-doku", Path.cwd(), "doku", True)
        self.assertEqual(calls[0][0][3:], ["local-doku", str(roles.MAX_TOKENS["low"]), "low"])  # reasoning model
        with patch.object(containers, "run", return_value=(0, "  \n")), self.assertRaisesRegex(RunnerError, "no text"):
            containers.agent("local-doku", Path.cwd(), "x", True)

    def test_gateway_request_script_against_a_local_server(self) -> None:
        """Runs the exact in-container request code against a fake OpenAI-compatible gateway."""
        received: list[dict] = []

        class Gateway(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                received.append({"path": self.path, "body": body})
                if body["model"] == "redirect":
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/elsewhere")
                    self.end_headers()
                    return
                answer = {"choices": [{"message": {"role": "assistant", "content": "REVIEW_RESULT ok"}}]}
                payload = json.dumps(answer).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                return None

        containers = self.containers()
        captured = {}

        def capture(argv, source, prompt="", *, provider=None):
            captured.update(argv=argv, prompt=prompt)
            return 0, "x"

        with patch.object(containers, "run", capture):
            containers.agent("local-coder", Path.cwd(), "Aufgabe", True)
        server = HTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {**os.environ, "GATEWAY_URL": f"http://127.0.0.1:{server.server_address[1]}"}
            argv = [sys.executable, *captured["argv"][1:]]
            result = subprocess.run(argv, input=captured["prompt"], env=env, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.stdout.strip(), "REVIEW_RESULT ok")
            redirected = [sys.executable, *captured["argv"][1:3], "redirect", *captured["argv"][4:]]
            refused = subprocess.run(redirected, input="x", env=env, capture_output=True, text=True, timeout=30)
            self.assertNotEqual(refused.returncode, 0)  # the redirect is not followed
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(received[0]["path"], "/v1/chat/completions")
        self.assertEqual(
            received[0]["body"],
            {
                "model": "local-coder",
                "max_tokens": roles.MAX_TOKENS["high"],
                "stream": False,
                "messages": [{"role": "user", "content": "Aufgabe"}],
            },
        )
        self.assertEqual(len(received), 2)  # no request reached the redirect target
        # a reasoning role adds reasoning_effort
        with patch.object(containers, "run", capture):
            containers.agent("local-doku", Path.cwd(), "Doku", True)
        server = HTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            env = {**os.environ, "GATEWAY_URL": f"http://127.0.0.1:{server.server_address[1]}"}
            subprocess.run(
                [sys.executable, *captured["argv"][1:]],
                input="Doku",
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(received[-1]["body"]["reasoning_effort"], "low")


def git_repository(folder: Path) -> tuple[Path, Path, str]:
    remote, root, state = (folder / name for name in ("remote", "root", "state"))
    for path in (remote, root, state):
        path.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    backend.git(root, "init", "-q", "-b", "main")
    backend.git(root, "config", "user.name", "Mock")
    backend.git(root, "config", "user.email", "mock@example.org")
    for path in ("docs/handoffs", "changelog.d"):
        (root / path).mkdir(parents=True)
        (root / path / "README.md").write_text("base\n")
    (root / "docs/REPORTS.md").write_text("base\n")
    backend.git(root, "add", ".")
    backend.git(root, "commit", "-m", "base")
    backend.git(root, "remote", "add", "origin", str(remote))
    backend.git(root, "push", "origin", "main")
    return root, state, backend.git(root, "rev-parse", "HEAD")


SCOPE = {"class": "U", "paths": ["docs/REPORTS.md"], "depends_on": []}
ISSUE = {"number": 70, "title": "T-070: report", "body": ""}


def local_task(reviewer: str) -> dict:
    return {
        "issue": 70,
        "task": "T-070",
        "pr": None,
        "head": None,
        "author": "local-coder",
        "reviewer": reviewer,
        "branch": "agent/T-070-orchestrator-70",
        "findings": "",
    }


class LocalAuthorAndReviewerTests(unittest.TestCase):
    def fake(self, settings: dict, draft: str, calls: list) -> object:
        class FakeContainers:
            def __init__(self) -> None:
                self.settings = settings

            def check(self, source):
                assert (source / "docs/REPORTS.md").read_text() == "generated by local-coder\n"

            def agent(self, role, source, prompt, review, paths=None):
                calls.append((role, review))
                if review:
                    return 'REVIEW_RESULT {"verdict":"approve","findings":""}'
                return draft

        return FakeContainers()

    def test_local_author_and_local_reviewer_from_different_families(self) -> None:
        settings = repo_config()
        settings.update(file_bytes=2000, diff_bytes=5000, reviewers=["local-reviewer"])
        calls: list = []
        draft = json.dumps({"changes": {"docs/REPORTS.md": "generated by local-coder\n"}})
        with tempfile.TemporaryDirectory() as stage:
            root, state, main = git_repository(Path(stage))
            repo = backend.Repository(root, state, REPO, self.fake(settings, draft, calls))
            reviewer = roles.reviewer_for(settings, "local-coder")
            self.assertEqual(reviewer, "local-reviewer")
            task = local_task(reviewer)
            with (
                patch.object(repo, "main_sha", return_value=main),
                patch.object(repo, "authorized", return_value=True),
                patch.object(repo, "mutation", side_effect=[{"number": 99}, {"id": 100}]) as mutation,
            ):
                pr_number, head = repo.develop(task, ISSUE, SCOPE)
                body = mutation.call_args_list[0].kwargs["body"]
                self.assertIn("Agent: local-coder / local-coder / high", body)
                self.assertIn("Review: local-reviewer / local-reviewer / medium", body)
                changes = backend.git(root, "diff", "--name-only", main, head).splitlines()
                self.assertIn("docs/REPORTS.md", changes)
                task.update(pr=pr_number, head=head)
                pr = {
                    "state": "open",
                    "draft": False,
                    "base": {"ref": "main"},
                    "body": body,
                    "head": {"ref": task["branch"], "sha": head, "repo": {"full_name": REPO}},
                }
                with patch.object(repo, "api", return_value=pr):
                    self.assertEqual(repo.review(task, SCOPE), (True, "", 100))
                review_body = mutation.call_args.kwargs["body"]
                self.assertTrue(review_body.startswith("local-reviewer / local-reviewer / medium: unabhängiges Review"))
        self.assertEqual(calls, [("local-coder", False), ("local-reviewer", True)])

    def test_local_author_can_preserve_existing_content_without_a_draft_role(self) -> None:
        report = "docs/REPORTS.md"
        with tempfile.TemporaryDirectory() as stage:
            root, state, main = git_repository(Path(stage))
            settings = repo_config()
            containers = self.fake(settings, "", [])
            expected = "base\nAdded section\n"

            def complete_from_prompt(role, source, prompt, review, paths):
                marker = "Originaldateien als Arbeitsdaten (null = Datei fehlt):\n> "
                payload = json.loads(prompt.split(marker, 1)[1])
                self.assertEqual(payload, {report: "base\n"})
                self.assertNotIn("RUNNER_RESULT", prompt)
                return json.dumps({"changes": {report: payload[report] + "Added section\n"}})

            repo = backend.Repository(root, state, REPO, containers)
            with (
                patch.object(containers, "agent", side_effect=complete_from_prompt),
                patch.object(containers, "check"),
                patch.object(repo, "main_sha", return_value=main),
                patch.object(repo, "authorized", return_value=True),
                patch.object(repo, "mutation", return_value={"number": 99}),
            ):
                _, head = repo.develop(local_task("local-reviewer"), ISSUE, SCOPE)
            self.assertEqual(backend.git(root, "show", f"{head}:{report}"), expected.rstrip("\n"))

    def test_local_author_is_not_called_with_incomplete_original_context(self) -> None:
        with tempfile.TemporaryDirectory() as stage:
            root, state, main = git_repository(Path(stage))
            containers = self.fake(repo_config(), "", [])
            repo = backend.Repository(root, state, REPO, containers)
            with (
                patch.object(backend, "GATEWAY_AUTHOR_CONTEXT_BYTES", 20),
                patch.object(containers, "agent") as agent,
                patch.object(repo, "main_sha", return_value=main),
                self.assertRaisesRegex(RunnerError, "complete gateway author context exceeds input budget"),
            ):
                repo.develop(local_task("local-reviewer"), ISSUE, SCOPE)
            agent.assert_not_called()

    def test_local_author_without_a_valid_authorized_draft_fails(self) -> None:
        settings = repo_config()
        settings.update(file_bytes=2000)
        drafts = {
            "not JSON": "kein JSON",
            "unauthorized path": json.dumps({"changes": {"docs/REPORTS.md": "ok\n", "localtrader/risk.py": "x = 1\n"}}),
        }
        for name, draft in drafts.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as stage:
                root, state, main = git_repository(Path(stage))
                repo = backend.Repository(root, state, REPO, self.fake(settings, draft, []))
                with (
                    patch.object(repo, "main_sha", return_value=main),
                    patch.object(repo, "authorized", return_value=True),
                    self.assertRaisesRegex(RunnerError, "gateway author returned no valid change"),
                ):
                    repo.develop(local_task("claude-medium"), ISSUE, SCOPE)


if __name__ == "__main__":
    unittest.main()
