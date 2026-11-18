"""Operating modes and the persistent 15-minute paper/shadow runtime (T-006, spec §6, §12, §14).

Modes
-----
* ``backtest``: one engine run over the given files, no state.
* ``paper``: completed bars from a file feed drive the **same** ``IntradayEngine`` as the backtest. Orders
  go to the internal simulated paper broker (fills at the next eligible open, exactly as in the engine).
* ``shadow``: identical decisions, but nothing is submitted anywhere; intended orders are only logged.
* ``live``: technically locked. ``LiveGate`` lists every missing precondition and, even when all are present,
  refuses because live execution is not part of this build (T-016). No state, restart or model registry
  entry can switch a runtime to live.

Why replay instead of a second engine
-------------------------------------
Each tick stores newly completed bars (first-seen time = ``received_at``), replays the engine over the
whole stored history and books only what is new. The engine is deterministic and causal, so decisions are
identical to a backtest over the same bars with the same arrival times (no second implementation). Bars
received before ``trade_from`` are warm-up history only. Every tick also checks that everything booked
earlier is still reproduced unchanged; a divergence (changed config, code or data) halts the runtime.

Data rules: a bar is stored once per (symbol, start); a re-delivered identical bar is ignored, a changed
one halts the runtime. Incomplete bars (end after now) are not read. Bars before the end of the central
holdout window are refused before any price is parsed. During a session, if the newest bar is older than
``max_bar_age``, the tick books nothing (stale data -> no new orders); the engine itself later refuses
entries that follow a gap or whose signal has become too old.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from .config import AppConfig
from .domain import Bar, Fill
from .intraday_data import CsvImportSpec, DatasetManifest, ImportedDataset, import_csv, scan_gaps
from .intraday_engine import EngineResult, InstrumentSpec, IntradayEngine
from .intraday_strategies import IntradayStrategy

# End of the central holdout window (docs/research/T-008-protocol.json, central.end_exclusive). The paper
# and shadow runtime never reads bars before it, so running the service cannot expose the holdout.
PAPER_DATA_START = datetime(2026, 11, 1, tzinfo=UTC)
BAR = timedelta(minutes=15)


class Mode(StrEnum):
    BACKTEST = "backtest"
    PAPER = "paper"
    SHADOW = "shadow"
    LIVE = "live"


class LiveLocked(RuntimeError):
    def __init__(self, reasons: list[str]) -> None:
        super().__init__("live trading locked: " + "; ".join(reasons))
        self.reasons = reasons


class RuntimeHalted(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- live gate
LIVE_CREDENTIALS = ("LIVE_BROKER_API_KEY", "LIVE_BROKER_API_SECRET", "LIVE_BROKER_ACCOUNT_ID")


def code_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL).strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


@dataclass(frozen=True)
class LiveGate:
    """All preconditions for live trading (spec §12, §18.1, §18.9). ``check`` always raises in this build."""

    config: AppConfig
    env: dict[str, str]
    release_path: Path | None
    head: str

    def missing(self) -> list[str]:
        reasons: list[str] = []
        live = self.config.raw.get("live", {})
        if not isinstance(live, dict) or live.get("enabled") is not True:
            reasons.append("no live configuration ([live] enabled = true)")
        absent = [name for name in LIVE_CREDENTIALS if not self.env.get(name)]
        if absent:
            reasons.append("live credentials missing: " + ", ".join(absent))
        if self.release_path is None or not self.release_path.is_file():
            reasons.append("no documented release file")
        else:
            try:
                release = json.loads(self.release_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                release = None
            if not isinstance(release, dict):
                reasons.append("release file unreadable")
            else:
                if not self.head or release.get("code_sha") != self.head:
                    reasons.append("release is not bound to the running commit")
                if release.get("config_digest") != self.config.digest:
                    reasons.append("release is not bound to the active config digest")
                if not release.get("approved_by") or not release.get("approved_at"):
                    reasons.append("release lacks approver and approval time")
        return reasons

    def check(self) -> None:
        reasons = self.missing()
        # Even a complete set of preconditions does not unlock live orders before T-014..T-016 exist.
        reasons.append("live execution is not implemented in this build (T-016 live release process)")
        raise LiveLocked(reasons)


# ---------------------------------------------------------------------------------------------- order sinks
class OrderSink(Protocol):
    """Where the runtime sends what the engine decided. ``mode`` is stored with every record."""

    mode: Mode

    def book(self, fill: Fill) -> None: ...


class SimulatedPaperBroker:
    """Internal paper broker: the engine's fill at the eligible open is the execution. No network."""

    mode = Mode.PAPER

    def __init__(self) -> None:
        self.orders: list[str] = []

    def book(self, fill: Fill) -> None:
        self.orders.append(fill.fill_id)


