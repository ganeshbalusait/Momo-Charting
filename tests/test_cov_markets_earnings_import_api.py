"""Integration: the earnings screenshot import end to end on the server -
POST /api/earnings-calendar/ocr, /manual-import and /manual-imports/clear,
then the calendar read the page reloads - over HTTP (2026-11-04 coverage).

running_api() moves EARNINGS_MANUAL_IMPORT_PATH to a scratch file, Finnhub is
a fake fetcher, and the Windows OCR subprocess is faked except in the one
Windows-only test that runs the real local OCR on a rendered image.
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from datetime import datetime
from io import BytesIO
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from PIL import Image, ImageDraw, ImageFont

from api_harness import api_server, running_api

ET = ZoneInfo("America/New_York")


class PinnedPandas:
    def __init__(self, instant: datetime):
        self.instant = instant
        owner = self

        class PinnedTimestamp(pd.Timestamp):
            @classmethod
            def now(cls, tz=None):
                stamp = pd.Timestamp(owner.instant)
                return stamp.tz_convert(tz) if tz else stamp.tz_convert(None)

        self.Timestamp = PinnedTimestamp

    def __getattr__(self, name):
        return getattr(pd, name)


def png_data_uri(image=None) -> str:
    buffer = BytesIO()
    (image or Image.new("RGB", (1000, 500), "white")).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class FakeOcrSubprocess:
    def __init__(self, words):
        self.words = words
        self.calls = 0

    def run(self, command, **kwargs):
        self.calls += 1
        return SimpleNamespace(returncode=0, stderr="",
                               stdout=json.dumps({"width": 1000, "height": 500, "words": self.words}))

    def __getattr__(self, name):
        return getattr(subprocess, name)


@pytest.fixture
def server(monkeypatch):
    finnhub_urls: list[str] = []

    def finnhub(url):
        finnhub_urls.append(url)
        return {"earningsCalendar": [{"symbol": "NVDA", "date": "2026-11-19", "hour": "amc", "epsEstimate": 1.2}]}

    monkeypatch.setattr(api_server, "pd", PinnedPandas(datetime(2026, 11, 4, 10, 0, tzinfo=ET)))
    monkeypatch.setattr(api_server.DashboardState, "_earnings_calendar_request", staticmethod(finnhub))
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", ["NVDA", "AAPL", "MSFT"])
    monkeypatch.setattr(api_server.STATE, "earnings_calendar_cache", None)
    monkeypatch.setattr(api_server.STATE, "earnings_manual_imports", [])
    monkeypatch.setenv("FINNHUB_API_KEY", "test-finnhub")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    return SimpleNamespace(finnhub_urls=finnhub_urls)


def test_a_bad_upload_is_a_400_with_the_reason(server):
    with running_api() as api:
        status, body = api.post("/api/earnings-calendar/ocr", {"imageData": "data:text/plain;base64,aGk=", "weekStart": "2026-11-02"})
    assert status == 400
    assert body == {"error": "Upload a PNG, JPG, or WebP screenshot."}


def test_ocr_proposes_only_watchlist_tickers_on_their_weekday(server, monkeypatch):
    fake = FakeOcrSubprocess([
        {"text": "NVDA", "x": 250, "y": 200, "width": 40, "height": 20},     # Tuesday, before open
        {"text": "TSLA", "x": 450, "y": 200, "width": 40, "height": 20},     # not on the watchlist
        {"text": "AAPL", "x": 900, "y": 300, "width": 40, "height": 20},     # Friday, after close
    ])
    monkeypatch.setattr(api_server, "subprocess", fake)
    with running_api() as api:
        status, body = api.post("/api/earnings-calendar/ocr", {"imageData": png_data_uri(), "weekStart": "2026-11-04"})
    assert status == 200, body
    assert [(row["symbol"], row["date"], row["timingCode"]) for row in body["candidates"]] == [
        ("NVDA", "2026-11-03", "bmo"), ("AAPL", "2026-11-06", "amc"),
    ]
    assert body["matchCount"] == len(body["candidates"]) == 2
    assert body["weekStart"] == "2026-11-02" and fake.calls == 1


def test_import_then_calendar_then_clear_round_trip(server):
    with running_api() as api:
        status, saved = api.post("/api/earnings-calendar/manual-import",
                                 {"rows": [{"symbol": "NVDA", "date": "2026-11-18", "timingCode": "bmo", "key": "x", "selected": True}]})
        assert status == 200, saved
        assert saved == {"imported": 1, "skipped": 0, "manualImportCount": 1}
        written = json.loads(api_server.EARNINGS_MANUAL_IMPORT_PATH.read_text(encoding="utf-8"))
        assert [(row["symbol"], row["date"], row["timingCode"]) for row in written] == [("NVDA", "2026-11-18", "bmo")]

        status, calendar = api.get("/api/earnings-calendar?days=45&force=true")
        assert status == 200, calendar
        nvda = next(row for row in calendar["rows"] if row["symbol"] == "NVDA")
        assert (nvda["date"], nvda["manualImport"], nvda["timingCode"]) == ("2026-11-18", True, "bmo")
        assert calendar["manualImportCount"] == 1
        assert calendar["source"].endswith("+ manual image import")

        status, cleared = api.post("/api/earnings-calendar/manual-imports/clear", {})
        assert status == 200 and cleared == {"removed": 1, "manualImportCount": 0}
        assert json.loads(api_server.EARNINGS_MANUAL_IMPORT_PATH.read_text(encoding="utf-8")) == []
        status, after = api.get("/api/earnings-calendar?days=45&force=true")
    assert status == 200
    assert after["manualImportCount"] == 0
    assert [(row["symbol"], row["date"], row["manualImport"]) for row in after["rows"]] == [("NVDA", "2026-11-19", False)]


def test_importing_only_off_watchlist_rows_is_a_400(server):
    with running_api() as api:
        status, body = api.post("/api/earnings-calendar/manual-import",
                                {"rows": [{"symbol": "TSLA", "date": "2026-11-18", "timingCode": "amc"}]})
    assert status == 400
    assert body == {"error": "Select at least one valid watchlist earnings row."}
    assert api_server.STATE.earnings_manual_imports == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows OCR exists only on Windows")
def test_real_windows_ocr_reads_a_rendered_ticker_into_its_day(server):
    image = Image.new("RGB", (1000, 500), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arialbd.ttf", 44)
    except OSError:
        pytest.skip("Arial Bold is not installed")
    draw.text((210, 220), "NVDA", fill="black", font=font)      # Tuesday column, left half
    with running_api() as api:
        status, body = api.post("/api/earnings-calendar/ocr", {"imageData": png_data_uri(image), "weekStart": "2026-11-02"})
    if status == 400 and "not available" in str(body.get("error", "")):
        pytest.skip(f"Windows OCR language pack missing: {body['error']}")
    assert status == 200, body
    assert [(row["symbol"], row["date"], row["timingCode"]) for row in body["candidates"]] == [("NVDA", "2026-11-03", "bmo")]
