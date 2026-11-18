"""T-008 research operator entry point. Run from a clean, reviewed checkout with an external freeze receipt."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import tempfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from .attempt_journal import AttemptJournal, Budget
from .calendars import expected_bar_starts, trading_days
from .config import load_config
from .cost_schedule import load_schedule
from .domain import Market
from .experiment_registry import ExperimentRecord, Variant, write_record
from .fx_rates import read_fx_file
from .holdout import HOLDOUT_START
from .intraday_data import CsvImportSpec, import_csv
from .intraday_fetch import read_dividends
from .intraday_strategies import IntradayStrategy
from .research_protocol import ProtocolBlocked, assert_startable, load_protocol
from .validation_costs import amount
from .validation_replay import UNIVERSE, Replay, ReplayResult, RoleMarket
from .validation_runner import ResearchRun, persist
from .walkforward import NY, QUARTER, RoleWindow, Session, research_windows

PROTOCOL = Path("docs/research/T-008-protocol.json")
PLAN = Path("docs/research/EXP-T008-001.md")
ECONOMICS = Path("config/t008_economics.json")
COST_TABLE = Path("config/cost_schedule.json")
RISK = Path("config/default.toml")


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def digest_map(values: dict[str, str]) -> str:
    return sha(json.dumps(values, sort_keys=True, separators=(",", ":")).encode())


def sessions(start: date, end: date) -> list[Session]:
    result = []
    for day in trading_days(start, end):
        bars = expected_bar_starts(day)
        result.append(Session(bars[0], bars[-1] + QUARTER))
    return result


def annual_cost() -> Decimal:
    policy = json.loads(ECONOMICS.read_bytes())
    if (
        policy["schema"] != "T008-economics/1"
        or policy["historical_fetch_eur"] != 0
        or policy["funding"] != "usd_shortfall_only_reuse_proceeds"
        or policy["day_count"] != "actual_calendar_days_actual_year"
        or policy["currency"] != "EUR"
        or policy["annual_strategy_increment_eur"] != 0
    ):
        raise ValueError("unsupported economics policy")
    return amount(policy["annual_operations_eur"]) + amount(policy["annual_data_eur"])


def verified_checkout() -> str:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise ProtocolBlocked(["execution checkout must be clean"])
    return head


def load_role(data: Path, w: RoleWindow) -> RoleMarket:
    """Decode prices, corporate actions and FX only for the requested role in the attested snapshot."""
    bars = {}
    actions = {}
    with tempfile.TemporaryDirectory() as tmp:
        part = Path(tmp)
        for source in sorted(data.iterdir()):
            is_price = source.name.endswith("_15m.csv")
            is_fx = source.name == "EURUSD_ecb.csv"
            start = w.start - timedelta(days=5) if is_fx else w.start
            with source.open(newline="") as src, (part / source.name).open("w", newline="") as dst:
                rows = csv.reader(src)
                writer = csv.writer(dst)
                header = next(rows)
                if header[0] != ("timestamp" if is_price else "date" if is_fx else "ex_date"):
                    raise ValueError("unexpected research CSV schema")
                writer.writerow(header)
                for row in rows:
                    if is_price:
                        moment = datetime.fromisoformat(row[0])
                        if moment.tzinfo is None or moment + QUARTER >= HOLDOUT_START:
                            raise ProtocolBlocked(["price outside attested research interval"])
                        day = moment.astimezone(NY).date()
                    else:
                        day = date.fromisoformat(row[0])
                        if day >= HOLDOUT_START.date():
                            raise ProtocolBlocked(["action or rate outside attested research interval"])
                    if start <= day < w.end:
                        writer.writerow(row)
        for symbol in UNIVERSE:
            source_bars = import_csv(
                CsvImportSpec(
                    str(part / (symbol + "_15m.csv")),
                    symbol,
                    Market.ETF,
                    "US",
                    "alpaca_sip_raw",
                    "UTC",
                    available_until=HOLDOUT_START,
                )
            )
            if source_bars.manifest.quality["rejected"] or source_bars.manifest.quality["holdout_rows_withheld"]:
                raise ProtocolBlocked(["invalid research-only price inventory"])
            bars[symbol] = source_bars.bars
            actions[symbol] = read_dividends(part / (symbol + "_dividends.csv"), HOLDOUT_START)
        fx = read_fx_file(part / "EURUSD_ecb.csv", HOLDOUT_START - timedelta(microseconds=1))
    return RoleMarket.prepare(w, bars, actions, fx)


def journal_identity(root: Path, experiment: str) -> dict[str, list[int]]:
    identity = {}
    for folder in (root / experiment, root / experiment / "attempts", root / experiment / "budget"):
        if folder.is_symlink() or not folder.is_dir():
            raise ProtocolBlocked(["preprovisioned journal directory missing or replaced"])
        stat = folder.stat()
        identity[str(folder)] = [stat.st_dev, stat.st_ino]
    return identity


def run_research(deployment: Path) -> dict[str, Any]:
    protocol = load_protocol(PROTOCOL)
    # Block the repository draft even when the research volume or deployment config is unmounted.
    if protocol["state"] != "frozen" or not protocol["freeze"]["binding_receipt"]:
        raise ProtocolBlocked(["reviewed protocol and external freeze receipt required"])
    config = json.loads(deployment.read_bytes())
    bindings = config["bindings"]
    assert_startable(PROTOCOL, PLAN, bindings, HOLDOUT_START)
    receipt_path = PROTOCOL.parent / Path(protocol["freeze"]["binding_receipt"])
    receipt = json.loads(receipt_path.read_bytes())
    attestation_bytes = Path(config["custody_attestation"]).read_bytes()
    if receipt["bindings"]["custodian_research_only_storage_and_exposure_attestation_digest"] != sha(attestation_bytes):
        raise ProtocolBlocked(["custody attestation differs"])
    custody = json.loads(attestation_bytes)
    if (
        custody["files"] != config["files"]
        or custody["physical_research_only"] is not True
        or custody["historical_exposure_resolved"] is not True
        or custody["material_exceptions_resolved"] is not True
        or not custody["provider_exceptions_evidence"]
        or any(not Path(path).is_absolute() or Path(path).resolve() != Path(path) for path in custody["files"].values())
    ):
        raise ProtocolBlocked(["unattested source path, exposure or corporate actions"])
    journal_root = Path(config["journal_root"]).resolve(strict=True)
    experiment = protocol["freeze"]["experiment_id"]
    if custody["research_journal_identity"] != journal_identity(journal_root, experiment) or receipt["bindings"].get(
        "research_journal_path_digest"
    ) != sha(str(journal_root).encode()):
        raise ProtocolBlocked(["research journal differs from the bound authority"])
    head = verified_checkout()
    calendar = sessions(
        date.fromisoformat(protocol["folds"][0]["train"][0]), date.fromisoformat(protocol["folds"][-1]["test"][1])
    )
    windows = research_windows(protocol, calendar)
    inventory = Path(config["prior_inventory"]).read_bytes()
    prior = json.loads(inventory)["unique_same_family_strategy_slot_ids"]
    if (
        not isinstance(prior, list)
        or any(not isinstance(x, str) or not x.strip() for x in prior)
        or len(set(prior)) != len(prior)
    ):
        raise ProtocolBlocked(["invalid prior attempt inventory"])
    names = [*(s + "_15m.csv" for s in UNIVERSE), *(s + "_dividends.csv" for s in UNIVERSE), "EURUSD_ecb.csv"]
    # The custodian attests physical exclusion of the suffix before this point. Verify those same bytes
    # in an isolated snapshot before parsing. Never hash the old full files to create a receipt.
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp)
        hashes = {}
        for name in names:
            payload = Path(config["files"][name]).read_bytes()
            hashes[name] = sha(payload)
            (data / name).write_bytes(payload)
        inputs = {
            "verified_integrated_code_sha": head,
            "dependency_lock_digest": sha(Path("requirements-dev.txt").read_bytes()),
            "risk_config_digest": sha(RISK.read_bytes()),
            "separate_research_only_manifest_digest": digest_map(hashes),
            "calendar_digest": digest_map({s.opens.isoformat(): s.closes.isoformat() for s in calendar}),
            "point_in_time_corporate_actions_and_fx_digests": digest_map(
                {k: v for k, v in hashes.items() if not k.endswith("_15m.csv")}
            ),
            "complete_cost_schedule_digest": digest_map({str(p): sha(p.read_bytes()) for p in (ECONOMICS, COST_TABLE)}),
            "prior_experiment_inventory_digest": sha(inventory),
        }
        assert_startable(PROTOCOL, PLAN, inputs, HOLDOUT_START)
        cfg = load_config(RISK)
        schedule = load_schedule(COST_TABLE)
        active: RoleMarket | None = None
        annual = annual_cost()

        def evaluate(
            w: RoleWindow, scenario: str, strategy: IntradayStrategy | None, weights: dict[str, float] | None
        ) -> ReplayResult:
            nonlocal active
            if active is None or active.window is not w:
                active = load_role(data, w)
            return Replay(active, cfg, schedule, annual, scenario, strategy, weights).run()

        journal = AttemptJournal(journal_root, experiment, Budget.from_protocol(protocol))
        output = journal.root / "research-output"
        runner = ResearchRun(protocol, journal, output, evaluate, annual, len(prior), receipt["digest"], head)
        persist(output / "research-start.json", {"receipt_digest": receipt["digest"], "integrated_sha": head})
        failure: Exception | None = None
        try:
            result = runner.execute(windows)
        except Exception as error:
            failure = error
            result = {
                "passed": False,
                "nominee": None,
                "decision": "indeterminate",
                "reason": type(error).__name__,
                "receipt_digest": receipt["digest"],
                "integrated_sha": head,
                "attempts": journal.summary(),
                "planned_research_slots": protocol["budget"]["max_accounted_research_variant_slots"],
            }
            persist(output / "research-incomplete.json", result)
        slots = journal.status()
        variants = []
        for key, slot in sorted(slots.items()):
            if slot["kind"] != "strategy":
                continue
            evidence = {"status": slot["status"]}
            report_path = output / (key + ".json")
            if report_path.exists():
                evidence["result_sha256"] = sha(report_path.read_bytes())
            variants.append(
                Variant(key, slot["slot"], evidence, rejected=slot["status"] in ("rejected", "failed", "aborted"))
            )
        if not variants and failure is not None:
            raise failure

        record = ExperimentRecord(
            experiment,
            "T-008",
            "Codex",
            "frozen T-008 runner",
            datetime.now(UTC).isoformat(),
            head,
            list(hashes.values()),
            [protocol["inference"]["seed"]],
            {"folds": protocol["folds"]},
            {"binding": inputs["complete_cost_schedule_digest"]},
            variants,
            {"unadjusted": result.get("unadjusted"), "adjusted": result.get("adjusted")},
            "indeterminate" if failure else "central_review_pending" if result["passed"] else "cash",
            result.get("reason", "pre-registered research gates"),
            False,
            False,
        )
        write_record(record, output / "experiments")
        if failure is not None:
            raise failure
        persist(output / "research-verdict.json", result)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the frozen T-008 research batch in its bound journal")
    parser.add_argument("deployment", type=Path, help="custodian-provided research input manifest")
    args = parser.parse_args()
    try:
        result = run_research(args.deployment)
    except ProtocolBlocked as error:
        parser.exit(2, str(error) + "\n")
    print(json.dumps({"passed": result["passed"], "nominee": result["nominee"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
