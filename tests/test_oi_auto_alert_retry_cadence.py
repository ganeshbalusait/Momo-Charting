from __future__ import annotations

"""A rebuild that fails must keep trying, slowly, for the rest of the session.

Measured 2026-08-27. The 09:15 build failed while both option-chain providers
were refused. It retried six times at 120s, gave up after twelve minutes, and
nothing tried again for twenty-four hours. Meanwhile the providers recovered -
proved by a manual rebuild at 19:48 that armed all nine tickers first time.

So the ladders sat on the previous day's open interest for a full session, and
the only thing that fixed it was the owner pressing refresh on each card and
then asking why it was still broken.

Twelve minutes is the wrong horizon for a dependency that is down for hours.
The fix is not more fast retries - hammering a refused credential achieves
nothing - it is to keep a slow heartbeat going so that recovery is noticed.
"""

from datetime import timedelta

import pytest

from oi_auto_alerts import retry_delay_for_attempt


def test_the_first_failures_are_retried_quickly():
    """A transient blip should be caught within minutes, not quarter-hours."""
    for attempt in range(6):
        assert retry_delay_for_attempt(attempt) == timedelta(seconds=120)


def test_after_the_fast_burst_it_keeps_trying_slowly():
    """This is the whole point.

    Previously attempt 6 returned nothing and the symbol was abandoned until
    the next morning's 09:15. A provider that comes back an hour later was
    never noticed.
    """
    assert retry_delay_for_attempt(6) == timedelta(minutes=15)
    assert retry_delay_for_attempt(20) == timedelta(minutes=15)


def test_it_does_eventually_stop():
    """Not infinite: a credential refused all day is a human problem.

    The cadence covers a full trading session and then rests, so a genuinely
    dead provider is not called forever.
    """
    assert retry_delay_for_attempt(200) is None


def test_the_slow_cadence_covers_a_whole_session():
    """From a 09:15 failure to the 16:00 close is 6h45m.

    If the schedule ran out before the close, a provider that recovered at
    lunchtime would still be missed - which is the exact failure being fixed.
    """
    total = timedelta()
    attempt = 0
    while True:
        delay = retry_delay_for_attempt(attempt)
        if delay is None:
            break
        total += delay
        attempt += 1

    assert total >= timedelta(hours=7), f"only covers {total}"


@pytest.mark.parametrize("junk", [-1, None, "3"])
def test_a_nonsense_attempt_count_does_not_raise(junk):
    # This runs inside the alert scheduler loop; an exception there stops
    # every ladder, which is far worse than a missed retry.
    assert retry_delay_for_attempt(junk) in (timedelta(seconds=120), timedelta(minutes=15), None)
