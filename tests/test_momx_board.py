"""Tests for the MomoX board orchestrator.

NOTHING HERE TOUCHES THE NETWORK. An autouse fixture replaces
``momx.board.feed`` with a stub whose fetchers raise, so any test that forgets
to inject its own feed produces an empty board with errors rather than a live
Alpaca call. Tests that want bars pass ``feed_module=`` explicitly.

NOTHING HERE TOUCHES THE REAL UNIVERSE FILE either: every persistence test
uses ``tmp_path``, and an autouse fixture points ``MOMX_UNIVERSE_PATH`` at a
scratch file so an accidental default-path write cannot clobber the trader's
pasted 400-symbol list.
"""

from __future__ import annotations

import importlib
import sys
import threading
from collections import namedtuple
from datetime import datetime, timedelta, timezone

import pytest

from momx import board, flags, news


StubResult = namedtuple("StubResult", "bars errors")


def test_production_tapes_overlap_and_preserve_each_feed_error(monkeypatch):
    from types import SimpleNamespace
    barrier = threading.Barrier(3)
    completed = []
    def fetcher(kind):
        def call(symbols, **kwargs):
            barrier.wait(timeout=5)
            completed.append(kind)
            return StubResult({}, {"AAA": kind + " unavailable"})
        return call
    monkeypatch.setattr(board, "feed", SimpleNamespace(
        fetch_5m=fetcher("five"), fetch_30m=fetcher("thirty"),
        fetch_daily=fetcher("daily")))
    result = board.build_board(["AAA"])
    assert set(completed) == {"five", "thirty", "daily"}
    for kind in completed:
        assert kind + " unavailable" in result["errors"]["AAA"]

# 2026-08-03 is a Monday. 14:30 UTC = 10:30 ET, safely inside the session on
# both sides of a DST boundary, so every bar's Eastern date is its UTC date.
SESSION_ANCHOR = int(datetime(2026, 8, 3, 14, 30, tzinfo=timezone.utc).timestamp())


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------

# ---------------------------------------------------------------------------
# build_board resolves industries via momx.industry_lookup, which is allowed to
# call Finnhub/FMP in production. Discovered 2026-08-28: these tests' FAKE
# symbols (S00..S59, AAA, ZZZZ) were being looked up against the LIVE providers
# and written into artifacts/momx_industry_cache.json - network in unit tests,
# junk in the production cache, and a 25s test run. Every test here gets an
# isolated cache and a dead fetcher.
# ---------------------------------------------------------------------------
import pytest as _pytest

from momx import industry_lookup as _industry_lookup


@_pytest.fixture(autouse=True)
def _no_industry_network(monkeypatch, tmp_path):
    monkeypatch.setenv(_industry_lookup.CACHE_PATH_ENV, str(tmp_path / "industry_cache.json"))
    monkeypatch.setattr(_industry_lookup, "fetch_industry", lambda *a, **k: None)
    _industry_lookup.clear_memory_cache()
    yield
    _industry_lookup.clear_memory_cache()


def _daily_bars(closes, start: int = SESSION_ANCHOR) -> list[dict]:
    """One bar per calendar day, stamped the way ``momx.feed`` stamps them."""
    return [
        {
            "timestamp": start + index * 86_400,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000 + index,
        }
        for index, close in enumerate(closes)
    ]


def _intraday_bars(count: int, *, minutes: int, start: int = SESSION_ANCHOR) -> list[dict]:
    step = minutes * 60
    return [
        {
            "timestamp": start + index * step,
            "open": 100.0 + index * 0.1,
            "high": 100.5 + index * 0.1,
            "low": 99.5 + index * 0.1,
            "close": 100.0 + index * 0.1,
            "volume": 10_000 + index * 25,
        }
        for index in range(count)
    ]


def _pct_daily(pct: float) -> list[dict]:
    """A two-bar daily tape whose last close is ``pct`` percent off the prior."""
    return _daily_bars([100.0, 100.0 * (1.0 + pct / 100.0)])


class StubFeed:
    """A ``momx.feed`` stand-in: three fetchers, a call log, no network."""

    def __init__(self, five=None, thirty=None, daily=None, errors=None):
        self.five = five or {}
        self.thirty = thirty or {}
        self.daily = daily or {}
        self.errors = errors or {}
        self.calls: list[tuple[str, tuple[str, ...]]] = []

    def _result(self, kind: str, bars, symbols):
        self.calls.append((kind, tuple(symbols)))
        return StubResult(
            {symbol: bars[symbol] for symbol in symbols if symbol in bars},
            dict(self.errors.get(kind) or {}),
        )

    def fetch_5m(self, symbols, **kwargs):
        return self._result("5m", self.five, symbols)

    def fetch_30m(self, symbols, **kwargs):
        return self._result("30m", self.thirty, symbols)

    def fetch_daily(self, symbols, **kwargs):
        return self._result("daily", self.daily, symbols)


class ExplodingFeed:
    """The default during tests: reaching the network is a test failure."""

    def fetch_5m(self, symbols, **kwargs):
        raise AssertionError("live feed touched: pass feed_module= in the test")

    fetch_30m = fetch_5m
    fetch_daily = fetch_5m


@pytest.fixture(autouse=True)
def _no_live_feed(monkeypatch):
    monkeypatch.setattr(board, "feed", ExplodingFeed())


@pytest.fixture(autouse=True)
def _scratch_universe_path(monkeypatch, tmp_path):
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(tmp_path / "scratch_universe.json"))


@pytest.fixture(autouse=True)
def _empty_board_cache():
    board.clear_board_cache()
    yield
    board.clear_board_cache()


@pytest.fixture(autouse=True)
def _offline_earnings(monkeypatch):
    """The badge's earnings half must never reach a vendor from a test."""
    monkeypatch.setenv("MOMX_FLAGS_EARNINGS_NETWORK", "0")
    flags.reset_caches()
    yield
    flags.reset_caches()


@pytest.fixture(autouse=True)
def _offline_news(monkeypatch):
    """The news icon must never reach Alpaca from a test."""
    monkeypatch.setenv(news.NETWORK_ENV, "0")
    news.reset_caches()
    yield
    news.reset_caches()


# ----------------------------------------------------------------------
# payload shape
# ----------------------------------------------------------------------

PAYLOAD_KEYS = {"generatedAt",
    "tapeAsOf", "universe", "universeCount", "rows",
    "rest", "errors",
    # BEAR scanner (spec 2026-09-24): every payload says which way it faces.
    "direction"}
ROW_KEYS = {
    "symbol", "industry", "last", "pctChange", "prevClose", "scanPass", "scanReasons",
    "rvol", "sqz", "skittles", "highLow", "color", "quoteTrend", "sparkline",
    "badge", "news",
    # 30m / 1h squeezes for MX A+ (2026-09-28).
    "sqzFast",
    # The ticker card's "1-hr high / 1-hr low" (2026-09-02). This set IS the
    # wire contract: adding a field to _contract_row without adding it here is
    # meant to fail, and removing one is meant to fail louder - the same edit
    # that introduced this field also silently dropped "badge", which these
    # tests caught within the hour.
    "hourHighLow",
    # Scanner grade inputs + result (spec 2026-09-21, Task 5): m5 (momentum
    # summary) and sqzRaw (raw squeeze states) come from build_row; grade is
    # filled in build_board after news is attached.
    "m5", "sqzRaw", "grade",
    # Trend strength: ADX / +DI / -DI per timeframe (2026-09-22). A RECORDED
    # FACT - nothing in momx/grade.py reads it, and the letter is unchanged.
    "adx",
    # BEAR scanner (spec 2026-09-24): "direction" names the board the row is
    # served on; "bear" carries the bear verdicts for the same columns until
    # board.bear_view promotes them and service strips it before publish.
    "direction", "bear",
}


