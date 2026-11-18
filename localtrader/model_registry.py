"""Model registry with an auditable active pointer (T-008 protocol, "registry").

* The starting champion is cash (``CASH-EUR-v1``). It is always registered and is the rollback target.
* A model version is immutable once registered and must carry identity, code commit, dependency lock,
  data and criteria versions, parameters, periods, seeds, costs, fold reports, audit references and its
  activation decision.
* Passing the historical and central gates qualifies a version for **shadow at most**. Paper needs a
  separate operational decision outside this registry; live is never possible here.
* Every pointer change is a small transaction: an *intent* line (with the full previous pointer) is
  appended to the audit log first, then the pointer is replaced atomically (temp file + ``os.replace``),
  then a *commit* line is appended. If the intent cannot be written, nothing changes. If the process dies
  or the commit cannot be written, the pointer refers to an uncommitted transaction: ``active()`` refuses
  to answer (fail closed) until ``recover()`` restores the previous pointer and audits that. Rollback is only allowed once the caller confirms that new entries are blocked, orders are
  reconciled and positions of the old version are flat or protected; the registry records that
  confirmation, it does not perform it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

CASH_CHAMPION = "CASH-EUR-v1"
REQUIRED_FIELDS = (
    "model_id",
    "code_commit",
    "dependency_lock_digest",
    "data_versions",
    "criteria_version",
    "parameters",
    "periods",
    "seeds",
    "costs",
    "fold_reports",
    "audit_refs",
    "activation_decision",
)
MODES = ("shadow",)  # the only mode this registry can activate
MODEL_ID = re.compile(r"^[A-Z0-9][A-Za-z0-9._-]{2,80}$")
COMMIT = re.compile(r"^[0-9a-f]{7,40}$")
DIGEST = re.compile(r"^[0-9a-f]{64}$")
ROLLBACK_CONFIRMATIONS = ("new_entries_blocked", "orders_and_fills_reconciled", "positions_flat_or_protected")


class RegistryError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ModelRegistry:
    def __init__(self, directory: str | Path, clock: Callable[[], str] = _now) -> None:
        self.root = Path(directory)
        self.models = self.root / "models"
        self.models.mkdir(parents=True, exist_ok=True)
        self.pointer_path = self.root / "active.json"
        self.audit_path = self.root / "audit.jsonl"
        self.clock = clock
        if not self.pointer_path.exists():
            self._switch({"model_id": CASH_CHAMPION, "mode": "cash", "since": self.clock()}, "init", {}, None)

    # ------------------------------------------------------------------ versions
    def register(self, record: dict[str, Any]) -> Path:
        missing = [name for name in REQUIRED_FIELDS if name not in record or record[name] in (None, "", [], {})]
        if missing:
            raise RegistryError(f"missing fields: {missing}")
        model_id = str(record["model_id"])
        if not MODEL_ID.match(model_id) or model_id == CASH_CHAMPION:
            raise RegistryError(f"invalid model_id {model_id!r}")
        if not COMMIT.match(str(record["code_commit"])):
            raise RegistryError("code_commit must be a git SHA")
        if not DIGEST.match(str(record["dependency_lock_digest"])):
            raise RegistryError("dependency_lock_digest must be a SHA-256 hex digest")
        decision = record["activation_decision"]
        if not isinstance(decision, dict) or decision.get("eligible_mode") not in (None, *MODES):
            raise RegistryError("activation_decision.eligible_mode must be 'shadow' or null")
        path = self.models / f"{model_id}.json"
        payload = json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        try:
            with path.open("x", encoding="utf-8") as handle:  # immutable: never overwrite a version
                handle.write(payload)
        except FileExistsError as error:
            raise RegistryError(f"{model_id} is already registered") from error
        self._audit(
            {"action": "register", "model_id": model_id, "sha256": hashlib.sha256(payload.encode()).hexdigest()}
        )
        return path

    def get(self, model_id: str) -> dict[str, Any]:
        path = self.models / f"{model_id}.json"
        if not path.exists():
            raise RegistryError(f"unknown model {model_id}")
        record: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return record

    # ------------------------------------------------------------------ active pointer
    def active(self) -> dict[str, Any]:
        """The active pointer, only if its transaction is committed in the audit log (fail closed)."""
        pointer = self._read_pointer()
        if pointer.get("txn") not in self._committed():
            raise RegistryError("active pointer belongs to an uncommitted change; run recover()")
        return pointer

    def recover(self) -> dict[str, Any]:
        """Restore the last *committed* pointer after an interrupted change. Idempotent.

        The target is resolved from the audit log (the newest commit and the pointer its intent wrote),
        never from an intent's ``previous`` field, which may itself be an uncommitted intermediate state.
        The committed pointer is written back unchanged, with its original transaction id, so repeated
        crashes during recovery always converge on the same state and no intermediate pointer is ever
        confirmed. The audit note is written after the pointer; if it fails, the state is already safe.
        """
        pointer = self._read_pointer()
        committed = self._committed()
        if pointer.get("txn") in committed:
            return pointer
        target = self._last_committed_pointer()
        self._write_pointer(target)
        self._audit({"action": "recover", "undone": pointer.get("txn"), "restored": target["txn"]})
        return target

    def _last_committed_pointer(self) -> dict[str, Any]:
        entries = self.audit()
        last_commit = next((e for e in reversed(entries) if e.get("phase") == "commit"), None)
        if last_commit is None:
            raise RegistryError("no committed state to recover; manual review required")
        intent = next((e for e in entries if e.get("phase") == "intent" and e.get("txn") == last_commit["txn"]), None)
        if intent is None or not isinstance(intent.get("to"), dict) or intent["to"].get("txn") != last_commit["txn"]:
            raise RegistryError("committed transaction without its intent; manual review required")
        return dict(intent["to"])

    def activate(self, model_id: str, mode: str, approved_by: str) -> dict[str, Any]:
        """Point to a registered version in shadow mode; only if its gates made it shadow-eligible."""
        if mode not in MODES:
            raise RegistryError(f"mode {mode!r} cannot be activated here (shadow only; paper/live never)")
        if not approved_by:
            raise RegistryError("activation needs the approving decision maker")
        record = self.get(model_id)
        decision = record["activation_decision"]
        if decision.get("eligible_mode") != mode or not decision.get("gates_passed"):
            raise RegistryError(f"{model_id} is not eligible for {mode}")
        previous = self.active()
        if previous["model_id"] != CASH_CHAMPION:
            raise RegistryError("roll back to cash before activating another version")
        pointer = {"model_id": model_id, "mode": mode, "since": self.clock(), "approved_by": approved_by}
        return self._switch(pointer, "activate", {}, previous)

    def rollback(self, reason: str, confirmations: dict[str, bool]) -> dict[str, Any]:
        """Return the pointer to cash after the caller confirmed the controlled wind-down."""
        if not reason:
            raise RegistryError("rollback needs a reason")
        missing = [name for name in ROLLBACK_CONFIRMATIONS if confirmations.get(name) is not True]
        if missing:
            raise RegistryError(f"rollback not confirmed: {missing}")
        previous = self.active()
        pointer = {"model_id": CASH_CHAMPION, "mode": "cash", "since": self.clock()}
        return self._switch(pointer, "rollback", {"reason": reason, **confirmations}, previous)

    # ------------------------------------------------------------------ internals
    def _switch(
        self, pointer: dict[str, Any], action: str, details: dict[str, Any], previous: dict[str, Any] | None
    ) -> dict[str, Any]:
        txn = uuid.uuid4().hex
        new = {**pointer, "txn": txn}
        # 1) intent first: if this fails, nothing has changed
        self._audit({"action": action, "phase": "intent", "txn": txn, "previous": previous, "to": new, **details})
        # 2) atomic pointer replacement
        self._write_pointer(new)
        # 3) commit; until it exists active() refuses the new pointer
        self._audit({"action": action, "phase": "commit", "txn": txn, "model_id": new["model_id"]})
        return new

    def _read_pointer(self) -> dict[str, Any]:
        pointer: dict[str, Any] = json.loads(self.pointer_path.read_text(encoding="utf-8"))
        return pointer

    def _committed(self) -> set[str]:
        return {str(e["txn"]) for e in self.audit() if e.get("phase") == "commit" and "txn" in e}

    def _write_pointer(self, pointer: dict[str, Any]) -> None:
        temporary = self.pointer_path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(pointer, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.pointer_path)  # atomic on POSIX and Windows

    def _audit(self, entry: dict[str, Any]) -> None:
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"at": self.clock(), **entry}, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def audit(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
