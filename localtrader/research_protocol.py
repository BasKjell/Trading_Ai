"""Checks for the preregistered T-008 protocol (docs/research/T-008-protocol.json).

The protocol is binding only if the machine-readable JSON and the human-readable plan agree. This module
finds every inconsistency it can check mechanically and blocks a start while any remains (EXP-T008-001:
"bei Abweichungen zwischen Text und JSON den Start bis zur überprüften Berichtigung sperren").

What it checks: structure, chronological folds ending before the holdout, candidates that the strategy
factory builds with exactly the stated parameters, the neighbour map, the budget arithmetic, and that the
plan text states the same folds, candidates, time stops, budget and bootstrap settings as the JSON.
What it does not do: decide whether the design is sound (that is the independent method review), split
data, or touch the holdout.

Digests for the freeze receipt are computed over exact file bytes and kept outside the hashed files, so
no file hashes itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

SCHEMA = "T008-preregistration/1"
STARTABLE_STATE = "frozen"
GERMAN_MONTHS = {
    "jan": 1, "feb": 2, "mär": 3, "mar": 3, "apr": 4, "mai": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "okt": 10, "nov": 11, "dez": 12,
}  # fmt: skip


class ProtocolBlocked(RuntimeError):
    """Raised when a run must not start; lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("protocol blocked: " + "; ".join(problems))
        self.problems = problems


@dataclass(frozen=True, slots=True)
class Window:
    start: date  # inclusive
    end: date  # exclusive

    @property
    def last_month(self) -> tuple[int, int]:
        """(year, month) of the last included day."""
        if self.end.month == 1:
            return self.end.year - 1, 12
        return self.end.year, self.end.month - 1 if self.end.day == 1 else self.end.month


def load_protocol(path: str | Path) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("protocol must be a JSON object")
    return data


def _window(raw: Any) -> Window:
    start, end = (date.fromisoformat(str(item)) for item in raw)
    return Window(start, end)


def expected_budget(protocol: dict[str, Any]) -> dict[str, int]:
    """Strategy slots per fold = training and validation runs for every candidate + one test run per scenario."""
    folds = len(protocol["folds"])
    candidates = len(protocol["candidates"])
    scenarios = len(protocol["scenarios"])
    strategy = folds * (candidates + candidates + scenarios)
    references = strategy * len(protocol["benchmarks"])
    return {
        "max_active_strategy_evaluations": strategy,
        "max_reference_evaluations": references,
        "max_accounted_research_variant_slots": strategy + references,
    }


