from __future__ import annotations

import copy
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from localtrader.cost_schedule import (
    ScheduleError,
    digest,
    entries_for,
    load_schedule,
    measure_spreads,
    quoted_half_spread_bps,
    sample_bar_opens,
    validate,
)
from localtrader.holdout import HoldoutError

REPO = Path(__file__).resolve().parent.parent
SCHEDULE = REPO / "config" / "cost_schedule.json"


class ScheduleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.schedule = load_schedule(SCHEDULE)

    def test_committed_schedule_is_valid_and_complete(self) -> None:
        validate(self.schedule)
        for instrument in self.schedule["instruments"]:
            components = {e["component"] for e in entries_for(self.schedule, instrument, date(2024, 6, 3))}
            self.assertTrue({"commission", "regulatory_sec", "spread", "slippage"} <= components)

    def test_period_lookup_switches_sec_rates_by_date(self) -> None:
        def sec(day: date) -> float:
            return next(
                e["value"] for e in entries_for(self.schedule, "SPY", day) if e["component"] == "regulatory_sec"
            )

        self.assertEqual(sec(date(2026, 1, 15)), 0.0)  # SEC rate $0.00 until 2026-04-03
        self.assertAlmostEqual(sec(date(2026, 5, 1)), 0.206)  # $20.60 per million = 0.206 bp

    def test_invalid_schedules_are_rejected(self) -> None:
        def broken(mutate: Any) -> dict[str, Any]:
            changed = copy.deepcopy(self.schedule)
            mutate(changed)
            return changed

        cases = {
            "no reference": lambda s: s["entries"][0].__setitem__("reference", ""),
            "bad kind": lambda s: s["entries"][0].__setitem__("kind", "guess"),
            "negative": lambda s: s["entries"][0].__setitem__("value", -1),
            "overlap": lambda s: s["entries"].append({**s["entries"][0], "id": "dup-period"}),
            "duplicate id": lambda s: s["entries"].append(
                {**s["entries"][0], "valid_from": "1990-01-01", "valid_to": "1991-01-01"}
            ),
            "missing component": lambda s: s.__setitem__(
                "entries", [e for e in s["entries"] if e["component"] != "spread"]
            ),
            "measurement without sample": lambda s: next(e for e in s["entries"] if e["kind"] == "measurement").pop(
                "sample"
            ),
        }
        for name, mutate in cases.items():
            with self.subTest(name), self.assertRaises(ScheduleError):
                validate(broken(mutate))

    def test_digest_is_stable_and_sensitive(self) -> None:
        changed = copy.deepcopy(self.schedule)
        changed["entries"][0]["value"] += 0.001
        self.assertEqual(digest(self.schedule), digest(copy.deepcopy(self.schedule)))
        self.assertNotEqual(digest(self.schedule), digest(changed))


class SpreadTests(unittest.TestCase):
    def test_half_spread_and_unusable_quotes(self) -> None:
        self.assertAlmostEqual(quoted_half_spread_bps({"bp": 99.99, "ap": 100.01, "bs": 1, "as": 1}), 1.0)
        for quote in (
            {"bp": 100, "ap": 100, "bs": 1, "as": 1},  # locked
            {"bp": 101, "ap": 100, "bs": 1, "as": 1},  # crossed
            {"bp": 100, "ap": 100.01, "bs": 0, "as": 1},  # empty side
            {"bp": 0, "ap": 100.01, "bs": 1, "as": 1},
        ):
            self.assertIsNone(quoted_half_spread_bps(quote))

    def test_sampling_is_deterministic_and_never_reaches_the_holdout(self) -> None:
        start = datetime(2025, 10, 1, 14, 30, tzinfo=UTC)
        bars = [start + timedelta(days=i) for i in range(60)]
        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        first = sample_bar_opens(bars, 10, cutoff)
        self.assertEqual(first, sample_bar_opens(bars, 10, cutoff))
        self.assertTrue(all(moment < cutoff for moment in first))
        self.assertEqual(len(sample_bar_opens(bars, 1000, cutoff)), 31)

    def test_measure_uses_first_usable_quote_per_moment(self) -> None:
        urls: list[str] = []
        responses = [
            {
                "quotes": {
                    "SPY": [{"bp": 100, "ap": 100, "bs": 1, "as": 1}, {"bp": 99.99, "ap": 100.01, "bs": 5, "as": 5}]
                }
            },
            {"quotes": {}},
        ]

        def request_page(url: str) -> tuple[dict[str, Any], dict[str, str]]:
            urls.append(url)
            return responses[len(urls) - 1], {}

        moments = [datetime(2024, 3, 5, 14, 30, tzinfo=UTC), datetime(2024, 3, 5, 15, 0, tzinfo=UTC)]
        result = measure_spreads(request_page, "SPY", moments)
        self.assertEqual((result["samples"], result["missing"]), (1, 1))
        self.assertAlmostEqual(result["half_spread_bps"]["median"], 1.0)
        self.assertEqual(result["half_spread_bps_session_open"]["n"], 1)
        self.assertIn("feed=sip", urls[0])
        self.assertIn("start=2024-03-05T14%3A30%3A00Z", urls[0])


