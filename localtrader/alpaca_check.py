from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen
import json


class AlpacaCheckError(RuntimeError):
    pass


ALLOWED_PAPER_HOSTS = frozenset({"paper-api.alpaca.markets", "broker-api.sandbox.alpaca.markets"})


def load_local_env(path: str | Path = ".env") -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def verify_paper_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_PAPER_HOSTS:
        raise AlpacaCheckError("only an explicit Alpaca paper/sandbox URL is permitted")
    if parsed.path.rstrip("/") not in {"", "/v2"} or parsed.query or parsed.fragment:
        raise AlpacaCheckError("paper base URL must not contain an unexpected path, query or fragment")
    return f"https://{parsed.hostname}"


class AlpacaAccessChecker:
    def __init__(self, api_key: str, api_secret: str, paper_url: str, timeout: int = 20) -> None:
        if not api_key or not api_secret:
            raise AlpacaCheckError("paper API key and secret are required")
        self.paper_url = verify_paper_url(paper_url)
        self.timeout = timeout
        self.headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
            "User-Agent": "local-paper-trader/0.1",
        }

    def _get(self, url: str) -> tuple[int, dict[str, Any]]:
        request = Request(url, headers=self.headers, method="GET")
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, json.loads(response.read().decode())
        except HTTPError as error:
            try:
                payload = json.loads(error.read().decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {"message": "non-JSON error response"}
            return error.code, payload
        except (URLError, TimeoutError) as error:
            raise AlpacaCheckError(f"network request failed: {error}") from error

    @staticmethod
    def _safe_error(payload: dict[str, Any]) -> str | None:
        value = payload.get("message") or payload.get("error")
        return str(value)[:300] if value else None

    def check_account(self) -> dict[str, Any]:
        if urlparse(self.paper_url).hostname == "broker-api.sandbox.alpaca.markets":
            return {
                "status": "not_applicable",
                "reason": "Broker API sandbox credentials use a different account workflow; expected standard paper-api credentials.",
            }
        status, payload = self._get(f"{self.paper_url}/v2/account")
        return {
            "http_status": status,
            "authenticated": status == 200,
            "account_status": payload.get("status") if status == 200 else None,
            "trading_blocked": payload.get("trading_blocked") if status == 200 else None,
            "error": self._safe_error(payload) if status != 200 else None,
        }

    def _window(self) -> tuple[str, str]:
        end = datetime.now(UTC) - timedelta(minutes=20)
        start = end - timedelta(days=14)
        return start.isoformat().replace("+00:00", "Z"), end.isoformat().replace("+00:00", "Z")

    def check_stock_feed(self, feed: str) -> dict[str, Any]:
        if feed not in {"iex", "sip"}:
            raise ValueError("feed must be iex or sip")
        start, end = self._window()
        query = urlencode({
            "timeframe": "1Day", "start": start, "end": end,
            "limit": 5, "adjustment": "all", "feed": feed,
        })
        status, payload = self._get(f"https://data.alpaca.markets/v2/stocks/SPY/bars?{query}")
        bars = payload.get("bars", []) if status == 200 else []
        return {
            "feed": feed, "http_status": status, "available": status == 200 and bool(bars),
            "bars_received": len(bars),
            "first_timestamp": bars[0].get("t") if bars else None,
            "last_timestamp": bars[-1].get("t") if bars else None,
            "error": self._safe_error(payload) if status != 200 else None,
        }

    def check_latest_stock_feed(self, feed: str) -> dict[str, Any]:
        if feed not in {"iex", "sip"}:
            raise ValueError("feed must be iex or sip")
        query = urlencode({"feed": feed})
        status, payload = self._get(f"https://data.alpaca.markets/v2/stocks/SPY/bars/latest?{query}")
        bar = payload.get("bar") if status == 200 else None
        return {
            "feed": feed, "http_status": status, "available": status == 200 and bool(bar),
            "timestamp": bar.get("t") if bar else None,
            "error": self._safe_error(payload) if status != 200 else None,
        }

    def check_crypto(self) -> dict[str, Any]:
        start, end = self._window()
        query = urlencode({
            "symbols": "BTC/USD", "timeframe": "1Day", "start": start,
            "end": end, "limit": 5, "sort": "asc",
        })
        status, payload = self._get(f"https://data.alpaca.markets/v1beta3/crypto/us/bars?{query}")
        bars = payload.get("bars", {}).get("BTC/USD", []) if status == 200 else []
        return {
            "http_status": status, "available": status == 200 and bool(bars),
            "bars_received": len(bars),
            "first_timestamp": bars[0].get("t") if bars else None,
            "last_timestamp": bars[-1].get("t") if bars else None,
            "error": self._safe_error(payload) if status != 200 else None,
        }

    def run(self) -> dict[str, Any]:
        return {
            "safety": {"paper_host_verified": True, "orders_sent": False, "secrets_printed": False},
            "paper_account": self.check_account(),
            "stock_data": {
                "historical": {
                    "iex": self.check_stock_feed("iex"),
                    "sip": self.check_stock_feed("sip"),
                },
                "latest": {
                    "iex": self.check_latest_stock_feed("iex"),
                    "sip": self.check_latest_stock_feed("sip"),
                },
            },
            "crypto_data": self.check_crypto(),
        }


def checker_from_env(path: str | Path = ".env") -> AlpacaAccessChecker:
    env = load_local_env(path)
    return AlpacaAccessChecker(
        env.get("PAPER_BROKER_API_KEY", ""),
        env.get("PAPER_BROKER_API_SECRET", ""),
        env.get("PAPER_BROKER_BASE_URL", ""),
    )
