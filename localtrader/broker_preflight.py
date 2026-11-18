"""Read-only Alpaca paper preflight (T-012, spec §18.2/§18.3).

Answers, before any paper service starts: is this the expected paper account, is it allowed to trade, which
instruments and order features are available, is the account free of margin/short exposure, are there open
orders or positions we did not create, and how fresh is the market data per feed.

Safety:
- only ``GET`` requests, only to the verified paper host and the market-data host (enforced in ``_get``);
- the report contains flags, counts, symbols and timestamps only. Account numbers, balances, prices and
  quantities are never stored or printed: market data after the holdout start is reduced to timestamps
  immediately, and account values are reduced to yes/no checks (e.g. "equity > 0").
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from .alpaca_check import AlpacaCheckError, load_local_env, verify_paper_url

DATA_HOST = "data.alpaca.markets"
SYMBOLS = ("SPY", "QQQ")
FEEDS = ("iex", "sip")
ACCOUNT_FLAGS = (
    "trading_blocked",
    "account_blocked",
    "transfers_blocked",
    "trade_suspended_by_user",
    "pattern_day_trader",
    "shorting_enabled",
)
ASSET_FIELDS = ("status", "tradable", "fractionable", "marginable", "shortable", "easy_to_borrow", "exchange")
CONFIG_FIELDS = (
    "fractional_trading",
    "no_shorting",
    "max_margin_multiplier",
    "pdt_check",
    "dtbp_check",
    "suspend_trade",
)
MAX_CLOCK_SKEW_SECONDS = 2.0  # includes request latency; spec §18.3 asks to monitor time drift
RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining")

Response = tuple[int, dict[str, str], Any]


def _http_get(url: str, headers: dict[str, str], timeout: int) -> Response:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read())
    except HTTPError as error:
        try:
            payload = json.loads(error.read())
        except (json.JSONDecodeError, UnicodeDecodeError):
            payload = {}
        return error.code, {}, payload
    except (URLError, TimeoutError) as error:
        raise AlpacaCheckError(f"network request failed: {type(error).__name__}") from None


class PaperPreflight:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        paper_url: str,
        expected_account_id: str = "",
        getter: Callable[[str, dict[str, str], int], Response] = _http_get,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timeout: int = 20,
    ) -> None:
        if not api_key or not api_secret:
            raise AlpacaCheckError("paper API key and secret are required")
        self.base = verify_paper_url(paper_url)
        if urlparse(self.base).hostname != "paper-api.alpaca.markets":
            raise AlpacaCheckError("the preflight needs standard paper-api credentials")
        self.expected_account_id = expected_account_id.strip()
        self.headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret, "Accept": "application/json"}
        self.getter = getter
        self.clock = clock
        self.timeout = timeout
        self.rate_limits: dict[str, str] = {}

    def _get(self, url: str) -> tuple[int, Any]:
        host = urlparse(url).hostname
        if host not in {urlparse(self.base).hostname, DATA_HOST}:
            raise AlpacaCheckError("preflight requests only go to the paper and market-data hosts")
        status, headers, payload = self.getter(url, self.headers, self.timeout)
        for name in RATE_HEADERS:
            if name in headers:
                self.rate_limits[f"{host} {name}"] = headers[name]
        return status, payload

    @staticmethod
    def _error(status: int, payload: Any) -> str:
        message = payload.get("message") if isinstance(payload, dict) else None
        return f"HTTP {status}" + (f": {str(message)[:200]}" if message else "")

    # ------------------------------------------------------------------ broker side
    def account(self) -> dict[str, Any]:
        status, payload = self._get(f"{self.base}/v2/account")
        if status != 200:
            return {"ok": False, "error": self._error(status, payload)}
        result: dict[str, Any] = {flag: bool(payload.get(flag)) for flag in ACCOUNT_FLAGS}
        result["status"] = payload.get("status")
        result["currency"] = payload.get("currency")
        result["multiplier"] = str(payload.get("multiplier"))
        result["crypto_status"] = payload.get("crypto_status")
        result["equity_positive"] = _positive(payload.get("equity"))
        result["cash_at_least_equity"] = _number(payload.get("cash")) >= _number(payload.get("equity"))
        # PAPER_BROKER_ACCOUNT_ID may hold the account number (PA...) or the account UUID; neither is reported
        known = {str(payload.get("id") or ""), str(payload.get("account_number") or "")} - {""}
        result["matches_configured_account"] = (
            None if not self.expected_account_id else self.expected_account_id in known
        )
        return result

    def configuration(self) -> dict[str, Any]:
        status, payload = self._get(f"{self.base}/v2/account/configurations")
        if status != 200:
            return {"error": self._error(status, payload)}
        return {field: payload.get(field) for field in CONFIG_FIELDS}

    def assets(self) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for symbol in SYMBOLS:
            status, payload = self._get(f"{self.base}/v2/assets/{symbol}")
            output[symbol] = (
                {field: payload.get(field) for field in ASSET_FIELDS}
                if status == 200
                else {"error": self._error(status, payload)}
            )
        return output

    def open_orders_and_positions(self) -> dict[str, Any]:
        o_status, orders = self._get(f"{self.base}/v2/orders?{urlencode({'status': 'open', 'limit': 500})}")
        p_status, positions = self._get(f"{self.base}/v2/positions")
        result: dict[str, Any] = {}
        if o_status == 200 and isinstance(orders, list):
            result["open_orders"] = len(orders)
            result["open_order_symbols"] = sorted({str(order.get("symbol")) for order in orders})
        else:
            result["open_orders_error"] = self._error(o_status, orders)
        if p_status == 200 and isinstance(positions, list):
            result["positions"] = len(positions)
            result["position_symbols"] = sorted({str(position.get("symbol")) for position in positions})
            result["short_positions"] = sum(1 for p in positions if str(p.get("side")) == "short")
        else:
            result["positions_error"] = self._error(p_status, positions)
        return result

    def market_clock(self) -> dict[str, Any]:
        status, payload = self._get(f"{self.base}/v2/clock")
        if status != 200:
            return {"error": self._error(status, payload)}
        result = {key: payload.get(key) for key in ("timestamp", "is_open", "next_open", "next_close")}
        broker_time = _timestamp(payload.get("timestamp"))
        result["skew_seconds"] = round((self.clock() - broker_time).total_seconds(), 1) if broker_time else None
        return result

    # ------------------------------------------------------------------ data side (timestamps only)
    def data_freshness(self) -> dict[str, Any]:
        now = self.clock()
        output: dict[str, Any] = {}
        for feed in FEEDS:
            query = urlencode({"symbols": ",".join(SYMBOLS), "feed": feed})
            per_feed: dict[str, Any] = {}
            for kind, key in (("bars", "bars"), ("quotes", "quotes")):
                status, payload = self._get(f"https://{DATA_HOST}/v2/stocks/{kind}/latest?{query}")
                if status != 200:
                    per_feed[kind] = {"error": self._error(status, payload)}
                    continue
                stamps = {}
                for symbol, item in (payload.get(key) or {}).items():
                    moment = _timestamp(item.get("t") if isinstance(item, dict) else None)
                    stamps[symbol] = {
                        "timestamp": moment.isoformat() if moment else None,
                        "age_seconds": round((now - moment).total_seconds()) if moment else None,
                    }
                per_feed[kind] = stamps  # prices and sizes are dropped here, never stored
            output[feed] = per_feed
        return output

    def run(self) -> dict[str, Any]:
        account = self.account()
        report = {
            "checked_at": self.clock().isoformat(),
            "safety": {"requests": "GET only", "orders_sent": False, "values_reported": "flags, counts, timestamps"},
            "account": account,
            "configuration": self.configuration(),
            "assets": self.assets(),
            "orders_and_positions": self.open_orders_and_positions(),
            "clock": self.market_clock(),
            "data_freshness": self.data_freshness(),
            "rate_limits": dict(sorted(self.rate_limits.items())),
        }
        report["blockers"] = blockers(report)
        report["warnings"] = warnings(report)
        return report


def blockers(report: dict[str, Any]) -> list[str]:
    """Conditions that must stop a paper service from starting (spec §18.3: fail closed)."""
    found: list[str] = []
    account = report["account"]
    if "error" in account:
        return [f"account not readable ({account['error']})"]
    if account.get("status") != "ACTIVE":
        found.append(f"account status {account.get('status')}")
    for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user"):
        if account.get(flag):
            found.append(flag)
    if account.get("matches_configured_account") is False:
        found.append("account differs from PAPER_BROKER_ACCOUNT_ID")
    if not account.get("equity_positive"):
        found.append("no positive equity")
    for symbol, asset in report["assets"].items():
        if asset.get("error") or not asset.get("tradable") or asset.get("status") != "active":
            found.append(f"{symbol} not tradable")
    positions = report["orders_and_positions"]
    if positions.get("open_orders"):
        found.append("open orders exist that this system did not create")
    if positions.get("short_positions"):
        found.append("short positions exist")
    return found


def warnings(report: dict[str, Any]) -> list[str]:
    """Not blocking on their own, but the paper service must compensate or the user should change them."""
    found: list[str] = []
    account = report["account"]
    if account.get("multiplier") not in (None, "1", "None"):
        found.append(
            f"margin account (multiplier {account.get('multiplier')}): the service must size from cash only; "
            "setting max margin multiplier 1 at the broker removes the risk"
        )
    if account.get("shorting_enabled"):
        found.append("shorting enabled at the broker: the service must reject sells beyond the held quantity")
    if account.get("pattern_day_trader"):
        found.append("account flagged as pattern day trader")
    skew = report.get("clock", {}).get("skew_seconds")
    if skew is None or abs(skew) > MAX_CLOCK_SKEW_SECONDS:
        found.append(f"local clock differs from the broker clock ({skew} s); synchronize the system time")
    for feed, kinds in report.get("data_freshness", {}).items():
        for kind, value in kinds.items():
            if isinstance(value, dict) and "error" in value:
                found.append(f"{feed} {kind} not available in real time ({value['error']})")
    return found


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _positive(value: Any) -> bool:
    number = _number(value)
    return number == number and number > 0


def _timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def preflight_from_env(path: str = ".env") -> PaperPreflight:
    env = load_local_env(path)
    return PaperPreflight(
        env.get("PAPER_BROKER_API_KEY", ""),
        env.get("PAPER_BROKER_API_SECRET", ""),
        env.get("PAPER_BROKER_BASE_URL", ""),
        env.get("PAPER_BROKER_ACCOUNT_ID", ""),
    )
