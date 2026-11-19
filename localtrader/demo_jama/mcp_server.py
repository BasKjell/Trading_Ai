"""Read-only MCP server over stdio, using an operator-produced disclosure snapshot."""

import json
from pathlib import Path
from typing import Any

from .catalog import gaps


def serve(snapshot: Path) -> None:
    from mcp.server.fastmcp import FastMCP

    data = json.loads(snapshot.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError("Unsupported snapshot format")
    records = {(row["id"], row["revision"]): row for row in data["records"]}
    latest = {row["id"]: row for row in records.values()}
    server = FastMCP("trading-demo-jama")

    @server.tool()
    def search_requirements(query: str = "", offset: int = 0, limit: int = 25) -> list[dict[str, Any]]:
        """Find current requirement drafts by ID or statement."""
        if not 1 <= limit <= 100 or offset < 0 or len(query) > 500:
            raise ValueError("Invalid query or page")
        matching = [
            row
            for row in latest.values()
            if row["kind"] == "requirement" and query.casefold() in (row["id"] + " " + row["statement"]).casefold()
        ]
        return matching[offset : offset + limit]

    @server.tool()
    def get_requirement(identifier: str, revision: int | None = None) -> dict[str, Any]:
        """Read a requirement version."""
        row = latest.get(identifier) if revision is None else records.get((identifier, revision))
        if not row or row["kind"] != "requirement":
            raise ValueError("Unknown requirement")
        return row

    @server.tool()
    def list_test_cases(identifier: str, revision: int | None = None) -> list[dict[str, Any]]:
        """Read test cases for a requirement version."""
        requirement = get_requirement(identifier, revision)
        return [
            row
            for row in records.values()
            if row["kind"] == "test_case"
            and row["requirement_id"] == identifier
            and row["requirement_revision"] == requirement["revision"]
        ]

    @server.tool()
    def get_trace(identifier: str, revision: int | None = None) -> dict[str, Any]:
        """Read linked test definitions, baselines and test evidence."""
        requirement = get_requirement(identifier, revision)
        cases = list_test_cases(identifier, requirement["revision"])
        baselines = [x for x in data["snapshots"] if [identifier, requirement["revision"]] in x["references"]]
        baseline_ids = {entry["id"] for entry in baselines}
        return {
            "requirement": requirement,
            "test_cases": cases,
            "baselines": baselines,
            "evidence": [x for x in records.values() if x.get("baseline") in baseline_ids],
        }

    @server.tool()
    def list_gaps() -> dict[str, Any]:
        """Read the gap report."""
        return gaps(data)

    server.run(transport="stdio")
