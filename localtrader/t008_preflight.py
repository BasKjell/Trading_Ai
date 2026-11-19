"""Inspect custodian-designated T-008 research exports without running an experiment."""

from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .fx_rates import available_at
from .holdout import HOLDOUT_START
from .intraday_fetch import CSV_HEADER
from .research_protocol import ProtocolBlocked
from .t008 import UNIVERSE, digest_map, sha
from .walkforward import QUARTER

SCHEMAS = {
    **{f"{symbol}_15m.csv": CSV_HEADER for symbol in UNIVERSE},
    **{f"{symbol}_dividends.csv": ("ex_date", "pay_date", "amount", "source") for symbol in UNIVERSE},
    "EURUSD_ecb.csv": ("date", "usd_per_eur", "available_at"),
}


def _row_date(name: str, row: list[str]) -> date | datetime:
    if name.endswith("_15m.csv"):
        starts = datetime.fromisoformat(row[0])
        if starts.utcoffset() is None:
            raise ValueError("bar timestamp must have a timezone")
        if starts + QUARTER >= HOLDOUT_START:
            raise ValueError("bar reaches the holdout boundary")
        return starts

    day = date.fromisoformat(row[0])
    if day >= HOLDOUT_START.date():
        raise ValueError("date reaches the holdout boundary")
    if name == "EURUSD_ecb.csv":
        published = datetime.fromisoformat(row[2])
        if published != available_at(day) or published >= HOLDOUT_START:
            raise ValueError("FX availability is inconsistent with its research date")
    return day


def _inspect(name: str, payload: bytes) -> dict[str, Any]:
    rows = csv.reader(io.StringIO(payload.decode("utf-8"), newline=""), strict=True)
    schema = SCHEMAS[name]
    if tuple(next(rows, [])) != schema:
        raise ProtocolBlocked([f"{name}: unexpected CSV header"])
    first: date | datetime | None = None
    previous: date | datetime | None = None
    count = 0
    for row in rows:
        line = rows.line_num
        if len(row) != len(schema):
            raise ProtocolBlocked([f"{name}: unexpected number of fields in line {line}"])
        try:
            value = _row_date(name, row)
        except ValueError:
            raise ProtocolBlocked([f"{name}: invalid or out-of-range research timestamp in line {line}"]) from None
        if previous is not None and value <= previous:
            raise ProtocolBlocked([f"{name}: row timestamps must be strictly increasing (line {line})"])
        if first is None:
            first = value
        previous = value
        count += 1
    if first is None and not name.endswith("_dividends.csv"):
        raise ProtocolBlocked([f"{name}: no research rows"])
    return {
        "rows": count,
        "first": first.isoformat() if first is not None else None,
        "last": previous.isoformat() if previous is not None else None,
    }


def inspect_exports(directory: Path) -> dict[str, Any]:
    """Check all five files and bind only the exact bytes that passed the date checks."""
    if directory.is_symlink() or not directory.is_dir():
        raise ProtocolBlocked(["expected a dedicated research export directory"])
    sources = {name: directory / name for name in SCHEMAS}
    missing = [name for name, path in sources.items() if path.is_symlink() or not path.is_file()]
    if missing:
        raise ProtocolBlocked(["missing or non-regular research export: " + ", ".join(missing)])

    checks = {}
    payloads = {}
    for name, path in sources.items():
        try:
            payload = path.read_bytes()
            checks[name] = _inspect(name, payload)
        except (OSError, UnicodeError, csv.Error):
            raise ProtocolBlocked([f"{name}: unreadable research CSV"]) from None
        payloads[name] = payload
    hashes = {name: sha(payload) for name, payload in payloads.items()}
    return {
        "holdout_start": HOLDOUT_START.isoformat(),
        "files": {name: {**check, "sha256": hashes[name]} for name, check in checks.items()},
        "bindings": {
            "separate_research_only_manifest_digest": digest_map(hashes),
            "point_in_time_corporate_actions_and_fx_digests": digest_map(
                {name: digest for name, digest in hashes.items() if not name.endswith("_15m.csv")}
            ),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="custodian-designated research-only export directory")
    args = parser.parse_args()
    try:
        report = inspect_exports(args.directory)
    except ProtocolBlocked as error:
        parser.exit(2, f"T-008 preflight: {error}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
