import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import grade_log as grade_log_module
from momx.grade_log import GradeLog, read_tape

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 21, 9, 32, tzinfo=ET)


@pytest.fixture(autouse=True)
def _today_is_the_fixture_date(monkeypatch):
    """status() now asks the ET wall clock for "today" (see
    test_status_today_uses_the_et_clock_not_the_last_apply_day). Every other
    test in this file builds boards dated 2026-09-21 (``NOW``) regardless of
    the real date the suite happens to run on, so pin the clock to match --
    a test that wants a different "now" for status() overrides this again.
    """
    monkeypatch.setattr(grade_log_module, "_today_et", lambda: "2026-09-21")


def row(bg4h="black", letter=None, last=100.0):
    return {"symbol": "AAA", "last": last,
            "skittles": {"4h": {"bg": bg4h, "fg": "cyan"}},
            "rvol": {}, "sqz": {}, "news": None, "m5": {"chart": "below", "state": "quiet", "trigger": 101.0},
            "grade": {"letter": letter, "checks": {"skit": 7}, "reasons": ["r"]}}


def test_bg_change_becomes_fresh_icon_and_timeline(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    p = {"rows": [row("green")], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(seconds=30))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["icons"] == ["SKIT"]
    assert fresh["timeline"][-1]["what"] == "SKIT 4h bg green"


def test_icon_expires_after_15_minutes(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    log.apply("Watchlist", {"rows": [row("green")], "rest": []}, NOW + timedelta(minutes=1))
    p = {"rows": [row("green")], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(minutes=17))
    assert p["rows"][0]["gradeFresh"]["icons"] == []


def test_first_a_plus_is_latched_and_logged_once(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row(letter="A+", last=100.0)], "rest": []}, NOW)
    p = {"rows": [row(letter="A+", last=105.0)], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(minutes=5))
    first = p["rows"][0]["gradeFresh"]["firstToday"]["A+"]
    assert first["price"] == 100.0
    assert p["rows"][0]["gradeFresh"]["ageMinutes"] == 5
    events = (tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-21.json").read_text()
    assert events.count('"letter": "A+"') == 1


def test_latch_survives_restart(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row(letter="A+")], "rest": []}, NOW)
    p = {"rows": [row(letter="A+", last=110.0)], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, NOW + timedelta(minutes=9))
    assert p["rows"][0]["gradeFresh"]["firstToday"]["A+"]["price"] == 100.0


def test_tape_written_at_most_every_5_minutes(tmp_path):
    # Brief's test, adapted: the tape is append-only JSONL (one line per
    # 5-minute sample) because a whole-file rewrite measured ~480 ms at the
    # end of a 358-symbol day. read_tape() rebuilds the brief's document shape.
    log = GradeLog(tmp_path)
    for s in (0, 60, 120, 301):
        log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW + timedelta(seconds=s))
    path = tmp_path / "momx_grade_tape" / "Watchlist" / "2026-09-21.jsonl"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 2
    tape = read_tape(tmp_path, "Watchlist", "2026-09-21")
    assert len(tape["entries"]["AAA"]) == 2


def test_apply_never_raises_on_garbage(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [None, 5, {"symbol": None}], "rest": None}, NOW)


# ---- beyond the brief -------------------------------------------------------


def full_row(letter="A+", last=100.0):
    return {
        "symbol": "BBB", "last": last, "pctChange": 3.2,
        "skittles": {"2h": {"bg": "black", "fg": "cyan"}, "4h": {"bg": "green", "fg": "dark_green"}},
        "rvol": {"5m": {"value": 2.4, "bg": "green", "barAt": 1790000000},
                 "15m": {"value": 0.3, "bg": "black"}},
        "sqz": {"4h": {"bg": "cyan"}},
        "sqzRaw": {"4h": {"ms": False, "msf": 1, "lastReleaseAt": 1789990000}},
        "news": {"headline": "BBB wins contract", "at": "2026-09-21T12:00:00+00:00"},
        "m5": {"chart": "holding", "state": "building", "pattern": "explosive", "trigger": 99.5,
               "lastCompleted": {"time": 1790000000, "open": 99, "high": 101, "low": 98.5,
                                 "close": 100.5, "volume": 12345}},
        "grade": {"letter": letter, "reasons": ["SQZ fired 4h", "SKIT 7/8"],
                  "checks": {"skit": 7, "hlDegree": 71.5, "newsAgeHours": 1.53}},
    }


