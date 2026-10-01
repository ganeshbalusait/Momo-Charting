"""Tests for the MomoX lightning badge (``momx.flags``).

NOTHING HERE TOUCHES THE NETWORK. An autouse fixture replaces
``flags._http_json`` with a function that raises and clears both vendor API
keys out of the environment, so any test that forgets to inject its own
``fetch=`` gets an empty calendar rather than a live Finnhub/FMP call. The
same fixture drops the module's TTL cache between tests so one test can never
see another one's dates.

The badge's whole job is to be harmless: an unknown answer shows nothing, and
a source that blows up loses its own reason and nothing else. Most of what is
asserted below is that harmlessness.
"""

from __future__ import annotations

import importlib
import json
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from momx import flags


TODAY = date(2026, 8, 27)

#: Names whose REAL chain lists weekly expiries (census 2026-09-26).
WEEKLY_SEED = ("AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO", "SPY", "QQQ")


# ----------------------------------------------------------------------
# fixtures / helpers
# ----------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _no_network(monkeypatch, tmp_path):
    """No vendor call, no vendor key, no cache leaking between tests, and no
    reading of the trader's real ``artifacts/earnings_manual_imports.json`` -
    a real future date in that file must never decide a test."""

    def _boom(*args, **kwargs):  # pragma: no cover - only fires on a mistake
        raise AssertionError("momx.flags made a live HTTP request in a test")

    monkeypatch.setattr(flags, "_http_json", _boom)
    monkeypatch.setattr(flags, "EARNINGS_MANUAL_IMPORT_PATH", tmp_path / "no-imports.json")
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    # The weeklies answer now comes from the real-chain store
    # (momx/optionable.py). Each test gets its own store, shaped exactly like
    # the one the checker writes: the seed universe read as weekly, and two
    # names read with options but MONTHLY expiries only.
    store = {sym: {"hasOptions": True, "weeklies": True} for sym in WEEKLY_SEED}
    store["QMCO"] = {"hasOptions": True, "weeklies": False}
    store["JAGX"] = {"hasOptions": False}
    (tmp_path / "momx_optionable.json").write_text(json.dumps({"symbols": store}), encoding="utf-8")
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    flags.reset_caches()
    yield
    flags.reset_caches()


def rvol_row(**by_timeframe) -> dict:
    """A row shaped exactly like ``momx.columns.build_row`` leaves it."""
    cells = {key: {"value": None, "bg": None, "fg": None}
             for key in ("5m", "15m", "30m", "1h", "2h", "4h", "D")}
    for key, value in by_timeframe.items():
        cells[key] = {"value": value, "bg": None, "fg": None}
    return {"symbol": "TEST", "rvol": cells}


def quiet_row() -> dict:
    return rvol_row(**{"5m": 0.4, "15m": -1.2, "D": 1.1})


def loud_row() -> dict:
    return rvol_row(**{"5m": 0.4, "4h": 3.1, "D": 1.1})


class RaisingGet(dict):
    """A mapping whose ``get`` blows up -- a broken calendar."""

    def get(self, *args, **kwargs):
        raise RuntimeError("earnings source exploded")


class RaisingContains:
    """A table whose membership test blows up -- a broken weeklies source."""

    def __contains__(self, item):
        raise RuntimeError("weeklies source exploded")


class RaisingRow(dict):
    """A row whose ``get`` blows up -- a broken RVOL source."""

    def get(self, *args, **kwargs):
        raise RuntimeError("row exploded")


def finnhub(rows):
    return lambda url: {"earningsCalendar": list(rows)}


# ----------------------------------------------------------------------
# import discipline (same rules momx.board lives under)
# ----------------------------------------------------------------------

def test_import_starts_no_thread():
    """The board runs this on a timer; import must be inert."""
    before = threading.active_count()
    importlib.reload(flags)
    assert threading.active_count() == before


def test_module_never_imports_api_server():
    """api_server is 780KB and starts servers. momx may not touch it."""
    source = Path(flags.__file__).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s+api_server", source, re.MULTILINE)


# ----------------------------------------------------------------------
# 1. has_weekly_options
# ----------------------------------------------------------------------

def test_real_chain_store_answers_true_for_the_seed_universe():
    for symbol in WEEKLY_SEED:
        assert flags.has_weekly_options(symbol) is True, symbol


