"""Bulk watchlist bar feed (momx/feed.py).

Every test here stubs the HTTP getter and the credential list - nothing in
this file may touch the network or the credential vault.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from momx import feed


NOW = datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)  # 11:00 ET, market open

GOOD = feed.FeedCredential("vault", "good-key", "good-secret")


class FakeResponse:
    def __init__(self, payload, status_code=200, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        return self._payload


def _bar(stamp: str, close: float = 1.0) -> dict:
    return {"t": stamp, "o": 1.0, "h": 2.0, "l": 0.5, "c": close, "v": 100}


class FakeAlpaca:
    """Records every request and answers from a per-symbol bar table."""

    def __init__(self, bars_by_symbol: dict[str, list[dict]] | None = None):
        self.bars_by_symbol = bars_by_symbol or {}
        self.calls: list[dict] = []

    def get(self, url, params=None, headers=None, timeout=None):
        params = params or {}
        requested = [s for s in str(params.get("symbols", "")).split(",") if s]
        self.calls.append(
            {
                "url": url,
                "symbols": tuple(requested),
                "feed": params.get("feed"),
                "timeframe": params.get("timeframe"),
                "page_token": params.get("page_token"),
                "key": (headers or {}).get("APCA-API-KEY-ID"),
                "params": params,
            }
        )
        bars = {
            symbol: list(self.bars_by_symbol[symbol])
            for symbol in requested
            if symbol in self.bars_by_symbol
        }
        return FakeResponse({"bars": bars, "next_page_token": None})


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    """No shared cache, no real sleeps, no vault lookup."""
    feed.clear_cache()
    monkeypatch.setattr(feed, "_sleep", lambda _seconds: None)
    monkeypatch.setattr(feed, "_vault_credentials", lambda: [])
    yield
    feed.clear_cache()


# ---------------------------------------------------------------------------
# batching
# ---------------------------------------------------------------------------


def test_symbols_are_chunked_at_the_batch_size_and_every_chunk_is_requested():
    symbols = [f"SYM{index}" for index in range(7)]
    fake = FakeAlpaca({symbol: [_bar("2026-08-27T14:00:00Z")] for symbol in symbols})

    bars, errors = feed.fetch_daily(
        symbols, years=1, get=fake.get, credentials=[GOOD], now=NOW,
        batch_size=3, max_workers=1,
    )

    assert errors == {}
    assert set(bars) == set(symbols)
    # 7 symbols / 3 per batch = 3 requests, and every symbol appears exactly once.
    assert [call["symbols"] for call in fake.calls] == [
        ("SYM0", "SYM1", "SYM2"),
        ("SYM3", "SYM4", "SYM5"),
        ("SYM6",),
    ]


def test_intraday_batches_ask_both_extended_hours_feeds():
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})

    bars, _ = feed.fetch_5m(
        ["AAPL"], days=2, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert set(bars) == {"AAPL"}
    # sip carries 04:00-20:00 ET, boats carries the 20:00-04:00 overnight session,
    # and iex is the live tail that extends past SIP's ~20-min recency block
    # (UPDATED for the IEX live-tail merge; was {"sip", "boats"}).
    assert {call["feed"] for call in fake.calls} == {"sip", "boats", "iex"}
    assert {call["timeframe"] for call in fake.calls} == {"5Min"}


def test_daily_uses_the_explicit_day_timeframe_and_split_adjustment():
    """Split-adjusted, NOT raw. This is a regression guard, not a preference.

    Alpaca defaults to adjustment=raw, which leaves a stock split in the tape as
    a genuine price gap. Measured on CRWD (4:1 on 2026-07-02): raw showed
    07-01=772.74 -> 07-02=193.98. Its weekly Skittles then read 25 against TOS's
    71, because the 8-week stochastic window straddled the fake -75% bar, while
    the daily value -- whose window did not -- matched TOS exactly.

    "split" and not "all": thinkorswim is split-adjusted with dividend
    adjustment OFF by default, so "all" would reintroduce a smaller mismatch on
    every dividend payer.
    """
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-26T04:00:00Z")]})

    feed.fetch_daily(["AAPL"], years=3, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    assert [call["timeframe"] for call in fake.calls] == ["1Day"]
    assert fake.calls[0]["params"]["adjustment"] == "split"
    assert fake.calls[0]["feed"] == "sip"


def test_intraday_tapes_are_split_adjusted_too():
    """The 30m tape is ~30 days deep, so a split inside it corrupts 1h/2h/4h."""
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-26T14:00:00Z")]})

    feed.fetch_30m(["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    sip = [c for c in fake.calls if c["feed"] == "sip"]
    assert sip, "the SIP leg is required"
    assert all(c["params"]["adjustment"] == "split" for c in sip)


# ---------------------------------------------------------------------------
# pagination
# ---------------------------------------------------------------------------


def test_pagination_follows_next_page_token_until_it_is_exhausted():
    pages = [
        {"bars": {"AAPL": [_bar("2026-08-27T12:00:00Z", 1.0)]}, "next_page_token": "p2"},
        {"bars": {"AAPL": [_bar("2026-08-27T12:05:00Z", 2.0)]}, "next_page_token": "p3"},
        {"bars": {"AAPL": [_bar("2026-08-27T12:10:00Z", 3.0)]}, "next_page_token": None},
    ]
    tokens: list[object] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        # Only the sip leg is paginated here; boats and the iex live tail return
        # empty so the token sequence stays sip-only (UPDATED for the IEX
        # live-tail merge - iex was previously not a feed this module requested).
        if (params or {}).get("feed") in ("boats", "iex"):
            return FakeResponse({"bars": {}, "next_page_token": None})
        tokens.append((params or {}).get("page_token"))
        return FakeResponse(pages[len(tokens) - 1])

    bars, errors = feed.fetch_5m(
        ["AAPL"], days=2, get=fake_get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert tokens == [None, "p2", "p3"]
    assert errors == {}
    assert list(bars["AAPL"]["close"]) == [1.0, 2.0, 3.0]


# ---------------------------------------------------------------------------
# partial failure
# ---------------------------------------------------------------------------


def test_a_symbol_missing_from_the_response_lands_in_errors_and_the_rest_survive():
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})  # MSFT absent

    bars, errors = feed.fetch_daily(
        ["AAPL", "MSFT"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert set(bars) == {"AAPL"}
    assert set(errors) == {"MSFT"}
    assert not bars["AAPL"].empty


def test_a_failing_batch_reports_its_symbols_instead_of_raising():
    def fake_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({"message": "boom"}, status_code=500)

    bars, errors = feed.fetch_daily(
        ["AAPL", "MSFT"], years=1, get=fake_get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert bars == {}
    assert set(errors) == {"AAPL", "MSFT"}
    assert "500" in errors["AAPL"]


def test_the_overnight_feed_failing_never_fails_an_intraday_tape():
    def fake_get(url, params=None, headers=None, timeout=None):
        if (params or {}).get("feed") == "boats":
            return FakeResponse({"message": "no"}, status_code=500)
        return FakeResponse(
            {"bars": {"AAPL": [_bar("2026-08-27T12:00:00Z")]}, "next_page_token": None}
        )

    bars, errors = feed.fetch_30m(
        ["AAPL"], days=5, get=fake_get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert len(bars["AAPL"]) == 1


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------


def test_ttl_cache_skips_the_network_inside_the_window_and_refetches_after_it(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})

    first, _ = feed.fetch_daily(
        ["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    calls_after_first = len(fake.calls)
    assert calls_after_first == 1

    clock["now"] += feed.CACHE_TTL_SECONDS / 2
    second, _ = feed.fetch_daily(
        ["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    assert len(fake.calls) == calls_after_first  # served from cache
    assert second["AAPL"] is first["AAPL"]

    clock["now"] += feed.CACHE_TTL_SECONDS
    feed.fetch_daily(
        ["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    assert len(fake.calls) == calls_after_first + 1


def test_only_the_uncached_symbols_are_requested_on_a_second_call(monkeypatch):
    monkeypatch.setattr(feed, "_monotonic", lambda: 500.0)
    fake = FakeAlpaca(
        {
            "AAPL": [_bar("2026-08-27T12:00:00Z")],
            "MSFT": [_bar("2026-08-27T12:00:00Z")],
        }
    )

    feed.fetch_daily(["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)
    bars, _ = feed.fetch_daily(
        ["AAPL", "MSFT"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert set(bars) == {"AAPL", "MSFT"}
    assert [call["symbols"] for call in fake.calls] == [("AAPL",), ("MSFT",)]


def test_a_failed_symbol_is_not_cached_as_empty():
    fake = FakeAlpaca({})
    feed.fetch_daily(["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)
    fake.bars_by_symbol["AAPL"] = [_bar("2026-08-27T12:00:00Z")]
    bars, errors = feed.fetch_daily(
        ["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert set(bars) == {"AAPL"}


# ---------------------------------------------------------------------------
# symbol normalisation and frame shape
# ---------------------------------------------------------------------------


def test_symbols_are_uppercased_on_the_wire_and_in_the_result():
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")], "MSFT": [_bar("2026-08-27T12:00:00Z")]})

    bars, errors = feed.fetch_daily(
        [" aapl ", "Msft", "aapl", "", None], years=1,
        get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert set(bars) == {"AAPL", "MSFT"}
    assert errors == {}
    assert fake.calls[0]["symbols"] == ("AAPL", "MSFT")  # deduped, uppercased


def test_frames_use_the_house_columns_in_eastern_time():
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T13:30:00Z")]})

    bars, _ = feed.fetch_daily(
        ["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    frame = bars["AAPL"]
    assert list(frame.columns) == ["timestamp", "open", "high", "low", "close", "volume"]
    assert str(frame["timestamp"].iloc[0]) == "2026-08-27 09:30:00-04:00"


def test_an_empty_symbol_list_never_touches_the_network():
    fake = FakeAlpaca({})
    bars, errors = feed.fetch_5m([], get=fake.get, credentials=[GOOD], now=NOW)
    assert (bars, errors, fake.calls) == ({}, {}, [])


# ---------------------------------------------------------------------------
# credentials
# ---------------------------------------------------------------------------


class FakeProfile:
    def __init__(self, profile_id, key, secret):
        self.profile_id = profile_id
        self.key = key
        self.secret = secret


def test_the_dead_paper3_key_is_never_selected():
    candidates = [
        FakeProfile("paper3", "dead-key", "dead-secret"),
        FakeProfile("paper5", "live-key", "live-secret"),
    ]

    resolved = feed.resolve_credentials(candidates)

    assert [credential.profile_id for credential in resolved] == ["paper5"]
    assert all(credential.key != "dead-key" for credential in resolved)


def test_the_vault_key_is_tried_before_the_env_profiles(monkeypatch):
    monkeypatch.setattr(
        feed, "_vault_credentials", lambda: [feed.FeedCredential("vault", "vault-key", "vault-secret")]
    )
    resolved = feed.resolve_credentials([FakeProfile("paper5", "live-key", "live-secret")])
    assert [credential.profile_id for credential in resolved] == ["vault", "paper5"]


def test_a_credential_that_raises_falls_through_to_the_next_one():
    dead = feed.FeedCredential("paper4", "dead-key", "dead-secret")
    calls: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        key = (headers or {}).get("APCA-API-KEY-ID")
        calls.append(key)
        if key == "dead-key":
            raise ConnectionResetError(10054, "connection reset")
        return FakeResponse(
            {"bars": {"AAPL": [_bar("2026-08-27T12:00:00Z")]}, "next_page_token": None}
        )

    bars, errors = feed.fetch_daily(
        ["AAPL"], years=1, get=fake_get, credentials=[dead, GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert set(bars) == {"AAPL"}
    assert calls[0] == "dead-key" and calls[-1] == "good-key"


def test_a_401_retires_the_key_and_the_next_one_answers():
    dead = feed.FeedCredential("paper3-lookalike", "dead-key", "dead-secret")

    def fake_get(url, params=None, headers=None, timeout=None):
        if (headers or {}).get("APCA-API-KEY-ID") == "dead-key":
            return FakeResponse({"message": "forbidden"}, status_code=401)
        return FakeResponse(
            {"bars": {"AAPL": [_bar("2026-08-27T12:00:00Z")]}, "next_page_token": None}
        )

    bars, errors = feed.fetch_daily(
        ["AAPL"], years=1, get=fake_get, credentials=[dead, GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert set(bars) == {"AAPL"}


def test_no_credentials_reports_every_symbol_instead_of_raising():
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})
    bars, errors = feed.fetch_daily(["AAPL"], years=1, get=fake.get, credentials=[], now=NOW)
    assert bars == {}
    assert errors == {"AAPL": "no usable Alpaca credentials"}
    assert fake.calls == []


# ---------------------------------------------------------------------------
# performance guardrails
# ---------------------------------------------------------------------------


def test_the_worker_pool_is_capped_no_matter_what_the_caller_asks_for(monkeypatch):
    seen: list[int] = []
    real_pool = feed.ThreadPoolExecutor

    def spy(max_workers=None, **kwargs):
        seen.append(max_workers)
        return real_pool(max_workers=max_workers, **kwargs)

    monkeypatch.setattr(feed, "ThreadPoolExecutor", spy)
    symbols = [f"SYM{index}" for index in range(40)]
    fake = FakeAlpaca({symbol: [_bar("2026-08-27T12:00:00Z")] for symbol in symbols})

    feed.fetch_daily(
        symbols, years=1, get=fake.get, credentials=[GOOD], now=NOW,
        batch_size=1, max_workers=64,
    )

    assert seen and max(seen) <= feed.MAX_WORKERS == 4


def test_the_recent_window_is_clamped_off_the_end_of_every_request():
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})
    feed.fetch_5m(["AAPL"], days=2, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    ends = {call["feed"]: call["params"]["end"] for call in fake.calls}
    # sip 403s on the most recent ~20 minutes, boats on ~16.
    assert ends["sip"] == "2026-08-27T14:40:00Z"
    assert ends["boats"] == "2026-08-27T14:44:00Z"


# ---------------------------------------------------------------------------
# incremental store (tail fetches after the first full build)
# ---------------------------------------------------------------------------
#
# The tests below pin the behaviour that makes a refresh take seconds instead
# of minutes: the first build fetches full depth, later builds fetch only the
# tail behind the newest held bar and splice it on. FakeAlpaca ignores the
# requested window, which would let a "tail" fetch silently return the whole
# tape - so these tests use WindowedAlpaca, which honours start/end.

import pandas as pd


def _parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


class WindowedAlpaca(FakeAlpaca):
    """FakeAlpaca that only serves bars inside the requested [start, end]."""

    def get(self, url, params=None, headers=None, timeout=None):
        response = super().get(url, params=params, headers=headers, timeout=timeout)
        payload = response.json()
        start = _parse_stamp((params or {})["start"])
        end = _parse_stamp((params or {})["end"])
        payload["bars"] = {
            symbol: [
                row for row in rows if start <= _parse_stamp(row["t"]) <= end
            ]
            for symbol, rows in (payload.get("bars") or {}).items()
        }
        return FakeResponse(payload)


FULL_30M_START = "2026-07-28T04:00:00Z"   # NOW minus 30 days, ET midnight
FULL_1Y_START = "2025-08-27T04:00:00Z"    # NOW minus 365 days, ET midnight

EARLY_30M = [
    _bar("2026-08-27T12:00:00Z", 1.0),
    _bar("2026-08-27T12:30:00Z", 2.0),
    _bar("2026-08-27T13:00:00Z", 3.0),    # partial when first fetched
]
LATER_30M = [
    _bar("2026-08-27T12:00:00Z", 1.0),
    _bar("2026-08-27T12:30:00Z", 2.0),
    _bar("2026-08-27T13:00:00Z", 3.5),    # the completed restatement
    _bar("2026-08-27T13:30:00Z", 4.0),    # genuinely new
]


def _sip_calls(fake, since=0):
    return [call for call in fake.calls[since:] if call["feed"] == "sip"]


def _first_then_second_30m(monkeypatch):
    """First build on EARLY_30M, then a TTL-expired second build on LATER_30M."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    fake = WindowedAlpaca({"AAPL": list(EARLY_30M)})
    first, _ = feed.fetch_30m(
        ["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    fake.bars_by_symbol["AAPL"] = list(LATER_30M)
    clock["now"] += feed.CACHE_TTL_SECONDS + 1  # TTL lapsed, store still fresh
    calls_before = len(fake.calls)
    second, errors = feed.fetch_30m(
        ["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    return first, second, errors, fake, calls_before


def test_the_first_build_requests_full_depth():
    fake = WindowedAlpaca({"AAPL": list(EARLY_30M)})

    feed.fetch_30m(["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    assert _sip_calls(fake)[0]["params"]["start"] == FULL_30M_START


def test_the_second_build_fetches_only_the_tail_and_matches_a_full_fetch(monkeypatch):
    """THE PARITY TEST: incremental splicing must be invisible in the output."""
    _, second, errors, fake, calls_before = _first_then_second_30m(monkeypatch)

    assert errors == {}
    tail_calls = _sip_calls(fake, calls_before)
    assert len(tail_calls) == 1
    # newest held bar 13:00Z minus 2 x 30m bars minus the 20m sip recency
    # delay - NOT the 30-day full depth.
    assert tail_calls[0]["params"]["start"] == "2026-08-27T11:40:00Z"

    # A single full fetch of the SAME provider data, on a cold store.
    feed.clear_cache()
    fresh = WindowedAlpaca({"AAPL": list(LATER_30M)})
    full, _ = feed.fetch_30m(
        ["AAPL"], days=30, get=fresh.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    pd.testing.assert_frame_equal(second["AAPL"], full["AAPL"])


def test_the_previously_partial_bar_is_replaced_not_duplicated(monkeypatch):
    _, second, errors, _, _ = _first_then_second_30m(monkeypatch)

    assert errors == {}
    frame = second["AAPL"]
    assert list(frame["close"]) == [1.0, 2.0, 3.5, 4.0]  # 3.0 -> 3.5, no dup
    assert frame["timestamp"].is_unique
    assert frame["timestamp"].is_monotonic_increasing


def test_a_failed_tail_fetch_keeps_the_held_tape_without_erroring(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    fake = WindowedAlpaca({"AAPL": list(EARLY_30M)})
    first, _ = feed.fetch_30m(
        ["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    def broken_get(url, params=None, headers=None, timeout=None):
        return FakeResponse({"message": "boom"}, status_code=500)

    clock["now"] += feed.CACHE_TTL_SECONDS + 1
    second, errors = feed.fetch_30m(
        ["AAPL"], days=30, get=broken_get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}  # a stale tape beats a blank board
    assert second["AAPL"] is first["AAPL"]  # the held frame, untouched


def test_a_new_symbol_gets_full_depth_while_the_rest_get_tails(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    fake = WindowedAlpaca(
        {
            "AAPL": [_bar("2026-08-26T04:00:00Z", 5.0)],
            "MSFT": [_bar("2026-08-26T04:00:00Z", 6.0)],
        }
    )
    feed.fetch_daily(["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    clock["now"] += feed.CACHE_TTL_SECONDS + 1
    calls_before = len(fake.calls)
    bars, errors = feed.fetch_daily(
        ["AAPL", "MSFT"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert set(bars) == {"AAPL", "MSFT"}
    starts = {call["symbols"]: call["params"]["start"] for call in fake.calls[calls_before:]}
    assert starts[("MSFT",)] == FULL_1Y_START           # new symbol: full depth
    # held symbol: 2 daily bars + the 20m sip delay behind its newest bar
    assert starts[("AAPL",)] == "2026-08-24T03:40:00Z"


def test_a_store_older_than_the_full_rebuild_window_forces_a_full_fetch(monkeypatch):
    """THE SPLIT HEAL. adjustment=split restates ALL history after a split;
    appending new-adjustment tails to old-adjustment history would rebuild the
    CRWD 4:1 corruption (weekly Skittles 25 vs TOS 71). A store older than
    FULL_REBUILD_SECONDS must therefore be thrown away wholesale."""
    clock = {"now": 1000.0}
    wall = {"now": 50_000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    monkeypatch.setattr(feed, "_wall_clock", lambda: wall["now"])
    fake = WindowedAlpaca({"AAPL": list(EARLY_30M)})
    feed.fetch_30m(["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    clock["now"] += feed.CACHE_TTL_SECONDS + 1
    wall["now"] += feed.FULL_REBUILD_SECONDS + 1
    calls_before = len(fake.calls)
    feed.fetch_30m(["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    assert _sip_calls(fake, calls_before)[0]["params"]["start"] == FULL_30M_START


def test_clear_cache_drops_the_incremental_store():
    fake = WindowedAlpaca({"AAPL": list(EARLY_30M)})
    feed.fetch_30m(["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    feed.clear_cache()
    calls_before = len(fake.calls)
    feed.fetch_30m(["AAPL"], days=30, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    assert _sip_calls(fake, calls_before)[0]["params"]["start"] == FULL_30M_START


def test_symbols_idle_past_the_prune_window_are_dropped_from_the_store(monkeypatch):
    clock = {"now": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    fake = WindowedAlpaca(
        {
            "AAPL": [_bar("2026-08-26T04:00:00Z")],
            "MSFT": [_bar("2026-08-26T04:00:00Z")],
        }
    )
    feed.fetch_daily(
        ["AAPL", "MSFT"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    clock["now"] += feed.STORE_PRUNE_SECONDS + 1
    feed.fetch_daily(["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1)

    with feed._CACHE_LOCK:
        store = feed._STORE["daily"]
        assert "AAPL" in store.tapes      # requested this call: kept (tail path)
        assert "MSFT" not in store.tapes  # removed from the universe: dropped


def test_prune_horizon_survives_two_worst_case_builds():
    """Regression guard for the 2026-08-28 death spiral.

    The warmer idles as long as the last build took, so a list's touch-to-touch
    gap is TWO build times. With a 15-minute horizon, one slow build (>7.5 min -
    daytime full builds measured ~8 min, and the 12h split-heal forces one every
    day) evicted the whole store, made the next build full and slower (771s,
    then 866s), and the spiral locked in. The horizon must comfortably exceed
    2x the WORST build, not the typical one.
    """
    worst_observed_full_build = 900.0  # ~15 min, 866.7s measured + margin
    assert feed.STORE_PRUNE_SECONDS >= 2 * worst_observed_full_build


# ---------------------------------------------------------------------------
# IEX live-tail merge (intraday only)
# ---------------------------------------------------------------------------
#
# SIP is complete but 403s on bars newer than ~20 min; IEX has no such block
# but its volume is IEX-only (~6% of consolidated). So the intraday tapes fetch
# a short IEX tail after the SIP(+BOATS) legs and append the bars STRICTLY
# NEWER than the newest SIP bar. FakeAlpaca/WindowedAlpaca answer every feed
# identically, which cannot separate SIP from IEX, so these tests use
# FeedWindowedAlpaca, which keys bars on the requested feed AND honours the
# [start, end] window.


def _vbar(stamp: str, close: float = 1.0, volume: float = 100) -> dict:
    bar = _bar(stamp, close)
    bar["v"] = volume
    return bar


class FeedWindowedAlpaca(FakeAlpaca):
    """Serves a different bar table per feed, honouring the requested window."""

    def __init__(self, bars_by_feed: dict[str, dict[str, list[dict]]]):
        super().__init__({})
        self.bars_by_feed = {feed_name: dict(table) for feed_name, table in bars_by_feed.items()}

    def get(self, url, params=None, headers=None, timeout=None):
        feed_name = (params or {}).get("feed")
        self.bars_by_symbol = self.bars_by_feed.get(feed_name, {})
        response = super().get(url, params=params, headers=headers, timeout=timeout)
        payload = response.json()
        start = _parse_stamp((params or {})["start"])
        end = _parse_stamp((params or {})["end"])
        payload["bars"] = {
            symbol: [row for row in rows if start <= _parse_stamp(row["t"]) <= end]
            for symbol, rows in (payload.get("bars") or {}).items()
        }
        return FakeResponse(payload)


def _row_at_utc(frame, utc_iso: str):
    want = pd.Timestamp(utc_iso)
    stamps = frame["timestamp"].dt.tz_convert("UTC")
    match = frame[stamps == want]
    assert len(match) == 1, f"expected exactly one bar at {utc_iso}, got {len(match)}"
    return match.iloc[0]


def test_the_iex_tail_appends_only_bars_newer_than_sip_and_sip_wins_on_overlap():
    fake = FeedWindowedAlpaca(
        {
            "sip": {
                "AAPL": [
                    _vbar("2026-08-27T14:30:00Z", 1.0, 5000),
                    _vbar("2026-08-27T14:35:00Z", 2.0, 5000),
                    _vbar("2026-08-27T14:40:00Z", 3.0, 5000),  # newest SIP (clamp = 14:40)
                ]
            },
            "iex": {
                "AAPL": [
                    _vbar("2026-08-27T14:40:00Z", 99.0, 7),  # OVERLAP - must be ignored
                    _vbar("2026-08-27T14:45:00Z", 4.0, 7),   # newer - appended
                    _vbar("2026-08-27T14:50:00Z", 5.0, 7),   # newer - appended
                ]
            },
        }
    )

    bars, errors = feed.fetch_5m(
        ["AAPL"], days=2, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    frame = bars["AAPL"]
    # sip 14:30/14:35/14:40 then iex 14:45/14:50; the 14:40 overlap keeps SIP.
    assert list(frame["close"]) == [1.0, 2.0, 3.0, 4.0, 5.0]
    assert list(frame["feed"]) == ["sip", "sip", "sip", "iex", "iex"]
    # SIP volume on the overlap bar survived; the thin IEX copy (close 99) lost.
    assert list(frame["volume"]) == [5000.0, 5000.0, 5000.0, 7.0, 7.0]


def test_a_failed_or_empty_iex_tail_leaves_the_sip_tape_unchanged():
    sip_payload = {
        "bars": {"AAPL": [_bar("2026-08-27T12:00:00Z", 1.0)]},
        "next_page_token": None,
    }

    def make_get(iex_response):
        def fake_get(url, params=None, headers=None, timeout=None):
            feed_name = (params or {}).get("feed")
            if feed_name == "iex":
                return iex_response()
            if feed_name == "boats":
                return FakeResponse({"bars": {}, "next_page_token": None})
            return FakeResponse(dict(sip_payload))

        return fake_get

    for iex_response in (
        lambda: FakeResponse({"message": "boom"}, status_code=500),   # failed leg
        lambda: FakeResponse({"bars": {}, "next_page_token": None}),  # empty leg
    ):
        feed.clear_cache()
        bars, errors = feed.fetch_5m(
            ["AAPL"], days=2, get=make_get(iex_response), credentials=[GOOD],
            now=NOW, max_workers=1,
        )
        assert errors == {}  # a best-effort IEX leg never fails the board
        frame = bars["AAPL"]
        assert list(frame["close"]) == [1.0]
        assert "feed" not in frame.columns  # no IEX bars -> SIP frame untouched


def test_the_iex_tail_makes_the_newest_bar_fresher_than_sip_alone():
    fake = FeedWindowedAlpaca(
        {
            "sip": {
                "AAPL": [
                    _bar("2026-08-27T14:35:00Z", 1.0),
                    _bar("2026-08-27T14:40:00Z", 2.0),  # the freshest SIP can serve
                ]
            },
            "iex": {"AAPL": [_bar("2026-08-27T14:50:00Z", 3.0)]},  # 10 min past the block
        }
    )

    bars, _ = feed.fetch_5m(
        ["AAPL"], days=2, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    frame = bars["AAPL"]

    newest_utc = frame["timestamp"].iloc[-1].tz_convert("UTC")
    assert newest_utc > pd.Timestamp("2026-08-27T14:40:00Z")  # fresher than SIP alone
    assert newest_utc == pd.Timestamp("2026-08-27T14:50:00Z")
    assert frame["feed"].iloc[-1] == "iex"


def test_a_thin_iex_bar_is_upgraded_to_the_real_sip_bar_when_sip_catches_up(monkeypatch):
    """THE REPLACEMENT TEST. A thin IEX bar at T must not stick once SIP catches
    up: on the next build SIP's recency block has cleared for T, ``_splice_tail``
    drops the IEX copy and re-owns T from SIP (keep="last", the real volume
    wins), and the bar is upgraded iex -> sip."""
    clock = {"now": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["now"])
    # 14:45 exists in SIP with real volume but only clears the ~20-min block once
    # "now" advances past 15:05; IEX carries a thin copy of 14:45 immediately.
    sip_table = {
        "AAPL": [
            _vbar("2026-08-27T14:30:00Z", 1.0, 5000),
            _vbar("2026-08-27T14:35:00Z", 2.0, 5000),
            _vbar("2026-08-27T14:40:00Z", 3.0, 5000),
            _vbar("2026-08-27T14:45:00Z", 4.0, 5000),  # the REAL bar, full volume
        ]
    }
    iex_table = {
        "AAPL": [
            _vbar("2026-08-27T14:45:00Z", 4.0, 8),  # thin live copy of 14:45
            _vbar("2026-08-27T14:50:00Z", 5.0, 8),
            _vbar("2026-08-27T14:55:00Z", 6.0, 8),
        ]
    }
    fake = FeedWindowedAlpaca({"sip": sip_table, "iex": iex_table})

    # Build 1 at 15:00Z: SIP clamp is 14:40, so 14:45 is IEX-only (thin).
    now1 = datetime(2026, 8, 27, 15, 0, tzinfo=timezone.utc)
    first, _ = feed.fetch_5m(
        ["AAPL"], days=2, get=fake.get, credentials=[GOOD], now=now1, max_workers=1,
    )
    row_first = _row_at_utc(first["AAPL"], "2026-08-27T14:45:00Z")
    assert row_first["feed"] == "iex" and row_first["volume"] == 8.0

    # Build 2 at 15:05Z: TTL lapsed, store fresh; SIP clamp is now 14:45, so SIP
    # owns 14:45 with real volume and must REPLACE the thin IEX bar.
    clock["now"] += feed.CACHE_TTL_SECONDS + 1
    now2 = datetime(2026, 8, 27, 15, 5, tzinfo=timezone.utc)
    second, errors = feed.fetch_5m(
        ["AAPL"], days=2, get=fake.get, credentials=[GOOD], now=now2, max_workers=1,
    )

    assert errors == {}
    row_second = _row_at_utc(second["AAPL"], "2026-08-27T14:45:00Z")
    assert row_second["feed"] == "sip"     # upgraded iex -> sip
    assert row_second["volume"] == 5000.0  # the real volume won, not the thin 8
    # and the tail still extends past SIP's block via IEX.
    assert second["AAPL"]["feed"].iloc[-1] == "iex"


def test_the_daily_tape_never_asks_for_or_merges_the_iex_tail():
    fake = FeedWindowedAlpaca(
        {
            "sip": {"AAPL": [_bar("2026-08-26T04:00:00Z", 1.0)]},
            "iex": {"AAPL": [_bar("2026-08-27T14:50:00Z", 9.0)]},  # would look "fresher"
        }
    )

    bars, errors = feed.fetch_daily(
        ["AAPL"], years=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert all(call["feed"] != "iex" for call in fake.calls)  # daily is RTH-only
    frame = bars["AAPL"]
    assert "feed" not in frame.columns
    assert list(frame["close"]) == [1.0]


# ---------------------------------------------------------------------------
# schwab_volume=False -- the grading fetch's opt-out (fix round 2)
# ---------------------------------------------------------------------------
#
# The nightly/catch-up grading fetch (momx/service.py::_fetch_5m_bars) only
# reads OHLC and never reads volume, but it retries every unscored symbol
# every 15 minutes, all day, for up to CATCHUP_MAX_DAYS days. Left
# unconditional, the Schwab volume correction below would turn that into a
# sustained burst of uncached, one-call-per-symbol Schwab price-history
# requests -- this app's home IP has been blocked by Schwab's CDN before for
# far smaller bursts. These tests spy on ``_schwab_volume_map`` directly
# (not the SCHWAB_VOLUME_ENABLED gate, which conftest.py disables globally
# for the whole suite) so they prove the SKIP happens before that gate would
# even matter.


def test_schwab_volume_false_never_calls_the_correction(monkeypatch):
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})

    def boom(*args, **kwargs):
        raise AssertionError("_schwab_volume_map must not be called when schwab_volume=False")

    monkeypatch.setattr(feed, "_schwab_volume_map", boom)

    bars, errors = feed.fetch_5m(
        ["AAPL"], days=1, get=fake.get, credentials=[GOOD], now=NOW,
        max_workers=1, schwab_volume=False,
    )

    assert errors == {}
    assert set(bars) == {"AAPL"}


def test_schwab_volume_defaults_to_true_and_calls_the_correction(monkeypatch):
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})
    iex = FakeAlpaca({"AAPL": [_bar("2026-08-27T14:50:00Z")]})
    calls: list[tuple] = []

    def spy(symbols, timeframe, now, client=None, **kw):   # bars_out since 2026-09-29
        calls.append((tuple(symbols), timeframe))
        return {}   # never the network

    def get(url, params=None, headers=None, timeout=None):
        # 2026-09-30: the paced correction is only requested for a frame that
        # holds an IEX-marked (thin-volume) bar, so the IEX leg must give one.
        source = iex if (params or {}).get("feed") == "iex" else fake
        return source.get(url, params=params, headers=headers, timeout=timeout)

    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", True)
    monkeypatch.setattr(feed, "SCHWAB_ONLY", False)
    monkeypatch.setattr(feed, "_schwab_volume_map", spy)

    bars, errors = feed.fetch_5m(
        ["AAPL"], days=1, get=get, credentials=[GOOD], now=NOW, max_workers=1,
    )

    assert errors == {}
    assert calls and calls[0][0] == ("AAPL",)


def test_schwab_volume_false_is_forwarded_through_fetch_tape_kwarg(monkeypatch):
    # Belt-and-braces on the explicit keyword itself (not just the observable
    # behaviour above), since fetch_5m re-declares it rather than relying only
    # on **kwargs passthrough.
    seen = {}
    real_fetch_tape = feed._fetch_tape

    def spy(*args, **kwargs):
        seen["schwab_volume"] = kwargs.get("schwab_volume")
        return real_fetch_tape(*args, **kwargs)

    monkeypatch.setattr(feed, "_fetch_tape", spy)
    fake = FakeAlpaca({"AAPL": [_bar("2026-08-27T12:00:00Z")]})

    feed.fetch_5m(
        ["AAPL"], days=1, get=fake.get, credentials=[GOOD], now=NOW,
        max_workers=1, schwab_volume=False,
    )
    assert seen["schwab_volume"] is False

    feed.fetch_5m(
        ["AAPL"], days=1, get=fake.get, credentials=[GOOD], now=NOW, max_workers=1,
    )
    assert seen["schwab_volume"] is True
