from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from math import sqrt
from statistics import fmean, pstdev
from typing import Any

from .domain import Bar, Market
from .strategies import atr, sma

ALLOWED_INDICATORS = frozenset(
    {
        "sma",
        "ema",
        "momentum",
        "atr",
        "rsi",
        "volatility",
        "breakout_high",
        "breakout_low",
        "relative_volume",
    }
)
ALLOWED_OPERATORS = frozenset({">", ">=", "<", "<=", "crosses_above", "crosses_below"})
SEARCHABLE_FAMILIES = (
    "etf_trend",
    "stock_momentum",
    "crypto_trend",
    "multi_asset_trend",
)
EVENT_FAMILIES = frozenset({"news_momentum", "news_underreaction", "shortseller_intelligence", "crisis_event"})
STRATEGY_FAMILY_CATALOG: dict[str, dict[str, str]] = {
    "etf_trend": {"label": "ETF-Trendfolge", "stage": "P0", "mode": "price_research"},
    "stock_momentum": {"label": "Aktien-Momentum", "stage": "P0", "mode": "price_research"},
    "crypto_trend": {"label": "BTC-/ETH-Trendfolge", "stage": "P0", "mode": "price_research"},
    "multi_asset_trend": {"label": "Multi-Asset-Trend", "stage": "P1", "mode": "price_research"},
    "news_momentum": {"label": "News-Momentum", "stage": "P2", "mode": "shadow_event"},
    "news_underreaction": {"label": "News-Underreaction", "stage": "P2", "mode": "shadow_event"},
    "shortseller_intelligence": {"label": "Shortseller-Intelligence", "stage": "P2", "mode": "shadow_event"},
    "crisis_event": {"label": "Crisis-/Event-Trading", "stage": "P2", "mode": "shadow_event"},
}
ALLOWED_FAMILIES = frozenset(STRATEGY_FAMILY_CATALOG)
BASE_FIELDS = frozenset({"open", "high", "low", "close", "volume"})


class StrategySpecError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class IndicatorSpec:
    alias: str
    kind: str
    window: int
    source: str = "close"


@dataclass(frozen=True, slots=True)
class RuleSpec:
    left: str
    op: str
    right: str | float


@dataclass(frozen=True, slots=True)
class StrategySpec:
    name: str
    family: str
    market: Market
    universe: tuple[str, ...]
    indicators: tuple[IndicatorSpec, ...]
    entry_all: tuple[RuleSpec, ...]
    exit_any: tuple[RuleSpec, ...]
    stop_atr_alias: str
    stop_atr_multiple: float
    timeframe: str = "1Day"
    stop_mode: str = "fixed"
    stop_expression: tuple[str | float, ...] = ()
    rank_by: str | None = None
    max_positions: int = 1
    rebalance_every: int = 1
    data_requirements: tuple[str, ...] = ()
    rationale: str = ""
    version: str = "1.0"

    @property
    def is_shadow_only(self) -> bool:
        return self.family in EVENT_FAMILIES or bool(self.data_requirements)

    @property
    def warmup(self) -> int:
        return max((item.window for item in self.indicators), default=2) + 2

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["market"] = self.market.value
        result["universe"] = list(self.universe)
        result["data_requirements"] = list(self.data_requirements)
        return result


