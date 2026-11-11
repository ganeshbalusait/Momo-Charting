"""Extended-hours bad prints (2026-11-11): the Python port must match the JS.

SPY's 16:55 ET five-minute candle was drawn from $711 to $760 off one bad
trade print. The real SPY minutes from that evening are the fixture.
"""
import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

import chart_aggregation
from chart_bad_prints import is_extended_hours_time, remove_extended_hours_bad_prints

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "spy_2026_11_11_bad_prints.json"
SPY = json.loads(FIXTURE.read_text(encoding="utf-8"))["bars"]


def et(hour, minute):
    # 2026-11-11 is EST (UTC-5).
    return int(datetime(2026, 11, 11, hour + 5, minute, tzinfo=timezone.utc).timestamp())


def flat(start, count, price=100.0):
    return [{"time": start + i * 60, "open": price, "high": price + 0.02, "low": price - 0.02,
             "close": price, "volume": 1000} for i in range(count)]


def test_real_spy_bad_prints_are_removed_and_nothing_else_changes():
    cleaned = remove_extended_hours_bad_prints(SPY)
    changed = [bar["time"] for bar, original in zip(cleaned, SPY) if bar is not original]
    assert changed == [et(16, 15), et(16, 28), et(16, 38), et(16, 55)]
    by_time = {bar["time"]: bar for bar in cleaned}
    assert all(by_time[t]["low"] > 759 and by_time[t]["open"] > 759 for t in changed)


def test_the_display_builder_draws_no_711_candle():
    five = chart_aggregation.build_chart_display_bars(live_bars=SPY, aggregation_minutes=5)
    assert all(bar["low"] > 755 for bar in five if bar["time"] >= et(16, 0))


def test_a_real_after_hours_drop_and_a_fresh_newest_move_are_kept():
    drop = flat(et(16, 0), 5) + [{"time": et(16, 5), "open": 100, "high": 100, "low": 93.5, "close": 94, "volume": 9e5}] + flat(et(16, 6), 15, 94)
    assert remove_extended_hours_bad_prints(drop) is drop
    fresh = flat(et(16, 0), 12) + [{"time": et(16, 12), "open": 100, "high": 100, "low": 93.9, "close": 94, "volume": 5e4}]
    assert remove_extended_hours_bad_prints(fresh) is fresh


def test_regular_session_and_penny_ticks_are_left_alone():
    rth = flat(et(10, 0), 10) + [{"time": et(10, 10), "open": 100, "high": 100, "low": 90, "close": 100, "volume": 5000}] + flat(et(10, 11), 10)
    assert remove_extended_hours_bad_prints(rth) is rth
    penny = flat(et(7, 30), 10, 0.77) + [{"time": et(7, 40), "open": 0.78, "high": 0.78, "low": 0.77, "close": 0.77, "volume": 5e4}] + flat(et(7, 41), 10, 0.77)
    assert remove_extended_hours_bad_prints(penny) is penny
    assert is_extended_hours_time(et(9, 29)) and not is_extended_hours_time(et(9, 30))
    assert not is_extended_hours_time(et(15, 59)) and is_extended_hours_time(et(16, 0))


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_python_and_browser_clean_the_same_minutes_the_same_way():
    script = (
        "import { readFileSync } from 'node:fs';"
        "import { removeExtendedHoursBadPrints } from './frontend/src/chartBadPrints.js';"
        "const bars = JSON.parse(readFileSync(process.argv[1], 'utf8')).bars;"
        "process.stdout.write(JSON.stringify(removeExtendedHoursBadPrints(bars)));"
    )
    out = subprocess.run(
        ["node", "--input-type=module", "-e", script, str(FIXTURE)],
        cwd=ROOT, capture_output=True, text=True, timeout=60, check=True,
    ).stdout
    browser = json.loads(out)
    server = remove_extended_hours_bad_prints(SPY)
    assert len(browser) == len(server)
    for left, right in zip(browser, server):
        for field in ("time", "open", "high", "low", "close"):
            assert left[field] == pytest.approx(right[field]), (left["time"], field)
