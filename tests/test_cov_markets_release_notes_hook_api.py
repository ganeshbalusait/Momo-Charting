"""Integration: the release-notes commit gate as git runs it - the real
scripts/check_release_notes.py in a scratch git repository, reading the real
index and HEAD (2026-11-04 coverage). Nothing touches this repository's git.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "check_release_notes.py"
NOTES = "frontend/public/release-notes.json"

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def stamp(hours_ago: float) -> str:
    eastern = timezone(timedelta(hours=-5))
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).astimezone(eastern).isoformat(timespec="seconds")


def write_notes(root: Path, *entries: dict) -> None:
    path = root / NOTES
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"note": "test", "releases": list(entries)}, indent=2), encoding="utf-8")


@pytest.fixture
def scratch_repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.email", "tests@example.invalid")
    git(root, "config", "user.name", "Coverage Tests")
    git(root, "config", "commit.gpgsign", "false")
    write_notes(root, {"at": stamp(72), "heading": "Older deploy", "items": ["Something older."]})
    (root / "api_server.py").write_text("print('v1')\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "--no-verify", "-m", "initial")       # the scratch repo has no hook installed
    return root


def gate(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)], cwd=root, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=60)


def test_app_code_staged_without_notes_is_refused_with_the_reason(scratch_repo):
    (scratch_repo / "api_server.py").write_text("print('v2')\n", encoding="utf-8")
    git(scratch_repo, "add", "api_server.py")
    result = gate(scratch_repo)
    assert result.returncode == 1, result.stdout
    assert "COMMIT REFUSED" in result.stdout
    assert f"{NOTES} is not part of this commit." in result.stdout
    assert "- api_server.py" in result.stdout


def test_app_code_with_a_fresh_top_entry_is_allowed(scratch_repo):
    (scratch_repo / "api_server.py").write_text("print('v2')\n", encoding="utf-8")
    write_notes(scratch_repo,
                {"at": stamp(0.25), "heading": "Sectors: tap a tile to chart it", "items": ["Tiles open the 5m chart."]},
                {"at": stamp(72), "heading": "Older deploy", "items": ["Something older."]})
    git(scratch_repo, "add", "api_server.py", NOTES)
    result = gate(scratch_repo)
    assert result.returncode == 0, result.stdout
    assert 'release notes ok: "Sectors: tap a tile to chart it" (1 item)' in result.stdout


def test_notes_staged_but_the_top_entry_is_old_is_refused(scratch_repo):
    (scratch_repo / "api_server.py").write_text("print('v2')\n", encoding="utf-8")
    write_notes(scratch_repo,
                {"at": stamp(30), "heading": "Forgot to restamp", "items": ["x"]},
                {"at": stamp(72), "heading": "Older deploy", "items": ["Something older."]})
    git(scratch_repo, "add", "api_server.py", NOTES)
    result = gate(scratch_repo)
    assert result.returncode == 1
    assert "more than 24h ago" in result.stdout


def test_a_tests_only_commit_passes_without_notes(scratch_repo):
    tests_dir = scratch_repo / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_cov_markets_example.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    (scratch_repo / "frontend" / "src").mkdir(parents=True)
    (scratch_repo / "frontend" / "src" / "x.cov.test.js").write_text("// test\n", encoding="utf-8")
    git(scratch_repo, "add", "-A")
    result = gate(scratch_repo)
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == ""
