from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from localtrader.experiment_registry import (
    ExperimentRecord,
    Variant,
    load_all,
    next_experiment_id,
    summary,
    total_variants,
    write_record,
)

REPO = Path(__file__).resolve().parent.parent


def record(experiment_id: str = "EXP-T004-001", variants: int = 2, task_id: str = "T-004") -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        task_id=task_id,
        agent="test",
        model="test-model",
        created_at="2026-11-17T00:00:00+00:00",
        code_version="abc1234",
        data_versions=["v1"],
        seeds=[1],
        splits={"type": "full_sample"},
        cost_assumptions={"spread_bps": 1.0},
        variants=[Variant(f"v{i}", {"p": i}, {"total_return": 0.0}) for i in range(variants)],
        uncertainty={},
        decision="rejected",
        rationale="test",
    )


class RegistryTests(unittest.TestCase):
    def test_records_are_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_record(record(), tmp)
            with self.assertRaises(FileExistsError):
                write_record(replace(record(), decision="accepted"), tmp)
            self.assertEqual(load_all(tmp)[0].decision, "rejected")

    def test_total_variants_counts_all_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            write_record(record("EXP-T004-001", 3), tmp)
            write_record(replace(record("EXP-T005-001", 4, "T-005"), agent="other"), tmp)
            self.assertEqual(total_variants(load_all(tmp)), 7)
            self.assertEqual(next_experiment_id("T-004", tmp), "EXP-T004-002")
            self.assertEqual(next_experiment_id("T-006", tmp), "EXP-T006-001")
            self.assertEqual(summary(tmp)["total_variants_tested"], 7)

    def test_invalid_records_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (
                replace(record(), experiment_id="X1"),
                replace(record(), experiment_id="EXP-0002"),  # global counter IDs are no longer issued
                replace(record(), experiment_id="EXP-T005-001"),  # ID of another task
                replace(record(), task_id="4"),
                replace(record(), variants=[]),
                replace(record(), rationale=""),
                replace(record(), data_versions=[]),
            ):
                with self.subTest(bad=bad.experiment_id), self.assertRaises(ValueError):
                    write_record(bad, tmp)

    def test_renamed_file_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = write_record(record(), tmp)
            path.rename(Path(tmp) / "EXP-T004-009.json")
            with self.assertRaises(ValueError):
                load_all(tmp)

    def test_separate_worktrees_do_not_collide_and_merge_without_loss(self) -> None:
        # Regression (PR #18 review): two worktrees from the same commit both drew EXP-0002.
        with tempfile.TemporaryDirectory() as tmp:
            base, first, second, merged = (Path(tmp) / name for name in ("base", "a", "b", "merged"))
            write_record(replace(record("EXP-0001", 5), task_id="T-004"), base)
            for clone in (first, second):
                shutil.copytree(base, clone)
            id_a = next_experiment_id("T-010", first)
            id_b = next_experiment_id("T-011", second)
            self.assertNotEqual(id_a, id_b)
            write_record(record(id_a, 3, "T-010"), first)
            write_record(record(id_b, 4, "T-011"), second)
            merged.mkdir()
            for source in (first, second):
                for path in source.glob("*.json"):
                    target = merged / path.name
                    if target.exists():
                        self.assertEqual(target.read_bytes(), path.read_bytes())  # only shared base files
                    else:
                        shutil.copy2(path, target)
            records = load_all(merged)
            self.assertEqual(sorted(r.experiment_id for r in records), ["EXP-0001", id_a, id_b])
            self.assertEqual(total_variants(records), 12)

    def test_versioned_register_is_valid(self) -> None:
        records = load_all(REPO / "experiments")
        self.assertGreaterEqual(len(records), 1)
        first = json.loads((REPO / "experiments" / "EXP-0001.json").read_text(encoding="utf-8"))
        self.assertFalse(first["holdout_used"])


if __name__ == "__main__":
    unittest.main()
