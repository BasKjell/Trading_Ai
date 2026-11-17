"""Download multi-year 15-minute ETF bars from Alpaca into the CSV format read by ``import_csv``.

Source: Alpaca Market Data v2 ``/stocks/bars`` with the existing paper key (Basic plan, no extra cost),
feed ``sip`` (consolidated tape, all US venues). Two series are fetched and kept apart:

* ``<SYMBOL>_15m.csv`` – ``adjustment=raw``: prices as actually traded. The engine uses only these for
  signals, fills, stops and valuation.
* ``<SYMBOL>_15m_adjusted.csv`` – ``adjustment=all``: split/dividend adjusted, for analysis only.
* ``<SYMBOL>_dividends.csv`` – cash dividend per share by ex-date, derived from the two series
  (see :func:`derive_dividends`). The engine books them on held positions.

Splits are not supported yet: a split in the window aborts the download (fail closed).
Only bars inside the regular NYSE session of that day are written (early closes from
:mod:`localtrader.calendars`); extended hours have thin volume and Alpaca accepts only limit orders there.
Raw API pages and a manifest stay under ``data/raw/alpaca/<request_id>/`` (not versioned).
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

from .calendars import NYSE_COVERED_UNTIL, NYSE_EARLY_CLOSE, NYSE_EARLY_CLOSES
from .domain import Bar
from .intraday_data import SessionSpec

US_REGULAR_SESSION = SessionSpec(
    "America/New_York", time(9, 30), time(16, 0), NYSE_EARLY_CLOSES, NYSE_EARLY_CLOSE, NYSE_COVERED_UNTIL
)
CSV_HEADER = ("timestamp", "open", "high", "low", "close", "volume")
# A day-to-day change of adjusted/raw below this is rounding noise; above it is a corporate action.
DIVIDEND_MIN_STEP = 2e-4
# A larger factor change than this cannot be a cash dividend of an index ETF (e.g. a split).
MAX_DIVIDEND_YIELD = 0.10


class CorporateActionError(ValueError):
    pass


class BarDownloader(Protocol):
    def download(
        self, request: Any, progress: Callable[[str], None] | None = None
    ) -> tuple[list[Bar], dict[str, Any], Path]: ...


def regular_session_bars(bars: list[Bar], session: SessionSpec = US_REGULAR_SESSION) -> list[Bar]:
    """Bars that start inside that day's regular session (early closes respected)."""
    return [bar for bar in bars if session.contains(bar.start)]


