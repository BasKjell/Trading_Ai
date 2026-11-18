from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from localtrader.broker import BrokerOrder, FakeBroker
from localtrader.config import load_config
from localtrader.domain import Direction, Market, OrderIntent, OrderStatus, RiskDecision
from localtrader.orders import OrderError, OrderManager
from localtrader.portfolio import Portfolio
from localtrader.risk import RiskEngine

S = OrderStatus


def request(
    client_id: str = "one", side: Direction = Direction.LONG, quantity: int = 4
) -> tuple[OrderIntent, RiskDecision]:
    now = datetime.now(UTC)
    intent = OrderIntent(
        client_id, "unit", "ETF", Market.ETF, now, side, quantity, "market", now + timedelta(minutes=5), 9
    )
    return intent, RiskDecision(True, quantity, (), "approved", now)


class LifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "orders.sqlite"
        self.broker = FakeBroker(10_000, {"ETF": 5})
        self.manager = self.open()

    def open(self) -> OrderManager:
        manager = OrderManager(self.path, self.broker, 10_000, {"ETF": 5})
        self.addCleanup(manager.close)
        return manager

    def test_repeated_intent_unsorted_executions_and_terminal_conflict(self) -> None:
        intent, decision = request()
        self.assertEqual(self.manager.submit(intent, decision, 4800), S.SUBMITTED)
        self.assertEqual(self.manager.submit(intent, decision, 4800), S.SUBMITTED)
        with self.assertRaises(OrderError):
            self.manager.submit(replace(intent, symbol="other"), decision, 4800)
        self.assertEqual(self.broker.submissions, 1)
        partial = self.broker.advance("one", S.PARTIALLY_FILLED, 2, 2100)
        complete = self.broker.advance("one", S.FILLED, 4, 4250)
        self.manager.observe(complete)
        self.manager.observe(partial)
        self.manager.observe(complete)
        self.assertTrue(self.manager.reconcile())
        self.assertEqual(self.manager.status("one"), S.FILLED)
        with self.assertRaises(OrderError):
            self.manager.observe(replace(complete, status=S.CANCELLED))
        self.assertFalse(self.manager.positions_reconciled)
        self.assertEqual(self.manager.status("one"), S.FILLED)
        self.assertEqual(
            self.manager.db.execute("SELECT count(*) FROM journal WHERE kind='transition'").fetchone()[0], 4
        )

    def test_partial_sale_reserves_remaining_shares_until_cancel(self) -> None:
        intent, decision = request(side=Direction.FLAT)
        self.manager.submit(intent, decision, 0)
        partial = self.broker.advance("one", S.PARTIALLY_FILLED, 2, 1900)
        self.manager.observe(partial)
        with self.assertRaisesRegex(OrderError, "unreserved shares"):
            self.manager.submit(*request("two", Direction.FLAT, 2), 0)
        self.broker.advance("one", S.CANCELLED, 2, 1900)
        self.assertTrue(self.manager.reconcile())
        self.assertEqual(self.manager.submit(*request("two", Direction.FLAT, 3), 0), S.SUBMITTED)
        self.broker.advance("two", S.FILLED, 3, 2950)
        self.assertTrue(self.manager.reconcile())
        self.assertEqual(self.broker.positions["ETF"], 0)

    def test_expired_rejected_or_unsupported_intent_never_dispatches(self) -> None:
        intent, decision = request()
        for invalid, approval in [
            (replace(intent, valid_until=intent.created_at - timedelta(seconds=1)), decision),
            (intent, replace(decision, allowed=False)),
            (replace(intent, order_type="stop"), decision),
            (replace(intent, target_quantity=float("nan")), decision),
        ]:
            with self.subTest(invalid), self.assertRaises(OrderError):
                self.manager.submit(invalid, approval, 4000)
        self.assertEqual(self.broker.submissions, 0)

    def test_buy_reservations_and_rejected_order(self) -> None:
        self.manager.submit(*request(), 7000)
        with self.assertRaisesRegex(OrderError, "unreserved cash"):
            self.manager.submit(*request("two"), 3100)
        self.broker.advance("one", S.REJECTED, 0, 0)
        self.assertTrue(self.manager.reconcile())
        self.assertEqual(self.manager.submit(*request("two"), 3100), S.SUBMITTED)

    def test_timeout_after_accept_requires_reconciliation(self) -> None:
        send = self.broker.submit

        def lose_response(order: BrokerOrder) -> None:
            send(order)
            raise TimeoutError

        with patch.object(self.broker, "submit", side_effect=lose_response):
            self.assertEqual(self.manager.submit(*request(), 4000), S.UNKNOWN)
        self.assertEqual(self.manager.submit(*request(), 4000), S.UNKNOWN)
        with self.assertRaises(OrderError):
            self.manager.submit(*request("two"), 4000)
        self.assertTrue(self.manager.reconcile())
        self.assertEqual(self.manager.status("one"), S.SUBMITTED)
        self.assertEqual(self.broker.submissions, 1)

    def test_missing_uncertain_order_stays_locked_after_restart(self) -> None:
        with patch.object(self.broker, "submit", side_effect=ConnectionError):
            self.manager.submit(*request(), 4000)
        manager = self.open()
        self.assertFalse(manager.positions_reconciled)
        self.assertEqual(manager.submit(*request(), 4000), S.UNKNOWN)
        self.assertEqual(self.broker.submissions, 0)
        self.assertGreater(manager.db.execute("SELECT count(*) FROM journal WHERE kind='alarm'").fetchone()[0], 0)

    def test_unexplained_account_changes_block_risk_and_order_dispatch(self) -> None:
        intent, decision = request()
        risk = RiskEngine(load_config().risk, reconciliation_ok=lambda: self.manager.positions_reconciled)
        self.broker.positions["manual"] = 1
        with self.assertRaises(OrderError):
            self.manager.submit(intent, decision, 4000)
        result = risk.decide(intent, Portfolio(10_000), {"ETF": 10}, intent.created_at, 10_000)
        self.assertIn("position_mismatch", result.violated_limits)
        del self.broker.positions["manual"]
        self.broker.cash -= 1
        self.assertFalse(self.manager.reconcile())
        self.broker.cash += 1
        self.broker.submit(BrokerOrder("manual", "ETF", Direction.FLAT, 1, 0))
        self.assertFalse(self.manager.reconcile())
        self.assertIn("manual", self.broker.orders)

    def test_conflicting_snapshot_rolls_back_all_derived_updates(self) -> None:
        self.manager.submit(*request(), 4500)
        self.broker.advance("one", S.PARTIALLY_FILLED, 2, 2200)
        self.broker.cash += 10
        self.assertFalse(self.manager.reconcile())
        self.assertEqual(self.manager.status("one"), S.SUBMITTED)
        self.broker.cash -= 10
        self.assertTrue(self.manager.reconcile())
        self.assertEqual(self.manager.status("one"), S.PARTIALLY_FILLED)
        invalid = self.broker.advance("one", S.REJECTED, 2, 2200)
        with self.assertRaises(OrderError):
            self.manager.observe(invalid)
        self.assertFalse(self.manager.reconcile())

    def test_simultaneous_clients_dispatch_only_once(self) -> None:
        barrier = threading.Barrier(2)

        def submit() -> OrderStatus:
            manager = OrderManager(self.path, self.broker, 10_000, {"ETF": 5})
            try:
                barrier.wait(timeout=5)
                return manager.submit(*request(), 4200)
            finally:
                manager.close()

        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: submit(), range(2)))
        self.assertIn(S.SUBMITTED, results)
        self.assertEqual(self.broker.submissions, 1)

    def test_real_process_exit_after_fake_accept_then_restart(self) -> None:
        receipt = self.path.with_suffix(".json")
        child = subprocess.run(
            [sys.executable, __file__, str(self.path), str(receipt)],
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
            check=False,
        )
        self.assertEqual(child.returncode, 79)
        data = json.loads(receipt.read_text())
        data["direction"] = Direction(data["direction"])
        self.broker.submit(BrokerOrder(**data))
        self.broker.advance("crash", S.PARTIALLY_FILLED, 2, 1900)
        manager = self.open()
        self.assertTrue(manager.positions_reconciled)
        self.assertEqual(manager.status("crash"), S.PARTIALLY_FILLED)
        self.assertEqual(manager.submit(*request("crash"), 4000), S.PARTIALLY_FILLED)
        self.assertEqual(self.broker.submissions, 1)


if __name__ == "__main__":

    class CrashOnAcceptance(FakeBroker):
        def submit(self, order: BrokerOrder) -> None:
            super().submit(order)
            Path(sys.argv[2]).write_text(json.dumps(asdict(order)))
            os._exit(79)

    OrderManager(Path(sys.argv[1]), CrashOnAcceptance(10_000, {"ETF": 5}), 10_000, {"ETF": 5}).submit(
        *request("crash"), 4000
    )
