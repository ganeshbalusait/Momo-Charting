from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from data.tradier_client import TradierClient


class TradierClientTests(unittest.TestCase):
    def test_normalizes_zero_dte_chain_for_existing_oi_wall_helpers(self) -> None:
        expiration = date(2026, 7, 15)
        payload = TradierClient._normalize_chain(
            "AAPL",
            expiration,
            {
                "options": {
                    "option": [
                        {"symbol": "AAPL-C", "option_type": "call", "strike": 210, "volume": 900, "open_interest": 1200},
                        {"symbol": "AAPL-P", "option_type": "put", "strike": 205, "volume": 700, "open_interest": 1500},
                    ]
                }
            },
            {"quotes": {"quote": {"symbol": "AAPL", "last": 207.5}}},
            contract_type="ALL",
            strike_count=80,
            today=expiration,
        )

        self.assertEqual(payload["underlyingPrice"], 207.5)
        call = payload["callExpDateMap"]["2026-07-15:0"]["210.0"][0]
        put = payload["putExpDateMap"]["2026-07-15:0"]["205.0"][0]
        self.assertEqual(call["daysToExpiration"], 0)
        self.assertEqual(call["totalVolume"], 900)
        self.assertEqual(put["openInterest"], 1500)

    def test_loads_every_expiration_through_fourteen_dte(self) -> None:
        client = TradierClient()
        today = datetime.now(client._tz).date()
        expirations = [today, today + timedelta(days=7), today + timedelta(days=21)]

        def fake_get_json(path: str, params: dict[str, str]) -> dict:
            if path.endswith("/expirations"):
                return {"expirations": {"date": [item.isoformat() for item in expirations]}}
            if path.endswith("/quotes"):
                return {"quotes": {"quote": {"symbol": "AAPL", "last": 200}}}
            expiration = params["expiration"]
            return {
                "options": {
                    "option": [
                        {"symbol": f"AAPL-{expiration}-C", "option_type": "call", "strike": 205, "volume": 100, "open_interest": 200},
                        {"symbol": f"AAPL-{expiration}-P", "option_type": "put", "strike": 195, "volume": 90, "open_interest": 180},
                    ]
                }
            }

        client._get_json = fake_get_json
        payload = client.get_option_chain_range("AAPL", max_days_to_expiration=14)

        self.assertEqual(len(payload["callExpDateMap"]), 2)
        self.assertEqual(len(payload["putExpDateMap"]), 2)
        self.assertNotIn(f"{expirations[2].isoformat()}:21", payload["callExpDateMap"])


if __name__ == "__main__":
    unittest.main()
