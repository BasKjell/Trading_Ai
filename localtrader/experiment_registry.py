"""Shared, versionable experiment register (Spec §19.10).

One immutable JSON file per experiment under ``experiments/`` avoids write conflicts between agents.
Files are created exclusively and never rewritten; corrections are new experiments that reference the
old one.

IDs are scoped to the task: ``EXP-T009-001`` belongs to task ``T-009``. A task is claimed by exactly one
agent working in its own worktree (AGENTS.md §1), so two agents never draw from the same sequence and
separate registers merge without collisions. A purely global counter would hand out the same next ID
in every worktree that starts from the same commit. ``EXP-0001`` is the only legacy ID and stays valid. ``total_variants`` counts every tested variant across all agents so the multiple-testing burden
stays visible.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LEGACY_IDS = frozenset({"EXP-0001"})
ID_PATTERN = re.compile(r"^EXP-T(\d{3})-(\d{3})$")
TASK_PATTERN = re.compile(r"^T-(\d{3})$")
DEFAULT_DIRECTORY = Path("experiments")


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    parameters: dict[str, Any]
    results: dict[str, Any]
    rejected: bool = False


@dataclass(frozen=True, slots=True)
class ExperimentRecord:
    experiment_id: str
    task_id: str
    agent: str
    model: str
    created_at: str
    code_version: str
    data_versions: list[str]
    seeds: list[int]
    splits: dict[str, Any]
    cost_assumptions: dict[str, Any]
    variants: list[Variant]
    uncertainty: dict[str, Any]
    decision: str
    rationale: str
    holdout_used: bool = False
    synthetic_data: bool = False
    references: list[str] = field(default_factory=list)

    def validate(self) -> None:
        task = TASK_PATTERN.match(self.task_id)
        if not task:
            raise ValueError("task_id must look like T-001")
        if self.experiment_id not in LEGACY_IDS:
            match = ID_PATTERN.match(self.experiment_id)
            if not match:
                raise ValueError("experiment_id must look like EXP-T001-001")
            if match.group(1) != task.group(1):
                raise ValueError("experiment_id must belong to task_id (EXP-T009-xxx for T-009)")
        if not self.variants:
            raise ValueError("at least one variant must be recorded")
        if not self.code_version or not self.data_versions:
            raise ValueError("code_version and data_versions are required")
        if not self.decision or not self.rationale:
            raise ValueError("decision and rationale are required")


def write_record(record: ExperimentRecord, directory: str | Path = DEFAULT_DIRECTORY) -> Path:
    """Create the record file; refuses to overwrite an existing experiment."""
    record.validate()
    path = Path(directory) / f"{record.experiment_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(asdict(record), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    with path.open("x", encoding="utf-8") as handle:  # "x": fail if it already exists
        handle.write(payload)
    return path


def load_all(directory: str | Path = DEFAULT_DIRECTORY) -> list[ExperimentRecord]:
    records: list[ExperimentRecord] = []
    for path in sorted(Path(directory).glob("EXP-*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["variants"] = [Variant(**item) for item in raw["variants"]]
        record = ExperimentRecord(**raw)
        record.validate()
        if path.stem != record.experiment_id:
            raise ValueError(f"{path.name}: file name does not match experiment_id")
        records.append(record)
    return records


def total_variants(records: list[ExperimentRecord]) -> int:
    return sum(len(record.variants) for record in records)


def next_experiment_id(task_id: str, directory: str | Path = DEFAULT_DIRECTORY) -> str:
    """Next ID within ``task_id``; only the agent that claimed the task may draw from this sequence."""
    task = TASK_PATTERN.match(task_id)
    if not task:
        raise ValueError("task_id must look like T-001")
    numbers = []
    for path in Path(directory).glob(f"EXP-T{task.group(1)}-*.json"):
        match = ID_PATTERN.match(path.stem)
        if match:
            numbers.append(int(match.group(2)))
    return f"EXP-T{task.group(1)}-{max(numbers, default=0) + 1:03d}"


def summary(directory: str | Path = DEFAULT_DIRECTORY) -> dict[str, Any]:
    records = load_all(directory)
    return {
        "experiments": len(records),
        "total_variants_tested": total_variants(records),
        "holdout_uses": sum(1 for r in records if r.holdout_used),
        "entries": [
            {
                "id": r.experiment_id,
                "task": r.task_id,
                "agent": r.agent,
                "variants": len(r.variants),
                "decision": r.decision,
            }
            for r in records
        ],
    }
