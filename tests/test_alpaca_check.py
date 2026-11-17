import unittest

from localtrader.alpaca_check import AlpacaCheckError, verify_paper_url


class AlpacaCheckTests(unittest.TestCase):
    def test_only_paper_hosts_are_allowed(self) -> None:
        self.assertEqual(verify_paper_url("https://paper-api.alpaca.markets/"), "https://paper-api.alpaca.markets")
        self.assertEqual(verify_paper_url("https://paper-api.alpaca.markets/v2"), "https://paper-api.alpaca.markets")
        self.assertEqual(verify_paper_url("https://broker-api.sandbox.alpaca.markets"), "https://broker-api.sandbox.alpaca.markets")
        with self.assertRaises(AlpacaCheckError):
            verify_paper_url("https://api.alpaca.markets")
        with self.assertRaises(AlpacaCheckError):
            verify_paper_url("http://paper-api.alpaca.markets")


if __name__ == "__main__":
    unittest.main()
