from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
import json
import time

from .data import available_data_catalog
from .strategy_dsl import SEARCHABLE_FAMILIES, StrategySpec, StrategySpecError, parse_strategy_spec, strategy_json_schema
from .strategy_lab import CandidateEvaluation, StrategyLab
from .data_requests import DataWorker, load_datasets, requests_for_strategy


class OllamaError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class OllamaProposal:
    spec: StrategySpec
    model: str
    prompt_eval_count: int | None
    eval_count: int | None
    total_duration_ns: int | None


class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", timeout_seconds: int = 900) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise OllamaError("only a loopback Ollama endpoint is allowed")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(self, endpoint: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            f"{self.base_url}{endpoint}", data=data,
            headers={"Content-Type": "application/json"}, method="POST" if data else "GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise OllamaError(f"local Ollama request failed: {error}") from error

    def models(self) -> list[str]:
        response = self._request("/api/tags")
        return [str(item["name"]) for item in response.get("models", [])]

    def propose(
        self, model: str, champion: dict[str, Any] | None, iteration: int,
        downloadable_only: bool = False,
        feedback: list[dict[str, Any]] | None = None,
        family: str | None = None,
    ) -> OllamaProposal:
        available = self.models()
        if model not in available:
            raise OllamaError(f"model {model!r} is not installed; available={available}")
        schema = strategy_json_schema()
        champion_context = json.dumps(champion, sort_keys=True) if champion else "null"
        system = (
            "You are a local quantitative research assistant. Return exactly one strategy as structured JSON. "
            "You never place orders, never request credentials, never generate executable code, never use shorts, "
            "leverage, future data or event data. Your proposal will be rejected unless it conforms to the schema. "
            "Choose symbols only from the supplied local data catalog. multi_asset_trend may combine asset classes. "
            "Every strategy must include an ATR indicator and a valid stop. stop_expression is safe reverse Polish "
            "notation, for example [\"close\",3.0,\"atr20\",\"*\",\"-\"]. Allowed math operators are "
            "+, -, *, /, min and max; stop_mode is fixed or trailing. "
            "Choose one timeframe from 5Min, 15Min, 30Min, 1Hour or 1Day; avoid 1Min unless its extra noise and "
            "download volume are clearly justified. Prefer robust parameter ranges and few explainable rules over historical curve fitting."
            " A renamed strategy with otherwise identical behavior is a duplicate and will be rejected."
        )
        catalog = available_data_catalog()
        if downloadable_only:
            catalog = {symbol: metadata for symbol, metadata in catalog.items() if metadata["market"] != "fx"}
        family_instruction = family or "one of " + ", ".join(SEARCHABLE_FAMILIES)
        user = (
            f"Iteration {iteration}. Propose a new long/cash challenger for exactly {family_instruction}. The local data catalog is "
            f"{json.dumps(catalog, sort_keys=True)}. Improve robustness after 1x/2x/3x costs and keep drawdown below 15%. "
            f"The current research champion is: {champion_context}. Do not copy its exact name. "
            "A candidate passes only with positive median return, positive median Sharpe, positive median return at 3x costs, "
            "at least five median trades and at least 55% positive walk-forward folds. "
            f"Recent research outcomes to learn from and not duplicate are: {json.dumps(feedback or [], sort_keys=True)}. "
            "Change actual behavior such as family, timeframe, universe, indicator types/windows, rules or stop design; changing only name or rationale is not novel. "
            f"Use this exact JSON schema: {json.dumps(schema, sort_keys=True)}"
        )
        response = self._request("/api/chat", {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "format": schema,
            "think": False,
            "keep_alive": "10m",
            "options": {"temperature": 0.2, "seed": 20261106 + iteration, "num_predict": 1800},
        })
        try:
            raw = json.loads(response["message"]["content"])
            spec = parse_strategy_spec(raw)
        except (KeyError, TypeError, json.JSONDecodeError, StrategySpecError) as error:
            raise OllamaError(f"model returned an invalid strategy: {error}") from error
        self._enforce_family_universe(spec)
        if family is not None and spec.family != family:
            raise OllamaError(f"model returned {spec.family}; required family is {family}")
        return OllamaProposal(
            spec=spec, model=model, prompt_eval_count=response.get("prompt_eval_count"),
            eval_count=response.get("eval_count"), total_duration_ns=response.get("total_duration"),
        )

    @staticmethod
    def _enforce_family_universe(spec: StrategySpec) -> None:
        catalog = available_data_catalog()
        if set(spec.universe) - set(catalog):
            raise OllamaError(f"universe contains symbols without local data: {spec.universe}")
        allowed = {
            "etf_trend": {"SPY", "QQQ"},
            "stock_momentum": {"AAPL", "MSFT", "NVDA"},
            "crypto_trend": {"BTC-USD", "ETH-USD"},
        }
        if spec.family in allowed and set(spec.universe) - allowed[spec.family]:
            raise OllamaError(f"universe is not available for {spec.family}: {spec.universe}")
        expected_market = "multi_asset" if spec.family == "multi_asset_trend" else ("crypto" if spec.family == "crypto_trend" else "equity")
        if spec.market.value != expected_market:
            raise OllamaError(f"wrong market for {spec.family}")


