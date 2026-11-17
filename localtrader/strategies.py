from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import fmean

from .domain import Bar, Direction, Market, Signal


def sma(values: list[float], window: int) -> float | None:
    if window <= 0 or len(values) < window:
        return None
    return fmean(values[-window:])


def atr(bars: list[Bar], window: int) -> float | None:
    if len(bars) < window + 1:
        return None
    ranges: list[float] = []
    for previous, current in zip(bars[-window - 1 : -1], bars[-window:]):
        ranges.append(max(
            current.high - current.low,
            abs(current.high - previous.close),
            abs(current.low - previous.close),
        ))
    return fmean(ranges)


def _signal(
    name: str, bars: list[Bar], direction: Direction, strength: float, reason: str, stop: float | None
) -> Signal:
    latest = bars[-1]
    return Signal(
        strategy=name,
        symbol=latest.symbol,
        market=latest.market,
        signal_time=latest.end,
        earliest_execution=latest.end + timedelta(microseconds=1),
        direction=direction,
        strength=max(0.0, min(1.0, strength)),
        reason=reason,
        stop_price=stop,
    )


@dataclass(frozen=True, slots=True)
class ETFTrendStrategy:
    fast_window: int = 50
    slow_window: int = 200
    breakout_window: int = 55
    atr_window: int = 20
    stop_atr: float = 3.0
    name: str = "etf_trend"

    def evaluate(self, bars: list[Bar]) -> Signal | None:
        if not bars or bars[-1].market != Market.EQUITY:
            return None
        closes = [bar.close for bar in bars]
        fast, slow, volatility = sma(closes, self.fast_window), sma(closes, self.slow_window), atr(bars, self.atr_window)
        if fast is None or slow is None or volatility is None or len(closes) <= self.breakout_window:
            return None
        prior_high = max(closes[-self.breakout_window - 1 : -1])
        latest = closes[-1]
        long = latest > slow and fast > slow and latest >= prior_high
        if long:
            strength = (latest / slow - 1) / 0.20
            return _signal(self.name, bars, Direction.LONG, strength, "trend and prior-high breakout", latest - self.stop_atr * volatility)
        if latest < fast or fast < slow:
            return _signal(self.name, bars, Direction.FLAT, 1.0, "trend exit", None)
        return None


@dataclass(frozen=True, slots=True)
class CryptoTrendStrategy:
    fast_window: int = 50
    slow_window: int = 200
    breakout_window: int = 55
    atr_window: int = 20
    stop_atr: float = 3.5
    name: str = "crypto_trend"

    def evaluate(self, bars: list[Bar]) -> Signal | None:
        if not bars or bars[-1].market != Market.CRYPTO:
            return None
        closes = [bar.close for bar in bars]
        fast, slow, volatility = sma(closes, self.fast_window), sma(closes, self.slow_window), atr(bars, self.atr_window)
        if fast is None or slow is None or volatility is None or len(closes) <= self.breakout_window:
            return None
        latest = closes[-1]
        prior_high = max(closes[-self.breakout_window - 1 : -1])
        if latest > slow and fast > slow and latest >= prior_high:
            return _signal(self.name, bars, Direction.LONG, (latest / slow - 1) / 0.30, "crypto trend breakout", latest - self.stop_atr * volatility)
        if latest < fast or fast < slow:
            return _signal(self.name, bars, Direction.FLAT, 1.0, "crypto trend exit", None)
        return None


@dataclass(frozen=True, slots=True)
class StockMomentumStrategy:
    trend_window: int = 200
    momentum_window: int = 126
    skip_recent: int = 21
    atr_window: int = 20
    stop_atr: float = 3.0
    top_n: int = 2
    name: str = "stock_momentum"

    def rank(self, histories: dict[str, list[Bar]]) -> list[Signal]:
        ranked: list[tuple[float, list[Bar], float]] = []
        for bars in histories.values():
            if not bars or bars[-1].market != Market.EQUITY or len(bars) < max(self.trend_window, self.momentum_window + self.skip_recent) + 1:
                continue
            closes = [bar.close for bar in bars]
            trend = sma(closes, self.trend_window)
            volatility = atr(bars, self.atr_window)
            if trend is None or volatility is None or closes[-1] <= trend:
                continue
            old = closes[-self.momentum_window - self.skip_recent]
            recent = closes[-self.skip_recent - 1]
            score = recent / old - 1
            if score > 0:
                ranked.append((score, bars, volatility))
        ranked.sort(key=lambda item: (-item[0], item[1][-1].symbol))
        selected = {bars[-1].symbol for _, bars, _ in ranked[: self.top_n]}
        signals: list[Signal] = []
        for score, bars, volatility in ranked:
            if bars[-1].symbol in selected:
                signals.append(_signal(self.name, bars, Direction.LONG, score / 0.50, "cross-sectional momentum rank", bars[-1].close - self.stop_atr * volatility))
            else:
                signals.append(_signal(self.name, bars, Direction.FLAT, 1.0, "outside top momentum rank", None))
        return signals

