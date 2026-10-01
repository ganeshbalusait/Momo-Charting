"""The frozen tape: a searched ticker served candles hours behind its broker.

Measured 2026-08-31 12:56 ET against a running backend, newest served bar vs
wall clock: GME 151.8 min, SOUN 43.7, NOW 16.7, TEAM 10.7 - while a direct
SchwabClient.get_intraday_bars probe at 13:00 returned 12:59 bars, 0.7 min old,
for those same symbols. The broker had the data the whole time; the request
path never asked for it.

Two things gated it, both exercised here:

* One in-flight guard covered BOTH kinds of refresh. A deep rebuild holds that
  guard from the moment it is queued (2-worker pool, up to 20 minutes) and the
  cheap tail splice that keeps the served tape current was discarded for the
  whole wait.
* refresh=true was suppressed by that same guard, and even when honoured it
  only SUBMITTED a job and returned the old payload - which is why re-probing
  with &refresh=true moved TEAM 10.7 -> 10.8 and GME 151.8 -> 151.9 rather
  than healing anything.
"""

from __future__ import annotations

import threading
import time
import unittest
from collections import OrderedDict
from unittest.mock import patch

import api_server
from api_server import DashboardState


def minute_bars(count: int, newest_epoch: float, price: float = 100.0) -> list[dict]:
    """`count` one-minute bars ending at `newest_epoch`."""
    start = int(newest_epoch) - (count - 1) * 60
    return [
        {"time": start + index * 60, "open": price, "high": price,
         "low": price, "close": price, "volume": 10}
        for index in range(count)
    ]


class _ServePathTestCase(unittest.TestCase):
    """Drives the real serve path on the real state, with its caches swapped."""

    # A symbol with no disk cache on this machine: the serve path hydrates
    # from disk when memory misses, and a real GME archive would replace the
    # deliberately frozen tape under test.
    SYMBOL = "ZZFROZEN"

    def setUp(self) -> None:
        self.state = api_server.STATE
        saved = {
            "oi_finder_chart_cache": self.state.oi_finder_chart_cache,
            "oi_finder_chart_refreshes": self.state.oi_finder_chart_refreshes,
            "oi_finder_chart_recency_refreshes": getattr(
                self.state, "oi_finder_chart_recency_refreshes", {}
            ),
            "_chart_forced_extend_at": getattr(self.state, "_chart_forced_extend_at", {}),
        }
        for name, value in saved.items():
            self.addCleanup(setattr, self.state, name, value)
        self.state.oi_finder_chart_cache = {}
        self.state.oi_finder_chart_refreshes = {}
        self.state.oi_finder_chart_recency_refreshes = {}
        self.state._chart_forced_extend_at = {}

    def seed(self, minutes_behind: float, *, ready: bool = True) -> dict:
        """A cache entry whose CONTENT is intact and whose tape is behind."""
        newest = time.time() - minutes_behind * 60.0
        entry = {
            # One poll interval old, which is what a chart on screen looks
            # like 30s after its last store. Aged explicitly so the test does
            # not depend on where the wall clock sits in the trading session.
            "cached_at": time.monotonic() - 2 * api_server.OI_FINDER_CHART_REFRESH_SECONDS,
            "payload": {
                "symbol": self.SYMBOL,
                "bars": minute_bars(120, newest),
                "studyBars": minute_bars(90, newest),
                "dailyBars": minute_bars(60, newest),
            },
            "history_ready": ready,
            "built_at_epoch": 0.0,
        }
        self.state.oi_finder_chart_cache[self.SYMBOL] = entry
        return entry

    def hold_deep_guard(self) -> None:
        """A deep rebuild is queued or running - the everyday live condition."""
        self.state.oi_finder_chart_refreshes[self.SYMBOL] = [
            ("full", 1), time.monotonic(),
        ]

    def serve(self, content_ready: bool = True, **kwargs):
        with (
            patch.object(self.state, "_chart_payload_has_ready_ganesh_signals",
                         return_value=content_ready),
            patch.object(self.state, "_chart_payload_has_multi_timeframe_depth",
                         return_value=True),
            patch.object(self.state, "_chart_payload_has_current_mtf_labels",
                         return_value=True),
            patch.object(self.state, "_windowed_payload_for_cache_entry",
                         side_effect=lambda entry: dict(entry["payload"])),
            patch.object(self.state, "_start_oi_finder_chart_refresh") as kick,
        ):
            payload = self.state._oi_finder_chart_payload_impl(self.SYMBOL, **kwargs)
        return payload, kick


