import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

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
