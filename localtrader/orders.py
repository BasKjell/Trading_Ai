"""SQLite order journal for a single simulated account. Money is in account-currency cents."""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NoReturn

from .broker import Broker, BrokerEvent, BrokerOrder
from .domain import Direction, Market, OrderIntent, OrderStatus, RiskDecision
from .instance_lock import LockError, OrderLock

S = OrderStatus
TERMINAL = {S.FILLED, S.CANCELLED, S.REJECTED}
BROKER_STATES = {S.SUBMITTED, S.PARTIALLY_FILLED, *TERMINAL}
NEXT = {
    S.UNKNOWN: BROKER_STATES,
    S.SUBMITTED: BROKER_STATES,
    S.PARTIALLY_FILLED: {S.PARTIALLY_FILLED, S.FILLED, S.CANCELLED},
}
SCHEMA = """
CREATE TABLE IF NOT EXISTS account (
    id INTEGER PRIMARY KEY CHECK(id=1), opening_cash INTEGER NOT NULL, opening_positions TEXT NOT NULL,
    reconciled INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS orders (
    client_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, direction TEXT NOT NULL, quantity INTEGER NOT NULL,
    max_debit INTEGER NOT NULL, status TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 0,
    filled INTEGER NOT NULL DEFAULT 0, cash_value INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS journal (
    seq INTEGER PRIMARY KEY, at TEXT NOT NULL, client_id TEXT, kind TEXT NOT NULL, detail TEXT NOT NULL
);
"""


class OrderError(ValueError):
    pass


class _GuardedCursor:
    def __init__(self, cursor: sqlite3.Cursor, on_error: Callable[[BaseException], NoReturn]) -> None:
        self._cursor = cursor
        self._on_error = on_error

    def fetchone(self) -> Any:
        try:
            return self._cursor.fetchone()
        except sqlite3.Error as exc:
            self._on_error(exc)

    def fetchall(self) -> list[Any]:
        try:
            return self._cursor.fetchall()
        except sqlite3.Error as exc:
            self._on_error(exc)

    def __iter__(self) -> Iterator[Any]:
        return iter(self.fetchall())

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _GuardedConnection:
    """Every SQLite error, also from reads outside a transaction, latches the session's persistence failure
    (PR #38 review): a journal that could not be read is as untrustworthy as one that could not be written."""

    def __init__(self, db: sqlite3.Connection, on_error: Callable[[BaseException], NoReturn]) -> None:
        self._db = db
        self._on_error = on_error

    def execute(self, sql: str, params: Any = ()) -> _GuardedCursor:
        try:
            return _GuardedCursor(self._db.execute(sql, params), self._on_error)
        except sqlite3.Error as exc:
            self._on_error(exc)

    def _guard(self, name: str, *args: Any) -> Any:
        try:
            return getattr(self._db, name)(*args)
        except sqlite3.Error as exc:
            self._on_error(exc)

    def executescript(self, script: str) -> Any:
        return self._guard("executescript", script)

    def commit(self) -> None:
        self._guard("commit")

    def rollback(self) -> None:
        self._guard("rollback")

    def backup(self, target: sqlite3.Connection) -> None:
        self._guard("backup", target)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._db, name)