class QueuedRebuildMustNotFreezeTheTape(_ServePathTestCase):
    def test_a_searched_ticker_extends_while_a_deep_rebuild_is_queued(self) -> None:
        """THE BUG. GME sat 151.8 min behind with a deep rebuild holding the
        only guard; every poll returned the frozen tape and kicked nothing."""
        self.seed(minutes_behind=151.8)
        self.hold_deep_guard()

        _, kick = self.serve()

        kick.assert_called_once_with(self.SYMBOL, full_history=False)

    def test_broken_content_waiting_on_its_rebuild_still_gets_the_tail(self) -> None:
        """A not-content-ready entry earns a deep rebuild - but while that
        rebuild sits in the queue its tape must keep moving, not freeze for the
        whole 20-minute guard."""
        self.seed(minutes_behind=43.7)
        self.hold_deep_guard()

        _, kick = self.serve(content_ready=False)

        kick.assert_called_once_with(self.SYMBOL, full_history=False)

    def test_broken_content_with_no_rebuild_running_still_earns_one(self) -> None:
        """The tail must not have replaced the deep rebuild."""
        self.seed(minutes_behind=43.7)

        _, kick = self.serve(content_ready=False)

        kick.assert_called_once_with(self.SYMBOL, full_history=True)

    def test_a_tail_pass_already_running_is_not_duplicated(self) -> None:
        """Splitting the guards must not mean kicking a tail on every poll."""
        self.seed(minutes_behind=10.7)
        self.state.oi_finder_chart_recency_refreshes[self.SYMBOL] = [
            ("recency", 1), time.monotonic(),
        ]

        _, kick = self.serve()

        kick.assert_not_called()


class ForcedRefreshReplacesAStaleTape(_ServePathTestCase):
    def broker_answers(self, minutes_behind: float = 0.7):
        """Stand in for the recency build: fold fresh bars into the entry."""
        fresh_newest = time.time() - minutes_behind * 60.0

        def _fake_refresh(target, full_history, release=True, skip_studies=False,
                          release_token=None):
            entry = self.state.oi_finder_chart_cache[target]
            entry["payload"] = dict(entry["payload"])
            entry["payload"]["bars"] = minute_bars(150, fresh_newest)
            entry["cached_at"] = time.monotonic()

        return fresh_newest, _fake_refresh

    def test_a_forced_refresh_replaces_a_stale_tape_in_the_same_response(self) -> None:
        self.seed(minutes_behind=151.8)
        fresh_newest, fake = self.broker_answers()

        with patch.object(self.state, "_refresh_oi_finder_chart_payload", side_effect=fake):
            payload, _ = self.serve(refresh=True)

        self.assertEqual(payload["bars"][-1]["time"], int(fresh_newest))

    def test_a_forced_refresh_is_not_swallowed_by_a_queued_rebuild(self) -> None:
        """&refresh=true against the live server changed nothing because the
        in-flight guard discarded it before it reached the broker."""
        self.seed(minutes_behind=151.8)
        self.hold_deep_guard()
        fresh_newest, fake = self.broker_answers()

        with patch.object(self.state, "_refresh_oi_finder_chart_payload", side_effect=fake) as forced:
            payload, _ = self.serve(refresh=True)

        forced.assert_called_once()
        self.assertEqual(payload["bars"][-1]["time"], int(fresh_newest))

    def test_a_current_tape_costs_a_forced_refresh_nothing(self) -> None:
        """The inline fetch is bounded by the DATA: once the tape is caught up
        refresh=true does no broker work on the request thread."""
        self.seed(minutes_behind=0.2)
        _, fake = self.broker_answers()

        with patch.object(self.state, "_refresh_oi_finder_chart_payload", side_effect=fake) as forced:
            self.serve(refresh=True)

        forced.assert_not_called()

    def test_a_prefetch_never_forces_anything(self) -> None:
        self.seed(minutes_behind=151.8)
        _, fake = self.broker_answers()

        with patch.object(self.state, "_refresh_oi_finder_chart_payload", side_effect=fake) as forced:
            self.serve(refresh=True, prefetch=True)

        forced.assert_not_called()

    def test_back_to_back_forces_are_rate_limited_per_symbol(self) -> None:
        """Outside market hours no tape can catch up to the clock, so the
        inline path needs its own bound or a client loop pins a request
        thread."""
        self.seed(minutes_behind=151.8)
        _, fake = self.broker_answers(minutes_behind=151.8)  # tape stays behind

        with patch.object(self.state, "_refresh_oi_finder_chart_payload", side_effect=fake) as forced:
            self.serve(refresh=True)
            self.serve(refresh=True)
            self.serve(refresh=True)

        self.assertEqual(forced.call_count, 1)


