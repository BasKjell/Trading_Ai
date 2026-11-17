"""Intraday base strategies evaluated on completed 15-minute bars.

Strategies are pure functions of the data available at a bar's close. They return a ``Signal`` (entry
with a protective stop, or an exit) or ``None`` ("do nothing" is a valid decision). Execution happens
no earlier than the next bar's open; the engine enforces this.

A. ``IntradayTrendStrategy`` – economic hypothesis: markets under-react to new information, so moves
   that break a multi-hour range *in the direction of the higher-timeframe trend* tend to persist for
   hours to days (time-series momentum). Few parameters: trend window (1 h bars), breakout window,
   exit window, ATR stop multiple, optional time stop.

B. ``MeanReversionStrategy`` – hypothesis: short-horizon deviations from a moving reference in calm,
   range-bound phases are largely liquidity-driven over-reactions that revert. A trend filter (Kaufman
   efficiency ratio) keeps it out of directional phases. Exit on return to the reference, a stop, or a
   maximum holding time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import fmean, pstdev
from typing import Protocol

from .domain import Bar, Direction, Signal

STRATEGY_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Everything a strategy may look at for the decision at ``now`` (a completed bar's end)."""

    now: datetime
    bars: list[Bar]  # completed 15m bars, last one ends at ``now``
    hourly: list[Bar]  # 1h bars completely available at ``now``
    in_position: bool
    entry_time: datetime | None
    entry_price: float | None


class IntradayStrategy(Protocol):
    @property
    def name(self) -> str: ...

    def evaluate(self, ctx: StrategyContext) -> Signal | None: ...

    def parameters(self) -> dict[str, float]: ...


def sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return fmean(values[-window:])


def atr(bars: list[Bar], window: int) -> float | None:
    if len(bars) < window + 1:
        return None
    ranges = [
        max(cur.high - cur.low, abs(cur.high - prev.close), abs(cur.low - prev.close))
        for prev, cur in zip(bars[-window - 1 : -1], bars[-window:], strict=True)
    ]
    return fmean(ranges)


def efficiency_ratio(closes: list[float], window: int) -> float | None:
    """Kaufman efficiency ratio in [0, 1]: 1 = straight trend, ~0 = noise/range."""
    if len(closes) < window + 1:
        return None
    path = sum(abs(b - a) for a, b in zip(closes[-window - 1 : -1], closes[-window:], strict=True))
    return abs(closes[-1] - closes[-window - 1]) / path if path > 0 else 0.0


def _signal(name: str, ctx: StrategyContext, direction: Direction, reason: str, stop: float | None) -> Signal:
    bar = ctx.bars[-1]
    return Signal(
        strategy=name,
        symbol=bar.symbol,
        market=bar.market,
        signal_time=ctx.now,
        # The next bar opens exactly at ``now``; its open is the earliest executable price.
        earliest_execution=ctx.now,
        direction=direction,
        strength=1.0,
        reason=reason,
        version=STRATEGY_VERSION,
        stop_price=stop,
    )


