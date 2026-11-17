from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from localtrader.config import load_config
from localtrader.domain import Bar, Direction, Market, Signal
from localtrader.execution import CostProfile
from localtrader.intraday_data import (
    CsvImportSpec,
    DatasetManifest,
    ImportedDataset,
    SessionSpec,
    import_csv,
    synthetic_intraday,
)
from localtrader.intraday_engine import (
    InstrumentSpec,
    IntradayEngine,
    block_bootstrap_monthly,
    compute_metrics,
    instruments_from_config,
)
from localtrader.intraday_report import run_scenarios, write_intraday_report
from localtrader.intraday_strategies import IntradayTrendStrategy, MeanReversionStrategy, StrategyContext

SESSION = SessionSpec("UTC", time(0, 0), time(23, 59))
NO_COSTS = CostProfile(0.0, 0.0, 0.0, 0.0)


def instrument(costs: CostProfile = NO_COSTS, financing: float = 0.0) -> dict[str, InstrumentSpec]:
    return {"X": InstrumentSpec("X", Market.INDEX, "test", "EUR", SESSION, costs, 0.0001, financing)}


def bars(
    prices: list[tuple[float, float, float, float]], start: datetime | None = None, volume: float = 1e6
) -> ImportedDataset:
    start = start or datetime(2024, 1, 2, 9, 0, tzinfo=UTC)
    out = []
    for i, (o, h, low, c) in enumerate(prices):
        s = start + timedelta(minutes=15 * i)
        out.append(
            Bar(
                "X",
                Market.INDEX,
                "15m",
                s,
                s + timedelta(minutes=15),
                o,
                h,
                low,
                c,
                volume,
                "unit",
                s + timedelta(minutes=15),
            )
        )
    manifest = DatasetManifest("X", "index", "test", "unit", "UTC", 15, "v", "v", True, {})
    return ImportedDataset(out, manifest, frozenset())


@dataclass(frozen=True)
class Scripted:
    """Emits signals at fixed bar indices; records what it was allowed to see."""

    entries: dict[int, float]  # bar index -> stop
    exits: frozenset[int] = frozenset()
    name: str = "scripted"
    seen: list[datetime] | None = None

    def parameters(self) -> dict[str, float]:
        return {}

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        index = len(ctx.bars) - 1
        if self.seen is not None:
            self.seen.append(ctx.bars[-1].received_at)
            assert ctx.bars[-1].received_at <= ctx.now
        bar = ctx.bars[-1]
        if not ctx.in_position and index in self.entries:
            return Signal(
                self.name,
                "X",
                Market.INDEX,
                ctx.now,
                ctx.now,
                Direction.LONG,
                1.0,
                "scripted entry",
                stop_price=self.entries[index],
            )
        if ctx.in_position and index in self.exits:
            return Signal(
                self.name,
                "X",
                Market.INDEX,
                ctx.now,
                ctx.now,
                Direction.FLAT,
                1.0,
                "scripted exit",
                stop_price=None,
            )
        del bar
        return None


FLAT = (100.0, 100.5, 99.5, 100.0)


class TimingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_signal_at_close_executes_at_next_open_not_same_close(self) -> None:
        data = bars([FLAT, (101.0, 101.5, 100.5, 101.0), FLAT, FLAT])
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 95.0}, frozenset({2}))]).run({"X": data})
        entry = result.fills[0]
        self.assertEqual(entry.timestamp, data.bars[1].start)
        self.assertAlmostEqual(entry.price, 101.0)  # open of bar 1, not close of bar 0
        self.assertEqual(result.fills[1].timestamp, data.bars[3].start)

    def test_strategy_only_sees_completed_bars(self) -> None:
        seen: list[datetime] = []
        data = bars([FLAT] * 5)
        IntradayEngine(self.config, instrument(), [Scripted({}, seen=seen)]).run({"X": data})
        self.assertEqual(seen, [b.received_at for b in data.bars])

    def test_extra_delay_shifts_execution_by_one_bar(self) -> None:
        data = bars([FLAT, (101, 101.5, 100.5, 101), (102, 102.5, 101.5, 102), FLAT])
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 95.0})], extra_delay_bars=1).run({"X": data})
        self.assertEqual(result.fills[0].timestamp, data.bars[2].start)
        self.assertAlmostEqual(result.fills[0].price, 102.0)


