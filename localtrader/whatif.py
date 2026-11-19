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
* Drawdown is measured on the equity of closed trades (not intra-trade) including fixed costs accrued up to
  every trade exit and up to the end of the period, for baseline and variant alike.
* Each instrument keeps its own cost profile from the report; a slider overrides only the field it sets.
* Only research reports are admitted: ``holdout.used`` must be explicitly false, every dataset must end
  before the holdout start (checked in ``summary.json`` before trades or equity are read), and no equity
  point, trade entry or trade exit may lie at or after it. Report files must be regular files inside the report directory.
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .holdout import HOLDOUT_START

BPS = 10_000


COST_FIELDS = ("fee_bps", "min_fee", "spread_bps", "slippage_bps")
BAR = timedelta(minutes=15)


class ReportRefused(ValueError):
    pass


@dataclass(frozen=True)
class Assumptions:
    """Account-level values plus the cost fields the user changed; other fields keep each instrument's profile."""

    capital: float
    server_per_year: float = 0.0
    data_per_year: float = 0.0
    overrides: tuple[tuple[str, float], ...] = ()

    def validate(self) -> None:
        values = [self.capital, self.server_per_year, self.data_per_year, *(v for _, v in self.overrides)]
        if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values):
            raise ValueError("assumptions must be finite numbers")
        if self.capital <= 0 or min(values[1:]) < 0:
            raise ValueError("capital must be positive and costs non-negative")
        unknown = {k for k, _ in self.overrides} - set(COST_FIELDS)
        if unknown:
            raise ValueError(f"unknown cost fields {sorted(unknown)}")

    def costs_for(self, profile: dict[str, float]) -> dict[str, float]:
        merged = {**profile, **dict(self.overrides)}
        if merged["spread_bps"] / 2 + merged["slippage_bps"] >= BPS:
            raise ValueError("spread and slippage are unrealistically large")
        return merged


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
    profiles: dict[str, dict[str, float]]  # effective per instrument (report multipliers applied)
    other_income: float  # final equity minus capital minus the sum of trade P&L (dividends, financing)
    approximate_profiles: bool


def _file(directory: Path, name: str) -> Path:
    path = directory / name
    if path.is_symlink() or not path.is_file() or path.resolve().parent != directory.resolve():
        raise ReportRefused(f"{name} must be a regular file inside the report directory")
    return path


