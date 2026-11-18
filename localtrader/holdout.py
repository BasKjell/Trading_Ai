"""Fixed start of the final holdout (ADR 0005, T-008 protocol, user decisions of 2026-11-18).

The boundary is a constant in code, not a setting: a different configuration file or a command-line
argument can only move a research limit **earlier**, never later. Research commands have no path to data
at or after the boundary; the central evaluation (T-008) gets its own reserved, journaled access.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

HOLDOUT_START = datetime(2025, 11, 1, tzinfo=UTC)


class HoldoutError(ValueError):
    pass


def research_limit(requested: datetime | None = None) -> datetime:
    """The limit a research command may use: the requested one if it is not later than the holdout start."""
    if requested is None:
        return HOLDOUT_START
    if requested.tzinfo is None:
        raise HoldoutError("research limit must be timezone-aware")
    if requested > HOLDOUT_START:
        raise HoldoutError(f"{requested.isoformat()} is after the holdout start {HOLDOUT_START.isoformat()}")
    return requested


def check_config(raw: dict[str, Any]) -> None:
    """A configured [holdout] start must equal the fixed boundary (it documents, it does not decide)."""
    configured = (raw.get("holdout") or {}).get("start")
    if configured is None:
        return
    try:
        value = datetime.fromisoformat(str(configured))
    except ValueError as error:
        raise HoldoutError("[holdout] start is not an ISO timestamp") from error
    if value.tzinfo is None or value != HOLDOUT_START:
        raise HoldoutError(f"[holdout] start must be {HOLDOUT_START.isoformat()} (fixed in localtrader/holdout.py)")