def test_payload_key_set_matches_the_contract_exactly():
    stub = StubFeed(
        five={"NVDA": _intraday_bars(120, minutes=5)},
        thirty={"NVDA": _intraday_bars(120, minutes=30)},
        daily={"NVDA": _daily_bars([100.0 + index for index in range(60)])},
    )
    payload = board.build_board(["NVDA"], feed_module=stub)

    assert set(payload) == PAYLOAD_KEYS
    assert payload["universe"] == ["NVDA"]
    assert payload["universeCount"] == 1
    assert isinstance(payload["errors"], dict)

    (row,) = payload["rows"]
    assert set(row) == ROW_KEYS
    assert row["symbol"] == "NVDA"
    assert row["industry"] == "Semis"          # from momx.industries
    assert isinstance(row["scanPass"], bool)
    assert isinstance(row["scanReasons"], list)
    assert set(row["rvol"]) == {"5m", "15m", "30m", "1h", "2h", "4h", "D"}
    assert set(row["sqz"]) == {"2h", "4h", "D", "Wk"}
    assert set(row["skittles"]) == {
        "2h", "4h", "D", "2D", "3D", "4D", "Wk", "M",
    }
    # highLow also carries its 8-bar hh/ll so the panel can re-run the formula
    # on the live price between rebuilds (2026-09-30).
    assert set(row["highLow"]) <= {"value", "bg", "fg", "hh", "ll"}
    assert {"value", "bg", "fg"} <= set(row["highLow"])
    assert set(row["color"]) == {"value", "bg", "fg"}
    # quoteTrend cells also carry "price" (drives the MomoX-style bar height).
    for cell in row["quoteTrend"]:
        assert set(cell) == {"value", "bg", "fg", "price"}


def test_generated_at_is_iso8601_and_honours_an_injected_now():
    moment = datetime(2026, 8, 26, 14, 30, tzinfo=timezone.utc)
    payload = board.build_board(["NVDA"], feed_module=StubFeed(), now=moment)
    assert payload["generatedAt"] == moment.isoformat()
    # and it parses back, which a hand-built string would not be guaranteed to
    assert datetime.fromisoformat(payload["generatedAt"]) == moment


def test_a_populated_tape_produces_real_cells_not_just_nulls():
    stub = StubFeed(
        five={"NVDA": _intraday_bars(400, minutes=5)},
        thirty={"NVDA": _intraday_bars(400, minutes=30)},
        daily={"NVDA": _daily_bars([100.0 + index * 0.5 for index in range(120)])},
    )
    (row,) = board.build_board(["NVDA"], feed_module=stub)["rows"]

    assert row["last"] is not None
    assert row["pctChange"] is not None
    assert row["rvol"]["5m"]["value"] is not None
    assert row["skittles"]["D"]["value"] is not None
    assert row["sparkline"]


def test_empty_universe_returns_an_empty_board_without_fetching():
    stub = StubFeed()
    payload = board.build_board([], feed_module=stub)
    assert payload == {
        "generatedAt": payload["generatedAt"],
        "direction": "bull",
        "tapeAsOf": None,
        "universe": [],
        "universeCount": 0,
        "rows": [],
        "rest": [],
        "errors": {},
    }
    assert stub.calls == []


# ----------------------------------------------------------------------
# one symbol may never break the board
# ----------------------------------------------------------------------

def test_a_symbol_whose_column_build_raises_lands_in_errors_and_the_rest_return(
    monkeypatch,
):
    real_build_row = board.columns.build_row

    def exploding_build_row(symbol, tapes, industry=None):
        if symbol == "BAD":
            raise RuntimeError("column build blew up")
        return real_build_row(symbol, tapes, industry)

    monkeypatch.setattr(board.columns, "build_row", exploding_build_row)

    stub = StubFeed(
        daily={
            "AAPL": _pct_daily(2.0),
            "BAD": _pct_daily(9.0),
            "MSFT": _pct_daily(1.0),
        }
    )
    payload = board.build_board(["AAPL", "BAD", "MSFT"], feed_module=stub)

    assert [row["symbol"] for row in payload["rows"]] == ["AAPL", "MSFT"]
    assert "BAD" in payload["errors"]
    assert "RuntimeError" in payload["errors"]["BAD"]
    assert "column build blew up" in payload["errors"]["BAD"]
    # the board still reports the full requested universe
    assert payload["universe"] == ["AAPL", "BAD", "MSFT"]
    assert payload["universeCount"] == 3


def test_a_symbol_whose_scan_raises_also_lands_in_errors(monkeypatch):
    def exploding_scan(tapes, last):
        raise ValueError("scan blew up")

    monkeypatch.setattr(board.scan, "scan_symbol", exploding_scan)
    stub = StubFeed(daily={"AAPL": _pct_daily(2.0)})
    payload = board.build_board(["AAPL"], feed_module=stub)

    assert payload["rows"] == []
    assert "ValueError" in payload["errors"]["AAPL"]


def test_a_whole_tape_fetch_raising_does_not_abort_the_board():
    class HalfDeadFeed(StubFeed):
        def fetch_5m(self, symbols, **kwargs):
            raise TimeoutError("alpaca timed out")

    stub = HalfDeadFeed(daily={"AAPL": _pct_daily(3.0)})
    payload = board.build_board(["AAPL"], feed_module=stub)

    assert [row["symbol"] for row in payload["rows"]] == ["AAPL"]
    assert payload["rows"][0]["pctChange"] == pytest.approx(3.0)
    assert "TimeoutError" in payload["errors"]["AAPL"]


def test_feed_reported_errors_are_surfaced_per_symbol_and_still_yield_a_row():
    stub = StubFeed(
        daily={"AAPL": _pct_daily(1.0)},
        errors={"5m": {"AAPL": "no bars returned"}},
    )
    payload = board.build_board(["AAPL"], feed_module=stub)

    assert payload["errors"]["AAPL"] == "5m: no bars returned"
    assert [row["symbol"] for row in payload["rows"]] == ["AAPL"]


def test_multiple_tape_failures_accumulate_into_one_message():
    stub = StubFeed(
        errors={
            "5m": {"AAPL": "no bars returned"},
            "daily": {"AAPL": "no usable Alpaca credentials"},
        }
    )
    payload = board.build_board(["AAPL"], feed_module=stub)
    assert payload["errors"]["AAPL"] == (
        "5m: no bars returned; daily: no usable Alpaca credentials"
    )


# ----------------------------------------------------------------------
# the news icon (best-effort, attached after pass 2)
# ----------------------------------------------------------------------

def test_a_fresh_headline_attaches_to_its_row_and_the_rest_get_none(monkeypatch):
    entry = {
        "headline": "AAPL ships something",
        "at": "2026-08-28T14:00:00+00:00",
        "source": "benzinga",
        "url": "https://example.com/aapl",
    }
    seen: list[tuple[str, ...]] = []

    def stub_fresh_news(symbols, **kwargs):
        seen.append(tuple(symbols))
        return {"AAPL": entry}

    monkeypatch.setattr(board.news, "fresh_news", stub_fresh_news)
    stub = StubFeed(daily={"AAPL": _pct_daily(2.0), "MSFT": _pct_daily(1.0)})
    payload = board.build_board(["AAPL", "MSFT"], feed_module=stub)

    by_symbol = {row["symbol"]: row for row in payload["rows"]}
    assert by_symbol["AAPL"]["news"] == entry
    assert by_symbol["MSFT"]["news"] is None
    assert seen == [("AAPL", "MSFT")]


def test_news_is_fetched_once_for_rows_and_rest_together(monkeypatch):
    """One fetch covering the whole shipped list, rows first then rest, so
    the rest rows (visible with "Scan matches only" off) get icons too and
    the cache key is the stable universe rather than the shifting top 50."""
    seen: list[tuple[str, ...]] = []

    def stub_fresh_news(symbols, **kwargs):
        seen.append(tuple(symbols))
        return {"CCC": {"headline": "CCC beats", "at": "2026-09-02T05:00:00+00:00"}}

    monkeypatch.setattr(board.news, "fresh_news", stub_fresh_news)
    stub = StubFeed(
        daily={
            "AAA": _pct_daily(5.0),
            "BBB": _pct_daily(-2.0),
            "CCC": _pct_daily(1.0),
            "DDD": _pct_daily(10.0),
        }
    )
    payload = board.build_board(["AAA", "BBB", "CCC", "DDD"], feed_module=stub, limit=2)

    assert [row["symbol"] for row in payload["rows"]] == ["DDD", "AAA"]
    assert seen == [("DDD", "AAA", "CCC", "BBB")]
    rest = {row["symbol"]: row for row in payload["rest"]}
    assert rest["CCC"]["news"]["headline"] == "CCC beats"
    assert rest["BBB"]["news"] is None


