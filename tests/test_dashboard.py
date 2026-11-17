from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from localtrader.dashboard import _best_model, _read_dashboard, _spec_digest, serve_dashboard
from localtrader.research_db import ResearchDB


class DashboardTests(unittest.TestCase):
    def test_best_model_prefers_robust_then_closest_well_tested_candidate(self):
        weak = {"model": "small", "eligible": 0, "eligible_rate": 0.0}
        tested = {"model": "coder", "eligible": 0, "eligible_rate": 0.0}
        base = {"eligible": False, "score": 0.1, "reasons": ["a", "b"], "name": "x"}
        result = _best_model(
            [weak, tested],
            {"small": [base], "coder": [{**base, "score": -0.2, "reasons": ["a"]}] * 20},
        )
        self.assertEqual(result["model"], "coder")
        self.assertFalse(result["trade_ready"])

        robust_stats = {"model": "robust", "eligible": 1, "eligible_rate": 0.1}
        robust = {**base, "eligible": True, "score": -1.0, "reasons": []}
        result = _best_model([weak, tested, robust_stats], {"small": [base], "coder": [base], "robust": [robust]})
        self.assertEqual(result["model"], "robust")
        self.assertTrue(result["trade_ready"])

    def test_dashboard_attributes_existing_proposal_to_model(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            db = ResearchDB(root / "research.db")
            spec = {"name": "safe_test", "family": "etf_trend", "timeframe": "1Day", "universe": ["SPY"]}
            candidate_id = _spec_digest(spec)
            db.store_spec(candidate_id, spec, "test")
            db.store_llm_event("qwen-test", "proposal", {"iteration": 4, "spec": spec})
            result = {
                "candidate_id": candidate_id, "name": "safe_test", "family": "etf_trend",
                "status": "evaluated", "score": 0.2, "eligible": True, "paper_approved": False,
                "evaluated_at": "2026-01-01T00:00:00+00:00", "folds": 2, "runs": 6,
                "median_total_return": 0.03, "median_sharpe": 1.1, "worst_max_drawdown": 0.04,
                "median_trades": 5, "cost_3x_total_return": 0.01, "reasons": [],
                "spec": spec, "dataset": {"source": "alpaca", "bars": 500},
            }
            db.store_evaluation(candidate_id, result)
            payload = _read_dashboard(root / "research.db", root / "research_champion.json")
            self.assertEqual(payload["candidates"][0]["model"], "qwen-test")
            self.assertEqual(payload["models"][0]["evaluations"], 1)
            self.assertEqual(payload["best_model"]["model"], "qwen-test")
            self.assertEqual(len(payload["strategy_coverage"]), 8)
            self.assertEqual(
                next(item for item in payload["strategy_coverage"] if item["family"] == "news_momentum")["status"],
                "shadow_missing_data",
            )
            db.store_evaluation(candidate_id, result, "qwen-explicit", 4)
            payload = _read_dashboard(root / "research.db", root / "research_champion.json")
            self.assertEqual(payload["candidates"][0]["model"], "qwen-explicit")

    def test_dashboard_rejects_external_bind_address(self):
        with self.assertRaisesRegex(ValueError, "loopback"):
            serve_dashboard("unused", "0.0.0.0", 8501)


if __name__ == "__main__":
    unittest.main()
