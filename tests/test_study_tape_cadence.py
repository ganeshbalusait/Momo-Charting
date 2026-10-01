"""studyBars must be a 30-minute tape, whatever the provider actually sends.

Regression cover for the 2026-08-27 flood: _alpaca_fallback_chart_bars answered
a "30Min" request with ONE-minute bars, and the deep study pull pairs that
cadence with a 7300-day lookback, so studyBars arrived as twenty years of minute
data - AAPL 594,766 rows against an intended ~13,000, 97.9% of a 60 MB payload,
and 130 of 399 cached symbols (629 MB) written that way.
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import chart_aggregation  # noqa: E402
from chart_aggregation import normalize_study_tape  # noqa: E402

HALF_HOUR = 1800
MINUTE = 60
# A Monday 09:30 ET open, so folded buckets land on real session boundaries.
OPEN = 1787751000


def tape(count, step, start=OPEN, base=100.0):
    return [
        {
            "time": start + index * step,
            "open": base + index,
            "high": base + index + 1,
            "low": base + index - 1,
            "close": base + index + 0.5,
            "volume": 10,
        }
        for index in range(count)
    ]


class NormalizeStudyTapeTests(unittest.TestCase):
    def test_a_one_minute_tape_is_folded_to_thirty(self):
        # THE REGRESSION. 600 one-minute bars is ten hours; at 30 minutes that
        # is 20 buckets, not 600 rows.
        folded = normalize_study_tape(tape(600, MINUTE))
        self.assertLess(len(folded), 600)
        self.assertLessEqual(len(folded), 24)
        gaps = {b["time"] - a["time"] for a, b in zip(folded, folded[1:])}
        self.assertTrue(all(gap % HALF_HOUR == 0 for gap in gaps), gaps)

    def test_an_already_correct_tape_is_returned_unchanged(self):
        # This runs on every disk load, so a healthy tape must cost nothing and
        # must not be re-bucketed (which would shift bar times).
        original = tape(200, HALF_HOUR)
        self.assertEqual(
            [b["time"] for b in normalize_study_tape(original)],
            [b["time"] for b in original],
        )

    def test_folding_is_idempotent(self):
        once = normalize_study_tape(tape(600, MINUTE))
        self.assertEqual(
            [b["time"] for b in normalize_study_tape(once)],
            [b["time"] for b in once],
        )

    def test_a_coarser_archive_is_never_made_finer(self):
        # studyBars opens with a daily-spaced archive section. Re-bucketing a
        # daily tape onto a 30-minute grid would invent slots that never traded.
        daily = tape(120, 86400)
        self.assertEqual(len(normalize_study_tape(daily)), len(daily))

    def test_the_time_span_survives_folding(self):
        original = tape(600, MINUTE)
        folded = normalize_study_tape(original)
        self.assertEqual(folded[0]["time"] % HALF_HOUR, original[0]["time"] % HALF_HOUR)
        self.assertLessEqual(folded[-1]["time"], original[-1]["time"])
        self.assertGreaterEqual(folded[-1]["time"], original[-1]["time"] - HALF_HOUR)

    def test_ohlc_is_aggregated_not_sampled(self):
        rows = tape(60, MINUTE)
        folded = normalize_study_tape(rows)
        first = [r for r in rows if r["time"] < folded[1]["time"]] if len(folded) > 1 else rows
        self.assertEqual(folded[0]["open"], first[0]["open"])
        self.assertEqual(folded[0]["high"], max(r["high"] for r in first))
        self.assertEqual(folded[0]["low"], min(r["low"] for r in first))
        self.assertEqual(folded[0]["volume"], sum(r["volume"] for r in first))

    def test_empty_and_junk_are_survivable(self):
        self.assertEqual(normalize_study_tape([]), [])
        self.assertEqual(normalize_study_tape(None), [])

    def test_a_tape_too_short_to_have_a_cadence_is_never_emptied(self):
        # Regression: folding a lone bar dropped it entirely (the bucket
        # function rejects an out-of-range timestamp), which would turn a small
        # disk cache into an empty one on load. Caught by
        # test_chart_and_chain_browser_caches_survive_restart.
        for count in (1, 2):
            rows = tape(count, MINUTE, start=60)
            self.assertEqual(len(normalize_study_tape(rows)), count, count)
        self.assertEqual(len(normalize_study_tape([{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}])), 1)


class AlpacaTimeframeTests(unittest.TestCase):
    """The fetch must ASK for the cadence it was told to ask for."""

    def _requested_timeframe(self, timeframe):
        """Ask the fetch what cadence it requests, with the gate forced ON.

        Chart candles are Schwab-only now (CHART_BARS_USE_ALPACA_FALLBACK is
        False), so this path returns early in production. The parser still has
        to be correct: the flag exists to be flipped back, and shipping a
        dormant landmine that answers "30Min" with 1-minute bars is how this
        cost 629 MB of cache the first time.
        """
        import api_server

        captured = {}

        class FakeClient:
            def get_stock_bars(self, request):
                captured["timeframe"] = str(request.timeframe)
                return SimpleNamespace(df=None)

        state = api_server.DashboardState.__new__(api_server.DashboardState)
        state._owner_alpaca_chart_client = lambda: FakeClient()
        original = api_server.CHART_BARS_USE_ALPACA_FALLBACK
        api_server.CHART_BARS_USE_ALPACA_FALLBACK = True
        try:
            api_server.DashboardState._alpaca_fallback_chart_bars(state, "AAPL", timeframe, 30)
        finally:
            api_server.CHART_BARS_USE_ALPACA_FALLBACK = original
        return captured.get("timeframe")

    def test_alpaca_is_off_by_default_and_asks_the_provider_nothing(self):
        """Chart candles are Schwab/TOS only.

        Ganesh's instruction 2026-08-27, and the measurement behind it: the
        Alpaca key on file is the free IEX tier, which had MSTR at 126.72 and
        five hours stale while it traded at 137.91. A wrong candle is worse
        than a missing one - a gap tells the trader to go and look.
        """
        import api_server

        self.assertFalse(api_server.CHART_BARS_USE_ALPACA_FALLBACK)

        called = []

        class FakeClient:
            def get_stock_bars(self, request):  # pragma: no cover - must not run
                called.append(request)
                return SimpleNamespace(df=None)

        state = api_server.DashboardState.__new__(api_server.DashboardState)
        state._owner_alpaca_chart_client = lambda: called.append("client") or FakeClient()
        frame = api_server.DashboardState._alpaca_fallback_chart_bars(state, "AAPL", "1Min", 30)
        self.assertTrue(frame.empty)
        self.assertEqual(called, [], "the disabled fallback must not build a client or call out")

    def test_thirty_minutes_is_requested_as_thirty_minutes(self):
        # THE BUG: this used to come back "1Min", which is how a 7300-day deep
        # pull turned into twenty years of minute bars.
        self.assertEqual(self._requested_timeframe("30Min"), "30Min")

    def test_the_cadences_the_chart_actually_uses(self):
        self.assertEqual(self._requested_timeframe("1Min"), "1Min")
        self.assertEqual(self._requested_timeframe("5Min"), "5Min")
        self.assertEqual(self._requested_timeframe("1Day"), "1Day")

    def test_an_hour_is_not_silently_downgraded_to_a_minute(self):
        # Alpaca rejects a minute amount >= 60, and the old else-branch would
        # have answered this with 1Min - the same silent downgrade.
        self.assertEqual(self._requested_timeframe("1Hour"), "1Hour")
        self.assertEqual(self._requested_timeframe("60Min"), "1Hour")

    def test_an_unrecognised_string_does_not_become_twenty_years_of_minutes(self):
        # Whatever it resolves to, it must not be finer than the 30-minute
        # study contract by accident.
        self.assertEqual(self._requested_timeframe("15Min"), "15Min")


if __name__ == "__main__":
    unittest.main()
