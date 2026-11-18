"""Versioned cost schedule with sources, assumptions and measurements kept apart (T-008 prerequisite).

The schedule (``config/cost_schedule.json``) lists cost components per instrument, currency, side and
validity period. Every entry says what kind of evidence it is:

* ``source`` – an official, citable rate (regulator or broker document); ``reference`` is a URL.
* ``measurement`` – measured from data in this project (e.g. quoted spreads); ``reference`` names the
  command and data version, ``sample`` the size.
* ``assumption`` – a modelling choice without a measurement or document; ``reference`` gives the reason.

``validate`` rejects overlapping periods, missing evidence, unknown units and a schedule without the
components a run needs. ``digest`` gives a stable hash for the freeze receipt. The schedule does not
change the engine's cost model yet; wiring it in is part of T-008/T-011.

Spread measurement: quotes are sampled at bar opens (where the engine fills) from Alpaca's historical
SIP quotes, only inside the research period (the holdout is never queried).
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

KINDS = ("source", "measurement", "assumption")
NEW_YORK = ZoneInfo("America/New_York")
UNITS = ("bps_of_notional", "usd_per_share", "usd_per_trade_max", "bps_half_spread", "bps_per_conversion")
SIDES = ("buy", "sell", "both", "conversion")
# Mandatory coverage per component and trade side: every pair needs a valid entry on every covered day
# (a documented zero is a valid entry). ``both`` covers buy and sell. SEC fee and FINRA TAF apply to sales only.
REQUIRED_COVERAGE: dict[str, tuple[str, ...]] = {
    "commission": ("buy", "sell"),
    "regulatory_sec": ("sell",),
    "regulatory_taf": ("sell",),
    "regulatory_cat": ("buy", "sell"),
    "spread": ("buy", "sell"),
    "slippage": ("buy", "sell"),
}
REQUIRED_COMPONENTS = tuple(REQUIRED_COVERAGE)


class ScheduleError(ValueError):
    pass


def load_schedule(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ScheduleError("schedule must be a JSON object")
    validate(data)
    return data


def _period(entry: dict[str, Any]) -> tuple[date, date]:
    start = date.fromisoformat(str(entry["valid_from"]))
    end = date.fromisoformat(str(entry.get("valid_to") or "9999-12-31"))
    if end <= start:
        raise ScheduleError(f"{entry.get('id')}: empty validity period")
    return start, end


def validate(schedule: dict[str, Any]) -> None:
    problems: list[str] = []
    entries = schedule.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ScheduleError("schedule needs a non-empty list 'entries'")
    seen_ids: set[str] = set()
    periods: dict[tuple[str, str, str, str], list[tuple[date, date, str]]] = {}
    for entry in entries:
        entry_id = str(entry.get("id"))
        if entry_id in seen_ids:
            problems.append(f"duplicate id {entry_id}")
        seen_ids.add(entry_id)
        if entry.get("kind") not in KINDS:
            problems.append(f"{entry_id}: kind must be one of {KINDS}")
        if entry.get("unit") not in UNITS:
            problems.append(f"{entry_id}: unknown unit {entry.get('unit')!r}")
        if entry.get("side") not in SIDES:
            problems.append(f"{entry_id}: unknown side {entry.get('side')!r}")
        value = entry.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
            problems.append(f"{entry_id}: value must be a finite non-negative number")
        if not str(entry.get("reference") or "").strip():
            problems.append(f"{entry_id}: every entry needs a reference (URL, command or reason)")
        if entry.get("kind") == "measurement" and not isinstance(entry.get("sample"), int):
            problems.append(f"{entry_id}: a measurement needs its sample size")
        try:
            start, end = _period(entry)
        except (KeyError, ValueError) as error:
            problems.append(f"{entry_id}: invalid period ({error})")
            continue
        key = (
            str(entry.get("instrument")),
            str(entry.get("component")),
            str(entry.get("side")),
            str(entry.get("unit")),
        )
        for other_start, other_end, other_id in periods.get(key, []):
            if start < other_end and other_start < end:
                problems.append(f"{entry_id} overlaps {other_id}")
        periods.setdefault(key, []).append((start, end, entry_id))
    coverage = schedule.get("coverage") or {}
    try:
        cover_from = date.fromisoformat(str(coverage["from"]))
        cover_to = date.fromisoformat(str(coverage["to"]))
    except (KeyError, ValueError):
        problems.append("schedule needs coverage.from/coverage.to (dates the schedule vouches for)")
    else:
        # every required component must be valid on every day of the coverage, not just somewhere
        boundaries = {cover_from}
        for entry in entries:
            for field_name in ("valid_from", "valid_to"):
                try:
                    day = date.fromisoformat(str(entry.get(field_name)))
                except ValueError:
                    continue
                if cover_from <= day < cover_to:
                    boundaries.add(day)
        for instrument in schedule.get("instruments", []):
            for day in sorted(boundaries):
                for component in missing_components(schedule, instrument, day):
                    problems.append(f"{instrument}: {component} missing on {day}")
    if problems:
        raise ScheduleError("; ".join(problems))


def missing_components(schedule: dict[str, Any], instrument: str, day: date) -> list[str]:
    """Required ``component/side`` pairs without a valid entry on ``day`` (a run must not use a day with gaps)."""
    present: set[tuple[str, str]] = set()
    for entry in schedule.get("entries", []):
        try:
            start, end = _period(entry)
        except (KeyError, ValueError, ScheduleError):
            continue
        if entry.get("instrument") in (instrument, "*") and start <= day < end:
            side = str(entry.get("side"))
            for covered in ("buy", "sell") if side == "both" else (side,):
                present.add((str(entry.get("component")), covered))
    return [
        f"{component}/{side}"
        for component, sides in REQUIRED_COVERAGE.items()
        for side in sides
        if (component, side) not in present
    ]


def entries_for(schedule: dict[str, Any], instrument: str, day: date) -> list[dict[str, Any]]:
    """Entries valid on ``day`` for the instrument (instrument-specific or ``*``)."""
    output = []
    for entry in schedule["entries"]:
        start, end = _period(entry)
        if entry.get("instrument") in (instrument, "*") and start <= day < end:
            output.append(entry)
    return output


def digest(schedule: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(schedule, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ------------------------------------------------------------------------------------- spread sampling


def sample_bar_opens(
    bar_starts: list[datetime],
    count: int,
    before: datetime,
    seed: int = 20261106,
    window: timedelta = timedelta(seconds=2),
) -> list[datetime]:
    """Deterministic random sample of bar opens whose whole quote window ends strictly before ``before``
    (the request end is never the cutoff itself, so the holdout is never queried)."""
    eligible = sorted(start for start in bar_starts if start + window < before)
    if count >= len(eligible):
        return eligible
    return sorted(random.Random(seed).sample(eligible, count))


def quoted_half_spread_bps(quote: dict[str, Any]) -> float | None:
    """Half the quoted spread relative to the mid in bps; None for unusable (crossed, locked, empty)."""
    bid, ask = float(quote.get("bp") or 0), float(quote.get("ap") or 0)
    if bid <= 0 or ask <= bid or not quote.get("bs") or not quote.get("as"):
        return None
    mid = (bid + ask) / 2
    return (ask - bid) / 2 / mid * 10_000


def measure_spreads(
    request_page: Callable[[str], tuple[dict[str, Any], dict[str, str]]],
    symbol: str,
    moments: list[datetime],
    window: timedelta = timedelta(seconds=2),
) -> dict[str, Any]:
    """First usable SIP quote within ``window`` after each moment; summary statistics in bps."""
    values: list[float] = []
    by_year: dict[int, list[float]] = {}
    at_open: list[float] = []
    missing = 0
    for moment in moments:
        params = {
            "symbols": symbol,
            "start": moment.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "end": (moment + window).astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "feed": "sip",
            "limit": 20,
        }
        payload, _ = request_page(f"https://data.alpaca.markets/v2/stocks/quotes?{urlencode(params)}")
        quotes = (payload.get("quotes") or {}).get(symbol) or []
        value = next((v for v in (quoted_half_spread_bps(q) for q in quotes) if v is not None), None)
        if value is None:
            missing += 1
            continue
        values.append(value)
        by_year.setdefault(moment.year, []).append(value)
        if moment.astimezone(NEW_YORK).strftime("%H:%M") == "09:30":  # session open, DST-aware
            at_open.append(value)
    return {
        "symbol": symbol,
        "samples": len(values),
        "missing": missing,
        "half_spread_bps": _stats(values),
        "half_spread_bps_session_open": _stats(at_open),
        "half_spread_bps_by_year": {str(year): _stats(v) for year, v in sorted(by_year.items())},
    }


def _stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"n": 0, "median": None, "p75": None, "p90": None, "p99": None, "mean": None}
    ordered = sorted(values)

    def quantile(q: float) -> float:
        return ordered[min(len(ordered) - 1, int(q * len(ordered)))]

    return {
        "n": len(values),
        "median": round(statistics.median(values), 4),
        "p75": round(quantile(0.75), 4),
        "p90": round(quantile(0.90), 4),
        "p99": round(quantile(0.99), 4),
        "mean": round(statistics.fmean(values), 4),
    }
