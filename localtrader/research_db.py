from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ResearchDB:
    """Typed append-oriented storage. LLMs never receive a SQL execution primitive."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS strategy_specs (
                    candidate_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    family TEXT NOT NULL,
                    source TEXT NOT NULL,
                    spec_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    score REAL,
                    eligible INTEGER NOT NULL,
                    result_json TEXT NOT NULL,
                    evaluated_at TEXT NOT NULL,
                    generator_model TEXT,
                    generator_iteration INTEGER,
                    FOREIGN KEY(candidate_id) REFERENCES strategy_specs(candidate_id)
                );
                CREATE TABLE IF NOT EXISTS llm_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_catalog (
                    symbol TEXT PRIMARY KEY,
                    market TEXT NOT NULL,
                    asset_class TEXT NOT NULL,
                    source TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_requests (
                    request_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    cooldown_until TEXT,
                    error TEXT,
                    dataset_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    normalized_path TEXT NOT NULL,
                    manifest_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES data_requests(request_id)
                );
            """)
            evaluation_columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(evaluations)").fetchall()
            }
            if "generator_model" not in evaluation_columns:
                connection.execute("ALTER TABLE evaluations ADD COLUMN generator_model TEXT")
            if "generator_iteration" not in evaluation_columns:
                connection.execute("ALTER TABLE evaluations ADD COLUMN generator_iteration INTEGER")

    def store_spec(self, candidate_id: str, spec: dict[str, Any], source: str) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO strategy_specs(candidate_id,name,family,source,spec_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    candidate_id,
                    spec["name"],
                    spec["family"],
                    source,
                    json.dumps(spec, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def store_evaluation(
        self,
        candidate_id: str,
        result: dict[str, Any],
        generator_model: str | None = None,
        generator_iteration: int | None = None,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO evaluations(candidate_id,status,score,eligible,result_json,evaluated_at,generator_model,generator_iteration) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (
                    candidate_id,
                    result["status"],
                    result.get("score"),
                    int(bool(result["eligible"])),
                    json.dumps(result, sort_keys=True),
                    result["evaluated_at"],
                    generator_model,
                    generator_iteration,
                ),
            )

    def store_llm_event(self, model: str, event_type: str, payload: dict[str, Any]) -> None:
        if event_type not in {"proposal", "rejection", "research_note"}:
            raise ValueError("unsupported LLM event type")
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO llm_events(model,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (model, event_type, json.dumps(payload, sort_keys=True), datetime.now(UTC).isoformat()),
            )

    def sync_catalog(self, catalog: dict[str, dict[str, str]], source: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            for symbol, metadata in catalog.items():
                connection.execute(
                    "INSERT INTO data_catalog(symbol,market,asset_class,source,updated_at) VALUES(?,?,?,?,?) "
                    "ON CONFLICT(symbol) DO UPDATE SET market=excluded.market,asset_class=excluded.asset_class,source=excluded.source,updated_at=excluded.updated_at",
                    (symbol, metadata["market"], metadata["asset_class"], source, now),
                )

    def status(self) -> dict[str, int]:
        with self.connect() as connection:
            return {
                table: int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                for table in (
                    "strategy_specs",
                    "evaluations",
                    "llm_events",
                    "data_catalog",
                    "data_requests",
                    "datasets",
                )
            }

    def enqueue_data_request(self, request_id: str, payload: dict[str, Any]) -> None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO data_requests(request_id,status,request_json,created_at,updated_at) VALUES(?,?,?,?,?)",
                (request_id, "pending", json.dumps(payload, sort_keys=True), now, now),
            )

    def data_request(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM data_requests WHERE request_id=?", (request_id,)).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["request"] = json.loads(result.pop("request_json"))
            return result

    def next_data_request(self) -> dict[str, Any] | None:
        now = datetime.now(UTC).isoformat()
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM data_requests WHERE status='pending' OR (status='cooldown' AND cooldown_until<=?) ORDER BY created_at LIMIT 1",
                (now,),
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["request"] = json.loads(result.pop("request_json"))
            return result

    def mark_data_request(
        self,
        request_id: str,
        status: str,
        *,
        error: str | None = None,
        cooldown_until: str | None = None,
        dataset_id: str | None = None,
        increment_attempt: bool = False,
    ) -> None:
        if status not in {"pending", "running", "cooldown", "complete", "failed"}:
            raise ValueError("invalid data request status")
        with self.connect() as connection:
            connection.execute(
                "UPDATE data_requests SET status=?,error=?,cooldown_until=?,dataset_id=COALESCE(?,dataset_id),"
                "attempt_count=attempt_count+?,updated_at=? WHERE request_id=?",
                (
                    status,
                    error[:500] if error else None,
                    cooldown_until,
                    dataset_id,
                    1 if increment_attempt else 0,
                    datetime.now(UTC).isoformat(),
                    request_id,
                ),
            )

    def store_dataset(
        self,
        dataset_id: str,
        request_id: str,
        provider: str,
        normalized_path: str,
        manifest: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO datasets(dataset_id,request_id,provider,normalized_path,manifest_json,created_at) VALUES(?,?,?,?,?,?)",
                (
                    dataset_id,
                    request_id,
                    provider,
                    normalized_path,
                    json.dumps(manifest, sort_keys=True),
                    datetime.now(UTC).isoformat(),
                ),
            )

    def dataset_for_request(self, request_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM datasets WHERE request_id=? ORDER BY created_at DESC LIMIT 1", (request_id,)
            ).fetchone()
            if row is None:
                return None
            result = dict(row)
            result["manifest"] = json.loads(result.pop("manifest_json"))
            return result

    def evaluated_strategy_specs(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT s.spec_json FROM strategy_specs s "
                "INNER JOIN evaluations e ON e.candidate_id=s.candidate_id"
            ).fetchall()
            return [json.loads(row["spec_json"]) for row in rows]

    def recent_evaluations(self, limit: int = 20) -> list[dict[str, Any]]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be in [1, 1000]")
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT result_json,generator_model,generator_iteration FROM evaluations ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [
                {
                    "result": json.loads(row["result_json"]),
                    "generator_model": row["generator_model"],
                    "generator_iteration": row["generator_iteration"],
                }
                for row in rows
            ]

    def family_evaluation_counts(self, generator_model: str) -> dict[str, int]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT s.family,COUNT(*) AS total FROM evaluations e "
                "INNER JOIN strategy_specs s ON s.candidate_id=e.candidate_id "
                "WHERE e.generator_model=? AND e.status='evaluated' GROUP BY s.family",
                (generator_model,),
            ).fetchall()
            return {str(row["family"]): int(row["total"]) for row in rows}
