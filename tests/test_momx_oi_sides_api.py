"""Integration: the bought-or-sold estimate through the real chain route and the real Ask route.

Real api_server over HTTP: /api/oi-finder-chain builds the chain twice from a faked Schwab whose volume grows
between the reads, and the High OI board's rows must carry the estimate. Then /api/bot-chat answers "Oi for nvda
buyers or sellers?" from a saved chain with the estimate in words. Only Schwab and the AI are faked.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api_harness import RELAYED, api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chat_ai, chat_chart, chat_limits, chat_state, oi_sides
from test_cov_options_chain_feed_api import FakeSchwab, chain_env, schwab_chain  # noqa: F401 - chain_env is a fixture
from test_momx_chat_market_api import _chain

ET = ZoneInfo("America/New_York")
VIEWER = "viewer-oi-sides@example.com"
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}


@pytest.fixture(autouse=True)
def _scratch(tmp_path, monkeypatch):
    seed_user(VIEWER, role="user")
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    oi_sides.reset()
    chat_limits.LIMITS.reset()
    yield
    oi_sides.reset()
    chat_limits.LIMITS.reset()


def _traded(chain: dict, side_map: str, strike: float, added: int, at: str) -> None:
    for strikes in chain[side_map].values():
        for key, contracts in strikes.items():
            if float(key) == strike:
                for contract in contracts:
                    contract["totalVolume"] += added
                    contract["last"] = contract[at]


def test_two_reads_of_the_chain_put_the_estimate_on_the_high_oi_rows(chain_env, monkeypatch):
    monkeypatch.setattr(oi_sides, "in_session", lambda now: True)
    chain = schwab_chain("AAPL")
    chain_env.schwab = [FakeSchwab(chain=lambda symbol: chain)]
    with running_api() as api:
        status, first = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
        assert status == 200, first
        assert not any("sideEst" in row for row in first["selectedExpiryChainRows"]), "nothing was watched before the first read"
        _traded(chain, "callExpDateMap", 205.0, 900, "ask")       # 900 calls bought at the ask
        _traded(chain, "putExpDateMap", 195.0, 700, "bid")        # 700 puts sold at the bid
        monkeypatch.setattr(api_server.STATE, "oi_finder_chain_cache", {})
        monkeypatch.setattr(api_server.STATE, "oi_finder_refresh_cooldown", {})
        status, second = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, second
    rows = {(row["side"], row["strike"]): row for row in second["selectedExpiryChainRows"] if "sideEst" in row}
    assert set(rows) == {("CALL", 205.0), ("PUT", 195.0)}, sorted(rows)
    call, put = rows[("CALL", 205.0)]["sideEst"], rows[("PUT", 195.0)]["sideEst"]
    assert (call["label"], call["bought"], call["sold"]) == ("mostly bought", 900, 0)
    assert (put["label"], put["bought"], put["sold"]) == ("mostly sold", 0, 700)
    assert call["unseen"] == 1205 and call["volume"] == 2105           # what traded before the first read is not sorted


def test_ask_answers_buyers_or_sellers_with_the_estimate_and_no_ai(tmp_path, monkeypatch):
    asked = []
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: asked.append(system) or {"ok": False, "error": "no AI"},
                        raising=False)
    monkeypatch.setattr(api_server.STATE, "oi_finder_chain_cache", {})
    chat_state.unbind()
    chat_chart.reset()
    chat_ai.clear_plans()
    now = datetime.now(ET)
    saved = _chain("NVDA", now)
    for row in saved["selectedExpiryChainRows"]:
        if (row["side"], row["strike"]) == ("CALL", 110):
            row["sideEst"] = {"bought": 620, "sold": 380, "mid": 100, "unseen": 400, "volume": 1500, "share": 0.62,
                              "label": "mostly bought", "oiChange": 250, "oiSince": "2026-11-18", "day": "2026-11-19"}
        if (row["side"], row["strike"]) == ("PUT", 95):
            row["sideEst"] = {"bought": 100, "sold": 400, "mid": 0, "unseen": 0, "volume": 600, "share": 0.2,
                              "label": "mostly sold", "oiChange": None, "oiSince": None, "day": "2026-11-19"}
    (tmp_path / "oi_chain_cache").mkdir()
    with gzip.open(tmp_path / "oi_chain_cache" / "NVDA.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(saved, handle)
    with running_api() as api:
        status, body = api.post("/api/bot-chat", {"question": "Oi for nvda buyers or sellers?"}, headers=REGULAR)
    said = " ".join(body["lines"])
    assert status == 200, body
    assert "Each one has a buyer and a seller" in body["lines"][0]
    assert "Estimate for 2026-11-19, not exchange data." in said, body["lines"]
    assert "110 call: mostly bought, 62% bought of 1,000 sorted contracts (1,500 traded); open interest up 250 since 2026-11-18." in said
    assert "95 put: mostly sold, 80% sold of 500 sorted contracts (600 traded)." in said
    assert [column["label"] for column in body["table"]["columns"]][-1] == "Est. traded"
    by_strike = {(row["side"], row["strike"]): row["est"] for row in body["table"]["rows"]}
    assert by_strike[("Call", "110")] == "62% bought" and by_strike[("Put", "95")] == "80% sold" and by_strike[("Call", "105")] == "-"
    assert "not exchange data" in body["table"]["note"]
    assert asked == []


def test_with_no_estimate_saved_the_walls_answer_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "no AI"}, raising=False)
    monkeypatch.setattr(api_server.STATE, "oi_finder_chain_cache", {})
    chat_state.unbind()
    chat_chart.reset()
    (tmp_path / "oi_chain_cache").mkdir()
    with gzip.open(tmp_path / "oi_chain_cache" / "NVDA.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(_chain("NVDA", datetime.now(ET)), handle)
    with running_api() as api:
        status, body = api.post("/api/bot-chat", {"question": "nvda high oi"}, headers=REGULAR)
    assert status == 200 and "Estimate" not in " ".join(body["lines"]), body
    assert [column["label"] for column in body["table"]["columns"]] == ["Side", "#", "Strike", "OI", "Expiry", "Strength"]
    assert "est" not in body["table"]["rows"][0] and "exchange" not in body["table"]["note"]
