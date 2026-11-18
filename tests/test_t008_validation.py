from __future__ import annotations

import copy
import json
import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal as D
from statistics import fmean

import pytest

from localtrader.attempt_journal import AttemptJournal, Budget, JournalError
from localtrader.calendars import expected_bar_starts
from localtrader.config import load_config
from localtrader.cost_schedule import ScheduleError, load_schedule
from localtrader.domain import Bar, Market
from localtrader.fx_rates import FxRate, FxSeries, StaleRate, available_at
from localtrader.intraday_strategies import build_strategy
from localtrader.research_protocol import ProtocolBlocked, load_protocol
from localtrader.t008 import COST_TABLE, PROTOCOL, annual_cost, run_research, sessions
from localtrader.t008_central import once, run_central
from localtrader.validation_costs import CashBook, Rates, running_cost
from localtrader.validation_replay import UNIVERSE, Replay, ReplayResult, RoleMarket
from localtrader.validation_runner import ResearchRun, chain
from localtrader.validation_stats import Compared, Sleeve, bootstrap, nominate, select_candidate
from localtrader.walkforward import QUARTER, RoleWindow


@pytest.fixture
def protocol():
    return load_protocol(PROTOCOL)


@pytest.fixture
def schedule():
    return load_schedule(COST_TABLE)


def test_actual_funding_costs_and_valuation(schedule):
    day = date(2024, 3, 1)
    now = available_at(day)
    quotes = FxSeries([FxRate(day, 1.2, now), FxRate(date(2024, 3, 4), 1.0, available_at(date(2024, 3, 4)))])
    usd = CashBook(D(1000), quotes)
    r = Rates.for_fill(schedule, "QQQ", day, "buy", "base")
    usd.trade_cash(D(-120), now, r)
    assert (usd.eur, usd.usd) == (D(899), D(0))
    usd.trade_cash(D(121), now, r)
    usd.trade_cash(D(-60), now, r)
    assert len(usd.transfers) == 1
    later = available_at(date(2024, 3, 4))
    assert usd.value(D(0), later) == D(960)
    usd.withdraw_usd(later, r)
    assert usd.eur == D("959.39") and len(usd.transfers) == 2
    with pytest.raises(StaleRate):
        usd.value(D(120), later + timedelta(days=6))
    assert annual_cost() == D(1320)
    assert running_cost(date(2024, 1, 1), date(2025, 1, 1), annual_cost()) == D(1320)


def test_dated_fees_and_stress_are_applied_once(schedule):
    day = date(2024, 3, 1)

    def estimate(scenario):
        return Rates.for_fill(schedule, "SPY", day, "sell", scenario).fill(D(100), D(-100))

    base, spread, stress = map(estimate, ["base", "spread3", "cost2"])
    assert spread.spread_usd == 3 * base.spread_usd
    assert spread.slippage_usd == base.slippage_usd
    assert stress.slippage_usd == 2 * base.slippage_usd and stress.spread_usd == 2 * base.spread_usd
    assert base.fees_usd == base.execution_usd * 100 * D("0.0000278") + D("0.0166") + D("0.0046")
    assert Rates.for_fill(schedule, "SPY", date(2025, 10, 2), "sell", "base").sec == 0
    incomplete = copy.deepcopy(schedule)
    incomplete["entries"] = [x for x in incomplete["entries"] if x["component"] != "fx_conversion"]
    with pytest.raises(ScheduleError):
        Rates.for_fill(incomplete, "SPY", day, "sell", "base")


def sleeve(start, levels):
    return Sleeve(
        tuple(start + timedelta(days=i) for i in range(len(levels))),
        tuple(levels),
        100.0,
        0.01,
        20,
        frozenset({(start.year, i) for i in range(1, 13)}),
        False,
    )


def compared(start, levels):
    s = sleeve(start, levels)
    return Compared(
        s,
        replace(s, equity=(100.0,) * len(levels)),
        replace(s, equity=tuple(99.0 + 0.25 * i for i in range(len(levels)))),
    )


