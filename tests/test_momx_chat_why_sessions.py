"""The why-answer split by session (2026-11-11, the trader at 23:05 ET: "I asked why nvda down after market hours
it's not giving information"). On real data at 23:00 ET the answer said "NVDA +2.87% today at 161.31 ... NVDA is up
today, not down" while NVDA was about -8% after hours.

Unit tests on fixed minute tapes (evening after a green day, morning premarket, regular hours, the weekend, a bad
print), the real /api/bot-chat route over saved chart files (the child's sum run in-process), and a read-only check
of the real saved NVDA and SPY charts. Google News and the AI are faked; nothing leaves the PC.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from momx import chart_levels, chat_ai, chat_chart, chat_facts, chat_limits, chat_state, chat_why

ET = ZoneInfo("America/New_York")
EVENING = datetime(2026, 11, 11, 23, 0, tzinfo=ET)        # Wednesday night, after a green day
MORNING = datetime(2026, 11, 12, 8, 15, tzinfo=ET)        # Thursday premarket
MIDDAY = datetime(2026, 11, 12, 11, 0, tzinfo=ET)         # Thursday regular hours
SATURDAY = datetime(2026, 11, 14, 12, 0, tzinfo=ET)
KNOWN = frozenset({"NVDA", "SPY", "QQQ", "AMD", "AVGO", "SMH"})


def bar(day, hour, minute, close, open_=None, high=None, low=None):
    stamp = int(datetime.fromisoformat(day).replace(hour=hour, minute=minute, tzinfo=ET).timestamp())
    open_ = close if open_ is None else open_
    return {"time": stamp, "open": open_, "high": max(open_, close) if high is None else high,
            "low": min(open_, close) if low is None else low, "close": close, "volume": 100}


def session_bars(day, open_price, close, after=None, overnight=None, pre=None):
    """A thin but complete day: premarket, 09:30, 15:58, the 15:59 close, then after-hours and overnight prints."""
    out = []
    for hour, minute, price in pre or []:
        out.append(bar(day, hour, minute, price))
    out += [bar(day, 9, 30, open_price), bar(day, 15, 58, close), bar(day, 15, 59, close)]
    for hour, minute, price in after or []:
        out.append(bar(day, hour, minute, price))
    for hour, minute, price in overnight or []:
        out.append(bar(day, hour, minute, price))
    return out


def tapes():
    """NVDA: +3.00% on Wed 11-11 (168.78 -> 173.85), then -8.66% after hours (158.79 at 19:58) and 161.12 overnight;
    Thursday premarket 157.00 at 08:10. SPY and QQQ a little red after hours, AMD red with NVDA."""
    nvda = (session_bars("2026-11-10", 170.0, 168.78)
            + session_bars("2026-11-11", 169.0, 173.85, after=[(16, 1, 172.0), (19, 58, 158.79)], overnight=[(22, 50, 161.12)])
            + [bar("2026-11-12", 4, 5, 160.0), bar("2026-11-12", 8, 10, 157.0), bar("2026-11-12", 9, 30, 156.0), bar("2026-11-12", 10, 59, 155.0)])
    spy = (session_bars("2026-11-10", 748.0, 750.0)
           + session_bars("2026-11-11", 751.0, 760.0, after=[(19, 58, 756.0)], overnight=[(22, 48, 757.0)])
           + [bar("2026-11-12", 8, 10, 758.0), bar("2026-11-12", 10, 59, 759.0)])
    qqq = (session_bars("2026-11-10", 670.0, 675.0)
           + session_bars("2026-11-11", 676.0, 690.0, after=[(19, 58, 683.1)])
           + [bar("2026-11-12", 8, 10, 684.0), bar("2026-11-12", 10, 59, 685.0)])
    amd = (session_bars("2026-11-10", 230.0, 240.0)
           + session_bars("2026-11-11", 241.0, 250.0, after=[(19, 55, 240.0)])
           + [bar("2026-11-12", 8, 0, 241.0)])
    return {"NVDA": nvda, "SPY": spy, "QQQ": qqq, "AMD": amd}


def board_file(base, name, rows, at):
    folder = base / "momx_board_cache"
    folder.mkdir(exist_ok=True)
    doc = {"list": name, "generatedAt": at.isoformat(), "rows": [
        {"symbol": s, "scanPass": True, "last": price, "pctChange": pct, "marketDataAt": at.isoformat(),
         "highLow": {"value": 0.5, "degree": 0.5}, "scannerInputs": {"highestLevel": 2, "setupTypes": []}} for s, pct, price in rows]}
    (folder / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")


class Store:
    def latest(self, symbols):
        return {"feed": [], "storeError": None}


def hold(bars_by_symbol):
    """The app's in-memory chart tapes, as api_server holds them."""
    state = type("S", (), {})()
    state.oi_finder_chart_cache = {s: {"payload": {"bars": b}, "history_ready": True} for s, b in bars_by_symbol.items()}
    state._momx_news = lambda: Store()
    chat_state.bind(state=state)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.delenv("MOMX_BOARD_CACHE_DIR", raising=False)
    monkeypatch.delenv("AGX_MOMX_GRADE_DIR", raising=False)
    # The scanner board's day change at 23:00 is the regular session's and its price the overnight print: the live bug.
    board_file(tmp_path, "Mag7", [("NVDA", 2.87, 161.31), ("SPY", 1.3, 757.0), ("QQQ", 2.2, 684.0)], EVENING - timedelta(minutes=5))
    board_file(tmp_path, "Watchlist", [("AMD", 4.0, 241.0)], EVENING - timedelta(minutes=5))
    chat_facts.reset()
    chat_why.reset()
    chat_chart.reset()
    chat_ai._REST.update(until=0.0, why="")
    chat_ai._REST_SCANNER.update(until=0.0, why="")
    chat_limits.LIMITS.reset()
    monkeypatch.setattr(chat_why, "_fetch", lambda url, timeout: b"<rss/>")
    hold(tapes())
    yield tmp_path
    chat_state.unbind()
    chat_why.reset()
    chat_chart.reset()


