"""Unit: the Ticker trader journal, notes and AI review (momx/ticker_journal.py and the TickerTrader methods
that use it). Real engine trades from fake quotes, hand-made runs for the counts, and a stand-in AI: nothing
here reaches a broker or a real AI service."""
from __future__ import annotations

import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest

from momx import chat_ai, ticker_journal
from momx.ticker_trader import TraderError
from test_momx_ticker_trader import World, bought, run_of

GOOD = "NVDA Call has 2 closed trades with net +$290.00, and 1 of them reached T3."


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


@pytest.fixture(autouse=True)
def _ai_on(monkeypatch):
    monkeypatch.setenv("AGX_CHAT_AI", "1")


def two_trades(world):
    """A stop inside 5 minutes (-$180), then Trade again and T1 / T2 / T3 (+$470), through the real engine."""
    trader = bought(world)
    stop = run_of(trader)["open"]["stopStock"]
    world.set(stop - 0.05, bid=0.5, ask=0.6)
    world.step(trader)
    trader.again("NVDA-CALL")
    world.set(100.0, bid=1.0, ask=1.1)
    world.step(trader)
    world.step(trader)
    for stock, bid in ((102.1, 1.8), (104.2, 2.6), (106.0, 3.6)):
        world.set(stock, bid=bid, ask=bid + 0.1)
        world.step(trader)
    assert [t["pl"] for t in run_of(trader)["trades"]] == [-180.0, 470.0]
    return trader


class Ask:
    """A stand-in for the AI: counts its calls and answers with a fixed paragraph (or raises)."""

    def __init__(self, text=GOOD, ok=True, boom=False):
        self.text, self.ok, self.boom, self.calls = text, ok, boom, []

    def __call__(self, system, user, schema):
        self.calls.append(json.loads(user))
        if self.boom:
            raise RuntimeError("down")
        return {"ok": self.ok, "data": {"summary": self.text}} if self.ok else {"ok": False, "error": "quota"}


# ---------------------------------------------------------------- the journal's numbers
def test_journal_numbers_from_real_engine_trades(world):
    trader = two_trades(world)
    doc = trader.journal("NVDA-CALL")
    s = doc["stats"]
    assert doc["label"] == "NVDA Call" and doc["simulated"] is True and doc["scope"] == "NVDA-CALL"
    assert (s["n"], s["wins"], s["losses"], s["winRatePct"], s["netPnl"]) == (2, 1, 1, 50.0, 290.0)
    assert (s["avgWin"], s["avgLoss"], s["profitFactor"], s["expectancy"]) == (470.0, -180.0, 2.61, 145.0)
    assert (s["bestTrade"], s["worstTrade"]) == (470.0, -180.0)
    assert doc["startBalance"] == 10000.0 and doc["balance"] == 10290.0
    assert [p["balance"] for p in doc["equity"]] == [9820.0, 10290.0]
    newest, oldest = doc["trades"]
    assert doc["bestId"] == newest["id"] and doc["worstId"] == oldest["id"]
    assert oldest["outcome"] == "stopped" and oldest["fastStop"] is True and oldest["reachedT1"] is False
    assert newest["outcome"] == "T3" and newest["reachedT1"] is True and newest["fastStop"] is False
    assert oldest["timeBucket"] == "morning" and oldest["holdBucket"] == "under5" and oldest["holdMinutes"] == 0.2
    assert newest["t1"] == 102.0 and [h["target"] for h in newest["hits"]] == ["T1", "T2", "T3"]
    by = {g: {r["key"]: r["trades"] for r in rows} for g, rows in doc["breakdowns"].items()}
    assert by["side"] == {"CALL": 2, "PUT": 0}
    assert by["outcome"] == {"stopped": 1, "T1": 0, "T2": 0, "T3": 1, "early": 0}
    assert "ticker" not in by                               # one run: no per-ticker split
    # The Scorecard's own shapes, so its tiles, bars and calendar draw these unchanged.
    assert doc["days"] == [{"day": "2026-11-13", "sent": 2, "filled": 2, "closed": 2, "pnl": 290.0, "cum": 290.0}]
    assert doc["daySummary"]["tradingDays"] == 1 and "dailyLimitBreaks" not in doc["daySummary"]
    periods = doc["journal"]["periods"]
    assert periods["day"] == [{"key": "2026-11-13", "trades": 2, "wins": 1, "losses": 1, "winRatePct": 50.0, "pnl": 290.0,
                               "calls": 290.0, "puts": 0.0}]
    assert periods["week"][0]["key"] == "2026-11-09" and periods["month"][0]["key"] == "2026-11"
    assert doc["thisWeek"] == "2026-11-09" and doc["review"] is None


