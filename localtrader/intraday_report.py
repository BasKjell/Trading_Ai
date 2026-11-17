"""Reports and robustness checks for the intraday engine."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, replace
from pathlib import Path

from .config import AppConfig
from .intraday_data import ImportedDataset
from .intraday_engine import (
    EngineResult,
    InstrumentSpec,
    IntradayEngine,
    buy_and_hold,
    compute_metrics,
    monthly_returns,
)
from .intraday_strategies import IntradayStrategy, build_strategy
from .reporting import _svg_bars, _svg_line

KNOWN_LIMITS = [
    "Modelled costs (spread, slippage, financing) are assumptions, not measured broker costs.",
    "Protective stops are assumed to fill completely at min(open, stop) plus slippage.",
    "Holidays are not modelled separately; days without data produce no signals.",
    "Annualised figures from less than one year of data are unreliable.",
]


def _json_default(value: object) -> object:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def write_intraday_report(
    result: EngineResult,
    output: str | Path,
    benchmark: dict[str, float] | None = None,
    extra: dict[str, object] | None = None,
) -> Path:
    path = Path(output)
    path.mkdir(parents=True, exist_ok=True)
    metrics = compute_metrics(result)
    months = monthly_returns(result)
    summary = {
        "metrics": metrics,
        "monthly_returns": months,
        "benchmark": benchmark or {},
        "parameters": result.parameters,
        "settings": result.settings,
        "datasets": [asdict(m) for m in result.manifests],
        "initial_cash": result.initial_cash,
        "warnings": result.warnings
        + (["Short sample: annualised metrics are not meaningful."] if metrics["short_period_warning"] else []),
        "known_limits": KNOWN_LIMITS,
        **(extra or {}),
    }
    (path / "summary.json").write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")
    with (path / "trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "strategy",
                "symbol",
                "entry_time",
                "entry_price",
                "quantity",
                "exit_time",
                "exit_price",
                "fees",
                "pnl",
                "entry_reason",
                "exit_reason",
            ]
        )
        for t in result.trades:
            writer.writerow(
                [
                    t.strategy,
                    t.symbol,
                    t.entry_time.isoformat(),
                    f"{t.entry_price:.4f}",
                    t.quantity,
                    t.exit_time.isoformat() if t.exit_time else "",
                    f"{t.exit_price:.4f}" if t.exit_price else "",
                    f"{t.fees:.4f}",
                    f"{t.pnl:.4f}" if t.pnl is not None else "",
                    t.entry_reason,
                    t.exit_reason,
                ]
            )
    with (path / "equity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["timestamp", "equity", "cash", "exposure", "drawdown"])
        for p in result.equity:
            writer.writerow(
                [p.timestamp.isoformat(), f"{p.equity:.4f}", f"{p.cash:.4f}", f"{p.exposure:.4f}", f"{p.drawdown:.6f}"]
            )
    with (path / "decisions.jsonl").open("w", encoding="utf-8") as handle:
        for decision in result.decisions:
            handle.write(json.dumps(decision, default=_json_default) + "\n")
    (path / "equity.svg").write_text(
        _svg_line([p.equity for p in result.equity], "Equity", "#42d392"), encoding="utf-8"
    )
    (path / "drawdown.svg").write_text(
        _svg_line([-p.drawdown for p in result.equity], "Drawdown", "#ff6b6b", percent=True), encoding="utf-8"
    )
    (path / "monthly_returns.svg").write_text(_svg_bars(months, "Monthly returns"), encoding="utf-8")
    return path


def strategies_from_config(config: AppConfig, names: list[str]) -> list[IntradayStrategy]:
    return [build_strategy(name, dict(config.raw["strategies"][name])) for name in names]


def _perturb(strategy: IntradayStrategy, factor: float) -> IntradayStrategy:
    params = strategy.parameters()
    changed: dict[str, float] = {}
    for key, value in params.items():
        if key == "max_hold_bars" and value == 0:
            changed[key] = 0
        elif isinstance(value, int) and not isinstance(value, bool):
            changed[key] = max(1, round(value * factor))
        else:
            changed[key] = value * factor
    return build_strategy(strategy.name, changed)


def run_scenarios(
    config: AppConfig,
    instruments: dict[str, InstrumentSpec],
    strategy_names: list[str],
    datasets: dict[str, ImportedDataset],
) -> dict[str, dict[str, object]]:
    """Baseline plus the robustness checks required by the project spec (section 7)."""
    base = strategies_from_config(config, strategy_names)
    scenarios: dict[str, tuple[list[IntradayStrategy], dict[str, float], dict[str, ImportedDataset]]] = {
        "baseline": (base, {}, datasets),
        "costs_2x": (base, {"cost_multiplier": 2.0}, datasets),
        "spread_3x": (base, {"spread_multiplier": 3.0}, datasets),
        "delay_1_bar": (base, {"extra_delay_bars": 1}, datasets),
        "params_minus_20pct": ([_perturb(s, 0.8) for s in base], {}, datasets),
        "params_plus_20pct": ([_perturb(s, 1.2) for s in base], {}, datasets),
    }
    for index, (label, part) in enumerate(_phases(datasets)):
        scenarios[f"phase_{index + 1}_{label}"] = (base, {}, part)
    output: dict[str, dict[str, object]] = {}
    for name, (strategies, settings, data) in scenarios.items():
        engine = IntradayEngine(
            config,
            instruments,
            strategies,
            cost_multiplier=settings.get("cost_multiplier", 1.0),
            spread_multiplier=settings.get("spread_multiplier", 1.0),
            extra_delay_bars=int(settings.get("extra_delay_bars", 0)),
        )
        result = engine.run(data)
        metrics = compute_metrics(result)
        output[name] = {
            "total_return": metrics["total_return"],
            "geometric_monthly_return": metrics["geometric_monthly_return"],
            "max_drawdown": metrics["max_drawdown"],
            "trades": metrics["trades"],
            "total_costs": metrics["total_costs"],
            "benchmark": buy_and_hold(data, instruments, config, settings.get("cost_multiplier", 1.0)),
        }
    return output


def _phases(datasets: dict[str, ImportedDataset], parts: int = 3) -> list[tuple[str, dict[str, ImportedDataset]]]:
    """Split the sample chronologically into equal parts (different market phases)."""
    output: list[tuple[str, dict[str, ImportedDataset]]] = []
    for part in range(parts):
        sliced: dict[str, ImportedDataset] = {}
        label = ""
        for symbol, data in datasets.items():
            n = len(data.bars)
            lo, hi = n * part // parts, n * (part + 1) // parts
            bars = data.bars[lo:hi]
            if not bars:
                continue
            label = f"{bars[0].start.date()}_{bars[-1].start.date()}"
            sliced[symbol] = replace(data, bars=bars)
        if sliced:
            output.append((label, sliced))
    return output