def ctx(base, now, scope="scanner", ask=None):
    return {"scope": scope, "account": "v@x", "pageTicker": None, "pageName": "Charts & OI", "ticker": None, "day": None, "side": None,
            "now": now, "artifacts": base, "ask": ask, "aiAllowed": None}


def why(base, now, question, ticker="NVDA", **extra):
    args = {"ticker": ticker, "question": question} if ticker else {"market": True, "question": question}
    return chat_why.why_move(args, ctx(base, now, **extra))


# ---------------------------------------------------------------- which session the words mean
@pytest.mark.parametrize("question,session", [
    ("why is nvda down after hours", "after"), ("why did nvda drop after the close", "after"), ("why is nvda down tonight", "after"),
    ("why nvda down ah", "after"), ("why is nvda down in extended hours", "extended"), ("why is nvda down after-hours", "after"),
    ("why did nvda drop premarket", "pre"), ("why is nvda down pre-market", "pre"), ("why did nvda drop this morning before the open", "pre"),
    ("why is nvda down overnight", "overnight"), ("why nvda down", None), ("why is nvda down today", None),
    ("why did nvda drop in the regular session", "regular"),
])
def test_the_session_a_question_names(question, session):
    assert chat_facts.asked_session(question) == session


@pytest.mark.parametrize("question", ["why nvda down", "why is nvda down after hours", "why did nvda drop premarket",
                                      "why is nvda down tonight", "why nvda dropped after the close", "why is nvda down overnight"])
def test_session_words_still_route_to_the_why_answer(tmp_path, question):
    plan = chat_ai.sure_calls(question, ctx(tmp_path, EVENING), KNOWN)
    assert plan["calls"] == [{"tool": "why_move", "args": {"ticker": "NVDA", "question": question}}], plan


# ---------------------------------------------------------------- the evening after a green day
def test_evening_shows_the_regular_session_and_after_hours_and_never_calls_a_red_evening_up(world):
    out = why(world, EVENING, "why nvda down")
    text = "\n".join(out["lines"])
    assert out["lines"][0] == "Regular session: NVDA +3.00% to 173.85 (4:00 PM ET)."
    assert out["lines"][1] == "After hours: NVDA -8.66% to 158.79 since the 4:00 PM close (7:58 PM ET)."
    assert out["lines"][2] == "Overnight: NVDA -7.32% to 161.12 since the 4:00 PM close (10:50 PM ET)."
    assert "not down" not in text and "up today" not in text
    assert "+2.87%" not in text and "161.31" not in text                     # the scanner board's day change and price
    assert out["facts"]["session"] == "after" and out["facts"]["movePct"] == -8.66 and out["asOf"] == "as of 7:58 PM ET"


def test_the_market_and_the_sector_are_in_the_same_session(world):
    text = "\n".join(why(world, EVENING, "why is nvda down after hours")["lines"])
    assert "Market after hours: SPY -0.53% (7:58 PM ET), QQQ -1.00% (7:58 PM ET)." in text, text
    assert "Sector (Semiconductors) after hours: AMD -4.00% (7:55 PM ET)." in text, text
    assert "SPY +1.33%" not in text.split("Regular session")[-1].split("\n", 1)[-1]              # no regular-session % next to it
    assert "NVDA moved mostly on its own (SPY -0.53%, Semiconductors average -4.00%)." in text or "Its sector moved with it" in text


def test_a_question_that_names_a_session_is_judged_on_that_session(world):
    assert "NVDA is down after hours, not up." in why(world, EVENING, "why is nvda up after hours")["lines"]
    assert "NVDA is down overnight, not up." in why(world, EVENING, "why is nvda rallying overnight")["lines"]
    assert "NVDA is up in the regular session, not down." in why(world, EVENING, "why did nvda drop in the regular session")["lines"]
    plain = why(world, EVENING, "why is nvda up")                    # no session named: green day, red evening -> no call either way
    assert not any("not up" in line or "not down" in line for line in plain["lines"]), plain["lines"]


