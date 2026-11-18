from __future__ import annotations

import unittest
from datetime import UTC, datetime

from localtrader.holdout import HOLDOUT_START, HoldoutError, check_config, research_limit


class HoldoutBoundaryTests(unittest.TestCase):
    def test_limit_can_only_move_earlier(self) -> None:
        self.assertEqual(research_limit(), HOLDOUT_START)
        earlier = datetime(2025, 6, 1, tzinfo=UTC)
        self.assertEqual(research_limit(earlier), earlier)
        with self.assertRaises(HoldoutError):
            research_limit(datetime(2025, 11, 1, 0, 0, 1, tzinfo=UTC))
        with self.assertRaises(HoldoutError):
            research_limit(datetime(2025, 6, 1))

    def test_configuration_cannot_move_the_boundary(self) -> None:
        check_config({})
        check_config({"holdout": {"start": "2025-11-01T00:00:00+00:00"}})
        for bad in ("2025-12-01T00:00:00+00:00", "2025-11-01T00:00:00", "nonsense"):
            with self.subTest(bad), self.assertRaises(HoldoutError):
                check_config({"holdout": {"start": bad}})
