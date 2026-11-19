import asyncio
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from localtrader.demo_jama.catalog import gaps, import_catalog, record_case
from localtrader.demo_jama.store import baseline, connect, decision, export, put, verify

CATALOG = Path("docs/demo-jama/catalog.json")
HEAD = "a" * 40


def seeded(tmp_path):
    directory = tmp_path / "private"
    path = directory / "jama.sqlite"
    with connect(path, write=True) as db:
        import_catalog(db, json.loads(CATALOG.read_text()), "operator")
    return path


def test_catalog_idempotent_and_audited(tmp_path):
    path = seeded(tmp_path)
    with connect(path, write=True) as db:
        original = export(db)
        import_catalog(db, json.loads(CATALOG.read_text()), "operator")
        assert export(db) == original
        report = gaps(original)
        assert report["issues"] == report["issues_with_requirements"] == 22
        assert len(report["requirements_without_baseline"]) == report["requirements"]
        verify(db)
    with connect(path, write=True) as db:
        db.execute("UPDATE versions SET body = '{}' WHERE id = 'GH-1'")
        with pytest.raises(ValueError, match="provenance"):
            verify(db)


def test_evidence_and_independence_gate(tmp_path):
    with connect(seeded(tmp_path), write=True) as db:
        identifier = "REQ-GH-001-01"
        record_case(
            db,
            {
                "id": "TC-001",
                "requirement_id": identifier,
                "requirement_revision": 1,
                "preconditions": "synthetic fixture",
                "test_data": "fixture",
                "steps": "run",
                "expected": "blocked",
                "test_type": "negative",
                "procedure": "pytest case",
            },
            "operator",
        )
        snap = baseline(db, [(identifier, 1), ("TC-001", 1)], "operator")
        scope = {"baseline": snap, "head": HEAD, "target": "main"}
        put(
            db,
            "test_run",
            "RUN-1",
            scope
            | {
                "result": "passed",
                "cases": [{"id": "TC-001", "revision": 1, "result": "passed"}],
            },
            "operator",
        )
        review = scope | {
            "result": "recommend",
            "blocking_count": 0,
            "evidence_id": "RUN-1",
            "evidence_revision": 1,
            "author_family": "Codex",
            "reviewer_family": "Codex",
        }
        put(db, "review", "REV-1", review, "operator")
        entry = scope | {
            "evidence_id": "RUN-1",
            "evidence_revision": 1,
            "review_id": "REV-1",
            "review_revision": 1,
            "stage": "integration",
            "reason": "synthetic test",
        }
        with pytest.raises(ValueError, match="Independent"):
            decision(db, entry, "operator")
        put(db, "review", "REV-1", review | {"reviewer_family": "Claude"}, "operator")
        entry["review_revision"] = 2
        with pytest.raises(ValueError, match="mismatched"):
            decision(db, entry | {"target": "other"}, "operator")
        assert decision(db, entry, "operator").startswith("DEC-")
        verify(db)


def test_failed_import_rollback_and_backup(tmp_path):
    path = seeded(tmp_path)
    broken = json.loads(CATALOG.read_text())
    broken["issues"][0]["requirements"][0]["statement"] = "changed"
    del broken["issues"][-1]["requirements"][0]["statement"]
    with pytest.raises(ValueError, match="statement"), connect(path, write=True) as db:
        import_catalog(db, broken, "operator")
    with connect(path) as source:
        assert max(x["revision"] for x in export(source)["records"]) == 1
        target = tmp_path / "restore.sqlite"
        with sqlite3.connect(target) as copy:
            source.backup(copy)
    with connect(target) as recovered:
        verify(recovered)
        assert gaps(export(recovered))["issues"] == 22


def test_mcp_stdio_snapshot(tmp_path):
    pytest.importorskip("mcp")
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    file = tmp_path / "snapshot.json"
    with connect(seeded(tmp_path)) as db:
        file.write_text(json.dumps(export(db)))

    async def run():
        parameters = StdioServerParameters(
            command=sys.executable, args=["-m", "localtrader.demo_jama", "mcp", str(file)]
        )
        async with stdio_client(parameters) as (reader, writer), ClientSession(reader, writer) as client:
            await client.initialize()
            tools = await client.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "search_requirements",
                "get_requirement",
                "get_trace",
                "list_test_cases",
                "list_gaps",
            }
            result = await client.call_tool("get_trace", {"identifier": "REQ-GH-001-01"})
            assert not result.isError
            assert result.structuredContent["requirement"]["status"] == "review_needed"
            error = await client.call_tool("get_requirement", {"identifier": "nope"})
            assert error.isError

    asyncio.run(run())
