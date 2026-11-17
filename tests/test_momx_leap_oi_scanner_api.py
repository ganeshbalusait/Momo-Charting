"""Integration: the LEAP OI Scanner page through the real GET /api/leap-oi-scanner route (2026-11-17).

Real api_server route and auth gate, the real long_oi + leap_oi_scanner modules. Schwab is the one thing faked
(long_oi.fetch); the saved copies land in a scratch folder (AGX_MOMX_GRADE_DIR). Set LEAP_OI_CAPTURE to a file path to
save a real reply for the browser spec."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import leap_oi_scanner, long_oi
from test_momx_long_oi import ET, chain

VIEWER = "viewer-leapoi@example.com"
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(VIEWER, role="user")


@pytest.fixture
def world(tmp_path, monkeypatch):
    asked: list = []
    today = datetime.now(ET).date()

    def fetch(ticker, day):
        asked.append(ticker)
        doc = chain()
        doc["symbol"] = ticker
        shift = (today - datetime(2026, 11, 16).date()).days
        for key in ("callExpDateMap", "putExpDateMap"):
            doc[key] = {f"{(datetime.fromisoformat(k[:10]).date() + timedelta(days=shift)).isoformat()}:{k[11:]}": v for k, v in doc[key].items()}
        return doc

    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setattr(long_oi, "fetch", fetch)
    long_oi.reset()
    leap_oi_scanner.reset()
    yield asked
    long_oi.reset()
    leap_oi_scanner.reset()


def _wait_ready(api, path, headers):
    deadline = time.monotonic() + 10
    while True:
        status, body = api.get(path, headers=headers)
        if status != 200 or not body.get("pending") or time.monotonic() > deadline:
            return status, body
        time.sleep(0.1)


def test_the_page_reads_schwab_in_the_background_then_shows_the_board(world, tmp_path):
    path = "/api/leap-oi-scanner?symbols=NVDA,aapl&from=1&to=12&side=both&min_delta=0.10"
    with running_api() as api:
        first_status, first = api.get(path, headers=REGULAR)
        status, body = _wait_ready(api, path, REGULAR)
        again_status, again = api.get(path, headers=REGULAR)
    assert first_status == 200 and set(first["pending"]) <= {"NVDA", "AAPL"}
    assert status == 200 and body["pending"] == [], body
    assert {t["ticker"]: t["state"] for t in body["tickers"]} == {"NVDA": "ready", "AAPL": "ready"}
    top = body["rows"][0]
    assert top["strike"] == 200.0 and top["oi"] == 344959 and top["side"] == "CALL"
    assert all(abs(r["delta"]) >= 0.10 for r in body["rows"])
    assert body["entries"] and all(r["entry"] for r in body["entries"])
    assert again_status == 200 and sorted(world) == ["AAPL", "NVDA"]       # one Schwab read per ticker
    assert (tmp_path / "long_oi_cache" / "NVDA.json.gz").exists()
    capture = os.environ.get("LEAP_OI_CAPTURE")
    if capture:
        with open(capture, "w", encoding="utf-8") as handle:
            json.dump(again, handle)


def test_entry_only_and_puts(world):
    with running_api() as api:
        _wait_ready(api, "/api/leap-oi-scanner?symbols=NVDA&from=1&to=12", REGULAR)
        status, body = api.get("/api/leap-oi-scanner?symbols=NVDA&from=1&to=12&side=puts&entry_only=1", headers=REGULAR)
    assert status == 200 and body["rows"] and all(r["side"] == "PUT" and r["entry"] for r in body["rows"])


def test_no_tickers_is_a_plain_error(world):
    with running_api() as api:
        status, body = api.get("/api/leap-oi-scanner?symbols=,,bad%20ticker", headers=REGULAR)
    assert status == 400 and body["error"] == "Add at least one ticker." and world == []


def test_signed_out_gets_nothing_from_schwab(world):
    with running_api() as api:
        status, _ = api.get("/api/leap-oi-scanner?symbols=NVDA", headers=RELAYED)
    assert status in (401, 403)
    time.sleep(0.3)
    assert world == []
