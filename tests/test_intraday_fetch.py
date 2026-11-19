from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from localtrader.cli import _load_intraday
from localtrader.config import load_config
from localtrader.domain import Bar, Market
from localtrader.execution import CostProfile
from localtrader.holdout import HoldoutError
from localtrader.intraday_data import CsvImportSpec, DatasetManifest, ImportedDataset, SessionSpec, import_csv
from localtrader.intraday_engine import InstrumentSpec, IntradayEngine, buy_and_hold, compute_metrics
from localtrader.intraday_fetch import (
    US_REGULAR_SESSION,
    CorporateActionError,
    default_end,
    derive_dividends,
    fetch_intraday_csv,
    output_paths,
    read_dividends,
    regular_session_bars,
)
from localtrader.intraday_strategies import StrategyContext

REPO = Path(__file__).resolve().parent.parent


def bar(symbol: str, start: datetime, close: float = 100.5, delay: timedelta = timedelta(0)) -> Bar:
    end = start + timedelta(minutes=15)
    return Bar(
        symbol,
        Market.EQUITY,
        "15Min",
        start,
        end,
        close - 0.5,
        close + 0.5,
        close - 1,
        close,
        1000.0,
        "alpaca",
        end + delay,
    )


class FakeDownloader:
    """Returns raw bars for adjustment=raw and the same bars scaled by ``factor(day)`` for adjustment=all."""

    def __init__(self, raw: list[Bar], factor: Any = lambda day: 1.0, actions: dict[str, Any] | None = None) -> None:
        self.raw = raw
        self.factor = factor
        self.actions = actions or {}
        self.requests: list[Any] = []

    def corporate_actions(self, symbol: str, start: date, end: date) -> dict[str, Any]:
        return self.actions

    def download(self, request: Any, progress: Any = None) -> tuple[list[Bar], dict[str, Any], Path]:
        self.requests.append(request)
        if request.adjustment == "raw":
            bars = self.raw
        else:
            bars = []
            for b in self.raw:
                f = self.factor(US_REGULAR_SESSION.local_date(b.start))
                bars.append(replace(b, open=b.open * f, high=b.high * f, low=b.low * f, close=b.close * f))
        return bars, {"request_id": request.adjustment, "downloaded_at": "2026-11-17T00:00:00+00:00"}, Path("x")


# 2024-03-05 is a Tuesday; New York is UTC-5 (before the DST switch on 2024-03-10).
DAY = datetime(2024, 3, 5, tzinfo=UTC)
PRE_MARKET = DAY.replace(hour=13, minute=0)  # 08:00 New York
OPEN = DAY.replace(hour=14, minute=30)  # 09:30 New York
LAST = DAY.replace(hour=20, minute=45)  # 15:45 New York
AFTER = DAY.replace(hour=21, minute=0)  # 16:00 New York, after-hours


class SessionTests(unittest.TestCase):
    def test_only_regular_session_bars_are_kept(self) -> None:
        kept = regular_session_bars([bar("SPY", t) for t in (PRE_MARKET, OPEN, LAST, AFTER)])
        self.assertEqual([b.start for b in kept], [OPEN, LAST])

    def test_session_follows_daylight_saving_time(self) -> None:
        self.assertTrue(US_REGULAR_SESSION.contains(datetime(2024, 7, 2, 13, 30, tzinfo=UTC)))  # 09:30 EDT

    def test_early_close_ends_the_session_at_13_00(self) -> None:
        # Regression (PR #21 review): 2026-11-27 13:15 New York was accepted although NYSE closes at 13:00.
        self.assertFalse(US_REGULAR_SESSION.contains(datetime(2026, 11, 27, 18, 15, tzinfo=UTC)))
        self.assertTrue(US_REGULAR_SESSION.contains(datetime(2026, 11, 27, 17, 45, tzinfo=UTC)))  # 12:45
        self.assertFalse(US_REGULAR_SESSION.contains(datetime(2024, 12, 24, 18, 0, tzinfo=UTC)))
        self.assertTrue(US_REGULAR_SESSION.contains(datetime(2024, 12, 23, 18, 0, tzinfo=UTC)))  # normal day

    def test_days_beyond_the_known_calendar_are_not_traded(self) -> None:
        self.assertFalse(US_REGULAR_SESSION.contains(datetime(2027, 1, 5, 15, 0, tzinfo=UTC)))

    def test_config_uses_the_calendar(self) -> None:
        from localtrader.intraday_engine import instruments_from_config

        session = instruments_from_config(load_config())["SPY"].session
        self.assertFalse(session.contains(datetime(2026, 11, 27, 18, 15, tzinfo=UTC)))