def test_event_record_carries_every_spec_field(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Mag7", {"rows": [full_row()], "rest": []}, NOW)
    doc = json.loads((tmp_path / "momx_grade_events" / "Mag7" / "2026-09-21.json").read_text())
    # full_row is also Explosive while Building, so a pattern event follows it.
    event, pattern_event = doc["events"]
    assert event["kind"] == "letter" and pattern_event["kind"] == "pattern"
    assert pattern_event["letter"] == "A+"
    assert event["board"] == "Mag7" and event["symbol"] == "BBB" and event["letter"] == "A+"
    assert event["at"] == NOW.isoformat() and event["price"] == 100.0
    assert event["reasons"] == ["SQZ fired 4h", "SKIT 7/8"]
    assert event["checks"]["skit"] == 7
    assert event["push"]["rvol"]["5m"] == {"value": 2.4, "bg": "green", "barAt": 1790000000}
    assert set(event["push"]["rvol"]) == {"5m", "15m", "30m", "1h", "2h", "4h"}
    assert event["push"]["rvol"]["1h"] == {"value": None, "bg": None, "barAt": None}
    assert event["push"]["news"] == {"headline": "BBB wins contract",
                                     "at": "2026-09-21T12:00:00+00:00", "ageHours": 1.53}
    assert event["sqzRaw"]["4h"]["lastReleaseAt"] == 1789990000
    assert event["skittles"]["4h"] == {"fg": "dark_green", "bg": "green", "bgChangedAt": None}
    assert len(event["skittles"]) == 8
    assert event["hlDegree"] == 71.5
    assert event["lastCompleted5m"]["close"] == 100.5
    assert event["m5"]["trigger"] == 99.5
    assert event["pattern"] == "explosive" and event["momentum"] == "building"
    assert event["pctChange"] == 3.2


def test_bg_changed_at_comes_from_consecutive_scans(tmp_path):
    log = GradeLog(tmp_path)
    before = full_row(letter=None)
    before["skittles"]["4h"]["bg"] = "black"
    log.apply("Mag7", {"rows": [before], "rest": []}, NOW)
    later = NOW + timedelta(seconds=20)
    log.apply("Mag7", {"rows": [full_row()], "rest": []}, later)
    events = json.loads((tmp_path / "momx_grade_events" / "Mag7" / "2026-09-21.json").read_text())["events"]
    (event,) = [e for e in events if e["kind"] == "letter"]
    assert event["skittles"]["4h"]["bgChangedAt"] == later.isoformat()
    assert event["skittles"]["2h"]["bgChangedAt"] is None


def test_each_letter_is_its_own_first(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Mag7", {"rows": [full_row("B", 90.0)], "rest": []}, NOW)
    p = {"rows": [full_row("A+", 95.0)], "rest": []}
    log.apply("Mag7", p, NOW + timedelta(minutes=12))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["firstToday"]["B"]["price"] == 90.0
    assert fresh["firstToday"]["A+"]["price"] == 95.0
    assert fresh["firstToday"]["A"] is None
    assert fresh["ageMinutes"] == 0
    p2 = {"rows": [full_row(None, 96.0)], "rest": []}
    log.apply("Mag7", p2, NOW + timedelta(minutes=13))
    assert p2["rows"][0]["gradeFresh"]["ageMinutes"] is None


def test_other_timeline_kinds(tmp_path):
    log = GradeLog(tmp_path)
    quiet = full_row(None)
    quiet["rvol"]["5m"]["bg"] = "black"
    quiet["sqz"]["4h"]["bg"] = "orange"
    quiet["news"] = None
    quiet["m5"]["chart"] = "below"
    log.apply("Mag7", {"rows": [quiet], "rest": []}, NOW)
    p = {"rows": [], "rest": [full_row(None)]}
    log.apply("Mag7", p, NOW + timedelta(seconds=20))
    fresh = p["rest"][0]["gradeFresh"]
    whats = [item["what"] for item in fresh["timeline"]]
    assert "RVOL 5m 2.4" in whats
    assert "SQZ 4h released" in whats
    assert "news" in whats
    assert "5m breakout confirmed" not in whats  # holding is not a new confirmation
    assert fresh["icons"] == ["RVOL", "SQZ", "NEWS"]


def test_breakout_confirmed_is_a_timeline_item(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Mag7", {"rows": [row()], "rest": []}, NOW)
    confirmed = row()
    confirmed["m5"]["chart"] = "breakout_confirmed"
    p = {"rows": [confirmed], "rest": []}
    log.apply("Mag7", p, NOW + timedelta(seconds=20))
    assert p["rows"][0]["gradeFresh"]["timeline"][-1]["what"] == "5m breakout confirmed"
    assert p["rows"][0]["gradeFresh"]["icons"] == []


def test_first_scan_after_start_has_blank_timeline(tmp_path):
    p = {"rows": [row("green")], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, NOW)
    assert p["rows"][0]["gradeFresh"]["timeline"] == []
    assert p["rows"][0]["gradeFresh"]["icons"] == []


def test_new_day_resets_latch_and_timeline(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    log.apply("Watchlist", {"rows": [row("green", letter="A+")], "rest": []}, NOW + timedelta(seconds=20))
    p = {"rows": [row("green", letter="A+", last=120.0)], "rest": []}
    tomorrow = NOW + timedelta(days=1)
    log.apply("Watchlist", p, tomorrow)
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["timeline"] == []
    assert fresh["firstToday"]["A+"]["price"] == 120.0
    assert (tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-22.json").exists()


def test_status_counts_today(tmp_path):
    log = GradeLog(tmp_path)
    assert log.status() == {"tapeLastWriteAt": None, "tapeEntriesToday": 0, "eventsToday": 0}
    log.apply("Watchlist", {"rows": [row(letter="A")], "rest": []}, NOW)
    log.apply("Mag7", {"rows": [full_row("A+")], "rest": []}, NOW)
    status = log.status()
    assert status["tapeLastWriteAt"] == NOW.isoformat()
    assert status["tapeEntriesToday"] == 2
    assert status["eventsToday"] == 2


def test_status_counts_survive_restart(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row(letter="A")], "rest": []}, NOW)
    log = GradeLog(tmp_path)
    # No tape due yet (60 s after the last sample), but today's files count.
    log.apply("Watchlist", {"rows": [row(letter="A")], "rest": []}, NOW + timedelta(seconds=60))
    status = log.status()
    assert status["tapeEntriesToday"] == 1
    assert status["eventsToday"] == 1
    assert len(read_tape(tmp_path, "Watchlist", "2026-09-21")["entries"]["AAA"]) == 1


def test_tape_entry_fields(tmp_path):
    GradeLog(tmp_path).apply("Mag7", {"rows": [full_row("A+")], "rest": []}, NOW)
    (entry,) = read_tape(tmp_path, "Mag7", "2026-09-21")["entries"]["BBB"]
    assert entry == {"t": NOW.isoformat(), "last": 100.0, "letter": "A+",
                     "checks": {"skit": 7, "hlDegree": 71.5, "newsAgeHours": 1.53},
                     "reasons": ["SQZ fired 4h", "SKIT 7/8"], "m5state": "building",
                     "chart": "holding", "trigger": 99.5, "pattern": "explosive"}


def test_read_tape_skips_a_torn_last_line(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    path = tmp_path / "momx_grade_tape" / "Watchlist" / "2026-09-21.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"t": "2026-09-21T09:37')
    assert len(read_tape(tmp_path, "Watchlist", "2026-09-21")["entries"]["AAA"]) == 1
    assert read_tape(tmp_path, "Watchlist", "2026-01-01") is None
    # A restarted worker counts from the line headers and skips the torn line too.
    restarted = GradeLog(tmp_path)
    restarted.apply("Watchlist", {"rows": [row()], "rest": []}, NOW + timedelta(seconds=30))
    assert restarted.status()["tapeEntriesToday"] == 1
    assert restarted.status()["tapeLastWriteAt"] == NOW.isoformat()
    # The next sample after a torn line is not swallowed by it.
    later = NOW + timedelta(seconds=301)
    restarted.apply("Watchlist", {"rows": [row()], "rest": []}, later)
    assert len(read_tape(tmp_path, "Watchlist", "2026-09-21")["entries"]["AAA"]) == 2
    again = GradeLog(tmp_path)
    again.apply("Watchlist", {"rows": [row()], "rest": []}, later + timedelta(seconds=20))
    assert again.status() == {"tapeLastWriteAt": later.isoformat(), "tapeEntriesToday": 2,
                              "eventsToday": 0}


def test_status_today_uses_the_et_clock_not_the_last_apply_day(tmp_path, monkeypatch):
    """status() must not report yesterday's counts as "today" just because
    no scan has landed yet after ET midnight."""
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row(letter="A")], "rest": []}, NOW)

    # The autouse fixture already pins _today_et() to 2026-09-21 (NOW's day).
    assert log.status()["eventsToday"] == 1

    # ET midnight has passed with no new scan yet: status() must report the
    # NEW day's (empty) counts, not relabel yesterday's counts as today's.
    monkeypatch.setattr(grade_log_module, "_today_et", lambda: "2026-09-22")
    status = log.status()
    assert status["eventsToday"] == 0
    assert status["tapeEntriesToday"] == 0
    assert status["tapeLastWriteAt"] is None


def test_board_name_with_odd_characters_round_trips(tmp_path):
    # Only Watchlist/Mag7 write a tape, so the round trip is checked on the
    # event file: a restart must reload the odd-named board's latch, not
    # log the same A a second time.
    board = 'My "list" \\ 2'
    GradeLog(tmp_path).apply(board, {"rows": [row(letter="A")], "rest": []}, NOW)
    restarted = GradeLog(tmp_path)
    p = {"rows": [row(letter="A")], "rest": []}
    restarted.apply(board, p, NOW + timedelta(seconds=20))
    assert restarted.status()["eventsToday"] == 1
    assert p["rows"][0]["gradeFresh"]["firstToday"]["A"]["at"] == NOW.isoformat()


def test_prune_removes_files_older_than_30_days_once_per_day(tmp_path):
    old_tape = tmp_path / "momx_grade_tape" / "Watchlist" / "2026-08-21.jsonl"
    old_events = tmp_path / "momx_grade_events" / "Watchlist" / "2026-08-21.json"
    keep = tmp_path / "momx_grade_events" / "Watchlist" / "2026-08-22.json"
    stray = tmp_path / "momx_grade_events" / "Watchlist" / "notes.txt"
    for path in (old_tape, old_events, keep, stray):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    assert not old_tape.exists() and not old_events.exists()
    assert keep.exists() and stray.exists()
    # Once per day: a file that appears later the same day is left alone.
    old_events.write_text("{}", encoding="utf-8")
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW + timedelta(minutes=10))
    assert old_events.exists()


def test_unwritable_directory_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    p = {"rows": [row(letter="A+")], "rest": []}
    GradeLog(blocker).apply("Watchlist", p, NOW)
    assert p["rows"][0]["gradeFresh"]["firstToday"]["A+"]["price"] == 100.0



# --------------------------------------------------------------- Task 7: outcomes

from momx.grade_log import record_response, tape_response  # noqa: E402

DAY = "2026-09-21"
AFTER_CLOSE = datetime(2026, 9, 21, 16, 20, tzinfo=ET)


def _session_bars():
    """10:00 .. 15:55 ET 5m bars, plus one 16:00 after-hours bar that must be
    ignored. Closes step 100.1, 100.2 ... up to 101.3 (11:00) then hold; the
    10:10 bar's high is 103, the 10:40 bar's low is 98, the 15:55 close is 101."""
    bars = []
    t = datetime(2026, 9, 21, 10, 0, tzinfo=ET)
    i = 0
    while t.hour < 16:
        close = round(100 + 0.1 * (min(i, 12) + 1), 4)
        bars.append({"time": int(t.timestamp()), "open": 100.0, "high": max(close, 100.5),
                     "low": min(close, 99.5), "close": close, "volume": 1000})
        t += timedelta(minutes=5)
        i += 1
    bars[2]["high"] = 103.0           # 10:10
    bars[8]["low"] = 98.0             # 10:40
    bars[-1]["close"] = 101.0         # 15:55
    bars.append({"time": int(t.timestamp()), "open": 101.0, "high": 150.0, "low": 50.0,
                 "close": 120.0, "volume": 5})   # 16:00, after the close
    return bars


def _event(symbol="AAA", letter="A+", at="10:00", price=100.0, momentum="Building",
           pattern="Explosive", bg_changed=None, board="Watchlist"):
    hh, mm = (int(x) for x in at.split(":"))
    at_dt = datetime(2026, 9, 21, hh, mm, tzinfo=ET)
    return {"board": board, "symbol": symbol, "letter": letter, "at": at_dt.isoformat(),
            "price": price, "reasons": [], "checks": {},
            "skittles": {"4h": {"fg": "cyan", "bg": "green", "bgChangedAt": bg_changed}},
            "pattern": pattern, "momentum": momentum}


