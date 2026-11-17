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