def strategy_json_schema() -> dict[str, Any]:
    indicator = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "alias": {"type": "string"},
            "kind": {"type": "string", "enum": sorted(ALLOWED_INDICATORS)},
            "window": {"type": "integer", "minimum": 2, "maximum": 500},
            "source": {"type": "string", "enum": sorted(BASE_FIELDS)},
        },
        "required": ["alias", "kind", "window", "source"],
    }
    rule = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "left": {"type": "string"},
            "op": {"type": "string", "enum": sorted(ALLOWED_OPERATORS)},
            "right": {"anyOf": [{"type": "string"}, {"type": "number"}]},
        },
        "required": ["left", "op", "right"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string"},
            "family": {"type": "string", "enum": list(SEARCHABLE_FAMILIES)},
            "market": {"type": "string", "enum": ["equity", "etf", "crypto", "fx", "bond", "commodity", "multi_asset"]},
            "universe": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 20},
            "indicators": {"type": "array", "items": indicator, "minItems": 2, "maxItems": 12},
            "entry_all": {"type": "array", "items": rule, "minItems": 1, "maxItems": 8},
            "exit_any": {"type": "array", "items": rule, "minItems": 1, "maxItems": 8},
            "stop_atr_alias": {"type": "string"},
            "stop_atr_multiple": {"type": "number", "minimum": 1.0, "maximum": 8.0},
            "timeframe": {"type": "string", "enum": ["1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"]},
            "stop_mode": {"type": "string", "enum": ["fixed", "trailing"]},
            "stop_expression": {
                "type": "array",
                "minItems": 3,
                "maxItems": 32,
                "items": {"anyOf": [{"type": "string"}, {"type": "number"}]},
            },
            "rank_by": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            "max_positions": {"type": "integer", "minimum": 1, "maximum": 10},
            "rebalance_every": {"type": "integer", "minimum": 1, "maximum": 21},
            "data_requirements": {"type": "array", "items": {"type": "string"}, "maxItems": 0},
            "rationale": {"type": "string"},
            "version": {"type": "string"},
        },
        "required": [
            "name",
            "family",
            "market",
            "universe",
            "indicators",
            "entry_all",
            "exit_any",
            "stop_atr_alias",
            "stop_atr_multiple",
            "timeframe",
            "stop_mode",
            "stop_expression",
            "rank_by",
            "max_positions",
            "rebalance_every",
            "data_requirements",
            "rationale",
            "version",
        ],
    }


