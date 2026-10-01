"""momx/feed.py - the toolbar "TOS" checkbox (2026-09-30).

Ticked = "tos": every tape from Schwab only, RATE-PACED (the first Schwab-only
build left 124 symbols empty after a 429). Unticked = "alpaca": SIP/BOATS/IEX
with Schwab only for the IEX-tail volume (default lookback) - no overlay."""
import io
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from momx import feed, service

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 30, 23, 0, tzinfo=ET)

@pytest.fixture(autouse=True)
def _unlocked_switch(monkeypatch):
    """These tests exercise the switch machinery behind TOS_LOCKED."""
    monkeypatch.setattr(feed, "TOS_LOCKED", False)


def test_locked_scanner_is_tos_and_refuses_alpaca(monkeypatch, tmp_path):
    """His order 2026-10-01: TOS data permanently, no checkbox."""
    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(tmp_path / "source.json"))
    monkeypatch.setattr(feed, "TOS_LOCKED", True)
    assert feed.get_data_source() == "tos"
    with pytest.raises(ValueError):
        feed.set_data_source("alpaca")
    assert feed.set_data_source("tos") == "tos"



def bars(close=10.0, volume=1000.0):
    return pd.DataFrame({"timestamp": [pd.Timestamp(datetime(2026, 9, 30, 15, 30, tzinfo=ET))],
                         "open": close, "high": close, "low": close, "close": close,
                         "volume": volume})


class Clock:
    def __init__(self):
        self.mono = 1_000.0
        self.wall = 1_000_000.0

    def advance(self, seconds):
        self.mono += seconds
        self.wall += seconds


class Client:
    def __init__(self, empty=()):
        self.calls = []
        self.empty = set(empty)

    def _get_price_history(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe, start))
        return None if symbol in self.empty else bars()


@pytest.fixture
def tos(monkeypatch, tmp_path):
    """TOS mode with Schwab faked, a fake clock and a clean store/budget."""
    clock = Clock()
    client = Client()
    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(tmp_path / "source.json"))
    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", True)
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    monkeypatch.setattr(feed, "_schwab_client", lambda: client)
    monkeypatch.setattr(feed, "_monotonic", lambda: clock.mono)
    monkeypatch.setattr(feed, "_wall_clock", lambda: clock.wall)

    def no_alpaca(*args, **kwargs):
        raise AssertionError("Alpaca must not be called in TOS mode")

    monkeypatch.setattr(feed, "_fetch_batch", no_alpaca)
    monkeypatch.setattr(feed, "resolve_credentials", no_alpaca)
    feed.clear_cache()
    feed._tos_reset()
    yield clock, client
    feed.clear_cache()
    feed._tos_reset()


def alpaca_with_iex_tail(symbols, **kwargs):
    """Fake Alpaca: a settled SIP bar plus a newer IEX live-tail bar (thin volume)."""
    stamp = {"sip": "2026-09-30T19:30:00Z", "iex": "2026-10-01T02:30:00Z"}.get(kwargs["feed"])
    if stamp is None:
        return {}
    return {s: [{"t": stamp, "o": 1, "h": 1, "l": 1, "c": 1, "v": 5}] for s in symbols}


def asked(client, since=0):
    return sorted(symbol for symbol, _tf, _start in client.calls[since:])


# --- persistence --------------------------------------------------------------

def test_default_is_tos_and_a_bad_file_falls_back(monkeypatch, tmp_path):
    path = tmp_path / "source.json"
    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(path))
    assert feed.get_data_source() == "tos"                      # missing
    path.write_text("{not json", encoding="utf-8")
    assert feed.get_data_source() == "tos"                      # corrupt
    path.write_text(json.dumps({"source": "yahoo"}), encoding="utf-8")
    assert feed.get_data_source() == "tos"                      # unknown value


