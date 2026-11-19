"""Versioned SQLite register. Only the trusted local operator can open it for writing."""

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS versions (
    id TEXT NOT NULL, revision INTEGER NOT NULL, kind TEXT NOT NULL,
    body TEXT NOT NULL, PRIMARY KEY (id, revision)
);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, actor TEXT NOT NULL,
    action TEXT NOT NULL, object TEXT NOT NULL, previous TEXT NOT NULL, digest TEXT NOT NULL
);
"""
HEAD_LENGTH = 40


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def required_text(value: dict[str, Any], *fields: str) -> None:
    for field in fields:
        if not isinstance(value.get(field), str) or not value[field].strip():
            raise ValueError(f"{field} must be non-empty text")


@contextmanager
def connect(path: Path, *, write: bool = False) -> Iterator[sqlite3.Connection]:
    if write:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = path.parent.stat()
        if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
            raise ValueError("Use an operator-owned private directory (0700)")
        flags = os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        os.close(fd)
        info = path.stat()
        if info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("The register must be owned by the operator with mode 0600")
    database = sqlite3.connect(path.resolve().as_uri() + ("?mode=rw" if write else "?mode=ro"), uri=True)
    database.row_factory = sqlite3.Row
    try:
        if write:
            database.executescript(SCHEMA)
            database.execute("BEGIN IMMEDIATE")
        else:
            database.execute("BEGIN")
        with database:
            yield database
    finally:
        database.close()


def audit(db: sqlite3.Connection, actor: str, action: str, object_ref: str) -> None:
    last = db.execute("SELECT seq, digest FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    seq, previous = (last["seq"] + 1, last["digest"]) if last else (1, "")
    fields = (seq, datetime.now(UTC).isoformat(), actor, action, object_ref, previous)
    db.execute("INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?)", (*fields, digest(fields)))


def put(db: sqlite3.Connection, kind: str, identifier: str, body: dict[str, Any], actor: str) -> int:
    row = db.execute(
        "SELECT revision, kind, body FROM versions WHERE id = ? ORDER BY revision DESC LIMIT 1",
        (identifier,),
    ).fetchone()
    if row and row["kind"] != kind:
        raise ValueError(f"ID belongs to a different type: {identifier}")
    encoded = encode(body)
    if row and row["body"] == encoded:
        return int(row["revision"])
    rev = row["revision"] + 1 if row else 1
    db.execute("INSERT INTO versions VALUES (?, ?, ?, ?)", (identifier, rev, kind, encoded))
    audit(db, actor, f"record_{kind}", f"{identifier}@{rev}:{digest(body)}")
    return int(rev)


def get(db: sqlite3.Connection, identifier: str, revision: int | None = None) -> dict[str, Any]:
    sql = "SELECT * FROM versions WHERE id = ?"
    row = (
        db.execute(sql + " AND revision = ?", (identifier, revision)).fetchone()
        if revision is not None
        else db.execute(sql + " ORDER BY revision DESC LIMIT 1", (identifier,)).fetchone()
    )
    if not row:
        raise ValueError(f"Unknown record: {identifier}")
    return {"id": row["id"], "revision": row["revision"], "kind": row["kind"], **json.loads(row["body"])}


def export(db: sqlite3.Connection) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "records": [
            {"id": row["id"], "revision": row["revision"], "kind": row["kind"], **json.loads(row["body"])}
            for row in db.execute("SELECT * FROM versions ORDER BY id, revision")
        ],
        "snapshots": [
            {"id": row["id"], **json.loads(row["body"])} for row in db.execute("SELECT * FROM snapshots ORDER BY id")
        ],
        "audit": [dict(row) for row in db.execute("SELECT * FROM audit ORDER BY seq")],
    }


def verify(db: sqlite3.Connection) -> None:
    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise ValueError("SQLite integrity check failed")
    previous = ""
    recorded = set()
    for expected_seq, row in enumerate(db.execute("SELECT * FROM audit ORDER BY seq"), 1):
        fields = tuple(row)[:-1]
        if row["seq"] != expected_seq or row["previous"] != previous or digest(fields) != row["digest"]:
            raise ValueError("Broken audit chain")
        if row["action"].startswith("record_"):
            recorded.add(row["object"])
        previous = row["digest"]
    versions = {
        f"{row['id']}@{row['revision']}:{digest(json.loads(row['body']))}"
        for row in db.execute("SELECT * FROM versions")
    }
    if versions != recorded:
        raise ValueError("Missing record provenance or archived version")
    for row in db.execute("SELECT * FROM snapshots"):
        if (
            row["id"] != "BL-" + digest(json.loads(row["body"]))[:24]
            or not db.execute("SELECT 1 FROM audit WHERE action = 'baseline' AND object = ?", (row["id"],)).fetchone()
        ):
            raise ValueError("Missing baseline provenance")


def baseline(db: sqlite3.Connection, references: list[tuple[str, int]], actor: str) -> str:
    if not references:
        raise ValueError("Baseline must contain approved requirement versions")
    if len({identifier for identifier, _ in references}) != len(references):
        raise ValueError("Select one revision per record")
    records = [get(db, identifier, revision) for identifier, revision in sorted(references)]
    if any(item["kind"] not in {"requirement", "test_case"} for item in records):
        raise ValueError("A baseline can contain only requirements and test cases")
    required = {(item["id"], item["revision"]) for item in records if item["kind"] == "requirement"}
    if not required or any(
        (item["requirement_id"], item["requirement_revision"]) not in required
        for item in records
        if item["kind"] == "test_case"
    ):
        raise ValueError("Test cases must refer to requirements in the baseline")
    body = {"references": sorted(references)}
    identifier = "BL-" + digest(body)[:24]
    cursor = db.execute("INSERT OR IGNORE INTO snapshots VALUES (?, ?)", (identifier, encode(body)))
    if cursor.rowcount:
        audit(db, actor, "baseline", identifier)
    return identifier


def decision(db: sqlite3.Connection, body: dict[str, Any], actor: str) -> str:
    required_text(body, "baseline", "head", "target", "evidence_id", "review_id", "stage", "reason")
    head = body["head"]
    if len(head) != HEAD_LENGTH or any(char not in "0123456789abcdef" for char in head):
        raise ValueError("A complete lowercase Git commit SHA is required")
    if body["stage"] not in {"integration", "release", "operations"}:
        raise ValueError("Unknown decision stage")
    if body["stage"] != "integration":
        required_text(body, "artifact_digest")
    snapshot = db.execute("SELECT body FROM snapshots WHERE id = ?", (body["baseline"],)).fetchone()
    if not snapshot:
        raise ValueError("Unknown baseline")
    evidence = get(db, body["evidence_id"], body["evidence_revision"])
    review = get(db, body["review_id"], body["review_revision"])
    for item, kind in ((evidence, "test_run"), (review, "review")):
        if item["kind"] != kind or any(item[field] != body[field] for field in ("baseline", "head", "target")):
            raise ValueError("Outdated or mismatched evidence")
    if evidence["result"] != "passed" or review["result"] != "recommend" or review["blocking_count"]:
        raise ValueError("A passing test run and an unblocked positive review are required")
    if (review["evidence_id"], review["evidence_revision"]) != (evidence["id"], evidence["revision"]):
        raise ValueError("Review did not consider the cited test run")
    if review["author_family"].casefold() == review["reviewer_family"].casefold():
        raise ValueError("Independent review required")
    records = [get(db, *ref) for ref in json.loads(snapshot["body"])["references"]]
    required = {(x["id"], x["revision"]) for x in records if x["kind"] == "requirement"}
    cases = [x for x in records if x["kind"] == "test_case"]
    covered = {(x["requirement_id"], x["requirement_revision"]) for x in cases}
    passed = {(x["id"], x["revision"]) for x in evidence["cases"] if x["result"] == "passed"}
    if (
        required != covered
        or passed != {(x["id"], x["revision"]) for x in cases}
        or len(evidence["cases"]) != len(cases)
    ):
        raise ValueError("The cited test run must pass every case in the baseline")
    identifier = "DEC-" + digest(body)[:24]
    put(db, "decision", identifier, body, actor)
    return identifier
