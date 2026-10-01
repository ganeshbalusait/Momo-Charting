"""A background client must not be able to impersonate the trader.

The chart engine already has priority machinery that works: ChartBuildLane
serves a symbol someone is watching ahead of an abandoned kick, and
touch_oi_finder_interactive_window() pauses background scanning for 45s
whenever "a trader-facing payload" is requested.

Both read the same signal - that a chart was requested over HTTP - which was a
true proxy for "a human is looking" when it was written. It stopped being true
when scripts/agx_keeper.py began touching up to 120 symbols per cycle through
the very same endpoint. Measured 2026-08-28: ~50 of 399 tapes current, so the
keeper touches ~100 symbols every cycle, each one resetting the 45s "human is
watching" pause and claiming interactive priority in the build lane against the
chart actually on screen.

These tests pin the distinction the priority code needs and never had.
"""
import threading

import pytest

from request_context import (
    BACKGROUND_HEADER,
    background_scope,
    is_background_request,
    in_background_request,
)


class Headers(dict):
    """Stands in for http.client.HTTPMessage, which is .get()-shaped."""


def test_a_plain_browser_request_is_not_background():
    assert is_background_request(Headers()) is False
    assert is_background_request(Headers({"User-Agent": "Mozilla"})) is False


def test_the_keeper_marks_itself():
    assert is_background_request(Headers({BACKGROUND_HEADER: "1"})) is True


def test_header_matching_ignores_case():
    # http.server lowercases nothing reliably and proxies rewrite case freely.
    assert is_background_request(Headers({BACKGROUND_HEADER.lower(): "1"})) is True
    assert is_background_request(Headers({BACKGROUND_HEADER.upper(): "1"})) is True


def test_only_affirmative_values_count():
    # A header that arrives empty or negated must read as foreground: the
    # failure that matters is a REAL trader being demoted to background, which
    # would make his own chart wait behind the keeper's.
    for value in ("", "0", "false", "no", None):
        assert is_background_request(Headers({BACKGROUND_HEADER: value})) is False
    for value in ("1", "true", "yes", "TRUE"):
        assert is_background_request(Headers({BACKGROUND_HEADER: value})) is True


def test_headers_that_explode_are_treated_as_foreground():
    class Hostile:
        def get(self, *_args, **_kwargs):
            raise RuntimeError("nope")

    # Failing closed here means "treat as a real trader", which is the safe
    # direction: worst case we do too much work, never too little.
    assert is_background_request(Hostile()) is False


def test_nothing_is_background_by_default():
    assert in_background_request() is False


def test_the_scope_marks_and_then_clears():
    with background_scope(True):
        assert in_background_request() is True
    assert in_background_request() is False


def test_the_scope_clears_even_when_the_request_raises():
    # http.server reuses worker threads. A scope leaked by an exception would
    # silently demote whichever real request landed on that thread next.
    with pytest.raises(ValueError):
        with background_scope(True):
            raise ValueError("boom")
    assert in_background_request() is False


def test_a_foreground_scope_inside_a_background_one_restores_it():
    with background_scope(True):
        with background_scope(False):
            assert in_background_request() is False
        assert in_background_request() is True


def test_one_thread_cannot_mark_another():
    # The keeper's request and the trader's are served on different threads.
    seen = {}
    def worker():
        seen["value"] = in_background_request()

    with background_scope(True):
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

    assert seen["value"] is False, "background must not leak across threads"