def test_the_ai_and_the_owner_summary_read_the_session_lines(world, monkeypatch):
    from momx import chat_summary
    sent = []

    def ask(system, user, schema):
        sent.append(user)
        return {"ok": True, "provider": "fake", "data": {"reasons": [], "explained": False}}
    monkeypatch.setattr(chat_why, "saved_headlines", lambda t, now, hours=48: ([{"title": "Chipmaker guides lower after the bell", "source": "Wire",
                                                                                 "at": EVENING - timedelta(hours=4), "url": None, "via": "app"}], None))   # 19:00, inside after hours
    monkeypatch.setattr(chat_ai, "enabled", lambda: True)
    # Changed on purpose (2026-11-14): the AI reads the facts only for the owner's write-up (a regular user's reasons
    # are code-built), as FACTS.
    out = why(world, EVENING, "why is nvda down after hours", ask=ask, scope="owner")
    chat_why.collect(out["more"], "v@x", "owner", wait=30)
    facts = json.loads(sent[-1])["FACTS"]
    assert "After hours: NVDA -8.66% to 158.79 since the 4:00 PM close (7:58 PM ET)." in facts and "Regular session: NVDA +3.00% to 173.85 (4:00 PM ET)." in facts
    lines = chat_summary._fact_lines("NVDA", ctx(world, EVENING, "owner"), {"question": "why is nvda down after hours"})
    assert "After hours: NVDA -8.66% to 158.79 since the 4:00 PM close (7:58 PM ET)." in lines
    assert not any("+2.87%" in line for line in lines)


def test_a_headline_pointing_the_other_way_is_measured_against_the_session_move(world, monkeypatch):
    monkeypatch.setattr(chat_why, "saved_headlines", lambda t, now, hours=48: ([{"title": "Nvidia shares rally on new chip", "source": "Wire",
                                                                                 "at": EVENING - timedelta(hours=1), "url": None, "via": "app"}], None))
    out = why(world, EVENING, "why is nvda down after hours")
    assert any(line.endswith("points the other way from the move after hours.") for line in out["lines"]), out["lines"]


def test_the_market_question_after_hours(world):
    out = why(world, EVENING, "why is the market down after hours", ticker=None)
    assert out["lines"][0] == "Regular session: SPY +1.33% to 760.00 (4:00 PM ET); QQQ +2.22% to 690.00 (4:00 PM ET)."
    assert out["lines"][1] == ("After hours: SPY -0.53% to 756.00 since the 4:00 PM close (7:58 PM ET); "
                               "QQQ -1.00% to 683.10 since the 4:00 PM close (7:58 PM ET).")


# ---------------------------------------------------------------- the morning before the open
def test_morning_shows_the_previous_regular_session_and_premarket_since_that_close(world):
    out = why(world, MORNING, "why did nvda drop")
    assert out["lines"][0] == "Regular session: NVDA +3.00% to 173.85 (Wed Nov 11, 4:00 PM ET)."
    assert out["lines"][1] == "Premarket: NVDA -9.69% to 157.00 since the Wed Nov 11 4:00 PM close (8:10 AM ET)."
    assert not any(line.startswith("After hours:") for line in out["lines"])
    assert "Market in premarket: SPY -0.26% (8:10 AM ET), QQQ -0.87% (8:10 AM ET)." in out["lines"]
    assert "NVDA is down in premarket, not up." in why(world, MORNING, "why is nvda up premarket")["lines"]
    assert "NVDA is down in premarket, not up." in why(world, MORNING, "why is nvda up this morning before the open")["lines"]
    asked_ah = why(world, MORNING, "why is nvda down after hours")["lines"]
    assert "After hours: NVDA -8.66% to 158.79 since the Wed Nov 11 4:00 PM close (Wed Nov 11, 7:58 PM ET)." in asked_ah


# ---------------------------------------------------------------- regular hours
def test_regular_hours_keep_todays_move_and_add_only_a_named_session(world, monkeypatch):
    board_file(world, "Mag7", [("NVDA", -3.2, 170.0), ("SPY", 0.4, 760.0), ("QQQ", 0.6, 690.0)], MIDDAY - timedelta(minutes=2))
    board_file(world, "Watchlist", [("AMD", -2.5, 230.0)], MIDDAY - timedelta(minutes=2))
    chat_facts.reset()
    real = chat_chart.session_tape
    calls = []
    import threading
    here = threading.current_thread()
    # Only this question's fast part counts (2026-11-14: another test's background card may still be reading tapes).
    monkeypatch.setattr(chat_chart, "session_tape", lambda *a, **k: (threading.current_thread() is here and calls.append(a[0])) or real(*a, **k))
    out = why(world, MIDDAY, "why is nvda down")
    # Changed on purpose (2026-11-14 review, "Morning also gave me similar answer"): during the session the saved chart's
    # today-so-far line leads (with the market in the same window); the scanner's own move line stays below. No session
    # split is read.
    assert out["lines"][0].startswith("Today so far (9:30 AM-11:00 AM ET, Thu Nov 12): NVDA ") and not calls
    assert out["lines"][1].startswith("Market today so far: SPY ")
    assert "NVDA -3.20% today at 170.00 (scanner board, 10:58 ET)." in out["lines"]
    assert not any(line.startswith(("Regular session:", "After hours:", "Premarket:")) for line in out["lines"])
    asked = why(world, MIDDAY, "why did nvda drop premarket")["lines"]
    assert asked[0] == "NVDA -3.20% today at 170.00 (scanner board, 10:58 ET)."
    assert asked[1] == "Premarket: NVDA -9.69% to 157.00 since the Wed Nov 11 4:00 PM close (8:10 AM ET)."
    # The correction reads the same today-so-far move as the lead line (2026-11-14 review).
    assert "NVDA is down today so far, not up." in why(world, MIDDAY, "why is nvda up")["lines"]


