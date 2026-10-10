"""Stale-tape fast paint, 2026-10-10.

The cold-click retry only fired for a ticker with NO candles. A ticker opened
yesterday, on the hot set or warmed by the board prewarm has a tape - just not
today's - and its today candles waited for the 2-worker FIFO recency pool with
a full study replay: minutes under hot-set, board and warmer load. The same
candles-only paint now runs for a tape that does not reach the session or is
more than OI_FINDER_CHART_PAINT_BEHIND_SECONDS behind, rate limited per symbol.

It ships with the label carry-forward: a candles-only paint spliced onto a
history_ready entry used to store - and SAVE TO DISK - a payload without the
per-timeframe TOS MTF block, so the next disk load judged it not content-ready
and queued a full rebuild. Every paint would have poisoned its disk copy.

Review fixes, same day: the paint splice now MERGES inside its window (a paint
skips the premarket/overnight backfills, so truncating deleted those candles);
the carried labels are replayed on the merged tapes instead of being stamped
current; the clock/market "behind" test only applies inside Schwab's 1-minute
window; and paints that stop moving a tape back off.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import patch

import api_server
from api_server import DashboardState


SYMBOL = "ZZSTALE"
# A tape older than OI_FINDER_CHART_STALE_TAPE_SECONDS (26h) is not current
# for any session, so these tests do not depend on the weekday or hour they
# run at (Schwab's minute window only decides the clock/market test).
NOT_CURRENT_HOURS = 30.0


def minute_bars(count: int, newest_epoch: float, price: float = 100.0) -> list[dict]:
    start = int(newest_epoch) - (count - 1) * 60
    return [
        {"time": start + index * 60, "open": price, "high": price,
         "low": price, "close": price, "volume": 10}
        for index in range(count)
    ]


class _RecordingPool:
    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.jobs.append((fn, args, kwargs))
        return None

    def run_all(self) -> None:
        jobs, self.jobs = self.jobs, []
        for fn, args, kwargs in jobs:
            fn(*args, **kwargs)


LABELS = {"5": [{"time": 1, "label": "CALL5"}], "240": []}


class _Stub(DashboardState):
    """Real serve path, real paint plumbing, real splice; the broker build is
    a scripted candles-only payload ending NOW."""

    def __init__(self) -> None:  # noqa: D107
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_ondemand_builds = 0
        self.oi_finder_chart_cache: dict = {}
        self.oi_finder_chart_refreshes: dict = {}
        self.oi_finder_chart_recency_refreshes: dict = {}
        self.oi_finder_chart_refresh_pool = _RecordingPool()
        self.oi_finder_chart_full_pool = _RecordingPool()
        self.oi_finder_chart_paint_pool = _RecordingPool()
        self.oi_finder_chart_interactive_paint_pool = _RecordingPool()
        self.saved: list[dict] = []
        self.builds = 0

    def _build_oi_finder_chart_payload(self, symbol, fast_start=False, skip_studies=False):
        assert skip_studies, "only the candles-only paint may run here"
        self.builds += 1
        return {
            "symbol": symbol,
            "bars": minute_bars(30, time.time()),
            "studyBars": [],
            "fineStudyBars": [],
            "dailyBars": [],
            "mtfSignals": [],
            # Exactly what the real paint emits (see skip_studies in
            # _build_oi_finder_chart_payload_impl).
            "mtfSignalsByTimeframe": None,
            "mtfSignalsPending": True,
            "mtfSourceSessions": 0,
            "mtfSignalStates": [],
            "mtfSignalMode": "tos_secondary_bucket_projection",
            "watchlistMtfStates": [],
            "studiesPending": True,
            "historyLoading": True,
        }

    def _save_oi_finder_chart_disk_payload(self, target, payload):
        self.saved.append(payload)


def _seed(state, hours_behind: float = NOT_CURRENT_HOURS) -> dict:
    """Content-ready, history_ready, labels current - tape from a prior session."""
    newest = time.time() - hours_behind * 3600.0
    entry = {
        "cached_at": time.monotonic(),
        "payload": {
            "symbol": SYMBOL,
            "bars": minute_bars(240, newest),
            "studyBars": minute_bars(400, newest, price=90.0),
            "fineStudyBars": [],
            "dailyBars": minute_bars(60, newest),
            "mtfSignals": [],
            "mtfSignalsByTimeframe": LABELS,
            "mtfSignalsPending": False,
            "mtfSourceSessions": api_server.TOS_CHART_SESSION_DAYS,
            "mtfSignalStates": [],
            "mtfSignalMode": DashboardState.OI_CHART_MTF_ENGINE_MODE,
            "historyLoading": False,
        },
        "history_ready": True,
        "built_at_epoch": 0.0,
    }
    state.oi_finder_chart_cache[SYMBOL] = entry
    return entry


def _serve(state, **kwargs):
    with (
        patch.object(state, "_chart_payload_has_ready_ganesh_signals", return_value=True),
        patch.object(state, "_chart_payload_has_multi_timeframe_depth", return_value=True),
        patch.object(state, "_windowed_payload_for_cache_entry",
                     side_effect=lambda entry: dict(entry["payload"])),
        patch.object(state, "_start_oi_finder_chart_refresh") as kick,
    ):
        payload = state._oi_finder_chart_payload_impl(SYMBOL, **kwargs)
    return payload, kick


def _all_paint_jobs(state) -> int:
    return len(state.oi_finder_chart_paint_pool.jobs) + len(
        state.oi_finder_chart_interactive_paint_pool.jobs
    )


def test_a_behind_tape_gets_one_fast_paint_and_reaches_today():
    state = _Stub()
    entry = _seed(state)
    deep_study = list(entry["payload"]["studyBars"])

    _serve(state, initial_paint=True)
    assert _all_paint_jobs(state) == 1
    # The request path stamped the symbol, so the paint is on the live lane.
    assert len(state.oi_finder_chart_interactive_paint_pool.jobs) == 1

    # A second poll within the 30s interval adds nothing (in flight)...
    _serve(state, initial_paint=True)
    assert _all_paint_jobs(state) == 1

    state.oi_finder_chart_interactive_paint_pool.run_all()
    assert state.builds == 1
    stored = state.oi_finder_chart_cache[SYMBOL]["payload"]
    assert stored["bars"][-1]["time"] >= int(time.time()) - 120, "today's candles landed"
    assert stored["studyBars"][: len(deep_study)] == deep_study, "deep tape kept"

    # ...and once current, no more paints (current tape needs none).
    _serve(state, initial_paint=True)
    assert _all_paint_jobs(state) == 0


def _deep_fine_tape(newest_epoch: float) -> list[dict]:
    """5-minute bars on more distinct ET days than TOS_CHART_SESSION_DAYS."""
    days = api_server.TOS_CHART_SESSION_DAYS + 2
    return [
        {"time": int(newest_epoch) - day * 86400, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
        for day in range(days, -1, -1)
    ]


REPLAYED = {"5": [{"time": 2, "label": "CALL4H"}], "240": []}


def _fake_replay(state, target, payload):
    payload["mtfSignalsByTimeframe"] = REPLAYED
    payload["mtfSignalsPending"] = False
    payload["mtfSignalMode"] = DashboardState.OI_CHART_MTF_ENGINE_MODE
    return True


def test_the_paint_replays_the_labels_on_the_merged_tapes():
    # Review 2026-10-10: carrying the pre-paint block AND its mtfSourceSessions
    # stamp served yesterday's labels as current on today's candles, and the
    # cached branch's one-shot recompute never fired. The paint worker now
    # replays them from the merged tapes, and the saved copy matches.
    state = _Stub()
    entry = _seed(state)
    entry["payload"]["fineStudyBars"] = _deep_fine_tape(time.time() - NOT_CURRENT_HOURS * 3600)
    _serve(state, initial_paint=True)
    with patch.object(DashboardState, "_refresh_mtf_block_from_payload_tapes", _fake_replay):
        state.oi_finder_chart_interactive_paint_pool.run_all()
    stored = state.oi_finder_chart_cache[SYMBOL]["payload"]
    assert stored["mtfSignalsByTimeframe"] == REPLAYED
    assert stored["mtfSourceSessions"] >= api_server.TOS_CHART_SESSION_DAYS
    assert DashboardState._chart_payload_has_current_mtf_labels(stored)
    assert state.saved, "a history_ready splice persists"
    assert state.saved[-1]["mtfSignalsByTimeframe"] == REPLAYED
    assert DashboardState._chart_payload_has_current_mtf_labels(state.saved[-1])


def test_labels_the_paint_could_not_replay_are_carried_but_not_stamped_current():
    state = _Stub()
    entry = _seed(state)
    entry["payload"]["fineStudyBars"] = _deep_fine_tape(time.time() - NOT_CURRENT_HOURS * 3600)
    _serve(state, initial_paint=True)
    with patch.object(DashboardState, "_refresh_mtf_block_from_payload_tapes", return_value=False):
        state.oi_finder_chart_interactive_paint_pool.run_all()
    stored = state.oi_finder_chart_cache[SYMBOL]["payload"]
    # Still SHOWN (old labels beat none)...
    assert stored["mtfSignalsByTimeframe"] == LABELS
    assert stored["mtfSignalsPending"] is False
    # ...but judged stale, so the cached branch / next disk load recompute.
    assert "mtfSourceSessions" not in stored
    assert not DashboardState._chart_payload_has_current_mtf_labels(stored)
    assert not DashboardState._chart_payload_has_current_mtf_labels(state.saved[-1])


def test_the_paint_splice_keeps_held_backfilled_bars_inside_its_window():
    # Review 2026-10-10 (scratchpad/rev/probe_splice.py): the paint skips the
    # Tradier premarket and Alpaca BOATS backfills, and the splice replaced
    # every held bar from the first fetched minute on - so the backfilled
    # candles inside the paint window vanished, from memory AND from disk.
    state = _Stub()
    now = int(time.time()) // 60 * 60
    hole = range(now - 150 * 60, now - 120 * 60)
    fetched = [bar for bar in minute_bars(200, now) if bar["time"] not in hole]
    state._build_oi_finder_chart_payload = lambda *a, **k: {
        **_Stub._build_oi_finder_chart_payload(state, SYMBOL, skip_studies=True),
        "bars": fetched,
    }
    entry = _seed(state)
    entry["payload"]["bars"] = minute_bars(600, now - 600)
    backfilled = [bar["time"] for bar in entry["payload"]["bars"] if bar["time"] in hole]
    assert len(backfilled) == 30
    token = state._claim_chart_paint(SYMBOL)
    state._run_chart_paint(SYMBOL, token)
    stored = state.oi_finder_chart_cache[SYMBOL]["payload"]["bars"]
    times = [bar["time"] for bar in stored]
    assert set(backfilled) <= set(times), "held backfilled bars survive the paint"
    assert times == sorted(set(times)), "one bar per minute, in order"
    assert times[-1] == now, "the fetched tail still lands"
    assert set(backfilled) <= {bar["time"] for bar in state.saved[-1]["bars"]}


def test_the_broker_row_wins_a_collision_in_the_paint_window():
    held = minute_bars(5, 1_000_000 * 60, price=1.0)
    fresh = minute_bars(3, 1_000_000 * 60, price=2.0)
    merged = DashboardState._splice_chart_minute_bars(held, fresh, keep_held=True)
    assert [bar["time"] for bar in merged] == [bar["time"] for bar in held]
    assert [bar["close"] for bar in merged] == [1.0, 1.0, 2.0, 2.0, 2.0]
    # Recency/full builds backfill themselves: they keep the plain truncate.
    assert DashboardState._splice_chart_minute_bars(held, fresh[1:]) == held[:3] + fresh[1:]


def test_a_prefetch_dispatches_no_paint():
    state = _Stub()
    _seed(state)
    _serve(state, initial_paint=True, prefetch=True)
    assert _all_paint_jobs(state) == 0


def test_a_paint_that_finished_is_rate_limited_and_backs_off_while_flat():
    state = _Stub()
    _seed(state)
    # The broker answers, but the market has not moved past the old tape
    # (holiday/halt): the tape stays behind after the paint.
    state._build_oi_finder_chart_payload = lambda *a, **k: {"bars": []}
    _serve(state, initial_paint=True)
    state.oi_finder_chart_interactive_paint_pool.run_all()
    _serve(state, initial_paint=True)
    assert _all_paint_jobs(state) == 0, "30s, not one broker call per poll"
    guard = state.oi_finder_chart_paint_guard[SYMBOL]
    # That paint did not move the tape, so the next waits 60s, not 30s
    # (review 2026-10-10: a halted ticker was painted every 30s forever).
    guard[2] -= api_server.OI_FINDER_CHART_REFRESH_SECONDS + 1
    _serve(state, initial_paint=True)
    assert _all_paint_jobs(state) == 0, "flat paint doubles the interval"
    guard[2] -= api_server.OI_FINDER_CHART_REFRESH_SECONDS
    _serve(state, initial_paint=True)
    assert _all_paint_jobs(state) == 1


def test_a_paint_that_moves_the_tape_resets_the_backoff():
    state = _Stub()
    _seed(state)
    state._chart_paint_flat_streak = {SYMBOL: 3}
    entry = state.oi_finder_chart_cache[SYMBOL]
    assert state._chart_paint_flat_backoff(SYMBOL, 30.0, entry) == 240.0
    state._chart_paint_flat_streak = {SYMBOL: 30}
    assert state._chart_paint_flat_backoff(SYMBOL, 30.0, entry) == (
        api_server.OI_FINDER_CHART_PAINT_FLAT_BACKOFF_CAP_SECONDS
    )
    # Cold (no bars): the click retry is never stretched.
    assert state._chart_paint_flat_backoff(SYMBOL, 3.0, {"payload": {"bars": []}}) == 3.0
    _serve(state, initial_paint=True)
    state.oi_finder_chart_paint_guard.pop(SYMBOL)  # drop the queued live paint
    token = state._claim_chart_paint(SYMBOL)
    state._run_chart_paint(SYMBOL, token)
    assert SYMBOL not in state._chart_paint_flat_streak


def test_history_status_paints_a_behind_tape_too():
    state = _Stub()
    _seed(state)
    with patch.object(state, "_start_oi_finder_chart_refresh"):
        status = state.oi_finder_chart_history_status(SYMBOL)
    assert status["refreshing"] is True
    assert _all_paint_jobs(state) == 1


def test_history_status_leaves_a_disk_tape_to_the_chart_endpoint(tmp_path):
    state = _Stub()
    state.oi_finder_chart_disk_cache_dir = tmp_path
    (tmp_path / f"{SYMBOL}.json.gz").write_bytes(b"only the mtime is read")
    with patch.object(state, "_start_oi_finder_chart_refresh") as kick:
        status = state.oi_finder_chart_history_status(SYMBOL)
    # One gunzip away: no Schwab paint, no studies build from a status poll.
    assert _all_paint_jobs(state) == 0
    kick.assert_not_called()
    assert status["hasBars"] is True


def test_history_status_with_nothing_anywhere_starts_the_cold_build():
    state = _Stub()
    with patch.object(state, "_start_oi_finder_chart_refresh") as kick:
        state.oi_finder_chart_history_status(SYMBOL)
    # The same full build (paint first) a cold chart request starts, not a
    # recency build WITH studies on top of a separate paint.
    kick.assert_called_once_with(SYMBOL, full_history=True)


def _et_epoch(year, month, day, hour, minute=0) -> float:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    return datetime(year, month, day, hour, minute, tzinfo=ZoneInfo("America/New_York")).timestamp()


def test_behind_the_clock_but_level_with_the_market_needs_no_paint():
    state = _Stub()
    clock = _et_epoch(2026, 10, 7, 14, 0)  # a Wednesday, inside Schwab's window
    newest = clock - 600.0  # ten minutes behind the wall clock
    entry = {
        "payload": {"bars": minute_bars(10, newest)},
        "history_ready": True,
        # Full-built this session, so tape-currency alone is satisfied.
        "built_at_epoch": clock,
    }
    # A quiet minute: every tape on the box ends at the same minute.
    state._chart_market_newest_bar_epoch = float(int(newest))
    assert state._chart_entry_wants_fast_paint(entry, clock) is None
    # The market moved on and this symbol did not: paint, at the 30s budget.
    state._chart_market_newest_bar_epoch = clock - 30.0
    assert state._chart_entry_wants_fast_paint(entry, clock) == api_server.OI_FINDER_CHART_REFRESH_SECONDS
    # No bars at all: the cold retry interval.
    assert state._chart_entry_wants_fast_paint({"payload": {"bars": []}}) == (
        api_server.OI_FINDER_CHART_COLD_PAINT_RETRY_SECONDS
    )


def test_overnight_the_market_mark_does_not_make_every_chart_behind():
    # Review 2026-10-10 (scratchpad/rev/probe_overnight.py): the mark is
    # advanced by BOATS-backfilled tapes, Schwab has no bars for tonight and
    # the paint skips that backfill - so a 4-16 pane layout left open spent a
    # useless Schwab call per pane per 30s from 20:00 to 07:00.
    state = _Stub()
    clock = _et_epoch(2026, 10, 8, 2, 0)  # Thursday 02:00 ET
    tape_end = _et_epoch(2026, 10, 7, 20, 0)
    entry = {"payload": {"bars": minute_bars(10, tape_end - 60)}, "history_ready": True, "built_at_epoch": 0.0}
    state._chart_market_newest_bar_epoch = clock - 15 * 60  # TSLA's overnight print
    assert state._chart_entry_tape_is_current(entry, clock)
    assert state._chart_entry_wants_fast_paint(entry, clock) is None
    # Same tape at 07:30 (Schwab premarket) is behind again.
    morning = _et_epoch(2026, 10, 8, 7, 30)
    state._chart_market_newest_bar_epoch = morning - 30
    assert state._chart_entry_wants_fast_paint(entry, morning) == api_server.OI_FINDER_CHART_REFRESH_SECONDS
    # Weekends are outside the window entirely.
    assert not DashboardState._schwab_minute_window_open(_et_epoch(2026, 10, 10, 12, 0))
