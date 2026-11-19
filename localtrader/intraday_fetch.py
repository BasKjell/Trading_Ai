"""Download multi-year 15-minute ETF bars from Alpaca into the CSV format read by ``import_csv``.

Source: Alpaca Market Data v2 ``/stocks/bars`` with the existing paper key (Basic plan, no extra cost),
feed ``sip`` (consolidated tape, all US venues). Two series are fetched and kept apart:

* ``<SYMBOL>_15m.csv`` – ``adjustment=raw``: prices as actually traded. The engine uses only these for
  signals, fills, stops and valuation.
* ``<SYMBOL>_15m_adjusted.csv`` – ``adjustment=all``: split/dividend adjusted, for analysis only.
* ``<SYMBOL>_dividends.csv`` – cash dividends from Alpaca's corporate-actions endpoint: ex-date, payment
  date and amount per share. The engine records an entitlement at the ex-date and pays cash on the payment
  date. As an independent cross-check the amounts are also derived from the raw and adjusted series
  (:func:`derive_dividends`); a deviation above 5 % aborts the download.

Splits are not supported yet: a split in the window (either source) aborts the download (fail closed).
Only bars inside the regular NYSE session of that day are written (early closes from
:mod:`localtrader.calendars`); extended hours have thin volume and Alpaca accepts only limit orders there.
Raw API pages and a manifest stay under ``data/raw/alpaca/<request_id>/`` (not versioned).
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from statistics import median
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .calendars import NYSE_COVERED_UNTIL, NYSE_EARLY_CLOSE, NYSE_EARLY_CLOSES
from .domain import Bar
from .intraday_data import SessionSpec

US_REGULAR_SESSION = SessionSpec(
    "America/New_York", time(9, 30), time(16, 0), NYSE_EARLY_CLOSES, NYSE_EARLY_CLOSE, NYSE_COVERED_UNTIL
)
CSV_HEADER = ("timestamp", "open", "high", "low", "close", "volume")
# A day-to-day change of adjusted/raw below this is rounding noise; above it is a corporate action.
DIVIDEND_MIN_STEP = 2e-4
# Alpaca rounds adjusted (and most raw) prices to whole cents. The ratio adjusted/raw of one bar is therefore
# only known within this relative band (T-021: EFA at ~42 USD adjusted moves by about 2.4e-4 within a day).
PRICE_TICK = 0.01


def rounding_band(raw_close: float, adjusted_close: float) -> float:
    """Largest relative error of adjusted/raw caused by rounding both prices to half a tick."""
    return 0.5 * PRICE_TICK / adjusted_close + 0.5 * PRICE_TICK / raw_close


# A larger factor change than this cannot be a cash dividend of an index ETF (e.g. a split).
MAX_DIVIDEND_YIELD = 0.10


class CorporateActionError(ValueError):
    pass


class BarDownloader(Protocol):
    def download(
        self, request: Any, progress: Callable[[str], None] | None = None
    ) -> tuple[list[Bar], dict[str, Any], Path]: ...

    def corporate_actions(self, symbol: str, start: date, end: date) -> dict[str, list[dict[str, Any]]]: ...


Dividend = tuple[date, float]  # (payment date, amount per share), keyed by ex-date
MAX_CROSSCHECK_DEVIATION = 0.05
BAR_WIDTH = timedelta(minutes=15)


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

    Strict rule of the central T-008 validator (``t008_central``), kept unchanged on purpose (PR #48 review).
    Research downloads use ``derive_dividends_with_precision`` instead.
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


def derive_dividends_with_precision(
    raw: list[Bar], adjusted: list[Bar], session: SessionSpec = US_REGULAR_SESSION
) -> tuple[dict[date, float], dict[date, float]]:
    """Cash dividend per share and its absolute precision in price units, both by ex-date.

    The precision is what cent rounding allows: ``prev_close`` times the larger rounding band of the two
    days. A small dividend (EEM 2021-12-30: 0.026 USD, 0.05 % of the price) cannot be measured to 5 % from
    cent-rounded prices; it is checked against this absolute precision instead.

    Cash dividend per share by ex-date from raw and dividend-adjusted closes.

    The adjusted price is ``raw * f``, where ``f`` multiplies ``(1 - D / prev_close)`` for every later
    ex-date. Between the last day before and the ex-date the ratio therefore moves from ``r_prev`` to
    ``r_ex`` with ``D = prev_close * (1 - r_prev / r_ex)``. Within a day the ratio must be constant up to
    cent rounding (``rounding_band``); the day's ratio is the median over its bars, which damps that noise.
    """
    adjusted_by_start = {bar.start: bar for bar in adjusted}
    # day -> (adjusted/raw ratios of the day's bars, rounding band of the first bar, raw close of the day)
    daily: dict[date, tuple[list[float], float, float]] = {}
    for bar in sorted(raw, key=lambda b: b.start):
        twin = adjusted_by_start.get(bar.start)
        if twin is None:
            raise CorporateActionError(f"adjusted series misses bar {bar.start.isoformat()}")
        ratio = twin.close / bar.close
        band = rounding_band(bar.close, twin.close)
        day = session.local_date(bar.start)
        if day in daily:
            ratios, first_band, _ = daily[day]
            if abs(ratio / ratios[0] - 1) > max(DIVIDEND_MIN_STEP, first_band + band):
                raise CorporateActionError(f"adjustment changes within {day}")
            ratios.append(ratio)
            daily[day] = (ratios, first_band, bar.close)
        else:
            daily[day] = ([ratio], band, bar.close)
    dividends: dict[date, float] = {}
    precision: dict[date, float] = {}
    days = sorted(daily)
    for previous, current in zip(days, days[1:], strict=False):
        ratios_prev, band_prev, prev_close = daily[previous]
        ratios_ex, band_ex, _ = daily[current]
        step = median(ratios_prev) / median(ratios_ex)
        if abs(step - 1) <= max(DIVIDEND_MIN_STEP, band_prev + band_ex):
            continue
        if step > 1 or step < 1 - MAX_DIVIDEND_YIELD:
            raise CorporateActionError(f"unsupported corporate action on {current} (factor step {step:.6f})")
        dividends[current] = round(prev_close * (1 - step), 4)
        precision[current] = prev_close * max(band_prev, band_ex)
    return dividends, precision


def dividend_gaps(dividends: dict[date, float], first: date, last: date, max_days: int = 120) -> list[str]:
    """Stretches longer than ``max_days`` without an ex-date for a quarterly payer (likely missing data).

    Alpaca's adjusted bars and its corporate-actions endpoint both lack some SPY dividends (e.g. 2016-03
    and 2018-06), so a gap is reported in the manifest instead of being silently ignored.
    """
    marks = [first, *sorted(dividends), last]
    return [
        f"{a.isoformat()}..{b.isoformat()}" for a, b in zip(marks, marks[1:], strict=False) if (b - a).days > max_days
    ]


def dividends_from_actions(
    actions: dict[str, list[dict[str, Any]]],
    symbol: str,
    first: date,
    last: date,
    available_until: datetime | None = None,
) -> dict[date, Dividend]:
    """Cash dividends with ex-date in [first, last]; any split in that window aborts."""
    for kind, items in actions.items():
        if "split" in kind:
            for item in items:
                ex = date.fromisoformat(str(item.get("ex_date")))
                if item.get("symbol", symbol) == symbol and first <= ex <= last:
                    raise CorporateActionError(f"{kind} on {ex} is not supported")
    raw: dict[date, tuple[date | None, float]] = {}
    for item in actions.get("cash_dividends", []):
        if item.get("symbol", symbol) != symbol:
            continue
        ex = date.fromisoformat(str(item["ex_date"]))
        if not first <= ex <= last:
            continue
        if available_until is not None and not ex_date_known_before(ex, available_until):
            continue  # never let a holdout action influence research data (e.g. the payment-lag estimate)
        pay = date.fromisoformat(str(item["payable_date"])) if item.get("payable_date") else None
        amount = float(item["rate"])
        if ex in raw and raw[ex] != (pay, amount):
            previous_pay, previous_amount = raw[ex]
            if previous_amount != amount:
                raise CorporateActionError(f"conflicting dividend amounts for ex-date {ex}")
            # same amount, different payment dates (seen at Alpaca for QQQ 2022-09-19): keep the later
            # date, i.e. cash arrives later (conservative); reported via duplicate_pay_dates()
            known = [d for d in (previous_pay, pay) if d is not None]
            pay = max(known) if known else None
        raw[ex] = (pay, amount)
    dividends, _ = fill_missing_pay_dates(raw)
    validate_dividends(dividends)
    return dividends


def duplicate_pay_dates(
    actions: dict[str, list[dict[str, Any]]], symbol: str, first: date | None = None, last: date | None = None
) -> list[str]:
    """Ex-dates within [first, last] listed more than once with different payment dates (for the manifest)."""
    by_ex: dict[str, set[str]] = {}
    for item in actions.get("cash_dividends", []):
        ex = date.fromisoformat(str(item["ex_date"]))
        if (first is not None and ex < first) or (last is not None and ex > last):
            continue
        if item.get("symbol", symbol) == symbol:
            by_ex.setdefault(str(item["ex_date"]), set()).add(str(item.get("payable_date")))
    return [f"{ex}: {sorted(pays)}" for ex, pays in sorted(by_ex.items()) if len(pays) > 1]


def fill_missing_pay_dates(raw: dict[date, tuple[date | None, float]]) -> tuple[dict[date, Dividend], list[date]]:
    """Alpaca has no payment dates before 2020. Model assumption, stated explicitly: payment on
    ex-date + the **longest** observed ex-to-payment lag of the same symbol (cash later = conservative).
    Without any observed lag the payment date cannot be modelled and the download aborts."""
    lags = [(pay - ex).days for ex, (pay, _) in raw.items() if pay is not None]
    missing = sorted(ex for ex, (pay, _) in raw.items() if pay is None)
    if missing and not lags:
        raise CorporateActionError("no payment dates at all: cannot model dividend cash timing")
    lag = timedelta(days=max(lags)) if lags else timedelta(0)
    filled = {ex: (pay if pay is not None else ex + lag, amount) for ex, (pay, amount) in raw.items()}
    return filled, missing


def crosscheck_dividends(
    official: dict[date, Dividend],
    derived: dict[date, float],
    listings: dict[date, int] | None = None,
    precision: dict[date, float] | None = None,
) -> list[str]:
    """Problems between provider amounts and amounts derived from raw/adjusted prices.

    ``listings`` counts how often the provider lists an ex-date. Alpaca applies a duplicated entry twice
    in its adjusted series (QQQ 2022-09-19: 2 x 0.51856); a derived amount of exactly ``count`` times the
    official one is therefore explained and reported, not treated as a disagreement. With ``precision``
    (absolute, from ``derive_dividends_with_precision``) a difference within the rounding precision agrees.
    """
    problems = []
    listings = listings or {}
    for ex in sorted(set(official) | set(derived)):
        if ex not in official:
            problems.append(f"{ex}: only in adjusted prices ({derived[ex]})")
        elif ex not in derived:
            problems.append(f"{ex}: only in corporate actions ({official[ex][1]})")
        elif abs(derived[ex] / official[ex][1] - 1) > MAX_CROSSCHECK_DEVIATION and abs(
            derived[ex] - official[ex][1]
        ) > (precision or {}).get(ex, 0.0):
            count = listings.get(ex, 1)
            if count > 1 and abs(derived[ex] / (count * official[ex][1]) - 1) <= MAX_CROSSCHECK_DEVIATION:
                problems.append(f"{ex}: adjusted series applies a {count}x listed dividend {count} times (explained)")
            else:
                problems.append(f"{ex}: {official[ex][1]} vs derived {derived[ex]}")
    return problems


def validate_dividends(dividends: dict[date, Dividend]) -> None:
    for ex, (pay, amount) in dividends.items():
        if not math.isfinite(amount) or amount <= 0:
            raise CorporateActionError(f"dividend on {ex} must be a positive finite amount")
        if pay < ex:
            raise CorporateActionError(f"dividend on {ex} is paid before its ex-date")


def write_dividends(
    dividends: dict[date, Dividend], path: str | Path, source: str, assumed_pay: set[date] | None = None
) -> str:
    target = Path(path)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("ex_date", "pay_date", "amount", "source"))
        for ex in sorted(dividends):
            pay, amount = dividends[ex]
            label = source + ("; pay_date assumed (max observed lag)" if ex in (assumed_pay or set()) else "")
            writer.writerow((ex.isoformat(), pay.isoformat(), repr(amount), label))
    return hashlib.sha256(target.read_bytes()).hexdigest()


def ex_date_known_before(ex: date, cutoff: datetime) -> bool:
    """An ex-date belongs to the research period if its session opens before the cutoff (consistent with
    bars: a 00:00 UTC cutoff keeps the last US trading day, whose New York date is the day before)."""
    return datetime.combine(ex, US_REGULAR_SESSION.start, ZoneInfo(US_REGULAR_SESSION.timezone)) < cutoff


def read_dividends(path: str | Path, available_until: datetime | None = None) -> dict[date, Dividend]:
    """Read and validate. With a cutoff, a row is skipped on its ex-date alone, before anything else is
    parsed, so a withheld row can neither abort nor leak; error messages name the line, never its values."""
    dividends: dict[date, Dividend] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            try:
                ex = date.fromisoformat(row["ex_date"])
            except (KeyError, TypeError, ValueError):
                raise CorporateActionError(f"unreadable ex_date in line {line}") from None
            if available_until is not None and not ex_date_known_before(ex, available_until):
                continue
            try:
                pay = date.fromisoformat(row["pay_date"])
                amount = float(row["amount"])
            except (KeyError, TypeError, ValueError):
                raise CorporateActionError(f"unreadable dividend in line {line}") from None
            if ex in dividends:
                raise CorporateActionError(f"duplicate ex-date in line {line}")
            dividends[ex] = (pay, amount)
    validate_dividends(dividends)
    return dividends


def dividends_digest(dividends: dict[date, Dividend]) -> str:
    payload = json.dumps([[ex.isoformat(), p.isoformat(), a] for ex, (p, a) in sorted(dividends.items())])
    return hashlib.sha256(payload.encode()).hexdigest()


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
    available_until: datetime | None = None,
) -> dict[str, Any]:
    """Download and write the research files. With ``available_until`` (the holdout start) nothing at or
    after the cutoff is requested, read or written: research files physically contain no holdout rows."""
    from .data_requests import DataRequestSpec

    if available_until is not None:
        if available_until.tzinfo is None:
            raise ValueError("available_until must be timezone-aware")
        if end > available_until:
            raise ValueError(f"end {end.isoformat()} reaches into the holdout (starts {available_until.isoformat()})")
    paths = output_paths(output)
    series: dict[str, list[Bar]] = {}
    requests: dict[str, dict[str, Any]] = {}
    # Alpaca's ``end`` is inclusive and refers to the bar start: the last allowed bar starts one bar width
    # before the cutoff (and ends exactly at it). Rows beyond the request are dropped before persisting.
    provider_end = end if available_until is None else min(end, available_until - BAR_WIDTH)
    for adjustment in ("raw", "all"):
        request = DataRequestSpec(
            provider="alpaca",
            endpoint_type="stocks",
            symbols=(symbol,),
            timeframe="15Min",
            start=start,
            end=provider_end,
            feed=feed,
            purpose="research:T-018 multi-year 15-minute data",
            strategy_name="intraday",
            adjustment=adjustment,
        )
        bars, manifest, _ = downloader.download(request, progress)
        series[adjustment] = regular_session_bars(
            [b for b in bars if b.symbol == symbol and (available_until is None or b.end <= available_until)]
        )
        requests[adjustment] = {
            "request_id": manifest.get("request_id"),
            "downloaded_at": manifest.get("downloaded_at"),
            "bars_downloaded": len(bars),
        }
    raw_bars = series["raw"]
    first_day = US_REGULAR_SESSION.local_date(raw_bars[0].start) if raw_bars else start.date()
    last_day = US_REGULAR_SESSION.local_date(raw_bars[-1].start) if raw_bars else end.date()
    # raises before any file is written
    derived, precision = derive_dividends_with_precision(series["raw"], series["all"])
    actions_end = last_day + timedelta(days=120)
    if available_until is not None:  # never query corporate actions processed inside the holdout
        # the endpoint's end date is inclusive and in UTC calendar days: last full UTC day before the cutoff
        actions_end = min(actions_end, (available_until.astimezone(UTC) - timedelta(microseconds=1)).date())
    actions = downloader.corporate_actions(symbol, first_day - timedelta(days=120), actions_end)
    dividends = dividends_from_actions(actions, symbol, first_day, last_day, available_until)
    assumed = sorted(
        date.fromisoformat(str(i["ex_date"]))
        for i in actions.get("cash_dividends", [])
        if not i.get("payable_date") and first_day <= date.fromisoformat(str(i["ex_date"])) <= last_day
    )
    listings: dict[date, int] = {}
    for item in actions.get("cash_dividends", []):
        if item.get("symbol", symbol) == symbol:
            ex = date.fromisoformat(str(item["ex_date"]))
            listings[ex] = listings.get(ex, 0) + 1
    mismatches = crosscheck_dividends(dividends, derived, listings, precision)
    if any(" vs derived " in problem for problem in mismatches):
        raise CorporateActionError("dividend amounts disagree: " + "; ".join(mismatches))
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
            "dividends": {
                "path": str(paths["dividends"]),
                "sha256": write_dividends(dividends, paths["dividends"], "alpaca_corporate_actions", set(assumed)),
            },
        },
        "dividends": len(dividends),
        "dividend_crosscheck": mismatches,
        "pay_dates_assumed": [ex.isoformat() for ex in assumed],
        "duplicate_pay_dates_later_used": duplicate_pay_dates(actions, symbol, first_day, last_day),
        "available_until": available_until.isoformat() if available_until else None,
        "dividend_gaps": dividend_gaps(
            {ex: amount for ex, (_, amount) in dividends.items()},
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
