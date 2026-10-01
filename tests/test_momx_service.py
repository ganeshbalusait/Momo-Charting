"""Tests for the warmed MomX board service, one per named watchlist.

NOTHING HERE STARTS THE WARMER AND NOTHING HERE TOUCHES THE NETWORK. An
autouse fixture replaces ``service.start_warmer`` with a no-op and
``board.cached_board`` with a stub, so a test that forgets to inject a build
gets a recorded fake instead of a live Alpaca pull. The one test that cares
about threads asserts they are NOT started.

NOTHING HERE TOUCHES THE REAL UNIVERSE FILE either: ``MOMX_UNIVERSE_PATH``
points at a scratch file for every test, so a save cannot clobber the trader's
pasted list.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import threading

import pytest

from momx import board, service


@pytest.fixture(autouse=True)
def _scratch_universe_path(monkeypatch, tmp_path):
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(tmp_path / "universe.json"))


@pytest.fixture(autouse=True)
def _scratch_board_cache_dir(monkeypatch, tmp_path):
    """Point the on-disk board cache at a scratch dir, never the real one.

    Without this a real ``artifacts/momx_board_cache`` from a live run would
    leak into the warming-stub tests, and a test write would clobber the
    trader's cached boards.
    """
    monkeypatch.setenv(service.BOARD_CACHE_DIR_ENV, str(tmp_path / "board_cache"))


@pytest.fixture(autouse=True)
def _pinned_watchlist_seed(monkeypatch):
    """Pin the Watchlist seed; production reads watchlist.txt (355 names)."""
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["SPY", "QQQ"])


@pytest.fixture(autouse=True)
def _no_warmer_thread(monkeypatch):
    monkeypatch.setattr(service, "start_warmer", lambda: None)


@pytest.fixture(autouse=True)
def _clean_state():
    service._STATES.clear()
    yield
    service._STATES.clear()


class StubBuilder:
    """Stands in for ``board.cached_board``: records symbols, returns a board."""

    def __init__(self, seconds: float = 0.0, fail: bool = False, bear_row: bool = False):
        self.calls: list[tuple[str, ...]] = []
        self.seconds = seconds
        self.fail = fail
        # BEAR scanner tests want a second row that only the bear scan matches.
        self.bear_row = bear_row

    def __call__(self, symbols=None, ttl_seconds=None, **kwargs):
        wanted = tuple(symbols or [])
        self.calls.append(wanted)
        if self.fail:
            raise RuntimeError("alpaca said no")
        # Two rows: the first is a bull match (what every older test reads),
        # the second a BEAR match that the bear board must lead with.
        rows = [
            {"symbol": wanted[0], "pctChange": 1.0, "scanPass": True,
             "bear": {"scanPass": False, "scanReasons": [], "m5": None, "grade": None}},
            {"symbol": "DN", "pctChange": -3.0, "scanPass": False,
             "bear": {"scanPass": True, "scanReasons": ["rvol:5m"], "m5": None, "grade": None}},
        ] if wanted else []
        if not self.bear_row:
            rows = rows[:1]
        return {
            "generatedAt": f"built-{len(self.calls)}",
            "direction": "bull",
            "universe": list(wanted),
            "universeCount": len(wanted),
            "rows": rows,
            "rest": [],
            "errors": {},
        }


@pytest.fixture
def builder(monkeypatch):
    stub = StubBuilder()
    monkeypatch.setattr(board, "cached_board", stub)
    return stub


@pytest.fixture
def builder2(monkeypatch):
    """A builder whose board carries a bull match AND a bear-only match."""
    stub = StubBuilder(bear_row=True)
    monkeypatch.setattr(board, "cached_board", stub)
    return stub


# ----------------------------------------------------------------------
# per-list isolation
# ----------------------------------------------------------------------

def test_each_named_list_warms_into_its_own_payload(builder):
    service._build_once("Mag7")

    mag7 = service.snapshot("Mag7")
    assert mag7["list"] == "Mag7"
    assert mag7["universe"] == list(board.MEGA7_SEED)
    assert not mag7.get("warming")

    # The other list has not been built and must NOT be served Mag7's board.
    watchlist = service.snapshot("Watchlist")
    assert watchlist["list"] == "Watchlist"
    assert watchlist["warming"] is True
    assert watchlist["rows"] == []
    assert watchlist["universe"] == ["SPY", "QQQ"]

    service._build_once("Watchlist")
    assert service.snapshot("Watchlist")["universe"] == ["SPY", "QQQ"]
    assert service.snapshot("Mag7")["universe"] == list(board.MEGA7_SEED)


def test_snapshot_defaults_to_the_active_list(builder):
    service._build_once("Mag7")
    service._build_once("Watchlist")
    assert service.snapshot()["list"] == "Mag7"

    board.set_active_list("Watchlist")
    assert service.snapshot()["list"] == "Watchlist"


def test_an_unknown_list_name_raises_for_the_http_layer_to_turn_into_a_400(builder):
    with pytest.raises(board.UnknownListError):
        service.snapshot("Mag8")
    with pytest.raises(board.UnknownListError):
        service.set_universe("NVDA", "Mag8")


def test_a_failed_build_serves_that_lists_stale_board_and_leaves_others_alone(
    monkeypatch, builder
):
    service._build_once("Mag7")
    service._build_once("Watchlist")

    monkeypatch.setattr(board, "cached_board", StubBuilder(fail=True))
    service._build_once("Mag7")

    stale = service.snapshot("Mag7")
    assert stale["stale"] is True
    assert "alpaca said no" in stale["errors"]["_refresh"]
    assert stale["rows"], "a failed refresh must not blank a good board"
    assert "stale" not in service.snapshot("Watchlist")


def test_a_first_build_failure_reads_as_warming_with_the_reason(monkeypatch):
    monkeypatch.setattr(board, "cached_board", StubBuilder(fail=True))
    service._build_once("Mag7")
    payload = service.snapshot("Mag7")
    assert payload["warming"] is True
    assert "alpaca said no" in payload["message"]


# ----------------------------------------------------------------------
# sequential warming and the adaptive delay
# ----------------------------------------------------------------------

def test_refresh_seconds_is_a_floor_and_a_slow_build_still_rests():
    """The 355-symbol list must not enter a permanent rebuild loop.

    The rest used to be the FULL build duration, which held the Watchlist at
    ~50% duty and a ~212s cadence (106s build + 106s idle) -- every alert from
    that list up to three and a half minutes late. That rule existed to stop a
    build starting before the previous one landed; the per-list build claim and
    the heavy gate now guarantee that outright, so the rest is scaled to
    IDLE_FRACTION_OF_BUILD instead. It is deliberately NOT zero: duty cycle is
    still real and CPU here has starved the chart engine before.
    """
    assert service.REFRESH_SECONDS == 15.0
    assert 0.0 < service.IDLE_FRACTION_OF_BUILD < 1.0, (
        "a full-duration rest is the old behaviour; zero would remove the "
        "duty-cycle protection entirely"
    )
    # The floor still wins for quick builds - Mag7 keeps its 15s cadence.
    assert service._next_delay(5.0, False) == service.REFRESH_SECONDS
    # A slow build rests in proportion, not for its whole duration.
    assert service._next_delay(106.0, False) == 106.0 * service.IDLE_FRACTION_OF_BUILD
    assert service._next_delay(106.0, False) < 106.0
    # Capped at MAX_IDLE_SECONDS since 2026-08-31: a 1312s build used to
    # schedule a 1312s idle and froze the board on 30-minute-old data.
    assert service._next_delay(2400.0, False) == service.MAX_IDLE_SECONDS
    assert service._next_delay(240.0, True) == service.RETRY_SECONDS
    assert service._next_delay(0.0, True) == service.RETRY_SECONDS


def test_the_adaptive_delay_is_per_list_so_the_big_list_cannot_slow_mag7(monkeypatch):
    timeline = {"now": 1000.0}
    monkeypatch.setattr(service.time, "monotonic", lambda: timeline["now"])

    def slow_watchlist(symbols=None, ttl_seconds=None, **kwargs):
        # 355 names take minutes; ten take seconds.
        timeline["now"] += 300.0 if len(list(symbols or [])) == 2 else 5.0
        return {"generatedAt": "x", "universe": list(symbols or []), "rows": [], "errors": {}}

    monkeypatch.setattr(board, "cached_board", slow_watchlist)
    service._warm_due_lists(["Mag7", "Watchlist"])

    finished = timeline["now"]
    assert service._STATES["Mag7"].due_at == pytest.approx(
        finished - 300.0 + service.REFRESH_SECONDS
    )
    # The 300s build now idles at the MAX_IDLE_SECONDS cap, not 300s (see
    # _next_delay). The point of this test is unchanged: Mag7's short delay is
    # independent of the big list's long one.
    assert service._STATES["Watchlist"].due_at == pytest.approx(
        finished + service.MAX_IDLE_SECONDS
    )


def test_one_pass_builds_every_due_list_once_and_then_nothing_is_due(builder):
    assert service._warm_due_lists(["Mag7", "Watchlist"]) == ["Mag7", "Watchlist"]
    assert [call[0] for call in builder.calls] == ["AAPL", "SPY"]
    # Immediately after, both are idling: a second pass must not rebuild.
    assert service._warm_due_lists(["Mag7", "Watchlist"]) == []
    assert len(builder.calls) == 2


def test_lists_are_built_one_at_a_time_never_concurrently(monkeypatch):
    live = {"count": 0, "max": 0}

    def watcher(symbols=None, ttl_seconds=None, **kwargs):
        live["count"] += 1
        live["max"] = max(live["max"], live["count"])
        live["count"] -= 1
        return {"generatedAt": "x", "universe": list(symbols or []), "rows": [], "errors": {}}

    monkeypatch.setattr(board, "cached_board", watcher)
    before = threading.active_count()
    service._warm_due_lists(["Mag7", "Watchlist"])
    assert live["max"] == 1
    assert threading.active_count() == before, "warming must not spawn per-list threads"


def test_refresh_now_marks_one_list_due_without_disturbing_the_others(builder):
    service._warm_due_lists(["Mag7", "Watchlist"])
    service.refresh_now("Mag7")
    assert service._warm_due_lists(["Mag7", "Watchlist"]) == ["Mag7"]

    service.refresh_now()
    assert service._warm_due_lists(["Mag7", "Watchlist"]) == ["Mag7", "Watchlist"]


def test_request_rebuild_marks_only_that_list_due_and_reports_the_current_stamp(builder):
    service._warm_due_lists(["Mag7", "Watchlist"])

    result = service.request_rebuild("Mag7")
    assert result == {
        "ok": True,
        "list": "Mag7",
        "queued": True,
        "generatedAt": "built-1",
    }
    # Only Mag7 is due again; the Watchlist keeps idling out its own delay.
    assert service._warm_due_lists(["Mag7", "Watchlist"]) == ["Mag7"]


def test_request_rebuild_defaults_to_the_active_list_and_never_builds_here(builder):
    result = service.request_rebuild()
    assert result["ok"] is True
    assert result["list"] == "Mag7"
    assert result["queued"] is True
    # No build has ever completed, so there is no stamp yet -- and crucially
    # the request itself did NOT build on this thread.
    assert result["generatedAt"] is None
    assert builder.calls == []


def test_request_rebuild_for_an_unknown_list_raises_for_the_400(builder):
    with pytest.raises(board.UnknownListError):
        service.request_rebuild("Mag8")


def test_request_rebuild_twice_is_harmless_and_still_one_build(builder):
    service._warm_due_lists(["Mag7"])
    assert service.request_rebuild("Mag7")["ok"] is True
    assert service.request_rebuild("Mag7")["ok"] is True
    # Two queued requests collapse into the single already-due state: the next
    # pass builds Mag7 exactly once, and the pass after that builds nothing.
    assert service._warm_due_lists(["Mag7"]) == ["Mag7"]
    assert service._warm_due_lists(["Mag7"]) == []
    assert len(builder.calls) == 2  # the initial build + the queued rebuild


# ----------------------------------------------------------------------
# lists / status / set_universe
# ----------------------------------------------------------------------

def test_lists_reports_every_list_with_its_count_and_warm_state(builder):
    service._build_once("Mag7")
    payload = service.lists()

    assert payload["active"] == "Mag7"
    assert [entry["name"] for entry in payload["lists"]] == ["Mag7", "Watchlist"]
    mag7, watchlist = payload["lists"]
    assert mag7["count"] == len(board.MEGA7_SEED)
    assert mag7["warming"] is False
    assert mag7["builtAt"] == "built-1"
    assert watchlist["count"] == 2
    assert watchlist["warming"] is True
    assert watchlist["builtAt"] is None


def test_status_carries_per_list_detail(builder):
    service._build_once("Mag7")
    payload = service.status()

    assert payload["running"] is False  # start_warmer is stubbed out here
    assert payload["active"] == "Mag7"
    assert payload["refreshSeconds"] == service.REFRESH_SECONDS
    detail = {entry["name"]: entry for entry in payload["lists"]}
    assert detail["Mag7"]["builds"] == 1
    assert detail["Mag7"]["hasPayload"] is True
    assert detail["Watchlist"]["builds"] == 0
    assert detail["Watchlist"]["hasPayload"] is False
    assert payload["builds"] == 1


def test_set_universe_replaces_only_the_named_list_and_makes_it_active(builder):
    service._build_once("Mag7")
    service._build_once("Watchlist")

    result = service.set_universe("NVDA, AMD", "Watchlist")
    assert result == {
        "ok": True,
        "list": "Watchlist",
        "universe": ["NVDA", "AMD"],
        "universeCount": 2,
    }
    assert board.load_universe("Watchlist") == ["NVDA", "AMD"]
    assert board.load_universe("Mag7") == list(board.MEGA7_SEED)
    assert board.active_list() == "Watchlist"

    # The rewritten list drops its now-wrong board; the other keeps its own.
    assert service.snapshot("Watchlist")["warming"] is True
    assert service.snapshot("Mag7")["universe"] == list(board.MEGA7_SEED)


def test_set_universe_defaults_to_the_active_list(builder):
    assert service.set_universe("NVDA")["list"] == "Mag7"
    assert board.load_universe("Mag7") == ["NVDA"]


def test_set_universe_rejects_junk_without_touching_the_saved_list(builder):
    result = service.set_universe("12345 !!!", "Watchlist")
    assert result["ok"] is False
    assert result["list"] == "Watchlist"
    assert result["universe"] == ["SPY", "QQQ"]
    assert board.load_universe("Watchlist") == ["SPY", "QQQ"]


# ----------------------------------------------------------------------
# disk persistence -- the board survives a restart
# ----------------------------------------------------------------------

def _seed_disk_board(name: str, generated_at: str, symbol: str = "ZZZ") -> None:
    """Write a completed-looking board straight to a list's cache file."""
    path = service._cache_file(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generatedAt": generated_at,
                "list": name,
                "universe": [symbol],
                "universeCount": 1,
                "rows": [{"symbol": symbol}],
                "errors": {},
            }
        ),
        encoding="utf-8",
    )


