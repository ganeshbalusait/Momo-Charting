"""The three 2026-08-20 open defects, as rules.

Evidence they encode:
- MSTR's on-screen full build waited 371s behind eight queued builds for
  tickers nobody was watching ("QQQ just happened to be earlier in line").
- SPY served 19:59-yesterday bars all morning because a premarket-built
  in-memory entry stayed "ready" after the session started.
- NVDA sat un-refreshable for 25 minutes behind a stuck in-flight flag that
  only a restart could clear.
"""
from __future__ import annotations

import threading
import time as time_module
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

import api_server

ET = ZoneInfo("America/New_York")


def et_epoch(y: int, m: int, d: int, hh: int, mm: int) -> float:
    return datetime(y, m, d, hh, mm, tzinfo=ET).timestamp()


class BuildLanePriorityTests(unittest.TestCase):
    def test_a_watched_symbol_outranks_an_earlier_abandoned_kick(self) -> None:
        ahead = api_server.ChartBuildLane._waiter_ahead
        interactive = {"MSTR"}.__contains__
        waiters = {1: "QQQ", 2: "MSTR"}
        self.assertTrue(ahead(1, False, waiters, interactive))  # QQQ yields
        self.assertFalse(ahead(2, True, waiters, interactive))  # MSTR goes

    def test_fifo_within_a_tier(self) -> None:
        ahead = api_server.ChartBuildLane._waiter_ahead
        waiters = {1: "A", 2: "B"}
        self.assertFalse(ahead(1, False, waiters, lambda s: False))
        self.assertTrue(ahead(2, False, waiters, lambda s: False))
        self.assertFalse(ahead(1, True, waiters, lambda s: True))
        self.assertTrue(ahead(2, True, waiters, lambda s: True))

    def test_the_live_lane_hands_off_to_the_watched_pane_first(self) -> None:
        watched = {"MSTR"}
        lane = api_server.ChartBuildLane(lambda s: s in watched)
        order: list[str] = []
        lane.acquire("SPY")  # current holder

        def worker(sym: str) -> None:
            lane.acquire(sym)
            order.append(sym)
            lane.release()

        # QQQ queues FIRST, MSTR second - the bare Lock would run QQQ first.
        threads = []
        for sym in ("QQQ", "MSTR"):
            t = threading.Thread(target=worker, args=(sym,), daemon=True)
            t.start()
            threads.append(t)
            time_module.sleep(0.2)  # deterministic queue order
        lane.release()
        for t in threads:
            t.join(timeout=15)
        self.assertEqual(order, ["MSTR", "QQQ"])


class RefreshDeadlineTests(unittest.TestCase):
    """The in-flight guard must expire; a wedged thread must not need a restart."""

    def setUp(self) -> None:
        self.state = api_server.STATE
        self._saved = self.state.oi_finder_chart_refreshes
        self.state.oi_finder_chart_refreshes = {}
        self.addCleanup(
            setattr, self.state, "oi_finder_chart_refreshes", self._saved
        )

    def test_a_fresh_entry_counts_a_stalled_entry_does_not(self) -> None:
        deadline = api_server.OI_FINDER_CHART_REFRESH_DEADLINE_SECONDS
        self.state.oi_finder_chart_refreshes["NVDA"] = [1000.0, 1000.0]
        self.assertTrue(
            self.state._chart_refresh_in_flight("NVDA", now=1000.0 + deadline - 1)
        )
        self.assertFalse(
            self.state._chart_refresh_in_flight("NVDA", now=1000.0 + deadline + 1)
        )

    def test_missing_entries_are_not_in_flight(self) -> None:
        self.assertFalse(self.state._chart_refresh_in_flight("NVDA", now=1.0))

    def test_progress_notes_apply_only_to_the_current_token(self) -> None:
        self.state.oi_finder_chart_refreshes["NVDA"] = [111.0, 50.0]
        self.state._note_chart_refresh_progress("NVDA", 999.0)  # replaced thread
        self.assertEqual(self.state.oi_finder_chart_refreshes["NVDA"][1], 50.0)
        self.state._note_chart_refresh_progress("NVDA", 111.0)  # current thread
        self.assertGreater(self.state.oi_finder_chart_refreshes["NVDA"][1], 50.0)


