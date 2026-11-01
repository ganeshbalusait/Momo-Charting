"""momx/live_bolt.py - the per-second ⚡ state (ScannerX3 model)."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from momx import live_bolt as lb

ET = ZoneInfo("America/New_York")


def falling_then_flat(n=80, start="2026-09-25T09:30"):
    """A tape that drifted DOWN (EMA9 < EMA20, MACD under its signal) and then
    went flat, so a pop in the forming bar produces a fresh cross up."""
    t0 = int(datetime.fromisoformat(start).replace(tzinfo=ET).timestamp()) - n * 300
    out = []
    for i in range(n):
        c = 100 - i * 0.05 if i < n - 10 else 100 - (n - 10) * 0.05
        out.append({"time": t0 + i * 300, "open": c + 0.02, "high": c + 0.05, "low": c - 0.05, "close": c, "volume": 1000 + (i % 5) * 100})
    # make the last completed CLOCK hour close up (the 1h leg gate)
    return out


def seed_for(bars, rvol4h=1.5):
    s = lb.seed_from_bars(bars)
    s["leg1h"] = True
    s["rvol4h"] = rvol4h
    return s


def test_seed_has_the_state_the_live_step_needs():
    s = lb.seed_from_bars(falling_then_flat())
    for key in ("ema4", "ema8", "ema9", "ema20", "ema12", "ema26", "macd", "macdSignal", "closes19", "trs19",
                "sqzOn", "volMean", "volSd", "hourHighs", "hourLows", "avgHourRange", "leg1h", "t", "close"):
        assert key in s
    assert len(s["closes19"]) == 19 and len(s["hourHighs"]) == 11
    assert lb.seed_from_bars(falling_then_flat()[:30]) is None


def test_a_pop_in_the_forming_bar_fires_crosses_and_passes_gates():
    s = seed_for(falling_then_flat())
    last = s["close"]
    ev = lb.evaluate(s, {"open": last, "high": last * 1.02, "low": last, "close": last * 1.02, "volume": 5000}, 1.5)
    assert "9x20" in ev["families"] and "4x8" in ev["families"]
    assert any(f.startswith("RVOL") for f in ev["families"])
    assert all(ev["gates"].values())
    assert ev["hlPos"] == 1.0


def test_gates_block_cheap_names_and_weak_4h_volume():
    s = seed_for(falling_then_flat(), rvol4h=0.2)
    ev = lb.evaluate(s, {"open": 3, "high": 3.1, "low": 3, "close": 3.1, "volume": 1}, 0.2)
    assert ev["gates"]["price"] is False and ev["gates"]["vol4h"] is False


def test_book_lights_fades_and_evicts(tmp_path):
    bars = falling_then_flat()
    s = seed_for(bars)
    book = lb.LiveBoltBook(tmp_path)
    t = datetime.fromtimestamp(s["t"] + 300 + 60, ET)       # inside the next 5m bucket, in session
    last = s["close"]
    book.step({"X": s}, {"X": {"last": last, "totalVolume": 10_000}}, t)
    state = book.step({"X": s}, {"X": {"last": last * 1.02, "totalVolume": 16_000}}, t + timedelta(seconds=1))
    assert state["X"]["on"] is True and state["X"]["opacity"] == 1.0
    assert "9x20" in state["X"]["families"]
    # an hour later, still in the upper half of the hour -> on, faded
    later = book.step({"X": s}, {"X": {"last": last * 1.02, "totalVolume": 16_000}}, t + timedelta(hours=1))
    assert later["X"]["on"] is True and 0.25 < later["X"]["opacity"] < 1.0
    # price falls into the lower half of the hour range -> evicted
    low = book.step({"X": s}, {"X": {"last": last * 0.98, "totalVolume": 16_000}}, t + timedelta(hours=1, seconds=1))
    assert low["X"]["on"] is False and low["X"]["firedAt"]
    # the day's fires survive a restart
    again = lb.LiveBoltBook(tmp_path)
    back = again.step({"X": s}, {"X": {"last": last * 1.02, "totalVolume": 16_000}}, t + timedelta(hours=1, seconds=2))
    assert back["X"]["firstAt"] == state["X"]["firstAt"]


def test_no_fire_outside_the_session(tmp_path):
    s = seed_for(falling_then_flat())
    book = lb.LiveBoltBook(tmp_path)
    night = datetime(2026, 9, 25, 18, 0, tzinfo=ET)
    out = book.step({"X": s}, {"X": {"last": s["close"] * 1.05, "totalVolume": 50_000}}, night)
    assert out["X"]["on"] is False and out["X"]["firedAt"] is None


def test_roll_seed_advances_one_completed_bar():
    s = seed_for(falling_then_flat())
    bar = {"open": s["close"], "high": s["close"] + 1, "low": s["close"], "close": s["close"] + 1, "volume": 2000}
    r = lb.roll_seed(s, bar, s["t"] + 300)
    assert r["t"] == s["t"] + 300 and r["close"] == s["close"] + 1
    assert r["ema9"] > s["ema9"] and r["closes19"][-1] == s["close"] + 1 and len(r["closes19"]) == 19
    assert r["hourHighs"][-1] == s["close"] + 1


def test_macd_cross_up_fires():
    s = seed_for(falling_then_flat())
    s = {**s, "macd": -0.01, "macdSignal": 0.0, "ema12": 100.0, "ema26": 100.01}
    ev = lb.evaluate(s, {"open": 100, "high": 101, "low": 100, "close": 101, "volume": 1}, 1.5)
    assert "MACD" in ev["families"]


def test_bolt_runs_all_session_and_skips_etfs():
    """2026-09-28: the bolt only ran while a MomX page polled, so the morning's
    bolts were never recorded. It now runs 09:25-16:05 ET on weekdays."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    import api_server
    et = ZoneInfo("America/New_York")
    assert api_server._live_bolt_session_open(datetime(2026, 9, 28, 9, 30, tzinfo=et)) is True
    assert api_server._live_bolt_session_open(datetime(2026, 9, 28, 16, 30, tzinfo=et)) is False
    assert api_server._live_bolt_session_open(datetime(2026, 9, 27, 10, 0, tzinfo=et)) is False   # Sunday


