from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import fmean, pstdev

from .config import AppConfig
from .data import group_by_symbol, validate_bars
from .domain import Bar, Direction, Fill, Market, OrderIntent, PortfolioSnapshot, Signal
from .execution import FillModel, IdempotentPaperExecutor
from .portfolio import Portfolio
from .risk import RiskEngine
from .strategies import CryptoTrendStrategy, ETFTrendStrategy, StockMomentumStrategy

ETF_SYMBOLS = {"SPY", "QQQ"}
STOCK_SYMBOLS = {"AAPL", "MSFT", "NVDA"}
CRYPTO_SYMBOLS = {"BTC-USD", "ETH-USD"}


@dataclass(slots=True)
class BacktestResult:
    snapshots: list[PortfolioSnapshot]
    fills: list[Fill]
    decisions: list[dict[str, object]]
    metrics: dict[str, float | int | None]
    strategy_pnl: dict[str, float]
    benchmark: dict[str, float]
    data_hash: str
    config_hash: str
    warnings: list[str] = field(default_factory=list)


def _order_id(signal: Signal) -> str:
    raw = f"{signal.strategy}|{signal.symbol}|{signal.signal_time.isoformat()}|{signal.direction}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _intent(signal: Signal, quantity: float, now: datetime, cost_per_unit: float = 0.0) -> OrderIntent:
    return OrderIntent(
        client_order_id=_order_id(signal),
        strategy=signal.strategy,
        symbol=signal.symbol,
        market=signal.market,
        created_at=now,
        direction=signal.direction,
        target_quantity=quantity,
        order_type="market",
        valid_until=now + timedelta(days=4),
        stop_price=signal.stop_price,
        risk_metadata={**{"signal_strength": signal.strength}, "cost_per_unit": cost_per_unit},
    )


def calculate_metrics(
    snapshots: list[PortfolioSnapshot],
    fills: list[Fill],
    initial_cash: float,
    periods_per_year: float = 252.0,
) -> dict[str, float | int | None]:
    if len(snapshots) < 2:
        return {"total_return": 0.0, "cagr": 0.0, "max_drawdown": 0.0, "trades": 0}
    equities = [item.equity for item in snapshots]
    daily = [b / a - 1 for a, b in zip(equities, equities[1:], strict=False) if a > 0]
    years = max((snapshots[-1].timestamp - snapshots[0].timestamp).days / 365.25, 1 / 365.25)
    total_return = equities[-1] / initial_cash - 1
    cagr = max(0.0, equities[-1] / initial_cash) ** (1 / years) - 1
    volatility = pstdev(daily) * math.sqrt(periods_per_year) if len(daily) > 1 else 0.0
    mean_daily = fmean(daily) if daily else 0.0
    downside = [min(0.0, value) for value in daily]
    downside_dev = (
        math.sqrt(fmean([value * value for value in downside])) * math.sqrt(periods_per_year) if downside else 0.0
    )
    sharpe = mean_daily * periods_per_year / volatility if volatility > 0 else None
    sortino = mean_daily * periods_per_year / downside_dev if downside_dev > 0 else None
    max_dd = max(item.drawdown for item in snapshots)
    calmar = cagr / max_dd if max_dd > 0 else None
    drawdown_duration = 0
    current_duration = 0
    for item in snapshots:
        current_duration = current_duration + 1 if item.drawdown > 0 else 0
        drawdown_duration = max(drawdown_duration, current_duration)
    monthly: dict[str, tuple[float, float]] = {}
    for item in snapshots:
        key = item.timestamp.strftime("%Y-%m")
        if key not in monthly:
            monthly[key] = (item.equity, item.equity)
        else:
            monthly[key] = (monthly[key][0], item.equity)
    monthly_returns = [end / start - 1 for start, end in monthly.values() if start > 0]
    positive_months = sum(value > 0 for value in monthly_returns) / len(monthly_returns) if monthly_returns else 0.0
    buys: dict[str, list[Fill]] = {}
    trade_pnls: list[float] = []
    for fill in fills:
        if fill.quantity > 0:
            buys.setdefault(fill.symbol, []).append(fill)
        elif fill.quantity < 0 and buys.get(fill.symbol):
            buy = buys[fill.symbol].pop(0)
            qty = min(buy.quantity, -fill.quantity)
            trade_pnls.append(
                qty * (fill.price - buy.price) - buy.fee * qty / buy.quantity - fill.fee * qty / -fill.quantity
            )
    gains = sum(value for value in trade_pnls if value > 0)
    losses = -sum(value for value in trade_pnls if value < 0)
    exposure_values = [item.gross_exposure / item.equity for item in snapshots if item.equity > 0]
    costs = sum(fill.fee + fill.spread_cost + fill.slippage_cost for fill in fills)
    return {
        "total_return": total_return,
        "cagr": cagr,
        "annualized_volatility": volatility,
        "sharpe": sharpe,
        "sortino": sortino,
        "max_drawdown": max_dd,
        "max_drawdown_duration_bars": drawdown_duration,
        "calmar": calmar,
        "positive_month_ratio": positive_months,
        "profit_factor": gains / losses if losses > 0 else None,
        "win_rate": sum(value > 0 for value in trade_pnls) / len(trade_pnls) if trade_pnls else None,
        "average_trade_pnl": fmean(trade_pnls) if trade_pnls else None,
        "trades": len(trade_pnls),
        "fills": len(fills),
        "costs": costs,
        "average_exposure": fmean(exposure_values) if exposure_values else 0.0,
        "max_exposure": max(exposure_values, default=0.0),
    }