class SessionAwareReadinessTests(unittest.TestCase):
    """In-memory entries obey the same session rule the disk loader learned."""

    def setUp(self) -> None:
        self.state = api_server.STATE

    @staticmethod
    def entry(newest_epoch: float, built_at: float = 0.0) -> dict:
        return {
            "payload": {"bars": [{"time": int(newest_epoch)}]},
            "built_at_epoch": built_at,
        }

    def test_a_premarket_build_demotes_at_the_bell(self) -> None:
        prior_close = et_epoch(2026, 8, 19, 19, 59)  # Wed evening
        e = self.entry(prior_close, built_at=et_epoch(2026, 8, 20, 3, 50))
        # 03:55 Thursday: the most recent session start is WEDNESDAY's -
        # the tape reaches it, so it is honestly current overnight.
        self.assertTrue(
            self.state._chart_entry_tape_is_current(e, et_epoch(2026, 8, 20, 3, 55))
        )
        # 09:31 Thursday: a new session exists and this tape does not reach
        # it. Not ready - this is what schedules the rebuild at the open.
        self.assertFalse(
            self.state._chart_entry_tape_is_current(e, et_epoch(2026, 8, 20, 9, 31))
        )

    def test_a_full_build_after_the_open_is_exempt_holiday_or_halt(self) -> None:
        friday_close = et_epoch(2026, 8, 14, 19, 59)
        e = self.entry(friday_close, built_at=et_epoch(2026, 8, 17, 9, 40))  # Mon
        self.assertTrue(
            self.state._chart_entry_tape_is_current(e, et_epoch(2026, 8, 17, 9, 45))
        )

    def test_weekend_tape_current_saturday_stale_sunday_by_window(self) -> None:
        e = self.entry(et_epoch(2026, 8, 14, 19, 59))  # Friday close
        self.assertTrue(
            self.state._chart_entry_tape_is_current(e, et_epoch(2026, 8, 15, 12, 0))
        )
        # Sunday noon is ~40h old: past the 26h window however the session
        # rule reads - the pre-existing overnight/weekend governor.
        self.assertFalse(
            self.state._chart_entry_tape_is_current(e, et_epoch(2026, 8, 16, 12, 0))
        )

    def test_empty_or_damaged_entries_are_never_current(self) -> None:
        when = et_epoch(2026, 8, 20, 9, 31)
        for bad in (None, {}, {"payload": {}}, {"payload": {"bars": []}},
                    {"payload": {"bars": [{"time": "x"}]}}):
            self.assertFalse(self.state._chart_entry_tape_is_current(bad, when))