def _days(n: int) -> list[datetime]:
    """Opening bars of n consecutive weekdays from Monday 2024-03-04 (EST)."""
    out: list[datetime] = []
    day = datetime(2024, 3, 4, 14, 30, tzinfo=UTC)
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


class DividendTests(unittest.TestCase):
    def test_dividend_is_derived_from_raw_and_adjusted_series(self) -> None:
        starts = _days(4)
        raw = [bar("SPY", s, 100.0) for s in starts]
        ex_day = US_REGULAR_SESSION.local_date(starts[2])
        # 1.00 dividend on prev close 100.0: earlier prices scaled by 0.99
        factor = lambda day: 0.99 if day < ex_day else 1.0  # noqa: E731
        adjusted = FakeDownloader(raw, factor).download(replace_request("all"))[0]
        self.assertEqual(derive_dividends(raw, adjusted), {ex_day: 1.0})

    def test_missing_quarters_are_reported(self) -> None:
        from localtrader.intraday_fetch import dividend_gaps

        dividends = {date(2018, 3, 16): 1.1, date(2018, 9, 21): 1.3, date(2018, 12, 21): 1.4}
        self.assertEqual(dividend_gaps(dividends, date(2018, 1, 2), date(2019, 2, 1)), ["2018-03-16..2018-09-21"])

    def test_split_is_rejected(self) -> None:
        starts = _days(3)
        raw = [bar("SPY", s, 100.0) for s in starts]
        ex_day = US_REGULAR_SESSION.local_date(starts[1])
        adjusted = FakeDownloader(raw, lambda day: 0.5 if day < ex_day else 1.0).download(replace_request("all"))[0]
        with self.assertRaises(CorporateActionError):
            derive_dividends(raw, adjusted)

    def test_cent_rounding_of_low_priced_adjusted_series_is_tolerated(self) -> None:
        # T-021 regression (EFA 2016-01-05): adjusted ~42 USD rounded to cents moves adjusted/raw by ~2.4e-4
        starts = [OPEN + timedelta(minutes=15 * i) for i in range(4)]
        raw = [bar("EFA", s, p) for s, p in zip(starts, (57.72, 57.55, 57.35, 57.68), strict=True)]
        adjusted = [replace(b, close=c) for b, c in zip(raw, (42.12, 42.0, 41.85, 42.1), strict=True)]
        from localtrader.intraday_fetch import derive_dividends_with_precision

        self.assertEqual(derive_dividends_with_precision(raw, adjusted)[0], {})
        # PR #48 review 2: the central T-008 rule stays strict and still rejects this noise
        with self.assertRaises(CorporateActionError):
            derive_dividends(raw, adjusted)

    def test_daily_median_damps_rounding_noise_in_the_derived_amount(self) -> None:
        # T-021 (TLT 2020-11-02): a noisy first bar must not move the derived amount by several percent
        starts = _days(2)
        day = [s + timedelta(minutes=15 * i) for s in starts for i in range(5)]
        raw = [bar("TLT", s, 160.0) for s in day]
        factor = 1 - 0.17 / 160.0
        noisy = [factor * 120.0 / 160.0 * (1 + (6e-5 if i == 0 else 0.0)) for i in range(5)]
        ratios = noisy + [120.0 / 160.0] * 5
        adjusted = [replace(b, close=b.close * r) for b, r in zip(raw, ratios, strict=True)]
        from localtrader.intraday_fetch import derive_dividends_with_precision

        derived = derive_dividends_with_precision(raw, adjusted)[0]
        self.assertEqual(list(derived), [US_REGULAR_SESSION.local_date(starts[1])])
        self.assertAlmostEqual(derived[US_REGULAR_SESSION.local_date(starts[1])], 0.17, delta=0.002)

    def test_small_dividend_is_checked_against_the_rounding_precision(self) -> None:
        # T-021 regression (EEM 2021-12-30): 0.026007 official vs 0.0241 derived at ~48 USD is within cent precision
        from localtrader.intraday_fetch import crosscheck_dividends, derive_dividends_with_precision

        ex = date(2021, 12, 30)
        official = {ex: (date(2022, 1, 5), 0.026007)}
        self.assertIn(" vs derived ", crosscheck_dividends(official, {ex: 0.0241})[0])
        self.assertEqual(crosscheck_dividends(official, {ex: 0.0241}, None, {ex: 0.011}), [])
        # outside the precision it still aborts
        self.assertIn(" vs derived ", crosscheck_dividends(official, {ex: 0.05}, None, {ex: 0.011})[0])
        starts = _days(3)
        raw = [bar("EEM", s, 48.0) for s in starts]
        ex_day = US_REGULAR_SESSION.local_date(starts[2])
        adjusted = FakeDownloader(raw, lambda day: 0.95 if day < ex_day else 1.0).download(replace_request("all"))[0]
        amounts, precision = derive_dividends_with_precision(raw, adjusted)
        self.assertEqual(list(amounts), [ex_day])
        self.assertAlmostEqual(
            precision[ex_day], 48.0 * (0.005 / (48.0 * 0.95) + 0.005 / 48.0), places=9
        )  # larger band: day before

    def test_central_validator_keeps_the_strict_rule(self) -> None:
        # PR #48 review 2: the T-021 rounding rule must not change the central T-008 path (checked without a run)
        import inspect

        from localtrader import t008_central
        from localtrader.intraday_fetch import crosscheck_dividends

        source = inspect.getsource(t008_central)
        self.assertIn("derive_dividends(series[", source)
        self.assertNotIn("derive_dividends_with_precision", source)
        self.assertNotIn("precision", source)
        # same synthetic case as the review: 0.046 official, 0.040 derived, band ~0.01125
        ex = date(2024, 3, 18)
        official = {ex: (date(2024, 3, 22), 0.046)}
        self.assertIn(" vs derived ", crosscheck_dividends(official, {ex: 0.040})[0])  # central: rejected
        self.assertEqual(crosscheck_dividends(official, {ex: 0.040}, None, {ex: 0.01125}), [])  # research download

    def test_adjustment_changing_within_a_day_is_rejected(self) -> None:
        raw = [bar("SPY", OPEN, 100.0), bar("SPY", OPEN + timedelta(minutes=15), 100.0)]
        adjusted = [raw[0], replace(raw[1], close=99.0)]
        with self.assertRaises(CorporateActionError):
            derive_dividends(raw, adjusted)