class TailSpliceExtendsTheStoredTape(unittest.TestCase):
    """The store itself: fresh broker bars must land on the served tape, and
    an older answer must never roll it back."""

    SYMBOL = "ZZTEST"

    def setUp(self) -> None:
        self.state = api_server.STATE
        for name in ("oi_finder_chart_cache", "oi_finder_chart_refreshes",
                     "oi_finder_chart_recency_refreshes"):
            self.addCleanup(setattr, self.state, name, getattr(self.state, name, {}))
        self.state.oi_finder_chart_cache = {}
        self.state.oi_finder_chart_refreshes = {}
        self.state.oi_finder_chart_recency_refreshes = {}

    def held(self) -> dict:
        return self.state.oi_finder_chart_cache[self.SYMBOL]["payload"]

    def seed(self, minutes_behind: float) -> float:
        newest = time.time() - minutes_behind * 60.0
        self.state.oi_finder_chart_cache[self.SYMBOL] = {
            "cached_at": time.monotonic(),
            "payload": {
                "symbol": self.SYMBOL,
                "bars": minute_bars(400, newest),
                "studyBars": minute_bars(300, newest),
                "dailyBars": minute_bars(200, newest),
                "historyLoading": False,
            },
            "history_ready": True,
            "built_at_epoch": 0.0,
        }
        return newest

    def refresh_with_broker_tape(self, broker_newest: float) -> None:
        built = {
            "symbol": self.SYMBOL,
            "bars": minute_bars(300, broker_newest),
            "studyBars": minute_bars(120, broker_newest),
            "dailyBars": minute_bars(3, broker_newest),
            "historyLoading": False,
        }
        with (
            patch.object(self.state, "_build_oi_finder_chart_payload", return_value=built),
            patch.object(self.state, "_save_oi_finder_chart_disk_payload"),
        ):
            self.state._refresh_oi_finder_chart_payload(self.SYMBOL, False)

    def test_a_stored_tape_older_than_the_broker_ends_up_extended(self) -> None:
        self.seed(minutes_behind=151.8)
        broker_newest = time.time() - 42.0

        self.refresh_with_broker_tape(broker_newest)

        self.assertEqual(self.held()["bars"][-1]["time"], int(broker_newest))

    def test_a_stale_broker_answer_never_rolls_the_served_tape_back(self) -> None:
        """A tail pass may now land while a deep rebuild is still running, so
        the rebuild can finish holding bars it fetched minutes earlier."""
        held_newest = self.seed(minutes_behind=0.5)

        self.refresh_with_broker_tape(time.time() - 20 * 60.0)

        self.assertEqual(self.held()["bars"][-1]["time"], int(held_newest))