class BellDemotionKickPlanTests(unittest.TestCase):
    """A stale tape with intact content heals by SPLICE, never a full rebuild.

    Measured 2026-08-21 09:30-09:45: demoting every premarket entry into a
    full rebuild queued 17-minute lane tails (WRBY 314s -> 1053s) and starved
    request threads. The deep tape and signals were never wrong - only the
    tail was missing.
    """

    def setUp(self) -> None:
        self.state = api_server.STATE
        self._cache = self.state.oi_finder_chart_cache
        self._refreshes = self.state.oi_finder_chart_refreshes
        self.state.oi_finder_chart_cache = {}
        self.state.oi_finder_chart_refreshes = {}
        self.addCleanup(setattr, self.state, "oi_finder_chart_cache", self._cache)
        self.addCleanup(setattr, self.state, "oi_finder_chart_refreshes", self._refreshes)

    def seed(self, newest_epoch: float) -> dict:
        entry = {
            "cached_at": 1.0,
            "payload": {"symbol": "SPY", "bars": [{"time": int(newest_epoch), "close": 640.0}]},
            "history_ready": True,
            "built_at_epoch": 0.0,
        }
        self.state.oi_finder_chart_cache["SPY"] = entry
        return entry

    def test_a_bell_demoted_entry_kicks_a_recency_splice(self) -> None:
        from unittest.mock import patch
        stale = et_epoch(2026, 8, 20, 19, 59)  # yesterday evening
        entry = self.seed(stale)
        with (
            patch.object(self.state, "_chart_payload_has_ready_ganesh_signals", return_value=True),
            patch.object(self.state, "_chart_payload_has_multi_timeframe_depth", return_value=True),
            patch.object(self.state, "_windowed_payload_for_cache_entry", side_effect=lambda e: dict(e["payload"])),
            patch.object(self.state, "_start_oi_finder_chart_refresh") as kick,
        ):
            payload = self.state._oi_finder_chart_payload_impl("SPY")
        self.assertTrue(payload["historyLoading"])  # honest: tape not current
        kick.assert_called_once_with("SPY", full_history=False)  # splice, not full
        self.assertTrue(entry["history_ready"])  # stored flag survives demotion

    def test_broken_content_still_earns_a_full_rebuild(self) -> None:
        from unittest.mock import patch
        entry = self.seed(et_epoch(2026, 8, 20, 19, 59))
        with (
            patch.object(self.state, "_chart_payload_has_ready_ganesh_signals", return_value=False),
            patch.object(self.state, "_chart_payload_has_multi_timeframe_depth", return_value=True),
            patch.object(self.state, "_windowed_payload_for_cache_entry", side_effect=lambda e: dict(e["payload"])),
            patch.object(self.state, "_start_oi_finder_chart_refresh") as kick,
        ):
            self.state._oi_finder_chart_payload_impl("SPY")
        kick.assert_called_once_with("SPY", full_history=True)
        self.assertFalse(entry["history_ready"])


if __name__ == "__main__":
    unittest.main()


class RecencySpliceReadinessTests(unittest.TestCase):
    """2026-10-05: SPX (no overnight bars) failed readiness on every request after a
    restart - disk-loaded entry, no built_at - and rebuilt 666 times in ~9 minutes."""

    def setUp(self) -> None:
        self.state = api_server.STATE

    def test_a_recency_splice_after_the_session_start_is_honestly_current(self) -> None:
        friday_close = et_epoch(2026, 10, 2, 16, 0)
        stale = {"payload": {"bars": [{"time": int(friday_close)}]}, "built_at_epoch": 0.0}
        monday_night = et_epoch(2026, 10, 5, 1, 10)
        self.assertFalse(self.state._chart_entry_tape_is_current(stale, monday_night))
        checked = {**stale, "recency_at_epoch": et_epoch(2026, 10, 5, 1, 5)}
        self.assertTrue(self.state._chart_entry_tape_is_current(checked, monday_night))

    def test_a_splice_from_before_the_open_does_not_cover_the_new_session(self) -> None:
        friday_close = et_epoch(2026, 10, 2, 16, 0)
        e = {"payload": {"bars": [{"time": int(friday_close)}]}, "recency_at_epoch": et_epoch(2026, 10, 5, 1, 5)}
        self.assertFalse(self.state._chart_entry_tape_is_current(e, et_epoch(2026, 10, 5, 9, 31)))


