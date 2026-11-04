"""Integration: the Record column's per-rule numbers
(GET /api/momx-scanner/rule-record, answered by api_server itself) over
scratch bot day files.

The column shows each bot rule's win rate and ROI over the last N completed
trading days. Any signed-in user may read it, so it must carry totals only:
no ticker, no contract. The day files live in a scratch AGX_MOMX_GRADE_DIR;
the AI score files are faked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from api_harness import api_server, running_api
from momx import option_track

SECRET_SYMBOL = "ZQXW"            # a ticker that must never appear in the answer
SECRET_CONTRACT = "ZQXW261120C00050000"


def trade(day, at="09:45:00", entry=1.0, exit_=2.0, rule="go", status="closed", **extra):
    hh, mm, _ = at.split(":")
    return {
        "rule": rule, "symbol": SECRET_SYMBOL, "contract": SECRET_CONTRACT, "status": status, "board": "Watchlist",
        "at": f"{day}T{at}-04:00", "entryAt": f"{day}T{hh}:{mm}:20-04:00",
        "exitAt": f"{day}T{int(hh) + 1:02d}:00:00-04:00", "exitPriceAt": f"{day}T{int(hh) + 1:02d}:00:30-04:00",
        "entry": entry, "entryBid": round(entry - 0.1, 2), "entryAsk": round(entry + 0.1, 2),
        "exitPrice": exit_, "exitBid": round(exit_ - 0.1, 2), "exitAsk": round(exit_ + 0.1, 2),
        **extra,
    }


def write_day(base: Path, day: str, trades: list[dict]):
    folder = base / option_track.DIRNAME
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps({"day": day, "trades": trades}), encoding="utf-8")


@pytest.fixture
def bot_days(tmp_path, monkeypatch):
    base = tmp_path / "grade"
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    monkeypatch.setattr(api_server, "_MOMX_RULE_RECORD", {})
    monkeypatch.setattr(api_server, "_MOMX_BOT_FILES", {})
    monkeypatch.setattr(api_server, "_momx_ai_scores", lambda day: {})
    monkeypatch.setattr(api_server.ApiHandler, "_session_user",
                        lambda self: {"id": "u1", "email": "member@example.com", "role": "member"})
    # Two completed sessions on the bull side: GO won twice and lost once.
    write_day(base, "2026-10-08", [trade("2026-10-08"), trade("2026-10-08", at="10:15:00", exit_=0.5)])
    write_day(base, "2026-10-09", [trade("2026-10-09"),
                                   trade("2026-10-09", at="11:00:00", status="open", exitPrice=None),   # still open
                                   trade("2026-10-09", at="09:31:00", rule="mxaplus", entry=2.0, exit_=3.0)])
    # The bear side keeps its own book.
    write_day(base / "bear", "2026-10-09", [trade("2026-10-09", rule="go", entry=1.0, exit_=0.8)])
    return base


def test_the_record_column_gets_per_rule_totals(bot_days):
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/rule-record?days=5")
    assert status == 200, body
    go = body["bull"]["go"]
    assert (go["n"], go["wins"], go["losses"], go["winRate"]) == (3, 2, 1, 66.7)
    assert go["roi"] == pytest.approx(50.0)                   # (+100 +100 -50) / 300 cost
    assert go["netPnl"] == pytest.approx(150.0)
    assert go["excluded"] == {"open": 1} and go["seen"] == 4
    assert (go["from"], go["through"]) == ("2026-10-08", "2026-10-09")
    assert body["bull"]["mxaplus"]["wins"] == 1
    assert body["bear"]["go"]["losses"] == 1 and body["bear"]["go"]["n"] == 1
    assert body["ranges"]["bull"] == {"days": 2, "from": "2026-10-08", "through": "2026-10-09"}
    assert body["days"] == 5


def test_the_record_never_leaks_a_ticker_or_a_contract(bot_days):
    with running_api() as api:
        api.get("/api/momx-scanner/rule-record?days=10")
        raw = json.dumps(api.get("/api/momx-scanner/rule-record?days=10")[1])
    assert SECRET_SYMBOL not in raw and SECRET_CONTRACT not in raw


def test_days_counts_back_completed_sessions_only(bot_days):
    with running_api() as api:
        one = api.get("/api/momx-scanner/rule-record?days=1")[1]
    # Only the newest completed session (10-09) is in a one-day record.
    assert one["ranges"]["bull"] == {"days": 1, "from": "2026-10-09", "through": "2026-10-09"}
    assert one["bull"]["go"]["n"] == 1 and one["bull"]["go"]["wins"] == 1
