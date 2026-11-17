"""Integration: the Ask chat's market tools inside the real server, on scratch files.

Real api_server and STATE, the real news store on the scratch database, the real
High OI board code and POST /api/bot-chat over HTTP. Only the AI is faked. The
harness guards refuse anything that would leave the PC or write a live file.

The spy test is the point of this file: asking the chat must never start a Schwab
call, a chart build, a refresh or a phone push.
"""
from __future__ import annotations

import gzip
import json
import re
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api_harness import RELAYED, api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from data import schwab_client
from long_term import service as long_term_service
from momx import chat_ai, chat_limits, chat_market, chat_state, chat_tools, long_oi
from news_feed_store import MomxNewsStore

ET = ZoneInfo("America/New_York")
OWNER = "owner-market@example.com"
VIEWER = "viewer-market@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/bot-chat"
MARKET_TOOLS = ("chain", "walls", "expected_move", "max_pain", "sectors", "premarket", "news", "earnings",
                "long_term", "morning_brief", "quote")
#: What the chat must never reach. Each of these goes to Schwab or Finnhub, builds
#: a chart or sends a phone push.
FORBIDDEN_STATE_METHODS = ("oi_finder_payload", "oi_finder_chart_payload", "premarket_scanner_payload",
                           "earnings_calendar_payload", "momx_news_refresh", "momx_news_latest")
STACKS: list[str] = []      # where each forbidden call came from, for the failure message
_CHAT_FILE = re.compile(r"[\\/]momx[\\/]chat")


def _next_weekday(start, weekday):
    return start + timedelta(days=(weekday - start.weekday()) % 7 or 7)


def _row(side, strike, expiry, oi):
    return {"side": side, "strike": strike, "expiry": expiry, "open_interest": oi, "bid": 1.0, "ask": 1.2, "delta": 0.4,
            "volume": 5, "days_to_expiration": 3, "mark": 1.1}


