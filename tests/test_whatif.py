from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from urllib.request import urlopen

from localtrader.config import load_config
from localtrader.dashboard import DashboardServer
from localtrader.whatif import Assumptions, evaluate, evaluate_named, load_report, report_names

ROOT = Path(__file__).resolve().parents[1]


class WhatIfTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.reports = Path(cls.tmp.name) / "reports"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "localtrader",
                "intraday-backtest",
                "--synthetic-days",
                "90",
                "--symbol",
                "SPY",
                "--strategies",
                "intraday_trend,mean_reversion",
                "--output",
                str(cls.reports / "syn"),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.report = load_report(cls.reports / "syn")

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def test_report_assumptions_reproduce_the_report(self) -> None:
        self.assertFalse(self.report.approximate_profiles)
        self.assertGreater(len(self.report.trades), 10)
        result = evaluate(self.report, self.report.baseline)
        total = result["results"]["Gesamt"]
        self.assertAlmostEqual(total["variant"]["final_equity"], self.report.final_equity, delta=0.05)
        self.assertEqual(total["baseline"], total["variant"])
        strategies = [k for k in result["results"] if k != "Gesamt"]
        self.assertEqual(sorted(strategies), ["intraday_trend", "mean_reversion"])
        self.assertEqual(sum(result["results"][s]["variant"]["trades"] for s in strategies), total["variant"]["trades"])

    def test_costs_lower_and_capital_scales_the_result(self) -> None:
        base = self.report.baseline
        dearer = evaluate(self.report, replace(base, fee_bps=base.fee_bps + 5))["results"]["Gesamt"]["variant"]
        reference = evaluate(self.report, base)["results"]["Gesamt"]["variant"]
        self.assertLess(dearer["final_equity"], reference["final_equity"])
        self.assertEqual(dearer["gross_pnl"], reference["gross_pnl"])  # the trades themselves are unchanged
        fixed = evaluate(self.report, replace(base, server_per_year=365.25, data_per_year=0))["results"]["Gesamt"]
        days = (self.report.end - self.report.start).total_seconds() / 86_400
        self.assertAlmostEqual(fixed["variant"]["fixed_costs"], days, places=6)
        # with a fixed minimum fee, a larger account pays a smaller share of its capital in costs
        small = replace(base, min_fee=1.0)
        big = replace(small, capital=base.capital * 10)
        r_small = evaluate(self.report, small)["results"]["Gesamt"]["variant"]
        r_big = evaluate(self.report, big)["results"]["Gesamt"]["variant"]
        self.assertAlmostEqual(r_big["gross_pnl"], 10 * r_small["gross_pnl"], places=6)
        self.assertLess(r_big["trading_costs"] / big.capital, r_small["trading_costs"] / small.capital)
        self.assertGreater(r_big["total_return"], r_small["total_return"])

    def test_invalid_input_and_unknown_reports_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            evaluate(self.report, replace(self.report.baseline, capital=0))
        with self.assertRaises(ValueError):
            evaluate(self.report, replace(self.report.baseline, fee_bps=float("nan")))
        with self.assertRaises(ValueError):
            evaluate_named(self.reports, "../syn", {})
        with self.assertRaises(ValueError):
            evaluate_named(self.reports, "syn", {"risk_per_trade": 0.02})  # risk changes trades: not offered
        self.assertEqual(report_names(self.reports), ["syn"])

    def test_old_report_without_profiles_uses_config_and_says_so(self) -> None:
        old = Path(self.tmp.name) / "old"
        shutil.copytree(self.reports / "syn", old / "syn")
        summary = json.loads((old / "syn" / "summary.json").read_text())
        summary.pop("cost_profiles")
        (old / "syn" / "summary.json").write_text(json.dumps(summary))
        profiles = {"SPY": {"fee_bps": 0.1, "spread_bps": 0.5, "slippage_bps": 1.0, "min_fee": 0.0}}
        result = evaluate_named(old, "syn", {}, profiles)
        self.assertTrue(result["approximate_cost_profiles"])

    def test_dashboard_endpoint(self) -> None:
        server = DashboardServer(
            ("127.0.0.1", 0),
            Path(self.tmp.name) / "none.db",
            Path(self.tmp.name) / "none.json",
            ROOT / "localtrader" / "dashboard.html",
            load_config(ROOT / "config" / "default.toml"),
            reports_dir=self.reports,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_address[1]}/api/whatif"
        self.assertEqual(json.loads(urlopen(base).read())["reports"], ["syn"])
        result = json.loads(urlopen(base + "?report=syn&capital=50000&min_fee=1").read())
        self.assertEqual(result["assumptions"]["capital"], 50000.0)
        self.assertIn("Gesamt", result["results"])
        self.assertIn("error", json.loads(urlopen(base + "?report=syn&capital=abc").read()))
        self.assertIn("error", json.loads(urlopen(base + "?report=../../etc").read()))


class AssumptionTests(unittest.TestCase):
    def test_negative_costs_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            Assumptions(10_000, -1, 0, 0, 0).validate()


if __name__ == "__main__":
    unittest.main()