def write_csv(bars: list[Bar], path: str | Path) -> str:
    """Write bars as UTC ISO timestamps; returns the SHA-256 of the file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for bar in sorted(bars, key=lambda b: b.start):
            writer.writerow(
                [
                    bar.start.astimezone(UTC).isoformat(),
                    repr(bar.open),
                    repr(bar.high),
                    repr(bar.low),
                    repr(bar.close),
                    repr(bar.volume),
                ]
            )
    return hashlib.sha256(target.read_bytes()).hexdigest()


def derive_dividends(
    raw: list[Bar], adjusted: list[Bar], session: SessionSpec = US_REGULAR_SESSION
) -> dict[date, float]:
    """Cash dividend per share by ex-date from raw and dividend-adjusted closes.

    The adjusted price is ``raw * f``, where ``f`` multiplies ``(1 - D / prev_close)`` for every later
    ex-date. Between the last day before and the ex-date the ratio therefore moves from ``r_prev`` to
    ``r_ex`` with ``D = prev_close * (1 - r_prev / r_ex)``. Within a day the ratio must be constant.
    """
    adjusted_by_start = {bar.start: bar for bar in adjusted}
    daily: dict[date, tuple[float, float]] = {}  # day -> (ratio, raw close of the day)
    for bar in sorted(raw, key=lambda b: b.start):
        twin = adjusted_by_start.get(bar.start)
        if twin is None:
            raise CorporateActionError(f"adjusted series misses bar {bar.start.isoformat()}")
        ratio = twin.close / bar.close
        day = session.local_date(bar.start)
        if day in daily and abs(ratio / daily[day][0] - 1) > DIVIDEND_MIN_STEP:
            raise CorporateActionError(f"adjustment changes within {day}")
        daily[day] = (daily[day][0] if day in daily else ratio, bar.close)
    dividends: dict[date, float] = {}
    days = sorted(daily)
    for previous, current in zip(days, days[1:], strict=False):
        r_prev, prev_close = daily[previous]
        r_ex = daily[current][0]
        step = r_prev / r_ex
        if abs(step - 1) <= DIVIDEND_MIN_STEP:
            continue
        if step > 1 or step < 1 - MAX_DIVIDEND_YIELD:
            raise CorporateActionError(f"unsupported corporate action on {current} (factor step {step:.6f})")
        dividends[current] = round(prev_close * (1 - step), 4)
    return dividends


def dividend_gaps(dividends: dict[date, float], first: date, last: date, max_days: int = 120) -> list[str]:
    """Stretches longer than ``max_days`` without an ex-date for a quarterly payer (likely missing data).

    Alpaca's adjusted bars and its corporate-actions endpoint both lack some SPY dividends (e.g. 2016-03
    and 2018-06), so a gap is reported in the manifest instead of being silently ignored.
    """
    marks = [first, *sorted(dividends), last]
    return [
        f"{a.isoformat()}..{b.isoformat()}" for a, b in zip(marks, marks[1:], strict=False) if (b - a).days > max_days
    ]


def write_dividends(dividends: dict[date, float], path: str | Path) -> str:
    target = Path(path)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("ex_date", "amount"))
        for day in sorted(dividends):
            writer.writerow((day.isoformat(), repr(dividends[day])))
    return hashlib.sha256(target.read_bytes()).hexdigest()


def read_dividends(path: str | Path) -> dict[date, float]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return {date.fromisoformat(row["ex_date"]): float(row["amount"]) for row in csv.DictReader(handle)}


def output_paths(output: str | Path) -> dict[str, Path]:
    """``data/SPY_15m.csv`` -> raw file, adjusted file, dividends file, manifest."""
    raw = Path(output)
    base = raw.with_suffix("")
    return {
        "raw": raw,
        "adjusted": base.with_name(base.name + "_adjusted.csv"),
        "dividends": base.with_name(base.name.removesuffix("_15m") + "_dividends.csv"),
        "manifest": raw.with_name(raw.name + ".manifest.json"),
    }


def fetch_intraday_csv(
    downloader: BarDownloader,
    symbol: str,
    start: datetime,
    end: datetime,
    output: str | Path,
    feed: str = "sip",
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    from .data_requests import DataRequestSpec

    paths = output_paths(output)
    series: dict[str, list[Bar]] = {}
    requests: dict[str, dict[str, Any]] = {}
    for adjustment in ("raw", "all"):
        request = DataRequestSpec(
            provider="alpaca",
            endpoint_type="stocks",
            symbols=(symbol,),
            timeframe="15Min",
            start=start,
            end=end,
            feed=feed,
            purpose="research:T-018 multi-year 15-minute data",
            strategy_name="intraday",
            adjustment=adjustment,
        )
        bars, manifest, _ = downloader.download(request, progress)
        series[adjustment] = regular_session_bars([b for b in bars if b.symbol == symbol])
        requests[adjustment] = {
            "request_id": manifest.get("request_id"),
            "downloaded_at": manifest.get("downloaded_at"),
            "bars_downloaded": len(bars),
        }
    dividends = derive_dividends(series["raw"], series["all"])  # raises before any file is written
    raw_bars = series["raw"]
    summary = {
        "symbol": symbol,
        "provider": "alpaca",
        "feed": feed,
        "requests": requests,
        "bars_regular_session": len(raw_bars),
        "first_bar": raw_bars[0].start.isoformat() if raw_bars else None,
        "last_bar": raw_bars[-1].start.isoformat() if raw_bars else None,
        "files": {
            "raw": {"path": str(paths["raw"]), "sha256": write_csv(raw_bars, paths["raw"])},
            "adjusted": {"path": str(paths["adjusted"]), "sha256": write_csv(series["all"], paths["adjusted"])},
            "dividends": {"path": str(paths["dividends"]), "sha256": write_dividends(dividends, paths["dividends"])},
        },
        "dividends": len(dividends),
        "dividend_gaps": dividend_gaps(
            dividends,
            US_REGULAR_SESSION.local_date(raw_bars[0].start),
            US_REGULAR_SESSION.local_date(raw_bars[-1].start),
        )
        if raw_bars
        else [],
        "calendar": f"NYSE early closes until {NYSE_COVERED_UNTIL.isoformat()}",
    }
    paths["manifest"].write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def default_end(now: datetime | None = None) -> datetime:
    """Latest allowed end: at least 15 minutes old (Basic plan SIP restriction), aligned to 15 minutes."""
    moment = (now or datetime.now(UTC)) - timedelta(minutes=30)
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)
