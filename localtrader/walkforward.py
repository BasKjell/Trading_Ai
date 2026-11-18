"""T-008 research windows. Callers supply a reviewed session calendar independently of price observations."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .domain import Bar, require_aware
from .intraday_data import resample

NY = ZoneInfo("America/New_York")
QUARTER = timedelta(minutes=15)


@dataclass(frozen=True, slots=True)
class Session:
    opens: datetime
    closes: datetime

    def __post_init__(self) -> None:
        require_aware(self.opens, "session open")
        require_aware(self.closes, "session close")
        if self.opens >= self.closes or self.day != self.closes.astimezone(NY).date():
            raise ValueError("expected a single regular exchange session")

    @property
    def day(self) -> date:
        return self.opens.astimezone(NY).date()


@dataclass(frozen=True, slots=True)
class RoleWindow:
    fold: str
    role: str
    start: date
    end: date
    embargo: tuple[Session, ...]
    scored: tuple[Session, ...]  # includes cash-only warm-up and entry blackout days
    purge: tuple[Session, ...]
    blackout: int
    min_quarters: int
    min_hours: int
    research_cutoff: datetime
    max_signal_sessions: int
    max_position_sessions: int


def research_windows(protocol: dict[str, Any], calendar: Sequence[Session]) -> tuple[RoleWindow, ...]:
    """Build the nine research roles from the protocol and a complete, chronologically ordered calendar."""
    days = [s.day for s in calendar]
    if days != sorted(set(days)):
        raise ValueError("calendar dates must be unique and ordered")
    rules = protocol["boundary"]
    embargo = rules["embargo_first_complete_scheduled_sessions"]
    purge = rules["purge_last_complete_scheduled_sessions"]
    blackout = rules["entry_blackout_before_scored_end_sessions"]
    max_exposure = max(
        rules["max_entry_to_flat_sessions_inclusive"], rules["max_entry_signal_to_flat_sessions_inclusive"]
    )
    min_quarters = rules["minimum_observed_completed_15m_bars_per_symbol"]
    min_hours = rules["minimum_observed_full_completed_hourly_bars_per_symbol"]
    if min(embargo, purge, blackout) <= max_exposure or min(min_quarters, min_hours) <= 0:
        raise ValueError("invalid safety distances or warm-up")
    cutoff = datetime.fromisoformat(protocol["central"]["start_inclusive"])
    require_aware(cutoff, "research cutoff")
    windows: list[RoleWindow] = []
    for fold in protocol["folds"]:
        for role in ("train", "validation", "test"):
            start, end = map(date.fromisoformat, fold[role])
            if start >= end or (windows and start < windows[-1].end):
                raise ValueError("overlapping or reversed research windows")
            subset = tuple(calendar[bisect_left(days, start) : bisect_left(days, end)])
            core = subset[embargo:-purge]
            if len(core) < max(blackout + 1, rules["minimum_scheduled_scored_sessions"][role]):
                raise ValueError(f"too few scheduled scored sessions for {fold['id']} {role}")
            if subset[-1].closes >= cutoff:
                raise ValueError("research calendar reaches the central window")
            windows.append(
                RoleWindow(
                    fold["id"],
                    role,
                    start,
                    end,
                    subset[:embargo],
                    core,
                    subset[-purge:],
                    blackout,
                    min_quarters,
                    min_hours,
                    cutoff,
                    rules["max_entry_signal_to_flat_sessions_inclusive"],
                    rules["max_entry_to_flat_sessions_inclusive"],
                )
            )
    return tuple(windows)


@dataclass(frozen=True, slots=True)
class FeatureContext:
    quarters: tuple[Bar, ...]
    hours: tuple[Bar, ...]
    ready: bool
    scored: bool
    may_enter: bool


class _AvailableBars:
    def __init__(self, bars: Iterable[Bar]) -> None:
        self.bars = sorted(bars, key=lambda b: b.end)
        self.ends = [b.end for b in self.bars]

    def latest(self, moment: datetime, count: int) -> tuple[Bar, ...]:
        # Search by event time, then check receipt independently: delayed bars can arrive out of order.
        result = []
        for i in range(bisect_right(self.ends, moment) - 1, -1, -1):
            bar = self.bars[i]
            if bar.received_at <= moment:
                result.append(bar)
                if len(result) == count:
                    break
        return tuple(reversed(result))


class RoleHistory:
    """Feature context for one symbol in one role, built from physically research-only input.

    Input may span research roles. Only this role's scheduled bars contribute. The caller verifies
    calendar coverage and the data-quality manifest before an economic run.
    """

    def __init__(self, window: RoleWindow, symbol: str, bars: Iterable[Bar]) -> None:
        self.window = window
        self.sessions = {s.day: s for s in (*window.embargo, *window.scored, *window.purge)}
        self.scored_index = {s.day: index for index, s in enumerate(window.scored)}
        self.entry_days = {s.day for s in window.scored[: -window.blackout]}
        quarters: dict[datetime, Bar] = {}
        buckets: dict[datetime, list[Bar]] = defaultdict(list)
        for bar in bars:
            if bar.symbol != symbol:
                continue
            local = bar.start.astimezone(NY)
            if not window.start <= local.date() < window.end:
                continue
            if (
                bar.start >= window.research_cutoff
                or bar.end > window.research_cutoff
                or bar.received_at >= window.research_cutoff
            ):
                continue
            session = self.sessions.get(local.date())
            if session is None or bar.start < session.opens or bar.end > session.closes:
                raise ValueError("regular-session bar outside supplied calendar")
            if (
                bar.timeframe != "15m"
                or bar.end - bar.start != QUARTER
                or local.minute % 15
                or local.second
                or local.microsecond
                or bar.start in quarters
            ):
                raise ValueError("expected unique aligned 15-minute bars")
            quarters[bar.start] = bar
            buckets[local.replace(minute=0)].append(bar)
        self.quarters = _AvailableBars(quarters.values())
        # Unique aligned quarters within a single session imply that any four in a clock hour cover it fully.
        full = (bar for bucket in buckets.values() if len(bucket) == 4 for bar in bucket)
        self.hours = _AvailableBars(resample(full, 60, NY.key))

    def at(self, moment: datetime) -> FeatureContext:
        require_aware(moment, "decision time")
        day = moment.astimezone(NY).date()
        session = self.sessions.get(day)
        if session is None or not session.opens <= moment <= session.closes or moment >= self.window.research_cutoff:
            raise ValueError("decision outside research role session")
        quarters = self.quarters.latest(moment, self.window.min_quarters)
        hours = self.hours.latest(moment, self.window.min_hours)
        ready = len(quarters) == self.window.min_quarters and len(hours) == self.window.min_hours
        return FeatureContext(quarters, hours, ready, day in self.scored_index, ready and day in self.entry_days)

    def validate_trade_span(self, signal: datetime, first_fill: datetime, flat: datetime | None) -> None:
        """Check an observed strategy trade, including its final partial exit, against the role boundaries."""
        if flat is None:
            raise ValueError("unclosed position in scored window")
        if not self.at(signal).may_enter or not self.at(first_fill).may_enter or not self.at(flat).scored:
            raise ValueError("trade outside entry or scoring window")
        if not signal <= first_fill <= flat:
            raise ValueError("nonchronological trade")
        finish = self.scored_index[flat.astimezone(NY).date()]
        for start, limit in (
            (signal, self.window.max_signal_sessions),
            (first_fill, self.window.max_position_sessions),
        ):
            if finish - self.scored_index[start.astimezone(NY).date()] + 1 > limit:
                raise ValueError("trade exceeds scheduled session limit")
