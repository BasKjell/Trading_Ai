from __future__ import annotations

import io
import json
import tempfile
import unittest
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from localtrader.fx_rates import (
    FxDataError,
    FxRate,
    FxSeries,
    StaleRate,
    available_at,
    fetch_ecb_csv,
    parse_ecb_csv,
    read_fx_file,
    write_fx_file,
)

HEADER = "KEY,FREQ,CURRENCY,CURRENCY_DENOM,EXR_TYPE,EXR_SUFFIX,TIME_PERIOD,OBS_VALUE,OBS_STATUS\n"


def row(day: str, value: str, status: str = "A", key: str = "EXR.D.USD.EUR.SP00.A") -> str:
    return f"{key},D,USD,EUR,SP00,A,{day},{value},{status}\n"


SAMPLE = HEADER + "".join(
    row(d, v)
    for d, v in [
        ("2025-10-27", "1.164"),
        ("2025-10-28", "1.163"),
        ("2025-10-29", "1.1636"),
        ("2025-10-30", "1.155"),
        ("2025-10-31", "1.154"),
        ("2025-11-03", "1.1534"),
    ]
)


class ParseTests(unittest.TestCase):
    def test_parses_and_marks_publication_time(self) -> None:
        series, report = parse_ecb_csv(SAMPLE)
        self.assertEqual(report["rates_accepted"], 6)
        self.assertEqual(series[0].available_at, datetime(2025, 10, 27, 15, 0, tzinfo=UTC))  # 16:00 CET
        self.assertEqual(available_at(date(2025, 7, 1)), datetime(2025, 7, 1, 14, 0, tzinfo=UTC))  # CEST

    def test_invalid_rows_are_rejected_and_conflicts_abort(self) -> None:
        text = (
            HEADER
            + row("2025-10-27", "1.164")
            + row("2025-10-28", "nan")
            + row("2025-10-29", "9.0")
            + row("2025-10-30", "1.15", status="P")
            + row("2025-10-31", "1.15", key="EXR.D.GBP.EUR.SP00.A")
            + row("2025-10-27", "1.164")
            + row("x", "1.1")
        )
        series, report = parse_ecb_csv(text)
        self.assertEqual([r.day for r in series], [date(2025, 10, 27)])
        self.assertEqual(
            report["rejected"],
            {"implausible_value": 2, "not_final": 1, "other_series": 1, "duplicate": 1, "unparseable": 1},
        )
        with self.assertRaises(FxDataError):
            parse_ecb_csv(HEADER + row("2025-10-27", "1.164") + row("2025-10-27", "1.2"))

    def test_jumps_and_gaps_are_reported(self) -> None:
        _, report = parse_ecb_csv(HEADER + row("2025-10-01", "1.10") + row("2025-10-20", "1.20"))
        self.assertEqual(report["gaps"], ["2025-10-01..2025-10-20"])
        self.assertEqual(len(report["jumps"]), 1)


class SeriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.series = FxSeries(parse_ecb_csv(SAMPLE)[0])

    def test_rate_is_only_used_after_publication(self) -> None:
        before = datetime(2025, 10, 28, 14, 59, tzinfo=UTC)  # 15:59 CET on the 28th
        after = datetime(2025, 10, 28, 15, 0, tzinfo=UTC)
        self.assertEqual(self.series.rate_at(before).day, date(2025, 10, 27))
        self.assertEqual(self.series.rate_at(after).day, date(2025, 10, 28))

    def test_weekend_uses_friday_and_stale_or_missing_rates_fail(self) -> None:
        self.assertEqual(self.series.rate_at(datetime(2025, 11, 2, 12, tzinfo=UTC)).day, date(2025, 10, 31))
        with self.assertRaises(StaleRate):
            self.series.rate_at(datetime(2025, 11, 20, tzinfo=UTC))
        with self.assertRaises(StaleRate):
            self.series.rate_at(datetime(2025, 10, 27, 10, tzinfo=UTC))
        with self.assertRaises(ValueError):
            self.series.rate_at(datetime(2025, 10, 28))

    def test_conversion(self) -> None:
        moment = datetime(2025, 10, 29, 20, tzinfo=UTC)
        self.assertAlmostEqual(self.series.usd_to_eur(1163.6, moment), 1000.0)

    def test_invalid_series_is_rejected(self) -> None:
        good = FxRate(date(2025, 1, 2), 1.0, available_at(date(2025, 1, 2)))
        with self.assertRaises(FxDataError):
            FxSeries([good, good])
        with self.assertRaises(FxDataError):
            FxSeries([FxRate(date(2025, 1, 3), -1.0, available_at(date(2025, 1, 3)))])


class FileTests(unittest.TestCase):
    def test_round_trip_manifest_and_holdout_cut(self) -> None:
        series, report = parse_ecb_csv(SAMPLE)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "EURUSD_ecb.csv"
            manifest = write_fx_file(series, path, report, "https://example")
            stored = json.loads(Path(str(path) + ".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["sha256"], manifest["sha256"])
            full = read_fx_file(path)
            cut = read_fx_file(path, datetime(2025, 11, 1, tzinfo=UTC))
            self.assertEqual(len(full.rates), 6)
            self.assertEqual(cut.rates[-1].day, date(2025, 10, 31))
            self.assertNotEqual(full.digest(), cut.digest())
            text = path.read_text(encoding="utf-8").replace("2025-10-31T15:00:00+00:00", "2025-10-31T09:00:00+00:00")
            path.write_text(text, encoding="utf-8")
            with self.assertRaises(FxDataError):  # earlier availability than published: look-ahead
                read_fx_file(path)
            with self.assertRaises(ValueError):
                read_fx_file(path, datetime(2025, 11, 1))

    def test_fetch_builds_the_ecb_request(self) -> None:
        seen: list[Any] = []

        class Response(io.BytesIO):
            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

        def opener(request: Any) -> Response:
            seen.append(request.full_url)
            return Response(SAMPLE.encode())

        text = fetch_ecb_csv(date(2025, 10, 27), date(2025, 11, 3) + timedelta(days=0), opener)
        self.assertIn("EXR/D.USD.EUR.SP00.A", seen[0])
        self.assertIn("startPeriod=2025-10-27", seen[0])
        self.assertEqual(text, SAMPLE)
