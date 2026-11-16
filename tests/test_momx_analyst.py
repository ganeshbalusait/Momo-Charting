"""Analyst ratings and price targets (momx/analyst.py, 2026-11-11): unit tests and the real GET /api/long-term/analyst.
Finnhub and FMP are faked; under pytest the real request is refused anyway."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import analyst

ET = ZoneInfo("America/New_York")
KEY = "secret-key-123"
FINNHUB = [{"symbol": "NVDA", "period": "2026-11-01", "strongBuy": 25, "buy": 40, "hold": 6, "sell": 1, "strongSell": 0},
           {"symbol": "NVDA", "period": "2026-10-01", "strongBuy": 24, "buy": 41, "hold": 7, "sell": 1, "strongSell": 0}]
CONSENSUS = [{"symbol": "NVDA", "targetHigh": 352.0, "targetLow": 140.0, "targetConsensus": 260.5, "targetMedian": 250.0}]
SUMMARY = [{"symbol": "NVDA", "lastMonthCount": 12, "lastMonthAvgPriceTarget": 255.0, "lastQuarterCount": 30, "lastQuarterAvgPriceTarget": 245.0,
            "lastYearCount": 90, "lastYearAvgPriceTarget": 220.0, "publishers": "[\"Benzinga\", \"TheFly\"]"}]
GRADES = [{"symbol": "NVDA", "strongBuy": 1, "buy": 60, "hold": 5, "sell": 1, "strongSell": 0, "consensus": "Buy"}]


@pytest.fixture
def cache(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_ANALYST_CACHE_DIR", str(tmp_path / "analyst"))
    monkeypatch.setenv("FINNHUB_API_KEY", KEY)
    monkeypatch.setenv("FMP_API_KEY", KEY)
    analyst.reset()
    yield tmp_path / "analyst"
    analyst.reset()


def fake_get(sent, fail=()):
    def get(url):
        sent.append(url)
        if any(name in url for name in fail):
            raise TimeoutError("down")
        if "finnhub" in url:
            return FINNHUB
        if "price-target-consensus" in url:
            return CONSENSUS
        if "price-target-summary" in url:
            return SUMMARY
        return GRADES
    return get


def test_fetch_reads_both_services_and_keeps_no_key_anywhere(cache):
    sent: list = []
    doc = analyst.fetch("NVDA", fake_get(sent))
    assert len(sent) == 4 and all(url.startswith("https://") for url in sent)
    assert doc["finnhub"]["months"][0] == {"period": "2026-11-01", "strongBuy": 25, "buy": 40, "hold": 6, "sell": 1, "strongSell": 0}
    assert doc["targets"]["targetConsensus"] == 260.5 and doc["summary"]["publishers"] == ["Benzinga", "TheFly"]
    assert doc["grades"]["consensus"] == "Buy" and KEY not in json.dumps(doc)


def test_a_failing_part_is_named_without_its_address(cache):
    doc = analyst.fetch("NVDA", fake_get([], fail=("finnhub",)))
    assert doc["finnhub"] is None and doc["errors"]["recommendation"] == "TimeoutError" and KEY not in json.dumps(doc)


def test_the_lines_say_their_source_and_never_merge_the_two_counts(cache):
    doc = analyst.fetch("NVDA", fake_get([]))
    lines = analyst.lines_for(doc, "today", price=200.0)
    text = "\n".join(lines)
    assert lines[0] == analyst.LABEL
    assert "Finnhub ratings, 2026-11 (72 analysts): Strong buy 25 (35%), Buy 40 (56%), Hold 6 (8%), Sell 1 (1%), Strong sell 0 (0%)." in text
    assert "65 of 72 Finnhub analysts rate it buy or strong buy." in text
    assert "FMP grades (67 grades)" in text and "FMP's consensus word: Buy" in text
    assert "average 260.50 (+30.2% from 200.00)" in text and "low 140.00 (-30.0% from 200.00)" in text
    assert "12 last month, 30 last quarter, 90 last year" in text and "count analysts differently" in text
    assert "139" not in text                                              # 72 + 67 is never shown as one number


def test_one_fetch_a_day_and_a_failure_keeps_the_earlier_copy_with_its_date(cache):
    sent: list = []
    first, status = analyst.get("NVDA", fetcher=lambda s: analyst.fetch(s, fake_get(sent)))
    again, status2 = analyst.get("NVDA", fetcher=lambda s: pytest.fail("fetched twice in a day"))
    assert status == status2 == "today" and first == again and len(sent) == 4
    # Yesterday's copy, and today's fetch fails: the old one is used, said with its date, and not tried again today.
    yesterday = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
    folder = cache / "AAPL"
    folder.mkdir(parents=True)
    (folder / f"{yesterday}.json").write_text(json.dumps({**first, "symbol": "AAPL", "day": yesterday}), encoding="utf-8")
    tries = []
    doc, status = analyst.get("AAPL", fetcher=lambda s: tries.append(s) or {"symbol": s, "day": "x", "finnhub": None, "targets": None, "grades": None})
    assert status == "earlier" and doc["day"] == yesterday and tries == ["AAPL"]
    assert f"These are from {yesterday}" in "\n".join(analyst.lines_for(doc, status))
    assert analyst.get("AAPL", fetcher=lambda s: pytest.fail("tried again the same day"))[1] == "earlier"


def test_a_bad_symbol_fetches_nothing(cache):
    assert analyst.get("not a ticker!", fetcher=lambda s: pytest.fail("fetched"))== (None, "bad ticker")


def test_under_pytest_the_real_request_is_refused():
    with pytest.raises(ConnectionRefusedError):
        analyst._get_json("https://finnhub.io/api/v1/stock/recommendation?symbol=NVDA")


def test_the_route_answers_signed_in_users_only(cache, monkeypatch):
    from api_harness import RELAYED, running_api, seed_user
    from cf_access_auth import ACCESS_EMAIL_HEADER
    monkeypatch.setattr(analyst, "_get_json", fake_get([]))
    seed_user("viewer-analyst@example.com")
    viewer = {**RELAYED, ACCESS_EMAIL_HEADER: "viewer-analyst@example.com"}
    with running_api() as api:
        status, body = api.get("/api/long-term/analyst?symbol=nvda", headers=viewer)
        bad, _ = api.get("/api/long-term/analyst?symbol=%3Cscript%3E", headers=viewer)
        anonymous, _ = api.get("/api/long-term/analyst?symbol=NVDA", headers=RELAYED)
    assert status == 200 and body["status"] == "today" and body["label"] == analyst.LABEL and body["lines"][0] == analyst.LABEL
    assert body["views"]["targets"]["targetMedian"] == 250.0 and KEY not in json.dumps(body)
    assert bad == 400 and anonymous == 401
