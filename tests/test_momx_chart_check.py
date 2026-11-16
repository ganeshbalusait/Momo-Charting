"""The paper bots' 5m chart check (momx/chart_check.py, his rule 2026-11-10)."""
from __future__ import annotations

from momx import chart_check as cc


def chart(price=100.0, **levels):
    base = {"ema21": 99.0}
    base.update(levels)
    return {"referencePrice": price, "levels": base}


def test_calls_need_the_price_above_the_21_ema():
    assert cc.check("bull", chart(100.0, ema21=99.0)) is None
    assert cc.check("bull", chart(100.0, ema21=101.0)) == "price under the 21 EMA"
    assert cc.check("bull", chart(100.0, ema21=100.0)) == "price on the 21 EMA"
    assert cc.verdict("bull", chart(100.0, ema21=101.0)) == ("failed", "price under the 21 EMA")
    assert cc.verdict("bull", chart(100.0, ema21=99.0)) == ("passed", None)


def test_room_before_the_next_level_ahead():
    # R1 0.3% above: no room. 0.8% above: room. A level BEHIND the price never counts.
    assert cc.check("bull", chart(100.0, R1=100.30, S1=99.9)) == "R1 $100.30 is only 0.3% ahead"
    assert cc.check("bull", chart(100.0, R1=100.80, S1=99.9)) is None
    # The NEAREST level ahead decides, whichever it is.
    assert cc.check("bull", chart(100.0, R2=101.5, prevHigh=100.2)) == "Previous-day high $100.20 is only 0.2% ahead"
    assert cc.check("bull", chart(100.0, premarketHigh=100.46)) == "Premarket high $100.46 is only 0.46% ahead"
    # Nothing above at all: open room.
    assert cc.check("bull", chart(100.0, S1=98.0, P=99.5)) is None


def test_puts_are_the_mirror():
    assert cc.check("bear", chart(100.0, ema21=101.0)) is None
    assert cc.check("bear", chart(100.0, ema21=99.0)) == "price above the 21 EMA"
    assert cc.check("bear", chart(100.0, ema21=101.0, S1=99.7, R1=100.1)) == "S1 $99.70 is only 0.3% ahead"
    assert cc.check("bear", chart(100.0, ema21=101.0, S1=99.2, R1=100.1)) is None
    assert cc.check("bear", chart(100.0, ema21=101.0, prevLow=99.6, premarketLow=99.0)) == \
        "Previous-day low $99.60 is only 0.4% ahead"


def test_a_level_exactly_at_the_price_is_ahead_with_no_room():
    # Review 2026-11-10: the price sitting ON a level has 0% room, it is not "nothing ahead".
    assert cc.check("bull", chart(100.0, R1=100.0)) == "R1 $100.00 is only 0.0% ahead"
    assert cc.verdict("bull", chart(100.0, R1=100.0))[0] == "failed"
    assert cc.check("bear", chart(100.0, ema21=101.0, S1=100.0)) == "S1 $100.00 is only 0.0% ahead"
    assert cc.verdict("bear", chart(100.0, ema21=101.0, S1=100.0))[0] == "failed"


def test_a_failing_room_never_reads_as_the_half_percent_minimum():
    # Truncated, never rounded up: 0.499% is "0.49%", 0.46% is "0.46%".
    assert cc.check("bull", chart(100.0, R1=100.499)) == "R1 $100.50 is only 0.49% ahead"
    assert cc.check("bull", chart(100.0, R1=100.46)) == "R1 $100.46 is only 0.46% ahead"
    assert cc.check("bear", chart(100.0, ema21=101.0, S1=99.501)) == "S1 $99.50 is only 0.49% ahead"
    assert cc.check("bull", chart(100.0, R1=100.5)) is None                 # exactly 0.5% is enough room
    for cents in range(0, 50):
        why = cc.check("bull", chart(100.0, R1=100.0 + cents / 100 + 0.009))
        assert why is not None and "0.5%" not in why and "0.50%" not in why


def test_missing_chart_never_blocks_and_says_not_checked():
    for bad in (None, {}, {"referencePrice": 100.0}, {"referencePrice": None, "levels": {"ema21": 99}},
                {"referencePrice": 100.0, "levels": {"ema21": None}}, chart(float("nan")), "x"):
        assert cc.check("bull", bad) is None and cc.check("bear", bad) is None
        assert cc.checkable(bad) is False
        assert cc.verdict("bull", bad) == ("not checked", None)
    # A bad level is ignored, not treated as "no room".
    assert cc.check("bull", chart(100.0, R1=None, R2="x", P=True)) is None