@dataclass(frozen=True)
class Recorder:
    """Records (now, bar starts) of every context and checks that no bar is newer than ``now``."""

    seen: list[tuple[datetime, list[datetime]]]
    delay: timedelta = timedelta(0)
    name: str = "recorder"

    def parameters(self) -> dict[str, float]:
        return {}

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        assert all(b.received_at <= ctx.now for b in ctx.bars)
        assert all(h.received_at <= ctx.now for h in ctx.hourly)
        self.seen.append((ctx.now, [b.start for b in ctx.bars]))
        if self.delay and len(self.seen) == 1:
            return Signal(
                self.name,
                "X",
                Market.INDEX,
                ctx.now,
                ctx.now + self.delay,
                Direction.LONG,
                1.0,
                "delayed entry",
                stop_price=95.0,
            )
        return None


class AvailabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_late_bar_is_invisible_until_it_arrives(self) -> None:
        # Regression (PR #17 review): bar 0 is only available at 10:00, bar 1 already at 09:30.
        data = bars([FLAT] * 4)
        first = data.bars[0]
        late = replace(first, received_at=first.start + timedelta(hours=1))
        data = replace(data, bars=[late, *data.bars[1:]])
        seen: list[tuple[datetime, list[datetime]]] = []
        result = IntradayEngine(self.config, instrument(), [Recorder(seen)]).run({"X": data})
        starts = [b.start for b in data.bars]
        self.assertEqual(
            seen,
            [
                (data.bars[1].received_at, [starts[1]]),
                (data.bars[2].received_at, [starts[1], starts[2]]),
                (data.bars[3].received_at, starts),
            ],
        )
        self.assertTrue(any(d["event"] == "late_bar" for d in result.decisions))

    def test_earliest_execution_is_binding(self) -> None:
        data = bars([FLAT, FLAT, FLAT, (102.0, 102.5, 101.5, 102.0), FLAT])
        seen: list[tuple[datetime, list[datetime]]] = []
        strategy = Recorder(seen, delay=timedelta(minutes=20))
        result = IntradayEngine(self.config, instrument(), [strategy]).run({"X": data})
        # signal at 09:15, earliest 09:35 -> first eligible open is the 09:45 bar
        self.assertEqual(result.fills[0].timestamp, data.bars[3].start)
        self.assertAlmostEqual(result.fills[0].price, 102.0)


class StopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_gap_through_stop_fills_at_open_not_stop(self) -> None:
        data = bars([FLAT, FLAT, (90.0, 91.0, 89.0, 90.5), FLAT])
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 98.0})]).run({"X": data})
        stop_fill = result.fills[1]
        self.assertAlmostEqual(stop_fill.price, 90.0)
        self.assertEqual(result.trades[0].exit_reason, "protective stop")

    def test_intrabar_stop_fills_at_stop_price(self) -> None:
        data = bars([FLAT, FLAT, (100.0, 100.2, 97.0, 99.0), FLAT])
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 98.0})]).run({"X": data})
        self.assertAlmostEqual(result.fills[1].price, 98.0)

    def test_stop_in_entry_bar_is_assumed_hit(self) -> None:
        data = bars([FLAT, (100.0, 103.0, 97.0, 102.0), FLAT])
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 98.0})]).run({"X": data})
        self.assertEqual(result.trades[0].exit_reason, "protective stop")
        self.assertEqual(result.trades[0].exit_time, data.bars[1].start)

    def test_stop_slippage_makes_fill_worse(self) -> None:
        data = bars([FLAT, FLAT, (100.0, 100.2, 97.0, 99.0), FLAT])
        costs = CostProfile(0.0, 0.0, 10.0, 0.0)
        result = IntradayEngine(self.config, instrument(costs), [Scripted({0: 98.0})]).run({"X": data})
        self.assertLess(result.fills[1].price, 98.0)


class ExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config()

    def test_exit_is_partially_filled_when_volume_is_thin(self) -> None:
        prices = [FLAT] * 8
        data = bars(prices, volume=1e6)
        thin = [replace(b, volume=20.0) if i >= 2 else b for i, b in enumerate(data.bars)]
        data = replace(data, bars=thin)
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 99.0}, frozenset({2}))]).run({"X": data})
        exits = [f for f in result.fills if f.quantity < 0 and f.timestamp < data.bars[-1].end]
        self.assertGreater(len(exits), 1)  # 5 % of 20 = 1 unit per bar
        self.assertTrue(all(abs(f.quantity) <= 1.0 + 1e-9 for f in exits[:-1]))

    def test_overnight_financing_is_charged(self) -> None:
        day1 = bars([FLAT, FLAT]).bars
        day2 = bars([FLAT, FLAT], start=datetime(2024, 1, 3, 9, 0, tzinfo=UTC)).bars
        data = replace(bars([FLAT]), bars=day1 + day2)
        result = IntradayEngine(self.config, instrument(financing=0.036), [Scripted({0: 99.0})]).run({"X": data})
        self.assertGreater(result.financing_costs, 0)
        qty = result.fills[0].quantity
        self.assertAlmostEqual(result.financing_costs, qty * 100.0 * 0.036 / 360, places=6)

    def test_costs_reduce_result_and_are_reported(self) -> None:
        data = bars([FLAT, FLAT, FLAT, FLAT])
        free = IntradayEngine(self.config, instrument(), [Scripted({0: 99.0}, frozenset({1}))]).run({"X": data})
        paid = IntradayEngine(
            self.config, instrument(CostProfile(5, 4, 1, 1.0)), [Scripted({0: 99.0}, frozenset({1}))]
        ).run({"X": data})
        self.assertLess(paid.equity[-1].equity, free.equity[-1].equity)
        self.assertGreater(compute_metrics(paid)["total_costs"], 0)

    def test_signal_after_data_gap_is_skipped(self) -> None:
        data = bars([FLAT, FLAT, FLAT])
        data = replace(data, after_gap=frozenset({data.bars[1].start}))
        result = IntradayEngine(self.config, instrument(), [Scripted({1: 99.0})]).run({"X": data})
        self.assertEqual(result.fills, [])
        self.assertTrue(any(d.get("reason") == "bar follows a data gap" for d in result.decisions))

    def test_pending_entry_on_bar_after_gap_is_cancelled(self) -> None:
        # Regression (PR #17 review): an entry signalled before a gap was executed on the after_gap bar.
        full = bars([FLAT] * 4)
        kept = [b for i, b in enumerate(full.bars) if i != 1]
        data = replace(full, bars=kept, after_gap=frozenset({full.bars[2].start}))
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 95.0})]).run({"X": data})
        self.assertEqual(result.fills, [])
        cancelled = [d for d in result.decisions if d["event"] == "entry_cancelled"]
        self.assertEqual(len(cancelled), 1)
        self.assertEqual(cancelled[0]["reason"], "execution bar follows a data gap")

    def test_exit_is_not_blocked_by_gap(self) -> None:
        data = bars([FLAT] * 5)
        data = replace(data, after_gap=frozenset({data.bars[3].start}))
        result = IntradayEngine(self.config, instrument(), [Scripted({0: 95.0}, frozenset({2}))]).run({"X": data})
        self.assertEqual([f.timestamp for f in result.fills], [data.bars[1].start, data.bars[3].start])
        self.assertEqual(result.trades[0].exit_reason, "scripted exit")

    def test_stale_entry_after_overnight_break_is_cancelled(self) -> None:
        data = bars([FLAT] * 3)
        late = [
            replace(
                b,
                start=b.start + timedelta(days=1),
                end=b.end + timedelta(days=1),
                received_at=b.received_at + timedelta(days=1),
            )
            for b in data.bars[2:]
        ]
        data = replace(data, bars=data.bars[:2] + late)
        result = IntradayEngine(self.config, instrument(), [Scripted({1: 95.0})]).run({"X": data})
        self.assertEqual(result.fills, [])
        self.assertTrue(any(d.get("reason") == "signal too old for entry" for d in result.decisions))

    def test_drawdown_kill_switch_forces_controlled_exit(self) -> None:
        config = load_config()
        tight = replace(
            config.risk,
            risk_per_trade=0.005,
            max_risk_per_trade=0.005,
            drawdown_reduce=0.001,
            drawdown_block_new=0.002,
            drawdown_kill=0.003,
        )
        config = replace(config, risk=tight)
        # 2.5 units (50 EUR risk / 20 stop distance); prices fall 1.5 per bar without reaching the stop
        falling = [FLAT, FLAT] + [
            (100 - 1.5 * i, 100.2 - 1.5 * i, 99.5 - 1.5 * i, 99.6 - 1.5 * i) for i in range(1, 12)
        ]
        data = bars(falling)
        result = IntradayEngine(config, instrument(), [Scripted({0: 80.0})]).run({"X": data})
        kill_exit = next(t for t in result.trades if t.exit_reason.startswith("drawdown kill"))
        self.assertLess(kill_exit.exit_time, data.bars[-1].start)  # well before the end of data
        entries = [f for f in result.fills if f.quantity > 0]
        self.assertEqual(len(entries), 1)
        reasons = [t.exit_reason for t in result.trades]
        self.assertIn("drawdown kill switch: controlled de-risking", reasons)


