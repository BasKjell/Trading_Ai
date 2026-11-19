from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from localtrader.broker import FakeBroker
from localtrader.domain import Direction, Market, OrderIntent, OrderStatus, RiskDecision
from localtrader.instance_lock import InstanceLock, LockError
from localtrader.orders import OrderError, OrderManager, restore_journal

S = OrderStatus
ROOT = Path(__file__).resolve().parents[1]


def request(client_id: str, quantity: int = 2) -> tuple[OrderIntent, RiskDecision]:
    now = datetime.now(UTC)
    intent = OrderIntent(
        client_id, "unit", "ETF", Market.ETF, now, Direction.LONG, quantity, "market", now + timedelta(minutes=5), 9
    )
    return intent, RiskDecision(True, quantity, (), "approved", now)


def child(code: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", code, *args],
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


HOLD_AND_REPORT = """
import sys, time
from localtrader.instance_lock import InstanceLock, LockError
lock = InstanceLock(sys.argv[1])
try:
    lock.acquire()
except LockError:
    print("refused"); sys.exit(3)
print("acquired", lock.generation, flush=True)
if sys.argv[2] == "crash":
    import os; os._exit(9)
"""


FLOCK_PROBE = """
import fcntl, sys
with open(sys.argv[1], "a+") as handle:
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("held")
    else:
        print("free")
"""


class Monotonic:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 18, 15, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class InstanceLockTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "account.lock"

    def test_second_process_cannot_start_while_the_first_holds_the_lock(self) -> None:
        with InstanceLock(self.path) as lock:
            self.assertTrue(lock.held)
            result = child(HOLD_AND_REPORT, str(self.path), "exit")
            self.assertEqual(result.returncode, 3, result.stderr)
            self.assertIn("refused", result.stdout)
            with self.assertRaises(LockError):
                InstanceLock(self.path).acquire()  # also a second lock object in the same process
        self.assertIn("acquired", child(HOLD_AND_REPORT, str(self.path), "exit").stdout)

    def test_crashed_holder_releases_and_generation_grows(self) -> None:
        crashed = child(HOLD_AND_REPORT, str(self.path), "crash")
        self.assertEqual(crashed.returncode, 9)
        self.assertIn("acquired 1", crashed.stdout)
        with InstanceLock(self.path) as lock:
            self.assertEqual(lock.generation, 2)  # fencing token: never reused after a crash

    def test_lost_lock_is_detected_and_stays_lost(self) -> None:
        clock, mono = Clock(), Monotonic()
        lock = InstanceLock(self.path, lease_seconds=60, clock=clock, monotonic=mono)
        lock.acquire()
        self.addCleanup(lock.release)
        mono.now += 59
        lock.renew()
        mono.now += 61
        with self.assertRaisesRegex(LockError, "lease expired"):
            lock.assert_held()
        # PR #38 review 5: neither a wall-clock jump nor a monotonic rewind revives an expired lease
        clock.now -= timedelta(seconds=61)
        mono.now -= 61
        with self.assertRaisesRegex(LockError, "lost earlier: order lock lease expired"):
            lock.assert_held()
        with self.assertRaises(LockError):
            lock.renew()
        lock.release()
        lock.acquire()  # only a new successful acquisition restores ownership
        lock.assert_held()

    def test_wall_clock_jumps_do_not_affect_the_lease(self) -> None:
        clock, mono = Clock(), Monotonic()
        lock = InstanceLock(self.path, lease_seconds=60, clock=clock, monotonic=mono)
        lock.acquire()
        self.addCleanup(lock.release)
        clock.now += timedelta(hours=5)
        lock.assert_held()
        clock.now -= timedelta(days=1)
        lock.assert_held()

    def test_takeover_removal_and_replacement_are_detected(self) -> None:
        for case in ("taken over", "disappeared", "replaced"):
            with self.subTest(case):
                lock = InstanceLock(self.path)
                lock.acquire()
                try:
                    owner = json.loads(lock.owner_path.read_text())
                    if case == "taken over":
                        lock.owner_path.write_text(json.dumps({**owner, "instance_id": "someone-else"}))
                    elif case == "disappeared":
                        self.path.unlink()
                    else:
                        self.path.unlink()
                        self.path.write_text("")
                    with self.assertRaisesRegex(LockError, case):
                        lock.assert_held()
                    if case == "taken over":
                        lock.owner_path.write_text(json.dumps(owner))  # restoring the record does not revive it
                        with self.assertRaisesRegex(LockError, "lost earlier"):
                            lock.assert_held()
                finally:
                    lock.release()

    def test_forked_child_is_not_the_holder_and_cannot_release_the_parent_lock(self) -> None:
        lock = InstanceLock(self.path)
        lock.acquire()
        self.addCleanup(lock.release)
        pid = os.fork()
        if pid == 0:  # child: inherits descriptor, instance ID and generation
            code = 0
            try:
                lock.assert_held()
                code = 11  # must not count as holder
            except LockError:
                pass
            try:
                lock.renew()
                code = 12
            except LockError:
                pass
            lock.release()  # must not drop the parent's flock
            os._exit(code)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 0)
        lock.assert_held()  # parent still holds the lock after the child ended
        refused = child(HOLD_AND_REPORT, str(self.path), "exit")
        self.assertEqual(refused.returncode, 3, refused.stderr)

    def test_damaged_owner_record_refuses_start_instead_of_resetting_the_generation(self) -> None:
        with InstanceLock(self.path):
            pass
        for damaged in ("{not json", json.dumps({"generation": "7"}), json.dumps([1, 2])):
            with self.subTest(damaged):
                InstanceLock(self.path).owner_path.write_text(damaged)
                lock = InstanceLock(self.path)
                with self.assertRaisesRegex(LockError, "damaged"):
                    lock.acquire()
                self.assertFalse(lock.held)
                # the flock was rolled back: another process can take it (and is refused only by the record)
                probe = child(FLOCK_PROBE, str(self.path))
                self.assertEqual(probe.stdout.strip(), "free", probe.stderr)

    def test_failed_acquisition_is_rolled_back_completely(self) -> None:
        from unittest import mock

        lock = InstanceLock(self.path)
        failing = mock.patch.object(lock, "_write_owner", side_effect=OSError("disk full"))
        with failing, self.assertRaisesRegex(LockError, "acquisition failed"):
            lock.acquire()
        self.assertFalse(lock.held)
        self.assertEqual(child(FLOCK_PROBE, str(self.path)).stdout.strip(), "free")
        lock.acquire()  # a later attempt works normally
        self.addCleanup(lock.release)
        self.assertEqual(lock.generation, 1)


