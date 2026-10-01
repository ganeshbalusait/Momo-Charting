"""Per-list warm threads: a slow board must not stall a fast one.

WHY THIS EXISTS (measured 2026-09-01, right after the volume fix went live)

    11:05:42  Mag7 builds=11   Watchlist building...
    11:06:35  Mag7 builds=11   Watchlist building...   <- frozen 80s
    11:07:01  Mag7 builds=11   Watchlist done
    11:07:54  Mag7 builds=13   <- catches up in a burst

Mag7 is 10 symbols and rebuilds in ~8-18s, but it shared ONE warmer thread
with the 357-name Watchlist, so it stopped dead for the length of every
Watchlist build. Its 15s floor was fiction; the real gap was 60-100s and every
momentum alert inherited it.

WHAT MUST NOT REGRESS
    Two concurrent 355-symbol pulls -- each driving the process pool in
    momx.board -- is the CPU/network pile-up that has starved this box's chart
    engine before. Threads made that possible for the first time, so the heavy
    gate is the load-bearing part of this change and most of the tests below
    are about IT, not about speed.

NOTHING HERE TOUCHES THE NETWORK. Builds are fakes with real blocking, so the
concurrency being asserted is genuine rather than simulated.
"""

from __future__ import annotations

import threading
import time

import pytest

from momx import board, service


@pytest.fixture(autouse=True)
def _scratch_universe_path(monkeypatch, tmp_path):
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(tmp_path / "universe.json"))