def test_paired_bootstrap_matches_slow_non_crossing_reference():
    folds = [compared(date(year, 1, 1), [100, 102, 101, 104, 103, 108, 110]) for year in (2016, 2019, 2022)]
    estimates = bootstrap(folds, block=3, replicates=99, seed=41, alphas=[0.05, 0.025])
    rng = random.Random(41)
    means = [[], []]
    for _ in range(99):
        sampled = [[], []]
        for fold in folds:
            pair = fold.paired()
            indices = []
            while len(indices) < 7:
                ix = rng.randrange(7)
                indices.extend((ix + i) % 7 for i in range(3))
            for k in range(2):
                sampled[k].extend(pair[k][i] for i in indices[:7])
        for k in range(2):
            means[k].append(fmean(sampled[k]))
    for alpha, bound in zip([0.05, 0.025], estimates, strict=True):
        for k, actual in enumerate((bound.cash, bound.matched)):
            observed = fmean(x for fold in folds for x in fold.paired()[k])
            assert actual == pytest.approx(2 * observed - sorted(means[k])[math.ceil((1 - alpha) * 99) - 1])
    with pytest.raises(ValueError):
        bootstrap(folds[::-1], block=3, replicates=1, seed=0, alphas=[0.05])


def test_selection_checks_risk_tie_and_no_replacement():
    run = compared(date(2016, 1, 1), [100, 105, 110])
    opt = {"min_trips": 10, "drawdown_limit": 0.08}
    assert select_candidate({"T16": run, "T12": run}, ["T12", "T16"], **opt) == "T12"
    assert (
        select_candidate({"T12": replace(run, strategy=replace(run.strategy, max_drawdown=0.08))}, ["T12"], **opt)
        is None
    )
    assert nominate(["T12", "T12", "T16"]) is None
    assert nominate(["T12", None, "T12"]) is None
    assert nominate(["T12", "T16", "T12"]) == "T12"


def market_data():
    cal = sessions(date(2024, 3, 1), date(2024, 4, 19))
    w = RoleWindow(
        "SYN",
        "train",
        cal[0].day,
        cal[-1].day + timedelta(days=1),
        tuple(cal[:6]),
        tuple(cal[6:-6]),
        tuple(cal[-6:]),
        6,
        33,
        50,
        datetime(2025, 11, 1, tzinfo=UTC),
        5,
        5,
    )
    inputs = {}
    for s in UNIVERSE:
        rows = []
        for d in cal:
            for t in expected_bar_starts(d.day):
                p = 100 + len(rows) * 0.03
                rows.append(
                    Bar(
                        s,
                        Market.ETF,
                        "15m",
                        t,
                        t + QUARTER,
                        p,
                        p + 0.02,
                        p - 0.02,
                        p + 0.01,
                        150000,
                        "synthetic",
                        t + QUARTER,
                    )
                )
        inputs[s] = rows
    fx = FxSeries([FxRate(d.day, 1.1, available_at(d.day)) for d in cal])
    actions = {s: {} for s in UNIVERSE}
    return w, inputs, actions, fx


def test_role_replay_full_hours_cash_dividends_and_no_terminal_liquidation(schedule, protocol):
    w, inputs, actions, fx = market_data()
    actions["SPY"] = {w.scored[5].day: (w.scored[-1].day, 1.0)}
    market = RoleMarket.prepare(w, inputs, actions, fx)
    cfg = load_config()
    candidate = protocol["candidates"][0]
    method = build_strategy(candidate["family"], candidate["parameters"])
    replay = Replay(market, cfg, schedule, annual_cost(), "base", method)
    r = replay.run()
    assert r.sleeve.round_trips > 0
    assert not replay.positions and not replay.pending
    cash = Replay(market, cfg, schedule, annual_cost(), "base", passive={}).run()
    assert cash.sleeve.equity[-1] == pytest.approx(
        10000 - float(running_cost(w.scored[0].day, w.scored[-1].day + timedelta(days=1), annual_cost()))
    )
    assert not cash.transfers
    bench = Replay(market, cfg, schedule, annual_cost(), "base", passive={"SPY": 1.0}).run()
    assert bench.sleeve.round_trips == 1 and len(bench.transfers) >= 2
    first = w.scored[-7].opens
    illiquid = {s: [replace(b, volume=0) if b.start >= first else b for b in rows] for s, rows in inputs.items()}
    with pytest.raises(ValueError, match="unclosed"):
        Replay(
            RoleMarket.prepare(w, illiquid, actions, fx), cfg, schedule, annual_cost(), "base", passive={"SPY": 1.0}
        ).run()
    with pytest.raises(ValueError, match="inventory"):
        RoleMarket.prepare(w, {**inputs, "SPY": inputs["SPY"][1:]}, actions, fx)


