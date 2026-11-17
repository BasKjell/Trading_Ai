"""Intraday CSV import, quality checks and resampling.

Import format (documented in docs/DATA_FORMAT.md)
-------------------------------------------------
Either MetaTrader style ``Date,Time,Open,High,Low,Close,Volume`` (``2020.10.06``, ``14:00``) in a
named source time zone, or ``timestamp,open,high,low,close,volume`` with ISO-8601 timestamps.
Timestamps denote the **start** of a bar. Internally every time is UTC.

Quality policy
--------------
* Rows with invalid OHLC, non-positive prices, negative volume, duplicates or timestamps not aligned
  to the bar grid are **rejected** (never repaired).
* Missing bars are never filled. Gaps inside a trading session are reported and the first bar after
  such a gap is marked so the engine can block new entries on it.
* Price jumps above ``jump_threshold`` are reported (not removed): they may be genuine.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .domain import Bar, Market


@dataclass(frozen=True, slots=True)
class CsvImportSpec:
    path: str
    symbol: str
    market: Market
    venue: str
    source: str
    source_timezone: str
    timeframe_minutes: int = 15
    # Minimum delay between bar end and availability to the strategy.
    availability_delay_seconds: int = 0
    jump_threshold: float = 0.03
    # Holdout: only bars completely available (received_at) at or before this moment are imported.
    # Later rows are dropped before any quality check, so no holdout price reaches the manifest.
    available_until: datetime | None = None


@dataclass(frozen=True, slots=True)
class SessionSpec:
    """Regular trading session in exchange-local time; signals and entries only inside it."""

    timezone: str
    start: time
    end: time
    # Optional exchange calendar: days that close early, and the last day the calendar is known for.
    early_closes: frozenset[date] = frozenset()
    early_close: time | None = None
    calendar_until: date | None = None

    def close_on(self, day: date) -> time:
        if self.early_close is not None and day in self.early_closes:
            return self.early_close
        return self.end

    def contains(self, moment: datetime) -> bool:
        local = moment.astimezone(ZoneInfo(self.timezone))
        if self.calendar_until is not None and local.date() > self.calendar_until:
            return False  # unknown calendar: fail closed
        return local.weekday() < 5 and self.start <= local.time() < self.close_on(local.date())

    def local_date(self, moment: datetime) -> date:
        return moment.astimezone(ZoneInfo(self.timezone)).date()


@dataclass(slots=True)
class QualityReport:
    rows_read: int = 0
    bars_accepted: int = 0
    rejected: dict[str, int] = field(default_factory=dict)
    intraday_gaps: int = 0
    missing_bars_in_gaps: int = 0
    jumps: list[str] = field(default_factory=list)
    first_bar: str | None = None
    last_bar: str | None = None
    # Rows withheld for the holdout. Only the count is recorded, never prices or timestamps.
    holdout_rows_withheld: int = 0

    def reject(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1


@dataclass(frozen=True, slots=True)
class DatasetManifest:
    symbol: str
    market: str
    venue: str
    source: str
    source_timezone: str
    timeframe_minutes: int
    data_version: str
    file_sha256: str
    synthetic: bool
    quality: dict[str, object]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


@dataclass(frozen=True, slots=True)
class ImportedDataset:
    bars: list[Bar]
    manifest: DatasetManifest
    # Start times (UTC) of bars that directly follow an unexpected gap inside a session.
    after_gap: frozenset[datetime]
    # Cash dividend per share by ex-date (exchange-local date), booked on held positions.
    dividends: dict[date, float] = field(default_factory=dict)


def _parse_timestamp(row: dict[str, str], zone: ZoneInfo) -> datetime:
    if "timestamp" in row:
        parsed = datetime.fromisoformat(row["timestamp"])
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=zone)
        return parsed.astimezone(UTC)
    local = datetime.strptime(f"{row['date']} {row['time']}", "%Y.%m.%d %H:%M").replace(tzinfo=zone)
    return local.astimezone(UTC)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def import_csv(spec: CsvImportSpec, session: SessionSpec | None = None) -> ImportedDataset:
    if spec.available_until is not None and spec.available_until.tzinfo is None:
        raise ValueError("available_until must be timezone-aware")
    path = Path(spec.path)
    zone = ZoneInfo(spec.source_timezone)
    step = timedelta(minutes=spec.timeframe_minutes)
    report = QualityReport()
    seen: set[datetime] = set()
    candidates: list[Bar] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            report.rows_read += 1
            row = {key.strip().lower(): (value or "").strip() for key, value in raw.items() if key}
            try:
                start = _parse_timestamp(row, zone)
            except (KeyError, ValueError):
                report.reject("unparseable")
                continue
            if spec.available_until is not None and (
                start + step + timedelta(seconds=spec.availability_delay_seconds) > spec.available_until
            ):
                report.holdout_rows_withheld += 1
                continue
            try:
                o, h, low, c = (float(row[k]) for k in ("open", "high", "low", "close"))
                volume = float(row.get("volume") or 0.0)
            except (KeyError, ValueError):
                report.reject("unparseable")
                continue
            # NaN/inf pass every comparison below unnoticed, so reject them explicitly first.
            if not all(math.isfinite(value) for value in (o, h, low, c, volume)):
                report.reject("non_finite_value")
                continue
            if (start.timestamp() % step.total_seconds()) != 0:
                report.reject("misaligned_timestamp")
                continue
            if start in seen:
                report.reject("duplicate")
                continue
            if min(o, h, low, c) <= 0:
                report.reject("non_positive_price")
                continue
            if h < max(o, c, low) or low > min(o, c, h):
                report.reject("invalid_ohlc")
                continue
            if volume < 0:
                report.reject("negative_volume")
                continue
            seen.add(start)
            end = start + step
            candidates.append(
                Bar(
                    symbol=spec.symbol,
                    market=spec.market,
                    timeframe=f"{spec.timeframe_minutes}m",
                    start=start,
                    end=end,
                    open=o,
                    high=h,
                    low=low,
                    close=c,
                    volume=volume,
                    source=spec.source,
                    received_at=end + timedelta(seconds=spec.availability_delay_seconds),
                )
            )
    candidates.sort(key=lambda bar: bar.start)
    after_gap: set[datetime] = set()
    for previous, current in zip(candidates, candidates[1:], strict=False):
        if current.close <= 0:
            continue
        jump = abs(current.open / previous.close - 1)
        if jump > spec.jump_threshold:
            report.jumps.append(f"{current.start.isoformat()} open/prev_close {jump:.2%}")
        gap = current.start - previous.end
        if gap <= timedelta(0):
            continue
        same_session = session is None or (
            session.local_date(previous.start) == session.local_date(current.start)
            and session.contains(previous.start)
            and session.contains(current.start)
        )
        if same_session:
            report.intraday_gaps += 1
            report.missing_bars_in_gaps += int(gap / step)
            after_gap.add(current.start)
    report.bars_accepted = len(candidates)
    if candidates:
        report.first_bar = candidates[0].start.isoformat()
        report.last_bar = candidates[-1].start.isoformat()
    payload = json.dumps(
        [[b.start.isoformat(), b.open, b.high, b.low, b.close, b.volume] for b in candidates], separators=(",", ":")
    )
    manifest = DatasetManifest(
        symbol=spec.symbol,
        market=spec.market.value,
        venue=spec.venue,
        source=spec.source,
        source_timezone=spec.source_timezone,
        timeframe_minutes=spec.timeframe_minutes,
        data_version=hashlib.sha256(payload.encode()).hexdigest()[:16],
        file_sha256=_file_sha256(path),
        synthetic=False,
        quality=asdict(report),
    )
    return ImportedDataset(candidates, manifest, frozenset(after_gap))


def resample(bars: Iterable[Bar], minutes: int, timezone: str = "UTC") -> list[Bar]:
    """Aggregate bars into ``minutes`` buckets aligned in ``timezone`` (daily buckets: local calendar day).

    A resampled bar's ``end`` is the end of its **last constituent bar**, so it only becomes available
    once all its data is known. Buckets are never padded.
    """
    zone = ZoneInfo(timezone)
    buckets: dict[tuple[str, datetime], list[Bar]] = {}
    for bar in bars:
        local = bar.start.astimezone(zone)
        if minutes >= 1440:
            key_local = datetime(local.year, local.month, local.day, tzinfo=zone)
        else:
            midnight = datetime(local.year, local.month, local.day, tzinfo=zone)
            offset = int((local - midnight).total_seconds() // 60) // minutes * minutes
            key_local = midnight + timedelta(minutes=offset)
        buckets.setdefault((bar.symbol, key_local), []).append(bar)
    output: list[Bar] = []
    for (symbol, key_local), items in sorted(buckets.items(), key=lambda item: (item[0][0], item[0][1])):
        items.sort(key=lambda bar: bar.start)
        output.append(
            Bar(
                symbol=symbol,
                market=items[0].market,
                timeframe=f"{minutes}m" if minutes < 1440 else "1d",
                start=key_local.astimezone(UTC),
                end=items[-1].end,
                open=items[0].open,
                high=max(bar.high for bar in items),
                low=min(bar.low for bar in items),
                close=items[-1].close,
                volume=sum(bar.volume for bar in items),
                source=items[0].source,
                received_at=max(bar.received_at for bar in items),
            )
        )
    return output


def completed_before(bars: list[Bar], moment: datetime) -> list[Bar]:
    """Bars whose data was fully available at ``moment`` (look-ahead guard)."""
    return [bar for bar in bars if bar.received_at <= moment]


def synthetic_intraday(
    symbol: str = "SYN",
    days: int = 60,
    seed: int = 7,
    start_price: float = 100.0,
    session: SessionSpec | None = None,
) -> ImportedDataset:
    """Deterministic, explicitly synthetic 15-minute bars for tests and the offline demo."""
    import random

    session = session or SessionSpec("Europe/Berlin", time(9, 0), time(17, 30))
    zone = ZoneInfo(session.timezone)
    rng = random.Random(seed)
    price = start_price
    bars: list[Bar] = []
    day = date(2024, 1, 1)
    produced = 0
    while produced < days:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        produced += 1
        drift = rng.gauss(0, 0.0004)
        cursor = datetime.combine(day, session.start, tzinfo=zone)
        close_time = datetime.combine(day, session.end, tzinfo=zone)
        while cursor < close_time:
            start = cursor.astimezone(UTC)
            open_price = price
            close = max(0.01, open_price * (1 + rng.gauss(drift, 0.002)))
            high = max(open_price, close) * (1 + abs(rng.gauss(0, 0.0008)))
            low = min(open_price, close) * (1 - abs(rng.gauss(0, 0.0008)))
            end = start + timedelta(minutes=15)
            bars.append(
                Bar(
                    symbol,
                    Market.INDEX,
                    "15m",
                    start,
                    end,
                    open_price,
                    high,
                    low,
                    close,
                    float(rng.randint(500, 5000)),
                    "synthetic_intraday",
                    end,
                )
            )
            price = close
            cursor += timedelta(minutes=15)
    payload = json.dumps([[b.start.isoformat(), b.close] for b in bars], separators=(",", ":"))
    version = hashlib.sha256(payload.encode()).hexdigest()[:16]
    manifest = DatasetManifest(
        symbol,
        Market.INDEX.value,
        "synthetic",
        "synthetic_intraday",
        session.timezone,
        15,
        version,
        version,
        True,
        asdict(QualityReport(rows_read=len(bars), bars_accepted=len(bars))),
    )
    return ImportedDataset(bars, manifest, frozenset())
