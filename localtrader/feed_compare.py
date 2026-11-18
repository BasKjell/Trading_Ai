"""Compare two 15-minute bar feeds of the same instrument (T-012 feed decision: IEX vs SIP).

Only data properties are measured: coverage, price and volume differences, agreement of bar directions and
of a generic breakout flag. No strategy is run and no return of any strategy is computed, so the measurement
adds no attempt to the T-008 experiment inventory. Research files end before the holdout; the comparison
never reads anything else.
"""

from __future__ import annotations

import csv
import math
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

Row = tuple[float, float, float, float, float]  # open, high, low, close, volume


def read_bars(path: str | Path) -> dict[datetime, Row]:
    bars: dict[datetime, Row] = {}
    with Path(path).open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            start = datetime.fromisoformat(row["timestamp"])
            if start.tzinfo is None:
                raise ValueError(f"{path}: timestamps must be timezone-aware")
            values = tuple(float(row[k]) for k in ("open", "high", "low", "close", "volume"))
            bars[start] = values  # type: ignore[assignment]
    return bars


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"median": None, "p95": None, "p99": None, "max": None}
    ordered = sorted(values)

    def rank(p: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, math.ceil(p * len(ordered)) - 1))]

    return {"median": median(ordered), "p95": rank(0.95), "p99": rank(0.99), "max": ordered[-1]}


def _bps(a: float, b: float) -> float:
    return abs(a / b - 1) * 10_000


def _breakouts(starts: list[datetime], bars: dict[datetime, Row], window: int) -> dict[datetime, bool]:
    """Close above the highest high of the previous ``window`` bars (generic feature, no trading rule)."""
    flags = {}
    for index in range(window, len(starts)):
        previous = max(bars[t][1] for t in starts[index - window : index])
        flags[starts[index]] = bars[starts[index]][3] > previous
    return flags


def compare(reference: dict[datetime, Row], candidate: dict[datetime, Row], window: int = 16) -> dict[str, Any]:
    """``reference`` (e.g. SIP) against ``candidate`` (e.g. IEX), restricted to the candidate's time span."""
    if not candidate:
        raise ValueError("candidate feed is empty")
    first, last = min(candidate), max(candidate)
    span = sorted(t for t in reference if first <= t <= last)
    common = [t for t in span if t in candidate]
    if not common:
        raise ValueError("no common bars")
    diff = {
        name: [_bps(candidate[t][i], reference[t][i]) for t in common]
        for i, name in enumerate(("open", "high", "low", "close"))
    }
    volume_share = [candidate[t][4] / reference[t][4] for t in common if reference[t][4] > 0]
    direction_ok = 0
    direction_n = 0
    for previous, current in zip(common, common[1:], strict=False):
        r = reference[current][3] - reference[previous][3]
        c = candidate[current][3] - candidate[previous][3]
        if r != 0 and c != 0:
            direction_n += 1
            direction_ok += (r > 0) == (c > 0)
    ref_flags = _breakouts(common, reference, window)
    cand_flags = _breakouts(common, candidate, window)
    both = sum(1 for t in ref_flags if ref_flags[t] and cand_flags[t])
    either = sum(1 for t in ref_flags if ref_flags[t] or cand_flags[t])
    years: dict[str, list[float]] = {}
    for t, value in zip(common, diff["close"], strict=True):
        years.setdefault(str(t.year), []).append(value)
    return {
        "span": [first.isoformat(), last.isoformat()],
        "reference_bars_in_span": len(span),
        "candidate_bars": len(candidate),
        "common_bars": len(common),
        "coverage": len(common) / len(span),
        "missing_in_candidate": len(span) - len(common),
        "abs_diff_bps": {name: _quantiles(values) for name, values in diff.items()},
        "close_diff_bps_median_by_year": {y: median(v) for y, v in sorted(years.items())},
        "volume_share": _quantiles(volume_share),
        "same_direction_share": direction_ok / direction_n if direction_n else None,
        f"breakout{window}_reference": sum(ref_flags.values()),
        f"breakout{window}_candidate": sum(cand_flags.values()),
        f"breakout{window}_jaccard": both / either if either else None,
    }


def compare_files(reference: str | Path, candidate: str | Path, window: int = 16) -> dict[str, Any]:
    return compare(read_bars(reference), read_bars(candidate), window)


def summary(results: Iterable[tuple[str, dict[str, Any]]]) -> str:
    lines = [
        "| Symbol | Abdeckung | Close-Abw. Median / p95 / p99 (bp) | High / Low p95 (bp) | IEX-Volumenanteil (Median) | gleiche Richtung | Ausbruch-Jaccard |",
        "|---|---|---|---|---|---|---|",
    ]
    for symbol, r in results:
        close = r["abs_diff_bps"]["close"]
        jaccard = next(v for k, v in r.items() if k.endswith("_jaccard"))
        lines.append(
            f"| {symbol} | {r['coverage']:.2%} | {close['median']:.2f} / {close['p95']:.2f} / {close['p99']:.2f} | "
            f"{r['abs_diff_bps']['high']['p95']:.2f} / {r['abs_diff_bps']['low']['p95']:.2f} | "
            f"{r['volume_share']['median']:.2%} | {r['same_direction_share']:.1%} | {jaccard:.2f} |"
        )
    return "\n".join(lines)
