"""Unit: momx/market_events.py and the chat's my_trades / market_events tools (2026-11-11)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import chat_tools, market_events as me, schwab_history

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 11, 17, 0, tzinfo=ET)
FOREX = {"weeklyEvents": [
    {"title": "CPI m/m", "time": "2026-11-13T08:30:00-05:00", "date": "2026-11-13", "impact": "Medium", "forecast": "0.3%"},
    {"title": "Crude Oil Inventories", "time": "2026-11-12T10:30:00-05:00", "date": "2026-11-12", "impact": "Medium"},
    {"title": "FOMC Meeting Minutes", "time": "2026-11-12T14:00:00-05:00", "date": "2026-11-12", "impact": "High"},
    {"title": "Old news", "time": "2026-11-10T08:30:00-05:00", "date": "2026-11-10", "impact": "High"},
]}
EARNINGS = {"rows": [{"symbol": "NVDA", "date": "2026-11-18", "timing": "After close"}, {"symbol": "WMT", "date": "2026-11-25"}]}


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_INSIGHTS_DIR", str(tmp_path / "insights"))
    return tmp_path


def test_refresh_keeps_high_impact_and_key_events_for_7_days():
    doc = me.refresh(lambda: FOREX, lambda: EARNINGS, NOW)
    assert [e["title"] for e in doc["items"]] == ["FOMC Meeting Minutes", "CPI m/m", "NVDA earnings (After close)"]
    assert doc["items"][1]["impact"] == "High"              # CPI counts as high impact whatever the feed says
    read = me.read(NOW)
    assert len(read["items"]) == 3 and "this week only" in read["note"]
    assert me.read(datetime(2026, 11, 13, 9, 0, tzinfo=ET))["items"][0]["title"] == "CPI m/m"      # past days drop off


def test_refreshed_at_most_every_6_hours():
    assert me.due(NOW)
    me.refresh(lambda: FOREX, lambda: EARNINGS, NOW)
    assert not me.due(NOW.replace(hour=22, minute=59)) and me.due(NOW.replace(hour=23))


def test_a_calendar_that_fails_is_said_not_hidden():
    def broken():
        raise TimeoutError
    doc = me.refresh(broken, lambda: EARNINGS, NOW)
    assert [e["kind"] for e in doc["items"]] == ["earnings"] and "economic calendar unavailable" in doc["note"].lower()
    assert "economic calendar unavailable" in me.read(NOW)["note"].lower()


def test_never_read_says_so(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_INSIGHTS_DIR", str(tmp_path / "empty"))
    assert me.read(NOW) == {"items": [], "note": "Market events have not been read yet.", "refreshedAt": None, "source": ""}


def test_chat_lines_find_one_event():
    me.refresh(lambda: FOREX, lambda: EARNINGS, NOW)
    doc = me.read(NOW)
    assert me.chat_lines(doc, "CPI")[0] == "11-13 08:30 ET CPI m/m (forecast 0.3%)"
    assert me.chat_lines(doc, "PPI")[0] == "No PPI on the saved calendar for the next 7 days."


# ---------------------------------------------------------------- the chat tools
def ctx(scope, artifacts):
    return {"scope": scope, "account": "x", "now": NOW, "artifacts": artifacts}


def test_my_trades_is_owner_only(tmp_path):
    assert "my_trades" in chat_tools.registry("owner")
    for scope in ("scanner", "user", "", "all"):
        assert "my_trades" not in chat_tools.registry(scope)
        assert "my_trades" not in {t["tool"] for t in chat_tools.planner_catalog(scope)}
        assert chat_tools.run("my_trades", {"topic": "summary"}, ctx(scope, tmp_path))["lines"] == ["I cannot look that up here."]
    # The handler checks again on its own.
    assert chat_tools.OWN_TOOLS["my_trades"]["handler"]({}, ctx("scanner", tmp_path))["lines"] == ["I cannot look that up here."]


def test_my_trades_for_the_owner_reads_the_saved_store_only(tmp_path):
    fill = lambda i, at, side, price: {"id": i, "orderId": i, "account": "…5678", "at": at, "day": at[:10], "underlying": "SPY",
                                       "optionSymbol": "SPY   261113P00700000", "assetType": "OPTION", "putCall": "PUT", "strike": 700.0,
                                       "expiry": "2026-11-13", "dte": 2, "side": side, "quantity": 1.0, "price": price, "fees": 0.0,
                                       "multiplier": 100.0, "kind": "trade"}
    schwab_history.save_store({"fills": {"a": fill("a", "2026-11-11T09:45:00-05:00", "BUY", 1.0), "b": fill("b", "2026-11-11T10:00:00-05:00", "SELL", 1.3)}})
    out = chat_tools.run("my_trades", {"topic": "puts", "days": 30}, ctx("owner", tmp_path))
    assert out["lines"][0].startswith("Puts: 1 trades, +$30 after fees, win rate 100%")
    assert out["links"] == [{"label": "Open My Trading", "view": "My Trading"}] and out["facts"]["days"] == 30


def test_market_events_is_for_everyone(tmp_path):
    me.refresh(lambda: FOREX, lambda: EARNINGS, NOW)
    for scope in ("owner", "scanner"):
        assert "market_events" in chat_tools.registry(scope)
        out = chat_tools.run("market_events", {"event": "FOMC"}, ctx(scope, tmp_path))
        assert out["lines"][0] == "11-12 14:00 ET FOMC Meeting Minutes"
        assert [link["view"] for link in out["links"]] == ["News Feed", "Earnings Calendar"]


# ---------------------------------------------------------------- review fixes (2026-11-12)
#: What api_server's forex_factory_us_news_payload answers when the fetch fails: it does not raise.
FF_FAILED = {"live": False, "source": "Forex Factory calendar", "events": [], "weeklyEvents": [],
             "errors": ["Forex Factory calendar unavailable: HTTPError."]}


def test_a_failed_forex_factory_reply_is_not_an_empty_calendar():
    doc = me.refresh(lambda: FF_FAILED, lambda: {"rows": []}, NOW)
    assert doc["economic"] is False and "not available" in doc["note"]
    read = me.read(NOW)
    assert read["economicError"] and "this week only" not in read["note"]
    lines = me.chat_lines(read, "CPI")
    assert lines[0] == "CPI: the economic calendar is not available right now (Forex Factory did not answer)."
    assert not any(line.startswith("No CPI") for line in lines)
    assert me.chat_lines(read, None)[0].startswith("The economic calendar is not available right now")
    # A ticker's earnings question is not answered with the economic calendar's state.
    assert me.chat_lines(read, "NVDA")[0] == "No NVDA on the saved calendar for the next 7 days."
    # "live": False alone (no rows read) also counts as not available.
    assert me.economic_failed({"weeklyEvents": [], "errors": [], "live": False}) and not me.economic_failed(FOREX)


def test_a_failed_fetch_keeps_the_last_good_economic_events():
    me.refresh(lambda: FOREX, lambda: EARNINGS, NOW)
    doc = me.refresh(lambda: FF_FAILED, lambda: EARNINGS, NOW.replace(hour=23, minute=30))
    assert [e["title"] for e in doc["items"]] == ["FOMC Meeting Minutes", "CPI m/m", "NVDA earnings (After close)"]
    assert doc["economic"] is False and "from the copy read at 11-11 17:00 ET" in doc["note"]
    assert me.chat_lines(me.read(NOW.replace(hour=23, minute=30)), "CPI")[0] == "11-13 08:30 ET CPI m/m (forecast 0.3%)"


JOBS_AND_FED = {"weeklyEvents": [
    {"title": "Non-Farm Employment Change", "time": "2026-11-14T08:30:00-05:00", "date": "2026-11-14", "impact": "High"},
    {"title": "Unemployment Rate", "time": "2026-11-14T08:30:00-05:00", "date": "2026-11-14", "impact": "High"},
    {"title": "Federal Funds Rate", "time": "2026-11-12T14:00:00-05:00", "date": "2026-11-12", "impact": "High"},
    {"title": "FOMC Meeting Minutes", "time": "2026-11-12T14:00:00-05:00", "date": "2026-11-12", "impact": "High"},
]}


@pytest.mark.parametrize("asked", ["jobs", "jobs report", "NFP", "payrolls", "employment"])
def test_the_jobs_report_is_found_by_the_words_traders_use(asked):
    me.refresh(lambda: JOBS_AND_FED, lambda: {"rows": []}, NOW)
    lines = me.chat_lines(me.read(NOW), asked)
    assert lines[0] == "11-14 08:30 ET Non-Farm Employment Change" and "11-14 08:30 ET Unemployment Rate" in lines
    assert not any("Federal Funds" in line for line in lines)


@pytest.mark.parametrize("asked", ["FOMC", "Fed", "rate decision"])
def test_fomc_includes_the_rate_decision(asked):
    me.refresh(lambda: JOBS_AND_FED, lambda: {"rows": []}, NOW)
    lines = me.chat_lines(me.read(NOW), asked)
    assert "11-12 14:00 ET Federal Funds Rate" in lines and "11-12 14:00 ET FOMC Meeting Minutes" in lines
    assert not any("Non-Farm" in line for line in lines)


def test_inflation_finds_cpi():
    me.refresh(lambda: FOREX, lambda: EARNINGS, NOW)
    assert me.chat_lines(me.read(NOW), "inflation")[0] == "11-13 08:30 ET CPI m/m (forecast 0.3%)"
