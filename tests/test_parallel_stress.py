"""T-023: parallel stress scenarios must give exactly the serial result."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import time
from pathlib import Path

from localtrader.cli import build_parser
from localtrader.config import load_config
from localtrader.intraday_data import SessionSpec, synthetic_intraday
from localtrader.intraday_engine import instruments_from_config
from localtrader.intraday_report import default_workers, run_scenarios

SESSION = SessionSpec("Europe/Berlin", time(9, 0), time(17, 30))


class ParallelScenarioTests(unittest.TestCase):
    def test_parallel_run_is_identical_to_serial_run(self) -> None:
        config = load_config()
        data = synthetic_intraday("DAX", days=20, seed=7, start_price=15000, session=SESSION)
        args = (config, instruments_from_config(config), ["intraday_trend", "mean_reversion"], {"DAX": data})
        serial = run_scenarios(*args, workers=1)
        parallel = run_scenarios(*args, workers=3)
        # same keys in the same order and bit-identical numbers (JSON text compares floats exactly)
        self.assertEqual(list(serial), list(parallel))
        self.assertEqual(json.dumps(serial, default=str), json.dumps(parallel, default=str))
        self.assertEqual(len(serial), 9)
        self.assertGreater(sum(int(str(v["trades"])) for v in serial.values()), 0)

    def test_invalid_worker_count_is_rejected(self) -> None:
        config = load_config()
        data = synthetic_intraday("DAX", days=5, seed=7, start_price=15000, session=SESSION)
        with self.assertRaises(ValueError):
            run_scenarios(config, instruments_from_config(config), ["intraday_trend"], {"DAX": data}, workers=0)

    def test_default_workers_is_positive(self) -> None:
        self.assertGreaterEqual(default_workers(), 1)

    def test_cli_workers_option_gives_identical_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outputs = {}
            for workers in ("1", "3"):
                out = Path(tmp) / f"w{workers}"
                common = ["intraday-stress", "--synthetic-days", "15", "--strategies", "intraday_trend"]
                args = build_parser().parse_args([*common, "--workers", workers, "--output", str(out)])
                with contextlib.redirect_stdout(io.StringIO()):
                    code = args.func(args)
                self.assertEqual(code, 0)
                outputs[workers] = (out / "scenarios.json").read_text(encoding="utf-8")
            self.assertEqual(outputs["1"], outputs["3"])
            self.assertFalse(json.loads(outputs["1"])["holdout"]["used"])


if __name__ == "__main__":
    unittest.main()
