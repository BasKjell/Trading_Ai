from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from .config import CostConfig
from .domain import Fill, Market, OrderIntent, OrderStatus, PaperOrder


@dataclass(frozen=True, slots=True)
class CostProfile:
    """Modelled execution costs of one instrument (assumptions, not measured costs)."""

    fee_bps: float
    spread_bps: float
    slippage_bps: float
    min_fee: float = 0.0


class FillModel:
    """Shared by backtest and paper trading so both books costs identically.

    The fill price includes half the spread and slippage; the fee is reported separately.
    ``multiplier`` scales all variable costs (cost stress); ``spread_multiplier`` additionally widens
    only the spread (wide-spread stress).
    """

    def __init__(
        self,
        costs: CostConfig,
        multiplier: float = 1.0,
        overrides: dict[str, CostProfile] | None = None,
        spread_multiplier: float = 1.0,
    ) -> None:
        self.costs = costs
        self.multiplier = multiplier
        self.overrides = dict(overrides or {})
        self.spread_multiplier = spread_multiplier

    def profile(self, symbol: str, market: Market) -> CostProfile:
        if symbol in self.overrides:
            return self.overrides[symbol]
        if market == Market.CRYPTO:
            c = self.costs
            return CostProfile(c.crypto_fee_bps, c.crypto_spread_bps, c.crypto_slippage_bps, c.crypto_min_fee)
        c = self.costs
        return CostProfile(c.equity_fee_bps, c.equity_spread_bps, c.equity_slippage_bps, c.equity_min_fee)

    def _price_impact_bps(self, profile: CostProfile) -> tuple[float, float]:
        half_spread = profile.spread_bps / 2 * self.spread_multiplier * self.multiplier
        return half_spread, profile.slippage_bps * self.multiplier

    def round_trip_cost_per_unit(self, market: Market, price: float, symbol: str = "") -> float:
        """Estimated entry+exit cost per unit (fees, half spread, slippage) used for position sizing.

        Minimum fees are ignored here because they depend on the final quantity; the risk gate's
        minimum order value keeps tiny orders, where minimum fees dominate, out.
        """
        profile = self.profile(symbol, market)
        half_spread, slippage = self._price_impact_bps(profile)
        return 2 * price * (profile.fee_bps * self.multiplier + half_spread + slippage) / 10_000

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
        profile = self.profile(intent.symbol, intent.market)
        half_spread_bps, slippage_bps = self._price_impact_bps(profile)
        side = 1 if signed_quantity > 0 else -1
        fill_price = reference_price * (1 + side * (half_spread_bps + slippage_bps) / 10_000)
        notional = abs(signed_quantity * reference_price)
        fee = max(notional * profile.fee_bps / 10_000, profile.min_fee) * self.multiplier
        spread = notional * half_spread_bps / 10_000
        slippage = notional * slippage_bps / 10_000
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
