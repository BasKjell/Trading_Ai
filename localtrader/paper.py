from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .config import AppConfig
from .domain import Direction, Market, OrderIntent, Position, to_dict
from .execution import FillModel, IdempotentPaperExecutor
from .portfolio import Portfolio
from .risk import RiskEngine


class PaperSession:
    """Offline-only deterministic simulator. It contains no network or broker code."""

    def __init__(
        self, config: AppConfig, state_path: str | Path | None = None, audit_path: str | Path | None = None
    ) -> None:
        paper = config.raw["paper"]
        self.config = config
        self.state_path = Path(state_path or paper["state_path"])
        self.audit_path = Path(audit_path or paper["audit_path"])
        self.portfolio = Portfolio(config.initial_cash)
        self.risk = RiskEngine(config.risk, config.correlation_groups)
        self.risk.paused = bool(paper.get("paused", False))
        self.executor = IdempotentPaperExecutor(FillModel(config.costs))
        self.processed_events: set[str] = set()
        self._load()

    def _load(self) -> None:
        if not self.state_path.exists():
            return
        raw = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.portfolio.cash = float(raw["cash"])
        self.portfolio.peak_equity = float(raw["peak_equity"])
        self.processed_events = set(raw.get("processed_events", []))
        self.risk.kill_switch = bool(raw.get("kill_switch", False))
        self.risk.deleveraging_required = bool(raw.get("deleveraging_required", False))
        self.risk.paused = bool(raw.get("paused", False))
        for item in raw.get("positions", []):
            position = Position(
                symbol=item["symbol"],
                market=Market(item["market"]),
                strategy=item["strategy"],
                quantity=float(item["quantity"]),
                average_entry=float(item["average_entry"]),
                realized_pnl=float(item.get("realized_pnl", 0.0)),
                unrealized_pnl=float(item.get("unrealized_pnl", 0.0)),
                stop_price=float(item["stop_price"]) if item.get("stop_price") is not None else None,
                entry_fees=float(item.get("entry_fees", 0.0)),
            )
            self.portfolio.positions[position.symbol] = position

    def _persist(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cash": self.portfolio.cash,
            "peak_equity": self.portfolio.peak_equity,
            "processed_events": sorted(self.processed_events),
            "kill_switch": self.risk.kill_switch,
            "deleveraging_required": self.risk.deleveraging_required,
            "paused": self.risk.paused,
            "mode": "offline-paper-only",
            "positions": [
                {
                    "symbol": p.symbol,
                    "market": p.market.value,
                    "strategy": p.strategy,
                    "quantity": p.quantity,
                    "average_entry": p.average_entry,
                    "realized_pnl": p.realized_pnl,
                    "unrealized_pnl": p.unrealized_pnl,
                    "stop_price": p.stop_price,
                    "entry_fees": p.entry_fees,
                }
                for p in self.portfolio.positions.values()
            ],
        }
        self.state_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def audit(self, event: dict[str, object]) -> None:
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def process_demo_event(
        self, event_id: str, symbol: str, market: Market, price: float, stop: float, timestamp: datetime
    ) -> bool:
        if event_id in self.processed_events:
            self.audit({"event_id": event_id, "result": "duplicate_ignored", "time": timestamp.isoformat()})
            return False
        self.processed_events.add(event_id)
        quantity = self.risk.size_for_stop(self.portfolio.equity({symbol: price}), price, stop)
        intent = OrderIntent(
            event_id,
            "offline_demo",
            symbol,
            market,
            timestamp,
            Direction.LONG,
            quantity,
            "market",
            timestamp + timedelta(minutes=5),
            stop,
            {},
        )
        decision = self.risk.decide(
            intent, self.portfolio, {symbol: price}, timestamp, self.portfolio.equity({symbol: price})
        )
        record: dict[str, object] = {"event_id": event_id, "decision": to_dict(decision), "mode": "offline-paper-only"}
        if decision.allowed:
            order, fill = self.executor.execute(intent, decision.approved_quantity, price, timestamp)
            if fill:
                self.portfolio.apply_fill(fill, intent.strategy, stop)
                record["order"] = to_dict(order)
                record["fill"] = to_dict(fill)
        self.audit(record)
        self._persist()
        return decision.allowed

    def set_kill_switch(self, enabled: bool) -> None:
        """Manual switch. Turning it off is the only way to re-enable trading after a drawdown latch."""
        self.risk.kill_switch = enabled
        if not enabled:
            self.risk.deleveraging_required = False
        self.audit({"action": "kill_switch", "enabled": enabled, "time": datetime.now(UTC).isoformat()})
        self._persist()
