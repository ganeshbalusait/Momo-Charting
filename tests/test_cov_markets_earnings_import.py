"""Unit: the earnings screenshot import (local OCR -> review rows -> saved
imports -> clear), 2026-11-04 coverage.

Windows OCR is a PowerShell subprocess; here api_server.subprocess is a proxy
whose run() returns canned OCR words, so the word-to-day mapping is tested
exactly. The imports file is a scratch path (it is NOT isolated by conftest).
"""
from __future__ import annotations

import base64
import json
import subprocess
import threading
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

import api_server

WATCHLIST = ["NVDA", "AAPL", "MSFT", "OPEN"]


def png_data_uri(width=1000, height=500) -> str:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


class FakeOcr:
    """Stands in for subprocess.run(powershell ... Windows OCR)."""

    def __init__(self, words=None, width=1000, height=500, returncode=0, stderr="", timeout=False):
        self.payload = {"width": width, "height": height, "words": words or []}
        self.returncode, self.stderr, self.timeout = returncode, stderr, timeout
        self.image_paths: list[str] = []

    def run(self, command, **kwargs):
        path = kwargs["env"]["AGENTIC_EARNINGS_OCR_IMAGE"]
        self.image_paths.append(path)
        assert Path(path).exists(), "the screenshot must be on disk while OCR reads it"
        if self.timeout:
            raise subprocess.TimeoutExpired(command, 90)
        stdout = "WARNING: noise before the json\n" + json.dumps(self.payload) if self.returncode == 0 else ""
        return SimpleNamespace(returncode=self.returncode, stdout=stdout, stderr=self.stderr)


class SubprocessProxy:
    def __init__(self, fake):
        self.run = fake.run

    def __getattr__(self, name):
        return getattr(subprocess, name)


@pytest.fixture
def importer(monkeypatch, tmp_path):
    imports_file = tmp_path / "earnings_manual_imports.json"
    monkeypatch.setattr(api_server, "EARNINGS_MANUAL_IMPORT_PATH", imports_file)
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", list(WATCHLIST))
    state = object.__new__(api_server.DashboardState)
    state.earnings_manual_lock = threading.Lock()
    state.earnings_manual_imports = []
    state.earnings_calendar_cache = ("cached",)
    return SimpleNamespace(state=state, file=imports_file)


def with_ocr(monkeypatch, fake):
    monkeypatch.setattr(api_server, "subprocess", SubprocessProxy(fake))
    return fake


def word(text, x, y, width=40):
    return {"text": text, "x": x, "y": y, "width": width, "height": 20}


# ---------------------------------------------------------------- data URI guard

@pytest.mark.parametrize("data,message", [
    ("", "Upload a PNG, JPG, or WebP screenshot."),
    ("data:text/plain;base64,aGVsbG8=", "Upload a PNG, JPG, or WebP screenshot."),
    ("data:image/gif;base64,R0lGODlh", "Upload a PNG, JPG, or WebP screenshot."),
    ("data:image/png;base64,abc", "The screenshot could not be read."),           # torn base64
    ("data:image/png;base64,", "Upload a PNG, JPG, or WebP screenshot."),
])
def test_only_a_readable_png_jpg_or_webp_is_accepted(importer, data, message):
    with pytest.raises(ValueError) as error:
        importer.state.analyze_earnings_screenshot(data, "2026-11-02")
    assert str(error.value) == message


def test_an_oversized_image_is_refused_before_ocr(importer, monkeypatch):
    fake = with_ocr(monkeypatch, FakeOcr())
    monkeypatch.setattr(api_server, "EARNINGS_IMAGE_MAX_BYTES", 100)
    with pytest.raises(ValueError, match="^Use an image smaller than 8 MB.$"):
        importer.state.analyze_earnings_screenshot(png_data_uri(), "2026-11-02")
    assert fake.image_paths == []


def test_an_image_pillow_cannot_open_is_refused(importer, monkeypatch):
    with_ocr(monkeypatch, FakeOcr())
    junk = "data:image/png;base64," + base64.b64encode(b"not really a png").decode()
    with pytest.raises(ValueError, match="could not be prepared for local OCR"):
        importer.state.analyze_earnings_screenshot(junk, "2026-11-02")


# ---------------------------------------------------------------- week start

@pytest.mark.parametrize("value,monday", [
    ("2026-11-02", "2026-11-02"), ("2026-11-05", "2026-11-02"), ("2026-11-08", "2026-11-02"),
    ("2026-11-09T10:00:00", "2026-11-09"),
])
def test_any_date_maps_to_its_monday(value, monday):
    assert api_server.DashboardState._earnings_week_start(value).isoformat() == monday


@pytest.mark.parametrize("junk", ["", None, "next week", "2026-13-01"])
def test_a_junk_week_is_refused_with_the_plain_instruction(junk):
    with pytest.raises(ValueError, match="^Choose the Monday for the screenshot's earnings week.$"):
        api_server.DashboardState._earnings_week_start(junk)


# ---------------------------------------------------------------- OCR word mapping

