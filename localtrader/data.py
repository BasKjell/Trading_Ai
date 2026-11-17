from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime, time, timedelta
import hashlib
import json
import math
import random
from zoneinfo import ZoneInfo

from .domain import Bar, Market


SYNTHETIC_CATALOG: dict[str, dict[str, str]] = {
    "SPY": {"market": "equity", "asset_class": "us_index_etf"},
    "QQQ": {"market": "equity", "asset_class": "us_index_etf"},
    "AAPL": {"market": "equity", "asset_class": "us_large_cap_equity"},
    "MSFT": {"market": "equity", "asset_class": "us_large_cap_equity"},
    "NVDA": {"market": "equity", "asset_class": "us_large_cap_equity"},
    "BTC-USD": {"market": "crypto", "asset_class": "crypto_spot"},
    "ETH-USD": {"market": "crypto", "asset_class": "crypto_spot"},
    "EUR-USD": {"market": "fx", "asset_class": "fx_spot_research"},
    "GLD": {"market": "commodity", "asset_class": "gold_etf_proxy"},
    "TLT": {"market": "bond", "asset_class": "treasury_bond_etf_proxy"},
}


def available_data_catalog() -> dict[str, dict[str, str]]:
    return {symbol: dict(metadata) for symbol, metadata in SYNTHETIC_CATALOG.items()}


class DataQualityError(ValueError):
    pass


def validate_bars(bars: list[Bar]) -> None:
    seen: set[tuple[str, datetime]] = set()
    previous: dict[str, datetime] = {}
    for bar in bars:
        key = (bar.symbol, bar.start)
        if key in seen:
            raise DataQualityError(f"duplicate bar: {key}")
        seen.add(key)
        if bar.symbol in previous and bar.start <= previous[bar.symbol]:
            raise DataQualityError(f"non-monotonic bars for {bar.symbol}")
        previous[bar.symbol] = bar.start
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            raise DataQualityError(f"non-positive price for {bar.symbol}")
        if bar.high < max(bar.open, bar.close, bar.low) or bar.low > min(bar.open, bar.close, bar.high):
            raise DataQualityError(f"invalid OHLC for {bar.symbol}")
        if bar.volume < 0:
            raise DataQualityError(f"negative volume for {bar.symbol}")
        if bar.end <= bar.start or bar.received_at < bar.end:
            raise DataQualityError(f"invalid timestamps for {bar.symbol}")


def bars_hash(bars: list[Bar]) -> str:
    payload = [
        [b.symbol, b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume, b.source]
        for b in bars
    ]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def group_by_symbol(bars: list[Bar]) -> dict[str, list[Bar]]:
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in bars:
        grouped[bar.symbol].append(bar)
    return dict(grouped)


def is_market_open(timestamp: datetime, market: Market) -> bool:
    if timestamp.tzinfo is None:
        return False
    if market == Market.CRYPTO:
        return True
    local = timestamp.astimezone(ZoneInfo("America/New_York"))
    return local.weekday() < 5 and time(9, 30) <= local.time() < time(16, 0)


def synthetic_fixture(seed: int = 20261106, days: int = 1000) -> list[Bar]:
    """Deterministic, explicitly synthetic daily data for offline smoke tests."""
    specs = {
        "SPY": (Market.EQUITY, 420.0, 0.00028, 0.010),
        "QQQ": (Market.EQUITY, 350.0, 0.00032, 0.013),
        "AAPL": (Market.EQUITY, 170.0, 0.00035, 0.016),
        "MSFT": (Market.EQUITY, 330.0, 0.00036, 0.015),
        "NVDA": (Market.EQUITY, 120.0, 0.00050, 0.023),
        "BTC-USD": (Market.CRYPTO, 30000.0, 0.00055, 0.028),
        "ETH-USD": (Market.CRYPTO, 1800.0, 0.00050, 0.032),
        "EUR-USD": (Market.FX, 1.08, 0.00002, 0.004),
        "GLD": (Market.COMMODITY, 180.0, 0.00018, 0.009),
        "TLT": (Market.BOND, 100.0, 0.00008, 0.008),
    }
    rng = random.Random(seed)
    start_date = datetime(2022, 1, 1, tzinfo=UTC)
    output: list[Bar] = []
    prices = {symbol: spec[1] for symbol, spec in specs.items()}
    produced = {symbol: 0 for symbol in specs}
    cursor = start_date
    while min(produced.values()) < days:
        for symbol, (market, _, drift, vol) in specs.items():
            if produced[symbol] >= days or (market != Market.CRYPTO and cursor.weekday() >= 5):
                continue
            idx = produced[symbol]
            regime = -0.0009 if 260 <= idx < 380 else (0.00005 if 620 <= idx < 720 else drift)
            shock = rng.gauss(regime, vol)
            previous = prices[symbol]
            open_price = previous * (1 + rng.gauss(0, vol * 0.18))
            close = max(0.01, open_price * math.exp(shock))
            width = abs(rng.gauss(vol * 0.75, vol * 0.2))
            high = max(open_price, close) * (1 + width)
            low = min(open_price, close) * max(0.01, 1 - width)
            start = cursor
            end = cursor + timedelta(hours=23, minutes=59)
            output.append(Bar(
                symbol=symbol, market=market, timeframe="1d", start=start, end=end,
                open=round(open_price, 6), high=round(high, 6), low=round(low, 6),
                close=round(close, 6), volume=float(rng.randint(1_000_000, 10_000_000)),
                source="synthetic_fixture", received_at=end + timedelta(minutes=1),
            ))
            prices[symbol] = close
            produced[symbol] += 1
        cursor += timedelta(days=1)
    output.sort(key=lambda bar: (bar.symbol, bar.start))
    validate_bars(output)
    return output