def test_monthly_only_and_no_options_read_false_not_unknown():
    """A chain that was actually read can say no: QMCO lists Oct 16 only."""
    assert flags.has_weekly_options("QMCO") is False
    assert flags.has_weekly_options("JAGX") is False
    assert flags.build_badge("QMCO", {})["reasons"] == []


def test_no_store_at_all_is_unknown_for_everyone(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path / "empty"))
    assert flags.has_weekly_options("NVDA") is None


def test_a_corrupt_store_is_unknown_not_a_crash(tmp_path, monkeypatch):
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "momx_optionable.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path / "bad"))
    assert flags.has_weekly_options("NVDA") is None


def test_the_hand_made_table_is_gone():
    assert not hasattr(flags, "WEEKLY_OPTION_SYMBOLS")


def test_symbol_outside_the_table_is_unknown_not_false():
    assert flags.has_weekly_options("ZZZQ") is None
    assert flags.has_weekly_options("ZZZQ") is not False


def test_symbol_is_normalised_before_lookup():
    assert flags.has_weekly_options("  nvda \n") is True


@pytest.mark.parametrize("bad", [None, "", "   ", 0])
def test_blank_symbol_is_unknown(bad):
    assert flags.has_weekly_options(bad) is None


def test_unknown_is_never_remembered_as_false():
    """No negative cache: the same call can flip to True the moment a source
    learns the answer, without a restart or a cache purge."""
    assert flags.has_weekly_options("NEWCO") is None
    assert flags.has_weekly_options("NEWCO") is None
    assert flags.has_weekly_options("NEWCO", weekly_symbols={"NEWCO"}) is True


def test_injected_mapping_can_say_yes_no_or_unknown():
    table = {"AAA": True, "BBB": False, "CCC": None}
    assert flags.has_weekly_options("AAA", weekly_symbols=table) is True
    assert flags.has_weekly_options("BBB", weekly_symbols=table) is False
    assert flags.has_weekly_options("CCC", weekly_symbols=table) is None
    assert flags.has_weekly_options("DDD", weekly_symbols=table) is None


def test_a_raising_weeklies_source_is_unknown_not_an_exception():
    assert flags.has_weekly_options("NVDA", weekly_symbols=RaisingContains()) is None


# ----------------------------------------------------------------------
# 2. load_earnings_calendar
# ----------------------------------------------------------------------

def test_calendar_costs_one_bulk_request_for_the_whole_universe(monkeypatch):
    """The whole point: one range call per build, never one per symbol."""
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return {"earningsCalendar": [{"symbol": "NVDA", "date": "2026-08-30"}]}

    universe = [f"SYM{index}" for index in range(60)] + ["NVDA"]
    result = flags.load_earnings_calendar(universe, now=TODAY, fetch=fetch)

    assert len(calls) == 1
    assert "from=2026-08-27" in calls[0]
    assert result["NVDA"] == 3
    assert result["SYM7"] is None
    assert len(result) == 61


