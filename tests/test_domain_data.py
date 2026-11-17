from datetime import UTC, datetime, timedelta
import unittest

from localtrader.data import DataQualityError, bars_hash, is_market_open, synthetic_fixture, validate_bars
from localtrader.domain import Bar, Market


class DomainDataTests(unittest.TestCase):
    def test_timezone_is_required(self) -> None:
        with self.assertRaises(ValueError):
            Bar("SPY", Market.EQUITY, "1d", datetime(2024, 1, 1), datetime(2024, 1, 2), 1, 2, 1, 2, 1, "x", datetime.now(UTC))

    def test_quality_rejects_duplicate_and_bad_ohlc(self) -> None:
        bars = synthetic_fixture(days=3)
        with self.assertRaises(DataQualityError):
            validate_bars([bars[0], bars[0]])
        item = bars[0]
        invalid = Bar(item.symbol, item.market, item.timeframe, item.start, item.end, 10, 9, 8, 10, 1, item.source, item.received_at)
        with self.assertRaises(DataQualityError):
            validate_bars([invalid])

    def test_hash_and_seed_are_reproducible(self) -> None:
        self.assertEqual(bars_hash(synthetic_fixture(7, 10)), bars_hash(synthetic_fixture(7, 10)))
        self.assertNotEqual(bars_hash(synthetic_fixture(7, 10)), bars_hash(synthetic_fixture(8, 10)))

    def test_market_calendars(self) -> None:
        saturday = datetime(2026, 1, 3, 16, 0, tzinfo=UTC)
        monday = datetime(2026, 1, 5, 15, 0, tzinfo=UTC)
        self.assertFalse(is_market_open(saturday, Market.EQUITY))
        self.assertTrue(is_market_open(saturday, Market.CRYPTO))
        self.assertTrue(is_market_open(monday, Market.EQUITY))


if __name__ == "__main__":
    unittest.main()

