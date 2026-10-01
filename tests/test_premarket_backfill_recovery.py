"""The premarket 04:00-07:00 backfill must be able to RECOVER.

Two sticky-failure bugs found 2026-09-01 while diagnosing "No data
04:00-07:00" on a morning when every ingredient actually worked: Tradier was
genuinely dead (unfunded account, API access revoked), but the Alpaca SIP
fallback that exists for exactly this case reported "returned nothing" even
though calling it directly with the saved Settings key returned 144 real
one-minute AAPL bars.

Both bugs are the same shape - a failure cached forever - and both are
invisible, because the thing they disable is itself the fallback nobody
watches until the primary dies.
"""
import time
import types

import pytest


class _Stub:
    """Minimal stand-in carrying only what the methods under test touch."""

    def __init__(self):
        self._owner_alpaca_client_cache = None
        self.resolve_calls = 0


def _client_getter(api_server):
    return api_server.DashboardState._owner_alpaca_chart_client


@pytest.fixture()
def api_server():
    import api_server as module
    return module


def test_a_transient_credential_failure_is_retried_not_permanent(api_server, monkeypatch):
    """The bug: `cache = client if client is not None else False`, read as
    `if cached is not None: return cached or None`. One failed resolve - a
    locked DB during boot, a vault hiccup - pinned the Alpaca fallback OFF for
    the entire life of the process. The premarket backfill then reported "the
    Alpaca SIP backup returned nothing" every morning until someone restarted,
    while the key in Settings was perfectly good.
    """
    state = api_server.DashboardState.__new__(api_server.DashboardState)
    state._owner_alpaca_client_cache = None

    attempts = {"n": 0}
    sentinel = object()

    def fake_resolve(self):
        attempts["n"] += 1
        # Fail the first time, succeed after - the transient case.
        return None if attempts["n"] == 1 else sentinel

    monkeypatch.setattr(api_server.DashboardState, "_resolve_owner_alpaca_client", fake_resolve, raising=False)

    assert api_server.DashboardState._owner_alpaca_chart_client(state) is None
    assert attempts["n"] == 1

    # Immediately after: still cached, no hammering of the DB/vault.
    assert api_server.DashboardState._owner_alpaca_chart_client(state) is None
    assert attempts["n"] == 1, "a failure must not be re-resolved on every call"

    # After the retry window: it tries again and recovers.
    state._owner_alpaca_client_cache_at = time.monotonic() - (
        api_server.OWNER_ALPACA_CLIENT_RETRY_SECONDS + 1
    )
    assert api_server.DashboardState._owner_alpaca_chart_client(state) is sentinel
    assert attempts["n"] == 2, "a failure must be retried once the window passes"


def test_a_successful_client_is_still_cached_indefinitely(api_server, monkeypatch):
    """The retry window applies to FAILURES only. Re-resolving a working
    client would put a SQLite read and a vault decrypt on the chart path."""
    state = api_server.DashboardState.__new__(api_server.DashboardState)
    state._owner_alpaca_client_cache = None
    calls = {"n": 0}
    sentinel = object()

    def fake_resolve(self):
        calls["n"] += 1
        return sentinel

    monkeypatch.setattr(api_server.DashboardState, "_resolve_owner_alpaca_client", fake_resolve, raising=False)
    for _ in range(5):
        assert api_server.DashboardState._owner_alpaca_chart_client(state) is sentinel
    assert calls["n"] == 1


def test_an_empty_backfill_is_not_cached_for_the_rest_of_the_day(api_server):
    """The second sticky failure. The backfill cached its result per
    (symbol, day, interval) and, after 07:10, reused ANY cached entry for the
    rest of the day - including an EMPTY one. So a single failed attempt at
    06:05, while Tradier was down and before the fallback recovered, served
    an empty premarket window until midnight even once the data was
    available. Empty results must expire; good ones may persist.
    """
    keep = api_server.premarket_backfill_cache_entry_is_usable
    now = 10_000.0
    ttl = api_server.PREMARKET_BACKFILL_EMPTY_RETRY_SECONDS

    class _Frame:
        def __init__(self, empty):
            self.empty = empty
        def __len__(self):
            return 0 if self.empty else 5

    good, bad = {"frame": _Frame(False), "at": now - 3600}, {"frame": _Frame(True), "at": now - 3600}

    # A good frame stays usable all day, even hours later.
    assert keep(good, now=now, hole_still_growing=False) is True
    # An empty one goes stale quickly, so the next poll retries.
    assert keep(bad, now=now, hole_still_growing=False) is False
    # ...but is not re-fetched on every single request either.
    fresh_bad = {"frame": _Frame(True), "at": now - (ttl / 2)}
    assert keep(fresh_bad, now=now, hole_still_growing=False) is True
    # A missing entry is never usable.
    assert keep(None, now=now, hole_still_growing=False) is False


def test_while_the_hole_is_still_growing_even_good_frames_expire(api_server):
    """Before 07:10 the window is still filling, so a frame captured at 06:05
    is incomplete by 06:30. That existing 60s rule must survive this change."""
    now = 10_000.0
    entry = {"frame": types.SimpleNamespace(empty=False), "at": now - 120}
    assert api_server.premarket_backfill_cache_entry_is_usable(
        entry, now=now, hole_still_growing=True
    ) is False
    entry_fresh = {"frame": types.SimpleNamespace(empty=False), "at": now - 5}
    assert api_server.premarket_backfill_cache_entry_is_usable(
        entry_fresh, now=now, hole_still_growing=True
    ) is True


def test_the_badge_travels_with_the_cached_fill(api_server):
    """A cached fill must carry the STATE it was fetched under.

    Found 2026-09-01 07:57 while answering "is the app healthy?": all nine
    scanner symbols had their 04:00-07:00 window filled (AAPL 148 bars, TSLA
    167, NVDA 166 - the Alpaca fallback working exactly as designed), and the
    app still displayed "Premarket 04:00-07:00 is unavailable... Check the
    Tradier access token in Settings."

    The state and the error string were written only on the FETCH path. Once a
    good frame was cached for the day, no fetch re-ran, so the failure text
    from the earlier attempt was never cleared - the data healed and the badge
    did not. That reads as an outage during premarket and could send the
    trader off to fund a broker account he does not need.

    Binding the state to the entry makes the two impossible to disagree: serve
    the frame, serve its verdict.
    """
    entry = {"frame": object(), "at": 1.0, "state": "backup", "error": "on the Alpaca backup"}
    assert api_server.premarket_backfill_state_of(entry) == ("backup", "on the Alpaca backup")

    # A clean fetch carries no complaint.
    assert api_server.premarket_backfill_state_of(
        {"frame": object(), "at": 1.0, "state": "", "error": ""}
    ) == ("", "")

    # A pre-existing cache entry from before this change has no state recorded.
    # It must degrade to "no complaint" rather than inventing an outage.
    assert api_server.premarket_backfill_state_of({"frame": object(), "at": 1.0}) == ("", "")
    assert api_server.premarket_backfill_state_of(None) == ("", "")
