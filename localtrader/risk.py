"""Independent, deterministic risk gate. Every entry order passes through :meth:`RiskEngine.decide`.

Definitions
-----------
* Equity: cash plus mark-to-market value of open positions (unrealised P&L included).
* High-water mark (HWM): highest equity observed in snapshots; deposits/withdrawals shift it by the
  same amount (see :class:`Portfolio`), so external cash flows are not counted as gains or losses.
* Drawdown: ``1 - equity / HWM``.
* Drawdown ladder (defaults): >= 5 % halve the per-trade risk budget, >= 8 % no new positions,
  >= 10 % latch the kill switch and require controlled de-risking plus manual review.
* Re-enabling after a kill-switch latch is manual only (``trader kill-switch off``); a restart never
  clears it because the paper state persists the flag.

Exits and protective stops are never blocked by this gate.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import datetime

from .config import RiskConfig
from .domain import Market, OrderIntent, RiskDecision
from .portfolio import Portfolio

# Thresholds are inclusive; a tolerance prevents float artefacts (1 - 9000/10000 = 0.0999...) from
# letting a position slip just past a limit.
_EPS = 1e-9


def _reached(value: float, threshold: float) -> bool:
    return value >= threshold - _EPS


class RiskEngine:
    def __init__(
        self,
        config: RiskConfig,
        groups: Mapping[str, str] | None = None,
        *,
        reconciliation_ok: Callable[[], bool] | None = None,
    ) -> None:
        self.config = config
        self.groups = dict(groups or {})
        self.kill_switch = False
        self.deleveraging_required = False
        self.paused = False
        self.data_healthy = True
        self.positions_reconciled = True
        self._reconciliation_ok = reconciliation_ok

    def per_trade_risk_fraction(self, drawdown: float = 0.0) -> float:
        fraction = min(self.config.risk_per_trade, self.config.max_risk_per_trade)
        if _reached(drawdown, self.config.drawdown_reduce):
            fraction *= 0.5
        return fraction

    def size_for_stop(
        self, equity: float, entry: float, stop: float, cost_per_unit: float = 0.0, drawdown: float = 0.0
    ) -> float:
        """Quantity whose loss from ``entry`` to ``stop`` plus round-trip costs equals the risk budget."""
        distance = entry - stop
        if equity <= 0 or entry <= 0 or stop <= 0 or distance <= 0 or cost_per_unit < 0:
            return 0.0
        risk_cash = equity * self.per_trade_risk_fraction(drawdown)
        return max(0.0, risk_cash / (distance + cost_per_unit))

    def _weight_cap(self, intent: OrderIntent) -> float:
        if intent.market == Market.CRYPTO:
            cap = self.config.max_crypto_weight
        elif intent.market == Market.ETF or intent.strategy == "etf_trend":
            cap = self.config.max_etf_weight
        elif intent.market == Market.EQUITY:
            cap = self.config.max_stock_weight
        else:
            cap = self.config.max_instrument_weight
        return min(cap, self.config.max_instrument_weight)

    def decide(
        self,
        intent: OrderIntent,
        portfolio: Portfolio,
        prices: dict[str, float],
        now: datetime,
        day_start_equity: float,
        bar_volume: float | None = None,
    ) -> RiskDecision:
        violations: list[str] = []
        price = prices.get(intent.symbol, 0.0)
        snapshot = portfolio.snapshot(now, prices)
        if _reached(snapshot.drawdown, self.config.drawdown_kill):
            self.kill_switch = True
            self.deleveraging_required = True
            violations.append("drawdown_kill")
        elif _reached(snapshot.drawdown, self.config.drawdown_block_new):
            violations.append("drawdown_block_new")
        if self.kill_switch and "drawdown_kill" not in violations:
            violations.append("kill_switch")
        if self.paused:
            violations.append("paused")
        if not self.data_healthy:
            violations.append("data_quality")
        if not self.positions_reconciled or (self._reconciliation_ok is not None and not self._reconciliation_ok()):
            violations.append("position_mismatch")
        if now > intent.valid_until or price <= 0:
            violations.append("invalid_or_expired_price")
        if day_start_equity > 0 and _reached(1 - snapshot.equity / day_start_equity, self.config.daily_loss_limit):
            violations.append("daily_loss_limit")
        if intent.target_quantity <= 0:
            violations.append("non_positive_quantity")
        if intent.stop_price is None or intent.stop_price <= 0 or intent.stop_price >= price:
            violations.append("invalid_stop")
        if violations or intent.stop_price is None:
            return RiskDecision(False, 0.0, tuple(violations), "fail-closed rejection", now)

        cost_per_unit = float(intent.risk_metadata.get("cost_per_unit", 0.0))
        stop_distance = price - intent.stop_price + cost_per_unit
        caps: dict[str, float] = {
            "target": intent.target_quantity,
            "per_trade_risk": self.size_for_stop(
                snapshot.equity, price, intent.stop_price, cost_per_unit, snapshot.drawdown
            ),
            "open_stop_risk": max(0.0, snapshot.equity * self.config.max_open_risk - snapshot.open_risk)
            / stop_distance,
            "cash": portfolio.cash / (price * (1 + 1e-4) + cost_per_unit / 2),
            "instrument_weight": snapshot.equity * self._weight_cap(intent) / price,
            "gross_exposure": max(0.0, snapshot.equity * self.config.max_total_exposure - snapshot.gross_exposure)
            / price,
        }
        existing = portfolio.positions.get(intent.symbol)
        if existing is not None:
            caps["instrument_weight"] = max(
                0.0, caps["instrument_weight"] - existing.quantity * prices.get(intent.symbol, price) / price
            )
        if intent.market == Market.CRYPTO:
            caps["crypto_exposure"] = (
                max(0.0, snapshot.equity * self.config.max_crypto_exposure - portfolio.exposure(prices, Market.CRYPTO))
                / price
            )
        group = self.groups.get(intent.symbol)
        if group is not None:
            group_value = sum(
                position.quantity * prices.get(symbol, position.average_entry)
                for symbol, position in portfolio.positions.items()
                if self.groups.get(symbol) == group
            )
            caps["correlation_group"] = max(0.0, snapshot.equity * self.config.max_group_weight - group_value) / price
        if bar_volume is not None:
            caps["liquidity"] = max(0.0, bar_volume * self.config.max_volume_participation)
        binding = min(caps, key=lambda name: caps[name])
        approved = math.floor(max(0.0, caps[binding]) * 1_000_000) / 1_000_000
        if approved <= 0:
            return RiskDecision(False, 0.0, (f"no_capacity:{binding}",), "no capacity", now)
        if approved * price < self.config.min_order_value:
            return RiskDecision(False, 0.0, ("below_min_order_value",), f"order value below minimum ({binding})", now)
        return RiskDecision(True, approved, (), f"approved; binding limit: {binding}", now)
