"""Unit: the LEAP setup rule (long_term/setups.py) on synthetic daily bars. No network.

The rule is the tested scan's (artifacts/scan_monthly_ema_touch.py): monthly 50 EMA of COMPLETED months,
uptrend = last close above a rising EMA, touched = a daily low this month at or below the level.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from long_term.setups import (REASON_NO_DAY, REASON_SHORT, STATE_KEYS, daily_bars, ema, monthly_from_daily,
                              setup_state)

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 8)                  # Sunday: November so far = Nov 2..6


def months_back(n, end=(2026, 10)):
    """The n calendar months ending at `end`, oldest first, as (year, month)."""
    y, m = end
    out = []
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out[::-1]


def bar(d, low, high, close, *, tz=ET, hour=1):
    """A Schwab-shaped daily bar: stamped 01:00 ET on its trading day."""
    return {"timestamp": datetime(d.year, d.month, d.day, hour, tzinfo=tz), "open": close, "low": low,
            "high": high, "close": close, "volume": 1.0}


def history(closes, end=(2026, 10)):
    """Three daily bars per completed month (5th, 15th, 25th); the month closes at `closes[i]`, and the
    month's lows / highs stay within 2% of the close."""
    rows = []
    for (y, m), c in zip(months_back(len(closes), end), closes):
        for day, px in ((5, c * 0.99), (15, c * 1.01), (25, c)):
            rows.append(bar(date(y, m, day), px * 0.99, px * 1.01, px))
    return rows


def rising(n=130, start=50.0, end=165.0):
    return [start + (end - start) * i / (n - 1) for i in range(n)]


def expected_level(closes):
    s = pd.Series(closes).ewm(span=50, adjust=False).mean()
    return float(s.iat[-1]), float(s.iat[-2])


def november(lows, closes=None):
    days = [date(2026, 11, d) for d in (2, 3, 4, 5, 6)][:len(lows)]
    closes = closes or [lo * 1.02 for lo in lows]
    return [bar(d, lo, c * 1.01, c) for d, lo, c in zip(days, lows, closes)]


# ---------- the rule ----------

def test_qcom_shaped_touch_in_an_uptrend_passes():
    closes = rising()
    level, prev = expected_level(closes)
    lows = [level + 6, level + 3, level - 2, level + 1, level + 4]     # Nov 4 dips through the level
    s = setup_state(history(closes) + november(lows), today=TODAY)
    assert set(s) == set(STATE_KEYS)
    assert s["level"] == pytest.approx(level, abs=1e-4) and s["prevLevel"] == pytest.approx(prev, abs=1e-4)
    assert s["lastClose"] == pytest.approx(closes[-1]) and s["lastMonth"] == "2026-10"
    assert s["closeAbove"] is True and s["emaRising"] is True and s["uptrend"] is True
    assert s["month"] == "2026-11" and s["touched"] is True and s["touchDate"] == "2026-11-04"
    assert s["monthLow"] == pytest.approx(round(level - 2, 4))
    assert s["pass"] is True and s["reason"] is None and s["asOf"] == "2026-11-06"


def test_last_close_below_the_level_is_no_uptrend_even_when_touched():
    closes = rising()
    closes[-1] = expected_level(closes[:-1])[0] * 0.9                  # October closed under the EMA
    level, _ = expected_level(closes)
    s = setup_state(history(closes) + november([level - 5]), today=TODAY)
    assert s["closeAbove"] is False and s["uptrend"] is False
    assert s["touched"] is True and s["pass"] is False


def test_falling_ema_is_no_uptrend():
    closes = rising()[::-1]                                             # 165 -> 50, EMA falling
    level, prev = expected_level(closes)
    assert level < prev
    s = setup_state(history(closes) + november([level - 1]), today=TODAY)
    assert s["emaRising"] is False and s["uptrend"] is False and s["pass"] is False


def test_touched_on_the_first_trading_day_of_the_month():
    closes = rising()
    level, _ = expected_level(closes)
    s = setup_state(history(closes) + november([level - 0.5, level + 5, level + 5]), today=TODAY)
    assert s["touched"] is True and s["touchDate"] == "2026-11-02" and s["pass"] is True


def test_low_exactly_at_the_level_counts_as_a_touch():
    closes = rising()
    level = ema(closes)[-1]          # the module's own float (pandas can differ in the last bit)
    s = setup_state(history(closes) + november([level + 4, level]), today=TODAY)
    assert s["touched"] is True and s["touchDate"] == "2026-11-03"


def test_not_touched_reports_the_month_low():
    closes = rising()
    level, _ = expected_level(closes)
    s = setup_state(history(closes) + november([level + 4, level + 2.5, level + 7]), today=TODAY)
    assert s["touched"] is False and s["touchDate"] is None and s["pass"] is False
    assert s["monthLow"] == pytest.approx(round(level + 2.5, 4)) and s["uptrend"] is True


def test_first_day_of_the_month_with_no_bar_yet_is_not_touched():
    closes = rising()
    s = setup_state(history(closes), today=date(2026, 11, 1))           # Sunday Nov 1: no November bar
    assert s["month"] == "2026-11" and s["lastMonth"] == "2026-10"
    assert s["touched"] is False and s["monthLow"] is None and s["pass"] is False
    assert s["reason"] == REASON_NO_DAY and s["uptrend"] is True and s["level"] is not None


