from __future__ import annotations

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("coding_runner", Path(__file__).parents[1] / "scripts/coding_runner.py")
assert spec and spec.loader
runner = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)


def tags(*extra: str) -> list[dict[str, str]]:
    return [{"name": name} for name in ("auto-ready", "effort:medium", *extra)]


def pr(number: int = 1, owner: str = "Claude Code", task: str = "T-007") -> dict:
    return {
        "number": number,
        "title": f"{task}: example",
        "body": f"Agent: {owner}",
        "labels": tags(),
        "head": {"sha": "a" * 40, "ref": "agent/example", "repo": {"full_name": "BasKjell/Trading_Ai"}},
        "base": {"ref": "main"},
    }


def issue(number: int = 2) -> dict:
    return {"number": number, "title": "T-017: implementation", "labels": tags("work:coding")}


class FakeGitHub:
    repo = "BasKjell/Trading_Ai"

    def __init__(self, prs=None, issues=None, reviews=None):
        self.prs, self.issues, self.reviews = prs or [], issues or [], reviews or []
        self.comments: list[dict] = []
        self.after_post = None

    def api(self, endpoint, body=None):
        if "comments" in endpoint:
            if body is not None:
                self.comments.append({"id": len(self.comments) + 10, "body": body})
                if self.after_post:
                    self.after_post(self)
                return self.comments[-1]
            return self.comments
        if endpoint.startswith("pulls?"):
            return self.prs
        if "/reviews" in endpoint:
            return self.reviews
        if endpoint.startswith("issues?"):
            return self.issues
        raise AssertionError(endpoint)


class PlanningTests(unittest.TestCase):
    def test_priority_fix_review_implementation_and_provenance(self):
        fix = pr(owner="Codex")
        review = pr(3)
        change = {
            "id": 42,
            "commit_id": "a" * 40,
            "body": "Claude Code: unabhängiges Review\nEmpfehlung: ÄNDERUNGEN NÖTIG",
        }
        jobs = runner.plan(FakeGitHub([fix, review], [issue()], [change]), ["codex", "claude"])
        self.assertEqual([j.kind for j in jobs[:2]], ["fix", "review"])
        self.assertEqual(jobs[0].provider, "codex")
        self.assertEqual(jobs[1].provider, "codex")  # different actual agent, same GitHub login irrelevant

    def test_no_self_review_or_duplicate_review_of_same_head(self):
        review = {"commit_id": "a" * 40, "body": "Codex: unabhängiges Review\nEmpfehlung: FREIGEBEN"}
        self.assertEqual(runner.plan(FakeGitHub([pr()], reviews=[review]), ["codex", "claude"]), [])

    def test_old_review_does_not_schedule_fix_of_new_head(self):
        change = {"commit_id": "b" * 40, "body": "Codex: unabhängiges Review\nÄNDERUNGEN NÖTIG"}
        self.assertEqual(runner.plan(FakeGitHub([pr()], reviews=[change]), ["claude"]), [])

    def test_old_findings_quoted_in_approval_do_not_trigger_new_fix(self):
        approval = {
            "commit_id": "a" * 40,
            "body": "Codex: unabhängiges Review\nFrüher: Empfehlung: ÄNDERUNGEN NÖTIG\nEmpfehlung: FREIGEBEN",
        }
        self.assertEqual(runner.plan(FakeGitHub([pr()], reviews=[approval]), ["claude"]), [])

    def test_research_critical_forks_drafts_unknown_agents_are_skipped(self):
        fixtures = [pr(i) for i in range(6)]
        fixtures[0]["labels"] = tags("needs-independent-review")
        fixtures[1]["title"] = "T-005: orders"
        fixtures[2]["head"]["repo"]["full_name"] = "other/fork"
        fixtures[3]["draft"] = True
        fixtures[4]["body"] = "no agent metadata"
        fixtures[5]["labels"] = tags("research")
        self.assertEqual(runner.plan(FakeGitHub(fixtures), ["codex", "claude"]), [])

    def test_only_explicit_medium_coding_unclaimed_issues(self):
        rows = [issue(i) for i in range(5)]
        rows[0]["labels"] = tags()
        rows[1]["labels"] = tags("work:coding", "claimed")
        rows[2]["labels"] = tags("work:coding", "agent:claude")
        rows[3]["labels"] = tags("work:coding", "agent:claude", "agent:codex")
        rows[4]["labels"] = [{"name": "auto-ready"}, {"name": "work:coding"}]
        jobs = runner.plan(FakeGitHub(issues=rows), ["codex", "claude"])
        self.assertEqual([(j.number, j.provider) for j in jobs], [(2, "claude")])

    def test_owned_quota_job_resumes_after_claimed_label(self):
        row = issue()
        row["labels"] = tags("work:coding", "claimed", "in_progress")
        jobs = runner.plan(FakeGitHub(issues=[row]), ["codex", "claude"], {2: "codex"})
        self.assertEqual([j.provider for j in jobs], ["codex"])


