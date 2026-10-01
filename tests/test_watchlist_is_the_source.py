"""One watchlist: a narrow list may never outlive a deletion from the master.

The trader deleted EA from My Watchlist on 2026-08-27 and the MomX board kept
scanning it, because MomX held its own copy. The same shape existed for the
option and MAG7 lists: each is its own stored collection, and nothing pruned
them when the master changed.

_within_watchlist applies the rule at READ time. A sync on write can be missed
(and was); a filter cannot be.
"""

from __future__ import annotations

import types


def _make(monkeypatch, master):
    import api_server
    from config import settings

    monkeypatch.setattr(settings.scanner, "default_universe", list(master), raising=False)
    obj = types.SimpleNamespace()
    obj._within_watchlist = types.MethodType(
        api_server.DashboardState._within_watchlist, obj
    )
    return obj


def test_a_ticker_removed_from_the_master_is_dropped(monkeypatch):
    state = _make(monkeypatch, ["AAPL", "MSFT"])
    assert state._within_watchlist(["AAPL", "EA", "MSFT"]) == ["AAPL", "MSFT"]


def test_a_clean_subset_is_returned_untouched(monkeypatch):
    state = _make(monkeypatch, ["AAPL", "MSFT", "NVDA"])
    assert state._within_watchlist(["AAPL", "NVDA"]) == ["AAPL", "NVDA"]


def test_matching_ignores_case_and_spacing(monkeypatch):
    state = _make(monkeypatch, ["AAPL"])
    assert state._within_watchlist([" aapl "]) == [" aapl "]


def test_an_empty_master_never_blanks_a_list(monkeypatch):
    # A failed watchlist read must not silently empty the option surfaces.
    state = _make(monkeypatch, [])
    assert state._within_watchlist(["AAPL", "MSFT"]) == ["AAPL", "MSFT"]


def test_a_broken_settings_object_never_blanks_a_list(monkeypatch):
    import api_server
    from config import settings

    class Boom:
        @property
        def default_universe(self):
            raise RuntimeError("settings exploded")

    monkeypatch.setattr(api_server, "settings", types.SimpleNamespace(scanner=Boom()))
    obj = types.SimpleNamespace()
    obj._within_watchlist = types.MethodType(
        api_server.DashboardState._within_watchlist, obj
    )
    assert obj._within_watchlist(["AAPL"]) == ["AAPL"]


def test_missing_input_is_an_empty_list_not_a_crash(monkeypatch):
    state = _make(monkeypatch, ["AAPL"])
    assert state._within_watchlist(None) == []


# ---------------------------------------------------------------------------
# The dashboard payload is cached for DASHBOARD_FULL_CACHE_TTL_SECONDS (60s),
# and when the cache is stale it serves the OLD payload while refreshing in the
# background, re-merging only a fixed list of "dynamic" keys. `watchlist` is not
# on that list. So without an explicit invalidation, a ticker the trader just
# added or removed does not reach ANY surface for up to a minute - which is
# precisely "I added it and it doesn't work everywhere".
# ---------------------------------------------------------------------------

def _persister_source(name: str) -> str:
    import inspect
    import api_server

    return inspect.getsource(getattr(api_server.DashboardState, name))


def test_writing_the_master_watchlist_invalidates_the_dashboard_cache():
    assert "_invalidate_dashboard_cache" in _persister_source("_persist_watchlist")


def test_writing_the_option_watchlist_invalidates_the_dashboard_cache():
    assert "_invalidate_dashboard_cache" in _persister_source("_persist_option_watchlist")


def test_writing_the_mag7_watchlist_invalidates_the_dashboard_cache():
    assert "_invalidate_dashboard_cache" in _persister_source("_persist_mag7_scanner_watchlist")


def test_watchlist_is_still_absent_from_the_stale_merge_keys():
    # If `watchlist` is ever added to the stale-serve merge list, the
    # invalidation above becomes belt-and-braces rather than load-bearing.
    # This test exists to make that a deliberate change, not an accident.
    import inspect
    import api_server

    source = inspect.getsource(api_server.DashboardState.dashboard_payload)
    merge_block = source.split("for key in (")[-1].split("):")[0]
    assert '"watchlist"' not in merge_block


def test_the_master_write_patches_the_served_payload_not_just_the_ttl():
    # _invalidate_dashboard_cache only ages the cache 10s against a 60s TTL, so
    # on its own it does NOT make an edit visible. The patch is what does.
    source = _persister_source("_persist_watchlist")
    assert "_patch_dashboard_cache" in source
    for key in ("watchlist=", "optionWatchlist=", "activeOptionWatchlist=", "mag7OptionWatchlist="):
        assert key in source, f"{key} is not refreshed when the master changes"