@pytest.fixture(autouse=True)
def _scratch_board_cache_dir(monkeypatch, tmp_path):
    monkeypatch.setenv(service.BOARD_CACHE_DIR_ENV, str(tmp_path / "board_cache"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Fresh module state, and always stop the threads a test started.

    Written defensively (getattr) so the suite can also be pointed at the OLD
    single-threaded service to confirm these tests actually discriminate. A
    test that passes against both implementations proves nothing, and this
    file's whole claim is that the sequential warmer fails it.
    """
    service._STATES.clear()
    getattr(service, "_THREADS", {}).clear()
    getattr(service, "_STOP", threading.Event()).clear()
    yield
    stop = getattr(service, "_stop_warmer", None)
    if stop is not None:
        stop(timeout=5.0)
    service._STATES.clear()
    getattr(service, "_THREADS", {}).clear()


def install(monkeypatch, sizes, durations, failures=()):
    """Fake the universe sizes and the cost of building each list.

    ``sizes``      list name -> how many symbols (drives heavy/light).
    ``durations``  list name -> how long its build blocks, in seconds.
    Returns a recorder with ``.log`` of (event, name, monotonic) tuples.
    """

    class Recorder:
        def __init__(self):
            self.log = []
            self.lock = threading.Lock()
            self.overlap = []          # names concurrently inside a heavy build
            self.inflight = set()

        def note(self, event, name):
            with self.lock:
                self.log.append((event, name, time.monotonic()))

        def builds(self, name):
            return sum(1 for e, n, _ in self.log if e == "end" and n == name)

    rec = Recorder()

    monkeypatch.setattr(board, "list_names", lambda *a, **k: list(sizes))
    monkeypatch.setattr(
        board, "load_universe",
        lambda name=None, **k: ["S%d" % i for i in range(sizes.get(name, 1))],
    )

    def fake_build(name):
        rec.note("start", name)
        with rec.lock:
            rec.inflight.add(name)
            if len(rec.inflight) > 1:
                rec.overlap.append(tuple(sorted(rec.inflight)))
        try:
            if name in failures:
                raise RuntimeError("build blew up: %s" % name)
            time.sleep(durations.get(name, 0.01))
        finally:
            with rec.lock:
                rec.inflight.discard(name)
            rec.note("end", name)
        return durations.get(name, 0.01)

    monkeypatch.setattr(service, "_build_once", fake_build)
    # The warm loop runs the grade nightly after each build; after 16:15 ET the
    # real one would fetch bars from the network. Record the calls instead.
    monkeypatch.setattr(service, "_grade_nightly", lambda: rec.note("nightly", None))
    # Rebuild promptly so a short test sees several cycles.
    monkeypatch.setattr(service, "_next_delay", lambda elapsed, failed: 0.02)
    return rec


# ---------------------------------------------------------------------------
# the point of the change
# ---------------------------------------------------------------------------

def test_a_slow_list_does_not_stall_a_fast_one(monkeypatch):
    """The regression this whole change exists for.

    Against the old single-threaded warmer this FAILS: Mag7 could only rebuild
    between Watchlist builds, so it managed about one cycle, not many.
    """
    rec = install(
        monkeypatch,
        sizes={"Mag7": 10, "Watchlist": 357},
        durations={"Mag7": 0.02, "Watchlist": 1.5},
    )
    service.start_warmer()
    time.sleep(1.0)          # still inside the FIRST Watchlist build
    fast = rec.builds("Mag7")
    slow = rec.builds("Watchlist")
    assert slow == 0, "the slow list should still be building"
    assert fast >= 5, (
        "Mag7 managed only %d builds while the Watchlist was in flight -- it is "
        "still queued behind it" % fast
    )


def test_warm_loop_offers_the_grade_nightly_after_each_build(monkeypatch):
    rec = install(monkeypatch, sizes={"Mag7": 10}, durations={"Mag7": 0.01})
    service.start_warmer()
    time.sleep(0.3)
    nightly = sum(1 for e, _, _ in rec.log if e == "nightly")
    assert rec.builds("Mag7") >= 2 and nightly >= rec.builds("Mag7") - 1


def test_grade_nightly_passes_the_et_clock_and_bar_fetcher(monkeypatch):
    seen = []
    monkeypatch.setattr(service._GRADE_LOG, "nightly",
                        lambda now, fetch: seen.append((now, fetch)) or False)
    service._grade_nightly()
    (now, fetch), = seen
    assert now.tzinfo is not None and fetch is service._fetch_5m_bars


def test_fetch_5m_bars_never_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no network in tests")

    monkeypatch.setattr(service.feed, "fetch_5m", boom)
    assert service._fetch_5m_bars(["AAA"]) == {}


def test_a_small_list_builds_while_a_heavy_one_is_in_flight(monkeypatch):
    rec = install(
        monkeypatch,
        sizes={"Mag7": 10, "Watchlist": 357},
        durations={"Mag7": 0.05, "Watchlist": 1.0},
    )
    service.start_warmer()
    time.sleep(0.6)
    assert any(
        pair == ("Mag7", "Watchlist") for pair in rec.overlap
    ), "a light list never overlapped the heavy one: %r" % (rec.overlap,)


# ---------------------------------------------------------------------------
# the invariant that must survive: no pile-up
# ---------------------------------------------------------------------------

def test_two_heavy_lists_never_build_at_the_same_time(monkeypatch):
    """The original non-negotiable, now enforced by the gate instead of by
    having only one thread. Two 355-symbol pulls at once is the pile-up that
    has starved this machine's chart engine before."""
    rec = install(
        monkeypatch,
        sizes={"Big1": 357, "Big2": 400},
        durations={"Big1": 0.3, "Big2": 0.3},
    )
    service.start_warmer()
    time.sleep(1.2)
    assert rec.overlap == [], "two heavy builds overlapped: %r" % (rec.overlap,)
    # and both still made progress -- the gate must serialise, not starve.
    assert rec.builds("Big1") >= 1 and rec.builds("Big2") >= 1


def test_one_list_is_never_built_by_two_threads_at_once(monkeypatch):
    rec = install(monkeypatch, sizes={"Mag7": 10}, durations={"Mag7": 0.15})
    service.start_warmer()
    # Hammer the synchronous seam while the warm thread is also running.
    for _ in range(12):
        service._warm_due_lists(["Mag7"])
        time.sleep(0.01)
    assert rec.overlap == [], "the same list was built concurrently"


def test_a_raising_build_frees_the_heavy_gate(monkeypatch):
    """A build that throws must not leave the gate held -- that would stop
    every heavy list for the life of the process, silently."""
    rec = install(
        monkeypatch,
        sizes={"Big1": 357, "Big2": 400},
        durations={"Big1": 0.05, "Big2": 0.05},
        failures={"Big1"},
    )
    service.start_warmer()
    time.sleep(0.8)
    assert rec.builds("Big2") >= 2, (
        "the healthy heavy list stopped building, so the gate leaked"
    )


def test_one_lists_failure_leaves_the_others_running(monkeypatch):
    rec = install(
        monkeypatch,
        sizes={"Mag7": 10, "Watchlist": 357},
        durations={"Mag7": 0.02, "Watchlist": 0.05},
        failures={"Watchlist"},
    )
    service.start_warmer()
    time.sleep(0.5)
    assert rec.builds("Mag7") >= 3


# ---------------------------------------------------------------------------
# lifecycle
# ---------------------------------------------------------------------------

def test_start_warmer_is_idempotent(monkeypatch):
    install(monkeypatch, sizes={"Mag7": 10}, durations={"Mag7": 0.01})
    service.start_warmer()
    with service._LOCK:
        first = dict(service._THREADS)
    for _ in range(4):
        service.start_warmer()
    with service._LOCK:
        again = dict(service._THREADS)
    assert first == again, "start_warmer spawned duplicate warm threads"


def test_a_list_added_after_startup_gets_its_own_thread(monkeypatch):
    sizes = {"Mag7": 10}
    rec = install(monkeypatch, sizes=sizes, durations={"Mag7": 0.01, "Later": 0.01})
    monkeypatch.setattr(service, "SUPERVISOR_POLL_SECONDS", 0.05)
    service.start_warmer()
    time.sleep(0.15)
    sizes["Later"] = 12          # the universe gains a list mid-flight
    time.sleep(0.4)
    assert rec.builds("Later") >= 1, "a list added after startup never got warmed"


def test_every_warm_thread_is_a_daemon(monkeypatch):
    """Non-daemon threads would stop momx_worker from ever exiting."""
    install(monkeypatch, sizes={"Mag7": 10, "Watchlist": 357}, durations={})
    service.start_warmer()
    with service._LOCK:
        threads = list(service._THREADS.values()) + [service._THREAD]
    assert threads and all(t.daemon for t in threads if t is not None)


def test_refresh_now_cuts_the_named_lists_wait_only(monkeypatch):
    rec = install(
        monkeypatch,
        sizes={"Mag7": 10, "Watchlist": 357},
        durations={"Mag7": 0.01, "Watchlist": 0.01},
    )
    monkeypatch.setattr(service, "_next_delay", lambda elapsed, failed: 30.0)
    service.start_warmer()
    time.sleep(0.25)                      # both build once, then sleep 30s
    before_fast = rec.builds("Mag7")
    before_slow = rec.builds("Watchlist")
    service.refresh_now("Mag7")
    time.sleep(0.25)
    assert rec.builds("Mag7") > before_fast, "refresh_now did not wake the list"
    assert rec.builds("Watchlist") == before_slow, "it woke a list it was not asked to"


def test_fetch_5m_bars_bypasses_the_shared_feed_cache(monkeypatch):
    """The feed's TTL cache / incremental store key has no depth, and the board
    fetches the same "5m" kind at days=5: a nightly days=N fetch through the
    cache would leave the scanner holding N days of history."""
    calls = []

    class Result:
        bars = {}

    def fake(symbols, **kwargs):
        calls.append((list(symbols), kwargs))
        return Result()

    monkeypatch.setattr(service.feed, "fetch_5m", fake)
    assert service._fetch_5m_bars(["AAA", "BBB"], days=3) == {}
    assert calls == [
        (["AAA", "BBB"], {"days": 3, "use_cache": False, "schwab_volume": False})
    ]
    service._fetch_5m_bars(["AAA"])
    assert calls[-1][1] == {"days": 1, "use_cache": False, "schwab_volume": False}


def test_fetch_5m_bars_never_asks_for_the_schwab_volume_correction(monkeypatch):
    """This fetch only reads OHLC (outcome_for never touches volume), but the
    grade_log catch-up retries every unscored symbol every 15 minutes, all
    day, for up to 10 days. Left on, the Schwab volume-correction pass inside
    feed._fetch_tape would turn that into a sustained burst of uncached,
    one-call-per-symbol Schwab requests -- this app's home IP has been
    blocked by Schwab's CDN before for far smaller bursts."""
    seen = {}

    class Result:
        bars = {}

    def fake(symbols, **kwargs):
        seen.update(kwargs)
        return Result()

    monkeypatch.setattr(service.feed, "fetch_5m", fake)
    service._fetch_5m_bars(["AAA"])
    assert seen["schwab_volume"] is False
