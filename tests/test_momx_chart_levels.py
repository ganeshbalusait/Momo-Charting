"""Unit: momx/chart_levels.py against the chart's REAL JavaScript (2026-11-11).

The chat quotes these numbers as "what the chart shows", so they are held to the
chart value for value: tests/chart_levels_truth.mjs runs the chart's own modules
and functions lifted from App.jsx over fixed tapes, on all 11 timeframes, and
every value must match exactly. A chart formula that changes fails this test,
which is the point: the port must then change with it.

tests/fixtures/chart_levels holds frozen copies of two real chart tapes.
"""
from __future__ import annotations

import ast
import gzip
import json
import random
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from momx import chart_levels as cl

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "chart_levels"
TRUTH = ROOT / "tests" / "chart_levels_truth.mjs"
ET = ZoneInfo("America/New_York")
ALL_TIMEFRAMES = ["3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "D", "W", "M"]


def stamp(day: date, hour: int, minute: int) -> int:
    return int(datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).timestamp())


def _weekdays(end: date, count: int) -> list[date]:
    days, cursor = [], end
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor)
        cursor -= timedelta(days=1)
    return list(reversed(days))


def _candle(rng: random.Random, time: int, price: float, volume=None) -> dict:
    move = rng.uniform(-0.4, 0.4)
    close = round(price + move, 2)
    return {"time": time, "open": round(price, 2), "high": round(max(price, close) + rng.uniform(0, 0.3), 2),
            "low": round(min(price, close) - rng.uniform(0, 0.3), 2), "close": close,
            "volume": rng.randint(0, 5000) if volume is None else volume}