def test_round_trip_and_invalid_value(monkeypatch, tmp_path):
    path = tmp_path / "nested" / "source.json"
    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(path))
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    assert feed.set_data_source("ALPACA") == "alpaca"
    assert feed.get_data_source() == "alpaca" and feed.SCHWAB_ONLY is False
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["source"] == "alpaca" and saved["savedAt"]
    assert not list(path.parent.glob("*.tmp"))                  # atomic: no leftovers
    with pytest.raises(ValueError):
        feed.set_data_source("schwab")
    assert feed.get_data_source() == "alpaca" and feed.SCHWAB_ONLY is False
    assert feed.set_data_source("tos") == "tos" and feed.SCHWAB_ONLY is True


def test_a_real_change_swaps_stores_and_drops_only_the_ttl_cache(monkeypatch, tmp_path):
    """A switch never serves one source's tape as the other's (TTL cache
    dropped, stores swapped), and keeps each source's held tapes for its
    return; a no-op switch touches nothing."""
    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(tmp_path / "source.json"))
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    feed.clear_cache()
    tos_store = feed._KindStore(0.0)
    with feed._CACHE_LOCK:
        feed._STORE["30m"] = tos_store
        feed._CACHE[("k",)] = (0.0, None)
    feed.set_data_source("tos")                                  # no change
    assert feed._STORE.get("30m") is tos_store and feed._CACHE
    feed.set_data_source("alpaca")
    assert "30m" not in feed._STORE and not feed._CACHE
    feed.set_data_source("tos")
    assert feed._STORE.get("30m") is tos_store                   # the TOS tapes came back
    feed.clear_cache()


# --- the TOS budget -----------------------------------------------------------

def test_budget_missing_first_then_stalest_and_held_tapes_serve_the_rest(tos, monkeypatch):
    clock, client = tos
    monkeypatch.setattr(feed, "TOS_REQUESTS_PER_MINUTE", 2)
    monkeypatch.setattr(feed, "TOS_KINDS", ("30m",))          # only the 30m tape shares it here
    symbols = ["A", "B", "C", "D", "E"]

    first = feed.fetch_30m(symbols, now=NOW)
    assert asked(client) == ["A", "B"]                           # never past the budget
    assert set(first.bars) == {"A", "B"}
    assert first.errors == {s: feed.TOS_QUEUED_ERROR for s in ("C", "D", "E")}

    clock.advance(61)                                            # window + TTL expire
    second = feed.fetch_30m(symbols, now=NOW)
    assert asked(client, 2) == ["C", "D"]                        # missing before stale
    assert set(second.bars) == {"A", "B", "C", "D"}
    assert second.errors == {"E": feed.TOS_QUEUED_ERROR}

    clock.advance(feed.TOS_MIN_REFETCH_SECONDS + 1)            # A/B now past the re-read floor
    third = feed.fetch_30m(symbols, now=NOW)
    # E has no tape; then the stalest held tape (A/B were read 61s before C/D).
    assert asked(client, 4) == ["A", "E"]
    assert set(third.bars) == set(symbols) and third.errors == {}


def test_every_request_keeps_the_same_full_depth_start(tos):
    clock, client = tos
    feed.fetch_30m(["A"], now=NOW)
    clock.advance(feed.TOS_MIN_REFETCH_SECONDS + 1)            # past the re-read floor
    feed.fetch_30m(["A"], now=NOW)
    starts = [start for _s, _tf, start in client.calls]
    assert len(starts) == 2 and starts[0] == starts[1]
    assert starts[0] < NOW.replace(day=10)                       # full depth, not a tail


def test_daily_is_not_rerequested_within_the_refresh_interval(tos):
    clock, client = tos
    feed.fetch_daily(["A"], now=NOW)
    assert len(client.calls) == 1
    clock.advance(1000)
    held = feed.fetch_daily(["A"], now=NOW)
    assert len(client.calls) == 1 and "A" in held.bars           # held tape served
    clock.advance(feed.TOS_DAILY_REFRESH_SECONDS)
    feed.fetch_daily(["A"], now=NOW)
    assert len(client.calls) == 2


def test_schwab_empty_keeps_the_held_tape_and_reports_a_missing_one(tos):
    clock, client = tos
    feed.fetch_30m(["A"], now=NOW)
    clock.advance(61)
    client.empty = {"A", "B"}
    out = feed.fetch_30m(["A", "B"], now=NOW)
    assert "A" in out.bars                                       # stale beats blank
    assert out.errors == {"B": "schwab: no candles returned"}


