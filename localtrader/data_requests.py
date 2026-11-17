from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from collections import deque
import hashlib
import json
import time

from .alpaca_check import load_local_env
from .data import available_data_catalog, bars_hash, validate_bars
from .domain import Bar, Market, to_dict
from .research_db import ResearchDB
from .strategy_dsl import StrategySpec


ALLOWED_TIMEFRAMES = frozenset({"1Min", "5Min", "15Min", "30Min", "1Hour", "1Day"})


class DataRequestError(RuntimeError):
    pass


class RetryExhausted(DataRequestError):
    pass


@dataclass(frozen=True, slots=True)
class DataRequestSpec:
    provider: str
    endpoint_type: str
    symbols: tuple[str, ...]
    timeframe: str
    start: datetime
    end: datetime
    feed: str | None
    purpose: str
    strategy_name: str

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise DataRequestError("data request timestamps must be timezone-aware")

    @property
    def request_id(self) -> str:
        raw = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "endpoint_type": self.endpoint_type,
            "symbols": list(self.symbols), "timeframe": self.timeframe,
            "start": self.start.isoformat(), "end": self.end.isoformat(),
            "feed": self.feed, "purpose": self.purpose,
            "strategy_name": self.strategy_name,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DataRequestSpec:
        return cls(
            provider=str(raw["provider"]), endpoint_type=str(raw["endpoint_type"]),
            symbols=tuple(str(item) for item in raw["symbols"]),
            timeframe=str(raw["timeframe"]),
            start=datetime.fromisoformat(str(raw["start"]).replace("Z", "+00:00")),
            end=datetime.fromisoformat(str(raw["end"]).replace("Z", "+00:00")),
            feed=str(raw["feed"]) if raw.get("feed") else None,
            purpose=str(raw["purpose"]), strategy_name=str(raw["strategy_name"]),
        )