def admit(directory: str | Path) -> dict[str, Any]:
    """Read only ``summary.json`` and refuse anything that is not an explicit pre-holdout research report."""
    path = Path(directory)
    if path.is_symlink() or not path.is_dir():
        raise ReportRefused("report must be a directory")
    for name in ("trades.csv", "equity.csv"):
        _file(path, name)
    summary = json.loads(_file(path, "summary.json").read_text(encoding="utf-8"))
    if not isinstance(summary, dict):
        raise ReportRefused("summary must be an object")
    holdout = summary.get("holdout")
    if not isinstance(holdout, dict) or holdout.get("used") is not False:
        raise ReportRefused("only reports explicitly marked holdout.used=false are shown")
    datasets = summary.get("datasets")
    if not isinstance(datasets, list) or not datasets:
        raise ReportRefused("report lists no datasets")
    for dataset in datasets:
        quality = dataset.get("quality") if isinstance(dataset, dict) else None
        last = quality.get("last_bar") if isinstance(quality, dict) else None
        if last is None and dataset.get("synthetic") is True:
            continue  # generated data holds no market prices; trades and equity are still checked below
        if not isinstance(last, str):
            raise ReportRefused("dataset end unknown")
        moment = datetime.fromisoformat(last)
        if moment.tzinfo is None or moment + BAR > HOLDOUT_START:
            raise ReportRefused("dataset reaches into the holdout")
    # only after the summary passed: every equity point and every trade entry/exit must be research time
    with _file(path, "equity.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            _research_time(row.get("timestamp") or "", "equity curve")
    with _file(path, "trades.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            _research_time(row.get("entry_time") or "", "trade entry")
            if row.get("exit_time"):
                _research_time(row["exit_time"], "trade exit")
    return summary


def _research_time(value: str, what: str) -> datetime:
    """Parse a report timestamp; anything at or after the holdout start (or without zone) refuses the report."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ReportRefused(f"{what}: unreadable timestamp") from None
    if moment.tzinfo is None or moment >= HOLDOUT_START:
        raise ReportRefused(f"{what} reaches into the holdout")
    return moment


def _impact_bps(profile: dict[str, float]) -> float:
    return profile["spread_bps"] / 2 + profile["slippage_bps"]


def load_report(directory: str | Path, fallback_profiles: dict[str, dict[str, float]] | None = None) -> Report:
    path = Path(directory)
    summary = admit(path)
    settings = summary.get("settings") or {}
    stored = summary.get("cost_profiles")
    approximate = not isinstance(stored, dict)
    raw_profiles: dict[str, Any] = stored if isinstance(stored, dict) else dict(fallback_profiles or {})
    cost = float(settings.get("cost_multiplier", 1.0))
    spread = float(settings.get("spread_multiplier", 1.0))
    profiles = {
        symbol: {
            "fee_bps": float(p["fee_bps"]) * cost,
            "min_fee": float(p["min_fee"]) * cost,
            "spread_bps": float(p["spread_bps"]) * spread * cost,
            "slippage_bps": float(p["slippage_bps"]) * cost,
        }
        for symbol, p in raw_profiles.items()
    }
    initial = float(summary["initial_cash"])
    with _file(path, "equity.csv").open(encoding="utf-8") as handle:
        equity_rows = list(csv.DictReader(handle))
    if not equity_rows:
        raise ValueError("report has no equity curve")
    # every point, not only the ends: an unordered or tampered curve must not carry a holdout timestamp
    moments = [_research_time(row["timestamp"], "equity curve") for row in equity_rows]
    start, end = min(moments), max(moments)
    if moments != sorted(moments):
        raise ReportRefused("equity curve is not in time order")
    final = float(equity_rows[-1]["equity"])
    trades = []
    with _file(path, "trades.csv").open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not row["exit_time"]:
                continue
            profile = profiles.get(row["symbol"])
            if profile is None:
                raise ValueError(f"no cost profile for {row['symbol']}")
            impact = _impact_bps(profile) / BPS
            entry = _research_time(row["entry_time"], "trade entry")
            leave = _research_time(row["exit_time"], "trade exit")
            if not start <= entry <= leave <= end:
                raise ReportRefused("trade lies outside the report period")
            trade = TradeRow(
                row["strategy"],
                row["symbol"],
                entry,
                leave,
                float(row["quantity"]),
                float(row["entry_price"]) / (1 + impact),
                float(row["exit_price"]) / (1 - impact),
                float(row["pnl"]),
            )
            trades.append(trade)
    trades.sort(key=lambda t: (t.exit_time, t.entry_time, t.symbol))
    return Report(
        path.name,
        initial,
        start,
        end,
        final,
        trades,
        profiles,
        final - initial - sum(t.pnl for t in trades),
        approximate,
    )


def _book(report: Report, trades: list[TradeRow], a: Assumptions, scale: float) -> list[dict[str, float]]:
    """Net result per trade under the assumptions; entry fees of partially closed entries are shared."""
    entry_size: dict[tuple[str, str, datetime], float] = {}
    for t in trades:
        key = (t.strategy, t.symbol, t.entry_time)
        entry_size[key] = entry_size.get(key, 0.0) + t.quantity
    costs = {symbol: a.costs_for(profile) for symbol, profile in report.profiles.items()}
    output = []
    for t in trades:
        c = costs[t.symbol]
        impact = _impact_bps(c) / BPS
        qty = t.quantity * scale
        order_qty = entry_size[(t.strategy, t.symbol, t.entry_time)] * scale
        entry_fee = max(order_qty * t.entry_reference * c["fee_bps"] / BPS, c["min_fee"]) * (qty / order_qty)
        exit_fee = max(qty * t.exit_reference * c["fee_bps"] / BPS, c["min_fee"])
        impact_cost = qty * (t.entry_reference + t.exit_reference) * impact
        gross = qty * (t.exit_reference - t.entry_reference)
        output.append(
            {
                "time": t.exit_time.timestamp(),
                "gross": gross,
                "fees": entry_fee + exit_fee,
                "impact": impact_cost,
                "net": gross - entry_fee - exit_fee - impact_cost,
            }
        )
    return output


def _summary(report: Report, booked: list[dict[str, float]], a: Assumptions, other: float) -> dict[str, Any]:
    seconds_per_year = 365.25 * 86_400
    per_second = (a.server_per_year + a.data_per_year) / seconds_per_year
    days = max((report.end - report.start).total_seconds() / 86_400, 1.0)
    years = days / 365.25
    fixed = (a.server_per_year + a.data_per_year) * years
    equity = peak = a.capital
    worst = 0.0
    previous = report.start.timestamp()

    def observe(value: float) -> None:
        nonlocal peak, worst
        peak = max(peak, value)
        worst = max(worst, 1 - value / peak if peak > 0 else 1.0)

    for b in booked:
        # fixed costs accrue continuously: the drop just before an exit counts as well
        equity -= per_second * max(0.0, b["time"] - previous)
        previous = max(previous, b["time"])
        observe(equity)
        equity += b["net"]
        observe(equity)
    # the remaining period after the last exit (or the whole period without trades)
    equity -= fixed - per_second * (previous - report.start.timestamp())
    observe(equity)
    equity += other
    observe(equity)
    gross = sum(b["gross"] for b in booked)
    trading_costs = sum(b["fees"] + b["impact"] for b in booked)
    final = a.capital + gross - trading_costs - fixed + other
    return {
        "final_equity": final,
        "total_return": final / a.capital - 1,
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
    base = Assumptions(report.initial_cash)
    first = next(iter(report.profiles.values()), dict.fromkeys(COST_FIELDS, 0.0))
    output: dict[str, Any] = {
        "report": report.name,
        "period": [report.start.isoformat(), report.end.isoformat()],
        # slider start values; with differing instruments a slider sets the value for all of them
        "baseline_assumptions": {"capital": report.initial_cash, "server_per_year": 0.0, "data_per_year": 0.0, **first},
        "cost_profiles": report.profiles,
        "profiles_differ": any(p != first for p in report.profiles.values()),
        "assumptions": {**asdict(assumptions), "overrides": dict(assumptions.overrides)},
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
        # Fixed costs and non-trade income belong to the portfolio; per strategy they are shown as if the
        # strategy ran alone on the full capital (each would carry the full fixed costs).
        other = report.other_income if name == "Gesamt" else 0.0
        rows[name] = {
            "baseline": _summary(report, _book(report, trades, base, 1.0), base, other),
            "variant": _summary(report, _book(report, trades, assumptions, scale), assumptions, other * scale),
        }
    output["results"] = rows
    output["strategies"] = len(groups) - 1
    return output


def report_names(root: str | Path) -> list[str]:
    """Admissible research reports only (see ``admit``); refused directories are not listed."""
    base = Path(root)
    if not base.is_dir():
        return []
    names = []
    for directory in base.iterdir():
        try:
            admit(directory)
        except (ReportRefused, OSError, ValueError, TypeError, AttributeError):
            continue
        names.append(directory.name)
    return sorted(names)


def evaluate_named(
    root: str | Path,
    name: str,
    values: dict[str, float],
    fallback_profiles: dict[str, dict[str, float]] | None = None,
) -> dict[str, Any]:
    """Entry point for the dashboard: ``name`` must be one of the admitted report directories."""
    if name not in report_names(root):
        raise ValueError("unknown or not admitted report")
    report = load_report(Path(root) / name, fallback_profiles)
    allowed = {"capital", "server_per_year", "data_per_year", *COST_FIELDS}
    unknown = set(values) - allowed
    if unknown:
        raise ValueError(f"unknown assumptions {sorted(unknown)}")
    return evaluate(
        report,
        Assumptions(
            float(values.get("capital", report.initial_cash)),
            float(values.get("server_per_year", 0.0)),
            float(values.get("data_per_year", 0.0)),
            tuple(sorted((k, float(v)) for k, v in values.items() if k in COST_FIELDS)),
        ),
    )
