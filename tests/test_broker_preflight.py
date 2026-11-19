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


CONFIGURATION = {"fractional_trading": True, "no_shorting": False, "max_margin_multiplier": "4", "suspend_trade": False}


class FakeAlpaca:
    def __init__(self, **overrides: Any) -> None:
        self.calls: list[str] = []
        self.account = {**ACCOUNT, **overrides.get("account", {})}
        for key in overrides.get("drop_account", ()):
            self.account.pop(key)
        self.configuration = {**CONFIGURATION, **overrides.get("configuration", {})}
        self.orders = overrides.get("orders", [])
        self.positions = overrides.get("positions", [])
        self.status = overrides.get("status", {})  # path -> HTTP status
        self.data = overrides.get("data")  # (kind, feed) -> payload override
        self.is_open = overrides.get("is_open", True)

    def __call__(self, url: str, headers: dict[str, str], timeout: int) -> tuple[int, dict[str, str], Any]:
        self.calls.append(url)
        path = urlparse(url).path
        rate = {"x-ratelimit-limit": "200", "x-ratelimit-remaining": "199"}
        if path in self.status:
            return self.status[path], {}, {"message": "account PA0SECRET42 detail"}
        feed = "sip" if "feed=sip" in url else "iex"
        if self.data is not None and path.startswith("/v2/stocks/"):
            kind = "bars" if "bars" in path else "quotes"
            return 200, {}, self.data(kind, feed)
        if path == "/v2/account":
            return 200, rate, self.account
        if path == "/v2/account/configurations":
            return 200, {}, self.configuration
        if path.startswith("/v2/assets/"):
            return 200, {}, ASSET
        if path == "/v2/orders":
            return 200, {}, self.orders
        if path == "/v2/positions":
            return 200, {}, self.positions
        if path == "/v2/clock":
            return 200, {}, {"timestamp": NOW.isoformat(), "is_open": self.is_open}
        if path == "/v2/stocks/bars/latest":
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

        with mock.patch.object(broker_preflight._OPENER, "open", fake_urlopen):
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
        unconfirmed = preflight(FakeAlpaca(), account_id="").run()
        self.assertIsNone(unconfirmed["account"]["matches_configured_account"])
        # PR #36 review 3: an unconfirmed account identity blocks the start
        self.assertIn("account identity not confirmed: set PAPER_BROKER_ACCOUNT_ID", unconfirmed["blockers"])

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


def _cli(fake: FakeAlpaca) -> tuple[int, dict[str, Any]]:
    import argparse
    import contextlib
    import io
    from unittest import mock

    from localtrader import broker_preflight, cli

    out = io.StringIO()
    patch = mock.patch.object(broker_preflight, "preflight_from_env", lambda path: preflight(fake))
    with patch, contextlib.redirect_stdout(out):
        code = cli.alpaca_preflight(argparse.Namespace(env_file="unused"))
    return code, json.loads(out.getvalue())