def _chain(symbol, now):
    friday = _next_weekday(now.date(), 4).isoformat()
    later = (_next_weekday(now.date(), 4) + timedelta(days=7)).isoformat()
    rows = [_row("CALL", 110, friday, 900), _row("CALL", 105, friday, 500), _row("PUT", 95, friday, 700),
            _row("PUT", 90, friday, 300), _row("CALL", 95, friday, 10), _row("PUT", 110, friday, 10),
            _row("CALL", 100, friday, 50), _row("PUT", 100, friday, 50)]
    return {"symbol": symbol, "optionQuoteSchemaVersion": 1, "live": True, "scannedAt": now.isoformat(),
            "underlyingPrice": 100.0, "impliedVolatility": 29.0, "todayChangePercent": 1.5,
            "expiryExpectedMoves": {friday: 4.0, later: 6.0}, "selectedExpiryChainRows": rows}


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch):
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "no AI in tests"},
                        raising=False)
    chat_limits.LIMITS.reset()
    yield
    chat_limits.LIMITS.reset()


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Saved files, the news store and the earnings cache, as a running app holds them."""
    now = datetime.now(ET)
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setenv("AGX_LONG_TERM_DIR", str(tmp_path / "long_term"))
    monkeypatch.setattr(long_term_service, "_INSTANCE", None)
    chat_state.unbind()
    chains = tmp_path / "oi_chain_cache"
    chains.mkdir()
    for symbol in ("NVDA", "SPY"):
        with gzip.open(chains / f"{symbol}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(_chain(symbol, now), handle)
    day = now.date().isoformat()
    (tmp_path / "momx_sector_hot").mkdir()
    (tmp_path / "momx_sector_hot" / f"{day}.json").write_text(json.dumps({"date": day, "boards": {"Watchlist": {
        "hot": ["Telecom"], "leaders": {"VZ": {"rank": 1, "sector": "Telecom", "at": now.isoformat(), "pct": 0.1}}}}}), encoding="utf-8")
    (tmp_path / "premarket_scanner_history").mkdir()
    (tmp_path / "premarket_scanner_history" / f"{day}.json").write_text(json.dumps({
        "date": day, "briefing": {"lines": ["SPY flat."], "generatedAt": now.isoformat()},
        "rows": {"NVDA": {"firstSeenAt": now.isoformat(), "lastSeenAt": now.isoformat(),
                          "row": {"symbol": "NVDA", "lastPrice": 101.0, "changePct": 1.0, "signals48": ["CALL4H"], "strength": "WEAK"}}}}),
        encoding="utf-8")
    state = api_server.STATE
    monkeypatch.setattr(state, "oi_finder_chain_cache", {})
    payload = {"rows": [{"symbol": "NVDA", "date": (now.date() + timedelta(days=8)).isoformat(), "timing": "After close"}],
               "refreshedAt": now.isoformat(), "endDate": (now.date() + timedelta(days=30)).isoformat()}
    monkeypatch.setattr(state, "earnings_calendar_cache", (now, ("NVDA", "AAPL"), payload))
    state.repository.log_catalysts([{"symbol": "NVDA", "headline": "Chipmaker announces a thing", "source": "Wire",
                                     "url": "https://example.test/a", "published_at": now.astimezone(ZoneInfo("UTC")).isoformat()}])
    return tmp_path


@pytest.fixture
def spy(monkeypatch):
    """Every way out to Schwab, a build or a push raises and is recorded."""
    calls: list[str] = []
    STACKS.clear()

    def trip(name):
        def raised(*_args, **_kwargs):
            # The app's own background workers (the OI auto-alert loop) also call
            # these. Only a call that came through the chat's code counts.
            if any(_CHAT_FILE.search(frame.filename) for frame in traceback.extract_stack()):
                calls.append(name)
                STACKS.append("".join(traceback.format_stack(limit=10)))
            raise AssertionError(f"the chat called {name}")
        return raised

    for name in FORBIDDEN_STATE_METHODS:
        monkeypatch.setattr(api_server.DashboardState, name, trip(name))
    monkeypatch.setattr(schwab_client.SchwabClient, "__init__", trip("SchwabClient()"))
    monkeypatch.setattr(schwab_client.SchwabClient, "get_quotes", trip("SchwabClient.get_quotes"))
    monkeypatch.setattr(schwab_client.SchwabClient, "get_option_chain", trip("SchwabClient.get_option_chain"))
    monkeypatch.setattr(api_server, "_schwab_market_clients", trip("_schwab_market_clients"))
    monkeypatch.setattr(MomxNewsStore, "refresh", trip("MomxNewsStore.refresh"))
    monkeypatch.setattr(long_term_service.LongTermService, "quotes_for", trip("LongTermService.quotes_for"))
    return calls


def _ctx(scope="scanner", **over):
    return {"scope": scope, "account": VIEWER, "pageTicker": None, "pageName": None, "ticker": None, "day": None,
            "side": None, "now": datetime.now(ET), **over}


def test_market_tools_are_offered_to_every_session():
    for scope in ("owner", "scanner"):
        assert set(MARKET_TOOLS) <= set(chat_tools.registry(scope))


def test_no_chat_tool_starts_schwab_a_build_or_a_push(scratch, spy, monkeypatch):
    """Every tool in the registry, owner tools too, inside the running server.

    One exception since 2026-11-16: long_oi (open interest 1 to 12 months out) has no saved chain to read, so it may
    ask Schwab, and ONLY through momx.long_oi.fetch (one guarded request per ticker, see the next test). Here that
    one door is replaced by a recorder, so any other way out still counts, for long_oi too."""
    through_the_door: dict[str, int] = {}
    current = {"name": None}

    def door(ticker, today):
        through_the_door[current["name"]] = through_the_door.get(current["name"], 0) + 1
        return {}
    monkeypatch.setattr(long_oi, "fetch", door)
    long_oi.reset()
    with running_api():
        names = sorted(chat_tools.registry("owner"))
        assert set(MARKET_TOOLS) <= set(names)
        offenders: dict[str, list[str]] = {}
        for name in names:
            current["name"] = name
            for args in ({"ticker": "NVDA"}, {"ticker": "NOPE"}, {}):
                before = len(spy)
                reply = chat_tools.run(name, args, _ctx("owner"))
                assert isinstance(reply["lines"], list), name
                if len(spy) > before:
                    offenders.setdefault(name, []).extend(spy[before:])
    long_oi.reset()
    assert offenders == {} and spy == [], (offenders, spy, STACKS[:2])
    assert set(through_the_door) == {"long_oi"}, through_the_door


def test_long_oi_reaches_schwab_once_per_ticker_even_when_it_fails(scratch, spy, monkeypatch):
    """The real long_oi.fetch, behind the spy: one SchwabClient per ticker, then the failure is remembered."""
    monkeypatch.setattr(long_oi, "fetch", long_oi._schwab_fetch)   # conftest shuts this door for every other test
    long_oi.reset()
    try:
        with running_api():
            for _ in range(3):
                reply = chat_tools.run("long_oi", {"ticker": "NVDA"}, _ctx())
                assert reply["table"] is None and "NVDA" in reply["lines"][0]
    finally:
        long_oi.reset()
    assert spy == ["SchwabClient()"]


@pytest.mark.parametrize("method", FORBIDDEN_STATE_METHODS)
def test_the_spy_catches_a_tool_that_breaks_the_rule(spy, monkeypatch, method):
    """Proves the test above can fail: a tool reaching for a forbidden call is recorded."""
    def naughty(args, ctx):
        getattr(api_server.STATE, method)()
    monkeypatch.setitem(chat_tools.OWN_TOOLS, "naughty", {"scope": "all", "description": "x", "args": {}, "handler": naughty})
    chat_tools.run("naughty", {}, _ctx())
    assert spy == [method]


def test_real_state_answers_through_the_registry(scratch, spy):
    """The real STATE, the real news store on the scratch database, the links as the server cleans them."""
    with running_api():
        chain = chat_tools.run("chain", {"ticker": "NVDA", "expiry": "friday"}, _ctx())
        walls = chat_tools.run("walls", {"ticker": "NVDA", "side": "put"}, _ctx())
        news = chat_tools.run("news", {"ticker": "NVDA"}, _ctx())
        earnings = chat_tools.run("earnings", {"ticker": "NVDA"}, _ctx())
        off_list = chat_tools.run("earnings", {"ticker": "SPY"}, _ctx())
        quote = chat_tools.run("quote", {"ticker": "NVDA"}, _ctx())
    assert chain["table"]["kind"] == "rows" and chain["table"]["rows"] and chain["asOf"].startswith("as of")
    assert chain["links"][0]["view"] == "Quick Options" and chain["links"][0]["ticker"] == "NVDA"
    assert "put wall: 95" in " ".join(walls["lines"]) and "call wall" not in " ".join(walls["lines"])
    assert walls["links"][0]["sub"] == "high-oi"
    assert "Chipmaker announces a thing" in " ".join(news["lines"]) and news["links"][0]["view"] == "News Feed"
    assert "reports" in " ".join(earnings["lines"])
    assert "not tracked" in " ".join(off_list["lines"])
    assert quote["facts"]["source"] == "option chain" and "old" in " ".join(quote["lines"])
    assert spy == []


def test_schema_version_matches_the_server():
    assert chat_market.CHAIN_SCHEMA_VERSION == api_server.OI_FINDER_CHAIN_QUOTE_SCHEMA_VERSION


def test_words_for_days_and_expiries_get_through_argument_checking(scratch):
    chain = chat_tools.run("expected_move", {"ticker": "NVDA", "expiry": "friday"}, _ctx())
    assert chain["facts"]["expectedMove"] == 4.0
    premarket = chat_tools.run("premarket", {"day": "today"}, _ctx())
    assert premarket["table"]["rows"][0]["symbol"] == "NVDA"


QUESTIONS = [
    ("option chain nvda", None, "NVDA option chain", "Quick Options"),
    ("Option chain", {"pageTicker": "SPY", "pageName": "Charts & OI"}, "SPY option chain", "Quick Options"),
    ("call wall on spy", None, "High OI board", "Quick Options"),
    ("biggest put wall spy", None, "put wall: 95", "Quick Options"),
    ("expected move nvda friday", None, "NVDA expected move", "Quick Options"),
    ("what is max pain for SPY", None, "worked out from saved OI, 80 strikes only", "Quick Options"),
    ("NVDA price", None, "old", "Charts & OI"),
    ("how much is NVDA now", None, "old", "Charts & OI"),
    ("which sector is hot", None, "Telecom", "Sectors"),
    ("premarket today", None, "premarket scanner", "Premarket Scanner"),
    ("any news on NVDA", None, "Chipmaker announces a thing", "News Feed"),
    ("when is NVDA earnings", None, "reports", "Earnings Calendar"),
    ("when is ZETA earnings", None, "not tracked", "Earnings Calendar"),
    ("long term nvda", None, "Long Term", "Long Term"),
]


@pytest.mark.parametrize("headers", [ADMIN, REGULAR], ids=["owner", "regular"])
@pytest.mark.parametrize("question,context,phrase,view", QUESTIONS, ids=[q[0] for q in QUESTIONS])
def test_question_over_http(scratch, spy, headers, question, context, phrase, view):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": question, **({"context": context} if context else {})}, headers=headers)
    assert status == 200, body
    assert phrase in " ".join(body["lines"]), body["lines"]
    assert view in [link["view"] for link in body["links"]], body["links"]
    assert spy == []


def test_bare_option_chain_with_no_page_asks_which_ticker(scratch, spy):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "Option chain"}, headers=REGULAR)
    assert status == 200
    assert "ticker" in " ".join(body["lines"]).lower() and not body.get("table")
    assert [link["view"] for link in body["links"]] == ["Quick Options"]


def test_chain_table_reaches_the_screen(scratch, spy):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "option chain nvda"}, headers=REGULAR)
    assert status == 200 and body["table"]["kind"] == "rows" and body["table"]["rows"]
    assert body["asOf"].startswith("as of")


def test_is_it_a_buy_gets_no_advice(scratch, spy):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "is NVDA a buy"}, headers=REGULAR)
    assert status == 200
    said = " ".join(body["lines"]).lower()
    assert "you should buy" not in said and "i recommend" not in said
