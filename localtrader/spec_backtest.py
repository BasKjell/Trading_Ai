from __future__ import annotations

from datetime import datetime, timedelta
import hashlib

from .backtest import BacktestResult, calculate_metrics
from .config import AppConfig
from .data import group_by_symbol, validate_bars
from .domain import Bar, Direction, Fill, OrderIntent, PortfolioSnapshot, Signal
from .execution import FillModel, IdempotentPaperExecutor
from .portfolio import Portfolio
from .risk import RiskEngine
from .strategy_dsl import StrategySpec, evaluate_stop_expression, rules_match, values_for_bar


def _periods_per_year(spec: StrategySpec) -> float:
    minutes = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 1440}[spec.timeframe]
    if spec.market.value == "crypto":
        return 365.25 if spec.timeframe == "1Day" else 365.25 * 1440 / minutes
    return 252.0 if spec.timeframe == "1Day" else 252.0 * 390 / minutes


def _id(spec: StrategySpec, symbol: str, timestamp: datetime, direction: Direction) -> str:
    raw = f"dsl|{spec.name}|{spec.version}|{symbol}|{timestamp.isoformat()}|{direction.value}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _signal(spec: StrategySpec, bars: list[Bar], direction: Direction, stop: float | None, reason: str) -> Signal:
    latest = bars[-1]
    return Signal(
        strategy=spec.family, symbol=latest.symbol, market=latest.market,
        signal_time=latest.end, earliest_execution=latest.end + timedelta(microseconds=1),
        direction=direction, strength=1.0, reason=f"{spec.name}: {reason}",
        version=spec.version, stop_price=stop,
    )


def _intent(spec: StrategySpec, signal: Signal, quantity: float, now: datetime) -> OrderIntent:
    return OrderIntent(
        client_order_id=_id(spec, signal.symbol, signal.signal_time, signal.direction),
        strategy=spec.family, symbol=signal.symbol, market=signal.market, created_at=now,
        direction=signal.direction, target_quantity=quantity, order_type="market",
        valid_until=now + timedelta(days=4), stop_price=signal.stop_price,
        risk_metadata={"dsl_spec": 1.0},
    )


