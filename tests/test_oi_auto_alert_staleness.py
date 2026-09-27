"""Alerts must arm on levels that were actually BUILT today - not on a stamp.

The first version of this guard (6d5d925) trusted `sessionDate`. That field is
rewritten to today by `reset_row_for_new_session` at the ET midnight roll, which
KEEPS the previous levels and leaves an unavailable row's status alone
(oi_auto_alerts.py:459-474). So after any failed morning build a row reads
sessionDate=today over yesterday's walls.

Measured on the live server 2026-08-27 20:15 ET: eight Mag7 rows held
levelsUpdatedAt=2026-08-26T08:15 with sessionDate=2026-08-27. The guard armed
every one of them on the PREVIOUS day's ladder - converting "alerts silently
off" into the worse "alerts silently firing on stale levels".

These pin the corrected rule and the card that reports it.
"""
from __future__ import annotations

import unittest

from api_server import (
    _oi_auto_alert_built_on,
    _oi_auto_alert_levels_state,
    _oi_auto_alert_should_evaluate,
)

TODAY = "2026-08-27"
# Central offset, exactly as the server writes it; 08:15 CDT == 09:15 ET.
TODAY_BUILD = "2026-08-27T08:15:01.901599-05:00"
YESTERDAY_BUILD = "2026-08-26T08:15:01.901599-05:00"


def row(**over):
    base = {
        "symbol": "MSFT",
        "status": "armed",
        "sessionDate": TODAY,
        "levelsUpdatedAt": TODAY_BUILD,
        "callLevels": [{"strike": 500.0}],
        "putLevels": [{"strike": 480.0}],
    }
    base.update(over)
    return base


class LevelsState(unittest.TestCase):
    def test_levels_built_today_are_ok(self):
        self.assertEqual(_oi_auto_alert_levels_state(row(), TODAY), "ok")

    def test_the_regression_yesterdays_levels_under_todays_sessiondate(self):
        # The exact production shape at 20:15 ET on 2026-08-27. The previous
        # guard returned True here and armed eight tickers on Aug-26 walls.
        stale = row(status="unavailable", sessionDate=TODAY,
                    levelsUpdatedAt=YESTERDAY_BUILD)
        self.assertEqual(_oi_auto_alert_levels_state(stale, TODAY), "stale")
        self.assertFalse(_oi_auto_alert_should_evaluate(stale, TODAY))

    def test_session_date_alone_can_never_arm_a_row(self):
        # sessionDate says today, levels say yesterday: the stamp must lose.
        self.assertFalse(_oi_auto_alert_should_evaluate(
            row(sessionDate=TODAY, levelsUpdatedAt=YESTERDAY_BUILD), TODAY))

    def test_todays_levels_arm_even_when_a_later_refresh_failed(self):
        # Tonight's other half: a resolved failure must not disarm good levels.
        recovered = row(status="unavailable", levelsUpdatedAt=TODAY_BUILD,
                        message='Tradier request failed (HTTP 401): {"fault":...')
        self.assertTrue(_oi_auto_alert_should_evaluate(recovered, TODAY))

    def test_a_row_with_no_levels_is_none_not_stale(self):
        # Distinct on purpose: "never built" must keep its actionable error
        # rather than be described by the age of levels that do not exist.
        self.assertEqual(
            _oi_auto_alert_levels_state(row(callLevels=[], putLevels=[]), TODAY), "none")

    def test_missing_timestamp_fails_closed(self):
        # A wrong "not armed" costs a rebuild; a wrong "armed" fires a signal
        # off stale walls. Fail towards the cheap mistake.
        self.assertEqual(_oi_auto_alert_levels_state(row(levelsUpdatedAt=""), TODAY), "stale")

    def test_unparseable_timestamp_fails_closed(self):
        self.assertEqual(
            _oi_auto_alert_levels_state(row(levelsUpdatedAt="not a date"), TODAY), "stale")

    def test_naive_timestamp_is_read_as_eastern_not_utc(self):
        # Reading a naive 09:15 stamp as UTC would shift it to 05:15 ET and, on
        # the day boundary, silently misdate the build.
        self.assertEqual(
            _oi_auto_alert_levels_state(row(levelsUpdatedAt="2026-08-27T09:15:00"), TODAY), "ok")

    def test_an_after_hours_rebuild_does_not_arm_the_next_morning(self):
        # AAPL was rebuilt 2026-08-27 20:11 ET. After the midnight roll the
        # row reads sessionDate 2026-08-28, but its walls are still Aug-27's,
        # so it must wait for the 09:15 build.
        after_hours = row(symbol="AAPL", status="armed", sessionDate="2026-08-28",
                          levelsUpdatedAt="2026-08-27T19:11:32.779829-05:00")
        self.assertFalse(_oi_auto_alert_should_evaluate(after_hours, "2026-08-28"))
        self.assertTrue(_oi_auto_alert_should_evaluate(after_hours, "2026-08-27"))

    def test_empty_row_is_none(self):
        self.assertEqual(_oi_auto_alert_levels_state({}, TODAY), "none")


class BuiltOn(unittest.TestCase):
    def test_it_names_the_eastern_date(self):
        self.assertEqual(_oi_auto_alert_built_on(row(levelsUpdatedAt=YESTERDAY_BUILD)),
                         "Wed Aug 26")

    def test_it_is_blank_when_unknown_so_the_card_can_say_so(self):
        self.assertEqual(_oi_auto_alert_built_on(row(levelsUpdatedAt="")), "")
        self.assertEqual(_oi_auto_alert_built_on(row(levelsUpdatedAt="rubbish")), "")



class PanelStatus(unittest.TestCase):
    """The badge must not say ARMED over nine cards that are not."""

    def setUp(self):
        from api_server import _oi_auto_alert_panel_status
        self.status = _oi_auto_alert_panel_status

    def test_the_regression_armed_over_nothing_armed(self):
        # 00:00-09:15: the roll stamps "Armed" because a new session began,
        # while every ladder waits for the 9:15 rebuild.
        rows = [{"symbol": "AAPL", "armedToday": False},
                {"symbol": "MSFT", "armedToday": False}]
        self.assertEqual(self.status("Armed", rows, False), "Levels pending")

    def test_armed_stands_when_something_really_is_armed(self):
        rows = [{"symbol": "AAPL", "armedToday": True},
                {"symbol": "MSFT", "armedToday": False}]
        self.assertEqual(self.status("Armed", rows, False), "Armed")

    def test_other_states_are_never_overridden(self):
        rows = [{"symbol": "AAPL", "armedToday": False}]
        for stored in ("Building ladders", "Monitoring", "Partial", "Paused"):
            self.assertEqual(self.status(stored, rows, False), stored)

    def test_a_build_in_flight_keeps_its_own_wording(self):
        rows = [{"symbol": "AAPL", "armedToday": False}]
        self.assertEqual(self.status("Armed", rows, True), "Armed")

    def test_no_rows_at_all_is_left_alone(self):
        # Nothing to contradict, so nothing to correct.
        self.assertEqual(self.status("Armed", [], False), "Armed")

if __name__ == "__main__":
    unittest.main()