class OllamaStrategySearch:
    def __init__(
        self, lab: StrategyLab, client: OllamaClient | None = None,
        data_worker: DataWorker | None = None,
    ) -> None:
        self.lab = lab
        self.client = client or OllamaClient()
        self.data_worker = data_worker
        self.proposals_dir = self.lab.registry_dir / "proposals"
        self.proposals_dir.mkdir(parents=True, exist_ok=True)
        self.rejections_path = self.lab.registry_dir / "rejections.jsonl"

    def run(
        self, model: str, iterations: int, days: int = 900, *,
        auto_data: bool = False, lookback_days: int = 900, feed: str = "sip",
        progress: Callable[[str], None] | None = None,
        continuous: bool = False, pause_seconds: float = 0.0,
        families: tuple[str, ...] = SEARCHABLE_FAMILIES,
    ) -> list[CandidateEvaluation]:
        if not continuous and not 1 <= iterations <= 100:
            raise ValueError("iterations must be in [1, 100]")
        if not 0.0 <= pause_seconds <= 3600.0:
            raise ValueError("pause_seconds must be in [0, 3600]")
        if not families or any(family not in SEARCHABLE_FAMILIES for family in families):
            raise ValueError("families must contain supported price-research families")
        evaluations: list[CandidateEvaluation] = []
        iteration = 1
        while continuous or iteration <= iterations:
            prefix = f"[{iteration}/∞]" if continuous else f"[{iteration}/{iterations}]"
            emit = (lambda message: progress(f"{prefix} {message}")) if progress else (lambda message: None)
            try:
                family = self.lab.next_search_family(model, iteration, families)
                emit(f"Erzeuge {family}-Strategie mit {model} …")
                proposal = self.client.propose(
                    model, self.lab.champion(), iteration, downloadable_only=auto_data,
                    feedback=self.lab.research_feedback(model),
                    family=family,
                )
                emit(f"Strategie erzeugt: {proposal.spec.name}")
                proposal_path = self.proposals_dir / f"{proposal.spec.name}.json"
                if proposal_path.exists():
                    proposal_path = self.proposals_dir / f"{proposal.spec.name}_{iteration}_{time.time_ns()}.json"
                proposal_path.write_text(json.dumps({
                    "generator": {
                        "model": proposal.model, "prompt_eval_count": proposal.prompt_eval_count,
                        "eval_count": proposal.eval_count, "total_duration_ns": proposal.total_duration_ns,
                    },
                    "strategy": proposal.spec.to_dict(),
                }, indent=2, sort_keys=True), encoding="utf-8")
                self.lab.db.store_llm_event(proposal.model, "proposal", {
                    "iteration": iteration, "spec": proposal.spec.to_dict(),
                    "prompt_eval_count": proposal.prompt_eval_count, "eval_count": proposal.eval_count,
                })
                if self.lab.is_duplicate(proposal.spec):
                    raise OllamaError("duplicate_strategy_behavior: parameters, rules and universe were already evaluated")
                if auto_data:
                    if self.data_worker is None:
                        raise OllamaError("auto_data requires a configured data worker")
                    records: list[dict[str, Any]] = []
                    for data_request in requests_for_strategy(
                        proposal.spec, lookback_days, feed=feed,
                    ):
                        emit(f"Lade {data_request.timeframe}-Daten: {', '.join(data_request.symbols)}")
                        request_id = self.data_worker.enqueue(data_request)
                        records.append(self.data_worker.process(request_id, progress=emit))
                    bars, metadata = load_datasets(records)
                    evaluation = self.lab.evaluate(
                        proposal.spec, bars=bars, dataset_metadata=metadata, progress=emit,
                        generator_model=proposal.model, generator_iteration=iteration,
                    )
                else:
                    evaluation = self.lab.evaluate(
                        proposal.spec, days=days, progress=emit,
                        generator_model=proposal.model, generator_iteration=iteration,
                    )
                evaluations.append(evaluation)
                if continuous and len(evaluations) > 100:
                    evaluations.pop(0)
                verdict = "angenommen" if evaluation.eligible else "abgelehnt"
                detail = "" if evaluation.eligible else f" – {', '.join(evaluation.reasons)}"
                emit(f"Ergebnis: {verdict}{detail}")
            except Exception as error:
                emit(f"Fehler: {error}")
                self.lab.db.store_llm_event(model, "rejection", {"iteration": iteration, "error": str(error)})
                with self.rejections_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({
                        "iteration": iteration, "model": model, "error": str(error),
                    }, sort_keys=True) + "\n")
            iteration += 1
            if continuous and pause_seconds:
                time.sleep(pause_seconds)
        return evaluations
