from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
import json
import unittest

from localtrader.config import load_config
from localtrader.data import available_data_catalog, bars_hash, synthetic_fixture
from localtrader.ollama_lab import OllamaClient, OllamaError, OllamaStrategySearch
from localtrader.research_db import ResearchDB
from localtrader.spec_backtest import DeclarativeBacktestEngine
from localtrader.strategy_dsl import StrategySpecError, evaluate_stop_expression, load_strategy_spec, parse_strategy_spec, values_for_bar
from localtrader.data import group_by_symbol
from localtrader.strategy_lab import StrategyLab, behavior_digest, robustness_reasons, spec_digest


class StrategyLabTests(unittest.TestCase):
    def test_custom_stop_expression_and_multi_asset_spec(self) -> None:
        spec = load_strategy_spec("strategy_specs/multi_asset_trend.json")
        bars = group_by_symbol(synthetic_fixture(3, 250))["SPY"]
        values = values_for_bar(spec, bars)
        assert values is not None
        self.assertAlmostEqual(evaluate_stop_expression(spec, values), values["close"] - 3 * values["atr20"])
        self.assertEqual(spec.stop_mode, "trailing")

    def test_stop_expression_rejects_code(self) -> None:
        raw = json.loads(Path("strategy_specs/multi_asset_trend.json").read_text())
        raw["stop_expression"] = ["close", "__import__('os')", "-"]
        with self.assertRaises(StrategySpecError):
            parse_strategy_spec(raw)

    def test_declarative_engine_runs_cross_asset(self) -> None:
        config = load_config()
        spec = load_strategy_spec("strategy_specs/multi_asset_trend.json")
        bars = synthetic_fixture(config.seed, 350)
        result = DeclarativeBacktestEngine(config).run(spec, bars, bars_hash(bars))
        self.assertGreaterEqual(result.metrics["fills"], 0)
        self.assertLess(float(result.metrics["max_drawdown"]), 0.15)

    def test_research_db_only_accepts_typed_events(self) -> None:
        with TemporaryDirectory() as directory:
            db = ResearchDB(Path(directory) / "research.db")
            db.sync_catalog(available_data_catalog(), "test")
            db.store_llm_event("fake", "research_note", {"note": "safe"})
            self.assertEqual(db.status()["data_catalog"], len(available_data_catalog()))
            self.assertEqual(db.status()["llm_events"], 1)
            with self.assertRaises(ValueError):
                db.store_llm_event("fake", "raw_sql", {"sql": "DROP TABLE evaluations"})

    def test_ollama_must_be_loopback(self) -> None:
        with self.assertRaises(OllamaError):
            OllamaClient("https://example.com")

    def test_continuous_search_validates_pause_before_start(self) -> None:
        with TemporaryDirectory() as directory:
            search = OllamaStrategySearch(StrategyLab(load_config(), directory))
            with self.assertRaisesRegex(ValueError, "pause_seconds"):
                search.run("unused", 0, continuous=True, pause_seconds=-1)

    def test_behavior_digest_ignores_only_labels_and_prose(self) -> None:
        spec = load_strategy_spec("strategy_specs/crypto_trend.json")
        renamed = replace(spec, name="renamed_strategy", rationale="different prose", version="9")
        changed = replace(spec, stop_atr_multiple=spec.stop_atr_multiple + 0.5)
        self.assertEqual(behavior_digest(spec), behavior_digest(renamed))
        self.assertNotEqual(behavior_digest(spec), behavior_digest(changed))

    def test_robustness_gates_reject_negative_or_sparse_results(self) -> None:
        reasons = robustness_reasons(load_config(), {
            "worst_max_drawdown": 0.01, "median_trades": 2,
            "median_total_return": -0.01, "median_sharpe": -0.5,
            "cost_3x_total_return": -0.02, "positive_fold_ratio": 0.4,
        })
        self.assertIn("non_positive_median_return", reasons)
        self.assertIn("non_positive_median_sharpe", reasons)
        self.assertIn("fails_3x_cost_stress", reasons)
        self.assertIn("insufficient_positive_fold_ratio", reasons)

    def test_duplicate_detection_ignores_strategy_name(self) -> None:
        with TemporaryDirectory() as directory:
            lab = StrategyLab(load_config(), directory)
            spec = load_strategy_spec("strategy_specs/crypto_trend.json")
            candidate_id = spec_digest(spec)
            lab.db.store_spec(candidate_id, spec.to_dict(), "test")
            lab.db.store_evaluation(candidate_id, {
                "status": "evaluated", "score": 0.1, "eligible": True,
                "evaluated_at": "2026-01-01T00:00:00+00:00",
            })
            self.assertTrue(lab.is_duplicate(replace(spec, name="renamed_strategy")))

    def test_search_balances_underrepresented_strategy_families(self) -> None:
        with TemporaryDirectory() as directory:
            lab = StrategyLab(load_config(), directory)
            spec = load_strategy_spec("strategy_specs/crypto_trend.json")
            candidate_id = spec_digest(spec)
            lab.db.store_spec(candidate_id, spec.to_dict(), "test")
            lab.db.store_evaluation(candidate_id, {
                "status": "evaluated", "score": -0.1, "eligible": False,
                "evaluated_at": "2026-01-01T00:00:00+00:00",
            }, "qwen-test", 1)
            selected = lab.next_search_family(
                "qwen-test", 1, ("crypto_trend", "stock_momentum"),
            )
            self.assertEqual(selected, "stock_momentum")


if __name__ == "__main__":
    unittest.main()