class DataRequestPolicy:
    def __init__(self, max_estimated_bars: int = 1_000_000) -> None:
        self.max_estimated_bars = max_estimated_bars

    def validate(self, request: DataRequestSpec) -> DataRequestSpec:
        if request.provider != "alpaca" or request.endpoint_type not in {"stocks", "crypto"}:
            raise DataRequestError("only allowlisted Alpaca stock/crypto bar requests are supported")
        if request.timeframe not in ALLOWED_TIMEFRAMES:
            raise DataRequestError(f"unsupported timeframe: {request.timeframe}")
        if not 1 <= len(request.symbols) <= 20 or len(set(request.symbols)) != len(request.symbols):
            raise DataRequestError("request must contain 1-20 unique symbols")
        if request.end <= request.start or request.end > datetime.now(UTC) - timedelta(minutes=15):
            raise DataRequestError("end must be historical and at least 15 minutes old")
        if request.start < datetime(2016, 1, 1, tzinfo=UTC):
            raise DataRequestError("Alpaca Basic historical policy starts at 2016-01-01")
        catalog = available_data_catalog()
        missing = set(request.symbols) - set(catalog)
        if missing:
            raise DataRequestError(f"symbols are not in the approved data catalog: {sorted(missing)}")
        if request.endpoint_type == "crypto":
            if request.feed is not None or any(catalog[symbol]["market"] != "crypto" for symbol in request.symbols):
                raise DataRequestError("crypto requests may contain only crypto symbols and no stock feed")
        else:
            if request.feed not in {"iex", "sip"} or any(catalog[symbol]["market"] in {"crypto", "fx"} for symbol in request.symbols):
                raise DataRequestError("stock requests require iex/sip and Alpaca-tradable stock/ETF proxies")
        estimated = self._estimate_bars(request)
        if estimated > self.max_estimated_bars:
            raise DataRequestError(f"estimated download too large: {estimated} bars > {self.max_estimated_bars}")
        if not request.purpose.startswith(("strategy:", "research:")):
            raise DataRequestError("purpose must be an auditable strategy/research purpose")
        return request

    @staticmethod
    def _estimate_bars(request: DataRequestSpec) -> int:
        days = max(1.0, (request.end - request.start).total_seconds() / 86400)
        minutes = {"1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30, "1Hour": 60, "1Day": 1440}[request.timeframe]
        daily_minutes = 1440 if request.endpoint_type == "crypto" else 390
        trading_days = days if request.endpoint_type == "crypto" else days * 252 / 365.25
        return int(len(request.symbols) * trading_days * max(1, daily_minutes / minutes))


class MinuteRateLimiter:
    def __init__(
        self, requests_per_minute: int = 180,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not 1 <= requests_per_minute <= 200:
            raise ValueError("requests_per_minute must be in [1, 200]")
        self.limit = requests_per_minute
        self.clock = clock
        self.sleeper = sleeper
        self.timestamps: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            now = self.clock()
            while self.timestamps and now - self.timestamps[0] >= 60:
                self.timestamps.popleft()
            if len(self.timestamps) < self.limit:
                self.timestamps.append(now)
                return
            self._sleep_chunked(max(0.05, 60 - (now - self.timestamps[0])))

    def _sleep_chunked(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            chunk = min(30.0, remaining)
            self.sleeper(chunk)
            remaining -= chunk


class AlpacaDatasetDownloader:
    def __init__(
        self, api_key: str, api_secret: str, root: str | Path = "data",
        requests_per_minute: int = 180, max_attempts: int = 6,
        sleeper: Callable[[float], None] = time.sleep,
        wall_clock: Callable[[], float] = time.time,
    ) -> None:
        if not api_key or not api_secret:
            raise DataRequestError("Alpaca data credentials are missing")
        self.headers = {
            "APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json", "User-Agent": "local-paper-trader/0.1",
        }
        self.root = Path(root)
        self.max_attempts = max_attempts
        self.sleeper = sleeper
        self.wall_clock = wall_clock
        self.limiter = MinuteRateLimiter(requests_per_minute, sleeper=sleeper)

    @classmethod
    def from_env(cls, env_path: str | Path = ".env", **kwargs: Any) -> AlpacaDatasetDownloader:
        env = load_local_env(env_path)
        return cls(env.get("PAPER_BROKER_API_KEY", ""), env.get("PAPER_BROKER_API_SECRET", ""), **kwargs)

    def _request_page(self, url: str) -> tuple[dict[str, Any], dict[str, str]]:
        last_error = "unknown error"
        for attempt in range(1, self.max_attempts + 1):
            self.limiter.acquire()
            request = Request(url, headers=self.headers, method="GET")
            try:
                with urlopen(request, timeout=60) as response:
                    payload = json.loads(response.read().decode())
                    headers = {key.lower(): value for key, value in response.headers.items()}
                    if headers.get("x-ratelimit-remaining") == "0":
                        self._cooldown(headers, attempt)
                    return payload, headers
            except HTTPError as error:
                headers = {key.lower(): value for key, value in error.headers.items()}
                try:
                    payload = json.loads(error.read().decode())
                    last_error = str(payload.get("message") or payload.get("error") or error.reason)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    last_error = str(error.reason)
                if error.code == 429 or 500 <= error.code < 600:
                    self._cooldown(headers, attempt)
                    continue
                raise DataRequestError(f"Alpaca rejected data request ({error.code}): {last_error[:300]}") from error
            except (URLError, TimeoutError) as error:
                last_error = str(error)
                self._sleep_chunked(min(300.0, 2.0 ** attempt))
        raise RetryExhausted(f"Alpaca request failed after {self.max_attempts} attempts: {last_error[:300]}")

    def _cooldown(self, headers: dict[str, str], attempt: int) -> None:
        delay = min(300.0, 2.0 ** attempt)
        retry_after = headers.get("retry-after")
        reset = headers.get("x-ratelimit-reset")
        if retry_after:
            try:
                delay = max(delay, float(retry_after))
            except ValueError:
                pass
        if reset:
            try:
                delay = max(delay, float(reset) - self.wall_clock() + 1.0)
            except ValueError:
                pass
        self._sleep_chunked(max(0.05, min(delay, 900.0)))

    def _sleep_chunked(self, seconds: float) -> None:
        remaining = seconds
        while remaining > 0:
            chunk = min(30.0, remaining)
            self.sleeper(chunk)
            remaining -= chunk

    def _base_url(self, request: DataRequestSpec) -> str:
        if request.endpoint_type == "crypto":
            return "https://data.alpaca.markets/v1beta3/crypto/us/bars"
        return "https://data.alpaca.markets/v2/stocks/bars"

    def _params(self, request: DataRequestSpec, page_token: str | None = None) -> dict[str, Any]:
        symbols = [symbol.replace("-", "/") for symbol in request.symbols] if request.endpoint_type == "crypto" else list(request.symbols)
        params: dict[str, Any] = {
            "symbols": ",".join(symbols), "timeframe": request.timeframe,
            "start": request.start.isoformat().replace("+00:00", "Z"),
            "end": request.end.isoformat().replace("+00:00", "Z"),
            "limit": 10000, "sort": "asc",
        }
        if request.endpoint_type == "stocks":
            params.update({"feed": request.feed, "adjustment": "all"})
        if page_token:
            params["page_token"] = page_token
        return params

    def download(
        self, request: DataRequestSpec,
        progress: Callable[[str], None] | None = None,
    ) -> tuple[list[Bar], dict[str, Any], Path]:
        DataRequestPolicy().validate(request)
        raw_dir = self.root / "raw" / "alpaca" / request.request_id
        normalized_path = self.root / "normalized" / f"{request.request_id}.jsonl"
        raw_dir.mkdir(parents=True, exist_ok=True)
        normalized_path.parent.mkdir(parents=True, exist_ok=True)
        page_token: str | None = None
        pages: list[dict[str, Any]] = []
        seen_tokens: set[str] = set()
        page_number = 0
        while True:
            page_number += 1
            url = f"{self._base_url(request)}?{urlencode(self._params(request, page_token))}"
            payload, _ = self._request_page(url)
            pages.append(payload)
            (raw_dir / f"page_{page_number:05d}.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
            downloaded_bars = sum(
                len(items) for page in pages for items in page.get("bars", {}).values()
            )
            if progress:
                formatted = f"{downloaded_bars:,}".replace(",", ".")
                progress(f"Seite {page_number} – {formatted} Bars")
            next_token = payload.get("next_page_token")
            if not next_token:
                break
            if next_token in seen_tokens or page_number >= 10000:
                raise DataRequestError("invalid or excessive Alpaca pagination")
            seen_tokens.add(next_token)
            page_token = str(next_token)
        bars = self._normalize(request, pages)
        validate_bars(bars)
        with normalized_path.open("w", encoding="utf-8") as handle:
            for bar in bars:
                handle.write(json.dumps(to_dict(bar), sort_keys=True) + "\n")
        digest = bars_hash(bars)
        manifest = {
            "dataset_id": digest, "request_id": request.request_id, "provider": "alpaca",
            "request": request.to_dict(), "downloaded_at": datetime.now(UTC).isoformat(),
            "bars": len(bars), "pages": page_number, "hash_sha256": digest,
            "raw_directory": str(raw_dir), "normalized_path": str(normalized_path),
            "symbols": sorted({bar.symbol for bar in bars}),
            "first_timestamp": min(bar.start for bar in bars).isoformat() if bars else None,
            "last_timestamp": max(bar.start for bar in bars).isoformat() if bars else None,
        }
        (raw_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return bars, manifest, normalized_path

    @staticmethod
    def _duration(timeframe: str) -> timedelta:
        return {
            "1Min": timedelta(minutes=1), "5Min": timedelta(minutes=5),
            "15Min": timedelta(minutes=15), "30Min": timedelta(minutes=30),
            "1Hour": timedelta(hours=1), "1Day": timedelta(days=1),
        }[timeframe]

    def _normalize(self, request: DataRequestSpec, pages: list[dict[str, Any]]) -> list[Bar]:
        catalog = available_data_catalog()
        received = datetime.now(UTC)
        output: list[Bar] = []
        for payload in pages:
            for api_symbol, items in payload.get("bars", {}).items():
                symbol = api_symbol.replace("/", "-")
                market = Market(catalog[symbol]["market"])
                for item in items:
                    start = datetime.fromisoformat(str(item["t"]).replace("Z", "+00:00"))
                    end = start + self._duration(request.timeframe)
                    output.append(Bar(
                        symbol=symbol, market=market, timeframe=request.timeframe,
                        start=start, end=end, open=float(item["o"]), high=float(item["h"]),
                        low=float(item["l"]), close=float(item["c"]), volume=float(item.get("v", 0.0)),
                        source=f"alpaca_{request.feed or 'crypto_us'}", received_at=max(received, end),
                    ))
        output.sort(key=lambda bar: (bar.symbol, bar.start))
        if not output:
            raise DataRequestError("Alpaca returned no bars for the approved request")
        return output


class DataWorker:
    def __init__(self, db: ResearchDB, downloader: AlpacaDatasetDownloader) -> None:
        self.db = db
        self.downloader = downloader

    def enqueue(self, request: DataRequestSpec) -> str:
        validated = DataRequestPolicy().validate(request)
        self.db.enqueue_data_request(validated.request_id, validated.to_dict())
        return validated.request_id

    def process(
        self, request_id: str,
        progress: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        record = self.db.data_request(request_id)
        if record is None:
            raise DataRequestError("unknown data request")
        if record["status"] == "cooldown" and record.get("cooldown_until"):
            cooldown_until = datetime.fromisoformat(str(record["cooldown_until"]))
            if cooldown_until > datetime.now(UTC):
                raise DataRequestError(f"request cooldown is active until {cooldown_until.isoformat()}")
        if record["status"] == "failed":
            raise DataRequestError("request failed permanently; inspect the recorded policy/provider error")
        existing = self.db.dataset_for_request(request_id)
        if existing is not None:
            if progress:
                progress(f"Datensatz aus Cache – {existing['manifest']['bars']:,} Bars".replace(",", "."))
            return existing
        request = DataRequestSpec.from_dict(record["request"])
        self.db.mark_data_request(request_id, "running", increment_attempt=True)
        try:
            _, manifest, path = self.downloader.download(request, progress=progress)
            self.db.store_dataset(manifest["dataset_id"], request_id, "alpaca", str(path), manifest)
            self.db.mark_data_request(request_id, "complete", dataset_id=manifest["dataset_id"])
            result = self.db.dataset_for_request(request_id)
            assert result is not None
            return result
        except RetryExhausted as error:
            cooldown = datetime.now(UTC) + timedelta(minutes=5)
            self.db.mark_data_request(request_id, "cooldown", error=str(error), cooldown_until=cooldown.isoformat())
            raise
        except KeyboardInterrupt:
            self.db.mark_data_request(request_id, "pending", error="interrupted by user")
            raise
        except Exception as error:
            self.db.mark_data_request(request_id, "failed", error=str(error))
            raise

    def process_next(self, progress: Callable[[str], None] | None = None) -> dict[str, Any] | None:
        record = self.db.next_data_request()
        return None if record is None else self.process(str(record["request_id"]), progress=progress)


def requests_for_strategy(
    spec: StrategySpec, lookback_days: int, *, feed: str = "sip",
    now: datetime | None = None,
) -> list[DataRequestSpec]:
    """Translate a validated strategy into narrowly scoped Alpaca data requests."""
    if not 30 <= lookback_days <= 3650:
        raise DataRequestError("lookback_days must be in [30, 3650]")
    candidate_end = (now or datetime.now(UTC)) - timedelta(minutes=20)
    timeframe_minutes = {
        "1Min": 1, "5Min": 5, "15Min": 15, "30Min": 30,
        "1Hour": 60, "1Day": 1440,
    }[spec.timeframe]
    epoch = int(candidate_end.timestamp())
    end = datetime.fromtimestamp(
        epoch - epoch % (timeframe_minutes * 60), tz=UTC,
    )
    start = max(datetime(2016, 1, 1, tzinfo=UTC), end - timedelta(days=lookback_days))
    catalog = available_data_catalog()
    unknown = set(spec.universe) - set(catalog)
    if unknown:
        raise DataRequestError(f"strategy contains unknown symbols: {sorted(unknown)}")
    if any(catalog[symbol]["market"] == "fx" for symbol in spec.universe):
        raise DataRequestError("Alpaca historical FX data is not available; remove FX or configure a separate provider")
    groups = {
        "crypto": tuple(symbol for symbol in spec.universe if catalog[symbol]["market"] == "crypto"),
        "stocks": tuple(symbol for symbol in spec.universe if catalog[symbol]["market"] != "crypto"),
    }
    output: list[DataRequestSpec] = []
    for endpoint_type, symbols in groups.items():
        if not symbols:
            continue
        request = DataRequestSpec(
            provider="alpaca", endpoint_type=endpoint_type, symbols=symbols,
            timeframe=spec.timeframe, start=start, end=end,
            feed=None if endpoint_type == "crypto" else feed,
            purpose=f"strategy:{spec.name}", strategy_name=spec.name,
        )
        output.append(DataRequestPolicy().validate(request))
    return output


def load_datasets(records: list[dict[str, Any]]) -> tuple[list[Bar], dict[str, Any]]:
    bars: list[Bar] = []
    manifests: list[dict[str, Any]] = []
    for record in records:
        bars.extend(load_normalized_bars(record["normalized_path"]))
        manifests.append(record["manifest"])
    bars.sort(key=lambda bar: (bar.symbol, bar.start))
    validate_bars(bars)
    return bars, {
        "source": "alpaca", "dataset_ids": [item["dataset_id"] for item in manifests],
        "requests": [item["request"] for item in manifests],
        "downloaded_at": max(item["downloaded_at"] for item in manifests),
    }


def load_normalized_bars(path: str | Path) -> list[Bar]:
    output: list[Bar] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        output.append(Bar(
            symbol=item["symbol"], market=Market(item["market"]), timeframe=item["timeframe"],
            start=datetime.fromisoformat(item["start"]), end=datetime.fromisoformat(item["end"]),
            open=float(item["open"]), high=float(item["high"]), low=float(item["low"]),
            close=float(item["close"]), volume=float(item["volume"]), source=item["source"],
            received_at=datetime.fromisoformat(item["received_at"]),
        ))
    validate_bars(output)
    return output
