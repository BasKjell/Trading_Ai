"""Event-driven 15-minute engine shared by backtesting and (later) paper trading.

Timing rules (look-ahead protection)
------------------------------------
* Each step is causal: (0) information up to the open is decided, (1) pending orders fill at the open,
  (2) information arriving while the bar runs is decided on the state after the open, (3) stops, marking
  to market, drawdown and kill switch at the bar's end.
* Information is processed in order of **availability** (``received_at``), not of ``bar.start``. A
  strategy context at ``now`` only contains bars with ``received_at <= now``. A bar that arrives after a
  newer bar is added to the history but triggers no decision of its own (it is stale by then).
* A strategy sees a bar only after its ``received_at`` (its close). Signals are created at that time.
* Orders execute at the **open of a later bar** (next bar by default, ``extra_delay_bars`` more under
  stress), never at the close of the signal bar. ``Signal.earliest_execution`` is binding: a bar is
  eligible only if ``bar.start >= earliest_execution``.
* New entries are re-checked on the execution bar: no entry on a bar that follows a data gap, outside
  the session, or more than ``(1 + extra_delay_bars)`` bar lengths after ``earliest_execution``. Exits
  (including de-risking and protective stops) are never blocked by these checks.
* Position sizing uses the last *completed* bar's volume for the liquidity cap, never the execution
  bar's volume.
* Protective stops: if a bar trades through the stop, the exit fills at ``min(open, stop)`` (gaps fill
  worse than the stop) plus slippage. The stop is booked at the bar's **end**, when its low is known;
  decisions on information arriving while the bar runs still see the position (no future low leaks). If an entry and its stop fall into the same bar, the stop is
  assumed hit (conservative). There are no take-profit limit orders, so stop/target ambiguity
  within a bar cannot occur; profit exits are close-based and execute at the next open.
* Exit orders are capped by ``max_volume_participation`` of the previous bar's volume; the remainder
  stays pending (partial fills). Stops are assumed to fill completely.
* Overnight financing (e.g. CFD) is charged per calendar night on the position's notional.
"""

from __future__ import annotations

import hashlib
import math
import random
from bisect import insort
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from statistics import fmean, pstdev
from zoneinfo import ZoneInfo

from .calendars import NYSE_COVERED_UNTIL, NYSE_EARLY_CLOSE, NYSE_EARLY_CLOSES
from .config import AppConfig
from .domain import Bar, Direction, Fill, Market, OrderIntent, Signal
from .execution import CostProfile, FillModel
from .intraday_data import DatasetManifest, ImportedDataset, SessionSpec, resample
from .intraday_strategies import IntradayStrategy, StrategyContext
from .portfolio import Portfolio
from .risk import RiskEngine


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    symbol: str
    market: Market
    venue: str
    currency: str
    session: SessionSpec
    costs: CostProfile
    qty_step: float = 0.0001
    financing_rate_annual: float = 0.0
    # Share of cash dividends withheld at source (e.g. 15 % US treaty rate with W-8BEN; assumption).
    dividend_withholding: float = 0.0


@dataclass(slots=True)
class EquityPoint:
    timestamp: datetime
    equity: float
    cash: float
    exposure: float
    drawdown: float


@dataclass(slots=True)
class Trade:
    strategy: str
    symbol: str
    entry_time: datetime
    entry_price: float
    quantity: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    fees: float = 0.0
    pnl: float | None = None
    exit_reason: str = ""
    entry_reason: str = ""


@dataclass(slots=True)
class EngineResult:
    equity: list[EquityPoint]
    trades: list[Trade]
    fills: list[Fill]
    decisions: list[dict[str, object]]
    financing_costs: float
    manifests: list[DatasetManifest]
    parameters: dict[str, dict[str, float]]
    settings: dict[str, float]
    initial_cash: float
    timezone: str
    warnings: list[str] = field(default_factory=list)
    dividend_income: float = 0.0  # entitlements at ex-date, net of withholding
    dividend_receivable_end: float = 0.0  # entitled but not yet paid at the end of data


