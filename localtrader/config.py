from __future__ import annotations

import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RiskConfig:
    """Provisional protective parameters (not proven optimal). See localtrader/risk.py for definitions."""

    risk_per_trade: float
    max_risk_per_trade: float
    max_open_risk: float
    daily_loss_limit: float
    drawdown_reduce: float
    drawdown_block_new: float
    drawdown_kill: float
    max_total_exposure: float
    max_instrument_weight: float
    max_etf_weight: float
    max_stock_weight: float
    max_crypto_weight: float
    max_crypto_exposure: float
    max_group_weight: float
    min_order_value: float
    max_volume_participation: float


@dataclass(frozen=True, slots=True)
class CostConfig:
    equity_fee_bps: float
    equity_spread_bps: float
    equity_slippage_bps: float
    equity_min_fee: float
    crypto_fee_bps: float
    crypto_spread_bps: float
    crypto_slippage_bps: float
    crypto_min_fee: float


@dataclass(frozen=True, slots=True)
class ResearchConfig:
    min_median_return: float
    min_median_sharpe: float
    min_cost_3x_return: float
    min_positive_fold_ratio: float
    min_median_trades: float


@dataclass(frozen=True, slots=True)
class AppConfig:
    raw: dict[str, Any]
    risk: RiskConfig
    costs: CostConfig
    research: ResearchConfig

    @property
    def seed(self) -> int:
        return int(self.raw["system"]["seed"])

    @property
    def initial_cash(self) -> float:
        return float(self.raw["system"]["initial_cash"])

    @property
    def currency(self) -> str:
        return str(self.raw["system"].get("currency", "EUR"))

    @property
    def correlation_groups(self) -> dict[str, str]:
        return {str(k): str(v) for k, v in self.raw.get("correlation_groups", {}).items()}

    @property
    def digest(self) -> str:
        payload = json.dumps(self.raw, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


# Hard ceilings that no configuration (and no research process) may exceed.
HARD_LIMITS = {
    "max_risk_per_trade": 0.005,
    "max_open_risk": 0.02,
    "max_total_exposure": 1.0,
    "max_instrument_weight": 0.25,
    "drawdown_kill": 0.15,
}


def validate_risk(risk: RiskConfig) -> None:
    if not 0 < risk.risk_per_trade <= risk.max_risk_per_trade <= HARD_LIMITS["max_risk_per_trade"]:
        raise ValueError("risk_per_trade must be positive, <= max_risk_per_trade and <= 0.5%")
    if not 0 < risk.max_open_risk <= HARD_LIMITS["max_open_risk"]:
        raise ValueError("max_open_risk must be in (0, 2%]")
    if not 0 < risk.drawdown_reduce < risk.drawdown_block_new < risk.drawdown_kill <= HARD_LIMITS["drawdown_kill"]:
        raise ValueError("drawdown ladder must be strictly increasing and <= 15%")
    if not 0 < risk.max_total_exposure <= HARD_LIMITS["max_total_exposure"]:
        raise ValueError("max_total_exposure must be in (0, 1]; no leverage")
    if not 0 < risk.max_instrument_weight <= HARD_LIMITS["max_instrument_weight"]:
        raise ValueError("max_instrument_weight must be in (0, 25%]")
    for name in ("max_etf_weight", "max_stock_weight", "max_crypto_weight", "max_group_weight"):
        if not 0 < getattr(risk, name) <= 1.0:
            raise ValueError(f"{name} must be in (0, 1]")
    if not 0 < risk.daily_loss_limit <= 0.05:
        raise ValueError("daily_loss_limit must be in (0, 5%]")
    if risk.min_order_value < 0 or not 0 < risk.max_volume_participation <= 0.25:
        raise ValueError("invalid min_order_value or max_volume_participation")


def load_config(path: str | Path = "config/default.toml") -> AppConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    risk = RiskConfig(**raw["risk"])
    costs = CostConfig(**raw["costs"])
    research = ResearchConfig(**raw["research"])
    validate_risk(risk)
    if raw["system"].get("mode") != "offline":
        raise ValueError("MVP supports offline mode only")
    if not 0.5 <= research.min_positive_fold_ratio <= 1.0:
        raise ValueError("min_positive_fold_ratio must be in [0.5, 1.0]")
    if research.min_median_trades < 2:
        raise ValueError("min_median_trades must be at least 2")
    return AppConfig(raw=raw, risk=risk, costs=costs, research=research)
