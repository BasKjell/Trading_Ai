"""Download multi-year 15-minute ETF bars from Alpaca into the CSV format read by ``import_csv``.

Source: Alpaca Market Data v2 ``/stocks/bars`` with the existing paper key (Basic plan, no extra cost).
Feed ``sip`` (consolidated tape, all US venues) with ``adjustment=all`` (split- and dividend-adjusted).
Only bars that start inside the regular session (09:30-16:00 America/New_York) are written; extended
hours have thin volume and Alpaca accepts only limit orders there, so the engine must not fill on them.

Raw API pages and a manifest stay under ``data/raw/alpaca/<request_id>/`` (not versioned), so every CSV
can be traced back to the exact request.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, Protocol

from .domain import Bar
from .intraday_data import SessionSpec

US_REGULAR_SESSION = SessionSpec("America/New_York", time(9, 30), time(16, 0))
CSV_HEADER = ("timestamp", "open", "high", "low", "close", "volume")


class BarDownloader(Protocol):
    def download(
        self, request: Any, progress: Callable[[str], None] | None = None
    ) -> tuple[list[Bar], dict[str, Any], Path]: ...


def regular_session_bars(bars: list[Bar], session: SessionSpec = US_REGULAR_SESSION) -> list[Bar]:
    """Bars that start inside the session; a bar starting at the session end belongs to after-hours."""
    return [bar for bar in bars if session.contains(bar.start)]


def write_csv(bars: list[Bar], path: str | Path) -> str:
    """Write bars as UTC ISO timestamps; returns the SHA-256 of the file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(CSV_HEADER)
        for bar in sorted(bars, key=lambda b: b.start):
            writer.writerow(
                [
                    bar.start.astimezone(UTC).isoformat(),
                    repr(bar.open),
                    repr(bar.high),
                    repr(bar.low),
                    repr(bar.close),
                    repr(bar.volume),
                ]
            )
    return hashlib.sha256(target.read_bytes()).hexdigest()


def fetch_intraday_csv(
    downloader: BarDownloader,
    symbol: str,
    start: datetime,
    end: datetime,
    output: str | Path,
    feed: str = "sip",
    progress: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    from .data_requests import DataRequestSpec

    request = DataRequestSpec(
        provider="alpaca",
        endpoint_type="stocks",
        symbols=(symbol,),
        timeframe="15Min",
        start=start,
        end=end,
        feed=feed,
        purpose="research:T-018 multi-year 15-minute data",
        strategy_name="intraday",
    )
    bars, manifest, _ = downloader.download(request, progress)
    session_bars = regular_session_bars([b for b in bars if b.symbol == symbol])
    digest = write_csv(session_bars, output)
    summary = {
        "symbol": symbol,
        "provider": "alpaca",
        "feed": feed,
        "adjustment": "all",
        "request_id": manifest.get("request_id"),
        "downloaded_at": manifest.get("downloaded_at"),
        "bars_downloaded": len(bars),
        "bars_regular_session": len(session_bars),
        "first_bar": session_bars[0].start.isoformat() if session_bars else None,
        "last_bar": session_bars[-1].start.isoformat() if session_bars else None,
        "csv": str(output),
        "csv_sha256": digest,
    }
    Path(str(output) + ".manifest.json").write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def default_end(now: datetime | None = None) -> datetime:
    """Latest allowed end: at least 15 minutes old (Basic plan SIP restriction), aligned to 15 minutes."""
    moment = (now or datetime.now(UTC)) - timedelta(minutes=30)
    return moment.replace(minute=moment.minute - moment.minute % 15, second=0, microsecond=0)
