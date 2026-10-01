"""The MomX board prewarm: build the charts he can SEE before he clicks them.

His words: "chart loading very slow, can we do like this when the tickers you
see in the 'momscanner' backend chart make it load latest, instead of type and
load taking more time."

Measured on this box 2026-08-31, market open:
  * a genuinely cold chart build costs 112s wall clock (MMM), ~65s of it queue
    wait behind the 2-worker study pool;
  * but 60/60 board rows already have a cache file, and 44 of the 50 Watchlist
    rows are merely BEHIND (candles end 2026-08-28) - a ~4s recency splice;
  * the 2-worker refresh pool is already oversubscribed: ~15.2 tail refreshes
    a minute asked for, 10.3-12.4 completed.

So the prewarm's whole job is to be nearly free. These tests pin the four
things that make it free and the three that make it safe:

  free : steady state does nothing; a cached symbol is skipped; the tail budget
         is capped; the symbol list is capped.
  safe : the kill switch is a file he can create by hand; keeper_paused does
         NOT silently disable the cheap splices (that marker exists on this box
         today, so honouring it wholesale would ship a no-op) but DOES stop the
         expensive cold builds, which is the thing it was created to stop; a
         torn/missing/garbage board file never raises; a symbol that never
         produces a tape is blacklisted instead of rebuilt forever (the
         measured GOOGLE loop).

  honest: 'warm' has an AGE CEILING, not just a session pivot. Without it the
         loop was a no-op through the whole premarket window it starts early
         to serve, and a once-per-symbol-per-day event after that, while
         health reported it healthy. And health reports the observed BACKLOG,
         never the per-cycle budget, so 'behind: 0' cannot mean "eight stale
         but paced". Every clock-injected test drives ONE clock all the way
         down; the previous premarket test passed against a behaviour that
         did not exist because it only half-honoured its own injected clock.
"""

from __future__ import annotations

import inspect
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import api_server
from api_server import DashboardState


