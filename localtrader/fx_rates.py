"""EUR/USD reference rates of the European Central Bank as a causal time series (data part of T-011).

Source: ECB Data Portal, series ``EXR.D.USD.EUR.SP00.A`` (US dollars per euro, daily reference rate).
The rates are set at about 14:10 CET and published at around 16:00 CET on TARGET business days, so a rate
is treated as **available from 16:00 Europe/Berlin** on its date (conservative). Weekends and TARGET
holidays have no rate.

``FxSeries.rate_at(moment)`` returns the latest rate published at or before ``moment`` and refuses a rate
older than ``max_age`` (fail closed). The series can be cut at a holdout boundary by availability, like
the price data. Converting fills, valuation, dividends and costs into EUR is not part of this module
(T-011, accounting).

The ECB reference rate is an indicative mid rate, not a tradable quote: actual conversion costs (spread
and fees of the broker) must be modelled separately.
"""

from __future__ import annotations

import bisect
import csv
import hashlib
import io
import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ECB_SERIES = "EXR/D.USD.EUR.SP00.A"
ECB_URL = "https://data-api.ecb.europa.eu/service/data/" + ECB_SERIES
PUBLICATION_TIME = time(16, 0)
PUBLICATION_ZONE = ZoneInfo("Europe/Berlin")
PLAUSIBLE_RANGE = (0.5, 2.5)  # USD per EUR; outside this the file is rejected
JUMP_WARNING = 0.04  # day-to-day change above 4 % is reported, not removed
DEFAULT_MAX_AGE = timedelta(days=5)  # covers weekends plus Easter (Good Friday and Easter Monday)


class FxDataError(ValueError):
    pass


class StaleRate(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class FxRate:
    day: date
    usd_per_eur: float
    available_at: datetime


def available_at(day: date) -> datetime:
    return datetime.combine(day, PUBLICATION_TIME, PUBLICATION_ZONE).astimezone(UTC)


def parse_ecb_csv(text: str) -> tuple[list[FxRate], dict[str, Any]]:
    """Validated rates and a quality report from the ECB ``csvdata`` format."""
    rows = list(csv.DictReader(io.StringIO(text)))
    report: dict[str, Any] = {"rows_read": len(rows), "rejected": {}, "jumps": []}

    def reject(reason: str) -> None:
        report["rejected"][reason] = report["rejected"].get(reason, 0) + 1

    rates: dict[date, float] = {}
    for row in rows:
        if row.get("KEY") != "EXR.D.USD.EUR.SP00.A":
            reject("other_series")
            continue
        try:
            day = date.fromisoformat(row["TIME_PERIOD"])
            value = float(row["OBS_VALUE"])
        except (KeyError, ValueError):
            reject("unparseable")
            continue
        status = (row.get("OBS_STATUS") or "").strip()
        if not status:
            reject("status_missing")  # only explicitly final values (A) are used
            continue
        if status != "A":
            reject("not_final")
            continue
        if not math.isfinite(value) or not PLAUSIBLE_RANGE[0] <= value <= PLAUSIBLE_RANGE[1]:
            reject("implausible_value")
            continue
        if day in rates:
            if rates[day] != value:
                raise FxDataError(f"conflicting rates for {day}")
            reject("duplicate")
            continue
        rates[day] = value
    series = [FxRate(day, value, available_at(day)) for day, value in sorted(rates.items())]
    for previous, current in zip(series, series[1:], strict=False):
        change = current.usd_per_eur / previous.usd_per_eur - 1
        if abs(change) > JUMP_WARNING:
            report["jumps"].append(f"{current.day} {change:+.2%}")
        if (current.day - previous.day).days > 5:
            report.setdefault("gaps", []).append(f"{previous.day}..{current.day}")
    report["rates_accepted"] = len(series)
    report["first"] = series[0].day.isoformat() if series else None
    report["last"] = series[-1].day.isoformat() if series else None
    return series, report


def fetch_ecb_csv(start: date, end: date, opener: Callable[[Request], Any] = urlopen) -> str:
    query = urlencode({"format": "csvdata", "startPeriod": start.isoformat(), "endPeriod": end.isoformat()})
    request = Request(f"{ECB_URL}?{query}", headers={"Accept": "text/csv", "User-Agent": "local-paper-trader/0.1"})
    with opener(request) as response:
        text: str = response.read().decode("utf-8")
    return text


def write_fx_file(series: list[FxRate], path: str | Path, report: dict[str, Any], source_url: str) -> dict[str, Any]:
    """CSV ``date,usd_per_eur,available_at`` plus a manifest next to it; returns the manifest."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(("date", "usd_per_eur", "available_at"))
        for rate in series:
            writer.writerow((rate.day.isoformat(), repr(rate.usd_per_eur), rate.available_at.isoformat()))
    manifest = {
        "series": ECB_SERIES,
        "source": source_url,
        "publication_rule": "available from 16:00 Europe/Berlin on the rate date (conservative)",
        "file": str(target),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        "quality": report,
    }
    Path(str(target) + ".manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def read_fx_file(path: str | Path, available_until: datetime | None = None) -> FxSeries:
    """Load the stored series; with a cutoff only rates published at or before it are kept."""
    if available_until is not None and available_until.tzinfo is None:
        raise ValueError("available_until must be timezone-aware")
    rates: list[FxRate] = []
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            # the window is decided from the date alone, before any value is parsed, so a defect in a
            # withheld (holdout) row can neither abort nor influence the research import
            try:
                day = date.fromisoformat(row["date"])
            except (KeyError, TypeError, ValueError):
                raise FxDataError(f"unreadable date in line {line}") from None  # never echo row values
            if available_until is not None and available_at(day) > available_until:
                continue
            try:
                rate = FxRate(day, float(row["usd_per_eur"]), datetime.fromisoformat(row["available_at"]))
            except (KeyError, TypeError, ValueError):
                raise FxDataError(f"{day}: unreadable rate (line {line})") from None
            if rate.available_at != available_at(day):
                raise FxDataError(f"{day}: availability differs from the publication rule")
            rates.append(rate)
    return FxSeries(rates)


class FxSeries:
    def __init__(self, rates: list[FxRate], max_age: timedelta = DEFAULT_MAX_AGE) -> None:
        ordered = sorted(rates, key=lambda r: r.available_at)
        if any(not math.isfinite(r.usd_per_eur) or r.usd_per_eur <= 0 for r in ordered):
            raise FxDataError("rates must be positive and finite")
        if len({r.day for r in ordered}) != len(ordered):
            raise FxDataError("duplicate rate dates")
        self.rates = ordered
        self.max_age = max_age
        self._times = [r.available_at for r in ordered]

    def rate_at(self, moment: datetime) -> FxRate:
        """Latest rate already published at ``moment``; raises StaleRate if none or too old."""
        if moment.tzinfo is None:
            raise ValueError("moment must be timezone-aware")
        index = bisect.bisect_right(self._times, moment) - 1
        if index < 0:
            raise StaleRate(f"no EUR/USD rate published before {moment.isoformat()}")
        rate = self.rates[index]
        if moment - rate.available_at > self.max_age:
            raise StaleRate(f"latest EUR/USD rate from {rate.day} is older than {self.max_age}")
        return rate

    def usd_to_eur(self, amount_usd: float, moment: datetime) -> float:
        return amount_usd / self.rate_at(moment).usd_per_eur

    def digest(self) -> str:
        payload = json.dumps([[r.day.isoformat(), r.usd_per_eur] for r in self.rates])
        return hashlib.sha256(payload.encode()).hexdigest()
