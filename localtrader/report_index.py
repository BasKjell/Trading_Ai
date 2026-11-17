"""Read-only overview of the experiment register and local intraday reports for the dashboard (T-007).

Only files the project itself writes are read: ``experiments/EXP-*.json`` (via the register) and
``reports/<run>/summary.json`` (intraday backtest) or ``reports/<run>/scenarios.json`` (stress runs).
Nothing is computed anew; values are passed through so the dashboard shows exactly what the reports say.
A run that used the holdout is flagged, never hidden.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .experiment_registry import load_all, total_variants

MAX_REPORTS = 50
METRIC_KEYS = (
    "total_return",
    "geometric_monthly_return",
    "geometric_monthly_return_90ci",
    "max_drawdown",
    "sharpe",
    "trades",
    "win_rate",
    "profit_factor",
    "total_costs",
    "average_exposure",
    "trading_days",
)


def _clean(value: Any) -> Any:
    """JSON-safe copy: NaN/inf (not valid JSON) become None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    return value


def experiments_overview(directory: str | Path = "experiments") -> dict[str, Any]:
    path = Path(directory)
    if not path.is_dir():
        return {"available": False, "experiments": 0, "total_variants_tested": 0, "holdout_uses": 0, "entries": []}
    records = load_all(path)
    return {
        "available": True,
        "experiments": len(records),
        "total_variants_tested": total_variants(records),
        "holdout_uses": sum(1 for record in records if record.holdout_used),
        "entries": [
            {
                "id": record.experiment_id,
                "task": record.task_id,
                "agent": record.agent,
                "created_at": record.created_at,
                "variants": len(record.variants),
                "rejected_variants": sum(1 for variant in record.variants if variant.rejected),
                "decision": record.decision,
                "holdout_used": record.holdout_used,
                "synthetic_data": record.synthetic_data,
                "references": record.references,
            }
            for record in sorted(records, key=lambda r: r.created_at, reverse=True)
        ],
    }


def _field(raw: dict[str, Any], key: str, kind: type, default: Any) -> Any:
    """Optional field of a given JSON type; a present value of another type makes the file invalid."""
    value = raw.get(key)
    if value is None:
        return default
    if not isinstance(value, kind):
        raise ValueError(f"{key} must be a JSON {kind.__name__}")
    return value


def _number(value: Any, key: str) -> Any:
    if value is None or isinstance(value, bool):
        return value if value is None else None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list) and all(isinstance(item, (int, float)) for item in value):
        return value
    raise ValueError(f"metrics.{key} must be numeric")


def _backtest_entry(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    metrics = _field(raw, "metrics", dict, {})
    datasets = _field(raw, "datasets", list, [])
    if not all(isinstance(item, dict) for item in datasets):
        raise ValueError("datasets must be a list of objects")
    holdout = _field(raw, "holdout", dict, {})
    benchmark = _field(raw, "benchmark", dict, {})
    return {
        "name": name,
        "kind": "backtest",
        "symbols": [str(d.get("symbol")) for d in datasets],
        "synthetic": any(bool(d.get("synthetic")) for d in datasets),
        "strategies": sorted(str(key) for key in _field(raw, "parameters", dict, {})),
        "settings": _field(raw, "settings", dict, {}),
        "metrics": {key: _number(metrics.get(key), key) for key in METRIC_KEYS},
        "benchmark": {key: _number(value, key) for key, value in benchmark.items()},
        "holdout_used": bool(holdout.get("used")),
        "holdout_start": holdout.get("start"),
        "warnings": [str(item) for item in _field(raw, "warnings", list, [])],
    }


def _stress_entry(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    raw_holdout = raw.get("holdout")
    holdout: dict[str, Any] = raw_holdout if isinstance(raw_holdout, dict) else {}
    scenarios = {
        key: {
            "total_return": value.get("total_return"),
            "max_drawdown": value.get("max_drawdown"),
            "trades": value.get("trades"),
        }
        for key, value in raw.items()
        if isinstance(value, dict) and "total_return" in value
    }
    return {
        "name": name,
        "kind": "stress",
        "scenarios": scenarios,
        "phases": sorted(key for key in scenarios if key.startswith("phase_")),
        "holdout_used": bool(holdout.get("used")),
        "holdout_start": holdout.get("start"),
    }


def reports_overview(root: str | Path = "reports") -> dict[str, Any]:
    """Newest report directories first; unreadable files are listed as errors, not skipped silently."""
    base = Path(root)
    if not base.is_dir():
        return {"available": False, "reports": [], "errors": []}
    runs: list[tuple[float, dict[str, Any]]] = []
    errors: list[str] = []
    for directory in base.iterdir():
        if not directory.is_dir() or directory.is_symlink():
            continue
        for filename, build in (("summary.json", _backtest_entry), ("scenarios.json", _stress_entry)):
            file = directory / filename
            if not file.is_file():
                continue
            try:
                raw = json.loads(file.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("not a JSON object")
                entry = build(directory.name, raw)
            except (OSError, ValueError, TypeError, AttributeError) as error:  # one bad file never hides the rest
                errors.append(f"{directory.name}/{filename}: {str(error)[:120]}")
                continue
            entry["modified"] = file.stat().st_mtime
            runs.append((file.stat().st_mtime, entry))
    runs.sort(key=lambda item: item[0], reverse=True)
    return {"available": True, "reports": [_clean(entry) for _, entry in runs[:MAX_REPORTS]], "errors": errors}
