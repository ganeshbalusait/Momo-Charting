"""Integration (2026-11-20): Fib on Daily / Weekly / Monthly charts.

The chart's daily tape goes through the real GET /api/oi-finder-chart route
over HTTP (same seeded serve path as test_cov_charts-studies_chart_payload_api),
and the body the browser would receive is fed to the real frontend modules
(chartAggregation.js + chartFibSwing.js) under node. Checks that the route
ships enough daily history for a 60-month swing and a 20-month AutoFib, and
that the levels come out where the swing says.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from api_harness import running_api

ROOT = Path(__file__).resolve().parents[1]
RUNNER = Path(__file__).with_name("fib_swing_runner.mjs")
ET = ZoneInfo("America/New_York")
SYMBOL = "EA"

_spec = importlib.util.spec_from_file_location(
    "chart_payload_api", Path(__file__).with_name("test_cov_charts-studies_chart_payload_api.py"),
)
_payload_api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_payload_api)
ServeState = _payload_api.ServeState
_session_bars = _payload_api._session_bars

LOW_DAY = 300     # 2021-01 + 300 days: the swing low, 14.00
HIGH_DAY = 1700   # the swing high, 240.80, after the low -> an up swing


def _daily_tape() -> list[dict]:
    """~5.5 years of daily candles at Eastern midnight with one known swing."""
    start = int(datetime(2021, 6, 1, tzinfo=ET).timestamp())
    rows = []
    for day in range(2000):
        price = 60 + 120 * day / 2000
        low, high = price - 2, price + 2
        if day == LOW_DAY:
            low = 14.0
        if day == HIGH_DAY:
            high = 240.8
        rows.append({
            "time": start + day * 86_400,
            "open": price, "high": high, "low": low, "close": price, "volume": 1000,
        })
    return rows


def _payload() -> dict:
    return {
        "symbol": SYMBOL,
        "source": "fixture",
        "bars": _session_bars(datetime(2026, 11, 16, tzinfo=ET), 2, 1),
        "fineStudyBars": [],
        "studyBars": [],
        "dailyBars": _daily_tape(),
        "historyLoading": False,
        "error": "",
    }


def _run_frontend(body: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix="agx-fib-") as folder:
        path = Path(folder) / "payload.json"
        path.write_text(json.dumps(body), encoding="utf-8")
        done = subprocess.run(
            ["node", str(RUNNER), str(path)],
            cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
        )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def _weekday_tape() -> list[dict]:
    """~8 years of TRADING-day candles (no weekends), low 14.00 about 4.4
    years back and high 240.80 about 1.2 years back: both inside 60 months."""
    rows = []
    day = datetime(2018, 11, 1, tzinfo=ET)
    while len(rows) < 2080:
        if day.weekday() < 5:
            index = len(rows)
            price = 60 + 120 * index / 2080
            low = 14.0 if index == 2080 - 1100 else price - 2
            high = 240.8 if index == 2080 - 300 else price + 2
            rows.append({"time": int(day.timestamp()), "open": price, "high": high, "low": low, "close": price, "volume": 1000})
        day = datetime.fromtimestamp(day.timestamp() + 86_400, tz=ET)
    return rows


def test_first_paint_carries_five_years_of_daily_candles_for_the_monthly_fib():
    """A phone's 5m chart keeps the first-paint tape; its M Fib needs 60 months."""
    payload = {**_payload(), "dailyBars": _weekday_tape()}
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true")
    assert status == 200, body
    assert body["initialSlim"] is True
    daily = body["dailyBars"]
    assert len(daily) >= 1260, len(daily)
    result = _run_frontend({**body, "bars": body["bars"]})
    # The default Monthly swing reads the same from the first paint as from the full tape.
    assert result["D"]["mFib50"] == "M Fib 50% (127.40)", result["D"]["mFib50"]


def test_route_daily_tape_gives_monthly_weekly_daily_fib_levels():
    payload = _payload()
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}")
    assert status == 200, body
    assert len(body["dailyBars"]) == 2000, "the full chart reply must carry the whole daily tape"

    result = _run_frontend(body)
    monthly, weekly, daily = result["M"], result["W"], result["D"]

    # Monthly: 60 months back reaches both ends of the known swing.
    assert monthly["candles"] >= 60
    assert monthly["swing"]["direction"] == "up"
    assert monthly["swing"]["low"] == 14.0 and monthly["swing"]["high"] == 240.8
    assert monthly["levels"]["0.5"] == 127.4
    assert monthly["levels"]["0.618"] == 100.6376
    assert monthly["levels"]["0"] == 240.8 and monthly["levels"]["1"] == 14.0
    # The shaded zone runs from the swing's first candle to the last candle.
    assert monthly["zoneStart"] == monthly["swing"]["startTime"]
    assert monthly["zoneEnd"] == monthly["lastCandle"]

    # Weekly: 104 weeks back holds the high but not the low (day 300 is ~4 years ago).
    assert weekly["swing"]["high"] == 240.8
    assert weekly["swing"]["low"] > 14.0

    # Daily: the last 252 days miss both spikes, so the swing is the ordinary range.
    assert daily["swing"]["high"] < 240.8 and daily["swing"]["low"] > 14.0

    # The default Monthly swing ("M Fib") reads the same on every chart.
    for timeframe in (monthly, weekly, daily):
        assert timeframe["mFib50"] == "M Fib 50% (127.40)", timeframe["mFib50"]

    # Weekly / Monthly AutoFib: buckets line up one-to-one with the chart's candles,
    # and there are 20+ of them to take the TOS 20-bar high / low from.
    for timeframe in (monthly, weekly):
        assert timeframe["autoFibKeysMatchCandles"] is True
        assert timeframe["autoFibBars"] >= 20
        assert timeframe["autoFib20High"] > timeframe["autoFib20Low"]


def test_five_minute_chart_with_old_saved_settings_shows_only_the_monthly_fib():
    """His phone, 2026-11-20: an unlabelled "Fib 50%" (the chart's own 5-day
    swing, still ticked in his saved settings) sat beside "M Fib 50%". Through
    the real first-paint route: the one-time fix leaves only "M Fib" tags; if he
    ticks the own swing again it is tagged "5-day Fib", never a bare "Fib"."""
    payload = {**_payload(), "dailyBars": _weekday_tape()}
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true")
    assert status == 200, body
    five = _run_frontend(body)["fiveMinute"]
    assert five["candles"] > 0
    assert five["sourcesAfterFix"] == ["M"], five
    assert five["tagsAfterFix"] and all(tag.startswith("M Fib ") for tag in five["tagsAfterFix"]), five["tagsAfterFix"]
    assert "M Fib 50% (127.40)" in five["tagsAfterFix"]
    own = [tag for tag in five["tagsOwnTicked"] if not tag.startswith("M Fib ")]
    assert own, five["tagsOwnTicked"]
    assert all("5-day Fib " in tag for tag in own), own
    assert not any(tag.startswith("Fib ") or " · Fib " in tag for tag in five["tagsOwnTicked"]), five["tagsOwnTicked"]
