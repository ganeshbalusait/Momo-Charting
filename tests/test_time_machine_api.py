"""Integration (2026-11-15, feature chart-time-machine): GET /api/time-machine
through the real route, the real STATE method and the real 60-day tape loader
(_fine_study_chart_frame). Only Schwab is faked: it returns 30 synthetic
five-minute sessions. The answer is fed to the real frontend module
(frontend/src/chartTimeMachine.js) under Node, so a renamed field on either
side breaks this test instead of leaving the Odds card empty."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import api_server
from api_harness import running_api
from test_time_machine import sessions

MODULE = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "chartTimeMachine.js").as_uri()


class FakeSchwab:
    calls = 0

    def get_chart_bars(self, symbol, timeframe="5Min", days_back=60):
        FakeSchwab.calls += 1
        assert timeframe == "5Min"
        return sessions(count=30)


@pytest.fixture()
def schwab(monkeypatch):
    FakeSchwab.calls = 0
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [FakeSchwab()])
    monkeypatch.setattr(api_server.STATE, "_backfill_today_premarket", lambda frame, *a, **k: frame, raising=False)
    monkeypatch.setattr(api_server.STATE, "_backfill_overnight_session", lambda frame, *a, **k: frame, raising=False)
    monkeypatch.setattr(api_server.STATE, "_oi_finder_fine_study_cache", {}, raising=False)
    return FakeSchwab


NODE = """
const { timeMachineView } = await import(process.argv[1]);
let body = ""; for await (const chunk of process.stdin) body += chunk;
const view = timeMachineView(JSON.parse(body), { roiAtTarget: 60, roiAtStop: -50 });
process.stdout.write(JSON.stringify(view));
"""


def test_the_route_answers_from_the_chart_tape_and_the_card_can_read_it(schwab):
    with running_api() as api:
        status, body = api.get("/api/time-machine?symbol=nvda&side=CALL&spot=100&target=100.8&stop=99.4&paths=2000")
        assert status == 200, body
        assert body["ok"] is True and body["symbol"] == "NVDA" and body["side"] == "CALL"
        assert body["sessions"] == 30 and body["paths"] == 2000
        assert abs(sum(body["odds"].values()) - 100) < 0.2
        assert len(body["history"]) == 30 and body["verdict"]
        # Asked twice: the same numbers, and the tape is fetched once (cached).
        status, again = api.get("/api/time-machine?symbol=NVDA&side=CALL&spot=100&target=100.8&stop=99.4&paths=2000")
        assert again["odds"] == body["odds"] and schwab.calls == 1

        view = subprocess.run(["node", "--input-type=module", "-e", NODE, MODULE], input=json.dumps(body),
                              capture_output=True, text=True, timeout=60, check=False)
        assert view.returncode == 0, view.stderr
        card = json.loads(view.stdout)
        assert card["ready"] is True
        assert [row["key"] for row in card["rows"]] == ["target", "stop", "neither"]
        assert card["historyTotal"] == 30 and card["averageRoi"] is not None


def test_a_bad_plan_or_no_candles_says_why(schwab, monkeypatch):
    with running_api() as api:
        status, body = api.get("/api/time-machine?symbol=NVDA&side=CALL&spot=100&target=99&stop=98")
        assert status == 200 and body["ok"] is False and "either side" in body["reason"]
        monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [])
        monkeypatch.setattr(api_server.STATE, "_oi_finder_fine_study_cache", {}, raising=False)
        status, body = api.get("/api/time-machine?symbol=ZZZZ&side=PUT&spot=10&target=9&stop=11")
        assert status == 200 and body["ok"] is False and "No five-minute candles" in body["reason"]