def test_frozen_draft_and_failed_central_attempt(tmp_path):
    with pytest.raises(ProtocolBlocked):
        run_research(tmp_path / "missing")
    with pytest.raises(ProtocolBlocked):
        run_central(*(tmp_path / x for x in ("policy", "release", "signature")))
    j = AttemptJournal(tmp_path, "synthetic", Budget(39, 195, 1))
    calls = []

    def fail():
        calls.append(j.status()[j.central_slot_id]["status"])
        raise ValueError("mock-central-payload")

    with pytest.raises(RuntimeError, match="consumed") as err:
        once(j, "a" * 64, fail)
    assert "mock-central-payload" not in str(err.value)
    assert calls == ["started"] and j.status()[j.central_slot_id]["status"] == "failed"
    with pytest.raises(JournalError):
        once(j, "a" * 64, fail)
    assert calls == ["started"]


def test_chained_drawdown_includes_the_cash_bridge():
    s = sleeve(date(2024, 1, 1), [110.0, 120.0])
    a = ReplayResult(s, {}, [100.0, 110.0, 120.0], [], {})
    b = replace(
        a, sleeve=replace(s, days=(date(2024, 7, 1), date(2024, 7, 2)), equity=(90.0, 110.0)), marks=[90.0, 110.0]
    )
    with pytest.raises(ValueError, match="cash bridge depleted"):
        chain([a, b], D(365))
    total, dd = chain([a, b], D(10))
    assert total < 0.32 and dd > 0.1


def test_synthetic_selection_lock_precedes_tests_and_all_slots_are_accounted(tmp_path, protocol):
    protocol["inference"].update(replicates=99, block_sessions=2)
    windows = []
    base = market_data()[0]
    for i in range(9):
        day = date(2016 + i, 1, 4)
        scored = tuple(sessions(day, day + timedelta(days=10))[:5])
        windows.append(replace(base, fold=f"F{i // 3 + 1}", role=("train", "validation", "test")[i % 3], scored=scored))
    j = AttemptJournal(tmp_path, "smoke", Budget.from_protocol(protocol))
    out = tmp_path / "result"

    def evaluate(w, scenario, strategy, weights):
        if w.role == "test":
            assert json.loads((out / "selection.json").read_bytes())["nominee"] == "M175"
        days = tuple(s.day for s in w.scored)
        path = tuple(100.0 + (i + 1 if strategy else 0.0) for i in range(len(days)))
        s = Sleeve(days, path, 100.0, 0.01, 20, frozenset((days[0].year, m) for m in range(1, 5)), False)
        return ReplayResult(s, {"QQQ": 0.12, "SPY": 0.13}, list(path), [], {})

    report = ResearchRun(protocol, j, out, evaluate, D(0), 144, "a" * 64, "b" * 40).execute(tuple(windows))
    assert report["passed"] and report["nominee"] == "M175"
    assert len(j.status()) == 234
    assert report["adjusted"] is None
    with pytest.raises(JournalError):
        ResearchRun(protocol, j, out, evaluate, D(0), 144, "a" * 64, "b" * 40).execute(tuple(windows))