def closed(run_id, n, pl, at, side="CALL", ticker="NVDA", hits=(), events=(), held=10):
    bought_at = at
    closed_at = (at + timedelta(minutes=held))
    return {"id": f"{run_id}-{n}", "status": "closed", "ticker": ticker, "side": side, "entry": 1.0, "qty": 3, "cost": 300.0,
            "pl": pl, "boughtAt": bought_at.isoformat(), "closedAt": closed_at.isoformat(), "day": at.date().isoformat(),
            "hits": [{"target": t, "at": closed_at.isoformat()} for t in hits],
            "events": [{"at": closed_at.isoformat(), "text": t} for t in events]}


def run(run_id, trades, ticker="NVDA", side="CALL", start=10000.0):
    return {"id": run_id, "ticker": ticker, "side": side, "startBalance": start,
            "balance": start + sum(t["pl"] for t in trades), "trades": trades, "open": None}


def test_what_to_improve_says_too_few_under_ten_and_counts_each_line(world):
    at = world.now.replace(hour=9, minute=35)
    few = [closed("NVDA-CALL", i, -50.0, at, events=["Stop: stock traded through 99.00, sold 3 at $0.80."], held=3)
           for i in range(3)]
    doc = ticker_journal.build([run("NVDA-CALL", few)], "NVDA-CALL", now=world.now)
    lines = {x["key"]: x for x in doc["improve"]}
    assert set(lines) == {"lossTime", "lossSide", "t1First", "fastStop", "callsPuts"}
    assert all(x["judge"] == "Too few trades to judge" and x["enough"] is False and x["trades"] == 3 for x in lines.values())
    assert lines["lossTime"]["text"] == "Most losing trades were bought 9:30-10:00 ET: 3 of 3 losing trades, net -$150.00."
    assert lines["fastStop"]["text"] == "The stop was hit within 5 minutes of the buy on 3 of 3 trades (100%)."
    assert lines["t1First"]["text"] == "T1 was reached before the stop on 0 of 3 trades (0%)."


def test_what_to_improve_judges_from_ten_trades_and_splits_tickers_for_all(world):
    at = world.now.replace(hour=10, minute=5)
    nvda = [closed("NVDA-CALL", i, 100.0, at, hits=["T1"]) for i in range(6)]
    spy = [closed("SPY-PUT", i, -40.0, at.replace(hour=9, minute=40), side="PUT", ticker="SPY",
                  events=["Stop: stock traded through 600.00, sold 3 at $0.70."], held=20) for i in range(5)]
    doc = ticker_journal.build([run("NVDA-CALL", nvda), run("SPY-PUT", spy, "SPY", "PUT", 5000.0)], "all", now=world.now)
    lines = {x["key"]: x for x in doc["improve"]}
    assert doc["label"] == "All tickers" and doc["startBalance"] == 15000.0 and doc["balance"] == 15400.0
    assert lines["t1First"]["enough"] is True and lines["t1First"]["judge"] == "" and lines["t1First"]["trades"] == 11
    assert lines["t1First"]["text"] == "T1 was reached before the stop on 6 of 11 trades (54.5%)."
    assert lines["lossSide"]["text"] == "Most losing trades were Puts: 5 of 5 losing trades, net -$200.00."
    assert lines["lossSide"]["judge"] == "Too few trades to judge"              # only 5 losing trades behind it
    assert lines["lossTicker"]["text"] == "Most losing trades were on SPY: 5 of 5 losing trades, net -$200.00."
    assert lines["callsPuts"]["text"] == "Calls: 6 trades, 6 won, net +$600.00. Puts: 5 trades, 0 won, net -$200.00."
    tickers = {r["key"]: (r["trades"], r["pnl"]) for r in doc["breakdowns"]["ticker"]}
    assert tickers == {"NVDA": (6, 600.0), "SPY": (5, -200.0)}
    held = {r["key"]: r["trades"] for r in doc["breakdowns"]["hold"]}
    assert held["to15"] == 6 and held["to60"] == 5
    assert {r["key"]: r["trades"] for r in doc["breakdowns"]["time"]}["open"] == 5


