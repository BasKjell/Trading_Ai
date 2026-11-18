"""One-shot custodian entry point. Execute only on the separate custodian account with a signed release."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .attempt_journal import AttemptJournal, Budget
from .config import load_config
from .cost_schedule import load_schedule
from .data_requests import AlpacaDatasetDownloader, DataRequestSpec
from .domain import Market
from .fx_rates import FxSeries, fetch_ecb_csv, parse_ecb_csv
from .holdout import HOLDOUT_START
from .intraday_fetch import (
    crosscheck_dividends,
    derive_dividends,
    dividend_gaps,
    dividends_from_actions,
    regular_session_bars,
)
from .intraday_strategies import build_strategy
from .model_registry import ModelRegistry
from .research_protocol import ProtocolBlocked, assert_startable, load_protocol
from .t008 import COST_TABLE, ECONOMICS, PLAN, PROTOCOL, RISK, annual_cost, digest_map, sessions, sha, verified_checkout
from .validation_replay import UNIVERSE, Replay, RoleMarket
from .validation_runner import persist
from .validation_stats import Compared, bootstrap, nominate
from .walkforward import QUARTER, RoleWindow


def _fresh(protocol: dict[str, Any], private: Path, secret: Path) -> tuple[RoleMarket, dict[str, str]]:
    central = protocol["central"]
    start = datetime.fromisoformat(central["start_inclusive"])
    end = datetime.fromisoformat(central["end_exclusive"])
    if start != HOLDOUT_START or end <= start:
        raise ValueError("invalid central period")
    calendar = sessions(start.date(), end.date())
    b = protocol["boundary"]
    embargo = b["embargo_first_complete_scheduled_sessions"]
    purge = b["purge_last_complete_scheduled_sessions"]
    core = calendar[embargo:-purge]
    if len(core) < b["minimum_scheduled_scored_sessions"]["central"]:
        raise ValueError("insufficient central sessions")
    w = RoleWindow(
        "F3",
        "central",
        start.date(),
        end.date(),
        tuple(calendar[:embargo]),
        tuple(core),
        tuple(calendar[-purge:]),
        b["entry_blackout_before_scored_end_sessions"],
        b["minimum_observed_completed_15m_bars_per_symbol"],
        b["minimum_observed_full_completed_hourly_bars_per_symbol"],
        end,
        b["max_entry_signal_to_flat_sessions_inclusive"],
        b["max_entry_to_flat_sessions_inclusive"],
    )
    client = AlpacaDatasetDownloader.from_env(secret, root=private, max_attempts=1)
    bars = {}
    actions = {}
    hashes = {}

    for symbol in UNIVERSE:
        series = {}
        for adjustment in ("raw", "all"):
            request = DataRequestSpec(
                provider="alpaca",
                endpoint_type="stocks",
                symbols=(symbol,),
                timeframe="15Min",
                start=start,
                end=end - QUARTER,
                feed="sip",
                purpose="research:T-008 central",
                strategy_name="T-008",
                adjustment=adjustment,
            )
            rows, _, normalized = client.download(request)
            hashes[f"{symbol}-{adjustment}"] = sha(normalized.read_bytes())
            series[adjustment] = regular_session_bars(rows)
        # Historical file retrieval time is not historical signal time. Same completed-bar
        # convention as research CSV; execution disallows equality with the decision timestamp.
        bars[symbol] = [replace(row, market=Market.ETF, timeframe="15m", received_at=row.end) for row in series["raw"]]
        raw = client.corporate_actions(symbol, start.date(), end.date() - timedelta(days=1))
        hashes[symbol + "-actions"] = sha(json.dumps(raw, sort_keys=True).encode())
        if any(not row.get("payable_date") for row in raw.get("cash_dividends", [])):
            raise ValueError("unresolved central dividend payment date")
        actions[symbol] = dividends_from_actions(raw, symbol, start.date(), end.date() - timedelta(days=1), end)
        if crosscheck_dividends(actions[symbol], derive_dividends(series["raw"], series["all"])) or dividend_gaps(
            {d: v for d, (_, v) in actions[symbol].items()}, start.date(), end.date() - timedelta(days=1)
        ):
            raise ValueError("unresolved central corporate action")
    text = fetch_ecb_csv(start.date(), end.date() - timedelta(days=1))
    hashes["fx"] = sha(text.encode())
    rates, _ = parse_ecb_csv(text, available_before=end)
    return RoleMarket.prepare(w, bars, actions, FxSeries(rates)), hashes


def once(journal: AttemptJournal, release_digest: str, evaluate: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    key = journal.central_slot_id
    journal.reserve(key, "central", {"release_sha256": release_digest})
    journal.transition(key, "started")
    try:
        verdict = evaluate()
        persist(journal.root / "central-verdict.json", verdict)
    except Exception:
        journal.transition(key, "failed", "central evaluation failed")
        raise RuntimeError("central attempt consumed; evaluation failed") from None
    journal.transition(key, "completed")
    return verdict


def run_central(policy_path: Path, release_path: Path, signature: Path) -> dict[str, Any]:
    protocol = load_protocol(PROTOCOL)
    if protocol["state"] != "frozen" or not protocol["freeze"]["binding_receipt"]:
        raise ProtocolBlocked(["development freeze required"])
    receipt_path = PROTOCOL.parent / protocol["freeze"]["binding_receipt"]
    # The external receipt is the trust anchor for the signing key and journal authority.
    for anchor in (receipt_path.parent, receipt_path):
        info = anchor.stat()
        if anchor.is_symlink() or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ProtocolBlocked(["custodian freeze receipt must have restricted write access"])
    receipt = json.loads(receipt_path.read_bytes())
    raw_policy = policy_path.read_bytes()
    if sha(raw_policy) != receipt["bindings"]["central_single_journal_and_release_procedure_digest"]:
        raise ProtocolBlocked(["unbound custodian policy"])
    policy = json.loads(raw_policy)
    if policy["custodian_uid"] != os.geteuid():
        raise ProtocolBlocked(["wrong custodian account"])
    release_bytes = release_path.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        private = Path(tmp)
        (private / "public.pem").write_text(policy["public_key_pem"])
        (private / "signature").write_bytes(signature.read_bytes())
        verified = subprocess.run(
            [
                "openssl",
                "dgst",
                "-sha256",
                "-verify",
                str(private / "public.pem"),
                "-signature",
                str(private / "signature"),
            ],
            input=release_bytes,
            capture_output=True,
            check=False,
        )
        if verified.returncode:
            raise ProtocolBlocked(["invalid custodian signature"])
    release = json.loads(release_bytes)
    if (
        release["approved"] is not True
        or release["receipt_digest"] != receipt["digest"]
        or release["period"] != [protocol["central"]["start_inclusive"], protocol["central"]["end_exclusive"]]
    ):
        raise ProtocolBlocked(["release does not authorize this frozen batch"])
    head = verified_checkout()
    if release["integrated_sha"] != head:
        raise ProtocolBlocked(["executing code differs from release"])
    bindings = dict(receipt["bindings"])
    bindings.update(
        verified_integrated_code_sha=head,
        dependency_lock_digest=sha(Path("requirements-dev.txt").read_bytes()),
        risk_config_digest=sha(RISK.read_bytes()),
        complete_cost_schedule_digest=digest_map({str(p): sha(p.read_bytes()) for p in (ECONOMICS, COST_TABLE)}),
    )
    assert_startable(PROTOCOL, PLAN, bindings, HOLDOUT_START)
    research_bytes = Path(policy["research_verdict"]).read_bytes()
    research = json.loads(research_bytes)
    nominee = release["candidate"]
    if (
        sha(research_bytes) != release["research_verdict_sha256"]
        or research["receipt_digest"] != receipt["digest"]
        or research["integrated_sha"] != head
        or research["passed"] is not True
        or not research["gates"]
        or not all(v is True for v in research["gates"].values())
        or nominate(research["winners"]) != nominee
        or research["nominee"] != nominee
        or research["weights"]["F3/" + nominee] != release["benchmark_weights"]
    ):
        raise ProtocolBlocked(["research verdict or nomination does not authorize central evaluation"])
    exp = protocol["freeze"]["experiment_id"]
    root = Path(policy["journal_root"]).resolve(strict=True)
    # Opening the journal class can create a new empty authority. Require the exact preprovisioned
    # directory identities before construction. Custodian account must be isolated from researchers.
    for path in (root / exp, root / exp / "attempts", root / exp / "budget"):
        stat = path.stat()
        if (
            not path.is_dir()
            or path.is_symlink()
            or stat.st_uid != os.geteuid()
            or stat.st_mode & 0o077
            or [stat.st_dev, stat.st_ino] != policy["journal_identity"][str(path)]
        ):
            raise ProtocolBlocked(["unavailable or divergent central journal"])
    slot = policy["canonical_slot"]
    if slot != release["slot"] or protocol["budget"]["max_central_candidate_bundles"] != 1:
        raise ProtocolBlocked(["invalid central slot"])
    journal = AttemptJournal(root, exp, Budget.from_protocol(protocol), central_slot_id=slot)
    candidate = next(c for c in protocol["candidates"] if c["id"] == nominee)
    schedule = load_schedule(COST_TABLE)
    cfg = load_config(RISK)

    def evaluate() -> dict[str, Any]:
        with tempfile.TemporaryDirectory(dir=journal.root) as tmp:
            market, hashes = _fresh(protocol, Path(tmp), Path(policy["credentials"]))
            method = build_strategy(candidate["family"], candidate["parameters"])
            s = Replay(market, cfg, schedule, annual_cost(), "base", method).run().sleeve
            cash = Replay(market, cfg, schedule, annual_cost(), "base", passive={}).run().sleeve
            matched = (
                Replay(market, cfg, schedule, annual_cost(), "base", passive=release["benchmark_weights"]).run().sleeve
            )
        path = Compared(s, cash, matched)
        stats = protocol["inference"]
        bounds = bootstrap(
            [path], block=stats["block_sessions"], replicates=stats["replicates"], seed=stats["seed"], alphas=[0.025]
        )[0]
        criteria = protocol["central"]["criteria"]
        net = s.equity[-1] / s.initial - 1
        excess_cash = s.equity[-1] / cash.equity[-1] - 1
        excess_matched = s.equity[-1] / matched.equity[-1] - 1
        passed = (
            min(net, excess_cash, excess_matched, bounds.cash, bounds.matched) > 0
            and not s.breach
            and s.round_trips >= criteria["min_round_trips"]
            and len(s.entry_months) >= criteria["min_active_entry_months"]
            and s.max_drawdown < criteria["max_drawdown_lt"]
        )
        persist(journal.root / "central-input-digests.json", hashes)
        return {
            "passed": passed,
            "candidate": nominee,
            "round_trips": s.round_trips,
            "active_months": len(s.entry_months),
            "net_return": net,
            "excess_vs_cash": excess_cash,
            "excess_vs_matched": excess_matched,
            "max_drawdown": s.max_drawdown,
            "bounds_975": asdict(bounds),
        }

    result = once(journal, sha(release_bytes), evaluate)
    registration = {
        "model_id": f"{nominee}-F3-{head[:12]}",
        "code_commit": head,
        "dependency_lock_digest": bindings["dependency_lock_digest"],
        "data_versions": json.loads((journal.root / "central-input-digests.json").read_bytes()),
        "criteria_version": receipt["digest"],
        "parameters": candidate["parameters"],
        "periods": release["period"],
        "seeds": [protocol["inference"]["seed"]],
        "costs": bindings["complete_cost_schedule_digest"],
        "fold_reports": [release["research_verdict_sha256"]],
        "audit_refs": [slot, sha(release_bytes)],
        "activation_decision": {
            "eligible_mode": "shadow" if result["passed"] else None,
            "gates_passed": result["passed"],
        },
    }
    persist(journal.root / "registry-record.json", registration)
    ModelRegistry(policy["registry_root"]).register(registration)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Custodian-only signed one-shot T-008 evaluation")
    for name in ("policy", "release", "signature"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    try:
        result = run_central(args.policy, args.release, args.signature)
    except Exception:
        parser.exit(2, "Central evaluation unavailable. Check the private custodian journal.\n")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
