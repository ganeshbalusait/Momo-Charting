"""A failed refresh must not silently disarm a ticker for the rest of the day.

2026-08-27: the Mag7 OI ladders were built successfully at 09:15. A later
refresh hit Tradier's expired token, which stamped every Mag7 row
`status="unavailable"` while - by design - KEEPING the sixteen levels it had
already built. The evaluation loop skipped on that status, so the alerts
stopped firing for the whole session. On the phone the cards still showed the
walls and the live price, so nothing said the alerts were off; the only clue
was a wrapped block of broker JSON.

These pin both halves: which rows get evaluated, and what the trader is told.
"""
from __future__ import annotations

import unittest

from api_server import (
    _oi_auto_alert_plain_error,
    _oi_auto_alert_should_evaluate,
)

TODAY = "2026-08-27"
YESTERDAY = "2026-08-26"
# What decides arming is when the ladder was BUILT, not the sessionDate stamp -
# see test_oi_auto_alert_staleness.py for why that distinction cost a day.
TODAY_BUILT = "2026-08-27T08:15:01-05:00"
YESTERDAY_BUILT = "2026-08-26T08:15:01-05:00"


def row(**over):
    base = {
        "symbol": "AAPL",
        "status": "armed",
        "sessionDate": TODAY,
        "levelsUpdatedAt": TODAY_BUILT,
        "callLevels": [{"strike": 310.0}],
        "putLevels": [{"strike": 300.0}],
    }
    base.update(over)
    return base


class ShouldEvaluate(unittest.TestCase):
    def test_healthy_row_is_evaluated(self):
        self.assertTrue(_oi_auto_alert_should_evaluate(row(), TODAY))

    def test_the_regression_todays_levels_survive_a_failed_refresh(self):
        # The exact production shape: good levels, today's date, but the last
        # refresh threw. This returned False and cost a full session of alerts.
        failed = row(status="unavailable", message="Tradier request failed (HTTP 401)")
        self.assertTrue(_oi_auto_alert_should_evaluate(failed, TODAY))

    def test_stale_levels_from_a_previous_day_are_still_skipped(self):
        # What the status check was really standing in for - now said directly,
        # and read off the BUILD time rather than the sessionDate stamp.
        stale = row(status="unavailable", sessionDate=YESTERDAY,
                    levelsUpdatedAt=YESTERDAY_BUILT)
        self.assertFalse(_oi_auto_alert_should_evaluate(stale, TODAY))

    def test_a_row_that_cannot_prove_its_build_date_is_skipped(self):
        # Fails closed: a row that cannot prove its levels are from today does
        # not get to fire on them, whatever its status says.
        self.assertFalse(
            _oi_auto_alert_should_evaluate(row(status="unavailable", levelsUpdatedAt=""), TODAY))
        self.assertFalse(
            _oi_auto_alert_should_evaluate(row(status="armed", levelsUpdatedAt=""), TODAY))

    def test_row_without_levels_is_skipped(self):
        self.assertFalse(
            _oi_auto_alert_should_evaluate(row(callLevels=[], putLevels=[]), TODAY))

    def test_one_side_of_levels_is_enough(self):
        self.assertTrue(_oi_auto_alert_should_evaluate(row(putLevels=[]), TODAY))
        self.assertTrue(_oi_auto_alert_should_evaluate(row(callLevels=[]), TODAY))

    def test_empty_row_is_skipped(self):
        self.assertFalse(_oi_auto_alert_should_evaluate({}, TODAY))

    def test_a_healthy_looking_row_on_yesterdays_levels_is_NOT_evaluated(self):
        # CORRECTED 2026-08-27 20:40. This assertion used to be the opposite,
        # on the reasoning that "only the unavailable path consults the date,
        # so this cannot disarm anything". That was the bug: the midnight roll
        # stamps status="armed" and sessionDate=today onto rows that still hold
        # yesterday's walls, so a row can look perfectly healthy and be stale.
        # The build time is the only honest witness.
        self.assertFalse(_oi_auto_alert_should_evaluate(
            row(status="armed", sessionDate=TODAY,
                levelsUpdatedAt=YESTERDAY_BUILT), TODAY))


class PlainError(unittest.TestCase):
    def test_tradier_fault_json_becomes_a_sentence(self):
        raw = ('Tradier request failed (HTTP 401): {"fault":{"faultstring":'
               '"Access Token not approved","detail":{"errorcode":'
               '"keymanagement.service.access_token_not_approved"}}}')
        said = _oi_auto_alert_plain_error(raw)
        self.assertIn("Tradier", said)
        self.assertIn("renew it in Settings", said)
        self.assertNotIn("{", said)
        self.assertNotIn("faultstring", said)

    def test_schwab_invalid_client_is_named_for_its_cause(self):
        said = _oi_auto_alert_plain_error("invalid_client: Unauthorized")
        self.assertIn("Schwab", said)
        self.assertIn("Settings", said)

    def test_it_says_the_levels_are_still_armed_when_they_are(self):
        # The single most useful fact on the card, and the one the raw JSON
        # buried completely.
        said = _oi_auto_alert_plain_error("invalid_client: Unauthorized", kept=True)
        self.assertIn("still armed", said)

    def test_it_does_not_claim_armed_levels_when_there_are_none(self):
        said = _oi_auto_alert_plain_error("invalid_client: Unauthorized", kept=False)
        self.assertNotIn("still armed", said)

    def test_an_unfamiliar_failure_keeps_its_words_but_stays_one_line(self):
        said = _oi_auto_alert_plain_error("Something odd\nsecond line\nthird line")
        self.assertEqual(said, "Something odd")

    def test_a_very_long_unfamiliar_failure_is_trimmed(self):
        said = _oi_auto_alert_plain_error("x" * 500)
        self.assertLessEqual(len(said), 160)

    def test_an_empty_failure_still_says_something(self):
        self.assertTrue(_oi_auto_alert_plain_error("").strip())


if __name__ == "__main__":
    unittest.main()
