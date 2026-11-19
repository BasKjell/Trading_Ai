from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .alpaca_check import AlpacaCheckError, checker_from_env
from .backtest import BacktestEngine
from .config import AppConfig, load_config
from .dashboard import serve_dashboard
from .data import bars_hash, synthetic_fixture
from .data_requests import (
    AlpacaDatasetDownloader,
    DataRequestError,
    DataRequestSpec,
    DataWorker,
)
from .domain import Market
from .holdout import HOLDOUT_START, HoldoutError, research_limit
from .intraday_data import CsvImportSpec, ImportedDataset, import_csv, synthetic_intraday
from .intraday_engine import InstrumentSpec, IntradayEngine, buy_and_hold, compute_metrics, instruments_from_config
from .intraday_fetch import dividends_digest, output_paths, read_dividends
from .intraday_report import default_workers, run_scenarios, strategies_from_config, write_intraday_report
from .ollama_lab import OllamaClient, OllamaStrategySearch
from .paper import PaperSession
from .reporting import write_report
from .research_db import ResearchDB
from .strategy_dsl import SEARCHABLE_FAMILIES
from .strategy_lab import StrategyLab, load_builtin_specs


def run_backtest(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    bars = synthetic_fixture(config.seed, args.days)
    result = BacktestEngine(config, args.cost_multiplier).run(bars, bars_hash(bars))
    output = write_report(result, args.output)
    Path(output, "config_snapshot.toml").write_text(Path(args.config).read_text(encoding="utf-8"), encoding="utf-8")
    Path(output, "dataset_manifest.json").write_text(
        json.dumps(
            {
                "source": "synthetic_fixture",
                "retrieved_at": None,
                "bars": len(bars),
                "hash_sha256": bars_hash(bars),
                "purpose": "offline smoke test only",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "report": str(output),
                "metrics": result.metrics,
                "benchmark": result.benchmark,
                "warnings": result.warnings,
            },
            indent=2,
        )
    )
    return 0


def _load_intraday(
    config_path: str,
    data_file: str | None,
    synthetic_days: int | None,
    symbol: str | None = None,
) -> tuple[AppConfig, dict[str, InstrumentSpec], dict[str, ImportedDataset], dict[str, object]]:
    config = load_config(config_path)
    instruments = instruments_from_config(config)
    settings = config.raw["intraday"]
    symbol = symbol or str(settings["data_symbol"])
    if symbol not in instruments:
        raise SystemExit(f"no [instruments.{symbol}] in the config")
    spec = instruments[symbol]
    raw = config.raw["instruments"][symbol]
    # The research path never reads the holdout; the boundary is fixed in code (localtrader/holdout.py).
    holdout: dict[str, object] = {"start": HOLDOUT_START.isoformat(), "excluded_bars": 0, "used": False}
    if synthetic_days:
        data = synthetic_intraday(symbol, synthetic_days, config.seed, 15000.0, spec.session)
        return config, instruments, {symbol: data}, holdout
    cutoff = research_limit()
    data = import_csv(
        CsvImportSpec(
            data_file or str(raw.get("data_file", settings["data_file"])),
            symbol,
            spec.market,
            spec.venue,
            str(raw.get("data_source", settings["data_source"])),
            str(raw.get("data_timezone", settings["data_timezone"])),
            int(settings["signal_interval_minutes"]),
            available_until=cutoff,
        ),
        spec.session,
    )
    holdout["excluded_bars"] = data.manifest.quality.get("holdout_rows_withheld", 0)
    if raw.get("corporate_actions") == "dividends_file":
        # written by fetch-intraday next to the raw price file; missing file = fail closed
        dividends_path = output_paths(data_file or str(raw["data_file"]))["dividends"]
        if not dividends_path.exists():
            raise SystemExit(f"{dividends_path} missing: run fetch-intraday --symbol {symbol}")
        # withheld rows are skipped on their ex-date before any other field is parsed or validated
        dividends = read_dividends(dividends_path, cutoff)
        # bind the corporate actions actually used into the dataset version (changes are visible)
        digest = dividends_digest(dividends)
        manifest = replace(
            data.manifest,
            data_version=hashlib.sha256(f"{data.manifest.data_version}|{digest}".encode()).hexdigest()[:16],
            quality={
                **data.manifest.quality,
                "corporate_actions": {"source": "dividends file", "dividends": len(dividends), "digest": digest},
            },
        )
        data = replace(data, dividends=dividends, manifest=manifest)
    if not data.bars:
        raise SystemExit("no bars left for the research period")
    return config, instruments, {symbol: data}, holdout


def _strategy_names(value: str) -> list[str]:
    names = [item.strip() for item in value.split(",") if item.strip()]
    if not names:
        raise SystemExit("at least one strategy is required")
    return names


def run_intraday_backtest(args: argparse.Namespace) -> int:
    config, instruments, datasets, holdout = _load_intraday(args.config, args.data, args.synthetic_days, args.symbol)
    strategies = strategies_from_config(config, _strategy_names(args.strategies))
    engine = IntradayEngine(
        config,
        instruments,
        strategies,
        cost_multiplier=args.cost_multiplier,
        spread_multiplier=args.spread_multiplier,
        extra_delay_bars=args.delay_bars,
    )
    result = engine.run(datasets)
    benchmark = buy_and_hold(datasets, instruments, config, args.cost_multiplier)
    profiles = {
        symbol: {
            "fee_bps": instruments[symbol].costs.fee_bps,
            "spread_bps": instruments[symbol].costs.spread_bps,
            "slippage_bps": instruments[symbol].costs.slippage_bps,
            "min_fee": instruments[symbol].costs.min_fee,
        }
        for symbol in datasets
    }
    output = write_intraday_report(
        result,
        args.output,
        benchmark,
        {"config_digest": config.digest, "holdout": holdout, "cost_profiles": profiles},  # what-if view (T-007)
    )
    metrics = compute_metrics(result)
    print(
        json.dumps(
            {"report": str(output), "metrics": metrics, "benchmark": benchmark, "holdout": holdout},
            indent=2,
            default=str,
        )
    )
    return 0


def run_intraday_stress(args: argparse.Namespace) -> int:
    config, instruments, datasets, holdout = _load_intraday(args.config, args.data, args.synthetic_days, args.symbol)
    workers = args.workers if args.workers is not None else default_workers()
    if workers < 1:
        raise SystemExit("--workers must be at least 1")
    output = run_scenarios(config, instruments, _strategy_names(args.strategies), datasets, workers)
    output["holdout"] = holdout
    path = Path(args.output)
    path.mkdir(parents=True, exist_ok=True)
    (path / "scenarios.json").write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(json.dumps(output, indent=2, default=str))
    return 0


def run_measure_spreads(args: argparse.Namespace) -> int:
    import csv

    from .cost_schedule import measure_spreads, sample_bar_opens
    from .data_requests import AlpacaDatasetDownloader

    with Path(args.bars).open(newline="", encoding="utf-8") as handle:
        starts = [datetime.fromisoformat(row["timestamp"]) for row in csv.DictReader(handle)]

    load_config(args.config)  # rejects a configuration that tries to move the holdout boundary
    try:
        before = research_limit(datetime.fromisoformat(args.before) if args.before else None)
    except HoldoutError as error:
        raise SystemExit(f"--before not allowed: {error}") from None
    moments = sample_bar_opens(starts, args.samples, before)
    downloader = AlpacaDatasetDownloader.from_env(args.env)
    result = measure_spreads(downloader._request_page, args.symbol, moments)
    result.update({"bars_file": args.bars, "sampled_before": before.isoformat(), "seed": 20261106})
    output = Path(args.output or f"data/spreads_{args.symbol}.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


def run_fetch_intraday(args: argparse.Namespace) -> int:
    from .data_requests import AlpacaDatasetDownloader
    from .intraday_fetch import default_end, fetch_intraday_csv

    load_config(args.config)  # also rejects a configuration that tries to move the holdout boundary
    cutoff = research_limit()
    try:
        end = research_limit(datetime.fromisoformat(args.end)) if args.end else min(default_end(), cutoff)
    except HoldoutError as error:
        raise SystemExit(f"--end not allowed: {error}") from None
    downloader = AlpacaDatasetDownloader.from_env(args.env)
    summary = fetch_intraday_csv(
        downloader,
        args.symbol,
        datetime.fromisoformat(args.start),
        end,
        args.output or f"data/{args.symbol}_15m.csv",
        args.feed,
        progress=lambda message: print(message, file=sys.stderr),
        available_until=cutoff,
    )
    print(json.dumps(summary, indent=2))
    return 0


def run_fetch_fx(args: argparse.Namespace) -> int:
    from datetime import date

    from .fx_rates import ECB_URL, fetch_ecb_csv, parse_ecb_csv, research_series, write_fx_file

    load_config(args.config)  # rejects a configuration that tries to move the holdout boundary
    try:  # fixed boundary from localtrader/holdout.py; --available-until may only be earlier
        cutoff = research_limit(datetime.fromisoformat(args.available_until) if args.available_until else None)
    except (HoldoutError, ValueError) as error:
        raise SystemExit(f"--available-until not allowed: {error}") from None
    start = date.fromisoformat(args.start)
    last_allowed = cutoff.date() - timedelta(days=1)
    end = date.fromisoformat(args.end) if args.end else last_allowed
    if end > last_allowed:
        raise SystemExit(f"--end {end} reaches into the holdout ({cutoff.isoformat()}); not allowed")
    # rows published at/after the cutoff are dropped before validation, so the manifest's quality report
    # cannot describe the holdout either; research_series is a second, independent guard
    series, report = parse_ecb_csv(fetch_ecb_csv(start, end), available_before=cutoff)
    series = research_series(series, cutoff)
    if not series:
        raise SystemExit("ECB returned no usable rates")
    manifest = write_fx_file(series, args.output, report, ECB_URL)
    print(json.dumps(manifest, indent=2))
    return 0


def experiments(args: argparse.Namespace) -> int:
    from .experiment_registry import summary

    print(json.dumps(summary(args.directory), indent=2, ensure_ascii=False))
    return 0


def run_paper(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    session = PaperSession(config, args.state, args.audit)
    now = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
    accepted = session.process_demo_event("offline-demo-001", "SPY", Market.EQUITY, 500.0, 485.0, now)
    duplicate = session.process_demo_event("offline-demo-001", "SPY", Market.EQUITY, 500.0, 485.0, now)
    print(
        json.dumps(
            {
                "mode": "offline-paper-only",
                "first_event_accepted": accepted,
                "duplicate_accepted": duplicate,
                "state": str(session.state_path),
                "audit": str(session.audit_path),
            },
            indent=2,
        )
    )
    return 0


def _feeds(values: list[str]) -> dict[str, Path]:
    feeds = {}
    for item in values:
        symbol, _, path = item.partition("=")
        if not symbol or not path:
            raise SystemExit(f"--feed expects SYMBOL=PATH, got {item!r}")
        feeds[symbol.strip()] = Path(path.strip())
    if not feeds:
        raise SystemExit("at least one --feed SYMBOL=PATH is required")
    return feeds


def run_runtime(args: argparse.Namespace) -> int:
    """T-006: one entry point for all modes. Live is locked; paper/shadow replay the shared engine."""
    import time as _time

    from .alpaca_check import load_local_env
    from .runtime import LiveGate, LiveLocked, Mode, PaperRuntime, code_sha

    config = load_config(args.config)
    mode = Mode(args.mode)
    if mode == Mode.LIVE:
        env = load_local_env(args.env_file) if Path(args.env_file).exists() else {}
        try:
            LiveGate(config, env, Path(args.release) if args.release else None, code_sha()).check()
        except LiveLocked as error:
            print(json.dumps({"mode": "live", "locked": True, "reasons": error.reasons}, indent=2))
            return 2
        return 2  # unreachable: the gate always raises in this build
    instruments = instruments_from_config(config)
    strategies = strategies_from_config(config, _strategy_names(args.strategies))
    feeds = _feeds(args.feed)
    if mode == Mode.BACKTEST:
        datasets = {}
        for symbol, path in feeds.items():
            spec = instruments[symbol]
            datasets[symbol] = import_csv(
                CsvImportSpec(
                    str(path), symbol, spec.market, spec.venue, "file", "UTC", available_until=research_limit()
                ),
                spec.session,
            )
        result = IntradayEngine(config, instruments, strategies).run(datasets)
        print(json.dumps({"mode": "backtest", "metrics": compute_metrics(result)}, indent=2, default=str))
        return 0
    if not args.trade_from:
        raise SystemExit("--trade-from is required for paper and shadow")
    try:
        runtime = PaperRuntime(
            config,
            instruments,
            strategies,
            args.state_dir or f"state/runtime-{mode.value}",
            mode,
            feeds,
            datetime.fromisoformat(args.trade_from),
            max_bar_age=timedelta(minutes=args.max_bar_age_minutes),
            feed_delay=timedelta(minutes=args.feed_delay_minutes),
        )
    except ValueError as error:  # e.g. a feed delay below ADR 0006
        raise SystemExit(f"run --mode {mode.value}: {error}") from error
    try:
        if args.halt:
            runtime.halt(args.halt)
            print(json.dumps({"halted": args.halt}))
            return 0
        if args.resume:
            runtime.resume(args.resume)
            print(json.dumps({"resumed_by": args.resume}))
            return 0
        if args.resolve_gap:
            symbol, _, start = args.resolve_gap.partition("=")
            try:
                runtime.resolve_gap(
                    symbol.strip(), datetime.fromisoformat(start.strip()), args.operator or "", args.reason or ""
                )
            except ValueError as error:
                raise SystemExit(f"--resolve-gap: {error}") from error
            print(json.dumps({"gap_resolved": args.resolve_gap, "operator": args.operator}))
            return 0
        while True:
            print(json.dumps(runtime.tick(), default=str), flush=True)
            if args.once:
                return 0
            _time.sleep(args.interval_seconds)
    except KeyboardInterrupt:
        return 0
    finally:
        runtime.close()


def compare_feeds(args: argparse.Namespace) -> int:
    """T-012: data-only comparison of two bar feeds (no strategy, no returns)."""
    from .feed_compare import compare_files, summary

    results = []
    for symbol in args.symbols.split(","):
        symbol = symbol.strip()
        results.append(
            (
                symbol,
                compare_files(
                    Path(args.reference_dir) / f"{symbol}_15m.csv", Path(args.candidate_dir) / f"{symbol}_15m.csv"
                ),
            )
        )
    print(summary(results))
    if args.json:
        print(json.dumps(dict(results), indent=2, default=str))
    return 0


def kill_switch(args: argparse.Namespace) -> int:
    session = PaperSession(load_config(args.config), args.state, args.audit)
    session.set_kill_switch(args.action == "on")
    print(f"kill_switch={session.risk.kill_switch}")
    return 0


def lab_builtins(args: argparse.Namespace) -> int:
    lab = StrategyLab(load_config(args.config), args.registry)
    results = [lab.evaluate(spec, days=args.days).to_dict() for spec in load_builtin_specs(args.specs)]
    print(json.dumps({"evaluations": results, "research_champion": lab.champion()}, indent=2))
    return 0


def lab_search(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    lab = StrategyLab(config, args.registry)
    client = OllamaClient(args.ollama_url, args.timeout)
    worker = None
    if args.auto_data:
        data_config = config.raw.get("data", {}).get("alpaca", {})
        worker = DataWorker(
            lab.db,
            AlpacaDatasetDownloader.from_env(
                args.env_file,
                root=args.data_root,
                requests_per_minute=int(data_config.get("requests_per_minute", 180)),
                max_attempts=int(data_config.get("max_attempts", 6)),
            ),
        )
    try:
        families = tuple(item.strip() for item in args.families.split(",") if item.strip())
        unknown = set(families) - set(SEARCHABLE_FAMILIES)
        if not families or unknown:
            raise ValueError(f"invalid families: {sorted(unknown)}; allowed={list(SEARCHABLE_FAMILIES)}")
        results = OllamaStrategySearch(lab, client, worker).run(
            args.model,
            args.iterations,
            args.days,
            auto_data=args.auto_data,
            lookback_days=args.lookback_days,
            feed=args.feed,
            progress=lambda message: print(message, file=sys.stderr, flush=True),
            continuous=args.continuous,
            pause_seconds=args.pause_seconds,
            families=families,
        )
    except KeyboardInterrupt:
        print(
            "\nSuche kontrolliert beendet; abgeschlossene Iterationen bleiben gespeichert.", file=sys.stderr, flush=True
        )
        print(
            json.dumps(
                {
                    "model": args.model,
                    "status": "stopped_by_user",
                    "research_champion": lab.champion(),
                    "paper_promotion": "manual-only",
                },
                indent=2,
            )
        )
        return 130
    except ValueError as error:
        print(json.dumps({"status": "rejected", "error": str(error)}, indent=2))
        return 2
    print(
        json.dumps(
            {
                "model": args.model,
                "evaluated": [item.to_dict() for item in results],
                "research_champion": lab.champion(),
                "paper_promotion": "manual-only",
            },
            indent=2,
        )
    )
    return 0 if args.continuous or len(results) == args.iterations else 2


def _data_worker(args: argparse.Namespace) -> DataWorker:
    config = load_config(args.config)
    raw = config.raw.get("data", {}).get("alpaca", {})
    return DataWorker(
        ResearchDB(Path(args.registry) / "research.db"),
        AlpacaDatasetDownloader.from_env(
            args.env_file,
            root=args.data_root,
            requests_per_minute=int(raw.get("requests_per_minute", 180)),
            max_attempts=int(raw.get("max_attempts", 6)),
        ),
    )


def data_request(args: argparse.Namespace) -> int:
    try:
        request = DataRequestSpec(
            provider="alpaca",
            endpoint_type=args.endpoint_type,
            symbols=tuple(item.strip().upper() for item in args.symbols.split(",") if item.strip()),
            timeframe=args.timeframe,
            start=datetime.fromisoformat(args.start.replace("Z", "+00:00")),
            end=datetime.fromisoformat(args.end.replace("Z", "+00:00")),
            feed=None if args.endpoint_type == "crypto" else args.feed,
            purpose=args.purpose,
            strategy_name=args.strategy_name,
        )
        request_id = _data_worker(args).enqueue(request)
    except (DataRequestError, ValueError) as error:
        print(json.dumps({"status": "rejected", "error": str(error)}, indent=2))
        return 2
    print(json.dumps({"status": "pending", "request_id": request_id}, indent=2))
    return 0


def data_worker(args: argparse.Namespace) -> int:
    worker = _data_worker(args)

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        result = (
            worker.process(args.request_id, progress=progress)
            if args.request_id
            else worker.process_next(progress=progress)
        )
    except DataRequestError as error:
        print(json.dumps({"status": "cooldown_or_failed", "error": str(error)}, indent=2))
        return 2
    if result is None:
        print(json.dumps({"status": "idle", "message": "no eligible request; cooldowns remain queued"}, indent=2))
        return 0
    print(json.dumps({"status": "complete", "dataset": result}, indent=2))
    return 0


def lab_status(args: argparse.Namespace) -> int:
    lab = StrategyLab(load_config(args.config), args.registry)
    client = OllamaClient(args.ollama_url, args.timeout)
    print(
        json.dumps(
            {
                "ollama_models": client.models(),
                "research_champion": lab.champion(),
                "history": str(lab.history_path),
                "database": str(lab.db.path),
                "database_counts": lab.db.status(),
                "paper_promotion": "manual-only",
            },
            indent=2,
        )
    )
    return 0


def dashboard(args: argparse.Namespace) -> int:
    try:
        serve_dashboard(args.registry, args.host, args.port, args.config)
    except (OSError, ValueError, FileNotFoundError) as error:
        print(json.dumps({"status": "failed", "error": str(error)}, indent=2))
        return 2
    return 0


def alpaca_check(args: argparse.Namespace) -> int:
    try:
        result = checker_from_env(args.env_file).run()
    except AlpacaCheckError as error:
        print(json.dumps({"status": "failed_safe", "error": str(error), "orders_sent": False}, indent=2))
        return 2
    print(json.dumps(result, indent=2))
    account = result["paper_account"]
    data_available = (
        result["stock_data"]["historical"]["iex"]["available"] or result["stock_data"]["historical"]["sip"]["available"]
    )
    account_ok = account.get("authenticated", account.get("status") == "not_applicable")
    return 0 if account_ok and data_available else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Offline research and paper-trading platform")
    parser.add_argument("--config", default="config/default.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    backtest = sub.add_parser("backtest")
    backtest.add_argument("--days", type=int, default=1000)
    backtest.add_argument("--cost-multiplier", type=float, default=1.0)
    backtest.add_argument("--output", default="reports/baseline")
    backtest.set_defaults(func=run_backtest)
    for name, func, default_out in (
        ("intraday-backtest", run_intraday_backtest, "reports/intraday"),
        ("intraday-stress", run_intraday_stress, "reports/intraday_stress"),
    ):
        command = sub.add_parser(name, help="15-minute backtest of the base strategies")
        command.add_argument("--strategies", default="intraday_trend,mean_reversion")
        command.add_argument("--data", default=None, help="CSV file; default from [intraday] in the config")
        command.add_argument("--synthetic-days", type=int, default=None, help="use labelled synthetic data")
        command.add_argument("--output", default=default_out)
        command.add_argument("--symbol", default=None, help="instrument from [instruments.*]; default [intraday]")
        if name == "intraday-backtest":
            command.add_argument("--cost-multiplier", type=float, default=1.0)
            command.add_argument("--spread-multiplier", type=float, default=1.0)
            command.add_argument("--delay-bars", type=int, default=0)
        else:
            command.add_argument(
                "--workers",
                type=int,
                default=None,
                help="processes for the independent scenarios; default: available CPUs, 1 = serial",
            )
        command.set_defaults(func=func)
    spreads = sub.add_parser("measure-spreads", help="sample quoted SIP spreads at bar opens (research period only)")
    spreads.add_argument("--symbol", required=True)
    spreads.add_argument("--bars", required=True, help="raw 15-minute CSV whose bar opens are sampled")
    spreads.add_argument("--samples", type=int, default=400)
    spreads.add_argument("--before", default=None, help="optional earlier limit; never after [holdout] start")
    spreads.add_argument("--output", default=None, help="default: data/spreads_<SYMBOL>.json")
    spreads.add_argument("--env", default=".env")
    spreads.set_defaults(func=run_measure_spreads)
    fetch = sub.add_parser("fetch-intraday", help="download 15-minute ETF bars from Alpaca into data/")
    fetch.add_argument("--symbol", required=True)
    fetch.add_argument("--start", default="2016-01-01T00:00:00+00:00")
    fetch.add_argument("--end", default=None, help="default: latest allowed time")
    fetch.add_argument("--feed", default="sip", choices=["sip", "iex"])
    fetch.add_argument(
        "--output", default=None, help="raw file; default data/<SYMBOL>_15m.csv (+ _adjusted, _dividends)"
    )
    fetch.add_argument("--env", default=".env", help="local .env with the Alpaca paper key")
    fetch.set_defaults(func=run_fetch_intraday)
    fx = sub.add_parser("fetch-fx", help="download ECB EUR/USD reference rates into data/")
    fx.add_argument("--start", default="2015-12-01")
    fx.add_argument(
        "--end",
        default=None,
        help="default: last day before the effective cutoff (holdout start or --available-until); never later",
    )
    fx.add_argument("--output", default="data/EURUSD_ecb.csv")
    fx.add_argument(
        "--available-until", default=None, help="optional earlier limit; never after the fixed holdout start"
    )
    fx.set_defaults(func=run_fetch_fx)
    register = sub.add_parser("experiments", help="show the shared experiment register and total variants tested")
    register.add_argument("--directory", default="experiments")
    register.set_defaults(func=experiments)
    paper = sub.add_parser("paper-demo")
    paper.add_argument("--state", default="state/paper_state.json")
    paper.add_argument("--audit", default="state/audit.jsonl")
    paper.set_defaults(func=run_paper)
    runtime = sub.add_parser("run", help="15-minute runtime: backtest | paper | shadow | live (live is locked)")
    runtime.add_argument("--mode", required=True, choices=["backtest", "paper", "shadow", "live"])
    runtime.add_argument("--feed", action="append", default=[], help="SYMBOL=PATH of a completed-bar CSV feed")
    runtime.add_argument("--strategies", default="intraday_trend")
    runtime.add_argument("--state-dir", default=None, help="default state/runtime-<mode>")
    runtime.add_argument("--trade-from", default=None, help="aware ISO time; earlier bars are warm-up only")
    runtime.add_argument("--once", action="store_true", help="one tick, then exit")
    runtime.add_argument("--feed-delay-minutes", type=float, default=15.0, help="ADR 0006: delayed SIP, at least 15")
    runtime.add_argument("--max-bar-age-minutes", type=float, default=35.0)
    runtime.add_argument("--interval-seconds", type=float, default=60.0)
    runtime.add_argument("--halt", default=None, help="latch a halt with this reason")
    runtime.add_argument("--resume", default=None, help="operator name; clears a halt")
    runtime.add_argument(
        "--resolve-gap", default=None, help="SYMBOL=START: declare a missing slot as never delivered (audited)"
    )
    runtime.add_argument("--operator", default=None, help="operator name for --resolve-gap")
    runtime.add_argument("--reason", default=None, help="reason for --resolve-gap")
    runtime.add_argument("--release", default=None, help="live release file (live stays locked in this build)")
    runtime.add_argument("--env-file", default=".env")
    runtime.set_defaults(func=run_runtime)
    feeds = sub.add_parser("compare-feeds", help="compare two 15-minute feeds (e.g. SIP vs IEX), data only")
    feeds.add_argument("--reference-dir", default="data")
    feeds.add_argument("--candidate-dir", default="data/iex")
    feeds.add_argument("--symbols", default="SPY,QQQ")
    feeds.add_argument("--json", action="store_true")
    feeds.set_defaults(func=compare_feeds)
    kill = sub.add_parser("kill-switch")
    kill.add_argument("action", choices=["on", "off"])
    kill.add_argument("--state", default="state/paper_state.json")
    kill.add_argument("--audit", default="state/audit.jsonl")
    kill.set_defaults(func=kill_switch)
    builtins = sub.add_parser("evaluate-strategies", help="evaluate all built-in and shadow strategy families")
    builtins.add_argument("--specs", default="strategy_specs")
    builtins.add_argument("--registry", default="research_registry")
    builtins.add_argument("--days", type=int, default=900)
    builtins.set_defaults(func=lab_builtins)
    search = sub.add_parser("ollama-search", help="let a local Ollama model propose validated challengers")
    search.add_argument("--model", default="qwen3:8b")
    search.add_argument("--iterations", type=int, default=3)
    search.add_argument("--continuous", action="store_true", help="search indefinitely until Ctrl+C")
    search.add_argument("--pause-seconds", type=float, default=0.0, help="optional pause between continuous iterations")
    search.add_argument(
        "--families",
        default=",".join(SEARCHABLE_FAMILIES),
        help="comma-separated price strategy families; default covers all P0/P1 families",
    )
    search.add_argument("--days", type=int, default=900)
    search.add_argument("--registry", default="research_registry")
    search.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    search.add_argument("--timeout", type=int, default=900)
    search.add_argument("--auto-data", action="store_true", help="download approved Alpaca history for every proposal")
    search.add_argument("--lookback-days", type=int, default=900)
    search.add_argument("--feed", choices=["sip", "iex"], default="sip")
    search.add_argument("--env-file", default=".env")
    search.add_argument("--data-root", default="data")
    search.set_defaults(func=lab_search)
    status = sub.add_parser("lab-status")
    status.add_argument("--registry", default="research_registry")
    status.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    status.add_argument("--timeout", type=int, default=30)
    status.set_defaults(func=lab_status)
    dashboard_parser = sub.add_parser("dashboard", help="serve the local read-only research dashboard")
    dashboard_parser.add_argument("--registry", default="research_registry")
    dashboard_parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1", "localhost", "::1"])
    dashboard_parser.add_argument("--port", type=int, default=8501)
    dashboard_parser.set_defaults(func=dashboard)
    alpaca = sub.add_parser("alpaca-check", help="verify paper authentication and read-only market-data entitlements")
    alpaca.add_argument("--env-file", default=".env")
    alpaca.set_defaults(func=alpaca_check)
    request = sub.add_parser("data-request", help="enqueue an allowlisted historical Alpaca download")
    request.add_argument("--endpoint-type", choices=["stocks", "crypto"], required=True)
    request.add_argument("--symbols", required=True, help="comma-separated approved symbols")
    request.add_argument("--timeframe", choices=["1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"], required=True)
    request.add_argument("--start", required=True, help="timezone-aware ISO timestamp")
    request.add_argument("--end", required=True, help="timezone-aware ISO timestamp, at least 15 minutes old")
    request.add_argument("--feed", choices=["sip", "iex"], default="sip")
    request.add_argument("--purpose", default="research:manual")
    request.add_argument("--strategy-name", default="manual_research")
    request.add_argument("--registry", default="research_registry")
    request.add_argument("--env-file", default=".env")
    request.add_argument("--data-root", default="data")
    request.set_defaults(func=data_request)
    worker = sub.add_parser("data-worker", help="process one pending/cooldown-complete data request")
    worker.add_argument("--request-id")
    worker.add_argument("--registry", default="research_registry")
    worker.add_argument("--env-file", default=".env")
    worker.add_argument("--data-root", default="data")
    worker.set_defaults(func=data_worker)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
