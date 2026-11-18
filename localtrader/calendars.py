"""Exchange calendars needed to know the regular session of a given day (no third-party dependency).

NYSE early closes (13:00 New York) from the NYSE holiday calendar. For 2016-2025 every date was checked
against the downloaded SIP data: on exactly these days the volume after 13:00 collapses to after-hours
levels, and on no other day.

Full holidays follow the NYSE rules (Rule 7.2 observance: Saturday holiday → Friday closed, except New
Year's Day, which is then not observed; Sunday → Monday) plus unscheduled closures (national days of
mourning). They are needed to tell a holiday from a data gap when inventorying expected 15-minute slots
(T-008 protocol). Checked against the SIP research data 2016-01-04..2025-10-31: the computed trading days
equal the days with bars exactly (2,473 for SPY and QQQ). The slot inventory then shows the real gaps: SPY
2019-08-12 15:45 New York (1 slot), QQQ 2016-02-22 10:30 and 10:45 (2 slots).
Days after ``NYSE_COVERED_UNTIL`` are treated as unknown and are not part of the session (fail closed).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

NYSE_EARLY_CLOSE = time(13, 0)
NYSE_COVERED_UNTIL = date(2026, 12, 31)
NYSE_EARLY_CLOSES = frozenset(
    date.fromisoformat(day)
    for day in (
        "2016-11-25",
        "2017-07-03",
        "2017-11-24",
        "2018-07-03",
        "2018-11-23",
        "2018-12-24",
        "2019-07-03",
        "2019-11-29",
        "2019-12-24",
        "2020-11-27",
        "2020-12-24",
        "2021-11-26",
        "2022-11-25",
        "2023-07-03",
        "2023-11-24",
        "2024-07-03",
        "2024-11-29",
        "2024-12-24",
        "2025-07-03",
        "2025-11-28",
        "2025-12-24",
        "2026-11-27",
        "2026-12-24",
    )
)

NYSE_OPEN = time(9, 30)
NYSE_CLOSE = time(16, 0)
NYSE_ZONE = ZoneInfo("America/New_York")
NYSE_COVERED_FROM = date(2016, 1, 1)
# Unscheduled closures (national days of mourning).
NYSE_SPECIAL_CLOSURES = frozenset({date(2018, 12, 5), date(2025, 1, 9)})


class CalendarError(ValueError):
    pass


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous Gregorian algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month = (h + m - 7 * n + 114) // 31
    day = (h + m - 7 * n + 114) % 31 + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = (date(year, month + 1, 1) if month < 12 else date(year + 1, 1, 1)) - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(day: date) -> date | None:
    if day.weekday() == 5:  # Saturday → Friday
        return day - timedelta(days=1)
    if day.weekday() == 6:  # Sunday → Monday
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> frozenset[date]:
    """Full-day NYSE closures of a calendar year."""
    days: set[date | None] = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(date(year, 7, 4)),  # Independence Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(date(year, 12, 25)),  # Christmas
    }
    new_year = date(year, 1, 1)
    if new_year.weekday() != 5:  # Saturday: not observed on the preceding Friday (Rule 7.2)
        days.add(_observed(new_year))
    if year >= 2022:
        days.add(_observed(date(year, 6, 19)))  # Juneteenth
    days |= {d for d in NYSE_SPECIAL_CLOSURES if d.year == year}
    return frozenset(d for d in days if d is not None)


def _covered(day: date) -> None:
    if not NYSE_COVERED_FROM <= day <= NYSE_COVERED_UNTIL:
        raise CalendarError(f"{day} is outside the verified NYSE calendar ({NYSE_COVERED_FROM}..{NYSE_COVERED_UNTIL})")


def is_trading_day(day: date) -> bool:
    _covered(day)
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def trading_days(start: date, end: date) -> list[date]:
    """Trading days in [start, end)."""
    output = []
    day = start
    while day < end:
        if is_trading_day(day):
            output.append(day)
        day += timedelta(days=1)
    return output


def expected_bar_starts(day: date, minutes: int = 15) -> list[datetime]:
    """UTC start times of all regular-session bars of a trading day (early closes respected)."""
    if type(minutes) is not int or minutes <= 0:
        raise CalendarError("interval width must be a positive whole number of minutes")
    if not is_trading_day(day):
        return []
    close = NYSE_EARLY_CLOSE if day in NYSE_EARLY_CLOSES else NYSE_CLOSE
    moment = datetime.combine(day, NYSE_OPEN, NYSE_ZONE)
    end = datetime.combine(day, close, NYSE_ZONE)
    if (end - moment) % timedelta(minutes=minutes):
        # e.g. 60 minutes on a 6.5-hour session: the last slot would end after the close
        raise CalendarError(f"{minutes}-minute slots do not tile the session of {day}")
    output = []
    while moment < end:
        output.append(moment.astimezone(UTC))
        moment += timedelta(minutes=minutes)
    return output


def inventory(bar_starts: Iterable[datetime], start: date, end: date, minutes: int = 15) -> dict[str, object]:
    """Compare actual bars with every expected regular-session slot in [start, end).

    Returns counts plus the missing and unexpected slots; a holiday never counts as missing.
    """
    actual = {moment.astimezone(UTC) for moment in bar_starts if start <= moment.astimezone(NYSE_ZONE).date() < end}
    expected: set[datetime] = set()
    for day in trading_days(start, end):
        expected.update(expected_bar_starts(day, minutes))
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    return {
        "trading_days": len(trading_days(start, end)),
        "expected_slots": len(expected),
        "present_slots": len(expected & actual),
        "missing": [moment.isoformat() for moment in missing],
        "unexpected": [moment.isoformat() for moment in unexpected],
    }