def test_a_news_blowup_ships_the_board_with_no_icons_and_no_errors(monkeypatch):
    def exploding_fresh_news(symbols, **kwargs):
        raise RuntimeError("news endpoint is down")

    monkeypatch.setattr(board.news, "fresh_news", exploding_fresh_news)
    stub = StubFeed(daily={"AAPL": _pct_daily(2.0)})
    payload = board.build_board(["AAPL"], feed_module=stub)

    (row,) = payload["rows"]
    assert row["news"] is None
    assert payload["errors"] == {}


# ----------------------------------------------------------------------
# ranking
# ----------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Reusing display columns for rows that do not need rebuilding
#
# Measured 2026-09-03: build_row is 53% of the per-symbol build cost, and it
# was being paid for ~290 non-matching symbols every cycle, which is why the
# 358-name Watchlist took 297s and refreshed every ~7 minutes.
#
# The columns cannot simply be DROPPED for non-matches - they were until
# 2026-09-02 and he asked why the other 307 rows were blank - so they are
# reused from the previous build instead.
# ---------------------------------------------------------------------------


def _two_symbol_feed():
    return StubFeed(
        five={"AAA": _intraday_bars(60, minutes=5), "BBB": _intraday_bars(60, minutes=5)},
        thirty={"AAA": _intraday_bars(60, minutes=30), "BBB": _intraday_bars(60, minutes=30)},
        daily={"AAA": _pct_daily(5.0), "BBB": _pct_daily(1.0)},
    )


def test_by_default_every_row_is_still_built():
    """full_rows_for=None is the old behaviour, for every existing caller."""
    payload = board.build_board(["AAA", "BBB"], feed_module=_two_symbol_feed())
    for row in payload["rows"]:
        assert row["rvol"]["5m"]["value"] is not None or row["rvol"]["5m"] == {}, row["symbol"]
    assert {row["symbol"] for row in payload["rows"]} == {"AAA", "BBB"}


def test_a_skipped_non_matching_row_reuses_its_previous_columns():
    marker = {"symbol": "BBB", "pctChange": -99.0, "rvol": {"5m": {"value": 42.0}},
              "sqz": {}, "skittles": {}, "highLow": {}, "color": {}, "quoteTrend": [],
              "sparkline": [], "hourHighLow": {}, "industry": "", "last": 1.0}
    payload = board.build_board(
        ["AAA", "BBB"], feed_module=_two_symbol_feed(),
        full_rows_for=["AAA"], reuse_rows={"BBB": marker},
    )
    rows = {row["symbol"]: row for row in payload["rows"]}
    assert rows["BBB"]["rvol"]["5m"]["value"] == 42.0     # the reused columns
    assert rows["AAA"]["rvol"]["5m"]["value"] != 42.0     # rebuilt this cycle


def test_a_reused_row_keeps_this_cycles_price_and_percent_change():
    """Reuse the STUDIES, never the fast-moving numbers.

    _finished() builds the payload row from record["_row"], so without this a
    reused row would DISPLAY the previous build's pctChange while rank_board
    ordered it on this cycle's - one number shown, another sorted by, which is
    the same defect the 2026-09-03 audit found twice elsewhere.
    """
    stale = {"symbol": "BBB", "pctChange": -99.0, "last": 1.0,
             "rvol": {"5m": {"value": 42.0}}, "sqz": {}, "skittles": {}, "highLow": {},
             "color": {}, "quoteTrend": [], "sparkline": [], "hourHighLow": {}, "industry": ""}
    payload = board.build_board(
        ["AAA", "BBB"], feed_module=_two_symbol_feed(),
        full_rows_for=["AAA"], reuse_rows={"BBB": stale},
    )
    row = {r["symbol"]: r for r in payload["rows"]}["BBB"]
    assert row["rvol"]["5m"]["value"] == 42.0        # studies reused
    assert row["pctChange"] == pytest.approx(1.0)    # BBB's daily tape is +1%
    assert row["pctChange"] != -99.0
    assert row["last"] != 1.0                        # this cycle's price


def test_a_NEWLY_matching_symbol_never_shows_last_cycles_columns(monkeypatch):
    """The failure this design could have had.

    The skip list is the PREVIOUS cycle's matches, so a symbol that starts
    matching NOW is exactly the one that was skipped. It must be built anyway.
    """
    stale = {"symbol": "BBB", "pctChange": -99.0, "rvol": {"5m": {"value": 42.0}},
             "sqz": {}, "skittles": {}, "highLow": {}, "color": {}, "quoteTrend": [],
             "sparkline": [], "hourHighLow": {}, "industry": "", "last": 1.0}

    def only_bbb_matches(tapes, last=None, **kwargs):
        return (True, ["rvol:5m"])

    monkeypatch.setattr(board.scan, "scan_symbol", only_bbb_matches)
    payload = board.build_board(
        ["AAA", "BBB"], feed_module=_two_symbol_feed(),
        full_rows_for=["AAA"], reuse_rows={"BBB": stale},
    )
    rows = {row["symbol"]: row for row in payload["rows"]}
    assert rows["BBB"]["scanPass"] is True
    # Freshly built, NOT the 42.0 marker.
    assert rows["BBB"]["rvol"]["5m"]["value"] != 42.0
    assert rows["BBB"]["pctChange"] != -99.0


def test_a_skipped_symbol_with_nothing_cached_is_dropped_not_crashed():
    payload = board.build_board(
        ["AAA", "BBB"], feed_module=_two_symbol_feed(),
        full_rows_for=["AAA"], reuse_rows=None,
    )
    assert {row["symbol"] for row in payload["rows"]} == {"AAA"}
    assert payload["universeCount"] == 2      # the scan still counted it


def test_a_skipped_row_still_ranks_on_todays_percent_change():
    """The %Chg of a skipped row must not fall back to the daily-only call.

    That bare call is what printed YESTERDAY's change premarket (abc9ced).
    Reintroducing it for the ~290 skipped symbols would have undone that fix
    for exactly the rows this change touches.
    """
    day = 86_400
    tomorrow = SESSION_ANCHOR + 2 * day
    def premarket(price):
        bars = _intraday_bars(60, minutes=5, start=tomorrow)
        for bar in bars:
            bar["open"] = bar["high"] = bar["low"] = bar["close"] = price
        return bars

    stub = StubFeed(
        five={"AAA": premarket(110.0), "BBB": premarket(104.0)},
        thirty={"AAA": premarket(110.0), "BBB": premarket(104.0)},
        daily={"AAA": _daily_bars([100.0, 100.0]), "BBB": _daily_bars([100.0, 100.0])},
    )
    cached = {"symbol": "BBB", "pctChange": -99.0, "rvol": {}, "sqz": {}, "skittles": {},
              "highLow": {}, "color": {}, "quoteTrend": [], "sparkline": [],
              "hourHighLow": {}, "industry": "", "last": 1.0}
    payload = board.build_board(
        ["AAA", "BBB"], feed_module=stub,
        full_rows_for=["AAA"], reuse_rows={"BBB": cached},
    )
    order = [row["symbol"] for row in payload["rows"]]
    # AAA is +10% today, BBB +4%. Both closed flat yesterday, so a daily-only
    # reading would have made them indistinguishable at 0.0%.
    assert order == ["AAA", "BBB"], order