def replace_request(adjustment: str) -> Any:
    class R:
        pass

    request = R()
    request.adjustment = adjustment  # type: ignore[attr-defined]
    return request


class FetchTests(unittest.TestCase):
    def test_raw_adjusted_and_dividends_are_written_separately(self) -> None:
        starts = _days(3)
        raw = [bar("SPY", s, 100.0 + i) for i, s in enumerate(starts)]
        raw.insert(0, bar("SPY", starts[0] - timedelta(hours=1)))  # pre-market, dropped
        raw.append(bar("QQQ", starts[0]))  # other symbol, ignored
        ex_day = US_REGULAR_SESSION.local_date(starts[2])
        pay_day = ex_day + timedelta(days=40)
        official = {
            "cash_dividends": [{"symbol": "SPY", "ex_date": str(ex_day), "payable_date": str(pay_day), "rate": 1.01}]
        }
        downloader = FakeDownloader(raw, lambda day: 0.99 if day < ex_day else 1.0, official)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SPY_15m.csv"
            summary = fetch_intraday_csv(downloader, "SPY", starts[0], starts[-1] + timedelta(days=1), path)
            self.assertEqual(summary["dividend_crosscheck"], [])
            paths = output_paths(path)
            self.assertEqual([r.adjustment for r in downloader.requests], ["raw", "all"])
            self.assertEqual(summary["bars_regular_session"], 3)
            manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
            self.assertEqual(manifest["files"]["raw"]["sha256"], summary["files"]["raw"]["sha256"])
            data = import_csv(
                CsvImportSpec(str(path), "SPY", Market.ETF, "t", "alpaca_sip_raw", "UTC"), US_REGULAR_SESSION
            )
            adjusted = import_csv(
                CsvImportSpec(str(paths["adjusted"]), "SPY", Market.ETF, "t", "alpaca_sip_all", "UTC"),
                US_REGULAR_SESSION,
            )
            dividends = read_dividends(paths["dividends"])
        self.assertEqual([b.close for b in data.bars], [100.0, 101.0, 102.0])  # as traded
        self.assertAlmostEqual(adjusted.bars[0].close, 99.0)
        self.assertEqual(dividends, {ex_day: (pay_day, 1.01)})
        self.assertEqual(data.bars[0].received_at, data.bars[0].end)

    def test_disagreeing_or_split_corporate_actions_abort(self) -> None:
        starts = _days(3)
        raw = [bar("SPY", s, 100.0) for s in starts]
        ex_day = US_REGULAR_SESSION.local_date(starts[2])
        factor = lambda day: 0.99 if day < ex_day else 1.0  # noqa: E731
        cases = {
            "amount": {"cash_dividends": [{"ex_date": str(ex_day), "payable_date": str(ex_day), "rate": 2.0}]},
            "split": {"forward_splits": [{"ex_date": str(ex_day), "new_rate": 2, "old_rate": 1}]},
        }
        for name, actions in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "SPY_15m.csv"
                with self.assertRaises(CorporateActionError):
                    fetch_intraday_csv(FakeDownloader(raw, factor, actions), "SPY", starts[0], starts[-1], path)
                self.assertFalse(path.exists())  # nothing written before the check

    def test_duplicate_entry_with_other_pay_date_keeps_the_later_one(self) -> None:
        from localtrader.intraday_fetch import dividends_from_actions, duplicate_pay_dates

        ex = date(2022, 9, 19)
        entries = [
            {"ex_date": "2022-09-19", "payable_date": "2022-09-23", "rate": 0.51856},
            {"ex_date": "2022-09-19", "payable_date": "2022-10-31", "rate": 0.51856},
        ]
        actions = {"cash_dividends": entries}
        self.assertEqual(dividends_from_actions(actions, "QQQ", ex, ex), {ex: (date(2022, 10, 31), 0.51856)})
        self.assertEqual(len(duplicate_pay_dates(actions, "QQQ")), 1)
        entries[1]["rate"] = 0.6
        with self.assertRaises(CorporateActionError):
            dividends_from_actions(actions, "QQQ", ex, ex)

    def test_duplicate_listing_explains_a_doubled_derived_amount_only(self) -> None:
        from localtrader.intraday_fetch import crosscheck_dividends

        ex = date(2022, 9, 19)
        official = {ex: (date(2022, 10, 31), 0.51856)}
        explained = crosscheck_dividends(official, {ex: 1.0405}, {ex: 2})
        self.assertEqual(len(explained), 1)
        self.assertIn("explained", explained[0])
        self.assertIn(" vs derived ", crosscheck_dividends(official, {ex: 1.0405}, {ex: 1})[0])
        self.assertIn(" vs derived ", crosscheck_dividends(official, {ex: 1.6}, {ex: 2})[0])

    def test_missing_payment_dates_use_the_longest_observed_lag(self) -> None:
        from localtrader.intraday_fetch import fill_missing_pay_dates

        raw = {
            date(2019, 3, 15): (None, 1.2),
            date(2024, 3, 15): (date(2024, 4, 30), 1.6),
            date(2024, 6, 21): (date(2024, 7, 31), 1.7),
        }
        filled, missing = fill_missing_pay_dates(raw)
        self.assertEqual(missing, [date(2019, 3, 15)])
        self.assertEqual(filled[date(2019, 3, 15)], (date(2019, 3, 15) + timedelta(days=46), 1.2))
        with self.assertRaises(CorporateActionError):
            fill_missing_pay_dates({date(2019, 3, 15): (None, 1.2)})

    def test_holdout_actions_never_change_the_assumed_payment_lag(self) -> None:
        # Regression (PR #21 review 3): a 30-day holdout lag moved a 2019 assumed payment date.
        from localtrader.intraday_fetch import dividends_from_actions

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        actions = {
            "cash_dividends": [
                {"ex_date": "2019-03-01", "payable_date": None, "rate": 1.0},
                {"ex_date": "2024-03-15", "payable_date": "2024-03-25", "rate": 1.0},  # 10-day lag
                {"ex_date": "2025-11-03", "payable_date": "2025-12-03", "rate": 1.0},  # holdout, 30 days
            ]
        }
        with_cut = dividends_from_actions(actions, "SPY", date(2016, 1, 4), date(2026, 1, 1), cutoff)
        self.assertEqual(with_cut[date(2019, 3, 1)], (date(2019, 3, 11), 1.0))
        self.assertNotIn(date(2025, 11, 3), with_cut)

    def test_withheld_dividend_rows_are_skipped_before_parsing_and_never_echoed(self) -> None:
        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "d.csv"
            path.write_text(
                "ex_date,pay_date,amount,source\n"
                "2025-10-31,2025-11-28,1.11,t\n"  # last US trading day before a 00:00 UTC cutoff: research
                "2025-12-19,not-a-date,9.87654,t\n",  # withheld and defective
                encoding="utf-8",
            )
            self.assertEqual(read_dividends(path, cutoff), {date(2025, 10, 31): (date(2025, 11, 28), 1.11)})
            with self.assertRaises(CorporateActionError) as caught:
                read_dividends(path)  # without a cutoff the defect is reported - by line only
        self.assertIn("line 3", str(caught.exception))
        self.assertNotIn("9.87654", str(caught.exception))
        self.assertIsNone(caught.exception.__cause__)

    def test_invalid_dividend_files_are_rejected(self) -> None:
        cases = {
            "negative": "2024-03-05,2024-04-30,-1.0",
            "nan": "2024-03-05,2024-04-30,nan",
            "paid before ex": "2024-03-05,2024-03-01,1.0",
            "duplicate": "2024-03-05,2024-04-30,1.0\n2024-03-05,2024-04-30,1.1",
            "unparseable": "2024-03-05,,1.0",
        }
        for name, rows in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "d.csv"
                path.write_text(
                    "ex_date,pay_date,amount,source\n" + "\n".join(r + ",x" for r in rows.split("\n")), encoding="utf-8"
                )
                with self.assertRaises(CorporateActionError):
                    read_dividends(path)

    def test_research_fetch_never_reaches_the_holdout(self) -> None:
        # User decision 2026-11-18: research files physically contain no holdout rows.
        starts = _days(3)
        cutoff = starts[1] + timedelta(minutes=15)  # end of the second day's first bar
        raw = [bar("SPY", s, 100.0) for s in starts]
        late_dividend = {
            "ex_date": str(US_REGULAR_SESSION.local_date(starts[2])),
            "payable_date": "2024-04-30",
            "rate": 1.0,
        }
        downloader = FakeDownloader(raw, actions={"cash_dividends": [late_dividend]})
        seen: list[Any] = []
        original = downloader.corporate_actions

        def record(symbol: str, start: date, end: date) -> dict[str, Any]:
            seen.append(end)
            return original(symbol, start, end)

        downloader.corporate_actions = record  # type: ignore[method-assign]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SPY_15m.csv"
            with self.assertRaises(ValueError):
                fetch_intraday_csv(downloader, "SPY", starts[0], starts[2], path, available_until=cutoff)
            summary = fetch_intraday_csv(downloader, "SPY", starts[0], cutoff, path, available_until=cutoff)
            data = import_csv(CsvImportSpec(str(path), "SPY", Market.ETF, "t", "s", "UTC"), US_REGULAR_SESSION)
            dividends = read_dividends(output_paths(path)["dividends"])
        self.assertTrue(all(b.end <= cutoff for b in data.bars))
        self.assertEqual(len(data.bars), 2)
        # Regression (PR #21 review 5): Alpaca's end is inclusive, so the provider request ends one bar
        # before the cutoff (the last allowed bar starts there and ends exactly at the cutoff)
        self.assertTrue(all(r.end == cutoff - timedelta(minutes=15) for r in downloader.requests[-2:]))
        self.assertEqual(dividends, {})  # ex-date inside the holdout is never written
        # query ends on the last UTC day before the cutoff, inclusive (Regression: the New York date of a
        # 00:00 UTC cutoff is one day earlier and payments processed on the last research day were lost)
        self.assertEqual(seen[0], (cutoff - timedelta(microseconds=1)).astimezone(UTC).date())
        self.assertEqual(summary["available_until"], cutoff.isoformat())

    def test_default_end_is_old_enough_and_aligned(self) -> None:
        now = datetime(2026, 11, 17, 19, 52, 13, tzinfo=UTC)
        end = default_end(now)
        self.assertLessEqual(end, now - timedelta(minutes=15))
        self.assertEqual((end.minute % 15, end.second, end.microsecond), (0, 0, 0))