def make_tape(end: date, seed: int, days: int = 8, sessions: int = 320, study_minutes: int = 30) -> dict:
    """A deterministic tape with the awkward shapes real ones have had."""
    rng = random.Random(seed)
    price = 100.0
    daily = []
    for day in _weekdays(end, sessions):
        if day == date(2026, 9, 7):       # Labor Day: no session, so a Monday week has no Monday row
            continue
        row = _candle(rng, stamp(day, 1, 0), price, volume=rng.randint(1_000_000, 9_000_000))
        price = row["close"]
        daily.append({**row, "date": day.isoformat()})
    # a Sunday-night stub, and a second copy of one day with less volume
    sunday = end - timedelta(days=(end.weekday() + 1) % 7 or 7)
    daily.append({**_candle(rng, stamp(sunday, 20, 0), price, volume=50), "date": sunday.isoformat()})
    daily.append({**daily[-20], "time": daily[-20]["time"] + 3 * 3600, "volume": 10})
    study, fine, live = [], [], []
    for day in _weekdays(end, 40):
        if day == date(2026, 9, 7):
            continue
        for minute in range(4 * 60, 20 * 60, study_minutes):
            bar = _candle(rng, stamp(day, minute // 60, minute % 60), price)
            price = bar["close"]
            study.append(bar)
    for day in _weekdays(end, 20):
        if day == date(2026, 9, 7):
            continue
        for minute in range(4 * 60, 20 * 60, 5):
            bar = _candle(rng, stamp(day, minute // 60, minute % 60), price)
            price = bar["close"]
            fine.append(bar)
    for day in _weekdays(end, days):
        if day == date(2026, 9, 7):
            continue
        for minute in range(4 * 60, 20 * 60):
            if rng.random() < 0.15:       # quiet minutes leave holes
                continue
            bar = _candle(rng, stamp(day, minute // 60, minute % 60), price)
            price = bar["close"]
            if rng.random() < 0.02:       # a forming bar whose high lags its close
                bar["high"] = bar["close"] - 0.5
            if rng.random() < 0.02:
                bar["volume"] = None
            live.append(bar)
    last = live[-1]["time"]
    signals = [
        {"time": last - 900, "candleTimestamp": last - 900, "family": "4x8", "timeframe": "15", "direction": "CALL", "label": "CALL15", "compact": False, "liveForming": False, "isLiveSignal": False},
        {"time": last - 900, "family": "9x20", "timeframe": "30", "direction": "PUT", "label": "P30", "compact": True, "liveForming": False},
        {"time": last - 60, "family": "4x8", "timeframe": "4H", "direction": "CALL", "label": "CALL4H", "compact": False, "liveForming": True, "isLiveSignal": True},
        {"time": last - 86400 * 2, "family": "4x8", "timeframe": "1H", "direction": "PUT", "label": "PUT1H", "compact": False},
        {"time": 1000, "family": "4x8", "timeframe": "2H", "direction": "PUT", "label": "PUT2H", "compact": False},
    ]
    higher = [
        {"key": "a", "family": "ganesh48", "timeframe": "D", "direction": "CALL", "label": "CALLD", "time": last - 3600, "low": 1, "high": 2, "atrMultiplier": 0.2},
        {"key": "b", "family": "ganeshMacd", "timeframe": "W", "direction": "PUT", "label": "MACD-W", "time": last - 3600, "low": 1, "high": 2, "atrMultiplier": 1.8},
        {"key": "c", "family": "ganesh920", "timeframe": "3D", "direction": "PUT", "label": "PUT3D", "time": last - 7200, "low": 1, "high": 2, "atrMultiplier": 1.0},
        {"key": "d", "family": "ganesh48", "timeframe": "M", "direction": "CALL", "label": "CALLM", "time": 1000, "low": 1, "high": 2, "atrMultiplier": 2.2},
    ]
    return {"symbol": "EDGE", "bars": live, "studyBars": study, "fineStudyBars": fine, "dailyBars": daily, "mtfSignals": signals,
            "mtfSignalsByTimeframe": {"15": signals[:2], "30": []}, "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": higher},
            "mtfSignalsPending": False, "studiesPending": False}


def edge_tapes() -> dict[str, dict]:
    full = make_tape(date(2026, 9, 8), 1)                     # Tuesday after a Monday holiday
    dst = make_tape(date(2026, 11, 3), 2)                     # across the clock change on 11-01
    five = make_tape(date(2026, 10, 30), 3, study_minutes=5)  # a 5-minute study tape
    no_daily = {**make_tape(date(2026, 10, 14), 4), "dailyBars": []}
    no_study = {**make_tape(date(2026, 10, 15), 5), "studyBars": [], "fineStudyBars": []}
    tiny = {"bars": [{"time": stamp(date(2026, 10, 6), 9, 30), "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 0},
                     {"time": stamp(date(2026, 10, 6), 9, 31), "open": 10.5, "high": 10.5, "low": 10.5, "close": 10.5}]}
    odd = {"bars": [{"time": str(stamp(date(2026, 10, 6), 9, 30)), "open": "10", "high": "11", "low": "9", "close": "10.5", "volume": "7"},
                    {"time": stamp(date(2026, 10, 6), 9, 31), "open": None, "high": 1, "low": 1, "close": 1},
                    {"time": -5, "open": 1, "high": 1, "low": 1, "close": 1},
                    "not a bar",
                    {"time": stamp(date(2026, 10, 6), 9, 33), "open": 10.5, "high": 9, "low": 12, "close": 11, "volume": None}]}
    return {"edge_holiday.json": full, "edge_dst.json": dst, "edge_study5.json": five, "edge_no_daily.json": no_daily,
            "edge_no_study.json": no_study, "edge_tiny.json": tiny, "edge_odd.json": odd, "edge_empty.json": {"bars": []},
            "edge_bad_print.json": bad_print_tape(), "edge_bad_print_daily.json": bad_print_daily_tape()}


BAD_PRINT_DAY = date(2026, 10, 21)


def bad_print_tape() -> dict:
    """An off-market after-hours print 40% under the market (SPY 2026-11-11: $711.46 on a $760 tape) in the minute,
    five-minute and study tapes, plus one in the regular session that must be left alone."""
    tape = make_tape(date(2026, 10, 22), 6)

    def spoil(rows: list, field: str) -> None:
        # the first bar at or after 17:00 ET (the minute tape has quiet holes)
        row = next(bar for bar in rows if bar["time"] >= stamp(BAD_PRINT_DAY, 17, 0))
        bad = round(row["close"] * 0.6, 2)
        row.update({field: bad, "low": bad})

    spoil(tape["bars"], "open")
    spoil(tape["fineStudyBars"], "low")
    spoil(tape["studyBars"], "low")
    return tape


def bad_print_daily_tape() -> dict:
    """bad_print_tape plus that day's daily seed row built from the unfiltered minutes (SPY 2026-11-11: the saved row
    kept low 711.4601 from the $711.46 print), so D/W/M and every daily level must read the cleaned day."""
    tape = bad_print_tape()
    bad = min(bar["low"] for bar in tape["bars"])
    row = next(bar for bar in tape["dailyBars"] if bar.get("date") == BAD_PRINT_DAY.isoformat())
    row["low"] = round(bad + 0.0001, 4)
    return tape


def _load(path: Path) -> dict:
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


@pytest.fixture(scope="module")
def truth(tmp_path_factory):
    """Run the real chart code once. It is CPU-heavy, so the whole module shares one run."""
    if shutil.which("node") is None:
        pytest.fail("node is required: this test compares against the chart's real JavaScript")
    folder = tmp_path_factory.mktemp("chart-truth")
    tapes = {path.name: path for path in sorted(FIXTURES.glob("*.json.gz"))}
    for name, payload in edge_tapes().items():
        tapes[name] = folder / name
        tapes[name].write_text(json.dumps(payload), encoding="utf-8")
    out = folder / "truth.json"
    subprocess.run(["node", str(TRUTH), str(out), *map(str, tapes.values())], check=True, timeout=540, cwd=str(ROOT))
    return {"result": json.loads(out.read_text(encoding="utf-8")), "tapes": tapes}


def _differences(mine, theirs, where="") -> list:
    if isinstance(mine, dict) and isinstance(theirs, dict):
        out = []
        for key in sorted(set(mine) | set(theirs)):
            out += _differences(mine.get(key, "<absent>"), theirs.get(key, "<absent>"), f"{where}/{key}")
        return out
    if isinstance(mine, list) and isinstance(theirs, list):
        out = [] if len(mine) == len(theirs) else [(where, "length", len(mine), len(theirs))]
        for index, (left, right) in enumerate(zip(mine, theirs)):
            out += _differences(left, right, f"{where}[{index}]")
        return out
    return [] if mine == theirs else [(where, mine, theirs)]


def _count_values(value) -> int:
    if isinstance(value, dict):
        return sum(map(_count_values, value.values()))
    if isinstance(value, list):
        return sum(map(_count_values, value))
    return 1


def test_the_port_covers_exactly_the_charts_timeframes(truth):
    assert [row["key"] for row in truth["result"]["timeframes"]] == ALL_TIMEFRAMES == list(cl.TIMEFRAMES)
    assert {row["key"]: row["minutes"] for row in truth["result"]["timeframes"]} == cl.TIMEFRAMES


@pytest.mark.parametrize("name", ["SPY.json.gz", "AAPL.json.gz"])
@pytest.mark.parametrize("timeframe", ALL_TIMEFRAMES)
def test_real_tapes_match_the_chart_exactly(truth, name, timeframe):
    expected = truth["result"]["tapes"][name][timeframe]
    mine = cl.analyze(_load(truth["tapes"][name]), timeframe)
    assert expected is not None and mine is not None
    compared = {"levels": mine["levels"], "signals": mine["signals"]}
    assert _differences(compared, expected) == []
    assert _count_values(expected["levels"]) >= 30


@pytest.mark.parametrize("name", list(edge_tapes()))
def test_awkward_tapes_match_the_chart_exactly(truth, name):
    payload = edge_tapes()[name]
    for timeframe in ALL_TIMEFRAMES:
        expected = truth["result"]["tapes"][name][timeframe]
        mine = cl.analyze(payload, timeframe)
        if expected is None:
            assert mine is None, timeframe
            continue
        assert mine is not None, timeframe
        assert _differences({"levels": mine["levels"], "signals": mine["signals"]}, expected) == [], timeframe


def test_the_comparison_is_not_empty(truth):
    """Guards the guard: real tapes must exercise signals and every level family."""
    spy = truth["result"]["tapes"]["SPY.json.gz"]
    assert spy["5m"]["signals"]["mtf"] and spy["5m"]["signals"]["higher"]
    assert spy["5m"]["levels"]["sma200"] is not None and spy["5m"]["levels"]["mtfMa"]
    assert spy["D"]["levels"]["woodies"] is not None and spy["5m"]["levels"]["woodies"] is None
    assert all(spy["5m"]["levels"]["pivots"][period] for period in cl.PERIODS)
    edge = truth["result"]["tapes"]["edge_dst.json"]["5m"]["signals"]
    assert any(row["forming"] for row in edge["mtf"])
    assert truth["result"]["tapes"]["edge_tiny.json"]["5m"]["levels"]["sma200"] is None


def test_to_fixed_rounds_the_binary_value_like_javascript():
    # Expected values are what Node's toFixed(4) printed for each of these.
    assert cl.to_fixed4(1.00015) == 1.0002          # binary value is just above the tie
    assert cl.to_fixed4(2.00045) == 2.0004          # binary value is just below it
    assert cl.to_fixed4(123.45675) == 123.4567
    assert cl.to_fixed4(-1.00005) == -1.0001
    assert cl.to_fixed4(0.5) == 0.5


def test_four_hour_buckets_follow_the_tos_clock_across_the_clock_change():
    assert datetime.fromtimestamp(cl.bucket_time(stamp(date(2026, 10, 30), 9, 40), 240), ET).strftime("%H:%M") == "09:00"
    assert datetime.fromtimestamp(cl.bucket_time(stamp(date(2026, 11, 2), 9, 40), 240), ET).strftime("%H:%M") == "09:00"
    assert datetime.fromtimestamp(cl.bucket_time(stamp(date(2026, 11, 2), 8, 40), 120), ET).strftime("%H:%M") == "07:00"


def test_daily_dedupe_drops_a_weekend_stub_and_keeps_the_fullest_copy():
    monday, sunday = date(2026, 11, 9), date(2026, 11, 8)
    rows = cl.normalize([
        {"time": stamp(sunday, 20, 0), "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5},
        {"time": stamp(monday, 1, 0), "open": 2, "high": 2, "low": 2, "close": 2, "volume": 900},
        {"time": stamp(monday, 4, 0), "open": 3, "high": 3, "low": 3, "close": 3, "volume": 10},
    ])
    assert [(row["close"], row["date"]) for row in cl.dedupe_daily(rows)] == [(2, "2026-11-09")]


def test_a_one_minute_study_tape_is_folded_on_read_like_the_server():
    start = stamp(date(2026, 10, 6), 9, 30)
    minutes = [{"time": start + 60 * n, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for n in range(120)]
    folded = cl.fold_study_tape(minutes)
    assert len(folded) == 4 and folded[0]["volume"] == 30
    thirty = [{"time": start + 1800 * n, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1} for n in range(10)]
    assert len(cl.fold_study_tape(thirty)) == 10


def test_fold_agrees_with_the_servers_own_fold():
    import chart_aggregation
    rng = random.Random(9)
    start = stamp(date(2026, 10, 6), 9, 30)
    rows = [_candle(rng, start + 60 * n, 50.0) for n in range(500)]
    assert [(r["time"], r["high"], r["low"], r["close"], r["volume"]) for r in cl.fold_study_tape(rows)] == \
        [(r["time"], r["high"], r["low"], r["close"], r["volume"]) for r in chart_aggregation.normalize_study_tape(rows)]


def test_a_saved_copy_from_an_old_signal_engine_reads_as_pending():
    payload = make_tape(date(2026, 10, 15), 6)
    payload.update(mtfSignalMode="something-older", mtfSourceSessions=53)
    disk = cl.analyze(payload, "5m", from_disk=True)
    assert disk["signals"]["mtf"] == [] and disk["signals"]["pending"]["mtfSignalsPending"] is True
    payload.update(mtfSignalMode=cl.MTF_ENGINE_MODE)
    current = cl.analyze(payload, "5m", from_disk=True)
    assert current["signals"]["mtf"] and current["signals"]["pending"]["mtfSignalsPending"] is False


@pytest.mark.parametrize("flags, expected", [
    ({}, {"mtfSignalsPending": False, "studiesPending": False, "historyReady": True}),
    ({"mtfSignalsPending": True}, {"mtfSignalsPending": True, "studiesPending": False, "historyReady": True}),
    ({"studiesPending": True}, {"mtfSignalsPending": False, "studiesPending": True, "historyReady": True}),
])
def test_pending_flags_are_read_from_the_tape(flags, expected):
    payload = {"ganeshHigherTimeframeSignals": {"historyReady": True}, **flags}
    assert cl.signals_pending(payload) == expected


def test_history_not_ready_reads_as_not_ready():
    assert cl.signals_pending({"ganeshHigherTimeframeSignals": {"historyReady": False}})["historyReady"] is False
    assert cl.signals_pending({})["historyReady"] is False


def test_the_defaults_are_the_charts():
    assert cl.DEFAULTS["ema"] == [9, 21, 50] and cl.DEFAULTS["sma"] == 200
    source = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    for line in ("ema9Period", "ema21Period", "ema50Period", "sma200Period"):
        assert line in source
    assert re.search(r"\n  ema9Period: 9,", source) and re.search(r"\n  ema21Period: 21,", source)
    assert re.search(r"\n  ema50Period: 50,", source) and re.search(r"\n  sma200Period: 200,", source)


def test_constants_copied_from_the_server_still_match():
    server = (ROOT / "api_server.py").read_text(encoding="utf-8")
    scanner = (ROOT / "scanner.py").read_text(encoding="utf-8")
    assert f'OI_CHART_MTF_ENGINE_MODE = "{cl.MTF_ENGINE_MODE}"' in server
    assert f"TOS_CHART_SESSION_DAYS = {cl.MTF_SESSION_DAYS}" in scanner


def test_the_module_is_standard_library_only():
    """It runs in a child with no app code on its path."""
    tree = ast.parse(Path(cl.__file__).read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add((node.module or "").split(".")[0])
    assert names <= set(sys.stdlib_module_names)


def test_the_child_process_gives_the_same_answer():
    path = FIXTURES / "AAPL.json.gz"
    done = subprocess.run([sys.executable, "-I", str(Path(cl.__file__)), "15m", str(path)], capture_output=True, timeout=120, check=True)
    assert json.loads(done.stdout) == json.loads(json.dumps(cl.analyze(_load(path), "15m", from_disk=True)))


def test_the_child_reads_a_tape_on_stdin():
    tape = make_tape(date(2026, 10, 15), 7)
    done = subprocess.run([sys.executable, "-I", str(Path(cl.__file__)), "D", "-"], input=json.dumps(tape).encode(),
                          capture_output=True, timeout=120, check=True)
    assert json.loads(done.stdout) == json.loads(json.dumps(cl.analyze(tape, "D")))


def test_an_unknown_timeframe_or_empty_tape_is_none():
    assert cl.analyze({"bars": []}, "5m") is None
    assert cl.analyze({"bars": []}, "7m") is None
    assert cl.analyze("nonsense", "5m") is None


# ---- the chat's 21/50 EMA answer (2026-11-11): the chart's own EMA series, every timeframe, zero difference
@pytest.mark.parametrize("name", ["SPY.json.gz", "AAPL.json.gz"])
def test_the_21_50_ema_report_matches_the_chart_on_every_timeframe(truth, name):
    payload = _load(truth["tapes"][name])
    report = cl.ema_report(payload, cl.EMA_TIMEFRAMES)
    crosses = 0
    for timeframe in cl.EMA_TIMEFRAMES:
        expected = truth["result"]["tapes"][name][timeframe]["levels"]
        mine = dict(report["ema"][timeframe])
        assert (mine.pop("ema9"), mine.pop("sma200"), mine.pop("timeframe")) == (expected["ema9"], expected["sma200"], timeframe)
        assert _differences(mine, expected["emaDetail"]) == [], timeframe
        assert mine["ema21"]["value"] == expected["ema21"] and mine["ema50"]["value"] == expected["ema50"]
        crosses += mine["cross2150"] is not None
    assert crosses >= 3                                  # the cross rule is exercised, not vacuous


# ---- off-market extended-hours bad prints (d0db99ff): the chart repairs them before it builds candles
def test_an_after_hours_bad_print_never_reaches_a_candle_or_a_level():
    tape = bad_print_tape()
    bad_low = min(bar["low"] for bar in tape["bars"])
    spoiled = {min(bar["low"] for bar in tape[key]) for key in ("bars", "fineStudyBars", "studyBars")}
    for timeframe in ALL_TIMEFRAMES[:8]:                     # every intraday timeframe; they read different tapes
        week_low = cl.analyze(tape, timeframe)["levels"]["session"]["week"]["low"]
        assert week_low not in spoiled, timeframe
    # the same minutes without the rule would have drawn the print
    live = cl.normalize(tape["bars"])
    assert min(bar["low"] for bar in cl.display_bars([], [], live, [], 5)) == bad_low
    repaired = cl._bad_print_filter()(live)
    assert min(bar["low"] for bar in cl.display_bars([], [], repaired, [], 5)) > bad_low * 1.2
    assert sum(bool(bar.get("badPrintRemoved")) for bar in repaired) == 1


def test_a_bad_print_in_the_daily_seed_never_reaches_d_w_m_or_the_daily_levels():
    tape = bad_print_daily_tape()
    bad = min(bar["low"] for bar in tape["dailyBars"])
    for timeframe in ("D", "W", "M"):
        week_low = cl.analyze(tape, timeframe)["levels"]["session"]["week"]["low"]
        assert week_low > bad * 1.2, timeframe
    report = cl.ema_report(tape)
    assert min(row["low"] for row in report["daily"]) > bad * 1.2      # the chat's mini chart
    assert min(row["low"] for row in report["weekly"]) > bad * 1.2
    # the same seed without the rule would have drawn the print
    raw = cl.normalize(tape["bars"])
    daily = cl.dedupe_daily(cl.normalize(tape["dailyBars"]))
    assert min(bar["low"] for bar in cl.display_bars([], [], cl._bad_print_filter()(raw), daily, 1440)) == bad
    assert cl.reconcile_daily_seed(daily, raw, raw) is daily


def test_the_daily_seed_keeps_a_low_the_minute_rule_did_not_remove():
    day = BAD_PRINT_DAY
    evening = [{"time": stamp(day - timedelta(days=1), 19, 0), "open": 100, "high": 100.02, "low": 99.98, "close": 100, "volume": 1}]
    flat = [{"time": stamp(day, minute // 60, minute % 60), "open": 100, "high": 100.02, "low": 99.98, "close": 100, "volume": 1}
            for minute in range(15 * 60, 18 * 60)]
    repair = cl._bad_print_filter()
    spiked = evening + [dict(bar) for bar in flat]
    spiked[1 + 100]["low"] = 60                              # 16:40 ET: one print, nothing near it - removed
    row = {"time": stamp(day, 0, 0), "open": 100, "high": 100.02, "low": 60.0001, "close": 100, "volume": 9}
    fixed = cl.reconcile_daily_seed([row], spiked, repair(spiked))
    assert (fixed[0]["low"], fixed[0]["high"], fixed[0]["badPrintRemoved"]) == (99.98, 100.02, True)
    deeper = {**row, "low": 50}                              # a seed low that is not the removed print stays
    assert cl.reconcile_daily_seed([deeper], spiked, repair(spiked))[0] is deeper
    # the tape's first day may be partial, so it never repairs a row
    assert cl.reconcile_daily_seed([row], spiked[1:], repair(spiked[1:]))[0] is row
    # a real after-hours move keeps trading at the new price: nothing is removed, the seed keeps it
    moved = evening + [dict(bar) for bar in flat]
    for bar in moved[1 + 90:]:
        bar.update(open=110, high=110.02, low=109.98, close=110)
    seed = [{**row, "low": 99.98, "high": 110.02}]
    assert repair(moved) is moved and cl.reconcile_daily_seed(seed, moved, repair(moved)) is seed


def test_a_regular_session_drop_and_a_real_after_hours_move_are_kept():
    day = BAD_PRINT_DAY
    flat = [{"time": stamp(day, minute // 60, minute % 60), "open": 100, "high": 100.02, "low": 99.98, "close": 100, "volume": 1}
            for minute in range(15 * 60, 18 * 60)]
    regular = [dict(bar) for bar in flat]
    regular[30]["low"] = 60                                  # 15:30 ET: inside the session, left alone
    moved = [dict(bar) for bar in flat]
    for bar in moved[90:]:                                   # 16:30 ET onwards: price stays at the new level
        bar.update(open=110, high=110.02, low=109.98, close=110)
    repair = cl._bad_print_filter()
    assert repair(regular) is regular and repair(moved) is moved
    spiked = [dict(bar) for bar in flat]
    spiked[100]["low"] = 60                                  # 16:40 ET: one print, nothing near it
    fixed = repair(spiked)
    assert fixed[100]["low"] == 100 and fixed[100]["badPrintRemoved"] is True
    assert [bar for index, bar in enumerate(fixed) if index != 100] == [bar for index, bar in enumerate(spiked) if index != 100]


def test_ema_detail_reads_a_cross_and_the_slope():
    def bar(i, close):
        return {"time": 1_700_000_000 + i * 300, "open": close, "high": close, "low": close, "close": close, "volume": 1}
    rising = [bar(i, 100 - i * 0.5) for i in range(80)] + [bar(80 + i, 60 + i * 2.0) for i in range(60)]
    detail = cl.ema_detail(rising)
    assert detail["ema21Above50"] and detail["cross2150"]["dir"] == "up" and detail["slope21"] == "rising"
    assert detail["ema21"]["above"] and detail["priceCross21"]["dir"] == "up"
    assert cl.ema_detail(rising[:1]) is None