def test_premarket_board_is_ranked_by_todays_move_not_yesterdays():
    """The ranking and the displayed number must be the SAME number.

    _scan_one used to compute %Chg twice - once bare for the ranking record,
    once inside build_row for display. When pct_change learned to handle
    premarket (abc9ced: live price vs yesterday's close while today's daily
    bar does not exist yet), only the build_row call was given what it needed.
    Before the open the board therefore PRINTED today's change on every row
    while being ORDERED by yesterday's.

    It matters beyond the ordering: rank_board decides which symbols go in
    `rows` versus `rest`, and the frontend only leases live quotes for `rows`.

    Setup below is deliberately one where the two orders DISAGREE:

        SLEEPER  closed -6% yesterday, trading +4% premarket   <- should lead
        FADER    closed +8% yesterday, trading +1% premarket
    """
    day = 86_400
    # Daily tapes that END YESTERDAY - today's bar has not formed. The last
    # close is what premarket must measure against.
    sleeper_daily = _daily_bars([100.0, 94.0])          # -6.0% yesterday
    fader_daily = _daily_bars([100.0, 108.0])           # +8.0% yesterday
    # Intraday bars stamped a DAY LATER than the newest daily bar: that is how
    # pct_change detects "today's daily bar is missing".
    # +2 days: _daily_bars puts its LAST bar at SESSION_ANCHOR + 1 day, so the
    # intraday tape has to sit a day beyond THAT to look like an unformed session.
    tomorrow = SESSION_ANCHOR + 2 * day
    def premarket(last_price):
        bars = _intraday_bars(60, minutes=5, start=tomorrow)
        for bar in bars:
            bar["close"] = last_price
            bar["open"] = last_price
            bar["high"] = last_price
            bar["low"] = last_price
        return bars

    stub = StubFeed(
        five={"SLEEPER": premarket(97.76), "FADER": premarket(109.08)},
        thirty={"SLEEPER": premarket(97.76), "FADER": premarket(109.08)},
        daily={"SLEEPER": sleeper_daily, "FADER": fader_daily},
    )
    payload = board.build_board(["FADER", "SLEEPER"], feed_module=stub)
    by_symbol = {row["symbol"]: row for row in payload["rows"]}
    assert set(by_symbol) == {"SLEEPER", "FADER"}

    # SLEEPER: 97.76 against yesterday's 94.00 close = +4.0%
    assert by_symbol["SLEEPER"]["pctChange"] == pytest.approx(4.0, abs=0.05)
    # FADER: 109.08 against yesterday's 108.00 close = +1.0%
    assert by_symbol["FADER"]["pctChange"] == pytest.approx(1.0, abs=0.05)

    # THE POINT: today's mover leads. Ranked on yesterday's numbers FADER
    # (+8%) would have led SLEEPER (-6%) - the exact inversion.
    order = [row["symbol"] for row in payload["rows"]]
    assert order == ["SLEEPER", "FADER"], order


def test_rows_are_capped_at_limit_and_sorted_by_pct_change_descending():
    stub = StubFeed(
        daily={
            "AAA": _pct_daily(5.0),
            "BBB": _pct_daily(-2.0),
            "CCC": _pct_daily(1.0),
            "DDD": _pct_daily(10.0),
        }
    )
    payload = board.build_board(["AAA", "BBB", "CCC", "DDD"], feed_module=stub, limit=3)

    assert [row["symbol"] for row in payload["rows"]] == ["DDD", "AAA", "CCC"]
    changes = [row["pctChange"] for row in payload["rows"]]
    assert changes == sorted(changes, reverse=True)
    # the universe is NOT truncated by the display limit
    assert payload["universeCount"] == 4


def test_a_symbol_with_no_pct_change_sorts_last_rather_than_vanishing():
    stub = StubFeed(daily={"AAA": _pct_daily(-4.0), "CCC": _pct_daily(1.0)})
    payload = board.build_board(["AAA", "BBB", "CCC"], feed_module=stub, limit=None)

    assert [row["symbol"] for row in payload["rows"]] == ["CCC", "AAA", "BBB"]
    assert payload["rows"][-1]["pctChange"] is None


def test_limit_none_returns_every_row():
    daily = {f"S{index:02d}": _pct_daily(float(index)) for index in range(60)}
    payload = board.build_board(
        sorted(daily), feed_module=StubFeed(daily=daily), limit=None
    )
    assert len(payload["rows"]) == 60


def test_the_default_limit_is_the_scanners_show_50():
    daily = {f"S{index:02d}": _pct_daily(float(index)) for index in range(60)}
    payload = board.build_board(sorted(daily), feed_module=StubFeed(daily=daily))
    assert board.DEFAULT_LIMIT == 50
    assert len(payload["rows"]) == 50


# ----------------------------------------------------------------------
# tape folding
# ----------------------------------------------------------------------

def test_build_tapes_emits_every_contract_timeframe_key():
    tapes = board.build_tapes(
        _intraday_bars(200, minutes=5),
        _intraday_bars(200, minutes=30),
        _daily_bars([100.0 + index for index in range(80)]),
    )
    assert set(tapes) == set(board.TIMEFRAME_KEYS)
    # coarser folds are strictly shorter than the tape they came from
    assert len(tapes["5m"]) > len(tapes["15m"]) > len(tapes["30m"])
    assert len(tapes["1h"]) > len(tapes["2h"]) > len(tapes["4h"])
    assert len(tapes["D"]) > len(tapes["2D"]) > len(tapes["Wk"]) > len(tapes["M"])


def test_intraday_folds_sum_volume_and_keep_first_open_last_close():
    fine = _intraday_bars(6, minutes=5)
    tapes = board.build_tapes(fine, [], [])
    fifteen = tapes["15m"]
    assert len(fifteen) == 2
    assert fifteen[0]["open"] == fine[0]["open"]
    assert fifteen[0]["close"] == fine[2]["close"]
    assert fifteen[0]["volume"] == sum(bar["volume"] for bar in fine[:3])


def test_frame_to_bars_translates_the_feeds_timestamp_column_to_time():
    stamp = datetime(2026, 8, 26, 14, 30, tzinfo=timezone.utc)
    bars = board.frame_to_bars(
        [{"timestamp": stamp, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5,
          "volume": 10.0}]
    )
    assert bars[0]["time"] == int(stamp.timestamp())


def test_frame_to_bars_drops_unusable_rows_instead_of_raising():
    assert board.frame_to_bars(None) == []
    assert board.frame_to_bars([{"open": 1.0}]) == []
    assert board.frame_to_bars([{"timestamp": "not a date", "close": 1.0}]) == []
    assert board.frame_to_bars("nonsense") == []


# ----------------------------------------------------------------------
# cached_board
# ----------------------------------------------------------------------

