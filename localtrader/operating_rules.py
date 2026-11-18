"""Binding operating rules (project spec §18.7, T-019), loaded from ``[operating_rules]``.

The rules are configuration with a fixed vocabulary, so a typo or a weakening is rejected at start-up.
Only values that the system actually enforces are accepted; documented alternatives that are not
implemented yet (``PLANNED``) are rejected with a clear message instead of being silently ignored.
Two rules are not configurable at all: foreign positions are never liquidated automatically, and live
trading requires an account used only by this bot. ``docs/OPERATING_RULES.md`` explains each rule and
whether it is already enforced (backtest) or only planned (paper/live, T-006).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CHOICES: dict[str, tuple[str, ...]] = {
    "trading_hours": ("regular_session_only",),
    "before_close": ("no_new_entries_in_last_bar",),
    "overnight_positions": ("allowed",),
    "weekend_positions": ("allowed",),
    "trading_halt": ("no_orders_while_halted",),
    "dividends": ("entitlement_at_ex_date_cash_at_payment",),
    "splits": ("block_symbol_until_reviewed",),
    "delisting": ("block_new_entries_and_alert",),
    "foreign_positions": ("never_liquidate",),
    "manual_orders": ("never_touch_and_reconcile_as_foreign",),
}
FIXED = {"foreign_positions": "never_liquidate"}
# Alternatives described in docs/OPERATING_RULES.md that the engine does not enforce yet.
PLANNED = {"before_close": ("flatten_all",), "overnight_positions": ("forbidden",), "weekend_positions": ("forbidden",)}


class OperatingRulesError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class OperatingRules:
    trading_hours: str
    before_close: str
    overnight_positions: str
    weekend_positions: str
    trading_halt: str
    dividends: str
    splits: str
    delisting: str
    foreign_positions: str
    manual_orders: str
    dedicated_account_required_for_live: bool

    @property
    def allows_overnight(self) -> bool:
        return self.overnight_positions == "allowed"


def load_operating_rules(raw: dict[str, Any]) -> OperatingRules:
    """Validate ``[operating_rules]``; every key is required, unknown keys are rejected."""
    section = raw.get("operating_rules")
    if not isinstance(section, dict):
        raise OperatingRulesError("config needs an [operating_rules] section (spec §18.7)")
    expected = set(CHOICES) | {"dedicated_account_required_for_live"}
    problems = [f"missing {key}" for key in sorted(expected - set(section))]
    problems += [f"unknown key {key}" for key in sorted(set(section) - expected)]
    for key, allowed in CHOICES.items():
        if key in section and section[key] in PLANNED.get(key, ()):
            problems.append(f"{key} = {section[key]!r} is planned but not enforced yet; allowed: {allowed}")
        elif key in section and section[key] not in allowed:
            problems.append(f"{key} must be one of {allowed}, not {section[key]!r}")
    for key, value in FIXED.items():
        if section.get(key) != value:
            problems.append(f"{key} is fixed to {value!r} (spec §18.7)")
    if section.get("dedicated_account_required_for_live") is not True:
        problems.append("dedicated_account_required_for_live must be true")
    if problems:
        raise OperatingRulesError("; ".join(problems))
    return OperatingRules(**section)
