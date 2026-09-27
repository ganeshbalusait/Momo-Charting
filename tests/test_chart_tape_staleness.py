"""A chart tape that stopped printing must not report itself healthy.

EA stopped printing after 2026-08-04 (verified against the broker directly:
a 30-day one-minute request returned 6,251 rows ending 2026-08-04, and the
two-day recency request returned nothing at all, while AAPL was current in
the same pass). Neither outcome was an error anywhere in the pipeline:

  * the full build returned a non-empty tape, and the payload's error field
    was populated only when bars were EMPTY, so it stayed "";
  * the recency refresh returned an empty frame, which the caller's
    `if payload.get("bars")` guard dropped on the floor - the cached tape was
    left untouched and nothing was recorded.

So the chart drew nine-day-old candles as if they were live. These tests pin
the missing check: a tape is judged against the market, not against the fetch.
"""
from __future__ import annotations

import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from api_server import DashboardState, EASTERN_TZ

ET = ZoneInfo(EASTERN_TZ)


def _epoch(year: int, month: int, day: int, hour: int = 15, minute: int = 59) -> float:
    return datetime(year, month, day, hour, minute, tzinfo=ET).timestamp()


def _bars(*epochs: float) -> list[dict]:
    return [
        {"time": int(stamp), "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1}
        for stamp in epochs
    ]


class _ServeState(DashboardState):
    """Serve path only: bypass the real, expensive __init__."""

    def __init__(self, payload: dict, market_newest_epoch: float):
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_chart_cache = {
            "EA": {"cached_at": time.monotonic(), "payload": payload, "history_ready": True},
        }
        self.oi_finder_chart_refreshes: set[str] = set()
        self.oi_finder_interactive_until = 0.0
        # The serve path now falls back to the on-disk tape cache; point the
        # double at an empty temp dir so lookups miss cleanly.
        self.oi_finder_chart_disk_cache_dir = Path(tempfile.mkdtemp())
        self._chart_market_newest_bar_epoch = market_newest_epoch
        self.refresh_calls: list[tuple[str, bool]] = []

    def _start_oi_finder_chart_refresh(self, target: str, full_history: bool) -> None:
        self.refresh_calls.append((target, full_history))

    def _chart_payload_has_ready_ganesh_signals(self, payload) -> bool:
        return True

    def _chart_payload_has_multi_timeframe_depth(self, payload) -> bool:
        return True


def test_a_tape_that_missed_a_session_is_behind_the_market():
    # The exact EA case: last print 2026-08-04, market last print 2026-08-13.
    assert DashboardState._chart_tape_is_behind_market(
        _epoch(2026, 8, 4), _epoch(2026, 8, 13),
    ) is True


def test_a_tape_printing_in_the_same_session_is_current():
    # Same session, different minute - a thin name that stopped printing at
    # lunch is NOT stale, it simply had no trades.
    assert DashboardState._chart_tape_is_behind_market(
        _epoch(2026, 8, 13, 12, 5), _epoch(2026, 8, 13, 15, 59),
    ) is False


def test_a_market_wide_holiday_flags_nothing():
    # The reference is the newest bar seen for ANY symbol, not a calendar. On
    # a holiday nothing prints, so no symbol advances the mark and no symbol
    # is behind it. A clock-based rule would have flagged every ticker at once.
    holiday_reference = _epoch(2026, 8, 13)
    assert DashboardState._chart_tape_is_behind_market(
        holiday_reference, holiday_reference,
    ) is False


def test_a_cold_server_with_no_market_reference_flags_nothing():
    # Before anything has been built there is nothing to compare against.
    # Fail toward silence rather than crying wolf on every cold start.
    assert DashboardState._chart_tape_is_behind_market(_epoch(2026, 8, 4), 0.0) is False


def test_an_empty_tape_is_not_judged_here():
    # The empty-bars path already has its own error message.
    assert DashboardState._chart_tape_is_behind_market(0.0, _epoch(2026, 8, 13)) is False


def test_serving_a_stale_tape_reports_an_error_and_a_flag():
    # The core defect: this payload came back with error "" and
    # historyLoading false, so the chart painted 2026-08-04 candles as live.
    payload = {
        "symbol": "EA",
        "bars": _bars(_epoch(2026, 8, 4, 15, 58), _epoch(2026, 8, 4, 15, 59)),
        "historyLoading": False,
        "error": "",
    }
    state = _ServeState(payload, market_newest_epoch=_epoch(2026, 8, 13))

    served = state.oi_finder_chart_payload("EA")

    assert served["tapeStale"] is True, "a tape that missed a session must say so"
    assert served["tapeNewestSession"] == "2026-08-04"
    assert served["marketNewestSession"] == "2026-08-13"
    assert "2026-08-04" in served["error"], f"error must name the last print, got {served['error']!r}"
    assert "EA" in served["error"]


def test_serving_a_current_tape_stays_clean():
    payload = {
        "symbol": "AAPL",
        "bars": _bars(_epoch(2026, 8, 13, 15, 59)),
        "historyLoading": False,
        "error": "",
    }
    state = _ServeState(payload, market_newest_epoch=_epoch(2026, 8, 13))
    state.oi_finder_chart_cache["AAPL"] = state.oi_finder_chart_cache.pop("EA")
    state.oi_finder_chart_cache["AAPL"]["payload"] = payload

    served = state.oi_finder_chart_payload("AAPL")

    assert served["tapeStale"] is False
    assert served["error"] == ""


def test_a_stale_tape_does_not_overwrite_a_real_error():
    payload = {
        "symbol": "EA",
        "bars": _bars(_epoch(2026, 8, 4)),
        "historyLoading": False,
        "error": "Schwab/TOS is not connected.",
    }
    state = _ServeState(payload, market_newest_epoch=_epoch(2026, 8, 13))

    served = state.oi_finder_chart_payload("EA")

    assert served["tapeStale"] is True
    assert served["error"] == "Schwab/TOS is not connected."


def test_the_market_reference_tracks_the_newest_bar_seen_for_any_symbol():
    state = _ServeState({"bars": []}, market_newest_epoch=0.0)

    state._note_chart_market_tape(_bars(_epoch(2026, 8, 4)))
    assert state._chart_market_newest_bar_epoch == _epoch(2026, 8, 4)

    state._note_chart_market_tape(_bars(_epoch(2026, 8, 13)))
    assert state._chart_market_newest_bar_epoch == _epoch(2026, 8, 13)

    # A later symbol's older tape must not drag the high-water mark back.
    state._note_chart_market_tape(_bars(_epoch(2026, 8, 4)))
    assert state._chart_market_newest_bar_epoch == _epoch(2026, 8, 13)


def test_a_bar_stamped_in_the_future_cannot_poison_the_market_reference():
    # One bad tape timestamped next year would otherwise mark every healthy
    # symbol on the server as stale.
    state = _ServeState({"bars": []}, market_newest_epoch=0.0)
    state._note_chart_market_tape(_bars(time.time() + 30 * 86_400))
    assert state._chart_market_newest_bar_epoch == 0.0


def _open_chart_state(symbol: str):
    """A serve-path double whose cache already holds `symbol`, current tape."""
    payload = {
        "symbol": symbol,
        "bars": _bars(_epoch(2026, 8, 13, 15, 59)),
        "historyLoading": False,
        "error": "",
    }
    state = _ServeState(payload, market_newest_epoch=_epoch(2026, 8, 13))
    state.oi_finder_chart_cache[symbol] = state.oi_finder_chart_cache.pop("EA")
    state.oi_finder_chart_cache[symbol]["payload"] = payload
    return state


def test_a_delta_poll_of_an_open_chart_folds_into_the_hot_set():
    # A chart left OPEN across a backend restart keeps polling since=/delta
    # without re-sending initial=true. It must still register in the hot-set
    # recents so _hot_chart_refresher_loop keeps its tape current - otherwise
    # an off-hot-set tape sits frozen at its pre-restart snapshot all session
    # (SMCI, 2026-08-25). SMCI is in neither PREMARKET_SCAN_SYMBOLS nor
    # QUICK_STRIP_WARM_SYMBOLS, so it can only reach the hot set via recents.
    state = _open_chart_state("SMCI")
    assert "SMCI" not in state._hot_chart_symbols(), "precondition: not hot yet"

    # A delta poll: not a fresh open, carries a since= cursor.
    state.oi_finder_chart_payload(
        "SMCI", initial_paint=False, since_epoch=_epoch(2026, 8, 13, 15, 0),
    )

    assert "SMCI" in state._hot_chart_symbols(), (
        "an open chart's delta poll must fold into the hot-set refresher"
    )


def test_a_prefetch_does_not_claim_a_hot_set_slot():
    # Prefetches are speculative background fetches, not an open chart. They
    # must not evict a genuinely-watched ticker from the 10-slot recents.
    state = _open_chart_state("SMCI")

    state.oi_finder_chart_payload("SMCI", prefetch=True)

    recents = getattr(state, "oi_finder_recent_chart_symbols", None) or {}
    assert "SMCI" not in recents, "a prefetch must not fold into the hot set"


def test_an_initial_open_still_folds_into_the_hot_set():
    # The original behaviour must be preserved: a fresh open still registers.
    state = _open_chart_state("SMCI")

    state.oi_finder_chart_payload("SMCI", initial_paint=True)

    assert "SMCI" in state._hot_chart_symbols()