class ClosedIndexSpliceTests(unittest.TestCase):
    """2026-10-05 04:30-04:50 ET: SPX (prints 09:30-16:00 only) re-fetched Schwab every
    30 s for zero new bars; refresh=true requests blocked 3.7-17.9 s on it."""

    def setUp(self) -> None:
        self.state = api_server.STATE
        friday_close = et_epoch(2026, 10, 2, 16, 0)
        self.current = {"payload": {"bars": [{"time": int(friday_close)}]},
                        "recency_at_epoch": et_epoch(2026, 10, 5, 4, 0)}
        self.premarket = et_epoch(2026, 10, 5, 4, 45)

    def test_a_current_index_tape_is_left_alone_before_the_bell(self) -> None:
        self.assertTrue(self.state._index_tape_cannot_advance("SPX", self.current, self.premarket))

    def test_stocks_keep_splicing_premarket(self) -> None:
        self.assertFalse(self.state._index_tape_cannot_advance("SPY", self.current, self.premarket))

    def test_the_index_splices_during_regular_hours(self) -> None:
        self.assertFalse(self.state._index_tape_cannot_advance("SPX", self.current, et_epoch(2026, 10, 5, 9, 31)))
        self.assertFalse(self.state._index_tape_cannot_advance("SPX", self.current, et_epoch(2026, 10, 5, 16, 10)))

    def test_a_not_yet_current_index_still_gets_its_first_splice(self) -> None:
        # After a restart the disk entry carries no stamp: one splice must still run.
        unstamped = {"payload": self.current["payload"]}
        self.assertFalse(self.state._index_tape_cannot_advance("SPX", unstamped, self.premarket))

    def test_an_empty_index_tape_is_never_skipped(self) -> None:
        self.assertFalse(self.state._index_tape_cannot_advance("SPX", {"payload": {"bars": []}}, self.premarket))

    def test_weekends_count_as_closed(self) -> None:
        saturday = et_epoch(2026, 10, 3, 12, 0)
        entry = {**self.current, "recency_at_epoch": et_epoch(2026, 10, 2, 16, 20)}
        self.assertTrue(self.state._index_tape_cannot_advance("SPX", entry, saturday))

    def test_the_recency_kick_is_skipped_but_a_full_rebuild_is_not(self) -> None:
        from unittest.mock import patch
        with patch.object(self.state, "_index_tape_cannot_advance", return_value=True), \
                patch.object(self.state, "_chart_recency_refreshes") as recency_guard:
            self.state._start_oi_finder_chart_refresh("SPX", full_history=False)
        recency_guard.assert_not_called()


class StockPremarketRecencyTests(unittest.TestCase):
    """2026-10-05 07:55 ET: before 09:30 session_start is Friday's open, so one
    early splice made COHR/LASR 'current' until the bell - COHR served 06:06
    and LASR 05:36 while the stream had 07:48 / 07:34. A splice now vouches for
    a STOCK for STOCK_RECENCY_CURRENT_SECONDS; an index keeps the whole premarket."""

    def setUp(self) -> None:
        self.state = api_server.STATE
        self.friday_close = et_epoch(2026, 10, 2, 16, 0)

    def entry(self, symbol, spliced_at):
        return {"payload": {"symbol": symbol, "bars": [{"time": int(self.friday_close)}]},
                "recency_at_epoch": spliced_at}

    def test_an_old_premarket_splice_no_longer_keeps_a_stock_current(self) -> None:
        cohr = self.entry("COHR", et_epoch(2026, 10, 5, 6, 10))
        self.assertFalse(self.state._chart_entry_tape_is_current(cohr, et_epoch(2026, 10, 5, 7, 55)))

    def test_a_fresh_splice_still_keeps_a_stock_current(self) -> None:
        cohr = self.entry("COHR", et_epoch(2026, 10, 5, 7, 52))
        self.assertTrue(self.state._chart_entry_tape_is_current(cohr, et_epoch(2026, 10, 5, 7, 55)))

    def test_an_index_keeps_its_premarket_exemption(self) -> None:
        spx = self.entry("SPX", et_epoch(2026, 10, 5, 4, 0))
        self.assertTrue(self.state._chart_entry_tape_is_current(spx, et_epoch(2026, 10, 5, 7, 55)))
        self.assertTrue(self.state._index_tape_cannot_advance("SPX", spx, et_epoch(2026, 10, 5, 7, 55)))
