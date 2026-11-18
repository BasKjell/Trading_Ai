"""Economic inputs for the T-008 EUR accounts. Decimal amounts, dated costs, explicit currency transfers."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .cost_schedule import ScheduleError, entries_for
from .fx_rates import FxSeries

ZERO = Decimal(0)
ONE = Decimal(1)
BP = Decimal("0.0001")


def amount(value: object) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError("expected a finite nonnegative amount")
    return number


def running_cost(start: date, end: date, annual_eur: Decimal) -> Decimal:
    """ACT/ACT, half-open interval. Leap years and fold-to-fold cash intervals use the same convention."""
    if end < start:
        raise ValueError("reversed expense interval")
    result = ZERO
    while start < end:
        until = min(date(start.year + 1, 1, 1), end)
        result += annual_eur * (until - start).days / (366 if calendar.isleap(start.year) else 365)
        start = until
    return result


@dataclass(frozen=True)
class FillCost:
    execution_usd: Decimal
    fees_usd: Decimal
    spread_usd: Decimal
    slippage_usd: Decimal


@dataclass(frozen=True)
class Rates:
    commission: Decimal
    sec: Decimal
    taf: Decimal
    taf_cap: Decimal
    cat: Decimal
    spread: Decimal
    slip: Decimal
    fx: Decimal
    multiplier: Decimal

    @classmethod
    def for_fill(cls, schedule: dict[str, Any], symbol: str, day: date, side: str, scenario: str) -> Rates:
        if side not in ("buy", "sell") or scenario not in ("base", "cost2", "spread3", "delay1", "neighbor"):
            raise ScheduleError("unknown execution side or scenario")
        coverage = schedule["coverage"]
        if symbol not in schedule["instruments"] or not coverage["from"] <= day.isoformat() < coverage["to"]:
            raise ScheduleError("cost schedule outside bound coverage")
        rows = entries_for(schedule, symbol, day)

        def rate(component: str, unit: str, currency: str = "USD") -> Decimal:
            sides = ("conversion",) if component == "fx_conversion" else (side, "both")
            matches = [
                e
                for e in rows
                if (e["component"], e["currency"], e["unit"]) == (component, currency, unit) and e["side"] in sides
            ]
            if len(matches) != 1:
                raise ScheduleError(f"expected exactly one applicable {component}/{unit}/{currency} rate")
            return amount(matches[0]["value"])

        return cls(
            rate("commission", "bps_of_notional") * BP,
            rate("regulatory_sec", "bps_of_notional") * BP if side == "sell" else ZERO,
            rate("regulatory_taf", "usd_per_share") if side == "sell" else ZERO,
            rate("regulatory_taf", "usd_per_trade_max") if side == "sell" else ZERO,
            rate("regulatory_cat", "usd_per_share"),
            rate("spread", "bps_half_spread") * BP * (3 if scenario == "spread3" else 1),
            rate("slippage", "bps_of_notional") * BP,
            rate("fx_conversion", "bps_per_conversion", "EUR") * BP,
            Decimal(2 if scenario == "cost2" else 1),
        )

    def fill(self, raw_price: Decimal, signed_quantity: Decimal) -> FillCost:
        if raw_price <= 0 or not signed_quantity:
            raise ValueError("invalid fill")
        qty = abs(signed_quantity)
        spread = raw_price * self.spread * self.multiplier
        slip = raw_price * self.slip * self.multiplier
        execution = raw_price + (spread + slip) * (1 if signed_quantity > 0 else -1)
        if execution <= 0:
            raise ValueError("invalid execution price")
        fees = (
            execution * qty * (self.commission + self.sec) + min(qty * self.taf, self.taf_cap) + qty * self.cat
        ) * self.multiplier
        return FillCost(execution, fees, spread * qty, slip * qty)


@dataclass(frozen=True)
class FxBooking:
    moment: datetime
    available_at: datetime
    usd_per_eur: Decimal
    usd_change: Decimal
    fee_eur: Decimal


class CashBook:
    """Retain sale proceeds in USD; buy USD only for a shortfall. Valuation never generates a fee."""

    def __init__(self, initial_eur: Decimal, fx: FxSeries) -> None:
        self.eur = amount(initial_eur)
        self.usd = ZERO
        self.fx = fx
        self.transfers: list[FxBooking] = []

    def value(self, usd_assets: Decimal, moment: datetime) -> Decimal:
        return self.eur + (self.usd + usd_assets) / amount(self.fx.rate_at(moment).usd_per_eur)

    def trade_cash(self, change_usd: Decimal, moment: datetime, rates: Rates) -> None:
        if not change_usd.is_finite():
            raise ValueError("invalid USD change")
        shortfall = max(ZERO, -(self.usd + change_usd))
        if shortfall:
            quote = self.fx.rate_at(moment)
            mid = amount(quote.usd_per_eur)
            gross = shortfall / mid
            fee = gross * rates.fx * rates.multiplier
            if gross + fee > self.eur:
                raise ValueError("insufficient EUR funding")
            self.eur -= gross + fee
            self.usd += shortfall
            self.transfers.append(FxBooking(moment, quote.available_at, mid, shortfall, fee))
        self.usd += change_usd

    def withdraw_usd(self, moment: datetime, rates: Rates) -> None:
        if self.usd:
            quote = self.fx.rate_at(moment)
            mid = amount(quote.usd_per_eur)
            gross = self.usd / mid
            fee = gross * rates.fx * rates.multiplier
            self.transfers.append(FxBooking(moment, quote.available_at, mid, -self.usd, fee))
            self.eur += gross - fee
            self.usd = ZERO