def _write_events(directory, events, board="Watchlist"):
    path = directory / "momx_grade_events" / board / f"{DAY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"board": board, "date": DAY, "events": events}), encoding="utf-8")


def _read_events(directory, board="Watchlist"):
    path = directory / "momx_grade_events" / board / f"{DAY}.json"
    return json.loads(path.read_text(encoding="utf-8"))["events"]


def test_outcome_math_from_5m_bars(tmp_path):
    _write_events(tmp_path, [_event()])
    calls = []

    def fetch(symbols, days=1):
        calls.append(list(symbols))
        return {"AAA": _session_bars()}

    assert GradeLog(tmp_path).nightly(AFTER_CLOSE, fetch) is True
    assert calls == [["AAA"]]
    out = _read_events(tmp_path)[0]["outcome"]
    # pN = close of the LAST bar whose END (start + 5 min) is <= event + N min.
    assert out["p5"] == pytest.approx(0.1)    # 10:00 bar (ends 10:05) close 100.1
    assert out["p15"] == pytest.approx(0.3)   # 10:10 bar (ends 10:15) close 100.3
    assert out["p30"] == pytest.approx(0.6)   # 10:25 bar (ends 10:30) close 100.6
    assert out["p60"] == pytest.approx(1.2)   # 10:55 bar (ends 11:00) close 101.2
    assert out["close"] == 1.0                # 15:55 bar, not the 16:00 bar
    assert out["maxFav"] == 3.0
    assert out["maxAdv"] == -2.0


def test_event_after_the_close_gets_an_empty_outcome(tmp_path):
    _write_events(tmp_path, [_event(at="16:05")])
    assert GradeLog(tmp_path).nightly(AFTER_CLOSE, lambda s, days=1: {"AAA": _session_bars()}) is True
    out = _read_events(tmp_path)[0]["outcome"]
    assert out == {"p5": None, "p15": None, "p30": None, "p60": None,
                   "close": None, "maxFav": None, "maxAdv": None}
    assert record_response(tmp_path) == {"source": None, "patternSource": None, "direction": "bull"}   # nothing countable


def test_nightly_waits_for_1615_and_runs_once_per_day(tmp_path):
    _write_events(tmp_path, [_event()])
    fetch = lambda symbols, days=1: {"AAA": _session_bars()}  # noqa: E731
    log = GradeLog(tmp_path)
    assert log.nightly(datetime(2026, 9, 21, 16, 10, tzinfo=ET), fetch) is False
    assert "outcome" not in _read_events(tmp_path)[0]
    assert log.nightly(AFTER_CLOSE, fetch) is True
    assert log.nightly(AFTER_CLOSE + timedelta(minutes=5), fetch) is False
    # Another thread's / a restarted process's GradeLog sees builtFor and skips.
    assert GradeLog(tmp_path).nightly(AFTER_CLOSE + timedelta(minutes=9), fetch) is False
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["builtFor"] == DAY


def test_nightly_with_no_bars_does_not_mark_the_day_done(tmp_path):
    _write_events(tmp_path, [_event()])
    log = GradeLog(tmp_path)
    assert log.nightly(AFTER_CLOSE, lambda symbols, days=1: {}) is False
    assert not (tmp_path / "momx_grade_record.json").exists()
    good = lambda symbols, days=1: {"AAA": _session_bars()}  # noqa: E731
    # Throttled: not retried on the very next build ...
    assert log.nightly(AFTER_CLOSE + timedelta(minutes=1), good) is False
    # ... but retried later the same evening.
    assert log.nightly(AFTER_CLOSE + timedelta(minutes=16), good) is True
    assert _read_events(tmp_path)[0]["outcome"]["close"] == 1.0


def test_nightly_fetch_exception_returns_false(tmp_path):
    _write_events(tmp_path, [_event()])

    def boom(symbols, days=1):
        raise RuntimeError("feed down")

    assert GradeLog(tmp_path).nightly(AFTER_CLOSE, boom) is False


def test_outcome_survives_a_later_event_rewrite(tmp_path, monkeypatch):
    """apply() rewrites the day's events file from memory; the outcomes nightly
    writes must be in that memory or the next event erases them.

    Events are now logged only 09:30-15:59 ET (_in_session), so a same-day
    event after the 16:15 nightly cannot happen with today's session rule.
    The in-memory invariant is still what keeps a rewrite safe, so the
    rewrite is forced here by treating the second apply as in-session."""
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row(letter="A+", last=100.0)], "rest": []},
              datetime(2026, 9, 21, 10, 0, tzinfo=ET))
    assert log.nightly(AFTER_CLOSE, lambda symbols, days=1: {"AAA": _session_bars()}) is True
    monkeypatch.setattr(grade_log_module, "_in_session", lambda now: True)
    log.apply("Watchlist", {"rows": [row(letter="B", last=101.0)], "rest": []},
              AFTER_CLOSE + timedelta(minutes=2))
    events = _read_events(tmp_path)
    assert [e["letter"] for e in events] == ["A+", "B"]
    assert events[0]["outcome"]["close"] == 1.0


def _mirrored_bars():
    """_session_bars reflected around 100: every move is the negative of AAA's."""
    bars = _session_bars()
    for bar in bars:
        bar["close"] = round(200 - bar["close"], 4)
        bar["high"], bar["low"] = 200 - bar["low"], 200 - bar["high"]
    return bars


def test_record_aggregates_two_events(tmp_path):
    fresh_at = datetime(2026, 9, 21, 9, 50, tzinfo=ET).isoformat()
    _write_events(tmp_path, [
        _event("AAA", "A+", "10:00", momentum="Building", pattern="Explosive", bg_changed=fresh_at),
        _event("BBB", "A+", "10:00", momentum="Fading", pattern="Steady"),
    ])
    assert GradeLog(tmp_path).nightly(
        AFTER_CLOSE, lambda s, days=1: {"AAA": _session_bars(), "BBB": _mirrored_bars()})
    record = record_response(tmp_path)
    assert record["source"] == "recorded"
    assert record["days"] == [DAY]
    a_plus = record["letters"]["A+"]
    assert a_plus["count"] == 2
    assert a_plus["pctHigher15"] == 50.0
    assert a_plus["pctHigher60"] == 50.0
    assert a_plus["pctHigherClose"] == 50.0
    assert a_plus["avgToClose"] == 0.0
    assert a_plus["medianToClose"] == 0.0
    assert a_plus["avgMaxFav"] == pytest.approx(2.5)    # (3 + 2) / 2
    assert a_plus["avgMaxAdv"] == pytest.approx(-2.5)   # (-2 + -3) / 2
    # A letter with no events keeps every key, values None.
    assert record["letters"]["B"] == {
        "count": 0, "pctHigher15": None, "pctHigher60": None, "pctHigherClose": None,
        "avgToClose": None, "medianToClose": None, "avgMaxFav": None, "avgMaxAdv": None}
    assert record["byMomentum"]["A+"]["Building"]["avgToClose"] == 1.0
    assert record["byMomentum"]["A+"]["Fading"]["avgToClose"] == -1.0
    assert record["byFresh"]["A+"]["fresh"]["count"] == 1
    assert record["byFresh"]["A+"]["fresh"]["avgToClose"] == 1.0
    assert record["byFresh"]["A+"]["notFresh"]["avgToClose"] == -1.0
    assert record["byFresh"]["B"]["fresh"]["count"] == 0
    assert record["byPattern"]["A+"]["Explosive"]["avgToClose"] == 1.0
    assert record["byPattern"]["A+"]["Steady"]["avgToClose"] == -1.0


def test_record_counts_one_event_per_symbol_letter_day_across_boards(tmp_path):
    _write_events(tmp_path, [_event("AAA", "A+", "10:00")])
    _write_events(tmp_path, [_event("AAA", "A+", "10:00", board="Mag7")], board="Mag7")
    GradeLog(tmp_path).nightly(AFTER_CLOSE, lambda s, days=1: {"AAA": _session_bars()})
    assert record_response(tmp_path)["letters"]["A+"]["count"] == 1
    # Both boards' files still carry the outcome.
    assert _read_events(tmp_path, board="Mag7")[0]["outcome"]["close"] == 1.0