def test_budget_splits_evenly_and_never_starves_a_tape(tos, monkeypatch):
    """Regression, 2026-09-30 live: the max-min split gave 30m NOTHING (10
    simulated minutes: 5m 720, daily 180, 30m 0) and the board loaded 12 of
    368 symbols in 9 minutes. Simulate the real call pattern - three lists
    rebuilding every 15s, three tapes each - and require every tape to get a
    real share while no rolling 60s window exceeds the budget."""
    import bisect

    clock, _client = tos
    monkeypatch.setattr(feed, "TOS_REQUESTS_PER_MINUTE", 90)
    stamps, totals = [], {}
    for _step in range(40):                                   # 10 minutes
        for big in (True, False, False):                      # Watchlist, Mag7, Movers
            for kind in ("5m", "daily", "30m"):
                granted = feed._tos_take(368 if big else 10, kind)
                totals[kind] = totals.get(kind, 0) + granted
                stamps += [clock.mono] * granted
        clock.advance(15)
    assert min(totals.values()) >= 200, totals                # ~27+/min each, none starved
    assert sum(totals.values()) <= 90 * 11
    stamps.sort()
    assert max(bisect.bisect_left(stamps, s + 60) - i for i, s in enumerate(stamps)) <= 90
    # A tape with nothing left to load stops asking; its share goes to the rest.
    feed._tos_reset()
    clock.advance(61)
    assert feed._tos_take(368, "5m") == 30          # cold window: no tape takes it all
    feed._tos_reset()
    clock.advance(61)
    assert feed._tos_take(0, "daily") == 0
    assert feed._tos_take(368, "5m") == 45 and feed._tos_take(368, "30m") == 45
    assert feed._tos_take(10) == 0                            # never past the window


def test_tos_mode_heals_per_tape_not_per_kind(tos, monkeypatch):
    clock, client = tos
    # Keep the 2h idle prune out of it, or it would drop A for its own reason.
    monkeypatch.setattr(feed, "STORE_PRUNE_SECONDS", 10.0 ** 9)
    feed.fetch_30m(["A"], now=NOW)
    clock.advance(feed.FULL_REBUILD_SECONDS - 100)
    feed.fetch_30m(["B"], now=NOW)                               # B read recently
    clock.advance(200)                                           # A now > 12h old, B not
    store, held = feed._store_lookup("30m", ["A", "B"])
    assert set(held) == {"B"}
    assert "A" not in store.fetched_at


