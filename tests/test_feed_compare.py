from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from localtrader.feed_compare import compare, compare_files, read_bars, summary
from localtrader.holdout import HOLDOUT_START, HoldoutError


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


def write_rows(path: Path, rows: list[tuple[datetime, str, str, str, str, str]]) -> None:
    lines = ["timestamp,open,high,low,close,volume"] + [",".join([t.isoformat(), *v]) for t, *v in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class HoldoutBoundaryTests(unittest.TestCase):
    """Review #40 P1: no metric, span or row of the holdout may leave the comparison."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        step = timedelta(minutes=15)
        self.before = [HOLDOUT_START - step * (40 - i) for i in range(40)]  # last one ends exactly at the start
        self.after = [HOLDOUT_START + step * i for i in range(20)]  # starts at or after the holdout start
        for name, after_price in (("sip", "100"), ("iex", "120")):  # 2,000 bp apart inside the holdout only
            folder = self.dir / name
            folder.mkdir()
            rows = [(t, "100", "100.5", "99.5", "100", "1000") for t in self.before]
            rows += [(t, after_price, after_price, after_price, after_price, "1000") for t in self.after]
            write_rows(folder / "SPY_15m.csv", rows)

    def test_reader_keeps_rows_up_to_the_boundary_and_drops_the_holdout_before_parsing(self) -> None:
        path = self.dir / "mixed.csv"
        write_rows(
            path,
            [
                (self.before[-1], "1", "1", "1", "1", "1"),  # ends exactly at the holdout start: kept
                (HOLDOUT_START, "not-a-price", "x", "x", "x", "x"),  # would raise if it were parsed
                (HOLDOUT_START + timedelta(days=200), "x", "x", "x", "x", "x"),
            ],
        )
        self.assertEqual(list(read_bars(path)), [self.before[-1]])
        with self.assertRaises(HoldoutError):
            read_bars(path, HOLDOUT_START + timedelta(minutes=15))

    def test_both_cli_output_forms_contain_no_holdout_information(self) -> None:
        import contextlib
        import io
        import json

        from localtrader.cli import build_parser

        for extra in ([], ["--json"]):
            with self.subTest(extra=extra):
                out = io.StringIO()
                args = build_parser().parse_args(
                    ["compare-feeds", "--reference-dir", str(self.dir / "sip"), "--candidate-dir"]
                    + [str(self.dir / "iex"), "--symbols", "SPY", *extra]
                )
                with contextlib.redirect_stdout(out):
                    self.assertEqual(args.func(args), 0)
                text = out.getvalue()
                self.assertIn("| SPY | 100.00% | 0.00 / 0.00 / 0.00 |", text)
                for moment in self.after:
                    self.assertNotIn(moment.isoformat(), text)
                if extra:
                    result = json.loads(text[text.index("{") :])["SPY"]
                    self.assertEqual(result["span"], [self.before[0].isoformat(), self.before[-1].isoformat()])
                    self.assertEqual(result["candidate_bars"], len(self.before))
                    self.assertEqual(result["abs_diff_bps"]["close"]["max"], 0.0)

    def test_files_entirely_inside_the_holdout_yield_no_metrics(self) -> None:
        only = self.dir / "only.csv"
        write_rows(only, [(t, "1", "1", "1", "1", "1") for t in self.after])
        with self.assertRaises(ValueError):
            compare_files(only, only)
