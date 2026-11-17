from __future__ import annotations

import json
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


def record(experiment_id: str = "EXP-0001", variants: int = 2) -> ExperimentRecord:
    return ExperimentRecord(
        experiment_id=experiment_id,
        task_id="T-004",
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
            write_record(record("EXP-0001", 3), tmp)
            write_record(replace(record("EXP-0002", 4), agent="other"), tmp)
            self.assertEqual(total_variants(load_all(tmp)), 7)
            self.assertEqual(next_experiment_id(tmp), "EXP-0003")
            self.assertEqual(summary(tmp)["total_variants_tested"], 7)

    def test_invalid_records_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for bad in (
                replace(record(), experiment_id="X1"),
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
            path.rename(Path(tmp) / "EXP-0009.json")
            with self.assertRaises(ValueError):
                load_all(tmp)

    def test_versioned_register_is_valid(self) -> None:
        records = load_all(REPO / "experiments")
        self.assertGreaterEqual(len(records), 1)
        first = json.loads((REPO / "experiments" / "EXP-0001.json").read_text(encoding="utf-8"))
        self.assertFalse(first["holdout_used"])


if __name__ == "__main__":
    unittest.main()