def test_an_unknown_run_is_refused(world):
    trader = world.trader()
    with pytest.raises(TraderError) as err:
        trader.journal("AAPL-CALL")
    assert err.value.status == 404
    assert trader.journal("all")["stats"]["n"] == 0 and trader.journal("all")["improve"] == []


# ---------------------------------------------------------------- notes
def test_notes_are_plain_text_size_limited_kept_on_the_server_and_cleared_by_reset(world):
    trader = two_trades(world)
    first = run_of(trader)["trades"][0]["id"]
    saved = trader.save_note(first, "  Chased the open.\r\nWait\x07 next time‮  ")
    assert saved["text"] == "Chased the open.\nWait next time" and saved["at"]
    again = world.trader()                                       # a restart reads it back from the file
    card = next(t for t in again.journal("NVDA-CALL")["trades"] if t["id"] == first)
    assert card["note"] == "Chased the open.\nWait next time"
    for bad, status in ((("x" * 2001), 400), (["list"], 400)):
        with pytest.raises(TraderError) as err:
            trader.save_note(first, bad)
        assert err.value.status == status
    with pytest.raises(TraderError) as err:
        trader.save_note("NVDA-CALL-9-1", "hello")
    assert err.value.status == 404 and "not in the Ticker trader journal" in err.value.message
    assert trader.save_note(first, "x" * 2000)["text"] == "x" * 2000
    assert trader.save_note(first, "   ")["text"] == ""        # empty clears it
    assert next(t for t in trader.journal("all")["trades"] if t["id"] == first)["note"] == ""
    trader.save_note(first, "keep")
    trader.reset("NVDA-CALL")
    stored = json.loads((world.dir / "notes.json").read_text(encoding="utf-8"))
    assert first not in stored


# ---------------------------------------------------------------- the AI review
def test_the_ai_is_asked_once_per_run_per_day_and_the_paragraph_is_cached(world):
    trader = two_trades(world)
    ask = Ask()
    out = trader.review("NVDA-CALL", ask=ask)
    assert out["status"] == "done" and out["cached"] is False
    assert out["review"]["text"] == GOOD and out["review"]["label"] == "AI review, can be wrong. Only you see this."
    assert out["review"]["asOf"] == "written 10:01 AM ET from 2 closed trades"
    for _ in range(5):
        assert trader.review("NVDA-CALL", ask=ask)["review"]["text"] == GOOD
    assert len(ask.calls) == 1
    facts = ask.calls[0]["FACTS"]
    assert "Net: +$290.00. Average win: +$470.00. Average loss: -$180.00." in facts
    assert "Outcome Reached T3: 1 trades, 1 won, net +$470.00." in facts
    assert any(f.startswith("This week (week of 2026-11-09): 2 trades") for f in facts)
    # The journal read shows today's paragraph and never asks the AI itself.
    assert trader.journal("NVDA-CALL")["review"]["text"] == GOOD and len(ask.calls) == 1
    # "All tickers" is its own review; a new ET day may ask again.
    trader.review("all", ask=ask)
    assert len(ask.calls) == 2
    world.now += timedelta(days=1)
    assert trader.journal("NVDA-CALL")["review"] is None
    trader.review("NVDA-CALL", ask=ask)
    assert len(ask.calls) == 3


@pytest.mark.parametrize("ask", [
    Ask("You should buy more calls before 10:00 next week."),               # advice
    Ask("NVDA Call has 2 closed trades with net +$9,999.00."),               # a number the journal does not have
    Ask("NVDA Call did worse than AMD: 2 closed trades."),                   # a ticker the journal does not name
    Ask("NVDA Call has 2 closed trades, see nvda-journal.news for more."),   # a link
    Ask(ok=False), Ask(boom=True),                                          # the AI refused or broke
])
def test_a_failed_or_unsafe_reply_shows_nothing_and_is_not_retried_that_day(world, ask):
    trader = two_trades(world)
    out = trader.review("NVDA-CALL", ask=ask)
    assert out["review"] is None and out["status"] == "tried"
    assert trader.review("NVDA-CALL", ask=ask)["status"] == "tried"
    assert len(ask.calls) == 1 and trader.journal("NVDA-CALL")["review"] is None


