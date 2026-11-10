"""Unit: a My Watchlist edit reaches the MomX scanner without a restart.

2026-11-10: Ganesh deleted KVUE from My Watchlist at 12:34 ET. watchlist.txt
was rewritten, but the scanner worker is a separate process whose
settings.scanner.default_universe was read once at boot, so the board kept
scanning KVUE ("No data came back for KVUE", Watchlist 371 vs 370 in the file).

config.refresh_watchlist_if_changed re-reads the file when its stamp changes;
board._watchlist_seed calls it on every build.
"""
from __future__ import annotations

import os
import types

import pytest

import config
from momx import board


@pytest.fixture
def watch_file(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.txt"
    monkeypatch.setattr(config, "WATCHLIST_PATH", path)
    monkeypatch.setattr(config, "_WATCHLIST_SEEN", None)
    return path


def _write(path, text, bump=0):
    path.write_text(text, encoding="utf-8")
    if bump:  # same-size rewrites inside one mtime tick must still count
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + bump))


def test_a_deleted_ticker_leaves_the_seed_after_the_file_changes(watch_file, monkeypatch):
    _write(watch_file, "AAPL\nKVUE\nMSFT\n")
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["AAPL", "KVUE", "MSFT"])
    assert "KVUE" in board._watchlist_seed()
    _write(watch_file, "AAPL\nMSFT\n", bump=10_000_000)
    assert board._watchlist_seed() == ["AAPL", "MSFT"]


def test_an_added_ticker_joins_the_seed(watch_file, monkeypatch):
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["AAPL"])
    _write(watch_file, "AAPL, nvda\n")
    assert board._watchlist_seed() == ["AAPL", "NVDA"]


def test_an_unchanged_file_is_not_re_read(watch_file, monkeypatch):
    _write(watch_file, "AAPL\n")
    target = types.SimpleNamespace(default_universe=["X"])
    assert config.refresh_watchlist_if_changed(target) is True
    target.default_universe = ["SERVER_EDIT"]  # an in-memory edit stays until the file changes
    assert config.refresh_watchlist_if_changed(target) is False
    assert target.default_universe == ["SERVER_EDIT"]


def test_an_empty_file_never_blanks_the_list(watch_file):
    _write(watch_file, "   \n")
    target = types.SimpleNamespace(default_universe=["AAPL", "MSFT"])
    assert config.refresh_watchlist_if_changed(target) is False
    assert target.default_universe == ["AAPL", "MSFT"]


def test_a_missing_file_keeps_the_current_list(watch_file):
    target = types.SimpleNamespace(default_universe=["AAPL"])
    assert config.refresh_watchlist_if_changed(target) is False
    assert target.default_universe == ["AAPL"]


def test_an_unreadable_file_keeps_the_list_and_retries(watch_file, monkeypatch):
    _write(watch_file, "AAPL\nNVDA\n")
    target = types.SimpleNamespace(default_universe=["AAPL"])
    real = type(watch_file).read_text

    def boom(self, *a, **k):
        raise PermissionError("locked by the writer")

    monkeypatch.setattr(type(watch_file), "read_text", boom)
    assert config.refresh_watchlist_if_changed(target) is False
    assert target.default_universe == ["AAPL"]
    monkeypatch.setattr(type(watch_file), "read_text", real)
    assert config.refresh_watchlist_if_changed(target) is True
    assert target.default_universe == ["AAPL", "NVDA"]


def test_a_materialised_watchlist_key_still_wins(watch_file, tmp_path, monkeypatch):
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(tmp_path / "universe.json"))
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["AAPL"])
    board.save_universe(["TSLA"], board.WATCHLIST_LIST_NAME)
    _write(watch_file, "AAPL\nMSFT\n")
    assert board.load_universe(board.WATCHLIST_LIST_NAME) == ["TSLA"]
    board.reset_universe(board.WATCHLIST_LIST_NAME)
    assert board.load_universe(board.WATCHLIST_LIST_NAME) == ["AAPL", "MSFT"]