class ShadowLog:
    """Shadow mode: intended orders are recorded by the runtime only; nothing is submitted."""

    mode = Mode.SHADOW

    def book(self, fill: Fill) -> None:
        raise RuntimeError("shadow mode never submits orders")


# ---------------------------------------------------------------------------------------------- runtime
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS bars(
    symbol TEXT NOT NULL, start TEXT NOT NULL, payload TEXT NOT NULL, received_at TEXT NOT NULL,
    PRIMARY KEY(symbol, start));
CREATE TABLE IF NOT EXISTS signals(id TEXT PRIMARY KEY, time TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS fills(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, client_id TEXT UNIQUE NOT NULL, mode TEXT NOT NULL,
    detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit(
    seq INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT NOT NULL);
"""


def _fill_record(fill: Fill) -> dict[str, Any]:
    return {
        "fill_id": fill.fill_id,
        "symbol": fill.symbol,
        "side": "buy" if fill.quantity > 0 else "sell",
        "quantity": fill.quantity,
        "price": fill.price,
        "fee": fill.fee,
        "time": fill.timestamp.isoformat(),
    }


def _signal_id(decision: dict[str, object]) -> str:
    key = "|".join(str(decision.get(k)) for k in ("time", "symbol", "strategy", "direction", "reason"))
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def _bar_payload(bar: Bar) -> str:
    return json.dumps([bar.end.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume], separators=(",", ":"))


def check_feed(path: Path) -> None:
    """First pass over the timestamp column only: any bar before ``PAPER_DATA_START`` stops the feed before a
    price is parsed. Second pass: a re-delivered row must be identical; conflicting duplicates stop the feed."""
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            raw = (row.get("timestamp") or "").strip()
            try:
                start = datetime.fromisoformat(raw)
            except ValueError:
                continue  # rejected later by the regular import checks
            if start.tzinfo is None or start < PAPER_DATA_START:
                raise RuntimeHalted(f"{path.name}: feed contains bars before {PAPER_DATA_START.isoformat()}")
    seen: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("timestamp") or "").strip()
            if key in seen and seen[key] != row:
                raise RuntimeHalted(f"{path.name}: conflicting rows for {key}")
            seen[key] = row


class PaperRuntime:
    def __init__(
        self,
        config: AppConfig,
        instruments: dict[str, InstrumentSpec],
        strategies: list[IntradayStrategy],
        state_dir: str | Path,
        mode: Mode,
        feeds: dict[str, Path],
        trade_from: datetime,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_bar_age: timedelta = timedelta(minutes=35),
        feed_delay: timedelta = timedelta(minutes=15),
        sink: OrderSink | None = None,
    ) -> None:
        if mode == Mode.LIVE:
            raise LiveLocked(["the paper runtime never runs live; use the live gate (locked in this build)"])
        if mode not in (Mode.PAPER, Mode.SHADOW):
            raise ValueError("the runtime serves paper and shadow; backtests use the engine directly")
        if trade_from.tzinfo is None or trade_from < PAPER_DATA_START:
            raise ValueError(f"trade_from must be timezone-aware and not before {PAPER_DATA_START.isoformat()}")
        if not feeds or set(feeds) - set(instruments):
            raise ValueError("every feed needs an instrument specification")
        self.config = config
        self.instruments = instruments
        self.strategies = strategies
        self.mode = mode
        self.feeds = {symbol: Path(path) for symbol, path in feeds.items()}
        self.trade_from = trade_from
        self.clock = clock
        # ADR 0006: delayed SIP. A bar is complete at the provider only ``feed_delay`` after its end (a newer row
        # may still be running), and a normal newest bar is then 15-30 minutes old.
        if feed_delay < timedelta(0) or max_bar_age <= feed_delay:
            raise ValueError("max_bar_age must exceed the feed delay")
        self.max_bar_age = max_bar_age
        self.feed_delay = feed_delay
        self.sink: OrderSink = sink or (SimulatedPaperBroker() if mode == Mode.PAPER else ShadowLog())
        if self.sink.mode != mode:
            raise ValueError("order sink does not match the runtime mode")
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.state_dir / "runtime.sqlite")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(SCHEMA)
        self._bind_identity()

    # ------------------------------------------------------------------ state
    def close(self) -> None:
        self.db.close()

    @contextmanager
    def _tx(self) -> Iterator[None]:
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def _meta(self, key: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=?", (key, value, value)
        )

    def _audit(self, kind: str, detail: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT INTO audit(time, kind, detail) VALUES(?, ?, ?)",
            (self.clock().isoformat(), kind, json.dumps(detail, sort_keys=True, default=str)),
        )

    def _bind_identity(self) -> None:
        """A state directory belongs to one mode, config, strategy set and start; changing any needs a new one."""
        identity = {
            "mode": self.mode.value,
            "config_digest": self.config.digest,
            "strategies": sorted(s.name for s in self.strategies),
            "parameters": {s.name: s.parameters() for s in self.strategies},
            "symbols": sorted(self.feeds),
            "trade_from": self.trade_from.isoformat(),
        }
        stored = self._meta("identity")
        with self._tx():
            if stored is None:
                self._set_meta("identity", json.dumps(identity, sort_keys=True))
                self._audit("start", {"identity": identity, "first_start": True})
                return
            if json.loads(stored) != json.loads(json.dumps(identity, sort_keys=True)):
                self._audit("start_refused", {"stored": json.loads(stored), "requested": identity})
                raise RuntimeHalted("state directory belongs to a different mode, config, strategy set or start")
            self._audit("start", {"restart": True})

    @property
    def halted(self) -> str | None:
        return self._meta("halted")

    def halt(self, reason: str) -> None:
        """Latching stop; survives restarts. Only ``resume`` (an explicit operator action) clears it."""
        with self._tx():
            self._set_meta("halted", reason)
            self._audit("halt", {"reason": reason})

    def resume(self, operator: str) -> None:
        if not operator.strip():
            raise ValueError("resume needs the operator's name")
        with self._tx():
            self.db.execute("DELETE FROM meta WHERE key='halted'")
            self._audit("resume", {"operator": operator})

    # ------------------------------------------------------------------ data
    def _stored_bars(self, symbol: str) -> list[Bar]:
        spec = self.instruments[symbol]
        rows = self.db.execute(
            "SELECT start, payload, received_at FROM bars WHERE symbol=? ORDER BY start", (symbol,)
        ).fetchall()
        output = []
        for start, payload, received in rows:
            end, o, h, low, c, v = json.loads(payload)
            output.append(
                Bar(
                    symbol,
                    spec.market,
                    "15m",
                    datetime.fromisoformat(start),
                    datetime.fromisoformat(end),
                    o,
                    h,
                    low,
                    c,
                    v,
                    "paper-feed",
                    datetime.fromisoformat(received),
                )
            )
        return output

    def ingest(self, now: datetime) -> dict[str, int]:
        """Store newly completed bars once, stamped with the time they were first seen."""
        counts = {"new": 0, "duplicate": 0, "incomplete": 0}
        for symbol, path in self.feeds.items():
            if not path.exists():
                continue
            check_feed(path)
            spec = self.instruments[symbol]
            data = import_csv(
                CsvImportSpec(str(path), symbol, spec.market, spec.venue, "paper-feed", "UTC", 15), spec.session
            )
            # identical re-delivered rows were verified by check_feed; the import keeps the first copy
            reported = data.manifest.quality.get("rejected")
            rejected = {k: v for k, v in (reported if isinstance(reported, dict) else {}).items() if k != "duplicate"}
            if rejected:
                raise RuntimeHalted(f"{symbol}: invalid feed rows {rejected}")
            for bar in data.bars:
                if bar.end > now - self.feed_delay:
                    counts["incomplete"] += 1
                    continue
                payload = _bar_payload(bar)
                row = self.db.execute(
                    "SELECT payload FROM bars WHERE symbol=? AND start=?", (symbol, bar.start.isoformat())
                ).fetchone()
                if row is None:
                    self.db.execute(
                        "INSERT INTO bars(symbol, start, payload, received_at) VALUES(?, ?, ?, ?)",
                        (symbol, bar.start.isoformat(), payload, now.isoformat()),
                    )
                    counts["new"] += 1
                elif row[0] == payload:
                    counts["duplicate"] += 1
                else:
                    raise RuntimeHalted(f"{symbol} {bar.start.isoformat()}: bar changed after it was stored")
        return counts

    def stale_symbols(self, now: datetime) -> list[str]:
        stale = []
        for symbol in self.feeds:
            session = self.instruments[symbol].session
            if not session.contains(now - self.max_bar_age):
                continue  # market closed or first bar not yet due: no bar is expected
            row = self.db.execute("SELECT max(start) FROM bars WHERE symbol=?", (symbol,)).fetchone()
            newest_end = datetime.fromisoformat(row[0]) + BAR if row and row[0] else None
            if newest_end is None or now - newest_end > self.max_bar_age:
                stale.append(symbol)
        return stale

    def _datasets(self) -> dict[str, ImportedDataset]:
        datasets = {}
        for symbol in self.feeds:
            bars = self._stored_bars(symbol)
            spec = self.instruments[symbol]
            manifest = DatasetManifest(
                symbol, spec.market.value, spec.venue, "paper-feed", "UTC", 15, "runtime", "runtime", False, {}
            )
            datasets[symbol] = ImportedDataset(bars, manifest, frozenset(scan_gaps(bars, spec.session, BAR, 1.0)))
        return datasets

    def replay(self) -> EngineResult:
        engine = IntradayEngine(
            self.config, self.instruments, self.strategies, trade_from=self.trade_from, liquidate_at_end=False
        )
        datasets = {s: d for s, d in self._datasets().items() if d.bars}
        if not datasets:
            raise RuntimeHalted("no stored bars")
        return engine.run(datasets)

    # ------------------------------------------------------------------ tick
    def tick(self) -> dict[str, Any]:
        now = self.clock()
        if now.tzinfo is None:
            raise ValueError("clock must be timezone-aware")
        if self.halted:
            with self._tx():
                self._audit("tick_skipped", {"halted": self.halted})
            return {"status": "halted", "reason": self.halted}
        try:
            with self._tx():
                counts = self.ingest(now)
                self._audit("ingest", counts)
        except RuntimeHalted as error:
            self.halt(str(error))
            return {"status": "halted", "reason": str(error)}
        stale = self.stale_symbols(now)
        if stale:
            with self._tx():
                self._audit("stale_data", {"symbols": stale})
            return {"status": "stale", "symbols": stale, "bars": counts, "new_orders": 0}
        if not self.db.execute("SELECT 1 FROM bars LIMIT 1").fetchone():
            return {"status": "waiting", "bars": counts, "new_orders": 0}
        result = self.replay()
        booked = [json.loads(row[0]) for row in self.db.execute("SELECT detail FROM fills ORDER BY seq")]
        replayed = [_fill_record(fill) for fill in result.fills]
        if replayed[: len(booked)] != booked:
            reason = "replay diverged from booked fills (config, code or data changed)"
            self.halt(reason)
            return {"status": "halted", "reason": reason}
        new_fills = result.fills[len(booked) :]
        new_signals = 0
        with self._tx():
            for decision in result.decisions:
                if decision.get("event") != "signal":
                    continue
                cursor = self.db.execute(
                    "INSERT OR IGNORE INTO signals(id, time, detail) VALUES(?, ?, ?)",
                    (_signal_id(decision), str(decision["time"]), json.dumps(decision, sort_keys=True, default=str)),
                )
                new_signals += cursor.rowcount
            for fill in new_fills:
                record = _fill_record(fill)
                self.db.execute(
                    "INSERT INTO fills(client_id, mode, detail) VALUES(?, ?, ?)",
                    (fill.fill_id, self.mode.value, json.dumps(record, sort_keys=True)),
                )
                self._audit("order" if self.mode == Mode.PAPER else "shadow_order", record)
            last = result.equity[-1] if result.equity else None
            status = {
                "status": "ok",
                "mode": self.mode.value,
                "bars": counts,
                "new_signals": new_signals,
                "new_orders": len(new_fills),
                "equity": last.equity if last else self.config.initial_cash,
                "as_of": last.timestamp.isoformat() if last else None,
            }
            self._set_meta("last_status", json.dumps(status, sort_keys=True))
            self._audit("tick", status)
        if self.mode == Mode.PAPER:  # shadow never submits anything
            for fill in new_fills:  # after the commit; a lost submission is visible in the order journal
                self.sink.book(fill)
        return status

    def booked_fills(self) -> list[dict[str, Any]]:
        return [
            {**json.loads(detail), "mode": mode}
            for mode, detail in self.db.execute("SELECT mode, detail FROM fills ORDER BY seq")
        ]

    def signals(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT detail FROM signals ORDER BY time, id")]
