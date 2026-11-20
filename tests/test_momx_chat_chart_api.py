"""Integration: the chat's chart tools over HTTP and on real tapes (2026-11-11).

Real api_server route, real momx/chat_ai, real child process, on frozen chart
tapes in a scratch folder. AI is faked. The chat may only read: these tests fail
if a question starts a chart build, a refresh, or adds to any cache.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import subprocess
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import api_server
from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chart_levels, chat_ai, chat_chart, chat_limits, chat_state, chat_tools
from test_momx_chart_levels import ALL_TIMEFRAMES, FIXTURES, make_tape  # noqa: F401 - ALL_TIMEFRAMES drives the link check

OWNER = "owner-chart@example.com"
VIEWER = "viewer-chart@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/bot-chat"
ET = ZoneInfo("America/New_York")
BUILD_METHODS = ("oi_finder_chart_payload", "oi_finder_chart_history_status", "_touch_chart_tail", "_start_oi_finder_chart_refresh",
                 "_load_oi_finder_chart_disk_payload", "_submit_chart_label_heal")


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "no AI in tests"})
    # The real child runs in these tests; on a PC busy with other work it can take a while.
    monkeypatch.setattr(chat_chart, "CHILD_TIMEOUT_SECONDS", 240)
    monkeypatch.setattr(chat_chart, "CHILD_WAIT_SECONDS", 240)
    chat_limits.LIMITS.reset()
    chat_ai.clear_plans()
    chat_chart.reset()
    yield
    chat_limits.LIMITS.reset()
    chat_chart.reset()
    chat_state.unbind()


@pytest.fixture
def charts(tmp_path, monkeypatch):
    """Frozen tapes as the app's saved charts; an empty in-memory cache; every build path booby-trapped."""
    folder = tmp_path / "oi_chart_cache"
    folder.mkdir()
    for name in ("SPY", "AAPL"):
        (folder / f"{name}.json.gz").write_bytes((FIXTURES / f"{name}.json.gz").read_bytes())
    memory: dict = {}
    monkeypatch.setattr(api_server.STATE, "oi_finder_chart_disk_cache_dir", folder)
    monkeypatch.setattr(api_server.STATE, "oi_finder_chart_cache", memory)
    calls: list[str] = []
    for method in BUILD_METHODS:
        def trap(*args, _name=method, **kwargs):
            calls.append(_name)
            raise AssertionError(f"the chat called {_name}")
        # On the class, not the instance: undoing an instance patch leaves the original bound method stuck on STATE,
        # which hid the class-level spy in test_momx_chat_market_api for the rest of the session (2026-11-11).
        monkeypatch.setattr(api_server.DashboardState, method, trap)
    base = tmp_path / "grade"
    base.mkdir()
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    return {"folder": folder, "memory": memory, "calls": calls}


@pytest.fixture
def route_charts(charts, monkeypatch):
    """For the tests through HTTP. The test client gives up after 20 s, and a child on a PC this busy
    can take longer, so these do the same sum in-process; the child has its own tests below."""
    def in_process(timeframe, source, data):
        if source == "-":
            return json.loads(json.dumps(chart_levels.analyze(json.loads(data), timeframe)))
        payload = json.loads(gzip.decompress(Path(source).read_bytes()))
        return json.loads(json.dumps(chart_levels.analyze(payload, timeframe, from_disk=True)))
    monkeypatch.setattr(chat_chart, "_run_child", in_process)
    return charts


@lru_cache(maxsize=None)
def _expected(symbol: str, timeframe: str) -> dict:
    payload = json.loads(gzip.decompress((FIXTURES / f"{symbol}.json.gz").read_bytes()))
    return chart_levels.analyze(payload, timeframe, from_disk=True)


def _ctx(scope="scanner", **over) -> dict:
    return {"scope": scope, "account": VIEWER, "pageTicker": None, "pageName": None, "ticker": None, "day": None, "side": None,
            "now": datetime.now(ET), **over}


def _write(folder: Path, symbol: str, payload: dict) -> None:
    (folder / f"{symbol}.json.gz").write_bytes(gzip.compress(json.dumps(payload).encode()))


