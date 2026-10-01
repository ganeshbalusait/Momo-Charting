"""The morning brief must reach his phone TWICE: early, and again at 09:00.

He asked for "Both - 5:30 + 09:15 or 9:00am and 5:30am EST".

The bug this pins: the push memory was keyed by DAY, so the 05:30 build
claimed the day's only push and every later rebuild was silenced. The 09:00
brief -- built on three more hours of premarket tape, half an hour before the
open, and therefore the more actionable of the two -- never went out. Nothing
looked broken; the push simply never happened.

These tests drive the real _build_morning_briefing with its expensive parts
stubbed, because the slot decision lives inside it and a test of a helper in
isolation would not prove the push actually fires.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import api_server

ET = ZoneInfo("America/New_York")


@pytest.fixture
def rig(monkeypatch):
    """A DashboardState whose brief build is entirely local, plus a push log."""
    state = object.__new__(api_server.DashboardState)
    state._morning_briefing_payload = None
    pushes = []

    monkeypatch.setattr(
        api_server, "_push_phone_notification",
        lambda title, body, **k: pushes.append({"title": title, "body": body}),
    )
    monkeypatch.setattr(
        api_server.DashboardState, "premarket_scanner_payload", lambda self: {}
    )
    monkeypatch.setattr(
        api_server.DashboardState, "_record_morning_movers",
        lambda self, *a, **k: None,
    )
    monkeypatch.setattr(api_server, "_live_quote_client", lambda profile: _Quotes())
    monkeypatch.setattr(api_server, "news_credentials", lambda: None)
    monkeypatch.setattr(
        api_server, "morning_build_briefing",
        lambda scanner, quotes, now_et, catalysts: {
            "lines": ["**Tape** is firm", "**AAPL** breaking out", "third line"]
        },
    )
    monkeypatch.setattr(
        api_server, "premarket_history_record_briefing", lambda lines, now: None
    )
    return state, pushes


class _Quotes:
    def get_quotes(self, symbols, **k):
        return {"SPY": {"change_pct": 0.1, "last_price": 500.0}}


def at(hour, minute):
    return datetime(2026, 9, 2, hour, minute, tzinfo=ET)


def test_the_early_build_pushes_once(rig):
    state, pushes = rig
    state._build_morning_briefing(at(5, 30))
    assert len(pushes) == 1
    assert "Morning Brief" in pushes[0]["title"]


def test_repeated_early_builds_do_not_spam_the_phone(rig):
    state, pushes = rig
    for minute in (30, 35, 40, 45):
        state._build_morning_briefing(at(5, minute))
    assert len(pushes) == 1, "the 5-minute rebuild loop pushed more than once"


def test_the_0900_build_pushes_AGAIN(rig):
    """The regression. Before the fix this second push never happened."""
    state, pushes = rig
    state._build_morning_briefing(at(5, 30))
    state._build_morning_briefing(at(9, 0))
    assert len(pushes) == 2, "the 09:00 brief did not reach the phone"


def test_the_two_pushes_are_titled_differently(rig):
    # Two identical titles on a phone read as a duplicate worth ignoring, and
    # the second brief is materially fresher, not a repeat.
    state, pushes = rig
    state._build_morning_briefing(at(5, 30))
    state._build_morning_briefing(at(9, 5))
    assert pushes[0]["title"] != pushes[1]["title"]


def test_the_late_slot_also_pushes_only_once(rig):
    state, pushes = rig
    state._build_morning_briefing(at(5, 30))
    for minute in (0, 5, 10, 30):
        state._build_morning_briefing(at(9, minute))
    assert len(pushes) == 2


def test_the_slot_boundary_is_0900(rig):
    # 08:55 is still the early slot, so it must NOT open the late one.
    state, pushes = rig
    state._build_morning_briefing(at(5, 30))
    state._build_morning_briefing(at(8, 55))
    assert len(pushes) == 1
    state._build_morning_briefing(at(9, 0))
    assert len(pushes) == 2


def test_a_new_day_pushes_again(rig):
    state, pushes = rig
    state._build_morning_briefing(at(5, 30))
    state._build_morning_briefing(at(9, 0))
    later = datetime(2026, 9, 3, 5, 30, tzinfo=ET)
    state._build_morning_briefing(later)
    assert len(pushes) == 3, "a fresh day must start its slots over"


def test_a_brief_with_no_lines_pushes_nothing(rig, monkeypatch):
    state, pushes = rig
    monkeypatch.setattr(
        api_server, "morning_build_briefing",
        lambda scanner, quotes, now_et, catalysts: {"lines": []},
    )
    state._build_morning_briefing(at(5, 30))
    assert pushes == [], "an empty brief must not buzz his phone"


def test_a_failing_push_never_breaks_the_briefing(rig, monkeypatch):
    state, pushes = rig

    def boom(*a, **k):
        raise OSError("ntfy down")

    monkeypatch.setattr(api_server, "_push_phone_notification", boom)
    state._build_morning_briefing(at(5, 30))
    # The briefing itself must still be published for the app to read.
    assert state._morning_briefing_payload["status"] == "READY"
