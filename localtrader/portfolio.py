from __future__ import annotations

from datetime import datetime

from .domain import Fill, Market, PortfolioSnapshot, Position


class Portfolio:
    def __init__(self, initial_cash: float) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.initial_cash = initial_cash
        self.cash = initial_cash
        self.positions: dict[str, Position] = {}
        self.peak_equity = initial_cash
        self.realized_costs = 0.0

    def market_value(self, prices: dict[str, float]) -> float:
        return sum(
            position.quantity * prices.get(symbol, position.average_entry)
            for symbol, position in self.positions.items()
        )

    def equity(self, prices: dict[str, float]) -> float:
        return self.cash + self.market_value(prices)

    def exposure(self, prices: dict[str, float], market: Market | None = None) -> float:
        return sum(
            abs(position.quantity * prices.get(symbol, position.average_entry))
            for symbol, position in self.positions.items()
            if market is None or position.market == market
        )

    def apply_fill(self, fill: Fill, strategy: str, stop_price: float | None = None) -> None:
        total_cost = fill.fee + fill.spread_cost + fill.slippage_cost
        position = self.positions.get(fill.symbol)
        if fill.quantity > 0:
            gross = fill.quantity * fill.price
            if gross + total_cost > self.cash + 1e-8:
                raise ValueError("fill would create leverage")
            if position is None:
                position = Position(fill.symbol, fill.market, strategy)
                self.positions[fill.symbol] = position
            new_quantity = position.quantity + fill.quantity
            position.average_entry = (
                position.average_entry * position.quantity + fill.price * fill.quantity
            ) / new_quantity
            position.quantity = new_quantity
            position.stop_price = stop_price
            self.cash -= gross + total_cost
        elif fill.quantity < 0:
            if position is None or -fill.quantity > position.quantity + 1e-8:
                raise ValueError("naked shorting is prohibited")
            sold = min(position.quantity, -fill.quantity)
            gross = sold * fill.price
            position.realized_pnl += sold * (fill.price - position.average_entry) - total_cost
            position.quantity -= sold
            self.cash += gross - total_cost
            if position.quantity <= 1e-10:
                del self.positions[fill.symbol]
        self.realized_costs += total_cost

    def snapshot(self, timestamp: datetime, prices: dict[str, float]) -> PortfolioSnapshot:
        equity = self.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = 0.0 if self.peak_equity <= 0 else max(0.0, 1 - equity / self.peak_equity)
        open_risk = sum(
            max(0.0, (position.average_entry - (position.stop_price or position.average_entry)) * position.quantity)
            for position in self.positions.values()
        )
        return PortfolioSnapshot(
            timestamp=timestamp,
            cash=self.cash,
            equity=equity,
            gross_exposure=self.exposure(prices),
            open_risk=open_risk,
            drawdown=drawdown,
            peak_equity=self.peak_equity,
        )
