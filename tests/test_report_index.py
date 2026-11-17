from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

from localtrader.config import load_config
from localtrader.dashboard import DashboardServer
from localtrader.experiment_registry import ExperimentRecord, Variant, write_record
from localtrader.report_index import experiments_overview, reports_overview


def record(experiment_id: str, task: str, holdout: bool = False) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        task_id=task,
        agent="test",
        model="m",
        created_at=f"2026-11-1{len(experiment_id) % 9}T00:00:00+00:00",
        code_version="abc1234",
        data_versions=["v1"],
        seeds=[1],
        splits={},
        cost_assumptions={},
        variants=[Variant("a", {}, {}, rejected=True), Variant("b", {}, {})],
        uncertainty={},
        decision="rejected",
        rationale="test",
        holdout_used=holdout,
    )


def write_reports(root: Path) -> None:
    (root / "trend_spy").mkdir(parents=True)
    (root / "trend_spy" / "summary.json").write_text(
        json.dumps(
            {
                "metrics": {"total_return": 0.063, "max_drawdown": 0.016, "trades": 972, "sharpe": float("nan")},
                "benchmark": {"buy_and_hold_total_return": 2.63},
                "parameters": {"intraday_trend": {"breakout_bars": 16}},
                "datasets": [{"symbol": "SPY", "synthetic": False}],
                "holdout": {"start": "2025-11-01T00:00:00+00:00", "excluded_bars": 6784, "used": False},
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )
    (root / "stress_spy").mkdir()
    (root / "stress_spy" / "scenarios.json").write_text(
        json.dumps(
            {
                "baseline": {"total_return": 0.063, "max_drawdown": 0.016, "trades": 972},
                "costs_2x": {"total_return": 0.009, "max_drawdown": 0.02, "trades": 972},
                "phase_1_2016-01-04_2019-04-12": {"total_return": 0.016, "max_drawdown": 0.01, "trades": 300},
                "holdout": {"start": "2025-11-01T00:00:00+00:00", "excluded_bars": 0, "used": True},
            }
        ),
        encoding="utf-8",
    )
    (root / "broken").mkdir()
    (root / "broken" / "summary.json").write_text("{not json", encoding="utf-8")


class ReportIndexTests(unittest.TestCase):
    def test_experiments_overview_counts_all_variants_and_holdout_uses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_record(record("EXP-T018-001", "T-018"), tmp)
            write_record(record("EXP-T018-002", "T-018", holdout=True), tmp)
            overview = experiments_overview(tmp)
        self.assertEqual((overview["experiments"], overview["total_variants_tested"]), (2, 4))
        self.assertEqual(overview["holdout_uses"], 1)
        self.assertEqual(overview["entries"][0]["rejected_variants"], 1)

    def test_missing_register_is_reported_not_raised(self) -> None:
        self.assertFalse(experiments_overview("/nonexistent/experiments")["available"])
        self.assertFalse(reports_overview("/nonexistent/reports")["available"])

    def test_reports_overview_reads_backtests_and_stress_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_reports(Path(tmp))
            overview = reports_overview(tmp)
        kinds = {entry["name"]: entry for entry in overview["reports"]}
        backtest, stress = kinds["trend_spy"], kinds["stress_spy"]
        self.assertEqual(backtest["symbols"], ["SPY"])
        self.assertEqual(backtest["strategies"], ["intraday_trend"])
        self.assertIsNone(backtest["metrics"]["sharpe"])  # NaN becomes null, JSON stays valid
        self.assertFalse(backtest["holdout_used"])
        self.assertTrue(stress["holdout_used"])  # a holdout run is flagged, never hidden
        self.assertEqual(stress["phases"], ["phase_1_2016-01-04_2019-04-12"])
        self.assertNotIn("holdout", stress["scenarios"])
        self.assertEqual(len(overview["errors"]), 1)
        json.dumps(overview, allow_nan=False)


class ReportEndpointTests(unittest.TestCase):
    def test_api_reports_serves_both_overviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_record(replace(record("EXP-T018-001", "T-018")), root / "experiments")
            write_reports(root / "reports")
            html = root / "index.html"
            html.write_text("<html></html>", encoding="utf-8")
            server = DashboardServer(
                ("127.0.0.1", 0),
                root / "research.db",
                root / "champion.json",
                html,
                load_config(),
                root / "experiments",
                root / "reports",
            )
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/reports", timeout=5) as response:
                    payload = json.loads(response.read())
            finally:
                server.shutdown()
                server.server_close()
        self.assertEqual(payload["experiments"]["experiments"], 1)
        self.assertEqual(len(payload["intraday"]["reports"]), 2)
