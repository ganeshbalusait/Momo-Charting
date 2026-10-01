"""The keeper must not carry overnight rest into a live session.

Rest is earned when a touch fails to move a symbol's newest bar. Overnight that
happens to EVERY symbol - nothing trades - so without a reset the whole list
drifts to the 24-cycle cap and a symbol could wait two hours past 09:30 for its
first touch. That is the exact window the keeper exists to protect.
"""
import datetime

from zoneinfo import ZoneInfo

from scripts.agx_keeper import starts_new_session

ET = ZoneInfo("America/New_York")


def at(month, day, hour, minute=0):
    return datetime.datetime(2026, month, day, hour, minute, tzinfo=ET)


def test_first_cycle_of_a_trading_day_clears():
    # Thursday 04:00 - premarket, nothing cleared yet today.
    assert starts_new_session(at(8, 27, 4, 0), None) is True


def test_premarket_is_covered_not_just_the_open():
    # Ganesh trades the 06:00-09:30 premarket; warming must not wait for 09:30.
    assert starts_new_session(at(8, 27, 6, 30), datetime.date(2026, 8, 26)) is True


def test_same_day_does_not_clear_twice():
    assert starts_new_session(at(8, 27, 10, 0), datetime.date(2026, 8, 27)) is False


def test_post_close_does_not_re_clear():
    # 16:00-20:00 is when rest legitimately accrues; re-clearing here would
    # restore the every-cycle rebuild the backoff was added to stop.
    assert starts_new_session(at(8, 27, 18, 0), datetime.date(2026, 8, 27)) is False


def test_overnight_does_not_clear():
    # 22:00 with the day already cleared: rest keeps accruing, as intended.
    assert starts_new_session(at(8, 27, 22, 0), datetime.date(2026, 8, 27)) is False


def test_after_midnight_still_does_not_clear_before_0400():
    # The date has rolled but the session has not started; clearing here would
    # hand the overnight hours a full rebuild sweep for no benefit.
    assert starts_new_session(at(8, 28, 1, 0), datetime.date(2026, 8, 27)) is False


def test_the_0400_boundary_is_inclusive():
    assert starts_new_session(at(8, 28, 3, 59), datetime.date(2026, 8, 27)) is False
    assert starts_new_session(at(8, 28, 4, 0), datetime.date(2026, 8, 27)) is True


def test_weekend_never_clears():
    # Saturday and Sunday: no session to protect, so rest should persist and
    # keep the weekend quiet.
    assert starts_new_session(at(8, 29, 10, 0), datetime.date(2026, 8, 28)) is False
    assert starts_new_session(at(8, 30, 10, 0), datetime.date(2026, 8, 28)) is False


def test_monday_after_a_weekend_of_rest_clears():
    assert starts_new_session(at(8, 31, 4, 0), datetime.date(2026, 8, 28)) is True