def instruments_from_config(config: AppConfig) -> dict[str, InstrumentSpec]:
    output: dict[str, InstrumentSpec] = {}
    for symbol, raw in config.raw.get("instruments", {}).items():
        start_h, start_m = (int(x) for x in str(raw["session_start"]).split(":"))
        end_h, end_m = (int(x) for x in str(raw["session_end"]).split(":"))
        from datetime import time as _time

        output[symbol] = InstrumentSpec(
            symbol=symbol,
            market=Market(raw["market"]),
            venue=str(raw["venue"]),
            currency=str(raw["currency"]),
            session=_session(raw, _time(start_h, start_m), _time(end_h, end_m)),
            costs=CostProfile(
                float(raw["fee_bps"]), float(raw["spread_bps"]), float(raw["slippage_bps"]), float(raw["min_fee"])
            ),
            qty_step=float(raw["qty_step"]),
            financing_rate_annual=float(raw.get("financing_rate_annual", 0.0)),
            dividend_withholding=float(raw.get("dividend_withholding", 0.0)),
        )
    return output


def _session(raw: dict[str, object], start: time, end: time) -> SessionSpec:
    calendar = raw.get("session_calendar")
    if calendar is None:
        return SessionSpec(str(raw["session_timezone"]), start, end)
    if calendar != "nyse":
        raise ValueError(f"unknown session_calendar {calendar!r}")
    return SessionSpec(
        str(raw["session_timezone"]), start, end, NYSE_EARLY_CLOSES, NYSE_EARLY_CLOSE, NYSE_COVERED_UNTIL
    )


def _client_id(*parts: object) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:24]


@dataclass(slots=True)
class _Pending:
    signal: Signal
    bars_waited: int = 0


