"""Integration: open interest 1 to 12 months out through the real POST /api/bot-chat route (2026-11-16).

Real api_server route, the real auth gate and the real chat modules. Schwab is the one thing faked
(momx.long_oi.fetch), and the AI must never be asked: the words are certain. The copy lands in a scratch folder.
Set CHAT_LONG_OI_CAPTURE to a file path to save the regular user's reply for the browser spec."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chat_ai, chat_limits, long_oi
from test_momx_long_oi import ET, chain

OWNER = "owner-longoi@example.com"
VIEWER = "viewer-longoi@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/bot-chat"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Schwab faked, no AI allowed, the copy in a scratch folder. Returns what was asked of each."""
    asked = {"schwab": [], "ai": []}
    today = datetime.now(ET).date()

    def fetch(ticker, day):
        asked["schwab"].append(ticker)
        # The fixture's chain is laid out from 2026-11-16; move it to today so the windows match.
        doc = chain()
        shift = (today - datetime(2026, 11, 16).date()).days
        for key in ("callExpDateMap", "putExpDateMap"):
            doc[key] = {f"{(datetime.fromisoformat(k[:10]).date() + timedelta(days=shift)).isoformat()}:{k[11:]}": v for k, v in doc[key].items()}
        return doc

    def no_ai(system, user, schema):
        asked["ai"].append(system)
        return {"ok": False, "error": "the test allows no AI call"}

    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setattr(long_oi, "fetch", fetch)
    monkeypatch.setattr(chat_ai, "_default_ask", no_ai)
    long_oi.reset()
    chat_limits.LIMITS.reset()
    chat_ai.clear_plans()
    yield asked
    long_oi.reset()
    chat_limits.LIMITS.reset()


@pytest.mark.parametrize("headers", [ADMIN, REGULAR], ids=["owner", "regular user"])
def test_his_leap_question_gets_the_strikes_with_the_most_oi_and_their_delta(world, headers, tmp_path):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "Nvda check the leap which one had more OI",
                                        "context": {"pageTicker": "NVDA", "pageName": "Charts & OI"}}, headers=headers)
        again_status, again = api.post(ROUTE, {"question": "I want any stocks 1 months to 12 months which strikes have high oi with delta",
                                               "context": {"pageTicker": "NVDA", "pageName": "Charts & OI"}}, headers=headers)
    assert status == 200 and again_status == 200, (body, again)
    text = "\n".join(body["lines"])
    assert body["ticker"] == "NVDA"
    assert "expiries 3 to 12 months out" in text and "Most call OI: 185 call" in text and "delta 0.31" in text, text
    assert body["table"]["columns"][4]["label"] == "Delta" and body["table"]["rows"][0] == {
        **body["table"]["rows"][0], "contract": "185 Call", "oi": "108,750", "delta": "0.31"}
    assert "expiries 1 to 12 months out" in "\n".join(again["lines"]) and "Most call OI: 200 call" in "\n".join(again["lines"])
    assert world["schwab"] == ["NVDA"]                     # the second question read the 30-minute copy
    assert world["ai"] == []                               # certain words: no AI
    assert (tmp_path / "long_oi_cache" / "NVDA.json.gz").exists()


def test_leaps_which_strike_is_good_gets_the_oi_strikes_and_no_pick(world):
    """2026-11-19: "See nvda leaps which strike is good?" got the Long Term report with no strike in it."""
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "See nvda leaps which strike is good?",
                                        "context": {"pageTicker": "NVDA", "pageName": "Options"}}, headers=REGULAR)
    text = "\n".join(body["lines"])
    assert status == 200 and body["ticker"] == "NVDA", body
    assert "expiries 3 to 12 months out" in text and "Most call OI: 185 call" in text, text
    assert world["ai"] == []


def test_a_question_with_no_ticker_anywhere_asks_for_one(world):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "which strikes have high oi 1 to 12 months with delta", "context": {}}, headers=REGULAR)
    assert status == 200 and "Which ticker" in body["lines"][0] and world["schwab"] == []


def test_signed_out_gets_nothing_from_schwab(world):
    with running_api() as api:
        status, _ = api.post(ROUTE, {"question": "Nvda leap oi", "context": {}}, headers=RELAYED)
    assert status in (401, 403) and world["schwab"] == []


def test_capture_the_real_reply_for_the_browser_spec(world):
    out = os.environ.get("CHAT_LONG_OI_CAPTURE")
    if not out:
        pytest.skip("only when the browser spec asks for the real reply")
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "Nvda check the leap which one had more OI", "context": {"pageTicker": "NVDA"}},
                                headers=REGULAR)
    assert status == 200
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(body, handle)



