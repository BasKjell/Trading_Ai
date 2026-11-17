from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .backtest import BacktestResult


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(type(value).__name__)


def _svg_line(values: list[float], title: str, color: str, percent: bool = False) -> str:
    width, height, margin = 960, 360, 50
    if not values:
        values = [0.0]
    low, high = min(values), max(values)
    if high == low:
        high = low + 1
    points = []
    for index, value in enumerate(values):
        x = margin + index * (width - 2 * margin) / max(1, len(values) - 1)
        y = height - margin - (value - low) * (height - 2 * margin) / (high - low)
        points.append(f"{x:.1f},{y:.1f}")
    suffix = "%" if percent else ""
    scale = 100 if percent else 1
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="100%" height="100%" fill="#101820"/><text x="{margin}" y="28" fill="#f2f5f7" font-family="sans-serif" font-size="19">{title}</text>
<line x1="{margin}" y1="{height - margin}" x2="{width - margin}" y2="{height - margin}" stroke="#607080"/><line x1="{margin}" y1="{margin}" x2="{margin}" y2="{height - margin}" stroke="#607080"/>
<text x="8" y="{margin + 5}" fill="#b8c4cc" font-family="sans-serif" font-size="12">{high * scale:.2f}{suffix}</text><text x="8" y="{height - margin}" fill="#b8c4cc" font-family="sans-serif" font-size="12">{low * scale:.2f}{suffix}</text>
<polyline fill="none" stroke="{color}" stroke-width="2" points="{" ".join(points)}"/></svg>'''


def _period_returns(result: BacktestResult, format_string: str) -> dict[str, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for snapshot in result.snapshots:
        grouped[snapshot.timestamp.strftime(format_string)].append(snapshot.equity)
    return {key: values[-1] / values[0] - 1 for key, values in sorted(grouped.items()) if values[0] > 0}


def _svg_bars(items: dict[str, float], title: str) -> str:
    width, height, margin = 960, 380, 55
    if not items:
        items = {"n/a": 0.0}
    max_abs = max(max(abs(v) for v in items.values()), 0.01)
    zero = height / 2
    bar_width = (width - 2 * margin) / len(items)
    bars: list[str] = []
    for index, (label, value) in enumerate(items.items()):
        x = margin + index * bar_width + bar_width * 0.15
        h = abs(value) / max_abs * (height / 2 - margin)
        y = zero - h if value >= 0 else zero
        color = "#42d392" if value >= 0 else "#ff6b6b"
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width * 0.7:.1f}" height="{h:.1f}" fill="{color}"/><text x="{x:.1f}" y="{height - 15}" fill="#b8c4cc" font-size="11" font-family="sans-serif">{label}</text>'
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="#101820"/><text x="{margin}" y="28" fill="#f2f5f7" font-family="sans-serif" font-size="19">{title}</text><line x1="{margin}" y1="{zero}" x2="{width - margin}" y2="{zero}" stroke="#607080"/>{"".join(bars)}</svg>'''


def write_report(result: BacktestResult, output_dir: str | Path) -> Path:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = {
        "metrics": result.metrics,
        "strategy_pnl": result.strategy_pnl,
        "benchmark": result.benchmark,
        "data_hash": result.data_hash,
        "config_hash": result.config_hash,
        "warnings": result.warnings,
    }
    (output / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=_json_default), encoding="utf-8"
    )
    with (output / "equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "equity", "cash", "gross_exposure", "drawdown"])
        for item in result.snapshots:
            writer.writerow([item.timestamp.isoformat(), item.equity, item.cash, item.gross_exposure, item.drawdown])
    (output / "equity.svg").write_text(
        _svg_line([s.equity for s in result.snapshots], "Equity curve (synthetic fixture)", "#4da3ff"), encoding="utf-8"
    )
    (output / "drawdown.svg").write_text(
        _svg_line([-s.drawdown for s in result.snapshots], "Drawdown", "#ff6b6b", True), encoding="utf-8"
    )
    (output / "monthly_returns.svg").write_text(
        _svg_bars(_period_returns(result, "%Y-%m"), "Monthly returns"), encoding="utf-8"
    )
    (output / "annual_returns.svg").write_text(
        _svg_bars(_period_returns(result, "%Y"), "Annual returns"), encoding="utf-8"
    )
    return output
