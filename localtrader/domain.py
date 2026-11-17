from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


def require_aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


class Market(StrEnum):
    EQUITY = "equity"
    ETF = "etf"
    CRYPTO = "crypto"
    FX = "fx"
    BOND = "bond"
    COMMODITY = "commodity"
    MULTI_ASSET = "multi_asset"


class Direction(StrEnum):
    LONG = "long"
    FLAT = "flat"


class OrderStatus(StrEnum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Bar:
    symbol: str
    market: Market
    timeframe: str
    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str
    received_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.start, "start")
        require_aware(self.end, "end")
        require_aware(self.received_at, "received_at")


@dataclass(frozen=True, slots=True)
class Signal:
    strategy: str
    symbol: str
    market: Market
    signal_time: datetime
    earliest_execution: datetime
    direction: Direction
    strength: float
    reason: str
    version: str = "1.0"
    stop_price: float | None = None

    def __post_init__(self) -> None:
        require_aware(self.signal_time, "signal_time")
        require_aware(self.earliest_execution, "earliest_execution")


@dataclass(frozen=True, slots=True)
class OrderIntent:
    client_order_id: str
    strategy: str
    symbol: str
    market: Market
    created_at: datetime
    direction: Direction
    target_quantity: float
    order_type: str
    valid_until: datetime
    stop_price: float | None
    risk_metadata: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        require_aware(self.created_at, "created_at")
        require_aware(self.valid_until, "valid_until")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    allowed: bool
    approved_quantity: float
    violated_limits: tuple[str, ...]
    reason: str
    decided_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.decided_at, "decided_at")


@dataclass(slots=True)
class PaperOrder:
    order_id: str
    client_order_id: str
    intent: OrderIntent
    status: OrderStatus
    submitted_at: datetime
    requested_price: float
    filled_quantity: float = 0.0
    average_fill_price: float | None = None

    def __post_init__(self) -> None:
        require_aware(self.submitted_at, "submitted_at")


@dataclass(frozen=True, slots=True)
class Fill:
    fill_id: str
    order_id: str
    symbol: str
    market: Market
    timestamp: datetime
    quantity: float
    price: float
    fee: float
    spread_cost: float
    slippage_cost: float

    def __post_init__(self) -> None:
        require_aware(self.timestamp, "timestamp")


@dataclass(slots=True)
class Position:
    symbol: str
    market: Market
    strategy: str
    quantity: float = 0.0
    average_entry: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    stop_price: float | None = None


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    timestamp: datetime
    cash: float
    equity: float
    gross_exposure: float
    open_risk: float
    drawdown: float
    peak_equity: float

    def __post_init__(self) -> None:
        require_aware(self.timestamp, "timestamp")


@dataclass(frozen=True, slots=True)
class MarketEvent:
    event_id: str
    event_type: str
    symbol: str | None
    published_at: datetime
    received_at: datetime
    source: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        require_aware(self.published_at, "published_at")
        require_aware(self.received_at, "received_at")


def to_dict(value: Any) -> dict[str, Any]:
    def convert(item: Any) -> Any:
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, StrEnum):
            return item.value
        if isinstance(item, dict):
            return {key: convert(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [convert(value) for value in item]
        return item

    return convert(asdict(value))