class ReviewFindingsTests(unittest.TestCase):
    """PR #36 review (Codex): unknown broker state must never be start-ready."""

    def test_clean_account_exits_zero(self) -> None:
        code, report = _cli(FakeAlpaca())
        self.assertEqual((code, report["blockers"]), (0, []))

    def test_unreadable_orders_or_positions_block_on_cli_level(self) -> None:
        cases = {
            "orders": {"/v2/orders": 503},
            "positions": {"/v2/positions": 503},
            "both": {"/v2/orders": 503, "/v2/positions": 503},
        }
        for name, status in cases.items():
            with self.subTest(name):
                code, report = _cli(FakeAlpaca(status=status))
                self.assertEqual(code, 2)
                if "/v2/orders" in status:
                    self.assertIn("open orders not readable (HTTP 503)", report["blockers"])
                if "/v2/positions" in status:
                    self.assertIn("positions not readable (HTTP 503)", report["blockers"])
        code, report = _cli(FakeAlpaca(positions=[{"symbol": "SPY"}]))
        self.assertIn("positions with unknown side", report["blockers"])
        self.assertEqual(code, 2)

    def test_full_order_page_is_incomplete(self) -> None:
        from localtrader.broker_preflight import MAX_OPEN_ORDERS_QUERY

        report = preflight(FakeAlpaca(orders=[{"symbol": "SPY"}] * MAX_OPEN_ORDERS_QUERY)).run()
        self.assertIn("open order list may be incomplete", report["blockers"])

    def test_redirect_is_refused_before_credentials_are_resent(self) -> None:
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        from localtrader import broker_preflight

        seen: list[str] = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                seen.append(self.path + " " + str(self.headers.get("APCA-API-KEY-ID")))
                if self.path == "/start":
                    self.send_response(302)
                    self.send_header("Location", "/elsewhere")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"{}")

            def log_message(self, *args: object) -> None:
                return None

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/start"
            with self.assertRaises(AlpacaCheckError):
                broker_preflight._http_get(url, {"APCA-API-KEY-ID": "k"}, 5)
        finally:
            server.shutdown()
            server.server_close()
        self.assertEqual(seen, ["/start k"])  # the redirect target never received the credentials

    def test_account_configuration_and_flags_must_be_confirmed(self) -> None:
        cases = [
            ({"configuration": {"suspend_trade": True}}, "trading suspended in the account configuration"),
            ({"configuration": {"suspend_trade": None}}, "suspend_trade not reported in the account configuration"),
            ({"status": {"/v2/account/configurations": 500}}, "account configuration not readable (HTTP 500)"),
            ({"drop_account": ("trading_blocked",)}, "account flag trading_blocked not reported"),
            ({"drop_account": ("trade_suspended_by_user",)}, "account flag trade_suspended_by_user not reported"),
        ]
        for overrides, expected in cases:
            with self.subTest(expected):
                code, report = _cli(FakeAlpaca(**overrides))
                self.assertIn(expected, report["blockers"])
                self.assertEqual(code, 2)

    def test_error_messages_never_reach_the_report(self) -> None:
        for path in ("/v2/account", "/v2/stocks/bars/latest", "/v2/orders"):
            with self.subTest(path):
                text = json.dumps(preflight(FakeAlpaca(status={path: 403})).run())
                self.assertNotIn("PA0SECRET42", text)
                self.assertNotIn("detail", text)
                self.assertIn("HTTP 403", text)

    def test_missing_invalid_future_and_stale_data_block_when_no_feed_is_usable(self) -> None:
        def payload(stamp: str | None) -> Any:
            def data(kind: str, feed: str) -> dict[str, Any]:
                if stamp is None:
                    return {kind: {}}
                item = bar(stamp) if kind == "bars" else quote(stamp)
                return {kind: {"SPY": item, "QQQ": item}}

            return data

        cases = {
            "missing": None,
            "invalid": "not-a-time",
            "future": "2026-11-18T18:05:00Z",
            "stale": "2026-11-18T17:20:00Z",  # 40 minutes old while the market is open
        }
        for state, stamp in cases.items():
            with self.subTest(state):
                code, report = _cli(FakeAlpaca(data=payload(stamp)))
                self.assertEqual(code, 2)
                self.assertIn("no usable current bars for SPY in any feed", report["blockers"])
                self.assertEqual(report["data_freshness"]["sip"]["bars"]["QQQ"]["state"], state)
                self.assertIn(f"sip bars SPY: {state}", report["warnings"])
        # old data while the market is closed is normal; one usable feed suffices
        closed = preflight(FakeAlpaca(data=payload("2026-11-17T21:00:00Z"), is_open=False)).run()
        self.assertEqual(closed["blockers"], [])
        fresh = "2026-11-18T17:44:00Z"
        sip_only = preflight(FakeAlpaca(data=lambda k, f: payload(fresh)(k, f) if f == "sip" else {k: {}})).run()
        self.assertEqual(sip_only["blockers"], [])
        self.assertIn("iex bars SPY: missing", sip_only["warnings"])


if __name__ == "__main__":
    unittest.main()