def parse_strategy_spec(raw: dict[str, Any]) -> StrategySpec:
    required = {
        "name",
        "family",
        "market",
        "universe",
        "indicators",
        "entry_all",
        "exit_any",
        "stop_atr_alias",
        "stop_atr_multiple",
        "rank_by",
        "max_positions",
        "rebalance_every",
        "data_requirements",
        "rationale",
        "version",
    }
    optional = {"timeframe", "stop_mode", "stop_expression"}
    unknown = set(raw) - required - optional
    missing = required - set(raw)
    if unknown or missing:
        raise StrategySpecError(f"invalid fields; missing={sorted(missing)}, unknown={sorted(unknown)}")
    name = str(raw["name"])
    if not re.fullmatch(r"[a-z][a-z0-9_]{2,63}", name):
        raise StrategySpecError("name must be a safe snake_case identifier")
    family = str(raw["family"])
    if family not in ALLOWED_FAMILIES:
        raise StrategySpecError(f"unsupported family: {family}")
    try:
        market = Market(str(raw["market"]))
    except ValueError as error:
        raise StrategySpecError("unsupported market/asset class") from error
    universe = tuple(str(item).upper() for item in raw["universe"])
    if not universe or len(universe) > 20 or len(set(universe)) != len(universe):
        raise StrategySpecError("universe must contain 1-20 unique symbols")
    if any(not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", symbol) for symbol in universe):
        raise StrategySpecError("unsafe symbol")
    indicators = tuple(IndicatorSpec(**item) for item in raw["indicators"])
    aliases = {item.alias for item in indicators}
    if not 2 <= len(indicators) <= 12 or len(aliases) != len(indicators):
        raise StrategySpecError("2-12 indicators with unique aliases required")
    for item in indicators:
        if not re.fullmatch(r"[a-z][a-z0-9_]{1,31}", item.alias):
            raise StrategySpecError(f"unsafe indicator alias: {item.alias}")
        if item.kind not in ALLOWED_INDICATORS or item.source not in BASE_FIELDS or not 2 <= item.window <= 500:
            raise StrategySpecError(f"invalid indicator: {item.alias}")
        if item.kind == "atr" and item.source != "close":
            raise StrategySpecError("ATR source must be close")
    operands = aliases | BASE_FIELDS

    def parse_rules(items: list[dict[str, Any]], label: str) -> tuple[RuleSpec, ...]:
        if not 1 <= len(items) <= 8:
            raise StrategySpecError(f"{label} must contain 1-8 rules")
        rules = tuple(RuleSpec(**item) for item in items)
        for rule in rules:
            if rule.left not in operands or rule.op not in ALLOWED_OPERATORS:
                raise StrategySpecError(f"invalid {label} rule")
            if isinstance(rule.right, str) and rule.right not in operands:
                raise StrategySpecError(f"unknown right operand: {rule.right}")
            if rule.op.startswith("crosses_") and not isinstance(rule.right, str):
                raise StrategySpecError("cross rules require two series operands")
        return rules

    entry_all = parse_rules(raw["entry_all"], "entry_all")
    exit_any = parse_rules(raw["exit_any"], "exit_any")
    stop_alias = str(raw["stop_atr_alias"])
    kinds = {item.alias: item.kind for item in indicators}
    if kinds.get(stop_alias) != "atr":
        raise StrategySpecError("stop_atr_alias must reference an ATR indicator")
    stop_multiple = float(raw["stop_atr_multiple"])
    if not 1.0 <= stop_multiple <= 8.0:
        raise StrategySpecError("stop_atr_multiple must be in [1, 8]")
    timeframe = str(raw.get("timeframe", "1Day"))
    if timeframe not in {"1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"}:
        raise StrategySpecError("unsupported strategy timeframe")
    stop_mode = str(raw.get("stop_mode", "fixed"))
    if stop_mode not in {"fixed", "trailing"}:
        raise StrategySpecError("stop_mode must be fixed or trailing")
    expression_raw = raw.get("stop_expression", [])
    stop_expression = tuple(float(item) if isinstance(item, (int, float)) else str(item) for item in expression_raw)
    if stop_expression:
        validate_stop_expression(stop_expression, operands)
    rank_by = raw["rank_by"]
    if rank_by is not None and rank_by not in aliases:
        raise StrategySpecError("rank_by must reference an indicator alias")
    max_positions = int(raw["max_positions"])
    rebalance_every = int(raw["rebalance_every"])
    if not 1 <= max_positions <= min(10, len(universe)) or not 1 <= rebalance_every <= 21:
        raise StrategySpecError("position or rebalance limits invalid")
    requirements = tuple(str(item) for item in raw["data_requirements"])
    if family in EVENT_FAMILIES and not requirements:
        raise StrategySpecError("event strategies must declare unavailable event data requirements")
    if family not in EVENT_FAMILIES and requirements:
        raise StrategySpecError("P0 price strategies may not request undeclared external data")
    return StrategySpec(
        name=name,
        family=family,
        market=market,
        universe=universe,
        indicators=indicators,
        entry_all=entry_all,
        exit_any=exit_any,
        stop_atr_alias=stop_alias,
        stop_atr_multiple=stop_multiple,
        timeframe=timeframe,
        stop_mode=stop_mode,
        stop_expression=stop_expression,
        rank_by=rank_by,
        max_positions=max_positions,
        rebalance_every=rebalance_every,
        data_requirements=requirements,
        rationale=str(raw["rationale"])[:1000],
        version=str(raw["version"])[:32],
    )


def load_strategy_spec(path: str) -> StrategySpec:
    with open(path, encoding="utf-8") as handle:
        return parse_strategy_spec(json.load(handle))


def _series_value(bars: list[Bar], field: str, offset: int = 0) -> float | None:
    index = len(bars) - 1 - offset
    if index < 0:
        return None
    return float(getattr(bars[index], field))


def indicator_value(
    spec: IndicatorSpec,
    bars: list[Bar],
    offset: int = 0,
    periods_per_year: float = 252.0,
) -> float | None:
    usable = bars[: len(bars) - offset] if offset else bars
    if len(usable) < spec.window + (1 if spec.kind in {"atr", "momentum", "volatility"} else 0):
        return None
    values = [float(getattr(bar, spec.source)) for bar in usable]
    if spec.kind == "sma":
        return sma(values, spec.window)
    if spec.kind == "ema":
        alpha = 2 / (spec.window + 1)
        result = values[-spec.window]
        for value in values[-spec.window + 1 :]:
            result = alpha * value + (1 - alpha) * result
        return result
    if spec.kind == "momentum":
        return values[-1] / values[-spec.window - 1] - 1
    if spec.kind == "atr":
        return atr(usable, spec.window)
    if spec.kind == "volatility":
        returns = [
            b / a - 1 for a, b in zip(values[-spec.window - 1 : -1], values[-spec.window :], strict=False) if a > 0
        ]
        return pstdev(returns) * sqrt(periods_per_year) if len(returns) > 1 else 0.0
    if spec.kind == "breakout_high":
        return max(values[-spec.window - 1 : -1])
    if spec.kind == "breakout_low":
        return min(values[-spec.window - 1 : -1])
    if spec.kind == "relative_volume":
        baseline = fmean(values[-spec.window - 1 : -1])
        return values[-1] / baseline if baseline > 0 else None
    if spec.kind == "rsi":
        changes = [b - a for a, b in zip(values[-spec.window - 1 : -1], values[-spec.window :], strict=False)]
        gains = fmean([max(0.0, value) for value in changes])
        losses = fmean([max(0.0, -value) for value in changes])
        return 100.0 if losses == 0 else 100 - 100 / (1 + gains / losses)
    raise StrategySpecError(f"indicator not implemented: {spec.kind}")


def values_for_bar(spec: StrategySpec, bars: list[Bar], offset: int = 0) -> dict[str, float] | None:
    minutes = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 1440}[spec.timeframe]
    if spec.market == Market.CRYPTO:
        periods = 365.25 if spec.timeframe == "1Day" else 365.25 * 1440 / minutes
    else:
        periods = 252.0 if spec.timeframe == "1Day" else 252.0 * 390 / minutes
    values: dict[str, float] = {}
    for field in BASE_FIELDS:
        value = _series_value(bars, field, offset)
        if value is None:
            return None
        values[field] = value
    for indicator in spec.indicators:
        value = indicator_value(indicator, bars, offset, periods)
        if value is None:
            return None
        values[indicator.alias] = value
    return values


