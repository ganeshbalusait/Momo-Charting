"""The $3 floor must gate on the same price the row displays.

His filter is "Stock Last >= $3.00" and thinkorswim reads the live last trade.
The port fed price_floor() the newest DAILY close instead, while the row
DISPLAYED the newest intraday close -- two different numbers about the same
stock on the same line.

Premarket, and for the whole session until today's daily bar exists, the daily
close is YESTERDAY's. So a stock that closed at 2.60 and is ramping at 4.20
before the bell was blocked on 2.60 while the board would have shown 4.20:
precisely the mover this scanner exists to find, dropped without a trace.
(The block is not even visible as an error -- it is one entry in a reasons
list nobody reads.)

These tests pin the SOURCE of the price, not just the threshold, because the
threshold was never wrong.
"""

from __future__ import annotations

from momx import board, columns


def tapes(intraday_close=None, daily_close=None, *, minutes=5):
    out = {}
    if intraday_close is not None:
        out["%dm" % minutes] = [
            {"time": 0, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": intraday_close, "volume": 100.0},
        ]
    if daily_close is not None:
        out["D"] = [{"time": 0, "open": 1.0, "high": 1.0, "low": 1.0,
                     "close": daily_close, "volume": 100.0}]
    return out


# ---------------------------------------------------------------------------
# last_price: one definition, used by both the gate and the display
# ---------------------------------------------------------------------------

def test_last_price_is_the_newest_intraday_close_not_the_daily_one():
    """The premarket ramp this was losing."""
    assert columns.last_price(tapes(intraday_close=4.20, daily_close=2.60)) == 4.20


def test_last_price_falls_back_to_the_daily_close_with_no_intraday_tape():
    # _finest_intraday's preference chain ENDS at "D", so a halted or newly
    # listed symbol still yields a price rather than None. That matters: an
    # absent price must not become a free pass through the $3 floor.
    assert columns.last_price(tapes(daily_close=2.60)) == 2.60


def test_last_price_is_None_only_when_there_is_no_tape_at_all():
    assert columns.last_price({}) is None


def test_last_price_skips_a_non_finite_close():
    broken = {"5m": [
        {"time": 0, "close": float("nan"), "high": 1, "low": 1, "open": 1, "volume": 1},
        {"time": 300, "close": 7.5, "high": 1, "low": 1, "open": 1, "volume": 1},
    ]}
    assert columns.last_price(broken) == 7.5


def test_last_price_on_junk_is_None_not_an_exception():
    for junk in (None, {}, {"5m": []}, {"5m": "nonsense"}, 7):
        assert columns.last_price(junk) is None


# ---------------------------------------------------------------------------
# the gate itself
# ---------------------------------------------------------------------------

def scan_last(intraday_close, daily_close):
    """The `last` the scanner gate actually receives, via the real pass-1 path."""
    captured = {}
    original = board.scan.scan_symbol

    def spy(tapes_arg, last, direction="bull"):
        captured["last"] = last
        return original(tapes_arg, last, direction)

    board.scan.scan_symbol = spy
    try:
        built = tapes(intraday_close, daily_close)
        # _scan_one takes raw feed frames; feed the already-built tapes through
        # the same code path by patching the builder for this call.
        original_build = board.build_tapes
        board.build_tapes = lambda *a, **k: built
        try:
            board._scan_one(("TEST", None, None, None, board.TWO_HOUR_ANCHOR, None))
        finally:
            board.build_tapes = original_build
    finally:
        board.scan.scan_symbol = original
    return captured.get("last")


def test_the_gate_sees_the_intraday_price_not_yesterdays_close():
    """The regression. Before the fix this returned 2.60 and blocked the row."""
    assert scan_last(4.20, 2.60) == 4.20


def test_the_gate_still_has_a_price_when_only_a_daily_bar_exists():
    # Halted, newly listed, or a failed intraday fetch. A missing price must
    # not become a free pass through the floor, so the daily close is used.
    assert scan_last(None, 2.60) == 2.60


def test_a_symbol_below_three_intraday_is_still_blocked():
    # The fix must not become "let everything through": a stock that has FALLEN
    # under $3 today is correctly blocked even though yesterday it closed above.
    assert scan_last(2.40, 9.00) == 2.40
    assert columns.last_price(tapes(2.40, 9.00)) == 2.40
