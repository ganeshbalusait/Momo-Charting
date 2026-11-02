"""Scan sharing between lists (2026-10-16): Liquid Movers (229) is almost all
on the Watchlist (368), so its build takes the Watchlist's recent scan records
instead of scanning the same tickers again."""

from momx import board


def _records(symbols):
    return [{"symbol": s, "pctChange": 1.0, "scanPass": True, "_reasons": (), "_row": {"symbol": s, "bear": {}},
             "_last": 1.0, "bearPass": False, "_bearReasons": ()} for s in symbols]


def test_a_smaller_list_borrows_a_recent_larger_scan(monkeypatch):
    big = [f"S{i}" for i in range(200)]
    board._remember_scan(big, _records(big), "2026-10-16T15:00:00+00:00")
    small = big[:100] + ["NEW"]
    got, as_of = board._borrow_scan(small)
    assert set(got) == set(big[:100]) and as_of == "2026-10-16T15:00:00+00:00"
    got["S0"]["_row"]["symbol"] = "changed"                      # a copy, not the shared record
    assert board._LAST_SCAN["records"]["S0"]["_row"]["symbol"] == "S0"


def test_no_borrow_when_old_small_bigger_or_poorly_covered(monkeypatch):
    big = [f"S{i}" for i in range(200)]
    board._remember_scan(big, _records(big), None)
    assert board._borrow_scan(big)[0] == {}                       # same size: the Watchlist never borrows
    assert board._borrow_scan(big[:10])[0] == {}                  # small lists (Mag7) always scan their own
    assert board._borrow_scan(big[:60] + [f"X{i}" for i in range(40)])[0] == {}   # 60% covered
    monkeypatch.setattr(board, "SCAN_SHARE_MAX_AGE_SECONDS", -1.0)
    assert board._borrow_scan(big[:100])[0] == {}                 # too old


def test_shared_scan_stamp_matches_what_a_build_would_borrow():
    big = [f"S{i}" for i in range(200)]
    board._remember_scan(big, _records(big), None)
    stamp = board.shared_scan_stamp(big[:100])
    assert stamp == board._LAST_SCAN["at"]
    assert board.shared_scan_stamp(big) is None and board.shared_scan_stamp(big[:5]) is None


def test_shared_rows_preserve_source_start_instead_of_using_the_volume_midpoint(monkeypatch):
    import pandas as pd
    from types import SimpleNamespace
    stamp = pd.Timestamp("2026-10-30T11:25:00Z")
    frame = pd.DataFrame({"timestamp":[stamp],"open":[100.],"high":[101.],"low":[99.],"close":[100.],"volume":[1.]})
    fetched=[]
    def fetch(symbols, **kwargs):
        fetched.append(list(symbols))
        return SimpleNamespace(bars={s:frame for s in symbols},errors={})
    monkeypatch.setattr(board,"feed",SimpleNamespace(fetch_5m=fetch,fetch_30m=fetch,fetch_daily=fetch))
    monkeypatch.setattr(board,"PARALLEL_MIN_SYMBOLS",2)
    monkeypatch.setattr(board,"_pool",lambda:None)
    monkeypatch.setattr(board,"_earnings_map",lambda *a:{})
    monkeypatch.setattr(board,"_news_map",lambda *a:{})
    monkeypatch.setattr(board,"resolve_industries",lambda *a:{})
    monkeypatch.setattr(board,"_LAST_SCAN",{})
    monkeypatch.setattr(board,"_LAST_TAPES",{"5m":{},"daily":{}})
    def scan(item):
        symbol=item[0]
        return symbol, {"symbol":symbol,"pctChange":1.,"last":100.,"rvol":{"5m":{"barAt":1000}}}, 1., True, (), None, 100., False, ()
    monkeypatch.setattr(board,"_scan_one",scan)
    big=board.build_board(['AAA','BBB','CCC'],now=stamp.to_pydatetime())
    calls=len(fetched)
    small=board.build_board(['AAA','BBB'],now=stamp.to_pydatetime())
    assert len(fetched)==calls  # the smaller board borrowed; no fresh fetch
    for payload in (big,small):
        assert payload['rows']
        assert all(r['marketDataAt']==int(stamp.timestamp()) for r in payload['rows'])
        assert all(r['marketDataAt']!=r['rvol']['5m']['barAt'] for r in payload['rows'])