class FakeClock:
    def __init__(self, start: float = 1_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_cached_board_does_not_refetch_inside_the_ttl_and_does_after_it(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(board, "_monotonic", clock)
    stub = StubFeed(daily={"AAPL": _pct_daily(1.0)})

    first = board.cached_board(["AAPL"], ttl_seconds=60, feed_module=stub)
    assert len(stub.calls) == 3          # 5m + 30m + daily

    clock.advance(59.0)
    second = board.cached_board(["AAPL"], ttl_seconds=60, feed_module=stub)
    assert len(stub.calls) == 3
    assert second is first               # the very same payload object

    clock.advance(2.0)                   # now 61s old
    third = board.cached_board(["AAPL"], ttl_seconds=60, feed_module=stub)
    assert len(stub.calls) == 6
    assert third is not first


def test_cached_board_keys_on_the_requested_symbols(monkeypatch):
    monkeypatch.setattr(board, "_monotonic", FakeClock())
    stub = StubFeed(daily={"AAPL": _pct_daily(1.0), "MSFT": _pct_daily(2.0)})

    board.cached_board(["AAPL"], feed_module=stub)
    board.cached_board(["MSFT"], feed_module=stub)
    assert len(stub.calls) == 6
    board.cached_board(["AAPL"], feed_module=stub)
    assert len(stub.calls) == 6


def test_cached_board_with_a_non_positive_ttl_always_rebuilds(monkeypatch):
    monkeypatch.setattr(board, "_monotonic", FakeClock())
    stub = StubFeed(daily={"AAPL": _pct_daily(1.0)})
    board.cached_board(["AAPL"], ttl_seconds=0, feed_module=stub)
    board.cached_board(["AAPL"], ttl_seconds=0, feed_module=stub)
    assert len(stub.calls) == 6


def test_the_module_ttl_constant_is_one_minute():
    assert board.CACHE_TTL_SECONDS == 60.0


# ----------------------------------------------------------------------
# scanner grade inputs + result (spec 2026-09-21, Task 5)
# ----------------------------------------------------------------------

def _build_with_stub_feed(monkeypatch=None):
    """A populated single-symbol board, shaped like the nearest existing
    "real cells not just nulls" test, so grade/m5/sqzRaw have enough bars to
    compute something other than None."""
    stub = StubFeed(
        five={"NVDA": _intraday_bars(400, minutes=5)},
        thirty={"NVDA": _intraday_bars(400, minutes=30)},
        daily={"NVDA": _daily_bars([100.0 + index * 0.5 for index in range(120)])},
    )
    return board.build_board(["NVDA"], feed_module=stub)


def test_rows_and_rest_carry_grade_m5_and_sqz_raw(monkeypatch):
    payload = _build_with_stub_feed(monkeypatch)
    for row in payload["rows"] + payload["rest"]:
        assert "grade" in row and "m5" in row and "sqzRaw" in row
        if row["grade"] is not None:
            assert row["grade"]["letter"] in ("A+", "A", "B", None)


def test_grading_failure_leaves_row_intact(monkeypatch):
    from momx import grade

    monkeypatch.setattr(grade, "grade_row", lambda *a, **k: 1 / 0)
    payload = _build_with_stub_feed(monkeypatch)
    assert payload["rows"] and all(r["grade"] is None for r in payload["rows"])


def test_a_malformed_5m_bar_still_yields_a_row_with_m5_none():
    """momentum.summarize indexes bar["close"]/["high"] directly (KeyError on
    a bar missing them); build_row must swallow that and still return a row,
    with m5 None rather than propagating the exception."""
    bars = [
        {**bar, "time": bar["timestamp"]}
        for bar in _intraday_bars(8, minutes=5)
    ]
    bars[-1] = {"time": bars[-1]["time"]}     # malformed: no OHLCV at all
    row = board.columns.build_row("NVDA", {"5m": bars})
    assert row["m5"] is None
    assert row["symbol"] == "NVDA"            # the row itself still built


# ----------------------------------------------------------------------
# universe parsing
# ----------------------------------------------------------------------

def test_parse_universe_handles_a_comma_separated_list():
    assert board.parse_universe("AAPL,MSFT") == ["AAPL", "MSFT"]
    assert board.parse_universe("AAPL, MSFT , NVDA,") == ["AAPL", "MSFT", "NVDA"]


def test_parse_universe_handles_newlines_and_whitespace():
    assert board.parse_universe("AAPL\nMSFT\r\nNVDA") == ["AAPL", "MSFT", "NVDA"]
    assert board.parse_universe("AAPL MSFT\tNVDA") == ["AAPL", "MSFT", "NVDA"]


def test_parse_universe_handles_a_list_pasted_with_mixed_separators():
    # A human paste mixes spaces with commas; none of these may be dropped.
    assert board.parse_universe("AAPL MSFT, NVDA;TSLA") == [
        "AAPL", "MSFT", "NVDA", "TSLA",
    ]


def test_parse_universe_uppercases_and_dedupes_preserving_first_seen_order():
    assert board.parse_universe("msft, aapl, MSFT, nvda, aapl") == [
        "MSFT", "AAPL", "NVDA",
    ]


def test_parse_universe_reads_the_symbol_column_of_a_tos_csv_export():
    export = (
        "Symbol,Description,Last,Net Chg,Volume\n"
        "NVDA,NVIDIA CORP,180.52,1.25,250000000\n"
        "AMD,ADVANCED MICRO DEVICES,150.10,-0.75,90000000\n"
        "NVDA,NVIDIA CORP,180.52,1.25,250000000\n"
    )
    assert board.parse_universe(export) == ["NVDA", "AMD"]


def test_parse_universe_reads_a_symbol_column_that_is_not_first():
    export = (
        '"#","Description","Symbol","Last"\n'
        '1,"APPLE INC","AAPL",230.11\n'
        '2,"TESLA INC","TSLA",400.00\n'
    )
    assert board.parse_universe(export) == ["AAPL", "TSLA"]


def test_parse_universe_handles_a_headerless_export_with_extra_columns():
    assert board.parse_universe(
        "NVDA,NVIDIA CORP,180.52\nAMD,ADVANCED MICRO,150.10\n"
    ) == ["NVDA", "AMD"]


def test_parse_universe_drops_obvious_non_symbols():
    assert board.parse_universe(
        "AAPL 123 --- N/A 45.6 $$$ THISISWAYTOOLONGFORATICKER msft"
    ) == ["AAPL", "MSFT"]
    assert board.parse_universe("") == []
    assert board.parse_universe(None) == []


def test_parse_universe_keeps_dotted_and_index_style_symbols():
    assert board.parse_universe("BRK.B, $SPX.X, RDS-A") == ["BRK.B", "$SPX.X", "RDS-A"]


def test_parse_universe_accepts_an_already_split_list():
    assert board.parse_universe(["aapl", "", "msft", "aapl", 7]) == ["AAPL", "MSFT"]


def test_parse_universe_survives_a_four_hundred_symbol_paste():
    symbols = [f"SYM{index:03d}" for index in range(400)]
    assert board.parse_universe("\n".join(symbols)) == symbols


# ----------------------------------------------------------------------
# universe persistence
# ----------------------------------------------------------------------

def test_save_and_load_universe_round_trip(tmp_path):
    path = tmp_path / "universe.json"
    saved = board.save_universe(["nvda", "AAPL", "nvda", "bad symbol"], path=path)

    assert saved == ["NVDA", "AAPL"]
    assert board.load_universe(path=path) == ["NVDA", "AAPL"]

    # Schema 2: named lists. The single-argument call above wrote the ACTIVE
    # list, which is the old behaviour spelled out.
    document = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert document["lists"][document["active"]] == ["NVDA", "AAPL"]
    assert document["schemaVersion"] == board.UNIVERSE_SCHEMA_VERSION
    assert document["savedAt"]


def test_save_universe_accepts_one_pasted_blob(tmp_path):
    path = tmp_path / "universe.json"
    saved = board.save_universe("Symbol,Last\nNVDA,180\nAMD,150\n", path=path)
    assert saved == ["NVDA", "AMD"]
    assert board.load_universe(path=path) == ["NVDA", "AMD"]


def test_save_universe_leaves_no_temp_file_behind(tmp_path):
    path = tmp_path / "universe.json"
    board.save_universe(["NVDA"], path=path)
    assert [item.name for item in tmp_path.iterdir()] == ["universe.json"]


def test_load_universe_falls_back_to_the_seed_default(tmp_path):
    missing = tmp_path / "nothing.json"
    assert board.load_universe(path=missing) == list(board.DEFAULT_UNIVERSE)
    # The seed is the trader's thinkorswim 001_Mega7 list, named by him on
    # 2026-08-27 -- exactly premarket_scanner.PREMARKET_SCAN_SYMBOLS plus USO.
    # Asserted as a set against MEGA7_SEED rather than as a hardcoded prefix,
    # so reordering the tuple is not a test failure but ADDING a symbol is.
    assert board.DEFAULT_UNIVERSE == board.MEGA7_SEED
    assert set(board.DEFAULT_UNIVERSE) == {
        "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO",
    }


def test_load_universe_falls_back_on_corrupt_or_empty_content(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert board.load_universe(path=broken) == list(board.DEFAULT_UNIVERSE)

    empty = tmp_path / "empty.json"
    empty.write_text('{"symbols": []}', encoding="utf-8")
    assert board.load_universe(path=empty) == list(board.DEFAULT_UNIVERSE)


def test_load_universe_accepts_a_bare_json_list(tmp_path):
    path = tmp_path / "bare.json"
    path.write_text('["nvda", "aapl"]', encoding="utf-8")
    assert board.load_universe(path=path) == ["NVDA", "AAPL"]


def test_universe_path_defaults_under_artifacts_and_honours_the_env_override(
    monkeypatch, tmp_path
):
    monkeypatch.delenv(board.UNIVERSE_PATH_ENV, raising=False)
    default = board.universe_path()
    assert default.name == "momx_universe.json"
    assert default.parent.name == "artifacts"

    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(tmp_path / "elsewhere.json"))
    assert board.universe_path() == tmp_path / "elsewhere.json"


def test_build_board_defaults_to_the_persisted_universe(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    board.save_universe(["AAPL", "MSFT"], path=path)
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(path))

    stub = StubFeed()
    payload = board.build_board(feed_module=stub)
    assert payload["universe"] == ["AAPL", "MSFT"]
    assert stub.calls[0] == ("5m", ("AAPL", "MSFT"))


# ----------------------------------------------------------------------
# named lists
# ----------------------------------------------------------------------

@pytest.fixture
def stub_watchlist_seed(monkeypatch):
    """Pin the Watchlist seed so tests do not depend on watchlist.txt.

    In production this seed is ``settings.scanner.default_universe`` -- the
    355 names from watchlist.txt.
    """
    monkeypatch.setattr(
        board.settings.scanner, "default_universe", ["spy", "QQQ", "IWM", "QQQ"]
    )
    return ["SPY", "QQQ", "IWM"]


def test_a_fresh_file_seeds_mag7_and_the_watchlist(tmp_path, stub_watchlist_seed):
    path = tmp_path / "universe.json"
    assert board.list_names(path=path) == [
        board.MEGA7_LIST_NAME,
        board.WATCHLIST_LIST_NAME,
    ]
    assert board.load_universe(board.MEGA7_LIST_NAME, path=path) == list(board.MEGA7_SEED)
    assert board.load_universe(board.WATCHLIST_LIST_NAME, path=path) == stub_watchlist_seed
    # The small list is active by default: the panel's first paint must not
    # wait on a multi-minute 355-symbol build.
    assert board.active_list(path=path) == board.DEFAULT_LIST_NAME
    assert board.DEFAULT_LIST_NAME == board.MEGA7_LIST_NAME


def test_the_watchlist_seed_follows_settings_at_call_time(tmp_path, monkeypatch):
    path = tmp_path / "universe.json"
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["AAPL", "MSFT"])
    assert board.load_universe("Watchlist", path=path) == ["AAPL", "MSFT"]
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["NVDA"])
    assert board.load_universe("Watchlist", path=path) == ["NVDA"]


