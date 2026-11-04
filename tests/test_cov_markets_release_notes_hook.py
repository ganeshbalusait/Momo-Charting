"""Unit: the pre-commit release-notes gate (scripts/check_release_notes.py),
2026-11-04 coverage. It is what keeps the in-app Release Notes page honest:
a commit that ships app code without a fresh top entry is refused.

main() is driven with the git reads faked and the clock pinned.
"""
from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_release_notes.py"
spec = importlib.util.spec_from_file_location("check_release_notes_cov", SCRIPT)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

NOW = datetime(2026, 11, 4, 20, 0, tzinfo=timezone.utc)          # 15:00 ET


class PinnedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def entry(at="2026-11-04T14:30:00-05:00", heading="Charts: faster open", items=("The chart opens in one second.",)):
    return {"at": at, "heading": heading, "items": list(items)}


@pytest.mark.parametrize("path,shipped", [
    ("api_server.py", True),
    ("frontend/src/App.jsx", True),
    ("frontend/src/SectorHeatmapPanel.jsx", True),
    ("momx/feed.py", True),
    ("scripts/generate_oi_thinkscript.py", True),
    ("tests/test_cov_markets_sectors_api.py", False),
    ("tests/e2e/markets-sectors.e2e.test.mjs", False),
    ("frontend/src/sectorHeatmap.cov.test.js", False),
    ("frontend/src/releaseNotes.test.js", False),
    ("docs/plan.txt", False),
    ("artifacts/momx_history/2026-11-04.json", False),
    ("AGENTS.md", False),
    ("frontend/public/release-notes.json", False),
    ("scripts/check_release_notes.py", False),
    (".claude/skills/feature-testing/SKILL.md", False),
    ("frontend/public/learn/chart-workstation.png", False),
    ("_probe_stream.py", False),
])
def test_what_counts_as_shipping(path, shipped):
    assert gate.ships(path) is shipped


@pytest.mark.parametrize("value,ok", [
    ("2026-11-04T14:30:00-05:00", True), ("2026-08-31T18:00:00-04:00", True), ("2026-11-04T19:30:00Z", True),
    ("2026-11-04T14:30:00", False),           # no zone: cannot be placed on the ET day
    ("yesterday", False), ("", False), (None, False), (1730750000, False),
])
def test_parse_stamp_needs_an_iso_time_with_a_zone(value, ok):
    assert (gate.parse_stamp(value) is not None) is ok


def test_top_entry_is_the_first_release_or_none():
    assert gate.top_entry({"releases": [entry(heading="newest"), entry(heading="older")]})["heading"] == "newest"
    for empty in (None, {}, {"releases": []}, {"releases": "x"}, {"releases": ["not a dict"]}):
        assert gate.top_entry(empty) is None


@pytest.fixture
def repo(monkeypatch):
    """main() with the index and HEAD faked."""
    state = {"staged": [], "index": None, "head": {"releases": [entry(at="2026-11-03T10:00:00-05:00", heading="old")]}}
    monkeypatch.setattr(gate, "datetime", PinnedDatetime)
    monkeypatch.setattr(gate, "staged_paths", lambda: list(state["staged"]))
    monkeypatch.setattr(gate, "load_json", lambda ref: state["head"] if ref == "HEAD" else state["index"])
    return state


def run(capsys):
    code = gate.main()
    return code, capsys.readouterr().out


def test_a_commit_of_only_tests_and_docs_needs_no_notes(repo, capsys):
    repo["staged"] = ["tests/test_cov_markets_sectors_api.py", "docs/notes.md"]
    assert run(capsys) == (0, "")


def test_app_code_without_the_notes_file_is_refused(repo, capsys):
    repo["staged"] = ["api_server.py"]
    code, out = run(capsys)
    assert code == 1
    assert "COMMIT REFUSED" in out and "frontend/public/release-notes.json is not part of this commit." in out
    assert "- api_server.py" in out


def test_app_code_with_a_fresh_top_entry_passes(repo, capsys):
    repo["staged"] = ["frontend/src/App.jsx", gate.NOTES_PATH]
    repo["index"] = {"releases": [entry(), *repo["head"]["releases"]]}
    code, out = run(capsys)
    assert code == 0
    assert 'release notes ok: "Charts: faster open" (1 item)' in out


@pytest.mark.parametrize("top,reason", [
    (entry(at="2026-11-03T14:00:00-05:00"), "more than 24h ago"),
    (entry(at="2026-11-04T18:00:00-05:00"), "stamped in the future"),
    (entry(at="2026-11-04T14:30:00"), "not an ISO time with a zone"),
    (entry(heading="  "), "empty heading"),
    (entry(items=("", "  ")), "no items"),
])
def test_a_stale_or_empty_top_entry_is_refused(repo, capsys, top, reason):
    repo["staged"] = ["api_server.py", gate.NOTES_PATH]
    repo["index"] = {"releases": [top]}
    code, out = run(capsys)
    assert code == 1 and reason in out


def test_restaging_the_previous_top_entry_is_not_a_new_note(repo, capsys):
    repo["staged"] = ["api_server.py", gate.NOTES_PATH]
    repo["head"] = {"releases": [entry()]}
    repo["index"] = {"releases": [entry()]}
    code, out = run(capsys)
    assert code == 1 and "unchanged from the last commit" in out


def test_a_torn_notes_file_is_refused(repo, capsys):
    repo["staged"] = ["api_server.py", gate.NOTES_PATH]
    repo["index"] = None
    code, out = run(capsys)
    assert code == 1 and "is not valid JSON" in out