class FailingCommit:
    """Wraps a SQLite connection and fails the next commit like a full or broken disk."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.fail = True

    def commit(self) -> None:
        if self.fail:
            self.fail = False
            raise sqlite3.OperationalError("disk I/O error")
        self._db.commit()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


class _OrderBase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.broker = FakeBroker(100_000, {"ETF": 5})
        self.lock = InstanceLock(self.dir / "account.lock")
        self.lock.acquire()
        self.addCleanup(self.lock.release)

    def manager(self, path: Path | None = None, lock: Any = "default") -> OrderManager:
        manager = OrderManager(
            path or self.dir / "orders.sqlite",
            self.broker,
            100_000,
            {"ETF": 5},
            lock=self.lock if lock == "default" else lock,
        )
        self.addCleanup(manager.close)
        return manager


class OrderPathTests(_OrderBase):
    def test_without_or_after_losing_the_lock_nothing_is_sent(self) -> None:
        with self.assertRaisesRegex(OrderError, "no instance lock"):
            self.manager(lock=None).submit(*request("a"), 2400)
        manager = self.manager()
        self.lock.path.unlink()  # lock lost (e.g. removed by another deployment)
        with self.assertRaisesRegex(OrderError, "order lock not held"):
            manager.submit(*request("b"), 2400)
        self.assertEqual(self.broker.submissions, 0)
        self.assertEqual(manager.db.execute("SELECT count(*) FROM orders").fetchone()[0], 0)

    def test_lock_lost_between_journal_and_broker_withdraws_the_unsent_order(self) -> None:
        manager = self.manager()
        calls = {"n": 0}
        original = self.lock.assert_held

        def lose_on_second_check() -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise LockError("order lock taken over by another instance")
            original()

        self.lock.assert_held = lose_on_second_check  # type: ignore[method-assign]
        with self.assertRaisesRegex(OrderError, "taken over"):
            manager.submit(*request("c"), 2400)
        self.assertEqual(self.broker.submissions, 0)
        kinds = [row[0] for row in manager.db.execute("SELECT kind FROM journal WHERE client_id='c'")]
        self.assertEqual(kinds[-1], "withdrawn")
        self.lock.assert_held = original  # type: ignore[method-assign]
        self.assertTrue(manager.reconcile())  # journal and broker still agree

    def test_persistence_failure_blocks_new_orders_until_restart(self) -> None:
        manager = self.manager()
        manager.db = FailingCommit(manager.db)  # type: ignore[assignment]
        with self.assertRaisesRegex(OrderError, "persistence failure"):
            manager.submit(*request("d"), 2400)
        self.assertTrue(manager.persistence_failed)
        with self.assertRaisesRegex(OrderError, "persistence failure earlier"):
            manager.submit(*request("e"), 2400)  # the disk works again, the session stays blocked
        self.assertEqual(self.broker.submissions, 0)
        manager.close()
        restarted = self.manager()
        self.assertTrue(restarted.positions_reconciled)
        self.assertEqual(restarted.submit(*request("e"), 2400), S.SUBMITTED)

    def test_backup_restore_keeps_trading_locked_until_reconciled(self) -> None:
        live = self.dir / "orders.sqlite"
        manager = self.manager(live)
        manager.submit(*request("before"), 2400)
        backup = self.dir / "backup" / "orders-1.sqlite"
        manifest = manager.backup(backup)
        self.assertEqual(len(manifest["sha256"]), 64)
        with self.assertRaises(OrderError):
            manager.backup(backup)  # never overwrite a backup
        manager.submit(*request("after"), 2400)  # sent after the backup
        manager.close()

        # restore of an outdated backup: the broker knows "after", the backup does not -> stays locked
        restored = self.dir / "restored.sqlite"
        restore_journal(backup, restored)
        old = self.manager(restored)
        self.assertFalse(old.positions_reconciled)
        with self.assertRaisesRegex(OrderError, "unreconciled"):
            old.submit(*request("new"), 2400)
        self.assertEqual(self.broker.submissions, 2)  # "before" was not resubmitted either
        kinds = [row[0] for row in old.db.execute("SELECT kind FROM journal")]
        self.assertIn("restored", kinds)
        old.close()

        # restore of a current backup reconciles and unlocks
        current = self.dir / "backup" / "orders-2.sqlite"
        source = self.manager(live)
        source.backup(current)
        source.close()
        fresh = self.dir / "fresh.sqlite"
        restore_journal(current, fresh)
        self.assertTrue(self.manager(fresh).positions_reconciled)

    def test_restore_refuses_tampered_backup_and_existing_target(self) -> None:
        manager = self.manager()
        backup = self.dir / "b.sqlite"
        manager.backup(backup)
        with self.assertRaisesRegex(OrderError, "exists"):
            restore_journal(backup, self.dir / "orders.sqlite")
        backup.write_bytes(backup.read_bytes() + b"x")
        with self.assertRaisesRegex(OrderError, "manifest"):
            restore_journal(backup, self.dir / "other.sqlite")
        self.assertFalse((self.dir / "other.sqlite").exists())


class FailingRead:
    """Wraps a raw SQLite connection and fails the next SELECT like a broken disk (outside any transaction)."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self._db = db
        self.fail = True

    def execute(self, sql: str, params: Any = ()) -> Any:
        if self.fail and sql.lstrip().upper().startswith("SELECT"):
            self.fail = False
            raise sqlite3.OperationalError("disk I/O error")
        return self._db.execute(sql, params)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


