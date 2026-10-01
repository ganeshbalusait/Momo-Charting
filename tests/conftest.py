"""Keep the test suite away from the live trading database.

`config.DATABASE_PATH` is a module constant with no environment override, and
`database/repository.py` does `from config import DATABASE_PATH`, so the value
is bound when THAT module is first imported. Any test that constructs
`DashboardState` therefore opens the real `database/trades.db` - 795MB of live
trading history that the running server holds open during market hours.

Two things go wrong when that happens:

* Contention. Collection fails outright with "database is locked" while the
  backend is serving, which is how this suite behaved on 2026-08-21.
* Data loss. This repository has a 2026-08-10 incident where the live
  trades.db was truncated by an out-of-band operation. A test process that can
  reach the file at all is one bug away from repeating it.

Rebinding here is early enough because pytest imports conftest before any test
module, so `repository.py` picks up the scratch path at its own import time.
Each session gets a fresh empty file; `initialize()` builds the schema from
scratch, which is what the suite expects.
"""

from __future__ import annotations

import pathlib
import tempfile

import pytest

import config

_SCRATCH_DIR = pathlib.Path(tempfile.mkdtemp(prefix="agx-test-db-"))
config.DATABASE_PATH = _SCRATCH_DIR / "trades.db"

# Same hazard, second instance (2026-08-31, found within HOURS of the feature
# shipping): momx/service.py:_build_once records scan matches to the REAL
# artifacts/momx_history/ archive, and test_momx_service.py drives _build_once
# with fixture boards - so every test run planted JD/BILI/BUD/DHI/TQQQ into
# the archive the trader reads as "what the scan matched today". Fabricated
# history on a trading surface is worse than a crash: it looks like evidence.
# Rebind the accessor for the whole suite so no test, present or future, can
# reach the live archive. service.history_dir is THE single path accessor by
# design (writer and reader share it), which is what makes one rebind enough.
# An attribute rebind was the first attempt and it FAILED silently:
# test_momx_service.py calls importlib.reload(service), which rebuilds the
# module globals and restores the real accessor mid-suite. The env var is
# read inside history_dir() on every call, so it survives any reload.
import os as _os

_os.environ["AGX_MOMX_HISTORY_DIR"] = str(_SCRATCH_DIR / "momx_history")
# Third instance, same shape: _build_once also feeds momx/grade_log.py, whose
# tape and events are the scanner grade's trade record. Fixture boards must
# never land in the real artifacts/momx_grade_tape or momx_grade_events.
_os.environ["AGX_MOMX_GRADE_DIR"] = str(_SCRATCH_DIR / "momx_grade")
# Fourth: the AI news reader (momx/news_catalyst.py) runs off the same build
# and would spend REAL news + AI calls on fixture boards. Off for the suite.
_os.environ["AGX_CATALYST_READER"] = "0"
_os.environ["AGX_SECTOR_ROTATION"] = "0"  # same reason: no real daily-bar fetches
# Options-only gate (momx/optionable.py): fixture tickers (AAA, NVDA stubs) have no
# option chain in the scratch store; the gate has its own tests with it switched on.
_os.environ["AGX_MOMX_OPTIONS_ONLY"] = "0"
_os.environ["AGX_MOMX_OPTION_TRACK"] = "0"  # no real chain calls from fixture boards
_os.environ["AGX_MOMX_ETF_REFRESH"] = "0"  # no listing-directory download in tests
# The TOS/Alpaca toolbar choice (momx/feed.py set_data_source): no test may read
# the trader's real artifacts/momx_data_source.json (the suite would behave
# differently once he unticks TOS) or write it (it would flip the live scanner).
_os.environ["AGX_MOMX_DATA_SOURCE_PATH"] = str(_SCRATCH_DIR / "momx_data_source.json")


# The MomX volume correction reads Schwab. Feed tests drive the tape with a
# fake Alpaca transport AND a historical ``now``, and Schwab will serve real
# bars for a historical window -- which overwrote fixture volumes and failed
# three tests for reasons unrelated to their subject. No test may depend on
# the live market, so the correction is off for the whole suite; its own
# behaviour is tested directly against the pure functions.
try:
    from momx import feed as _momx_feed

    _momx_feed.SCHWAB_VOLUME_ENABLED = False
    # The background TOS refresher thread must never run under tests.
    _momx_feed.TOS_REFRESHER_ENABLED = False
    # ...nor poll the real api_server running on this machine.
    _momx_feed.STREAM_LIVE_ENABLED = False
except Exception:  # pragma: no cover - momx optional in some test runs
    pass


@pytest.fixture(autouse=True)
def _reset_schwab_gate():
    """Give every test a clean Schwab gate.

    data.schwab_rate_limit.GATE is a process-wide singleton by design, so
    without this one test arming a cooldown (or filling the rate window) makes
    every later Schwab call raise SchwabUnavailable. That surfaced as four
    unrelated tests failing in a full run while passing in isolation.
    """
    from data.schwab_rate_limit import GATE
    GATE.reset()
    # Same shape: momx/feed.py's TOS budget is a process-wide 60s window, so
    # one test spending it would leave later TOS-mode tests "queued".
    try:
        from momx import feed as _feed
        _feed._tos_reset()
    except Exception:  # pragma: no cover - momx optional in some test runs
        _feed = None
    yield
    GATE.reset()
    if _feed is not None:
        _feed._tos_reset()