def test_signed_custody_lifecycle(tmp_path, monkeypatch, protocol):
    import os
    import subprocess
    from pathlib import Path

    from localtrader import t008, t008_central
    from localtrader.research_protocol import make_receipt

    def put(name, value):
        file = tmp_path / name
        file.write_text(json.dumps(value))
        return file

    def openssl(*args):
        subprocess.run(["openssl", *map(str, args)], check=True, capture_output=True)

    key = tmp_path / "fixture.key"
    pub = tmp_path / "fixture.pub"
    openssl("genrsa", "-out", key, "2048")
    openssl("rsa", "-in", key, "-pubout", "-out", pub)
    sig = tmp_path / "sig"
    protocol["state"] = "frozen"
    protocol["freeze"]["binding_receipt"] = "receipt.json"
    frozen = put("protocol.json", protocol)
    monkeypatch.setattr(t008_central, "PROTOCOL", frozen)
    head = "b" * 40
    monkeypatch.setattr(t008_central, "verified_checkout", lambda: head)
    root = (tmp_path / "authority").resolve()
    j = AttemptJournal(root, protocol["freeze"]["experiment_id"], Budget.from_protocol(protocol))
    ids = {}
    for path in (j.root, j.root / "attempts", j.root / "budget"):
        path.chmod(0o700)
        ids[str(path)] = [path.stat().st_dev, path.stat().st_ino]
    policy = put(
        "policy",
        dict(
            custodian_uid=os.geteuid(),
            public_key_pem=pub.read_text(),
            registry_root=str(tmp_path / "registry"),
            journal_root=str(root),
            journal_identity=ids,
            research_verdict=str(tmp_path / "result"),
            canonical_slot=j.central_slot_id,
            credentials="unused",
        ),
    )
    bindings = dict.fromkeys(protocol["freeze"]["required"], "d" * 64)
    bindings.update(
        verified_integrated_code_sha=head,
        dependency_lock_digest=t008.sha(Path("requirements-dev.txt").read_bytes()),
        risk_config_digest=t008.sha(t008.RISK.read_bytes()),
        complete_cost_schedule_digest=t008.digest_map(
            {str(p): t008.sha(p.read_bytes()) for p in (t008.ECONOMICS, t008.COST_TABLE)}
        ),
        central_single_journal_and_release_procedure_digest=t008.sha(policy.read_bytes()),
    )
    receipt = make_receipt(frozen, t008.PLAN, bindings)
    put("receipt.json", receipt)
    weights = {"QQQ": 0.1, "SPY": 0.1}
    report = dict(
        passed=True,
        nominee="M175",
        winners=["M175"] * 3,
        gates={"confidence": True},
        weights={"F3/M175": weights},
        receipt_digest=receipt["digest"],
        integrated_sha=head,
    )
    result = put("result", report)
    release = dict(
        approved=True,
        candidate="M175",
        benchmark_weights=weights,
        integrated_sha=head,
        receipt_digest=receipt["digest"],
        period=[protocol["central"][k] for k in ("start_inclusive", "end_exclusive")],
        slot=j.central_slot_id,
        research_verdict_sha256=t008.sha(result.read_bytes()),
    )
    auth = put("release", release)
    reads = []

    def fresh(protocol, private, credentials):
        reads.append(j.status()[j.central_slot_id]["status"])
        assert private.parent == j.root
        return RoleMarket.prepare(*market_data()), {"synthetic": "f" * 64}

    monkeypatch.setattr(t008_central, "_fresh", fresh)
    openssl("dgst", "-sha256", "-sign", key, "-out", sig, auth)
    put("release", {**release, "candidate": "T16"})
    with pytest.raises(ProtocolBlocked, match="signature"):
        run_central(policy, auth, sig)
    put("release", release)
    result = put("result", {**report, "receipt_digest": "c" * 64})
    put("release", {**release, "research_verdict_sha256": t008.sha(result.read_bytes())})
    openssl("dgst", "-sha256", "-sign", key, "-out", sig, auth)
    with pytest.raises(ProtocolBlocked, match="verdict"):
        run_central(policy, auth, sig)
    put("result", report)
    put("release", release)
    openssl("dgst", "-sha256", "-sign", key, "-out", sig, auth)
    (j.root / "budget").rename(j.root / "saved-budget")
    with pytest.raises((OSError, ProtocolBlocked)):
        run_central(policy, auth, sig)
    assert not (j.root / "budget").exists() and not reads
    (j.root / "saved-budget").rename(j.root / "budget")
    verdict = run_central(policy, auth, sig)
    assert verdict["passed"] is False and reads == ["started"]
    assert j.status()[j.central_slot_id]["status"] == "completed"
    model = json.loads(next((tmp_path / "registry" / "models").glob("M175*.json")).read_bytes())
    assert model["activation_decision"] == {"gates_passed": False, "eligible_mode": None}
    with pytest.raises(JournalError):
        run_central(policy, auth, sig)
    assert reads == ["started"]