def test_record_response_falls_back_to_backtest_then_none(tmp_path):
    assert record_response(tmp_path) == {"source": None, "patternSource": None, "direction": "bull"}
    backtest = {"days": ["2026-09-01", "2026-09-02"], "source": "History archive",
                "letters": {"A+": {"count": 3, "pctHigherClose": 44.0, "avgToClose": 0.1,
                                   "medianToClose": -0.1, "up3": 1, "down3": 0}},
                "events": [{"day": "2026-09-01"}]}
    (tmp_path / "momx_grade_record_backtest.json").write_text(json.dumps(backtest), encoding="utf-8")
    got = record_response(tmp_path)
    assert got["source"] == "back-test from History archive (2 days)"
    assert got["letters"]["A+"]["count"] == 3
    # A recorded record with no days yet still defers to the back-test.
    (tmp_path / "momx_grade_record.json").write_text(
        json.dumps({"source": "recorded", "builtFor": DAY, "days": [], "letters": {}}),
        encoding="utf-8")
    assert record_response(tmp_path)["source"].startswith("back-test")


def _sym_row(symbol, letter):
    r = row(letter=letter)
    r["symbol"] = symbol
    return r


def test_tape_response_merges_boards_and_filters_symbol(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [_sym_row("META", "A+"), _sym_row("NVDA", "B")], "rest": []}, NOW)
    log.apply("Mag7", {"rows": [_sym_row("META", "A"), _sym_row("TSLA", "B")], "rest": []}, NOW)
    got = tape_response(tmp_path, "meta", days=5)
    assert got["symbol"] == "META"
    assert [(e["t"], e["letter"], e["board"]) for e in got["entries"]] == [
        (NOW.isoformat(), "A+", "Watchlist"),
    ]
    # A symbol only on Mag7 comes from Mag7.
    assert [e["board"] for e in tape_response(tmp_path, "TSLA")["entries"]] == ["Mag7"]
    assert set(got["entries"][0]) >= {"t", "letter", "reasons", "m5state", "chart", "trigger", "board"}
    assert tape_response(tmp_path, "ZZZZ")["entries"] == []


def test_tape_response_limits_days_and_skips_torn_lines(tmp_path):
    for day in (17, 18, 21):
        GradeLog(tmp_path).apply("Watchlist", {"rows": [row(letter="B")], "rest": []},
                                 datetime(2026, 9, day, 10, 0, tzinfo=ET))
    path = tmp_path / "momx_grade_tape" / "Watchlist" / "2026-09-21.jsonl"
    with path.open("ab") as handle:
        handle.write(b'{"t":"2026-09-21T10:05:00-04:00","board":"Watchlist","n":1,'
                     b'"entries":{"AAA":{"t":"2026')
    got = tape_response(tmp_path, "AAA", days=2)
    assert [e["t"][:10] for e in got["entries"]] == ["2026-09-18", "2026-09-21"]


def test_worker_routes_serve_tape_and_record(tmp_path, monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    import momx_worker
    from momx import service

    monkeypatch.setattr(service, "grade_dir", lambda direction="bull": tmp_path)
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row(letter="A+")], "rest": []}, NOW)
    server = ThreadingHTTPServer(("127.0.0.1", 0), momx_worker.Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def get(path):
        with urllib.request.urlopen(base + path, timeout=5) as resp:
            return resp.status, json.loads(resp.read())

    try:
        status, body = get("/api/momx-scanner/grade-tape?symbol=aaa&days=99")
        assert status == 200 and body["symbol"] == "AAA"
        assert body["entries"][0]["letter"] == "A+"
        assert get("/api/momx-scanner/grade-record") == (200, {"source": None, "patternSource": None, "direction": "bull"})
        with pytest.raises(urllib.error.HTTPError) as err:
            get("/api/momx-scanner/grade-tape")
        assert err.value.code == 400
    finally:
        server.shutdown()
        server.server_close()


def test_tape_response_watchlist_wins_even_when_boards_scan_at_different_times(tmp_path, monkeypatch):
    """Each board stamps its own scan time, so Watchlist and Mag7 samples never
    share a ``t``: precedence is per (symbol, date), not per sample."""
    # The look-back starts at "today"; this test's newest sample is 09-22.
    monkeypatch.setattr(grade_log_module, "_today_et", lambda: "2026-09-22")
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [_sym_row("META", "A+")], "rest": []}, NOW)
    log.apply("Mag7", {"rows": [_sym_row("META", "B")], "rest": []}, NOW + timedelta(seconds=17))
    log.apply("Watchlist", {"rows": [_sym_row("META", "A")], "rest": []}, NOW + timedelta(minutes=5))
    log.apply("Mag7", {"rows": [_sym_row("META", "B")], "rest": []},
              NOW + timedelta(minutes=5, seconds=23))
    # The next day only Mag7 carried META: Mag7 fills that date.
    nxt = datetime(2026, 9, 22, 9, 40, tzinfo=ET)
    log.apply("Watchlist", {"rows": [_sym_row("NVDA", "B")], "rest": []}, nxt)
    log.apply("Mag7", {"rows": [_sym_row("META", "B")], "rest": []}, nxt + timedelta(seconds=9))
    got = tape_response(tmp_path, "META", days=5)["entries"]
    assert [(e["t"], e["letter"], e["board"]) for e in got] == [
        (NOW.isoformat(), "A+", "Watchlist"),
        ((NOW + timedelta(minutes=5)).isoformat(), "A", "Watchlist"),
        ((nxt + timedelta(seconds=9)).isoformat(), "B", "Mag7"),
    ]


# ------------------------------------------ fix round: horizons + catch-up

def test_off_boundary_event_uses_the_last_bar_ending_by_each_horizon():
    event = _event(at="10:00", price=100.0)
    event["at"] = datetime(2026, 9, 21, 10, 2, 30, tzinfo=ET).isoformat()
    out = grade_log_module.outcome_for(event, _session_bars())
    assert out["p5"] == pytest.approx(0.1)    # 10:07:30 -> 10:00 bar (ends 10:05)
    assert out["p15"] == pytest.approx(0.3)   # 10:17:30 -> 10:10 bar (ends 10:15)
    assert out["p30"] == pytest.approx(0.6)   # 10:32:30 -> 10:25 bar (ends 10:30)
    assert out["p60"] == pytest.approx(1.2)   # 11:02:30 -> 10:55 bar (ends 11:00)
    assert out["close"] == 1.0                # 15:55 bar
    # Bars only through the 10:15 bar (ends 10:20): p30/p60 have no bar yet.
    early = [b for b in _session_bars()
             if b["time"] < datetime(2026, 9, 21, 10, 20, tzinfo=ET).timestamp()]
    out = grade_log_module.outcome_for(event, early)
    assert out["p5"] == pytest.approx(0.1) and out["p15"] == pytest.approx(0.3)
    assert out["p30"] is None and out["p60"] is None


def test_horizon_past_the_session_end_is_none():
    out = grade_log_module.outcome_for(_event(at="15:30"), _session_bars())
    assert out["p15"] == pytest.approx(1.3)   # 15:40 bar (ends 15:45)
    assert out["p30"] == 1.0                  # 15:55 bar ends 16:00 exactly
    assert out["p60"] is None                 # 16:30 is outside the session


def _shift_bars(bars, days):
    out = []
    for bar in bars:
        bar = dict(bar)
        bar["time"] = bar["time"] - days * 86400
        out.append(bar)
    return out


def _write_day(directory, day, events, board="Watchlist"):
    path = directory / "momx_grade_events" / board / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"board": board, "date": day, "events": events}), encoding="utf-8")


def _read_day(directory, day, board="Watchlist"):
    path = directory / "momx_grade_events" / board / f"{day}.json"
    return json.loads(path.read_text(encoding="utf-8"))["events"]


def _dated_event(day_offset, symbol="AAA", letter="A+"):
    e = _event(symbol, letter, "10:00")
    e["at"] = (datetime(2026, 9, 21, 10, 0, tzinfo=ET) - timedelta(days=day_offset)).isoformat()
    return e


def test_yesterdays_unscored_events_are_scored_on_todays_run(tmp_path):
    """The worker was down all of Friday 09-18's evening; Monday's run --
    even before 16:15 -- scores it, with a fetch deep enough to reach it."""
    _write_day(tmp_path, "2026-09-18", [_dated_event(3)])
    calls = []

    def fetch(symbols, days=1):
        calls.append((list(symbols), days))
        return {"AAA": _shift_bars(_session_bars(), 3)}

    assert GradeLog(tmp_path).nightly(NOW, fetch) is True        # 09:32 ET
    assert calls == [(["AAA"], 3)]
    assert _read_day(tmp_path, "2026-09-18")[0]["outcome"]["close"] == 1.0
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["days"] == ["2026-09-18"]
    assert record.get("builtFor") != DAY    # today's evening run is still owed
    # Nothing is left pending: later pre-close calls do not refetch.
    log = GradeLog(tmp_path)
    assert log.nightly(NOW, fetch) is False
    assert log.nightly(NOW + timedelta(hours=1), fetch) is False
    assert len(calls) == 1