def check_structure(protocol: dict[str, Any], holdout_start: datetime | None = None) -> list[str]:
    problems: list[str] = []
    if protocol.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}")
    if not isinstance(protocol.get("executions"), int) or protocol["executions"] < 0:
        problems.append("executions must be a non-negative integer")

    # folds: train -> validation -> test contiguous, folds ascending and disjoint
    previous_end: date | None = None
    ids = []
    for fold in protocol.get("folds", []):
        ids.append(fold.get("id"))
        try:
            train, validation, test = (_window(fold[role]) for role in ("train", "validation", "test"))
        except (KeyError, ValueError, TypeError):
            problems.append(f"fold {fold.get('id')}: invalid dates")
            continue
        for name, window in (("train", train), ("validation", validation), ("test", test)):
            if window.end <= window.start:
                problems.append(f"fold {fold['id']}: {name} window is empty or reversed")
        if train.end != validation.start or validation.end != test.start:
            problems.append(f"fold {fold['id']}: train/validation/test are not contiguous")
        if previous_end is not None and train.start < previous_end:
            problems.append(f"fold {fold['id']}: overlaps the previous fold")
        previous_end = test.end
    if not ids or len(set(ids)) != len(ids):
        problems.append("fold ids must be present and unique")

    central = protocol.get("central", {})
    try:
        central_start = datetime.fromisoformat(str(central["start_inclusive"]).replace("Z", "+00:00"))
        central_end = datetime.fromisoformat(str(central["end_exclusive"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        problems.append("central window must have ISO start_inclusive/end_exclusive")
    else:
        if central_end <= central_start:
            problems.append("central window is empty or reversed")
        if previous_end is not None and previous_end > central_start.date():
            problems.append("a research fold reaches into the central holdout")
        if holdout_start is not None and central_start != holdout_start:
            problems.append(f"central start {central_start.isoformat()} differs from configured holdout")

    # candidates and the strategy factory
    candidates = protocol.get("candidates", [])
    candidate_ids = [c.get("id") for c in candidates]
    if len(set(candidate_ids)) != len(candidate_ids) or not candidate_ids:
        problems.append("candidate ids must be present and unique")
    from .intraday_strategies import build_strategy  # local import: optional for structure-only checks

    families: dict[str, str] = {}
    for candidate in candidates:
        families[candidate["id"]] = candidate["family"]
        try:
            built = build_strategy(candidate["family"], dict(candidate["parameters"]))
        except Exception as error:  # noqa: BLE001 - every factory failure blocks
            problems.append(f"candidate {candidate['id']}: factory rejects it ({error})")
            continue
        if built.parameters() != candidate["parameters"]:
            problems.append(f"candidate {candidate['id']}: factory parameters differ from the protocol")

    # scenarios and the neighbour map
    scenario_ids = [s.get("id") for s in protocol.get("scenarios", [])]
    if len(set(scenario_ids)) != len(scenario_ids):
        problems.append("scenario ids must be unique")
    neighbour = next((s for s in protocol.get("scenarios", []) if s.get("id") == "neighbor"), None)
    if neighbour is None:
        problems.append("neighbor scenario missing")
    else:
        mapping = neighbour.get("candidate", {})
        if set(mapping) != set(candidate_ids):
            problems.append("neighbor map must cover exactly the candidates")
        for source, target in mapping.items():
            if target not in families or target == source:
                problems.append(f"neighbor of {source} must be another candidate")
            elif families.get(source) != families[target]:
                problems.append(f"neighbor of {source} must be in the same family")
            elif mapping.get(target) != source:
                problems.append(f"neighbor map is not symmetric for {source}")

    # budget arithmetic
    try:
        expected = expected_budget(protocol)
    except (KeyError, TypeError):
        problems.append("budget cannot be computed")
    else:
        budget = protocol.get("budget", {})
        for key, value in expected.items():
            if budget.get(key) != value:
                problems.append(f"budget.{key} is {budget.get(key)}, arithmetic gives {value}")
    return problems


def _month_range(text: str) -> tuple[tuple[int, int], tuple[int, int]]:
    """'Feb 2016–Jan 2018' or 'Feb–Jul 2018' -> ((2016, 2), (2018, 1))."""
    match = re.fullmatch(r"\s*(\w+)\.?\s*(\d{4})?\s*[–-]\s*(\w+)\.?\s*(\d{4})\s*", text)
    if not match:
        raise ValueError(f"cannot parse month range {text!r}")
    first_month, first_year, last_month, last_year = match.groups()
    first = (int(first_year or last_year), GERMAN_MONTHS[first_month.lower()[:3]])
    last = (int(last_year), GERMAN_MONTHS[last_month.lower()[:3]])
    return first, last


def check_text(protocol: dict[str, Any], plan_text: str) -> list[str]:
    """Facts the plan states must match the JSON."""
    problems: list[str] = []
    # fold table rows: | F1 | Feb 2016–Jan 2018 | Feb–Jul 2018 | Aug 2018–Apr 2019 |
    rows = {
        match.group(1): [cell.strip() for cell in match.group(2).split("|")[:3]]
        for match in re.finditer(r"^\|\s*(F\d+)\s*\|(.+)\|\s*$", plan_text, flags=re.MULTILINE)
    }
    for fold in protocol.get("folds", []):
        cells = rows.get(fold["id"])
        if cells is None:
            problems.append(f"plan has no table row for fold {fold['id']}")
            continue
        for role, cell in zip(("train", "validation", "test"), cells, strict=True):
            window = _window(fold[role])
            try:
                first, last = _month_range(cell)
            except (ValueError, KeyError):
                problems.append(f"fold {fold['id']} {role}: cannot read {cell!r}")
                continue
            if first != (window.start.year, window.start.month) or last != window.last_month:
                problems.append(f"fold {fold['id']} {role}: plan says {cell!r}, JSON says {window.start}..{window.end}")

    candidate_ids = {c["id"] for c in protocol.get("candidates", [])}
    # candidate IDs stand alone; "EXP-T008-001" or "T-018" are task/experiment IDs, not candidates
    mentioned = set(re.findall(r"(?<![\w-])([TM]\d{2,3})(?![\w-])", plan_text))
    for missing in sorted(candidate_ids - mentioned):
        problems.append(f"candidate {missing} is not mentioned in the plan")
    for extra in sorted(mentioned - candidate_ids):
        problems.append(f"plan mentions candidate {extra} that the JSON does not define")

    by_family: dict[str, set[int]] = {}
    for candidate in protocol.get("candidates", []):
        by_family.setdefault(candidate["family"], set()).add(int(candidate["parameters"]["max_hold_bars"]))
    stops = [int(value) for value in re.findall(r"Zeitstopp\s*\**\s*(\d+)\s*\**\s*Bars", plan_text)]
    expected_stops = [sorted(values)[0] for values in by_family.values() if len(values) == 1]
    if sorted(stops) != sorted(expected_stops):
        problems.append(f"time stops in plan {sorted(stops)} differ from JSON {sorted(expected_stops)}")

    budget = protocol.get("budget", {})
    slots = re.search(r"(\d+)\s+Strategie-\s+und\s+(\d+)\s+Referenzslots", plan_text)
    if not slots or (int(slots.group(1)), int(slots.group(2))) != (
        budget.get("max_active_strategy_evaluations"),
        budget.get("max_reference_evaluations"),
    ):
        problems.append("strategy/reference slots in plan differ from JSON budget")
    total = budget.get("max_accounted_research_variant_slots")
    if not re.search(rf"\b{total}\s+mögliche\s+Forschungsslots", plan_text):
        problems.append(f"plan does not state {total} research slots")

    inference = protocol.get("inference", {})
    if not re.search(rf"Seed\s+{inference.get('seed')}\b", plan_text):
        problems.append("bootstrap seed in plan differs from JSON")
    if not re.search(rf"\b{inference.get('block_sessions')}\s+Sitzungen", plan_text):
        problems.append("bootstrap block length in plan differs from JSON")
    if inference.get("replicates") == 1_000_000 and "Million Replikationen" not in plan_text:
        problems.append("bootstrap replicates in plan differ from JSON")
    return problems


def file_digests(paths: list[str | Path]) -> dict[str, str]:
    """SHA-256 of the exact bytes of each file, for an external freeze receipt."""
    return {Path(p).as_posix(): hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


def receipt_digest(digests: dict[str, str]) -> str:
    """One digest over the sorted (path, sha256) pairs; the receipt itself is never part of the input."""
    payload = json.dumps(sorted(digests.items()), separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


RECEIPT_SCHEMA = "T008-freeze-receipt/1"
HEX = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
# Bindings that describe the actual run inputs; the caller must present the same digests at start.
INPUT_BINDINGS = frozenset(
    {
        "verified_integrated_code_sha",
        "dependency_lock_digest",
        "risk_config_digest",
        "separate_research_only_manifest_digest",
        "calendar_digest",
        "point_in_time_corporate_actions_and_fx_digests",
        "complete_cost_schedule_digest",
        "prior_experiment_inventory_digest",
    }
)


def make_receipt(protocol_path: str | Path, plan_path: str | Path, bindings: dict[str, str]) -> dict[str, Any]:
    """Freeze receipt over the exact protocol and plan bytes plus all bindings (stored outside both files)."""
    files = {
        "protocol": hashlib.sha256(Path(protocol_path).read_bytes()).hexdigest(),
        "plan": hashlib.sha256(Path(plan_path).read_bytes()).hexdigest(),
    }
    return {
        "schema": RECEIPT_SCHEMA,
        "files": files,
        "bindings": dict(bindings),
        "digest": receipt_digest({**{f"file:{k}": v for k, v in files.items()}, **bindings}),
    }


def check_receipt(
    protocol: dict[str, Any],
    protocol_bytes: bytes,
    plan_bytes: bytes,
    receipt: dict[str, Any],
    run_inputs: dict[str, str],
) -> list[str]:
    """The receipt must bind exactly these protocol/plan bytes, every required binding and the run inputs."""
    problems: list[str] = []
    if receipt.get("schema") != RECEIPT_SCHEMA:
        return [f"receipt schema must be {RECEIPT_SCHEMA}"]
    files = receipt.get("files") or {}
    if files.get("protocol") != hashlib.sha256(protocol_bytes).hexdigest():
        problems.append("protocol bytes differ from the frozen receipt (criteria changed after freeze?)")
    if files.get("plan") != hashlib.sha256(plan_bytes).hexdigest():
        problems.append("plan bytes differ from the frozen receipt")
    bindings = receipt.get("bindings") or {}
    for name in protocol.get("freeze", {}).get("required", []):
        if not isinstance(bindings.get(name), str) or not HEX.match(bindings[name]):
            problems.append(f"receipt lacks a valid digest for {name}")
    expected = receipt_digest({**{f"file:{k}": v for k, v in files.items()}, **bindings})
    if receipt.get("digest") != expected:
        problems.append("receipt digest does not match its contents")
    for name in sorted(INPUT_BINDINGS & set(protocol.get("freeze", {}).get("required", []))):
        if name not in run_inputs:
            problems.append(f"run input {name} not presented")
        elif run_inputs[name] != bindings.get(name):
            problems.append(f"run input {name} differs from the frozen binding")
    return problems


def assert_startable(
    protocol_path: str | Path,
    plan_path: str | Path,
    run_inputs: dict[str, str] | None = None,
    holdout_start: datetime | None = None,
) -> None:
    """Raise ProtocolBlocked unless protocol and plan agree, the protocol is frozen and an existing receipt
    binds the exact protocol/plan bytes, every required binding and the presented run inputs."""
    protocol_bytes = Path(protocol_path).read_bytes()
    plan_bytes = Path(plan_path).read_bytes()
    protocol = json.loads(protocol_bytes)
    problems = check_structure(protocol, holdout_start) + check_text(protocol, plan_bytes.decode("utf-8"))
    if protocol.get("state") != STARTABLE_STATE:
        problems.append(f"state is {protocol.get('state')!r}, a run needs {STARTABLE_STATE!r}")
    reference = protocol.get("freeze", {}).get("binding_receipt")
    if not reference:
        problems.append("no binding freeze receipt")
    else:
        receipt_path = Path(reference)
        if not receipt_path.is_absolute():
            receipt_path = Path(protocol_path).parent / receipt_path
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            problems.append(f"freeze receipt unreadable: {error}")
        else:
            if not isinstance(receipt, dict):
                problems.append("freeze receipt must be a JSON object")
            else:
                problems += check_receipt(protocol, protocol_bytes, plan_bytes, receipt, run_inputs or {})
    if problems:
        raise ProtocolBlocked(problems)
