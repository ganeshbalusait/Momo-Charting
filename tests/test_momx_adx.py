"""ADX / +DI / -DI: the chart-parity port and everything the scanner records.

The study itself is proven equal to the chart's JavaScript by
``scripts/validate_adx_against_chart.py`` (INOD 5m, 6629 bars, and NVDA 30m:
max |python - js| = 0.0 on all three lines). These tests are the guards around
that: hand-checked arithmetic, the warm-up and short-tape cases that must
degrade rather than raise, the recorded row field, the timeline entry, the
session windows and the new track-record aggregates.

NOTHING HERE MAY ASSERT ON THE GRADE LETTER. ADX is a recorded fact; the day
one of these tests needs momx/grade.py to change, the change is wrong.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import columns, grade_log
from momx.grade_log import GradeLog, build_record, session_window
from momx.indicators import adx_lines, wilders

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# wilders()
# ---------------------------------------------------------------------------

def test_wilders_seeds_on_a_simple_average_then_recurses():
    # length 3 over 1..6: seed = (1+2+3)/3 = 2, then
    #   (2*2 + 4)/3 = 2.6666..., (2.6666..*2 + 5)/3 = 3.4444..,
    #   (3.4444..*2 + 6)/3 = 4.2962...
    out = wilders([1, 2, 3, 4, 5, 6], 3)
    assert out[0] is None and out[1] is None
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(8.0 / 3.0)
    assert out[4] == pytest.approx((8.0 / 3.0 * 2 + 5) / 3)
    assert out[5] == pytest.approx(((8.0 / 3.0 * 2 + 5) / 3 * 2 + 6) / 3)


def test_wilders_skips_non_finite_values_without_advancing():
    # The NaN neither seeds nor advances the recurrence, and leaves None at
    # its own index - exactly what the JS does with a null.
    out = wilders([1, float("nan"), 2, 3], 3)
    assert out[1] is None
    assert out[3] == pytest.approx(2.0)   # seeded from 1, 2, 3


def test_wilders_on_a_short_series_is_all_none():
    assert wilders([1, 2], 10) == [None, None]
    assert wilders([], 10) == []
    assert wilders(None, 10) == []


# ---------------------------------------------------------------------------
# adx_lines()
# ---------------------------------------------------------------------------

def test_adx_lines_hand_checked_on_a_two_bar_length_one_series():
    # length 1 makes Wilders the identity, so every number can be checked by
    # hand. Bar 0: TR = 10 - 8 = 2, +DM = -DM = 0.
    # Bar 1: h 12, l 9, prevClose 9 -> TR = max(3, 3, 0) = 3;
    #        up = 12 - 10 = 2, down = 8 - 9 = -1 -> +DM 2, -DM 0.
    # +DI[0] = 100 * 0 / 2 = 0, -DI[0] = 0, DX[0] = 0 (sum is 0).
    # +DI[1] = 100 * 2 / 3 = 66.666..., -DI[1] = 0, DX[1] = 100.
    out = adx_lines([10, 12], [8, 9], [9, 11], 1)
    assert out["plus"] == pytest.approx([0.0, 200.0 / 3.0])
    assert out["minus"] == pytest.approx([0.0, 0.0])
    assert out["adx"] == pytest.approx([0.0, 100.0])


def test_adx_lines_marks_a_down_bar_with_minus_dm():
    # Bar 1 falls: h 9 (down 1), l 6 (down 2) -> -DM 2, +DM 0.
    # TR = max(9-6, |9-9|, |6-9|) = 3. -DI = 100 * 2 / 3.
    out = adx_lines([10, 9], [8, 6], [9, 7], 1)
    assert out["minus"][1] == pytest.approx(200.0 / 3.0)
    assert out["plus"][1] == pytest.approx(0.0)


def test_adx_lines_with_fewer_bars_than_length_is_all_none_and_never_raises():
    out = adx_lines([1, 2, 3], [0, 1, 2], [1, 2, 3], 10)
    assert out["plus"] == [None, None, None]
    assert out["minus"] == [None, None, None]
    assert out["adx"] == [None, None, None]


def test_adx_lines_with_under_two_bars_returns_empty_lists():
    assert adx_lines([1], [0], [1], 10) == {"plus": [], "minus": [], "adx": []}
    assert adx_lines([], [], [], 10) == {"plus": [], "minus": [], "adx": []}
    assert adx_lines(None, None, None, 10) == {"plus": [], "minus": [], "adx": []}


def test_adx_lines_tolerates_a_garbage_tape():
    out = adx_lines(["x", None, 3], [1, "y", 2], [1, 2, None], 2)
    assert len(out["adx"]) == 3          # aligned to the input, no exception
    assert all(v is None or math.isfinite(v) for v in out["adx"])


def test_adx_lines_length_is_clamped_like_the_chart():
    bars = list(range(40))
    highs = [10 + i * 0.1 for i in bars]
    lows = [9 + i * 0.1 for i in bars]
    closes = [9.5 + i * 0.1 for i in bars]
    # 0 / None / unreadable -> the chart's default of 10, exactly as
    # `Number(options.mtfAdxLength) || 10` does.
    assert adx_lines(highs, lows, closes, 0) == adx_lines(highs, lows, closes, 10)
    assert adx_lines(highs, lows, closes, None) == adx_lines(highs, lows, closes, 10)
    assert adx_lines(highs, lows, closes, "nope") == adx_lines(highs, lows, closes, 10)
    # and clamped to 1..100
    assert adx_lines(highs, lows, closes, 900) == adx_lines(highs, lows, closes, 100)
    assert adx_lines(highs, lows, closes, -5) == adx_lines(highs, lows, closes, 1)


# ---------------------------------------------------------------------------
# columns.adx_cell / build_row
# ---------------------------------------------------------------------------

def bars(*ohlc) -> list[dict]:
    return [{"time": 1000 + i * 300, "high": h, "low": l, "close": c, "volume": 1}
            for i, (h, l, c) in enumerate(ohlc)]


def rising_then_crossing() -> list[dict]:
    """A tape that falls for a while, then turns up hard on the LAST bar.

    Enough bars to warm ADX(3) up, then a decisive up bar so +DI crosses -DI
    exactly once, on the bar the cell reports.
    """
    rows = []
    price = 100.0
    for _ in range(12):
        price -= 1.0
        rows.append((price + 0.5, price - 0.5, price))
    price += 12.0
    rows.append((price + 0.5, price - 0.5, price))
    return bars(*rows)


def test_adx_cell_reports_a_bull_cross_on_the_last_bar():
    cell = columns.adx_cell(rising_then_crossing(), length=3)
    assert cell["prevPlus"] <= cell["prevMinus"]
    assert cell["plus"] > cell["minus"]
    assert cell["cross"] == "bull"
    assert isinstance(cell["rising"], bool)


def test_adx_cell_reports_a_bear_cross_on_the_last_bar():
    rows = []
    price = 100.0
    for _ in range(12):
        price += 1.0
        rows.append((price + 0.5, price - 0.5, price))
    price -= 12.0
    rows.append((price + 0.5, price - 0.5, price))
    cell = columns.adx_cell(bars(*rows), length=3)
    assert cell["cross"] == "bear"


def test_adx_cell_strong_plus_is_the_chart_25_crossing():
    cell = columns.adx_cell(rising_then_crossing(), length=3)
    assert cell["strongPlus"] is (cell["prevPlus"] <= 25 < cell["plus"])
    assert cell["strongMinus"] is (cell["prevMinus"] <= 25 < cell["minus"])


def test_adx_cell_on_a_short_tape_has_stable_keys_and_no_cross():
    cell = columns.adx_cell(bars((10, 9, 9.5)))
    assert cell["cross"] is None
    assert cell["rising"] is False
    assert cell["strongPlus"] is False and cell["strongMinus"] is False
    for key in ("plus", "minus", "adx", "prevPlus", "prevMinus", "prevAdx"):
        assert cell[key] is None


def test_adx_cell_never_reads_a_warmup_none_as_zero():
    """The "Number(null) === 0" trap: a warm-up bar must not fake a cross."""
    cell = columns.adx_cell(bars((10, 9, 9.5), (11, 10, 10.5), (12, 11, 11.5)), length=10)
    assert cell["plus"] is None and cell["minus"] is None
    assert cell["cross"] is None
    assert cell["strongPlus"] is False


def test_adx_cell_carries_the_bar_time_it_was_computed_on():
    """``barAt`` must come from the same bar as the reading it labels.

    The grade recorder latches one timeline line per (timeframe, bar) off
    this; a barAt that drifted from the bar would latch the wrong thing.
    """
    tape = rising_then_crossing()
    assert columns.adx_cell(tape, length=3)["barAt"] == tape[-1]["time"]
    # Stable key on the short-tape shape and on a tape with no usable time.
    assert columns.adx_cell(bars((10, 9, 9.5)))["barAt"] == 1000
    assert columns.adx_cell([{"high": 1, "low": 1, "close": 1}])["barAt"] is None
    assert columns.adx_cell(None)["barAt"] is None


def long_random_tape(count: int = 2000, seed: int = 20260922) -> list[dict]:
    """A 2,000-bar tape: far more than ADX_TAIL_BARS, like a real 5m/30m tape."""
    import random
    rnd = random.Random(seed)
    rows, price = [], 100.0
    for _ in range(count):
        price = max(1.0, price * (1 + rnd.gauss(0, 0.005)))
        rows.append((price * (1 + abs(rnd.gauss(0, 0.004))),
                     price * (1 - abs(rnd.gauss(0, 0.004))), price))
    return bars(*rows)


def test_adx_cell_on_the_last_300_bars_equals_the_whole_tape(monkeypatch):
    """The PERF cut (ADX_TAIL_BARS) must not change a single published field.

    Wilders smoothing decays: at the chart's length of 10 a bar N back is
    worth 0.9^N, so 300 bars back is ~2e-14 of the reading. Measured
    2026-09-22 on six random 4,000-13,000 bar tapes, tails of 200 / 300 / 500
    all produced an identical published cell; the full-tape computation cost
    110-124 ms per symbol (5m ~6,600 + 30m ~13,000 bars) against ~3 ms at 300.
    """
    tape = long_random_tape()
    monkeypatch.setattr(columns, "ADX_TAIL_BARS", 10 ** 9)
    full = columns.adx_cell(tape)
    monkeypatch.setattr(columns, "ADX_TAIL_BARS", 300)
    assert columns.adx_cell(tape) == full
    for key in ("plus", "minus", "adx", "prevPlus", "prevMinus", "prevAdx"):
        assert full[key] is not None, key          # a real reading, not all-None
    # The tail is genuinely shorter than the tape, so this is a real cut.
    assert len(tape) > 300


def test_adx_cell_survives_a_garbage_tape():
    assert columns.adx_cell(None)["cross"] is None
    assert columns.adx_cell("nonsense")["cross"] is None
    assert columns.adx_cell([{"high": "x"}, {"low": None}])["cross"] is None


def test_build_row_records_adx_for_5m_and_30m():
    tape = rising_then_crossing()
    row = columns.build_row("AAA", {"5m": tape, "30m": tape})
    assert set(row["adx"]) == {"5m", "30m"}
    assert row["adx"]["5m"]["cross"] == columns.adx_cell(tape)["cross"]


def test_build_row_still_builds_when_the_adx_tape_is_missing():
    row = columns.build_row("AAA", {})
    assert row["symbol"] == "AAA"
    assert row["adx"]["5m"]["cross"] is None


def test_contract_row_carries_adx_to_the_client():
    from momx.board import _contract_row
    tape = rising_then_crossing()
    row = columns.build_row("AAA", {"5m": tape, "30m": tape})
    assert _contract_row(row, True, [])["adx"] == row["adx"]


# ---------------------------------------------------------------------------
# session_window()
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("hour,minute,expected", [
    (4, 0, "premarket"),
    (9, 29, "premarket"),
    (9, 30, "first30m"),
    (9, 59, "first30m"),
    (10, 0, "morning"),
    (11, 29, "morning"),
    (11, 30, "midday"),
    (13, 29, "midday"),
    (13, 30, "second30m"),
    (14, 59, "second30m"),
    (15, 0, "powerHour"),
    (15, 59, "powerHour"),
    (16, 0, "after"),
    (19, 30, "after"),
])
def test_session_window_boundaries(hour, minute, expected):
    assert session_window(datetime(2026, 9, 22, hour, minute, tzinfo=ET)) == expected


def test_session_window_converts_to_eastern():
    utc = datetime(2026, 9, 22, 17, 45, tzinfo=ZoneInfo("UTC"))  # 13:45 ET
    assert session_window(utc) == "second30m"


def test_session_window_of_nothing_is_none():
    assert session_window(None) is None
    assert session_window("2026-09-22T13:45:00") is None


# ---------------------------------------------------------------------------
# the timeline entry + the recorded event fields
# ---------------------------------------------------------------------------

NOW = datetime(2026, 9, 22, 13, 45, tzinfo=ET)


@pytest.fixture(autouse=True)
def _today_is_the_fixture_date(monkeypatch):
    monkeypatch.setattr(grade_log, "_today_et", lambda: "2026-09-22")


BAR = 1790000000          # the 5m bar every fixture row's reading belongs to


def adx_row(cross=None, rising=True, adx=35.0, letter=None, bar_at=BAR):
    cell = {
        "plus": 48.6, "minus": 13.0, "adx": adx,
        "prevPlus": 12.0, "prevMinus": 20.0, "prevAdx": 12.0,
        "cross": cross, "rising": rising, "strongPlus": True, "strongMinus": False,
        "barAt": bar_at,
    }
    quiet = dict(cell, cross=None)
    return {
        "symbol": "INOD", "last": 100.0,
        "skittles": {}, "rvol": {}, "sqz": {}, "news": None,
        "m5": {"chart": "below", "state": "quiet", "trigger": 101.0},
        "grade": {"letter": letter, "checks": {}, "reasons": []},
        "adx": {"5m": cell, "30m": quiet},
    }


def test_adx_bull_cross_becomes_a_timeline_item_and_an_icon(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [adx_row()], "rest": []}, NOW)
    payload = {"rows": [adx_row(cross="bull")], "rest": []}
    log.apply("Watchlist", payload, NOW + timedelta(seconds=30))
    fresh = payload["rows"][0]["gradeFresh"]
    assert fresh["icons"] == ["ADX"]
    assert fresh["timeline"][-1]["what"] == "ADX 5m bull cross (ADX 35↑)"


def test_adx_bear_cross_says_bear_and_drops_the_arrow_when_not_rising(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [adx_row()], "rest": []}, NOW)
    payload = {"rows": [adx_row(cross="bear", rising=False, adx=18.4)], "rest": []}
    log.apply("Watchlist", payload, NOW + timedelta(seconds=30))
    assert payload["rows"][0]["gradeFresh"]["timeline"][-1]["what"] == "ADX 5m bear cross (ADX 18)"


def test_a_standing_cross_is_logged_once_not_every_scan(tmp_path):
    """``cross`` stays set for the whole life of the bar; the timeline must not."""
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [adx_row()], "rest": []}, NOW)
    for step in range(1, 6):
        payload = {"rows": [adx_row(cross="bull")], "rest": []}
        log.apply("Watchlist", payload, NOW + timedelta(seconds=30 * step))
    timeline = payload["rows"][0]["gradeFresh"]["timeline"]
    assert sum(1 for item in timeline if item["what"].startswith("ADX")) == 1


def adx_lines_in(payload) -> list[str]:
    return [item["what"] for item in payload["rows"][0]["gradeFresh"]["timeline"]
            if item["what"].startswith("ADX")]


def test_a_cross_flickering_on_a_forming_bar_is_logged_once(tmp_path):
    """The bar the cell reads is still FORMING, so ``cross`` is not stable.

    +DI and -DI are recomputed on the live bar every scan, so as the high /
    low / close move the cell goes bull -> None -> bull. Diffing against the
    PREVIOUS SCAN reads that None as the end of the cross and the next bull as
    a new one: this exact 15-scan simulation of ONE 5m bar wrote two identical
    "ADX 5m bull cross" lines before the per-bar latch (2026-09-22).
    """
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [adx_row()], "rest": []}, NOW)
    # One bar's worth of ~20 s scans, the cross wobbling in and out.
    flicker = ["bull", "bull", None, "bull", "bull", None, None, "bull",
               "bull", "bull", None, "bull", "bull", "bull", "bull"]
    for step, cross in enumerate(flicker, start=1):
        payload = {"rows": [adx_row(cross=cross)], "rest": []}
        log.apply("Watchlist", payload, NOW + timedelta(seconds=20 * step))
    assert adx_lines_in(payload) == ["ADX 5m bull cross (ADX 35↑)"]


def test_the_next_bar_may_cross_again_and_does_log(tmp_path):
    """The latch is per BAR, not per day: a real new cross must still appear."""
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [adx_row()], "rest": []}, NOW)
    for step, cross in enumerate(["bull", None, "bull"], start=1):
        payload = {"rows": [adx_row(cross=cross)], "rest": []}
        log.apply("Watchlist", payload, NOW + timedelta(seconds=20 * step))
    assert len(adx_lines_in(payload)) == 1
    # The 5m bar rolls: same cross, NEW bar, so it is a new event.
    for step, cross in enumerate(["bull", None, "bull"], start=4):
        payload = {"rows": [adx_row(cross=cross, bar_at=BAR + 300)], "rest": []}
        log.apply("Watchlist", payload, NOW + timedelta(seconds=20 * step))
    assert len(adx_lines_in(payload)) == 2


def test_a_cross_without_a_bar_time_still_latches_once(tmp_path):
    """No barAt (old row / timeless tape) degrades to once per cross value."""
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [adx_row(bar_at=None)], "rest": []}, NOW)
    for step, cross in enumerate(["bull", None, "bull", "bull"], start=1):
        payload = {"rows": [adx_row(cross=cross, bar_at=None)], "rest": []}
        log.apply("Watchlist", payload, NOW + timedelta(seconds=20 * step))
    assert len(adx_lines_in(payload)) == 1


def test_a_row_without_adx_never_breaks_the_timeline(tmp_path):
    log = GradeLog(tmp_path)
    row = adx_row()
    row.pop("adx")
    log.apply("Watchlist", {"rows": [row], "rest": []}, NOW)
    payload = {"rows": [dict(row, adx=None)], "rest": []}
    log.apply("Watchlist", payload, NOW + timedelta(seconds=30))
    assert payload["rows"][0]["gradeFresh"]["icons"] == []


def test_a_recorded_event_carries_the_adx_snapshot_and_its_session(tmp_path):
    log = GradeLog(tmp_path)
    payload = {"rows": [adx_row(cross="bull", letter="A")], "rest": []}
    log.apply("Watchlist", payload, NOW)
    events = grade_log._read_json(
        tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-22.json")["events"]
    assert len(events) == 1
    assert events[0]["session"] == "second30m"
    assert events[0]["adx"]["5m"]["cross"] == "bull"
    assert events[0]["adx"]["5m"]["plus"] == 48.6


def test_an_event_from_the_first_30m_is_stamped_first30m(tmp_path):
    log = GradeLog(tmp_path)
    at = datetime(2026, 9, 22, 9, 42, tzinfo=ET)
    log.apply("Watchlist", {"rows": [adx_row(letter="B")], "rest": []}, at)
    events = grade_log._read_json(
        tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-22.json")["events"]
    assert events[0]["session"] == "first30m"


# ---------------------------------------------------------------------------
# build_record: bySession / patternBySession / byAdx
# ---------------------------------------------------------------------------

def write_events(tmp_path, events: list[dict]) -> None:
    folder = tmp_path / grade_log.EVENTS_DIRNAME / "Watchlist"
    folder.mkdir(parents=True, exist_ok=True)
    grade_log._atomic_write_json(folder / "2026-09-22.json",
                                 {"board": "Watchlist", "date": "2026-09-22",
                                  "events": events})


def event(symbol, *, kind="letter", letter="A", pattern=None, session="second30m",
          cross=None, rising=False, close=1.0) -> dict:
    cell = {"plus": 40.0, "minus": 10.0, "adx": 30.0, "prevPlus": 5.0,
            "prevMinus": 20.0, "prevAdx": 25.0, "cross": cross, "rising": rising,
            "strongPlus": False, "strongMinus": False}
    return {
        "board": "Watchlist", "symbol": symbol, "kind": kind, "letter": letter,
        "pattern": pattern, "at": "2026-09-22T13:45:00-04:00", "price": 10.0,
        "session": session, "adx": {"5m": cell, "30m": dict(cell, cross=None)},
        "outcome": {"close": close, "p15": close, "p60": close,
                    "maxFav": close, "maxAdv": 0.0},
    }


def test_build_record_splits_letter_events_by_session(tmp_path):
    write_events(tmp_path, [
        event("AAA", session="second30m", close=2.0),
        event("BBB", session="first30m", close=-1.0),
        event("CCC", session="second30m", close=4.0),
    ])
    record = build_record(tmp_path)
    by_session = record["bySession"]["A"]
    assert by_session["second30m"]["count"] == 2
    assert by_session["first30m"]["count"] == 1
    assert by_session["second30m"]["pctHigherClose"] == 100.0


def test_build_record_splits_pattern_events_by_session(tmp_path):
    write_events(tmp_path, [
        event("AAA", kind="pattern", pattern="explosive", session="powerHour"),
        event("BBB", kind="pattern", pattern="explosive", session="midday"),
    ])
    record = build_record(tmp_path)
    assert record["patternBySession"]["explosive"]["powerHour"]["count"] == 1
    assert record["patternBySession"]["explosive"]["midday"]["count"] == 1
    assert record["patternBySession"]["steady"] == {}


def test_build_record_by_adx_buckets(tmp_path):
    write_events(tmp_path, [
        event("AAA", cross="bull", rising=True, close=3.0),
        event("BBB", cross="bull", rising=False, close=1.0),
        event("CCC", cross=None, close=-2.0),
        event("DDD", cross="bear", close=-1.0),
    ])
    by_adx = build_record(tmp_path)["byAdx"]
    assert by_adx["bullCross"]["count"] == 2
    assert by_adx["bullCrossRising"]["count"] == 1     # a SUBSET of bullCross
    assert by_adx["none"]["count"] == 2                # no-cross AND bear
    assert by_adx["bullCrossRising"]["avgToClose"] == 3.0


def test_build_record_by_adx_counts_pattern_events_too(tmp_path):
    write_events(tmp_path, [
        event("AAA", cross="bull", rising=True),
        event("BBB", kind="pattern", pattern="steady", cross="bull", rising=True),
    ])
    assert build_record(tmp_path)["byAdx"]["bullCrossRising"]["count"] == 2


def test_build_record_treats_an_event_without_adx_as_no_cross(tmp_path):
    old = event("AAA")
    old.pop("adx")
    write_events(tmp_path, [old])
    record = build_record(tmp_path)
    assert record["byAdx"]["none"]["count"] == 1
    assert record["bySession"]["A"]["second30m"]["count"] == 1


def test_build_record_keeps_every_existing_aggregate(tmp_path):
    write_events(tmp_path, [event("AAA", cross="bull")])
    record = build_record(tmp_path)
    for key in ("source", "days", "patternDays", "letters", "byMomentum",
                "byPattern", "byFresh", "unscored", "patternEvents",
                "patternUnscored"):
        assert key in record, key
    assert record["letters"]["A"]["count"] == 1
