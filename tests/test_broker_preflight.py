from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from localtrader.alpaca_check import AlpacaCheckError
from localtrader.broker_preflight import PaperPreflight, blockers

NOW = datetime(2026, 11, 18, 18, 0, tzinfo=UTC)
ACCOUNT = {
    "id": "acc-123",
    "account_number": "PA0SECRET42",
    "status": "ACTIVE",
    "currency": "USD",
    "equity": "98765.43",
    "cash": "98765.43",
    "buying_power": "197530.86",
    "multiplier": "2",
    "trading_blocked": False,
    "account_blocked": False,
    "transfers_blocked": False,
    "trade_suspended_by_user": False,
    "pattern_day_trader": False,
    "shorting_enabled": True,
}
ASSET = {"status": "active", "tradable": True, "fractionable": True, "marginable": True, "shortable": True}


def quote(t: str) -> dict[str, Any]:
    return {"t": t, "bp": 678.91, "ap": 678.93, "bs": 4, "as": 7}


def bar(t: str) -> dict[str, Any]:
    return {"t": t, "o": 678.01, "h": 679.02, "l": 677.03, "c": 678.04, "v": 424242}


class FakeAlpaca:
    def __init__(self, **overrides: Any) -> None:
        self.calls: list[str] = []
        self.account = {**ACCOUNT, **overrides.get("account", {})}
        self.orders = overrides.get("orders", [])
        self.positions = overrides.get("positions", [])

    def __call__(self, url: str, headers: dict[str, str], timeout: int) -> tuple[int, dict[str, str], Any]:
        self.calls.append(url)
        path = urlparse(url).path
        rate = {"x-ratelimit-limit": "200", "x-ratelimit-remaining": "199"}
        if path == "/v2/account":
            return 200, rate, self.account
        if path == "/v2/account/configurations":
            return 200, {}, {"fractional_trading": True, "no_shorting": False, "max_margin_multiplier": "4"}
        if path.startswith("/v2/assets/"):
            return 200, {}, ASSET
        if path == "/v2/orders":
            return 200, {}, self.orders
        if path == "/v2/positions":
            return 200, {}, self.positions
        if path == "/v2/clock":
            return 200, {}, {"timestamp": NOW.isoformat(), "is_open": True}
        if path == "/v2/stocks/bars/latest":
            feed = "sip" if "feed=sip" in url else "iex"
            stamp = "2026-11-18T17:44:00Z" if feed == "sip" else "2026-11-18T17:59:00.123456789Z"
            return 200, rate, {"bars": {"SPY": bar(stamp), "QQQ": bar(stamp)}}
        if path == "/v2/stocks/quotes/latest":
            return 200, {}, {"quotes": {"SPY": quote("2026-11-18T17:59:59Z"), "QQQ": quote("2026-11-18T17:59:58Z")}}
        raise AssertionError(url)


def preflight(fake: FakeAlpaca, account_id: str = "acc-123") -> PaperPreflight:
    return PaperPreflight("key", "secret", "https://paper-api.alpaca.markets", account_id, fake, lambda: NOW)


class PreflightTests(unittest.TestCase):
    def test_clean_paper_account_has_no_blockers(self) -> None:
        report = preflight(FakeAlpaca()).run()
        self.assertEqual(report["blockers"], [])
        self.assertTrue(report["account"]["matches_configured_account"])
        self.assertEqual(report["assets"]["SPY"]["fractionable"], True)
        self.assertEqual(report["data_freshness"]["sip"]["bars"]["SPY"]["age_seconds"], 16 * 60)
        self.assertEqual(report["data_freshness"]["iex"]["bars"]["QQQ"]["age_seconds"], 60)
        self.assertIn("paper-api.alpaca.markets x-ratelimit-limit", report["rate_limits"])

    def test_report_contains_no_identifiers_balances_or_prices(self) -> None:
        # account number, balances, prices and sizes must never appear (holdout-period prices, private values)
        text = json.dumps(preflight(FakeAlpaca()).run())
        for secret in ("PA0SECRET42", "acc-123", "98765", "197530", "678.", "424242", "secret", "key"):
            self.assertNotIn(secret, text)

    def test_only_get_requests_to_allowed_hosts(self) -> None:
        fake = FakeAlpaca()
        preflight(fake).run()
        hosts = {urlparse(url).hostname for url in fake.calls}
        self.assertEqual(hosts, {"paper-api.alpaca.markets", "data.alpaca.markets"})
        check = preflight(fake)
        with self.assertRaises(AlpacaCheckError):
            check._get("https://api.alpaca.markets/v2/account")  # live host
        with self.assertRaises(AlpacaCheckError):
            PaperPreflight("k", "s", "https://api.alpaca.markets")
        with self.assertRaises(AlpacaCheckError):
            PaperPreflight("k", "s", "https://broker-api.sandbox.alpaca.markets")

    def test_default_getter_sends_get(self) -> None:
        from unittest import mock

        from localtrader import broker_preflight

        seen: list[str] = []

        class Response:
            status = 200
            headers: dict[str, str] = {}

            def __enter__(self) -> Response:
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def read(self) -> bytes:
                return b"{}"

        def fake_urlopen(request: Any, timeout: int) -> Response:
            seen.append(request.get_method())
            return Response()

        with mock.patch.object(broker_preflight, "urlopen", fake_urlopen):
            broker_preflight._http_get("https://paper-api.alpaca.markets/v2/clock", {}, 5)
        self.assertEqual(seen, ["GET"])

    def test_blockers_fail_closed(self) -> None:
        cases = [
            ({"account": {"trading_blocked": True}}, "trading_blocked"),
            ({"account": {"status": "ACCOUNT_UPDATED"}}, "account status ACCOUNT_UPDATED"),
            ({"account": {"equity": "0"}}, "no positive equity"),
            ({"orders": [{"symbol": "SPY"}]}, "open orders exist that this system did not create"),
            ({"positions": [{"symbol": "QQQ", "side": "short"}]}, "short positions exist"),
        ]
        for overrides, expected in cases:
            with self.subTest(expected):
                self.assertIn(expected, preflight(FakeAlpaca(**overrides)).run()["blockers"])
        # the configured value may be the account number instead of the UUID
        self.assertTrue(
            preflight(FakeAlpaca(), account_id="PA0SECRET42").run()["account"]["matches_configured_account"]
        )
        report = preflight(FakeAlpaca(), account_id="other").run()
        self.assertIn("account differs from PAPER_BROKER_ACCOUNT_ID", report["blockers"])
        self.assertIsNone(preflight(FakeAlpaca(), account_id="").run()["account"]["matches_configured_account"])

    def test_margin_shorting_and_missing_feed_are_warnings(self) -> None:
        report = preflight(FakeAlpaca()).run()
        self.assertTrue(any(w.startswith("margin account (multiplier 2)") for w in report["warnings"]))
        self.assertTrue(any(w.startswith("shorting enabled") for w in report["warnings"]))
        cash_only = preflight(FakeAlpaca(account={"multiplier": "1", "shorting_enabled": False})).run()
        self.assertEqual(cash_only["warnings"], [])
        self.assertEqual(cash_only["clock"]["skew_seconds"], 0.0)

    def test_unreadable_account_is_a_blocker(self) -> None:
        report = {"account": {"error": "HTTP 401"}, "assets": {}, "orders_and_positions": {}}
        self.assertEqual(blockers(report), ["account not readable (HTTP 401)"])


if __name__ == "__main__":
    unittest.main()