def _text(body: dict) -> str:
    return " ".join(body.get("lines") or [])


# ---------------------------------------------------------------- the trader's own words, through the route
def test_what_level_we_have_in_chart_answers_the_ticker_on_screen(route_charts):
    charts = route_charts
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "What level we have in chart",
                                        "context": {"pageTicker": "AAPL", "pageName": "Charts & OI", "ticker": "ZETA"}}, headers=ADMIN)
    assert status == 200
    assert "AAPL chart levels, 5m" in _text(body) and "ZETA" not in _text(body)
    assert any(link.get("ticker") == "AAPL" and link.get("view") == "Charts & OI" for link in body["links"])
    assert charts["calls"] == [] and charts["memory"] == {}


def test_a_regular_user_gets_levels_with_the_time_and_defaults(route_charts):
    charts = route_charts
    expected = _expected("SPY", "5m")["levels"]
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "chart levels spy"}, headers=REGULAR)
    assert status == 200
    text = _text(body)
    assert "SPY chart levels, 5m" in text and f"EMA 9 {expected['ema9']:.2f}" in text
    assert "vwap" not in text.lower() and "vwap" not in json.dumps(body["table"]).lower()     # 2026-11-11 "Don't use vwap"
    assert "chart defaults 9/21/50 and SMA 200" in text
    assert " ET" in text and body["asOf"].startswith("as of ")
    assert body["table"]["kind"] == "rows" and len(body["table"]["rows"]) <= 40
    assert not any(word in json.dumps(body).lower() for word in ("ganesh", "paper", "scorecard"))
    assert charts["calls"] == [] and charts["memory"] == {}


def test_what_is_spy_vwap_on_5m(route_charts):
    charts = route_charts
    with running_api() as api:
        _, body = api.post(ROUTE, {"question": "what is spy vwap on 5m"}, headers=ADMIN)
    # 2026-11-11, his words "Don't use vwap": one line and a chart link, nothing worked out.
    assert body["lines"] == [chat_chart.VWAP_LINE] and any(link["view"] == "Charts & OI" and link.get("ticker") == "SPY" for link in body["links"])
    assert charts["calls"] == []


def test_aapl_daily_pivots(route_charts):
    charts = route_charts
    expected = _expected("AAPL", "D")["levels"]["pivots"]["DAY"]
    with running_api() as api:
        _, body = api.post(ROUTE, {"question": "aapl daily pivots"}, headers=ADMIN)
    assert f"PP {expected['PP']:.2f}" in _text(body) and "AAPL chart levels, D" in _text(body)
    assert any(link.get("timeframe") == "D" for link in body["links"])


def test_signals_on_30m_flag_what_is_forming(route_charts):
    charts = route_charts
    expected = _expected("AAPL", "30m")["signals"]
    with running_api() as api:
        _, body = api.post(ROUTE, {"question": "aapl signals on 30m"}, headers=REGULAR)
    assert "AAPL chart signals, 30m" in _text(body)
    states = {row["state"] for row in body["table"]["rows"]}
    assert ("Forming" in states) == any(row["forming"] for row in [*expected["mtf"], *expected["higher"]])
    assert "ganesh" not in json.dumps(body).lower()


def test_any_call4h_reads_the_four_hour_chart(route_charts):
    charts = route_charts
    with running_api() as api:
        _, body = api.post(ROUTE, {"question": "any CALL4H on spy"}, headers=ADMIN)
    assert "SPY chart signals, 4h" in _text(body)
    assert charts["calls"] == []


def test_levels_and_signals_together_share_one_computation(route_charts, monkeypatch):
    runs: list[tuple] = []
    real = chat_chart._run_child
    monkeypatch.setattr(chat_chart, "_run_child", lambda tf, src, data: (runs.append((tf, src)), real(tf, src, data))[1])
    with running_api() as api:
        _, body = api.post(ROUTE, {"question": "why I don't fetch the chart levels and signals also",
                                   "context": {"pageTicker": "AAPL"}}, headers=ADMIN)
    assert "AAPL chart levels" in _text(body) and "AAPL chart signals" in _text(body)
    assert len(runs) == 1


