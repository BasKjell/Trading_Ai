"""Import curated, source-bound requirements and report coverage gaps."""

import re
from typing import Any

from .store import get, put, required_text


def import_catalog(db: Any, catalog: dict[str, Any], actor: str) -> None:
    issues = catalog["issues"]
    numbers = [row["number"] for row in issues]
    if len(numbers) != len(set(numbers)) or len(numbers) != catalog["issue_count"]:
        raise ValueError("Issue count and unique source numbers must match")
    for issue in issues:
        required_text(issue, "url", "state", "source_revision", "disposition")
        number = issue["number"]
        entries = issue["requirements"]
        if not entries and issue["disposition"] == "mapped":
            raise ValueError(f"Issue #{number} is missing requirements")
        for entry in entries:
            record_requirement(db, entry, actor)
        put(
            db,
            "source",
            f"GH-{number}",
            {key: value for key, value in issue.items() if key != "requirements"}
            | {
                "requirements": [entry["id"] for entry in entries],
            },
            actor,
        )
    for entry in catalog["project_requirements"]:
        record_requirement(db, entry, actor)


def record_requirement(db: Any, entry: dict[str, Any], actor: str) -> None:
    required_text(
        entry, "id", "statement", "acceptance", "source", "source_revision", "risk", "owner", "type", "rationale"
    )
    if not re.fullmatch(r"REQ-[A-Z0-9-]+", entry["id"]) or entry["risk"] not in {"K", "U"}:
        raise ValueError("Invalid requirement ID or risk class")
    body = {
        key: entry[key]
        for key in ("statement", "acceptance", "source", "source_revision", "risk", "owner", "type", "rationale")
    }
    put(db, "requirement", entry["id"], body | {"status": "review_needed"}, actor)


def record_case(db: Any, entry: dict[str, Any], actor: str) -> None:
    required_text(
        entry, "id", "requirement_id", "preconditions", "test_data", "steps", "expected", "test_type", "procedure"
    )
    if not re.fullmatch(r"TC-[A-Z0-9-]+", entry["id"]) or type(entry.get("requirement_revision")) is not int:
        raise ValueError("Invalid test case ID or requirement revision")
    if get(db, entry["requirement_id"], entry["requirement_revision"])["kind"] != "requirement":
        raise ValueError("The reference must be a requirement version")
    put(
        db,
        "test_case",
        entry["id"],
        {
            key: entry[key]
            for key in (
                "requirement_id",
                "requirement_revision",
                "preconditions",
                "test_data",
                "steps",
                "expected",
                "test_type",
                "procedure",
            )
        },
        actor,
    )


def gaps(data: dict[str, Any]) -> dict[str, Any]:
    latest = {row["id"]: row for row in data["records"]}
    cases = [row for row in latest.values() if row["kind"] == "test_case"]
    verified = {
        (item["id"], item["revision"])
        for row in latest.values()
        if row["kind"] == "test_run"
        for item in row["cases"]
        if item["result"] == "passed"
    }
    sources = [row for row in latest.values() if row["kind"] == "source"]
    baselined = {tuple(ref) for snapshot in data["snapshots"] for ref in snapshot["references"]}
    requirements = [row for row in latest.values() if row["kind"] == "requirement"]
    return {
        "issues": len(sources),
        "issues_with_requirements": sum(bool(row["requirements"]) for row in sources),
        "open_source_questions": [row["id"] for row in sources if row["disposition"] != "mapped"],
        "requirements": len(requirements),
        "requirements_without_baseline": [
            row["id"] for row in requirements if (row["id"], row["revision"]) not in baselined
        ],
        "requirements_without_tests": [
            row["id"]
            for row in requirements
            if not any(
                case["requirement_id"] == row["id"] and case["requirement_revision"] == row["revision"]
                for case in cases
            )
        ],
        "cases_without_passing_evidence": [row["id"] for row in cases if (row["id"], row["revision"]) not in verified],
    }