# ---------------------------------------------------------------- the weekend
def test_the_weekend_shows_fridays_regular_session_and_fridays_after_hours(world):
    fri = (session_bars("2026-11-12", 160.0, 150.0) + session_bars("2026-11-13", 151.0, 153.0, after=[(16, 30, 154.0), (19, 59, 155.53)]))
    spy = (session_bars("2026-11-12", 750.0, 750.0) + session_bars("2026-11-13", 751.0, 755.0, after=[(19, 59, 754.0)]))
    hold({"NVDA": fri, "SPY": spy})
    chat_chart.reset()
    out = why(world, SATURDAY, "why is nvda up")
    assert out["lines"][0] == "Regular session: NVDA +2.00% to 153.00 (Fri Nov 13, 4:00 PM ET)."
    assert out["lines"][1] == "After hours: NVDA +1.65% to 155.53 since the Fri Nov 13 4:00 PM close (Fri Nov 13, 7:59 PM ET)."
    assert "Market after hours: SPY -0.13% (Fri Nov 13, 7:59 PM ET)." in out["lines"]
    assert "NVDA is up in the regular session and after hours, not down." in why(world, SATURDAY, "why is nvda down")["lines"]


# ---------------------------------------------------------------- a bad print
def test_an_off_market_bad_print_is_not_the_after_hours_price(world):
    """SPY 2026-11-11: one $711.46 print among $756 prints (d0db99ff). It is the last after-hours minute here, with
    overnight minutes after it, so the chart's rule repairs it and the after-hours price stays near $756."""
    spy = session_bars("2026-11-10", 748.0, 750.0) + session_bars("2026-11-11", 751.0, 760.0)
    spy += [bar("2026-11-11", 19, m, 756.0 + (m % 3) * 0.05) for m in range(40, 59)]
    spy += [bar("2026-11-11", 19, 59, 711.46)]
    spy += [bar("2026-11-11", 20, m, 756.1) for m in range(0, 12)]
    found = {(p["window"], p["anchor"]): p for p in chart_levels.extended_prints(spy)}
    assert found[("after", "2026-11-11")]["price"] != 711.46 and abs(found[("after", "2026-11-11")]["price"] - 756.0) < 0.2
    import chart_bad_prints
    whole = chart_bad_prints.remove_extended_hours_bad_prints(chart_levels.normalize(spy))
    assert found[("after", "2026-11-11")]["price"] == next(b["close"] for b in whole if b["time"] == found[("after", "2026-11-11")]["time"])
    hold({"NVDA": tapes()["NVDA"], "SPY": spy, "QQQ": tapes()["QQQ"]})
    chat_chart.reset()
    text = "\n".join(why(world, EVENING, "why is spy down after hours", ticker="SPY")["lines"])
    assert "711.46" not in text and "-6.39%" not in text and "After hours: SPY -0.5" in text, text


# ---------------------------------------------------------------- nothing saved
def test_no_saved_chart_says_the_move_is_not_split_and_makes_no_up_down_call(world, monkeypatch):
    hold({})
    chat_chart.reset()
    monkeypatch.setattr(chat_chart, "_disk_folder", lambda ctx_: None)
    out = why(world, EVENING, "why nvda down")
    text = "\n".join(out["lines"])
    assert "NVDA +2.87% on the day, last 161.31 (scanner board, 10:55 PM ET)." in text
    assert "not split into the regular session and after hours" in text
    assert "not down" not in text and "today at" not in text


def test_a_session_with_no_prints_says_so(world):
    hold({"NVDA": session_bars("2026-11-10", 170.0, 168.78) + session_bars("2026-11-11", 169.0, 173.85), "SPY": tapes()["SPY"]})
    chat_chart.reset()
    out = why(world, datetime(2026, 11, 11, 16, 2, tzinfo=ET), "why is nvda down after hours")
    assert "After hours: no NVDA prints are saved since the 4:00 PM close." in out["lines"]
    assert not any("not down" in line for line in out["lines"])


def test_a_memory_tape_is_walked_once_per_tape(world, monkeypatch):
    calls = []
    real = chart_levels.session_closes
    monkeypatch.setattr(chart_levels, "session_closes", lambda payload: calls.append(1) or real(payload))
    for _ in range(3):
        chat_chart.session_tape("NVDA", {"artifacts": world})
    assert len(calls) == 1


# ---------------------------------------------------------------- the real route, over saved chart files
@pytest.fixture
def saved_charts(world, monkeypatch):
    from test_momx_chat_search_acceptance import _child_in_process
    chat_state.unbind()
    charts = world / "oi_chart_cache"
    charts.mkdir()
    for symbol, rows in tapes().items():
        (charts / f"{symbol}.json.gz").write_bytes(gzip.compress(json.dumps({"bars": rows}).encode()))
    monkeypatch.setattr(chat_chart, "_run_child", _child_in_process)
    monkeypatch.setattr(chat_chart, "_disk_folder", lambda ctx_: charts)
    # The route binds the app's own state; a chart an earlier test left in its memory must not answer for these files.
    monkeypatch.setattr(chat_chart, "_memory_tape", lambda symbol: None)
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(world))
    chat_chart.reset()
    return charts