def test_a_completed_build_writes_the_list_cache_file(builder):
    service._build_once("Mag7")

    path = service._cache_file("Mag7")
    assert path.exists(), "a completed build must cache its board to disk"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert stored["generatedAt"] == "built-1"
    assert [row["symbol"] for row in stored["rows"]] == ["AAPL"]
    # The grade recorder runs BEFORE the disk write, so the cache carries it.
    assert "gradeFresh" in stored["rows"][0]


def test_build_feeds_the_grade_log_and_status_reports_it(monkeypatch, tmp_path, builder):
    from datetime import datetime as real_datetime
    from zoneinfo import ZoneInfo

    from momx import grade_log
    from momx.grade_log import GradeLog

    # The tape is only written on weekdays 04:00-19:59 ET: pin the build's
    # clock to a Monday morning so this test does not depend on when it runs.
    monday = real_datetime(2026, 9, 21, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    class _Clock(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return monday.astimezone(tz) if tz else monday.replace(tzinfo=None)

    monkeypatch.setattr(service, "datetime", _Clock)
    monkeypatch.setattr(grade_log, "_today_et", lambda: "2026-09-21")
    monkeypatch.setattr(service, "_GRADE_LOG", GradeLog(tmp_path))
    service._build_once("Mag7")

    assert "gradeFresh" in service.snapshot("Mag7")["rows"][0]
    grade = service.status()["grade"]
    assert grade["tapeEntriesToday"] == 1 and grade["tapeLastWriteAt"]
    assert list((tmp_path / "momx_grade_tape" / "Mag7").glob("*.jsonl"))


def test_grade_log_applies_before_the_payload_is_published(monkeypatch, builder):
    """The grade recorder must run BEFORE ``state.payload`` is published.

    Otherwise a reader that grabs ``state.payload`` under ``_LOCK`` right
    after publish (momx_worker._momo_evaluate/_momentum baselines,
    service.snapshot) can observe a board mid-mutation: some rows already
    carry ``gradeFresh`` and some do not, and Python-level iteration over a
    row dict being written from another thread can raise "dictionary
    changed size during iteration".
    """
    already_published = []

    class SpyGradeLog:
        def apply(self, name, payload, now):
            state = service._state(name)
            with service._LOCK:
                already_published.append(state.payload is payload)
            for row in payload.get("rows") or []:
                if isinstance(row, dict):
                    row["gradeFresh"] = {"icons": []}

        def status(self):
            return {}

    monkeypatch.setattr(service, "_GRADE_LOG", SpyGradeLog())
    service._build_once("Mag7")

    assert already_published == [False], (
        "grade log ran AFTER the payload was published under _LOCK"
    )


def test_grade_dir_honours_the_test_override():
    # conftest points it at scratch: fixture boards must never reach the real
    # artifacts/momx_grade_* trade record.
    assert str(service.grade_dir()) == os.environ["AGX_MOMX_GRADE_DIR"]
    assert service.ARTIFACTS_DIR not in service.grade_dir().parents


def test_after_a_restart_snapshot_serves_the_disk_board_stale_not_a_warming_stub(builder):
    service._build_once("Mag7")
    original = service.snapshot("Mag7")["generatedAt"]

    # Simulate a worker restart: every scrap of in-memory state is gone.
    service._STATES.clear()

    restored = service.snapshot("Mag7")
    assert restored.get("warming") is not True, "a restart must not blank the board"
    assert restored["stale"] is True
    assert restored["fromDisk"] is True
    assert restored["generatedAt"] == original, "the ORIGINAL build time is preserved"
    assert [row["symbol"] for row in restored["rows"]] == ["AAPL"]


def test_precedence_fresh_memory_beats_disk(builder):
    # A stale disk board from a previous session exists...
    _seed_disk_board("Mag7", "disk-old")
    # ...but a fresh in-memory build must win over it, with no stale flag.
    service._build_once("Mag7")

    snap = service.snapshot("Mag7")
    assert snap["generatedAt"] == "built-1"
    assert snap.get("stale") is not True
    assert snap.get("fromDisk") is not True


def test_precedence_disk_beats_the_warming_stub(builder):
    _seed_disk_board("Mag7", "disk-old", symbol="ZZZ")
    # Nothing was ever built in memory this session, so the only choices are
    # disk (stale) and the warming stub. Disk must win.
    snap = service.snapshot("Mag7")
    assert snap.get("warming") is not True
    assert snap["stale"] is True
    assert snap["rows"] == [{"symbol": "ZZZ"}]
    assert snap["generatedAt"] == "disk-old"


def test_a_very_old_disk_board_is_still_served_flagged_as_a_previous_session(builder):
    from datetime import datetime, timedelta, timezone

    old = (
        datetime.now(timezone.utc)
        - timedelta(seconds=service.STALE_DISK_MAX_AGE_SECONDS + 3600)
    ).isoformat()
    _seed_disk_board("Mag7", old)

    snap = service.snapshot("Mag7")
    assert snap.get("warming") is not True, "old tickers beat no tickers"
    assert snap["stale"] is True
    assert snap["generatedAt"] == old
    assert "previous session" in snap["message"]


def test_a_corrupt_or_missing_cache_file_falls_back_to_the_warming_stub(builder):
    # Missing file: never built, no cache written.
    assert service.snapshot("Mag7")["warming"] is True

    # Corrupt file: half-written / garbage must not raise and must not be served.
    path = service._cache_file("Watchlist")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    snap = service.snapshot("Watchlist")
    assert snap["warming"] is True
    assert snap["rows"] == []


def test_set_universe_drops_the_disk_cache_so_the_old_board_is_not_served(builder):
    service._build_once("Watchlist")
    assert service._cache_file("Watchlist").exists()

    service.set_universe("NVDA, AMD", "Watchlist")
    # The cache for the OLD universe must be gone; snapshot must warm, not serve
    # the previous ticker list's board under the new one.
    assert not service._cache_file("Watchlist").exists()
    assert service.snapshot("Watchlist")["warming"] is True


def test_a_write_failure_breaks_neither_the_build_nor_the_snapshot(monkeypatch, builder):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(service.os, "replace", boom)

    # The build still completes and records its board in memory...
    service._build_once("Mag7")
    assert not service._cache_file("Mag7").exists(), "the failed write left no file"

    snap = service.snapshot("Mag7")
    assert snap.get("warming") is not True
    assert snap["generatedAt"] == "built-1"
    assert [row["symbol"] for row in snap["rows"]] == ["AAPL"]


# ----------------------------------------------------------------------
# the performance contract
# ----------------------------------------------------------------------

def test_importing_momx_service_starts_no_threads():
    before = threading.active_count()
    importlib.reload(service)
    assert threading.active_count() == before


def _imports_api_server_in_a_fresh_interpreter(module: str) -> bool:
    """Does importing `module` pull in api_server, asked of a CLEAN interpreter.

    This must not be asked of the running pytest process: its sys.modules is
    shared by every test in the run, so the answer would depend on whether some
    earlier test happened to import api_server. That is exactly how these
    assertions used to fail under whole-suite collection and pass in isolation.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    probe = (
        "import sys; import %s; "
        "print('YES' if 'api_server' in sys.modules else 'NO')" % module
    )
    result = subprocess.run(
        [_sys.executable, "-c", probe],
        capture_output=True, text=True, cwd=str(root), timeout=120,
    )
    assert result.returncode == 0, (
        "importing %s failed in a fresh interpreter:\n%s" % (module, result.stderr)
    )
    return result.stdout.strip().endswith("YES")


def test_momx_service_does_not_import_api_server():
    """momx.service must not drag in the 1MB api_server module.

    Asked of a FRESH interpreter. Asking the pytest process instead makes
    this assert whichever test ran first, which is why it used to fail in
    the full suite and pass alone.
    """
    assert not _imports_api_server_in_a_fresh_interpreter("momx.service")


def test_reading_a_list_or_status_never_builds_on_the_caller(builder):
    """Readers hand back the last completed board; they never fetch."""
    service.snapshot("Mag7")
    service.lists()
    service.status()
    assert builder.calls == []


# ----------------------------------------------------------------------
# matched-since -- WHEN a symbol entered the matched set
# ----------------------------------------------------------------------

class ScanStub:
    """``cached_board`` stand-in whose rows carry ``scanPass``, scripted per
    build. ``generatedAt`` is ``<prefix>-<n>`` so each build's stamp is
    distinguishable, and a post-"restart" stub can use a different prefix to
    prove a persisted stamp was KEPT rather than coincidentally re-issued."""

    def __init__(self, *builds, prefix: str = "stamp"):
        self.builds = list(builds)
        self.prefix = prefix
        self.count = 0

    def __call__(self, symbols=None, ttl_seconds=None, **kwargs):
        rows = self.builds.pop(0) if self.builds else []
        self.count += 1
        return {
            "generatedAt": f"{self.prefix}-{self.count}",
            "universe": list(symbols or []),
            "universeCount": len(list(symbols or [])),
            "rows": [dict(entry) for entry in rows],
            "errors": {},
        }


def _rows(snap):
    return {entry["symbol"]: entry for entry in snap["rows"]}


def test_a_symbol_entering_the_matched_set_is_stamped_with_that_builds_time(monkeypatch):
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "JD", "scanPass": True}, {"symbol": "NIO", "scanPass": False}],
    ))
    service._build_once("Mag7")
    rows = _rows(service.snapshot("Mag7"))
    assert rows["JD"]["matchedSince"] == "stamp-1"
    assert "matchedSince" not in rows["NIO"], "only scanPass rows carry the stamp"


def test_a_symbol_still_matched_keeps_its_original_stamp(monkeypatch):
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "JD", "scanPass": True}],
        [{"symbol": "JD", "scanPass": True}, {"symbol": "BILI", "scanPass": True}],
    ))
    service._build_once("Mag7")
    service._build_once("Mag7")
    rows = _rows(service.snapshot("Mag7"))
    assert rows["JD"]["matchedSince"] == "stamp-1", "still matched = ORIGINAL stamp"
    assert rows["BILI"]["matchedSince"] == "stamp-2", "new entrant = THIS build's stamp"


def test_dropping_out_forgets_the_stamp_and_reentry_restamps_fresh(monkeypatch):
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "JD", "scanPass": True}],
        [{"symbol": "JD", "scanPass": False}],
        [{"symbol": "JD", "scanPass": True}],
    ))
    service._build_once("Mag7")
    service._build_once("Mag7")
    assert "matchedSince" not in _rows(service.snapshot("Mag7"))["JD"]
    service._build_once("Mag7")
    assert _rows(service.snapshot("Mag7"))["JD"]["matchedSince"] == "stamp-3"


def test_matched_since_round_trips_through_the_disk_cache(monkeypatch):
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "BUD", "scanPass": True}],
    ))
    service._build_once("Mag7")

    # The stamp rode the EXISTING cache write -- no sibling file.
    stored = json.loads(service._cache_file("Mag7").read_text(encoding="utf-8"))
    assert stored["rows"][0]["matchedSince"] == "stamp-1"

    # Simulate a worker restart: every scrap of in-memory state is gone. The
    # disk-served stale board still carries Friday's stamp...
    service._STATES.clear()
    assert _rows(service.snapshot("Mag7"))["BUD"]["matchedSince"] == "stamp-1"

    # ...and the FIRST build of the new session seeds its map from disk, so a
    # still-matched BUD keeps "stamp-1" instead of being restamped as new.
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "BUD", "scanPass": True}, {"symbol": "DHI", "scanPass": True}],
        prefix="boot2",
    ))
    service._build_once("Mag7")
    rows = _rows(service.snapshot("Mag7"))
    assert rows["BUD"]["matchedSince"] == "stamp-1"
    assert rows["DHI"]["matchedSince"] == "boot2-1"


def test_a_first_ever_run_stamps_the_current_set_with_that_build_time(monkeypatch):
    # No disk cache exists at all (fresh scratch dir): the honest one-time
    # answer is "matched since this build".
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "TQQQ", "scanPass": True}],
    ))
    service._build_once("Mag7")
    assert _rows(service.snapshot("Mag7"))["TQQQ"]["matchedSince"] == "stamp-1"


def test_set_universe_resets_the_matched_map_with_the_board(monkeypatch):
    monkeypatch.setattr(board, "cached_board", ScanStub(
        [{"symbol": "JD", "scanPass": True}],
        [{"symbol": "JD", "scanPass": True}],
    ))
    service._build_once("Mag7")
    service.set_universe("JD", "Mag7")
    # The new universe's first build stamps fresh: the old stamp died with
    # the old universe's board and its deleted disk cache.
    service._build_once("Mag7")
    assert _rows(service.snapshot("Mag7"))["JD"]["matchedSince"] == "stamp-2"


def test_next_delay_is_capped_so_one_slow_build_cannot_blind_the_board():
    """A 1312s build once scheduled a 1312s idle and the board sat on 10:30
    data at 11:00 (trader, 2026-08-31). Politeness is bounded now."""
    assert service._next_delay(4.0, False) == service.REFRESH_SECONDS      # floor holds
    # A normal build rests in proportion to its cost (see
    # IDLE_FRACTION_OF_BUILD); it used to rest for the build's whole duration.
    assert service._next_delay(150.0, False) == 150.0 * service.IDLE_FRACTION_OF_BUILD
    assert service._next_delay(1312.0, False) == service.MAX_IDLE_SECONDS  # pathological capped
    assert service.MAX_IDLE_SECONDS < 1312.0



# ----------------------------------------------------------------------
# BEAR board per list (spec 2026-09-24)
# ----------------------------------------------------------------------

def test_a_build_publishes_a_bull_and_a_bear_board(builder2):
    service._build_once("Mag7")
    bull = service.snapshot("Mag7")
    bear = service.snapshot("Mag7", "bear")
    assert bull["direction"] == "bull" and bear["direction"] == "bear" and bear["list"] == "Mag7"
    assert all("bear" not in r for r in bull["rows"])               # Review Focus 1
    assert [r["symbol"] for r in bull["rows"]] == [board.MEGA7_SEED[0], "DN"]
    assert [r["symbol"] for r in bear["rows"]][0] == "DN"           # the bear match leads
    assert bear["rows"][0]["scanPass"] is True and bear["rows"][0].get("matchedSince")
    assert bull["rows"][0].get("matchedSince")
    assert "strategyDaily2" not in bear or bear["strategyDaily2"] is not None


def test_bear_board_survives_a_restart_from_its_own_disk_file(builder2):
    service._build_once("Mag7")
    assert service._cache_file("Mag7", "bear").name == "Mag7.bear.json"
    assert service._cache_file("Mag7").name == "Mag7.json"
    service._STATES.clear()
    disk = service.snapshot("Mag7", "bear")
    assert disk["fromDisk"] is True and disk["direction"] == "bear"
    assert disk["rows"][0]["symbol"] == "DN"
    assert service.snapshot("Mag7")["direction"] == "bull"


def test_bear_dirs_hang_under_the_bear_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setenv("AGX_MOMX_HISTORY_DIR", str(tmp_path / "h"))
    assert service.grade_dir("bear") == tmp_path / "bear"
    assert service.history_dir("bear") == tmp_path / "h" / "bear"
    assert service.grade_dir() == tmp_path and service.history_dir() == tmp_path / "h"


def test_unknown_direction_is_bull_and_warming_says_so(builder2):
    assert service.snapshot("Mag7", "bear")["warming"] is True
    assert service.snapshot("Mag7", "bear")["direction"] == "bear"
    service._build_once("Mag7")
    assert service.snapshot("Mag7", "sideways")["direction"] == "bull"


def test_status_reports_the_bear_recorder(builder2):
    service._build_once("Mag7")
    status = service.status()
    assert "gradeBear" in status and set(status["gradeBear"]) == set(status["grade"])


def test_reuse_rows_carry_the_bear_momentum_so_a_reused_row_keeps_it():
    """Review 2026-09-25 (Critical): the reuse cache is built from the PUBLISHED
    bull rows, which strip_bear has already cleaned, so a reused row's bear block
    lost its m5 on 3 of every 4 builds. The cache must carry m5Bear from the
    bear board."""
    state = service._ListState()
    bear_m5 = {"direction": "bear", "state": "extended"}
    state.payload = {"rows": [{"symbol": "AAA", "scanPass": True, "m5": {"direction": "bull"}}],
                     "rest": [{"symbol": "BBB", "scanPass": False, "m5": {"direction": "bull"}}]}
    state.bear = {"rows": [{"symbol": "BBB", "scanPass": True, "m5": bear_m5}],
                  "rest": [{"symbol": "AAA", "scanPass": False, "m5": None}]}
    state.builds = 1
    kwargs = service._reuse_kwargs(state)
    assert kwargs["full_rows_for"] == {"AAA", "BBB"}
    assert kwargs["reuse_rows"]["BBB"]["m5Bear"] is bear_m5
    assert kwargs["reuse_rows"]["AAA"]["m5Bear"] is None
    assert "m5Bear" not in state.payload["rows"][0]        # the published row is untouched
