"""The premarket scanner must not answer READY over a hole it cannot see.

Measured 2026-08-27 by direct probe of the running server, AAPL 5-minute tape:
8,987 bars, newest 21:44 EDT, first bar of today 00:00 ET - and exactly ZERO
bars in the 04:00-07:00 band. The Alpaca BOATS overnight feed runs 20:00-04:00
and stops AT the band, not through it; Tradier, the only feed that carries
04:00-07:00, is refused.

The existing freshness test compares DATES only. It therefore sees today-dated
bars from the 00:00-04:00 overnight stretch and reports every symbol READY,
while the window the scanner actually reads is empty - and the panel says
"N of 9 live tickers match in this premarket window".

The test below pins the session-relative rule that replaces it. The key
property is that it is NOT a wall-clock age test: the same two-hour-old tape is
broken at 06:00 and completely normal at 21:44.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from api_server import premarket_tape_reaches_window

ET = ZoneInfo("America/New_York")


def at(hour, minute=0):
    return datetime(2026, 8, 28, hour, minute, tzinfo=ET)


class TapeReachesWindow(unittest.TestCase):
    def test_a_current_tape_inside_the_window_is_fine(self):
        now = at(6, 30)
        self.assertTrue(premarket_tape_reaches_window(now - timedelta(minutes=2), now))

    def test_THE_REGRESSION_a_0400_tape_read_at_0600_does_not_reach(self):
        # The exact production shape: BOATS stopped at 04:00, the trader opens
        # the scanner at 06:00, and the date test says READY.
        now = at(6, 0)
        self.assertFalse(premarket_tape_reaches_window(at(4, 0), now))

    def test_the_same_lag_is_NOT_a_problem_in_the_evening(self):
        # THE POINT, and the reason this is not an age-in-seconds test. A
        # two-hour-old tape at 21:44 is normal; at 06:00 it is a hole.
        evening = datetime(2026, 8, 27, 21, 44, tzinfo=ET)
        self.assertTrue(premarket_tape_reaches_window(evening - timedelta(hours=2), evening))

    def test_the_window_edges(self):
        # 04:00 in, 09:30 out - the scanner's own window.
        self.assertFalse(premarket_tape_reaches_window(at(1, 0), at(4, 0)))
        self.assertTrue(premarket_tape_reaches_window(at(6, 0), at(9, 30)))
        self.assertFalse(premarket_tape_reaches_window(at(6, 0), at(9, 29)))

    def test_just_before_0400_is_not_judged(self):
        # Overnight the tape legitimately lags; judging it there would flag
        # every symbol all night for no benefit.
        self.assertTrue(premarket_tape_reaches_window(at(1, 0), at(3, 59)))

    def test_a_missing_newest_bar_never_claims_coverage(self):
        self.assertFalse(premarket_tape_reaches_window(None, at(6, 0)))

    def test_the_threshold_is_configurable_and_inclusive(self):
        now = at(7, 0)
        self.assertTrue(premarket_tape_reaches_window(now - timedelta(minutes=15), now))
        self.assertFalse(premarket_tape_reaches_window(now - timedelta(minutes=16), now))

    def test_a_bar_stamped_slightly_ahead_is_not_penalised(self):
        # Clock skew between the stream and the server must not read as a hole.
        now = at(6, 0)
        self.assertTrue(premarket_tape_reaches_window(now + timedelta(seconds=30), now))


if __name__ == "__main__":
    unittest.main()
