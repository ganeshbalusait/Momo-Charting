"""The report family (2026-11-11): full report, fundamentals, technicals, sentiment, the three views and "should I buy".

Unit tests on scratch files with the chart worked out in-process by the real chart_levels port; the integration
test goes through the real /api/bot-chat route and its one "more" call. The AI, Google News and the analyst
services are faked or refused; nothing leaves the PC.
"""
from __future__ import annotations

import gzip
import json
import re
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from momx import analyst, chart_levels, chat_ai, chat_chart, chat_facts, chat_limits, chat_report, chat_state, chat_why
from test_momx_chart_levels import make_tape
from test_momx_chat_why import KNOWN, NOW, board, ctx

ADVICE = re.compile(r"\byou\s+should\b|\bi\s+recommend\b|\bgood\s+buy\b|\b(?:strong\s+)?buy\s+now\b|\bsell\s+now\b", re.I)


def _summary(base):
    folder = base / "long_term"
    folder.mkdir(exist_ok=True)
    metrics = {"symbol": "NVDA", "years": 8, "window": ["2019-01-27", "2026-01-25"], "roic_now": 0.73, "roe_now": 0.84, "fcf_ttm": 127e9,
               "eps_ttm_usd": 7.91, "growth": {"sales": 0.6, "eps": 0.82, "fcf": None, "equity": 0.63}, "debt": 33.4e9, "equity": 229e9,
               "pe_hist_median": 52.2, "big5": 5, "checks": {}}
    valuation = {"sticker": 135.44, "buffett": 175.97, "fair_value": 135.44, "g": 0.15, "payback_years": 13.0}
    (folder / "summary.json").write_text(json.dumps({"refreshed_at": (NOW - timedelta(hours=11)).isoformat(),
                                                     "rows_by_symbol": {"NVDA": {"metrics": metrics, "valuation": valuation}}}), encoding="utf-8")


