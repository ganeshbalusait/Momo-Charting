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
