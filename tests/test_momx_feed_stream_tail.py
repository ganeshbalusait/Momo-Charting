"""momx/feed.py stream tail (2026-10-01): TOS tapes are extended with the
api_server's saved Schwab CHART_EQUITY minutes - but only when the stream
reproduces the tape's last complete REST buckets exactly (it has gaps)."""
import gzip
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from momx import feed

ET = ZoneInfo("America/New_York")
BASE = int(datetime(2026, 10, 1, 10, 0, tzinfo=ET).timestamp())


def rest_frame(buckets, span=300):
    """REST 5m tape: list of (offset_bucket, volume); last row is forming."""
    return pd.DataFrame({
        "timestamp": [pd.Timestamp(BASE + i * span, unit="s", tz="UTC").tz_convert(ET) for i, _ in buckets],
        "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5,
        "volume": [float(v) for _, v in buckets], "feed": "schwab",
    })


def minutes_for(per_bucket_volume, buckets, span=300):
    """Stream minutes reproducing ``per_bucket_volume`` for each bucket index."""
    out = []
    for index in buckets:
        for minute in range(span // 60):
            t = BASE + index * span + minute * 60
            out.append({"time": t, "open": 10.0 + index, "high": 12.0 + index, "low": 9.0,
                        "close": 10.0 + index + minute / 10, "volume": per_bucket_volume[index] / (span // 60)})
    return out


@pytest.fixture
def stream_file(tmp_path, monkeypatch):
    path = tmp_path / "hist.json.gz"
    monkeypatch.setenv("AGX_MOMX_STREAM_HISTORY_PATH", str(path))
    feed._STREAM_CACHE.update({"mtime": None, "symbols": {}})

    def write(symbols):
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"version": 1, "symbols": symbols}, handle)
        stamp = time.time() + len(str(symbols)) * 1e-6        # distinct mtime per write
        os.utime(path, (stamp, stamp))
    return write


def test_matching_stream_extends_the_tape_and_replaces_the_forming_bucket(stream_file):
    vol = {i: 500.0 * (i + 1) for i in range(7)}
    frame = rest_frame([(0, vol[0]), (1, vol[1]), (2, vol[2]), (3, vol[3]), (4, 10.0)])   # 4 forming
    stream_file({"AAA": minutes_for(vol, range(7))})
    out = feed._extend_with_stream(frame, "AAA", 5)
    stamps = [int(t.timestamp()) - BASE for t in out["timestamp"]]
    assert stamps == [0, 300, 600, 900, 1200, 1500, 1800]
    assert list(out["volume"])[-3:] == [vol[4], vol[5], vol[6]]      # forming 10.0 replaced
    assert list(out["feed"])[-3:] == ["stream"] * 3
    assert out["high"].iloc[-1] == 18.0 and out["close"].iloc[-1] == 6 + 10.4


def test_a_gap_leaving_too_few_matching_buckets_keeps_the_rest_tape(stream_file):
    vol = {i: 500.0 * (i + 1) for i in range(7)}
    frame = rest_frame([(0, vol[0]), (1, vol[1]), (2, vol[2]), (3, vol[3]), (4, 10.0)])
    gappy = {**vol, 2: vol[2] / 3, 3: vol[3] / 2}                     # stream missed minutes
    stream_file({"AAA": minutes_for(gappy, range(7))})
    assert feed._extend_with_stream(frame, "AAA", 5) is frame


def test_an_old_restart_hole_is_skipped_when_older_buckets_match(stream_file):
    """13:10 ET 2026-10-01: the REST end sat in an api_server-restart hole and
    0/368 tapes extended. Under-counted buckets are holes, not disproof."""
    vol = {i: 500.0 * (i + 1) for i in range(9)}
    frame = rest_frame([(i, vol[i]) for i in range(6)] + [(6, 10.0)])
    gappy = {**vol, 5: vol[5] / 4}
    stream_file({"AAA": minutes_for(gappy, range(9))})
    out = feed._extend_with_stream(frame, "AAA", 5)
    assert list(out["volume"])[-3:] == [vol[6], vol[7], vol[8]]


def test_a_stream_that_over_counts_is_rejected(stream_file):
    vol = {i: 500.0 * (i + 1) for i in range(7)}
    frame = rest_frame([(0, vol[0]), (1, vol[1]), (2, vol[2]), (3, vol[3]), (4, 10.0)])
    stream_file({"AAA": minutes_for({**vol, 3: vol[3] * 2}, range(7))})
    assert feed._extend_with_stream(frame, "AAA", 5) is frame


def test_appending_stops_before_a_dead_minute(stream_file):
    """A minute NO symbol streamed = the stream was down; buckets from there on
    would under-count, so only the buckets before it are appended."""
    vol = {i: 500.0 * (i + 1) for i in range(8)}
    frame = rest_frame([(0, vol[0]), (1, vol[1]), (2, vol[2]), (3, vol[3]), (4, 10.0)])
    minutes = [m for m in minutes_for(vol, range(8)) if m["time"] != BASE + 6 * 300 + 120]
    stream_file({"AAA": minutes, "BBB": [m for m in minutes if m["time"] >= BASE + 4 * 300]})
    feed._STREAM_EXTENDED.pop(("AAA", 5), None)
    out = feed._extend_with_stream(frame, "AAA", 5)
    stamps = [int(t.timestamp()) - BASE for t in out["timestamp"]]
    assert stamps == [0, 300, 600, 900, 1200, 1500]                  # bucket 6 holds the hole
    # Stopped short by the hole: NOT "kept fresh by the stream", or the
    # symbol's REST refetch is demoted for 30 min (Mag7 on 10:10 until ~10:42,
    # 2026-11-03).
    assert ("AAA", 5) not in feed._STREAM_EXTENDED


def test_too_little_overlap_or_no_stream_keeps_the_rest_tape(stream_file):
    vol = {i: 500.0 * (i + 1) for i in range(7)}
    frame = rest_frame([(0, vol[0]), (1, vol[1]), (2, vol[2]), (3, vol[3]), (4, 10.0)])
    stream_file({"AAA": minutes_for(vol, range(3, 7))})               # covers 1 complete bucket
    assert feed._extend_with_stream(frame, "AAA", 5) is frame
    assert feed._extend_with_stream(frame, "ZZZ", 5) is frame


def test_a_stale_stream_file_is_ignored(stream_file, monkeypatch):
    vol = {i: 500.0 * (i + 1) for i in range(7)}
    frame = rest_frame([(0, vol[0]), (1, vol[1]), (2, vol[2]), (3, vol[3]), (4, 10.0)])
    stream_file({"AAA": minutes_for(vol, range(7))})
    monkeypatch.setattr(feed, "STREAM_FILE_MAX_AGE_SECONDS", -1.0)    # api_server "down"
    assert feed._extend_with_stream(frame, "AAA", 5) is frame


def test_premarket_fill_only_inserts_where_schwab_has_no_bar():
    def frame(times, vol, feed_mark):
        return pd.DataFrame({"timestamp": [pd.Timestamp(datetime(2026, 10, 1, h, m, tzinfo=ET)) for h, m in times],
                             "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                             "volume": vol, "feed": feed_mark})
    schwab = frame([(7, 0), (7, 30), (9, 0)], 100.0, "schwab")
    sip = frame([(4, 0), (4, 30), (6, 30), (7, 0)], 5.0, "sip-premarket")
    out = feed._fill_premarket_hole(schwab, sip)
    assert [t.strftime("%H:%M") for t in out["timestamp"]] == ["04:00", "04:30", "06:30", "07:00", "07:30", "09:00"]
    assert out.loc[out["timestamp"].dt.hour == 7, "feed"].tolist() == ["schwab", "schwab"]   # Schwab wins
    assert feed._fill_premarket_hole(schwab, None) is schwab


def test_premarket_fill_is_skipped_before_sip_can_serve_and_on_weekends(monkeypatch):
    monkeypatch.setattr(feed, "resolve_credentials", lambda: (_ for _ in ()).throw(AssertionError("no fetch")))
    early = datetime(2026, 10, 1, 4, 10, tzinfo=ET)            # SIP blocks the last ~20 min
    assert feed._premarket_fill(["AAPL"], feed.TIMEFRAME_5M, early) == {}
    saturday = datetime(2026, 10, 3, 9, 0, tzinfo=ET)
    assert feed._premarket_fill(["AAPL"], feed.TIMEFRAME_5M, saturday) == {}


def test_concurrent_cold_live_poll_does_not_parse_history_archive(monkeypatch):
    import io
    import threading
    started, release = threading.Event(), threading.Event()
    calls, result = [], []
    def slow_live(request, timeout=0):
        calls.append(request.full_url)
        started.set()
        assert release.wait(3)
        return io.BytesIO(json.dumps({'bars':{'AAA':[[time.time(),1,2,.5,1.5,100]]}}).encode())
    monkeypatch.setattr('urllib.request.urlopen', slow_live)
    monkeypatch.setattr(feed, 'STREAM_LIVE_ENABLED', True)
    monkeypatch.setattr(feed, '_STREAM_LIVE', {'at':0.,'ok_at':0.,'max_t':0.,'bars':{},'dead':[]})
    monkeypatch.setattr(feed, '_STREAM_LIVE_FETCHING', threading.Event())
    monkeypatch.setattr(feed, '_stream_minutes_from_file', lambda symbol: pytest.fail('Unexpected archive decode'))
    worker = threading.Thread(target=lambda: result.append(feed._stream_minutes('AAA')))
    worker.start()
    try:
        assert started.wait(3)
        assert feed._stream_minutes('BBB') == []
        assert len(calls) == 1
    finally:
        release.set()
        worker.join(3)
    assert not worker.is_alive() and result[0][0]['volume'] == 100
    assert not feed._STREAM_LIVE_FETCHING.is_set()


def test_failed_live_poll_clears_inflight_and_keeps_file_fallback(monkeypatch):
    import threading
    monkeypatch.setattr(feed, '_STREAM_LIVE', {'at':0.,'ok_at':0.,'max_t':0.,'bars':{},'dead':[]})
    monkeypatch.setattr(feed, '_STREAM_LIVE_FETCHING', threading.Event())
    monkeypatch.setattr(feed, 'STREAM_LIVE_ENABLED', True)
    def unavailable(*args, **kwargs):
        raise OSError('Synthetic offline endpoint')
    monkeypatch.setattr('urllib.request.urlopen', unavailable)
    monkeypatch.setattr(feed, '_stream_minutes_from_file', lambda symbol: [{'time':BASE}])
    assert feed._stream_minutes('AAA') == [{'time':BASE}]
    assert not feed._STREAM_LIVE_FETCHING.is_set()


def test_live_stream_poll_merges_minutes_and_is_preferred(monkeypatch):
    import io

    calls = []
    payloads = [
        {"bars": {"AAA": [[BASE, 1, 2, 0.5, 1.5, 100], [BASE + 60, 1, 2, 0.5, 1.6, 50]]}},
        {"bars": {"AAA": [[BASE + 60, 1, 2, 0.5, 1.7, 80], [BASE + 120, 1, 2, 0.5, 1.8, 30]]}},
    ]

    def fake_urlopen(request, timeout=0):
        calls.append(request.full_url)
        return io.BytesIO(json.dumps(payloads[len(calls) - 1]).encode())

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr(feed, "STREAM_LIVE_ENABLED", True)
    monkeypatch.setattr(feed, "STREAM_LIVE_WINDOW_SECONDS", 10 ** 10)
    clock = [1000.0]
    monkeypatch.setattr(feed, "_monotonic", lambda: clock[0])
    feed._STREAM_LIVE.update({"at": 0.0, "ok_at": 0.0, "max_t": 0.0, "bars": {}})
    first = feed._stream_minutes("AAA")
    assert [m["time"] for m in first] == [BASE, BASE + 60]
    assert len(feed._stream_minutes("AAA")) == 2 and len(calls) == 1     # within the poll interval
    clock[0] += feed.STREAM_LIVE_POLL_SECONDS
    second = feed._stream_minutes("AAA")
    assert [m["time"] for m in second] == [BASE, BASE + 60, BASE + 120]
    assert second[1]["volume"] == 80                                    # forming minute replaced
    assert f"since={BASE + 60 - 180}" in calls[1]                       # incremental ask
    feed._STREAM_LIVE.update({"at": 0.0, "ok_at": 0.0, "max_t": 0.0, "bars": {}})


def test_premarket_fill_matches_across_timestamp_units():
    """The int64 membership check must treat us- and ns-unit stamps alike."""
    import pandas as pd
    from momx import feed

    stamps = pd.date_range("2026-10-01 04:00", periods=3, freq="5min", tz="America/New_York")
    frame = pd.DataFrame({"timestamp": stamps[1:].as_unit("ns"), "open": 1.0, "high": 1.0,
                          "low": 1.0, "close": 1.0, "volume": 10.0, "feed": "schwab"})
    fill = pd.DataFrame({"timestamp": stamps.as_unit("us"), "open": 2.0, "high": 2.0,
                         "low": 2.0, "close": 2.0, "volume": 5.0, "feed": "sip-premarket"})
    merged = feed._fill_premarket_hole(frame, fill)
    assert len(merged) == 3
    assert list(merged["feed"]) == ["sip-premarket", "schwab", "schwab"]
    assert feed._fill_premarket_hole(merged, fill) is merged


def test_tos_order_puts_unstreamed_tapes_first(monkeypatch):
    """The paced REST budget serves symbols the 300-cap stream cannot carry
    first; a streamed tape waits until TOS_STREAMED_REFETCH_SECONDS old."""
    store = feed._KindStore(0.0)
    wall = 10_000.0
    store.fetched_at.update({"LIVE": 1000.0, "COLD": 2000.0, "OLDLIVE": 100.0})
    monkeypatch.setattr(feed, "_stream_live_symbols", lambda: {"LIVE", "OLDLIVE"})
    monkeypatch.setattr(feed, "TOS_STREAMED_REFETCH_SECONDS", 9_500.0)
    monkeypatch.setattr(feed, "_STREAM_EXTENDED", {("LIVE", 5): wall - 60, ("OLDLIVE", 5): wall - 60})
    held = {"LIVE": 1, "COLD": 1, "OLDLIVE": 1}
    order = feed._tos_order(["LIVE", "COLD", "OLDLIVE"], held, store, feed.TIMEFRAME_5M, wall)
    assert order == ["OLDLIVE", "COLD", "LIVE"]


def test_a_streamed_symbol_whose_tail_is_not_extending_is_not_demoted(monkeypatch):
    """APLD / CRDO 2026-10-02: on the stream, but the tail failed its volume check,
    so the REST tape was all the scanner had - it must not wait 30 minutes."""
    store = feed._KindStore(0.0)
    wall = 10_000.0
    store.fetched_at.update({"APLD": 1000.0, "COLD": 2000.0})
    monkeypatch.setattr(feed, "_stream_live_symbols", lambda: {"APLD"})
    monkeypatch.setattr(feed, "TOS_STREAMED_REFETCH_SECONDS", 9_500.0)
    monkeypatch.setattr(feed, "_STREAM_EXTENDED", {("APLD", 5): wall - 3_000})   # tail last worked 50 min ago
    held = {"APLD": 1, "COLD": 1}
    assert feed._tos_order(["APLD", "COLD"], held, store, feed.TIMEFRAME_5M, wall) == ["APLD", "COLD"]
    monkeypatch.setattr(feed, "_STREAM_EXTENDED", {})                          # never extended
    assert feed._tos_order(["APLD", "COLD"], held, store, feed.TIMEFRAME_5M, wall) == ["APLD", "COLD"]
    # the 30m tail verifying says nothing about the 5m tape (and the other way round)
    monkeypatch.setattr(feed, "_STREAM_EXTENDED", {("APLD", 30): wall - 60})
    assert feed._tos_order(["APLD", "COLD"], held, store, feed.TIMEFRAME_5M, wall) == ["APLD", "COLD"]
    assert feed._tos_order(["APLD", "COLD"], held, store, feed.TIMEFRAME_30M, wall) == ["COLD", "APLD"]


def test_overnight_fill_asks_boats_for_yesterday_2000_to_today_0400(monkeypatch):
    """2026-10-09: MRNA's 2h squeeze built overnight; the Schwab tape had no
    20:00-07:00 bars, so the scanner said "no squeeze ON" while TOS fired."""
    feed._PM_FILL.clear()
    asked = []

    def fake_batch(batch, *, timeframe, start, end, feed, credentials, get, adjustment):
        asked.append((feed, start, end))
        return {}

    monkeypatch.setattr(feed, "resolve_credentials", lambda: {"key": "x"})
    monkeypatch.setattr(feed, "_fetch_batch", fake_batch)
    monkeypatch.setattr(feed, "_default_get", lambda: None)
    now = datetime(2026, 10, 9, 9, 30, tzinfo=ET)
    feed._overnight_fill(["MRNA"], feed.TIMEFRAME_30M, now)
    assert asked and asked[0][0] == feed.INTRADAY_OVERNIGHT_FEED
    assert asked[0][1].astimezone(ET) == datetime(2026, 10, 8, 20, 0, tzinfo=ET)
    assert asked[0][2].astimezone(ET) <= datetime(2026, 10, 9, 4, 0, tzinfo=ET)
    feed._PM_FILL.clear()


def test_overnight_fill_is_skipped_on_weekends(monkeypatch):
    monkeypatch.setattr(feed, "resolve_credentials", lambda: (_ for _ in ()).throw(AssertionError("no fetch")))
    assert feed._overnight_fill(["AAPL"], feed.TIMEFRAME_5M, datetime(2026, 10, 10, 9, 0, tzinfo=ET)) == {}


def test_one_failed_live_poll_does_not_fall_back_to_the_file(monkeypatch):
    """2026-10-09: one slow poll sent every tapes thread to re-parse the 17 MB
    history file. A recent good poll keeps the live minutes in use."""
    import urllib.request
    clock = {"t": 1000.0}
    monkeypatch.setattr(feed, "_monotonic", lambda: clock["t"])
    monkeypatch.setitem(feed._STREAM_LIVE, "ok_at", 990.0)
    monkeypatch.setitem(feed._STREAM_LIVE, "at", 0.0)

    def boom(*args, **kwargs):
        raise OSError("timed out")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert feed._stream_live_refresh() is True                     # 10 s since the last good poll
    clock["t"] = 990.0 + feed.STREAM_LIVE_TRUST_SECONDS + 1
    monkeypatch.setitem(feed._STREAM_LIVE, "at", 0.0)
    assert feed._stream_live_refresh() is False                    # really down: file fallback