def _saved_board(tmp_path, changes, prev_day="2026-11-18T20:04:00-05:00"):
    """The LEAP OI Scanner's saved copy of NVDA as the page leaves it: today's open interest (from the chat's own
    copy, so both describe one posting) and yesterday's, `changes` contracts lower."""
    import gzip
    from momx import leap_oi_scanner
    full = long_oi._read_disk(tmp_path, "NVDA")
    saved = leap_oi_scanner.compact(full)
    prev = {key: oi - changes.get(key, 0) for key, oi in saved["allOi"].items()}
    saved.update({"prevOi": prev, "prevAt": prev_day})
    (tmp_path / "leap_oi_board").mkdir(exist_ok=True)
    with gzip.open(tmp_path / "leap_oi_board" / "NVDA.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(saved, handle)
    return saved


def test_new_open_interest_since_yesterday_goes_on_top_with_a_change_column(world, tmp_path):
    """2026-11-19 "what change column do it will show new added top of that?": the biggest NEW open interest since the
    previous reading heads the answer and the table, and every row says how much it changed."""
    from momx import leap_oi_scanner
    with running_api() as api:
        api.post(ROUTE, {"question": "Nvda leap oi", "context": {"pageTicker": "NVDA"}}, headers=REGULAR)   # the chat's copy
        full = long_oi._read_disk(tmp_path, "NVDA")
        calls = sorted((r for r in full["rows"] if r["side"] == "CALL" and r["days"] >= 88 and abs(r["delta"] or 0) >= 0.1 and r["oi"] > 3000),
                       key=lambda r: r["oi"])
        jump, small = calls[0], calls[-1]
        _saved_board(tmp_path, {leap_oi_scanner.contract_key(jump): 3000, leap_oi_scanner.contract_key(small): -500})
        status, body = api.post(ROUTE, {"question": "Nvda leap oi buyers or sellers?", "context": {"pageTicker": "NVDA"}}, headers=REGULAR)
    assert status == 200, body
    assert "Each one has a buyer and a seller" in body["lines"][0], body["lines"]
    said = "\n".join(body["lines"])
    strike = f"{jump['strike']:g}"
    assert f"Biggest new open interest since the Nov 18 2026 reading: {strike} call" in said and "+3,000" in said, said
    assert "cannot show whether they were bought or sold" in said
    columns = [c["label"] for c in body["table"]["columns"]]
    assert columns == ["Contract", "Expiry", "Days", "OI", "Change", "Delta", "Bid", "Ask"]
    top = body["table"]["rows"][0]
    assert top["contract"].startswith(f"{strike} Call · NEW") and top["change"] == "+3,000", top
    changed = {r["contract"].split(" ·")[0] + r["expiry"]: r["change"] for r in body["table"]["rows"]}
    assert changed[f"{small['strike']:g} Call{small['expiry']}"] == "-500"
    assert sum(1 for r in body["table"]["rows"] if "NEW" in r["contract"]) == 1      # under 1,000 is not "new"
    assert len({(r["contract"].replace(" · NEW", ""), r["expiry"]) for r in body["table"]["rows"]}) == len(body["table"]["rows"])
    assert "NEW rows first" in body["table"]["note"]
    assert world["ai"] == []


def test_a_saved_copy_from_another_posting_gives_no_change(world, tmp_path):
    """When the LEAP board's copy holds different open interest than the chat's (another posting), no change is made up."""
    from momx import leap_oi_scanner
    with running_api() as api:
        api.post(ROUTE, {"question": "Nvda leap oi", "context": {"pageTicker": "NVDA"}}, headers=REGULAR)
        saved = _saved_board(tmp_path, {})
        import gzip
        saved["allOi"] = {key: oi + 7 for key, oi in saved["allOi"].items()}            # another day's numbers
        with gzip.open(tmp_path / "leap_oi_board" / "NVDA.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(saved, handle)
        status, body = api.post(ROUTE, {"question": "Nvda leap oi", "context": {"pageTicker": "NVDA"}}, headers=REGULAR)
    assert status == 200
    assert "Change" not in [c["label"] for c in body["table"]["columns"]] and "Biggest new" not in "\n".join(body["lines"])
    assert not any("NEW" in r["contract"] for r in body["table"]["rows"])


def test_the_leap_answer_says_what_traded_today_bought_or_sold(world, tmp_path):
    """2026-11-20 "I have to search agx flow ... I feel little confusion": the LEAP answer carries today's trades on its
    strikes (AGX Flow's copy of the same contracts), so the trader learns bought or sold in one place."""
    import gzip
    from momx import agx_flow
    agx_flow.reset()
    with running_api() as api:
        api.post(ROUTE, {"question": "Nvda leap oi", "context": {"pageTicker": "NVDA"}}, headers=REGULAR)
        full = long_oi._read_disk(tmp_path, "NVDA")
        row = max((r for r in full["rows"] if r["side"] == "CALL" and r["days"] >= 88 and abs(r["delta"] or 0) >= 0.1), key=lambda r: r["oi"])
        flow = {"schema": agx_flow.SCHEMA, "symbol": "NVDA", "fetchedAt": full["fetchedAt"], "spot": full.get("spot"),
                "maxDays": agx_flow.FETCH_MAX_DAYS, "contracts": [
                    {"side": "CALL", "expiry": row["expiry"], "days": row["days"], "strike": row["strike"], "volume": 1200,
                     "oi": row["oi"], "last": 3.15, "bid": 3.0, "ask": 3.15, "mark": 3.08, "delta": row["delta"], "iv": 40.0,
                     "newVolume": 0}]}
        (tmp_path / "agx_flow_cache").mkdir(exist_ok=True)
        with gzip.open(tmp_path / "agx_flow_cache" / "NVDA.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(flow, handle)
        status, body = api.post(ROUTE, {"question": "Nvda leap oi buyers or sellers?", "context": {"pageTicker": "NVDA"}}, headers=REGULAR)
    agx_flow.reset()
    assert status == 200, body
    said = "\n".join(body["lines"])
    strike = f"{row['strike']:g}"
    assert f"Traded today (the app's guess from where the last trade printed: at the ask = Bought, at the bid = Sold): {strike} call" in said, said
    assert "1,200 contracts, about $370K, Bought" in said, said
    assert "Traded today" in [c["label"] for c in body["table"]["columns"]]
    cells = {r["contract"].split(" ·")[0] + r["expiry"]: r["today"] for r in body["table"]["rows"]}
    assert cells[f"{strike} Call{row['expiry']}"] == "1,200 Bought"
    assert "Delta = roughly the chance" in said
    assert world["ai"] == []
