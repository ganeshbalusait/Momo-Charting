from __future__ import annotations

import unittest

from config import settings
from data.alpaca_client import AlpacaClient


class _FakeTradingClient:
    def __init__(self) -> None:
        self.orders = []

    def submit_order(self, order_data):
        self.orders.append(order_data)
        return order_data


class AlpacaOrderQuantityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_fractional = settings.trading.allow_fractional_shares
        settings.trading.allow_fractional_shares = False
        self.client = AlpacaClient.__new__(AlpacaClient)
        self.client._trading_client = _FakeTradingClient()

    def tearDown(self) -> None:
        settings.trading.allow_fractional_shares = self.original_fractional

    def test_market_exit_preserves_legacy_fractional_position(self) -> None:
        order = self.client.submit_market_exit_order("PLTR", 0.35, "exit-test")

        self.assertAlmostEqual(float(order.qty), 0.35, places=4)

    def test_extended_hours_sell_preserves_legacy_fractional_position(self) -> None:
        order = self.client.submit_extended_hours_limit_order(
            "PLTR",
            0.35,
            100.0,
            "sell",
            "exit-extended-test",
        )

        self.assertAlmostEqual(float(order.qty), 0.35, places=4)

    def test_new_fractional_entry_remains_blocked_when_disabled(self) -> None:
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            self.client.submit_market_entry_order("PLTR", 0.35, "entry-test")


if __name__ == "__main__":
    unittest.main()