@pytest.mark.parametrize("question,at,expected,never", [
    ("why nvda down", EVENING, ["Regular session: NVDA +3.00% to 173.85 (4:00 PM ET).",
                                "After hours: NVDA -8.66% to 158.79 since the 4:00 PM close (7:58 PM ET)."], ["not down", "+2.87%"]),
    ("why is nvda down after hours", EVENING, ["After hours: NVDA -8.66% to 158.79 since the 4:00 PM close (7:58 PM ET).",
                                               "Market after hours: SPY -0.53% (7:58 PM ET), QQQ -1.00% (7:58 PM ET)."], ["not down"]),
    ("why did nvda drop premarket", MORNING, ["Regular session: NVDA +3.00% to 173.85 (Wed Nov 11, 4:00 PM ET).",
                                              "Premarket: NVDA -9.69% to 157.00 since the Wed Nov 11 4:00 PM close (8:10 AM ET)."], ["not down"]),
])
def test_the_route_answers_by_session(saved_charts, monkeypatch, question, at, expected, never):
    from api_harness import RELAYED, running_api, seed_user
    from cf_access_auth import ACCESS_EMAIL_HEADER
    real_answer = chat_ai.answer
    monkeypatch.setattr(chat_ai, "answer", lambda q, a, now=None, **kw: real_answer(q, a, at, **kw))
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "the AI is faked off here"})
    seed_user("viewer-session@example.com")
    regular = {**RELAYED, ACCESS_EMAIL_HEADER: "viewer-session@example.com"}
    try:
        with running_api() as api:
            status, body = api.post("/api/bot-chat", {"question": question, "context": {"pageTicker": "SPY", "pageName": "Charts & OI"}}, headers=regular)
    finally:
        chat_chart.reset()
    assert status == 200 and body["intent"] == "why_move" and body["ticker"] == "NVDA", body
    for line in expected:
        assert line in body["lines"], body["lines"]
    for word in never:
        assert not any(word in line for line in body["lines"]), body["lines"]


# ---------------------------------------------------------------- the real saved charts, read only
REAL = Path(os.environ.get("AGX_REAL_ARTIFACTS", r"C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\artifacts"))


@pytest.mark.skipif(not (REAL / "oi_chart_cache" / "NVDA.json.gz").exists(), reason="the real saved charts are not on this PC")
@pytest.mark.parametrize("symbol", ["NVDA", "SPY"])
def test_real_saved_chart_split_by_session_read_only(symbol, monkeypatch, capsys):
    """Reads the real saved chart file (never writes) with the child's sum in this process; prints what it shows."""
    from test_momx_chat_search_acceptance import _child_in_process
    chat_state.unbind()
    chat_chart.reset()
    path = REAL / "oi_chart_cache" / f"{symbol}.json.gz"
    monkeypatch.setattr(chat_chart, "_run_child", _child_in_process)
    monkeypatch.setattr(chat_chart, "_disk_folder", lambda ctx_: REAL / "oi_chart_cache")
    monkeypatch.setattr(chat_chart, "DISK_MAX_AGE_SECONDS", 30 * 86400)
    now = datetime.now(ET)
    try:
        tape = chat_chart.session_tape(symbol, {"artifacts": REAL}, wait=60)
    finally:
        chat_chart.reset()
    view = chat_facts.pick_sessions(symbol, tape, now)
    assert view is not None and view["regular"]["price"] > 0
    lines = [chat_facts.session_line([view], window, now, missing=False) for window in chat_facts.WINDOWS]
    with capsys.disabled():
        print(f"\n[{symbol} at {now:%Y-%m-%d %H:%M ET}] close day {view['day']} vs {view['prevDay']}")
        for line in lines:
            if line:
                print("   ", line)
    for window in ("after", "overnight", "pre"):
        if view[window]:
            assert view[window]["at"] > view["closeAt"] and abs(view[window]["pct"]) < 50


LIVE_FIXTURE = Path(r"C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\tests\fixtures\spy_2026_11_11_bad_prints.json")


def _random_tape(seed):
    import random
    rng = random.Random(seed)
    price, out = 100.0, []
    start = int(datetime(2026, 11, 10, 4, 0, tzinfo=ET).timestamp())
    for minute in range(0, 2 * 1440, 1):
        price *= 1 + rng.gauss(0, 0.0008)
        close = price * (0.93 if rng.random() < 0.01 else 1.0)        # an occasional off-market print
        open_ = price * (1.08 if rng.random() < 0.005 else 1.0)
        out.append({"time": start + minute * 60, "open": open_, "high": max(open_, close), "low": min(open_, close), "close": close, "volume": 1})
    return out


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_the_ported_bad_print_rule_matches_the_charts_on_every_bar(seed):
    """chart_levels runs in a child with no app code on its path, so it carries a port of chart_bad_prints; same output."""
    import chart_bad_prints
    bars = chart_levels.normalize(_random_tape(seed))
    assert chart_levels.remove_extended_hours_bad_prints(bars) == chart_bad_prints.remove_extended_hours_bad_prints(bars)
    assert chart_levels.remove_extended_hours_bad_prints(bars) is not bars                # the tape does have bad prints


