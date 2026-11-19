"""Operator-only commands and local read-only interfaces."""

import argparse
import json
import os
import sqlite3
import sys
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .catalog import gaps, import_catalog, record_case, record_requirement
from .github_evidence import policy_at, verify_github
from .store import baseline, connect, decision, digest, encode, export, get, put, required_text, verify


def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _operator_command(db: sqlite3.Connection, command: str, payload: Any, actor: str) -> Any:
    if command == "import" or command == "dry-run":
        import_catalog(db, payload, actor)
        return gaps(export(db))
    if command == "requirement":
        record_requirement(db, payload, actor)
    elif command == "test-case":
        record_case(db, payload, actor)
    elif command == "baseline":
        return baseline(db, [(row["id"], row["revision"]) for row in payload], actor)
    elif command == "decide":
        return decision(db, payload | {"provenance": "local_unverified"}, actor)
    else:
        required_text(payload, "baseline", "head", "target", "result")
        if command == "test-run":
            required_text(payload, "ci_run", "environment", "timestamp", "evidence_url")
            for case in payload["cases"]:
                required_text(case, "id", "actual", "result")
                if type(case.get("revision")) is not int or case["result"] not in {"passed", "failed", "skipped"}:
                    raise ValueError("Invalid case result")
                if case["result"] != "passed":
                    required_text(case, "reason")
            if payload["result"] not in {"passed", "failed", "incomplete"} or not payload["cases"]:
                raise ValueError("A test run needs an explicit result and case outcomes")
        else:
            required_text(payload, "author_family", "reviewer_family", "evidence_id", "reference")
            if payload["result"] not in {"recommend", "changes_required"} or type(payload["blocking_count"]) is not int:
                raise ValueError("Invalid review verdict")
        kind = command.replace("-", "_")
        identifier = kind.upper() + "-" + digest(payload)[:24]
        put(db, kind, identifier, payload, actor)
        return identifier
    return payload["id"]


def _ui(snapshot: Path, port: int) -> None:
    data = _read(snapshot)
    latest = {row["id"]: row for row in data["records"]}

    class View(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.headers.get("Host") != f"127.0.0.1:{port}":
                self.send_error(400)
                return
            params = parse_qs(urlsplit(self.path).query)
            query = params.get("q", [""])[0]
            identifier = params.get("id", [""])[0]
            rows = [
                row
                for row in latest.values()
                if row["kind"] == "requirement" and query.casefold() in (row["id"] + " " + row["statement"]).casefold()
            ]
            content = "".join(
                f'<li><a href="?id={escape(row["id"])}">{escape(row["id"])}</a> {escape(row["statement"])}</li>'
                for row in rows
            )
            detail = latest.get(identifier)
            if detail and detail["kind"] == "requirement":
                related = [row for row in data["records"] if row.get("requirement_id") == identifier]
                snapshots = [row for row in data["snapshots"] if any(ref[0] == identifier for ref in row["references"])]
                content = (
                    "<pre>"
                    + escape(
                        json.dumps(
                            {"requirement": detail, "test_cases": related, "baselines": snapshots},
                            ensure_ascii=False,
                            indent=2,
                        )
                    )
                    + "</pre>"
                )
            counts = gaps(data)
            page = (
                '<!doctype html><html lang="de"><meta charset="utf-8"><title>Demo-Jama</title>'
                '<h1>Demo-Jama</h1><form><label>Requirement suchen <input name="q" value="'
                + escape(query)
                + '"></label><button>Suchen</button></form>'
                + f"<p>{counts['issues']} Issues · {counts['requirements']} Requirements</p>"
                + content
                + "<details><summary>Nachweislücken</summary><pre>"
                + escape(json.dumps(counts, ensure_ascii=False, indent=2))
                + "</pre></details></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header(
                "Content-Security-Policy", "default-src 'none'; form-action 'self'; frame-ancestors 'none'"
            )
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(page)

    with ThreadingHTTPServer(("127.0.0.1", port), View) as http:
        http.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("import", "dry-run", "requirement", "test-case", "baseline", "test-run", "review", "decide"):
        commands.add_parser(name).add_argument("input", type=Path)
    verified = commands.add_parser("github-decide")
    verified.add_argument("input", type=Path)
    verified.add_argument("--policy", type=Path, required=True)
    for name in ("export", "gaps", "verify"):
        commands.add_parser(name)
    for name in ("backup", "mcp", "ui"):
        sub = commands.add_parser(name)
        sub.add_argument("file", type=Path)
        if name == "ui":
            sub.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        if args.command == "mcp":
            from .mcp_server import serve

            serve(args.file)
            return
        if args.command == "ui":
            _ui(args.file, args.port)
            return
        if not args.db:
            parser.error("--db is required for operator commands")
        actor = f"local-operator:uid:{os.getuid()}"
        if args.command == "github-decide":
            request = _read(args.input)
            required_text(request, "baseline", "head", "target", "reason")
            with connect(args.db, write=True) as db:
                row = db.execute("SELECT body FROM snapshots WHERE id = ?", (request["baseline"],)).fetchone()
                if not row:
                    raise ValueError("Unknown baseline")
                records = [get(db, *ref) for ref in json.loads(row["body"])["references"]]
                requirements = {(row["id"], row["revision"]) for row in records if row["kind"] == "requirement"}
                cases = [row for row in records if row["kind"] == "test_case"]
                if requirements != {(row["requirement_id"], row["requirement_revision"]) for row in cases}:
                    raise ValueError("All baseline requirements need acceptance tests")
                risk = "K" if any(row.get("risk") == "K" for row in records) else "U"
                proof = verify_github(
                    policy_at(args.policy), request, risk, {f"{row['id']}@{row['revision']}" for row in cases}
                )
                body = proof | {
                    "stage": "integration",
                    "provenance": "github_verified",
                    "reason": request["reason"],
                }
                identifier = "DEC-" + digest(body)[:24]
                put(db, "decision", identifier, body, actor)
                print(encode(identifier))
            return
        if args.command == "dry-run":
            with sqlite3.connect(":memory:") as db:
                from .store import SCHEMA

                db.row_factory = sqlite3.Row
                if args.db.exists():
                    with connect(args.db) as source:
                        source.backup(db)
                db.executescript(SCHEMA)
                print(encode(_operator_command(db, args.command, _read(args.input), actor)))
            return
        if args.command in {"export", "gaps", "verify", "backup"}:
            with connect(args.db) as db:
                if args.command == "verify":
                    verify(db)
                    print("Integrity verified")
                elif args.command == "backup":
                    fd = os.open(args.file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                    os.close(fd)
                    try:
                        with sqlite3.connect(args.file, autocommit=True) as destination:
                            db.backup(destination)
                    except BaseException:
                        args.file.unlink()
                        raise
                else:
                    data = export(db)
                    print(encode(data if args.command == "export" else gaps(data)))
        else:
            with connect(args.db, write=True) as db:
                print(encode(_operator_command(db, args.command, _read(args.input), actor)))
    except (OSError, ValueError, sqlite3.Error, KeyError, TypeError) as exc:
        sys.exit(f"Demo-Jama: {exc}")


if __name__ == "__main__":
    main()
