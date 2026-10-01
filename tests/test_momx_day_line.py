"""His daily-line entry (2026-09-29): momx.momentum._day_line + the bot's dayLine rule."""
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import momentum, option_track as ot

ET = ZoneInfo("America/New_York")


def session(day, start_price, step, *, first_open=None, vols=None, n=78):
    t0 = datetime(2026, 9, day, 9, 30, tzinfo=ET).timestamp()
    out, p = [], start_price
    for i in range(n):
        o = first_open if (i == 0 and first_open is not None) else p
        c = o + step
        out.append({"time": int(t0 + 300 * i), "open": o, "high": max(o, c) + 0.05, "low": min(o, c) - 0.05,
                    "close": c, "volume": (vols[i] if vols and i < len(vols) else 1000)})
        p = c
    return out


def test_gap_above_prev_high_then_above_ema_with_momentum_fires_calls():
    prev = session(25, 100.0, 0.01)                         # prev high ~100.83
    today = session(28, 0, 0.05, first_open=102.0, n=6)     # opens above it, keeps rising
    d = momentum._day_line(prev + today, bear=False)
    assert d["bias"] and d["side"] and d["mom"] and d["signal"]
    assert d["prevHigh"] > 100 and d["open"] == 102.0 and d["dollarVol"] > 0
    assert momentum._day_line(prev + today, bear=True)["signal"] is False


def test_gap_below_prev_low_falling_fires_puts_and_window_is_0935_1100():
    prev = session(25, 100.0, 0.0)
    today = session(28, 0, -0.05, first_open=98.0, n=6)
    assert momentum._day_line(prev + today, bear=True)["signal"] is True
    late = session(28, 0, -0.05, first_open=98.0, n=40)     # last bar 12:45 -> not read
    assert momentum._day_line(prev + late, bear=True) is None


def test_gap_down_then_first_candle_back_above_prev_low_is_a_reclaim():
    prev = session(25, 100.0, 0.0)                          # prev low 99.95
    today = session(28, 0, 0.3, first_open=98.0, n=6)       # 09:30 bar 98.0 -> 98.3? keep climbing
    today[0]["close"] = 100.5; today[0]["high"] = 100.6
    d = momentum._day_line(prev + today, bear=False)
    assert d["reclaim"] is True and d["bias"] is False
    now = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    assert ot.day_line_now({"symbol": "ZS", "m5": {"dayLine": d}}, now, "reclaim")


def test_open_inside_yesterday_never_fires():
    prev = session(25, 100.0, 0.02)
    today = session(28, 0, 0.05, first_open=100.5, n=6)
    assert momentum._day_line(prev + today, bear=False)["signal"] is False


def test_bot_takes_day_line_only_on_the_biggest_stocks():
    now = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    bar = datetime(2026, 9, 28, 9, 50, tzinfo=ET).timestamp()
    def row(sym, dollar, signal=True):
        return {"symbol": sym, "last": 100.0, "m5": {"dayLine": {"signal": signal, "barAt": bar, "dollarVol": dollar}}}
    rows = [row(f"B{i}", 1e9 + i) for i in range(ot.DAY_LINE_MEGA)] + [row("SMALL", 1e6)]
    mega = ot.mega_caps(rows)
    assert len(mega) == ot.DAY_LINE_MEGA and "SMALL" not in mega
    assert ot.day_line_now(rows[0], now) and not ot.day_line_now(row("X", 1e9, False), now)
    assert not ot.day_line_now({**rows[0], "etf": True}, now)


def test_setup_stamp_comes_from_the_days_trades(tmp_path):
    book = ot.OptionTrackBook(tmp_path, background=False, fetch=lambda s: None)
    now = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    bar = datetime(2026, 9, 28, 9, 50, tzinfo=ET).timestamp()
    rows = [{"symbol": "ZS", "last": 190.0, "m5": {"dayLine": {"reclaim": True, "signal": False, "barAt": bar, "dollarVol": 1}}},
            {"symbol": "OLD", "last": 5.0, "dayLines": {"line": "2026-09-25T09:40:00-04:00"}, "m5": {}}]
    book.apply("Watchlist", {"rows": rows}, now)
    assert rows[0]["dayLines"]["reclaim"].startswith("2026-09-28T10:00")
    assert "dayLines" not in rows[1]


def test_dollar100_version_needs_price_and_half_dollar_room():
    prev = session(25, 150.0, 0.01)                         # prev high ~150.83, close ~150.78
    today = session(28, 0, 0.05, first_open=152.0, n=6)
    d = momentum._day_line(prev + today, bear=False)
    assert d["signal"] and d["clear050"] and d["signal100"]
    cheap = momentum._day_line(session(25, 50.0, 0.01) + session(28, 0, 0.05, first_open=52.0, n=6), bear=False)
    assert cheap["signal"] and not cheap["signal100"]           # under $100
    older = session(24, 140.0, 0.0)                         # two sessions back...
    older[10] = {**older[10], "high": today[-1]["close"] + 0.3}   # ...its high sits $0.30 above the price
    d2 = momentum._day_line(older + prev + today, bear=False)
    assert d2["signal"] and not d2["clear050"] and not d2["signal100"]


def test_spy_and_qqq_are_the_only_etfs_the_daily_line_takes(tmp_path):
    now = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    bar = datetime(2026, 9, 28, 9, 50, tzinfo=ET).timestamp()
    line = {"signal": True, "signal100": True, "reclaim": True, "barAt": bar, "dollarVol": 1}
    for sym, ok in (("SPY", True), ("QQQ", True), ("TQQQ", False), ("IBIT", False)):
        r = {"symbol": sym, "etf": True, "m5": {"dayLine": line}}
        assert ot.day_line_now(r, now) is ok and ot.day_line_now(r, now, "reclaim") is ok, sym
    book = ot.OptionTrackBook(tmp_path, background=False, fetch=lambda s: None)
    rows = [{"symbol": "SPY", "etf": True, "last": 760.0, "m5": {"dayLine": line}},
            {"symbol": "TQQQ", "etf": True, "last": 90.0, "m5": {"dayLine": line}}]
    book.apply("Watchlist", {"rows": rows}, now)
    rules = {(t["rule"], t["symbol"]) for t in book.trades()}
    assert {("dayLine", "SPY"), ("dayLine100", "SPY"), ("dayReclaim", "SPY")} <= rules
    assert not any(s == "TQQQ" for _, s in rules)


def test_squeeze_momentum_line_colours_like_the_chart():
    up = session(28, 100.0, 0.05, n=60)
    up[-1] = {**up[-1], "close": up[-1]["close"] + 1.0, "high": up[-1]["close"] + 1.1}
    m = momentum._sqz_momentum(up)
    assert m["value"] > 0 and m["value"] > m["prev"] and m["color"] == "cyan"
    down = session(28, 100.0, -0.05, n=60)
    down[-1] = {**down[-1], "close": down[-1]["close"] - 1.0, "low": down[-1]["close"] - 1.1}
    assert momentum._sqz_momentum(down)["color"] == "magenta"
    assert momentum._sqz_momentum(up[:30]) is None
