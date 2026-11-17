"""Append-only attempt journal for a preregistered experiment (T-008 protocol, "Versuchsjournal").

Every planned run first reserves an attempt with a unique identity, then appends status events. Nothing
is ever rewritten: aborted, failed and rejected attempts keep their ID and status, and unstarted slots stay
visible. Each event carries the SHA-256 of the previous event, so edits or deletions are detectable.

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
        if not self.events_path.exists():
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
        return events

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