def _csv(tmp: str, rows: list[tuple[datetime, float]]) -> Path:
    path = Path(tmp) / "SPY_15m.csv"
    lines = ["timestamp,open,high,low,close,volume"]
    lines += [f"{s.isoformat()},{p},{p + 1},{p - 1},{p},1000" for s, p in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class HoldoutTests(unittest.TestCase):
    def _import(self, path: Path, cutoff: datetime | None, delay: int = 0) -> ImportedDataset:
        return import_csv(
            CsvImportSpec(
                str(path), "SPY", Market.ETF, "t", "s", "UTC", availability_delay_seconds=delay, available_until=cutoff
            ),
            US_REGULAR_SESSION,
        )

    def test_holdout_prices_do_not_reach_research_metadata(self) -> None:
        # Regression (PR #21 review): a jump inside the holdout appeared in quality.jumps of research reports.
        before = datetime(2025, 10, 31, 14, 30, tzinfo=UTC)
        inside = datetime(2025, 11, 3, 14, 30, tzinfo=UTC)
        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            calm = self._import(_csv(tmp, [(before, 100.0), (inside, 100.0)]), cutoff)
            jump = self._import(_csv(tmp, [(before, 100.0), (inside, 200.0)]), cutoff)
        self.assertEqual([b.start for b in jump.bars], [before])
        self.assertEqual(jump.manifest.quality["holdout_rows_withheld"], 1)
        self.assertEqual(jump.manifest.quality["jumps"], [])
        self.assertEqual(jump.manifest.quality["last_bar"], before.isoformat())
        self.assertEqual(jump.manifest.data_version, calm.manifest.data_version)
        self.assertEqual(jump.manifest.quality, calm.manifest.quality)
        self.assertEqual(jump.manifest, calm.manifest)  # incl. the provenance hash of the research rows

    def test_bar_straddling_the_cutoff_is_withheld(self) -> None:
        # Regression (PR #21 review): a 14:30-14:45 bar stayed in research with cutoff 14:37.
        with tempfile.TemporaryDirectory() as tmp:
            path = _csv(tmp, [(OPEN, 100.0), (OPEN + timedelta(minutes=15), 100.0)])
            data = self._import(path, OPEN + timedelta(minutes=7))
            self.assertEqual(data.bars, [])
            exact = self._import(path, OPEN + timedelta(minutes=15))
            self.assertEqual([b.start for b in exact.bars], [OPEN])
            delayed = self._import(path, OPEN + timedelta(minutes=15), delay=60)  # available 14:46
            self.assertEqual(delayed.bars, [])

    def test_naive_cutoff_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            self._import(_csv(tmp, [(OPEN, 100.0)]), datetime(2025, 11, 1))

    def test_research_runs_exclude_the_configured_holdout(self) -> None:
        before = datetime(2025, 10, 31, 14, 30, tzinfo=UTC)
        inside = datetime(2025, 11, 3, 14, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(_csv(tmp, [(before, 100.0), (inside, 100.0)]))
            dividends = Path(tmp) / "SPY_dividends.csv"
            rows = "ex_date,pay_date,amount,source\n2025-09-22,2025-10-31,1.5,t\n2025-12-22,2026-01-30,1.6,t\n"
            dividends.write_text(rows, encoding="utf-8")
            config = str(REPO / "config" / "default.toml")
            _, _, datasets, holdout = _load_intraday(config, path, None, "SPY")
            self.assertEqual([b.start for b in datasets["SPY"].bars], [before])
            self.assertEqual(holdout["excluded_bars"], 1)
            self.assertFalse(holdout["used"])
            self.assertEqual(datasets["SPY"].dividends, {date(2025, 9, 22): (date(2025, 10, 31), 1.5)})
            version = datasets["SPY"].manifest.data_version
            # Regression (PR #21 review 2): a changed dividend must change the dataset version
            dividends.write_text(rows.replace(",1.5,", ",50.0,"), encoding="utf-8")
            _, _, changed, _ = _load_intraday(config, path, None, "SPY")
            self.assertNotEqual(changed["SPY"].manifest.data_version, version)
            # ... but a change only in the withheld holdout part must not
            dividends.write_text(rows.replace(",1.6,", ",60.0,"), encoding="utf-8")
            _, _, holdout_only, _ = _load_intraday(config, path, None, "SPY")
            self.assertEqual(holdout_only["SPY"].manifest, datasets["SPY"].manifest)
            dividends.unlink()
            with self.assertRaises(SystemExit):  # missing corporate actions: fail closed
                _load_intraday(config, path, None, "SPY")
            dividends.write_text(rows, encoding="utf-8")
            # Regression (PR #21 review 4): no research path to the holdout, no configuration that moves it
            moved = Path(tmp) / "moved.toml"
            moved.write_text(
                Path(config)
                .read_text(encoding="utf-8")
                .replace('start = "2025-11-01T00:00:00+00:00"', 'start = "2026-06-01T00:00:00+00:00"'),
                encoding="utf-8",
            )
            with self.assertRaises(HoldoutError):
                _load_intraday(str(moved), path, None, "SPY")
            import inspect

            self.assertNotIn("use_holdout", inspect.signature(_load_intraday).parameters)


SESSION = SessionSpec("UTC", time(0, 0), time(23, 59))


class HoldThrough:
    name = "hold"

    def parameters(self) -> dict[str, float]:
        return {}

    def evaluate(self, ctx: StrategyContext) -> Any:
        from localtrader.domain import Direction, Signal

        if not ctx.in_position and len(ctx.bars) == 1:
            return Signal("hold", "X", Market.ETF, ctx.now, ctx.now, Direction.LONG, 1.0, "hold", stop_price=95.0)
        return None


class SellOnDay:
    """Enters on the first bar, exits once the given number of bars has been seen."""

    name = "hold"

    def __init__(self, exit_after: int | None = None) -> None:
        self.exit_after = exit_after

    def parameters(self) -> dict[str, float]:
        return {}

    def evaluate(self, ctx: StrategyContext) -> Any:
        from localtrader.domain import Direction, Signal

        if not ctx.in_position and len(ctx.bars) == 1:
            return Signal("hold", "X", Market.ETF, ctx.now, ctx.now, Direction.LONG, 1.0, "in", stop_price=95.0)
        if ctx.in_position and self.exit_after is not None and len(ctx.bars) == self.exit_after:
            return Signal("hold", "X", Market.ETF, ctx.now, ctx.now, Direction.FLAT, 1.0, "out", stop_price=None)
        return None


class DividendBookingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()
        self.spec = InstrumentSpec("X", Market.ETF, "t", "USD", SESSION, CostProfile(0, 0, 0, 0), 0.001, 0.0, 0.15)
        days = [datetime(2024, 3, d, 10, tzinfo=UTC) for d in (4, 5, 6, 7)]
        starts = [d + timedelta(minutes=15 * i) for d in days for i in range(3)]
        self.bars = [replace(bar("X", s, 100.0), market=Market.ETF) for s in starts]
        self.manifest = DatasetManifest("X", "etf", "t", "unit", "UTC", 15, "v", "v", False, {})
        self.ex, self.pay = date(2024, 3, 5), date(2024, 3, 7)

    def run_engine(self, strategy: Any, dividends: dict[date, Any]) -> Any:
        data = ImportedDataset(self.bars, self.manifest, frozenset(), dividends)
        return IntradayEngine(self.config, {"X": self.spec}, [strategy]).run({"X": data})

    def test_entitlement_at_ex_date_cash_only_at_payment(self) -> None:
        # Regression (PR #21 review 2): the dividend was spendable cash from the ex-date on.
        result = self.run_engine(SellOnDay(), {self.ex: (self.pay, 2.0)})
        quantity = result.fills[0].quantity
        net = quantity * 2.0 * 0.85
        events = [d for d in result.decisions if str(d["event"]).startswith("dividend")]
        self.assertEqual([e["event"] for e in events], ["dividend_entitlement", "dividend_paid"])
        self.assertTrue(str(events[0]["time"]).startswith("2024-03-05"))
        self.assertTrue(str(events[1]["time"]).startswith("2024-03-07"))
        on_ex = [p for p in result.equity if p.timestamp.date() == self.ex][-1]
        before = [p for p in result.equity if p.timestamp.date() == date(2024, 3, 4)][-1]
        self.assertAlmostEqual(on_ex.cash, before.cash)  # not yet paid
        self.assertAlmostEqual(on_ex.equity - before.equity, net)  # but part of equity
        self.assertAlmostEqual(result.dividend_income, net)
        self.assertAlmostEqual(result.dividend_receivable_end, 0.0)

    def test_sold_between_ex_and_payment_is_still_paid(self) -> None:
        result = self.run_engine(SellOnDay(exit_after=5), {self.ex: (self.pay, 2.0)})
        exit_fill = [f for f in result.fills if f.quantity < 0][0]
        self.assertEqual(exit_fill.timestamp.date(), self.ex)
        paid = [d for d in result.decisions if d["event"] == "dividend_paid"]
        self.assertEqual(len(paid), 1)
        self.assertAlmostEqual(result.equity[-1].cash, result.equity[-1].equity)

    def test_unpaid_entitlement_at_end_is_reported(self) -> None:
        result = self.run_engine(SellOnDay(), {self.ex: (date(2024, 4, 30), 2.0)})
        self.assertGreater(result.dividend_receivable_end, 0)
        self.assertAlmostEqual(compute_metrics(result)["dividends_unpaid_at_end"], result.dividend_receivable_end)

    def test_benchmark_includes_dividends(self) -> None:
        with_div = ImportedDataset(self.bars, self.manifest, frozenset(), {self.ex: (self.pay, 2.0)})
        without = ImportedDataset(self.bars, self.manifest, frozenset())
        bh_with = buy_and_hold({"X": with_div}, {"X": self.spec}, self.config)["buy_and_hold_total_return"]
        bh_without = buy_and_hold({"X": without}, {"X": self.spec}, self.config)["buy_and_hold_total_return"]
        self.assertGreater(bh_with, bh_without)
