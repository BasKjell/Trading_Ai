import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from localtrader.backtest import BacktestEngine
from localtrader.broker_guard import PaperEndpointGuard, UnsafeBrokerEndpoint
from localtrader.config import load_config
from localtrader.data import bars_hash, synthetic_fixture
from localtrader.domain import Market
from localtrader.paper import PaperSession
from localtrader.reporting import write_report


class PaperBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_backtest_reproducible_and_reports(self) -> None:
        bars = synthetic_fixture(self.config.seed, 300)
        first = BacktestEngine(self.config).run(bars, bars_hash(bars))
        second = BacktestEngine(self.config).run(bars, bars_hash(bars))
        self.assertEqual(first.metrics, second.metrics)
        with TemporaryDirectory() as directory:
            output = write_report(first, directory)
            self.assertTrue((output / "summary.json").exists())
            self.assertTrue((output / "annual_returns.svg").exists())

    def test_paper_restart_and_duplicate_event(self) -> None:
        with TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            audit = Path(directory) / "audit.jsonl"
            now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
            first = PaperSession(self.config, state, audit)
            self.assertTrue(first.process_demo_event("same", "SPY", Market.EQUITY, 500, 485, now))
            restarted = PaperSession(self.config, state, audit)
            self.assertFalse(restarted.process_demo_event("same", "SPY", Market.EQUITY, 500, 485, now))
            self.assertIn("SPY", restarted.portfolio.positions)

    def test_kill_switch(self) -> None:
        with TemporaryDirectory() as directory:
            session = PaperSession(self.config, Path(directory) / "s.json", Path(directory) / "a.jsonl")
            session.set_kill_switch(True)
            allowed = session.process_demo_event(
                "blocked", "SPY", Market.EQUITY, 500, 485, datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
            )
            self.assertFalse(allowed)

    def test_live_broker_endpoint_is_rejected(self) -> None:
        with self.assertRaises(UnsafeBrokerEndpoint):
            PaperEndpointGuard.verify("https://api.alpaca.markets", True)
        with self.assertRaises(UnsafeBrokerEndpoint):
            PaperEndpointGuard.verify("https://paper-api.alpaca.markets", False)
        self.assertEqual(
            PaperEndpointGuard.verify("https://paper-api.alpaca.markets/", True), "https://paper-api.alpaca.markets"
        )


if __name__ == "__main__":
    unittest.main()
