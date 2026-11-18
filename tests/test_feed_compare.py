from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from localtrader.feed_compare import compare, compare_files, summary


def feed(n: int, shift: float = 0.0, volume: float = 1000.0, skip: frozenset[int] = frozenset()):
    start = datetime(2024, 3, 4, 14, 30, tzinfo=UTC)
    rows = {}
    for i in range(n):
        if i in skip:
            continue
        price = 100 + (i % 7) - (i % 3) + i * 0.1 + shift
        rows[start + timedelta(minutes=15 * i)] = (price, price + 0.5, price - 0.5, price + 0.2, volume)
    return rows


class FeedCompareTests(unittest.TestCase):
    def test_identical_feeds_agree_completely(self) -> None:
        result = compare(feed(80), feed(80))
        self.assertEqual(result["coverage"], 1.0)
        self.assertEqual(result["abs_diff_bps"]["close"]["max"], 0.0)
        self.assertEqual(result["same_direction_share"], 1.0)
        self.assertEqual(result["breakout16_jaccard"], 1.0)

    def test_gaps_offsets_and_volume_share_are_measured(self) -> None:
        result = compare(feed(80), feed(80, shift=0.01, volume=20.0, skip=frozenset({10, 11})))
        self.assertEqual(result["missing_in_candidate"], 2)
        self.assertAlmostEqual(result["coverage"], 78 / 80)
        self.assertAlmostEqual(result["volume_share"]["median"], 0.02)
        self.assertGreater(result["abs_diff_bps"]["close"]["median"], 0.5)
        self.assertLess(result["abs_diff_bps"]["close"]["median"], 1.2)

    def test_reference_outside_the_candidate_span_is_ignored(self) -> None:
        reference = feed(100)
        candidate = {t: v for t, v in feed(100).items() if t >= min(reference) + timedelta(hours=5)}
        self.assertEqual(compare(reference, candidate)["coverage"], 1.0)

    def test_files_and_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = []
            for name, rows in (("sip", feed(60)), ("iex", feed(60, shift=0.02))):
                path = Path(tmp) / f"{name}.csv"
                lines = ["timestamp,open,high,low,close,volume"]
                lines += [f"{t.isoformat()},{o},{h},{lo},{c},{v}" for t, (o, h, lo, c, v) in rows.items()]
                path.write_text("\n".join(lines) + "\n")
                paths.append(path)
            result = compare_files(*paths)
        self.assertIn("| SPY |", summary([("SPY", result)]))
        with self.assertRaises(ValueError):
            compare(feed(10), {})
