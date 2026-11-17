from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from typing import Any
from urllib.parse import urlparse
import hashlib
import json
import sqlite3

from .research_db import ResearchDB
from .config import AppConfig, load_config
from .strategy_lab import robustness_reasons
from .strategy_dsl import STRATEGY_FAMILY_CATALOG


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _spec_digest(spec: dict[str, Any]) -> str:
    raw = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def _median(values: list[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None]
    return median(clean) if clean else None


def _best_model(
    model_stats: list[dict[str, Any]], by_model: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    """Select the leading LLM without presenting a rejected result as trade-ready."""
    ranked: list[tuple[tuple[float, ...], dict[str, Any]]] = []
    stats_by_name = {str(item["model"]): item for item in model_stats}
    for model, items in by_model.items():
        if model == "baseline/manual" or not items:
            continue
        robust = [item for item in items if item["eligible"] and item["score"] is not None]
        if robust:
            candidate = max(robust, key=lambda item: float(item["score"]))
            rank = (
                1.0, float(candidate["score"]),
                float(stats_by_name[model]["eligible_rate"] or 0.0), float(len(items)),
            )
            status = "robust"
        else:
            evaluated = [item for item in items if item["score"] is not None]
            if not evaluated:
                continue
            candidate = min(
                evaluated,
                key=lambda item: (
                    len(item["reasons"]),
                    -float(item["score"]),
                ),
            )
            # First compare proximity to the gates, then the amount of evidence.
            rank = (
                0.0, -float(len(candidate["reasons"])),
                float(min(len(items), 20)) / 20.0, float(candidate["score"]),
            )
            status = "provisional"
        ranked.append((rank, {
            "model": model, "status": status, "trade_ready": status == "robust",
            "evaluations": len(items), "eligible": stats_by_name[model]["eligible"],
            "eligible_rate": stats_by_name[model]["eligible_rate"],
            "candidate": candidate,
            "ranking_basis": (
                "best robust candidate" if status == "robust"
                else "fewest failed robustness gates, then evidence and score"
            ),
        }))
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def _read_dashboard(
    db_path: Path, champion_path: Path, config: AppConfig | None = None,
) -> dict[str, Any]:
    config = config or load_config()
    if not db_path.exists():
        raise FileNotFoundError(f"research database not found: {db_path}")
    connection = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    try:
        proposal_rows = connection.execute(
            "SELECT id,model,event_type,payload_json,created_at FROM llm_events ORDER BY id"
        ).fetchall()
        fallback_generators: dict[str, dict[str, Any]] = {}
        proposal_counts: Counter[str] = Counter()
        rejection_events: Counter[str] = Counter()
        for row in proposal_rows:
            payload = json.loads(row["payload_json"])
            model = str(row["model"])
            if row["event_type"] == "proposal" and isinstance(payload.get("spec"), dict):
                proposal_counts[model] += 1
                fallback_generators[_spec_digest(payload["spec"])] = {
                    "model": model, "iteration": payload.get("iteration"),
                }
            elif row["event_type"] == "rejection":
                rejection_events[model] += 1

        evaluation_rows = connection.execute(
            "SELECT id,candidate_id,status,score,eligible,result_json,evaluated_at,"
            "generator_model,generator_iteration FROM evaluations ORDER BY id"
        ).fetchall()
        candidates: list[dict[str, Any]] = []
        reason_counts: Counter[str] = Counter()
        by_model: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in evaluation_rows:
            result = json.loads(row["result_json"])
            fallback = fallback_generators.get(str(row["candidate_id"]), {})
            model = str(row["generator_model"] or fallback.get("model") or "baseline/manual")
            reasons = list(dict.fromkeys(
                [str(reason) for reason in result.get("reasons", [])]
                + robustness_reasons(config, result)
            ))
            reason_counts.update(reasons)
            spec = result.get("spec", {})
            dataset = result.get("dataset", {})
            candidate = {
                "id": int(row["id"]), "candidate_id": str(row["candidate_id"]),
                "model": model,
                "iteration": row["generator_iteration"] or fallback.get("iteration"),
                "name": result.get("name"), "family": result.get("family"),
                "timeframe": spec.get("timeframe", "1Day"),
                "universe": spec.get("universe", []), "status": result.get("status"),
                "eligible": result.get("status") == "evaluated" and not reasons, "score": result.get("score"),
                "return": result.get("median_total_return"), "sharpe": result.get("median_sharpe"),
                "drawdown": result.get("worst_max_drawdown"), "trades": result.get("median_trades"),
                "cost_3x_return": result.get("cost_3x_total_return"), "reasons": reasons,
                "positive_fold_ratio": result.get("positive_fold_ratio"),
                "dataset_source": dataset.get("source"), "bars": dataset.get("bars"),
                "evaluated_at": result.get("evaluated_at") or row["evaluated_at"],
            }
            candidates.append(candidate)
            by_model[model].append(candidate)

        model_stats: list[dict[str, Any]] = []
        all_models = sorted(set(by_model) | set(proposal_counts) | set(rejection_events))
        for model in all_models:
            items = by_model.get(model, [])
            eligible_count = sum(bool(item["eligible"]) for item in items)
            scored = [item for item in items if item["score"] is not None]
            best = max(scored, key=lambda item: float(item["score"]), default=None)
            model_stats.append({
                "model": model, "proposals": proposal_counts[model], "evaluations": len(items),
                "eligible": eligible_count,
                "eligible_rate": eligible_count / len(items) if items else None,
                "median_score": _median([item["score"] for item in items]),
                "median_return": _median([item["return"] for item in items]),
                "median_sharpe": _median([item["sharpe"] for item in items]),
                "median_drawdown": _median([item["drawdown"] for item in items]),
                "best_score": best["score"] if best else None,
                "best_candidate": best["name"] if best else None,
                "generation_rejections": rejection_events[model],
            })

        best_model = _best_model(model_stats, by_model)
        if best_model is not None:
            for item in model_stats:
                item["is_best"] = item["model"] == best_model["model"]

        request_rows = connection.execute(
            "SELECT request_id,status,request_json,attempt_count,cooldown_until,error,updated_at "
            "FROM data_requests ORDER BY updated_at DESC LIMIT 12"
        ).fetchall()
        requests = []
        for row in request_rows:
            request = json.loads(row["request_json"])
            requests.append({
                "request_id": str(row["request_id"]), "status": str(row["status"]),
                "symbols": request.get("symbols", []), "timeframe": request.get("timeframe"),
                "strategy": request.get("strategy_name"), "attempts": int(row["attempt_count"]),
                "cooldown_until": row["cooldown_until"], "error": row["error"],
                "updated_at": row["updated_at"],
            })
        request_status = dict(Counter(item["status"] for item in requests))
    finally:
        connection.close()

    champion = json.loads(champion_path.read_text(encoding="utf-8")) if champion_path.exists() else None
    if champion is not None:
        champion_reasons = robustness_reasons(config, champion)
        if champion_reasons:
            champion["eligible"] = False
            champion["stale"] = True
            champion["reasons"] = champion_reasons
    newest = candidates[-1] if candidates else None
    strategy_coverage = []
    for family, metadata in STRATEGY_FAMILY_CATALOG.items():
        items = [item for item in candidates if item["family"] == family]
        missing = sorted({
            reason.removeprefix("missing:") for item in items for reason in item["reasons"]
            if reason.startswith("missing:")
        })
        evaluated = sum(item["status"] == "evaluated" for item in items)
        eligible = sum(bool(item["eligible"]) for item in items)
        real_eligible = sum(
            bool(item["eligible"]) and item["dataset_source"] != "synthetic_fixture"
            for item in items
        )
        if real_eligible:
            status = "robust_candidate"
        elif eligible:
            status = "fixture_candidate"
        elif evaluated:
            status = "research_active"
        elif metadata["mode"] == "shadow_event":
            status = "shadow_missing_data"
        else:
            status = "not_evaluated"
        strategy_coverage.append({
            "family": family, **metadata, "status": status,
            "evaluations": len(items), "completed": evaluated, "eligible": eligible,
            "real_eligible": real_eligible,
            "missing_requirements": missing,
        })
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "overview": {
            "evaluations": len(candidates),
            "eligible": sum(bool(item["eligible"]) for item in candidates),
            "models": len([item for item in all_models if item != "baseline/manual"]),
            "running_downloads": request_status.get("running", 0),
            "latest_candidate": newest["name"] if newest else None,
        },
        "champion": champion,
        "best_model": best_model,
        "models": model_stats,
        "strategy_coverage": strategy_coverage,
        "candidates": list(reversed(candidates[-500:])),
        "timeline": candidates[-500:],
        "rejection_reasons": [
            {"reason": reason, "count": count} for reason, count in reason_counts.most_common()
        ],
        "data_requests": requests,
        "request_status": request_status,
    }


class DashboardServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self, address: tuple[str, int], db_path: Path, champion_path: Path,
        html_path: Path, config: AppConfig,
    ) -> None:
        super().__init__(address, DashboardHandler)
        self.db_path = db_path
        self.champion_path = champion_path
        self.html_path = html_path
        self.config = config


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    def _send(self, status: int, content_type: str, payload: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            if path == "/api/dashboard":
                payload = json.dumps(
                    _read_dashboard(self.server.db_path, self.server.champion_path, self.server.config),
                    separators=(",", ":"), allow_nan=False,
                ).encode()
                self._send(200, "application/json; charset=utf-8", payload)
                return
            if path == "/health":
                self._send(200, "application/json", b'{"status":"ok"}')
                return
            if path in {"/", "/index.html"}:
                self._send(200, "text/html; charset=utf-8", self.server.html_path.read_bytes())
                return
            self._send(404, "application/json", b'{"error":"not found"}')
        except Exception as error:
            payload = json.dumps({"error": str(error)[:500]}).encode()
            self._send(500, "application/json; charset=utf-8", payload)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve_dashboard(
    registry_dir: str | Path = "research_registry", host: str = "127.0.0.1", port: int = 8501,
    config_path: str | Path = "config/default.toml",
) -> None:
    if host not in LOOPBACK_HOSTS:
        raise ValueError("dashboard may bind only to a loopback host")
    if not 1 <= port <= 65535:
        raise ValueError("invalid dashboard port")
    registry = Path(registry_dir)
    ResearchDB(registry / "research.db")  # Apply safe additive migrations before read-only serving.
    html_path = Path(__file__).with_name("dashboard.html")
    server = DashboardServer(
        (host, port), registry / "research.db", registry / "research_champion.json",
        html_path, load_config(config_path),
    )
    print(f"Dashboard: http://{host}:{port}", flush=True)
    print("Beenden mit Ctrl+C", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
