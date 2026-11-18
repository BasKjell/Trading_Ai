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
The lock is local to one machine and file system; a second host needs its own coordination (not supported:
the paper service runs on one machine, spec §18.6).
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
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
    ) -> None:
        if lease_seconds <= 0:
            raise LockError("lease must be positive")
        self.path = Path(path)
        self.owner_path = self.path.with_name(self.path.name + ".owner.json")
        self.lease = timedelta(seconds=lease_seconds)
        self.clock = clock
        self.instance_id = uuid.uuid4().hex
        self.generation = 0
        self._handle: IO[str] | None = None
        self._inode: tuple[int, int] | None = None
        self._renewed: datetime | None = None

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
            holder = self._read_owner()
            who = f" (held by pid {holder.get('pid')} on {holder.get('host')})" if holder else ""
            raise LockError(f"another instance holds the order lock{who}") from None
        stat = os.fstat(handle.fileno())
        self._handle, self._inode = handle, (stat.st_dev, stat.st_ino)
        previous = self._read_owner()
        self.generation = int(previous.get("generation", 0)) + 1 if previous else 1
        now = self.clock()
        owner = LockOwner(
            self.instance_id, self.generation, os.getpid(), socket.gethostname(), now.isoformat(), now.isoformat()
        )
        self._write_owner(owner)
        self._renewed = now
        return owner

    def release(self) -> None:
        if self._handle is None:
            return
        # the owner record stays (it carries the generation for the next holder); the flock is what is released
        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = self._inode = self._renewed = None

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
        now = self.clock()
        self._write_owner(LockOwner(**{**owner, "heartbeat_at": now.isoformat()}))
        self._renewed = now

    def assert_held(self) -> None:
        """Raise ``LockError`` unless this instance still owns the lock; call before every broker submission."""
        if self._handle is None or self._inode is None or self._renewed is None:
            raise LockError("order lock not held")
        try:
            stat = self.path.stat()
        except OSError:
            raise LockError("order lock file disappeared") from None
        if (stat.st_dev, stat.st_ino) != self._inode:
            raise LockError("order lock file was replaced")
        owner = self._read_owner()
        if owner.get("instance_id") != self.instance_id or owner.get("generation") != self.generation:
            raise LockError("order lock taken over by another instance")
        if self.clock() - self._renewed > self.lease:
            raise LockError("order lock lease expired")
        if self.clock() < self._renewed:
            raise LockError("clock moved backwards; lock state uncertain")

    @property
    def held(self) -> bool:
        try:
            self.assert_held()
        except LockError:
            return False
        return True

    # ------------------------------------------------------------------ owner record
    def _read_owner(self) -> dict[str, Any]:
        try:
            data = json.loads(self.owner_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_owner(self, owner: LockOwner) -> None:
        temporary = self.owner_path.with_name(self.owner_path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(_as_dict(owner), handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.owner_path)


def _as_dict(owner: LockOwner) -> dict[str, Any]:
    return {field: getattr(owner, field) for field in LockOwner.__slots__}