def test_a_list_does_not_rebuild_from_the_same_shared_scan(monkeypatch):
    """2026-10-16: Liquid Movers rebuilt 2-3 times per Watchlist scan."""
    from momx import service
    monkeypatch.setattr(service, "_STATES", {})
    monkeypatch.setattr(service, "_BORROWED_AT", {"Liquid Movers": 123.0})
    monkeypatch.setattr(service.board, "load_universe", lambda name=None, **k: ["A", "B"])
    monkeypatch.setattr(service, "_options_gate", lambda symbols, **kwargs: (symbols, None))
    monkeypatch.setattr(service.board, "shared_scan_stamp", lambda symbols: 123.0)
    built = []
    monkeypatch.setattr(service.board, "cached_board", lambda *a, **k: built.append(1) or {})
    service._state("Liquid Movers").payload = {"rows": []}
    assert service._build_once("Liquid Movers") == service.SKIPPED_BUILD and built == []


def _lender_setup(monkeypatch, borrowed_age, watchlist_building=False, watchlist_due_in=600.0):
    import tempfile
    import time
    from momx import service
    # A build that runs writes the board cache: never the real artifacts one.
    monkeypatch.setenv(service.BOARD_CACHE_DIR_ENV, tempfile.mkdtemp())
    monkeypatch.setattr(service, "_STATES", {})
    monkeypatch.setattr(service, "_BORROWED_AT", {"Liquid Movers": time.monotonic() - borrowed_age})
    sizes = {"Liquid Movers": ["A", "B"], "Watchlist": ["A", "B", "C"], "Mag7": ["A"]}
    monkeypatch.setattr(service.board, "load_universe", lambda name=None, **k: sizes[name])
    monkeypatch.setattr(service, "_options_gate", lambda symbols, **kwargs: (symbols, None))
    monkeypatch.setattr(service.board, "shared_scan_stamp", lambda symbols: None)   # borrowed scan aged out
    built = []
    monkeypatch.setattr(service.board, "cached_board", lambda *a, **k: built.append(1) or {"rows": []})
    service._state("Liquid Movers").payload = {"rows": []}
    service._state("Mag7").due_at = time.monotonic()                               # smaller: never a lender
    wl = service._state("Watchlist")
    wl.building = watchlist_building
    wl.due_at = time.monotonic() + watchlist_due_in
    return service, built


def test_borrower_waits_for_the_watchlist_instead_of_a_full_rebuild(monkeypatch):
    """2026-10-16: past the 90 s share window Liquid Movers scanned all 229
    itself (~80 s on the heavy gate) and the Watchlist waited behind it."""
    service, built = _lender_setup(monkeypatch, borrowed_age=100, watchlist_building=True)
    assert service._build_once("Liquid Movers") == service.SKIPPED_BUILD and built == []
    service, built = _lender_setup(monkeypatch, borrowed_age=100, watchlist_due_in=20)
    assert service._build_once("Liquid Movers") == service.SKIPPED_BUILD and built == []


def test_borrower_builds_itself_when_the_lender_is_not_coming(monkeypatch):
    service, built = _lender_setup(monkeypatch, borrowed_age=100, watchlist_due_in=600)
    assert service._build_once("Liquid Movers") != service.SKIPPED_BUILD and built == [1]
    service, built = _lender_setup(monkeypatch, borrowed_age=400, watchlist_building=True)  # lender stuck
    assert service._build_once("Liquid Movers") != service.SKIPPED_BUILD and built == [1]


def test_recent_tapes_serve_a_builds_bars_until_they_age_out(monkeypatch):
    """2026-10-16: the chart-arrow loop re-downloaded bars a build had just fetched."""
    board._remember_tapes({"AAA": "five-a", "BBB": "five-b"}, {"AAA": "day-a"})
    five, daily, missing = board.recent_tapes(["AAA", "BBB", "CCC"], 60.0)
    assert five == {"AAA": "five-a"} and daily == {"AAA": "day-a"} and missing == ["BBB", "CCC"]
    assert board.recent_tapes(["AAA"], -1.0)[2] == ["AAA"]