def board_file(directory: Path, name: str, symbols, *, generated=None, rows=None) -> Path:
    """A board file shaped exactly like momx/service.py writes one."""
    if rows is None:
        rows = [{"symbol": symbol, "last": 1.0, "pctChange": 0.0} for symbol in symbols]
    payload = {
        "generatedAt": (generated or datetime.now(timezone.utc)).isoformat(),
        "tapeAsOf": None,
        "universe": list(symbols),
        "universeCount": len(symbols),
        "rows": rows,
        "errors": [],
        "list": name,
    }
    path = directory / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class _PrewarmStub(unittest.TestCase):
    """A DashboardState built WITHOUT __init__ - the house rule in this file.

    Every helper the prewarm adds has to survive that, because every other
    test in tests/ builds its state this way.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.boards = self.root / "momx_board_cache"
        self.boards.mkdir()
        self.charts = self.root / "oi_chart_cache"
        self.charts.mkdir()
        self.addCleanup(self._tmp.cleanup)

        # HERMETIC MARKERS. This box really has artifacts/keeper_paused, and
        # the cold-build path now honours it, so any test that read the real
        # ARTIFACTS_DIR would pass or fail depending on the machine it ran on.
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        marker_patch = patch.object(api_server, "ARTIFACTS_DIR", self.artifacts)
        marker_patch.start()
        self.addCleanup(marker_patch.stop)

        self.state = DashboardState.__new__(DashboardState)
        self.state.momx_board_cache_dir = self.boards
        self.state.oi_finder_chart_disk_cache_dir = self.charts
        self.state.oi_finder_chart_cache = {}
        self.state.oi_finder_chart_refreshes = {}
        self.state.oi_finder_chart_recency_refreshes = {}
        self.state.oi_finder_interactive_until = 0.0
        self.state.oi_finder_ondemand_builds = 0
        self.state._chart_symbol_last_requested = {}

        self.touched: list[str] = []
        self.built: list[str] = []
        self.state._touch_chart_tail = lambda symbol, *a, **k: self.touched.append(symbol)
        self.state._start_oi_finder_chart_refresh = (
            lambda symbol, full_history=False: self.built.append((symbol, full_history))
        )

    # -- helpers ---------------------------------------------------------
    # ONE CLOCK. Every test drives the cycle at self.clock() and every fixture
    # mtime is expressed relative to THAT, never to the real wall clock.
    # Getting this wrong is not cosmetic: the previous harness injected now_et
    # into the cycle while deriving both session_start and the fixture mtimes
    # from datetime.now(), so test_premarket_0330_is_inside_the_window
    # certified a premarket behaviour that did not exist. A clock-injected
    # test that half-honours the clock proves only that the code ran.
    def clock(self, hour: int = 10, minute: int = 0) -> datetime:
        return weekday_at(hour, minute)

    def session_start(self, at: datetime | None = None) -> float:
        return DashboardState._most_recent_session_start(
            (at or self.clock()).astimezone(timezone.utc)
        ).timestamp()

    def fresh(self, at: datetime | None = None, ago: float = 60.0) -> float:
        """An mtime that is warm AS OF `at` - this session AND inside the age
        ceiling. Both halves matter; see BOARD_PREWARM_MAX_CACHE_AGE_SECONDS."""
        return (at or self.clock()).timestamp() - ago

    def classify(self, symbol: str, at: datetime | None = None) -> str:
        at = at or self.clock()
        return self.state._board_prewarm_classify(
            symbol, self.session_start(at), at.timestamp()
        )

    def cache_file(self, symbol: str, *, mtime: float) -> Path:
        path = self.charts / f"{symbol}.json.gz"
        path.write_bytes(b"not really gzip - only the mtime is ever read")
        os.utime(path, (mtime, mtime))
        return path


class SymbolSelection(_PrewarmStub):
    def test_board_rows_are_taken_in_board_order(self) -> None:
        board_file(self.boards, "Watchlist", ["CRCL", "QMCO", "COIN", "TSLA"])
        self.assertEqual(
            self.state._board_prewarm_symbols(), ["CRCL", "QMCO", "COIN", "TSLA"]
        )

    def test_only_the_top_n_rows_of_each_board_are_taken(self) -> None:
        self.state.BOARD_PREWARM_TOP_N = 3
        board_file(self.boards, "Watchlist", ["A", "B", "C", "D", "E"])
        self.assertEqual(self.state._board_prewarm_symbols(), ["A", "B", "C"])

    def test_two_boards_merge_and_dedupe(self) -> None:
        board_file(self.boards, "Mag7", ["TSLA", "NVDA"])
        board_file(self.boards, "Watchlist", ["CRCL", "TSLA"])
        # Watchlist claims slots first; TSLA is on both boards and must
        # occupy one slot.
        self.assertEqual(self.state._board_prewarm_symbols(), ["CRCL", "TSLA", "NVDA"])

    def test_the_bull_watchlist_claims_slots_before_bear_and_news_boards(self) -> None:
        """2026-10-01: alphabetical order spent all 64 slots on Daily_news,
        Mag7.bear and Watchlist.bear; 2/50 of his main board were warmed."""
        self.state.BOARD_PREWARM_MAX_SYMBOLS = 4
        board_file(self.boards, "Daily_news", ["N1", "N2"])
        board_file(self.boards, "Watchlist.bear", ["B1", "B2"])
        board_file(self.boards, "Mag7.bear", ["M1"])
        board_file(self.boards, "Watchlist", ["W1", "W2"])
        board_file(self.boards, "Mag7", ["T1"])
        self.assertEqual(self.state._board_prewarm_symbols(), ["W1", "W2", "T1", "N1"])
        self.state.BOARD_PREWARM_MAX_SYMBOLS = 64
        self.assertEqual(self.state._board_prewarm_symbols(),
                         ["W1", "W2", "T1", "N1", "N2", "B1", "B2", "M1"])

    def test_a_row_that_is_not_a_ticker_never_spends_a_slot(self) -> None:
        board_file(
            self.boards,
            "Watchlist",
            [],
            rows=[
                {"symbol": "SPY"},
                {"symbol": "not a ticker"},
                {"symbol": ""},
                "a string row, not a dict",
                {"symbol": "WAYTOOLONGSYMBOL"},
                {"symbol": "crcl"},
            ],
        )
        self.assertEqual(self.state._board_prewarm_symbols(), ["SPY", "CRCL"])

    def test_a_board_older_than_the_freshness_gate_contributes_nothing(self) -> None:
        """A dead momx_worker means we no longer know what is on his screen,
        so the list must go EMPTY rather than freeze on rows he left."""
        stale = datetime.now(timezone.utc) - timedelta(
            seconds=DashboardState.BOARD_PREWARM_BOARD_MAX_AGE_SECONDS + 60
        )
        board_file(self.boards, "Watchlist", ["CRCL"], generated=stale)
        self.assertEqual(self.state._board_prewarm_symbols(), [])


class TheCaps(_PrewarmStub):
    def test_the_symbol_list_is_hard_capped(self) -> None:
        self.state.BOARD_PREWARM_TOP_N = 50
        self.state.BOARD_PREWARM_MAX_SYMBOLS = 5
        board_file(self.boards, "Watchlist", [f"S{index}" for index in range(40)])
        self.assertEqual(len(self.state._board_prewarm_symbols()), 5)

    def test_at_most_two_tail_splices_per_cycle(self) -> None:
        behind = self.session_start() - 3 * 86400
        symbols = ["AAA", "BBB", "CCC", "DDD"]
        for symbol in symbols:
            self.cache_file(symbol, mtime=behind)
        plan = self.state._board_prewarm_plan(symbols, self.session_start())
        self.assertEqual(len(plan["tails"]), DashboardState.BOARD_PREWARM_MAX_TAILS_PER_CYCLE)
        self.assertEqual(plan["tails"], ["AAA", "BBB"])

    def test_at_most_one_cold_build_per_cycle(self) -> None:
        plan = self.state._board_prewarm_plan(["AAA", "BBB", "CCC"], self.session_start())
        self.assertEqual(plan["cold"], ["AAA"])

    def test_the_ledgers_cannot_grow_without_bound(self) -> None:
        self.state.BOARD_PREWARM_LEDGER_LIMIT = 4
        for index in range(20):
            self.state._board_prewarm_note_touch(f"S{index}")
            self.state._board_prewarm_note_cold_attempt(f"C{index}")
        self.assertLessEqual(len(self.state._board_prewarm_touched_at), 4)
        self.assertLessEqual(len(self.state._board_prewarm_strikes), 4)


class AlreadyCachedCostsNothing(_PrewarmStub):
    """Steady state: once a board symbol's cache was written this session the
    loop must do nothing at all for it - one stat() and no submission."""

    def test_a_cache_just_written_is_warm(self) -> None:
        self.cache_file("CRCL", mtime=self.fresh())
        self.assertEqual(self.classify("CRCL"), "warm")

    def test_a_warm_board_produces_no_work_at_all(self) -> None:
        board_file(self.boards, "Watchlist", ["CRCL", "TSLA"])
        for symbol in ("CRCL", "TSLA"):
            self.cache_file(symbol, mtime=self.fresh())
        with patch.object(api_server.time, "sleep"):
            delay = self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
        self.assertEqual(self.touched, [])
        self.assertEqual(self.built, [])
        self.assertEqual(delay, DashboardState.BOARD_PREWARM_CYCLE_SECONDS)
        self.assertEqual(self.state._board_prewarm_status["warm"], 2)

    def test_a_fresh_in_memory_entry_is_warm_even_with_no_file(self) -> None:
        at = self.clock()
        self.state.oi_finder_chart_cache["CRCL"] = {
            "history_ready": True,
            "built_at_epoch": self.session_start(at) + 120,
            "payload": {"bars": [{"time": int(at.timestamp())}]},
        }
        self.assertEqual(self.classify("CRCL"), "warm")

    def test_a_friday_cache_is_behind_not_cold(self) -> None:
        """44 of 50 Watchlist rows on 2026-08-31. Behind buys a ~4s splice;
        calling it cold would buy a 45-199s rebuild instead."""
        self.cache_file("QMCO", mtime=self.fresh(ago=3 * 86400))
        self.assertEqual(self.classify("QMCO"), "behind")

    def test_a_symbol_with_no_file_is_cold(self) -> None:
        self.assertEqual(self.classify("ZZNEW"), "cold")

    def test_a_symbol_the_disk_path_rejects_is_skipped_entirely(self) -> None:
        self.assertEqual(self.classify("not a ticker"), "skip")

    # -- the age ceiling: three findings, three tests --------------------
    def test_a_cache_written_this_session_but_hours_old_is_behind(self) -> None:
        """THE mid-session decay bug. Anchoring 'warm' to "was this file
        written since 09:30?" made the loop a once-per-symbol-per-DAY event:
        a symbol spliced at 09:35 stayed 'warm' for the rest of the session
        while its candles aged, and 11 of the 22 live board rows are in
        neither QUICK_STRIP_WARM_SYMBOLS nor PREMARKET_SCAN_SYMBOLS, so
        nothing else was going to touch them. Measured on the live board at
        15:26 ET 2026-08-31: CRCL (file 14:22) and RBLX (file 13:40) both
        classified warm and were planned for no work."""
        at = self.clock(15, 0)
        self.cache_file("RBLX", mtime=self.session_start(at) + 300)  # 09:35
        self.assertGreater(at.timestamp(), self.session_start(at) + 300)
        self.assertEqual(self.classify("RBLX", at), "behind")

    def test_a_cache_from_the_previous_session_is_behind_during_premarket(self) -> None:
        """THE premarket bug, and the reason this loop starts at 03:30.
        _most_recent_session_start returns the most recent 09:30 that has
        ALREADY PASSED, so from 03:30 to 09:30 it still points at YESTERDAY's
        open - and every file this loop wrote yesterday therefore answered
        'warm'. The entire premarket window did the least work of any."""
        # Both instants are derived from the SAME premarket clock. Deriving
        # "yesterday afternoon" from an independent self.clock(15, 30) made the
        # arithmetic depend on whether 15:30 had happened yet in the real world:
        # once weekday_at started returning only past times, that call stepped
        # back a day on its own and "yesterday" silently became two days ago.
        premarket = self.clock(7, 0)
        yesterday_afternoon = (premarket - timedelta(days=1)).replace(hour=15, minute=30)
        self.cache_file("QMCO", mtime=yesterday_afternoon.timestamp())
        # The trap itself, pinned: the session pivot alone still says warm.
        self.assertGreaterEqual(
            yesterday_afternoon.timestamp(), self.session_start(premarket)
        )
        self.assertEqual(self.classify("QMCO", premarket), "behind")

    def test_a_cache_past_the_five_day_disk_cliff_is_cold_not_behind(self) -> None:
        """_load_oi_finder_chart_disk_payload returns None past
        OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS, so _touch_chart_tail
        cannot hydrate it, the splice lands on an empty cache as a bare fast
        tape, historyLoading stays set, the disk save returns early and the
        mtime never advances - a symbol re-kicked forever that never appears
        in the cold counter or the strike ledger built to catch exactly that.
        'cold' routes it back under the budget and the yield gates."""
        self.cache_file(
            "STALE", mtime=self.fresh(ago=api_server.OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS + 3600)
        )
        self.assertEqual(self.classify("STALE"), "cold")
        # One day either side of the cliff is the difference between a ~4s
        # splice and a full rebuild, so pin the safe side too.
        self.cache_file(
            "OKISH", mtime=self.fresh(ago=api_server.OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS - 3600)
        )
        self.assertEqual(self.classify("OKISH"), "behind")


class TheKillSwitch(_PrewarmStub):
    def test_the_marker_file_stops_the_cycle_doing_any_work(self) -> None:
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.session_start() - 3 * 86400)
        with tempfile.TemporaryDirectory() as artifacts:
            marker = Path(artifacts) / "prewarm_paused"
            marker.write_text("", encoding="utf-8")
            with patch.object(api_server, "ARTIFACTS_DIR", Path(artifacts)):
                self.assertTrue(self.state._board_prewarm_is_paused())
                with patch.object(api_server.time, "sleep"):
                    delay = self.state._momx_board_chart_prewarm_cycle(
                        now_et=weekday_at(10, 0)
                    )
        self.assertEqual(self.touched, [])
        self.assertEqual(self.built, [])
        self.assertEqual(delay, 30.0)
        self.assertEqual(self.state._board_prewarm_status["state"], "paused")

    def test_windows_leaving_dot_txt_on_the_name_still_counts(self) -> None:
        with tempfile.TemporaryDirectory() as artifacts:
            (Path(artifacts) / "prewarm_paused.txt").write_text("", encoding="utf-8")
            with patch.object(api_server, "ARTIFACTS_DIR", Path(artifacts)):
                self.assertTrue(self.state._board_prewarm_is_paused())

    def test_keeper_paused_does_NOT_disable_this_loop(self) -> None:
        """THE core gate. artifacts/keeper_paused exists on this box right
        now; it was created against the 380-name warmer's inline builds. If
        this loop honoured it wholesale, the feature would report itself
        enabled and do nothing - the failure this codebase keeps paying for.
        The cheap splices carry essentially all of the measured value (8
        behind, 0 cold on the live board), so they keep running."""
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.fresh(ago=3 * 86400))
        for name in ("keeper_paused", "warmer_paused"):
            (self.artifacts / name).write_text("", encoding="utf-8")
        self.assertTrue(self.state._warmer_is_paused())
        self.assertFalse(self.state._board_prewarm_is_paused())
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
        self.assertEqual(self.touched, ["QMCO"])

    def test_prewarm_cold_paused_stops_the_cold_build_and_keeper_paused_does_not(self) -> None:
        """The other half, and review's operational point. A human dropped
        keeper_paused on this box during a live incident to stop background
        chart BUILDING. Shipping a second background full-build path that the
        marker does not cover means the next time he reaches for that lever it
        silently will not work, and he has no way to find out why. Honouring
        it for the expensive path only costs the feature nothing measurable."""
        board_file(self.boards, "Watchlist", ["ZZNEW"])
        self.assertTrue(self.state._board_prewarm_cold_build_allowed("ZZNEW"))
        # 2026-10-01: keeper_paused (left over from 08-28) no longer stops
        # this loop's cold builds - it blocked 38/64 board charts for a month.
        (self.artifacts / "keeper_paused").write_text("", encoding="utf-8")
        self.assertTrue(self.state._board_prewarm_cold_build_allowed("ZZNEW"))
        (self.artifacts / "prewarm_cold_paused").write_text("", encoding="utf-8")
        self.assertFalse(self.state._board_prewarm_cold_build_allowed("ZZNEW"))
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
        self.assertEqual(self.built, [])
        # ...and health says so, rather than looking healthy and idle.
        self.assertTrue(self.state._board_prewarm_status_payload()["coldBuildsPaused"])

    def test_no_marker_means_running(self) -> None:
        self.assertFalse(self.state._board_prewarm_is_paused())
        self.assertTrue(self.state._board_prewarm_cold_build_allowed("CRCL"))


class ABadBoardFileNeverKillsTheLoop(_PrewarmStub):
    def test_a_missing_directory_yields_nothing(self) -> None:
        self.state.momx_board_cache_dir = self.root / "does_not_exist"
        self.assertEqual(self.state._board_prewarm_symbols(), [])

    def test_a_missing_file_yields_nothing(self) -> None:
        self.assertEqual(
            self.state._board_prewarm_read_file(self.boards / "Gone.json"), []
        )

    def test_a_truncated_file_yields_nothing(self) -> None:
        good = board_file(self.boards, "Watchlist", ["CRCL", "QMCO"])
        raw = good.read_bytes()
        good.write_bytes(raw[: len(raw) // 2])
        self.assertEqual(self.state._board_prewarm_read_file(good), [])
        self.assertEqual(self.state._board_prewarm_symbols(), [])

    def test_garbage_yields_nothing(self) -> None:
        path = self.boards / "Watchlist.json"
        path.write_bytes(b"\x00\x01 not json at all \xff")
        self.assertEqual(self.state._board_prewarm_symbols(), [])

    def test_valid_json_of_the_wrong_shape_yields_nothing(self) -> None:
        for content in ("[]", '"a string"', '{"rows": "not a list"}', "null"):
            path = self.boards / "Watchlist.json"
            path.write_text(content, encoding="utf-8")
            self.assertEqual(self.state._board_prewarm_symbols(), [], content)

    def test_one_bad_board_does_not_hide_the_good_one(self) -> None:
        (self.boards / "Broken.json").write_bytes(b"{oh no")
        board_file(self.boards, "Watchlist", ["CRCL"])
        self.assertEqual(self.state._board_prewarm_symbols(), ["CRCL"])

    def test_a_board_with_no_generatedAt_falls_back_to_the_file_mtime(self) -> None:
        path = self.boards / "Watchlist.json"
        path.write_text(json.dumps({"rows": [{"symbol": "CRCL"}]}), encoding="utf-8")
        self.assertEqual(self.state._board_prewarm_symbols(), ["CRCL"])


class ItYieldsToTheTrader(_PrewarmStub):
    def test_a_symbol_he_is_looking_at_is_never_prewarm_built(self) -> None:
        self.state._chart_symbol_last_requested["CRCL"] = time.monotonic()
        self.assertFalse(self.state._board_prewarm_cold_build_allowed("CRCL"))

    def test_no_second_concurrent_deep_build(self) -> None:
        self.state.oi_finder_chart_refreshes["MSTR"] = [("full", 1), time.monotonic()]
        self.assertFalse(self.state._board_prewarm_cold_build_allowed("CRCL"))

    def test_an_on_demand_build_he_triggered_defers_the_prewarm(self) -> None:
        self.state.oi_finder_ondemand_builds = 1
        self.assertFalse(self.state._board_prewarm_cold_build_allowed("CRCL"))

    def test_chart_polling_alone_no_longer_defers_the_prewarm(self) -> None:
        """2026-10-01: every open chart tab's poll held the 45s window, so with
        his charts open the prewarm built one cold chart per 15 min (32 cold,
        0 built in 6 min). Measured cost of one background build after
        hours: warm chart refresh 0.29s -> 0.45s avg, auth 0.10s -> 0.23s."""
        self.state.oi_finder_interactive_until = time.monotonic() + 45
        self.state._board_prewarm_last_build_at = time.monotonic()
        self.assertTrue(self.state._board_prewarm_cold_build_allowed("CRCL"))

    def test_a_chart_he_is_opening_defers_the_prewarm(self) -> None:
        self.state.oi_finder_ondemand_builds = 1
        self.assertFalse(self.state._board_prewarm_cold_build_allowed("CRCL"))

    def test_a_permanently_busy_app_cannot_starve_it_forever(self) -> None:
        """WARMER_FORCE_PROGRESS_SECONDS is the floor: a tab open all day
        would otherwise turn this into the silent no-op it must never be."""
        self.state.oi_finder_interactive_until = time.monotonic() + 45
        self.state._board_prewarm_last_build_at = (
            time.monotonic() - DashboardState.WARMER_FORCE_PROGRESS_SECONDS - 1
        )
        self.assertTrue(self.state._board_prewarm_cold_build_allowed("CRCL"))

    def test_the_in_flight_scan_holds_the_chart_lock(self) -> None:
        """Every writer of oi_finder_chart_refreshes holds this lock, and
        list() over a dict being mutated raises RuntimeError - which the
        enclosing except would swallow into 'never cold-build': fail-safe, but
        silently, with no counter that would ever show it happened."""
        seen = []

        class _WatchedLock:
            def __enter__(inner):
                seen.append("acquired")
                return inner

            def __exit__(inner, *exc):
                return False

        self.state.oi_finder_chart_lock = _WatchedLock()
        self.state.oi_finder_chart_refreshes = {"MSTR": [(), time.monotonic()]}
        self.state._board_prewarm_cold_build_allowed("CRCL")
        self.assertEqual(seen, ["acquired"])

    def test_an_idle_box_lets_it_build(self) -> None:
        self.assertTrue(self.state._board_prewarm_cold_build_allowed("CRCL"))


class TheClockGate(_PrewarmStub):
    def test_overnight_does_nothing(self) -> None:
        board_file(self.boards, "Watchlist", ["QMCO"])
        with patch.object(api_server.time, "sleep"):
            delay = self.state._momx_board_chart_prewarm_cycle(now_et=weekday_at(2, 0))
        self.assertEqual((self.touched, self.built), ([], []))
        self.assertEqual(delay, 120.0)

    def test_the_weekend_does_nothing(self) -> None:
        board_file(self.boards, "Watchlist", ["QMCO"])
        eastern = ZoneInfo(api_server.EASTERN_TZ)
        saturday = datetime.now(eastern).replace(hour=10, minute=0)
        while saturday.weekday() != 5:
            saturday += timedelta(days=1)
        with patch.object(api_server.time, "sleep"):
            delay = self.state._momx_board_chart_prewarm_cycle(now_et=saturday)
        self.assertEqual((self.touched, self.built), ([], []))
        self.assertEqual(delay, 120.0)

    def test_premarket_0330_actually_splices_yesterdays_tape(self) -> None:
        """Half an hour before _hot_chart_refresher_loop starts, on purpose:
        a Monday board's top rows still end on Friday, so the catch-up has to
        begin before he is awake.

        This test used to assert only that 03:45 is inside the window, with a
        fixture mtime derived from the REAL clock while now_et was injected -
        so it passed while the premarket window was a total no-op. It now
        pins the case that matters: a cache written during YESTERDAY's session
        must be spliced at 07:00 today, not reported warm."""
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=(self.clock(15, 30) - timedelta(days=1)).timestamp())
        with patch.object(api_server.time, "sleep"):
            delay = self.state._momx_board_chart_prewarm_cycle(now_et=self.clock(3, 45))
        self.assertEqual(self.touched, ["QMCO"])
        self.assertEqual(delay, DashboardState.BOARD_PREWARM_CYCLE_SECONDS)
        self.assertEqual(self.state._board_prewarm_status["behind"], 1)

    def test_the_cycle_uses_one_clock_for_the_window_and_the_tape(self) -> None:
        """The injected clock must reach the classifier, not just the window
        gate. Same fixture, two clocks, two different answers - which is only
        possible if now_et is honoured all the way down."""
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.clock(9, 40).timestamp())
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock(9, 45))
        self.assertEqual(self.touched, [])  # five minutes old: warm
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock(15, 0))
        self.assertEqual(self.touched, ["QMCO"])  # five hours old: behind


class TheWorkItActuallyDoes(_PrewarmStub):
    def test_a_behind_symbol_gets_a_tail_splice_never_a_rebuild(self) -> None:
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.session_start() - 3 * 86400)
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=weekday_at(10, 0))
        self.assertEqual(self.touched, ["QMCO"])
        self.assertEqual(self.built, [])

    def test_a_cold_symbol_goes_through_the_normal_refresh_door(self) -> None:
        board_file(self.boards, "Watchlist", ["ZZNEW"])
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=weekday_at(10, 0))
        self.assertEqual(self.built, [("ZZNEW", True)])
        self.assertEqual(self.touched, [])

    def test_a_tail_that_RAISES_still_records_its_cooldown(self) -> None:
        """The note used to come after the call, inside the same try. A
        _touch_chart_tail that reliably raises then never recorded a cooldown
        and the symbol was retried every 60s forever - two submissions a
        minute against a max_workers=2 pool that is already oversubscribed,
        with no strike ledger on the cheap path to stop it. The negative cache
        has to survive the thing it is protecting against failing."""
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.fresh(ago=3 * 86400))

        def _boom(symbol, *a, **k):
            self.touched.append(symbol)
            raise RuntimeError("pool rejected the submission")

        self.state._touch_chart_tail = _boom
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock(10, 1))
        self.assertEqual(self.touched, ["QMCO"], "retried despite raising")

    def test_a_touched_symbol_is_not_retouched_next_cycle(self) -> None:
        """05b5d6c skips the disk write when a splice changes nothing, so the
        mtime never advances - without our OWN memory this symbol would be
        re-touched every single cycle forever."""
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.session_start() - 3 * 86400)
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=weekday_at(10, 0))
            self.state._momx_board_chart_prewarm_cycle(now_et=weekday_at(10, 1))
        self.assertEqual(self.touched, ["QMCO"])

    def test_a_symbol_that_never_produces_a_tape_is_blacklisted(self) -> None:
        """The measured GOOGLE loop: 16 full builds in 21 minutes, every paint
        bars=0, no cache file ever written, so "file missing => cold" stayed
        true forever. A prewarm without a negative cache is a GOOGLE factory."""
        board_file(self.boards, "Watchlist", ["GOOGLE"])
        with patch.object(api_server.time, "sleep"):
            for minute in range(6):
                self.state._momx_board_chart_prewarm_cycle(now_et=weekday_at(10, minute))
        self.assertEqual(
            len(self.built), DashboardState.BOARD_PREWARM_FAILURE_STRIKES
        )
        self.assertEqual(self.state._board_prewarm_status["blocked"], ["GOOGLE"])

    def test_a_blacklisted_symbol_is_forgiven_after_the_cooldown(self) -> None:
        for _ in range(DashboardState.BOARD_PREWARM_FAILURE_STRIKES):
            self.state._board_prewarm_note_cold_attempt("GOOGLE")
        now = time.monotonic()
        blocked = self.state._board_prewarm_plan(["GOOGLE"], self.session_start(), now_mono=now)
        self.assertEqual(blocked["cold"], [])
        self.assertEqual(blocked["blocked"], ["GOOGLE"])
        later = now + DashboardState.BOARD_PREWARM_FAILURE_COOLDOWN_SECONDS + 1
        probation = self.state._board_prewarm_plan(
            ["GOOGLE"], self.session_start(), now_mono=later
        )
        self.assertEqual(probation["cold"], ["GOOGLE"])

    def test_a_symbol_that_starts_working_clears_its_strikes(self) -> None:
        self.state._board_prewarm_note_cold_attempt("SBET")
        self.cache_file("SBET", mtime=self.fresh())
        self.state._board_prewarm_plan(["SBET"], self.session_start())
        self.assertNotIn("SBET", self.state._board_prewarm_strikes)


class HealthReportsIt(_PrewarmStub):
    def test_the_status_payload_answers_is_it_doing_anything(self) -> None:
        board_file(self.boards, "Watchlist", ["CRCL"])
        self.cache_file("CRCL", mtime=self.fresh())
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
        payload = self.state._board_prewarm_status_payload()
        self.assertEqual(payload["state"], "running")
        self.assertEqual(payload["warm"], 1)
        self.assertEqual(payload["candidates"], 1)
        self.assertFalse(payload["paused"])
        self.assertIsNotNone(payload["boardAgeSeconds"])
        # Internal bookkeeping never leaks into the health response.
        self.assertNotIn("_announced", payload)
        self.assertNotIn("_emptyAnnounced", payload)

    def test_health_reports_the_BACKLOG_not_the_budget(self) -> None:
        """The payload is the only verification surface this feature has, and
        the defence of the stat()-only classifier is "the failure is visible in
        one curl". Reporting len(plan['tails']) - capped at 2 - made 'behind'
        unable to exceed 2, so eight stale symbols read as 'behind: 2' and the
        other six were unaccounted for anywhere. 'behind: 0' then means both
        "nothing is stale" and "everything is stale but paced"."""
        board_file(self.boards, "Watchlist", [f"SYM{i}" for i in range(8)])
        for i in range(8):
            self.cache_file(f"SYM{i}", mtime=self.fresh(ago=3 * 86400))
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
        payload = self.state._board_prewarm_status_payload()
        self.assertEqual(payload["candidates"], 8)
        self.assertEqual(payload["behind"], 8)  # observed, not dispatched
        self.assertEqual(payload["tailsPlanned"], DashboardState.BOARD_PREWARM_MAX_TAILS_PER_CYCLE)
        self.assertEqual(payload["tails"], DashboardState.BOARD_PREWARM_MAX_TAILS_PER_CYCLE)
        # The invariant that makes the numbers trustworthy at a glance.
        self.assertEqual(
            payload["warm"] + payload["behind"] + payload["cold"] + payload["skipped"],
            payload["candidates"],
        )

    def test_an_idle_cycle_does_not_leave_last_cycles_numbers_visible(self) -> None:
        """'closed' and 'paused' updated only state/tails/builds, so overnight
        health kept showing the last running cycle's candidates/warm/behind as
        if they were current."""
        board_file(self.boards, "Watchlist", ["QMCO"])
        self.cache_file("QMCO", mtime=self.fresh(ago=3 * 86400))
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
        self.assertEqual(self.state._board_prewarm_status["behind"], 1)
        with patch.object(api_server.time, "sleep"):
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock(2, 0))
        payload = self.state._board_prewarm_status_payload()
        self.assertEqual(payload["state"], "closed")
        self.assertEqual((payload["candidates"], payload["behind"], payload["warm"]), (0, 0, 0))

    def test_the_payload_cannot_500_health_while_the_ledger_churns(self) -> None:
        """It runs on an HTTP handler thread while the prewarm thread inserts
        into and trims the strikes ledger. A comprehension over the live dict
        raises RuntimeError, which _dispatch_get's blanket except turns into a
        500 on /api/health - exactly when you are curling it to find out why
        the prewarm is misbehaving."""

        class _ChurningDict(dict):
            def items(inner):
                raise RuntimeError("dictionary changed size during iteration")

        self.state._board_prewarm_strikes = _ChurningDict({"GOOGLE": [3, 0.0]})
        payload = self.state._board_prewarm_status_payload()  # must not raise
        self.assertIn("strikes", payload)
        self.assertIn("chartCacheSize", payload)

    def test_losing_the_board_schema_is_announced_once(self) -> None:
        """A renamed directory, suffix, rows array or symbol key returns []
        through a bare except, and health then shows 'running' with
        candidates 0 while boardAgeSeconds stays small - two observations that
        disagree, with nothing logged. The one condition meaning "I have lost
        my input entirely" was the only one that printed nothing."""
        (self.boards / "Watchlist.json").write_text(
            json.dumps({"generatedAt": datetime.now(timezone.utc).isoformat(), "roze": []}),
            encoding="utf-8",
        )
        with patch.object(api_server.time, "sleep"), patch(
            "builtins.print"
        ) as printed:
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock())
            self.state._momx_board_chart_prewarm_cycle(now_et=self.clock(10, 1))
        said = [c for c in printed.call_args_list if "yielded no symbols" in str(c)]
        self.assertEqual(len(said), 1, "edge-triggered: announce once, not every cycle")