def test_symbol_missing_from_the_fetch_stays_unscored_and_is_retried(tmp_path):
    _write_events(tmp_path, [_event("AAA"), _event("BBB")])
    calls = []

    def partial(symbols, days=1):
        calls.append(list(symbols))
        return {"AAA": _session_bars()}   # BBB came back empty

    log = GradeLog(tmp_path)
    assert log.nightly(AFTER_CLOSE, partial) is True      # AAA was scored
    events = {e["symbol"]: e for e in _read_events(tmp_path)}
    assert events["AAA"]["outcome"]["close"] == 1.0
    assert "outcome" not in events["BBB"]
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["letters"]["A+"]["count"] == 1          # rebuilt with AAA
    assert record.get("builtFor") != DAY                  # the day is NOT done

    def both(symbols, days=1):
        calls.append(list(symbols))
        return {"AAA": _session_bars(), "BBB": _mirrored_bars()}

    # Throttled, then retried -- asking only for what is still missing.
    assert log.nightly(AFTER_CLOSE + timedelta(minutes=1), both) is False
    assert log.nightly(AFTER_CLOSE + timedelta(minutes=16), both) is True
    assert calls == [["AAA", "BBB"], ["BBB"]]
    assert {e["symbol"]: e["outcome"]["close"] for e in _read_events(tmp_path)} == {
        "AAA": 1.0, "BBB": -1.0}
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["builtFor"] == DAY and record["letters"]["A+"]["count"] == 2
    assert log.nightly(AFTER_CLOSE + timedelta(minutes=40), both) is False


def test_bars_not_covering_the_event_date_leave_it_unscored(tmp_path):
    _write_day(tmp_path, "2026-09-18", [_dated_event(3)])
    # Bars only for today: they say nothing about 09-18.
    assert GradeLog(tmp_path).nightly(NOW, lambda s, days=1: {"AAA": _session_bars()}) is False
    assert "outcome" not in _read_day(tmp_path, "2026-09-18")[0]


def test_todays_events_are_not_scored_before_1615(tmp_path):
    _write_events(tmp_path, [_event()])
    calls = []

    def fetch(symbols, days=1):
        calls.append(list(symbols))
        return {"AAA": _session_bars()}

    log = GradeLog(tmp_path)
    for t in (NOW, datetime(2026, 9, 21, 16, 0, tzinfo=ET), datetime(2026, 9, 21, 16, 14, tzinfo=ET)):
        assert log.nightly(t, fetch) is False
    assert calls == []
    assert "outcome" not in _read_events(tmp_path)[0]
    assert log.nightly(AFTER_CLOSE, fetch) is True
    assert _read_events(tmp_path)[0]["outcome"]["close"] == 1.0


# ------------------------------------------- fix round 2: capped catch-up

def test_symbol_missing_four_runs_becomes_unavailable_and_is_never_requested_again(tmp_path):
    """A symbol whose fetch never covers its event date must not be retried
    forever -- that is a sustained, uncached Schwab volume-correction burst
    every 15 minutes, all day, for up to CATCHUP_MAX_DAYS days (see
    momx/feed.py::fetch_5m schwab_volume). After MAX_FETCH_ATTEMPTS (4)
    consecutive misses it is given up on: outcome={"unavailable": True},
    excluded from the track record, and dropped from the pending set."""
    _write_events(tmp_path, [_event("BBB")])
    calls = []

    def empty(symbols, days=1):
        calls.append(list(symbols))
        return {}  # BBB never comes back, on any run

    log = GradeLog(tmp_path)
    t = AFTER_CLOSE
    for attempt in range(1, grade_log_module.MAX_FETCH_ATTEMPTS):
        assert log.nightly(t, empty) is False       # still missing: not complete
        assert calls == [["BBB"]] * attempt
        assert "outcome" not in _read_events(tmp_path)[0]
        t += timedelta(minutes=grade_log_module.NIGHTLY_RETRY_MINUTES + 1)

    # The 4th miss: given up on, marked unavailable, and the day IS complete.
    assert log.nightly(t, empty) is True
    assert calls == [["BBB"]] * grade_log_module.MAX_FETCH_ATTEMPTS
    event = _read_events(tmp_path)[0]
    assert event["outcome"] == {"unavailable": True}
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["builtFor"] == DAY
    assert record["unscored"]["A+"] == 1
    assert record["letters"]["A+"]["count"] == 0   # excluded from the numeric stats

    # Run 5: BBB is no longer pending (it has an "outcome"), so it is not
    # fetched again, and nothing changes.
    calls.clear()
    t += timedelta(minutes=grade_log_module.NIGHTLY_RETRY_MINUTES + 1)
    assert log.nightly(t, empty) is False
    assert calls == []


def test_a_symbol_that_recovers_before_the_cap_is_scored_normally_and_keeps_no_attempt_debt(tmp_path):
    """A transient miss must not count toward some LATER, unrelated run of
    misses: the counter resets the moment bars finally cover the date."""
    _write_events(tmp_path, [_event("BBB")])
    log = GradeLog(tmp_path)
    t = AFTER_CLOSE

    # Two misses (below the cap), then a hit.
    for _ in range(2):
        assert log.nightly(t, lambda s, days=1: {}) is False
        t += timedelta(minutes=grade_log_module.NIGHTLY_RETRY_MINUTES + 1)
    assert log.nightly(t, lambda s, days=1: {"BBB": _session_bars()}) is True
    event = _read_events(tmp_path)[0]
    assert event["outcome"]["close"] == 1.0
    assert ("BBB", DAY) not in log._fetch_attempts


def test_build_record_excludes_unavailable_events_and_counts_them_as_unscored(tmp_path):
    scored = _event("AAA", letter="A+")
    scored["outcome"] = {
        "p5": 1.0, "p15": 1.0, "p30": 1.0, "p60": 1.0,
        "close": 1.0, "maxFav": 1.0, "maxAdv": -1.0,
    }
    unavailable_a_plus = _event("BBB", letter="A+")
    unavailable_a_plus["outcome"] = {"unavailable": True}
    unavailable_b = _event("CCC", letter="B")
    unavailable_b["outcome"] = {"unavailable": True}
    _write_events(tmp_path, [scored, unavailable_a_plus, unavailable_b])

    record = grade_log_module.build_record(tmp_path)
    assert record["letters"]["A+"]["count"] == 1
    assert record["unscored"] == {"A+": 1, "A": 0, "B": 1}


# ------------------------------------------ Task 7b: pattern events

def _pattern_row(pattern="steady", state="building", letter=None, last=100.0, symbol="NFLX"):
    r = row(letter=letter, last=last)
    r["symbol"] = symbol
    r["m5"] = {"chart": "holding", "state": state, "pattern": pattern, "trigger": 99.0}
    return r


def _events_file(directory, board="Watchlist"):
    path = directory / "momx_grade_events" / board / "2026-09-21.json"
    return json.loads(path.read_text(encoding="utf-8"))["events"]


def test_steady_building_without_a_letter_logs_one_pattern_event(tmp_path):
    log = GradeLog(tmp_path)
    p = {"rows": [_pattern_row()], "rest": []}
    log.apply("Watchlist", p, NOW)
    (event,) = _events_file(tmp_path)
    assert event["kind"] == "pattern"
    assert event["letter"] is None
    assert event["pattern"] == "steady" and event["momentum"] == "building"
    assert event["symbol"] == "NFLX" and event["price"] == 100.0 and event["at"] == NOW.isoformat()
    # Same fields as a letter event.
    for key in ("reasons", "checks", "push", "sqzRaw", "skittles", "hlDegree",
                "lastCompleted5m", "m5", "pctChange", "board"):
        assert key in event
    assert "bgChangedAt" in event["skittles"]["4h"]
    patterns = p["rows"][0]["gradeFresh"]["firstToday"]["patterns"]
    assert patterns == {"explosive": None, "steady": {"at": NOW.isoformat(), "price": 100.0}}
    assert p["rows"][0]["gradeFresh"]["firstToday"]["A+"] is None


def test_pattern_event_is_latched_per_symbol_pattern_day(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [_pattern_row(last=100.0)], "rest": []}, NOW)
    log.apply("Watchlist", {"rows": [_pattern_row(last=101.0)], "rest": []}, NOW + timedelta(seconds=20))
    log.apply("Watchlist", {"rows": [_pattern_row(state="fading", last=99.0)], "rest": []},
              NOW + timedelta(seconds=40))
    p = {"rows": [_pattern_row(last=102.0)], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(minutes=2))
    assert len(_events_file(tmp_path)) == 1
    assert p["rows"][0]["gradeFresh"]["firstToday"]["patterns"]["steady"]["price"] == 100.0
    # A later Explosive on the same symbol is its own event.
    log.apply("Watchlist", {"rows": [_pattern_row("explosive", last=104.0)], "rest": []},
              NOW + timedelta(minutes=3))
    events = _events_file(tmp_path)
    assert [(e["kind"], e["pattern"]) for e in events] == [("pattern", "steady"), ("pattern", "explosive")]


