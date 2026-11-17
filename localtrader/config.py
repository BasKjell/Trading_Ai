from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
import hashlib
import json
import tomllib


@dataclass(frozen=True, slots=True)
class RiskConfig:
    risk_per_trade: float
    max_risk_per_trade: float
    daily_loss_limit: float
    drawdown_reduce: float
    drawdown_block_experimental: float
    drawdown_kill: float
    max_total_exposure: float
    max_etf_weight: float
    max_stock_weight: float
    max_crypto_weight: float
    max_crypto_exposure: float


@dataclass(frozen=True, slots=True)
class CostConfig:
    equity_fee_bps: float
    equity_spread_bps: float
    equity_slippage_bps: float
    crypto_fee_bps: float
    crypto_spread_bps: float
    crypto_slippage_bps: float


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
    def digest(self) -> str:
        payload = json.dumps(self.raw, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


def load_config(path: str | Path = "config/default.toml") -> AppConfig:
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    risk = RiskConfig(**raw["risk"])
    costs = CostConfig(**raw["costs"])
    research = ResearchConfig(**raw["research"])
    if not 0 < risk.risk_per_trade <= risk.max_risk_per_trade <= 0.005:
        raise ValueError("risk_per_trade must be positive and capped at 0.5%")
    if not (risk.drawdown_reduce < risk.drawdown_block_experimental < risk.drawdown_kill):
        raise ValueError("drawdown ladder must be strictly increasing")
    if raw["system"].get("mode") != "offline":
        raise ValueError("MVP supports offline mode only")
    if not 0.5 <= research.min_positive_fold_ratio <= 1.0:
        raise ValueError("min_positive_fold_ratio must be in [0.5, 1.0]")
    if research.min_median_trades < 2:
        raise ValueError("min_median_trades must be at least 2")
    return AppConfig(raw=raw, risk=risk, costs=costs, research=research)