def test_schwab_opt_out_call_never_writes_the_tos_store(tos, monkeypatch):
    clock, client = tos

    def alpaca(symbols, **kwargs):
        return {s: [{"t": "2026-09-30T19:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 5}]
                for s in symbols} if kwargs["feed"] == "sip" else {}

    monkeypatch.setattr(feed, "_fetch_batch", alpaca)
    out = feed.fetch_5m(["A"], now=NOW, schwab_volume=False,
                        credentials=[feed.FeedCredential("t", "k", "s")], get=lambda: None)
    assert "A" in out.bars
    assert not feed._CACHE and not feed._STORE["5m"].tapes


# --- alpaca mode --------------------------------------------------------------

def test_alpaca_mode_has_no_overlay_and_no_full_depth_schwab(monkeypatch):
    seen = []

    def volume_map(symbols, timeframe, now, client=None, bars_out=None, starts=None):
        seen.append((timeframe, starts))
        return {}

    def forbidden(*args, **kwargs):
        raise AssertionError("alpaca mode must not overlay Schwab or SIP prices")

    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", True)
    monkeypatch.setattr(feed, "SCHWAB_ONLY", False)
    monkeypatch.setattr(feed, "_schwab_volume_map", volume_map)
    monkeypatch.setattr(feed, "_overlay_schwab", forbidden)
    monkeypatch.setattr(feed, "_sip_prices", forbidden)
    monkeypatch.setattr(feed, "_fetch_batch", alpaca_with_iex_tail)
    feed.clear_cache()
    feed._tos_reset()
    try:
        kwargs = dict(now=NOW, credentials=[feed.FeedCredential("t", "k", "s")], get=lambda: None)
        assert "A" in feed.fetch_30m(["A"], **kwargs).bars
        assert "A" in feed.fetch_5m(["A"], **kwargs).bars
        assert "A" in feed.fetch_daily(["A"], **kwargs).bars
        # The IEX-tail fix only, on its default lookback window; none for daily.
        assert seen == [("30Min", None), ("5Min", None)]
        assert feed._STORE["30m"].tapes["A"] is not None        # alpaca writes the store
    finally:
        feed.clear_cache()
        feed._tos_reset()


def test_alpaca_mode_volume_fix_is_paced_and_only_for_iex_tails(monkeypatch):
    """Unpaced, alpaca mode made one Schwab call per symbol per 5m/30m every
    build (~736/min - the 459/min that preceded the 2026-09-04 Akamai ban)."""
    clock = Clock()
    seen = []

    def volume_map(symbols, timeframe, now, client=None, bars_out=None, starts=None):
        seen.append(sorted(symbols))
        return {}

    def sip_only(symbols, **kwargs):
        return alpaca_with_iex_tail(symbols, **kwargs) if kwargs["feed"] == "sip" else {}

    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", True)
    monkeypatch.setattr(feed, "SCHWAB_ONLY", False)
    monkeypatch.setattr(feed, "TOS_REQUESTS_PER_MINUTE", 2)
    monkeypatch.setattr(feed, "TOS_KINDS", ("30m",))          # only the 30m tape shares it here
    monkeypatch.setattr(feed, "_schwab_volume_map", volume_map)
    monkeypatch.setattr(feed, "_monotonic", lambda: clock.mono)
    monkeypatch.setattr(feed, "_wall_clock", lambda: clock.wall)
    monkeypatch.setattr(feed, "_fetch_batch", alpaca_with_iex_tail)
    feed.clear_cache()
    feed._tos_reset()
    try:
        kwargs = dict(now=NOW, credentials=[feed.FeedCredential("t", "k", "s")], get=lambda: None)
        symbols = ["A", "B", "C", "D", "E"]
        first = feed.fetch_30m(symbols, **kwargs)
        assert set(first.bars) == set(symbols) and first.errors == {}   # board never waits
        assert seen == [["A", "B"]]                                  # never past the budget
        clock.advance(61)
        feed.fetch_30m(symbols, **kwargs)
        assert seen[1] == ["C", "D"]                                 # longest-uncorrected first
        clock.advance(61)
        feed.fetch_30m(symbols, **kwargs)
        assert seen[2] == ["A", "E"]
        # No IEX-marked bar (and not premarket): nothing to correct, no call.
        monkeypatch.setattr(feed, "_fetch_batch", sip_only)
        feed.clear_cache()
        clock.advance(61)
        feed.fetch_30m(symbols, **kwargs)
        assert len(seen) == 3
    finally:
        feed.clear_cache()
        feed._tos_reset()


# --- tos_status + the board payload -----------------------------------------

def test_tos_status_counts_symbols_with_all_three_tapes(tos):
    clock, _client = tos
    feed.fetch_5m(["A", "B"], now=NOW)
    feed.fetch_30m(["A", "B"], now=NOW)
    feed.fetch_daily(["A"], now=NOW)
    clock.advance(30)
    status = feed.tos_status(["a", "B", "C"])
    assert status == {"source": "tos", "total": 3, "loaded": 1, "pending": 2, "oldestSeconds": 30}
    feed.clear_cache()
    assert feed.tos_status(["A"])["oldestSeconds"] is None


def test_board_payload_carries_the_source_without_mutating_the_board(tos, monkeypatch):
    board = {"universe": ["A"], "rows": []}
    out = service._with_data_source("Watchlist", board)
    assert out["dataSource"] == "tos" and out["tosStatus"]["total"] == 1
    assert "dataSource" not in board
    # The served source is the switch the feed actually runs on.
    monkeypatch.setattr(feed, "SCHWAB_ONLY", False)
    out = service._with_data_source("Watchlist", board)
    assert out["dataSource"] == "alpaca" and "tosStatus" not in out


# --- the worker routes --------------------------------------------------------

def _call(method, path, body=None, user="boss@x.com"):
    import momx_worker as w

    handler = w.Handler.__new__(w.Handler)
    raw = json.dumps(body or {}).encode("utf-8")
    handler.path = path
    handler.headers = {"X-AGX-User": user, "Content-Length": str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    handler.wfile = io.BytesIO()
    sent = {}
    handler.send_response = lambda status: sent.__setitem__("status", int(status))
    handler.send_header = lambda *a: None
    handler.end_headers = lambda: None
    getattr(handler, "do_" + method)()
    return sent["status"], json.loads(handler.wfile.getvalue())


def test_worker_data_source_routes(monkeypatch, tmp_path):
    import momx_worker as w

    monkeypatch.setenv(feed.DATA_SOURCE_PATH_ENV, str(tmp_path / "source.json"))
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    monkeypatch.setattr(feed, "clear_cache", lambda: None)
    monkeypatch.setattr(w, "viewer_is_admin", lambda email: email == "boss@x.com")
    rebuilt = []
    monkeypatch.setattr(w.service, "request_rebuild",
                        lambda name=None: rebuilt.append(name) or {"ok": True, "queued": True})

    assert _call("GET", "/api/momx-scanner/data-source") == (200, {"source": "tos"})
    status, out = _call("POST", "/api/momx-scanner/data-source", {"source": "alpaca"})
    assert status == 200 and out == {"source": "alpaca", "rebuild": {"ok": True, "queued": True}}
    assert rebuilt == [None] and feed.SCHWAB_ONLY is False
    assert _call("GET", "/api/momx-scanner/data-source") == (200, {"source": "alpaca"})
    status, out = _call("POST", "/api/momx-scanner/data-source", {"source": "iex"})
    assert status == 400 and "source" in out["error"]
    status, out = _call("POST", "/api/momx-scanner/data-source", {"source": "tos"}, user="user@x.com")
    assert (status, out) == (403, {"error": "admin only"})
    assert feed.get_data_source() == "alpaca"                    # the refusal changed nothing


def test_symbol_schwab_answers_empty_backs_off_instead_of_starving_the_list(monkeypatch):
    """Review 2026-09-30: a never-served symbol stayed first in the queue
    forever. After an empty answer it waits TOS_EMPTY_RETRY_SECONDS."""
    asked_rounds = []

    def fake_map(symbols, timeframe, now, bars_out=None, starts=None, **kwargs):
        asked_rounds.append(list(symbols))
        for symbol in symbols:
            if symbol != "DEAD":
                bars_out[symbol] = {1790700000: (1.0, 1.0, 1.0, 1.0, 10.0)}
        return {}

    monkeypatch.setattr(feed, "_schwab_volume_map", fake_map)
    monkeypatch.setattr(feed, "_tos_take", lambda n, kind=None: min(n, 1))
    store = feed._KindStore(0.0)
    clock = [1000.0]
    monkeypatch.setattr(feed, "_wall_clock", lambda: clock[0])

    def run(held):
        return feed._fetch_tape_schwab_only(
            ["DEAD", "LIVE"], held, store, {}, {}, lambda s: ("30m", s),
            timeframe=feed.TIMEFRAME_30M, start=None, now=None, use_cache=True,
            cache_kind="30m")

    run({})                                  # DEAD asked first, comes back empty
    assert asked_rounds[-1] == ["DEAD"] and "DEAD" in store.empty_at
    run({})                                  # LIVE gets the slot now
    assert asked_rounds[-1] == ["LIVE"]
    clock[0] += feed.TOS_EMPTY_RETRY_SECONDS
    run({"LIVE": store.tapes["LIVE"]})       # DEAD may be retried after the wait
    assert asked_rounds[-1] == ["DEAD"]


def test_small_list_asking_nothing_does_not_starve_the_big_lists_tape(tos, monkeypatch):
    """2026-10-01 live: Mag7's 'daily wants 0' overwrote Watchlist's demand,
    5m/30m took the whole window and 14 Watchlist daily tapes were never asked."""
    clock, _client = tos
    monkeypatch.setattr(feed, "TOS_REQUESTS_PER_MINUTE", 90)
    got = {"daily": 0}
    for _step in range(20):                                    # 5 minutes
        for kind in ("5m", "30m", "daily"):
            granted = feed._tos_take(368, kind)                # Watchlist wants plenty
            if kind == "daily":
                got["daily"] += granted
            feed._tos_take(0, kind)                            # Mag7: nothing to load
        clock.advance(15)
    assert got["daily"] >= 100, got                            # ~30/min, never starved


def test_held_intraday_tape_is_not_reread_within_the_refetch_floor(tos, monkeypatch):
    clock, client = tos
    feed.fetch_30m(["A"], now=NOW)
    first = len(client.calls)
    for _ in range(4):                                         # a small list rebuilding (244s)
        clock.advance(61)                                      # TTL + window expire
        feed.fetch_30m(["A"], now=NOW)
    assert len(client.calls) == first                          # held, < 300s old: no re-read
    clock.advance(61)                                          # 305s since the read
    feed.fetch_30m(["A"], now=NOW)
    assert len(client.calls) == first + 1


# --- TOS tapes survive switches and restarts (2026-10-01, "taking too much time") ---

def test_switching_away_and_back_keeps_the_tos_tapes(tos):
    clock, client = tos
    feed.fetch_30m(["A", "B"], now=NOW)
    calls = len(client.calls)
    feed.set_data_source("alpaca")
    feed.set_data_source("tos")
    clock.advance(61)                                         # TTL gone, tapes < 300s old
    out = feed.fetch_30m(["A", "B"], now=NOW)
    assert set(out.bars) == {"A", "B"} and out.errors == {}
    assert len(client.calls) == calls                         # nothing re-read: board full at once


def test_tos_tapes_reload_from_disk_after_a_restart(tos):
    clock, client = tos
    feed.fetch_30m(["A", "B"], now=NOW)
    feed._persist_tos_store("30m", feed._STORE["30m"], force=True)
    calls = len(client.calls)
    with feed._CACHE_LOCK:                                    # a fresh process: memory empty
        feed._CACHE.clear()
        feed._STORE.clear()
    clock.advance(61)
    out = feed.fetch_30m(["A", "B"], now=NOW)
    assert set(out.bars) == {"A", "B"} and out.errors == {}
    assert len(client.calls) == calls                         # served from disk, no re-read
    assert feed.tos_status(["A", "B"])["loaded"] == 0         # (5m/daily never fetched here)


def test_disk_tapes_older_than_the_split_heal_are_not_restored(tos):
    clock, client = tos
    feed.fetch_30m(["A"], now=NOW)
    feed._persist_tos_store("30m", feed._STORE["30m"], force=True)
    with feed._CACHE_LOCK:
        feed._CACHE.clear()
        feed._STORE.clear()
    clock.advance(feed.FULL_REBUILD_SECONDS + 1)
    calls = len(client.calls)
    feed.fetch_30m(["A"], now=NOW)
    assert len(client.calls) == calls + 1                     # re-read, not served stale


def test_background_refresher_refreshes_the_stalest_tapes_without_a_build(tos):
    """Freshness must not depend on build time (Watchlist builds take ~2 min)."""
    clock, client = tos
    feed.fetch_30m(["A", "B", "C"], now=NOW)                  # a build loads all three
    first = len(client.calls)
    clock.advance(feed.TOS_MIN_REFETCH_SECONDS + 61)          # all due, budget window clear
    made = feed._tos_refresh_once()
    assert made == 3 and len(client.calls) == first + 3       # refreshed with NO build
    store = feed._STORE["30m"]
    assert all(clock.wall - store.fetched_at[s] < 1 for s in ("A", "B", "C"))
    assert feed._tos_refresh_once() == 0                      # nothing due again yet
    feed.SCHWAB_ONLY = False
    clock.advance(feed.TOS_MIN_REFETCH_SECONDS + 61)
    assert feed._tos_refresh_once() == 0                      # Alpaca mode: never runs