# ---------------------------------------------------------------- the tools on real tapes
@pytest.mark.parametrize("symbol", ["SPY", "AAPL"])
def test_the_child_matches_the_port(charts, symbol):
    """All 11 timeframes are proven in test_momx_chart_levels; this is the path through the child."""
    chat_state.bind(state=api_server.STATE)
    for timeframe in ("5m", "4h", "D"):
        out = chat_tools.run("chart_levels", {"ticker": symbol, "timeframe": timeframe}, _ctx())
        expected = _expected(symbol, timeframe)["levels"]
        assert out["facts"]["timeframe"] == timeframe and out["facts"]["ema21"] == expected["ema21"], timeframe
        assert "vwap" not in out["facts"], timeframe
        assert out["facts"]["pivots"] == expected["pivots"], timeframe
        assert out["facts"]["newestCandle"] == expected["lastTime"], timeframe
    assert charts["calls"] == [] and charts["memory"] == {}


def test_the_copy_in_memory_comes_first_and_stays_untouched(charts):
    chat_state.bind(state=api_server.STATE)
    tape = make_tape(date(2026, 10, 15), 11)
    entry = {"cached_at": 1.0, "payload": tape, "history_ready": True}
    charts["memory"]["AAPL"] = entry
    before = json.dumps(tape, sort_keys=True)
    out = chat_tools.run("chart_levels", {"ticker": "AAPL", "timeframe": "5m"}, _ctx())
    assert out["facts"]["close"] == chart_levels.analyze(tape, "5m")["levels"]["lastClose"]
    assert json.dumps(tape, sort_keys=True) == before and set(entry) == {"cached_at", "payload", "history_ready"}
    assert list(charts["memory"]) == ["AAPL"]


def test_a_bare_fast_start_copy_falls_back_to_the_saved_file(charts):
    chat_state.bind(state=api_server.STATE)
    tiny = {"bars": make_tape(date(2026, 10, 15), 12)["bars"][:30]}
    charts["memory"]["AAPL"] = {"cached_at": 1.0, "payload": tiny, "history_ready": False}
    out = chat_tools.run("chart_levels", {"ticker": "AAPL", "timeframe": "5m"}, _ctx())
    assert out["facts"]["close"] == _expected("AAPL", "5m")["levels"]["lastClose"]


def test_no_saved_chart_says_so_with_a_link_and_starts_nothing(charts):
    chat_state.bind(state=api_server.STATE)
    out = chat_tools.run("chart_levels", {"ticker": "ZZZZ"}, _ctx())
    assert out["facts"] == {} and "don't have a saved chart for ZZZZ" in out["lines"][0]
    assert out["links"][0]["ticker"] == "ZZZZ" and out["links"][0]["view"] == "Charts & OI"
    assert charts["calls"] == [] and charts["memory"] == {}


def test_a_saved_chart_older_than_the_apps_limit_is_not_used(charts):
    chat_state.bind(state=api_server.STATE)
    path = charts["folder"] / "AAPL.json.gz"
    old = time.time() - chat_chart.DISK_MAX_AGE_SECONDS - 60
    os.utime(path, (old, old))
    assert chat_tools.run("chart_levels", {"ticker": "AAPL"}, _ctx())["facts"] == {}


def test_no_ticker_at_all_asks_for_one(charts):
    chat_state.bind(state=api_server.STATE)
    out = chat_chart.chart_levels_tool({}, _ctx())
    assert "Which ticker" in out["lines"][0] and out["facts"] == {} and out["links"][0]["view"] == "Charts & OI"


@pytest.mark.parametrize("flags", [{"mtfSignalsPending": True}, {"studiesPending": True}, {"history": False}])
def test_signals_say_pending(charts, flags):
    chat_state.bind(state=api_server.STATE)
    tape = make_tape(date(2026, 10, 15), 13)
    tape.update({"mtfSignalMode": chart_levels.MTF_ENGINE_MODE, "mtfSourceSessions": 53})
    if flags.get("history") is False:
        tape["ganeshHigherTimeframeSignals"]["historyReady"] = False
    else:
        tape.update(flags)
    _write(charts["folder"], "NVDA", tape)
    out = chat_tools.run("chart_signals", {"ticker": "NVDA", "timeframe": "5m"}, _ctx())
    assert out["facts"]["pending"] is True and any("pending" in line for line in out["lines"])
    assert not any(line.startswith("No signals") for line in out["lines"])


