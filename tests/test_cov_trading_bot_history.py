"""Unit: api_server._momx_bot_history - how many bot day files one Bot request reads.

The Range buttons send days=1..180. Zero / negative must still read today, and
nothing may ever read past the bot's own retention (KEEP_DAYS) - a 180D answer
is already ~27 MB of work.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

import api_server
from momx import option_track as ot


@pytest.fixture(scope="module")
def day_files(tmp_path_factory):
    root = tmp_path_factory.mktemp("bot-history")
    folder = root / ot.DIRNAME
    folder.mkdir()
    first = date(2026, 1, 1)
    for i in range(ot.KEEP_DAYS + 3):
        day = (first + timedelta(days=i)).isoformat()
        (folder / f"{day}.json").write_text(json.dumps({"trades": [{"rule": "go", "symbol": f"S{i}"}]}), encoding="utf-8")
    return root


@pytest.fixture
def base(day_files, tmp_path, monkeypatch):
    monkeypatch.setattr(api_server, "ARTIFACTS_DIR", tmp_path / "artifacts")   # no live AI scores
    return day_files


@pytest.mark.parametrize("days,expected", [(0, 1), (-5, 1), (1, 1), (5, 5), (180, ot.KEEP_DAYS), (99999, ot.KEEP_DAYS)])
def test_the_range_is_clamped_to_one_day_and_to_the_retention(base, days, expected):
    history = api_server._momx_bot_history(base, days)
    assert len(history) == expected
    newest = (date(2026, 1, 1) + timedelta(days=ot.KEEP_DAYS + 2)).isoformat()
    assert history[0]["day"] == newest
    assert [d["day"] for d in history] == sorted((d["day"] for d in history), reverse=True)
