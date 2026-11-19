from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import urlopen

from localtrader.config import load_config
from localtrader.dashboard import DashboardServer
from localtrader.whatif import Assumptions, ReportRefused, evaluate, evaluate_named, load_report, report_names

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
        result = evaluate(self.report, Assumptions(self.report.initial_cash))
        total = result["results"]["Gesamt"]
        self.assertAlmostEqual(total["variant"]["final_equity"], self.report.final_equity, delta=0.05)
        self.assertEqual(total["baseline"], total["variant"])
        strategies = [k for k in result["results"] if k != "Gesamt"]
        self.assertEqual(sorted(strategies), ["intraday_trend", "mean_reversion"])
        self.assertEqual(sum(result["results"][s]["variant"]["trades"] for s in strategies), total["variant"]["trades"])

    def test_costs_lower_and_capital_scales_the_result(self) -> None:
        capital = self.report.initial_cash
        fee = self.report.profiles["SPY"]["fee_bps"]
        dearer = evaluate(self.report, Assumptions(capital, overrides=(("fee_bps", fee + 5),)))["results"]["Gesamt"]
        reference = evaluate(self.report, Assumptions(capital))["results"]["Gesamt"]["variant"]
        self.assertLess(dearer["variant"]["final_equity"], reference["final_equity"])
        self.assertEqual(dearer["variant"]["gross_pnl"], reference["gross_pnl"])  # the trades are unchanged
        fixed = evaluate(self.report, Assumptions(capital, server_per_year=365.25))["results"]["Gesamt"]
        days = (self.report.end - self.report.start).total_seconds() / 86_400
        self.assertAlmostEqual(fixed["variant"]["fixed_costs"], days, places=6)
        # with a fixed minimum fee, a larger account pays a smaller share of its capital in costs
        small = Assumptions(capital, overrides=(("min_fee", 1.0),))
        big = Assumptions(capital * 10, overrides=(("min_fee", 1.0),))
        r_small = evaluate(self.report, small)["results"]["Gesamt"]["variant"]
        r_big = evaluate(self.report, big)["results"]["Gesamt"]["variant"]
        self.assertAlmostEqual(r_big["gross_pnl"], 10 * r_small["gross_pnl"], places=6)
        self.assertLess(r_big["trading_costs"] / big.capital, r_small["trading_costs"] / small.capital)
        self.assertGreater(r_big["total_return"], r_small["total_return"])

    def _copy(self, name: str) -> Path:
        root = Path(self.tmp.name) / name
        shutil.copytree(self.reports / "syn", root / "syn")
        return root

    def _edit_summary(self, root: Path, change: object) -> None:
        file = root / "syn" / "summary.json"
        summary = json.loads(file.read_text())
        change(summary)  # type: ignore[operator]
        file.write_text(json.dumps(summary))

    def test_holdout_reports_are_neither_listed_nor_recomputed(self) -> None:
        cases = {
            "used": lambda s: s["holdout"].update(used=True),
            "unmarked": lambda s: s.pop("holdout"),
            "late_data": lambda s: [
                d.update(synthetic=False, quality={**d["quality"], "last_bar": "2025-11-03T15:00:00+00:00"})
                for d in s["datasets"]
            ],
            "no_end": lambda s: [d.update(synthetic=False) for d in s["datasets"]],
        }
        for name, change in cases.items():
            with self.subTest(name):
                root = self._copy("refused-" + name)
                self._edit_summary(root, change)
                self.assertEqual(report_names(root), [])
                with self.assertRaises(ValueError):
                    evaluate_named(root, "syn", {})
                with self.assertRaises(ReportRefused):
                    load_report(root / "syn")
        # synthetic data with trades dated in the holdout is refused after the summary check as well
        root = self._copy("late-trades")
        trades = (root / "syn" / "trades.csv").read_text().splitlines()
        first = trades[1].split(",")
        first[5] = "2025-11-03T15:00:00+00:00"
        trades[1] = ",".join(first)
        (root / "syn" / "trades.csv").write_text("\n".join(trades) + "\n")
        with self.assertRaises(ReportRefused):
            load_report(root / "syn")
        # report files must be regular files inside the report directory
        root = self._copy("linked")
        outside = Path(self.tmp.name) / "outside.csv"
        shutil.copy(root / "syn" / "trades.csv", outside)
        (root / "syn" / "trades.csv").unlink()
        (root / "syn" / "trades.csv").symlink_to(outside)
        self.assertEqual(report_names(root), [])

    def _serve(self, root: Path) -> str:
        server = DashboardServer(
            ("127.0.0.1", 0),
            Path(self.tmp.name) / "none.db",
            Path(self.tmp.name) / "none.json",
            ROOT / "localtrader" / "dashboard.html",
            load_config(ROOT / "config" / "default.toml"),
            reports_dir=root,
        )
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_address[1]}/api/whatif"

    def _replace_cell(self, root: Path, file: str, row: int, column: str, value: str) -> None:
        path = root / "syn" / file
        lines = path.read_text().splitlines()
        header = lines[0].split(",")
        cells = lines[row].split(",")
        cells[header.index(column)] = value
        lines[row] = ",".join(cells)
        path.write_text("\n".join(lines) + "\n")

    def test_every_timestamp_is_checked_and_the_api_refuses_holdout_reports(self) -> None:
        cases = {
            "equity-middle": ("equity.csv", 5, "timestamp", "2025-11-05T15:00:00+00:00"),
            "equity-first": ("equity.csv", 1, "timestamp", "2025-11-05T15:00:00+00:00"),
            "trade-entry": ("trades.csv", 1, "entry_time", "2025-11-05T15:00:00+00:00"),
            "trade-exit": ("trades.csv", 1, "exit_time", "2025-11-05T15:00:00+00:00"),
            "naive-time": ("equity.csv", 3, "timestamp", "2024-01-03T15:00:00"),
        }
        for name, (file, row, column, value) in cases.items():
            with self.subTest(name):
                root = self._copy("ts-" + name)
                self._replace_cell(root, file, row, column, value)
                with self.assertRaises(ReportRefused):
                    load_report(root / "syn")
                api = self._serve(root)
                listed = json.loads(urlopen(api).read())["reports"]
                answer = json.loads(urlopen(api + "?report=syn&capital=20000").read())
                self.assertIn("error", answer)
                self.assertNotIn("2025-11-05", json.dumps(answer))
                self.assertEqual(listed, [])
        for name, change in {
            "used": lambda s: s["holdout"].update(used=True),
            "unmarked": lambda s: s.pop("holdout"),
        }.items():
            with self.subTest("api-" + name):
                root = self._copy("api-" + name)
                self._edit_summary(root, change)
                api = self._serve(root)
                self.assertEqual(json.loads(urlopen(api).read())["reports"], [])
                self.assertIn("error", json.loads(urlopen(api + "?report=syn").read()))

    def test_each_instrument_keeps_its_own_costs_and_overrides_are_partial(self) -> None:
        root = self._copy("profiles")
        expensive = {"fee_bps": 3.0, "spread_bps": 4.0, "slippage_bps": 2.0, "min_fee": 10.0}
        self._edit_summary(root, lambda s: s.update(cost_profiles={"AAA": expensive, **s["cost_profiles"]}))
        result = evaluate_named(root, "syn", {})
        self.assertTrue(result["profiles_differ"])
        total = result["results"]["Gesamt"]
        self.assertAlmostEqual(total["baseline"]["final_equity"], self.report.final_equity, delta=0.05)
        only_fee = evaluate_named(root, "syn", {"fee_bps": 0.1})["results"]["Gesamt"]["variant"]
        self.assertAlmostEqual(only_fee["final_equity"], total["baseline"]["final_equity"], delta=1e-6)
        with_min = evaluate_named(root, "syn", {"min_fee": 1.0})["results"]["Gesamt"]["variant"]
        # every exit and every entry order pays at least 1.0 (partial exits share their entry order)
        added = with_min["fees"] - total["baseline"]["fees"]
        self.assertGreater(added, 1.0 * with_min["trades"])
        self.assertLessEqual(added, 2.0 * with_min["trades"])

    def test_drawdown_includes_fixed_costs_until_the_end_of_the_period(self) -> None:
        root = self._copy("no-trades")
        trades = root / "syn" / "trades.csv"
        trades.write_text(trades.read_text().splitlines()[0] + "\n")
        result = evaluate_named(root, "syn", {"server_per_year": 20_000})["results"]["Gesamt"]["variant"]
        self.assertEqual(result["trades"], 0)
        self.assertGreater(result["fixed_costs"], 0)
        before_other = result["final_equity"] - result["other_income"]  # fixed costs only
        expected = 1 - min(before_other, result["final_equity"]) / 10_000
        self.assertAlmostEqual(result["max_drawdown_closed"], expected, places=9)
        # long quiet period after the last trade: the drawdown sees the costs after it
        root = self._copy("early-trade")
        trades = root / "syn" / "trades.csv"
        lines = trades.read_text().splitlines()
        trades.write_text("\n".join(lines[:2]) + "\n")
        early = evaluate_named(root, "syn", {"server_per_year": 20_000})["results"]["Gesamt"]["variant"]
        self.assertGreater(early["max_drawdown_closed"], 0.2)

    def test_invalid_input_and_unknown_reports_are_refused(self) -> None:
        with self.assertRaises(ValueError):
            evaluate(self.report, Assumptions(0))
        with self.assertRaises(ValueError):
            evaluate(self.report, Assumptions(10_000, overrides=(("fee_bps", float("nan")),)))
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
            Assumptions(10_000, overrides=(("fee_bps", -1.0),)).validate()
        with self.assertRaises(ValueError):
            Assumptions(10_000, overrides=(("risk_per_trade", 0.02),)).validate()


if __name__ == "__main__":
    unittest.main()
