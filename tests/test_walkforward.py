"""Calendar and market observations are synthetic; no research or central data files are opened."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest

from localtrader.domain import Bar, Market
from localtrader.walkforward import NY, QUARTER, RoleHistory, Session, research_windows


def session(day: date, close: time = time(16)) -> Session:
    return Session(
        datetime.combine(day, time(9, 30), NY).astimezone(UTC), datetime.combine(day, close, NY).astimezone(UTC)
    )


def bars(s: Session, symbol: str = "SPY") -> list[Bar]:
    rows = []
    t = s.opens
    while t < s.closes:
        p = 100 + len(rows)
        rows.append(
            Bar(symbol, Market.ETF, "15m", t, t + QUARTER, p, p + 2, p - 1, p + 1, 10, "synthetic", t + QUARTER)
        )
        t += QUARTER
    return rows


@pytest.fixture(scope="module")
def protocol():
    return json.loads((Path(__file__).resolve().parents[1] / "docs/research/T-008-protocol.json").read_text())


@pytest.fixture(scope="module")
def calendar():
    # Artificial schedule with one deliberate holiday and one early close. No provider dependency.
    start, end = date(2016, 2, 1), date(2025, 11, 1)
    days = (start + timedelta(days=i) for i in range((end - start).days))
    return tuple(
        session(d, time(13) if d == date(2016, 2, 12) else time(16))
        for d in days
        if d.weekday() < 5 and d != date(2016, 2, 5)
    )


@pytest.fixture(scope="module")
def windows(protocol, calendar):
    return research_windows(protocol, calendar)


def test_all_nine_roles_partition_the_supplied_sessions_exactly_once(windows, calendar, protocol):
    assert [(w.fold, w.role) for w in windows] == [
        (f, r) for f in ("F1", "F2", "F3") for r in ("train", "validation", "test")
    ]
    assert [s for w in windows for s in (*w.embargo, *w.scored, *w.purge)] == list(calendar)
    for w in windows:
        assert len(w.purge) == len(w.embargo) == w.blackout == 6
        assert len(w.scored) >= protocol["boundary"]["minimum_scheduled_scored_sessions"][w.role]
        assert all(w.start <= s.day < w.end for s in (*w.embargo, *w.scored, *w.purge))
    assert windows[0].embargo[-1].day == date(2016, 2, 9)
    assert windows[-1].purge[-1].day == date(2025, 10, 31)
    history = RoleHistory(windows[0], "SPY", [])
    assert history.at(windows[0].scored[0].opens).scored
    assert not history.at(windows[0].scored[0].opens).may_enter


def test_calendar_errors_and_short_or_overlapping_roles_fail(protocol, calendar):
    for invalid in [calendar[:-250], (calendar[0], *calendar), tuple(reversed(calendar))]:
        with pytest.raises(ValueError):
            research_windows(protocol, invalid)
    overlap = json.loads(json.dumps(protocol))
    overlap["folds"][1]["train"][0] = "2019-04-30"
    with pytest.raises(ValueError, match="overlapping"):
        research_windows(overlap, calendar)


def test_warmup_extends_into_scored_cash_days_and_blackout_allows_only_scoring(windows):
    w = windows[0]
    sessions = (*w.embargo, *w.scored[:6])
    history = RoleHistory(w, "SPY", (b for s in sessions for b in bars(s)))
    embargo = history.at(w.embargo[-1].closes)
    assert not embargo.scored and not embargo.may_enter and len(embargo.hours) == 36
    assert history.at(w.scored[0].opens).scored and not history.at(w.scored[0].opens).ready
    early = next(s for s in sessions if s.day == date(2016, 2, 12))
    assert early.closes.astimezone(NY).hour == 13
    assert len([h for h in history.at(early.closes).hours if h.start.date() == early.day]) == 3
    fifty = early.opens + timedelta(hours=2, minutes=30)  # 12:00 local, after 8 prior full sessions
    assert not history.at(fifty - timedelta(microseconds=1)).ready
    assert history.at(fifty).ready and history.at(fifty).may_enter
    assert history.at(w.scored[-7].opens).may_enter
    for s in w.scored[-6:]:
        ctx = history.at(s.opens)
        assert ctx.scored and ctx.ready and not ctx.may_enter
    for s in w.purge:
        ctx = history.at(s.closes)
        assert not ctx.scored and not ctx.may_enter


@pytest.mark.parametrize("day,utc_hour", [(date(2016, 3, 11), 15), (date(2016, 3, 14), 14)])
def test_hours_use_local_clock_all_four_slots_and_latest_receipt(windows, day, utc_hour):
    s = session(day)
    rows = bars(s)
    fourth = rows[5]  # 10:45-11:00 New York
    delayed = replace(fourth, received_at=fourth.end + timedelta(minutes=80))
    history = RoleHistory(windows[0], "SPY", [*rows[:5], delayed, *rows[6:]])
    before = history.at(delayed.received_at - timedelta(microseconds=1))
    assert delayed not in before.quarters
    assert [h.start.hour for h in before.hours] == [utc_hour + 1]  # later hour arrived first
    after = history.at(delayed.received_at)
    assert [h.start.hour for h in after.hours] == [utc_hour, utc_hour + 1]
    h = after.hours[0]
    assert (h.open, h.high, h.low, h.close, h.volume) == (102, 107, 101, 106, 40)
    assert h.received_at == delayed.received_at
    missing = RoleHistory(windows[0], "SPY", rows[:4] + rows[5:]).at(s.closes)
    assert len(missing.hours) == 5


def test_only_same_role_symbol_and_causally_completed_quarters(windows):
    old, new = windows[:2]
    s = new.embargo[0]
    row = bars(s)[0]
    late_old = replace(bars(old.purge[-1])[-1], received_at=s.closes)
    history = RoleHistory(
        new, "SPY", [*bars(old.purge[0]), late_old, *bars(s, "QQQ"), replace(row, received_at=row.start)]
    )
    assert not history.at(row.start).quarters
    ctx = history.at(s.closes)
    assert len(ctx.quarters) == 1 and not ctx.hours and not ctx.ready
    for moment in [old.purge[-1].closes, s.opens - timedelta(seconds=1), s.closes + timedelta(seconds=1)]:
        with pytest.raises(ValueError):
            history.at(moment)


def test_unusable_or_duplicate_quarters_cannot_make_an_hour(windows):
    w = windows[0]
    row = bars(w.embargo[0])[0]
    for invalid in [
        [row, row],
        [replace(row, end=row.end + QUARTER)],
        [replace(row, start=row.start + timedelta(seconds=1))],
        bars(session(date(2016, 2, 5))),
    ]:
        with pytest.raises(ValueError):
            RoleHistory(w, "SPY", invalid)
    tail = windows[-1]
    s = tail.purge[-1]
    rows = bars(s)
    denied = replace(rows[-1], received_at=tail.research_cutoff)
    assert denied not in RoleHistory(tail, "SPY", [*rows[:-1], denied]).at(s.closes).quarters


def test_actual_final_fill_within_five_scheduled_sessions_or_trade_is_invalid(windows):
    w = windows[0]
    history = RoleHistory(w, "SPY", [b for s in (*w.embargo, *w.scored[:5]) for b in bars(s)])
    signal, first = w.scored[5].closes, w.scored[6].opens
    history.validate_trade_span(signal, first, w.scored[9].closes)
    with pytest.raises(ValueError, match="session limit"):
        history.validate_trade_span(signal, first, w.scored[10].opens)
    with pytest.raises(ValueError, match="unclosed"):
        history.validate_trade_span(signal, first, None)
    for entry, flat in [(w.scored[-6].opens, w.scored[-6].closes), (first, w.purge[0].opens)]:
        with pytest.raises(ValueError, match="window"):
            history.validate_trade_span(signal, entry, flat)
