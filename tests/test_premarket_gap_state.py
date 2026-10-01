"""The badge can only tell backup from outage if the server says which it is.

Source-read rather than imported: importing api_server boots every scheduler
and opens the live database. code_only() strips prose first, because every one
of these branches is explained in a comment naming the very strings asserted on.
"""
from pathlib import Path

from handler_source import code_only

API = code_only((Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8"))


def test_the_backup_state_is_set_where_the_fill_succeeded():
    assert '_premarket_backfill_state = "backup"' in API


def test_the_missing_state_is_set_where_the_fill_came_back_empty():
    assert '_premarket_backfill_state = "missing"' in API


def test_a_healthy_morning_clears_the_state():
    # Without this a single bad morning would leave the badge up all day.
    assert '_premarket_backfill_state = ""' in API


def test_every_payload_that_sends_the_note_also_sends_the_state():
    # The frontend falls back to the loud "FEED DOWN" label when it gets a note
    # with no state. That fallback is correct for an older server, but if a
    # CURRENT payload omitted the state the badge would overstate the problem
    # forever - silently, since nothing errors.
    notes = API.count("premarketGapNote")
    states = API.count("premarketGapState")
    assert notes == states, (
        f"{notes} payload sites send the note but {states} send the state; "
        "a site that sends one without the other cannot be labelled correctly"
    )
