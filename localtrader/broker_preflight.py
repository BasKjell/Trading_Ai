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
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

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
# Account flags that must be reported and explicitly False before a start (missing = unconfirmed = blocker).
REQUIRED_FALSE_FLAGS = ("trading_blocked", "account_blocked", "trade_suspended_by_user")
# Paper/shadow use 15-minute delayed SIP bars (ADR 0006): a bar up to 35 minutes old is normal while the
# market is open (same limit as the paper service, T-006). Older is stale.
MAX_DATA_AGE_SECONDS = 35 * 60
MAX_OPEN_ORDERS_QUERY = 500
# ADR 0006: signals use 15-minute SIP bars delayed by at least 15 minutes. The preflight reads them through the
# historical bars endpoint (the real-time SIP ``latest`` endpoint is not part of the data plan).
SIP_DELAY = timedelta(minutes=15)
SIP_LOOKBACK = timedelta(days=5)  # covers weekends and holidays
BAR = timedelta(minutes=15)
MAX_CLOCK_SKEW_SECONDS = 2.0  # includes request latency; spec §18.3 asks to monitor time drift
RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining")

Response = tuple[int, dict[str, str], Any]


class _RefuseRedirects(HTTPRedirectHandler):
    """Authenticated preflight requests never follow a redirect: the host check applies to the URL actually
    requested, and the APCA credential headers must not be re-sent to another location (PR #36 review)."""

    def redirect_request(self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        raise AlpacaCheckError(f"redirect refused (HTTP {code})")


_OPENER = build_opener(_RefuseRedirects)


def _http_get(url: str, headers: dict[str, str], timeout: int) -> Response:
    request = Request(url, headers=headers, method="GET")
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, json.loads(response.read())
    except HTTPError as error:
        if 300 <= error.code < 400:
            raise AlpacaCheckError(f"redirect refused (HTTP {error.code})") from None
        return error.code, {}, {}  # the error body is not read: it may contain account details
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
        """Only the HTTP status: provider messages may contain account details (PR #36 review)."""
        del payload
        return f"HTTP {status}"

    # ------------------------------------------------------------------ broker side
    def account(self) -> dict[str, Any]:
        status, payload = self._get(f"{self.base}/v2/account")
        if status != 200:
            return {"ok": False, "error": self._error(status, payload)}
        flags = payload if isinstance(payload, dict) else {}
        result: dict[str, Any] = {
            flag: flags.get(flag) if isinstance(flags.get(flag), bool) else None for flag in ACCOUNT_FLAGS
        }
        payload = flags
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
        if not isinstance(payload, dict):
            return {"error": "invalid response"}
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
        if o_status == 200 and isinstance(orders, list) and all(isinstance(o, dict) for o in orders):
            result["open_orders"] = len(orders)
            result["open_order_symbols"] = sorted({str(order.get("symbol")) for order in orders})
            # a full page may hide further open orders: the inventory is not known completely
            result["open_orders_complete"] = len(orders) < MAX_OPEN_ORDERS_QUERY
        else:
            result["open_orders_error"] = self._error(o_status, orders) if o_status != 200 else "invalid response"
        if p_status == 200 and isinstance(positions, list) and all(isinstance(p, dict) for p in positions):
            result["positions"] = len(positions)
            result["position_symbols"] = sorted({str(position.get("symbol")) for position in positions})
            result["short_positions"] = sum(1 for p in positions if str(p.get("side")) == "short")
            result["positions_unknown_side"] = sum(1 for p in positions if str(p.get("side")) not in {"long", "short"})
        else:
            result["positions_error"] = self._error(p_status, positions) if p_status != 200 else "invalid response"
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
    def data_freshness(self, market_open: bool | None = None) -> dict[str, Any]:
        """Per feed, data type and ETF: timestamp, age and a state (ok, missing, invalid, future, stale).

        Age is only judged while the market is open; ``None`` (clock unknown) is judged like open (fail closed).
        """
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
                items = payload.get(key) if isinstance(payload, dict) else None
                items = items if isinstance(items, dict) else {}
                per_feed[kind] = {  # prices and sizes are dropped here, never stored
                    symbol: _freshness(items.get(symbol), now, market_open is not False) for symbol in SYMBOLS
                }
            output[feed] = per_feed
        return output

    def delayed_sip(self, market_open: bool | None = None) -> dict[str, Any]:
        """Newest complete delayed 15-minute SIP bar per ETF (ADR 0006): the start condition for paper and shadow.

        Alpaca applies ``end`` to the bar start, so the newest returned bar may still be running; as in ADR 0006
        every bar that ends after ``now - 15 min`` is discarded. Only the bar end time is kept. While the market
        is open (or the clock is unknown) the newest complete bar must have ended at most
        ``MAX_DATA_AGE_SECONDS`` ago.
        """
        now = self.clock()
        end = now - SIP_DELAY
        query = urlencode(
            {
                "symbols": ",".join(SYMBOLS),
                "timeframe": "15Min",
                "feed": "sip",
                "start": (end - SIP_LOOKBACK).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "sort": "desc",
                "limit": 1000,
            }
        )
        status, payload = self._get(f"https://{DATA_HOST}/v2/stocks/bars?{query}")
        if status != 200:
            return {"error": self._error(status, payload)}
        items = payload.get("bars") if isinstance(payload, dict) else None
        items = items if isinstance(items, dict) else {}
        output: dict[str, Any] = {}
        for symbol in SYMBOLS:
            rows = items.get(symbol)
            starts = (
                [_timestamp(row.get("t")) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            )
            if not starts:
                output[symbol] = {"bar_end": None, "age_seconds": None, "state": "missing"}
                continue
            if any(start is None for start in starts):
                output[symbol] = {"bar_end": None, "age_seconds": None, "state": "invalid"}
                continue
            complete = [start + BAR for start in starts if start is not None and start + BAR <= end]
            if not complete:
                output[symbol] = {"bar_end": None, "age_seconds": None, "state": "missing"}
                continue
            newest = max(complete)
            age = round((now - newest).total_seconds())
            state = "stale" if market_open is not False and age > MAX_DATA_AGE_SECONDS else "ok"
            output[symbol] = {"bar_end": newest.isoformat(), "age_seconds": age, "state": state}
        return output  # prices and volumes are dropped here, never stored

    def run(self) -> dict[str, Any]:
        account = self.account()
        clock = self.market_clock()
        report = {
            "checked_at": self.clock().isoformat(),
            "safety": {"requests": "GET only", "orders_sent": False, "values_reported": "flags, counts, timestamps"},
            "account": account,
            "configuration": self.configuration(),
            "assets": self.assets(),
            "orders_and_positions": self.open_orders_and_positions(),
            "clock": clock,
            "sip_delayed": self.delayed_sip(clock.get("is_open") if "error" not in clock else None),
            "data_freshness": self.data_freshness(clock.get("is_open") if "error" not in clock else None),
            "rate_limits": dict(sorted(self.rate_limits.items())),
        }
        report["blockers"] = blockers(report)
        report["warnings"] = warnings(report)
        return report


def blockers(report: dict[str, Any]) -> list[str]:
    """Conditions that must stop a paper service from starting (spec §18.3: fail closed).

    Everything a start depends on must be confirmed positively; an unknown or unreadable value blocks.
    """
    found: list[str] = []
    account = report["account"]
    if "error" in account:
        return [f"account not readable ({account['error']})"]
    if account.get("status") != "ACTIVE":
        found.append(f"account status {account.get('status')}")
    for flag in REQUIRED_FALSE_FLAGS:
        if account.get(flag) is None:
            found.append(f"account flag {flag} not reported")
        elif account.get(flag):
            found.append(flag)
    if account.get("matches_configured_account") is None:
        found.append("account identity not confirmed: set PAPER_BROKER_ACCOUNT_ID")
    elif account.get("matches_configured_account") is False:
        found.append("account differs from PAPER_BROKER_ACCOUNT_ID")
    if not account.get("equity_positive"):
        found.append("no positive equity")
    configuration = report.get("configuration", {})
    if "error" in configuration:
        found.append(f"account configuration not readable ({configuration['error']})")
    elif configuration.get("suspend_trade") is not False:
        found.append(
            "trading suspended in the account configuration"
            if configuration.get("suspend_trade")
            else "suspend_trade not reported in the account configuration"
        )
    for symbol, asset in report["assets"].items():
        if asset.get("error") or asset.get("tradable") is not True or asset.get("status") != "active":
            found.append(f"{symbol} not tradable")
    positions = report["orders_and_positions"]
    if "open_orders_error" in positions or "open_orders" not in positions:
        found.append(f"open orders not readable ({positions.get('open_orders_error', 'missing')})")
    else:
        if positions["open_orders"]:
            found.append("open orders exist that this system did not create")
        if not positions.get("open_orders_complete"):
            found.append("open order list may be incomplete")
    if "positions_error" in positions or "positions" not in positions:
        found.append(f"positions not readable ({positions.get('positions_error', 'missing')})")
    else:
        if positions.get("short_positions"):
            found.append("short positions exist")
        if positions.get("positions_unknown_side"):
            found.append("positions with unknown side")
    # ADR 0006: only delayed SIP bars may drive signals; IEX and real-time SIP are diagnostics only
    sip = report.get("sip_delayed", {})
    if "error" in sip:
        found.append(f"delayed SIP bars not readable ({sip['error']})")
    else:
        for symbol in SYMBOLS:
            state = sip.get(symbol, {}).get("state", "missing")
            if state != "ok":
                found.append(f"no usable delayed SIP bars for {symbol} ({state})")
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
                found.append(f"{feed} {kind} not available ({value['error']})")
                continue
            for symbol, entry in value.items():
                if entry.get("state") != "ok":
                    found.append(f"{feed} {kind} {symbol}: {entry.get('state')}")
    return found


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float("nan")


def _positive(value: Any) -> bool:
    number = _number(value)
    return number == number and number > 0


def _freshness(item: Any, now: datetime, judge_age: bool) -> dict[str, Any]:
    if not isinstance(item, dict) or "t" not in item:
        return {"timestamp": None, "age_seconds": None, "state": "missing"}
    moment = _timestamp(item.get("t"))
    if moment is None:
        return {"timestamp": None, "age_seconds": None, "state": "invalid"}
    age = round((now - moment).total_seconds())
    if age < -MAX_CLOCK_SKEW_SECONDS:
        state = "future"
    elif judge_age and age > MAX_DATA_AGE_SECONDS:
        state = "stale"
    else:
        state = "ok"
    return {"timestamp": moment.isoformat(), "age_seconds": age, "state": state}


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
