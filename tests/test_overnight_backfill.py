"""Overnight-session (20:00-04:00 ET) patch from Alpaca BOATS."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from chart_backfill import keep_inside_windows, overnight_gaps, overnight_windows
from data.alpaca_overnight import boats_bars_to_frame, clamp_end, fetch_boats_bars

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


def test_monday_night_starts_sunday_evening_and_tonight_counts_after_2000():
    # Monday 2026-08-24 22:10 ET: the running session (Mon 20:00 -> Tue 04:00)
    # is first, clipped to now; then Sun 20:00 -> Mon 04:00; weekend skipped.
    windows = overnight_windows(_et(2026, 8, 24, 22, 10), nights=3)
    assert windows[0] == (_et(2026, 8, 24, 20), _et(2026, 8, 24, 22, 10))
    assert windows[1] == (_et(2026, 8, 23, 20), _et(2026, 8, 24, 4))
    assert windows[2] == (_et(2026, 8, 20, 20), _et(2026, 8, 21, 4))  # Thu -> Fri


def test_gaps_only_report_windows_the_tape_lacks():
    stamps = pd.date_range("2026-08-23 20:00", "2026-08-24 03:55", freq="5min", tz=ET)
    frame = pd.DataFrame({"timestamp": stamps, "close": 1.0})
    gaps = overnight_gaps(frame, _et(2026, 8, 24, 9, 10), nights=2)
    # Sunday night present -> only Thursday night is missing.
    assert gaps == [(_et(2026, 8, 20, 20), _et(2026, 8, 21, 4))]


def test_boats_bars_convert_from_utc_and_are_clipped_to_windows():
    bars = [
        {"t": "2026-08-24T00:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},  # Sun 20:00 ET
        {"t": "2026-08-24T07:55:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.6, "v": 10},  # Mon 03:55 ET
        {"t": "2026-08-24T13:30:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.7, "v": 10},  # Mon 09:30 ET (RTH)
    ]
    frame = boats_bars_to_frame(bars)
    assert [ts.strftime("%a %H:%M") for ts in frame["timestamp"]] == ["Sun 20:00", "Mon 03:55", "Mon 09:30"]
    kept = keep_inside_windows(frame, [(_et(2026, 8, 23, 20), _et(2026, 8, 24, 4))])
    assert list(kept["close"]) == [1.5, 1.6]


def test_fetch_pages_and_clamps_recent_end():
    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        if params.get("page_token") is None:
            return Response({"bars": {"META": [{"t": "2026-08-24T00:00:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}, "next_page_token": "p2"})
        return Response({"bars": {"META": [{"t": "2026-08-24T00:05:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]}, "next_page_token": None})

    now = datetime(2026, 8, 25, 2, 30, tzinfo=timezone.utc)
    out = fetch_boats_bars("k", "s", ["meta"], _et(2026, 8, 23, 20), _et(2026, 8, 24, 23, 0), "5min", get=fake_get, now=now)
    assert len(out["META"]) == 2
    assert calls[0]["feed"] == "boats" and calls[0]["timeframe"] == "5Min" and calls[0]["symbols"] == "META"
    # 23:00 ET on the 24th is 03:00Z on the 25th - later than now-16min, so clamped.
    assert calls[0]["end"] == clamp_end(_et(2026, 8, 24, 23, 0), now).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert calls[1]["page_token"] == "p2"


def test_fetch_returns_empty_on_http_error():
    class Response:
        status_code = 403

        def json(self):
            return {"message": "subscription does not permit querying recent BOATS data"}

    out = fetch_boats_bars("k", "s", ["META"], _et(2026, 8, 23, 20), _et(2026, 8, 24, 4), get=lambda *a, **k: Response())
    assert out == {}


# The overnight session that ended most recently: 20:00 ET the previous day
# through 04:00 ET. Derived from the clock so the test means the same thing on
# any day it runs.
NOW = pd.Timestamp.now(tz=ET).floor("5min")
GAP_END = NOW.normalize() + pd.Timedelta(hours=4)
if GAP_END > NOW:
    GAP_END -= pd.Timedelta(days=1)
GAP_START = GAP_END - pd.Timedelta(hours=8)


def _iso(ts):
    """The UTC instant, shaped like the BOATS feed writes it."""
    return ts.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def test_dashboard_patch_fills_only_missing_nights_and_caches():
    import api_server

    owner = next(c for c in vars(api_server).values() if isinstance(c, type) and hasattr(c, "_backfill_overnight_session"))

    class Probe:
        OI_OVERNIGHT_NIGHTS = owner.OI_OVERNIGHT_NIGHTS
        OI_OVERNIGHT_TTL_SECONDS = owner.OI_OVERNIGHT_TTL_SECONDS
        _backfill_overnight_session = owner._backfill_overnight_session
        calls = []

        def _owner_alpaca_credentials(self):
            return "k", "s"

        def _overnight_fetcher(self, key, secret, symbols, start, end, interval):
            self.calls.append((symbols, start, end, interval))
            return {"META": [
                {"t": _iso(GAP_START), "o": 1, "h": 1, "l": 1, "c": 550.0, "v": 5},        # 20:00 ET
                {"t": _iso(GAP_END - timedelta(minutes=5)), "o": 1, "h": 1, "l": 1, "c": 550.5, "v": 5},  # 03:55 ET
                {"t": _iso(GAP_END + timedelta(hours=5, minutes=30)), "o": 1, "h": 1, "l": 1, "c": 999.0, "v": 5},  # RTH: must be dropped
            ]}

    probe = Probe()
    # A tape that already holds every night EXCEPT the one that ended this
    # morning. Anchored to the clock, not to fixed dates: the previous version
    # pinned the gap to 2026-08-23 and asserted it was backfilled, but
    # _backfill_overnight_session only looks back OI_OVERNIGHT_NIGHTS (6)
    # nights - so from 2026-08-30 onward the gap fell outside the window, the
    # code correctly did nothing, and the test read that as a failure. A test
    # that quietly expires is worse than no test: it goes red on a morning
    # when nothing is wrong and trains you to ignore red.
    days = pd.date_range(GAP_START - timedelta(days=10), NOW, freq="5min", tz=ET)
    keep = ~((days >= GAP_START) & (days < GAP_END))
    frame = pd.DataFrame({"timestamp": days[keep], "open": 1.0, "high": 1.0, "low": 1.0, "close": 552.0, "volume": 1.0})
    patched = probe._backfill_overnight_session(frame, "meta", interval="5min")
    added = patched[~patched["timestamp"].isin(frame["timestamp"])]
    assert list(added["timestamp"]) == [GAP_START, GAP_END - timedelta(minutes=5)], (
        f"expected the two overnight bars back, got {list(added['timestamp'])}"
    )
    assert 999.0 not in set(patched["close"])          # RTH row from BOATS never overrides the tape
    first_calls = len(probe.calls)
    probe._backfill_overnight_session(frame, "meta", interval="5min")
    assert len(probe.calls) == first_calls             # second call served from the cache


def test_boats_bars_to_frame_parses_the_column_once_not_per_row():
    """The parse must be VECTORISED, and it must stay that way.

    boats_bars_to_frame called pd.to_datetime once per bar, inside its loop, so
    every call re-ran pandas' format inference on a single scalar. Measured
    2026-08-27 on 20,000 bars: 25.69s per-row against 0.098s vectorised - 261x.
    This runs on the chart build path, so with 61 builder threads live nothing
    finished: 357 of 399 cached tickers had no complete session, all parked in
    _array_strptime_with_fallback under _backfill_overnight_session.

    A wall-clock budget is the only assertion that actually catches a
    reintroduction; the budget is ~100x the vectorised cost, so it fails on the
    per-row shape and passes on any sane machine.
    """
    import time

    from data.alpaca_overnight import boats_bars_to_frame

    bars = [
        {
            "t": "2026-08-2%dT%02d:%02d:00Z" % (1 + index % 7, index % 24, index % 60),
            "o": 1.0, "h": 2.5, "l": 0.5, "c": 1.5, "v": 10,
        }
        for index in range(20000)
    ]
    started = time.perf_counter()
    frame = boats_bars_to_frame(bars)
    elapsed = time.perf_counter() - started

    assert len(frame) == 20000
    assert elapsed < 5.0, (
        "boats_bars_to_frame took %.1fs for 20k bars - that is the per-row "
        "pd.to_datetime shape again" % elapsed
    )


def test_boats_bars_to_frame_keeps_its_contract():
    from data.alpaca_overnight import boats_bars_to_frame

    columns = ["timestamp", "open", "high", "low", "close", "volume"]
    assert list(boats_bars_to_frame([]).columns) == columns
    assert list(boats_bars_to_frame("nonsense").columns) == columns
    assert len(boats_bars_to_frame(["junk", None, 7])) == 0

    good = {"t": "2026-08-24T04:00:00Z", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 3}
    # An unparseable stamp is dropped, not raised on, and does not take the
    # good rows with it.
    mixed = boats_bars_to_frame([{**good, "t": "not-a-date"}, good])
    assert len(mixed) == 1
    # A row missing a price field is dropped.
    assert len(boats_bars_to_frame([{"t": "2026-08-24T04:00:00Z", "o": 1}])) == 0

    # Eastern, and ascending regardless of input order.
    later = {**good, "t": "2026-08-24T05:00:00Z"}
    frame = boats_bars_to_frame([later, good])
    assert str(frame["timestamp"].dt.tz) == "America/New_York"
    assert frame["timestamp"].is_monotonic_increasing


# ---------------------------------------------------------------------------
# The SIP premarket net, 2026-08-28. The Tradier token died and the 04:00-07:00
# window went dark, because the code (and its error message) claimed "this
# window has no other source". That claim had tested Alpaca feed=iex and
# feed=boats - both genuinely empty there - but never feed=sip, which carries
# the full 04:00-07:00 on the same free owner key: 161 one-minute AAPL bars,
# all nine scanner symbols covered, behind the same ~15-minute recency wall
# that clamp_end already clears.
# ---------------------------------------------------------------------------

def test_fetch_can_ask_for_the_sip_feed():
    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        return Response({"bars": {"AAPL": []}, "next_page_token": None})

    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    fetch_boats_bars("k", "s", ["AAPL"], _et(2026, 8, 28, 4), _et(2026, 8, 28, 7), "1min", get=fake_get, now=now, feed="sip")
    assert calls[0]["feed"] == "sip"


def test_fetch_defaults_to_boats_so_the_overnight_path_is_unchanged():
    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append(dict(params))
        return Response({"bars": {}, "next_page_token": None})

    now = datetime(2026, 8, 28, 12, 0, tzinfo=timezone.utc)
    fetch_boats_bars("k", "s", ["AAPL"], _et(2026, 8, 27, 20), _et(2026, 8, 28, 4), "1min", get=fake_get, now=now)
    assert calls[0]["feed"] == "boats"


def test_a_dead_tradier_token_falls_through_to_sip_not_to_empty_space():
    # Source-level pin on _backfill_today_premarket: the Tradier except-path
    # must try SIP before surrendering, and each outcome must say which feed
    # the trader is actually looking at.
    import inspect
    import api_server

    source = inspect.getsource(api_server.DashboardState._backfill_today_premarket)
    assert 'feed="sip"' in source, "the Tradier failure path no longer tries SIP"
    assert "running on the Alpaca SIP" in source, "a silent fallback hides which feed is live"
    assert "the Alpaca SIP backup returned nothing" in source, (
        "a double failure must say both sources failed"
    )