def test_signals_say_forming(charts):
    chat_state.bind(state=api_server.STATE)
    tape = make_tape(date(2026, 10, 15), 14)
    tape.update({"mtfSignalMode": chart_levels.MTF_ENGINE_MODE, "mtfSourceSessions": 53})
    _write(charts["folder"], "NVDA", tape)
    out = chat_tools.run("chart_signals", {"ticker": "NVDA", "timeframe": "5m"}, _ctx())
    assert out["facts"]["pending"] is False and out["facts"]["forming"] >= 1
    assert any("CALL4H (forming" in line for line in out["lines"])


def test_one_label_is_filtered_and_picks_its_timeframe(charts):
    chat_state.bind(state=api_server.STATE)
    tape = make_tape(date(2026, 10, 15), 15)
    tape.update({"mtfSignalMode": chart_levels.MTF_ENGINE_MODE, "mtfSourceSessions": 53})
    tape["mtfSignalsByTimeframe"]["240"] = tape["mtfSignals"]
    _write(charts["folder"], "NVDA", tape)
    out = chat_chart.chart_signals_tool({"ticker": "NVDA", "label": "CALL4H"}, _ctx())
    assert out["facts"]["timeframe"] == "4h" and {row["signal"] for row in out["table"]["rows"]} == {"CALL4H"}


# ---------------------------------------------------------------- whose ticker
@pytest.mark.parametrize("args, ctx, expected", [
    ({}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "AAPL"),
    ({"question": "What level we have in chart"}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "AAPL"),
    ({"question": "levels on this"}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "AAPL"),
    ({"ticker": "ZETA", "question": "what is here"}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "AAPL"),
    ({"ticker": "ZETA", "question": "zeta chart levels"}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "ZETA"),
    ({"ticker": "NVDA"}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "NVDA"),
    ({"question": "and the levels"}, {"pageTicker": "AAPL", "ticker": "ZETA"}, "ZETA"),
    ({"question": "and the levels"}, {"pageTicker": "AAPL"}, "AAPL"),
    ({}, {"ticker": "ZETA"}, "ZETA"),
    ({}, {}, None),
    ({"ticker": "../../etc"}, {}, None),
])
def test_which_ticker(args, ctx, expected):
    assert chat_chart.resolve_ticker(args, ctx) == expected


@pytest.mark.parametrize("args, label, expected", [
    ({}, None, "5m"), ({"timeframe": "1h"}, None, "1h"), ({"timeframe": "240"}, None, "4h"), ({"timeframe": "daily"}, None, "D"),
    ({"question": "spy on 15m"}, None, "15m"), ({"question": "aapl daily pivots"}, None, "D"), ({"question": "2 hour"}, None, "2h"),
    ({}, "CALL4H", "4h"), ({}, "P15", "15m"), ({}, "CALLD", "D"), ({}, "PUTM", "M"), ({"timeframe": "30m"}, "CALL4H", "30m"),
])
def test_which_timeframe(args, label, expected):
    assert chat_chart.resolve_timeframe(args, label) == expected


# ---------------------------------------------------------------- staying out of the app's way
def test_the_same_question_again_does_not_recompute(charts, monkeypatch):
    chat_state.bind(state=api_server.STATE)
    runs: list[str] = []
    real = chat_chart._run_child
    monkeypatch.setattr(chat_chart, "_run_child", lambda tf, src, data: (runs.append(tf), real(tf, src, data))[1])
    first = chat_tools.run("chart_levels", {"ticker": "AAPL", "timeframe": "15m"}, _ctx())
    second = chat_tools.run("chart_signals", {"ticker": "AAPL", "timeframe": "15m"}, _ctx())
    assert first["facts"] and second["facts"] and runs == ["15m"]


