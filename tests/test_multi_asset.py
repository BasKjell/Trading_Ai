"""T-021: several instruments in one shared portfolio run (research path only)."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from localtrader.cli import _load_intraday
from localtrader.config import load_config
from localtrader.data import approved_download_catalog, available_data_catalog
from localtrader.data_requests import DataRequestError, DataRequestPolicy, DataRequestSpec
from localtrader.intraday_engine import IntradayEngine, instruments_from_config
from localtrader.intraday_report import strategies_from_config

REPO = Path(__file__).resolve().parent.parent
CONFIG = REPO / "config" / "default.toml"
T021_SYMBOLS = ("SPY", "EFA", "EEM", "TLT", "GLD", "DBC")


def _write_bars(path: Path, starts: list[datetime], price: float) -> None:
    lines = ["timestamp,open,high,low,close,volume"]
    lines += [f"{s.isoformat()},{price},{price + 1},{price - 1},{price},1000" for s in starts]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class MultiSymbolLoadingTests(unittest.TestCase):
    def test_comma_separated_symbols_load_into_one_run(self) -> None:
        _, _, datasets, holdout = _load_intraday(str(CONFIG), None, 3, "SPY, QQQ")
        self.assertEqual(list(datasets), ["SPY", "QQQ"])
        self.assertTrue(all(d.bars for d in datasets.values()))
        self.assertEqual({b.symbol for b in datasets["QQQ"].bars}, {"QQQ"})
        self.assertFalse(holdout["used"])

    def test_single_symbol_unchanged(self) -> None:
        _, _, datasets, _ = _load_intraday(str(CONFIG), None, 3, "SPY")
        self.assertEqual(list(datasets), ["SPY"])

    def test_invalid_symbol_lists_are_rejected(self) -> None:
        for value in ("SPY,SPY", "SPY,NOPE", " , "):
            with self.subTest(value=value), self.assertRaises(SystemExit):
                _load_intraday(str(CONFIG), None, 3, value)
        with self.assertRaises(SystemExit):  # one explicit file cannot serve two instruments
            _load_intraday(str(CONFIG), "data/SPY_15m.csv", 3, "SPY,QQQ")

    def test_holdout_is_withheld_for_every_symbol(self) -> None:
        before = datetime(2025, 10, 31, 14, 30, tzinfo=UTC)
        inside = datetime(2025, 11, 3, 14, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            text = CONFIG.read_text(encoding="utf-8")
            for symbol, price in (("SPY", 500.0), ("QQQ", 400.0)):
                path = Path(tmp) / f"{symbol}_15m.csv"
                _write_bars(path, [before, inside, inside + timedelta(minutes=15)], price)
                (Path(tmp) / f"{symbol}_dividends.csv").write_text(
                    "ex_date,pay_date,amount,source\n2025-12-22,2026-01-30,1.6,t\n", encoding="utf-8"
                )
                text = text.replace(f'data_file = "data/{symbol}_15m.csv"', f'data_file = "{path}"')
            config = Path(tmp) / "config.toml"
            config.write_text(text, encoding="utf-8")
            _, _, datasets, holdout = _load_intraday(str(config), None, None, "SPY,QQQ")
        for symbol in ("SPY", "QQQ"):
            self.assertEqual([b.start for b in datasets[symbol].bars], [before])
            self.assertEqual(datasets[symbol].dividends, {})
        self.assertEqual(holdout["excluded_bars"], 4)
        self.assertFalse(holdout["used"])


class DownloadCatalogTests(unittest.TestCase):
    def test_t021_etfs_are_downloadable_but_not_in_the_daily_lab_catalog(self) -> None:
        downloadable = approved_download_catalog()
        lab = available_data_catalog()
        for symbol in T021_SYMBOLS:
            self.assertIn(symbol, downloadable)
        for symbol in ("EFA", "EEM", "DBC"):
            self.assertNotIn(symbol, lab)
        start = datetime(2024, 1, 2, tzinfo=UTC)
        request = DataRequestSpec(
            "alpaca",
            "stocks",
            ("EFA", "DBC"),
            "15Min",
            start,
            start + timedelta(days=1),
            "sip",
            "research:t021",
            "t",
            "raw",
        )
        DataRequestPolicy().validate(request)
        with self.assertRaises(DataRequestError):
            DataRequestPolicy().validate(replace(request, symbols=("USO",)))


class T021ConfigurationTests(unittest.TestCase):
    def test_t021_instruments_are_configured_without_raising_limits(self) -> None:
        config = load_config(CONFIG)
        instruments = instruments_from_config(config)
        groups = config.correlation_groups
        for symbol in T021_SYMBOLS:
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, instruments)
                self.assertIn(symbol, groups)
                raw = config.raw["instruments"][symbol]
                self.assertEqual(raw["corporate_actions"], "dividends_file")
                self.assertEqual(float(raw["financing_rate_annual"]), 0.0)
                self.assertGreater(float(raw["spread_bps"]), 0.0)
        # EFA and EEM are both non-US equity: one group, so the 40 % group limit binds them together
        self.assertEqual(groups["EFA"], groups["EEM"])
        self.assertEqual(len({groups[s] for s in T021_SYMBOLS}), 5)
        # unchanged protective limits (ADR 0002, AGENTS.md section 5)
        self.assertEqual(config.risk.risk_per_trade, 0.002)
        self.assertEqual(config.risk.max_open_risk, 0.01)
        self.assertEqual(config.risk.max_instrument_weight, 0.20)
        self.assertEqual(config.risk.max_group_weight, 0.40)
        self.assertEqual(config.risk.max_total_exposure, 1.0)

    def test_shared_portfolio_run_trades_several_instruments(self) -> None:
        config, instruments, datasets, _ = _load_intraday(str(CONFIG), None, 40, "SPY,TLT,GLD")
        engine = IntradayEngine(config, instruments, strategies_from_config(config, ["intraday_trend"]))
        result = engine.run(datasets)
        traded = {t.symbol for t in result.trades}
        self.assertGreater(len(traded), 1)
        self.assertTrue(traded <= {"SPY", "TLT", "GLD"})
        # one portfolio: exposure never above the no-leverage limit
        limit = config.risk.max_total_exposure
        self.assertTrue(all(p.exposure <= p.equity * limit + 1e-6 for p in result.equity))


if __name__ == "__main__":
    unittest.main()