class ReviewFindingsTests(_OrderBase):
    """PR #38 review (Codex): read failures latch, backup/restore never overwrite."""

    def test_failed_heartbeat_is_a_permanent_loss_for_the_order_path(self) -> None:
        """PR #38 review 2: a heartbeat that could not be written must stop orders until a new acquisition."""
        from unittest import mock

        manager = self.manager()
        failing = mock.patch.object(self.lock, "_write_owner", side_effect=OSError("disk full"))
        with failing, self.assertRaisesRegex(LockError, "heartbeat failed"):
            self.lock.renew()
        self.assertFalse(self.lock.held)  # the disk works again; the loss stays
        with self.assertRaisesRegex(OrderError, "order lock not held"):
            manager.submit(*request("z"), 2400)
        self.assertEqual(self.broker.submissions, 0)
        self.lock.release()
        self.lock.acquire()  # only a new acquisition restores ownership
        self.assertEqual(manager.submit(*request("z"), 2400), S.SUBMITTED)

    def test_read_failure_outside_a_transaction_blocks_until_restart(self) -> None:
        manager = self.manager()
        manager.submit(*request("x"), 2400)
        manager.db._db = FailingRead(manager.db._db)
        with self.assertRaisesRegex(OrderError, "persistence failure"):
            manager.status("x")
        self.assertTrue(manager.persistence_failed)
        with self.assertRaisesRegex(OrderError, "persistence failure earlier"):
            manager.submit(*request("y"), 2400)  # the disk works again, the session stays blocked
        self.assertEqual(self.broker.submissions, 1)
        manager.close()
        restarted = self.manager()
        self.assertEqual(restarted.submit(*request("y"), 2400), S.SUBMITTED)

    def test_restore_never_overwrites_a_journal_created_meanwhile(self) -> None:
        from unittest import mock

        from localtrader import orders

        manager = self.manager()
        backup = self.dir / "b.sqlite"
        manager.backup(backup)
        target = self.dir / "restored.sqlite"
        real = orders._private_temporary

        def meanwhile(path: Path) -> Path:
            temporary = real(path)
            target.write_bytes(b"journal of another process")
            return temporary

        with mock.patch.object(orders, "_private_temporary", meanwhile), self.assertRaisesRegex(OrderError, "exists"):
            restore_journal(backup, target)
        self.assertEqual(target.read_bytes(), b"journal of another process")
        self.assertEqual(sorted(p.name for p in self.dir.glob(".*.tmp")), [])  # private copy removed

    def test_concurrent_restores_publish_only_once(self) -> None:
        manager = self.manager()
        backup = self.dir / "b.sqlite"
        manager.backup(backup)
        target = self.dir / "restored.sqlite"
        restore_journal(backup, target)
        first = target.read_bytes()
        with self.assertRaisesRegex(OrderError, "exists"):
            restore_journal(backup, target)
        self.assertEqual(target.read_bytes(), first)

    def test_backup_is_exclusive_and_failures_leave_nothing_behind(self) -> None:
        from unittest import mock

        from localtrader import orders

        manager = self.manager()
        target = self.dir / "backup" / "orders.sqlite"
        Path(str(target) + ".manifest.json").parent.mkdir(parents=True)
        Path(str(target) + ".manifest.json").write_text("{}")
        with self.assertRaisesRegex(OrderError, "exists"):
            manager.backup(target)  # an existing manifest is never replaced
        self.assertFalse(target.exists())
        Path(str(target) + ".manifest.json").unlink()
        failing = mock.patch.object(orders, "_publish_exclusive", side_effect=OSError("disk full"))
        with failing, self.assertRaises(OSError):
            manager.backup(target)
        self.assertEqual(list(target.parent.iterdir()), [])  # no partial backup, no temporary copy
        manager.backup(target)
        self.assertTrue(target.exists())


if __name__ == "__main__":
    unittest.main()