class StrategyTests(unittest.TestCase):
    def _ctx(self, closes: list[float], hourly: list[float], in_position: bool = False) -> StrategyContext:
        start = datetime(2024, 1, 2, tzinfo=UTC)
        b = [
            Bar(
                "X",
                Market.INDEX,
                "15m",
                start + timedelta(minutes=15 * i),
                start + timedelta(minutes=15 * (i + 1)),
                c,
                c + 0.5,
                c - 0.5,
                c,
                1.0,
                "u",
                start + timedelta(minutes=15 * (i + 1)),
            )
            for i, c in enumerate(closes)
        ]
        h = [Bar("X", Market.INDEX, "1h", start, start, c, c, c, c, 1.0, "u", start) for c in hourly]
        return StrategyContext(b[-1].received_at, b, h, in_position, b[0].start if in_position else None, None)

    def test_trend_requires_breakout_and_higher_timeframe_trend(self) -> None:
        strategy = IntradayTrendStrategy(trend_window_hours=3, breakout_bars=4, exit_bars=2, atr_bars=3)
        breakout = [100, 100, 100, 100, 100, 102]
        self.assertEqual(strategy.evaluate(self._ctx(breakout, [90, 95, 100])).direction, Direction.LONG)
        self.assertIsNone(strategy.evaluate(self._ctx(breakout, [110, 105, 100])))  # downtrend on 1h
        self.assertIsNone(strategy.evaluate(self._ctx([100] * 6, [90, 95, 100])))  # no breakout

    def test_trend_stop_below_entry(self) -> None:
        strategy = IntradayTrendStrategy(trend_window_hours=3, breakout_bars=4, exit_bars=2, atr_bars=3)
        signal = strategy.evaluate(self._ctx([100, 100, 100, 100, 100, 102], [90, 95, 100]))
        assert signal is not None and signal.stop_price is not None
        self.assertLess(signal.stop_price, 102)

    def test_mean_reversion_enters_on_deviation_in_range_and_exits_at_reference(self) -> None:
        strategy = MeanReversionStrategy(
            reference_bars=10, entry_z=1.5, trend_filter_bars=10, max_efficiency=0.5, atr_bars=3
        )
        ranging = [100, 101, 100, 101, 100, 101, 100, 101, 100, 101, 100, 97]
        signal = strategy.evaluate(self._ctx(ranging, []))
        self.assertIsNotNone(signal)
        trending = [100 - i for i in range(12)]
        self.assertIsNone(strategy.evaluate(self._ctx(trending, [])))
        exit_signal = strategy.evaluate(self._ctx(ranging[:-1] + [101], [], in_position=True))
        assert exit_signal is not None
        self.assertEqual(exit_signal.direction, Direction.FLAT)


