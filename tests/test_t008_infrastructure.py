from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from localtrader.attempt_journal import AttemptJournal, Budget, JournalError
from localtrader.model_registry import CASH_CHAMPION, ModelRegistry, RegistryError
from localtrader.research_protocol import (
    INPUT_BINDINGS,
    ProtocolBlocked,
    assert_startable,
    check_structure,
    check_text,
    expected_budget,
    file_digests,
    load_protocol,
    make_receipt,
    receipt_digest,
)

REPO = Path(__file__).resolve().parent.parent
PROTOCOL = REPO / "docs" / "research" / "T-008-protocol.json"
PLAN = REPO / "docs" / "research" / "EXP-T008-001.md"


class ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.protocol = load_protocol(PROTOCOL)
        self.text = PLAN.read_text(encoding="utf-8")

    def test_committed_protocol_and_plan_are_consistent(self) -> None:
        self.assertEqual(check_structure(self.protocol), [])
        self.assertEqual(check_text(self.protocol, self.text), [])

    def test_budget_arithmetic(self) -> None:
        self.assertEqual(
            expected_budget(self.protocol),
            {
                "max_active_strategy_evaluations": 39,
                "max_reference_evaluations": 195,
                "max_accounted_research_variant_slots": 234,
            },
        )

    def freeze(self, tmp: str, mutate: Any = None, after: Any = None) -> tuple[Path, Path, dict[str, str]]:
        """Write a frozen copy of protocol + plan with a valid receipt; ``after`` edits the protocol later."""
        protocol = copy.deepcopy(self.protocol)
        if mutate:
            mutate(protocol)
        protocol["state"] = "frozen"
        protocol["freeze"]["binding_receipt"] = "receipt.json"
        protocol_path, plan_path = Path(tmp) / "protocol.json", Path(tmp) / "plan.md"
        protocol_path.write_text(json.dumps(protocol, indent=2), encoding="utf-8")
        plan_path.write_text(self.text, encoding="utf-8")
        bindings = {name: f"{index:064x}" for index, name in enumerate(protocol["freeze"]["required"], 1)}
        receipt = make_receipt(protocol_path, plan_path, bindings)
        (Path(tmp) / "receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
        if after:
            after(protocol)
            protocol_path.write_text(json.dumps(protocol, indent=2), encoding="utf-8")
        inputs = {name: value for name, value in bindings.items() if name in INPUT_BINDINGS}
        return protocol_path, plan_path, inputs

    def blocked(self, *args: Any, **kwargs: Any) -> list[str]:
        with self.assertRaises(ProtocolBlocked) as caught:
            assert_startable(*args, **kwargs)
        return caught.exception.problems

    def test_unfrozen_protocol_blocks_the_start(self) -> None:
        problems = self.blocked(PROTOCOL, PLAN)
        self.assertIn("state is 'proposed_unrun', a run needs 'frozen'", problems)
        self.assertIn("no binding freeze receipt", problems)

    def test_frozen_protocol_with_valid_receipt_and_matching_inputs_may_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            protocol, plan, inputs = self.freeze(tmp)
            assert_startable(protocol, plan, inputs)

    def test_missing_receipt_blocks(self) -> None:
        # Regression (PR #23 review): state=frozen with a nonexistent receipt path allowed the start.
        with tempfile.TemporaryDirectory() as tmp:
            protocol, plan, inputs = self.freeze(tmp)
            (Path(tmp) / "receipt.json").unlink()
            self.assertTrue(any("receipt unreadable" in p for p in self.blocked(protocol, plan, inputs)))

    def test_criteria_changed_after_freeze_block(self) -> None:
        # Regression (PR #23 review): outer criteria edited after the freeze must not pass.
        def loosen(p: dict[str, Any]) -> None:
            p["outer_accept_all"]["pooled_base_net_total_return_gt"] = -1

        with tempfile.TemporaryDirectory() as tmp:
            protocol, plan, inputs = self.freeze(tmp, after=loosen)
            self.assertTrue(any("criteria changed" in p for p in self.blocked(protocol, plan, inputs)))
        with tempfile.TemporaryDirectory() as tmp:
            protocol, plan, inputs = self.freeze(tmp)
            plan.write_text(self.text + "\nNachtrag", encoding="utf-8")
            self.assertTrue(any("plan bytes" in p for p in self.blocked(protocol, plan, inputs)))

    def test_tampered_or_incomplete_receipt_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            protocol, plan, inputs = self.freeze(tmp)
            path = Path(tmp) / "receipt.json"
            receipt = json.loads(path.read_text(encoding="utf-8"))
            receipt["bindings"]["calendar_digest"] = "f" * 64  # digest no longer matches
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertTrue(any("receipt digest" in p for p in self.blocked(protocol, plan, inputs)))
            del receipt["bindings"]["independent_method_review"]
            path.write_text(json.dumps(receipt), encoding="utf-8")
            self.assertTrue(any("independent_method_review" in p for p in self.blocked(protocol, plan, inputs)))

    def test_run_inputs_must_match_the_frozen_bindings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            protocol, plan, inputs = self.freeze(tmp)
            self.assertTrue(any("not presented" in p for p in self.blocked(protocol, plan, {})))
            changed = {**inputs, "complete_cost_schedule_digest": "e" * 64}
            self.assertTrue(any("differs from the frozen" in p for p in self.blocked(protocol, plan, changed)))

    def test_text_and_json_mismatches_block(self) -> None:
        cases: list[tuple[str, Any]] = [
            ("fold date", lambda p: p["folds"][1]["test"].__setitem__(1, "2022-09-01")),
            ("time stop", lambda p: p["candidates"][0]["parameters"].__setitem__("max_hold_bars", 40)),
            ("seed", lambda p: p["inference"].__setitem__("seed", 1)),
            ("block", lambda p: p["inference"].__setitem__("block_sessions", 10)),
            ("candidate id", lambda p: p["candidates"][0].__setitem__("id", "T13")),
        ]
        for name, mutate in cases:
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                protocol, plan, inputs = self.freeze(tmp, mutate)  # frozen with a valid receipt, still blocked
                self.blocked(protocol, plan, inputs)

    def test_structural_errors_are_found(self) -> None:
        cases: list[tuple[str, Any, str]] = [
            ("gap", lambda p: p["folds"][0]["validation"].__setitem__(0, "2018-03-01"), "not contiguous"),
            ("holdout", lambda p: p["folds"][2]["test"].__setitem__(1, "2025-12-01"), "central holdout"),
            ("overlap", lambda p: p["folds"][1]["train"].__setitem__(0, "2019-01-01"), "overlaps"),
            ("budget", lambda p: p["budget"].__setitem__("max_reference_evaluations", 200), "arithmetic"),
            ("factory", lambda p: p["candidates"][2]["parameters"].__setitem__("unknown", 1), "factory"),
            ("neighbor", lambda p: p["scenarios"][4]["candidate"].__setitem__("T12", "M175"), "same family"),
        ]
        for name, mutate, expected in cases:
            with self.subTest(name):
                changed = copy.deepcopy(self.protocol)
                mutate(changed)
                problems = check_structure(changed)
                self.assertTrue(any(expected in problem for problem in problems), problems)

    def test_configured_holdout_must_match_the_central_window(self) -> None:
        self.assertEqual(check_structure(self.protocol, datetime(2025, 11, 1, tzinfo=UTC)), [])
        problems = check_structure(self.protocol, datetime(2025, 12, 1, tzinfo=UTC))
        self.assertTrue(any("differs from configured holdout" in p for p in problems))

    def test_receipt_digest_changes_with_any_byte_and_excludes_itself(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.json", Path(tmp) / "b.md"
            a.write_bytes(b"{}")
            b.write_bytes(b"plan")
            first = receipt_digest(file_digests([a, b]))
            b.write_bytes(b"plan ")
            self.assertNotEqual(first, receipt_digest(file_digests([a, b])))
            self.assertEqual(receipt_digest(file_digests([b, a])), receipt_digest(file_digests([a, b])))


def _clock() -> Any:
    ticks = iter(range(10_000))
    return lambda: f"2026-11-18T00:00:{next(ticks):05d}"


class JournalTests(unittest.TestCase):
    def journal(self, tmp: str, budget: Budget | None = None) -> AttemptJournal:
        return AttemptJournal(tmp, "EXP-T008-001", budget or Budget(2, 2, 1), clock=_clock())

    def test_lifecycle_is_recorded_and_terminal_states_are_final(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("F1-train-T12", "strategy", {"fold": "F1", "role": "train", "candidate": "T12"})
            journal.transition("F1-train-T12", "started")
            journal.transition("F1-train-T12", "completed")
            with self.assertRaises(JournalError):
                journal.transition("F1-train-T12", "started")
            with self.assertRaises(JournalError):
                journal.transition("never", "started")
            self.assertEqual(journal.status()["F1-train-T12"]["status"], "completed")

    def test_failed_and_aborted_attempts_keep_their_slot_and_need_a_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("a", "strategy", {})
            journal.transition("a", "started")
            with self.assertRaises(JournalError):
                journal.transition("a", "failed")
            journal.transition("a", "failed", "timeout after 8 h")
            journal.reserve("b", "strategy", {})
            journal.transition("b", "aborted", "data gate failed")
            self.assertEqual(journal.counts()["strategy"], {"used": 2, "remaining": 0})
            with self.assertRaises(JournalError):  # failures still consume the budget
                journal.reserve("c", "strategy", {})

    def test_ids_are_unique_and_research_and_central_are_counted_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("x", "reference", {})
            with self.assertRaises(JournalError):
                journal.reserve("x", "strategy", {})
            journal.reserve("central-1", "central", {})
            with self.assertRaises(JournalError):
                journal.reserve("central-2", "central", {})
            counts = journal.counts()
            self.assertEqual((counts["reference"]["used"], counts["central"]["used"]), (1, 1))
            self.assertEqual(counts["strategy"]["used"], 0)

    def test_tampering_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("a", "strategy", {})
            journal.transition("a", "started")
            journal.transition("a", "failed", "crash")
            lines = journal.events_path.read_text(encoding="utf-8").splitlines()
            edited = json.loads(lines[2])
            edited["status"] = "completed"
            journal.events_path.write_text("\n".join([*lines[:2], json.dumps(edited)]) + "\n", encoding="utf-8")
            with self.assertRaises(JournalError):
                journal.events()
            journal.events_path.write_text(lines[0] + "\n" + lines[2] + "\n", encoding="utf-8")  # deletion
            with self.assertRaises(JournalError):
                journal.events()

    def test_truncated_end_is_detected_and_cannot_restart(self) -> None:
        # Regression (PR #23 review): keeping only the first line made a failed attempt startable again.
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("a", "strategy", {})
            journal.transition("a", "started")
            journal.transition("a", "failed", "crash")
            first = journal.events_path.read_text(encoding="utf-8").splitlines()[0]
            journal.events_path.write_text(first + "\n", encoding="utf-8")
            with self.assertRaises(JournalError):
                journal.status()
            with self.assertRaises(JournalError):
                journal.transition("a", "started")
            (journal.root / "head.json").unlink()  # even without the head, the markers reveal the cut
            with self.assertRaises(JournalError):
                journal.status()

    def test_crash_between_marker_and_event_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("a", "strategy", {})
            (journal.root / "attempts" / "a.started").touch()  # marker written, event never appended
            with self.assertRaises(JournalError):
                journal.status()

    def test_crash_after_event_before_head_is_tolerated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("a", "strategy", {})
            head = (journal.root / "head.json").read_text(encoding="utf-8")
            journal.transition("a", "started")
            (journal.root / "head.json").write_text(head, encoding="utf-8")  # head update lost
            self.assertEqual(journal.status()["a"]["status"], "started")

    def test_reservation_without_journal_entry_is_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            (journal.root / "attempts" / "ghost.json").write_text("{}", encoding="utf-8")
            with self.assertRaises(JournalError):
                journal.status()

    def test_summary_lists_unstarted_and_open_slots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            journal = self.journal(tmp)
            journal.reserve("a", "strategy", {})
            journal.transition("a", "started")
            summary = journal.summary(["a", "b"])
            self.assertEqual(summary["open"], ["a"])
            self.assertEqual(summary["unstarted"], ["b"])

    def test_budget_from_protocol(self) -> None:
        budget = Budget.from_protocol(load_protocol(PROTOCOL))
        self.assertEqual((budget.strategy, budget.reference, budget.central), (39, 195, 1))


def model(model_id: str = "T12-F3-v1", eligible: str | None = "shadow", passed: bool = True) -> dict[str, Any]:
    return {
        "model_id": model_id,
        "code_commit": "c9c9213",
        "dependency_lock_digest": "a" * 64,
        "data_versions": ["SPY:a898c0b6d9c6"],
        "criteria_version": "T008-preregistration/1@1.0.0",
        "parameters": {"breakout_bars": 12},
        "periods": {"train": ["2022-08-01", "2024-08-01"]},
        "seeds": [20261106],
        "costs": {"spread_bps": 0.5},
        "fold_reports": ["reports/F3.json"],
        "audit_refs": ["journal:EXP-T008-001"],
        "activation_decision": {"eligible_mode": eligible, "gates_passed": passed},
    }


CONFIRMED = {"new_entries_blocked": True, "orders_and_fills_reconciled": True, "positions_flat_or_protected": True}


class RegistryTests(unittest.TestCase):
    def test_audit_failure_before_activation_changes_nothing(self) -> None:
        # Regression (PR #23 review): with an unwritable audit log the model was active anyway.
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(tmp, clock=_clock())
            registry.register(model())
            audit = Path(tmp) / "audit.jsonl"
            saved = audit.read_bytes()
            audit.unlink()
            audit.mkdir()
            with self.assertRaises(OSError):
                registry.activate("T12-F3-v1", "shadow", "user")
            with self.assertRaises(OSError):
                registry.active()  # unreadable audit: no answer rather than a guess
            audit.rmdir()
            audit.write_bytes(saved)
            self.assertEqual(registry.active()["model_id"], CASH_CHAMPION)

    def test_crash_before_commit_fails_closed_and_recovers(self) -> None:
        for action in ("activate", "rollback"):
            with self.subTest(action), tempfile.TemporaryDirectory() as tmp:
                registry = ModelRegistry(tmp, clock=_clock())
                registry.register(model())
                if action == "rollback":
                    registry.activate("T12-F3-v1", "shadow", "user")
                before = registry.active()["model_id"]
                original = registry._audit

                def failing_commit(entry: dict[str, Any], original: Any = original, action: str = action) -> None:
                    if entry.get("phase") == "commit" and entry.get("action") == action:
                        raise OSError("disk full")
                    original(entry)

                registry._audit = failing_commit  # type: ignore[method-assign]
                with self.assertRaises(OSError):
                    if action == "activate":
                        registry.activate("T12-F3-v1", "shadow", "user")
                    else:
                        registry.rollback("drift", CONFIRMED)
                registry._audit = original  # type: ignore[method-assign]
                with self.assertRaises(RegistryError):
                    registry.active()
                self.assertEqual(registry.recover()["model_id"], before)
                self.assertEqual(registry.active()["model_id"], before)
                self.assertEqual(registry.audit()[-1]["action"], "recover")

    def test_cash_is_the_initial_champion(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(ModelRegistry(tmp, clock=_clock()).active()["model_id"], CASH_CHAMPION)

    def test_versions_are_complete_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(tmp, clock=_clock())
            registry.register(model())
            with self.assertRaises(RegistryError):
                registry.register(model())
            incomplete = model("T16-F3-v1")
            del incomplete["dependency_lock_digest"]
            with self.assertRaises(RegistryError):
                registry.register(incomplete)
            with self.assertRaises(RegistryError):
                registry.register({**model("X"), "model_id": CASH_CHAMPION})

    def test_only_shadow_and_only_after_passed_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(tmp, clock=_clock())
            registry.register(model())
            registry.register(model("M200-F3-v1", eligible=None, passed=False))
            for mode in ("paper", "live"):
                with self.assertRaises(RegistryError):
                    registry.activate("T12-F3-v1", mode, "user")
            with self.assertRaises(RegistryError):
                registry.activate("M200-F3-v1", "shadow", "user")
            with self.assertRaises(RegistryError):
                registry.activate("T12-F3-v1", "shadow", "")
            with self.assertRaises(RegistryError):
                registry.register(model("T12-F3-v2", eligible="paper"))
            registry.activate("T12-F3-v1", "shadow", "user")
            self.assertEqual(registry.active()["mode"], "shadow")

    def test_rollback_requires_confirmed_wind_down_and_is_audited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            registry = ModelRegistry(tmp, clock=_clock())
            registry.register(model())
            registry.activate("T12-F3-v1", "shadow", "user")
            with self.assertRaises(RegistryError):
                registry.activate("T12-F3-v1", "shadow", "user")  # must return to cash first
            with self.assertRaises(RegistryError):
                registry.rollback("drift", {**CONFIRMED, "positions_flat_or_protected": False})
            registry.rollback("drift", CONFIRMED)
            self.assertEqual(registry.active()["model_id"], CASH_CHAMPION)
            actions = [entry["action"] for entry in registry.audit() if entry.get("phase") != "intent"]
            self.assertEqual(actions, ["init", "register", "activate", "rollback"])
            self.assertFalse((Path(tmp) / "active.tmp").exists())
