import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from localtrader.data_requests import (
    DataRequestError,
    DataRequestPolicy,
    DataRequestSpec,
    DataWorker,
    MinuteRateLimiter,
    RetryExhausted,
    requests_for_strategy,
)
from localtrader.research_db import ResearchDB
from localtrader.strategy_dsl import load_strategy_spec


def valid_request(**changes):
    values = {
        "provider": "alpaca",
        "endpoint_type": "stocks",
        "symbols": ("SPY",),
        "timeframe": "5Min",
        "start": datetime(2026, 1, 1, tzinfo=UTC),
        "end": datetime(2026, 2, 1, tzinfo=UTC),
        "feed": "sip",
        "purpose": "research:test",
        "strategy_name": "test",
    }
    values.update(changes)
    return DataRequestSpec(**values)


class DataRequestTests(unittest.TestCase):
    def test_data_policy_rejects_recent_and_excessive_requests(self):
        policy = DataRequestPolicy(max_estimated_bars=100)
        with self.assertRaisesRegex(DataRequestError, "too large"):
            policy.validate(valid_request())
        with self.assertRaisesRegex(DataRequestError, "at least 15 minutes"):
            DataRequestPolicy().validate(valid_request(end=datetime.now(UTC)))

    def test_rate_limiter_waits_until_window_opens(self):
        state = {"now": 0.0, "slept": 0.0}

        def sleep(seconds):
            state["now"] += seconds
            state["slept"] += seconds

        limiter = MinuteRateLimiter(2, clock=lambda: state["now"], sleeper=sleep)
        limiter.acquire()
        limiter.acquire()
        limiter.acquire()
        self.assertGreaterEqual(state["slept"], 60.0)

    def test_strategy_requests_are_split_by_alpaca_endpoint(self):
        spec = load_strategy_spec("strategy_specs/multi_asset_trend.json")
        spec = replace(spec, universe=tuple(symbol for symbol in spec.universe if symbol != "EUR-USD"))
        requests = requests_for_strategy(
            spec,
            365,
            now=datetime(2026, 6, 1, tzinfo=UTC),
        )
        self.assertEqual({item.endpoint_type for item in requests}, {"stocks", "crypto"})
        self.assertTrue(all(item.end == datetime(2026, 5, 31, tzinfo=UTC) for item in requests))

    def test_worker_persists_cooldown_and_does_not_retry_early(self):
        class ExhaustedDownloader:
            def download(self, request, progress=None):
                raise RetryExhausted("rate limit")

        with TemporaryDirectory() as directory:
            db = ResearchDB(Path(directory) / "research.db")
            worker = DataWorker(db, ExhaustedDownloader())
            request_id = worker.enqueue(valid_request())
            with self.assertRaises(RetryExhausted):
                worker.process(request_id)
            self.assertEqual(db.data_request(request_id)["status"], "cooldown")
            self.assertIsNone(db.next_data_request())
            with self.assertRaisesRegex(DataRequestError, "cooldown is active"):
                worker.process(request_id)

    def test_interrupted_download_returns_to_pending(self):
        class InterruptedDownloader:
            def download(self, request, progress=None):
                raise KeyboardInterrupt

        with TemporaryDirectory() as directory:
            db = ResearchDB(Path(directory) / "research.db")
            worker = DataWorker(db, InterruptedDownloader())
            request_id = worker.enqueue(valid_request())
            with self.assertRaises(KeyboardInterrupt):
                worker.process(request_id)
            self.assertEqual(db.data_request(request_id)["status"], "pending")


if __name__ == "__main__":
    unittest.main()