def _chain(base):
    folder = base / "oi_chain_cache"
    folder.mkdir(exist_ok=True)
    friday = (NOW.date() + timedelta(days=(4 - NOW.weekday()) % 7 or 7)).isoformat()

    def row(side, strike, oi, volume):
        return {"side": side, "strike": strike, "expiry": friday, "open_interest": oi, "volume": volume, "bid": 1, "ask": 1.1, "mark": 1.05,
                "delta": 0.4, "days_to_expiration": 2}
    doc = {"symbol": "NVDA", "optionQuoteSchemaVersion": 1, "scannedAt": (NOW - timedelta(minutes=3)).isoformat(), "underlyingPrice": 170.0,
           "todayChangePercent": -3.2, "expiryExpectedMoves": {friday: 6.0},
           "selectedExpiryChainRows": [row("CALL", 180, 900, 300), row("CALL", 175, 500, 200), row("PUT", 160, 1800, 600), row("PUT", 165, 700, 100)]}
    with gzip.open(folder / "NVDA.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(doc, handle)


@pytest.fixture
def world(tmp_path, monkeypatch):
    # The chat reads a chain the app holds in memory before the saved one (chat_market._open_chain, newest wins).
    # A process that already bound or imported the app's STATE (an earlier /api/bot-chat route test, or a run beside
    # the live app's chains) handed put/call the REAL NVDA chain: 0.65 = 475,296 put OI / 727,694 call OI on
    # 2026-11-11 and 11-13. Pin an empty app memory so every test here reads only the chain it wrote.
    monkeypatch.setattr(chat_state, "_BOUND", {"state": SimpleNamespace(oi_finder_chain_cache={}), "stream": None})
    monkeypatch.delenv("MOMX_BOARD_CACHE_DIR", raising=False)
    monkeypatch.delenv("AGX_MOMX_GRADE_DIR", raising=False)
    monkeypatch.setenv("AGX_ANALYST_CACHE_DIR", str(tmp_path / "analyst"))
    board(tmp_path, "Mag7", [("SPY", 0.4, 760.0, 0.5, False), ("QQQ", 0.6, 690.0, 0.5, False), ("NVDA", -3.2, 170.0, 0.9, True)])
    board(tmp_path, "Mag7", [("NVDA", -3.2, 170.0, -0.9, True)], bear=True)
    board(tmp_path, "Watchlist", [("AMD", -2.5, 230.0, 0.4, False)])
    _summary(tmp_path)
    _chain(tmp_path)
    tape = make_tape(date(2026, 11, 10), 7)

    def compute(symbol, timeframe, ctx_):
        if symbol != "NVDA":
            return None
        if timeframe.startswith("ema:"):
            return chart_levels.ema_report(tape, timeframe[4:].split(","))
        return chart_levels.analyze(tape, timeframe)
    monkeypatch.setattr(chat_chart, "compute", compute)
    monkeypatch.setattr(chat_chart, "peek", lambda *a: None)
    monkeypatch.setattr(chat_why, "_fetch", lambda url, timeout: b"<rss><channel></channel></rss>")
    monkeypatch.setattr(analyst, "_get_json", lambda url: (_ for _ in ()).throw(TimeoutError()))
    for module in (chat_facts, chat_why, analyst):
        module.reset()
    chat_report._lt_cache.clear()
    chat_limits.LIMITS.reset()
    yield tmp_path
    chat_why.reset()
    chat_state.unbind()


# ---------------------------------------------------------------- routing
@pytest.mark.parametrize("question,tool", [
    ("NVDA fundamentals", "fundamentals"), ("NVDA technicals", "technicals"), ("full report on NVDA", "report"), ("NVDA", "report"),
    ("everything on nvda", "report"), ("tell me about nvda", "report"), ("should I buy NVDA", "rules"), ("is NVDA a buy", "rules"),
    ("short term TSLA", "outlook_short"), ("TSLA today", "outlook_short"), ("TSLA this week", "outlook_short"), ("TSLA for a weekly call", "outlook_short"),
    ("long term TSLA", "outlook_long"), ("TSLA leap", "outlook_long"), ("TSLA for a LEAP", "outlook_long"), ("NVDA mid term", "outlook_mid"),
    ("NVDA analyst ratings", "analyst"), ("nvda price target", "analyst"), ("nvda sentiment", "sentiment"), ("AAPL fundamentals", "fundamentals"),
])
@pytest.mark.parametrize("scope", ["owner", "scanner"])
def test_each_report_question_reaches_its_tool(tmp_path, question, tool, scope):
    plan = chat_ai.sure_calls(question, ctx(tmp_path, scope), KNOWN)
    assert plan["calls"][0]["tool"] == tool and plan["calls"][0]["args"]["ticker"] in ("NVDA", "TSLA", "AAPL"), plan


def test_should_i_buy_starts_with_the_no_advice_line():
    plan = chat_ai.sure_calls("should I buy NVDA", ctx(None), KNOWN)
    assert plan["lines"] == [chat_ai.NO_ADVICE_LINE]


# ---------------------------------------------------------------- the verdict rule
def _event(days_ago, side, timeframe="1H"):
    return {"at": NOW - timedelta(days=days_ago), "timeframe": timeframe, "name": "x", "side": side}


@pytest.mark.parametrize("sides,verdict,strength", [
    ([], "Neutral", 1), (["bull", "bear"], "Neutral", 1), (["bull"] * 3, "Bullish", 4), (["bear"] * 3 + ["bull"], "Bearish", 3),
    (["bull"] * 3 + ["bear"] * 2, "Bullish", 2), (["bull"] * 4 + ["bear"] * 3, "Bullish", 1),
])
def test_the_verdict_rule_and_its_strength_edges(sides, verdict, strength):
    out = chat_report.verdict("short", [_event(1, side) for side in sides], NOW)
    assert (out["verdict"], out["strength"]) == (verdict, strength) and out["label"] == chat_report.VIEWS_LABEL


def test_events_outside_a_views_window_or_timeframes_do_not_count():
    events = [_event(30, "bear"), _event(1, "bear", "M"), _event(1, "bull")]
    assert chat_report.verdict("short", events, NOW)["verdict"] == "Bullish"
    assert chat_report.verdict("long", events, NOW)["bear"] and chat_report.verdict("long", events, NOW)["verdict"] == "Bearish"


# ---------------------------------------------------------------- the sections
def test_the_full_report_has_its_sections_in_order_and_the_bot_only_for_the_owner(world):
    for scope in ("scanner", "owner"):
        handler = chat_report.TOOLS["report"]["handler"]
        out = handler({"ticker": "NVDA"}, ctx(world, scope))
        keys = [s["key"] for s in out["sections"]]
        assert keys == [k for k in chat_report.SECTION_ORDER if k in keys] and keys[:2] == ["price", "why"] and "bot" not in keys
        more = chat_why.collect(out["more"], "v@x", scope)
        slow = [s["key"] for s in more["sections"]]
        assert slow == [k for k in chat_report.SECTION_ORDER if k in slow]
        assert ("bot" in slow) == (scope == "owner")
        assert "bot" not in [s["key"] for s in chat_report.clean_sections(more["sections"], "scanner")]
        assert {"technicals", "fundamentals", "options", "sentiment", "rules"} <= set(slow)
        assert all(s["lines"] for s in more["sections"])                       # never an empty section
        assert [v["view"] for v in more["views"]] == ["short", "mid", "long"] and more["miniChart"]["daily"]
        assert out["lines"][-1] == chat_report.ADVICE_LINE


def test_the_21_50_ema_section_matches_the_chart_port(world):
    tape = make_tape(date(2026, 11, 10), 7)
    out = chat_report.TOOLS["technicals"]["handler"]({"ticker": "NVDA"}, ctx(world))
    more = chat_why.collect(out["more"], "v@x", "scanner")
    technicals = next(s for s in more["sections"] if s["key"] == "technicals")
    expected = chart_levels.analyze(tape, "D")["levels"]["emaDetail"]
    assert any(line.startswith("D (newest bar") and f"the 21 EMA {expected['ema21']['value']:,.2f}" in line
               and f"the 50 EMA {expected['ema50']['value']:,.2f}" in line for line in technicals["lines"])
    assert not any("vwap" in line.lower() and "chart only" not in line.lower() for line in technicals["lines"])


def test_fundamentals_name_the_period_and_a_missing_metric(world):
    out = chat_report.TOOLS["fundamentals"]["handler"]({"ticker": "NVDA"}, ctx(world))
    section = next(s for s in out["sections"] if s["key"] == "fundamentals")
    text = "\n".join(section["lines"])
    assert "2019-2026 fiscal years" in text and "revenue 60%/yr" in text and "free cash flow missing" in text
    assert "ROIC 73%, ROE 84%." in text and "debt/equity 0.15" in text and "0.3 years of free cash flow" in text
    assert "P/E 21.5 at 170.00" in text and "50% margin of safety: 67.72" in text and "Long Term rule: AVOID" in text and section["asOf"]


def test_sentiment_reads_put_call_from_the_chain_with_its_expiries(world):
    found = chat_report.put_call("NVDA", ctx(world), NOW)
    assert round(found["oi"], 2) == round(2500 / 1400, 2) and round(found["volume"], 2) == round(700 / 500, 2)
    assert "by open interest 1.79" in found["line"] and found["expiries"]


def test_should_i_buy_shows_the_rules_counted_with_risks_and_never_advice(world):
    out = chat_report.TOOLS["rules"]["handler"]({"ticker": "NVDA"}, ctx(world))
    more = chat_why.collect(out["more"], "v@x", "scanner")
    rules = next(s for s in more["sections"] if s["key"] == "rules")
    text = "\n".join(rules["lines"])
    assert re.search(r"Short term \(today to about 2 weeks\): \d of 6 rule checks point up, \d point down", text)
    assert "Long Term rule:" in text and "It is on a puts board now." in text and rules["lines"][-1] == chat_report.ADVICE_LINE
    assert not ADVICE.search(json.dumps(more)) and not ADVICE.search(json.dumps(out))


def test_the_ai_down_report_still_answers_and_the_mini_chart_has_markers_and_levels(world):
    out = chat_report.TOOLS["report"]["handler"]({"ticker": "NVDA"}, ctx(world))
    more = chat_why.collect(out["more"], "v@x", "scanner")
    chart = chat_report.clean_mini_chart(more["miniChart"])
    assert len(chart["daily"]) == 60 and chart["weekly"] and all(m["chart"] in ("D", "W") for m in chart["markers"])
    assert chart["support"] or chart["resistance"]


def test_the_clean_checks_drop_anything_odd():
    assert chat_report.clean_views([{"view": "short", "verdict": "Buy now", "strength": 9}]) == []
    assert chat_report.clean_views([{"view": "short", "verdict": "Bullish", "strength": 9, "reason": "x"}])[0]["strength"] == 1
    assert chat_report.clean_sections([{"key": "bot", "title": "Bot", "lines": ["x"]}, {"key": "evil", "lines": ["x"]}], "scanner") == []


# ---------------------------------------------------------------- through the real route
def test_the_route_answers_the_report_fast_and_the_more_call_fills_it(world, monkeypatch):
    from api_harness import RELAYED, running_api, seed_user
    from cf_access_auth import ACCESS_EMAIL_HEADER
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(world))
    real_answer = chat_ai.answer
    monkeypatch.setattr(chat_ai, "answer", lambda q, a, now=None, **kw: real_answer(q, a, NOW, **kw))
    monkeypatch.setattr(chat_ai, "_default_ask", lambda *a: {"ok": False, "error": "no AI in tests"})
    seed_user("viewer-report@example.com")
    regular = {**RELAYED, ACCESS_EMAIL_HEADER: "viewer-report@example.com"}
    with running_api() as api:
        _, rules = api.post("/api/bot-chat", {"question": "is NVDA a buy"}, headers=regular)
        _, report = api.post("/api/bot-chat", {"question": "full report on NVDA"}, headers=regular)
        _, more = api.post("/api/bot-chat", {"more": report["more"]}, headers=regular)
    assert rules["intent"] == "rules" and rules["lines"][0] == chat_ai.NO_ADVICE_LINE and rules["lines"][-1] == chat_report.ADVICE_LINE
    assert any(link["view"] == "Long Term" and link.get("ticker") == "NVDA" for link in rules["links"])
    assert report["intent"] == "report" and report["sections"][0]["key"] == "price" and report["more"]
    keys = [s["key"] for s in more["more"]["sections"]]
    assert "bot" not in keys and "technicals" in keys and more["more"]["views"][0]["view"] == "short"
    assert not re.search(r"\bbot\b|paper|scorecard", json.dumps(more).lower())