def test_fewer_than_100_completed_months_is_none_with_the_reason():
    closes = rising(99)
    level, _ = expected_level(closes)
    s = setup_state(history(closes) + november([level - 5]), today=TODAY)
    assert s["pass"] is None and s["reason"] == REASON_SHORT and s["level"] is None
    assert s["touched"] is False and s["uptrend"] is False
    s100 = setup_state(history(rising(100)) + november([1.0]), today=TODAY)
    assert s100["pass"] is not None and s100["reason"] is None


def test_the_current_month_is_never_part_of_the_ema():
    closes = rising()
    level, _ = expected_level(closes)
    crash = november([level * 0.5], closes=[level * 0.5])                # a huge November drop
    s = setup_state(history(closes) + crash, today=TODAY)
    assert s["level"] == pytest.approx(level, abs=1e-4) and s["lastMonth"] == "2026-10"


def test_split_adjusted_input_scales_the_level_and_keeps_the_verdict():
    """Schwab daily bars come split-adjusted; the rule is scale-free, so a 4:1-adjusted copy of the same
    history gives a quarter of the level and the same verdict (no split repair happens here)."""
    closes = rising()
    level, _ = expected_level(closes)
    rows = history(closes) + november([level + 3, level - 1])
    quarter = [dict(r, low=r["low"] / 4, high=r["high"] / 4, close=r["close"] / 4, open=r["open"] / 4) for r in rows]
    a, b = setup_state(rows, today=TODAY), setup_state(quarter, today=TODAY)
    assert b["level"] == pytest.approx(a["level"] / 4, abs=1e-3)
    assert (a["pass"], b["pass"]) == (True, True) and a["touchDate"] == b["touchDate"]


def test_dataframe_and_dict_input_agree():
    closes = rising()
    level, _ = expected_level(closes)
    rows = history(closes) + november([level + 3, level - 1])
    frame = pd.DataFrame(rows)
    frame["timestamp"] = pd.to_datetime(frame["timestamp"].map(lambda t: t.isoformat()), utc=True).dt.tz_convert(ET)
    assert setup_state(frame, today=TODAY) == setup_state(rows, today=TODAY)
    assert monthly_from_daily(frame) == monthly_from_daily(rows)


def test_et_month_boundary_for_utc_stamped_bars():
    """A bar stamped 2026-10-01 03:00 UTC is Sept 30 23:00 ET: it belongs to SEPTEMBER."""
    rows = [bar(date(2026, 9, 29), 9, 11, 10, tz=timezone.utc, hour=12),
            {"timestamp": datetime(2026, 10, 1, 3, tzinfo=timezone.utc), "low": 19, "high": 21, "close": 20},
            bar(date(2026, 10, 2), 29, 31, 30)]
    m = monthly_from_daily(rows)
    assert [x["period"] for x in m] == ["2026-09", "2026-10"]
    assert m[0]["close"] == 20 and m[0]["high"] == 21 and m[1]["close"] == 30
    # Schwab's own stamps (01:00 ET, which is 05:00/06:00 UTC) stay on their day
    assert daily_bars([bar(date(2026, 11, 2), 1, 2, 1.5)])[0][0] == date(2026, 11, 2)
    assert daily_bars([{"timestamp": "2026-11-01T02:00:00Z", "low": 1, "high": 2, "close": 1.5}])[0][0] == \
        date(2026, 10, 31)


def test_monthly_bars_take_min_low_max_high_and_last_close():
    rows = [bar(date(2026, 3, 2), 10, 12, 11), bar(date(2026, 3, 16), 8, 15, 9), bar(date(2026, 3, 31), 9, 13, 12.5),
            bar(date(2026, 4, 1), 12, 14, 13)]
    assert monthly_from_daily(rows) == [{"period": "2026-03", "low": 8, "high": 15, "close": 12.5},
                                        {"period": "2026-04", "low": 12, "high": 14, "close": 13}]


def test_bad_rows_duplicates_and_future_bars_are_ignored():
    closes = rising()
    level, _ = expected_level(closes)
    rows = history(closes) + november([level + 3])
    rows += [rows[-1], {"timestamp": None, "low": 1, "high": 1, "close": 1},
             {"timestamp": datetime(2026, 11, 5, 1, tzinfo=ET), "low": 0, "high": 1, "close": 1},
             {"timestamp": datetime(2026, 11, 5, 1, tzinfo=ET), "low": "x", "high": 1, "close": 1},
             bar(date(2026, 11, 20), level - 9, level, level)]          # after `today`: not seen yet
    s = setup_state(rows, today=TODAY)
    assert s["touched"] is False and s["asOf"] == "2026-11-02"


def test_no_usable_bars_is_none():
    assert setup_state([], today=TODAY) is None
    assert setup_state(None, today=TODAY) is None
    assert setup_state(pd.DataFrame(), today=TODAY) is None


def test_ema_matches_pandas_ewm_adjust_false():
    vals = [3.0, 5.0, 4.0, 10.0, 7.5, 8.25]
    assert ema(vals, 3) == pytest.approx(list(pd.Series(vals).ewm(span=3, adjust=False).mean()))


def test_floats_are_rounded_to_4_places():
    closes = [c + 0.123456789 for c in rising()]
    level, _ = expected_level(closes)
    s = setup_state(history(closes) + november([level - 1.000033]), today=TODAY)
    for k in ("level", "prevLevel", "lastClose", "monthLow"):
        assert s[k] == round(s[k], 4)
