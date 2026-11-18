from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
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
from .intraday_data import CsvImportSpec, ImportedDataset, import_csv, synthetic_intraday
from .intraday_engine import InstrumentSpec, IntradayEngine, buy_and_hold, compute_metrics, instruments_from_config
from .intraday_report import run_scenarios, strategies_from_config, write_intraday_report
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
    config_path: str, data_file: str | None, synthetic_days: int | None
) -> tuple[AppConfig, dict[str, InstrumentSpec], dict[str, ImportedDataset]]:

    config = load_config(config_path)
    instruments = instruments_from_config(config)
    settings = config.raw["intraday"]
    symbol = str(settings["data_symbol"])
    spec = instruments[symbol]
    if synthetic_days:
        data = synthetic_intraday(symbol, synthetic_days, config.seed, 15000.0, spec.session)
    else:
        data = import_csv(
            CsvImportSpec(
                data_file or str(settings["data_file"]),
                symbol,
                spec.market,
                spec.venue,
                str(settings["data_source"]),
                str(settings["data_timezone"]),
                int(settings["signal_interval_minutes"]),
            ),
            spec.session,
        )
    return config, instruments, {symbol: data}


def _strategy_names(value: str) -> list[str]:
    names = [item.strip() for item in value.split(",") if item.strip()]
    if not names:
        raise SystemExit("at least one strategy is required")
    return names


def run_intraday_backtest(args: argparse.Namespace) -> int:
    config, instruments, datasets = _load_intraday(args.config, args.data, args.synthetic_days)
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
    output = write_intraday_report(result, args.output, benchmark, {"config_digest": config.digest})
    metrics = compute_metrics(result)
    print(json.dumps({"report": str(output), "metrics": metrics, "benchmark": benchmark}, indent=2, default=str))
    return 0


def run_intraday_stress(args: argparse.Namespace) -> int:
    config, instruments, datasets = _load_intraday(args.config, args.data, args.synthetic_days)
    output = run_scenarios(config, instruments, _strategy_names(args.strategies), datasets)
    path = Path(args.output)
    path.mkdir(parents=True, exist_ok=True)
    (path / "scenarios.json").write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    print(json.dumps(output, indent=2, default=str))
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
        if name == "intraday-backtest":
            command.add_argument("--cost-multiplier", type=float, default=1.0)
            command.add_argument("--spread-multiplier", type=float, default=1.0)
            command.add_argument("--delay-bars", type=int, default=0)
        command.set_defaults(func=func)
    register = sub.add_parser("experiments", help="show the shared experiment register and total variants tested")
    register.add_argument("--directory", default="experiments")
    register.set_defaults(func=experiments)
    paper = sub.add_parser("paper-demo")
    paper.add_argument("--state", default="state/paper_state.json")
    paper.add_argument("--audit", default="state/audit.jsonl")
    paper.set_defaults(func=run_paper)
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