class MetricsTests(unittest.TestCase):
    def test_bootstrap_interval_is_deterministic_and_ordered(self) -> None:
        returns = [0.001 * ((i % 7) - 3) for i in range(100)]
        first = block_bootstrap_monthly(returns)
        self.assertEqual(first, block_bootstrap_monthly(returns))
        assert first is not None
        self.assertLessEqual(first[0], first[1])
        self.assertIsNone(block_bootstrap_monthly([0.01] * 5))


class EndToEndTests(unittest.TestCase):
    def test_import_to_report_pipeline_is_deterministic(self) -> None:
        """Data import -> features -> signal -> risk -> simulated order -> fill -> portfolio -> report."""
        config = load_config()
        session = SessionSpec("Europe/Berlin", time(9, 0), time(17, 30))
        synthetic = synthetic_intraday("DAX", days=40, seed=3, start_price=15000, session=session)
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "dax.csv"
            lines = ["timestamp,open,high,low,close,volume"]
            lines += [f"{b.start.isoformat()},{b.open},{b.high},{b.low},{b.close},{b.volume}" for b in synthetic.bars]
            csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            data = import_csv(CsvImportSpec(str(csv_path), "DAX", Market.INDEX, "test", "e2e", "UTC"), session)
            instruments = instruments_from_config(config)
            strategies = [IntradayTrendStrategy(trend_window_hours=10, breakout_bars=8), MeanReversionStrategy()]
            first = IntradayEngine(config, instruments, strategies).run({"DAX": data})
            second = IntradayEngine(config, instruments, strategies).run({"DAX": data})
            self.assertEqual([f.price for f in first.fills], [f.price for f in second.fills])
            self.assertTrue(any(d["event"] == "signal" for d in first.decisions))
            self.assertTrue(any(d["event"] == "risk" for d in first.decisions))
            self.assertGreater(len(first.fills), 0)
            report = write_intraday_report(first, Path(tmp) / "report")
            summary = json.loads((report / "summary.json").read_text(encoding="utf-8"))
            self.assertIn("geometric_monthly_return", summary["metrics"])
            self.assertTrue((report / "trades.csv").exists())
            self.assertTrue((report / "decisions.jsonl").exists())
            # accounting identity: equity change == sum of trade P&L - financing
            pnl = sum(t.pnl or 0.0 for t in first.trades)
            self.assertAlmostEqual(first.equity[-1].equity - config.initial_cash, pnl - first.financing_costs, places=6)

    def test_scenarios_cover_required_stress_cases(self) -> None:
        config = load_config()
        session = SessionSpec("Europe/Berlin", time(9, 0), time(17, 30))
        data = synthetic_intraday("DAX", days=15, seed=5, start_price=15000, session=session)
        output = run_scenarios(config, instruments_from_config(config), ["intraday_trend"], {"DAX": data})
        for name in ("baseline", "costs_2x", "spread_3x", "delay_1_bar", "params_minus_20pct", "params_plus_20pct"):
            self.assertIn(name, output)
        self.assertEqual(sum(1 for k in output if k.startswith("phase_")), 3)


if __name__ == "__main__":
    unittest.main()
