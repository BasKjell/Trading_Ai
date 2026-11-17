from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, median
from typing import Any, Callable
import hashlib
import json
import os

from .config import AppConfig
from .data import Bar, available_data_catalog, bars_hash, group_by_symbol, synthetic_fixture
from .research_db import ResearchDB
from .research import development_validation_holdout, walk_forward_splits
from .spec_backtest import DeclarativeBacktestEngine
from .strategy_dsl import SEARCHABLE_FAMILIES, StrategySpec, load_strategy_spec


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    candidate_id: str
    name: str
    family: str
    status: str
    score: float | None
    eligible: bool
    paper_approved: bool
    evaluated_at: str
    folds: int
    runs: int
    median_total_return: float | None
    median_sharpe: float | None
    worst_max_drawdown: float | None
    median_trades: float | None
    cost_3x_total_return: float | None
    reasons: tuple[str, ...]
    spec: dict[str, Any]
    dataset: dict[str, Any]
    generator_model: str | None = None
    generator_iteration: int | None = None
    positive_fold_ratio: float | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["reasons"] = list(self.reasons)
        return value


def spec_digest(spec: StrategySpec) -> str:
    payload = json.dumps(spec.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def behavior_digest(spec: StrategySpec | dict[str, Any]) -> str:
    """Stable strategy identity that ignores labels and prose."""
    payload = spec.to_dict() if isinstance(spec, StrategySpec) else dict(spec)
    for field in ("name", "rationale", "version"):
        payload.pop(field, None)
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def robustness_reasons(config: AppConfig, result: CandidateEvaluation | dict[str, Any]) -> list[str]:
    value = result.to_dict() if isinstance(result, CandidateEvaluation) else result
    reasons: list[str] = []
    drawdown = value.get("worst_max_drawdown")
    trades = value.get("median_trades")
    median_return = value.get("median_total_return")
    sharpe = value.get("median_sharpe")
    stressed = value.get("cost_3x_total_return")
    fold_ratio = value.get("positive_fold_ratio")
    if drawdown is not None and float(drawdown) >= config.risk.drawdown_kill:
        reasons.append("drawdown_kill_threshold_breached")
    if trades is not None and float(trades) < config.research.min_median_trades:
        reasons.append("insufficient_independent_trades")
    if median_return is not None and float(median_return) <= config.research.min_median_return:
        reasons.append("non_positive_median_return")
    if sharpe is not None and float(sharpe) <= config.research.min_median_sharpe:
        reasons.append("non_positive_median_sharpe")
    if stressed is not None and float(stressed) <= config.research.min_cost_3x_return:
        reasons.append("fails_3x_cost_stress")
    if fold_ratio is not None and float(fold_ratio) < config.research.min_positive_fold_ratio:
        reasons.append("insufficient_positive_fold_ratio")
    return reasons


def _validation_bars(bars: list[Bar], spec: StrategySpec, validation: slice) -> list[Bar]:
    grouped = group_by_symbol([bar for bar in bars if bar.symbol in spec.universe])
    start = max(0, int(validation.start or 0) - spec.warmup)
    stop = int(validation.stop or 0)
    selected: list[Bar] = []
    for symbol in spec.universe:
        selected.extend(grouped[symbol][start:stop])
    selected.sort(key=lambda bar: (bar.symbol, bar.start))
    return selected


class StrategyLab:
    def __init__(self, config: AppConfig, registry_dir: str | Path = "research_registry") -> None:
        self.config = config
        self.registry_dir = Path(registry_dir)
        self.registry_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.registry_dir / "history.jsonl"
        self.champion_path = self.registry_dir / "research_champion.json"
        self.db = ResearchDB(self.registry_dir / "research.db")
        self.db.sync_catalog(available_data_catalog(), "synthetic_fixture")

    def evaluate(
        self, spec: StrategySpec, days: int = 900, *, bars: list[Bar] | None = None,
        dataset_metadata: dict[str, Any] | None = None,
        progress: Callable[[str], None] | None = None,
        generator_model: str | None = None, generator_iteration: int | None = None,
    ) -> CandidateEvaluation:
        candidate_id = spec_digest(spec)
        now = datetime.now(UTC).isoformat()
        if spec.is_shadow_only:
            result = CandidateEvaluation(
                candidate_id, spec.name, spec.family, "blocked_missing_data", None, False, False,
                now, 0, 0, None, None, None, None, None,
                tuple(f"missing:{item}" for item in spec.data_requirements), spec.to_dict(),
                {"source": "none", "available": ["synthetic_price_ohlcv"]},
                generator_model, generator_iteration,
            )
            self._record(result)
            return result

        using_synthetic = bars is None
        if bars is None:
            bars = synthetic_fixture(self.config.seed, days)
        selected = [bar for bar in bars if bar.symbol in spec.universe]
        grouped = group_by_symbol(selected)
        missing = set(spec.universe) - set(grouped)
        if missing:
            raise ValueError(f"dataset is missing strategy symbols: {sorted(missing)}")
        series_length = min(len(grouped[symbol]) for symbol in spec.universe)
        if series_length < max(120, spec.warmup + 60):
            raise ValueError(
                f"not enough bars per symbol: {series_length}; need at least {max(120, spec.warmup + 60)}"
            )
        development, holdout = development_validation_holdout(series_length, 0.20)
        development_length = int(development.stop or 0)
        train_size = min(450, max(spec.warmup + 50, development_length // 2))
        validation_size = min(120, max(60, (development_length - train_size - 10) // 2))
        splits = walk_forward_splits(
            development_length, train_size, validation_size,
            purge_size=5, embargo_size=5, step_size=validation_size,
        )
        run_metrics: list[tuple[int, dict[str, float | int | None]]] = []
        for fold_number, split in enumerate(splits, start=1):
            fold_bars = _validation_bars(bars, spec, split.validation)
            for multiplier in (1, 2, 3):
                result = DeclarativeBacktestEngine(self.config, multiplier).run(spec, fold_bars, bars_hash(fold_bars))
                run_metrics.append((multiplier, result.metrics))
            if progress:
                progress(f"Walk-forward {fold_number}/{len(splits)}")
        returns = [float(item[1]["total_return"] or 0.0) for item in run_metrics]
        sharpes = [float(item[1]["sharpe"] or 0.0) for item in run_metrics]
        drawdowns = [float(item[1]["max_drawdown"] or 0.0) for item in run_metrics]
        trades = [float(item[1]["trades"] or 0.0) for item in run_metrics]
        stressed = [float(metrics["total_return"] or 0.0) for multiplier, metrics in run_metrics if multiplier == 3]
        base_returns = [float(metrics["total_return"] or 0.0) for multiplier, metrics in run_metrics if multiplier == 1]
        median_return = median(returns) if returns else 0.0
        median_sharpe = median(sharpes) if sharpes else 0.0
        worst_dd = max(drawdowns, default=0.0)
        median_trade_count = median(trades) if trades else 0.0
        stressed_return = median(stressed) if stressed else 0.0
        positive_fold_ratio = sum(value > 0 for value in base_returns) / len(base_returns) if base_returns else 0.0
        preliminary = {
            "worst_max_drawdown": worst_dd, "median_trades": median_trade_count,
            "median_total_return": median_return, "median_sharpe": median_sharpe,
            "cost_3x_total_return": stressed_return, "positive_fold_ratio": positive_fold_ratio,
        }
        reasons = robustness_reasons(self.config, preliminary)
        eligible = not reasons
        score = median_return + 0.02 * median_sharpe - 2.0 * worst_dd + 0.25 * stressed_return
        dataset = {
            "source": "synthetic_fixture", "seed": self.config.seed, "days": days,
            "hash_sha256": bars_hash(selected), "bars": len(selected),
            "development": [development.start, development.stop],
            "reserved_holdout": [holdout.start, holdout.stop], "holdout_used": False,
            "cost_multipliers": [1, 2, 3],
        }
        if not using_synthetic:
            dataset.update(dataset_metadata or {})
            dataset["source"] = str((dataset_metadata or {}).get("source", "external_validated"))
            dataset.pop("seed", None)
            dataset.pop("days", None)
        evaluation = CandidateEvaluation(
            candidate_id, spec.name, spec.family, "evaluated", score, eligible, False, now,
            len(splits), len(run_metrics), median_return, median_sharpe, worst_dd,
            median_trade_count, stressed_return, tuple(reasons), spec.to_dict(),
            dataset,
            generator_model, generator_iteration,
            positive_fold_ratio,
        )
        self._record(evaluation)
        self._consider_research_champion(evaluation)
        return evaluation

    def _record(self, evaluation: CandidateEvaluation) -> None:
        self.db.store_spec(evaluation.candidate_id, evaluation.spec, "validated_strategy_lab")
        self.db.store_evaluation(
            evaluation.candidate_id, evaluation.to_dict(),
            evaluation.generator_model, evaluation.generator_iteration,
        )
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(evaluation.to_dict(), sort_keys=True) + "\n")

    def _consider_research_champion(self, evaluation: CandidateEvaluation) -> None:
        if not evaluation.eligible or evaluation.score is None:
            return
        current: dict[str, Any] | None = None
        if self.champion_path.exists():
            current = json.loads(self.champion_path.read_text(encoding="utf-8"))
        if current is not None:
            if robustness_reasons(self.config, current):
                current = None
        if current is not None:
            current_source = str(current.get("dataset", {}).get("source", "unknown"))
            new_source = str(evaluation.dataset.get("source", "unknown"))
            if current_source != "synthetic_fixture" and new_source == "synthetic_fixture":
                return
            same_evidence_class = (current_source == "synthetic_fixture") == (new_source == "synthetic_fixture")
            if same_evidence_class and float(current.get("score", float("-inf"))) >= evaluation.score:
                return
        payload = evaluation.to_dict()
        payload["registry_role"] = "research_champion"
        payload["paper_approved"] = False
        payload["promotion_note"] = "Automatic research ranking only; manual approval is required for paper use."
        temporary = self.champion_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.champion_path)

    def champion(self) -> dict[str, Any] | None:
        if not self.champion_path.exists():
            return None
        champion = json.loads(self.champion_path.read_text(encoding="utf-8"))
        return None if robustness_reasons(self.config, champion) else champion

    def is_duplicate(self, spec: StrategySpec) -> bool:
        fingerprint = behavior_digest(spec)
        return any(behavior_digest(item) == fingerprint for item in self.db.evaluated_strategy_specs())

    def research_feedback(self, model: str, limit: int = 10) -> list[dict[str, Any]]:
        feedback: list[dict[str, Any]] = []
        for item in self.db.recent_evaluations(limit * 3):
            if item.get("generator_model") not in {None, model}:
                continue
            result = item["result"]
            spec = result.get("spec", {})
            feedback.append({
                "family": result.get("family"), "timeframe": spec.get("timeframe", "1Day"),
                "universe": spec.get("universe", []),
                "indicator_windows": [
                    [indicator.get("kind"), indicator.get("window")]
                    for indicator in spec.get("indicators", [])
                ],
                "stop_multiple": spec.get("stop_atr_multiple"),
                "eligible": bool(result.get("eligible")), "score": result.get("score"),
                "return": result.get("median_total_return"), "sharpe": result.get("median_sharpe"),
                "reasons": result.get("reasons", []),
                "behavior_fingerprint": behavior_digest(spec)[:12] if spec else None,
            })
            if len(feedback) >= limit:
                break
        return feedback

    def next_search_family(
        self, model: str, iteration: int,
        families: tuple[str, ...] = SEARCHABLE_FAMILIES,
    ) -> str:
        if not families or any(family not in SEARCHABLE_FAMILIES for family in families):
            raise ValueError("families must contain supported price-research families")
        counts = self.db.family_evaluation_counts(model)
        least = min(counts.get(family, 0) for family in families)
        tied = [family for family in families if counts.get(family, 0) == least]
        return tied[(iteration - 1) % len(tied)]


def load_builtin_specs(directory: str | Path = "strategy_specs") -> list[StrategySpec]:
    return [load_strategy_spec(str(path)) for path in sorted(Path(directory).glob("*.json"))]