def test_skipped_quietly_when_off_resting_over_allowance_or_nothing_to_review(world, monkeypatch):
    from momx import chat_limits
    monkeypatch.setattr(chat_ai, "_default_ask", lambda *a: pytest.fail("the AI must not be asked"))
    empty = World(world.dir.parent / "empty").trader()
    empty.start("NVDA", "CALL")
    assert empty.review("NVDA-CALL", ask=Ask())["why"] == "No closed trades yet."
    trader = two_trades(world)
    monkeypatch.setenv("AGX_CHAT_AI", "0")
    assert trader.review("NVDA-CALL")["why"] == "The AI is off."
    monkeypatch.setenv("AGX_CHAT_AI", "1")
    monkeypatch.setitem(chat_ai._REST, "until", 1e18)
    monkeypatch.setitem(chat_ai._REST, "why", "the AI service did not answer")
    assert trader.review("NVDA-CALL")["why"] == "The AI is not answering right now."
    monkeypatch.setitem(chat_ai._REST, "until", 0.0)
    monkeypatch.setattr(chat_limits.LIMITS, "ai_call_allowed", lambda scope: False)
    out = trader.review("NVDA-CALL")
    assert out == {"review": None, "status": "skipped", "why": "Today's AI allowance is used up.", "cached": False}
    # Nothing was recorded: once the AI is back, today's one review can still be written.
    ask = Ask()
    assert trader.review("NVDA-CALL", ask=ask)["status"] == "done" and len(ask.calls) == 1


def test_the_real_path_uses_the_owner_share_and_does_not_rest_the_ai(world, monkeypatch):
    seen = []
    monkeypatch.setattr(chat_ai, "_bounded", lambda ask, system, user, schema, seconds, scope="owner", rest=True:
                        seen.append((ask, scope, rest, seconds)) or {"ok": True, "data": {"summary": GOOD}})
    trader = two_trades(world)
    trader.review("NVDA-CALL")
    assert seen == [(chat_ai._default_ask, "owner", False, ticker_journal.REVIEW_SECONDS)]


def test_the_journal_module_imports_no_broker_code():
    source = Path(ticker_journal.__file__).read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported |= {f"{node.module}.{a.name}" for a in node.names}
    allowed = {"__future__.annotations", "json", "os", "re", "collections.defaultdict", "datetime.datetime",
               "pathlib.Path", "typing.Any", "typing.Callable", "typing.Mapping", "zoneinfo.ZoneInfo", "momx.bot_desk",
               "momx.chat_ai", "momx.chat_limits", "momx.chat_summary"}
    assert imported <= allowed, imported - allowed
    low = source.lower()
    for word in ("alpaca", "schwab", "submit_order", "place_order", "paper_mirror", "requests", "urllib"):
        assert word not in low, word


def test_reset_drops_todays_review_for_that_run_and_for_all(world):
    """A reset clears the history, so the AI review written about it must go too (2026-11-13 review)."""
    trader = two_trades(world)
    ask = Ask()
    assert trader.review("NVDA-CALL", ask=ask)["status"] == "done"
    assert trader.review(ticker_journal.ALL, ask=ask)["status"] == "done"
    assert trader.journal("NVDA-CALL")["review"] is not None
    trader.reset("NVDA-CALL", 5000)
    assert trader.journal("NVDA-CALL")["review"] is None
    assert trader.journal(ticker_journal.ALL)["review"] is None
    saved = json.loads((world.dir / ticker_journal.REVIEWS_FILE).read_text(encoding="utf-8"))
    assert saved["entries"]["NVDA-CALL"]["status"] == "tried" and saved["entries"][ticker_journal.ALL]["status"] == "tried"
    # A reset never buys a second AI call the same day.
    assert trader.review("NVDA-CALL", ask=ask)["status"] == "tried" and len(ask.calls) == 2
