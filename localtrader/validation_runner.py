"""Chronological T-008 execution. A bound research frontend supplies only the requested role to the replay."""

from __future__ import annotations

import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import fmean, stdev
from typing import Any

from .attempt_journal import AttemptJournal
from .intraday_strategies import IntradayStrategy, build_strategy
from .validation_costs import running_cost
from .validation_replay import ReplayResult
from .validation_stats import Compared, bootstrap, nominate, select_candidate
from .walkforward import RoleWindow

Evaluate = Callable[[RoleWindow, str, IntradayStrategy | None, dict[str, float] | None], ReplayResult]


def persist(path: Path, value: object) -> None:
    """Exclusive immutable result. A partial write consumes this run and cannot authorize the next stage."""
    with path.open("x", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True, allow_nan=False)
        f.flush()
        os.fsync(f.fileno())


@dataclass
class Comparison:
    path: Compared
    strategy: ReplayResult
    cash: ReplayResult
    matched: ReplayResult


def chain(paths: list[ReplayResult], annual_cost: Decimal) -> tuple[float, float]:
    capital = peak = paths[0].sleeve.initial
    initial = capital
    worst = 0.0
    previous: date | None = None
    for p in paths:
        if previous:
            if p.sleeve.days[0] <= previous:
                raise ValueError("overlapping return paths")
            capital -= float(running_cost(previous + timedelta(days=1), p.sleeve.days[0], annual_cost))
        if capital <= 0:
            raise ValueError("cash bridge depleted capital")
        scale = capital / p.sleeve.initial
        for value in (capital, *[scale * x for x in p.marks]):
            peak = max(peak, value)
            worst = max(worst, 1 - value / peak)
        capital = scale * p.sleeve.equity[-1]
        previous = p.sleeve.days[-1]
    return capital / initial - 1, worst


def diagnostics(paths: list[ReplayResult], annual_cost: Decimal) -> dict[str, Any]:
    """Cash flows in EUR at their event rates. Residual includes USD cash FX and running costs."""
    net, dd = chain(paths, annual_cost)
    first = paths[0].sleeve
    calendar_days = (paths[-1].sleeve.days[-1] - first.days[0]).days + 1
    returns = [math.expm1(r) for p in paths for r in p.sleeve.log_returns()]
    sigma = stdev(returns) if len(returns) > 1 else 0.0
    costs: dict[str, float] = {}
    contributions = dict.fromkeys(("QQQ", "SPY"), 0.0)
    exposure = dict.fromkeys(contributions, 0.0)
    intervals = sum(p.intervals for p in paths)
    turnover = 0.0
    capital = first.initial
    previous = first.days[0]
    for p in paths:
        bridge = float(running_cost(previous, p.sleeve.days[0], annual_cost))
        capital -= bridge
        scale = capital / p.sleeve.initial
        costs["running"] = costs.get("running", 0.0) + bridge
        for key, value in p.costs_eur.items():
            costs[key] = costs.get(key, 0.0) + scale * value
        for f in p.fills:
            gross = float(f["quantity"]) * float(f["execution_usd"]) / float(f["usd_per_eur"])
            turnover += scale * abs(gross)
            contributions[f["symbol"]] -= scale * (gross + float(f["fees_usd"]) / float(f["usd_per_eur"]))
        for dividend in p.dividends:
            contributions[dividend["symbol"]] += (
                scale * float(dividend["net_usd"]) / float(dividend["recognition_usd_per_eur"])
            )
        for symbol, weight in p.weights.items():
            if intervals:
                exposure[symbol] += weight * p.intervals / intervals
        capital = p.sleeve.equity[-1] * scale
        previous = p.sleeve.days[-1] + timedelta(days=1)
    trips = sum(p.sleeve.round_trips for p in paths)
    return {
        "net_return": net,
        "geometric_monthly_return": math.expm1(math.log1p(net) * 365.2425 / (12 * calendar_days)),
        "drawdown": dd,
        "longest_within_fold_underwater_seconds": max(p.max_underwater_seconds for p in paths),
        "session_volatility_annualized": sigma * math.sqrt(252),
        "sharpe": fmean(returns) * math.sqrt(252) / sigma if sigma else None,
        "round_trips": trips,
        "active_months": sorted(set.union(set(), *(set(p.sleeve.entry_months) for p in paths))),
        "gross_traded_eur": turnover,
        "turnover_on_initial_capital": turnover / first.initial,
        "time_average_exposure": exposure,
        "capital_committed_fraction": sum(exposure.values()),
        "net_instrument_cash_flows_eur": contributions,
        "cash_fx_and_remaining_costs_eur": net * first.initial - sum(contributions.values()),
        "costs_eur": costs,
        "cost_per_round_trip_eur": sum(costs.values()) / trips if trips else None,
        "cost_fraction_of_traded_value": sum(costs.values()) / turnover if turnover else None,
    }