def weekday_at(hour: int, minute: int) -> datetime:
    """A weekday at H:M that has ALREADY HAPPENED.

    It used to return TODAY at H:M, which made the whole suite depend on the
    time of day it was run. board_file() stamps its fixture with the REAL
    clock, and _board_prewarm_read_file rejects a board older than
    BOARD_PREWARM_BOARD_MAX_AGE_SECONDS - so with an injected clock of 10:00
    and a real clock of 08:05, the fixture looked 115 minutes stale, every
    board yielded no symbols, and eleven tests failed with "board files exist
    but yielded no symbols". They passed when written in the afternoon and
    failed every morning after: run AFTER the injected hour the age is
    negative (fresh), run BEFORE it the age is positive (stale).

    Stepping back until the probe is in the past makes a real-clock fixture
    unconditionally fresh, so the suite means the same thing at 08:00 as at
    16:00. Hour-of-day and weekday semantics - which the market-window gates
    actually test - are unchanged.
    """
    eastern = ZoneInfo(api_server.EASTERN_TZ)
    probe = datetime.now(eastern).replace(hour=hour, minute=minute, second=0, microsecond=0)
    while probe.weekday() >= 5 or probe >= datetime.now(eastern):
        probe -= timedelta(days=1)
    return probe


class NewMatchFastPath(_PrewarmStub):
    """2026-10-01: LASR entered the matches while a cold build was running, so
    the 60s cycle neither noticed it for up to a minute nor started anything
    until that 45-199s build ended. "I cannot wait for 2 mins." A new match
    now gets its ~2s candles-first paint within one tick - and ONLY the paint:
    the full build stays with the cycle, so it never queues ahead of his own
    chart opens on the single-worker deep pool."""

    def setUp(self) -> None:
        super().setUp()
        self.painted: list = []

        class Pool:
            def submit(pool_self, fn, *args):
                self.painted.append(args)

        self.state.oi_finder_chart_paint_pool = Pool()

    def _match_row(self, symbol, since):
        return {"symbol": symbol, "last": 1.0, "pctChange": 0.0,
                "scanPass": True, "matchedSince": since.isoformat()}

    def _new_match_board(self, *symbols, ago=60.0):
        since = datetime.fromtimestamp(self.clock().timestamp() - ago, timezone.utc)
        board_file(self.boards, "Watchlist", list(symbols),
                   rows=[self._match_row(symbol, since) for symbol in symbols])

    def test_a_new_cold_match_gets_its_paint_at_once_even_behind_a_running_build(self) -> None:
        self._new_match_board("LASR")
        self.state._chart_refresh_in_flight = lambda *a, **k: True  # another full build running
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 1)
        # Paint only: (symbol, full_history=False, release=False, skip_studies=True).
        self.assertEqual(self.painted, [("LASR", False, False, True)])
        self.assertEqual(self.built, [])
        self.assertEqual(self.state._board_prewarm_status["lastNewMatch"]["action"], "paint")

    def test_each_match_is_started_once(self) -> None:
        self._new_match_board("LASR")
        self.state._board_prewarm_new_match_pass(now_et=self.clock())
        self.state._board_prewarm_new_match_pass(now_et=self.clock())
        self.assertEqual(len(self.painted), 1)

    def test_a_behind_match_gets_the_fast_tail_splice(self) -> None:
        self._new_match_board("LASR")
        self.cache_file("LASR", mtime=self.fresh(ago=3 * 86400))
        self.state._board_prewarm_new_match_pass(now_et=self.clock())
        self.assertEqual(self.touched, ["LASR"])
        self.assertEqual(self.painted, [])

    def test_an_old_match_is_left_to_the_regular_cycle(self) -> None:
        self._new_match_board("OLDM", ago=2 * 3600)
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 0)
        self.assertEqual(self.painted, [])

    def test_a_burst_is_capped_per_tick(self) -> None:
        self._new_match_board("AAA", "BBB", "CCC", "DDD", "EEE")
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 3)
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 2)

    def test_both_switches_and_the_clock_gate_stop_it(self) -> None:
        self._new_match_board("LASR")
        (self.artifacts / "prewarm_cold_paused").write_text("", encoding="utf-8")
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 0)
        (self.artifacts / "prewarm_cold_paused").unlink()
        (self.artifacts / "prewarm_paused").write_text("", encoding="utf-8")
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 0)
        (self.artifacts / "prewarm_paused").unlink()
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock(hour=21)), 0)
        self.assertEqual(self.painted, [])

    def test_a_chart_he_already_has_open_is_left_alone(self) -> None:
        self._new_match_board("LASR")
        self.state._chart_symbol_is_interactive = lambda symbol, now=None: symbol == "LASR"
        self.assertEqual(self.state._board_prewarm_new_match_pass(now_et=self.clock()), 0)
        self.assertEqual(self.painted, [])

    def test_an_unchanged_board_is_not_reparsed_and_a_torn_read_is_retried(self) -> None:
        self._new_match_board("LASR")
        reads = []
        real = api_server.json.loads
        with patch.object(api_server.json, "loads", lambda text: reads.append(1) or real(text)):
            self.state._board_prewarm_new_matches(self.clock().timestamp())
            self.state._board_prewarm_new_matches(self.clock().timestamp())
        self.assertEqual(len(reads), 1)
        # A failed read is not remembered: the next tick tries again.
        self.state.__dict__.pop("_board_prewarm_new_match_files", None)
        with patch.object(api_server.json, "loads", side_effect=ValueError("torn")):
            self.assertEqual(self.state._board_prewarm_new_matches(self.clock().timestamp()), [])
        self.assertEqual([s for s, _ in self.state._board_prewarm_new_matches(self.clock().timestamp())], ["LASR"])

    def test_the_loop_checks_new_matches_every_tick_and_cycles_once_a_minute(self) -> None:
        calls = {"cycle": 0, "fast": 0, "sleeps": []}
        self.state.BOARD_PREWARM_BOOT_DELAY_SECONDS = 0.0
        self.state._momx_board_chart_prewarm_cycle = lambda: calls.__setitem__("cycle", calls["cycle"] + 1) or 60.0
        self.state._board_prewarm_new_match_pass = lambda: calls.__setitem__("fast", calls["fast"] + 1) or 0

        class Stop(Exception):
            pass

        def fake_sleep(seconds):
            calls["sleeps"].append(seconds)
            if len(calls["sleeps"]) > 4:  # the boot sleep + four ticks
                raise Stop()

        with patch.object(api_server.time, "sleep", fake_sleep), patch.dict(os.environ, {"AGX_CHART_PREWARM": "1"}):
            with self.assertRaises(Stop):
                self.state._momx_board_chart_prewarm_loop()
        self.assertEqual(calls["cycle"], 1)        # 60s cycle: once in ~20s of ticks
        self.assertEqual(calls["fast"], 4)         # new matches: every tick
        self.assertEqual(calls["sleeps"][1:], [5.0, 5.0, 5.0, 5.0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
