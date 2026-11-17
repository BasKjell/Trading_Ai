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


def _backtest_entry(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    metrics = raw.get("metrics", {})
    datasets = raw.get("datasets") or []
    holdout = raw.get("holdout") or {}
    return {
        "name": name,
        "kind": "backtest",
        "symbols": [d.get("symbol") for d in datasets if isinstance(d, dict)],
        "synthetic": any(bool(d.get("synthetic")) for d in datasets if isinstance(d, dict)),
        "strategies": sorted((raw.get("parameters") or {}).keys()),
        "settings": raw.get("settings") or {},
        "metrics": {key: metrics.get(key) for key in METRIC_KEYS},
        "benchmark": raw.get("benchmark") or {},
        "holdout_used": bool(holdout.get("used")),
        "holdout_start": holdout.get("start"),
        "warnings": raw.get("warnings") or [],
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
            except (OSError, ValueError) as error:
                errors.append(f"{directory.name}/{filename}: {str(error)[:120]}")
                continue
            entry["modified"] = file.stat().st_mtime
            runs.append((file.stat().st_mtime, entry))
    runs.sort(key=lambda item: item[0], reverse=True)
    return {"available": True, "reports": [_clean(entry) for _, entry in runs[:MAX_REPORTS]], "errors": errors}