def _compare(left: float, op: str, right: float, previous_left: float | None, previous_right: float | None) -> bool:
    if op == ">":
        return left > right
    if op == ">=":
        return left >= right
    if op == "<":
        return left < right
    if op == "<=":
        return left <= right
    if previous_left is None or previous_right is None:
        return False
    if op == "crosses_above":
        return previous_left <= previous_right and left > right
    if op == "crosses_below":
        return previous_left >= previous_right and left < right
    return False


def rules_match(spec: StrategySpec, bars: list[Bar], rules: tuple[RuleSpec, ...], require_all: bool) -> bool:
    current = values_for_bar(spec, bars)
    previous = values_for_bar(spec, bars, 1)
    if current is None:
        return False
    outcomes: list[bool] = []
    for rule in rules:
        left = current[rule.left]
        right = current[rule.right] if isinstance(rule.right, str) else float(rule.right)
        previous_left = previous.get(rule.left) if previous else None
        previous_right = (
            previous.get(rule.right)
            if previous and isinstance(rule.right, str)
            else (float(rule.right) if not isinstance(rule.right, str) else None)
        )
        outcomes.append(_compare(left, rule.op, right, previous_left, previous_right))
    return all(outcomes) if require_all else any(outcomes)


STOP_OPERATORS = frozenset({"+", "-", "*", "/", "min", "max"})


def validate_stop_expression(tokens: tuple[str | float, ...], operands: set[str] | frozenset[str]) -> None:
    """Validate a bounded reverse-Polish expression; no code or function calls are allowed."""
    if not 3 <= len(tokens) <= 32:
        raise StrategySpecError("stop_expression must contain 3-32 RPN tokens")
    depth = 0
    for token in tokens:
        if isinstance(token, float):
            if not -1000 <= token <= 1000:
                raise StrategySpecError("numeric stop token out of bounds")
            depth += 1
        elif token in operands:
            depth += 1
        elif token in STOP_OPERATORS:
            if depth < 2:
                raise StrategySpecError("invalid stop RPN stack")
            depth -= 1
        else:
            raise StrategySpecError(f"invalid stop token: {token}")
    if depth != 1:
        raise StrategySpecError("stop_expression must resolve to one value")


def evaluate_stop_expression(spec: StrategySpec, values: dict[str, float]) -> float:
    if not spec.stop_expression:
        return values["close"] - spec.stop_atr_multiple * values[spec.stop_atr_alias]
    stack: list[float] = []
    for token in spec.stop_expression:
        if isinstance(token, float):
            stack.append(token)
        elif token in values:
            stack.append(values[token])
        else:
            right = stack.pop()
            left = stack.pop()
            if token == "+":
                stack.append(left + right)
            elif token == "-":
                stack.append(left - right)
            elif token == "*":
                stack.append(left * right)
            elif token == "/":
                if abs(right) < 1e-12:
                    raise StrategySpecError("division by zero in stop expression")
                stack.append(left / right)
            elif token == "min":
                stack.append(min(left, right))
            elif token == "max":
                stack.append(max(left, right))
    return stack[0]
