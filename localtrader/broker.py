"""Offline order transport contract. Snapshots include terminal orders and cumulative executions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .domain import Direction, OrderStatus


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    client_id: str
    symbol: str
    direction: Direction
    quantity: int  # whole shares in the fake adapter
    max_debit: int  # cash cents including fees; zero for sales

    def __post_init__(self) -> None:
        if not self.client_id.strip() or not self.symbol.strip() or not isinstance(self.direction, Direction):
            raise ValueError("invalid order identity")
        if type(self.quantity) is not int or not 0 < self.quantity < 2**63:
            raise ValueError("invalid share quantity")
        if type(self.max_debit) is not int or not 0 <= self.max_debit < 2**63:
            raise ValueError("invalid cash limit")
        if (self.direction == Direction.LONG) != (self.max_debit > 0):
            raise ValueError("a buy requires a cash limit, a sale requires zero")


@dataclass(frozen=True, slots=True)
class BrokerEvent:
    order: BrokerOrder
    revision: int
    status: OrderStatus
    filled: int = 0
    cash_value: int = 0  # cumulative debit including fees, or net sale proceeds, in cents


@dataclass(frozen=True, slots=True)
class BrokerSnapshot:
    cash: int
    positions: dict[str, int]
    orders: dict[str, BrokerEvent]


class Broker(Protocol):
    def submit(self, order: BrokerOrder) -> None: ...
    def snapshot(self) -> BrokerSnapshot: ...


class FakeBroker:
    """In-memory counterparty. Tests explicitly advance executions, without a network transport."""

    def __init__(self, cash: int, positions: dict[str, int] | None = None) -> None:
        self.cash = cash
        self.positions = dict(positions or {})
        self.orders: dict[str, BrokerEvent] = {}
        self.submissions = 0

    def submit(self, order: BrokerOrder) -> None:
        self.submissions += 1
        if order.client_id in self.orders:
            raise ValueError("duplicate submission")
        self.orders[order.client_id] = BrokerEvent(order, 1, OrderStatus.SUBMITTED)

    def advance(self, client_id: str, status: OrderStatus, filled: int, cash_value: int) -> BrokerEvent:
        old = self.orders[client_id]
        sign = 1 if old.order.direction == Direction.LONG else -1
        self.cash -= sign * (cash_value - old.cash_value)
        symbol = old.order.symbol
        self.positions[symbol] = self.positions.get(symbol, 0) + sign * (filled - old.filled)
        event = BrokerEvent(old.order, old.revision + 1, status, filled, cash_value)
        self.orders[client_id] = event
        return event

    def snapshot(self) -> BrokerSnapshot:
        return BrokerSnapshot(self.cash, dict(self.positions), dict(self.orders))
