"""The gate in front of Schwab, tested against what actually happened.

Every case here is taken from the 2026-09-04 01:05-02:15 ET outage or from
Schwab's published limits, not invented:

  * the edge refusal is the literal Akamai page the server returned
  * 120 requests/minute and "back off 60s on a 429" are Schwab's documented
    numbers
  * the recovery order (401 unauthenticated -> 429 with a key -> 200) is what
    was measured on the way out, and is why a success must clear a cooldown
"""
from __future__ import annotations

import pytest

from data.schwab_rate_limit import (
    SchwabGate,
    SchwabUnavailable,
    looks_like_edge_block,
)

# The exact body Schwab's edge returned on every path, including the bare host
# root and the OAuth endpoint.
AKAMAI_BODY = (
    "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n"
    "<H1>Access Denied</H1>\n \nYou don't have permission to access "
    "\"http&#58;&#47;&#47;api&#46;schwabapi&#46;com&#47;marketdata&#47;v1&#47;quotes&#63;\" "
    "on this server.<P>\nReference&#32;&#35;18&#46;983b2f17&#46;1788501265&#46;3ecb85c3\n"
    "<P>https&#58;&#47;&#47;errors&#46;edgesuite&#46;net&#47;18&#46;983b2f17&#46;1788501265&#46;3ecb85c3</P>\n"
    "</BODY></HTML>"
)


class FakeClock:
    """Time under test control - no sleeping in the suite."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += float(seconds)


def gate(**kwargs) -> tuple[SchwabGate, FakeClock]:
    clock = FakeClock()
    return SchwabGate(clock=clock, **kwargs), clock


# -- telling an edge block apart from a real Schwab answer -------------------
def test_akamai_page_is_recognised_as_an_edge_block():
    assert looks_like_edge_block(403, AKAMAI_BODY) is True


def test_a_real_schwab_403_is_not_an_edge_block():
    """A JSON 403 is Schwab talking about entitlement - a different problem.

    Conflating them is what sends a trader to re-authenticate during a block,
    which cannot work: the OAuth endpoint is behind the same edge.
    """
    assert looks_like_edge_block(403, '{"errors":[{"status":"403"}]}') is False


def test_a_200_is_never_an_edge_block_whatever_the_body_says():
    assert looks_like_edge_block(200, "access denied appears in this text") is False


# -- the rate ceiling --------------------------------------------------------
def test_requests_are_allowed_up_to_the_ceiling_then_refused():
    g, clock = gate(max_per_minute=5, burst=5)
    for _ in range(5):
        g.acquire(wait=0.0)
    with pytest.raises(SchwabUnavailable) as caught:
        g.acquire(wait=0.0)
    assert caught.value.kind == "throttled"


def test_the_window_slides_so_the_ceiling_is_a_rate_not_a_quota():
    """Sixty seconds on, the earlier requests no longer count against us."""
    g, clock = gate(max_per_minute=5, burst=5)
    for _ in range(5):
        g.acquire(wait=0.0)
    clock.advance(61.0)
    g.acquire(wait=0.0)  # must not raise


def test_refusing_does_not_send_anything():
    """The point of the gate: a refusal costs nothing on the wire."""
    g, _ = gate(max_per_minute=1, burst=1)
    g.acquire(wait=0.0)
    before = g.state()["requestsSent"]
    with pytest.raises(SchwabUnavailable):
        g.acquire(wait=0.0)
    assert g.state()["requestsSent"] == before


# -- 429, the warning shot ---------------------------------------------------
def test_a_429_stops_further_requests_immediately():
    g, clock = gate()
    g.note_rate_limited()
    with pytest.raises(SchwabUnavailable) as caught:
        g.acquire(wait=0.0)
    assert caught.value.kind == "rate_limit"
    clock.advance(61.0)
    g.acquire(wait=0.0)  # cooldown served; allowed again


def test_a_longer_retry_after_is_honoured_and_never_shortened():
    g, clock = gate()
    g.note_rate_limited(retry_after=120.0)
    clock.advance(61.0)
    with pytest.raises(SchwabUnavailable):
        g.acquire(wait=0.0)


# -- the edge block, which is worse than a rate limit ------------------------
def test_an_edge_block_pauses_much_longer_than_a_429():
    """Because requests sent during a block cannot succeed and plausibly
    extend it - the 2026-09-03 block lifted only after the flooding stopped."""
    g, clock = gate()
    g.note_edge_block()
    clock.advance(61.0)  # a 429's worth of waiting is not enough
    with pytest.raises(SchwabUnavailable) as caught:
        g.acquire(wait=0.0)
    assert caught.value.kind == "blocked"


def test_repeated_blocks_escalate_the_pause():
    g, clock = gate()
    g.note_edge_block()
    clock.advance(301.0)
    g.note_edge_block()
    clock.advance(301.0)
    with pytest.raises(SchwabUnavailable):
        g.acquire(wait=0.0)


def test_a_success_clears_the_cooldown_so_the_app_heals_itself():
    """Tonight's block lifted on its own. Nothing should need a restart."""
    g, clock = gate()
    g.note_edge_block()
    g.note_success()
    g.acquire(wait=0.0)  # must not raise
    assert g.state()["coolingDown"] is False