@pytest.mark.parametrize("state", ["holding", "fading", "extended", "quiet", None])
def test_pattern_without_building_logs_nothing(tmp_path, state):
    log = GradeLog(tmp_path)
    p = {"rows": [_pattern_row(state=state)], "rest": []}
    log.apply("Watchlist", p, NOW)
    assert not (tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-21.json").exists()
    assert p["rows"][0]["gradeFresh"]["firstToday"]["patterns"] == {"explosive": None, "steady": None}


def test_no_pattern_or_no_m5_logs_nothing(tmp_path):
    none_pattern = _pattern_row(pattern=None)
    no_m5 = _pattern_row(symbol="ZZZ")
    no_m5["m5"] = None
    GradeLog(tmp_path).apply("Watchlist", {"rows": [none_pattern, no_m5], "rest": []}, NOW)
    assert not (tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-21.json").exists()


def test_pattern_latch_survives_restart(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [_pattern_row(last=100.0)], "rest": []}, NOW)
    p = {"rows": [_pattern_row(last=110.0)], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, NOW + timedelta(minutes=9))
    assert len(_events_file(tmp_path)) == 1
    assert p["rows"][0]["gradeFresh"]["firstToday"]["patterns"]["steady"]["price"] == 100.0
    # A pattern event does not fill the letter latch.
    assert p["rows"][0]["gradeFresh"]["firstToday"]["A+"] is None


def test_lettered_row_with_pattern_logs_a_letter_event_and_a_pattern_event(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [_pattern_row("explosive", letter="A")], "rest": []}, NOW)
    events = _events_file(tmp_path)
    assert [(e["kind"], e["letter"], e["pattern"]) for e in events] == [
        ("letter", "A", "explosive"), ("pattern", "A", "explosive")]
    # Restarted: neither event is logged again.
    GradeLog(tmp_path).apply("Watchlist", {"rows": [_pattern_row("explosive", letter="A")], "rest": []},
                             NOW + timedelta(minutes=1))
    assert len(_events_file(tmp_path)) == 2


def test_pattern_event_with_a_letter_does_not_fill_the_letter_latch_on_reload(tmp_path):
    # Only a pattern event on disk (e.g. its letter event lived on another board file).
    pattern_event = _event("AAA", "A+", "09:31", pattern="explosive", momentum="building")
    pattern_event["kind"] = "pattern"
    _write_events(tmp_path, [pattern_event])
    p = {"rows": [row(letter="A+", last=120.0)], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, NOW)
    fresh = p["rows"][0]["gradeFresh"]["firstToday"]
    assert fresh["A+"]["price"] == 120.0          # the letter event fires now
    assert fresh["patterns"]["explosive"]["price"] == 100.0


def test_old_event_file_without_kind_reloads_as_letter_events(tmp_path):
    legacy = _event("AAA", "A+", "09:31")
    legacy["pattern"], legacy["momentum"] = "steady", "building"
    _write_events(tmp_path, [legacy])
    p = {"rows": [row(letter="A+", last=120.0)], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, NOW)
    fresh = p["rows"][0]["gradeFresh"]["firstToday"]
    assert fresh["A+"]["price"] == 100.0
    assert fresh["patterns"]["steady"] is None


def test_nightly_scores_a_legacy_event_file_with_no_kind(tmp_path):
    # A pending event written before "kind" existed (on disk, not in memory) --
    # _needs_outcome must still recognize it as a letter event to score.
    legacy = _event("AAA", "A+")
    assert "kind" not in legacy
    _write_events(tmp_path, [legacy])
    assert GradeLog(tmp_path).nightly(AFTER_CLOSE, lambda s, days=1: {"AAA": _session_bars()}) is True
    (event,) = _read_events(tmp_path)
    assert event["outcome"]["close"] == 1.0


def test_nightly_scores_pattern_events(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [_pattern_row(symbol="AAA", last=100.0)], "rest": []},
              datetime(2026, 9, 21, 10, 0, tzinfo=ET))
    assert log.nightly(AFTER_CLOSE, lambda s, days=1: {"AAA": _session_bars()}) is True
    (event,) = _read_events(tmp_path)
    assert event["kind"] == "pattern" and event["outcome"]["close"] == 1.0
    assert event["outcome"]["p5"] == pytest.approx(0.1)
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["patternEvents"]["steady"]["none"]["count"] == 1
    assert record["letters"]["A+"]["count"] == 0


def test_pattern_events_hit_the_retry_cap_like_letters(tmp_path):
    pattern_event = _event("BBB", letter=None, pattern="explosive", momentum="building")
    pattern_event["kind"] = "pattern"
    _write_events(tmp_path, [pattern_event])
    log = GradeLog(tmp_path)
    t = AFTER_CLOSE
    for _ in range(grade_log_module.MAX_FETCH_ATTEMPTS - 1):
        assert log.nightly(t, lambda s, days=1: {}) is False
        t += timedelta(minutes=grade_log_module.NIGHTLY_RETRY_MINUTES + 1)
    assert log.nightly(t, lambda s, days=1: {}) is True
    assert _read_events(tmp_path)[0]["outcome"] == {"unavailable": True}
    record = json.loads((tmp_path / "momx_grade_record.json").read_text(encoding="utf-8"))
    assert record["patternUnscored"]["explosive"]["none"] == 1
    assert record["unscored"] == {"A+": 0, "A": 0, "B": 0}


def test_build_record_puts_pattern_events_under_pattern_events_only(tmp_path):
    scored = {"p5": 1.0, "p15": 1.0, "p30": 1.0, "p60": 1.0, "close": 1.0, "maxFav": 2.0, "maxAdv": -1.0}
    letter_event = _event("AAA", "A+", pattern="steady", momentum="building")
    letter_event["kind"] = "letter"
    letter_event["outcome"] = dict(scored)
    legacy_letter = _event("CCC", "B")            # written before "kind" existed
    legacy_letter["outcome"] = dict(scored, close=-1.0)
    nflx = _event("NFLX", None, pattern="steady", momentum="building")
    nflx["kind"] = "pattern"
    nflx["outcome"] = dict(scored, close=2.0)
    with_letter = _event("AAA", "A+", pattern="steady", momentum="building")
    with_letter["kind"] = "pattern"
    with_letter["outcome"] = dict(scored)
    _write_events(tmp_path, [letter_event, legacy_letter, nflx, with_letter])
    # The same pattern event on Mag7 is one trade, not two.
    _write_events(tmp_path, [dict(nflx, board="Mag7")], board="Mag7")

    record = grade_log_module.build_record(tmp_path)
    assert record["letters"]["A+"]["count"] == 1
    assert record["letters"]["B"]["count"] == 1
    assert sum(g["count"] for g in record["byPattern"]["A+"].values()) == 1
    assert sum(g["count"] for g in record["byMomentum"]["A+"].values()) == 1
    assert record["byFresh"]["A+"]["notFresh"]["count"] == 1
    steady = record["patternEvents"]["steady"]
    assert set(steady) == {"A+", "A", "B", "none"}
    assert steady["none"]["count"] == 1 and steady["none"]["avgToClose"] == 2.0
    assert steady["A+"]["count"] == 1
    assert record["patternEvents"]["explosive"]["none"] == {
        "count": 0, "pctHigher15": None, "pctHigher60": None, "pctHigherClose": None,
        "avgToClose": None, "medianToClose": None, "avgMaxFav": None, "avgMaxAdv": None}
    assert record["patternUnscored"] == {"explosive": {"A+": 0, "A": 0, "B": 0, "none": 0},
                                         "steady": {"A+": 0, "A": 0, "B": 0, "none": 0}}
    assert record["unscored"] == {"A+": 0, "A": 0, "B": 0}


def test_a_day_with_only_pattern_events_is_not_a_letter_day(tmp_path):
    # Controller ruling (fix round 1): "days" (letter events) and
    # "patternDays" (pattern events) are tracked separately so a pattern-only
    # day never makes record_response think there is recorded LETTER data.
    nflx = _event("NFLX", None, pattern="steady", momentum="building")
    nflx["kind"] = "pattern"
    nflx["outcome"] = {"p5": 1.0, "p15": 1.0, "p30": 1.0, "p60": 1.0, "close": 2.0,
                       "maxFav": 2.0, "maxAdv": -1.0}
    _write_events(tmp_path, [nflx])
    record = grade_log_module.build_record(tmp_path)
    assert record["days"] == []
    assert record["patternDays"] == [DAY]