# ---------------------------------------------------------------- the Long Term page's Technical card (2026-11-11 review F5)
def _held(monkeypatch):
    """The chart child's answers are already held (peek finds them), as after any chat question on the ticker."""
    monkeypatch.setattr(chat_chart, "peek", chat_chart.compute)


def test_the_technical_card_has_the_three_terms_and_the_mini_chart_in_the_screens_shape(world, monkeypatch):
    _held(monkeypatch)
    card = chat_report.technical("NVDA", ctx(world), wait=0)
    assert card["symbol"] == "NVDA" and card["source"] == chat_report.TECHNICAL_SOURCE and card["asOf"].startswith("as of ")
    assert set(card["terms"]) == {"short", "mid", "long"}
    for term in card["terms"].values():
        assert term["verdict"] in ("bullish", "bearish", "neutral") and 1 <= term["strength"] <= 4 and term["reason"]
    daily, weekly = card["charts"]["daily"], card["charts"]["weekly"]
    assert len(daily["bars"]) == 60 and weekly["bars"] and all({"time", "open", "high", "low", "close"} <= set(b) for b in daily["bars"])
    assert all(e["kind"] in ("bull", "bear", "earnings") and e["time"] and e["name"] for e in daily["events"] + weekly["events"])
    assert daily["support"] or daily["resistance"]
    assert all(set(line) == {"price", "label"} for line in (daily["support"], daily["resistance"]) if line)
    assert "vwap" not in json.dumps(card).lower() and not ADVICE.search(json.dumps(card))


