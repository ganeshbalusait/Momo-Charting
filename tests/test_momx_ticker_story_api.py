"""Integration: the ticker story through the real GET /api/ticker-story route (2026-11-20).

Real api_server route and auth gate, the real leap_oi_scanner + long_oi + agx_flow + ticker_story modules. Schwab is
the one thing faked (long_oi.fetch and agx_flow.fetch); the copies land in a scratch folder (AGX_MOMX_GRADE_DIR)."""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import agx_flow, leap_oi_scanner, long_oi
from test_momx_agx_flow import TODAY as FLOW_DAY, chain as flow_chain
from test_momx_long_oi import ET, chain as leap_chain

VIEWER = "viewer-story@example.com"
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/ticker-story"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(VIEWER, role="user")


def _shift(doc, start):
    shift = (datetime.now(ET).date() - start).days
    for key in ("callExpDateMap", "putExpDateMap"):
        doc[key] = {f"{(datetime.fromisoformat(k[:10]).date() + timedelta(days=shift)).isoformat()}:{k[11:]}": v
                    for k, v in doc.get(key, {}).items()}
    return doc


@pytest.fixture
def world(tmp_path, monkeypatch):
    asked = {"leap": [], "flow": []}

    def leap_fetch(ticker, day):
        asked["leap"].append(ticker)
        return _shift({**leap_chain(), "symbol": ticker}, datetime(2026, 11, 16).date())

    def flow_fetch(ticker, day):
        asked["flow"].append(ticker)
        return _shift(flow_chain(), FLOW_DAY)

    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setattr(leap_oi_scanner, "PACE_SECONDS", 0.0)
    monkeypatch.setattr(long_oi, "fetch", leap_fetch)
    monkeypatch.setattr(agx_flow, "fetch", flow_fetch)
    for module in (long_oi, leap_oi_scanner, agx_flow):
        module.reset()
    yield asked
    agx_flow.wait_idle(5)
    for module in (long_oi, leap_oi_scanner, agx_flow):
        module.reset()


def test_one_ticker_gets_one_answer_from_both_pages(world):
    with running_api() as api:
        status, first = api.get(f"{ROUTE}?symbol=nvda", headers=REGULAR)
        assert status == 200, first
        deadline = time.monotonic() + 15
        body = first
        while (not body.get("ready") or body.get("pending")) and time.monotonic() < deadline:
            time.sleep(0.2)
            status, body = api.get(f"{ROUTE}?symbol=NVDA", headers=REGULAR)
        # 2026-11-23 "which strike is that?": once AGX Flow's read is held, Today's bets carries the table.
        table_status, again = api.get(f"{ROUTE}?symbol=NVDA", headers=REGULAR)
    assert table_status == 200
    table = next(s for s in again["sections"] if s["title"] == "Today's bets").get("table")
    assert table and table["calls"][0]["strike"] == 185.0 and 0 < table["calls"][0]["share"] <= 100, table
    assert status == 200 and body["ready"] and not body["pending"], body
    assert body["ticker"] == "NVDA" and world["leap"] == ["NVDA"] and world["flow"] == ["NVDA"]
    titles = [s["title"] for s in body["sections"]]
    assert titles[:3] == ["Price", "Today's bets", "Long bets (3 to 12 months)"], titles
    today = " ".join(line["text"] for line in body["sections"][1]["lines"])
    assert "went into Calls (bets the price goes up)" in today and "Big bet: 185 Call" in today, today
    assert "looked like a buyer (paid the asking price)" in today
    assert body["verdict"].startswith("Today more money went into Calls on NVDA")
    assert "not advice" in body["notAdvice"]


def test_junk_and_several_tickers_are_400_and_never_reach_schwab(world):
    with running_api() as api:
        bad, body = api.get(f"{ROUTE}?symbol=%3Cscript%3E", headers=REGULAR)
        two, _ = api.get(f"{ROUTE}?symbol=NVDA,AAPL", headers=REGULAR)
        none, _ = api.get(ROUTE, headers=REGULAR)
    assert (bad, two, none) == (400, 400, 400) and body["error"]
    assert world == {"leap": [], "flow": []}


def test_signed_out_gets_nothing(world):
    with running_api() as api:
        status, _ = api.get(f"{ROUTE}?symbol=NVDA", headers=RELAYED)
    assert status in (401, 403) and world == {"leap": [], "flow": []}
