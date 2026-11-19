import hashlib
import io
import json
from zipfile import ZipFile

import pytest

from localtrader.demo_jama.github_evidence import REPO, verify_github

HEAD, BASE, BLOB = "a" * 40, "b" * 40, "c" * 40
CASE = "TC-1@1"


def archive(status):
    buffer = io.BytesIO()
    with ZipFile(buffer, "w") as zip_file:
        zip_file.writestr(
            "junit.xml",
            '<testsuite><testcase classname="tests.acceptance" name="test_gate">' + status + "</testcase></testsuite>",
        )
    return buffer.getvalue()


def fixture(risk="K", status=""):
    request = {"baseline": "BL-1", "head": HEAD, "target": "main", "pr": 2, "ci_run": 3}
    workflow = ".github/workflows/ci.yml"
    test_file = "tests/acceptance.py"
    pins = dict.fromkeys([workflow, "Makefile", "requirements-dev.txt", "pyproject.toml", test_file], BLOB)
    policy = {
        "target": "main",
        "workflow_path": workflow,
        "workflow_id": 1,
        "protected_blobs": pins,
        "cases": {CASE: {"file": test_file, "junit": "tests.acceptance::test_gate"}},
        "review_families": {"101": "Codex", "102": "Claude", "103": "Human"},
        "owner_id": 103,
        "uncritical_pr_heads": {"2": HEAD} if risk == "U" else {},
    }
    binding = {k: v for k, v in request.items() if k != "pr"} | {
        "risk": risk,
        "run_attempt": 1,
        "base_sha": BASE,
    }
    wire = archive(status)
    repo = {"full_name": REPO}
    now = "2026-11-19T14:00:00Z"
    reviews = [
        {
            "user": {"id": uid},
            "id": uid + 10,
            "state": "APPROVED",
            "commit_id": HEAD,
            "author_association": "MEMBER",
            "body": json.dumps(binding | {"purpose": purpose}),
        }
        for uid, purpose in ((102, "independent_review"), (103, "final_integration"))
    ]
    responses = {
        "pulls/2": {
            "number": 2,
            "state": "open",
            "draft": False,
            "head": {"sha": HEAD, "repo": repo},
            "base": {"ref": "main", "sha": BASE, "repo": repo},
            "user": {"id": 101},
            "changed_files": 1,
        },
        "pulls/2/files?per_page=100&page=1": [{"filename": "localtrader/example.py"}],
        "actions/runs/3": {
            "id": 3,
            "workflow_id": 1,
            "path": workflow,
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "head_sha": HEAD,
            "head_repository": repo,
            "repository": repo,
            "pull_requests": [{"number": 2, "head": {"sha": HEAD}, "base": {"ref": "main", "sha": BASE}}],
            "run_attempt": 1,
            "run_started_at": now,
        },
        "actions/runs/3/artifacts?per_page=100": {
            "total_count": 1,
            "artifacts": [
                {
                    "name": "test-reports",
                    "id": 4,
                    "expired": False,
                    "created_at": now,
                    "size_in_bytes": len(wire),
                    "digest": "sha256:" + hashlib.sha256(wire).hexdigest(),
                }
            ],
        },
        "pulls/2/reviews?per_page=100&page=1": reviews,
    }
    responses.update(
        {f"contents/{path}?ref={ref}": {"type": "file", "sha": BLOB} for path in pins for ref in (HEAD, BASE)}
    )

    def read(endpoint):
        if endpoint == "actions/artifacts/4/zip":
            return wire
        return json.dumps(responses[endpoint]).encode()

    return policy, request, responses, reviews, read


@pytest.mark.parametrize("risk", ["U", "K"])
def test_authoritative_integration(risk):
    policy, request, _, reviews, read = fixture(risk)
    if risk == "U":
        reviews.pop()
    result = verify_github(policy, request, risk, {CASE}, read)
    assert result["review_id"] == 112
    assert result["final_review_id"] == (113 if risk == "K" else None)


def test_owner_approval_required_and_drafts_do_not_count():
    policy, request, _, reviews, read = fixture()
    reviews[-1]["state"] = "COMMENTED"
    with pytest.raises(ValueError, match="owner integration"):
        verify_github(policy, request, "K", {CASE}, read)