def test_saving_one_list_leaves_every_other_list_untouched(tmp_path, stub_watchlist_seed):
    path = tmp_path / "universe.json"
    board.save_universe(["NVDA", "AMD"], "Watchlist", path=path)

    assert board.load_universe("Watchlist", path=path) == ["NVDA", "AMD"]
    assert board.load_universe("Mag7", path=path) == list(board.MEGA7_SEED)

    board.save_universe(["TSLA"], "Mag7", path=path)
    assert board.load_universe("Mag7", path=path) == ["TSLA"]
    assert board.load_universe("Watchlist", path=path) == ["NVDA", "AMD"]


def test_omitting_the_name_reads_and_writes_the_active_list(tmp_path, stub_watchlist_seed):
    path = tmp_path / "universe.json"
    # The old single-argument calls still work and mean "the list I am on".
    board.save_universe(["NVDA"], path=path)
    assert board.load_universe(path=path) == ["NVDA"]
    assert board.load_universe("Mag7", path=path) == ["NVDA"]
    assert board.load_universe("Watchlist", path=path) == stub_watchlist_seed

    board.set_active_list("Watchlist", path=path)
    board.save_universe(["AMD", "INTC"], path=path)
    assert board.load_universe(path=path) == ["AMD", "INTC"]
    assert board.load_universe("Watchlist", path=path) == ["AMD", "INTC"]
    assert board.load_universe("Mag7", path=path) == ["NVDA"]


def test_set_active_list_persists_and_matches_case_insensitively(tmp_path):
    path = tmp_path / "universe.json"
    assert board.set_active_list("watchlist", path=path) == "Watchlist"
    assert board.active_list(path=path) == "Watchlist"
    assert board.resolve_list_name("MAG7", path=path) == "Mag7"
    assert board.resolve_list_name(None, path=path) == "Watchlist"
    assert board.resolve_list_name("  ", path=path) == "Watchlist"


def test_an_unknown_list_name_raises_and_names_the_known_lists(tmp_path):
    path = tmp_path / "universe.json"
    with pytest.raises(board.UnknownListError) as caught:
        board.load_universe("Mag8", path=path)
    message = str(caught.value)
    assert "Mag8" in message and "Mag7" in message and "Watchlist" in message
    assert caught.value.known == ["Mag7", "Watchlist"]

    with pytest.raises(board.UnknownListError):
        board.set_active_list("nope", path=path)
    with pytest.raises(board.UnknownListError):
        board.save_universe(["NVDA"], "nope", path=path)
    # A rejected write must not have created anything.
    assert board.list_names(path=path) == ["Mag7", "Watchlist"]


def test_a_brand_new_list_needs_create_and_is_then_a_first_class_list(tmp_path):
    path = tmp_path / "universe.json"
    board.save_universe(["NVDA", "AMD"], "Semis", path=path, create=True)
    assert board.list_names(path=path) == ["Mag7", "Watchlist", "Semis"]
    assert board.load_universe("semis", path=path) == ["NVDA", "AMD"]


def test_a_schema_one_flat_file_migrates_into_the_watchlist(tmp_path, stub_watchlist_seed):
    """The trader's pasted list must survive the split to named lists."""
    path = tmp_path / "universe.json"
    pasted = ["BILI", "CART", "BABA", "AAPL"]
    path.write_text(
        __import__("json").dumps({"schemaVersion": 1, "symbols": pasted}),
        encoding="utf-8",
    )

    assert board.load_universe("Watchlist", path=path) == pasted
    assert board.load_universe("Mag7", path=path) == list(board.MEGA7_SEED)
    # He keeps the board he was looking at, not just the symbols.
    assert board.active_list(path=path) == "Watchlist"
    assert board.load_universe(path=path) == pasted


def test_a_schema_one_bare_json_array_migrates_the_same_way(tmp_path):
    path = tmp_path / "universe.json"
    path.write_text('["nvda", "amd"]', encoding="utf-8")
    assert board.load_universe("Watchlist", path=path) == ["NVDA", "AMD"]
    assert board.active_list(path=path) == "Watchlist"


def test_a_schema_one_file_holding_exactly_the_mag7_seed_migrates_into_mag7(tmp_path):
    path = tmp_path / "universe.json"
    path.write_text(
        __import__("json").dumps({"symbols": list(board.MEGA7_SEED)}), encoding="utf-8"
    )
    assert board.load_universe("Mag7", path=path) == list(board.MEGA7_SEED)
    assert board.active_list(path=path) == "Mag7"


def test_reading_a_schema_one_file_does_not_rewrite_it(tmp_path):
    """Migration is a read-time translation. A read must not touch the disk."""
    path = tmp_path / "universe.json"
    original = '{"schemaVersion": 1, "symbols": ["NVDA", "AMD"]}'
    path.write_text(original, encoding="utf-8")
    board.load_universe(path=path)
    board.list_names(path=path)
    assert path.read_text(encoding="utf-8") == original


def test_migrated_symbols_survive_the_next_save_of_a_different_list(tmp_path):
    path = tmp_path / "universe.json"
    pasted = ["BILI", "CART", "BABA"]
    path.write_text(
        __import__("json").dumps({"schemaVersion": 1, "symbols": pasted}),
        encoding="utf-8",
    )
    board.save_universe(["TSLA"], "Mag7", path=path)

    document = __import__("json").loads(path.read_text(encoding="utf-8"))
    assert document["schemaVersion"] == 2
    assert document["lists"]["Watchlist"] == pasted
    assert document["lists"]["Mag7"] == ["TSLA"]


def test_an_empty_saved_list_reads_back_as_its_seed(tmp_path, stub_watchlist_seed):
    path = tmp_path / "universe.json"
    assert board.save_universe(["123", "!!"], "Watchlist", path=path) == []
    assert board.load_universe("Watchlist", path=path) == stub_watchlist_seed


def test_load_universes_returns_every_list(tmp_path, stub_watchlist_seed):
    path = tmp_path / "universe.json"
    board.save_universe(["NVDA"], "Mag7", path=path)
    assert board.load_universes(path=path) == {
        "Mag7": ["NVDA"],
        "Watchlist": stub_watchlist_seed,
    }


def test_build_board_follows_the_active_list(monkeypatch, tmp_path):
    path = tmp_path / "universe.json"
    board.save_universe(["AAPL", "MSFT"], "Mag7", path=path)
    board.save_universe(["NVDA"], "Watchlist", path=path)
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(path))

    stub = StubFeed()
    assert board.build_board(feed_module=stub)["universe"] == ["AAPL", "MSFT"]

    board.set_active_list("Watchlist", path=path)
    assert board.build_board(feed_module=stub)["universe"] == ["NVDA"]


# ----------------------------------------------------------------------
# the lightning badge (momx.flags, wired in build_board)
# ----------------------------------------------------------------------

BADGE_KEYS = {"on", "reasons", "tooltip"}


def _one_row(symbol="NVDA", **kwargs):
    stub = StubFeed(daily={symbol: _pct_daily(1.0)})
    (row,) = board.build_board([symbol], feed_module=stub, **kwargs)["rows"]
    return row


def test_every_row_carries_a_contract_shaped_badge():
    badge = _one_row()["badge"]
    assert set(badge) == BADGE_KEYS
    assert isinstance(badge["on"], bool)
    assert isinstance(badge["reasons"], list)
    assert isinstance(badge["tooltip"], str)