# -- what the status endpoint and the lamp will read -------------------------
def test_state_reports_unreachable_while_blocked():
    g, _ = gate()
    g.note_edge_block()
    state = g.state()
    assert state["reachable"] is False
    assert state["coolingDown"] is True
    assert state["cooldownKind"] == "blocked"
    assert "not a login problem" in state["cooldownDetail"]


def test_state_reports_unreachable_after_a_failure_with_no_later_success():
    """The exact shape of tonight's outage: calls failing, token perfectly
    valid. A status derived from the token said healthy; this must not."""
    g, clock = gate()
    g.note_success()
    clock.advance(1.0)
    g.note_failure("http_403", "Access Denied")
    assert g.state()["reachable"] is False


def test_state_distinguishes_never_observed_from_observed_good():
    g, _ = gate()
    assert g.state()["observed"] is False
    g.note_success()
    assert g.state()["observed"] is True
    assert g.state()["reachable"] is True


def test_state_counts_the_recent_request_rate():
    g, clock = gate(max_per_minute=10, burst=10)
    for _ in range(3):
        g.acquire(wait=0.0)
    assert g.state()["requestsLastMinute"] == 3
    clock.advance(61.0)
    assert g.state()["requestsLastMinute"] == 0


def test_an_isolated_429_costs_a_blink_not_a_minute():
    """A single 429 must not freeze every chart on screen for 60 seconds.

    That would trade a provider's rate limit for an outage of our own, during
    the session, which is the worse of the two.
    """
    g, clock = gate()
    g.note_rate_limited()
    clock.advance(6.0)
    g.acquire(wait=0.0)  # first strike is 5s - already served


def test_consecutive_429s_escalate_to_schwabs_full_sixty_seconds():
    g, clock = gate()
    g.note_rate_limited()   # 5s
    g.note_rate_limited()   # 15s
    g.note_rate_limited()   # 60s
    clock.advance(30.0)
    with pytest.raises(SchwabUnavailable) as caught:
        g.acquire(wait=0.0)
    assert caught.value.kind == "rate_limit"
    clock.advance(31.0)
    g.acquire(wait=0.0)


def test_a_success_resets_the_ladder_so_brushing_the_limit_stays_cheap():
    """Occasionally touching the limit and hammering through it are different
    behaviours and must not accumulate into the same penalty."""
    g, clock = gate()
    g.note_rate_limited()
    g.note_rate_limited()
    g.note_success()
    g.note_rate_limited()   # back to the first rung
    clock.advance(6.0)
    g.acquire(wait=0.0)


def test_the_ceiling_is_a_runaway_backstop_not_the_documented_limit():
    """Documents the deliberate gap: MomX measurably runs 459 req/min, so a
    120/min gate would starve the volume behind RVOL. The gate measures and
    reacts to Schwab's own pushback instead. Lowering this follows the MomX
    fix - this test is here so that change is a conscious one."""
    from data.schwab_rate_limit import MAX_REQUESTS_PER_MINUTE
    assert MAX_REQUESTS_PER_MINUTE > 459, "would throttle the MomX volume sweep"


def test_duplicate_candles_from_schwab_are_collapsed():
    """Schwab returns every candle TWICE.

    Measured 2026-09-04 on the raw response: AAPL 5Min over four hours came
    back with 310 candles carrying 155 distinct datetimes, each bar repeated
    with identical OHLC and volume. Two rows with the same timestamp is the
    input that makes a chart series throw, and any consumer that sums rows was
    working on double.
    """
    from data.schwab_client import SchwabClient
    candle = {"datetime": 1788500000000, "open": 1.0, "high": 2.0,
              "low": 0.5, "close": 1.5, "volume": 100}
    later = dict(candle, datetime=1788500300000, volume=200)
    frame = SchwabClient.__new__(SchwabClient)
    frame._tz = __import__("zoneinfo").ZoneInfo("America/New_York")
    out = SchwabClient._normalize_candles(frame, [candle, candle, later, later])
    assert len(out) == 2, "each duplicated pair must collapse to one bar"
    assert list(out["volume"]) == [100.0, 200.0], "volume must not be summed or altered"
