from __future__ import annotations

from datetime import datetime
import math

from .config import RiskConfig
from .domain import Market, OrderIntent, RiskDecision
from .portfolio import Portfolio


class RiskEngine:
    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.kill_switch = False
        self.paused = False
        self.data_healthy = True
        self.positions_reconciled = True

    def size_for_stop(self, equity: float, entry: float, stop: float) -> float:
        distance = entry - stop
        if equity <= 0 or entry <= 0 or stop <= 0 or distance <= 0:
            return 0.0
        risk_cash = equity * min(self.config.risk_per_trade, self.config.max_risk_per_trade)
        return max(0.0, risk_cash / distance)

    def decide(
        self,
        intent: OrderIntent,
        portfolio: Portfolio,
        prices: dict[str, float],
        now: datetime,
        day_start_equity: float,
        experimental: bool = False,
    ) -> RiskDecision:
        violations: list[str] = []
        price = prices.get(intent.symbol, 0.0)
        snapshot = portfolio.snapshot(now, prices)
        if self.kill_switch:
            violations.append("kill_switch")
        if self.paused:
            violations.append("paused")
        if not self.data_healthy:
            violations.append("data_quality")
        if not self.positions_reconciled:
            violations.append("position_mismatch")
        if now > intent.valid_until or price <= 0:
            violations.append("invalid_or_expired_price")
        if day_start_equity > 0 and snapshot.equity / day_start_equity - 1 <= -self.config.daily_loss_limit:
            violations.append("daily_loss_limit")
        if snapshot.drawdown >= self.config.drawdown_kill:
            self.kill_switch = True
            violations.append("drawdown_kill")
        if experimental and snapshot.drawdown >= self.config.drawdown_block_experimental:
            violations.append("experimental_blocked")
        if intent.target_quantity <= 0:
            violations.append("non_positive_quantity")
        if intent.stop_price is None or intent.stop_price <= 0 or intent.stop_price >= price:
            violations.append("invalid_stop")
        if violations:
            return RiskDecision(False, 0.0, tuple(violations), "fail-closed rejection", now)

        raw = min(intent.target_quantity, self.size_for_stop(snapshot.equity, price, intent.stop_price))
        weight_cap = (
            self.config.max_crypto_weight if intent.market == Market.CRYPTO
            else self.config.max_etf_weight if intent.strategy == "etf_trend"
            else self.config.max_stock_weight
        )
        if snapshot.drawdown >= self.config.drawdown_reduce:
            weight_cap *= 0.5
            raw *= 0.5
        cash_cap = portfolio.cash / price
        symbol_cap = snapshot.equity * weight_cap / price
        total_room = max(0.0, snapshot.equity * self.config.max_total_exposure - snapshot.gross_exposure) / price
        crypto_room = float("inf")
        if intent.market == Market.CRYPTO:
            crypto_room = max(0.0, snapshot.equity * self.config.max_crypto_exposure - portfolio.exposure(prices, Market.CRYPTO)) / price
        approved = max(0.0, min(raw, cash_cap, symbol_cap, total_room, crypto_room))
        approved = math.floor(approved * 1_000_000) / 1_000_000
        if approved <= 0:
            return RiskDecision(False, 0.0, ("exposure_or_cash_limit",), "no capacity", now)
        return RiskDecision(True, approved, (), "approved within deterministic limits", now)

