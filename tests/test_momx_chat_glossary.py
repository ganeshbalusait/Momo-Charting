"""The chat's glossary and the end of AI-written prose (2026-11-11 design change).

The trader's rule: the chat never gives buy/sell/hold advice. Three rechecks in a row (rv4, rv5, rv6) found new advice
phrasings slipping past the word filter on the AI's sentences, so the chat no longer shows any sentence an AI wrote:

* fact answers are the tools' lines (no writer),
* "what is theta", "explain how calls work" are a fixed entry from momx/chat_glossary.json (momx/chat_glossary.py),
* the why-answer's AI returns only {category, headline_index}; the line is the server's (tests/test_momx_chat_why.py).

Unit: every glossary entry against the advice rules, matching and typos. Integration: ai.answer() for the owner and a
regular user, with a fake AI that returns every text the rechecks caught (tests/fixtures/chat_advice_repros.json) in
every field it could; none may reach a reply. Over HTTP: tests/test_momx_chat_search_acceptance.py. Nothing leaves the PC.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from momx import chat_ai as ai
from momx import chat_glossary as glossary
from momx import chat_rag, chat_rag_index
from test_momx_chat_ai import is_plan, scanner_files
from test_momx_chatbot import NOW, loser, signal, write

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "chat_advice_repros.json"
REPROS = [row["text"] for row in json.loads(FIXTURE.read_text(encoding="utf-8"))["texts"]]

#: Words a definition never needs: talking to the reader, the writer's own view, a recommendation, a forecast.
NEVER_IN_AN_ENTRY = re.compile(
    r"(?-i:\bus\b)|\b(?:you|your|yours|we|our|should|ought|must|recommend\w*|suggest\w*|best|great|ideal|perfect|smart|opportunit\w+|bargain|"
    r"undervalued|overvalued|cheap|attractive|will|likely|probably|expect(?:s|ing)?\s+(?:it|the\s+stock|a\s+rise|a\s+fall)|time\s+to|load\s+up|"
    r"now\s+is|right\s+now|buy\s+now|sell\s+now|good\s+(?:time|entry|buy|sell|trade|idea))\b|(?<![\w'-])I(?![\w'-])|https?:|www\.|@", re.I)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    ai._REST.update(until=0.0, why="")
    ai._REST_SCANNER.update(until=0.0, why="")
    ai.clear_plans()
    yield
    ai._REST.update(until=0.0, why="")
    ai._REST_SCANNER.update(until=0.0, why="")
    ai.clear_plans()


# ---------------------------------------------------------------- unit: the entries themselves
def test_the_glossary_has_about_120_short_plain_entries():
    rows = glossary.entries()
    assert len(rows) >= 120, len(rows)
    for row in rows:
        sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"(])", row["text"]) if s.strip()]
        assert 1 <= len(sentences) <= 3, (row["term"], sentences)
        assert len(row["text"]) <= 420 and row["text"].endswith("."), row["term"]


@pytest.mark.parametrize("row", glossary.entries(), ids=lambda row: row["term"])
def test_every_entry_passes_the_chats_advice_rules(row):
    """The chat's own advice rules for a general explanation (chat_ai._unsafe_general: links, logins, recommendation,
    entry and exit wording, orders to the reader, opinions, forecasts, value words, stranger tickers), and a stricter list."""
    raw = json.loads(glossary.GLOSSARY_JSON.read_text(encoding="utf-8"))
    names = next((item.get("names") or [] for item in raw["terms"] if item["term"] == row["term"]), [])
    question = " ".join(["what is", row["term"], *row["aliases"], *names])
    assert not ai._unsafe_general(row["text"], question), row
    assert not NEVER_IN_AN_ENTRY.search(row["text"]), (row["term"], NEVER_IN_AN_ENTRY.search(row["text"]).group(0))


def test_every_entry_is_public_and_no_two_entries_share_a_name():
    seen: dict[str, str] = {}
    for row in glossary.entries():
        assert row["scope"] == "all" and not chat_rag_index.OWNER_ONLY.search(f"{row['term']} {row['text']}"), row["term"]
        for name in (row["term"], *row["aliases"]):
            key = glossary._key(name)
            assert seen.setdefault(key, row["term"]) == row["term"], (name, seen[key], row["term"])


# ---------------------------------------------------------------- unit: matching
@pytest.mark.parametrize("question,term", [
    ("what is theta", "Theta"), ("what is an iron condor", "Iron condor"), ("what is implied volatility", "Implied volatility"),
    ("what is max pain", "Max pain"), ("explain how calls work", "Call"), ("short squeeze", "Short squeeze"),
    ("What is IV?", "Implied volatility"), ("define vega", "Vega"), ("what does ITM mean", "In the money"), ("what are leaps", "LEAPS"),
    ("how does a covered call work", "Covered call"), ("what is a cash secured put", "Cash secured put"), ("what is 0dte", "0DTE"),
    ("what is skittles", "Skittles"), ("what is sqz", "SQZ"), ("what is H/L", "H/L"), ("what is confirm l1-l3", "Confirm level"),
    ("what is CALL2H", "CALL2H"), ("what is momox", "MX A+"), ("what is the vwap", "VWAP"), ("what is a gamma squeeze", "Gamma squeeze"),
    ("what is gamma exposure", "Gamma exposure"), ("what is a pin", "Pin"), ("what is the expected move", "Expected move"),
    ("what is free cash flow", "Free cash flow"), ("what is margin of safety", "Margin of safety"), ("what is a bear call spread", "Bear call spread")])
def test_a_definition_question_finds_its_entry(question, term):
    found = glossary.term_of(question, "scanner")
    assert found and found["term"] == term, (question, found)


@pytest.mark.parametrize("question,term", [("what is thetta", "Theta"), ("what's an iron condr", "Iron condor"), ("what is implied volitility", "Implied volatility"),
                                           ("explain how cals work", "Call"), ("waht is a straddle", None), ("what is a credit spred", "Credit spread"),
                                           ("whats a strangel", "Strangle")])
def test_matching_forgives_a_typo_like_the_help_search(question, term):
    found = glossary.term_of(question, "scanner")
    assert (found["term"] if found else None) == term, (question, found)


@pytest.mark.parametrize("question", ["what is a flibbertigibbet", "nvda", "hello", "what is spy"])
def test_no_entry_for_what_the_glossary_does_not_hold(question):
    assert glossary.term_of(question, "scanner") is None


def test_an_unknown_term_says_so_and_links_learn_and_setup(monkeypatch):
    monkeypatch.setattr(chat_rag, "search", lambda *a, **k: [])
    out = glossary.define({"query": "what is a flibbertigibbet"}, {"scope": "scanner"})
    assert out["lines"] == [glossary.UNKNOWN_LINE] and out["links"] == [glossary.LEARN_LINK]
    assert out["lines"][0].startswith("That term isn't in the app's glossary yet")


def test_the_help_text_answers_a_term_the_glossary_lacks_but_never_news(monkeypatch):
    rows = [{"source": "news", "title": "NVDA: buy now", "text": "x", "date": None, "link": {"view": "News Feed"}},
            {"source": "page", "title": "OI Level Scripts page", "text": "Turns the walls into a study.", "date": None, "link": {"label": "Open OI Level Scripts", "view": "OI Level Scripts"}}]
    monkeypatch.setattr(chat_rag, "search", lambda *a, **k: rows)
    out = glossary.define({"query": "what is the oi level scripts thing"}, {"scope": "scanner"})
    assert out["lines"] == ["Page: OI Level Scripts page. Turns the walls into a study."] and "buy now" not in json.dumps(out)


# ---------------------------------------------------------------- integration: through the chat's answer, both scopes
def _never(*a):
    pytest.fail("a definition the words make sure asks no AI")


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question,term", [("what is theta", "Theta"), ("what is an iron condor", "Iron condor"), ("what is implied volatility", "Implied volatility"),
                                           ("what is max pain", "Max pain"), ("explain how calls work", "Call"), ("short squeeze", "Short squeeze")])
def test_the_definitions_he_asked_for_are_the_glossary_entry(tmp_path, scope, question, term):
    out = ai.finish(ai.answer(question, tmp_path, NOW, None, _never, scope=scope), scope)
    entry = glossary.lookup(term)
    assert out["intent"] == "define" and out["lines"] == [glossary.line_of(entry)], out
    assert out["links"][0]["view"] == "Learn + Setup" and "ai" not in out and "general" not in out


@pytest.mark.parametrize("scope", ["owner", "scanner"])
def test_a_ticker_on_screen_keeps_its_number_but_not_for_a_pure_idea(tmp_path, scope):
    plan = ai.sure_calls("what is max pain", {"scope": scope, "account": "x", "pageTicker": "SPY", "pageName": None, "ticker": None, "day": None,
                                              "side": None, "now": NOW}, frozenset({"SPY"}))
    assert plan["calls"][0]["tool"] == "max_pain"
    plan = ai.sure_calls("what is theta", {"scope": scope, "account": "x", "pageTicker": "SPY", "pageName": None, "ticker": None, "day": None,
                                           "side": None, "now": NOW}, frozenset({"SPY"}))
    assert plan["calls"][0]["tool"] == "define"


TICKER_WORDS = frozenset({"IRON", "GAP", "BOLT", "SPY", "NVDA"})      # real tickers on the 1149-name list (rv7)


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question,term", [("what is an iron condor", "Iron condor"), ("what is a gap up", "Gap"), ("what is gap down", "Gap"),
                                           ("what is the live bolt", "LIVE bolt"), ("what is iron butterfly", None)])
def test_a_glossary_term_beats_a_ticker_hidden_in_its_words(tmp_path, monkeypatch, scope, question, term):
    """IRON, GAP and BOLT are real tickers. "what is an iron condor" read IRON as the ticker, skipped the glossary and
    answered "Here is where to look at IRON." with the AI resting (2026-11-11 review rv7)."""
    from momx import chat_tools
    monkeypatch.setattr(chat_tools, "market_tickers", lambda *a, **k: TICKER_WORDS)
    entry = glossary.lookup(glossary.asked_term(question), scope, fuzzy=False)
    assert entry and (term is None or entry["term"] == term), (question, entry)
    out = ai.finish(ai.answer(question, tmp_path, NOW, None, _never, scope=scope), scope)
    assert out["intent"] == "define" and out["lines"] == [glossary.line_of(entry)], out


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question,tool", [("what is IRON", None), ("what is GAP", None), ("what is the price of iron", "quote"),
                                           ("what is the max pain on SPY", "max_pain"), ("what is the call wall on nvda", None),
                                           ("what is the expected move on spy", None), ("what is nvda 21 ema", "ema")])
def test_a_ticker_question_with_a_glossary_word_stays_a_ticker_question(scope, question, tool):
    ctx = {"scope": scope, "account": "x", "pageTicker": None, "pageName": None, "ticker": None, "day": None, "side": None, "now": NOW}
    plan = ai.sure_calls(question, ctx, TICKER_WORDS)
    tools = [call["tool"] for call in (plan or {}).get("calls") or []]
    assert "define" not in tools, (question, plan)
    if tool:
        assert tools and tools[0] == tool, (question, plan)


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question", ["what is ITM", "What does OTM mean?", "what is ATM", "what is IV", "what is EPS", "what is DTE"])
def test_a_term_typed_in_capitals_that_is_not_a_listed_ticker_is_the_glossary(tmp_path, monkeypatch, scope, question):
    """With the AI off, "what is ITM" read ITM as a ticker (capitals) and answered "Here is where to look at ITM."
    (2026-11-11 recheck). Only listed tickers keep a capital word as a ticker."""
    from momx import chat_tools
    monkeypatch.setattr(chat_tools, "market_tickers", lambda *a, **k: TICKER_WORDS)
    entry = glossary.lookup(glossary.asked_term(question), scope, fuzzy=False)
    assert entry, question
    out = ai.finish(ai.answer(question, tmp_path, NOW, None, _never, scope=scope), scope)
    assert out["intent"] == "define" and out["lines"] == [glossary.line_of(entry)], out
    ctx = {"scope": scope, "account": "x", "pageTicker": None, "pageName": None, "ticker": None, "day": None, "side": None, "now": NOW}
    tools = [call["tool"] for call in (ai.sure_calls("what is GAP", ctx, TICKER_WORDS) or {}).get("calls") or []]
    assert "define" not in tools


# ---------------------------------------------------------------- integration: no AI prose on any path
def _everything(texts):
    """A fake AI that answers every prompt with every caught text in every field the old code read: a writer's "reply",
    a general answer, reason sentences and conflicts, and as reason categories too. The planner part routes as asked."""
    joined = " ".join(texts)

    def make(calls, general=False):
        def ask(system, user, schema):
            data = {"reply": joined, "text": joined, "conflict": joined, "summary": joined, "explained": True,
                    "reasons": [{"text": t, "category": t, "n": 1, "headline_index": 1} for t in texts]}
            if is_plan(system):
                data.update(calls=calls, general=general)
            return {"ok": True, "provider": "fake", "data": data}
        return ask
    return make


def _shown(out) -> str:
    return json.dumps(out, default=str)


PATHS = [
    ("why aapl failed", [], False),                                                          # the records (owner) / refused (regular)
    ("mag7 list at 10:00", [], False),                                                       # the saved scanner
    ("what was cooking this morning around ten", [{"tool": "scanner_list", "args": {"list": "Mag7", "time": "10:00"}}], False),   # planner -> tool
    ("did that nvidia trade make money", [{"tool": "bot_what_if", "args": {"ticker": "NVDA"}}], False),                           # planner -> records
    ("tell me in general terms what a call option is all about", [], True),                 # planner -> general idea -> glossary
    ("tell me in general terms about flibberwocks", [], True),                              # planner -> general idea the glossary lacks
    ("what is theta", [], False),                                                           # sure definition
    ("is it smart to load up on tsla calls into the close", [], True),
]


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question,calls,general", PATHS, ids=[p[0][:30] for p in PATHS])
def test_no_text_the_rechecks_caught_can_reach_a_reply(tmp_path, monkeypatch, scope, question, calls, general):
    monkeypatch.setattr(chat_rag, "search", lambda *a, **k: [])
    write(tmp_path, bull=[signal("NVDA"), loser("AAPL")])
    scanner_files(tmp_path)
    ask = _everything(REPROS)(calls, general)
    out = ai.finish(ai.answer(question, tmp_path, NOW, None, ask, scope=scope, account="x@y"), scope)
    shown = _shown(out)
    leaked = [text for text in REPROS if text in shown]
    assert not leaked and "ai" not in out and "general" not in out, (leaked[:5], out)


def test_the_caught_texts_are_all_here():
    assert len(REPROS) >= 250
    for wanted in ("We expect SPY to reach 600.", "Time to load up on SPY calls.", "Hold SPY.", "We like SPY here.",
                   "The capex raise makes NVDA a stock to hold through earnings."):
        assert wanted in REPROS, wanted


NUMBER_TERMS = [("what is the market cap", "Market cap"), ("what is the p/e", "P/E ratio"), ("what is the eps", "EPS"),
                ("what is the iv", "Implied volatility"), ("what is the float", "Float"), ("what is the open interest", "Open interest"),
                ("what is delta", "Delta"), ("what is gamma exposure", "Gamma exposure"), ("what is the short interest", "Short interest")]


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question,term", NUMBER_TERMS)
def test_a_per_ticker_number_term_with_a_ticker_in_play_is_that_tickers_number(scope, question, term):
    """"what is the market cap" on the NVDA page, or after a question about NVDA, answered with the dictionary line:
    market cap, P/E, EPS, IV, float, open interest, delta, gamma exposure and short interest were not data words, so
    the ticker in play was ignored (2026-11-11 review rv7). With no ticker anywhere it is still the glossary line."""
    entry = glossary.lookup(glossary.asked_term(question), scope)
    assert entry and entry["term"] == term and entry.get("number") is True, (question, entry)
    base = {"scope": scope, "account": "x", "pageTicker": None, "pageName": None, "ticker": None, "day": None, "side": None, "now": NOW}
    for ctx in ({**base, "pageTicker": "NVDA"}, {**base, "ticker": "NVDA"}, {**base, "ticker": "NVDA", "pageTicker": "NVDA"}):
        plan = ai.sure_calls(question, ctx, frozenset({"NVDA"}))
        assert not plan or all(call["tool"] != "define" for call in plan["calls"]), (question, ctx, plan)
    plan = ai.sure_calls(question, base, frozenset({"NVDA"}))
    assert plan and plan["calls"][0]["tool"] == "define", (question, plan)