class PersistenceTests(unittest.TestCase):
    def job(self):
        return runner.Job("implement", 2, "codex", "T-017", "agent/T-017-auto-codex", "", "main")

    def test_claim_race_across_independent_state_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            first, second = runner.State(Path(temp) / "a"), runner.State(Path(temp) / "b")
            gh = FakeGitHub()
            self.assertTrue(runner.claim(gh, first, self.job()))
            self.assertFalse(runner.claim(gh, second, replace(self.job(), provider="claude")))
            self.assertTrue(runner.claim(gh, first, self.job()))
            self.assertEqual(len(gh.comments), 1)

    def test_losing_claim_rechecks_after_post_and_never_expires_manual_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            state = runner.State(Path(temp))
            gh = FakeGitHub()
            prefix = f"{runner.CLAIM}key={self.job().key} "
            gh.after_post = lambda g: g.comments.insert(0, {"id": 1, "body": prefix + "owner=other -->"})
            self.assertFalse(runner.claim(gh, state, self.job()))
            gh.comments = [{"id": 1, "body": "claimed by human years ago"}]
            self.assertFalse(runner.claim(gh, state, self.job()))

    def test_quota_wait_is_persistent_and_never_switches_task_owner(self):
        with tempfile.TemporaryDirectory() as temp:
            state = runner.State(Path(temp))
            job = self.job()
            state.data["jobs"][job.key] = {"status": "rate_limited", "provider": "codex"}
            state.data["cooldown"]["codex"] = 500
            state.save()
            restored = runner.State(Path(temp))
            restored.load()
            self.assertFalse(restored.ready(job, 499))
            self.assertTrue(restored.ready(job, 501))
            self.assertFalse(restored.ready(replace(job, provider="claude"), 501))

    def test_crash_quarantines_workers_until_explicit_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            state = runner.State(Path(temp))
            state.data["jobs"]["abc"] = {"status": "running", "provider": "codex"}
            state.save()
            state.load()
            self.assertEqual(state.data["jobs"]["abc"]["status"], "blocked")
            self.assertEqual(set(state.data["paused"]), {"codex", "claude"})

    def test_process_lock_rejects_second_scheduler(self):
        with tempfile.TemporaryDirectory() as temp, runner.lock(Path(temp)):
            with self.assertRaises(runner.RunnerError), runner.lock(Path(temp)):
                pass

    def test_dry_run_never_claims_or_invokes_worker(self):
        with tempfile.TemporaryDirectory() as temp:
            state, gh = runner.State(Path(temp)), FakeGitHub(issues=[issue()])
            with patch.object(runner, "subscription_login") as login, patch.object(runner, "run_worker") as work:
                with patch("sys.stdout", new_callable=io.StringIO):
                    runner.cycle({"codex": {}, "claude": {}}, Path(temp), state, gh, dry_run=True)
                login.assert_not_called()
                work.assert_not_called()
                self.assertEqual(gh.comments, [])

    def test_unavailable_provider_does_not_reserve_new_task(self):
        with tempfile.TemporaryDirectory() as temp:
            state, gh = runner.State(Path(temp)), FakeGitHub(issues=[issue()])
            config = {"codex": {}, "claude": {}, "runner": {"timeout_seconds": 5, "cooldown_seconds": 100}}

            def login(provider, settings, root):
                if provider == "codex":
                    raise runner.RunnerError("not installed")

            with patch.object(runner, "subscription_login", side_effect=login):
                with patch.object(runner, "prepare", return_value=Path(temp)), patch.object(runner, "command"):
                    with patch.object(runner, "worker_command", return_value=[]):
                        with patch.object(runner, "run_worker", return_value="rate_limited"):
                            with patch("sys.stdout", new_callable=io.StringIO):
                                runner.cycle(config, Path(temp), state, gh, dry_run=False)
            self.assertIn("codex", state.data["paused"])
            self.assertEqual(next(iter(state.data["jobs"].values()))["provider"], "claude")
            self.assertGreater(state.data["cooldown"]["claude"], 0)


class VerificationTests(unittest.TestCase):
    def test_forged_success_with_no_pr_is_not_complete(self):
        job = runner.Job("implement", 2, "codex", "T-017", "agent/T-017-auto-codex", "", "main")
        with patch.object(runner, "command", side_effect=["", "checks passed", "a" * 40]):
            self.assertFalse(runner.verify(FakeGitHub(), job, Path("/tmp")))

    def test_dirty_worktree_is_preserved_and_not_marked_complete(self):
        with patch.object(runner, "command", return_value=" M code.py") as cmd:
            job = runner.Job("implement", 2, "codex", "T-017", "agent/task", "", "main")
            self.assertFalse(runner.verify(FakeGitHub(), job, Path("/tmp")))
            self.assertEqual(cmd.call_count, 1)

    def test_failed_or_pending_ci_never_completes_implementation(self):
        job = runner.Job("implement", 2, "codex", "T-017", "agent/example", "", "main")
        for conclusion in (None, "failure", "cancelled"):
            gh = FakeGitHub([pr(owner="Codex")])
            original = gh.api

            def api(endpoint, body=None, conclusion=conclusion, original=original):
                if "check-runs" in endpoint:
                    return {"check_runs": [{"conclusion": conclusion}]}
                if endpoint.endswith("/status"):
                    return {"state": "pending", "total_count": 0}
                return original(endpoint, body)

            gh.api = api
            with self.subTest(conclusion=conclusion):
                with patch.object(runner, "command", side_effect=["", "checks passed", "a" * 40]):
                    self.assertFalse(runner.verify(gh, job, Path("/tmp")))