@dataclass(frozen=True, slots=True)
class IntradayTrendStrategy:
    trend_window_hours: int = 50
    breakout_bars: int = 16
    exit_bars: int = 8
    atr_bars: int = 14
    stop_atr: float = 2.5
    max_hold_bars: int = 0  # 0 = no time stop
    name: str = "intraday_trend"

    def parameters(self) -> dict[str, float]:
        return {
            "trend_window_hours": self.trend_window_hours,
            "breakout_bars": self.breakout_bars,
            "exit_bars": self.exit_bars,
            "atr_bars": self.atr_bars,
            "stop_atr": self.stop_atr,
            "max_hold_bars": self.max_hold_bars,
        }

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        bars = ctx.bars
        needed = max(self.breakout_bars, self.exit_bars, self.atr_bars) + 1
        if len(bars) < needed:
            return None
        hourly_closes = [bar.close for bar in ctx.hourly]
        trend = sma(hourly_closes, self.trend_window_hours)
        close = bars[-1].close
        if ctx.in_position:
            if self.max_hold_bars and ctx.entry_time is not None:
                held = sum(1 for bar in bars if bar.start >= ctx.entry_time)
                if held >= self.max_hold_bars:
                    return _signal(self.name, ctx, Direction.FLAT, f"time stop after {held} bars", None)
            exit_low = min(bar.low for bar in bars[-self.exit_bars - 1 : -1])
            if close < exit_low:
                return _signal(
                    self.name, ctx, Direction.FLAT, f"close {close:.2f} below {self.exit_bars}-bar low", None
                )
            if trend is not None and hourly_closes[-1] < trend:
                return _signal(self.name, ctx, Direction.FLAT, "higher-timeframe trend turned down", None)
            return None
        if trend is None or hourly_closes[-1] <= trend:
            return None
        breakout = max(bar.high for bar in bars[-self.breakout_bars - 1 : -1])
        volatility = atr(bars, self.atr_bars)
        if volatility is None or volatility <= 0 or close <= breakout:
            return None
        stop = close - self.stop_atr * volatility
        reason = f"close {close:.2f} > {self.breakout_bars}-bar high {breakout:.2f}; 1h close above SMA{self.trend_window_hours}"
        return _signal(self.name, ctx, Direction.LONG, reason, stop)


@dataclass(frozen=True, slots=True)
class MeanReversionStrategy:
    reference_bars: int = 20
    entry_z: float = 2.0
    trend_filter_bars: int = 32
    max_efficiency: float = 0.30
    atr_bars: int = 14
    stop_atr: float = 2.0
    max_hold_bars: int = 16
    name: str = "mean_reversion"

    def parameters(self) -> dict[str, float]:
        return {
            "reference_bars": self.reference_bars,
            "entry_z": self.entry_z,
            "trend_filter_bars": self.trend_filter_bars,
            "max_efficiency": self.max_efficiency,
            "atr_bars": self.atr_bars,
            "stop_atr": self.stop_atr,
            "max_hold_bars": self.max_hold_bars,
        }

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        bars = ctx.bars
        if len(bars) < max(self.reference_bars, self.trend_filter_bars, self.atr_bars) + 1:
            return None
        closes = [bar.close for bar in bars]
        reference = sma(closes, self.reference_bars)
        assert reference is not None
        close = closes[-1]
        if ctx.in_position:
            if close >= reference:
                return _signal(self.name, ctx, Direction.FLAT, f"returned to reference {reference:.2f}", None)
            if ctx.entry_time is not None:
                held = sum(1 for bar in bars if bar.start >= ctx.entry_time)
                if held >= self.max_hold_bars:
                    return _signal(self.name, ctx, Direction.FLAT, f"max holding time {held} bars", None)
            return None
        deviation = pstdev(closes[-self.reference_bars :])
        if deviation <= 0:
            return None
        z = (close - reference) / deviation
        efficiency = efficiency_ratio(closes, self.trend_filter_bars)
        volatility = atr(bars, self.atr_bars)
        if efficiency is None or volatility is None or volatility <= 0:
            return None
        if z > -self.entry_z or efficiency > self.max_efficiency:
            return None
        stop = close - self.stop_atr * volatility
        reason = f"z={z:.2f} <= -{self.entry_z}; efficiency {efficiency:.2f} <= {self.max_efficiency}"
        return _signal(self.name, ctx, Direction.LONG, reason, stop)


def build_strategy(name: str, params: dict[str, float]) -> IntradayStrategy:
    cleaned = {key: value for key, value in params.items() if key != "enabled"}
    int_fields = {
        "trend_window_hours",
        "breakout_bars",
        "exit_bars",
        "atr_bars",
        "max_hold_bars",
        "reference_bars",
        "trend_filter_bars",
    }
    typed = {key: int(value) if key in int_fields else float(value) for key, value in cleaned.items()}
    if name == "intraday_trend":
        return IntradayTrendStrategy(**typed)  # type: ignore[arg-type]
    if name == "mean_reversion":
        return MeanReversionStrategy(**typed)  # type: ignore[arg-type]
    raise ValueError(f"unknown intraday strategy: {name}")