def test_a_weekly_option_symbol_lights_the_badge(tmp_path, monkeypatch):
    """NVDA's real chain lists weekly expiries (momx/optionable.py store), so
    its badge is on by that alone."""
    (tmp_path / "momx_optionable.json").write_text(
        '{"symbols": {"NVDA": {"hasOptions": true, "weeklies": true}}}', encoding="utf-8")
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    badge = _one_row("NVDA")["badge"]
    assert badge["on"] is True
    assert flags.REASON_WEEKLIES in badge["reasons"]


def test_a_symbol_outside_every_signal_carries_a_dark_badge():
    """Unknown weeklies must read as unknown, not as a reason."""
    symbol = "ZZZZ"
    assert flags.has_weekly_options(symbol) is None
    badge = _one_row(symbol)["badge"]
    assert badge == {"on": False, "reasons": [], "tooltip": ""}


def test_the_earnings_calendar_is_loaded_once_per_build_not_once_per_symbol():
    calls: list[tuple] = []

    def _spy(symbols, **kwargs):
        calls.append(tuple(symbols))
        return {symbol: 3 for symbol in symbols}

    original = flags.load_earnings_calendar
    flags.load_earnings_calendar = _spy
    try:
        symbols = ["AAPL", "MSFT", "NVDA", "AMZN", "META"]
        stub = StubFeed(daily={symbol: _pct_daily(1.0) for symbol in symbols})
        payload = board.build_board(symbols, feed_module=stub)
    finally:
        flags.load_earnings_calendar = original

    assert len(calls) == 1, f"earnings fetched {len(calls)} times for one build"
    assert calls[0] == tuple(symbols)
    for row in payload["rows"]:
        assert flags.REASON_EARNINGS in row["badge"]["reasons"]


def test_an_exploding_earnings_calendar_costs_the_reason_not_the_board():
    def _boom(symbols, **kwargs):
        raise RuntimeError("vendor down")

    original = flags.load_earnings_calendar
    flags.load_earnings_calendar = _boom
    try:
        stub = StubFeed(daily={"NVDA": _pct_daily(1.0)})
        payload = board.build_board(["NVDA"], feed_module=stub)
    finally:
        flags.load_earnings_calendar = original

    (row,) = payload["rows"]
    assert row["pctChange"] is not None          # the row is intact
    assert payload["errors"] == {}               # and not blamed on the symbol
    assert flags.REASON_EARNINGS not in row["badge"]["reasons"]


def test_an_exploding_badge_builder_leaves_a_dark_badge_and_a_live_row():
    def _boom(symbol, row, **kwargs):
        raise RuntimeError("badge broke")

    original = flags.build_badge
    flags.build_badge = _boom
    try:
        stub = StubFeed(daily={"NVDA": _pct_daily(1.0)})
        payload = board.build_board(["NVDA"], feed_module=stub)
    finally:
        flags.build_badge = original

    (row,) = payload["rows"]
    assert row["symbol"] == "NVDA"
    assert row["pctChange"] is not None
    assert row["badge"] == {"on": False, "reasons": [], "tooltip": ""}
    assert payload["errors"] == {}


def test_the_badge_reads_the_row_that_was_actually_built():
    """build_badge must see the finished row, so its rvol half can work."""
    seen: list[tuple] = []

    original = flags.build_badge

    def _spy(symbol, row, **kwargs):
        seen.append((symbol, sorted(row)))
        return original(symbol, row, **kwargs)

    flags.build_badge = _spy
    try:
        stub = StubFeed(
            five={"NVDA": _intraday_bars(400, minutes=5)},
            daily={"NVDA": _daily_bars([100.0 + index * 0.5 for index in range(120)])},
        )
        board.build_board(["NVDA"], feed_module=stub)
    finally:
        flags.build_badge = original

    assert len(seen) == 1
    symbol, keys = seen[0]
    assert symbol == "NVDA"
    assert "rvol" in keys


# ----------------------------------------------------------------------
# the performance contract
# ----------------------------------------------------------------------

def test_importing_momx_board_starts_no_threads():
    # Other tests' background threads may finish during reload. Compare
    # identities so their exit cannot masquerade as an import side effect.
    before = set(threading.enumerate())
    importlib.reload(board)
    assert not (set(threading.enumerate()) - before)


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


def test_momx_board_does_not_import_api_server():
    """momx.board must not drag in the 1MB api_server module.

    Asked of a FRESH interpreter. Asking the pytest process instead makes
    this assert whichever test ran first, which is why it used to fail in
    the full suite and pass alone.
    """
    assert not _imports_api_server_in_a_fresh_interpreter("momx.board")


def test_the_module_exposes_no_background_starter_by_accident():
    """A refresh loop must be opt-in. Today there is none at all.

    If someone adds one, they must add an explicit ``start_*`` function AND
    update this test - which is the point: the constraint becomes impossible
    to relax silently.
    """
    starters = [name for name in dir(board) if name.startswith("start_")]
    assert starters == []


def test_build_board_holds_no_module_state_between_calls():
    """Two identical calls must fetch twice; only cached_board may memoise."""
    stub = StubFeed(daily={"AAPL": _pct_daily(1.0)})
    board.build_board(["AAPL"], feed_module=stub)
    board.build_board(["AAPL"], feed_module=stub)
    assert len(stub.calls) == 6


# ---------------------------------------------------------------------------
# parse_universe must not delete real tickers that share a name with a column
# header. Measured 2026-08-27: watchlist.txt held 358 symbols, the board loaded
# 355. The three lost were LOW (Lowe's), OPEN (Opendoor) and NET (Cloudflare) -
# and NET is on the trader's own MomoX board. A dropped symbol raises nothing
# and logs nothing; it just never produces a row.
# ---------------------------------------------------------------------------

def test_header_named_tickers_survive_a_bare_list():
    assert board.parse_universe(["LOW", "OPEN", "NET", "AAPL"]) == ["LOW", "OPEN", "NET", "AAPL"]


def test_header_named_tickers_survive_a_space_separated_paste():
    # "NET LOW OPEN AAPL" is an ordinary first line, not a header row.
    assert board.parse_universe("NET LOW OPEN AAPL") == ["NET", "LOW", "OPEN", "AAPL"]


def test_a_real_csv_header_is_still_stripped():
    parsed = board.parse_universe("Symbol,Last\nNET,120\nLOW,230\nAAPL,190")
    assert parsed == ["NET", "LOW", "AAPL"]


def test_a_header_only_paste_yields_nothing():
    assert board.parse_universe("Symbol Last Volume") == []


def test_watchlist_txt_loads_every_symbol():
    from config import settings
    raw = [str(s).strip().upper() for s in settings.scanner.default_universe if str(s or "").strip()]
    parsed = board.parse_universe(raw)
    assert len(parsed) == len(set(raw)), "parse_universe dropped symbols from watchlist.txt"
    for ticker in ("LOW", "OPEN", "NET"):
        if ticker in raw:
            assert ticker in parsed, f"{ticker} was dropped"


# ---------------------------------------------------------------------------
# The EA incident, 2026-08-27. The trader deleted EA from My Watchlist. The
# MomX board kept scanning it and kept warning "No data came back for EA".
#
# Cause: _read_document SEEDS every known list, and _write_document wrote the
# document back verbatim. So set_active_list - a plain tab switch - saved the
# seeded 357-symbol Watchlist as a snapshot. From that moment the list stopped
# tracking settings.scanner.default_universe, and nothing the trader did to his
# watchlist could reach the board.
#
# The rule these pin: a list identical to its seed is never persisted, so it
# stays lazy; an explicit paste still saves, because it differs from the seed.
# ---------------------------------------------------------------------------

def _doc(tmp_path, **kw):
    import json
    target = tmp_path / "momx_universe.json"
    payload = {"schemaVersion": 2, "active": "Mag7", "lists": {"Mag7": ["AAPL"]}}
    payload.update(kw)
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def test_switching_tabs_does_not_freeze_the_seeded_watchlist(tmp_path):
    import json
    target = _doc(tmp_path)
    board.set_active_list("Watchlist", path=target)
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert "Watchlist" not in saved["lists"], (
        "a tab switch materialised the seeded Watchlist - it will stop "
        "following My Watchlist, which is the EA bug"
    )
    assert saved["active"] == "Watchlist"


