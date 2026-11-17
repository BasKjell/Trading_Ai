"""Cost accounting: spread and slippage are embedded in the fill price, the fee is charged in cash."""

from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from localtrader.config import CostConfig
from localtrader.domain import Direction, Market, OrderIntent
from localtrader.execution import FillModel
from localtrader.portfolio import Portfolio

NOW = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
COSTS = CostConfig(
    equity_fee_bps=10.0,
    equity_spread_bps=4.0,
    equity_slippage_bps=1.0,
    equity_min_fee=0.0,
    crypto_fee_bps=10.0,
    crypto_spread_bps=8.0,
    crypto_slippage_bps=4.0,
    crypto_min_fee=0.0,
)


def _intent(direction: Direction, client_id: str) -> OrderIntent:
    return OrderIntent(
        client_id, "s", "X", Market.EQUITY, NOW, direction, 10, "market", NOW + timedelta(minutes=5), 90.0, {}
    )


class CostAccountingTests(unittest.TestCase):
    def test_round_trip_charges_each_cost_exactly_once(self) -> None:
        # Regression: spread and slippage were applied in the fill price AND deducted again from cash.
        model = FillModel(COSTS)
        portfolio = Portfolio(10_000)
        _, buy = model.fill(_intent(Direction.LONG, "b"), 10, 100.0, NOW)
        portfolio.apply_fill(buy, "s", 90.0)
        _, sell = model.fill(_intent(Direction.FLAT, "s"), 10, 100.0, NOW)
        portfolio.apply_fill(sell, "s")
        # half spread 2 bps + slippage 1 bp per side = 3 bps of 1000 notional per side; fee 10 bps per side
        expected_loss = 2 * (1000 * 3 / 10_000) + 2 * (1000 * 10 / 10_000)
        self.assertAlmostEqual(10_000 - portfolio.cash, expected_loss, places=9)
        self.assertAlmostEqual(portfolio.realized_costs, expected_loss, places=9)

    def test_buy_cash_debit_is_price_times_quantity_plus_fee(self) -> None:
        _, buy = FillModel(COSTS).fill(_intent(Direction.LONG, "b"), 10, 100.0, NOW)
        portfolio = Portfolio(10_000)
        portfolio.apply_fill(buy, "s", 90.0)
        self.assertAlmostEqual(10_000 - portfolio.cash, 10 * buy.price + buy.fee, places=9)

    def test_minimum_fee_applies_to_small_orders(self) -> None:
        costs = replace(COSTS, equity_min_fee=1.5)
        _, small = FillModel(costs).fill(_intent(Direction.LONG, "b"), 1, 100.0, NOW)
        self.assertAlmostEqual(small.fee, 1.5)
        _, large = FillModel(costs).fill(_intent(Direction.LONG, "c"), 100, 100.0, NOW)
        self.assertAlmostEqual(large.fee, 10.0)

    def test_cost_multiplier_scales_fee_and_price_impact(self) -> None:
        _, base = FillModel(COSTS).fill(_intent(Direction.LONG, "b"), 10, 100.0, NOW)
        _, stressed = FillModel(COSTS, multiplier=2.0).fill(_intent(Direction.LONG, "b"), 10, 100.0, NOW)
        self.assertAlmostEqual(stressed.fee, 2 * base.fee)
        self.assertAlmostEqual(stressed.price - 100.0, 2 * (base.price - 100.0))

    def test_round_trip_cost_estimate_matches_actual_round_trip(self) -> None:
        model = FillModel(COSTS)
        _, buy = model.fill(_intent(Direction.LONG, "b"), 10, 100.0, NOW)
        _, sell = model.fill(_intent(Direction.FLAT, "s"), 10, 100.0, NOW)
        actual = (buy.price - sell.price) * 10 + buy.fee + sell.fee
        self.assertAlmostEqual(model.round_trip_cost_per_unit(Market.EQUITY, 100.0) * 10, actual, places=9)


if __name__ == "__main__":
    unittest.main()