def outer_gates(
    protocol: dict[str, Any], tests: dict[str, list[Comparison]], prior: int, annual_cost: Decimal
) -> dict[str, Any]:
    if (
        type(prior) is not int
        or prior < 0
        or set(tests) != {"base", "cost2", "spread3", "delay1", "neighbor"}
        or any(len(v) != 3 for v in tests.values())
    ):
        raise ValueError("incomplete outer evaluation")
    base = tests["base"]
    paths = [v.path for v in base]
    inference = protocol["inference"]
    adjusted = inference["adjusted_reporting"]
    conf = protocol["outer_accept_all"]
    alpha = 0.05 / (2 * (prior + adjusted["research_strategy_slots"]))
    enough = alpha * inference["replicates"] >= adjusted["minimum_expected_tail_draws"]
    bounds = bootstrap(
        paths,
        block=inference["block_sessions"],
        replicates=inference["replicates"],
        seed=inference["seed"],
        alphas=[0.05, alpha] if enough else [0.05],
    )
    excess = [tuple(map(fmean, p.paired())) for p in paths]
    flags = {
        "positive_folds": all(
            sum(x[k] > 0 for x in excess) >= conf["min_positive_base_excess_folds_each_comparator"] for k in (0, 1)
        ),
        "last_fold_positive": all(x > 0 for x in excess[-1]),
        "round_trips": min(p.strategy.round_trips for p in paths) >= conf["base_round_trips_per_fold_min"]
        and sum(p.strategy.round_trips for p in paths) >= conf["base_round_trips_total_min"],
        "months": len(set.union(set(), *(set(p.strategy.entry_months) for p in paths)))
        >= conf["base_active_entry_months_total_min"],
        "confidence": min(bounds[0].cash, bounds[0].matched) > 0,
    }
    scenarios: dict[str, dict[str, Any]] = {}
    for name, bundles in tests.items():
        net, dd = chain([b.strategy for b in bundles], annual_cost)
        cash, _ = chain([b.cash for b in bundles], annual_cost)
        matched, _ = chain([b.matched for b in bundles], annual_cost)
        scenarios[name] = {"net": net, "cash": cash, "matched": matched, "chained_drawdown": dd}
        scenarios[name]["diagnostics"] = diagnostics([b.strategy for b in bundles], annual_cost)
        flags[name + "_risk"] = max(dd, *(b.path.strategy.max_drawdown for b in bundles)) < conf[
            "observed_drawdown_all_scenarios_lt"
        ] and not any(p.breach for b in bundles for p in (b.path.strategy, b.path.cash, b.path.matched))
        flags[name + "_return"] = net > 0 and (name == "base" or (net > cash and (name == "neighbor" or net > matched)))
    return {
        "passed": all(flags.values()),
        "gates": flags,
        "scenarios": scenarios,
        "unadjusted": asdict(bounds[0]),
        "adjusted": asdict(bounds[1]) if enough else None,
        "adjusted_alpha": alpha,
    }


