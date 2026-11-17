"""Append-only attempt journal for a preregistered experiment (T-008 protocol, "Versuchsjournal").

Every planned run first reserves an attempt with a unique identity, then appends status events. Nothing
is ever rewritten: aborted, failed and rejected attempts keep their ID and status, and unstarted slots stay
visible. Each event carries the SHA-256 of the previous event, so edits inside the journal are detectable.

A cut-off end of the journal is detected in two independent ways: every status is also recorded as an
exclusively created marker file (``attempts/<id>.<status>``, written *before* the event), and
``head.json`` keeps the sequence and hash of the last event. A marker without its event, or fewer events
than the head records, blocks the journal (fail closed, manual recovery). Because a status marker can be
created only once, a failed or completed attempt can never be started again, even after truncation.
Limit: someone who rewrites events, markers and head consistently is not detected; that needs an
external anchor (planned with the central holdout service).

Reservation uses exclusive file creation (one file per attempt ID), which is atomic within one file
system. Across machines or worktrees this is not a global lock; the central holdout service needs its
own transactional reservation (planned, High).

Budgets come from the protocol: research strategy/reference slots and at most one central bundle.
Research and central use are counted separately.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

KINDS = ("strategy", "reference", "central")
STATUSES = ("reserved", "started", "completed", "failed", "aborted", "rejected")
TERMINAL = frozenset({"completed", "failed", "aborted", "rejected"})
TRANSITIONS = {
    "reserved": {"started", "aborted"},
    "started": {"completed", "failed", "aborted", "rejected"},
}
ATTEMPT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$")
GENESIS = "0" * 64


class JournalError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Budget:
    strategy: int
    reference: int
    central: int

    @classmethod
    def from_protocol(cls, protocol: dict[str, Any]) -> Budget:
        budget = protocol["budget"]
        return cls(
            int(budget["max_active_strategy_evaluations"]),
            int(budget["max_reference_evaluations"]),
            int(budget["max_central_candidate_bundles"]),
        )

    def limit(self, kind: str) -> int:
        return {"strategy": self.strategy, "reference": self.reference, "central": self.central}[kind]


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _event_hash(event: dict[str, Any]) -> str:
    body = {key: value for key, value in event.items() if key != "hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class AttemptJournal:
    """One directory per experiment: ``attempts/<id>.json`` (reservations) and ``events.jsonl``."""

    def __init__(
        self, directory: str | Path, experiment_id: str, budget: Budget, clock: Callable[[], str] = _now
    ) -> None:
        self.root = Path(directory) / experiment_id
        self.experiment_id = experiment_id
        self.budget = budget
        self.clock = clock
        (self.root / "attempts").mkdir(parents=True, exist_ok=True)
        self.events_path = self.root / "events.jsonl"

    # ------------------------------------------------------------------ reading and verification
    def events(self) -> list[dict[str, Any]]:
        """All events after verifying the hash chain; raises on any tampering."""
        head = self._head()
        if not self.events_path.exists():
            if head["sequence"]:
                raise JournalError("journal missing but head records events")
            return []
        events = []
        previous = GENESIS
        for number, line in enumerate(self.events_path.read_text(encoding="utf-8").splitlines(), 1):
            event = json.loads(line)
            if event.get("previous") != previous or event.get("hash") != _event_hash(event):
                raise JournalError(f"journal chain broken at event {number}")
            if event.get("sequence") != number:
                raise JournalError(f"journal sequence broken at event {number}")
            previous = event["hash"]
            events.append(event)
        if len(events) < head["sequence"]:
            raise JournalError(f"journal truncated: {len(events)} events, head records {head['sequence']}")
        recorded = events[head["sequence"] - 1]["hash"] if head["sequence"] else GENESIS
        if recorded != head["hash"]:
            raise JournalError("journal does not match its head")
        if len(events) > head["sequence"] + 1:
            raise JournalError("journal is ahead of its head by more than one event")
        return events

    def _head(self) -> dict[str, Any]:
        path = self.root / "head.json"
        if not path.exists():
            return {"sequence": 0, "hash": GENESIS}
        head: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return head

    def _write_head(self, event: dict[str, Any]) -> None:
        path = self.root / "head.json"
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump({"sequence": event["sequence"], "hash": event["hash"]}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)

    def status(self) -> dict[str, dict[str, Any]]:
        """Current state per attempt, from the verified chain."""
        state: dict[str, dict[str, Any]] = {}
        for event in self.events():
            attempt = event["attempt_id"]
            if event["status"] == "reserved":
                state[attempt] = {"kind": event["kind"], "status": "reserved", "slot": event["slot"]}
            else:
                state[attempt]["status"] = event["status"]
                if "reason" in event:
                    state[attempt]["reason"] = event["reason"]
        reserved_files = {path.stem for path in (self.root / "attempts").glob("*.json")}
        if reserved_files != set(state):
            raise JournalError("reservation files and journal disagree")
        seen: dict[str, set[str]] = {}
        for event in self.events():
            if event["status"] != "reserved":
                seen.setdefault(event["attempt_id"], set()).add(event["status"])
        for marker in (self.root / "attempts").iterdir():
            attempt, _, status = marker.name.rpartition(".")
            if status in STATUSES and status not in seen.get(attempt, set()):
                raise JournalError(f"status marker {marker.name} without journal event (truncated or crashed)")
        return state

    def counts(self) -> dict[str, dict[str, int]]:
        """Used slots per kind (every reservation counts, whatever its outcome) and the remaining budget."""
        used = dict.fromkeys(KINDS, 0)
        for attempt in self.status().values():
            used[attempt["kind"]] += 1
        return {kind: {"used": used[kind], "remaining": self.budget.limit(kind) - used[kind]} for kind in KINDS}

    # ------------------------------------------------------------------ writing
    def _append(self, event: dict[str, Any]) -> dict[str, Any]:
        existing = self.events()
        event = {
            **event,
            "experiment_id": self.experiment_id,
            "sequence": len(existing) + 1,
            "recorded_at": self.clock(),
            "previous": existing[-1]["hash"] if existing else GENESIS,
        }
        event["hash"] = _event_hash(event)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        self._write_head(event)
        return event

    def reserve(self, attempt_id: str, kind: str, slot: dict[str, Any]) -> dict[str, Any]:
        """Reserve a unique attempt before any data is read. Fails if the ID exists or the budget is used up."""
        if kind not in KINDS:
            raise JournalError(f"unknown kind {kind!r}")
        if not ATTEMPT_ID.match(attempt_id):
            raise JournalError(f"invalid attempt id {attempt_id!r}")
        if self.counts()[kind]["remaining"] <= 0:
            raise JournalError(f"{kind} budget exhausted")
        marker = self.root / "attempts" / f"{attempt_id}.json"
        try:
            with marker.open("x", encoding="utf-8") as handle:  # atomic: fails if already reserved
                json.dump({"attempt_id": attempt_id, "kind": kind, "slot": slot}, handle, sort_keys=True)
        except FileExistsError as error:
            raise JournalError(f"attempt {attempt_id} already reserved") from error
        return self._append({"attempt_id": attempt_id, "kind": kind, "slot": slot, "status": "reserved"})

    def transition(self, attempt_id: str, status: str, reason: str | None = None) -> dict[str, Any]:
        """Append a status change; terminal states are final. Failures and aborts need a reason."""
        current = self.status().get(attempt_id)
        if current is None:
            raise JournalError(f"attempt {attempt_id} was never reserved")
        if status not in TRANSITIONS.get(current["status"], set()):
            raise JournalError(f"{attempt_id}: {current['status']} -> {status} is not allowed")
        if status in {"failed", "aborted", "rejected"} and not reason:
            raise JournalError(f"{status} needs a reason")
        event: dict[str, Any] = {"attempt_id": attempt_id, "kind": current["kind"], "status": status}
        if reason:
            event["reason"] = reason
        marker = self.root / "attempts" / f"{attempt_id}.{status}"
        try:
            marker.open("x").close()  # once per status, ever; written before the event (fail closed)
        except FileExistsError as error:
            raise JournalError(f"{attempt_id} already had status {status}") from error
        return self._append(event)

    def summary(self, planned_slots: list[str] | None = None) -> dict[str, Any]:
        """Counts per kind and status; planned slots without a reservation are listed as unstarted."""
        state = self.status()
        by_status: dict[str, int] = {}
        for attempt in state.values():
            by_status[attempt["status"]] = by_status.get(attempt["status"], 0) + 1
        return {
            "experiment_id": self.experiment_id,
            "counts": self.counts(),
            "by_status": by_status,
            "open": sorted(a for a, v in state.items() if v["status"] not in TERMINAL),
            "unstarted": sorted(set(planned_slots or []) - set(state)),
        }