def test_the_technical_card_uses_the_chat_reports_own_verdicts(world, monkeypatch):
    _held(monkeypatch)
    chat_facts.board_row("NVDA", ctx(world), NOW)               # the boards the chat already read are in memory
    report = chat_report.build("NVDA", ctx(world), ("short", "mid", "long"), ("price", "technicals"), slow=False)
    card = chat_report.technical("NVDA", ctx(world), wait=0)
    assert {v["view"]: (v["verdict"].lower(), v["strength"], v["reason"]) for v in report["views"]} == \
        {k: (t["verdict"], t["strength"], t["reason"]) for k, t in card["terms"].items()}
    assert [b["time"] for b in card["charts"]["daily"]["bars"]] == [b["time"] for b in report["miniChart"]["daily"]]


def test_the_technical_card_never_reads_a_board_file(world, monkeypatch):
    _held(monkeypatch)
    monkeypatch.setattr(chat_facts, "board", lambda *a: (_ for _ in ()).throw(AssertionError("a board file was read")))
    assert chat_report.technical("NVDA", ctx(world), wait=0)["terms"]


def test_with_no_chart_held_the_card_is_left_out_and_the_child_is_asked_on_a_side_thread(world, monkeypatch):
    held, asked = {}, []
    real = chat_chart.compute

    def compute(symbol, timeframe, ctx_):
        asked.append(timeframe)
        held[(symbol, timeframe)] = real(symbol, timeframe, ctx_)
        return held[(symbol, timeframe)]
    monkeypatch.setattr(chat_chart, "compute", compute)
    monkeypatch.setattr(chat_chart, "peek", lambda symbol, timeframe, ctx_: held.get((symbol, timeframe)))
    assert chat_report.technical("NVDA", ctx(world), wait=0) is None and not asked        # no wait: cached reads only
    card = chat_report.technical("NVDA", ctx(world), wait=5)
    assert asked and card and card["charts"]["daily"]["bars"]
    assert chat_report.technical("ZZZZ", ctx(world), wait=0) is None


