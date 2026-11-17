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
        """Book a fill.

        Spread and slippage are already embedded in ``fill.price``; only the explicit fee is charged
        in cash. ``realized_costs`` tracks all three components for reporting.
        """
        position = self.positions.get(fill.symbol)
        if fill.quantity > 0:
            gross = fill.quantity * fill.price
            if gross + fill.fee > self.cash + 1e-8:
                raise ValueError("fill would create leverage")
            if position is None:
                position = Position(fill.symbol, fill.market, strategy)
                self.positions[fill.symbol] = position
            new_quantity = position.quantity + fill.quantity
            position.average_entry = (
                position.average_entry * position.quantity + fill.price * fill.quantity
            ) / new_quantity
            position.quantity = new_quantity
            position.entry_fees += fill.fee
            position.stop_price = stop_price
            self.cash -= gross + fill.fee
        elif fill.quantity < 0:
            if position is None or -fill.quantity > position.quantity + 1e-8:
                raise ValueError("naked shorting is prohibited")
            sold = min(position.quantity, -fill.quantity)
            entry_fee_share = position.entry_fees * sold / position.quantity
            position.entry_fees -= entry_fee_share
            position.realized_pnl += sold * (fill.price - position.average_entry) - fill.fee - entry_fee_share
            position.quantity -= sold
            self.cash += sold * fill.price - fill.fee
            if position.quantity <= 1e-10:
                del self.positions[fill.symbol]
        self.realized_costs += fill.fee + fill.spread_cost + fill.slippage_cost

    @staticmethod
    def position_stop_risk(position: Position, prices: dict[str, float]) -> float:
        """Planned loss from the current price to the protective stop.

        A position without a stop counts with its full market value (conservative). This is a planning
        figure, not a loss guarantee: gaps can fill below the stop.
        """
        price = prices.get(position.symbol, position.average_entry)
        if position.stop_price is None or position.stop_price <= 0:
            return position.quantity * price
        return max(0.0, (price - position.stop_price) * position.quantity)

    def charge(self, amount: float) -> None:
        """Non-trade cost (e.g. overnight financing). Counted as cost, may reduce cash below zero only
        through costs, never through purchases."""
        if amount < 0:
            raise ValueError("charge must be non-negative")
        self.cash -= amount
        self.realized_costs += amount

    def deposit(self, amount: float) -> None:
        """External cash flow in. The high-water mark moves by the same amount, so flows are not P&L."""
        if amount <= 0:
            raise ValueError("deposit must be positive")
        self.cash += amount
        self.peak_equity += amount

    def withdraw(self, amount: float) -> None:
        """External cash flow out. Reduces cash and the high-water mark equally; never creates leverage."""
        if amount <= 0:
            raise ValueError("withdrawal must be positive")
        if amount > self.cash + 1e-8:
            raise ValueError("withdrawal exceeds available cash")
        self.cash -= amount
        self.peak_equity = max(0.0, self.peak_equity - amount)

    def snapshot(self, timestamp: datetime, prices: dict[str, float]) -> PortfolioSnapshot:
        equity = self.equity(prices)
        self.peak_equity = max(self.peak_equity, equity)
        drawdown = 0.0 if self.peak_equity <= 0 else max(0.0, 1 - equity / self.peak_equity)
        open_risk = sum(self.position_stop_risk(position, prices) for position in self.positions.values())
        return PortfolioSnapshot(
            timestamp=timestamp,
            cash=self.cash,
            equity=equity,
            gross_exposure=self.exposure(prices),
            open_risk=open_risk,
            drawdown=drawdown,
            peak_equity=self.peak_equity,
        )