def test_calendar_reads_days_until_from_the_provider_date(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    rows = [
        {"symbol": "AAPL", "date": "2026-08-27"},   # today
        {"symbol": "MSFT", "date": "2026-08-28"},   # tomorrow
        {"symbol": "TSLA", "date": "2026-09-10"},   # 14 days out
    ]
    result = flags.load_earnings_calendar(
        ["AAPL", "MSFT", "TSLA", "AMZN"], now=TODAY, fetch=finnhub(rows)
    )
    assert result == {"AAPL": 0, "MSFT": 1, "TSLA": 14, "AMZN": None}


def test_calendar_keeps_the_earliest_date_when_a_symbol_repeats(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    rows = [
        {"symbol": "NVDA", "date": "2026-09-20"},
        {"symbol": "NVDA", "date": "2026-08-29"},
    ]
    result = flags.load_earnings_calendar(["NVDA"], now=TODAY, fetch=finnhub(rows))
    assert result["NVDA"] == 2


def test_calendar_falls_back_to_fmp_when_finnhub_has_no_key(monkeypatch):
    monkeypatch.setenv("FMP_API_KEY", "fmp-key")
    seen: list[str] = []

    def fetch(url):
        seen.append(url)
        return [{"symbol": "COIN", "date": "2026-08-31"}]

    result = flags.load_earnings_calendar(["COIN"], now=TODAY, fetch=fetch)
    assert len(seen) == 1
    assert "financialmodelingprep.com" in seen[0]
    assert result["COIN"] == 4


def test_a_dead_provider_degrades_to_all_unknown(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")

    def fetch(url):
        raise TimeoutError("vendor down")

    result = flags.load_earnings_calendar(["NVDA", "AAPL"], now=TODAY, fetch=fetch)
    assert result == {"NVDA": None, "AAPL": None}


def test_manual_screenshot_imports_are_read_and_beat_the_provider(monkeypatch, tmp_path):
    """Same precedence api_server uses: the trader reviewed these himself."""
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    path = tmp_path / "earnings_manual_imports.json"
    path.write_text(
        json.dumps([
            {"symbol": "NVDA", "date": "2026-08-28", "timingCode": "amc"},
            {"symbol": "WFC", "date": "2026-09-01", "timingCode": "bmo"},
        ]),
        encoding="utf-8",
    )
    rows = [{"symbol": "NVDA", "date": "2026-09-15"}]
    result = flags.load_earnings_calendar(
        ["NVDA", "WFC"], now=TODAY, fetch=finnhub(rows), manual_path=path
    )
    assert result == {"NVDA": 1, "WFC": 5}


def test_a_corrupt_manual_import_file_is_simply_no_dates(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{not json", encoding="utf-8")
    assert flags.load_earnings_calendar(["NVDA"], now=TODAY, manual_path=path) == {"NVDA": None}


def test_a_missing_manual_import_file_is_simply_no_dates(tmp_path):
    result = flags.load_earnings_calendar(
        ["NVDA"], now=TODAY, manual_path=tmp_path / "nope.json"
    )
    assert result == {"NVDA": None}


def test_calendar_with_no_symbols_is_empty_and_does_nothing():
    assert flags.load_earnings_calendar([]) == {}
    assert flags.load_earnings_calendar(None) == {}


def test_calendar_ignores_dates_outside_the_horizon(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    rows = [
        {"symbol": "OLD", "date": "2026-08-01"},   # already happened
        {"symbol": "FAR", "date": "2027-01-05"},   # past the horizon
    ]
    result = flags.load_earnings_calendar(
        ["OLD", "FAR"], now=TODAY, fetch=finnhub(rows)
    )
    assert result == {"OLD": None, "FAR": None}


def test_calendar_survives_junk_rows(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    rows = [
        "not a row",
        {"symbol": None, "date": "2026-08-28"},
        {"symbol": "NVDA", "date": "not-a-date"},
        {"symbol": "NVDA", "date": "2026-08-28"},
    ]
    assert flags.load_earnings_calendar(["NVDA"], now=TODAY, fetch=finnhub(rows))["NVDA"] == 1


def test_the_shared_cache_is_filled_once_and_reused(monkeypatch):
    """Board refreshes every 60s; the vendor must not see 60s traffic."""
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return {"earningsCalendar": [{"symbol": "ZZZQ",
                                      "date": (datetime.now().date() + timedelta(days=2)).isoformat()}]}

    monkeypatch.setattr(flags, "_http_json", fetch)
    first = flags.load_earnings_calendar(["ZZZQ"])
    second = flags.load_earnings_calendar(["ZZZQ", "AAPL"])
    assert first["ZZZQ"] == 2
    assert second["ZZZQ"] == 2
    assert second["AAPL"] is None
    assert len(calls) == 1


def test_network_is_skipped_entirely_without_a_vendor_key():
    """No key means no socket at all -- the autouse _http_json would raise."""
    assert flags.load_earnings_calendar(["ZZZQ"]) == {"ZZZQ": None}


def test_network_can_be_switched_off_by_env(monkeypatch):
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    monkeypatch.setenv("MOMX_FLAGS_EARNINGS_NETWORK", "0")
    assert flags.load_earnings_calendar(["ZZZQ"]) == {"ZZZQ": None}


# ----------------------------------------------------------------------
# 3. earnings_within
# ----------------------------------------------------------------------

def test_earnings_within_returns_the_day_count_inside_the_window():
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": 3}) == 3


def test_earnings_today_is_zero_not_falsy():
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": 0}) == 0


def test_earnings_at_the_exact_edge_of_the_window_counts():
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": 7}) == 7


def test_earnings_past_the_window_is_no_reason():
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": 8}) is None


def test_earnings_already_reported_is_no_reason():
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": -2}) is None


def test_earnings_unknown_symbol_is_none():
    assert flags.earnings_within("AAPL", 7, calendar={"NVDA": 1}) is None
    assert flags.earnings_within("AAPL", 7, calendar={"AAPL": None}) is None


def test_earnings_within_accepts_a_date_or_iso_string_calendar():
    soon = date.today() + timedelta(days=2)
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": soon}) == 2
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": soon.isoformat()}) == 2


def test_earnings_within_accepts_an_api_server_style_row():
    row = {"symbol": "NVDA", "date": "2026-09-01", "daysUntil": 4}
    assert flags.earnings_within("NVDA", 7, calendar={"NVDA": row}) == 4


def test_earnings_within_normalises_the_symbol():
    assert flags.earnings_within(" nvda ", 7, calendar={"NVDA": 1}) == 1


def test_a_raising_calendar_is_unknown_not_an_exception():
    assert flags.earnings_within("NVDA", 7, calendar=RaisingGet()) is None


def test_a_calendar_of_the_wrong_shape_is_unknown():
    assert flags.earnings_within("NVDA", 7, calendar=["NVDA"]) is None


# ----------------------------------------------------------------------
# 4. unusual_rvol -- reads the cells already on the row
# ----------------------------------------------------------------------

def test_rvol_fires_at_two_standard_deviations():
    """2.0 is the trader's own green/red rung in momx.columns.rvol_cell."""
    assert flags.unusual_rvol(rvol_row(**{"15m": 2.0})) is True


def test_rvol_just_below_two_does_not_fire():
    assert flags.unusual_rvol(rvol_row(**{"15m": 1.9})) is False


def test_rvol_fires_on_any_single_timeframe():
    assert flags.unusual_rvol(loud_row()) is True
    assert flags.unusual_rvol(quiet_row()) is False


def test_rvol_is_a_zscore_so_a_big_negative_is_not_unusual():
    assert flags.unusual_rvol(rvol_row(**{"5m": -4.0})) is False


def test_rvol_threshold_is_caller_settable():
    row = rvol_row(**{"D": 1.6})
    assert flags.unusual_rvol(row) is False
    assert flags.unusual_rvol(row, 1.5) is True


def test_rvol_ignores_blank_cells_and_missing_blocks():
    assert flags.unusual_rvol(rvol_row()) is False
    assert flags.unusual_rvol({"symbol": "X"}) is False
    assert flags.unusual_rvol({"symbol": "X", "rvol": None}) is False


def test_rvol_accepts_a_bare_cell_or_a_bare_number():
    assert flags.unusual_rvol({"rvol": {"value": 2.5}}) is True
    assert flags.unusual_rvol({"rvol": 2.5}) is True
    assert flags.unusual_rvol({"rvol": 0.5}) is False


def test_rvol_on_a_non_row_is_false_not_an_exception():
    assert flags.unusual_rvol(None) is False
    assert flags.unusual_rvol("NVDA") is False


def test_a_raising_row_is_false_not_an_exception():
    assert flags.unusual_rvol(RaisingRow()) is False


# ----------------------------------------------------------------------
# 5. build_badge -- the contract
# ----------------------------------------------------------------------

def test_badge_shape_is_exactly_the_contract():
    badge = flags.build_badge("NVDA", loud_row(), earnings_map={"NVDA": 3})
    assert set(badge) == {"on", "reasons", "tooltip"}
    assert isinstance(badge["on"], bool)
    assert isinstance(badge["reasons"], list)
    assert isinstance(badge["tooltip"], str)


def test_no_reason_means_no_badge():
    badge = flags.build_badge("ZZZQ", quiet_row(), earnings_map={"ZZZQ": None})
    assert badge == {"on": False, "reasons": [], "tooltip": ""}


def test_weeklies_alone():
    badge = flags.build_badge("NVDA", quiet_row(), earnings_map={"NVDA": None})
    assert badge["on"] is True
    assert badge["reasons"] == ["weeklies"]
    assert badge["tooltip"] == "Weekly options"


def test_earnings_alone():
    badge = flags.build_badge("ZZZQ", quiet_row(), earnings_map={"ZZZQ": 3})
    assert badge["on"] is True
    assert badge["reasons"] == ["earnings"]
    assert badge["tooltip"] == "Earnings in 3 days"


def test_rvol_alone():
    badge = flags.build_badge("ZZZQ", loud_row(), earnings_map={"ZZZQ": None})
    assert badge["on"] is True
    assert badge["reasons"] == ["rvol"]
    assert badge["tooltip"] == "Unusual volume (RVOL 3.1)"


def test_all_three_reasons_in_contract_order():
    badge = flags.build_badge("NVDA", loud_row(), earnings_map={"NVDA": 3})
    assert badge["on"] is True
    assert badge["reasons"] == ["weeklies", "earnings", "rvol"]
    assert badge["tooltip"] == "Weekly options - earnings in 3 days - unusual volume (RVOL 3.1)"


def test_two_reasons_read_like_the_spec_example():
    badge = flags.build_badge("NVDA", quiet_row(), earnings_map={"NVDA": 3})
    assert badge["reasons"] == ["weeklies", "earnings"]
    assert badge["tooltip"] == "Weekly options - earnings in 3 days"


@pytest.mark.parametrize("days,phrase", [
    (0, "Earnings today"),
    (1, "Earnings tomorrow"),
    (2, "Earnings in 2 days"),
    (7, "Earnings in 7 days"),
])
def test_earnings_tooltip_wording(days, phrase):
    badge = flags.build_badge("ZZZQ", quiet_row(), earnings_map={"ZZZQ": days})
    assert badge["tooltip"] == phrase


def test_unknown_weeklies_renders_no_badge():
    """Unknown is not a reason. ZZZQ is outside the curated table."""
    assert flags.has_weekly_options("ZZZQ") is None
    badge = flags.build_badge("ZZZQ", quiet_row(), earnings_map={})
    assert badge["on"] is False
    assert badge["reasons"] == []


def test_an_explicit_no_on_weeklies_renders_no_badge():
    badge = flags.build_badge(
        "NVDA", quiet_row(), earnings_map={}, weekly_symbols={"NVDA": False}
    )
    assert badge["on"] is False


def test_a_missing_earnings_date_does_not_blank_the_row():
    """One unknown date must cost its own reason and nothing else."""
    badge = flags.build_badge("NVDA", loud_row(), earnings_map={})
    assert badge["on"] is True
    assert badge["reasons"] == ["weeklies", "rvol"]
    assert badge["tooltip"] == "Weekly options - unusual volume (RVOL 3.1)"


def test_no_earnings_map_at_all_still_builds_a_badge():
    badge = flags.build_badge("NVDA", loud_row())
    assert badge["reasons"] == ["weeklies", "rvol"]


def test_a_raising_earnings_source_degrades_instead_of_propagating():
    badge = flags.build_badge("NVDA", loud_row(), earnings_map=RaisingGet())
    assert badge["on"] is True
    assert badge["reasons"] == ["weeklies", "rvol"]


def test_a_raising_weeklies_source_degrades_instead_of_propagating():
    badge = flags.build_badge(
        "NVDA", loud_row(), earnings_map={"NVDA": 3}, weekly_symbols=RaisingContains()
    )
    assert badge["on"] is True
    assert badge["reasons"] == ["earnings", "rvol"]


def test_a_raising_row_degrades_instead_of_propagating():
    badge = flags.build_badge("NVDA", RaisingRow(), earnings_map={"NVDA": 3})
    assert badge["on"] is True
    assert badge["reasons"] == ["weeklies", "earnings"]


def test_every_source_raising_at_once_is_still_just_a_dark_badge():
    badge = flags.build_badge(
        "NVDA", RaisingRow(), earnings_map=RaisingGet(), weekly_symbols=RaisingContains()
    )
    assert badge == {"on": False, "reasons": [], "tooltip": ""}


def test_badges_are_fresh_dicts_not_a_shared_one():
    first = flags.build_badge("ZZZQ", quiet_row())
    second = flags.build_badge("ZZZQ", quiet_row())
    first["reasons"].append("tampered")
    assert second["reasons"] == []


def test_build_badge_never_touches_the_network_per_symbol(monkeypatch):
    """355 rows must not become 355 vendor calls -- the autouse _http_json
    raises, so a per-symbol fetch would fail the test loudly."""
    monkeypatch.setenv("FINNHUB_API_KEY", "k")
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        return {"earningsCalendar": []}

    monkeypatch.setattr(flags, "_http_json", fetch)
    for index in range(50):
        flags.build_badge(f"SYM{index}", quiet_row())
    assert len(calls) <= 1
