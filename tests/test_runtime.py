from __future__ import annotations

import json
import math
import tempfile
import unittest
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from localtrader.calendars import expected_bar_starts, trading_days
from localtrader.config import load_config
from localtrader.domain import Direction, Signal
from localtrader.intraday_data import DatasetManifest, ImportedDataset, scan_gaps
from localtrader.intraday_engine import IntradayEngine, instruments_from_config
from localtrader.intraday_strategies import StrategyContext
from localtrader.research_protocol import load_protocol
from localtrader.runtime import (
    BAR,
    PAPER_DATA_START,
    LiveGate,
    LiveLocked,
    Mode,
    PaperRuntime,
    RuntimeHalted,
    SimulatedPaperBroker,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_config(ROOT / "config" / "default.toml")
INSTRUMENTS = instruments_from_config(CONFIG)
TRADE_FROM = datetime(2026, 11, 3, 14, 30, tzinfo=UTC)  # day 1 (Nov 2) is warm-up history only
SEEN_AFTER = timedelta(minutes=16)  # delayed SIP (ADR 0006): a bar is complete 15 minutes after its end


@dataclass(frozen=True)
class EveryHour:
    """Deterministic test strategy: enter at each full hour when flat, leave two bars later."""

    name: str = "every_hour"

    def parameters(self) -> dict[str, float]:
        return {"hold_bars": 2.0}

    def evaluate(self, ctx: StrategyContext) -> Signal | None:
        bar = ctx.bars[-1]
        common = {"strategy": self.name, "symbol": bar.symbol, "market": bar.market, "strength": 1.0}
        if ctx.in_position and ctx.entry_time and ctx.now - ctx.entry_time >= timedelta(minutes=30):
            return Signal(
                signal_time=ctx.now, earliest_execution=ctx.now, direction=Direction.FLAT, reason="time exit", **common
            )
        if not ctx.in_position and bar.end.minute == 0:
            return Signal(
                signal_time=ctx.now,
                earliest_execution=ctx.now,
                direction=Direction.LONG,
                reason="hourly entry",
                stop_price=bar.close * 0.97,
                **common,
            )
        return None


def bar_rows(days: int = 4) -> list[tuple[datetime, float, float, float, float, float]]:
    rows = []
    for index, start in enumerate(
        t
        for day in trading_days(datetime(2026, 11, 2).date(), datetime(2026, 11, 30).date())[:days]
        for t in expected_bar_starts(day)
    ):
        mid = 600 + 3 * math.sin(index / 5) + index * 0.01
        rows.append((start, mid, mid + 0.4, mid - 0.4, mid + 0.1, 2_000_000.0))
    return rows


def write_feed(path: Path, rows: list[tuple[datetime, float, float, float, float, float]]) -> None:
    lines = ["timestamp,open,high,low,close,volume"]
    lines += [f"{t.isoformat()},{o},{h},{low},{c},{v}" for t, o, h, low, c, v in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.feed = self.dir / "SPY_15m.csv"
        self.rows = bar_rows()
        write_feed(self.feed, self.rows)
        self.clock = Clock(self.rows[0][0])

    def runtime(self, mode: Mode = Mode.PAPER, state: str = "state", **kwargs: object) -> PaperRuntime:
        runtime = PaperRuntime(
            CONFIG,
            INSTRUMENTS,
            [EveryHour()],
            self.dir / state,
            mode,
            {"SPY": self.feed},
            TRADE_FROM,
            clock=self.clock,
            **kwargs,  # type: ignore[arg-type]
        )
        self.addCleanup(runtime.close)
        return runtime

    def run_ticks(self, runtime: PaperRuntime, until: datetime) -> list[dict[str, object]]:
        statuses = []
        for start, *_ in self.rows:
            moment = start + BAR + SEEN_AFTER
            if moment <= self.clock.now or moment > until:
                continue
            self.clock.now = moment
            statuses.append(runtime.tick())
        return statuses

    def backtest_fills(self) -> list[dict[str, object]]:
        """The same bars and arrival times through the engine directly (backtest path)."""
        spec = INSTRUMENTS["SPY"]
        runtime = self.runtime(state="reference")
        bars = [replace(b, received_at=b.end + SEEN_AFTER) for b in runtime._stored_bars("SPY")]
        if not bars:
            self.clock.now = self.rows[-1][0] + BAR + SEEN_AFTER
            runtime.ingest(self.clock.now)
            bars = [replace(b, received_at=b.end + SEEN_AFTER) for b in runtime._stored_bars("SPY")]
        manifest = DatasetManifest("SPY", "etf", "x", "x", "UTC", 15, "x", "x", False, {})
        data = ImportedDataset(bars, manifest, frozenset(scan_gaps(bars, spec.session, BAR, 1.0)))
        engine = IntradayEngine(CONFIG, INSTRUMENTS, [EveryHour()], trade_from=TRADE_FROM, liquidate_at_end=False)
        result = engine.run({"SPY": data})
        return [{"time": f.timestamp.isoformat(), "quantity": f.quantity, "price": f.price} for f in result.fills]

    @staticmethod
    def essentials(fills: list[dict[str, object]]) -> list[dict[str, object]]:
        return [{k: f[k] for k in ("time", "quantity", "price")} for f in fills]

    # ------------------------------------------------------------------ paper = backtest
    def test_paper_books_exactly_the_engine_decisions_and_survives_restart(self) -> None:
        end = self.rows[-1][0] + BAR + SEEN_AFTER
        middle = datetime(2026, 11, 4, 17, 1, tzinfo=UTC)
        first = self.runtime()
        statuses = self.run_ticks(first, middle)
        self.assertTrue(all(s["status"] == "ok" for s in statuses), statuses[-1])
        booked_before = first.booked_fills()
        signals_before = first.signals()
        first.close()

        restarted = self.runtime()  # same state directory
        self.assertEqual(restarted.booked_fills(), booked_before)
        self.run_ticks(restarted, end)
        fills = restarted.booked_fills()
        self.assertGreater(len(fills), 6)
        self.assertTrue(all(f["mode"] == "paper" for f in fills))
        self.assertTrue(all(datetime.fromisoformat(f["time"]) >= TRADE_FROM for f in fills))  # warm-up: no trades
        self.assertEqual(self.essentials(fills), self.backtest_fills())
        ids = [s["time"] + s["direction"] for s in restarted.signals()]
        self.assertEqual(len(ids), len(set(ids)))  # restart created no duplicate signals
        self.assertEqual(restarted.signals()[: len(signals_before)], signals_before)

    def test_redelivered_bars_create_no_duplicate_signals_or_orders(self) -> None:
        end = self.rows[-1][0] + BAR + SEEN_AFTER
        runtime = self.runtime()
        self.run_ticks(runtime, end)
        fills, signals = runtime.booked_fills(), runtime.signals()
        write_feed(self.feed, self.rows + self.rows[-30:])  # the provider repeats bars (also inside the file)
        self.clock.now += timedelta(minutes=1)
        status = runtime.tick()
        self.assertEqual(status["bars"]["new"], 0)
        self.assertGreater(status["bars"]["duplicate"], 0)
        self.assertEqual(status["new_orders"], 0)
        self.assertEqual((runtime.booked_fills(), runtime.signals()), (fills, signals))

    def test_changed_bar_or_holdout_rows_halt_until_an_operator_resumes(self) -> None:
        runtime = self.runtime()
        self.run_ticks(runtime, datetime(2026, 11, 3, 16, 1, tzinfo=UTC))
        changed = list(self.rows)
        start, o, h, low, c, v = changed[3]
        changed[3] = (start, o, h, low, c + 0.05, v)
        write_feed(self.feed, changed)
        self.clock.now += BAR
        self.assertEqual(runtime.tick()["status"], "halted")
        runtime.close()
        write_feed(self.feed, self.rows)
        again = self.runtime()  # the halt survives a restart
        self.assertEqual(again.tick()["status"], "halted")
        again.resume("operator")
        self.assertEqual(again.tick()["status"], "ok")

        write_feed(self.feed, [(datetime(2026, 10, 30, 19, 45, tzinfo=UTC), 1, 1, 1, 1, 1), *self.rows])
        self.clock.now += BAR
        status = again.tick()
        self.assertEqual(status["status"], "halted")
        self.assertIn("before", status["reason"])

    def test_stale_data_books_no_new_orders_and_late_bars_decide_only_from_arrival(self) -> None:
        runtime = self.runtime()
        cut = datetime(2026, 11, 3, 15, 16, tzinfo=UTC)  # 10:00 New York bar arrives (delayed SIP): entry signal
        self.run_ticks(runtime, cut)
        before = runtime.booked_fills()
        write_feed(self.feed, [r for r in self.rows if r[0] + BAR + SEEN_AFTER <= cut])  # the feed stops
        self.clock.now = cut + timedelta(minutes=45)
        status = runtime.tick()
        self.assertEqual(status["status"], "stale")
        self.assertEqual(status["new_orders"], 0)
        self.assertEqual(runtime.booked_fills(), before)
        resumed = cut + timedelta(hours=2)
        write_feed(self.feed, self.rows)  # delivery resumes; the missed bars arrive late
        self.clock.now = resumed
        self.assertEqual(runtime.tick()["status"], "ok")
        times = [datetime.fromisoformat(f["time"]) for f in runtime.booked_fills()]
        # The order decided on fresh data executes at the next eligible open (15:30 UTC). Late bars become
        # information only at their arrival: no decision dated inside the outage, no fill before arrival.
        self.assertIn(datetime(2026, 11, 3, 15, 30, tzinfo=UTC), times)
        self.assertEqual([t for t in times if datetime(2026, 11, 3, 15, 30, tzinfo=UTC) < t < resumed], [])
        outage = [s for s in runtime.signals() if cut < datetime.fromisoformat(s["time"]) < resumed]
        self.assertEqual(outage, [])

    def test_bars_inside_the_feed_delay_are_not_read(self) -> None:
        runtime = self.runtime()
        end_of_first = self.rows[0][0] + BAR
        self.clock.now = end_of_first + timedelta(minutes=10)  # the provider may still revise this bar
        self.assertEqual(runtime.ingest(self.clock.now)["new"], 0)
        self.clock.now = end_of_first + timedelta(minutes=15)
        self.assertEqual(runtime.ingest(self.clock.now)["new"], 1)
        with self.assertRaises(ValueError):
            PaperRuntime(
                CONFIG,
                INSTRUMENTS,
                [EveryHour()],
                self.dir / "y",
                Mode.PAPER,
                {"SPY": self.feed},
                TRADE_FROM,
                max_bar_age=timedelta(minutes=10),
            )

    def test_market_closed_is_not_stale(self) -> None:
        runtime = self.runtime()
        self.clock.now = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)  # Saturday
        self.assertNotEqual(runtime.tick()["status"], "stale")

    # ------------------------------------------------------------------ shadow
    def test_shadow_logs_the_same_decisions_but_never_submits(self) -> None:
        class Exploding:
            mode = Mode.SHADOW

            def book(self, fill: object) -> None:
                raise AssertionError("shadow submitted an order")

        end = self.rows[-1][0] + BAR + SEEN_AFTER
        shadow = self.runtime(Mode.SHADOW, state="shadow", sink=Exploding())
        self.run_ticks(shadow, end)
        self.clock.now = self.rows[0][0]
        broker = SimulatedPaperBroker()
        paper = self.runtime(Mode.PAPER, state="paper", sink=broker)
        self.run_ticks(paper, end)
        self.assertTrue(all(f["mode"] == "shadow" for f in shadow.booked_fills()))
        self.assertEqual(self.essentials(shadow.booked_fills()), self.essentials(paper.booked_fills()))
        self.assertEqual(len(broker.orders), len(paper.booked_fills()))
        with self.assertRaises(ValueError):
            self.runtime(Mode.SHADOW, state="mismatch", sink=SimulatedPaperBroker())

    def test_state_directory_is_bound_to_mode_config_and_start(self) -> None:
        self.runtime(Mode.SHADOW, state="bound").close()
        with self.assertRaises(RuntimeHalted):
            self.runtime(Mode.PAPER, state="bound")
        with self.assertRaises(ValueError):
            PaperRuntime(
                CONFIG,
                INSTRUMENTS,
                [EveryHour()],
                self.dir / "x",
                Mode.PAPER,
                {"SPY": self.feed},
                PAPER_DATA_START - timedelta(days=1),
            )

    # ------------------------------------------------------------------ live
    def test_live_is_locked_with_every_precondition_listed(self) -> None:
        with self.assertRaises(LiveLocked):
            self.runtime(Mode.LIVE, state="live")
        gate = LiveGate(CONFIG, {}, None, "abc")
        with self.assertRaises(LiveLocked) as missing:
            gate.check()
        text = " ".join(missing.exception.reasons)
        for part in ("live configuration", "credentials", "release file", "T-016"):
            self.assertIn(part, text)

        live_config = replace(CONFIG, raw={**CONFIG.raw, "live": {"enabled": True}})
        release = self.dir / "release.json"
        release.write_text(
            json.dumps(
                {"code_sha": "abc", "config_digest": live_config.digest, "approved_by": "user", "approved_at": "now"}
            )
        )
        env = {"LIVE_BROKER_API_KEY": "k", "LIVE_BROKER_API_SECRET": "s", "LIVE_BROKER_ACCOUNT_ID": "a"}
        complete = LiveGate(live_config, env, release, "abc")
        self.assertEqual(complete.missing(), [])
        with self.assertRaises(LiveLocked) as still:
            complete.check()  # complete preconditions still do not unlock this build
        self.assertEqual(len(still.exception.reasons), 1)
        self.assertIn("running commit", " ".join(LiveGate(live_config, env, release, "def").missing()))

    def test_paper_data_starts_after_the_central_holdout_window(self) -> None:
        protocol = load_protocol(ROOT / "docs" / "research" / "T-008-protocol.json")
        self.assertEqual(PAPER_DATA_START, datetime.fromisoformat(protocol["central"]["end_exclusive"]))


if __name__ == "__main__":
    unittest.main()


class CliTests(unittest.TestCase):
    def test_run_live_exits_locked_and_paper_needs_a_start(self) -> None:
        import contextlib
        import io

        from localtrader.cli import build_parser

        def main(argv: list[str]) -> int:
            args = build_parser().parse_args(argv)
            return int(args.func(args))

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["run", "--mode", "live", "--env-file", "/nonexistent/.env"])
        self.assertEqual(code, 2)
        self.assertTrue(json.loads(out.getvalue())["locked"])
        with self.assertRaises(SystemExit):
            main(["run", "--mode", "paper", "--feed", "SPY=x.csv"])
