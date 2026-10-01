"""momx/feed.py - Schwab premarket tail (2026-09-29): premarket CALL2H arrows
were seen a median 16 min late because the IEX live tail is ~empty before 09:30."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from momx import feed

ET = ZoneInfo("America/New_York")


def tape(times, feed_mark="sip"):
    return pd.DataFrame({"timestamp": [pd.Timestamp(t) for t in times],
                         "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2, "volume": 1000.0, "feed": feed_mark})


def ts(h, m):
    return datetime(2026, 9, 29, h, m, tzinfo=ET)


def epoch(h, m):
    return int(ts(h, m).timestamp())


def test_premarket_appends_only_newer_schwab_candles():
    frame = tape([ts(7, 30), ts(7, 35)])
    candles = {epoch(7, 35): (1, 1, 1, 1, 1), epoch(7, 40): (10.2, 10.6, 10.1, 10.5, 5000),
               epoch(7, 45): (10.5, 10.9, 10.4, 10.8, 7000)}
    out = feed._merge_schwab_tail(frame, candles, ts(7, 51))
    assert [t.strftime("%H:%M") for t in out["timestamp"]] == ["07:30", "07:35", "07:40", "07:45"]
    assert list(out["feed"]) == ["sip", "sip", "schwab", "schwab"]
    assert out["close"].iloc[1] == 10.2          # the held 07:35 bar is NOT overwritten
    assert out["close"].iloc[-1] == 10.8 and out["volume"].iloc[-1] == 7000


def test_regular_hours_and_empty_inputs_leave_the_tape_alone():
    frame = tape([ts(10, 0)])
    candles = {epoch(10, 5): (10, 11, 9, 10.5, 100)}
    assert feed._merge_schwab_tail(frame, candles, ts(10, 11)) is frame      # after 09:30: untouched
    assert feed._merge_schwab_tail(frame, {}, ts(8, 0)) is frame
    assert feed._merge_schwab_tail(frame, None, ts(8, 0)) is frame
    weekend = datetime(2026, 9, 27, 8, 0, tzinfo=ET)
    assert feed._merge_schwab_tail(tape([ts(7, 30)]), {epoch(7, 40): (1, 1, 1, 1, 1)}, weekend) is not None


def test_volume_map_fills_the_candle_dict_from_the_same_call():
    class Client:
        def _get_price_history(self, symbol, timeframe, start, end):
            return pd.DataFrame({"timestamp": [pd.Timestamp(ts(7, 40))], "open": [10.0], "high": [10.5],
                                 "low": [9.9], "close": [10.4], "volume": [2500.0]})
    old = feed.SCHWAB_VOLUME_ENABLED
    feed.SCHWAB_VOLUME_ENABLED = True
    try:
        candles = {}
        vol = feed._schwab_volume_map(["CHPT"], "5Min", ts(7, 50), client=Client(), bars_out=candles)
    finally:
        feed.SCHWAB_VOLUME_ENABLED = old
    assert vol == {"CHPT": {epoch(7, 40): 2500.0}}
    assert candles == {"CHPT": {epoch(7, 40): (10.0, 10.5, 9.9, 10.4, 2500.0)}}


def test_a_schwab_tail_with_a_premarket_hole_never_deletes_alpaca_bars():
    # Schwab has NO current-day bars before ~07:00 ET; Alpaca's 04:00-06:55 must survive.
    alpaca = tape([ts(4, 0), ts(5, 0), ts(6, 55)])
    out = feed._merge_schwab_tail(alpaca, {epoch(7, 5): (10, 10.4, 9.9, 10.3, 900)}, ts(7, 11))
    assert [t.strftime("%H:%M") for t in out["timestamp"]] == ["04:00", "05:00", "06:55", "07:05"]
    assert list(out["feed"][:3]) == ["sip", "sip", "sip"]
    # a Schwab slice that is empty / only older than the tape changes nothing
    assert feed._merge_schwab_tail(alpaca, {epoch(4, 0): (1, 1, 1, 1, 1)}, ts(6, 58)) is alpaca


def test_schwab_volume_false_makes_no_schwab_call_on_the_premarket_path(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("schwab_volume=False must stay Schwab-free, premarket included")
    monkeypatch.setattr(feed, "_schwab_volume_map", boom)
    monkeypatch.setattr(feed, "_schwab_client", boom)
    frame = tape([ts(7, 30)])
    # the tail merge itself never talks to Schwab - it only reads candles it is handed
    assert feed._merge_schwab_tail(frame, None, ts(7, 40)) is frame
