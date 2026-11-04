"""Unit: what /api/morning-briefing hands the Premarket page, and WHEN the
server builds the brief at all (2026-11-04 coverage).

The brief is the trader's daily driver. Two things bit before:
  * a backend restart after 09:35 wiped the in-memory brief and the card
    silently vanished for the rest of the day (2026-08-27) - the archive
    fallback below is the fix, and these tests pin its three branches;
  * the build window (weekdays 05:30-09:35 ET) lives inside the background
    loop, so it is driven here through the loop itself with a fixed clock.
"""
from __future__ import annotations

from datetime import datetime
from functools import partial
from zoneinfo import ZoneInfo

import pytest

import api_server
import premarket_scanner_history as history

ET = ZoneInfo("America/New_York")
TODAY_0812 = datetime(2026, 11, 4, 8, 12, tzinfo=ET)   # a Wednesday


def _bare_state():
    """A DashboardState with no __init__: nothing starts, nothing is read."""
    return object.__new__(api_server.DashboardState)


class _FixedClock(datetime):
    """api_server's datetime with now() pinned (only the loop reads it here)."""

    pinned = TODAY_0812

    @classmethod
    def now(cls, tz=None):
        return cls.pinned.astimezone(tz) if tz else cls.pinned.replace(tzinfo=None)


# ----------------------------------------------------------------------
# morning_briefing_payload: memory, then today's archive, then WAITING
# ----------------------------------------------------------------------

def test_the_in_memory_brief_wins_over_the_archive(monkeypatch, tmp_path):
    history.record_briefing(["**OLD** archived line"], TODAY_0812, directory=tmp_path)
    monkeypatch.setattr(api_server, "premarket_history_load_briefing", partial(history.load_briefing, directory=tmp_path))
    state = _bare_state()
    live = {"status": "READY", "lines": ["**NVDA** +5.0% live"], "generatedAt": "2026-11-04T09:05:00-05:00"}
    state._morning_briefing_payload = live
    assert state.morning_briefing_payload() is live


def test_after_a_restart_todays_archive_is_served_as_ready_and_flagged(monkeypatch, tmp_path):
    lines = ["SPY +0.4% premarket", "Watchlist gainers: **NVDA** +5.0%."]
    history.record_briefing(lines, TODAY_0812, directory=tmp_path)
    asked = []

    def load(now_et):
        asked.append(now_et)
        return history.load_briefing(TODAY_0812, directory=tmp_path)

    monkeypatch.setattr(api_server, "premarket_history_load_briefing", load)
    state = _bare_state()
    state._morning_briefing_payload = None
    payload = state.morning_briefing_payload()
    assert payload == {
        "status": "READY",
        "archived": True,
        "lines": lines,
        "generatedAt": TODAY_0812.isoformat(),   # the ORIGINAL build time, not "now"
    }
    assert asked and asked[0].tzinfo is not None, "the archive must be looked up by the ET day"


def test_with_no_brief_and_no_archive_the_card_waits_with_the_window_message(monkeypatch, tmp_path):
    monkeypatch.setattr(api_server, "premarket_history_load_briefing", partial(history.load_briefing, directory=tmp_path))
    state = _bare_state()
    payload = state.morning_briefing_payload()
    assert payload == {
        "status": "WAITING",
        "lines": [],
        "message": "The briefing builds weekday mornings 5:30-9:35 AM ET.",
    }


def test_an_archive_read_that_throws_still_answers_waiting(monkeypatch):
    def broken(now_et):
        raise OSError("disk gone")

    monkeypatch.setattr(api_server, "premarket_history_load_briefing", broken)
    assert _bare_state().morning_briefing_payload()["status"] == "WAITING"


def test_an_archived_day_with_empty_lines_is_not_a_brief(monkeypatch):
    monkeypatch.setattr(api_server, "premarket_history_load_briefing", lambda now_et: {"lines": [], "generatedAt": "x"})
    assert _bare_state().morning_briefing_payload()["status"] == "WAITING"


# ----------------------------------------------------------------------
# The build window, driven through the real loop with a pinned clock
# ----------------------------------------------------------------------

class _StopLoop(Exception):
    pass


def _one_loop_pass(monkeypatch, when: datetime) -> tuple[list, list]:
    """Run _morning_briefing_loop for exactly one iteration at `when`."""
    built, slept = [], []
    monkeypatch.setattr(_FixedClock, "pinned", when)
    monkeypatch.setattr(api_server, "datetime", _FixedClock)

    def sleep(seconds):
        slept.append(seconds)
        raise _StopLoop

    monkeypatch.setattr(api_server, "time", type("T", (), {"sleep": staticmethod(sleep)}))
    state = _bare_state()
    state._build_morning_briefing = lambda now_et: built.append(now_et)
    with pytest.raises(_StopLoop):
        state._morning_briefing_loop()
    return built, slept


@pytest.mark.parametrize("hour,minute,builds", [
    (5, 29, False),    # one minute early
    (5, 30, True),     # the window opens
    (9, 0, True),      # the half-hour-to-the-open brief
    (9, 34, True),     # last minute in the window
    (9, 35, False),    # closed: the open has happened
    (16, 30, False),   # after the close (2026-08-27 incident time)
])
def test_weekday_build_window(monkeypatch, hour, minute, builds):
    when = datetime(2026, 11, 4, hour, minute, tzinfo=ET)   # Wednesday
    built, slept = _one_loop_pass(monkeypatch, when)
    assert bool(built) is builds
    if builds:
        assert built[0].hour == hour and built[0].minute == minute
        assert slept == [300.0], "a build waits 5 minutes before the next one"
    else:
        assert slept == [120.0], "outside the window the loop only checks again in 2 minutes"


@pytest.mark.parametrize("day", [7, 8])   # Saturday 2026-11-07, Sunday 2026-11-08
def test_weekends_never_build(monkeypatch, day):
    built, _slept = _one_loop_pass(monkeypatch, datetime(2026, 11, day, 8, 0, tzinfo=ET))
    assert built == []


def test_a_failed_build_keeps_the_loop_alive(monkeypatch):
    monkeypatch.setattr(_FixedClock, "pinned", datetime(2026, 11, 4, 7, 0, tzinfo=ET))
    monkeypatch.setattr(api_server, "datetime", _FixedClock)
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        raise _StopLoop

    monkeypatch.setattr(api_server, "time", type("T", (), {"sleep": staticmethod(sleep)}))
    state = _bare_state()

    def explode(now_et):
        raise RuntimeError("Schwab down")

    state._build_morning_briefing = explode
    with pytest.raises(_StopLoop):
        state._morning_briefing_loop()
    assert slept == [300.0], "the error is swallowed and the loop waits for the next build"
