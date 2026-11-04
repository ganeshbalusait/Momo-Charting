"""Integration: the restore drill - run scripts/backup_agx.py's main() end to
end into a scratch "Drive" folder, then restore from what it wrote (coverage
2026-11-04, group "shell"). This is the end-to-end proof for a feature with no
screen: a backup only counts if it restores.

Everything is scratch: a small journal, a tiny git repo, fake secrets and
learning files, a fake home folder. Never the live trades.db, .env or Drive.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import zipfile
from pathlib import Path

import pytest

import config

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_agx.py"


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backup_agx_drill", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    repo, home, drive = tmp_path / "repo", tmp_path / "home", tmp_path / "home" / "My Drive"
    for folder in (repo / "artifacts" / "momx_option_track", repo / "artifacts" / "momx_ai_filter", home / ".cloudflared", drive):
        folder.mkdir(parents=True)
    (repo / ".env").write_bytes(b"SCHWAB_CLIENT_ID=drill\n")
    (repo / "artifacts" / "schwab_token.json").write_text('{"token": "drill"}')
    (repo / "artifacts" / "momx_option_track" / "2026-11-03.json").write_text('{"trades": [1, 2]}')
    (repo / "artifacts" / "momx_ai_filter" / "model.json").write_text("{}")
    (repo / "app.py").write_text("print('agx')\n")
    (repo / ".gitignore").write_text("artifacts/\n.env\n")
    (home / ".cloudflared" / "config.yml").write_text("tunnel: drill")
    for args in (("init", "-q"), ("config", "user.email", "d@x"), ("config", "user.name", "d"),
                 ("config", "commit.gpgsign", "false"), ("add", "-A"), ("commit", "-q", "--no-verify", "-m", "drill")):
        _git(repo, *args)
    db = tmp_path / "trades.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT)")
    con.executemany("INSERT INTO trades (symbol) VALUES (?)", [("SPY",), ("AAPL",), ("NVDA",)])
    con.commit()
    con.close()

    monkeypatch.setattr(module, "REPO", repo)
    monkeypatch.setattr(module, "LOG_FILE", tmp_path / "agx-backup.log")
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    # Only scan the scratch profile for a Drive folder, never this PC's real drive letters.
    monkeypatch.setattr(module, "destination", lambda: (drive / "AGX-Backups").mkdir(exist_ok=True) or drive / "AGX-Backups")
    monkeypatch.setattr(module, "warn_if_not_syncing", lambda out: None)
    return module, drive / "AGX-Backups", tmp_path


def test_a_full_run_restores_the_journal_the_code_and_the_secrets(machine):
    module, out, tmp = machine
    assert module.main() == 0
    names = sorted(p.name.split("-")[0] for p in out.iterdir())
    assert names == ["code", "learning", "secrets", "trades"]

    # The journal: unzip and open it.
    with zipfile.ZipFile(next(out.glob("trades-*.db.zip"))) as zf:
        zf.extract("trades.db", tmp / "restore")
    con = sqlite3.connect(tmp / "restore" / "trades.db")
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert [r[0] for r in con.execute("SELECT symbol FROM trades ORDER BY id")] == ["SPY", "AAPL", "NVDA"]
    finally:
        con.close()

    # The code: clone the bundle on a "new machine".
    clone = tmp / "new-machine"
    _git(tmp, "clone", "-q", str(next(out.glob("code-*.bundle"))), str(clone))
    assert (clone / "app.py").read_text() == "print('agx')\n"

    # The secrets and the learning data.
    with zipfile.ZipFile(next(out.glob("secrets-*.zip"))) as zf:
        secrets = set(zf.namelist())
        assert zf.read(".env") == b"SCHWAB_CLIENT_ID=drill\n", "the secret is restored byte for byte"
    assert {".env", "artifacts/schwab_token.json", "cloudflared/config.yml"} <= secrets
    with zipfile.ZipFile(next(out.glob("learning-*.zip"))) as zf:
        assert zf.testzip() is None
        assert "artifacts/momx_option_track/2026-11-03.json" in zf.namelist()

    log = (tmp / "agx-backup.log").read_text(encoding="utf-8")
    assert "all four backups completed and verified" in log


def test_a_run_without_a_journal_still_saves_the_rest_and_says_it_failed(machine, monkeypatch):
    module, out, tmp = machine
    monkeypatch.setattr(config, "DATABASE_PATH", tmp / "missing.db")
    assert module.main() == 1
    assert sorted(p.name.split("-")[0] for p in out.iterdir()) == ["code", "learning", "secrets"]
    assert "FINISHED WITH FAILURES: database" in (tmp / "agx-backup.log").read_text(encoding="utf-8")
