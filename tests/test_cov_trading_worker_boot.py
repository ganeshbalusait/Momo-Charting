"""Unit: the scanner worker's boot (momx_worker.main) and the paper mirror's off switch.

main() starts the paper mirror (Alpaca PAPER orders), the shadow AI, the
learning team and the spread reader, then binds :3010. Each starter is
replaced here by a recorder or a failure - no real loop, thread pool, order
or port is ever started - to prove the scanner always binds even when every
background starter fails, and that the mirror's off switch stops orders.
"""
from __future__ import annotations

import signal as real_signal
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import config
import momx_worker
from momx import ai_filter, option_spread, paper_mirror

ET = ZoneInfo("America/New_York")


def test_the_worker_binds_its_port_even_when_every_background_starter_fails(monkeypatch):
    bound, learning = [], []

    class FakeServer:
        def __init__(self, address, handler):
            bound.append((address, handler))

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            bound.append("closed")

    def fail(*args, **kwargs):
        raise RuntimeError("starter broke")

    monkeypatch.setattr(momx_worker, "_deprioritise", lambda: None)
    monkeypatch.setattr(momx_worker, "atexit", SimpleNamespace(register=lambda fn: None))
    monkeypatch.setattr(momx_worker, "signal", SimpleNamespace(signal=lambda *a: None, SIGTERM=real_signal.SIGTERM, SIGINT=real_signal.SIGINT))
    monkeypatch.setattr(momx_worker.service, "start_warmer", lambda: None)
    monkeypatch.setattr(momx_worker, "_momo_loop", lambda: None)
    monkeypatch.setattr(momx_worker, "_movers_loop", lambda: None)
    monkeypatch.setattr(paper_mirror, "start", fail)
    monkeypatch.setattr(ai_filter, "start", fail)
    monkeypatch.setattr(option_spread, "start", fail)
    monkeypatch.setattr(momx_worker, "start_learning_background", lambda books: learning.append(sorted(books)))
    monkeypatch.setattr(momx_worker, "ScannerHTTPServer", FakeServer)

    assert momx_worker.main() == 0
    assert bound == [(("127.0.0.1", momx_worker.PORT), momx_worker.Handler), "closed"]
    assert learning == [["bear", "bull"]]


class RecordingClient:
    def __init__(self):
        self.credentials = SimpleNamespace(paper=True)
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append(name)
            raise AssertionError(f"{name} called while the mirror is switched off")
        return record


class Book:
    def trades(self):
        return [{"rule": "go", "symbol": "AAPL", "at": "2026-10-19T09:58:00-04:00", "status": "open", "entry": 1.2,
                 "contract": "AAPL261023C00250000", "lastBid": 1.1, "lastAsk": 1.3,
                 "lastAt": "2026-10-19T09:59:40-04:00", "jevP": 0.25}]


def off_mirror(tmp_path, made):
    def factory():
        made.append(1)
        return RecordingClient()

    return paper_mirror.PaperMirror({"bull": Book()}, tmp_path / "paper3", client_factory=factory, account="paper3")


def test_the_environment_off_switch_stops_every_order_and_shows_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_PAPER_MIRROR", "0")
    made = []
    m = off_mirror(tmp_path, made)
    m.tick(datetime(2026, 10, 19, 10, 0, tzinfo=ET))          # a Monday, inside the entry window
    assert made == []
    assert m.status()["enabled"] is False
    assert not (tmp_path / "paper3").exists()


def test_the_off_file_switch_stops_orders_too(tmp_path, monkeypatch):
    monkeypatch.delenv("AGX_MOMX_PAPER_MIRROR", raising=False)
    monkeypatch.setattr(config, "ARTIFACTS_DIR", tmp_path)
    (tmp_path / "paper_mirror_off").write_text("", encoding="utf-8")
    made = []
    m = off_mirror(tmp_path, made)
    m.tick(datetime(2026, 10, 19, 10, 0, tzinfo=ET))
    assert made == [] and m.status()["enabled"] is False
