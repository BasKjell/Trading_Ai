from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from localtrader.domain import Market
from localtrader.intraday_data import (
    CsvImportSpec,
    SessionSpec,
    completed_before,
    import_csv,
    resample,
    synthetic_intraday,
)

XETRA_ZONE = ZoneInfo("Europe/Berlin")
XETRA = SessionSpec("Europe/Berlin", time(9, 0), time(17, 30))
HEADER = "Date,Time,Open,High,Low,Close,Volume\n"


def _spec(path: Path, tz: str = "Europe/Berlin") -> CsvImportSpec:
    return CsvImportSpec(str(path), "DAX", Market.INDEX, "test", "unit", tz)


class CsvImportTests(unittest.TestCase):
    def _import(self, body: str, tz: str = "Europe/Berlin", session: SessionSpec | None = XETRA):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bars.csv"
            path.write_text(HEADER + body, encoding="utf-8")
            return import_csv(_spec(path, tz), session)

    def test_converts_source_timezone_to_utc_and_bar_end_availability(self) -> None:
        data = self._import("2021.01.04,10:00,100,101,99,100.5,10\n")
        bar = data.bars[0]
        self.assertEqual(bar.start, datetime(2021, 1, 4, 9, 0, tzinfo=UTC))
        self.assertEqual(bar.end - bar.start, timedelta(minutes=15))
        self.assertEqual(bar.received_at, bar.end)

    def test_daylight_saving_time_is_respected(self) -> None:
        data = self._import("2021.07.05,10:00,100,101,99,100.5,10\n")
        self.assertEqual(data.bars[0].start, datetime(2021, 7, 5, 8, 0, tzinfo=UTC))

    def test_invalid_rows_are_rejected_not_repaired(self) -> None:
        body = (
            "2021.01.04,10:00,100,101,99,100.5,10\n"
            "2021.01.04,10:00,100,101,99,100.5,10\n"  # duplicate
            "2021.01.04,10:15,100,99,99.5,100,10\n"  # high < open
            "2021.01.04,10:30,0,1,0,1,10\n"  # non-positive
            "2021.01.04,10:35,100,101,99,100,10\n"  # misaligned
            "2021.01.04,10:45,100,101,99,100,-1\n"  # negative volume
            "2021.01.04,11:00,abc,101,99,100,1\n"  # unparseable
        )
        data = self._import(body)
        self.assertEqual(len(data.bars), 1)
        self.assertEqual(
            data.manifest.quality["rejected"],
            {
                "duplicate": 1,
                "invalid_ohlc": 1,
                "non_positive_price": 1,
                "misaligned_timestamp": 1,
                "negative_volume": 1,
                "unparseable": 1,
            },
        )

    def test_non_finite_values_are_rejected_in_every_field(self) -> None:
        # Regression (PR #17 review): NaN passes all comparison-based checks and was accepted.
        valid = ["100", "101", "99", "100.5", "10"]
        for field_index, field_name in enumerate(["open", "high", "low", "close", "volume"]):
            for token in ("nan", "NaN", "inf", "-inf", "Infinity"):
                with self.subTest(field=field_name, value=token):
                    values = list(valid)
                    values[field_index] = token
                    data = self._import("2021.01.04,10:00," + ",".join(values) + "\n")
                    self.assertEqual(data.bars, [])
                    self.assertEqual(data.manifest.quality["rejected"], {"non_finite_value": 1})

    def test_intraday_gap_is_reported_and_not_filled(self) -> None:
        body = "2021.01.04,10:00,100,101,99,100,1\n2021.01.04,11:00,100,101,99,100,1\n"
        data = self._import(body)
        self.assertEqual(len(data.bars), 2)
        self.assertEqual(data.manifest.quality["intraday_gaps"], 1)
        self.assertEqual(data.manifest.quality["missing_bars_in_gaps"], 3)
        self.assertIn(data.bars[1].start, data.after_gap)

    def test_overnight_gap_is_expected(self) -> None:
        body = "2021.01.04,17:15,100,101,99,100,1\n2021.01.05,09:00,100,101,99,100,1\n"
        data = self._import(body)
        self.assertEqual(data.manifest.quality["intraday_gaps"], 0)
        self.assertEqual(data.after_gap, frozenset())

    def test_price_jump_is_reported(self) -> None:
        body = "2021.01.04,10:00,100,101,99,100,1\n2021.01.04,10:15,110,111,109,110,1\n"
        self.assertEqual(len(self._import(body).manifest.quality["jumps"]), 1)

    def test_iso_timestamp_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "iso.csv"
            path.write_text(
                "timestamp,open,high,low,close,volume\n2021-01-04T09:00:00+00:00,1,2,0.5,1.5,3\n", encoding="utf-8"
            )
            data = import_csv(_spec(path), XETRA)
        self.assertEqual(data.bars[0].start, datetime(2021, 1, 4, 9, 0, tzinfo=UTC))

    def test_data_version_changes_with_content(self) -> None:
        a = self._import("2021.01.04,10:00,100,101,99,100,1\n").manifest.data_version
        b = self._import("2021.01.04,10:00,100,101,99,100.1,1\n").manifest.data_version
        self.assertNotEqual(a, b)


class DaxSampleTests(unittest.TestCase):
    def test_versioned_dax_sample_imports_cleanly(self) -> None:
        path = Path(__file__).resolve().parent.parent / "data" / "DAX3015.csv"
        data = import_csv(CsvImportSpec(str(path), "DAX", Market.INDEX, "cfd", "dax3015_csv", "Europe/Athens"), XETRA)
        self.assertGreater(len(data.bars), 14_000)
        self.assertEqual(data.manifest.quality["rejected"].get("duplicate", 0), 0)
        # Xetra opens 09:00 Berlin; the busiest bar of the day must be the open after conversion.
        volume_by_time: dict[str, list[float]] = {}
        for bar in data.bars:
            key = bar.start.astimezone(XETRA_ZONE).strftime("%H:%M")
            volume_by_time.setdefault(key, []).append(bar.volume)
        busiest = max(volume_by_time, key=lambda k: sum(volume_by_time[k]) / len(volume_by_time[k]))
        self.assertEqual(busiest, "09:00")


class ResampleTests(unittest.TestCase):
    def test_hourly_bar_available_only_after_last_constituent(self) -> None:
        data = synthetic_intraday(days=1)
        hourly = resample(data.bars, 60, "Europe/Berlin")
        first = hourly[0]
        constituents = [b for b in data.bars if first.start <= b.start < first.start + timedelta(hours=1)]
        self.assertEqual(len(constituents), 4)
        self.assertEqual(first.end, constituents[-1].end)
        self.assertEqual(first.high, max(b.high for b in constituents))
        self.assertEqual(first.close, constituents[-1].close)
        # At the end of the third 15m bar the hourly bar must not be visible yet
        self.assertEqual(completed_before([first], constituents[2].end), [])
        self.assertEqual(completed_before([first], constituents[3].end), [first])

    def test_daily_resample_uses_local_calendar_day(self) -> None:
        data = synthetic_intraday(days=3)
        daily = resample(data.bars, 1440, "Europe/Berlin")
        self.assertEqual(len(daily), 3)
        self.assertEqual(sum(b.volume for b in daily), sum(b.volume for b in data.bars))

    def test_synthetic_data_is_labelled(self) -> None:
        data = synthetic_intraday(days=2)
        self.assertTrue(data.manifest.synthetic)
        self.assertTrue(all(b.source == "synthetic_intraday" for b in data.bars))


if __name__ == "__main__":
    unittest.main()