def test_the_analyst_payload_carries_the_technical_card_only_when_asked(world, monkeypatch):
    _held(monkeypatch)
    with_card = analyst.payload("nvda", wait=0.5, artifacts=world, now=NOW)
    assert with_card["symbol"] == "NVDA" and with_card["technical"]["terms"]["long"]["verdict"]
    assert "technical" not in analyst.payload("NVDA", wait=0.5)
    assert analyst.payload("<b>", wait=0.5, artifacts=world)["technical"] is None


def test_the_long_term_analyst_route_sends_the_technical_card(world, monkeypatch):
    """GET /api/long-term/analyst through the real route: the analyst views (services faked down) and the card."""
    from api_harness import RELAYED, running_api, seed_user
    from cf_access_auth import ACCESS_EMAIL_HEADER
    _held(monkeypatch)
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(world))
    seed_user("viewer-lt-technical@example.com")
    viewer = {**RELAYED, ACCESS_EMAIL_HEADER: "viewer-lt-technical@example.com"}
    with running_api() as api:
        status, body = api.get("/api/long-term/analyst?symbol=NVDA", headers=viewer)
        none_status, none = api.get("/api/long-term/analyst?symbol=ZZZZ", headers=viewer)
    assert status == 200 and body["label"] == analyst.LABEL
    tech = body["technical"]
    assert set(tech["terms"]) == {"short", "mid", "long"} and tech["charts"]["daily"]["bars"] and tech["source"] == chat_report.TECHNICAL_SOURCE
    assert none_status == 200 and none["technical"] is None