def test_research_operator_with_synthetic_files_and_cash_outcome(tmp_path, monkeypatch, protocol):
    from pathlib import Path

    from localtrader import t008
    from localtrader.intraday_fetch import write_csv, write_dividends
    from localtrader.research_protocol import make_receipt

    root = tmp_path.resolve()
    exp = protocol["freeze"]["experiment_id"]
    journal = AttemptJournal(root / "authority", exp, Budget.from_protocol(protocol))
    head = "b" * 40
    monkeypatch.setattr(t008, "verified_checkout", lambda: head)
    protocol["state"] = "frozen"
    protocol["freeze"]["binding_receipt"] = "receipt.json"
    frozen = root / "frozen.json"
    frozen.write_text(json.dumps(protocol))
    monkeypatch.setattr(t008, "PROTOCOL", frozen)
    # Short synthetic roles exercise real strategies, execution, loaders, ledger and registry in CI.
    # Eligibility, costs and public operator preflight use the reviewed configuration.
    windows = []
    bars = {symbol: [] for symbol in UNIVERSE}
    fx_days = set()
    for fold in protocol["folds"]:
        for role in ("train", "validation", "test"):
            first = date.fromisoformat(fold[role][0])
            cal = sessions(first, first + timedelta(days=21))[:6]
            last = cal[-1].day + timedelta(days=1)
            windows.append(
                RoleWindow(
                    fold["id"],
                    role,
                    first,
                    last,
                    tuple(cal[:3]),
                    tuple(cal[3:5]),
                    tuple(cal[5:]),
                    1,
                    33,
                    50,
                    t008.HOLDOUT_START,
                    5,
                    5,
                )
            )
            for session in cal:
                fx_days.add(session.day)
                for t in expected_bar_starts(session.day):
                    for sym in UNIVERSE:
                        bars[sym].append(
                            Bar(
                                sym,
                                Market.ETF,
                                "15m",
                                t,
                                t + QUARTER,
                                100,
                                101,
                                99,
                                100,
                                20000,
                                "synthetic",
                                t + QUARTER,
                            )
                        )
    monkeypatch.setattr(t008, "research_windows", lambda proto, calendar: tuple(windows))
    files = {}
    for sym, rows in bars.items():
        prices = root / (sym + "_15m.csv")
        write_csv(rows, prices)
        dividend = root / (sym + "_dividends.csv")
        write_dividends({}, dividend, "synthetic")
        files.update({p.name: str(p) for p in (prices, dividend)})
    fx = root / "EURUSD_ecb.csv"
    fx.write_text(
        "date,usd_per_eur,available_at\n" + "".join(f"{d},1.1,{available_at(d).isoformat()}\n" for d in sorted(fx_days))
    )
    files[fx.name] = str(fx)
    attestation = root / "attestation"
    attestation.write_text(
        json.dumps(
            dict(
                physical_research_only=True,
                historical_exposure_resolved=True,
                research_journal_identity=t008.journal_identity(journal.root.parent, exp),
                material_exceptions_resolved=True,
                provider_exceptions_evidence=["synthetic QA record"],
                files=files,
            )
        )
    )
    inventory = root / "inventory"
    inventory.write_text(json.dumps({"unique_same_family_strategy_slot_ids": []}))
    calendar = sessions(
        date.fromisoformat(protocol["folds"][0]["train"][0]), date.fromisoformat(protocol["folds"][-1]["test"][1])
    )
    hashes = {name: t008.sha(Path(path).read_bytes()) for name, path in files.items()}
    bindings = dict.fromkeys(protocol["freeze"]["required"], "f" * 64)
    bindings.update(
        verified_integrated_code_sha=head,
        dependency_lock_digest=t008.sha(Path("requirements-dev.txt").read_bytes()),
        risk_config_digest=t008.sha(t008.RISK.read_bytes()),
        prior_experiment_inventory_digest=t008.sha(inventory.read_bytes()),
        calendar_digest=t008.digest_map({x.opens.isoformat(): x.closes.isoformat() for x in calendar}),
        complete_cost_schedule_digest=t008.digest_map(
            {str(p): t008.sha(p.read_bytes()) for p in (t008.ECONOMICS, t008.COST_TABLE)}
        ),
        separate_research_only_manifest_digest=t008.digest_map(hashes),
        point_in_time_corporate_actions_and_fx_digests=t008.digest_map(
            {k: v for k, v in hashes.items() if not k.endswith("_15m.csv")}
        ),
        custodian_research_only_storage_and_exposure_attestation_digest=t008.sha(attestation.read_bytes()),
        research_journal_path_digest=t008.sha(str(journal.root.parent).encode()),
    )
    receipt = make_receipt(frozen, t008.PLAN, bindings)
    (root / "receipt.json").write_text(json.dumps(receipt))
    deployment = root / "deploy"
    config = dict(
        bindings=bindings,
        files=files,
        prior_inventory=str(inventory),
        custody_attestation=str(attestation),
        journal_root=str(journal.root.parent),
    )
    deployment.write_text(json.dumps(config))
    report = run_research(deployment)
    assert report["passed"] is False and report["winners"] == [None] * 3
    assert report["receipt_digest"] == receipt["digest"] and report["integrated_sha"] == head
    assert len(report["attempts"]["unstarted"]) == 90 and len(journal.status()) == 144
    record = json.loads((journal.root / "research-output" / "experiments" / (exp + ".json")).read_bytes())
    assert record["decision"] == "cash" and len(record["variants"]) == 24 and not record["holdout_used"]