@pytest.mark.skipif(not LIVE_FIXTURE.exists(), reason="the 2026-11-11 SPY fixture (d0db99ff) is not on this PC")
def test_the_ported_rule_on_the_real_spy_bad_prints():
    import chart_bad_prints
    bars = chart_levels.normalize(json.loads(LIVE_FIXTURE.read_text(encoding="utf-8"))["bars"])
    mine = chart_levels.remove_extended_hours_bad_prints(bars)
    assert mine == chart_bad_prints.remove_extended_hours_bad_prints(bars)
    assert all(b["open"] > 750 and b["low"] > 750 for b in mine)                        # no $711.46 or $749.86 print left


# ---------------------------------------------------------------- a saved chart a session behind the market (2026-11-12)
def test_a_tape_one_session_behind_spy_is_not_judged(world, monkeypatch):
    """The real AXP case on a fixed tape: the ticker's chart stops at Tuesday's close (with Tuesday after hours green),
    SPY has Wednesday. At 23:00 Wed: no Tuesday sessions shown as current, no "up after hours, not down", no
    "points the other way" note; the line names the day the saved chart stops at."""
    nvda = session_bars("2026-11-09", 168.0, 168.0) + session_bars("2026-11-10", 169.0, 170.0, after=[(16, 30, 172.0), (19, 50, 173.0)])
    hold({"NVDA": nvda, "SPY": tapes()["SPY"], "QQQ": tapes()["QQQ"]})
    chat_chart.reset()
    monkeypatch.setattr(chat_why, "saved_headlines", lambda t, now, hours=48: ([{"title": "Nvidia shares rally on new chip", "source": "Wire",
                                                                                 "at": EVENING - timedelta(hours=1), "url": None, "via": "app"}], None))
    out = why(world, EVENING, "why is nvda down after hours")
    text = "\n".join(out["lines"])
    assert ("The saved NVDA chart stops at the Tue Nov 10 4:00 PM close (2026-11-10), behind the market's newest finished "
            "session on Wed Nov 11 (2026-11-11)") in text, text
    assert "not down" not in text and "not up" not in text and "points the other way" not in text, text
    assert not any(line.startswith(("Regular session:", "After hours:", "Overnight:", "Market after hours")) for line in out["lines"]), text
    assert out["facts"]["movePct"] is None and out["facts"]["session"] is None and out["facts"]["staleChartDay"] == "2026-11-10"
    assert out["asOf"] is None
    plain = "\n".join(why(world, EVENING, "why nvda up")["lines"])
    assert "stops at the Tue Nov 10" in plain and "not down" not in plain and "not up" not in plain


def test_with_no_spy_chart_the_calendar_is_the_newest_weekday_whose_close_has_passed(world):
    nvda = session_bars("2026-11-09", 168.0, 168.0) + session_bars("2026-11-10", 169.0, 170.0, after=[(16, 30, 172.0)])
    assert chat_facts.trading_day({}, datetime(2026, 11, 12, 0, 7, tzinfo=ET)) == "2026-11-11"
    assert chat_facts.trading_day({}, datetime(2026, 11, 11, 15, 59, tzinfo=ET)) == "2026-11-10"
    assert chat_facts.trading_day({}, SATURDAY) == "2026-11-13"
    assert chat_facts.trading_day({}, datetime(2026, 11, 27, 13, 1, tzinfo=ET)) == "2026-11-27"     # early close at 13:00
    hold({"NVDA": nvda})
    chat_chart.reset()
    text = "\n".join(why(world, datetime(2026, 11, 12, 0, 7, tzinfo=ET), "why is nvda down after hours")["lines"])
    assert "The saved NVDA chart stops at the Tue Nov 10 4:00 PM close (2026-11-10)" in text and "not down" not in text, text
    hold({"NVDA": tapes()["NVDA"]})                                    # a current chart with no SPY: judged as before
    chat_chart.reset()
    current = why(world, EVENING, "why is nvda up after hours")["lines"]
    assert "NVDA is down after hours, not up." in current and not any("stops at the" in line for line in current)