def test_words_map_to_weekday_columns_and_before_after_halves(importer, monkeypatch):
    # 1000 px wide: each weekday is a 200 px column; its left half is before the open.
    fake = with_ocr(monkeypatch, FakeOcr(words=[
        word("OPEN", 20, 40),            # header band (top 20%): "Before Open" is not the ticker OPEN
        word("NVDA", 50, 60),            # also header band -> ignored
        word("NVDA", 250, 200),          # centre 270 -> Tuesday, left half -> bmo
        word("NVDA", 252, 300),          # the same cell read twice -> one row
        word("aapl,", 150, 200),         # centre 170 -> Monday, right half -> amc (punctuation stripped)
        word("MSFT", 900, 450),          # centre 920 -> Friday, right half -> amc
        word("TSLA", 600, 200),          # not on the watchlist
        word("OPEN", 610, 250),          # body text: OPEN is on this watchlist -> Thursday bmo
        "not a word",
    ]))
    result = importer.state.analyze_earnings_screenshot(png_data_uri(), "2026-11-05")
    assert result["weekStart"] == "2026-11-02"
    assert [(row["symbol"], row["date"], row["timingCode"]) for row in result["candidates"]] == [
        ("AAPL", "2026-11-02", "amc"),
        ("NVDA", "2026-11-03", "bmo"),
        ("OPEN", "2026-11-05", "bmo"),
        ("MSFT", "2026-11-06", "amc"),
    ]
    assert result["matchCount"] == 4 and result["source"] == "Local Windows OCR"
    assert result["candidates"][1]["timing"] == "Before market open"
    assert all(not Path(path).exists() for path in fake.image_paths), "the screenshot is deleted after OCR"


@pytest.mark.parametrize("fake,message", [
    (FakeOcr(returncode=1, stderr="line one\nWindows OCR is not available for the current user profile."),
     "Local OCR could not read this screenshot: Windows OCR is not available for the current user profile."),
    (FakeOcr(timeout=True), "Local OCR timed out. Try a smaller screenshot."),
])
def test_the_screenshot_is_deleted_even_when_ocr_fails(importer, monkeypatch, fake, message):
    with_ocr(monkeypatch, fake)
    with pytest.raises(ValueError) as error:
        importer.state.analyze_earnings_screenshot(png_data_uri(), "2026-11-02")
    assert str(error.value) == message
    assert fake.image_paths and not Path(fake.image_paths[0]).exists()


# ---------------------------------------------------------------- save / clear

def test_save_keeps_watchlist_rows_counts_the_rest_and_persists(importer):
    result = importer.state.save_manual_earnings_imports([
        {"symbol": "nvda", "date": "2026-11-18", "timingCode": "after close"},
        {"symbol": "TSLA", "date": "2026-11-18", "timingCode": "amc"},       # not on the watchlist
        {"symbol": "AAPL", "date": "someday", "timingCode": "bmo"},          # bad date
        "junk",
        {"symbol": "MSFT", "date": "2026-11-20", "timingCode": "bmo"},
        {"symbol": "MSFT", "date": "2026-11-21", "timingCode": "weird"},     # last one per symbol wins
    ])
    assert result == {"imported": 2, "skipped": 3, "manualImportCount": 2}
    saved = json.loads(importer.file.read_text(encoding="utf-8"))
    assert [(row["symbol"], row["date"], row["timingCode"]) for row in saved] == [
        ("NVDA", "2026-11-18", "amc"), ("MSFT", "2026-11-21", "tbd"),
    ]
    assert importer.state.earnings_calendar_cache is None, "the calendar re-reads after an import"


def test_a_new_import_replaces_that_symbols_old_row_and_keeps_the_others(importer):
    importer.state.save_manual_earnings_imports([{"symbol": "NVDA", "date": "2026-11-18", "timingCode": "amc"},
                                                 {"symbol": "AAPL", "date": "2026-11-19", "timingCode": "bmo"}])
    result = importer.state.save_manual_earnings_imports([{"symbol": "NVDA", "date": "2026-11-25", "timingCode": "bmo"}])
    assert result["manualImportCount"] == 2
    assert {(row["symbol"], row["date"]) for row in importer.state.earnings_manual_imports} == {
        ("NVDA", "2026-11-25"), ("AAPL", "2026-11-19"),
    }


def test_only_the_first_100_rows_are_read(importer):
    rows = [{"symbol": "TSLA", "date": "2026-11-18", "timingCode": "amc"}] * 100
    rows.append({"symbol": "NVDA", "date": "2026-11-18", "timingCode": "amc"})   # row 101
    with pytest.raises(ValueError, match="^Select at least one valid watchlist earnings row.$"):
        importer.state.save_manual_earnings_imports(rows)


@pytest.mark.parametrize("rows,message", [
    ({"symbol": "NVDA"}, "Screenshot imports must be a list of reviewed rows."),
    ([], "Select at least one valid watchlist earnings row."),
    ([{"symbol": "TSLA", "date": "2026-11-18"}], "Select at least one valid watchlist earnings row."),
])
def test_nothing_valid_to_save_is_refused(importer, rows, message):
    with pytest.raises(ValueError) as error:
        importer.state.save_manual_earnings_imports(rows)
    assert str(error.value) == message
    assert not importer.file.exists()


def test_clear_reports_how_many_were_removed_and_persists_an_empty_list(importer):
    importer.state.save_manual_earnings_imports([{"symbol": "NVDA", "date": "2026-11-18", "timingCode": "amc"},
                                                 {"symbol": "AAPL", "date": "2026-11-19", "timingCode": "bmo"}])
    importer.state.earnings_calendar_cache = ("cached",)
    assert importer.state.clear_manual_earnings_imports() == {"removed": 2, "manualImportCount": 0}
    assert json.loads(importer.file.read_text(encoding="utf-8")) == []
    assert importer.state.earnings_calendar_cache is None
    assert importer.state.clear_manual_earnings_imports() == {"removed": 0, "manualImportCount": 0}
