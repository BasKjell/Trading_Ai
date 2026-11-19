"""Read GitHub evidence under a policy held by the local operator."""

import hashlib
import io
import json
import os
import re
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any
from zipfile import BadZipFile, ZipFile

from .store import digest

REPO = "BasKjell/Trading_Ai"
MAX_EVIDENCE = 20 * 1024 * 1024
Read = Callable[[str], bytes]


def policy_at(path: Path) -> dict[str, Any]:
    info = path.lstat()
    parent = path.parent.stat()
    if (
        path.is_symlink()
        or not path.is_file()
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise ValueError("GitHub policy must be in an operator-owned private directory")
    return json.loads(path.read_bytes())


def github_read(endpoint: str) -> bytes:
    try:
        return subprocess.run(
            ["gh", "api", "--hostname", "github.com", f"repos/{REPO}/{endpoint}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise ValueError("GitHub evidence could not be read by the operator's gh client") from None


def number(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("Positive numeric GitHub ID required")
    return value


def junit_outcomes(archive: bytes) -> dict[str, bool]:
    if len(archive) > MAX_EVIDENCE:
        raise ValueError("CI evidence archive exceeds size limit")
    try:
        with ZipFile(io.BytesIO(archive)) as zipped:
            files = [item for item in zipped.infolist() if item.filename == "junit.xml"]
            if len(files) != 1 or files[0].file_size > MAX_EVIDENCE:
                raise ValueError("CI archive must contain one bounded junit.xml")
            report = zipped.read(files[0])
        if re.search(rb"<!\s*(?:DOCTYPE|ENTITY)", report, re.IGNORECASE) or b"\x00" in report:
            raise ValueError("CI evidence contains an unsupported XML declaration")
        root = ET.fromstring(report)  # noqa: S314 -- bounded XML without DTD/entities
    except (BadZipFile, ET.ParseError, RuntimeError):
        raise ValueError("Invalid CI evidence archive") from None
    if root.tag not in {"testsuite", "testsuites"}:
        raise ValueError("Expected JUnit test results")
    results = {}
    for case in root.iter("testcase"):
        node = f"{case.attrib['classname']}::{case.attrib['name']}"
        if node in results:
            raise ValueError("CI evidence has duplicate test names")
        results[node] = all(case.find(tag) is None for tag in ("failure", "error", "skipped"))
    return results


def _bound_approval(review: dict[str, Any], binding: dict[str, Any], purpose: str) -> bool:
    if review["state"] != "APPROVED" or review["commit_id"] != binding["head"]:
        return False
    try:
        body = json.loads(review.get("body") or "")
    except ValueError:
        return False
    return body == binding | {"purpose": purpose} and review["author_association"] in {
        "OWNER",
        "MEMBER",
        "COLLABORATOR",
    }


def _check_ci_paths(
    fetch: Callable[[str], Any], pr: dict[str, Any], protected: set[str], qa_dirs: set[PurePosixPath]
) -> None:
    count = pr["changed_files"]
    if type(count) is not int or not 0 <= count <= 3000:
        raise ValueError("Incomplete GitHub changed-file inventory")
    pinned = {path.casefold() for path in protected}
    seen = set()
    for offset in range(0, count, 100):
        page = offset // 100 + 1
        rows = fetch(f"pulls/{pr['number']}/files?per_page=100&page={page}")
        if not isinstance(rows, list) or len(rows) != min(100, count - offset):
            raise ValueError("Incomplete GitHub changed-file inventory")
        for row in rows:
            current = row["filename"]
            if current in seen:
                raise ValueError("Incomplete GitHub changed-file inventory")
            seen.add(current)
            for name in (current, row.get("previous_filename", current)):
                path = PurePosixPath(name.casefold())
                if (
                    path.as_posix() in pinned
                    or path.parts[:1] == (".github",)
                    or any(parent in qa_dirs for parent in path.parents)
                    or path.name
                    in {
                        "conftest.py",
                        "pytest.ini",
                        ".pytest.ini",
                        "tox.ini",
                        "setup.cfg",
                        "pyproject.toml",
                        "setup.py",
                        "sitecustomize.py",
                        "usercustomize.py",
                    }
                    or path.suffix == ".pth"
                    or (path.name.startswith("requirements") and path.suffix in {".txt", ".in"})
                ):
                    raise ValueError(f"PR changes a protected CI or QA path: {name}")


def verify_github(
    policy: dict[str, Any],
    request: dict[str, Any],
    risk: str,
    case_refs: set[str],
    read: Read = github_read,
) -> dict[str, Any]:
    """Resolve the current PR, CI evidence and authenticated approvals without writing to GitHub."""
    pr_id, run_id = number(request["pr"]), number(request["ci_run"])
    if request["target"] != policy["target"] or risk not in {"K", "U"} or not case_refs:
        raise ValueError("Unconfigured target, risk class or baseline test cases")

    def fetch(endpoint: str) -> Any:
        return json.loads(read(endpoint))

    if risk == "U" and policy.get("uncritical_pr_heads", {}).get(str(pr_id)) != request["head"]:
        risk = "K"
    pr = fetch(f"pulls/{pr_id}")
    base_sha = pr["base"]["sha"]
    author = str(number(pr["user"]["id"]))
    roles = policy["review_families"]
    author_family = roles.get(author)
    if (
        pr["number"] != pr_id
        or pr["state"] != "open"
        or pr["draft"]
        or pr["head"]["sha"] != request["head"]
        or pr["base"]["ref"] != request["target"]
        or pr["base"]["repo"]["full_name"] != REPO
        or pr["head"]["repo"]["full_name"] != REPO
        or author_family is None
    ):
        raise ValueError("PR is not at the authorized head, target or identity")

    run = fetch(f"actions/runs/{run_id}")
    if (
        run["id"] != run_id
        or run["workflow_id"] != policy["workflow_id"]
        or run["path"].split("@", 1)[0] != policy["workflow_path"]
        or run["event"] != "pull_request"
        or run["status"] != "completed"
        or run["conclusion"] != "success"
        or run["head_sha"] != request["head"]
        or run["head_repository"]["full_name"] != REPO
        or run["repository"]["full_name"] != REPO
        or not any(
            row["number"] == pr_id
            and row["head"]["sha"] == request["head"]
            and row["base"]["ref"] == request["target"]
            and row["base"]["sha"] == base_sha
            for row in run["pull_requests"]
        )
    ):
        raise ValueError("CI run is not successful on this PR head and base")

    pins = policy["protected_blobs"]
    if not {policy["workflow_path"], "Makefile", "requirements-dev.txt", "pyproject.toml"} <= pins.keys():
        raise ValueError("Policy must pin the workflow, Makefile, pyproject.toml and dev dependency list")
    selected = policy["cases"]
    if not case_refs <= selected.keys():
        raise ValueError("Baseline cases must have protected CI test identifiers")
    names = set()
    qa_dirs = set()
    for ref in case_refs:
        case = selected[ref]
        if case["file"] not in pins or case["junit"] in names:
            raise ValueError("Every case needs a distinct CI test in a protected file")
        names.add(case["junit"])
        qa_dirs.add(PurePosixPath(case["file"].casefold()).parent)
    for path, sha in pins.items():
        if not re.fullmatch(r"(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+", path) or {"..", "."} & set(path.split("/")):
            raise ValueError("Invalid protected path")
        if not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid protected blob ID")
        for ref in (base_sha, request["head"]):
            obj = fetch(f"contents/{path}?ref={ref}")
            if obj["type"] != "file" or obj["sha"] != sha:
                raise ValueError(f"Protected CI file differs: {path}")

    _check_ci_paths(fetch, pr, set(pins), qa_dirs)

    artifact_list = fetch(f"actions/runs/{run_id}/artifacts?per_page=100")
    if artifact_list["total_count"] != len(artifact_list["artifacts"]):
        raise ValueError("Incomplete CI artifact inventory")
    archives = [
        item
        for item in artifact_list["artifacts"]
        if item["name"] == "test-reports" and not item["expired"] and item["created_at"] >= run["run_started_at"]
    ]
    if len(archives) != 1 or archives[0]["size_in_bytes"] > MAX_EVIDENCE:
        raise ValueError("Exactly one current CI test artifact is required")
    artifact = archives[0]
    archive = read(f"actions/artifacts/{number(artifact['id'])}/zip")
    if artifact.get("digest") != "sha256:" + hashlib.sha256(archive).hexdigest():
        raise ValueError("CI artifact digest mismatch")
    outcomes = junit_outcomes(archive)
    if any(outcomes.get(test) is not True for test in names):
        raise ValueError("A baseline test did not pass in GitHub CI")

    binding = {k: request[k] for k in ("baseline", "head", "target", "ci_run")} | {
        "base_sha": base_sha,
        "run_attempt": run["run_attempt"],
        "risk": risk,
    }
    latest = {}
    for page in range(1, 11):
        batch = fetch(f"pulls/{pr_id}/reviews?per_page=100&page={page}")
        for review in batch:
            uid = str(number(review["user"]["id"]))
            if uid not in roles or review["state"] not in {"APPROVED", "CHANGES_REQUESTED", "DISMISSED"}:
                continue
            latest[uid] = review
        if len(batch) < 100:
            break
    else:
        raise ValueError("Incomplete GitHub review inventory")
    if any(review["state"] == "CHANGES_REQUESTED" for review in latest.values()):
        raise ValueError("GitHub review has requested changes")
    independent = [
        review
        for uid, review in latest.items()
        if uid != author
        and roles[uid].casefold() != author_family.casefold()
        and _bound_approval(review, binding, "independent_review")
    ]
    if not independent:
        raise ValueError("Independent GitHub approval is missing")
    final = latest.get(str(policy["owner_id"]))
    if risk == "K" and (final is None or not _bound_approval(final, binding, "final_integration")):
        raise ValueError("Separate owner integration approval is missing")
    current = fetch(f"pulls/{pr_id}")
    if (
        current["state"] != "open"
        or current["draft"]
        or current["head"]["sha"] != request["head"]
        or current["base"]["sha"] != base_sha
        or current["base"]["ref"] != request["target"]
        or current["changed_files"] != pr["changed_files"]
    ):
        raise ValueError("PR changed during verification")

    return {
        **binding,
        "pr": pr_id,
        "artifact_id": artifact["id"],
        "artifact_digest": artifact["digest"],
        "review_id": independent[-1]["id"],
        "final_review_id": final["id"] if risk == "K" and final is not None else None,
        "policy_digest": digest(policy),
        "cases": [{"reference": ref, "junit": selected[ref]["junit"], "result": "passed"} for ref in sorted(case_refs)],
    }