@pytest.mark.skipif(not (REAL / "oi_chart_cache" / "AXP.json.gz").exists(), reason="the real saved AXP chart is not on this PC")
def test_real_axp_chart_asked_two_sessions_later_read_only(world, monkeypatch):
    """The real AXP.json.gz (copied into the scratch folder; the real one is only read), asked at 00:07 ET two
    weekdays after its newest regular session, with no SPY chart: the calendar says a newer session has closed, so
    the answer names the stale day and makes no up/down call. The time comes from the file itself, because the live
    app re-saves it every day (the first version pinned the Tue 11-10 save and broke on 11-13)."""
    import shutil

    def _child_in_process(timeframe, source, data):                 # exactly what chart_levels._main runs, in this process
        payload = json.loads(data) if source == "-" else json.loads(gzip.decompress(Path(source).read_bytes()))
        return json.loads(json.dumps(chart_levels.child_answer(payload, timeframe, source != "-")))
    chat_state.unbind()
    charts = world / "oi_chart_cache"
    charts.mkdir()
    shutil.copyfile(REAL / "oi_chart_cache" / "AXP.json.gz", charts / "AXP.json.gz")
    # The live app may have saved the chart in the middle of a session (11-13 12:50 ET): the chat rightly will not
    # call that half day "the close". The scratch copy ends at the newest complete regular session, so the question
    # below is about a chart that stops at a 4:00 PM close, whatever the live file looks like that day.
    saved = json.loads(gzip.decompress((charts / "AXP.json.gz").read_bytes()))
    complete = [s for s in chart_levels.child_answer(saved, "sessions", True)["sessions"] if s.get("complete")]
    assert complete, "the real AXP chart has no complete regular session"
    cut = float(complete[-1]["time"])
    for key, value in list(saved.items()):
        if isinstance(value, list) and value and all(isinstance(b, dict) and "time" in b for b in value):
            saved[key] = [b for b in value if float(b["time"]) <= cut]
    (charts / "AXP.json.gz").write_bytes(gzip.compress(json.dumps(saved).encode()))
    monkeypatch.setattr(chat_chart, "_run_child", _child_in_process)
    monkeypatch.setattr(chat_chart, "_disk_folder", lambda ctx_: charts)
    monkeypatch.setattr(chat_chart, "DISK_MAX_AGE_SECONDS", 30 * 86400)
    chat_chart.reset()
    chat_chart.session_tape("AXP", {"artifacts": world}, wait=90)                      # worked out before the question

    def weekday_after(day, n):
        d = day
        while n:
            d += timedelta(days=1)
            if d.weekday() < 5 and d.isoformat() not in chart_levels.EARLY_CLOSE_DAYS:
                n -= 1
        return d
    probe = chat_facts.pick_sessions("AXP", chat_chart.session_tape("AXP", {"artifacts": world}, start=False),
                                     datetime.now(ET))
    assert probe is not None, "the real AXP chart has no regular session"
    last = date.fromisoformat(probe["day"])
    newer = weekday_after(last, 1)
    at = datetime.combine(weekday_after(last, 2), time(0, 7), tzinfo=ET)
    view = chat_facts.pick_sessions("AXP", chat_chart.session_tape("AXP", {"artifacts": world}, start=False), at)
    assert view is not None and view["day"] == probe["day"], view
    out = why(world, at, "why is axp down after hours", ticker="AXP")
    text = "\n".join(out["lines"])
    assert f"The saved AXP chart stops at the {chat_why._day_words(view['day'])} 4:00 PM close ({view['day']})" in text, text
    assert f"({newer.isoformat()})" in text and "not down" not in text and "AXP is up" not in text and "points the other way" not in text, text
    assert not any(line.startswith(("Regular session:", "After hours:")) for line in out["lines"]), text
    chat_chart.reset()


# ---------------------------------------------------------------- a bad print as the window's last bar (2026-11-12)
def test_a_bad_print_as_the_last_bar_is_not_the_session_price(world):
    """SPY flat 756 after hours, then a 711.00 print at 19:57 as the tape's last bar: fewer than five bars follow it,
    so the chart's rule leaves the candle alone, but the session price is the newest clean bar before it."""
    import chart_bad_prints
    spy = session_bars("2026-11-10", 748.0, 750.0) + session_bars("2026-11-11", 751.0, 760.0)
    spy += [bar("2026-11-11", 19, m, 756.0 + (m % 3) * 0.05) for m in range(30, 57)]
    spy += [bar("2026-11-11", 19, 57, 711.00)]
    bars = chart_levels.normalize(spy)
    candles = chart_levels.remove_extended_hours_bad_prints(bars)
    assert candles == chart_bad_prints.remove_extended_hours_bad_prints(bars) and candles[-1]["close"] == 711.00     # chart parity
    found = {(p["window"], p["anchor"]): p for p in chart_levels.extended_prints(spy)}
    after = found[("after", "2026-11-11")]
    assert after["price"] == 756.1 and after["time"] == bar("2026-11-11", 19, 56, 0)["time"], after
    hold({"NVDA": tapes()["NVDA"], "SPY": spy, "QQQ": tapes()["QQQ"]})
    chat_chart.reset()
    text = "\n".join(why(world, EVENING, "why is spy down after hours", ticker="SPY")["lines"])
    assert "711.00" not in text and "-6.52%" not in text and "After hours: SPY -0.51% to 756.10 since the 4:00 PM close (7:56 PM ET)." in text, text


def test_the_last_bar_check_keeps_a_clean_last_bar_and_a_confirmed_move():
    spy = session_bars("2026-11-10", 748.0, 750.0) + session_bars("2026-11-11", 751.0, 760.0)
    spy += [bar("2026-11-11", 19, m, 756.0) for m in range(30, 58)]
    found = {(p["window"], p["anchor"]): p for p in chart_levels.extended_prints(spy)}
    assert found[("after", "2026-11-11")]["price"] == 756.0                                # clean: unchanged
    moved = spy + [bar("2026-11-11", 19, 58, 740.0), bar("2026-11-11", 19, 59, 740.1)]      # two prints confirm each other
    found = {(p["window"], p["anchor"]): p for p in chart_levels.extended_prints(moved)}
    assert found[("after", "2026-11-11")]["price"] == 740.1


