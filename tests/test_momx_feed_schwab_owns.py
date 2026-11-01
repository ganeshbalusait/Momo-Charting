"""momx/feed.py - Schwab owns the intraday tapes (2026-09-29): Alpaca SIP counted
30-110% more shares per candle than his TOS, so RVOL and the 4h volume gate read
a different tape. Schwab now owns every ET date it covers."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from momx import feed

ET = ZoneInfo("America/New_York")


def ts(day, h, m):
    return datetime(2026, 9, day, h, m, tzinfo=ET)


def tape(stamps, volume=1000.0, feed_mark="sip"):
    return pd.DataFrame({"timestamp": [pd.Timestamp(t) for t in stamps],
                         "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.2,
                         "volume": volume, "feed": feed_mark})


def candle(volume, close=11.0):
    return (close, close + 0.5, close - 0.5, close, volume)


def hhmm(frame):
    return [t.strftime("%d %H:%M") for t in frame["timestamp"]]


def test_schwab_replaces_every_bar_inside_its_span_for_that_date():
    frame = tape([ts(29, 9, 30), ts(29, 10, 0), ts(29, 10, 30), ts(29, 11, 0)], volume=2000.0)
    candles = {int(ts(29, 9, 30).timestamp()): candle(500),
               int(ts(29, 10, 30).timestamp()): candle(700)}
    out = feed._overlay_schwab(frame, candles)
    # 10:00 sits between Schwab's first and last candle but Schwab has no row:
    # TOS does not have that bar either, so it goes. 11:00 is after Schwab's
    # last candle that date, so the Alpaca bar is kept.
    assert hhmm(out) == ["29 09:30", "29 10:30", "29 11:00"]
    assert list(out["volume"]) == [500, 700, 2000.0]
    assert list(out["feed"]) == ["schwab", "schwab", "sip"]


def test_dates_schwab_does_not_cover_stay_on_alpaca():
    frame = tape([ts(8, 10, 0), ts(29, 10, 0)])
    candles = {int(ts(29, 10, 0).timestamp()): candle(300)}
    out = feed._overlay_schwab(frame, candles)
    assert hhmm(out) == ["08 10:00", "29 10:00"]
    assert list(out["feed"]) == ["sip", "schwab"]
    assert list(out["volume"]) == [1000.0, 300]


def test_premarket_hole_before_schwabs_first_candle_is_kept():
    # Schwab serves the current day from 07:00 only; 04:00-07:00 stays Alpaca.
    frame = tape([ts(29, 4, 0), ts(29, 6, 30), ts(29, 7, 0)])
    candles = {int(ts(29, 7, 0).timestamp()): candle(900),
               int(ts(29, 7, 30).timestamp()): candle(950)}
    out = feed._overlay_schwab(frame, candles)
    assert hhmm(out) == ["29 04:00", "29 06:30", "29 07:00", "29 07:30"]
    assert list(out["feed"]) == ["sip", "sip", "schwab", "schwab"]


def test_no_candles_or_switch_off_returns_the_same_object():
    frame = tape([ts(29, 10, 0)])
    assert feed._overlay_schwab(frame, {}) is frame
    assert feed._overlay_schwab(frame, None) is frame
    original = feed.SCHWAB_OWNS_INTRADAY
    try:
        feed.SCHWAB_OWNS_INTRADAY = False
        assert feed._overlay_schwab(frame, {int(ts(29, 10, 0).timestamp()): candle(1)}) is frame
    finally:
        feed.SCHWAB_OWNS_INTRADAY = original


def test_no_duplicate_timestamps_and_sorted():
    frame = tape([ts(29, 10, 0), ts(29, 10, 30)], feed_mark=None).drop(columns=["feed"])
    candles = {int(ts(29, 10, 0).timestamp()): candle(1),
               int(ts(29, 10, 30).timestamp()): candle(2),
               int(ts(29, 11, 0).timestamp()): candle(3)}
    out = feed._overlay_schwab(frame, candles)
    assert out["timestamp"].is_monotonic_increasing
    assert not out["timestamp"].duplicated().any()
    assert list(out["volume"]) == [1, 2, 3]


def test_successful_restatement_removes_retracted_schwab_tail_only():
    frame = tape([ts(28, 16, 50), ts(29, 16, 30), ts(29, 16, 50)], feed_mark="schwab")
    supplemental = tape([ts(29, 20, 0)], feed_mark="boats")
    frame = pd.concat([frame, supplemental], ignore_index=True)
    candles = {int(ts(29, 16, 30).timestamp()): candle(100)}
    out = feed._overlay_schwab(frame, candles, requested_start=ts(29, 0, 0))
    assert hhmm(out) == ["28 16:50", "29 16:30", "29 20:00"]
    # A failed/empty request is not evidence that the previous tail vanished.
    assert feed._overlay_schwab(frame, {}, requested_start=ts(29, 0, 0)) is frame


def test_incremental_refresh_keeps_full_history_candle_values(monkeypatch):
    """A short Schwab query must not overwrite settled history after boot."""
    now = ts(29, 23, 0)
    starts = []

    class Client:
        def _get_price_history(self, symbol, timeframe, start, end):
            starts.append(start)
            # Reproduce the observed startDate-sensitive volume response.
            volume = 178649 if start.date() < ts(28, 0, 0).date() else 201372
            return tape([ts(29, 16, 0)], volume=volume)

    def alpaca(symbols, **kwargs):
        if kwargs['feed'] != 'sip':
            return {}
        return {s: [{'t': ts(29, 16, 0).isoformat(), 'o': 10, 'h': 11,
                     'l': 9, 'c': 10, 'v': 250000}] for s in symbols}

    monkeypatch.setattr(feed, 'SCHWAB_VOLUME_ENABLED', True)
    monkeypatch.setattr(feed, 'SCHWAB_OWNS_INTRADAY', True)
    # The full-depth same-start request lives in TOS mode only (2026-09-30).
    monkeypatch.setattr(feed, 'SCHWAB_ONLY', True)
    monkeypatch.setattr(feed, '_schwab_client', lambda: Client())
    monkeypatch.setattr(feed, '_fetch_batch', alpaca)
    # This test is about the request SHAPE; the 5-min re-read floor would
    # (correctly) skip the second read, so turn it off here.
    monkeypatch.setattr(feed, 'TOS_MIN_REFETCH_SECONDS', 0.0)
    feed.clear_cache()
    try:
        kwargs = dict(now=now, credentials=[feed.FeedCredential('test', 'x', 'y')], get=lambda: None)
        cold = feed.fetch_30m(['TEST'], **kwargs).bars['TEST']
        feed._CACHE.clear()  # expire TTL while retaining the incremental store
        warm = feed.fetch_30m(['TEST'], **kwargs).bars['TEST']
        assert len(starts) == 2
        assert starts[0] == starts[1]
        assert cold['volume'].iloc[-1] == warm['volume'].iloc[-1] == 178649
        assert not warm['timestamp'].duplicated().any()
    finally:
        feed.clear_cache()


# --- _sip_prices: consolidated last-sale rules for price, Schwab for volume ---

def _schwab(stamps, volume, close):
    return pd.DataFrame({"timestamp": [pd.Timestamp(t) for t in stamps], "open": close, "high": close,
                         "low": close, "close": close, "volume": volume, "feed": "schwab"})


def test_prior_reference_print_candle_is_dropped_and_prices_come_from_sip():
    # DOCU 2026-09-29: SIP has 16:35 at 66.655 and nothing at 16:50 (a lone "P"
    # print). Schwab has both; its 16:50 candle must go, its 16:35 price is SIP's.
    alpaca = tape([ts(29, 16, 30), ts(29, 16, 35), ts(29, 16, 55)])
    alpaca.loc[1, ["open", "high", "low", "close"]] = 66.655
    frame = _schwab([ts(29, 16, 35), ts(29, 16, 50), ts(29, 16, 55)], 100.0, 66.98)
    out = feed._sip_prices(frame, alpaca)
    assert hhmm(out) == ["29 16:35", "29 16:55"]
    assert out["close"].iloc[0] == 66.655
    assert list(out["volume"]) == [100.0, 100.0]          # volume stays Schwab's


def test_bars_outside_the_sip_window_are_left_alone():
    alpaca = tape([ts(29, 10, 0), ts(29, 10, 30)])
    frame = _schwab([ts(29, 10, 30), ts(29, 11, 0), ts(29, 21, 0)], 5.0, 50.0)
    out = feed._sip_prices(frame, alpaca)
    # 11:00 is newer than SIP's last bar (recency block); 21:00 is overnight.
    assert hhmm(out) == ["29 10:30", "29 11:00", "29 21:00"]
    assert out["close"].iloc[0] == 10.2 and out["close"].iloc[1] == 50.0


def test_overnight_alpaca_rows_are_not_treated_as_sip_evidence():
    alpaca = tape([ts(28, 21, 0), ts(29, 3, 0)])            # BOATS hours only
    frame = _schwab([ts(29, 2, 0)], 5.0, 50.0)
    assert feed._sip_prices(frame, alpaca) is frame


def test_sip_switch_off_or_no_alpaca_returns_same_object():
    frame = _schwab([ts(29, 10, 0)], 5.0, 50.0)
    assert feed._sip_prices(frame, None) is frame
    original = feed.SIP_OWNS_PRICES
    try:
        feed.SIP_OWNS_PRICES = False
        assert feed._sip_prices(frame, tape([ts(29, 10, 0)])) is frame
    finally:
        feed.SIP_OWNS_PRICES = original


def test_schwab_only_builds_every_tape_from_schwab_and_never_calls_alpaca(monkeypatch):
    """His order 2026-09-30: the scanner uses only TOS (Schwab) data."""
    calls = []

    class Client:
        def _get_price_history(self, symbol, timeframe, start, end):
            calls.append((symbol, timeframe))
            if timeframe == "1Day":
                # Schwab stamps daily candles at 01:00 ET (00:00 CT).
                return pd.DataFrame({"timestamp": [pd.Timestamp("2026-09-29 01:00", tz=ET),
                                                   pd.Timestamp("2026-09-30 00:00", tz=ET)],
                                     "open": [1.0, 2.0], "high": [1.5, 2.5], "low": [0.5, 1.5],
                                     "close": [1.2, 2.2], "volume": [100.0, 200.0]})
            return tape([ts(30, 16, 0)], volume=196406.0).assign(close=312.69)

    def alpaca(*args, **kwargs):
        raise AssertionError("Alpaca must not be called in SCHWAB_ONLY mode")

    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", True)
    monkeypatch.setattr(feed, "SCHWAB_ONLY", True)
    monkeypatch.setattr(feed, "_schwab_client", lambda: Client())
    monkeypatch.setattr(feed, "_fetch_batch", alpaca)
    monkeypatch.setattr(feed, "resolve_credentials", alpaca)
    feed.clear_cache()
    try:
        thirty = feed.fetch_30m(["TLN"], now=ts(30, 23, 0)).bars["TLN"]
        assert list(thirty["close"]) == [312.69] and list(thirty["feed"]) == ["schwab"]
        daily = feed.fetch_daily(["TLN"], now=ts(30, 23, 0)).bars["TLN"]
        assert [t.strftime("%m-%d %H:%M") for t in daily["timestamp"]] == ["09-29 00:00", "09-30 00:00"]
        assert {tf for _, tf in calls} == {"30Min", "1Day"}
    finally:
        feed.clear_cache()
