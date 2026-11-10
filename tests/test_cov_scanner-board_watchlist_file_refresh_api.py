"""Integration: the real scanner worker picks up a My Watchlist edit without a restart.

The api_server rewrites watchlist.txt when the trader removes a ticker; the
worker (a separate process) must see the new list on its next read. Here the
real momx_worker.Handler serves GET /api/momx-scanner/lists over HTTP while the
file under it changes (tests/worker_harness.py, no network, no live files).
"""
from __future__ import annotations

import os

import config
from worker_harness import running_worker

LISTS = "/api/momx-scanner/lists"


def _watchlist_entry(body):
    for entry in body.get("lists") or []:
        if entry.get("name") == "Watchlist":
            return entry
    raise AssertionError(f"no Watchlist entry in {body!r}")


def test_a_ticker_removed_from_watchlist_txt_drops_from_the_worker_list(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.txt"
    path.write_text("AAPL\nKVUE\nMSFT\n", encoding="utf-8")
    monkeypatch.setattr(config, "WATCHLIST_PATH", path)
    monkeypatch.setattr(config, "_WATCHLIST_SEEN", None)
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(LISTS)
        assert status == 200, body
        assert _watchlist_entry(body)["count"] == 3

        path.write_text("AAPL\nMSFT\n", encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))

        status, body = worker.get(LISTS)
        assert status == 200, body
        assert _watchlist_entry(body)["count"] == 2


def test_an_emptied_watchlist_txt_never_blanks_the_worker_list(tmp_path, monkeypatch):
    path = tmp_path / "watchlist.txt"
    path.write_text("AAPL\nMSFT\n", encoding="utf-8")
    monkeypatch.setattr(config, "WATCHLIST_PATH", path)
    monkeypatch.setattr(config, "_WATCHLIST_SEEN", None)
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get(LISTS)
        assert _watchlist_entry(body)["count"] == 2
        path.write_text("\n", encoding="utf-8")
        st = path.stat()
        os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
        status, body = worker.get(LISTS)
        assert status == 200, body
        assert _watchlist_entry(body)["count"] == 2
