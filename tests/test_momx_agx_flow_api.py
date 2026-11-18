"""Integration: AGX Flow through the real GET /api/agx-flow route (2026-11-17).

Real api_server route, the real auth gate and the real momx.agx_flow module. Schwab is the one thing faked
(agx_flow.fetch). The disk copy lands in a scratch folder (AGX_MOMX_GRADE_DIR)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import agx_flow
from test_momx_agx_flow import ET, TODAY, chain

VIEWER = "viewer-agxflow@example.com"
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/agx-flow"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(VIEWER, role="user")


@pytest.fixture
def world(tmp_path, monkeypatch):
    asked: list[str] = []
    today = datetime.now(ET).date()

    def fetch(ticker, day):
        asked.append(ticker)
        doc = chain()
        shift = (today - TODAY).days                    # the fixture is laid out from 2026-11-17; move it to today
        for key in ("callExpDateMap", "putExpDateMap"):
            doc[key] = {f"{(datetime.fromisoformat(k[:10]).date() + timedelta(days=shift)).isoformat()}:{k[11:]}": v
                        for k, v in doc[key].items()}
        return doc

    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setattr(agx_flow, "fetch", fetch)
    agx_flow.reset()
    yield asked
    agx_flow.wait_idle(5)
    agx_flow.reset()


def test_first_read_answers_at_once_then_the_board_fills(world, tmp_path):
    with running_api() as api:
        status, first = api.get(f"{ROUTE}?symbols=nvda,AAPL", headers=REGULAR)
        assert status == 200, first
        assert [t["ticker"] for t in first["tickers"]] == ["NVDA", "AAPL"]
        assert agx_flow.wait_idle(10)
        status, body = api.get(f"{ROUTE}?symbols=NVDA,AAPL", headers=REGULAR)
    assert status == 200, body
    assert sorted(world) == ["AAPL", "NVDA"]               # one read each; the second request read the copies
    assert all(t["cached"] and not t["pending"] and t["error"] is None for t in body["tickers"])
    assert len(body["rows"]) == 4
    top = body["rows"][0]
    assert top["side"] == "CALL" and top["strike"] == 185.0 and top["fill"] == "ask" and top["premium"] == 1_200_000
    assert "not a direction signal" in body["note"]
    assert (tmp_path / "agx_flow_cache" / "NVDA.json.gz").exists()


def test_missing_symbols_is_400(world):
    with running_api() as api:
        status, body = api.get(ROUTE, headers=REGULAR)
        blank, _ = api.get(f"{ROUTE}?symbols=", headers=REGULAR)
    assert status == 400 and body.get("error") and blank == 400 and world == []


def test_garbage_symbols_never_reach_schwab(world):
    with running_api() as api:
        status, body = api.get(f"{ROUTE}?symbols=%3Cscript%3E,12345", headers=REGULAR)
    assert status in (400, 503) and body.get("error") and world == []


def test_signed_out_gets_nothing_from_schwab(world):
    with running_api() as api:
        status, _ = api.get(f"{ROUTE}?symbols=NVDA", headers=RELAYED)
    assert status in (401, 403) and world == []


def test_a_searched_ticker_over_http_jumps_the_warm_up_queue(world, tmp_path, monkeypatch):
    """2026-11-17 "Add search ticker also": one typed ticker through the real route is read before the warm-up's
    queue; an ETF typed in gets the plain stocks-only answer and Schwab is never asked."""
    import threading
    monkeypatch.setattr(agx_flow, "READERS", 1)
    gate, started = threading.Event(), threading.Event()
    real_fetch = agx_flow.fetch

    def fetch(ticker, day):
        if ticker == "AAPL":
            started.set()
            gate.wait(5)
        return real_fetch(ticker, day)
    monkeypatch.setattr(agx_flow, "fetch", fetch)
    with running_api() as api:
        assert agx_flow.warm(["AAPL", "MSFT", "AMZN"], tmp_path) == 3
        assert started.wait(5)
        status, first = api.get(f"{ROUTE}?symbols=pltr", headers=REGULAR)
        assert status == 200 and first["tickers"][0]["ticker"] == "PLTR" and first["tickers"][0]["pending"], first
        etf_status, etf = api.get(f"{ROUTE}?symbols=QQQ", headers=REGULAR)
        gate.set()
        assert agx_flow.wait_idle(10)
        status, body = api.get(f"{ROUTE}?symbols=PLTR", headers=REGULAR)
    assert etf_status == 400 and "stocks only" in etf["error"]
    assert status == 200 and body["tickers"][0]["cached"] and body["rows"], body
    assert world == ["AAPL", "PLTR", "MSFT", "AMZN"]


def test_expiry_and_hot_order_over_http(world, tmp_path):
    """2026-11-17 "All six": the page's Expiry and Order buttons reach the board; junk falls back to All / Biggest $."""
    with running_api() as api:
        api.get(f"{ROUTE}?symbols=NVDA", headers=REGULAR)
        assert agx_flow.wait_idle(10)
        status, week = api.get(f"{ROUTE}?symbols=NVDA&expiry=week", headers=REGULAR)
        hot_status, hot = api.get(f"{ROUTE}?symbols=NVDA&order=hot", headers=REGULAR)
        junk_status, junk = api.get(f"{ROUTE}?symbols=NVDA&expiry=%3Cx%3E&order=1", headers=REGULAR)
    window = agx_flow.expiry_window("week", datetime.now(ET).date())
    assert status == 200 and week["expiry"] == "week"
    assert all(agx_flow.in_window(r["expiry"], window) for r in week["rows"])
    assert hot_status == 200 and hot["order"] == "hot" and hot["rows"] == []            # one read: nothing new yet
    assert junk_status == 200 and junk["expiry"] == "all" and junk["order"] == "premium" and len(junk["rows"]) == 2


def test_phone_alert_switch_is_this_accounts_own(world, tmp_path):
    """2026-11-17 "All six": off by default, saved for this account only, never shows the phone topic."""
    from momx import agx_flow_alert
    with running_api() as api:
        status, before = api.get(f"{ROUTE}/alert", headers=REGULAR)
        on_status, on = api.post(f"{ROUTE}/alert", {"on": True}, headers=REGULAR)
        _, after = api.get(f"{ROUTE}/alert", headers=REGULAR)
        bad_status, bad = api.post(f"{ROUTE}/alert", {"on": "yes"}, headers=REGULAR)
        out_status, _ = api.post(f"{ROUTE}/alert", {"on": True}, headers=RELAYED)
    assert status == 200 and before["on"] is False and before["signedIn"] is True and before["minPremium"] == 1_000_000
    assert "ntfyTopic" not in before and set(before) >= {"hasPhone", "windowStart", "windowEnd"}
    assert on_status == 200 and on == {"on": True} and after["on"] is True
    assert bad_status == 400 and "true or false" in bad["error"]
    assert out_status in (401, 403)
    assert agx_flow_alert.is_on(tmp_path, VIEWER) is True and agx_flow_alert.is_on(tmp_path, "someone-else@example.com") is False
    assert (tmp_path / "agx_flow_alert.json").exists()