def test_central_provider_adapter_uses_raw_prices_and_etf_classification(tmp_path, monkeypatch, protocol):
    from localtrader import t008_central

    start = t008_central.HOLDOUT_START
    end = start + timedelta(days=55)
    protocol["central"]["end_exclusive"] = end.isoformat()
    protocol["boundary"]["minimum_scheduled_scored_sessions"]["central"] = 15
    cal = sessions(start.date(), end.date())
    calls = []

    class Provider:
        def download(self, request):
            calls.append(request)
            symbol = request.symbols[0]
            # The upstream stock endpoint classifies index ETFs as equity. Adjusted series differs.
            p = 100.0 if request.adjustment == "raw" else 70.0
            rows = [
                Bar(symbol, Market.EQUITY, "15Min", t, t + QUARTER, p, p, p, p, 1000.0, "synthetic", end + QUARTER)
                for session in cal
                for t in expected_bar_starts(session.day)
            ]
            raw = tmp_path / (symbol + request.adjustment)
            raw.write_text(str(p))
            return rows, {}, raw

        def corporate_actions(self, symbol, first, last):
            return {}

    def client(secret, *, root, max_attempts):
        assert root == tmp_path and max_attempts == 1
        return Provider()

    monkeypatch.setattr(t008_central.AlpacaDatasetDownloader, "from_env", client)
    from localtrader.fx_rates import ECB_SERIES

    def fake_fx(first, last):
        assert (first, last) == (start.date(), end.date() - timedelta(days=1))
        return "KEY,TIME_PERIOD,OBS_VALUE\n" + "".join(f"{ECB_SERIES},{s.day},1.1\n" for s in cal)

    monkeypatch.setattr(t008_central, "fetch_ecb_csv", fake_fx)
    market, digests = t008_central._fresh(protocol, tmp_path, tmp_path / "unused")
    assert len(calls) == 4 and all(r.start == start and r.end == end - QUARTER for r in calls)
    assert set(digests) == {"fx", "QQQ-raw", "QQQ-all", "SPY-raw", "SPY-all", "QQQ-actions", "SPY-actions"}
    for bars in market.bars.values():
        assert all(b.market == Market.ETF and b.close == 100 and b.received_at == b.end for b in bars.values())
