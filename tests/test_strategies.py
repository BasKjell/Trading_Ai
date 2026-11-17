import unittest

from localtrader.data import group_by_symbol, synthetic_fixture
from localtrader.domain import Direction
from localtrader.strategies import ETFTrendStrategy, StockMomentumStrategy, atr, sma


class StrategyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.grouped = group_by_symbol(synthetic_fixture(22, 400))

    def test_indicators_use_only_supplied_history(self) -> None:
        values = list(range(1, 11))
        self.assertEqual(sma(values, 3), 9)
        self.assertEqual(sma(values[:-1], 3), 8)
        full = atr(self.grouped["SPY"][:100], 20)
        truncated = atr(self.grouped["SPY"][:99], 20)
        self.assertNotEqual(full, truncated)

    def test_signal_executes_after_close(self) -> None:
        strategy = ETFTrendStrategy(fast_window=3, slow_window=5, breakout_window=3, atr_window=2)
        signal = strategy.evaluate(self.grouped["SPY"][:250])
        if signal is not None:
            self.assertGreater(signal.earliest_execution, signal.signal_time)

    def test_stock_ranking_is_bounded(self) -> None:
        signals = StockMomentumStrategy(top_n=2).rank({k: self.grouped[k] for k in ("AAPL", "MSFT", "NVDA")})
        self.assertLessEqual(sum(signal.direction == Direction.LONG for signal in signals), 2)


if __name__ == "__main__":
    unittest.main()
