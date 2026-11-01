"""scripts/backup_agx.py: the Bot / AI learning data rides to Google Drive too (2026-10-18)."""

from __future__ import annotations

import importlib.util
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup_agx.py"


def _load(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("backup_agx_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "REPO", tmp_path / "repo")
    monkeypatch.setattr(mod, "log", lambda message: None)
    return mod


def test_learning_archive_holds_the_bot_days_ai_data_and_paper_records(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    repo = tmp_path / "repo"
    files = {
        "artifacts/momx_option_track/2026-10-16.json": '{"trades": []}',
        "artifacts/bear/momx_option_track/2026-10-16.json": '{"trades": []}',
        "artifacts/momx_ai_filter/dataset/2026-10-16.jsonl.gz": "gz",
        "artifacts/momx_ai_filter/model.json": "{}",
        "artifacts/momx_paper_mirror/paper3/2026-10-19.json": "{}",
    }
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text)
    (repo / "artifacts/momx_ai_filter/.model.json.1.2.tmp").write_text("half")    # a save in flight
    out = tmp_path / "drive"
    out.mkdir()
    assert mod.backup_learning(out, "20261018") is True
    with zipfile.ZipFile(out / "learning-20261018.zip") as zf:
        assert sorted(zf.namelist()) == sorted(files)
    assert not list(out.glob("*.partial"))


def test_learning_archive_with_nothing_to_save_reports_failure(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    (tmp_path / "repo").mkdir()
    out = tmp_path / "drive"
    out.mkdir()
    assert mod.backup_learning(out, "20261018") is False
    assert not list(out.iterdir())


def test_prune_keeps_a_week_of_learning_archives(tmp_path, monkeypatch):
    mod = _load(tmp_path, monkeypatch)
    out = tmp_path / "drive"
    out.mkdir()
    for d in range(1, 11):
        (out / f"learning-202610{d:02d}.zip").write_text("x")
    mod.prune(out)
    assert sorted(p.name for p in out.iterdir())[0] == "learning-20261004.zip"
    assert len(list(out.iterdir())) == 7
