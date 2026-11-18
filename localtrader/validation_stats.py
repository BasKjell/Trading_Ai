"""T-008 selection and synchronized circular moving-block inference from scored EUR equity paths."""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from itertools import accumulate
from statistics import fmean


@dataclass(frozen=True)
class Sleeve:
    """Session closes and all observed drawdown extrema from the same scored portfolio."""

    days: tuple[date, ...]
    equity: tuple[float, ...]
    initial: float
    max_drawdown: float
    round_trips: int
    entry_months: frozenset[tuple[int, int]]
    breach: bool

    def __post_init__(self) -> None:
        if (
            not self.days
            or len(self.days) != len(self.equity)
            or list(self.days) != sorted(set(self.days))
            or not all(math.isfinite(n) and n > 0 for n in (self.initial, *self.equity))
            or not math.isfinite(self.max_drawdown)
            or not 0 <= self.max_drawdown <= 1
            or self.round_trips < 0
        ):
            raise ValueError("invalid scored equity path")

    def log_returns(self) -> list[float]:
        return [math.log(b / a) for a, b in zip((self.initial, *self.equity[:-1]), self.equity, strict=True)]


@dataclass(frozen=True)
class Compared:
    strategy: Sleeve
    cash: Sleeve
    matched: Sleeve

    def paired(self) -> tuple[list[float], list[float]]:
        if any(
            (ref.days, ref.initial) != (self.strategy.days, self.strategy.initial) for ref in (self.cash, self.matched)
        ):
            raise ValueError("comparison portfolios differ in sessions or opening capital")
        candidate = self.strategy.log_returns()
        return (
            [s - c for s, c in zip(candidate, self.cash.log_returns(), strict=True)],
            [s - p for s, p in zip(candidate, self.matched.log_returns(), strict=True)],
        )


def select_candidate(
    runs: dict[str, Compared], candidates: Sequence[str], *, min_trips: int, drawdown_limit: float
) -> str | None:
    if not candidates or set(runs) != set(candidates):
        raise ValueError("incomplete validation grid")
    scores = []
    for name, run in runs.items():
        cash, matched = run.paired()
        s = run.strategy
        if (
            s.equity[-1] > s.initial
            and fmean(cash) > 0
            and fmean(matched) > 0
            and s.round_trips >= min_trips
            and s.max_drawdown < drawdown_limit
            and not any(x.breach for x in (s, run.cash, run.matched))
        ):
            scores.append((-round(fmean(matched), 12), name))
    return min(scores)[1] if scores else None


def nominate(winners: Sequence[str | None]) -> str | None:
    if len(winners) != 3:
        raise ValueError("all three validation decisions are required")
    return winners[-1] if all(winners) and winners.count(winners[-1]) >= 2 else None


@dataclass(frozen=True)
class Bounds:
    cash: float
    matched: float


def bootstrap(
    folds: Sequence[Compared], *, block: int, replicates: int, seed: int, alphas: Sequence[float]
) -> list[Bounds]:
    """Basic one-sided lower bounds. Draw order is replicate, fold, block. No cross-fold block."""
    if not folds or min(block, replicates) <= 0 or not alphas or not all(0 < a < 1 for a in alphas):
        raise ValueError("invalid bootstrap settings")
    if any(a.strategy.days[-1] >= b.strategy.days[0] for a, b in zip(folds, folds[1:], strict=False)):
        raise ValueError("overlapping or unordered folds")
    # Precompute full and truncated circular block sums for each possible start.
    plans = []
    all_cash = []
    all_matched = []
    for fold in folds:
        cash, matched = fold.paired()
        n = len(cash)
        if n < block:
            raise ValueError("fewer scored sessions than a block")
        whole, remainder = divmod(n, block)
        all_cash.extend(cash)
        all_matched.extend(matched)
        a = list(accumulate(cash + cash, initial=0.0))
        b = list(accumulate(matched + matched, initial=0.0))

        def sums(width: int, a: list[float] = a, b: list[float] = b, n: int = n) -> list[tuple[float, float]]:
            return [(a[i + width] - a[i], b[i + width] - b[i]) for i in range(n)]

        plans.append((n, whole, sums(block), sums(remainder) if remainder else []))
    rng = random.Random(seed)
    bs_cash = []
    bs_matched = []
    for _ in range(replicates):
        x = y = 0.0
        for n, whole, full, tail in plans:
            for _ in range(whole):
                dx, dy = full[rng.randrange(n)]
                x += dx
                y += dy
            if tail:
                dx, dy = tail[rng.randrange(n)]
                x += dx
                y += dy
        bs_cash.append(x / len(all_cash))
        bs_matched.append(y / len(all_cash))
    bs_cash.sort()
    bs_matched.sort()
    result = []
    for alpha in alphas:
        rank = math.ceil((1 - alpha) * replicates) - 1
        result.append(Bounds(2 * fmean(all_cash) - bs_cash[rank], 2 * fmean(all_matched) - bs_matched[rank]))
    return result