class OrderManager:
    """Order journal and dispatch for one account.

    ``lock`` (T-013): only the holder of the account's ``InstanceLock`` may submit; it is checked right before
    every broker submission. Without a lock, submission is refused. A critical persistence error (SQLite or
    file system) latches ``persistence_failed`` for the life of this object: no further orders until a restart
    reopens the journal and a complete reconciliation succeeds.
    """

    def __init__(
        self,
        path: Path,
        broker: Broker,
        opening_cash: int,
        opening_positions: dict[str, int] | None = None,
        lock: OrderLock | None = None,
    ):
        holdings = dict(opening_positions or {})
        if (
            type(opening_cash) is not int
            or opening_cash < 0
            or any(type(v) is not int or v < 0 for v in holdings.values())
        ):
            raise OrderError("invalid opening balances")
        self.broker = broker
        self.lock = lock
        self.persistence_failed = False
        raw = sqlite3.connect(path, isolation_level=None)
        raw.row_factory = sqlite3.Row
        self.db: Any = _GuardedConnection(raw, self._persistence_failure)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        with self._transaction():
            self.db.execute(
                "INSERT OR IGNORE INTO account(id,opening_cash,opening_positions) VALUES (1,?,?)",
                (opening_cash, json.dumps(holdings, sort_keys=True)),
            )
        self.reconcile()

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.Error as exc:
            self._persistence_failure(exc)
        try:
            yield
            self.db.commit()
        except sqlite3.Error as exc:
            with suppress(sqlite3.Error, OrderError):
                self.db.rollback()
            self._persistence_failure(exc)
        except BaseException:
            with suppress(sqlite3.Error, OrderError):  # a failed rollback must not hide the original error
                self.db.rollback()
            raise

    def _persistence_failure(self, exc: BaseException) -> NoReturn:
        self.persistence_failed = True  # in memory: the journal itself may be unwritable
        raise OrderError(f"persistence failure, new orders blocked: {type(exc).__name__}") from exc

    def _log(self, kind: str, detail: str, client_id: str | None = None) -> None:
        self.db.execute(
            "INSERT INTO journal(at,client_id,kind,detail) VALUES(?,?,?,?)",
            (datetime.now(UTC).isoformat(), client_id, kind, detail),
        )

    @property
    def positions_reconciled(self) -> bool:
        return bool(self.db.execute("SELECT reconciled FROM account").fetchone()[0])

    def _alarm(self, detail: str) -> None:
        self.db.execute("UPDATE account SET reconciled=0")
        self._log("alarm", detail)

    def _row(self, client_id: str) -> sqlite3.Row | None:
        return self.db.execute("SELECT * FROM orders WHERE client_id=?", (client_id,)).fetchone()

    @staticmethod
    def _order(row: sqlite3.Row) -> BrokerOrder:
        return BrokerOrder(
            row["client_id"], row["symbol"], Direction(row["direction"]), row["quantity"], row["max_debit"]
        )

    def status(self, client_id: str) -> OrderStatus:
        row = self._row(client_id)
        if row is None:
            raise KeyError(client_id)
        return S(row["status"])

    def _balances(self) -> tuple[int, dict[str, int], int, dict[str, int]]:
        account = self.db.execute("SELECT * FROM account").fetchone()
        cash = int(account["opening_cash"])
        positions: dict[str, int] = json.loads(account["opening_positions"])
        reserved_cash = 0
        reserved_shares: dict[str, int] = {}
        for row in self.db.execute("SELECT * FROM orders"):
            symbol = row["symbol"]
            buying = row["direction"] == Direction.LONG
            sign = 1 if buying else -1
            cash -= sign * row["cash_value"]
            positions[symbol] = positions.get(symbol, 0) + sign * row["filled"]
            if row["status"] not in TERMINAL:
                if buying:
                    reserved_cash += row["max_debit"] - row["cash_value"]
                else:
                    reserved_shares[symbol] = reserved_shares.get(symbol, 0) + row["quantity"] - row["filled"]
        return cash, positions, reserved_cash, reserved_shares

    def _apply(self, event: BrokerEvent, *, snapshot: bool) -> None:
        order = event.order
        row = self._row(order.client_id)
        if row is None or self._order(row) != order:
            raise OrderError(f"unmatched broker order {order.client_id}")
        if type(event.revision) is not int or not 0 < event.revision < 2**63:
            raise OrderError("invalid broker revision")
        if event.revision < row["revision"]:
            if snapshot:
                raise OrderError("stale broker snapshot")
            return
        if event.revision == row["revision"]:
            if (event.status, event.filled, event.cash_value) != (row["status"], row["filled"], row["cash_value"]):
                raise OrderError("conflicting broker revision")
            return
        old = S(row["status"])
        terminal_repeat = old in TERMINAL and (event.status, event.filled, event.cash_value) == (
            old,
            row["filled"],
            row["cash_value"],
        )
        if not terminal_repeat and event.status not in NEXT.get(old, set()):
            raise OrderError(f"illegal transition {old} -> {event.status}")
        if type(event.filled) is not int or not row["filled"] <= event.filled <= order.quantity:
            raise OrderError("invalid cumulative quantity")
        if type(event.cash_value) is not int or not row["cash_value"] <= event.cash_value < 2**63:
            raise OrderError("invalid cumulative cash value")
        if (event.filled == row["filled"]) != (event.cash_value == row["cash_value"]):
            raise OrderError("fill and cash increments disagree")
        if order.direction == Direction.LONG and event.cash_value > order.max_debit:
            raise OrderError("buy exceeds cash limit")
        if event.status in (S.SUBMITTED, S.REJECTED) and event.filled:
            raise OrderError("unexpected execution")
        if (event.status == S.FILLED) != (event.filled == order.quantity):
            raise OrderError("full fill and status disagree")
        if event.status == S.PARTIALLY_FILLED and event.filled == 0:
            raise OrderError("empty partial fill")
        self.db.execute(
            "UPDATE orders SET status=?, revision=?, filled=?, cash_value=? WHERE client_id=?",
            (event.status, event.revision, event.filled, event.cash_value, order.client_id),
        )
        self._log(
            "transition",
            f"{old}->{event.status} rev={event.revision} filled={event.filled} cash={event.cash_value}",
            order.client_id,
        )

    def observe(self, event: BrokerEvent) -> None:
        """Apply a cumulative notification once. Only a complete reconciliation can clear an alarm."""
        try:
            with self._transaction():
                self._apply(event, snapshot=False)
        except OrderError as exc:
            with self._transaction():
                self._alarm(str(exc))
            raise

    def reconcile(self) -> bool:
        # Commit the lockout first so a crash/disconnect while reading the broker fails closed.
        with self._transaction():
            self.db.execute("UPDATE account SET reconciled=0")
        with self._transaction():
            self.db.execute("SAVEPOINT snapshot_events")
            try:
                view = self.broker.snapshot()
                local_ids = {row[0] for row in self.db.execute("SELECT client_id FROM orders")}
                if view.orders.keys() != local_ids:
                    raise OrderError("broker and journal order IDs differ")
                for client_id, event in view.orders.items():
                    if client_id != event.order.client_id:
                        raise OrderError("snapshot order key mismatch")
                    self._apply(event, snapshot=True)
                cash, positions, reserved_cash, reserved_shares = self._balances()
                if type(view.cash) is not int or cash != view.cash or cash < reserved_cash:
                    raise OrderError("cash mismatch or insufficient reserved cash")
                for symbol in positions.keys() | view.positions.keys():
                    held = positions.get(symbol, 0)
                    reported = view.positions.get(symbol, 0)
                    if type(reported) is not int or held != reported or held < reserved_shares.get(symbol, 0):
                        raise OrderError(f"position mismatch for {symbol}")
            except (OSError, OrderError) as exc:
                self.db.execute("ROLLBACK TO snapshot_events")
                self._alarm(str(exc))
                return False
            self.db.execute("UPDATE account SET reconciled=1")
            return True

    def _assert_lock(self) -> None:
        if self.lock is None:
            raise OrderError("no instance lock: only the lock holder may submit orders")
        try:
            self.lock.assert_held()
        except LockError as exc:
            raise OrderError(f"order lock not held: {exc}") from exc

    # ------------------------------------------------------------------ backup and restore (T-013)
    def backup(self, target: Path) -> dict[str, str]:
        """Consistent online copy of the journal (SQLite backup API) plus a manifest with its SHA-256.

        Written to a private temporary file, hashed, and published only if neither the backup nor its manifest
        exists yet (hard link: atomic and exclusive). Concurrent backups never overwrite each other.
        """
        target.parent.mkdir(parents=True, exist_ok=True)
        manifest_path = Path(str(target) + ".manifest.json")
        if target.exists() or manifest_path.exists():
            raise OrderError(f"backup target exists: {target}")
        temporary = _private_temporary(target)
        try:
            copy = sqlite3.connect(temporary)
            try:
                self.db.backup(copy)
            finally:
                copy.close()
            manifest = {
                "file": target.name,
                "sha256": hashlib.sha256(temporary.read_bytes()).hexdigest(),
                "created_at": datetime.now(UTC).isoformat(),
            }
            _publish_exclusive(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        try:
            with manifest_path.open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(manifest, indent=2))
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            target.unlink()  # our own, just published copy; the other manifest belongs to someone else
            raise OrderError(f"backup manifest exists: {manifest_path}") from None
        return manifest

    def submit(self, intent: OrderIntent, decision: RiskDecision, max_debit: int) -> OrderStatus:
        """Dispatch a risk-approved whole-share order once. Reusing an ID never retries transport."""
        if not decision.allowed or not all(
            math.isfinite(x) for x in (decision.approved_quantity, intent.target_quantity)
        ):
            raise OrderError("risk rejected")
        if intent.order_type != "market" or intent.market not in (Market.EQUITY, Market.ETF):
            raise OrderError("fake adapter supports whole-share equity/ETF market orders only")
        quantity = math.floor(min(decision.approved_quantity, intent.target_quantity))
        order = BrokerOrder(intent.client_order_id, intent.symbol, intent.direction, quantity, max_debit)
        if self._row(order.client_id) is None and self.positions_reconciled:
            self.reconcile()
        with self._transaction():
            row = self._row(order.client_id)
            if row is not None:
                if self._order(row) != order:
                    raise OrderError("client order ID reused for another order")
                return S(row["status"])
            if self.persistence_failed:
                raise OrderError("persistence failure earlier in this session; restart and reconcile first")
            if (
                not self.positions_reconciled
                or not intent.created_at <= decision.decided_at <= datetime.now(UTC) <= intent.valid_until
            ):
                raise OrderError("unreconciled account or invalid order time")
            self._assert_lock()
            cash, positions, reserved_cash, reserved_shares = self._balances()
            if order.direction == Direction.FLAT and quantity > positions.get(order.symbol, 0) - reserved_shares.get(
                order.symbol, 0
            ):
                raise OrderError("insufficient unreserved shares")
            if order.max_debit > cash - reserved_cash:
                raise OrderError("insufficient unreserved cash")
            self.db.execute(
                "INSERT INTO orders(client_id,symbol,direction,quantity,max_debit,status) VALUES(?,?,?,?,?,?)",
                (order.client_id, order.symbol, order.direction, quantity, max_debit, S.NEW),
            )
            self._log("transition", "new", order.client_id)
            self.db.execute("UPDATE orders SET status=? WHERE client_id=?", (S.UNKNOWN, order.client_id))
            self._log("transition", "new->unknown before broker I/O", order.client_id)
            self.db.execute("UPDATE account SET reconciled=0")
        try:
            self._assert_lock()  # again right before broker I/O: the lock may be lost while waiting for SQLite
        except OrderError:
            # never sent: withdraw the row so the journal keeps matching the broker; the account stays locked
            with self._transaction():
                self.db.execute("DELETE FROM orders WHERE client_id=? AND status=?", (order.client_id, S.UNKNOWN))
                self._log("withdrawn", "order lock lost before broker I/O; nothing sent", order.client_id)
            raise
        try:
            self.broker.submit(order)
        except OSError as exc:
            with self._transaction():
                self._alarm(f"submission uncertain for {order.client_id}: {type(exc).__name__}")
            return self.status(order.client_id)
        self.reconcile()
        return self.status(order.client_id)


