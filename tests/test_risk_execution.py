"""Risk gate behaviour against the provisional limits in config/default.toml (project spec section 10)."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from localtrader.config import HARD_LIMITS, load_config, validate_risk
from localtrader.domain import Direction, Market, OrderIntent, Position
from localtrader.execution import FillModel, IdempotentPaperExecutor
from localtrader.portfolio import Portfolio
from localtrader.risk import RiskEngine

NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)


def intent(
    quantity: float = 1000,
    stop: float = 95,
    symbol: str = "AAPL",
    market: Market = Market.EQUITY,
    strategy: str = "stock_momentum",
    client_id: str = "id-1",
    cost_per_unit: float = 0.0,
) -> OrderIntent:
    return OrderIntent(
        client_id,
        strategy,
        symbol,
        market,
        NOW,
        Direction.LONG,
        quantity,
        "market",
        NOW + timedelta(minutes=5),
        stop,
        {"cost_per_unit": cost_per_unit},
    )


class RiskSizingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.risk = RiskEngine(self.config.risk)

    def test_default_limits_match_project_spec(self) -> None:
        r = self.config.risk
        self.assertEqual((r.risk_per_trade, r.max_open_risk, r.max_instrument_weight), (0.002, 0.01, 0.20))
        self.assertEqual((r.drawdown_reduce, r.drawdown_block_new, r.drawdown_kill), (0.05, 0.08, 0.10))
        self.assertEqual(r.max_total_exposure, 1.0)
        self.assertEqual(self.config.initial_cash, 10_000)
        self.assertEqual(self.config.currency, "EUR")

    def test_size_risks_point_two_percent_of_equity(self) -> None:
        # 10_000 * 0.2 % = 20 risk; stop distance 5 -> 4 units
        self.assertAlmostEqual(self.risk.size_for_stop(10_000, 100, 95), 4.0)

    def test_round_trip_costs_reduce_size(self) -> None:
        self.assertAlmostEqual(self.risk.size_for_stop(10_000, 100, 95, cost_per_unit=5), 2.0)

    def test_drawdown_above_five_percent_halves_budget(self) -> None:
        self.assertAlmostEqual(self.risk.size_for_stop(10_000, 100, 95, drawdown=0.05), 2.0)

    def test_invalid_inputs_size_zero(self) -> None:
        for args in [(0, 100, 95), (10_000, 100, 100), (10_000, 100, 105), (10_000, 100, -1)]:
            self.assertEqual(self.risk.size_for_stop(*args), 0.0)


class RiskDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.portfolio = Portfolio(10_000)
        self.risk = RiskEngine(self.config.risk, self.config.correlation_groups)

    def decide(self, order: OrderIntent, prices: dict[str, float] | None = None, **kwargs: float) -> object:
        return self.risk.decide(order, self.portfolio, prices or {"AAPL": 100}, NOW, 10_000, **kwargs)

    def test_approves_within_per_trade_risk(self) -> None:
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 10_000)
        self.assertTrue(decision.allowed)
        # 20 EUR risk / 1 EUR stop = 20 units, but 10 % stock weight caps at 10 units (1000 EUR)
        self.assertAlmostEqual(decision.approved_quantity, 10.0)
        self.assertIn("instrument_weight", decision.reason)

    def test_instrument_weight_includes_existing_position(self) -> None:
        self.portfolio.positions["AAPL"] = Position(
            "AAPL", Market.EQUITY, "s", quantity=8, average_entry=100, stop_price=99.9
        )
        self.portfolio.cash -= 800
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 10_000)
        self.assertAlmostEqual(decision.approved_quantity, 2.0)

    def test_open_stop_risk_budget_is_shared(self) -> None:
        # existing positions already carry 95 EUR planned stop risk of the 100 EUR (1 %) budget
        self.portfolio.positions["SPY"] = Position(
            "SPY", Market.ETF, "etf_trend", quantity=19, average_entry=100, stop_price=95
        )
        self.portfolio.cash -= 1900
        prices = {"AAPL": 100, "SPY": 100}
        decision = self.risk.decide(intent(stop=95, symbol="AAPL"), self.portfolio, prices, NOW, 10_000)
        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.approved_quantity, 1.0)
        self.assertIn("open_stop_risk", decision.reason)

    def test_position_without_stop_counts_full_value_as_risk(self) -> None:
        self.portfolio.positions["SPY"] = Position("SPY", Market.ETF, "x", quantity=10, average_entry=100)
        snapshot = self.portfolio.snapshot(NOW, {"SPY": 100})
        self.assertAlmostEqual(snapshot.open_risk, 1000)

    def test_invalid_stop_and_data_failure_reject(self) -> None:
        self.assertFalse(self.risk.decide(intent(stop=100), self.portfolio, {"AAPL": 100}, NOW, 10_000).allowed)
        self.risk.data_healthy = False
        decision = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 10_000)
        self.assertIn("data_quality", decision.violated_limits)

    def test_unreconciled_positions_and_pause_reject(self) -> None:
        self.risk.positions_reconciled = False
        self.risk.paused = True
        decision = self.risk.decide(intent(), self.portfolio, {"AAPL": 100}, NOW, 10_000)
        self.assertIn("position_mismatch", decision.violated_limits)
        self.assertIn("paused", decision.violated_limits)

    def test_eight_percent_drawdown_blocks_new_positions(self) -> None:
        self.portfolio.peak_equity = 10_000
        self.portfolio.cash = 9_200
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 9_200)
        self.assertEqual(decision.violated_limits, ("drawdown_block_new",))
        self.assertFalse(self.risk.kill_switch)

    def test_ten_percent_drawdown_latches_kill_switch_and_deleveraging(self) -> None:
        self.portfolio.peak_equity = 10_000
        self.portfolio.cash = 9_000
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 9_000)
        self.assertIn("drawdown_kill", decision.violated_limits)
        self.assertTrue(self.risk.kill_switch)
        self.assertTrue(self.risk.deleveraging_required)
        # recovery alone does not clear the latch
        self.portfolio.cash = 10_000
        later = self.risk.decide(intent(stop=99, client_id="x"), self.portfolio, {"AAPL": 100}, NOW, 10_000)
        self.assertIn("kill_switch", later.violated_limits)

    def test_daily_loss_limit(self) -> None:
        self.portfolio.cash = 9_840
        self.portfolio.peak_equity = 9_840
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 10_000)
        self.assertIn("daily_loss_limit", decision.violated_limits)

    def test_correlation_group_cap(self) -> None:
        # MSFT and NVDA share a group with AAPL; 3_900 EUR already in the group, cap is 40 % = 4_000 EUR
        for symbol in ("MSFT", "NVDA"):
            self.portfolio.positions[symbol] = Position(
                symbol, Market.EQUITY, "s", quantity=19.5, average_entry=100, stop_price=99.99
            )
        self.portfolio.cash -= 3_900
        prices = {"AAPL": 100, "MSFT": 100, "NVDA": 100}
        decision = self.risk.decide(intent(stop=99), self.portfolio, prices, NOW, 10_000)
        self.assertTrue(decision.allowed)
        self.assertAlmostEqual(decision.approved_quantity, 1.0)
        self.assertIn("correlation_group", decision.reason)

    def test_liquidity_cap_uses_bar_volume(self) -> None:
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 10_000, bar_volume=100)
        self.assertAlmostEqual(decision.approved_quantity, 5.0)

    def test_minimum_order_value(self) -> None:
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 10_000, bar_volume=8)
        self.assertEqual(decision.violated_limits, ("below_min_order_value",))

    def test_cash_cap_prevents_leverage(self) -> None:
        self.portfolio.cash = 60
        self.portfolio.peak_equity = 60
        decision = self.risk.decide(intent(stop=99), self.portfolio, {"AAPL": 100}, NOW, 60)
        self.assertFalse(decision.allowed)


class HighWaterMarkTests(unittest.TestCase):
    def test_deposits_and_withdrawals_do_not_change_drawdown(self) -> None:
        portfolio = Portfolio(10_000)
        portfolio.peak_equity = 11_000
        portfolio.deposit(1_000)
        self.assertAlmostEqual(portfolio.snapshot(NOW, {}).drawdown, 1 - 11_000 / 12_000)
        portfolio.withdraw(2_000)
        self.assertAlmostEqual(portfolio.snapshot(NOW, {}).drawdown, 1 - 9_000 / 10_000)

    def test_withdrawal_cannot_exceed_cash(self) -> None:
        with self.assertRaises(ValueError):
            Portfolio(100).withdraw(101)


class ConfigValidationTests(unittest.TestCase):
    def test_hard_limits_reject_risk_increase(self) -> None:
        base = load_config().risk
        for change in (
            {"risk_per_trade": 0.006, "max_risk_per_trade": 0.006},
            {"max_open_risk": HARD_LIMITS["max_open_risk"] * 2},
            {"max_total_exposure": 1.5},
            {"drawdown_kill": 0.2},
            {"drawdown_block_new": 0.04},
            {"max_instrument_weight": 0.5},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_risk(replace(base, **change))


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

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
        order, _ = FillModel(self.config.costs).fill(intent(), 10, 100, NOW, 0.5)
        self.assertEqual(order.filled_quantity, 5)
        self.assertEqual(order.status.value, "partially_filled")


if __name__ == "__main__":
    unittest.main()