def test_snapshot_lists_the_days_fires_without_a_step(tmp_path):
    import json
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from momx import live_bolt as lb
    et = ZoneInfo("America/New_York")
    folder = tmp_path / "momx_live_bolt"
    folder.mkdir()
    fired = datetime(2026, 9, 28, 12, 6, 16, tzinfo=et).timestamp()
    (folder / "2026-09-28.json").write_text(json.dumps({"date": "2026-09-28", "fires": {
        "AMD": {"firstAt": fired, "lastAt": fired, "families": ["9x20"], "count": 1}}}), encoding="utf-8")
    book = lb.LiveBoltBook(tmp_path)
    book.load_day(datetime(2026, 9, 28, 20, 10, tzinfo=et))
    snap = book.snapshot()
    assert snap["AMD"]["firedAt"].startswith("2026-09-28T12:06") and snap["AMD"]["on"] is False


def test_premarket_hold_keeps_the_stream_from_0700_to_0925_on_weekdays():
    """2026-10-06: with no screen open, 145 Watchlist symbols fell off the stream
    08:03-09:25. Ganesh: keep them streaming from 07:00 without a screen."""
    import api_server
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    hold = api_server._live_bolt_premarket_hold
    assert hold(datetime(2026, 10, 6, 6, 59, tzinfo=et)) is False
    assert hold(datetime(2026, 10, 6, 7, 0, tzinfo=et)) is True
    assert hold(datetime(2026, 10, 6, 9, 24, tzinfo=et)) is True
    assert hold(datetime(2026, 10, 6, 9, 25, tzinfo=et)) is False       # session rule takes over
    assert hold(datetime(2026, 10, 4, 8, 0, tzinfo=et)) is False        # Sunday


def test_premarket_hold_leases_without_stepping_the_bolt(monkeypatch):
    """Leases are kept, but the bolt does not step (or record) before 09:25 without a screen."""
    import api_server
    acquired, stepped = [], []

    class Stream:
        def acquire_equities(self, symbols): acquired.extend(symbols)
        def release_equities(self, symbols): raise AssertionError("premarket hold must not release")
        def latest_equities(self, symbols): return {}

    class Book:
        def step(self, *args): stepped.append(args)

    monkeypatch.setattr(api_server, "MARKET_STREAM", Stream())
    monkeypatch.setattr(api_server, "_live_bolt_session_open", lambda now=None: False)
    monkeypatch.setattr(api_server, "_live_bolt_premarket_hold", lambda now=None: True)
    monkeypatch.setattr(api_server, "_live_bolt_fetch_seeds", lambda: {"AAPL": {}, "NVDA": {}})
    monkeypatch.setattr(api_server, "_live_bolt_is_etf", lambda s: False)
    monkeypatch.setitem(api_server._LIVE_BOLT, "book", Book())
    monkeypatch.setitem(api_server._LIVE_BOLT, "lastRequest", 0.0)
    monkeypatch.setitem(api_server._LIVE_BOLT, "seedsAt", 0.0)
    monkeypatch.setitem(api_server._LIVE_BOLT, "leased", frozenset())
    calls = {"n": 0}

    def fake_sleep(seconds):
        calls["n"] += 1
        if calls["n"] > 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(api_server.time, "sleep", fake_sleep)
    try:
        api_server._live_bolt_loop()
    except KeyboardInterrupt:
        pass
    assert sorted(acquired) == ["AAPL", "NVDA"]
    assert api_server._LIVE_BOLT["leased"] == frozenset({"AAPL", "NVDA"})
    assert stepped == []