class BacktestEngine:
    def __init__(self, config: AppConfig, cost_multiplier: float = 1.0) -> None:
        self.config = config
        self.cost_multiplier = cost_multiplier

    def run(self, bars: list[Bar], data_hash: str) -> BacktestResult:
        validate_bars(bars)
        grouped = group_by_symbol(bars)
        by_time: dict[datetime, dict[str, Bar]] = {}
        for symbol, items in grouped.items():
            for bar in items:
                by_time.setdefault(bar.start, {})[symbol] = bar
        portfolio = Portfolio(self.config.initial_cash)
        risk = RiskEngine(self.config.risk, self.config.correlation_groups)
        executor = IdempotentPaperExecutor(FillModel(self.config.costs, self.cost_multiplier))
        etf_cfg = self.config.raw["strategies"]["etf_trend"]
        stock_cfg = self.config.raw["strategies"]["stock_momentum"]
        crypto_cfg = self.config.raw["strategies"]["crypto_trend"]
        etf = ETFTrendStrategy(**{k: v for k, v in etf_cfg.items() if k != "enabled"})
        stock = StockMomentumStrategy(**{k: v for k, v in stock_cfg.items() if k != "enabled"})
        crypto = CryptoTrendStrategy(**{k: v for k, v in crypto_cfg.items() if k != "enabled"})
        history: dict[str, list[Bar]] = {symbol: [] for symbol in grouped}
        pending: dict[str, Signal] = {}
        prices: dict[str, float] = {}
        snapshots: list[PortfolioSnapshot] = []
        fills: list[Fill] = []
        decisions: list[dict[str, object]] = []
        entry_cash: dict[str, tuple[str, float]] = {}
        strategy_pnl = {"etf_trend": 0.0, "stock_momentum": 0.0, "crypto_trend": 0.0}
        day_start_equity = self.config.initial_cash
        warmup = int(self.config.raw["backtest"]["warmup_bars"])

        for timestamp in sorted(by_time):
            today = by_time[timestamp]
            if snapshots and snapshots[-1].timestamp.date() != timestamp.date():
                day_start_equity = snapshots[-1].equity
            for symbol, bar in today.items():
                prices[symbol] = bar.open
                position = portfolio.positions.get(symbol)
                if position and position.stop_price and bar.low <= position.stop_price:
                    exit_price = min(bar.open, position.stop_price)
                    stop_signal = Signal(
                        position.strategy,
                        symbol,
                        bar.market,
                        timestamp,
                        timestamp,
                        Direction.FLAT,
                        1.0,
                        "protective stop",
                        stop_price=None,
                    )
                    intent = _intent(stop_signal, position.quantity, timestamp)
                    _, fill = executor.execute(intent, position.quantity, exit_price, timestamp)
                    if fill:
                        basis = entry_cash.pop(symbol, (position.strategy, 0.0))[1]
                        before_cash = portfolio.cash
                        portfolio.apply_fill(fill, position.strategy)
                        strategy_pnl[position.strategy] += portfolio.cash - before_cash - basis
                        fills.append(fill)
                        pending.pop(symbol, None)

            for symbol, signal in list(pending.items()):
                maybe_bar = today.get(symbol)
                if maybe_bar is None or timestamp < signal.earliest_execution:
                    continue
                bar = maybe_bar
                position = portfolio.positions.get(symbol)
                if signal.direction == Direction.FLAT:
                    if position:
                        intent = _intent(signal, position.quantity, timestamp)
                        _, fill = executor.execute(intent, position.quantity, bar.open, timestamp)
                        if fill:
                            basis = entry_cash.pop(symbol, (position.strategy, 0.0))[1]
                            before_cash = portfolio.cash
                            portfolio.apply_fill(fill, position.strategy)
                            strategy_pnl[position.strategy] += portfolio.cash - before_cash - basis
                            fills.append(fill)
                    pending.pop(symbol, None)
                    continue
                if position is None:
                    cost_per_unit = executor.fill_model.round_trip_cost_per_unit(signal.market, bar.open)
                    provisional = risk.size_for_stop(
                        portfolio.equity(prices), bar.open, signal.stop_price or bar.open, cost_per_unit
                    )
                    intent = _intent(signal, provisional, timestamp, cost_per_unit)
                    decision = risk.decide(intent, portfolio, prices, timestamp, day_start_equity, bar.volume)
                    decisions.append(
                        {
                            "time": timestamp.isoformat(),
                            "symbol": symbol,
                            "allowed": decision.allowed,
                            "reason": decision.reason,
                            "limits": list(decision.violated_limits),
                        }
                    )
                    if decision.allowed:
                        _, fill = executor.execute(intent, decision.approved_quantity, bar.open, timestamp)
                        if fill:
                            cash_before = portfolio.cash
                            portfolio.apply_fill(fill, signal.strategy, signal.stop_price)
                            entry_cash[symbol] = (signal.strategy, cash_before - portfolio.cash)
                            fills.append(fill)
                pending.pop(symbol, None)

            for symbol, bar in today.items():
                prices[symbol] = bar.close
                history[symbol].append(bar)
            if min((len(items) for items in history.values()), default=0) >= warmup:
                for symbol in ETF_SYMBOLS & history.keys():
                    etf_signal = etf.evaluate(history[symbol])
                    if etf_signal:
                        pending[symbol] = etf_signal
                for symbol in CRYPTO_SYMBOLS & history.keys():
                    crypto_signal = crypto.evaluate(history[symbol])
                    if crypto_signal:
                        pending[symbol] = crypto_signal
                equity_bar_count = min((len(history[s]) for s in STOCK_SYMBOLS & history.keys()), default=0)
                if equity_bar_count and equity_bar_count % 5 == 0:
                    stock_histories = {s: history[s] for s in STOCK_SYMBOLS & history.keys()}
                    stock_signals = stock.rank(stock_histories)
                    selected = {signal.symbol for signal in stock_signals if signal.direction == Direction.LONG}
                    for signal in stock_signals:
                        pending[signal.symbol] = signal
                    for symbol, position in list(portfolio.positions.items()):
                        if position.strategy == "stock_momentum" and symbol not in selected and symbol in today:
                            bar = history[symbol][-1]
                            pending[symbol] = Signal(
                                "stock_momentum",
                                symbol,
                                Market.EQUITY,
                                bar.end,
                                bar.end + timedelta(microseconds=1),
                                Direction.FLAT,
                                1.0,
                                "left top-N",
                                stop_price=None,
                            )
            snapshots.append(portfolio.snapshot(timestamp, prices))

        if by_time:
            terminal_time = max(by_time) + timedelta(days=1)
            for symbol, position in list(portfolio.positions.items()):
                signal = Signal(
                    position.strategy,
                    symbol,
                    position.market,
                    terminal_time,
                    terminal_time,
                    Direction.FLAT,
                    1.0,
                    "terminal liquidation",
                    stop_price=None,
                )
                intent = _intent(signal, position.quantity, terminal_time)
                _, fill = executor.execute(intent, position.quantity, prices[symbol], terminal_time)
                if fill:
                    basis = entry_cash.pop(symbol, (position.strategy, 0.0))[1]
                    before_cash = portfolio.cash
                    portfolio.apply_fill(fill, position.strategy)
                    strategy_pnl[position.strategy] += portfolio.cash - before_cash - basis
                    fills.append(fill)
            snapshots.append(portfolio.snapshot(terminal_time, prices))

        metrics = calculate_metrics(snapshots, fills, self.config.initial_cash)
        spy = grouped.get("SPY", [])
        benchmark_return = spy[-1].close / spy[warmup].open - 1 if len(spy) > warmup else 0.0
        static_weights = {"SPY": 0.50, "QQQ": 0.20, "BTC-USD": 0.15, "ETH-USD": 0.15}
        static_return = 0.0
        for symbol, weight in static_weights.items():
            items = grouped.get(symbol, [])
            if len(items) > warmup:
                static_return += weight * (items[-1].close / items[warmup].open - 1)
        return BacktestResult(
            snapshots=snapshots,
            fills=fills,
            decisions=decisions,
            metrics=metrics,
            strategy_pnl=strategy_pnl,
            benchmark={
                "SPY_buy_hold_total_return": benchmark_return,
                "static_50_20_15_15_total_return": static_return,
            },
            data_hash=data_hash,
            config_hash=self.config.digest,
            warnings=[
                "Synthetic fixtures are not evidence of real-world profitability.",
                "Current-stock universe creates survivorship bias; results are preliminary.",
            ],
        )