class IntradayEngine:
    def __init__(
        self,
        config: AppConfig,
        instruments: dict[str, InstrumentSpec],
        strategies: list[IntradayStrategy],
        cost_multiplier: float = 1.0,
        spread_multiplier: float = 1.0,
        extra_delay_bars: int = 0,
    ) -> None:
        if not strategies:
            raise ValueError("at least one strategy is required")
        if extra_delay_bars < 0:
            raise ValueError("extra_delay_bars must be >= 0")
        self.config = config
        self.instruments = instruments
        self.strategies = strategies
        self.cost_multiplier = cost_multiplier
        self.spread_multiplier = spread_multiplier
        self.extra_delay_bars = extra_delay_bars

    def run(self, datasets: dict[str, ImportedDataset]) -> EngineResult:
        cfg = self.config
        missing = set(datasets) - set(self.instruments)
        if missing:
            raise ValueError(f"no instrument specification for {sorted(missing)}")
        fill_model = FillModel(
            cfg.costs,
            self.cost_multiplier,
            {symbol: spec.costs for symbol, spec in self.instruments.items()},
            self.spread_multiplier,
        )
        risk = RiskEngine(cfg.risk, cfg.correlation_groups)
        portfolio = Portfolio(cfg.initial_cash)
        primary_tz = next(iter(self.instruments[s].session.timezone for s in datasets))
        zone = ZoneInfo(primary_tz)

        by_time: dict[datetime, dict[str, Bar]] = {}
        hourly: dict[str, list[tuple[datetime, Bar]]] = {}
        for symbol, data in datasets.items():
            for bar in data.bars:
                by_time.setdefault(bar.start, {})[symbol] = bar
            session = self.instruments[symbol].session
            hourly[symbol] = [
                (max(h.received_at, h.start + timedelta(hours=1)), h) for h in resample(data.bars, 60, session.timezone)
            ]
        history: dict[str, list[Bar]] = {symbol: [] for symbol in datasets}
        # Bars not yet available to strategies, kept sorted by availability.
        inbox: list[Bar] = []
        timestamps = sorted(by_time)
        hourly_seen: dict[str, list[Bar]] = {symbol: [] for symbol in datasets}
        hourly_cursor: dict[str, int] = dict.fromkeys(datasets, 0)
        pending: dict[str, _Pending] = {}
        prices: dict[str, float] = {}
        last_volume: dict[str, float] = {}
        last_local_date: dict[str, object] = {}
        open_trades: dict[str, Trade] = {}
        trades: list[Trade] = []
        fills: list[Fill] = []
        decisions: list[dict[str, object]] = []
        equity_points: list[EquityPoint] = []
        financing = 0.0
        dividend_income = 0.0
        receivables: list[tuple[date, str, float]] = []  # (pay date, symbol, net amount)
        day_start_equity = cfg.initial_cash
        current_day = None
        strategy_by_name = {s.name: s for s in self.strategies}

        def book_exit(symbol: str, quantity: float, reference: float, when: datetime, reason: str) -> None:
            position = portfolio.positions[symbol]
            quantity = min(quantity, position.quantity)
            intent = OrderIntent(
                _client_id("exit", symbol, when.isoformat(), reason, quantity),
                position.strategy,
                symbol,
                position.market,
                when,
                Direction.FLAT,
                quantity,
                "market",
                when + timedelta(minutes=15),
                None,
                {},
            )
            _, fill = fill_model.fill(intent, quantity, reference, when)
            portfolio.apply_fill(fill, position.strategy)
            fills.append(fill)
            trade = open_trades.get(symbol)
            if trade is not None:
                share = quantity / trade.quantity if trade.quantity > 0 else 1.0
                closed = Trade(
                    trade.strategy,
                    symbol,
                    trade.entry_time,
                    trade.entry_price,
                    quantity,
                    when,
                    fill.price,
                    trade.fees * share + fill.fee,
                    quantity * (fill.price - trade.entry_price) - trade.fees * share - fill.fee,
                    reason,
                    trade.entry_reason,
                )
                trades.append(closed)
                trade.fees -= trade.fees * share
                trade.quantity -= quantity
                if symbol not in portfolio.positions:
                    open_trades.pop(symbol, None)

        def process_arrivals(limit: datetime, inclusive: bool) -> None:
            """Turn bars available up to ``limit`` into information, in arrival order, and evaluate them.

            The portfolio state seen here is the simulated state at that moment: stops of a bar that is
            still running are not applied yet, because its low is only known at the bar's end.
            """
            while inbox and (inbox[0].received_at <= limit if inclusive else inbox[0].received_at < limit):
                bar = inbox.pop(0)
                symbol = bar.symbol
                known = history[symbol]
                newest = not known or bar.start > known[-1].start
                insort(known, bar, key=lambda b: b.start)
                if newest:
                    last_volume[symbol] = bar.volume
                if risk.deleveraging_required:
                    continue
                if not newest:
                    decisions.append(
                        {
                            "time": bar.received_at.isoformat(),
                            "symbol": symbol,
                            "event": "late_bar",
                            "reason": "bar arrived after a newer bar; no decision on stale data",
                        }
                    )
                    continue
                spec = self.instruments[symbol]
                data = datasets[symbol]
                if symbol in pending or not spec.session.contains(bar.start):
                    continue
                series = hourly[symbol]
                while hourly_cursor[symbol] < len(series) and series[hourly_cursor[symbol]][0] <= bar.received_at:
                    hourly_seen[symbol].append(series[hourly_cursor[symbol]][1])
                    hourly_cursor[symbol] += 1
                visible = [b for b in known if b.received_at <= bar.received_at and b.start <= bar.start]
                position = portfolio.positions.get(symbol)
                trade = open_trades.get(symbol)
                candidates = [strategy_by_name[position.strategy]] if position else self.strategies
                for strategy in candidates:
                    if position is None and bar.start in data.after_gap:
                        decisions.append(
                            {
                                "time": bar.received_at.isoformat(),
                                "symbol": symbol,
                                "strategy": strategy.name,
                                "event": "skipped",
                                "reason": "bar follows a data gap",
                            }
                        )
                        break
                    ctx = StrategyContext(
                        now=bar.received_at,
                        bars=visible,
                        hourly=hourly_seen[symbol],
                        in_position=position is not None,
                        entry_time=trade.entry_time if trade else None,
                        entry_price=trade.entry_price if trade else None,
                    )
                    signal = strategy.evaluate(ctx)
                    if signal is None:
                        continue
                    pending[symbol] = _Pending(signal)
                    decisions.append(
                        {
                            "time": signal.signal_time.isoformat(),
                            "symbol": symbol,
                            "strategy": strategy.name,
                            "event": "signal",
                            "direction": signal.direction.value,
                            "reason": signal.reason,
                            "stop": signal.stop_price,
                        }
                    )
                    break

        for timestamp in timestamps:
            current = by_time[timestamp]
            bar_end = min(bar.end for bar in current.values())
            # 0) information available up to this open (incl. bars ending now) is decided before the open
            process_arrivals(timestamp, inclusive=True)
            local_day = timestamp.astimezone(zone).date()
            if current_day is not None and local_day != current_day and equity_points:
                day_start_equity = equity_points[-1].equity
            current_day = local_day

            for symbol, bar in current.items():
                spec = self.instruments[symbol]
                prices[symbol] = bar.open
                # overnight financing per elapsed calendar night
                bar_day = spec.session.local_date(bar.start)
                previous_day = last_local_date.get(symbol)
                position = portfolio.positions.get(symbol)
                if previous_day is not None and bar_day != previous_day and position and spec.financing_rate_annual:
                    nights = (bar_day - previous_day).days  # type: ignore[operator]
                    cost = position.quantity * bar.open * spec.financing_rate_annual / 360 * nights
                    portfolio.charge(cost)
                    financing += cost
                # dividend paid today: the entitlement becomes cash (also after the position was closed)
                for entitlement in [e for e in receivables if e[1] == symbol and e[0] <= bar_day]:
                    receivables.remove(entitlement)
                    portfolio.settle_income(entitlement[2])
                    decisions.append(
                        {
                            "time": bar.start.isoformat(),
                            "symbol": symbol,
                            "event": "dividend_paid",
                            "amount": entitlement[2],
                        }
                    )
                # entitlement for a position held into the ex-date (before any fill at this open)
                dividend = datasets[symbol].dividends.get(bar_day)
                if dividend and position and previous_day is not None and bar_day != previous_day:
                    pay_date, amount = dividend
                    income = position.quantity * amount * (1 - spec.dividend_withholding)
                    portfolio.accrue_income(income)
                    receivables.append((pay_date, symbol, income))
                    dividend_income += income
                    decisions.append(
                        {
                            "time": bar.start.isoformat(),
                            "symbol": symbol,
                            "event": "dividend_entitlement",
                            "amount": income,
                            "pay_date": pay_date.isoformat(),
                        }
                    )
                last_local_date[symbol] = bar_day

                # 1) pending orders execute at this bar's open
                order = pending.get(symbol)
                # A signal formed at a bar's close may execute at the open of any bar starting at/after it.
                if order is not None and bar.start >= order.signal.earliest_execution:
                    if order.bars_waited < self.extra_delay_bars:
                        order.bars_waited += 1
                    elif (block := self._entry_block(order.signal, bar, spec, datasets[symbol])) is not None:
                        pending.pop(symbol, None)
                        decisions.append(
                            {
                                "time": bar.start.isoformat(),
                                "symbol": symbol,
                                "strategy": order.signal.strategy,
                                "event": "entry_cancelled",
                                "reason": block,
                            }
                        )
                    else:
                        self._execute(
                            order,
                            bar,
                            spec,
                            portfolio,
                            risk,
                            fill_model,
                            prices,
                            day_start_equity,
                            last_volume.get(symbol),
                            pending,
                            open_trades,
                            fills,
                            decisions,
                            book_exit,
                        )

            # 2) information arriving while this bar runs: decided on the state after the open, before
            #    this bar's stops (its low is not known before the bar ends)
            process_arrivals(bar_end, inclusive=False)

            # 3) bar close: stops of this bar become known at its end; mark to market at the close
            for symbol, bar in current.items():
                position = portfolio.positions.get(symbol)
                if position and position.stop_price and bar.low <= position.stop_price:
                    exit_reference = min(bar.open, position.stop_price)
                    book_exit(symbol, position.quantity, exit_reference, bar.end, "protective stop")
                    pending.pop(symbol, None)
                prices[symbol] = bar.close
                insort(inbox, bar, key=lambda b: (b.received_at, b.start, b.symbol))

            snapshot = portfolio.snapshot(bar_end, prices)
            equity_points.append(
                EquityPoint(
                    snapshot.timestamp, snapshot.equity, snapshot.cash, snapshot.gross_exposure, snapshot.drawdown
                )
            )
            if snapshot.drawdown >= cfg.risk.drawdown_kill - 1e-9:
                risk.kill_switch = True
                risk.deleveraging_required = True
            if risk.deleveraging_required:
                for symbol, position in portfolio.positions.items():
                    if symbol in current and symbol not in pending:
                        bar = current[symbol]
                        pending[symbol] = _Pending(
                            Signal(
                                position.strategy,
                                symbol,
                                position.market,
                                bar.end,
                                bar.end,
                                Direction.FLAT,
                                1.0,
                                "drawdown kill switch: controlled de-risking",
                                stop_price=None,
                            )
                        )

        # the last bars still become information (decisions can no longer be executed)
        if timestamps:
            process_arrivals(datetime.max.replace(tzinfo=UTC), inclusive=True)

        # terminal liquidation at the last close so every result is net of exit costs
        if equity_points:
            final_time = equity_points[-1].timestamp
            for symbol in list(portfolio.positions):
                book_exit(symbol, portfolio.positions[symbol].quantity, prices[symbol], final_time, "end of data")
            snap = portfolio.snapshot(final_time, prices)
            equity_points[-1] = EquityPoint(snap.timestamp, snap.equity, snap.cash, 0.0, snap.drawdown)

        warnings = []
        if any(d.manifest.synthetic for d in datasets.values()):
            warnings.append("Synthetic data: results are technical checks, not evidence of profitability.")
        return EngineResult(
            equity=equity_points,
            trades=trades,
            fills=fills,
            decisions=decisions,
            financing_costs=financing,
            dividend_income=dividend_income,
            dividend_receivable_end=portfolio.income_receivable,
            manifests=[d.manifest for d in datasets.values()],
            parameters={s.name: s.parameters() for s in self.strategies},
            settings={
                "cost_multiplier": self.cost_multiplier,
                "spread_multiplier": self.spread_multiplier,
                "extra_delay_bars": self.extra_delay_bars,
            },
            initial_cash=cfg.initial_cash,
            timezone=primary_tz,
            warnings=warnings,
        )

    def _entry_block(self, signal: Signal, bar: Bar, spec: InstrumentSpec, data: ImportedDataset) -> str | None:
        """Reason why an entry must not be executed on ``bar``; ``None`` if allowed. Exits are never blocked."""
        if signal.direction == Direction.FLAT:
            return None
        if bar.start in data.after_gap:
            return "execution bar follows a data gap"
        if not spec.session.contains(bar.start):
            return "execution bar outside session"
        max_age = (bar.end - bar.start) * (1 + self.extra_delay_bars)
        if bar.start - max(signal.signal_time, signal.earliest_execution) > max_age:
            return "signal too old for entry"
        return None

    def _execute(
        self,
        order: _Pending,
        bar: Bar,
        spec: InstrumentSpec,
        portfolio: Portfolio,
        risk: RiskEngine,
        fill_model: FillModel,
        prices: dict[str, float],
        day_start_equity: float,
        previous_volume: float | None,
        pending: dict[str, _Pending],
        open_trades: dict[str, Trade],
        fills: list[Fill],
        decisions: list[dict[str, object]],
        book_exit: object,
    ) -> None:
        symbol = bar.symbol
        signal = order.signal
        position = portfolio.positions.get(symbol)
        if signal.direction == Direction.FLAT:
            if position is None:
                pending.pop(symbol, None)
                return
            cap = (previous_volume or 0.0) * self.config.risk.max_volume_participation
            quantity = position.quantity if cap <= 0 else min(position.quantity, cap)
            quantity = _floor_step(quantity, spec.qty_step) or position.quantity
            book_exit(symbol, quantity, bar.open, bar.start, signal.reason)  # type: ignore[operator]
            if symbol not in portfolio.positions:
                pending.pop(symbol, None)
            return
        pending.pop(symbol, None)
        if position is not None or signal.stop_price is None:
            return
        cost_per_unit = fill_model.round_trip_cost_per_unit(spec.market, bar.open, symbol)
        snapshot = portfolio.snapshot(bar.start, prices)
        target = risk.size_for_stop(snapshot.equity, bar.open, signal.stop_price, cost_per_unit, snapshot.drawdown)
        intent = OrderIntent(
            _client_id("entry", signal.strategy, symbol, signal.signal_time.isoformat()),
            signal.strategy,
            symbol,
            spec.market,
            bar.start,
            Direction.LONG,
            target,
            "market",
            bar.start + timedelta(minutes=15),
            signal.stop_price,
            {"cost_per_unit": cost_per_unit},
        )
        decision = risk.decide(intent, portfolio, prices, bar.start, day_start_equity, previous_volume)
        quantity = _floor_step(decision.approved_quantity, spec.qty_step) if decision.allowed else 0.0
        record: dict[str, object] = {
            "time": bar.start.isoformat(),
            "symbol": symbol,
            "strategy": signal.strategy,
            "event": "risk",
            "allowed": bool(quantity > 0),
            "reason": decision.reason if quantity > 0 or not decision.allowed else "below quantity step",
            "limits": list(decision.violated_limits),
            "quantity": quantity,
        }
        decisions.append(record)
        if quantity <= 0 or quantity * bar.open < self.config.risk.min_order_value:
            return
        _, fill = fill_model.fill(intent, quantity, bar.open, bar.start)
        portfolio.apply_fill(fill, signal.strategy, signal.stop_price)
        fills.append(fill)
        open_trades[symbol] = Trade(
            signal.strategy, symbol, bar.start, fill.price, quantity, fees=fill.fee, entry_reason=signal.reason
        )


