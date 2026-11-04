"""Integration: the real pre-commit hook (scripts/git-hooks/pre-commit running
scripts/check_release_notes.py) inside a throwaway git repository (coverage
2026-11-04, group "shell"). Real `git commit` calls; never the live repo.

The hook looks for .venv/Scripts/python.exe first. The throwaway repo gets a
copy of this checkout's venv launcher and pyvenv.cfg so the hook's own lookup
succeeds (the bare `python` fallback is the Microsoft Store stub on this PC).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

LIVE = Path(__file__).resolve().parents[1]
NOTES = "frontend/public/release-notes.json"
OLD = {"at": "2026-11-01T09:00:00-04:00", "heading": "Older deploy", "items": ["an older fix"]}


def _git(repo: Path, *args: str, env=None):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)


@pytest.fixture
def repo(tmp_path):
    launcher = LIVE / ".venv" / "Scripts" / "python.exe"
    if not launcher.exists() or not (LIVE / ".venv" / "pyvenv.cfg").exists():
        pytest.skip("this checkout has no Windows venv to hand the hook")
    root = tmp_path / "repo"
    (root / "scripts" / "git-hooks").mkdir(parents=True)
    (root / ".venv" / "Scripts").mkdir(parents=True)
    (root / "frontend" / "public").mkdir(parents=True)
    (root / "tests").mkdir()
    shutil.copy2(LIVE / "scripts" / "check_release_notes.py", root / "scripts" / "check_release_notes.py")
    shutil.copy2(LIVE / "scripts" / "git-hooks" / "pre-commit", root / "scripts" / "git-hooks" / "pre-commit")
    shutil.copy2(launcher, root / ".venv" / "Scripts" / "python.exe")
    shutil.copy2(LIVE / ".venv" / "pyvenv.cfg", root / ".venv" / "pyvenv.cfg")
    (root / ".gitignore").write_text(".venv/\n")
    (root / NOTES).write_text(json.dumps({"releases": [OLD]}, indent=2))
    (root / "api_server.py").write_text("VERSION = 1\n")

    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"),
           "HOME": str(tmp_path), "GIT_TERMINAL_PROMPT": "0"}
    (tmp_path / "gitconfig").write_text("")
    for args in (("init", "-q"), ("config", "user.name", "Cov Test"), ("config", "user.email", "cov@example.com"),
                 ("config", "commit.gpgsign", "false"), ("config", "core.hooksPath", "scripts/git-hooks"),
                 ("add", "-A"), ("commit", "-q", "--no-verify", "-m", "baseline")):
        result = _git(root, *args, env=env)
        assert result.returncode == 0, result.stderr
    return root, env


def _commit(root, env, message="change"):
    result = _git(root, "commit", "-m", message, env=env)
    return result.returncode, result.stdout + result.stderr


def _new_top(heading="Settings: something the trader notices", minutes_ago=2):
    at = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).astimezone(timezone(timedelta(hours=-5)))
    return {"at": at.isoformat(timespec="seconds"), "heading": heading, "items": ["what changed, in plain words"]}


def test_app_code_without_notes_is_refused_by_a_real_commit(repo):
    root, env = repo
    (root / "api_server.py").write_text("VERSION = 2\n")
    _git(root, "add", "api_server.py", env=env)
    code, out = _commit(root, env)
    assert code != 0
    assert "COMMIT REFUSED" in out and "api_server.py" in out
    assert _git(root, "log", "--oneline", env=env).stdout.count("\n") == 1, "nothing was committed"


def test_app_code_with_a_fresh_top_entry_commits(repo):
    root, env = repo
    (root / "api_server.py").write_text("VERSION = 3\n")
    (root / NOTES).write_text(json.dumps({"releases": [_new_top(), OLD]}, indent=2))
    _git(root, "add", "api_server.py", NOTES, env=env)
    code, out = _commit(root, env)
    assert code == 0, out
    assert 'release notes ok: "Settings: something the trader notices"' in out


def test_a_tests_only_commit_goes_through_without_notes(repo):
    root, env = repo
    (root / "tests" / "test_cov_example.py").write_text("def test_x():\n    assert True\n")
    _git(root, "add", "tests/test_cov_example.py", env=env)
    code, out = _commit(root, env)
    assert code == 0, out
    assert "COMMIT REFUSED" not in out


def test_restaging_the_old_notes_is_not_enough(repo):
    root, env = repo
    (root / "api_server.py").write_text("VERSION = 4\n")
    notes = json.loads((root / NOTES).read_text())
    notes["note"] = "touched, but no new entry"
    (root / NOTES).write_text(json.dumps(notes, indent=2))
    _git(root, "add", "api_server.py", NOTES, env=env)
    code, out = _commit(root, env)
    assert code != 0 and "unchanged from the last commit" in out
