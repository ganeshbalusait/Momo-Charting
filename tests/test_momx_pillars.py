"""momx/momentum.py pillars: EMA 9/21/50 ribbon, 30m ribbon, MACD, stochastic, levels."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momx import momentum as mm

ET = ZoneInfo("America/New_York")


def bars_rising(n=120, start="2026-09-24T09:30", step=0.1):
    t0 = int(datetime.fromisoformat(start).replace(tzinfo=ET).timestamp())
    out = []
    for i in range(n):
        c = 100 + i * step
        out.append({"time": t0 + i * 300, "open": c - 0.05, "high": c + 0.1, "low": c - 0.1, "close": c, "volume": 1000})
    return out


def test_ribbon_reads_bull_bear_and_mixed():
    assert mm._ribbon([100 + i * 0.1 for i in range(80)])["trend"] == "bull"
    assert mm._ribbon([100 - i * 0.1 for i in range(80)])["trend"] == "bear"
    assert mm._ribbon([100 + i * 0.1 for i in range(60)] + [106 - i * 0.3 for i in range(12)])["trend"] == "mixed"
    assert mm._ribbon([100.0] * 20) is None


def test_thirty_minute_closes_drop_the_forming_bucket():
    bars = bars_rising(13)  # 09:30 .. 10:30 -> two full 30m buckets + one 5m bar
    closes = mm._thirty_minute_closes(bars)
    assert len(closes) == 2 and closes[0] == bars[5]["close"]


def test_macd_cross_and_stochastic_and_levels():
    down = [{"time": b["time"], "open": 200 - i * 0.2, "high": 200 - i * 0.2 + 0.1, "low": 200 - i * 0.2 - 0.1,
             "close": 200 - i * 0.2, "volume": 1} for i, b in enumerate(bars_rising(60))]
    up = bars_rising(20, start="2026-09-25T04:00")
    for i, b in enumerate(up):
        b["close"] = down[-1]["close"] + (i + 1) * 0.5
        b["high"], b["low"] = b["close"] + 0.1, b["close"] - 0.1
    bars = down + up
    closes = [b["close"] for b in bars]
    cross_up, _ = mm._macd_crosses(bars, closes)
    assert cross_up is not None and cross_up >= up[0]["time"]
    st = mm._stochastic(bars)
    assert st["k"] > 80
    levels = mm._session_levels(bars)
    assert levels["prevHigh"] == round(max(b["high"] for b in down if datetime.fromtimestamp(b["time"], ET).hour < 16), 4)
    assert levels["premarketHigh"] == round(max(b["high"] for b in up if datetime.fromtimestamp(b["time"], ET).hour * 60
                                          + datetime.fromtimestamp(b["time"], ET).minute < 9 * 60 + 30), 4)


def test_summarize_carries_pillars():
    out = mm.summarize(bars_rising(120), {}, now_epoch=bars_rising(120)[-1]["time"] + 600)
    assert out["pillars"]["ribbon5m"]["trend"] == "bull"


def test_pillars_only_on_the_bull_pass_and_not_in_the_archive():
    bars = bars_rising(120)
    bear = mm.summarize(bars, {}, now_epoch=bars[-1]["time"] + 600, direction="bear")
    # The bear pass carries only the bear ZS read (2026-09-28), never the Five Pillars.
    assert bear["pillars"] is None or set(bear["pillars"]) == {"zs"}
    from momx import history
    assert ("m5", "pillars") in history.GRADE_STRIPPED_SUBFIELDS


def test_archive_row_drops_pillars_but_keeps_the_rest_of_m5():
    from momx import history
    row = {"symbol": "X", "m5": {"state": "holding", "lastCompleted": {"close": 1}, "pillars": {"stoch": {"k": 1}}}}
    out = history._archive_row(row, strip_bulk=False)
    assert out["m5"] == {"state": "holding"}