class ResearchRun:
    """Private implementation invoked after preflight. No provider clients, central reads or activation."""

    def __init__(
        self,
        protocol: dict[str, Any],
        journal: AttemptJournal,
        output: Path,
        evaluate: Evaluate,
        annual_cost: Decimal,
        prior: int,
        receipt_digest: str,
        integrated_sha: str,
    ):
        self.protocol = protocol
        self.journal = journal
        self.output = output
        self.evaluate = evaluate
        self.annual_cost = annual_cost
        self.prior = prior
        self.receipt_digest = receipt_digest
        self.integrated_sha = integrated_sha
        self.candidates = {c["id"]: c for c in protocol["candidates"]}
        self.weights: dict[tuple[str, str], dict[str, float]] = {}
        self.deadline = time.monotonic() + 3600 * protocol["budget"]["wall_clock_hours_max"]
        self.output.mkdir(parents=True, exist_ok=True)

    def _slot(
        self,
        w: RoleWindow,
        scenario: str,
        candidate: str,
        kind: str,
        sleeve: str,
        method: IntradayStrategy | None,
        weights: dict[str, float] | None,
    ) -> ReplayResult:
        if time.monotonic() >= self.deadline:
            raise TimeoutError("research wall time exhausted")
        key = f"{w.fold}-{w.role}-{scenario}-{candidate}-{sleeve}"
        self.journal.reserve(
            key, kind, {"fold": w.fold, "role": w.role, "candidate": candidate, "scenario": scenario, "sleeve": sleeve}
        )
        self.journal.transition(key, "started")
        try:
            result = self.evaluate(w, scenario, method, weights)
            s = result.sleeve
            if (
                s.days != tuple(session.day for session in w.scored)
                or not result.marks
                or result.marks[-1] != s.equity[-1]
            ):
                raise ValueError("incomplete scored calendar or event marks")
            persist(
                self.output / (key + ".json"),
                {
                    "sessions": [d.isoformat() for d in s.days],
                    "equity": s.equity,
                    "drawdown": s.max_drawdown,
                    "initial": s.initial,
                    "round_trips": s.round_trips,
                    "months": sorted(s.entry_months),
                    "breach": s.breach,
                    "weights": result.weights,
                    "costs_eur": result.costs_eur,
                    "fx": result.transfers,
                    "event_equity_eur": result.marks,
                    "fills": result.fills,
                    "dividends": result.dividends,
                    "diagnostics": diagnostics([result], self.annual_cost),
                },
            )
            if time.monotonic() >= self.deadline:
                raise TimeoutError("research wall time exhausted")
        except Exception as error:
            self.journal.transition(key, "failed", type(error).__name__)
            raise
        self.journal.transition(key, "completed")
        return result

    def _bundle(self, w: RoleWindow, scenario: str, candidate: str) -> Comparison:
        c = self.candidates[candidate]
        method = build_strategy(c["family"], c["parameters"])
        strat = self._slot(w, scenario, candidate, "strategy", "candidate", method, None)
        if w.role == "train":
            self.weights[w.fold, candidate] = strat.weights
        references = {
            "cash": {},
            "spy": {"SPY": 1.0},
            "qqq": {"QQQ": 1.0},
            "balanced": {"SPY": 0.2, "QQQ": 0.2},
            "matched": self.weights[w.fold, candidate],
        }
        refs = {k: self._slot(w, scenario, candidate, "reference", k, None, v) for k, v in references.items()}
        path = Compared(strat.sleeve, refs["cash"].sleeve, refs["matched"].sleeve)
        return Comparison(path, strat, refs["cash"], refs["matched"])

    def execute(self, windows: tuple[RoleWindow, ...]) -> dict[str, Any]:
        winners = []
        for w in windows:
            if w.role == "test":
                continue
            grid = {c: self._bundle(w, "base", c).path for c in self.candidates}
            if w.role == "validation":
                rules = self.protocol["validation_selection"]["eligibility"]
                winners.append(
                    select_candidate(
                        grid,
                        list(self.candidates),
                        min_trips=rules["min_round_trips"],
                        drawdown_limit=rules["max_observed_mark_to_market_drawdown_lt"],
                    )
                )
        nomination = nominate(winners)
        lock = {
            "winners": winners,
            "nominee": nomination,
            "weights": {f"{f}/{c}": v for (f, c), v in self.weights.items()},
        }
        persist(self.output / "selection.json", lock)
        tests: dict[str, list[Comparison]] = {s["id"]: [] for s in self.protocol["scenarios"]}
        for w, winner in zip((w for w in windows if w.role == "test"), winners, strict=True):
            if winner is None:
                continue
            for scenario in self.protocol["scenarios"]:
                c = scenario["candidate"][winner] if scenario["id"] == "neighbor" else winner
                tests[scenario["id"]].append(self._bundle(w, scenario["id"], c))
        verdict = (
            outer_gates(self.protocol, tests, self.prior, self.annual_cost)
            if nomination
            else {"passed": False, "reason": "no qualifying F3 nomination"}
        )
        verdict.update(lock, receipt_digest=self.receipt_digest, integrated_sha=self.integrated_sha)
        unstarted = []
        for w, winner in zip((w for w in windows if w.role == "test"), winners, strict=True):
            if winner is None:
                for scenario in self.protocol["scenarios"]:
                    for sleeve in ("candidate", "cash", "spy", "qqq", "balanced", "matched"):
                        unstarted.append(f"{w.fold}-test-{scenario['id']}-no-validation-winner-{sleeve}")
        verdict["attempts"] = self.journal.summary(unstarted)
        if time.monotonic() >= self.deadline:
            raise TimeoutError("research wall time exhausted")
        return verdict