def test_a_seeded_list_still_reports_the_live_watchlist(tmp_path):
    from config import settings
    target = _doc(tmp_path)
    board.set_active_list("Watchlist", path=target)
    live = board._clean_symbols(list(settings.scanner.default_universe))
    assert board.load_universe("Watchlist", path=target) == live


def test_an_explicit_paste_is_still_saved(tmp_path):
    import json
    target = _doc(tmp_path)
    board.save_universe(["TSLA", "NVDA"], "Watchlist", path=target)
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["lists"]["Watchlist"] == ["TSLA", "NVDA"]


def test_the_active_tab_survives_having_no_saved_copy(tmp_path):
    # Removing the stale EA snapshot left `active: "Watchlist"` pointing at a
    # name that existed only as a seed. Resolving `active` against the SAVED
    # lists silently threw the trader's tab away and fell back to Mag7.
    target = _doc(tmp_path, active="Watchlist")
    document = board._read_document(target)
    assert document["active"] == "Watchlist"


# ----------------------------------------------------------------------
# the rest of the list (2026-09-02)
# ----------------------------------------------------------------------

def test_the_symbols_cut_by_the_limit_ship_as_full_rest_rows():
    """Unticking "Scan matches only" must show all 357 WITH their columns.

    First cut (2026-09-02 02:08 ET) shipped them as cheap symbol / industry /
    % change records; the trader's next screenshot asked why they had no
    RVOL, SQZ or Skittles. Now the worker builds every symbol's row.
    """
    stub = StubFeed(
        daily={
            "AAA": _pct_daily(5.0),
            "BBB": _pct_daily(-2.0),
            "CCC": _pct_daily(1.0),
            "DDD": _pct_daily(10.0),
        }
    )
    payload = board.build_board(["AAA", "BBB", "CCC", "DDD"], feed_module=stub, limit=2)

    assert [row["symbol"] for row in payload["rows"]] == ["DDD", "AAA"]
    # everyone else, best day first, and nobody twice
    assert [row["symbol"] for row in payload["rest"]] == ["CCC", "BBB"]
    assert payload["rest"][0]["pctChange"] == pytest.approx(1.0)
    assert payload["rest"][0]["scanPass"] is False
    # the SAME contract as rows: every display column, badge and news slot
    assert set(payload["rest"][0]) == set(payload["rows"][0])
    for key in ("rvol", "sqz", "skittles", "highLow", "color", "quoteTrend", "sparkline", "badge", "news"):
        assert key in payload["rest"][0]
    # a real value, not a placeholder: the daily tape gives a % change AND a last
    assert payload["rest"][0]["last"] is not None


def test_rest_is_empty_when_the_limit_ships_everything():
    stub = StubFeed(daily={"AAA": _pct_daily(1.0), "BBB": _pct_daily(2.0)})
    payload = board.build_board(["AAA", "BBB"], feed_module=stub, limit=None)

    assert len(payload["rows"]) == 2
    assert payload["rest"] == []



# ----------------------------------------------------------------------
# BEAR scanner - one build, two boards (spec 2026-09-24)
# ----------------------------------------------------------------------


def test_every_row_scores_both_directions_and_the_bear_view_promotes_them():
    stub = StubFeed(
        five={"NVDA": _intraday_bars(120, minutes=5)},
        thirty={"NVDA": _intraday_bars(120, minutes=30)},
        daily={"NVDA": _daily_bars([100.0 + index for index in range(60)])},
    )
    payload = board.build_board(["NVDA"], feed_module=stub)
    (row,) = payload["rows"]
    assert row["direction"] == "bull" and payload["direction"] == "bull"
    assert set(row["bear"]) == {"scanPass", "scanReasons", "m5", "grade"}
    assert row["bear"]["m5"]["direction"] == "bear" and row["m5"]["direction"] == "bull"
    assert row["bear"]["grade"]["direction"] == "bear" and row["grade"]["direction"] == "bull"
    bear = board.bear_view(payload)
    assert bear["direction"] == "bear" and bear["universe"] == ["NVDA"]
    (b,) = bear["rows"] + bear["rest"]
    assert b["direction"] == "bear" and "bear" not in b
    assert b["scanPass"] == row["bear"]["scanPass"] and b["m5"] is row["bear"]["m5"]
    assert b["grade"] is row["bear"]["grade"]
    assert b["rvol"] is row["rvol"]              # columns shared, not copied
    board.strip_bear(payload)
    assert "bear" not in payload["rows"][0]


def test_bear_view_ranks_losers_first_matches_first_and_drops_bull_book_keys():
    rows = [
        {"symbol": "UP", "pctChange": 5.0, "scanPass": True, "matchedSince": "x", "strategy": {},
         "gradeFresh": {}, "chartSignals": [], "solo": None, "hotLeader": None, "sectorRotation": None,
         "bear": {"scanPass": False, "scanReasons": [], "m5": None, "grade": None}},
        {"symbol": "DN", "pctChange": -6.0, "scanPass": False,
         "bear": {"scanPass": True, "scanReasons": ["macd:4h"], "m5": None, "grade": {"letter": "A"}}},
        {"symbol": "MID", "pctChange": -1.0, "scanPass": False,
         "bear": {"scanPass": False, "scanReasons": [], "m5": None, "grade": None}},
    ]
    payload = {"generatedAt": "t", "tapeAsOf": None, "universe": ["UP", "DN", "MID"], "universeCount": 3,
               "rows": rows[:1], "rest": rows[1:], "errors": {}, "list": "Mag7", "strategyDaily2": []}
    bear = board.bear_view(payload, limit=2)
    # The bear match leads; the one free slot goes to the next-biggest loser.
    assert [r["symbol"] for r in bear["rows"]] == ["DN", "MID"]
    assert [r["symbol"] for r in bear["rest"]] == ["UP"]
    assert bear["rows"][0]["scanReasons"] == ["macd:4h"] and bear["rows"][0]["grade"] == {"letter": "A"}
    up = bear["rest"][0]
    assert up["scanPass"] is False and up["direction"] == "bear"
    assert not any(k in up for k in ("bear", "matchedSince", "strategy", "gradeFresh", "chartSignals",
                                      "solo", "hotLeader", "sectorRotation"))
    assert "strategyDaily2" not in bear and bear["list"] == "Mag7" and bear["universeCount"] == 3


def test_a_newly_bear_matching_symbol_never_shows_last_cycles_columns(monkeypatch):
    """Review Focus 2: the reuse path used to rebuild only for a NEW BULL match."""
    stale = {"symbol": "BBB", "pctChange": -99.0, "rvol": {"5m": {"value": 42.0}},
             "sqz": {}, "skittles": {}, "highLow": {}, "color": {}, "quoteTrend": [],
             "sparkline": [], "hourHighLow": {}, "industry": "", "last": 1.0}

    def bear_only(tapes, last=None, direction="bull", **kwargs):
        return (direction == "bear", ["rvol:5m"] if direction == "bear" else [])

    monkeypatch.setattr(board.scan, "scan_symbol", bear_only)
    payload = board.build_board(
        ["AAA", "BBB"], feed_module=_two_symbol_feed(),
        full_rows_for=["AAA"], reuse_rows={"BBB": stale},
    )
    rows = {row["symbol"]: row for row in payload["rows"] + payload["rest"]}
    assert rows["BBB"]["scanPass"] is False and rows["BBB"]["bear"]["scanPass"] is True
    assert rows["BBB"]["rvol"]["5m"]["value"] != 42.0


def test_contract_row_keeps_bear_momentum_from_a_reused_contract_row():
    """Review 2026-09-25: a reused row is a CONTRACT row (no m5Bear key). Its
    bear block, when still present, must feed the new bear block."""
    bear_m5 = {"direction": "bear", "state": "building"}
    reused = {"symbol": "AAA", "m5": {"direction": "bull"}, "bear": {"scanPass": False, "scanReasons": [], "m5": bear_m5, "grade": None}}
    shipped = board._contract_row(reused, False, [], None, True, ["rvol:5m"])
    assert shipped["bear"]["m5"] is bear_m5 and shipped["bear"]["scanPass"] is True
    fresh = {"symbol": "AAA", "m5": {"direction": "bull"}, "m5Bear": bear_m5}
    assert board._contract_row(fresh, False, [])["bear"]["m5"] is bear_m5
    assert board._contract_row({"symbol": "AAA"}, False, [])["bear"]["m5"] is None
