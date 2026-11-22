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
    monkeypatch.setattr(leap_oi_scanner, "PACE_SECONDS", 0.0)       # the real pace is 3 s between Schwab requests
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
    assert [t["ticker"] for t in body["byTicker"]] == ["AAPL", "NVDA"] and body["byTicker"][0]["call"]["strike"] == 200.0
    # By sector (2026-11-22): each line carries the scanner's industry group.
    assert {t["ticker"]: t["sector"] for t in body["byTicker"]} == {"AAPL": "Tech-HW", "NVDA": "Semis"}
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


def test_a_searched_ticker_over_http_jumps_the_warm_up_queue(world, tmp_path, monkeypatch):
    """2026-11-17 "Add search ticker also": the page asks the real route for one typed ticker while the warm-up is
    busy with the watchlist; that ticker is read next, then shows on the board."""
    import threading
    gate, started = threading.Event(), threading.Event()
    real_fetch = long_oi.fetch

    def fetch(ticker, day):
        if ticker == "AAPL":
            started.set()
            gate.wait(5)
        return real_fetch(ticker, day)
    monkeypatch.setattr(long_oi, "fetch", fetch)
    path = "/api/leap-oi-scanner?symbols=pltr&from=1&to=12"
    with running_api() as api:
        assert leap_oi_scanner.warm(["AAPL", "MSFT", "AMZN"], tmp_path) == 3
        assert started.wait(5)
        status, first = api.get(path, headers=REGULAR)
        assert status == 200 and first["pending"] == ["PLTR"], first
        assert leap_oi_scanner.pending()[0] == "PLTR"
        gate.set()
        status, body = _wait_ready(api, path, REGULAR)
    assert status == 200 and [t["ticker"] for t in body["byTicker"]] == ["PLTR"], body
    assert world[:2] == ["AAPL", "PLTR"]


def test_open_interest_change_shows_after_a_second_days_read(world, tmp_path):
    """2026-11-17 "All six": the first day shows no change; once yesterday's copy is on disk, the next read of the
    same contracts shows today's open interest minus yesterday's."""
    import gzip
    path = "/api/leap-oi-scanner?symbols=NVDA&from=1&to=12"
    with running_api() as api:
        status, first = _wait_ready(api, path, REGULAR)
        assert status == 200 and first["rows"] and all(r["oiChange"] is None for r in first["rows"])
        disk = tmp_path / "leap_oi_board" / "NVDA.json.gz"
        with gzip.open(disk, "rt", encoding="utf-8") as handle:
            doc = json.load(handle)
        doc["fetchedAt"] = (datetime.now(ET) - timedelta(days=1, hours=1)).isoformat()   # yesterday's read
        for row in doc["rows"]:
            row["oi"] -= 1000
        assert doc["allOi"]                                                              # the whole chain is kept
        doc["allOi"] = {key: oi - 1000 for key, oi in doc["allOi"].items()}
        with gzip.open(disk, "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)
        leap_oi_scanner.reset()                                                          # as after a restart
        status, second = _wait_ready(api, path, REGULAR)
    assert status == 200 and second["rows"]
    assert all(r["oiChange"] == 1000 and r["oiChangeSince"] == doc["fetchedAt"] for r in second["rows"]), second["rows"][0]
    assert sorted(world) == ["NVDA"]                                                     # no extra Schwab read


def test_a_noon_read_stays_on_screen_with_its_time_and_the_close_is_read_again(world, monkeypatch):
    """2026-11-20: the NVDA card at 5:39 PM ET showed Bid 1.90 / Mid 1.95 from a 12:53 PM read, before NVDA fell 7%.
    Each row now says when its prices were read, and after 4:05 PM the board reads the closing prices once while the
    noon copy stays on screen."""
    clock = {"now": datetime(2026, 11, 20, 12, 53, tzinfo=ET)}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"] if tz is None else clock["now"].astimezone(tz)

    monkeypatch.setattr(leap_oi_scanner, "datetime", Clock)
    path = "/api/leap-oi-scanner?symbols=NVDA&from=1&to=12"
    with running_api() as api:
        status, noon = _wait_ready(api, path, REGULAR)
        clock["now"] = datetime(2026, 11, 20, 16, 6, tzinfo=ET)
        _, after = api.get(path, headers=REGULAR)                        # the noon copy, never blank
        deadline = time.monotonic() + 10
        while True:
            _, fresh = api.get(path, headers=REGULAR)
            if fresh["rows"] and fresh["rows"][0]["readAt"] != noon["rows"][0]["readAt"] or time.monotonic() > deadline:
                break
            time.sleep(0.1)
    at = lambda row: datetime.fromisoformat(row["readAt"])                 # noqa: E731 - same moment, any zone
    assert status == 200 and noon["rows"] and {at(r) for r in noon["rows"]} == {datetime(2026, 11, 20, 12, 53, tzinfo=ET)}
    assert after["tickers"][0]["state"] == "ready" and at(after["rows"][0]) == datetime(2026, 11, 20, 12, 53, tzinfo=ET)
    assert at(fresh["rows"][0]) == datetime(2026, 11, 20, 16, 6, tzinfo=ET), fresh["rows"][0]
    assert world == ["NVDA", "NVDA"]                                       # one read at noon, one after the close
