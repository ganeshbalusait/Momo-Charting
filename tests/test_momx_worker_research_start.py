import threading
from momx_worker import start_learning_background
from momx import paper_learning


def test_slow_research_recovery_does_not_block_scanner_startup(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    books = {"bull": object()}
    def slow(value):
        assert value is books
        entered.set()
        assert release.wait(5)
    monkeypatch.setattr(paper_learning, "start", slow)
    thread = start_learning_background(books)
    try:
        assert entered.wait(2)
        assert thread.is_alive() and thread.daemon
    finally:
        release.set()
        thread.join(2)
    assert not thread.is_alive()