def test_record_response_uses_backtest_letters_when_only_patterns_are_recorded(tmp_path):
    backtest = {"days": ["2026-09-01", "2026-09-02"], "source": "History archive",
                "letters": {"A+": {"count": 3, "pctHigherClose": 44.0, "avgToClose": 0.1,
                                   "medianToClose": -0.1}}}
    (tmp_path / "momx_grade_record_backtest.json").write_text(json.dumps(backtest), encoding="utf-8")
    nflx = _event("NFLX", None, pattern="steady", momentum="building")
    nflx["kind"] = "pattern"
    nflx["outcome"] = {"p5": 1.0, "p15": 1.0, "p30": 1.0, "p60": 1.0, "close": 2.0,
                       "maxFav": 2.0, "maxAdv": -1.0}
    _write_events(tmp_path, [nflx])
    (tmp_path / "momx_grade_record.json").write_text(
        json.dumps(grade_log_module.build_record(tmp_path)), encoding="utf-8")
    got = record_response(tmp_path)
    # Letters: no recorded letter day yet, so the back-test's numbers stand.
    assert got["source"] == "back-test from History archive (2 days)"
    assert got["letters"]["A+"]["count"] == 3
    # Patterns: still come from the recorded file.
    assert got["patternSource"] == "recorded"
    assert got["patternDays"] == [DAY]
    assert got["patternEvents"]["steady"]["none"]["count"] == 1
    assert got["patternEvents"]["steady"]["none"]["avgToClose"] == 2.0


def test_record_response_uses_recorded_letters_when_letter_events_exist(tmp_path):
    lettered = _event("AAA", "A+")
    lettered["outcome"] = {"p5": 1.0, "p15": 1.0, "p30": 1.0, "p60": 1.0, "close": 1.0,
                           "maxFav": 1.0, "maxAdv": -1.0}
    _write_events(tmp_path, [lettered])
    (tmp_path / "momx_grade_record.json").write_text(
        json.dumps(grade_log_module.build_record(tmp_path)), encoding="utf-8")
    got = record_response(tmp_path)
    assert got["source"] == "recorded"
    assert got["days"] == [DAY]
    assert got["letters"]["A+"]["count"] == 1
    # No pattern events recorded.
    assert got["patternSource"] is None
    assert "patternEvents" not in got or got.get("patternDays") in (None, [])


def test_record_response_with_neither_letters_nor_patterns_falls_back_cleanly(tmp_path):
    got = record_response(tmp_path)
    assert got == {"source": None, "patternSource": None, "direction": "bull"}


# ------------------------------------- final review: session latch, tape volume

def _events_on(directory, day, board="Watchlist"):
    path = directory / "momx_grade_events" / board / f"{day}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["events"]


def test_in_session_is_weekday_0930_to_155959_et():
    in_session = grade_log_module._in_session
    assert in_session(datetime(2026, 9, 21, 9, 30, 0, tzinfo=ET))
    assert in_session(datetime(2026, 9, 21, 15, 59, 59, tzinfo=ET))
    assert not in_session(datetime(2026, 9, 21, 9, 29, 59, tzinfo=ET))
    assert not in_session(datetime(2026, 9, 21, 16, 0, 0, tzinfo=ET))
    assert not in_session(datetime(2026, 9, 19, 10, 0, tzinfo=ET))      # Saturday
    # A UTC clock is read in ET: 13:35 UTC = 09:35 EDT.
    assert in_session(datetime(2026, 9, 21, 13, 35, tzinfo=ZoneInfo("UTC")))


def test_lettered_row_after_midnight_logs_nothing_then_first_a_plus_at_0933(tmp_path):
    log = GradeLog(tmp_path)
    night = {"rows": [row(letter="A+", last=90.0)], "rest": []}
    log.apply("Watchlist", night, datetime(2026, 9, 21, 0, 5, tzinfo=ET))
    fresh = night["rows"][0]["gradeFresh"]
    assert fresh["ageMinutes"] is None
    assert fresh["firstToday"]["A+"] is None
    assert _events_on(tmp_path, "2026-09-21") == []

    open_ = datetime(2026, 9, 21, 9, 33, tzinfo=ET)
    p = {"rows": [row(letter="A+", last=100.5)], "rest": []}
    log.apply("Watchlist", p, open_)
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["firstToday"]["A+"] == {"at": open_.isoformat(), "price": 100.5}
    assert fresh["ageMinutes"] == 0
    (event,) = _events_on(tmp_path, "2026-09-21")
    assert (event["kind"], event["letter"], event["at"], event["price"]) == (
        "letter", "A+", open_.isoformat(), 100.5)


def test_after_the_close_nothing_new_latches_and_age_is_none(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row(letter="A")], "rest": []}, NOW)
    p = {"rows": [row(letter="A+", last=130.0)], "rest": []}
    log.apply("Watchlist", p, datetime(2026, 9, 21, 16, 0, tzinfo=ET))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["ageMinutes"] is None
    assert fresh["firstToday"]["A+"] is None           # not updated after the close
    assert fresh["firstToday"]["A"]["at"] == NOW.isoformat()  # the session latch stays
    assert [e["letter"] for e in _events_on(tmp_path, "2026-09-21")] == ["A"]


def test_saturday_1000_logs_nothing(tmp_path):
    p = {"rows": [row(letter="A+"), _pattern_row()], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, datetime(2026, 9, 19, 10, 0, tzinfo=ET))
    assert _events_on(tmp_path, "2026-09-19") == []
    assert p["rows"][0]["gradeFresh"]["ageMinutes"] is None
    assert p["rows"][1]["gradeFresh"]["firstToday"]["patterns"] == {"explosive": None, "steady": None}


def test_pattern_events_follow_the_session_rule(tmp_path):
    log = GradeLog(tmp_path)
    early = {"rows": [_pattern_row(last=95.0)], "rest": []}
    log.apply("Watchlist", early, datetime(2026, 9, 21, 7, 0, tzinfo=ET))
    assert _events_on(tmp_path, "2026-09-21") == []
    assert early["rows"][0]["gradeFresh"]["firstToday"]["patterns"]["steady"] is None
    at = datetime(2026, 9, 21, 9, 31, tzinfo=ET)
    p = {"rows": [_pattern_row(last=100.0)], "rest": []}
    log.apply("Watchlist", p, at)
    (event,) = _events_on(tmp_path, "2026-09-21")
    assert (event["kind"], event["at"], event["price"]) == ("pattern", at.isoformat(), 100.0)
    assert p["rows"][0]["gradeFresh"]["firstToday"]["patterns"]["steady"] == {
        "at": at.isoformat(), "price": 100.0}


def test_freshness_timeline_and_icons_work_outside_the_session(tmp_path):
    log = GradeLog(tmp_path)
    night = datetime(2026, 9, 21, 2, 0, tzinfo=ET)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, night)
    p = {"rows": [row("green")], "rest": []}
    log.apply("Watchlist", p, night + timedelta(seconds=30))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["icons"] == ["SKIT"]
    assert [item["what"] for item in fresh["timeline"]] == ["SKIT 4h bg green"]


def test_live_timeline_is_capped_to_the_last_ten_items(tmp_path):
    log = GradeLog(tmp_path)
    for i in range(30):
        log.apply("Watchlist", {"rows": [row("green" if i % 2 else "black")], "rest": []},
                  NOW + timedelta(seconds=20 * i))
    # i = 1, 3, ..., 29 each cross into green: 15 observed items.
    p = {"rows": [row("green")], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(seconds=20 * 30))
    timeline = p["rows"][0]["gradeFresh"]["timeline"]
    assert len(timeline) == 10
    assert timeline[-1]["at"] == (NOW + timedelta(seconds=20 * 29)).isoformat()
    assert timeline[0]["at"] == (NOW + timedelta(seconds=20 * 11)).isoformat()


def _tape_files(directory, board="Watchlist"):
    return sorted((directory / "momx_grade_tape").glob(f"{board}/*.jsonl"))


@pytest.mark.parametrize("at", [
    datetime(2026, 9, 19, 10, 0, tzinfo=ET),      # Saturday
    datetime(2026, 9, 20, 12, 0, tzinfo=ET),      # Sunday
    datetime(2026, 9, 21, 3, 59, 59, tzinfo=ET),  # before 04:00
    datetime(2026, 9, 21, 20, 0, tzinfo=ET),      # 20:00 onwards
])
def test_no_tape_outside_weekday_0400_to_1959(tmp_path, at):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row(letter="A")], "rest": []}, at)
    assert _tape_files(tmp_path) == []