class RefreshGuardsAreIndependent(unittest.TestCase):
    def state(self) -> DashboardState:
        stub = DashboardState.__new__(DashboardState)
        stub.oi_finder_chart_lock = threading.RLock()
        stub.oi_finder_chart_refreshes = {}
        stub.oi_finder_chart_recency_refreshes = {}
        stub.submitted = []
        stub.oi_finder_chart_refresh_pool = type(
            "_Pool", (), {"submit": lambda _self, fn, *a: stub.submitted.append(a)},
        )()
        stub.oi_finder_chart_paint_pool = stub.oi_finder_chart_refresh_pool
        return stub

    def test_a_queued_deep_rebuild_does_not_block_a_tail_pass(self) -> None:
        stub = self.state()
        stub.oi_finder_chart_refreshes["GME"] = [("full", 1), time.monotonic()]

        stub._start_oi_finder_chart_refresh("GME", full_history=False)

        self.assertEqual(len(stub.submitted), 1)
        self.assertIn("GME", stub.oi_finder_chart_recency_refreshes)

    def test_a_tail_pass_does_not_block_a_deep_rebuild(self) -> None:
        stub = self.state()
        stub.oi_finder_chart_recency_refreshes["GME"] = [("recency", 1), time.monotonic()]

        stub._start_oi_finder_chart_refresh("GME", full_history=True)

        self.assertEqual(len(stub.submitted), 1)
        self.assertIn("GME", stub.oi_finder_chart_refreshes)

    def test_each_kind_still_dedupes_against_itself(self) -> None:
        stub = self.state()
        stub._start_oi_finder_chart_refresh("GME", full_history=False)
        stub._start_oi_finder_chart_refresh("GME", full_history=False)
        self.assertEqual(len(stub.submitted), 1)

    def test_a_tail_guard_expires_far_sooner_than_a_deep_one(self) -> None:
        stub = self.state()
        stub.oi_finder_chart_recency_refreshes["GME"] = [("recency", 1), 1000.0]
        deadline = api_server.OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS
        self.assertLess(deadline, api_server.OI_FINDER_CHART_REFRESH_DEADLINE_SECONDS)
        self.assertTrue(
            stub._chart_refresh_in_flight("GME", now=1000.0 + deadline - 1, kind="recency")
        )
        self.assertFalse(
            stub._chart_refresh_in_flight("GME", now=1000.0 + deadline + 1, kind="recency")
        )

    def test_any_kind_is_what_the_client_sees_as_refreshing(self) -> None:
        stub = self.state()
        stub.oi_finder_chart_recency_refreshes["GME"] = [("recency", 1), time.monotonic()]
        self.assertTrue(stub._chart_refresh_in_flight("GME"))
        self.assertFalse(stub._chart_refresh_in_flight("GME", kind="full"))

    def test_a_release_clears_only_the_guard_its_token_owns(self) -> None:
        stub = self.state()
        stub.oi_finder_chart_cache = {}
        stub.oi_finder_chart_refreshes["GME"] = [("full", 7), time.monotonic()]
        stub.oi_finder_chart_recency_refreshes["GME"] = [("recency", 8), time.monotonic()]
        with patch.object(DashboardState, "_build_oi_finder_chart_payload", return_value={}):
            stub._refresh_oi_finder_chart_payload("GME", False, release_token=("recency", 8))
        self.assertIn("GME", stub.oi_finder_chart_refreshes)
        self.assertNotIn("GME", stub.oi_finder_chart_recency_refreshes)


