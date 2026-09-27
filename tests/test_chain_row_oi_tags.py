from __future__ import annotations

import unittest
from datetime import date, timedelta

from api_server import DashboardState

# A LIVE expiry, not a literal. The original fixture hardcoded the date it
# was written ("2026-08-21"); one day later every contract in the fixture
# was expired, the tagging filtered the whole chain, and both tests failed
# with an empty row set - a daily-rot flake that looked like a weekend
# regression (2026-08-22 triage).
_FIXTURE_EXPIRY = (date.today() + timedelta(days=30)).isoformat()


def _contract(strike: float, oi: int, *, vol: int = 10, delta: float = 0.3) -> dict:
    return {
        "symbol": f"X{strike}",
        "strike_price": strike,
        "open_interest": oi,
        "total_volume": vol,
        "delta": delta,
        "expiry_date": _FIXTURE_EXPIRY,
        "bid": 1.0,
        "ask": 1.2,
        "last": 1.1,
        "mark": 1.1,
    }


class ChainRowOiTagTests(unittest.TestCase):
    """The chain's OI-cell highlights follow the daily-sheet side rule.

    2026-08-20 (MSFT): the old tagging highlighted the top-5 OI per side
    plus a top-3 deep-ITM booster, so the 480 CALL's 24K glowed as a wall
    below spot while the sheet's put board printed 480 @ 8.4K. A call
    contract's OI only counts above spot, a put contract's only at/below.
    """

    @staticmethod
    def _state() -> DashboardState:
        state = DashboardState.__new__(DashboardState)

        def contracts(chain_payload: dict, contract_type: str = "CALL") -> list[dict]:
            if str(contract_type).upper() == "CALL":
                return [
                    _contract(480, 24_161, delta=0.61),   # deep ITM — never a wall
                    _contract(485, 3_871),
                    _contract(490, 9_330),
                    _contract(500, 47_262),
                    _contract(510, 17_676),
                ]
            return [
                _contract(500, 6_593, delta=-1.0),        # ITM put above spot
                _contract(480, 8_442, delta=-0.4),
                _contract(475, 5_441, delta=-0.2),
                _contract(460, 13_840, delta=-0.02),
                _contract(450, 12_694, delta=-0.02),
            ]

        state._option_chain_contracts = contracts  # type: ignore[method-assign]
        return state

    def test_oi_tags_are_side_correct_by_spot(self) -> None:
        rows = self._state()._oi_finder_selected_expiry_chain_rows({"underlyingPrice": 482.28})
        self.assertTrue(rows)
        calls_tagged = {row["strike"] for row in rows if row["side"] == "CALL" and row["is_high_open_interest"]}
        puts_tagged = {row["strike"] for row in rows if row["side"] == "PUT" and row["is_high_open_interest"]}
        # The 480 wall belongs to the PUT side (8.4K); the call's 24K is
        # positioning history and must not glow.
        self.assertNotIn(480.0, calls_tagged)
        self.assertIn(480.0, puts_tagged)
        # An ITM put above spot never tags either.
        self.assertNotIn(500.0, puts_tagged)
        self.assertIn(500.0, calls_tagged)
        self.assertEqual(calls_tagged, {485.0, 490.0, 500.0, 510.0})

    def test_high_volume_tags_are_unaffected(self) -> None:
        rows = self._state()._oi_finder_selected_expiry_chain_rows({"underlyingPrice": 482.28})
        # Volume is flow, not walls: the ITM call still tags on volume when it
        # leads (all fixtures share vol=10, so the top-3 cut keeps three).
        self.assertTrue(any(row["is_high_volume"] for row in rows))


if __name__ == "__main__":
    unittest.main()
