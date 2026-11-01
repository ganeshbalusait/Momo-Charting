"""momentum._zs_rule - his ZS entry read on the 5m tape (2026-09-28)."""
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import momentum as m

ET = ZoneInfo("America/New_York")


def tape(last_close, days=2):
    start = datetime(2026, 9, 25, 9, 30, tzinfo=ET).timestamp()
    bars, p = [], 100.0
    for d in range(days):
        for k in range(78):
            t = start + d * 3 * 86400 + k * 300          # Fri, then Mon
            bars.append({"time": int(t), "open": p, "high": p + 0.1, "low": p - 0.1, "close": p, "volume": 1000})
    bars[-1] = {**bars[-1], "close": last_close, "high": last_close}
    return bars


def test_above_everything_and_a_clear_path():
    zs = m._zs_rule(tape(100.5))
    assert zs["above"] is True
    assert "dH" not in zs["blockers"]            # the bar's own high is not ABOVE its close here


def test_blocked_by_a_level_within_two_percent():
    bars = tape(100.5)
    bars[80] = {**bars[80], "high": 101.5}       # a higher high earlier today = dH above the close
    zs = m._zs_rule(bars)
    assert "dH" in zs["blockers"] and zs["clear2"] is False


def test_below_a_line_is_not_above():
    assert m._zs_rule(tape(99.0))["above"] is False


def test_outside_the_session_nothing_is_read():
    bars = tape(100.5)
    late = datetime(2026, 9, 28, 17, 0, tzinfo=ET).timestamp()
    bars.append({"time": int(late), "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1})
    assert m._zs_rule(bars) is None



def test_bear_zs_reads_below_everything():
    bars = tape(99.5)
    bars[-1] = {**bars[-1], "close": 99.5, "low": 99.5}
    zs = m._zs_rule(bars, bear=True)
    assert zs["direction"] == "bear" and zs["above"] is True     # below every line
    assert m._zs_rule(tape(100.5), bear=True)["above"] is False
