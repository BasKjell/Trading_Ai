from __future__ import annotations

import hashlib
from datetime import datetime

from .config import CostConfig
from .domain import Fill, Market, OrderIntent, OrderStatus, PaperOrder


class FillModel:
    def __init__(self, costs: CostConfig, multiplier: float = 1.0) -> None:
        self.costs = costs
        self.multiplier = multiplier

    def fill(
        self,
        intent: OrderIntent,
        quantity: float,
        reference_price: float,
        timestamp: datetime,
        fill_fraction: float = 1.0,
    ) -> tuple[PaperOrder, Fill]:
        if not 0 < fill_fraction <= 1:
            raise ValueError("fill_fraction must be in (0, 1]")
        signed_quantity = quantity if intent.direction.value == "long" else -quantity
        signed_quantity *= fill_fraction
        crypto = intent.market == Market.CRYPTO
        fee_bps = self.costs.crypto_fee_bps if crypto else self.costs.equity_fee_bps
        spread_bps = self.costs.crypto_spread_bps if crypto else self.costs.equity_spread_bps
        slippage_bps = self.costs.crypto_slippage_bps if crypto else self.costs.equity_slippage_bps
        side = 1 if signed_quantity > 0 else -1
        fill_price = reference_price * (1 + side * (spread_bps / 2 + slippage_bps) * self.multiplier / 10_000)
        notional = abs(signed_quantity * reference_price)
        fee = notional * fee_bps * self.multiplier / 10_000
        spread = notional * (spread_bps / 2) * self.multiplier / 10_000
        slippage = notional * slippage_bps * self.multiplier / 10_000
        order_id = hashlib.sha256(intent.client_order_id.encode()).hexdigest()[:20]
        status = OrderStatus.FILLED if fill_fraction == 1 else OrderStatus.PARTIALLY_FILLED
        order = PaperOrder(
            order_id, intent.client_order_id, intent, status, timestamp, reference_price, signed_quantity, fill_price
        )
        fill = Fill(
            fill_id=f"fill-{order_id}-{fill_fraction:.6f}",
            order_id=order_id,
            symbol=intent.symbol,
            market=intent.market,
            timestamp=timestamp,
            quantity=signed_quantity,
            price=fill_price,
            fee=fee,
            spread_cost=spread,
            slippage_cost=slippage,
        )
        return order, fill


class IdempotentPaperExecutor:
    def __init__(self, fill_model: FillModel) -> None:
        self.fill_model = fill_model
        self.orders: dict[str, PaperOrder] = {}

    def execute(
        self, intent: OrderIntent, quantity: float, price: float, timestamp: datetime
    ) -> tuple[PaperOrder, Fill | None]:
        existing = self.orders.get(intent.client_order_id)
        if existing is not None:
            return existing, None
        order, fill = self.fill_model.fill(intent, quantity, price, timestamp)
        self.orders[intent.client_order_id] = order
        return order, fill