@pytest.mark.parametrize("at", [
    datetime(2026, 9, 21, 4, 0, tzinfo=ET),
    datetime(2026, 9, 21, 19, 59, 59, tzinfo=ET),
])
def test_tape_is_written_inside_weekday_0400_to_1959(tmp_path, at):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row()], "rest": []}, at)
    assert [p.name for p in _tape_files(tmp_path)] == ["2026-09-21.jsonl"]


def test_only_watchlist_and_mag7_write_a_tape(tmp_path):
    log = GradeLog(tmp_path)
    for board in ("Movers", "Semis", "Daily_news", "Watchlist", "Mag7"):
        log.apply(board, {"rows": [row()], "rest": []}, NOW)
    written = sorted(p.parent.name for p in (tmp_path / "momx_grade_tape").glob("*/*.jsonl"))
    assert written == ["Mag7", "Watchlist"]


def _tape_line(directory, board, at, symbol="AAA", letter="B"):
    path = directory / "momx_grade_tape" / board / f"{at.date().isoformat()}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"t": at.isoformat(), "last": 1.0, "letter": letter, "checks": None, "reasons": [],
             "m5state": None, "chart": None, "trigger": None, "pattern": None}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"t": at.isoformat(), "board": board, "n": 1,
                                 "entries": {symbol: entry}}, separators=(",", ":")) + "\n")
    return path


def test_tape_response_skips_dates_without_files_and_caps_the_lookback(tmp_path):
    # Fri 09-18 and Mon 09-21 recorded; Sat/Sun have no file.
    for d in (18, 21):
        _tape_line(tmp_path, "Watchlist", datetime(2026, 9, d, 10, 0, tzinfo=ET))
    got = tape_response(tmp_path, "AAA", days=2, today="2026-09-22")
    assert [e["t"][:10] for e in got["entries"]] == ["2026-09-18", "2026-09-21"]
    # 14 calendar days back is still read; 15 is past the cap.
    _tape_line(tmp_path, "Mag7", datetime(2026, 9, 8, 10, 0, tzinfo=ET))
    _tape_line(tmp_path, "Mag7", datetime(2026, 9, 7, 10, 0, tzinfo=ET))
    got = tape_response(tmp_path, "AAA", days=5, today="2026-09-22")
    assert [e["t"][:10] for e in got["entries"]] == ["2026-09-08", "2026-09-18", "2026-09-21"]


def test_tape_response_caches_past_days_by_mtime_and_size(tmp_path, monkeypatch):
    grade_log_module._TAPE_CACHE.clear()
    _tape_line(tmp_path, "Watchlist", datetime(2026, 9, 18, 10, 0, tzinfo=ET))
    _tape_line(tmp_path, "Watchlist", datetime(2026, 9, 21, 10, 0, tzinfo=ET))
    reads = []
    real = grade_log_module._tape_symbol_entries

    def spy(path, symbol):
        reads.append((path.name, symbol))
        return real(path, symbol)

    monkeypatch.setattr(grade_log_module, "_tape_symbol_entries", spy)
    first = tape_response(tmp_path, "AAA", days=2)       # fixture: today = 2026-09-21
    assert reads == [("2026-09-21.jsonl", "AAA"), ("2026-09-18.jsonl", "AAA")]
    reads.clear()
    assert tape_response(tmp_path, "AAA", days=2) == first
    assert reads == [("2026-09-21.jsonl", "AAA")]       # past day served from the cache
    reads.clear()
    # A different symbol is its own cache entry.
    tape_response(tmp_path, "ZZZ", days=2)
    assert ("2026-09-18.jsonl", "ZZZ") in reads
    reads.clear()
    # The past file changes (a late append): its mtime/size change, so it is re-read.
    _tape_line(tmp_path, "Watchlist", datetime(2026, 9, 18, 10, 5, tzinfo=ET))
    got = tape_response(tmp_path, "AAA", days=2)
    assert ("2026-09-18.jsonl", "AAA") in reads
    assert len(got["entries"]) == 3


def test_tape_cache_is_a_bounded_lru(tmp_path, monkeypatch):
    grade_log_module._TAPE_CACHE.clear()
    monkeypatch.setattr(grade_log_module, "TAPE_CACHE_SIZE", 2)
    _tape_line(tmp_path, "Watchlist", datetime(2026, 9, 18, 10, 0, tzinfo=ET))
    for symbol in ("AAA", "BBB", "CCC"):
        tape_response(tmp_path, symbol, days=1, today="2026-09-21")
    assert len(grade_log_module._TAPE_CACHE) == 2
    assert grade_log_module.TAPE_CACHE_SIZE == 2
    grade_log_module._TAPE_CACHE.clear()


# ----------------------------------------------------------------------
# BEAR recorder (spec 2026-09-24)
# ----------------------------------------------------------------------


def _bars5(prices, start=None, highs=None, lows=None):
    start = start or datetime(2026, 9, 21, 9, 30, tzinfo=ET)
    out = []
    for i, p in enumerate(prices):
        t = start + timedelta(minutes=5 * i)
        out.append({"time": int(t.timestamp()), "open": p, "high": (highs[i] if highs else p),
                    "low": (lows[i] if lows else p), "close": p, "volume": 100})
    return out


def test_bear_outcome_is_scored_in_the_trades_favour():
    event = {"at": datetime(2026, 9, 21, 9, 32, tzinfo=ET).isoformat(), "price": 100.0}
    prices = [100.0] + [100.0 - 0.1 * i for i in range(1, 78)]        # drifts to ~92.3 by 15:55
    bars = _bars5(prices, highs=[p + 1.0 for p in prices], lows=[p - 2.0 for p in prices])
    bull = grade_log_module.outcome_for(event, bars)
    bear = grade_log_module.outcome_for(event, bars, direction="bear")
    assert bull["close"] < 0 < bear["close"] and bear["close"] == -bull["close"]
    assert bear["maxFav"] == -bull["maxAdv"] and bear["maxAdv"] == -bull["maxFav"]
    assert bear["p15"] == -bull["p15"] and bear["p60"] == -bull["p60"]
    assert bear["maxFav"] > 0 > bear["maxAdv"]


def test_bear_log_reads_bear_colours_for_freshness_and_squeeze(tmp_path):
    base = {"symbol": "AAA", "last": 100.0, "skittles": {"4h": {"bg": "black", "fg": "magenta"}},
            "rvol": {"5m": {"bg": "black", "value": 0.5}}, "sqz": {"4h": {"bg": "orange"}}, "news": None,
            "m5": {"chart": "above", "state": "quiet", "trigger": 99.0},
            "grade": {"letter": None, "checks": {}, "reasons": []}}
    lit = dict(base, skittles={"4h": {"bg": "magenta", "fg": "black"}},
               rvol={"5m": {"bg": "magenta", "value": 3.2}}, sqz={"4h": {"bg": "magenta"}})
    log = GradeLog(tmp_path, direction="bear")
    log.apply("Watchlist", {"rows": [dict(base)], "rest": []}, NOW)
    p = {"rows": [dict(lit)], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(seconds=30))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["icons"] == ["SKIT", "RVOL", "SQZ"]
    assert log.direction == "bear"
    bull = GradeLog(tmp_path / "b")
    bull.apply("Watchlist", {"rows": [dict(base)], "rest": []}, NOW)
    q = {"rows": [dict(lit)], "rest": []}
    bull.apply("Watchlist", q, NOW + timedelta(seconds=30))
    assert q["rows"][0]["gradeFresh"]["icons"] == []
    assert bull.direction == "bull"


def test_bear_breakdown_confirmed_is_the_m5_timeline_line(tmp_path):
    base = {"symbol": "AAA", "last": 100.0, "skittles": {}, "rvol": {}, "sqz": {}, "news": None,
            "m5": {"chart": "above", "state": "quiet", "trigger": 99.0},
            "grade": {"letter": None, "checks": {}, "reasons": []}}
    log = GradeLog(tmp_path, direction="bear")
    log.apply("Watchlist", {"rows": [dict(base)], "rest": []}, NOW)
    p = {"rows": [dict(base, m5={"chart": "breakout_confirmed", "state": "building", "trigger": 99.0})], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(seconds=30))
    assert p["rows"][0]["gradeFresh"]["timeline"][-1]["what"] == "5m breakdown confirmed"


def test_bear_record_and_response_name_their_direction(tmp_path):
    assert grade_log_module.record_response(tmp_path, "bear")["direction"] == "bear"
    assert grade_log_module.record_response(tmp_path)["direction"] == "bull"
    record = grade_log_module.build_record(tmp_path, "bear")
    assert record["direction"] == "bear"
    assert set(record["byAdx"]) == {"bearCross", "bearCrossRising", "none"}
    assert set(grade_log_module.build_record(tmp_path)["byAdx"]) == {"bullCross", "bullCrossRising", "none"}
