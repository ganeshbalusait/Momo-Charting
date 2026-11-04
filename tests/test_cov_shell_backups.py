"""Unit: scripts/backup_agx.py piece by piece - the database snapshot, the
secrets zip, the code bundle, pruning, the destination choice and main()'s
exit code (coverage 2026-11-04, group "shell"). Everything runs in scratch
folders: never the live trades.db, .env or the Google Drive folder.
"""
from __future__ import annotations

import importlib.util
import sqlite3
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

import config

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_agx.py"
DAY = "20261104"


@pytest.fixture
def backup(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backup_agx_cov", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    lines = []
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(module, "REPO", repo)
    monkeypatch.setattr(module, "LOG_FILE", tmp_path / "agx-backup.log")
    monkeypatch.setattr(module, "log", lines.append)
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()
    out = tmp_path / "AGX-Backups"
    out.mkdir()
    module.lines, module.out, module.repo_root = lines, out, repo
    return module


def _journal(path: Path, rows: int = 200) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL)")
    con.executemany("INSERT INTO trades (symbol, pnl) VALUES (?, ?)", [("SPY", i * 1.5) for i in range(rows)])
    con.commit()
    con.close()
    return path


def test_the_snapshot_is_consistent_while_the_app_keeps_writing(backup, tmp_path, monkeypatch):
    db = _journal(tmp_path / "trades.db")
    monkeypatch.setattr(config, "DATABASE_PATH", db)
    stop = threading.Event()

    def writer():
        con = sqlite3.connect(db, timeout=30)
        while not stop.is_set():
            con.execute("INSERT INTO trades (symbol, pnl) VALUES ('NVDA', 1.0)")
            con.commit()
        con.close()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        ok = backup.backup_database(backup.out, DAY)
    finally:
        stop.set()
        thread.join(timeout=10)
    assert ok is True
    archive = backup.out / f"trades-{DAY}.db.zip"
    with zipfile.ZipFile(archive) as zf:
        assert zf.namelist() == ["trades.db"]
        zf.extract("trades.db", tmp_path / "restored")
    restored = sqlite3.connect(tmp_path / "restored" / "trades.db")
    try:
        assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert restored.execute("SELECT COUNT(*) FROM trades WHERE symbol='SPY'").fetchone()[0] == 200
    finally:
        restored.close()
    assert not list((backup.repo_root / "artifacts").glob("_backup_*")), "the staging copy is cleaned up"