def _floor_step(quantity: float, step: float) -> float:
    if step <= 0:
        return quantity
    return math.floor(quantity / step + 1e-9) * step


# --------------------------------------------------------------------------------------------- metrics


def daily_equity(result: EngineResult) -> list[tuple[str, float]]:
    zone = ZoneInfo(result.timezone)
    by_day: dict[str, float] = {}
    for point in result.equity:
        by_day[point.timestamp.astimezone(zone).date().isoformat()] = point.equity
    return sorted(by_day.items())


def monthly_returns(result: EngineResult) -> dict[str, float]:
    days = daily_equity(result)
    output: dict[str, float] = {}
    previous_end = result.initial_cash
    month_end: dict[str, float] = {}
    for day, equity in days:
        month_end[day[:7]] = equity
    for month, end in sorted(month_end.items()):
        output[month] = end / previous_end - 1 if previous_end > 0 else 0.0
        previous_end = end
    return output


def block_bootstrap_monthly(
    daily_returns: list[float], block: int = 5, samples: int = 1000, seed: int = 20261117
) -> tuple[float, float] | None:
    """90 % interval of the geometric monthly return via circular block bootstrap of daily returns.

    Blocks keep short-range dependence; trades are not treated as independent observations.
    """
    n = len(daily_returns)
    if n < 2 * block:
        return None
    rng = random.Random(seed)
    stats: list[float] = []
    for _ in range(samples):
        sample: list[float] = []
        while len(sample) < n:
            start = rng.randrange(n)
            sample.extend(daily_returns[(start + i) % n] for i in range(block))
        growth = math.prod(1 + r for r in sample[:n])
        stats.append(growth ** (21 / n) - 1 if growth > 0 else -1.0)
    stats.sort()
    return stats[int(0.05 * samples)], stats[int(0.95 * samples) - 1]


