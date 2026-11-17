from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from localtrader.cli import _load_intraday
from localtrader.domain import Bar, Market
from localtrader.intraday_data import CsvImportSpec, import_csv, split_holdout
from localtrader.intraday_fetch import US_REGULAR_SESSION, default_end, fetch_intraday_csv, regular_session_bars

REPO = Path(__file__).resolve().parent.parent


def bar(symbol: str, start: datetime, price: float = 100.0) -> Bar:
    end = start + timedelta(minutes=15)
    return Bar(
        symbol, Market.EQUITY, "15Min", start, end, price, price + 1, price - 1, price + 0.5, 1000.0, "alpaca_sip", end
    )


class FakeDownloader:
    def __init__(self, bars: list[Bar]) -> None:
        self.bars = bars
        self.requests: list[Any] = []

    def download(self, request: Any, progress: Any = None) -> tuple[list[Bar], dict[str, Any], Path]:
        self.requests.append(request)
        return self.bars, {"request_id": "abc", "downloaded_at": "2026-11-17T00:00:00+00:00"}, Path("x")


# 2024-03-05 is a Tuesday; New York is UTC-5 (before the DST switch on 2024-03-10).
DAY = datetime(2024, 3, 5, tzinfo=UTC)
PRE_MARKET = DAY.replace(hour=13, minute=0)  # 08:00 New York
OPEN = DAY.replace(hour=14, minute=30)  # 09:30 New York
LAST = DAY.replace(hour=20, minute=45)  # 15:45 New York
AFTER = DAY.replace(hour=21, minute=0)  # 16:00 New York, after-hours


class FetchTests(unittest.TestCase):
    def test_only_regular_session_bars_are_kept(self) -> None:
        kept = regular_session_bars([bar("SPY", t) for t in (PRE_MARKET, OPEN, LAST, AFTER)])
        self.assertEqual([b.start for b in kept], [OPEN, LAST])

    def test_session_follows_daylight_saving_time(self) -> None:
        summer_open = datetime(2024, 7, 2, 13, 30, tzinfo=UTC)  # 09:30 New York in summer
        self.assertEqual(len(regular_session_bars([bar("SPY", summer_open)])), 1)
        self.assertTrue(US_REGULAR_SESSION.contains(summer_open))

    def test_csv_round_trip_through_import_with_manifest(self) -> None:
        source = [bar("SPY", t, 100 + i) for i, t in enumerate((PRE_MARKET, OPEN, OPEN + timedelta(minutes=15)))]
        source.append(bar("QQQ", OPEN))  # other symbols in the response are ignored
        downloader = FakeDownloader(source)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "SPY_15m.csv"
            summary = fetch_intraday_csv(downloader, "SPY", DAY, DAY + timedelta(days=1), path)
            self.assertEqual(downloader.requests[0].feed, "sip")
            self.assertEqual(downloader.requests[0].timeframe, "15Min")
            self.assertEqual(summary["bars_regular_session"], 2)
            manifest = json.loads(Path(str(path) + ".manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["csv_sha256"], summary["csv_sha256"])
            data = import_csv(
                CsvImportSpec(str(path), "SPY", Market.ETF, "test", "alpaca_sip", "UTC"), US_REGULAR_SESSION
            )
        self.assertEqual([b.start for b in data.bars], [OPEN, OPEN + timedelta(minutes=15)])
        self.assertEqual([b.open for b in data.bars], [101.0, 102.0])
        self.assertEqual(data.bars[0].received_at, data.bars[0].end)
        self.assertEqual(data.manifest.quality["rejected"], {})

    def test_default_end_is_old_enough_and_aligned(self) -> None:
        now = datetime(2026, 11, 17, 19, 52, 13, tzinfo=UTC)
        end = default_end(now)
        self.assertLessEqual(end, now - timedelta(minutes=15))
        self.assertEqual((end.minute % 15, end.second, end.microsecond), (0, 0, 0))


class HoldoutTests(unittest.TestCase):
    def _csv(self, tmp: str, starts: list[datetime]) -> Path:
        path = Path(tmp) / "SPY_15m.csv"
        rows = ["timestamp,open,high,low,close,volume"]
        rows += [f"{s.isoformat()},100,101,99,100.5,1000" for s in starts]
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return path

    def test_split_removes_holdout_bars_and_their_gap_marks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            starts = [OPEN, OPEN + timedelta(minutes=15), OPEN + timedelta(minutes=45)]
            data = import_csv(
                CsvImportSpec(str(self._csv(tmp, starts)), "SPY", Market.ETF, "t", "s", "UTC"), US_REGULAR_SESSION
            )
        self.assertEqual(data.after_gap, frozenset({starts[2]}))
        research, removed = split_holdout(data, starts[2])
        self.assertEqual(removed, 1)
        self.assertEqual([b.start for b in research.bars], starts[:2])
        self.assertEqual(research.after_gap, frozenset())
        with self.assertRaises(ValueError):
            split_holdout(data, datetime(2024, 1, 1))

    def test_research_runs_exclude_the_configured_holdout(self) -> None:
        before = datetime(2025, 10, 31, 14, 30, tzinfo=UTC)
        inside = datetime(2025, 11, 3, 14, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp:
            path = str(self._csv(tmp, [before, inside]))
            config = str(REPO / "config" / "default.toml")
            _, _, datasets, holdout = _load_intraday(config, path, None, "SPY")
            self.assertEqual([b.start for b in datasets["SPY"].bars], [before])
            self.assertEqual(holdout["excluded_bars"], 1)
            self.assertFalse(holdout["used"])
            _, _, datasets, holdout = _load_intraday(config, path, None, "SPY", use_holdout=True)
            self.assertEqual(len(datasets["SPY"].bars), 2)
            self.assertTrue(holdout["used"])