def _private_temporary(target: Path) -> Path:
    handle, name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    os.close(handle)
    return Path(name)


def _publish_exclusive(source: Path, target: Path) -> None:
    """Make ``source`` visible as ``target`` only if ``target`` does not exist (atomic ``link``)."""
    with source.open("rb") as handle:
        os.fsync(handle.fileno())
    try:
        os.link(source, target)
    except FileExistsError:
        raise OrderError(f"target exists: {target}") from None


def restore_journal(backup: Path, target: Path) -> None:
    """Restore a journal backup to ``target`` (which must not exist; move a damaged journal aside first).

    The backup is copied into a private temporary file per call; exactly these bytes are verified against the
    manifest and SQLite's integrity check before any change. The copy is then marked unreconciled with a
    ``restored`` journal entry and published atomically only if ``target`` still does not exist, so a journal
    created meanwhile by another process is never overwritten. Opening it with ``OrderManager`` reconciles
    against the broker; trading stays locked until positions, orders and executions match completely.
    Orders the broker knows but the backup does not (sent after the backup) make the reconciliation fail, so
    nothing old is resubmitted blindly.
    """
    manifest = json.loads(Path(str(backup) + ".manifest.json").read_text(encoding="utf-8"))
    if target.exists():
        raise OrderError(f"restore target exists: {target}; move it aside first")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = _private_temporary(target)
    try:
        temporary.write_bytes(backup.read_bytes())
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != manifest.get("sha256"):
            raise OrderError("backup does not match its manifest")
        db = sqlite3.connect(temporary, isolation_level=None)
        try:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise OrderError("backup failed the SQLite integrity check")
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE account SET reconciled=0")
            db.execute(
                "INSERT INTO journal(at,client_id,kind,detail) VALUES(?,?,?,?)",
                (datetime.now(UTC).isoformat(), None, "restored", f"from {backup.name} ({manifest['sha256'][:12]})"),
            )
            db.commit()
        finally:
            db.close()
        try:
            _publish_exclusive(temporary, target)
        except OrderError:
            raise OrderError(f"restore target exists: {target}; move it aside first") from None
    finally:
        temporary.unlink(missing_ok=True)