def test_only_the_asked_timeframe_is_computed(charts, monkeypatch):
    chat_state.bind(state=api_server.STATE)
    runs: list[str] = []
    real = chat_chart._run_child
    monkeypatch.setattr(chat_chart, "_run_child", lambda tf, src, data: (runs.append(tf), real(tf, src, data))[1])
    chat_tools.run("chart_levels", {"ticker": "AAPL", "timeframe": "1h"}, _ctx())
    assert runs == ["1h"]


def test_a_child_that_runs_out_of_time_says_busy_and_rests(charts, monkeypatch):
    chat_state.bind(state=api_server.STATE)

    class Slow:
        returncode = None
        def communicate(self, data=None, timeout=None):
            if timeout is not None:
                raise subprocess.TimeoutExpired("chart", timeout)
            return b"", b""
        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(chat_chart.subprocess, "Popen", lambda *a, **k: Slow())
    out = chat_tools.run("chart_levels", {"ticker": "AAPL"}, _ctx())
    assert out["facts"] == {} and "busy" in out["lines"][0] and out["links"][0]["ticker"] == "AAPL"
    # 2026-11-11: only that ticker and timeframe rest; one slow tape no longer rests every user's chart answers.
    assert time.monotonic() < chat_chart._rest[("AAPL", "5m")] and ("SPY", "5m") not in chat_chart._rest


def test_the_child_runs_at_normal_priority_with_no_window():
    """Below normal it starved behind the desktop apps: SPY 5m took 29.3 s against 9.6 s (2026-11-11)."""
    assert chat_chart._child_flags() == (0x08000000 if os.name == "nt" else 0)


def test_a_slow_child_carries_on_and_the_next_question_reads_its_answer(charts, monkeypatch):
    """The question gives up after CHILD_TIMEOUT_SECONDS; the child does not, and asking again reads what it found."""
    chat_state.bind(state=api_server.STATE)
    import threading
    gate = threading.Event()
    real = chat_chart._run_child

    def slow(tf, src, data):
        gate.wait(10)
        return real(tf, src, data)
    monkeypatch.setattr(chat_chart, "_run_child", slow)
    monkeypatch.setattr(chat_chart, "CHILD_TIMEOUT_SECONDS", 0.2)
    first = chat_tools.run("chart_levels", {"ticker": "AAPL", "timeframe": "1h"}, _ctx())
    assert first["facts"] == {} and "busy" in first["lines"][0] and ("AAPL", "1h") not in chat_chart._rest
    monkeypatch.setattr(chat_chart, "_run_child", lambda *a: pytest.fail("a second child for the same tape"))
    gate.set()
    monkeypatch.setattr(chat_chart, "CHILD_TIMEOUT_SECONDS", 240)
    second = chat_tools.run("chart_levels", {"ticker": "AAPL", "timeframe": "1h"}, _ctx())
    assert second["facts"]["close"] == _expected("AAPL", "1h")["levels"]["lastClose"]


