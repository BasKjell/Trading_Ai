from datetime import UTC, datetime, timedelta
import unittest

from localtrader.config import load_config
from localtrader.domain import Direction, Market, OrderIntent
from localtrader.execution import FillModel, IdempotentPaperExecutor
from localtrader.portfolio import Portfolio
from localtrader.risk import RiskEngine


NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def intent(quantity: float = 1000, stop: float = 95) -> OrderIntent:
    return OrderIntent("id-1", "stock_momentum", "AAPL", Market.EQUITY, NOW, Direction.LONG, quantity, "market", NOW + timedelta(minutes=5), stop, {})


class RiskExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.portfolio = Portfolio(100_000)
        self.risk = RiskEngine(self.config.risk)

    def test_position_size_and_half_percent_cap(self) -> None:
        self.assertEqual(self.risk.size_for_stop(100_000, 100, 95), 50)
        self.assertLessEqual(self.risk.size_for_stop(100_000, 100, 95) * 5, 500)

    def test_invalid_stop_and_data_failure_reject(self) -> None:
        decision = self.risk.decide(intent(stop=100), self.portfolio, {"AAPL": 100}, NOW, 100_000)
        self.assertFalse(decision.allowed)
        self.risk.data_healthy = False
        decision = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 100_000)
        self.assertIn("data_quality", decision.violated_limits)

    def test_drawdown_ladder_and_daily_loss(self) -> None:
        self.portfolio.peak_equity = 100_000
        self.portfolio.cash = 85_000
        decision = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 100_000)
        self.assertFalse(decision.allowed)
        self.assertTrue(self.risk.kill_switch)
        self.assertIn("drawdown_kill", decision.violated_limits)

    def test_twelve_percent_blocks_experimental_and_daily_limit(self) -> None:
        self.portfolio.peak_equity = 100_000
        self.portfolio.cash = 88_000
        decision = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 88_000, experimental=True)
        self.assertFalse(decision.allowed)
        self.assertIn("experimental_blocked", decision.violated_limits)
        fresh = Portfolio(100_000)
        fresh.cash = 98_500
        daily = RiskEngine(self.config.risk).decide(intent(), fresh, {"AAPL": 100}, NOW, 100_000)
        self.assertFalse(daily.allowed)
        self.assertIn("daily_loss_limit", daily.violated_limits)

    def test_eight_percent_reduces_size(self) -> None:
        base = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 100_000)
        self.portfolio.peak_equity = 110_000
        reduced = self.risk.decide(OrderIntent("id-2", "stock_momentum", "AAPL", Market.EQUITY, NOW, Direction.LONG, 1000, "market", NOW + timedelta(minutes=5), 95, {}), self.portfolio, {"AAPL": 100}, NOW, 100_000)
        self.assertLess(reduced.approved_quantity, base.approved_quantity)

    def test_concentration_cap(self) -> None:
        decision = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 100_000)
        self.assertLessEqual(decision.approved_quantity * 100, 10_000)

    def test_costs_and_idempotency(self) -> None:
        executor = IdempotentPaperExecutor(FillModel(self.config.costs))
        order, fill = executor.execute(intent(), 10, 100, NOW)
        duplicate, second_fill = executor.execute(intent(), 10, 100, NOW)
        self.assertEqual(order.order_id, duplicate.order_id)
        self.assertIsNone(second_fill)
        assert fill is not None
        self.assertGreater(fill.price, 100)
        self.assertGreater(fill.fee + fill.spread_cost + fill.slippage_cost, 0)

    def test_partial_fill_status(self) -> None:
        order, fill = FillModel(self.config.costs).fill(intent(), 10, 100, NOW, 0.5)
        self.assertEqual(order.filled_quantity, 5)
        self.assertEqual(order.status.value, "partially_filled")


if __name__ == "__main__":
    unittest.main()