class DeclarativeBacktestEngine:
    """Runs validated declarative specs through the normal risk and fill layers."""

    def __init__(self, config: AppConfig, cost_multiplier: float = 1.0) -> None:
        self.config = config
        self.cost_multiplier = cost_multiplier

    def run(self, spec: StrategySpec, bars: list[Bar], data_hash: str) -> BacktestResult:
        if spec.is_shadow_only:
            raise ValueError("shadow/event specs require event datasets and cannot trade price-only bars")
        validate_bars(bars)
        selected_bars = [
            bar for bar in bars
            if bar.symbol in spec.universe and (bar.market == spec.market or spec.market.value == "multi_asset")
        ]
        if not selected_bars:
            raise ValueError("strategy universe has no matching bars")
        grouped = group_by_symbol(selected_bars)
        missing = set(spec.universe) - set(grouped)
        if missing:
            raise ValueError(f"missing symbols: {sorted(missing)}")
        aliases = {"1d": "1Day", "1day": "1Day", "1h": "1Hour", "1hour": "1Hour"}
        wrong_timeframes = {
            bar.timeframe for bar in selected_bars
            if aliases.get(bar.timeframe.lower(), bar.timeframe) != spec.timeframe
        }
        if wrong_timeframes:
            raise ValueError(f"dataset timeframe does not match strategy {spec.timeframe}: {sorted(wrong_timeframes)}")
        by_time: dict[datetime, dict[str, Bar]] = {}
        for symbol, items in grouped.items():
            for bar in items:
                by_time.setdefault(bar.start, {})[symbol] = bar

        portfolio = Portfolio(self.config.initial_cash)
        risk = RiskEngine(self.config.risk)
        executor = IdempotentPaperExecutor(FillModel(self.config.costs, self.cost_multiplier))
        histories: dict[str, list[Bar]] = {symbol: [] for symbol in spec.universe}
        pending: dict[str, Signal] = {}
        entry_basis: dict[str, float] = {}
        fills: list[Fill] = []
        snapshots: list[PortfolioSnapshot] = []
        decisions: list[dict[str, object]] = []
        prices: dict[str, float] = {}
        day_start_equity = self.config.initial_cash
        rebalance_counter = 0
        realized = 0.0

        for timestamp in sorted(by_time):
            current = by_time[timestamp]
            if snapshots and snapshots[-1].timestamp.date() != timestamp.date():
                day_start_equity = snapshots[-1].equity
            for symbol, bar in current.items():
                prices[symbol] = bar.open
                position = portfolio.positions.get(symbol)
                if position and position.stop_price is not None and bar.low <= position.stop_price:
                    stop_signal = _signal(spec, histories[symbol], Direction.FLAT, None, "protective stop")
                    intent = _intent(spec, stop_signal, position.quantity, timestamp)
                    _, fill = executor.execute(intent, position.quantity, min(bar.open, position.stop_price), timestamp)
                    if fill:
                        before = portfolio.cash
                        basis = entry_basis.pop(symbol, 0.0)
                        portfolio.apply_fill(fill, spec.family)
                        realized += portfolio.cash - before - basis
                        fills.append(fill)
                        pending.pop(symbol, None)

            for symbol, signal in list(pending.items()):
                bar = current.get(symbol)
                if bar is None or timestamp < signal.earliest_execution:
                    continue
                position = portfolio.positions.get(symbol)
                if signal.direction == Direction.FLAT:
                    if position:
                        intent = _intent(spec, signal, position.quantity, timestamp)
                        _, fill = executor.execute(intent, position.quantity, bar.open, timestamp)
                        if fill:
                            before = portfolio.cash
                            basis = entry_basis.pop(symbol, 0.0)
                            portfolio.apply_fill(fill, spec.family)
                            realized += portfolio.cash - before - basis
                            fills.append(fill)
                    pending.pop(symbol, None)
                    continue
                if position is None:
                    provisional = risk.size_for_stop(portfolio.equity(prices), bar.open, signal.stop_price or bar.open)
                    intent = _intent(spec, signal, provisional, timestamp)
                    decision = risk.decide(intent, portfolio, prices, timestamp, day_start_equity)
                    decisions.append({
                        "time": timestamp.isoformat(), "symbol": symbol, "allowed": decision.allowed,
                        "reason": decision.reason, "limits": list(decision.violated_limits),
                    })
                    if decision.allowed:
                        _, fill = executor.execute(intent, decision.approved_quantity, bar.open, timestamp)
                        if fill:
                            before = portfolio.cash
                            portfolio.apply_fill(fill, spec.family, signal.stop_price)
                            entry_basis[symbol] = before - portfolio.cash
                            fills.append(fill)
                pending.pop(symbol, None)

            for symbol, bar in current.items():
                prices[symbol] = bar.close
                histories[symbol].append(bar)
                position = portfolio.positions.get(symbol)
                if position and spec.stop_mode == "trailing":
                    values = values_for_bar(spec, histories[symbol])
                    if values is not None:
                        candidate_stop = evaluate_stop_expression(spec, values)
                        if 0 < candidate_stop < bar.close:
                            position.stop_price = max(position.stop_price or 0.0, candidate_stop)

            if all(len(items) >= spec.warmup for items in histories.values()):
                rebalance_counter += 1
                for symbol, position in list(portfolio.positions.items()):
                    if symbol in histories and rules_match(spec, histories[symbol], spec.exit_any, require_all=False):
                        pending[symbol] = _signal(spec, histories[symbol], Direction.FLAT, None, "exit rule")
                if rebalance_counter % spec.rebalance_every == 0:
                    candidates: list[tuple[float, str, float]] = []
                    for symbol, history in histories.items():
                        if rules_match(spec, history, spec.entry_all, require_all=True):
                            values = values_for_bar(spec, history)
                            assert values is not None
                            score = values[spec.rank_by] if spec.rank_by else 0.0
                            stop = evaluate_stop_expression(spec, values)
                            if stop > 0 and stop < history[-1].close:
                                candidates.append((score, symbol, stop))
                    candidates.sort(key=lambda item: (-item[0], item[1]))
                    selected = {symbol for _, symbol, _ in candidates[: spec.max_positions]}
                    if spec.rank_by:
                        for symbol, position in list(portfolio.positions.items()):
                            if position.strategy == spec.family and symbol not in selected and symbol in histories:
                                pending[symbol] = _signal(spec, histories[symbol], Direction.FLAT, None, "left rank selection")
                    for _, symbol, stop in candidates[: spec.max_positions]:
                        if symbol not in portfolio.positions:
                            pending[symbol] = _signal(spec, histories[symbol], Direction.LONG, stop, "entry rules")
            snapshots.append(portfolio.snapshot(timestamp, prices))

        terminal = max(by_time) + timedelta(days=1)
        for symbol, position in list(portfolio.positions.items()):
            signal = _signal(spec, histories[symbol], Direction.FLAT, None, "terminal liquidation")
            intent = _intent(spec, signal, position.quantity, terminal)
            _, fill = executor.execute(intent, position.quantity, prices[symbol], terminal)
            if fill:
                before = portfolio.cash
                basis = entry_basis.pop(symbol, 0.0)
                portfolio.apply_fill(fill, spec.family)
                realized += portfolio.cash - before - basis
                fills.append(fill)
        snapshots.append(portfolio.snapshot(terminal, prices))
        metrics = calculate_metrics(snapshots, fills, self.config.initial_cash, _periods_per_year(spec))
        first_symbol = grouped[spec.universe[0]]
        benchmark = first_symbol[-1].close / first_symbol[0].open - 1
        return BacktestResult(
            snapshots=snapshots, fills=fills, decisions=decisions, metrics=metrics,
            strategy_pnl={spec.name: realized},
            benchmark={f"{spec.universe[0]}_buy_hold_total_return": benchmark},
            data_hash=data_hash, config_hash=self.config.digest,
            warnings=["Research result only; paper/live promotion requires explicit approval."],
        )
