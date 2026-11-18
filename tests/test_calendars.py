from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta

from localtrader.calendars import (
    CalendarError,
    expected_bar_starts,
    inventory,
    is_trading_day,
    nyse_holidays,
    trading_days,
)


class HolidayTests(unittest.TestCase):
    def test_rule_based_holidays_and_observance(self) -> None:
        self.assertEqual(
            sorted(nyse_holidays(2022)),
            [
                date(2022, 1, 17), date(2022, 2, 21), date(2022, 4, 15), date(2022, 5, 30), date(2022, 6, 20),
                date(2022, 7, 4), date(2022, 9, 5), date(2022, 11, 24), date(2022, 12, 26),
            ],
        )  # fmt: skip
        self.assertNotIn(date(2021, 12, 31), nyse_holidays(2021))  # New Year 2022 on Saturday: not observed
        self.assertIn(date(2021, 12, 24), nyse_holidays(2021))  # Christmas on Saturday → Friday
        self.assertIn(date(2020, 7, 3), nyse_holidays(2020))  # Independence Day on Saturday → Friday
        self.assertNotIn(date(2021, 6, 18), nyse_holidays(2021))  # Juneteenth only from 2022
        self.assertIn(date(2026, 4, 3), nyse_holidays(2026))  # Good Friday
        self.assertIn(date(2025, 1, 9), nyse_holidays(2025))  # national day of mourning
        self.assertIn(date(2018, 12, 5), nyse_holidays(2018))

    def test_trading_days_and_coverage(self) -> None:
        self.assertFalse(is_trading_day(date(2024, 12, 25)))
        self.assertFalse(is_trading_day(date(2024, 12, 28)))  # Saturday
        self.assertTrue(is_trading_day(date(2024, 12, 24)))  # early close, still a trading day
        self.assertEqual(len(trading_days(date(2024, 12, 23), date(2024, 12, 30))), 4)
        with self.assertRaises(CalendarError):
            is_trading_day(date(2027, 1, 4))
        with self.assertRaises(CalendarError):
            is_trading_day(date(2015, 12, 31))


class SlotTests(unittest.TestCase):
    def test_expected_slots_respect_dst_and_early_close(self) -> None:
        winter = expected_bar_starts(date(2024, 1, 3))
        self.assertEqual((len(winter), winter[0]), (26, datetime(2024, 1, 3, 14, 30, tzinfo=UTC)))
        summer = expected_bar_starts(date(2024, 7, 2))
        self.assertEqual(summer[0], datetime(2024, 7, 2, 13, 30, tzinfo=UTC))
        half_day = expected_bar_starts(date(2024, 12, 24))
        self.assertEqual((len(half_day), half_day[-1]), (14, datetime(2024, 12, 24, 17, 45, tzinfo=UTC)))
        self.assertEqual(expected_bar_starts(date(2024, 12, 25)), [])

    def test_inventory_separates_holidays_from_gaps(self) -> None:
        days = trading_days(date(2024, 12, 23), date(2024, 12, 28))
        bars = [moment for day in days for moment in expected_bar_starts(day)]
        gap = bars.pop(5)
        extra = datetime(2024, 12, 24, 18, 0, tzinfo=UTC)  # after the 13:00 early close
        result = inventory([*bars, extra], date(2024, 12, 23), date(2024, 12, 28))
        self.assertEqual(result["trading_days"], 4)  # 25th is a holiday, not a gap
        self.assertEqual(result["missing"], [gap.isoformat()])
        self.assertEqual(result["unexpected"], [extra.isoformat()])
        self.assertEqual(result["expected_slots"], 26 * 3 + 14)
        complete = inventory(bars + [gap], date(2024, 12, 23), date(2024, 12, 28))
        self.assertEqual((complete["missing"], complete["unexpected"]), ([], []))
        self.assertEqual(complete["present_slots"], complete["expected_slots"])


class IntervalWidthTests(unittest.TestCase):
    def test_widths_that_do_not_tile_the_session_are_rejected(self) -> None:
        # Regression (PR #31 review): a 60-minute width produced a slot ending after the 16:00 close.
        for minutes in (60, 45, 0, -15):
            with self.subTest(minutes), self.assertRaises(CalendarError):
                expected_bar_starts(date(2024, 1, 3), minutes)
        with self.assertRaises(CalendarError):  # 13:00 early close: 3.5 h is not a multiple of 60
            expected_bar_starts(date(2024, 12, 24), 60)
        for minutes in (1, 5, 15, 30):
            slots = expected_bar_starts(date(2024, 12, 24), minutes)
            self.assertLessEqual(slots[-1] + timedelta(minutes=minutes), datetime(2024, 12, 24, 18, 0, tzinfo=UTC))
        with self.assertRaises(CalendarError):
            inventory([], date(2024, 1, 3), date(2024, 1, 4), minutes=60)