def test_chart_arrows_fetch_only_what_no_build_has(monkeypatch):
    from momx import chart_signals, feed

    class Result:
        def __init__(self, bars):
            self.bars = bars

    asked = []
    monkeypatch.setattr(feed, "fetch_5m", lambda symbols, **k: asked.append(("5m", list(symbols))) or Result({s: "net5" for s in symbols}))
    monkeypatch.setattr(feed, "fetch_daily", lambda symbols, **k: asked.append(("D", list(symbols))) or Result({s: "netD" for s in symbols}))
    board._remember_tapes({"AAA": "five-a"}, {"AAA": "day-a"})
    five, daily = chart_signals._default_fetch(["AAA", "ZZZ"], False)
    assert five == {"AAA": "five-a", "ZZZ": "net5"} and daily == {"AAA": "day-a", "ZZZ": "netD"}
    assert asked == [("5m", ["ZZZ"]), ("D", ["ZZZ"])]


def test_the_watchlist_idles_only_briefly_between_builds(monkeypatch):
    """2026-10-16: the 15 s+ idle was a quarter of his 1-minute target."""
    import time
    from momx import service
    monkeypatch.setattr(service, "_STATES", {})
    monkeypatch.setattr(service, "_is_heavy", lambda name: name != "Mag7")
    monkeypatch.setattr(service, "_yield_to_higher_priority", lambda name: None)
    monkeypatch.setattr(service, "_is_viewed", lambda name: True)
    monkeypatch.setattr(service, "_build_once", lambda name: 100.0)
    for name, most in (("Watchlist", service.PRIORITY_HEAVY_IDLE_SECONDS), ("Liquid Movers", 20.0), ("Mag7", 20.0)):
        service._build_claimed(name)
        idle = service._state(name).due_at - time.monotonic()
        assert idle <= most + 0.5, name
    assert service._state("Liquid Movers").due_at - time.monotonic() > 10     # others keep the old idle


def test_bear_only_matches_can_refresh_every_other_build(monkeypatch):
    from momx import service

    class State:
        payload = {"rows": [{"symbol": "BULL", "scanPass": True}, {"symbol": "BEAR", "scanPass": False}]}
        bear = {"rows": [{"symbol": "BEAR", "scanPass": True, "m5": {}}]}
        builds = 5

    monkeypatch.setattr(service, "BEAR_ONLY_FULL_ROWS_EVERY", 1)
    got = service._reuse_kwargs(State())
    assert got["full_rows_for"] == {"BULL", "BEAR"} and "reuse_bear_rows_for" not in got   # 1 = every build
    monkeypatch.setattr(service, "BEAR_ONLY_FULL_ROWS_EVERY", 2)
    got = service._reuse_kwargs(State())
    assert got["full_rows_for"] == {"BULL"} and got["reuse_bear_rows_for"] == {"BEAR"}      # odd build
    State.builds = 6
    assert service._reuse_kwargs(State())["full_rows_for"] == {"BULL", "BEAR"}


def test_a_still_bear_matching_row_keeps_its_columns_only_when_allowed(monkeypatch):
    from momx import board as b
    import sys
    sys.path.insert(0, "tests")
    from test_momx_board import _two_symbol_feed
    stale = {"symbol": "BBB", "pctChange": -99.0, "rvol": {"5m": {"value": 42.0}}, "last": 1.0}
    monkeypatch.setattr(b.scan, "scan_symbol",
                        lambda tapes, last=None, direction="bull", **k: (direction == "bear", ["x"]))
    kept = b.build_board(["AAA", "BBB"], feed_module=_two_symbol_feed(), full_rows_for=["AAA"],
                         reuse_rows={"BBB": stale}, reuse_bear_rows_for=["BBB"])
    fresh = b.build_board(["AAA", "BBB"], feed_module=_two_symbol_feed(), full_rows_for=["AAA"],
                          reuse_rows={"BBB": stale})
    row = lambda p: {r["symbol"]: r for r in p["rows"] + p["rest"]}["BBB"]
    assert row(kept)["rvol"]["5m"]["value"] == 42.0          # bear-only repeat match: reused
    assert row(kept)["pctChange"] != -99.0                    # price / %chg still this build's
    assert row(fresh)["rvol"]["5m"]["value"] != 42.0         # without the allowance: rebuilt
