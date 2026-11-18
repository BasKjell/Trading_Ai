from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("check_links", REPO / "scripts" / "check_links.py")
assert _SPEC and _SPEC.loader
check_links = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(check_links)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=False)


class LinkCheckTests(unittest.TestCase):
    def test_detects_broken_relative_link_and_ignores_urls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.md").write_text("[ok](b.md) [bad](missing.md) [web](https://x.y) [anchor](#top)\n")
            (root / "b.md").write_text("x\n")
            problems = check_links.broken_links(root, [root / "a.md", root / "b.md"])
        self.assertEqual(len(problems), 1)
        self.assertIn("missing.md", problems[0])


class StartTaskScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir()
        _git(self.root, "init", "-q", "-b", "main")
        _git(self.root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        self.script = REPO / "scripts" / "start_task.sh"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script), *args], cwd=self.root, capture_output=True, text=True, check=False
        )

    def test_creates_branch_and_isolated_worktree(self) -> None:
        result = self.run_script("T-005", "order-state", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        worktree = Path(self.tmp.name) / "wt-T-005"
        self.assertTrue((worktree / ".tmp").is_dir())
        self.assertEqual(_git(worktree, "branch", "--show-current").stdout.strip(), "agent/T-005-order-state")

    def test_refuses_existing_branch(self) -> None:
        self.assertEqual(self.run_script("T-005", "order-state", "main").returncode, 0)
        second = self.run_script("T-005", "order-state", "main")
        self.assertEqual(second.returncode, 1)
        self.assertIn("already exists", second.stderr)

    def test_rejects_invalid_ids(self) -> None:
        self.assertEqual(self.run_script("5", "x", "main").returncode, 2)
        self.assertEqual(self.run_script("T-005", "Bad Name", "main").returncode, 2)


if __name__ == "__main__":
    unittest.main()