class WorktreeTests(unittest.TestCase):
    def test_real_git_worktrees_are_isolated_and_existing_work_is_not_reset(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            origin, control, state = base / "origin", base / "control", base / "state"

            def git(*args):
                return subprocess.run(["git", *args], cwd=base, capture_output=True, text=True, check=True).stdout

            git("init", "--bare", str(origin))
            git("clone", str(origin), str(control))
            git("-C", str(control), "checkout", "-b", "main")
            (control / "fixture.txt").write_text("base")
            git("-C", str(control), "add", "fixture.txt")
            git(
                "-C",
                str(control),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-m",
                "test fixture",
            )
            git("-C", str(control), "push", "origin", "main")
            state.mkdir()
            job = runner.Job("implement", 2, "codex", "T-017", "agent/T-017-auto-codex", "", "main")
            path = runner.prepare(control, state, job, {})
            (path / "fixture.txt").write_text("unfinished work")
            resumed = runner.prepare(control, state, job, {"worktree": str(path)})
            self.assertEqual((resumed / "fixture.txt").read_text(), "unfinished work")
            self.assertEqual((control / "fixture.txt").read_text(), "base")
            with self.assertRaises(runner.RunnerError):
                runner.prepare(control, state, job, {})  # unmanaged existing directory, never reset


class WorkerTests(unittest.TestCase):
    def test_output_requires_structured_final_answer_not_tool_output(self):
        self.assertEqual(
            runner.outcome(
                json.dumps(
                    {
                        "type": "item.completed",
                        "item": {"type": "command_execution", "text": 'RUNNER_RESULT {"status":"done"}'},
                    }
                ),
                0,
            ),
            "blocked",
        )
        for event in (
            {"type": "result", "result": 'RUNNER_RESULT {"status":"done"}'},
            {"type": "item.completed", "item": {"type": "agent_message", "text": 'RUNNER_RESULT {"status":"done"}'}},
        ):
            self.assertEqual(runner.outcome(json.dumps(event), 0), "verify")

    def test_quota_error_even_with_zero_exit_code_and_not_generic_errors(self):
        event = {"type": "result", "is_error": True, "error": {"code": "rate_limit_error"}}
        self.assertEqual(runner.outcome(json.dumps(event), 0), "rate_limited")
        claude = {"type": "result", "is_error": True, "result": "You've hit your limit · resets later"}
        self.assertEqual(runner.outcome(json.dumps(claude), 0), "rate_limited")
        self.assertEqual(runner.outcome('{"type":"error","message":"missing authentication"}', 1), "blocked")
        self.assertEqual(runner.outcome('{"type":"result","result":"test rate limit handling"}', 0), "blocked")

    def test_pretty_printed_terminal_json_is_supported(self):
        event = {"type": "result", "result": 'RUNNER_RESULT {"status":"done"}'}
        self.assertEqual(runner.outcome(json.dumps(event, indent=2), 0), "verify")

    def test_medium_flags_no_permission_bypass_and_no_api_keys(self):
        for provider in runner.FAMILIES:
            cmd = runner.worker_command(provider, {"binary": provider, "model": "fixed-model"})
            self.assertIn("fixed-model", cmd)
            self.assertIn("medium", " ".join(cmd))
            self.assertNotIn("--dangerously-skip-permissions", cmd)
            self.assertNotIn("--yolo", cmd)
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sensitive", "PAPER_BROKER_API_KEY": "sensitive"}):
            env = runner.clean_environment()
            self.assertNotIn("OPENAI_API_KEY", env)
            self.assertNotIn("PAPER_BROKER_API_KEY", env)

    def test_fake_worker_completion_timeout_and_output_cap(self):
        with tempfile.TemporaryDirectory() as temp:
            payload = json.dumps({"type": "result", "result": 'RUNNER_RESULT {"status":"done"}'})
            code = "import sys; sys.stdin.read(); print(" + repr(payload) + ")"
            self.assertEqual(runner.run_worker([sys.executable, "-c", code], Path(temp), "prompt", 5), "verify")
            self.assertEqual(
                runner.run_worker([sys.executable, "-c", "import time; time.sleep(30)"], Path(temp), "prompt", 0),
                "blocked",
            )
            self.assertEqual(
                runner.run_worker([sys.executable, "-c", "print('x'*10000)"], Path(temp), "prompt", 5, max_output=50),
                "blocked",
            )


if __name__ == "__main__":
    unittest.main()
