"""Role-local, offline replay for T-008. All times, cash flows and price/volume inputs are explicit."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_DOWN, Decimal
from typing import Any

from .calendars import expected_bar_starts
from .config import AppConfig
from .domain import Bar, Direction, Market, OrderIntent, Position
from .fx_rates import FxSeries
from .intraday_strategies import IntradayStrategy, StrategyContext
from .portfolio import Portfolio
from .risk import RiskEngine
from .validation_costs import ONE, ZERO, CashBook, Rates, amount, running_cost
from .validation_stats import Sleeve
from .walkforward import NY, RoleHistory, RoleWindow

UNIVERSE = ("QQQ", "SPY")


@dataclass
class Holding:
    quantity: Decimal
    signal: datetime
    entry: datetime
    price: Decimal
    stop: Decimal | None


@dataclass
class Pending:
    buy: bool
    signal: datetime
    stop: Decimal | None = None
    remaining: Decimal | None = None
    opens: int = 0
    urgent: bool = False


@dataclass
class RoleMarket:
    window: RoleWindow
    bars: dict[str, dict[datetime, Bar]]
    histories: dict[str, RoleHistory]
    dividends: dict[str, dict[date, tuple[date, float]]]
    fx: FxSeries

    @classmethod
    def prepare(
        cls,
        window: RoleWindow,
        bars: dict[str, list[Bar]],
        dividends: dict[str, dict[date, tuple[date, float]]],
        fx: FxSeries,
    ) -> RoleMarket:
        if set(bars) != set(UNIVERSE) or set(dividends) != set(UNIVERSE):
            raise ValueError("need both frozen ETF inputs")
        slots = {t for s in (*window.embargo, *window.scored, *window.purge) for t in expected_bar_starts(s.day)}
        indexed = {}
        histories = {}
        for symbol in UNIVERSE:
            rows = [b for b in bars[symbol] if window.start <= b.start.astimezone(NY).date() < window.end]
            by_start = {b.start: b for b in rows}
            if (
                len(rows) != len(by_start)
                or set(by_start) != slots
                or any(
                    not all(math.isfinite(x) for x in (b.open, b.high, b.low, b.close, b.volume))
                    or not (0 < b.low <= min(b.open, b.close) <= max(b.open, b.close) <= b.high and b.volume >= 0)
                    or b.symbol != symbol
                    or b.market != Market.ETF
                    or b.received_at < b.end
                    or b.received_at >= window.research_cutoff
                    for b in rows
                )
            ):
                raise ValueError("research bar inventory or availability mismatch")
            indexed[symbol] = by_start
            histories[symbol] = RoleHistory(window, symbol, rows)
        return cls(window, indexed, histories, dividends, fx)


@dataclass
class ReplayResult:
    sleeve: Sleeve
    weights: dict[str, float]
    # All intra-session observations for chained drawdown across normalized fold paths.
    marks: list[float]
    transfers: list[dict[str, str]]
    costs_eur: dict[str, float]
    fills: list[dict[str, str]] = field(default_factory=list)
    dividends: list[dict[str, str]] = field(default_factory=list)
    max_underwater_seconds: float = 0.0
    intervals: int = 0


@dataclass
class Replay:
    market: RoleMarket
    config: AppConfig
    schedule: dict[str, Any]
    annual_cost: Decimal
    scenario: str
    strategy: IntradayStrategy | None = None
    passive: dict[str, float] | None = None
    positions: dict[str, Holding] = field(default_factory=dict, init=False)
    pending: dict[str, Pending] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if (self.strategy is None) == (self.passive is None):
            raise ValueError("choose exactly one evaluated sleeve")
        if self.passive is not None and (
            set(self.passive) - set(UNIVERSE) or sum(amount(v) for v in self.passive.values()) > ONE
        ):
            raise ValueError("invalid unlevered passive weights")
        for symbol in UNIVERSE:
            instrument = self.config.raw["instruments"][symbol]
            if instrument["min_fee"] != 0 or instrument["financing_rate_annual"] != 0:
                raise ValueError("unsupported fee minimum or financing in frozen ETF inputs")
        self.fills: list[dict[str, str]] = []
        self.dividends: list[dict[str, str]] = []
        self.last_high = self.market.window.scored[0].opens
        self.max_underwater_seconds = 0.0
        self.cash = CashBook(amount(self.config.initial_cash), self.market.fx)
        self.risk = RiskEngine(self.config.risk, self.config.correlation_groups)
        self.peak = amount(self.config.initial_cash)
        self.dd = 0.0
        self.marks: list[float] = []
        self.trips = 0
        self.months: set[tuple[int, int]] = set()
        self.receivables: dict[date, Decimal] = defaultdict(Decimal)
        self.costs: dict[str, Decimal] = defaultdict(Decimal)
        self.last_signal_bar: dict[str, datetime] = {}
        self.rates: dict[tuple[date, str, str], Rates] = {}

    def _rates(self, symbol: str, day: date, side: str) -> Rates:
        key = (day, symbol, side)
        if key not in self.rates:
            self.rates[key] = Rates.for_fill(self.schedule, symbol, day, side, self.scenario)
        return self.rates[key]

    def _usd_assets(self, prices: dict[str, Decimal]) -> Decimal:
        return sum(self.receivables.values(), ZERO) + sum(
            (h.quantity * prices[s] for s, h in self.positions.items()), ZERO
        )

    def _mark(self, now: datetime, prices: dict[str, Decimal]) -> Decimal:
        equity = self.cash.value(self._usd_assets(prices), now)
        if equity <= 0:
            raise ValueError("nonpositive portfolio value")
        if equity < self.peak:
            self.max_underwater_seconds = max(self.max_underwater_seconds, (now - self.last_high).total_seconds())
        else:
            self.last_high = now
        self.peak = max(self.peak, equity)
        self.dd = max(self.dd, float(ONE - equity / self.peak))
        if self.strategy is not None and self.dd >= self.config.risk.drawdown_kill:
            self.risk.kill_switch = True
        self.marks.append(float(equity))
        return equity

    def _approve(
        self, symbol: str, t: datetime, prices: dict[str, Decimal], stop: Decimal, cap: Decimal, day_start: float
    ) -> Decimal:
        mid = amount(self.market.fx.rate_at(t).usd_per_eur)
        book = Portfolio(self.config.initial_cash)
        book.cash = float(self.cash.eur + self.cash.usd / mid)
        book.income_receivable = float(sum(self.receivables.values(), ZERO) / mid)
        book.peak_equity = float(self.peak)
        for s, h in self.positions.items():
            book.positions[s] = Position(
                s,
                Market.ETF,
                "T008",
                float(h.quantity),
                float(h.price / mid),
                stop_price=float(h.stop / mid) if h.stop else None,
            )
        day = t.astimezone(NY).date()
        px = prices[symbol]
        buy = self._rates(symbol, day, "buy")
        sell = self._rates(symbol, day, "sell")
        b = buy.fill(px, ONE)
        e = sell.fill(px, -ONE)
        # Conservative per-unit round trip including two FX conversions for sizing. Actual ledger fees
        # are incurred only on transfers; these estimates are not booked as extra costs.
        estimate = (
            b.fees_usd
            + e.fees_usd
            + b.spread_usd
            + e.spread_usd
            + b.slippage_usd
            + e.slippage_usd
            + 2 * px * buy.fx * buy.multiplier
        ) / mid
        intent = OrderIntent(
            "T008",
            "T008",
            symbol,
            Market.ETF,
            t,
            Direction.LONG,
            float(cap),
            "market",
            t,
            float(stop / mid),
            {"cost_per_unit": float(estimate)},
        )
        decision = self.risk.decide(intent, book, {s: float(p / mid) for s, p in prices.items()}, t, day_start)
        return amount(decision.approved_quantity) if decision.allowed else ZERO

    def _fill(
        self,
        symbol: str,
        order: Pending,
        t: datetime,
        px: Decimal,
        cap: Decimal,
        prices: dict[str, Decimal],
        day_start: float,
    ) -> Decimal:
        if cap <= 0:
            return ZERO
        day = t.astimezone(NY).date()
        rates = self._rates(symbol, day, "buy" if order.buy else "sell")
        mid = amount(self.market.fx.rate_at(t).usd_per_eur)
        step = amount(self.config.raw["instruments"][symbol]["qty_step"])
        holding = self.positions.get(symbol)
        if order.buy:
            if self.strategy is not None:
                if order.stop is None or order.stop >= px:
                    return ZERO
                approved = self._approve(symbol, t, prices, order.stop, Decimal("1e12"), day_start)
                if order.remaining is None:
                    order.remaining = approved
            elif order.remaining is None:
                raise ValueError("missing passive order quantity")
            else:
                approved = order.remaining
            # Buy-side fees are linear in quantity in the bound table.
            unit = rates.fill(px, ONE)
            buying_power = self.cash.usd + max(ZERO, self.cash.eur - self.expense_reserve) * mid / (
                ONE + rates.fx * rates.multiplier
            )
            max_cash = buying_power / (unit.execution_usd + unit.fees_usd)
            qty = min(cap, order.remaining, approved, max_cash)
        else:
            if holding is None:
                return ZERO
            qty = min(cap, holding.quantity)
        qty = (qty / step).to_integral_value(rounding=ROUND_DOWN) * step
        if qty <= 0:
            return ZERO
        signed = qty if order.buy else -qty
        cost = rates.fill(px, signed)
        self.cash.trade_cash(-signed * cost.execution_usd - cost.fees_usd, t, rates)
        self.fills.append(
            {
                "symbol": symbol,
                "time": t.isoformat(),
                "signal": order.signal.isoformat(),
                "quantity": str(signed),
                "raw_usd": str(px),
                "execution_usd": str(cost.execution_usd),
                "fees_usd": str(cost.fees_usd),
                "usd_per_eur": str(mid),
                "fx_available_at": self.market.fx.rate_at(t).available_at.isoformat(),
            }
        )
        self.costs["fees"] += cost.fees_usd / mid
        self.costs["spread"] += cost.spread_usd / mid
        self.costs["slippage"] += cost.slippage_usd / mid
        if order.buy:
            assert order.remaining is not None
            order.remaining -= qty
            if holding:
                holding.price = (holding.price * holding.quantity + cost.execution_usd * qty) / (holding.quantity + qty)
                holding.quantity += qty
            else:
                self.positions[symbol] = Holding(qty, order.signal, t, cost.execution_usd, order.stop)
                self.months.add((day.year, day.month))
        elif holding:
            holding.quantity -= qty
            if not holding.quantity:
                if self.strategy:
                    self.market.histories[symbol].validate_trade_span(holding.signal, holding.entry, t)
                self.trips += 1
                del self.positions[symbol]
        self._mark(t, prices)
        return qty

    def run(self) -> ReplayResult:
        w = self.market.window
        previous = w.scored[0].day
        daily = []
        exposures = dict.fromkeys(UNIVERSE, ZERO)
        observations = 0
        previous_close = Decimal(str(self.config.initial_cash))
        h: Holding | None
        for index, session in enumerate(w.scored):
            day = session.day
            fee = running_cost(previous, day + timedelta(days=1), self.annual_cost)
            self.expense_reserve = running_cost(
                day + timedelta(days=1), w.scored[-1].day + timedelta(days=1), self.annual_cost
            )
            self.cash.eur -= fee
            if self.cash.eur < -Decimal("1e-18"):
                raise ValueError("unfunded operating expenses")
            self.costs["running"] += fee
            previous = day + timedelta(days=1)
            for symbol, h in self.positions.items():
                action = self.market.dividends[symbol].get(day)
                if action:
                    pay, value = action
                    if pay < day:
                        raise ValueError("payment precedes entitlement")
                    entitlement = (
                        amount(value)
                        * h.quantity
                        * (ONE - amount(self.config.raw["instruments"][symbol]["dividend_withholding"]))
                    )
                    self.receivables[pay] += entitlement
                    self.dividends.append(
                        {
                            "symbol": symbol,
                            "ex_date": day.isoformat(),
                            "pay_date": pay.isoformat(),
                            "quantity": str(h.quantity),
                            "net_usd": str(entitlement),
                            "recognition_usd_per_eur": str(self.market.fx.rate_at(session.opens).usd_per_eur),
                        }
                    )
            for pay in [d for d in self.receivables if d <= day]:
                self.cash.usd += self.receivables.pop(pay)
            for t in expected_bar_starts(day):
                rows = {s: self.market.bars[s][t] for s in UNIVERSE}
                prices = {s: amount(b.open) for s, b in rows.items()}
                remaining_caps: dict[str, Decimal] = {}
                self._mark(t, prices)
                for s in UNIVERSE:
                    history = self.market.histories[s]
                    known = history.at(t).quarters
                    volume = next((amount(b.volume) for b in reversed(known) if b.received_at < t), ZERO)
                    cap = volume * amount(self.config.risk.max_volume_participation)
                    h = self.positions.get(s)
                    if self.passive is not None and index == 0 and t == session.opens and self.passive.get(s, 0) > 0:
                        target = (
                            amount(self.config.initial_cash)
                            * amount(self.passive[s])
                            * amount(self.market.fx.rate_at(t).usd_per_eur)
                            / prices[s]
                        )
                        self.pending[s] = Pending(True, t, remaining=target)
                    force_exit = False
                    if h:
                        if self.strategy:
                            origin = history.scored_index[h.signal.astimezone(NY).date()]
                            force_exit = index - origin + 1 >= w.max_signal_sessions or self.risk.kill_switch
                        else:
                            force_exit = index >= len(w.scored) - w.blackout
                    if force_exit:
                        self.pending[s] = Pending(False, t, urgent=True)
                    order = self.pending.get(s)
                    if order and (order.signal < t or order.urgent or self.passive is not None):
                        order.opens += 1
                        if order.buy and (
                            t + timedelta(minutes=15) >= session.closes
                            or index >= len(w.scored) - w.blackout
                            or self.risk.kill_switch
                        ):
                            self.pending.pop(s)
                        elif order.urgent or self.scenario != "delay1" or order.opens > 1:
                            used = self._fill(s, order, t, prices[s], cap, prices, float(previous_close))
                            cap -= used
                            if (order.buy and (order.remaining == ZERO or order.opens >= 2)) or (
                                not order.buy and s not in self.positions
                            ):
                                self.pending.pop(s, None)
                    # Stops are broker-side. Evaluate after all symbols' open actions below.
                    remaining_caps[s] = cap
                close = next(iter(rows.values())).end
                contexts = {s: self.market.histories[s].at(close) for s in UNIVERSE}
                close_prices = {s: amount(b.close) if b.received_at <= close else prices[s] for s, b in rows.items()}
                for s, b in rows.items():
                    h = self.positions.get(s)
                    if h and h.stop and amount(b.low) <= h.stop:
                        order = Pending(False, close, urgent=True)
                        self.pending[s] = order
                        self._fill(
                            s,
                            order,
                            close,
                            min(amount(b.open), h.stop),
                            remaining_caps[s],
                            close_prices,
                            float(previous_close),
                        )
                        if s not in self.positions:
                            self.pending.pop(s, None)
                equity = self._mark(close, close_prices)
                mid = amount(self.market.fx.rate_at(close).usd_per_eur)
                for s, h in self.positions.items():
                    exposures[s] += h.quantity * close_prices[s] / mid / equity
                for s in UNIVERSE:
                    ctx = contexts[s]
                    if not self.strategy or not ctx.ready or s in self.pending:
                        continue
                    last = ctx.quarters[-1]
                    if (
                        close - last.end > timedelta(minutes=15)
                        or last.start.astimezone(NY).date() != day
                        or last.start <= self.last_signal_bar.get(s, session.opens - timedelta(days=1))
                    ):
                        continue
                    self.last_signal_bar[s] = last.start
                    h = self.positions.get(s)
                    signal = self.strategy.evaluate(
                        StrategyContext(
                            close,
                            list(ctx.quarters),
                            list(ctx.hours),
                            bool(h),
                            h.entry if h else None,
                            float(h.price) if h else None,
                        )
                    )
                    if signal and (
                        (h and signal.direction == Direction.FLAT)
                        or (not h and ctx.may_enter and signal.direction == Direction.LONG and close < session.closes)
                    ):
                        self.pending[s] = Pending(
                            signal.direction == Direction.LONG,
                            close,
                            amount(signal.stop_price) if signal.stop_price is not None else None,
                        )
                observations += 1
            for s in list(self.pending):
                if self.pending[s].buy:
                    self.pending.pop(s)
            if self.strategy and any(
                index - self.market.histories[s].scored_index[pos.signal.astimezone(NY).date()] + 1
                >= w.max_signal_sessions
                for s, pos in self.positions.items()
            ):
                raise ValueError("overdue unfilled exit after fifth session")
            if index + 1 == len(w.scored):
                if self.positions or self.pending:
                    raise ValueError("unclosed position or order at scored boundary")
                self.cash.withdraw_usd(session.closes, self._rates(UNIVERSE[0], day, "sell"))
                equity = self._mark(session.closes, close_prices)
            previous_close = equity
            daily.append(float(equity))
        sleeve = Sleeve(
            tuple(s.day for s in w.scored),
            tuple(daily),
            self.config.initial_cash,
            self.dd,
            self.trips,
            frozenset(self.months),
            self.risk.kill_switch,
        )
        transfers = [{k: str(v) for k, v in vars(x).items()} for x in self.cash.transfers]
        self.costs["fx"] = sum((x.fee_eur for x in self.cash.transfers), ZERO)
        return ReplayResult(
            sleeve,
            {s: float(v / observations) for s, v in exposures.items()},
            self.marks,
            transfers,
            {k: float(v) for k, v in self.costs.items()},
            self.fills,
            self.dividends,
            self.max_underwater_seconds,
            observations,
        )