class ReviewRegressionTests(unittest.TestCase):
    """PR #28 review: holdout limit, DST-aware open statistic, NaN and per-date completeness."""

    def test_nan_and_infinite_costs_are_rejected(self) -> None:
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad):
                schedule = copy.deepcopy(load_schedule(SCHEDULE))
                schedule["entries"][0]["value"] = bad
                with self.assertRaises(ScheduleError):
                    validate(schedule)

    def test_gap_on_a_covered_date_is_rejected_and_visible(self) -> None:
        from localtrader.cost_schedule import missing_components

        schedule = copy.deepcopy(load_schedule(SCHEDULE))
        self.assertEqual(missing_components(schedule, "SPY", date(2027, 1, 1)), ["regulatory_taf/sell"])
        schedule["coverage"]["to"] = "2027-06-01"  # vouching for 2027 although TAF ends 2026-12-31
        with self.assertRaisesRegex(ScheduleError, "regulatory_taf/sell missing on 2027-01-01"):
            validate(schedule)

    def test_coverage_is_checked_per_trade_side(self) -> None:
        # Regression (PR #28 review 3): setting commission from "both" to "sell" left every purchase
        # without a commission entry, yet validate accepted the table and no gap was reported.
        from localtrader.cost_schedule import missing_components

        schedule = copy.deepcopy(load_schedule(SCHEDULE))
        self.assertEqual(missing_components(schedule, "SPY", date(2025, 10, 31)), [])
        commission = next(entry for entry in schedule["entries"] if entry["component"] == "commission")
        commission["side"] = "sell"
        self.assertEqual(missing_components(schedule, "SPY", date(2025, 10, 31)), ["commission/buy"])
        self.assertFalse(any(e["component"] == "commission" and e["side"] in ("buy", "both")
                             for e in entries_for(schedule, "SPY", date(2025, 10, 31))))  # fmt: skip
        with self.assertRaisesRegex(ScheduleError, "SPY: commission/buy missing on 2016-01-01"):
            validate(schedule)
        # an explicit buy entry (a documented zero is valid) closes the gap
        schedule["entries"].append({**commission, "id": "commission-buy", "side": "buy", "value": 0})
        validate(schedule)
        # spread and slippage need both sides as well; SEC/TAF only the sale
        spread = next(entry for entry in schedule["entries"] if entry["id"] == "spread-qqq")
        spread["side"] = "buy"
        self.assertEqual(missing_components(schedule, "QQQ", date(2020, 1, 2)), ["spread/sell"])
        self.assertEqual(missing_components(schedule, "SPY", date(2020, 1, 2)), [])

    def test_session_open_is_classified_in_new_york_time(self) -> None:
        moments = [
            datetime(2024, 7, 2, 13, 30, tzinfo=UTC),  # 09:30 EDT: open
            datetime(2024, 7, 2, 14, 30, tzinfo=UTC),  # 10:30 EDT: not open
            datetime(2024, 1, 3, 14, 30, tzinfo=UTC),  # 09:30 EST: open
        ]
        quote = {"quotes": {"SPY": [{"bp": 99.99, "ap": 100.01, "bs": 1, "as": 1}]}}
        result = measure_spreads(lambda url: (quote, {}), "SPY", moments)
        self.assertEqual(result["half_spread_bps_session_open"]["n"], 2)

    def test_cli_never_queries_the_holdout(self) -> None:
        import argparse
        import tempfile
        from unittest import mock

        from localtrader import cli

        requested: list[str] = []

        class FakeDownloader:
            def _request_page(self, url: str) -> tuple[dict[str, Any], dict[str, str]]:
                requested.append(url)
                return {"quotes": {}}, {}

        with tempfile.TemporaryDirectory() as tmp:
            bars = Path(tmp) / "bars.csv"
            rows = [
                "timestamp",
                "2025-10-31T19:45:00+00:00",
                "2025-11-03T14:30:00+00:00",
                "2025-10-31T23:59:59+00:00",
                "2025-10-31T23:59:58+00:00",  # window ends exactly at the cutoff: still excluded (review 2)
            ]
            bars.write_text("\n".join(rows) + "\n", encoding="utf-8")
            config = REPO / "config" / "default.toml"
            moved = Path(tmp) / "moved.toml"  # an alternative configuration trying to move the boundary later
            moved.write_text(
                config.read_text(encoding="utf-8") + '\n[holdout]\nstart = "2026-06-01T00:00:00+00:00"\n',
                encoding="utf-8",
            )

            def args(before: str | None, config_path: Path = config) -> argparse.Namespace:
                return argparse.Namespace(
                    config=str(config_path), bars=str(bars), samples=10, before=before, symbol="SPY",
                    output=str(Path(tmp) / "out.json"), env="unused",
                )  # fmt: skip

            with mock.patch(
                "localtrader.data_requests.AlpacaDatasetDownloader.from_env", return_value=FakeDownloader()
            ):
                with self.assertRaises(SystemExit):
                    cli.run_measure_spreads(args("2025-12-01T00:00:00+00:00"))  # cannot move the limit later
                with self.assertRaises(HoldoutError):
                    cli.run_measure_spreads(args(None, moved))  # nor through another configuration file
                self.assertEqual(requested, [])
                cli.run_measure_spreads(args(None))
        self.assertEqual(len(requested), 1)  # only 2025-10-31 19:45; the 23:59:59 window would cross the cutoff
        self.assertIn("2025-10-31T19%3A45%3A00Z", requested[0])