def test_a_missing_journal_is_a_reported_failure_with_no_zip(backup, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATABASE_PATH", tmp_path / "nope.db")
    assert backup.backup_database(backup.out, DAY) is False
    assert list(backup.out.iterdir()) == []
    assert any("SKIP database" in line for line in backup.lines)


@pytest.mark.xfail(strict=True, reason=(
    "BUG: a corrupt trades.db makes backup_database RAISE instead of returning False, so "
    "main() dies and the secrets, code and learning backups for that run never happen."))
def test_a_corrupt_journal_fails_only_the_database_piece(backup, tmp_path, monkeypatch):
    bad = tmp_path / "trades.db"
    bad.write_bytes(b"this is not a sqlite database" * 100)
    monkeypatch.setattr(config, "DATABASE_PATH", bad)
    assert backup.backup_database(backup.out, DAY) is False
    assert not list(backup.out.glob("trades-*"))


def test_secrets_zip_holds_what_exists_and_notes_what_is_missing(backup, tmp_path):
    repo, home = backup.repo_root, tmp_path / "home"
    (repo / "artifacts" / "user_tokens").mkdir(parents=True)
    (repo / ".env").write_text("SCHWAB_CLIENT_ID=x\n")
    (repo / "artifacts" / "schwab_token.json").write_text("{}")
    (repo / "artifacts" / "user_tokens" / "u1.schwab_market_data.json").write_text("{}")
    (home / ".cloudflared").mkdir()
    (home / ".cloudflared" / "config.yml").write_text("tunnel: x")
    (home / ".cloudflared" / "abc-123.json").write_text("{}")
    (home / ".cloudflared" / "notes.txt").write_text("not a credential")
    assert backup.backup_secrets(backup.out, DAY) is True
    with zipfile.ZipFile(backup.out / f"secrets-{DAY}.zip") as zf:
        names = sorted(zf.namelist())
    assert names == sorted([".env", "artifacts/schwab_token.json", "artifacts/user_tokens/u1.schwab_market_data.json",
                            "cloudflared/config.yml", "cloudflared/abc-123.json"])
    assert any("watchlist.txt absent, skipped" in line for line in backup.lines)


def test_nothing_to_protect_is_a_failure_not_an_empty_zip(backup):
    assert backup.backup_secrets(backup.out, DAY) is False
    assert not (backup.out / f"secrets-{DAY}.zip").exists()


def test_the_code_bundle_verifies_and_holds_every_branch(backup):
    repo = backup.repo_root
    for args in (("init", "-q"), ("config", "user.email", "c@x"), ("config", "user.name", "c"),
                 ("config", "commit.gpgsign", "false")):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    (repo / "app.py").write_text("print('hi')\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-q", "--no-verify", "-m", "one"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "branch", "side"], cwd=repo, check=True, capture_output=True)
    assert backup.backup_code(backup.out, DAY) is True
    bundle = backup.out / f"code-{DAY}.bundle"
    verify = subprocess.run(["git", "bundle", "verify", str(bundle)], cwd=repo, capture_output=True, text=True)
    heads = subprocess.run(["git", "bundle", "list-heads", str(bundle)], cwd=repo, capture_output=True, text=True).stdout
    assert verify.returncode == 0, verify.stderr
    assert "refs/heads/side" in heads


def test_a_folder_that_is_not_a_repo_fails_the_code_piece(backup):
    assert backup.backup_code(backup.out, DAY) is False
    assert any("FAILED code bundle" in line for line in backup.lines)


def test_prune_keeps_the_newest_of_each_kind_and_nothing_else_is_touched(backup):
    for kind in ("trades", "secrets", "code", "learning"):
        for day in range(1, 11):
            (backup.out / f"{kind}-202610{day:02d}.zip").write_text("x")
    (backup.out / "README-restore.txt").write_text("keep me")
    backup.prune(backup.out)
    left = {kind: len(list(backup.out.glob(f"{kind}-*"))) for kind in ("trades", "secrets", "code", "learning")}
    assert left == {"trades": 7, "secrets": 7, "code": 3, "learning": 7}
    assert (backup.out / "README-restore.txt").exists()
    assert sorted(p.name for p in backup.out.glob("code-*")) == ["code-20261008.zip", "code-20261009.zip", "code-20261010.zip"]


def test_google_drive_wins_over_onedrive(backup, tmp_path):
    home = tmp_path / "home"
    (home / "OneDrive").mkdir()
    (home / "My Drive").mkdir()
    assert backup.destination() == home / "My Drive" / "AGX-Backups"
    assert (home / "My Drive" / "AGX-Backups").is_dir()
    assert not (home / "OneDrive" / "AGX-Backups").exists()


@pytest.mark.parametrize("results, code", [((True, True, True, True), 0), ((True, False, True, True), 1),
                                           ((False, False, False, False), 1)])
def test_main_exits_non_zero_when_any_piece_failed(backup, monkeypatch, results, code):
    monkeypatch.setattr(backup, "destination", lambda: backup.out)
    monkeypatch.setattr(backup, "warn_if_not_syncing", lambda out: None)
    for name, ok in zip(("backup_database", "backup_secrets", "backup_code", "backup_learning"), results):
        monkeypatch.setattr(backup, name, lambda out, day, ok=ok: ok)
    assert backup.main() == code
    final = backup.lines[-1]
    assert final.startswith("FINISHED WITH FAILURES") if code else final == "all four backups completed and verified"
