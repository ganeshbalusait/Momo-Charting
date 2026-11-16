"""Integration: the AI loss study reaches the Scorecard over HTTP (2026-11-09).

The real study (momx/loss_lessons.py) runs on scratch paper files with a fake
AI, saves its file, and GET /api/momx-scanner/bot-desk serves it. No AI call,
nothing leaves the PC.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import bot_desk, loss_lessons

OWNER = "owner-lessons@example.com"
VIEWER = "viewer-lessons@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
ET = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def paper(tmp_path, monkeypatch):
    base = tmp_path / "grade"
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    day = datetime.now(ET).date().isoformat()
    monkeypatch.setattr(bot_desk, "EVALUATION_START", day)
    at = day + "T09:40:00-05:00"
    row = lambda sym, pl, rule="level1": {"side": "bull", "rule": rule, "symbol": sym, "at": at, "qty": 5, "status": "closed",
                                         "exitPlan": "chart",   # opened under the plan being judged
                                         "buyFill": 0.6, "botEntry": 0.6, "buyAt": at, "buyFillAt": at, "pl": pl, "leg": "entry",
                                         "closedAt": day + "T10:10:00-05:00", "spreadAtOrder": 0.3,
                                         "sells": [{"qty": 5, "price": 0.4, "why": "Bot exit: stop", "at": day + "T10:10:00-05:00"}]}
    path = base / "momx_paper_mirror" / "paper3" / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = {f"bull|level1|L{i}|{at}": row(f"L{i}", -40.0) for i in range(10)}
    rows[f"bull|level2|W|{at}"] = row("W", 55.0, "level2")
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return base, day


def test_the_scorecard_serves_no_lessons_before_the_first_study_then_the_saved_study(paper):
    base, day = paper
    asked = []

    def fake_ai(system, user):
        asked.append(json.loads(user))
        return {"ok": True, "provider": "openai", "data": {
            "lesson": "All ten losing trades were bought on a spread over 25%.", "proposedChange": "Try skipping spreads over 25%.",
            "howToTest": "Track the next 30 trades by spread.", "bucket": "spread", "group": "spread over 25%", "evidenceCount": 10}}

    with running_api() as api:
        status, before = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
        loss_lessons.run(base, day, ask=fake_ai, now=datetime.now(ET))
        api_server._momx_bot_desk_clear()
        _, after = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
        refused, _ = api.get("/api/momx-scanner/bot-desk?days=30", headers={ACCESS_EMAIL_HEADER: VIEWER})
    assert status == 200 and before["lessons"] is None and before["startDay"] == day
    assert before["accounts"]["paper3"]["stats"]["n"] == 11 and before["accounts"]["paper3"]["balance"] == 9655.0
    assert len(asked) == 1 and asked[0]["strategy"] == "level1" and asked[0]["counts"]["losses"] == 10     # level2 has no losers
    study = after["lessons"]
    assert study["startDay"] == day and "Nothing here blocks" in study["note"]
    by_rule = {x["rule"]: x for x in study["lessons"]}
    assert by_rule["level1"]["status"] == "lesson" and by_rule["level1"]["lesson"].startswith("All ten losing trades")
    assert by_rule["level1"]["proposedChange"] == "Try skipping spreads over 25%." and "facts" not in by_rule["level1"]
    assert by_rule["level1"]["basis"] == "10 of 10 losing trades and 0 of 0 winning trades were 'spread over 25%'"
    assert by_rule["level2"]["status"] == "collecting" and by_rule["level2"]["losses"] == 0
    assert refused == 403


def test_a_study_whose_ai_fails_still_serves_the_plain_counts(paper):
    base, day = paper
    loss_lessons.run(base, day, ask=lambda *a: {"ok": False, "error": "quota"}, now=datetime.now(ET))
    with running_api() as api:
        _, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
    lesson = next(x for x in body["lessons"]["lessons"] if x["rule"] == "level1")
    assert lesson["status"] == "numbers" and lesson["source"] == "numbers only"
    assert lesson["lesson"].startswith("10 of the 10 losing trades were in the group")
