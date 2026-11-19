from __future__ import annotations

from pathlib import Path

import pytest

from localtrader.research_protocol import ProtocolBlocked
from localtrader.t008 import digest_map, sha
from localtrader.t008_preflight import inspect_exports


@pytest.fixture
def exports(tmp_path: Path) -> Path:
    for symbol in ("SPY", "QQQ"):
        (tmp_path / f"{symbol}_15m.csv").write_text(
            "timestamp,open,high,low,close,volume\n2025-10-31T19:45:00+00:00,10,11,9,10,1000\n"
        )
        (tmp_path / f"{symbol}_dividends.csv").write_text(
            "ex_date,pay_date,amount,source\n2025-10-31,2025-11-10,1,synthetic\n"
        )
    (tmp_path / "EURUSD_ecb.csv").write_text(
        "date,usd_per_eur,available_at\n2025-10-31,1.2,2025-10-31T15:00:00+00:00\n"
    )
    return tmp_path


def test_preflight_binds_the_exact_research_exports(exports: Path) -> None:
    report = inspect_exports(exports)
    hashes = {p.name: sha(p.read_bytes()) for p in exports.iterdir()}
    assert report["bindings"] == {
        "separate_research_only_manifest_digest": digest_map(hashes),
        "point_in_time_corporate_actions_and_fx_digests": digest_map(
            {name: digest for name, digest in hashes.items() if not name.endswith("_15m.csv")}
        ),
    }
    assert all(check["rows"] == 1 for check in report["files"].values())


@pytest.mark.parametrize(
    ("name", "row"),
    [
        ("SPY_15m.csv", "2025-10-31T23:45:00+00:00,private,private,private,private,private\n"),
        ("QQQ_15m.csv", "2025-11-03T15:00:00+00:00,private,private,private,private,private\n"),
        ("SPY_dividends.csv", "2025-11-03,private,private,private\n"),
        ("QQQ_dividends.csv", "2025-11-03,private,private,private\n"),
        ("EURUSD_ecb.csv", "2025-11-03,private,private\n"),
    ],
)
def test_preflight_refuses_holdout_rows_without_reporting_values(exports: Path, name: str, row: str) -> None:
    with (exports / name).open("a") as handle:
        handle.write(row)
    with pytest.raises(ProtocolBlocked, match="out-of-range") as exc:
        inspect_exports(exports)
    assert "private" not in str(exc.value)


def test_preflight_rejects_future_fx_availability(exports: Path) -> None:
    (exports / "EURUSD_ecb.csv").write_text("date,usd_per_eur,available_at\n2025-10-31,1.2,2025-11-01T00:00:00+00:00\n")
    with pytest.raises(ProtocolBlocked, match="out-of-range"):
        inspect_exports(exports)


def test_preflight_rejects_symlinked_exports(exports: Path) -> None:
    path = exports / "SPY_15m.csv"
    target = path.rename(exports / "elsewhere.csv")
    path.symlink_to(target)
    with pytest.raises(ProtocolBlocked, match="non-regular research export: SPY_15m.csv"):
        inspect_exports(exports)