class HotSetFollowsTheRequests(unittest.TestCase):
    """A chart REQUEST makes a symbol eligible to extend. The set stays bounded
    by the interactive window rather than by a ten-slot cap that the
    permanently-hot symbols could themselves consume."""

    def stub(self) -> DashboardState:
        stub = DashboardState.__new__(DashboardState)
        stub.oi_finder_recent_chart_symbols = OrderedDict()
        stub._chart_symbol_last_requested = {}
        return stub

    def test_a_symbol_outside_the_hot_set_is_eligible_while_it_is_polled(self) -> None:
        stub = self.stub()
        stub._chart_symbol_last_requested["SOUN"] = time.monotonic()
        self.assertNotIn("SOUN", stub._fixed_hot_chart_symbols())
        self.assertIn("SOUN", stub._hot_chart_symbols())

    def test_eligibility_lapses_once_nobody_is_looking(self) -> None:
        stub = self.stub()
        stub._chart_symbol_last_requested["SOUN"] = (
            time.monotonic() - api_server.OI_FINDER_CHART_INTERACTIVE_REQUEST_SECONDS - 1
        )
        self.assertNotIn("SOUN", stub._hot_chart_symbols())

    def test_a_permanently_hot_symbol_never_spends_a_recents_slot(self) -> None:
        """Opening SPY used to evict the ticker the trader had just searched."""
        stub = self.stub()
        stub.oi_finder_recent_chart_symbols["SOUN"] = time.time()
        with patch.object(DashboardState, "_oi_finder_chart_payload_impl", return_value={}):
            stub.oi_finder_chart_payload("SPY")
        self.assertNotIn("SPY", stub.oi_finder_recent_chart_symbols)
        self.assertIn("SOUN", stub.oi_finder_recent_chart_symbols)

    def test_a_searched_symbol_still_takes_a_recents_slot(self) -> None:
        stub = self.stub()
        with patch.object(DashboardState, "_oi_finder_chart_payload_impl", return_value={}):
            stub.oi_finder_chart_payload("soun")
        self.assertIn("SOUN", stub.oi_finder_recent_chart_symbols)


class MtfStudyMemo(unittest.TestCase):
    """Same tapes in, same labels out - the measured 47% hotspot.

    Keyed on every tape the label engines read, so a hit can only return what a
    recompute would have returned.
    """

    def frame(self, rows: int, newest: str, close: float = 10.0):
        import pandas as pd
        stamps = pd.date_range(end=newest, periods=rows, freq="1min", tz="UTC")
        return pd.DataFrame({"timestamp": stamps, "close": [close] * rows})

    def stub(self) -> DashboardState:
        return DashboardState.__new__(DashboardState)

    def test_identical_tapes_reuse_the_previous_label_run(self) -> None:
        stub = self.stub()
        tape = self.frame(50, "2026-08-31 16:00")
        key = stub._mtf_study_memo_key("GME", tape)
        stub._mtf_study_memo_put(key, {"signals": [1]}, {"5": [1]}, {"states": []})
        again = stub._mtf_study_memo_key("GME", self.frame(50, "2026-08-31 16:00"))
        self.assertEqual(key, again)
        self.assertIsNotNone(stub._mtf_study_memo_get(again))

    def test_one_more_bar_is_a_different_run(self) -> None:
        stub = self.stub()
        key = stub._mtf_study_memo_key("GME", self.frame(50, "2026-08-31 16:00"))
        stub._mtf_study_memo_put(key, {}, {}, {})
        moved = stub._mtf_study_memo_key("GME", self.frame(51, "2026-08-31 16:01"))
        self.assertIsNone(stub._mtf_study_memo_get(moved))

    def test_a_tick_inside_the_current_bar_is_a_different_run(self) -> None:
        """The daily bar's close moves without the row count or timestamp
        moving, and the daily frame feeds the label engine."""
        stub = self.stub()
        key = stub._mtf_study_memo_key("GME", self.frame(50, "2026-08-31 16:00", close=10.0))
        stub._mtf_study_memo_put(key, {}, {}, {})
        ticked = stub._mtf_study_memo_key("GME", self.frame(50, "2026-08-31 16:00", close=10.5))
        self.assertIsNone(stub._mtf_study_memo_get(ticked))

    def test_two_symbols_never_share_a_run(self) -> None:
        stub = self.stub()
        tape = self.frame(50, "2026-08-31 16:00")
        stub._mtf_study_memo_put(stub._mtf_study_memo_key("GME", tape), {}, {}, {})
        self.assertIsNone(
            stub._mtf_study_memo_get(stub._mtf_study_memo_key("SOUN", tape))
        )

    def test_the_memo_is_bounded(self) -> None:
        stub = self.stub()
        limit = api_server.OI_FINDER_MTF_STUDY_MEMO_LIMIT
        for index in range(limit + 20):
            stub._mtf_study_memo_put((f"SYM{index}", ()), {}, {}, {})
        self.assertEqual(len(stub._oi_finder_mtf_study_memo), limit)

    def test_a_missing_key_is_never_a_hit(self) -> None:
        self.assertIsNone(self.stub()._mtf_study_memo_get(None))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
