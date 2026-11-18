"""What-if accounting for an existing backtest report: costs, starting capital and fixed annual costs.

Only the bookkeeping is recomputed from the report's ``trades.csv``. Signals, entry and exit times and the
sequence of trades stay exactly as in the report; no strategy runs and no market data is read. Therefore a
what-if view adds no attempt to an experiment inventory (spec §8, T-008), unlike changing risk settings,
which would change the trades themselves.

Model (stated in the dashboard):
* Reference prices are recovered from the fill prices with the report's own spread/slippage assumptions,
  then the new spread/slippage is applied per side.
* Fees per order: ``max(notional * fee_bps, min_fee)``. An entry that was closed in several partial exits
  is one order; its fee is split by quantity.
* Starting capital scales every quantity linearly (sizing is a fraction of equity). Quantity steps,
  volume caps and the minimum order value are not re-applied, so this is an approximation for very large
  or very small accounts. Fixed fees (``min_fee``, fixed annual costs) do not scale: that is the effect of
  account size the view is meant to show.
* Fixed annual costs (server, data) are charged pro rata per calendar day of the report period.
* Income that is not part of trades (dividends, financing) is taken from the report and scaled with the
  capital.
* Drawdown is measured on the equity of closed trades (not intra-trade), for baseline and variant alike.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

BPS = 10_000


@dataclass(frozen=True)
class Assumptions:
    capital: float
    fee_bps: float
    min_fee: float
    spread_bps: float
    slippage_bps: float
    server_per_year: float = 0.0
    data_per_year: float = 0.0

    def validate(self) -> None:
        values = asdict(self)
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values.values()):
            raise ValueError("assumptions must be finite numbers")
        if self.capital <= 0 or min(v for k, v in values.items() if k != "capital") < 0:
            raise ValueError("capital must be positive and costs non-negative")
        if self.spread_bps / 2 + self.slippage_bps >= BPS:
            raise ValueError("spread and slippage are unrealistically large")


@dataclass(frozen=True)
class TradeRow:
    strategy: str
    symbol: str
    entry_time: datetime
    exit_time: datetime
    quantity: float
    entry_reference: float
    exit_reference: float
    pnl: float


@dataclass(frozen=True)
class Report:
    name: str
    initial_cash: float
    start: datetime
    end: datetime
    final_equity: float
    trades: list[TradeRow]
    baseline: Assumptions
    other_income: float  # final equity minus capital minus the sum of trade P&L (dividends, financing)
    approximate_profiles: bool


def _impact_bps(profile: dict[str, float], settings: dict[str, Any]) -> float:
    cost = float(settings.get("cost_multiplier", 1.0))
    spread = float(settings.get("spread_multiplier", 1.0))
    return profile["spread_bps"] / 2 * spread * cost + profile["slippage_bps"] * cost


def load_report(directory: str | Path, fallback_profiles: dict[str, dict[str, float]] | None = None) -> Report:
    path = Path(directory)
    summary = json.loads((path / "summary.json").read_text(encoding="utf-8"))
    settings = summary.get("settings") or {}
    profiles = summary.get("cost_profiles")
    approximate = not isinstance(profiles, dict)
    if approximate:
        profiles = fallback_profiles or {}
    initial = float(summary["initial_cash"])
    equity_rows = list(csv.DictReader((path / "equity.csv").open(encoding="utf-8")))
    if not equity_rows:
        raise ValueError("report has no equity curve")
    start = datetime.fromisoformat(equity_rows[0]["timestamp"])
    end = datetime.fromisoformat(equity_rows[-1]["timestamp"])
    final = float(equity_rows[-1]["equity"])
    trades = []
    for row in csv.DictReader((path / "trades.csv").open(encoding="utf-8")):
        if not row["exit_time"]:
            continue
        profile = profiles.get(row["symbol"])
        if profile is None:
            raise ValueError(f"no cost profile for {row['symbol']}")
        impact = _impact_bps(profile, settings) / BPS
        trades.append(
            TradeRow(
                row["strategy"],
                row["symbol"],
                datetime.fromisoformat(row["entry_time"]),
                datetime.fromisoformat(row["exit_time"]),
                float(row["quantity"]),
                float(row["entry_price"]) / (1 + impact),
                float(row["exit_price"]) / (1 - impact),
                float(row["pnl"]),
            )
        )
    trades.sort(key=lambda t: (t.exit_time, t.entry_time, t.symbol))
    first = next(iter(profiles.values()), {"fee_bps": 0.0, "min_fee": 0.0, "spread_bps": 0.0, "slippage_bps": 0.0})
    cost = float(settings.get("cost_multiplier", 1.0))
    baseline = Assumptions(
        initial,
        first["fee_bps"] * cost,
        first["min_fee"] * cost,
        first["spread_bps"] * float(settings.get("spread_multiplier", 1.0)) * cost,
        first["slippage_bps"] * cost,
    )
    return Report(
        path.name,
        initial,
        start,
        end,
        final,
        trades,
        baseline,
        final - initial - sum(t.pnl for t in trades),
        approximate,
    )


def _book(trades: list[TradeRow], a: Assumptions, scale: float) -> list[dict[str, float]]:
    """Net result per trade under the assumptions; entry fees of partially closed entries are shared."""
    entry_size: dict[tuple[str, str, datetime], float] = {}
    for t in trades:
        key = (t.strategy, t.symbol, t.entry_time)
        entry_size[key] = entry_size.get(key, 0.0) + t.quantity
    impact = (a.spread_bps / 2 + a.slippage_bps) / BPS
    output = []
    for t in trades:
        qty = t.quantity * scale
        order_qty = entry_size[(t.strategy, t.symbol, t.entry_time)] * scale
        entry_fee = max(order_qty * t.entry_reference * a.fee_bps / BPS, a.min_fee) * (qty / order_qty)
        exit_fee = max(qty * t.exit_reference * a.fee_bps / BPS, a.min_fee)
        impact_cost = qty * (t.entry_reference + t.exit_reference) * impact
        gross = qty * (t.exit_reference - t.entry_reference)
        output.append(
            {
                "gross": gross,
                "fees": entry_fee + exit_fee,
                "impact": impact_cost,
                "net": gross - entry_fee - exit_fee - impact_cost,
            }
        )
    return output


def _summary(
    report: Report, trades: list[TradeRow], booked: list[dict[str, float]], a: Assumptions, other: float
) -> dict[str, Any]:
    days = max((report.end - report.start).total_seconds() / 86_400, 1.0)
    fixed = (a.server_per_year + a.data_per_year) * days / 365.25
    equity = peak = a.capital
    worst = 0.0
    previous = report.start
    for t, b in zip(trades, booked, strict=True):
        # fixed costs accrue over time; book them up to each exit so the drawdown sees them too
        equity -= (a.server_per_year + a.data_per_year) * (t.exit_time - previous).total_seconds() / 86_400 / 365.25
        previous = max(previous, t.exit_time)
        equity += b["net"]
        peak = max(peak, equity)
        worst = max(worst, 1 - equity / peak if peak > 0 else 1.0)
    gross = sum(b["gross"] for b in booked)
    trading_costs = sum(b["fees"] + b["impact"] for b in booked)
    final = a.capital + gross - trading_costs - fixed + other
    total = final / a.capital - 1
    years = days / 365.25
    return {
        "final_equity": final,
        "total_return": total,
        "annual_return": (final / a.capital) ** (1 / years) - 1 if final > 0 and years >= 0.25 else None,
        "max_drawdown_closed": worst,
        "gross_pnl": gross,
        "fees": sum(b["fees"] for b in booked),
        "spread_slippage": sum(b["impact"] for b in booked),
        "trading_costs": trading_costs,
        "fixed_costs": fixed,
        "other_income": other,
        "trades": len(booked),
        "cost_per_trade": trading_costs / len(booked) if booked else None,
        "costs_share_of_gross": (trading_costs + fixed) / gross if gross > 0 else None,
        "break_even_fixed_per_year": (gross - trading_costs + other) / years if years > 0 else None,
    }


def evaluate(report: Report, assumptions: Assumptions) -> dict[str, Any]:
    assumptions.validate()
    base = replace(report.baseline, server_per_year=0.0, data_per_year=0.0)
    output: dict[str, Any] = {
        "report": report.name,
        "period": [report.start.isoformat(), report.end.isoformat()],
        "baseline_assumptions": asdict(report.baseline),
        "assumptions": asdict(assumptions),
        "approximate_cost_profiles": report.approximate_profiles,
        "report_final_equity": report.final_equity,
        "report_total_return": report.final_equity / report.initial_cash - 1,
    }
    scale = assumptions.capital / report.initial_cash
    groups: dict[str, list[TradeRow]] = {"Gesamt": report.trades}
    for t in report.trades:
        groups.setdefault(t.strategy, []).append(t)
    rows = {}
    for name, trades in groups.items():
        everything = name == "Gesamt"
        # Fixed costs and non-trade income belong to the portfolio; per strategy they are shown as if the
        # strategy ran alone on the full capital (each would carry the full fixed costs).
        other = report.other_income if everything else 0.0
        rows[name] = {
            "baseline": _summary(report, trades, _book(trades, base, 1.0), base, other),
            "variant": _summary(report, trades, _book(trades, assumptions, scale), assumptions, other * scale),
        }
    output["results"] = rows
    output["strategies"] = len(groups) - 1
    return output


def report_names(root: str | Path) -> list[str]:
    base = Path(root)
    if not base.is_dir():
        return []
    return sorted(
        d.name
        for d in base.iterdir()
        if d.is_dir()
        and not d.is_symlink()
        and all((d / f).is_file() for f in ("summary.json", "trades.csv", "equity.csv"))
    )


def evaluate_named(
    root: str | Path,
    name: str,
    values: dict[str, float],
    fallback_profiles: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Entry point for the dashboard: ``name`` must be one of the listed report directories."""
    if name not in report_names(root):
        raise ValueError("unknown report")
    report = load_report(Path(root) / name, fallback_profiles)
    fields = asdict(report.baseline)
    unknown = set(values) - set(fields)
    if unknown:
        raise ValueError(f"unknown assumptions {sorted(unknown)}")
    fields.update({k: float(v) for k, v in values.items()})
    return evaluate(report, Assumptions(**fields))