def test_the_patch_swaps_a_new_dict_rather_than_mutating_the_shared_one():
    # dashboard_payload hands the cached object to concurrent readers, so
    # mutating it in place would change a payload mid-serialisation.
    import inspect
    import api_server

    source = inspect.getsource(api_server.DashboardState._patch_dashboard_cache)
    assert "{**cached, **updates}" in source


# ---------------------------------------------------------------------------
# Market-open speed, 2026-08-28. The idle 5s poll shipped ~50 KB of which the
# surviving UI read ~1.5 KB, and the only keys CHANGING between polls fed pages
# deleted on 08-27. Each changed key produced a new top-level dashboard object,
# defeated the frontend's identity bail-out, and re-rendered the whole
# workspace 12x a minute - directly competing with the chart at the open.
# ---------------------------------------------------------------------------

def test_dead_page_scan_arrays_stay_out_of_the_poll_payload():
    import api_server

    omitted = set(api_server.DASHBOARD_COMPACT_OMIT_KEYS)
    for key in (
        "scanResults", "candidateResults", "mag7ScanResults",
        "oiScanResults", "oiMag7ScanResults", "oiWatchlistScanResults",
        "oiScanTimestamp", "oiMag7ScanTimestamp", "oiWatchlistScanTimestamp",
    ):
        assert key in omitted, f"{key} is back in the 5s poll - the render tax returns"


def test_the_watchlist_itself_is_never_omitted_from_the_poll():
    # The whole point of tonight: an add/remove must reach the UI on the next
    # poll. Omitting `watchlist` from compact would silently undo that.
    import api_server

    assert "watchlist" not in set(api_server.DASHBOARD_COMPACT_OMIT_KEYS)


# ---------------------------------------------------------------------------
# Chart build concurrency, 2026-08-28 (market hours). The background chart
# refresh spawned a bare unbounded thread per symbol. Nothing capped how many
# DIFFERENT symbols built at once, so a burst reached 68 concurrent builder
# threads fighting the GIL over pandas work - a slim chart fetch measured 94s
# and the app went blank. Bounded to a small pool so interactive requests keep
# GIL time no matter how many symbols poll.
# ---------------------------------------------------------------------------

def test_background_chart_refresh_is_bounded_not_a_bare_thread():
    import inspect
    import api_server

    src = inspect.getsource(api_server.DashboardState._start_oi_finder_chart_refresh)
    assert "oi_finder_chart_refresh_pool.submit" in src, (
        "chart refresh is spawning unbounded threads again - the 68-thread pileup returns"
    )
    assert "threading.Thread(" not in src, "a bare thread crept back into the refresh path"


def test_the_chart_refresh_pool_is_small():
    import inspect
    import api_server

    src = inspect.getsource(api_server.DashboardState.__init__)
    assert "oi_finder_chart_refresh_pool = ThreadPoolExecutor(" in src
    # A tight bound is the whole point; anything large reopens the GIL storm.
    import re
    m = re.search(r"oi_finder_chart_refresh_pool = ThreadPoolExecutor\(\s*max_workers=(\d+)", src)
    assert m and int(m.group(1)) <= 3, "chart refresh pool is too wide to bound GIL contention"


# ---------------------------------------------------------------------------
# Cold-open paint priority, 2026-08-28. The candles-first paint (~2s) and the
# full study build (9-101s) ran in ONE pool task, so a cold symbol's paint
# waited behind another symbol's slow study build - "Loading..." for tens of
# seconds. Split so the paint runs on its own priority pool and the studies
# queue on the bounded study pool.
# ---------------------------------------------------------------------------

def test_paint_has_its_own_pool_separate_from_the_study_pool():
    import inspect
    import api_server

    init = inspect.getsource(api_server.DashboardState.__init__)
    assert "oi_finder_chart_paint_pool = ThreadPoolExecutor(" in init
    assert "oi_finder_chart_refresh_pool = ThreadPoolExecutor(" in init


def test_a_full_refresh_paints_first_then_queues_the_study_build():
    import inspect
    import api_server

    src = inspect.getsource(api_server.DashboardState._start_oi_finder_chart_refresh)
    # Paint on the paint pool, studies on the refresh pool.
    assert "oi_finder_chart_paint_pool.submit" in src
    assert "oi_finder_chart_refresh_pool.submit" in src
    # skip_paint True is passed to the chained full build so it does not repaint.
    assert "skip_paint" in inspect.getsource(api_server.DashboardState._run_oi_finder_chart_refresh)