def test_new_head_or_base_needs_current_evidence():
    policy, request, responses, _, read = fixture()
    base = responses["pulls/2"]["base"]
    base["sha"] = "e" * 40
    with pytest.raises(ValueError, match="PR head and base"):
        verify_github(policy, request, "K", {CASE}, read)
    base["sha"] = BASE
    responses["pulls/2"]["head"]["sha"] = "e" * 40
    with pytest.raises(ValueError, match="authorized head"):
        verify_github(policy, request, "K", {CASE}, read)


@pytest.mark.parametrize("status", ["<skipped/>", "<failure/>"])
def test_artifact_must_attest_passing_baseline_cases(status):
    policy, request, _, _, read = fixture(status=status)
    with pytest.raises(ValueError, match="did not pass"):
        verify_github(policy, request, "K", {CASE}, read)


def test_protected_test_and_review_identity():
    policy, request, responses, reviews, read = fixture()
    protected = responses[f"contents/tests/acceptance.py?ref={HEAD}"]
    protected["sha"] = "d" * 40
    with pytest.raises(ValueError, match="Protected CI file differs"):
        verify_github(policy, request, "K", {CASE}, read)
    protected["sha"] = BLOB
    reviews[0]["user"]["id"] = 101
    with pytest.raises(ValueError, match="Independent GitHub approval"):
        verify_github(policy, request, "K", {CASE}, read)


def test_archive_digest_and_new_review_block_decision():
    policy, request, responses, reviews, read = fixture()
    artifacts = responses["actions/runs/3/artifacts?per_page=100"]["artifacts"]
    original = artifacts[0]["digest"]
    artifacts[0]["digest"] = "sha256:" + "f" * 64
    with pytest.raises(ValueError, match="artifact digest"):
        verify_github(policy, request, "K", {CASE}, read)
    artifacts[0]["digest"] = original
    reviews.append(reviews[0] | {"state": "CHANGES_REQUESTED"})
    with pytest.raises(ValueError, match="requested changes"):
        verify_github(policy, request, "K", {CASE}, read)


def test_operator_policy_requires_private_regular_file(tmp_path):
    from localtrader.demo_jama.github_evidence import policy_at

    private = tmp_path / "operator"
    private.mkdir(mode=0o700)
    path = private / "policy.json"
    path.write_text("{}")
    path.chmod(0o644)
    with pytest.raises(ValueError, match="private directory"):
        policy_at(path)
    path.chmod(0o600)
    assert policy_at(path) == {}
    alias = private / "alias.json"
    alias.symlink_to(path)
    with pytest.raises(ValueError, match="private directory"):
        policy_at(alias)


@pytest.mark.parametrize(
    "row",
    [
        {"filename": "localtrader/conftest.py", "status": "added"},
        {"filename": "pytest.ini", "status": "added"},
        {"filename": "tests/helper.py", "status": "added"},
        {"filename": ".github/actions/new/action.yml", "status": "added"},
        {"filename": "ordinary.py", "previous_filename": "sitecustomize.py", "status": "renamed"},
    ],
)
def test_new_or_moved_ci_influence_is_rejected(row):
    policy, request, responses, _, read = fixture()
    responses["pulls/2/files?per_page=100&page=1"] = [row]
    with pytest.raises(ValueError, match="protected CI or QA path"):
        verify_github(policy, request, "K", {CASE}, read)


def test_changed_files_are_checked_across_pages_and_must_be_complete():
    policy, request, responses, _, read = fixture()
    responses["pulls/2"]["changed_files"] = 101
    responses["pulls/2/files?per_page=100&page=1"] = [{"filename": f"src/{i}.py"} for i in range(100)]
    second = "pulls/2/files?per_page=100&page=2"
    responses[second] = [{"filename": "conftest.py"}]
    with pytest.raises(ValueError, match="protected CI or QA path"):
        verify_github(policy, request, "K", {CASE}, read)
    responses[second] = []
    with pytest.raises(ValueError, match="Incomplete GitHub"):
        verify_github(policy, request, "K", {CASE}, read)


def test_only_latest_owner_review_controls_integration():
    policy, request, _, reviews, read = fixture()
    final = reviews.pop()
    changes = final | {"id": 201, "state": "CHANGES_REQUESTED"}
    reviews.extend((changes, final | {"id": 202}))
    assert verify_github(policy, request, "K", {CASE}, read)["final_review_id"] == 202
    reviews.append(changes | {"id": 203})
    with pytest.raises(ValueError, match="requested changes"):
        verify_github(policy, request, "K", {CASE}, read)
