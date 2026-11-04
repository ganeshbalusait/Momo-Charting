"""Unit: scripts/check_release_notes.py - the pre-commit gate that refuses to
ship app code without a fresh release-notes entry (coverage 2026-11-04, group
"shell"). git is never called: staged_paths and load_json are replaced. The
last test reads the real notes file, read-only.
"""
from __future__ import annotations

import importlib.util
import io
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_release_notes.py"
NOTES = "frontend/public/release-notes.json"


@pytest.fixture
def gate(monkeypatch):
    spec = importlib.util.spec_from_file_location("check_release_notes_under_test", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _stamp(delta=timedelta()):
    return (datetime.now(timezone.utc) + delta).astimezone(timezone(timedelta(hours=-5))).isoformat(timespec="seconds")


def _notes(*entries):
    return {"releases": list(entries)}


OLD_TOP = {"at": "2026-11-01T09:00:00-04:00", "heading": "Older deploy", "items": ["an older fix"]}


def _run(gate, monkeypatch, capsys, staged, staged_notes=None, head_notes=None):
    monkeypatch.setattr(gate, "staged_paths", lambda: list(staged))
    monkeypatch.setattr(gate, "load_json", lambda ref: staged_notes if ref == "" else head_notes)
    code = gate.main()
    return code, capsys.readouterr().out


@pytest.mark.parametrize("path", [
    "tests/test_x.py", "docs/plan.md", "artifacts/run.json", "README.md", "frontend/src/foo.test.js",
    "frontend/src/foo.cov.test.js", "test_scratch.py", "_patch1.py", NOTES, "scripts/check_release_notes.py",
    "scripts/git-hooks/pre-commit", ".claude/skills/x/SKILL.md", "notes.txt", "shot.png",
])
def test_tests_docs_and_artifacts_do_not_count_as_shipping(gate, path):
    assert gate.ships(path) is False


@pytest.mark.parametrize("path", ["api_server.py", "frontend/src/App.jsx", "scripts/backup_agx.py", "momx/service.py",
                                  "frontend/public/sw.js", "frontend/index.html"])
def test_app_code_counts_as_shipping(gate, path):
    assert gate.ships(path) is True


def test_stamps_need_a_zone(gate):
    assert gate.parse_stamp("2026-11-04T10:00:00Z") == datetime(2026, 11, 4, 10, tzinfo=timezone.utc)
    assert gate.parse_stamp("2026-11-04T10:00:00-05:00").utcoffset() == timedelta(hours=-5)
    assert gate.parse_stamp("2026-07-04T10:00:00-04:00").utcoffset() == timedelta(hours=-4)
    for bad in ("2026-11-04T10:00:00", "", "   ", "yesterday", None, 20261104):
        assert gate.parse_stamp(bad) is None


def test_shipping_without_the_notes_file_is_refused(gate, monkeypatch, capsys):
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py"])
    assert code == 1
    assert "COMMIT REFUSED" in out and f"{NOTES} is not part of this commit" in out
    assert "- api_server.py" in out


def test_an_unchanged_top_entry_is_refused(gate, monkeypatch, capsys):
    notes = _notes(OLD_TOP)
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py", NOTES], notes, notes)
    assert code == 1 and "unchanged from the last commit" in out


@pytest.mark.parametrize("delta, reason", [(timedelta(hours=-25), "more than 24h ago"), (timedelta(hours=2), "in the future")])
def test_an_old_or_future_stamp_is_refused(gate, monkeypatch, capsys, delta, reason):
    entry = {"at": _stamp(delta), "heading": "Fix", "items": ["x"]}
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py", NOTES], _notes(entry, OLD_TOP), _notes(OLD_TOP))
    assert code == 1 and reason in out


@pytest.mark.parametrize("entry, reason", [
    ({"heading": " ", "items": ["x"]}, "empty heading"),
    ({"heading": "Fix", "items": ["", "  "]}, "no items"),
    ({"heading": "Fix", "items": []}, "no items"),
])
def test_an_entry_must_say_something(gate, monkeypatch, capsys, entry, reason):
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py", NOTES], _notes({"at": _stamp(), **entry}), _notes(OLD_TOP))
    assert code == 1 and reason in out


def test_a_naive_stamp_or_invalid_json_is_refused(gate, monkeypatch, capsys):
    naive = {"at": "2026-11-04T10:00:00", "heading": "Fix", "items": ["x"]}
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py", NOTES], _notes(naive), _notes(OLD_TOP))
    assert code == 1 and "not an ISO time with a zone" in out
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py", NOTES], None, _notes(OLD_TOP))
    assert code == 1 and "not valid JSON" in out


def test_a_tests_only_commit_needs_no_notes(gate, monkeypatch, capsys):
    code, out = _run(gate, monkeypatch, capsys, ["tests/test_cov_shell_x.py", "frontend/src/a.cov.test.js"])
    assert (code, out) == (0, "")


def test_a_fresh_entry_lets_the_commit_through(gate, monkeypatch, capsys):
    entry = {"at": _stamp(timedelta(minutes=-5)), "heading": "Settings: Renew both", "items": ["one", "two"]}
    code, out = _run(gate, monkeypatch, capsys, ["api_server.py", NOTES], _notes(entry, OLD_TOP), _notes(OLD_TOP))
    assert code == 0
    assert 'release notes ok: "Settings: Renew both" (2 items)' in out


def test_printing_an_emoji_heading_never_crashes_on_a_cp1252_console(gate, monkeypatch):
    raw = io.BytesIO()
    console = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", console)
    gate.say("Click the ⚡ bolt → renew")
    console.flush()
    assert raw.getvalue().decode("cp1252").startswith("Click the ? bolt ? renew")


def test_the_real_release_notes_file_is_valid_and_newest_first(gate):
    data = json.loads((REPO / NOTES).read_text(encoding="utf-8"))
    releases = data["releases"]
    assert releases, "the notes file has no entries"
    stamps = []
    for index, entry in enumerate(releases):
        stamp = gate.parse_stamp(entry.get("at"))
        assert stamp is not None, f"entry {index} ({entry.get('heading')!r}) has no zoned stamp"
        assert str(entry.get("heading") or "").strip(), f"entry {index} has no heading"
        assert [i for i in entry.get("items") or [] if str(i).strip()], f"entry {index} has no items"
        stamps.append(stamp)
    assert stamps[0] == max(stamps), "the top entry must be the newest deploy"
