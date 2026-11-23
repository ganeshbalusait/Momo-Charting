"""Preflight archive row against archives written by the REAL scanner history
module (momx/history.py), not hand-made files: the checklist must read the
layout the scanner actually writes.

2026-11-23: the 08:45 push warned every morning that 11 on-demand lists had no
archive. Those lists are built only while a screen has them open; the lists
the scanner keeps warm all day (Mag7, Watchlist) are the ones to grade.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import preflight
from momx import history

ET = ZoneInfo("America/New_York")


def _context(tmp_path, now_et):
    artifacts = tmp_path / "artifacts"
    (artifacts / "premarket_scanner_history").mkdir(parents=True)
    (artifacts / "premarket_scanner_history" / (now_et.date().isoformat() + ".json")).write_text(
        json.dumps({"rows": {"AAPL": {}}}), encoding="utf-8")
    return artifacts, preflight.Context(now_et=now_et, artifacts_dir=artifacts,
                                        history_dir=tmp_path / "preflight")


def test_quiet_warm_boards_plus_unopened_lists_pass(tmp_path):
    now = datetime(2026, 11, 23, 8, 45, tzinfo=ET)
    artifacts, ctx = _context(tmp_path, now)
    root = artifacts / "momx_history"
    # Mag7 quiet (nothing matched), Watchlist with a match: both via the real writer.
    assert history.record_board("Mag7", {"rows": []}, now, directory=root)
    assert history.record_board("Watchlist", {"rows": [{"symbol": "NVDA", "scanPass": True}]},
                                now, directory=root)
    # An on-demand list that ran on Friday but nobody has opened today.
    friday = datetime(2026, 11, 20, 15, 0, tzinfo=ET)
    assert history.record_board("21_EMA_Touch_Daily",
                                {"rows": [{"symbol": "AMD", "scanPass": True}]},
                                friday, directory=root)
    outcome = preflight.check_scanner_history(ctx)
    assert outcome["status"] == "pass", outcome["measured"]
    assert "momx Watchlist 1 symbols" in outcome["measured"]
    assert "momx Mag7 0 symbols" in outcome["measured"]
    assert "not opened yet today (built only while on screen): 21_EMA_Touch_Daily" in outcome["measured"]


def test_a_warm_board_that_never_wrote_today_still_warns(tmp_path):
    now = datetime(2026, 11, 23, 8, 45, tzinfo=ET)
    artifacts, ctx = _context(tmp_path, now)
    root = artifacts / "momx_history"
    assert history.record_board("Mag7", {"rows": []}, now, directory=root)
    friday = datetime(2026, 11, 20, 15, 0, tzinfo=ET)
    assert history.record_board("Watchlist", {"rows": []}, friday, directory=root)
    outcome = preflight.check_scanner_history(ctx)
    assert outcome["status"] == "warn"
    assert "momx Watchlist archive for 2026-11-23 is missing" in outcome["measured"]