def compute_metrics(result: EngineResult) -> dict[str, object]:
    initial = result.initial_cash
    days = daily_equity(result)
    final = result.equity[-1].equity if result.equity else initial
    values = [initial] + [equity for _, equity in days]
    daily = [b / a - 1 for a, b in zip(values, values[1:], strict=False) if a > 0]
    trading_days = len(days)
    total_return = final / initial - 1
    months = trading_days / 21 if trading_days else 0
    geometric_monthly = (final / initial) ** (1 / months) - 1 if months > 0 and final > 0 else None
    vol = pstdev(daily) * math.sqrt(252) if len(daily) > 1 else 0.0
    mean = fmean(daily) if daily else 0.0
    downside = math.sqrt(fmean([min(0.0, r) ** 2 for r in daily])) * math.sqrt(252) if daily else 0.0
    max_dd = max((p.drawdown for p in result.equity), default=0.0)
    longest = current = 0
    peak = initial
    for _, equity in days:
        peak = max(peak, equity)
        current = current + 1 if equity < peak else 0
        longest = max(longest, current)
    pnls = [t.pnl for t in result.trades if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    hold_hours = [(t.exit_time - t.entry_time).total_seconds() / 3600 for t in result.trades if t.exit_time is not None]
    traded_notional = sum(abs(f.quantity * f.price) for f in result.fills)
    avg_equity = fmean(p.equity for p in result.equity) if result.equity else initial
    fees = sum(f.fee for f in result.fills)
    spread_slippage = sum(f.spread_cost + f.slippage_cost for f in result.fills)
    exposure = [p.exposure / p.equity for p in result.equity if p.equity > 0]
    interval = block_bootstrap_monthly(daily)
    return {
        "total_return": total_return,
        "geometric_monthly_return": geometric_monthly,
        "geometric_monthly_return_90ci": interval,
        "annualized_return": (1 + total_return) ** (252 / trading_days) - 1 if trading_days and final > 0 else None,
        "annualized_volatility": vol,
        "sharpe": mean * 252 / vol if vol > 0 else None,
        "sortino": mean * 252 / downside if downside > 0 else None,
        "max_drawdown": max_dd,
        "max_drawdown_duration_days": longest,
        "trading_days": trading_days,
        "short_period_warning": trading_days < 252,
        "trades": len(pnls),
        "win_rate": len(wins) / len(pnls) if pnls else None,
        "average_win": fmean(wins) if wins else None,
        "average_loss": fmean(losses) if losses else None,
        "profit_factor": sum(wins) / -sum(losses) if losses and sum(losses) < 0 else None,
        "average_holding_hours": fmean(hold_hours) if hold_hours else None,
        "turnover": traded_notional / avg_equity if avg_equity > 0 else 0.0,
        "fees": fees,
        "spread_and_slippage": spread_slippage,
        "financing": result.financing_costs,
        "dividends_entitled": result.dividend_income,
        "dividends_unpaid_at_end": result.dividend_receivable_end,
        "total_costs": fees + spread_slippage + result.financing_costs,
        "cost_share_of_initial": (fees + spread_slippage + result.financing_costs) / initial,
        "average_exposure": fmean(exposure) if exposure else 0.0,
        "time_in_market": sum(1 for e in exposure if e > 0) / len(exposure) if exposure else 0.0,
    }


def buy_and_hold(
    datasets: dict[str, ImportedDataset],
    instruments: dict[str, InstrumentSpec],
    config: AppConfig,
    cost_multiplier: float = 1.0,
) -> dict[str, float]:
    """Equal-weight buy-and-hold of all instruments with the same cost model (fully invested, no leverage)."""
    if not datasets:
        return {}
    fill_model = FillModel(config.costs, cost_multiplier, {s: i.costs for s, i in instruments.items()})
    weight = 1.0 / len(datasets)
    final = 0.0
    for symbol, data in datasets.items():
        first, last = data.bars[0], data.bars[-1]
        cost = fill_model.round_trip_cost_per_unit(instruments[symbol].market, first.open, symbol) / first.open
        quantity = config.initial_cash * weight / (first.open * (1 + cost / 2))
        financing = 0.0
        rate = instruments[symbol].financing_rate_annual
        if rate:
            nights = (last.end - first.start).days
            financing = quantity * fmean([first.open, last.close]) * rate / 360 * nights
        dividends = sum(
            amount
            for day, (_, amount) in data.dividends.items()
            if instruments[symbol].session.local_date(first.start)
            < day
            <= instruments[symbol].session.local_date(last.start)
        )
        income = quantity * dividends * (1 - instruments[symbol].dividend_withholding)
        final += quantity * last.close * (1 - cost / 2) - financing + income
    return {"buy_and_hold_total_return": final / config.initial_cash - 1, "cash_total_return": 0.0}