def test_a_tape_in_memory_never_holds_the_interpreter_for_long(charts, monkeypatch):
    """2026-11-11: one json.dumps of a 30k-bar memory tape held the interpreter 0.4-1.4 s on the serving thread."""
    import threading
    chat_state.bind(state=api_server.STATE)
    tape = make_tape(date(2026, 10, 15), 21)
    bars = tape["bars"]
    while len(bars) < 30000:
        bars = bars + [{**bar, "time": bar["time"] + 86400 * 7 * (len(bars) // len(tape["bars"]))} for bar in tape["bars"]]
    tape["bars"] = sorted(bars[:30000], key=lambda bar: bar["time"])
    tape["studyBars"] = tape["bars"][:]
    tape["fineStudyBars"] = tape["bars"][:]
    charts["memory"]["AAPL"] = {"cached_at": 1.0, "payload": tape, "history_ready": True}
    sent = []
    monkeypatch.setattr(chat_chart, "_run_child", lambda tf, src, data: sent.append(data) or {"levels": {}, "signals": {}, "tape": {}})
    gaps, stop = [], threading.Event()

    def tick():
        last = time.perf_counter()
        while not stop.is_set():
            time.sleep(0.002)
            now = time.perf_counter()
            gaps.append(now - last)
            last = now
    ticker = threading.Thread(target=tick, daemon=True)
    ticker.start()
    time.sleep(0.05)
    try:
        chat_chart.compute("AAPL", "5m", _ctx())
    finally:
        stop.set()
        ticker.join()
    assert sent and json.loads(sent[0])["bars"] == json.loads(json.dumps(tape["bars"]))       # the same tape, every bar
    assert max(gaps) < 0.05, max(gaps)


def test_the_pieces_make_the_same_json_as_one_dumps():
    tape = make_tape(date(2026, 10, 15), 22)
    tape.update({"mtfSignalsByTimeframe": {"30": tape["mtfSignals"], "240": []}, "mtfSignalsPending": False, "studiesPending": True})
    for timeframe in ("5m", "4h", "D"):
        assert json.loads(chat_chart._tape_bytes(tape, timeframe)) == {key: tape[key] for key in chat_chart._TAPE_KEYS if key in tape
                                                                       and key not in ({"studyBars", "fineStudyBars"} if timeframe == "D" and tape.get("dailyBars")
                                                                                       else {"fineStudyBars"} if timeframe == "4h" and tape.get("studyBars") else set())}


def test_latest_bar_peeks_at_memory_and_reads_no_file(charts):
    chat_state.bind(state=api_server.STATE)
    assert chat_chart.latest_bar("AAPL") is None                       # a saved file alone is not read for a price
    tape = make_tape(date(2026, 10, 15), 23)
    charts["memory"]["AAPL"] = {"cached_at": 1.0, "payload": tape, "history_ready": True}
    assert chat_chart.latest_bar("AAPL") == (tape["bars"][-1]["time"], float(tape["bars"][-1]["close"]))


def test_a_regular_user_is_offered_the_chart_tools_and_no_scope_gets_nothing(charts):
    for scope in ("owner", "scanner"):
        assert {"chart_levels", "chart_signals"} <= set(chat_tools.registry(scope))
    out = chat_chart.chart_levels_tool({"ticker": "AAPL"}, _ctx(scope="visitor"))
    assert out["facts"] == {} and out["links"] == []


def test_links_pass_the_servers_link_check(charts):
    chat_state.bind(state=api_server.STATE)
    for timeframe in ALL_TIMEFRAMES:
        link = chat_chart._link("AAPL", timeframe)
        assert chat_tools.chat_pages.clean_link(link, "scanner") is not None, timeframe


def test_the_home_card_shows_levels_and_signals_for_the_ticker_on_screen(route_charts):
    charts = route_charts
    with running_api() as api:
        status, body = api.post(ROUTE, {"home": True, "context": {"pageTicker": "AAPL", "pageName": "Charts & OI"}}, headers=ADMIN)
    assert status == 200
    card = next(card for card in body["home"]["cards"] if card["kind"] == "ticker")
    assert "AAPL chart levels, 5m" in " ".join(card["lines"])
    assert charts["calls"] == [] and charts["memory"] == {}


# ---------------------------------------------------------------- support and resistance, 2026-11-20
def test_what_is_support_and_resistance_says_which_levels_are_which(route_charts):
    """His "What is support and resistance for nvda?" got every level but never said which were support and which
    resistance ("Why chatbot not showing support and resistance?"). Now: what the words mean, then the nearest
    levels below the close (support) and above it (resistance), each named, from the same chart levels."""
    charts = route_charts
    levels = _expected("SPY", "5m")["levels"]
    below, above = chat_chart.support_resistance(levels)
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "What is support and resistance for spy?"}, headers=REGULAR)
    assert status == 200
    lines = body["lines"]
    close = f"{levels['lastClose']:.2f}"
    assert lines[1].startswith(f"Support = price levels BELOW SPY's {close} that traders watch as a floor."), lines
    assert "not a forecast" in lines[1]
    assert lines[2].startswith(f"Nearest resistance above {close}: ") or lines[2] == f"No resistance level above {close} on this chart."
    assert lines[3].startswith(f"Nearest support below {close}: ") or lines[3] == f"No support level below {close} on this chart."
    for name, value in above:
        assert f"{value:.2f} ({name})" in lines[2] and value > levels["lastClose"]
    for name, value in below:
        assert f"{value:.2f} ({name})" in lines[3] and value < levels["lastClose"]
    assert "chart defaults 9/21/50 and SMA 200" in _text(body)            # the full level list still follows
    assert charts["calls"] == []


def test_plain_levels_question_has_no_support_resistance_block(route_charts):
    with running_api() as api:
        _, body = api.post(ROUTE, {"question": "chart levels spy"}, headers=REGULAR)
    assert not any(line.startswith("Support = ") for line in body["lines"])


def test_support_resistance_sorts_by_distance_and_skips_the_short_emas():
    levels = {"lastClose": 143.74, "ema9": 143.79, "ema21": 143.94, "ema50": 144.32, "sma200": 148.19,
              "session": {"pm": {"high": 154.67, "low": 150.58}, "day": {"high": 158.13, "low": 143.0}, "week": {"high": 163.86, "low": 143.0}},
              "previous": {"DAY": {"high": 161.26, "low": 149.88}},
              "pivots": {"DAY": {"R1": 158.97, "PP": 154.42, "S1": 147.59}, "WEEK": {"R1": 176.81, "PP": 167.79, "S1": 152.49}}}
    below, above = chat_chart.support_resistance(levels)
    assert above == [("Day pivot S1", 147.59), ("SMA 200", 148.19), ("Previous day low", 149.88)]
    assert below == [("Today's low", 143.0)]                                 # the week's low is the same price: listed once
    assert chat_chart.sr_lines("NVDA", levels)[1:] == [
        "Nearest resistance above 143.74: 147.59 (Day pivot S1); 148.19 (SMA 200); 149.88 (Previous day low).",
        "Nearest support below 143.74: 143.00 (Today's low)."]
    assert chat_chart.support_resistance({"lastClose": None}) == ([], [])


# ---------------------------------------------------------------- "if 135 breaks below", 2026-11-20
def test_if_a_price_breaks_says_the_next_levels_beyond_it(route_charts):
    """His "If 135 broke below then" (NVDA on screen) got the help list. Now: the chart's next levels beyond that price,
    where the stock is now, and that it is not a forecast."""
    charts = route_charts
    levels = _expected("SPY", "5m")["levels"]
    price = round(levels["lastClose"] * 0.97, 2)
    below, _ = chat_chart.support_resistance(levels, around=price)
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": f"If {price} broke below then",
                                        "context": {"pageTicker": "SPY", "pageName": "Charts & OI"}}, headers=REGULAR)
    assert status == 200 and body["intent"] == "chart_levels", body
    lines = body["lines"]
    assert lines[1].startswith(f"If SPY falls below {price:.2f}, the next levels below it on the chart are: "), lines
    for name, value in below:
        assert f"{value:.2f} ({name})" in lines[1] and value < price
    assert lines[2].startswith(f"SPY is at {levels['lastClose']:.2f} now, ") and f"above {price:.2f}" in lines[2]
    assert "not a forecast" in lines[3]
    assert charts["calls"] == []


def test_break_lines_above_and_with_no_level_left():
    levels = {"lastClose": 136.31, "sma200": 144.12, "session": {"day": {"high": 144.69, "low": 134.82}},
              "previous": {"DAY": {"high": 158.13, "low": 143.0}},
              "pivots": {"DAY": {"R1": 153.52, "S2": 133.13}, "WEEK": {"S1": 152.49}},
              "mtfMa": [{"key": "MONTH-ema3", "value": 131.49, "label": "50eMo"}]}
    assert chat_chart.break_lines("NVDA", levels, 135, "below") == [
        "If NVDA falls below 135.00, the next levels below it on the chart are: 134.82 (Today's low); 133.13 (Day pivot S2); 131.49 (Monthly 50 EMA).",
        "NVDA is at 136.31 now, 1.0% above 135.00.",
        "These are price levels traders watch, not a forecast: a level can break or hold."]
    assert chat_chart.break_lines("NVDA", levels, 150, "above")[0] == (
        "If NVDA rises above 150.00, the next levels above it on the chart are: 152.49 (Week pivot S1); 153.52 (Day pivot R1); 158.13 (Previous day high).")
    assert chat_chart.break_lines("NVDA", levels, 500, "above")[0] == "The chart has no level above 500.00."