# ---------------------------------------------------------------- a premarket question after the close (2026-11-12)
def _premarket_tapes(day_before, day):
    nvda = session_bars(day_before, 168.0, 168.78) + session_bars(day, 172.0, 175.0, pre=[(4, 5, 170.0), (9, 29, 172.77)],
                                                                  after=[(19, 58, 176.0)])
    spy = session_bars(day_before, 750.0, 750.0) + session_bars(day, 752.0, 755.0, pre=[(9, 29, 753.0)], after=[(19, 58, 756.0)])
    qqq = session_bars(day_before, 680.0, 680.0) + session_bars(day, 681.0, 684.0, pre=[(9, 29, 683.4)])
    return {"NVDA": nvda, "SPY": spy, "QQQ": qqq}


@pytest.mark.parametrize("at,day_before,day,since,when,market_when", [
    (datetime(2026, 11, 11, 21, 0, tzinfo=ET), "2026-11-10", "2026-11-11", "Tue Nov 10", "Wed Nov 11", "9:29 AM ET"),
    (SATURDAY, "2026-11-12", "2026-11-13", "Thu Nov 12", "Fri Nov 13", "Fri Nov 13, 9:29 AM ET"),
])
def test_a_premarket_question_after_the_close_finds_that_days_premarket(world, at, day_before, day, since, when, market_when):
    hold(_premarket_tapes(day_before, day))
    chat_chart.reset()
    out = why(world, at, "why did nvda drop premarket")
    lines = out["lines"]
    assert f"Premarket: NVDA +2.36% to 172.77 since the {since} 4:00 PM close ({when}, 9:29 AM ET)." in lines, lines
    assert f"Market in premarket: SPY +0.40% ({market_when}), QQQ +0.50% ({market_when})." in lines, lines
    assert "NVDA is up in premarket, not down." in lines
    assert not any("no NVDA prints are saved" in line for line in lines), lines
    assert out["facts"]["session"] == "pre" and out["facts"]["movePct"] == 2.36
    market = why(world, at, "why did the market drop premarket", ticker=None)["lines"]
    assert any(line.startswith(f"Premarket: SPY +0.40% to 753.00 since the {since} 4:00 PM close ({when}, 9:29 AM ET)")
               and "QQQ +0.50%" in line for line in market), market


def test_a_premarket_after_the_reference_close_still_wins(world):
    """Before the open the morning's own premarket is the one judged, never the day before's."""
    out = why(world, MORNING, "why did nvda drop premarket")
    assert "Premarket: NVDA -9.69% to 157.00 since the Wed Nov 11 4:00 PM close (8:10 AM ET)." in out["lines"], out["lines"]


# ---------------------------------------------------------------- an early-close day (2026-11-12)
EARLY = datetime(2026, 11, 27, 14, 0, tzinfo=ET)                 # Fri after Thanksgiving, the market closed at 13:00


def _early_tape(base, close, after):
    """Wed 11-25 flat at `base`; Fri 11-27 from `base` to `close` at 12:59, then every minute 13:01-13:55 down to `after`."""
    out = [bar("2026-11-25", 9, 30, base), bar("2026-11-25", 15, 58, base), bar("2026-11-25", 15, 59, base)]
    out += [bar("2026-11-27", 9, 30, base), bar("2026-11-27", 12, 58, close), bar("2026-11-27", 12, 59, close)]
    return out + [bar("2026-11-27", 13, m, round(close + (after - close) * m / 55, 4)) for m in range(1, 56)]


def test_an_early_close_day_splits_at_1_pm(world):
    import chart_bad_prints
    assert chat_facts.market_open(datetime(2026, 11, 27, 12, 30, tzinfo=ET))
    assert not chat_facts.market_open(EARLY) and chat_facts.market_open(datetime(2026, 11, 25, 14, 0, tzinfo=ET))
    tape = _early_tape(100.0, 102.0, 98.0)
    closes = chart_levels.session_closes({"bars": tape})
    friday = next(s for s in closes["sessions"] if s["day"] == "2026-11-27")
    assert friday["close"] == 102.0 and friday["complete"] and friday["time"] == bar("2026-11-27", 12, 59, 0)["time"]
    five = [bar("2026-11-25", 9, 30, 100.0), bar("2026-11-25", 15, 55, 100.0), bar("2026-11-27", 9, 30, 100.0), bar("2026-11-27", 12, 55, 101.0)]
    assert next(s for s in chart_levels.session_closes({"fineStudyBars": five})["sessions"] if s["day"] == "2026-11-27")["complete"]
    extended = {(p["window"], p["anchor"]): p for p in closes["extended"]}
    assert extended[("after", "2026-11-27")]["price"] == 98.0
    normal = chart_levels.normalize(tape)                                       # the chart's candles: untouched
    assert chart_levels.remove_extended_hours_bad_prints(normal) == chart_bad_prints.remove_extended_hours_bad_prints(normal)
    hold({"NVDA": tape, "SPY": _early_tape(700.0, 707.0, 706.0), "QQQ": _early_tape(600.0, 603.0, 600.0)})
    chat_chart.reset()
    out = why(world, EARLY, "why is nvda down")
    assert out["lines"][0] == "Regular session: NVDA +2.00% to 102.00 (1:00 PM ET).", out["lines"]
    assert out["lines"][1] == "After hours: NVDA -3.92% to 98.00 since the 1:00 PM close (1:55 PM ET).", out["lines"]
    assert "Market after hours: SPY -0.14% (1:55 PM ET), QQQ -0.50% (1:55 PM ET)." in out["lines"], out["lines"]
    assert out["facts"]["session"] == "after"
