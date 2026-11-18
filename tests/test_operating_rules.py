from __future__ import annotations

import copy
import unittest
from datetime import UTC, datetime, time, timedelta
from typing import Any

from localtrader.config import load_config
from localtrader.domain import Bar, Direction, Market, Signal
from localtrader.execution import CostProfile
from localtrader.intraday_data import DatasetManifest, ImportedDataset, SessionSpec
from localtrader.intraday_engine import InstrumentSpec, IntradayEngine
from localtrader.intraday_strategies import StrategyContext
from localtrader.operating_rules import OperatingRulesError, load_operating_rules

# Session 09:00-10:00 UTC on weekdays: four 15-minute bars per day.
SESSION = SessionSpec("UTC", time(9, 0), time(10, 0))


def instrument() -> dict[str, InstrumentSpec]:
    return {"X": InstrumentSpec("X", Market.ETF, "t", "USD", SESSION, CostProfile(0, 0, 0, 0), 0.001)}


def dataset(starts: list[datetime], after_gap: frozenset[datetime] = frozenset()) -> ImportedDataset:
    bars = [
        Bar(
            "X",
            Market.ETF,
            "15m",
            s,
            s + timedelta(minutes=15),
            100,
            100.5,
            99.5,
            100,
            1e6,
            "t",
            s + timedelta(minutes=15),
        )
        for s in starts
    ]
    return ImportedDataset(bars, DatasetManifest("X", "etf", "t", "t", "UTC", 15, "v", "v", True, {}), after_gap)


def day(d: int, hours: tuple[int, ...] = (9,), minutes: tuple[int, ...] = (0, 15, 30, 45)) -> list[datetime]:
    return [datetime(2024, 3, d, h, m, tzinfo=UTC) for h in hours for m in minutes]


class EnterOn:
    """Enters when the context's last bar starts at ``moment``; never exits."""

    name = "rule"

    def __init__(self, moment: datetime) -> None:
        self.moment = moment

    def parameters(self) -> dict[str, float]:
        return {}

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        if not ctx.in_position and ctx.bars[-1].start == self.moment:
            return Signal("rule", "X", Market.ETF, ctx.now, ctx.now, Direction.LONG, 1.0, "rule", stop_price=90.0)
        return None


class ValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw = copy.deepcopy(load_config().raw)

    def test_default_config_has_valid_rules(self) -> None:
        rules = load_operating_rules(self.raw)
        self.assertEqual(rules.foreign_positions, "never_liquidate")
        self.assertTrue(rules.allows_overnight)

    def test_fixed_rules_cannot_be_weakened(self) -> None:
        cases: dict[str, Any] = {
            "liquidate foreign": ("foreign_positions", "liquidate"),
            "shared account": ("dedicated_account_required_for_live", False),
            "typo": ("before_close", "flaten_all"),
            "extended hours": ("trading_hours", "extended_hours"),
        }
        for name, (key, value) in cases.items():
            with self.subTest(name):
                raw = copy.deepcopy(self.raw)
                raw["operating_rules"][key] = value
                with self.assertRaises(OperatingRulesError):
                    load_operating_rules(raw)

    def test_missing_unknown_and_inconsistent_rules_are_rejected(self) -> None:
        missing = copy.deepcopy(self.raw)
        del missing["operating_rules"]["manual_orders"]
        unknown = copy.deepcopy(self.raw)
        unknown["operating_rules"]["auto_liquidate"] = True
        inconsistent = copy.deepcopy(self.raw)
        inconsistent["operating_rules"]["overnight_positions"] = "forbidden"
        for raw in (missing, unknown, inconsistent, {"operating_rules": None}):
            with self.assertRaises(OperatingRulesError):
                load_operating_rules(raw)

    def test_flatten_before_close_disables_overnight(self) -> None:
        raw = copy.deepcopy(self.raw)
        raw["operating_rules"]["before_close"] = "flatten_all"
        self.assertFalse(load_operating_rules(raw).allows_overnight)


class EnforcedInBacktestTests(unittest.TestCase):
    """Rules marked 'implementiert (Backtest)' in docs/OPERATING_RULES.md."""

    def setUp(self) -> None:
        self.config = load_config()

    def run_engine(
        self, starts: list[datetime], enter_on: datetime, after_gap: frozenset[datetime] = frozenset()
    ) -> Any:
        return IntradayEngine(self.config, instrument(), [EnterOn(enter_on)]).run({"X": dataset(starts, after_gap)})

    def test_no_entry_outside_the_regular_session(self) -> None:
        # bars continue after 10:00 (after-hours); a signal on the last session bar must not fill there
        starts = day(5, hours=(9, 10))
        result = self.run_engine(starts, datetime(2024, 3, 5, 9, 45, tzinfo=UTC))
        self.assertEqual(result.fills, [])
        self.assertTrue(any(d.get("reason") == "execution bar outside session" for d in result.decisions))

    def test_no_new_entry_from_the_last_bar_before_close(self) -> None:
        # the signal at the close would only fill at the next day's open: rejected as stale
        starts = day(5) + day(6)
        result = self.run_engine(starts, datetime(2024, 3, 5, 9, 45, tzinfo=UTC))
        self.assertEqual(result.fills, [])
        self.assertTrue(any(d.get("reason") == "signal too old for entry" for d in result.decisions))

    def test_overnight_and_weekend_positions_are_kept(self) -> None:
        starts = day(8) + day(11)  # Friday and Monday
        result = self.run_engine(starts, datetime(2024, 3, 8, 9, 0, tzinfo=UTC))
        self.assertEqual(len(result.fills), 2)  # entry Friday, only the end-of-data liquidation afterwards
        self.assertEqual(result.trades[-1].exit_reason, "end of data")

    def test_no_entry_on_the_first_bar_after_a_data_gap(self) -> None:
        starts = day(5)
        gap = starts[2]
        result = self.run_engine([s for s in starts if s != starts[1]], starts[0], frozenset({gap}))
        self.assertEqual(result.fills, [])