class DroppedConnectionTests(unittest.TestCase):
    def test_remote_disconnect_is_retried(self):
        # Regression: a full 10-year download aborted on http.client.RemoteDisconnected after 270 pages.
        import http.client
        import io

        from localtrader.data_requests import AlpacaDatasetDownloader

        class Response(io.BytesIO):
            headers: dict = {}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

        calls = {"n": 0}

        def fake_urlopen(request, timeout=60):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http.client.RemoteDisconnected("Remote end closed connection without response")
            return Response(b'{"bars": {}}')

        downloader = AlpacaDatasetDownloader("k", "s", sleeper=lambda seconds: None)
        with mock.patch("localtrader.data_requests.urlopen", fake_urlopen):
            payload, _ = downloader._request_page("https://data.alpaca.markets/v2/stocks/bars")
        self.assertEqual(payload, {"bars": {}})
        self.assertEqual(calls["n"], 2)


class ProviderWindowTests(unittest.TestCase):
    def test_rows_beyond_the_request_never_reach_raw_or_normalized_files(self):
        # Regression (PR #21 review 5): Alpaca's end is inclusive and its answer was written to
        # raw/alpaca/.../page_*.json and normalized/*.jsonl before any window check, so a row exactly at
        # the holdout start stayed on disk even though the research CSV dropped it.
        from localtrader.data_requests import AlpacaDatasetDownloader

        cutoff = datetime(2025, 11, 1, tzinfo=UTC)
        start = datetime(2025, 10, 31, 19, 15, tzinfo=UTC)
        request = DataRequestSpec(
            "alpaca",
            "stocks",
            ("SPY",),
            "15Min",
            start,
            cutoff - timedelta(minutes=15),
            "sip",
            "research:t",
            "s",
            "raw",
        )

        def item(moment: str, price: float) -> dict:
            return {"t": moment, "o": price, "h": price, "l": price, "c": price, "v": 1}

        answer = {
            "bars": {
                "SPY": [
                    item("2025-10-31T19:00:00Z", 1.0),  # before the requested start
                    item("2025-10-31T19:15:00Z", 2.0),
                    item("2025-10-31T19:45:00Z", 3.0),  # last allowed: ends exactly at the cutoff
                    item("2025-11-01T00:00:00Z", 987.65),  # exactly at the holdout start
                    item("2025-11-03T14:30:00Z", 876.54),
                ]
            },
            "next_page_token": None,
        }
        with TemporaryDirectory() as tmp:
            downloader = AlpacaDatasetDownloader("k", "s", root=tmp, sleeper=lambda seconds: None)
            with mock.patch.object(downloader, "_request_page", return_value=(answer, {})):
                bars, manifest, normalized = downloader.download(request)
            stored = "".join(path.read_text(encoding="utf-8") for path in Path(tmp).rglob("*") if path.is_file())
        self.assertEqual([bar.close for bar in bars], [2.0, 3.0])
        self.assertEqual(manifest["last_timestamp"], "2025-10-31T19:45:00+00:00")
        self.assertTrue(all(bar.end <= cutoff for bar in bars))
        for withheld in ("2025-11-01", "2025-11-03", "987.65", "876.54", "2025-10-31T19:00"):
            self.assertNotIn(withheld, stored)

    def test_unreadable_timestamp_fails_without_echoing_the_row(self):
        from localtrader.data_requests import AlpacaDatasetDownloader

        request = DataRequestSpec(
            "alpaca", "stocks", ("SPY",), "15Min", datetime(2025, 10, 1, tzinfo=UTC),
            datetime(2025, 10, 2, tzinfo=UTC), "sip", "research:t", "s", "raw",
        )  # fmt: skip
        answer = {"bars": {"SPY": [{"t": "not-a-time", "o": 543.21, "h": 1, "l": 1, "c": 1}]}}
        with TemporaryDirectory() as tmp:
            downloader = AlpacaDatasetDownloader("k", "s", root=tmp, sleeper=lambda seconds: None)
            with (
                mock.patch.object(downloader, "_request_page", return_value=(answer, {})),
                self.assertRaises(DataRequestError) as caught,
            ):
                downloader.download(request)
            self.assertEqual(list(Path(tmp).rglob("page_*.json")), [])
        self.assertNotIn("543.21", str(caught.exception))
        self.assertNotIn("not-a-time", str(caught.exception))
