"""Single active order-producing instance per account (spec §18.6, T-013).

``InstanceLock`` combines two mechanisms:

- an exclusive, non-blocking ``flock`` on ``<path>``, held for the whole life of the process. The operating
  system releases it when the process dies, so a crash never leaves a stale lock behind, and a second process
  can never acquire it while the first one runs;
- an owner record next to it (``<path>.owner.json``) with a random instance ID, a **generation** that grows
  with every acquisition (fencing token), PID, host and a heartbeat. ``assert_held()`` re-reads the record and
  checks that the lock file was not replaced, so a lost lock (file deleted or taken over after a crash of the
  holder, clock jump, manual intervention) stops order submission instead of being noticed later.

A lease (``lease_seconds``) bounds how long a holder may go without renewing; an expired lease counts as lost.
The lease runs on a monotonic clock, so a wall-clock jump can neither extend nor revive it, and a lost lock
stays lost for this object until it is released and acquired again. Ownership is bound to the acquiring
process: a forked child inherits the descriptor but never counts as holder and cannot release the parent's lock.
An unreadable owner record is not treated as a first start (its generation would be reused): acquisition is
refused until a person inspects it.
The lock is local to one machine and file system; a second host needs its own coordination (not supported:
the paper service runs on one machine, spec §18.6).
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import IO, Any, Protocol


class LockError(RuntimeError):
    pass


class OrderLock(Protocol):
    """What the order path needs from a lock: raise ``LockError`` unless still held."""

    def assert_held(self) -> None: ...


@dataclass(frozen=True, slots=True)
class LockOwner:
    instance_id: str
    generation: int
    pid: int
    host: str
    acquired_at: str
    heartbeat_at: str


class InstanceLock:
    def __init__(
        self,
        path: str | Path,
        lease_seconds: float = 120.0,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if lease_seconds <= 0:
            raise LockError("lease must be positive")
        self.path = Path(path)
        self.owner_path = self.path.with_name(self.path.name + ".owner.json")
        self.lease = timedelta(seconds=lease_seconds)
        self.clock = clock  # wall clock: only for the human-readable owner record
        self.monotonic = monotonic  # lease deadline
        self.instance_id = uuid.uuid4().hex
        self.generation = 0
        self._handle: IO[str] | None = None
        self._inode: tuple[int, int] | None = None
        self._renewed: float | None = None
        self._pid: int | None = None
        self._lost: str | None = None

    # ------------------------------------------------------------------ acquire / release
    def acquire(self) -> LockOwner:
        if self._handle is not None:
            raise LockError("lock already acquired by this object")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            holder = self._read_owner_lenient()
            who = f" (held by pid {holder.get('pid')} on {holder.get('host')})" if holder else ""
            raise LockError(f"another instance holds the order lock{who}") from None
        try:
            stat = os.fstat(handle.fileno())
            previous = self._read_owner()  # raises on a damaged record: never restart the generation at 1
            generation = int(previous["generation"]) + 1 if previous else 1
            now = self.clock()
            owner = LockOwner(
                self.instance_id, generation, os.getpid(), socket.gethostname(), now.isoformat(), now.isoformat()
            )
            self._write_owner(owner)
        except BaseException as exc:
            # roll the acquisition back completely: no flock and no state may stay behind
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()
            if isinstance(exc, LockError):
                raise
            raise LockError(f"lock acquisition failed: {type(exc).__name__}") from exc
        self._handle, self._inode = handle, (stat.st_dev, stat.st_ino)
        self.generation = generation
        self._pid = os.getpid()
        self._renewed = self.monotonic()
        self._lost = None
        return owner

    def release(self) -> None:
        if self._handle is None:
            return
        if os.getpid() == self._pid:
            # the owner record stays (it carries the generation for the next holder); the flock is released
            fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        # a forked child only closes its inherited descriptor: LOCK_UN would drop the parent's lock
        self._handle.close()
        self._handle = self._inode = self._renewed = self._pid = None

    def __enter__(self) -> InstanceLock:
        self.acquire()
        return self

    def __exit__(self, *args: object) -> None:
        self.release()

    # ------------------------------------------------------------------ lease and checks
    def renew(self) -> None:
        """Heartbeat; fails (and the lock counts as lost) if the lease already expired or the record changed."""
        self.assert_held()
        owner = self._read_owner()
        self._write_owner(LockOwner(**{**owner, "heartbeat_at": self.clock().isoformat()}))
        self._renewed = self.monotonic()

    def assert_held(self) -> None:
        """Raise ``LockError`` unless this instance still owns the lock; call before every broker submission.

        Once lost, the lock stays lost for this object (no revival by a clock change or a restored record).
        """
        if self._handle is None or self._inode is None or self._renewed is None or self._pid is None:
            raise LockError("order lock not held")
        if os.getpid() != self._pid:
            raise LockError("order lock belongs to another process (forked child)")
        if self._lost is not None:
            raise LockError(f"order lock lost earlier: {self._lost}; release and acquire again")
        try:
            self._check()
        except LockError as exc:
            self._lost = str(exc)
            raise

    def _check(self) -> None:
        assert self._renewed is not None
        try:
            stat = self.path.stat()
        except OSError:
            raise LockError("order lock file disappeared") from None
        if (stat.st_dev, stat.st_ino) != self._inode:
            raise LockError("order lock file was replaced")
        try:
            owner = self._read_owner()
        except LockError:
            raise LockError("order lock owner record unreadable") from None
        if (
            owner.get("instance_id") != self.instance_id
            or owner.get("generation") != self.generation
            or owner.get("pid") != self._pid
        ):
            raise LockError("order lock taken over by another instance")
        if self.monotonic() - self._renewed > self.lease.total_seconds():
            raise LockError("order lock lease expired")

    @property
    def held(self) -> bool:
        try:
            self.assert_held()
        except LockError:
            return False
        return True

    # ------------------------------------------------------------------ owner record
    def _read_owner(self) -> dict[str, Any]:
        """``{}`` only if there is no record yet (first start); a damaged record raises ``LockError``."""
        try:
            text = self.owner_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise LockError(f"owner record unreadable ({type(exc).__name__}); inspect {self.owner_path}") from None
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("generation"), int)
            or isinstance(data.get("generation"), bool)
            or data["generation"] < 1
            or set(data) != set(LockOwner.__slots__)
        ):
            raise LockError(f"owner record damaged; inspect {self.owner_path} before starting again")
        return data

    def _read_owner_lenient(self) -> dict[str, Any]:
        try:
            return self._read_owner()
        except LockError:
            return {}

    def _write_owner(self, owner: LockOwner) -> None:
        # only the flock holder writes, so one temporary name per lock is enough
        temporary = self.owner_path.with_name(self.owner_path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(_as_dict(owner), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.owner_path)


def _as_dict(owner: LockOwner) -> dict[str, Any]:
    return {field: getattr(owner, field) for field in LockOwner.__slots__}
