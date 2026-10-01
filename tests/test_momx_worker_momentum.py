"""The worker's momentum ledger: pruned to the list, and clearable.

2026-09-01: he pasted a one-ticker watchlist over a ten-ticker one and the
strip kept ten chips for the tickers he had removed. The ledger held them
(cap 50, newest first) and nothing ever asked whether they were still in
the list. These pin the two behaviours that fixed it:

* a read after the list changed drops the chips for tickers that left --
  including while the new list is still warming, because the placeholder
  carries the new universe;
* POST /momentum/clear empties the strip for one list without resetting
  the diff baseline, so the next build reports changes, not a replay.

service.snapshot is stubbed; no board build, no network.
"""

from __future__ import annotations

import momx_worker
from momx import service

NOW = "2026-09-01T14:31:02+00:00"


def payload(universe, matched, *, stamp="2026-09-01T14:31:00+00:00", warming=False):
    rows = [{"symbol": s, "scanPass": s in matched, "pctChange": 1.0} for s in universe] if not warming else []
    return {
        "generatedAt": None if warming else stamp,
        "list": "Watchlist",
        "universe": list(universe),
        "universeCount": len(universe),
        "rows": rows,
        "errors": {},
        "warming": warming,
    }


def _serve(monkeypatch, snapshot):
    monkeypatch.setattr(service, "snapshot", lambda list_name=None, direction="bull": snapshot)


def _seed(monkeypatch):
    """Two builds: ten tickers, then the same ten with two newly matched."""
    momx_worker._LEDGERS.clear()
    universe = ["ULTA", "SPOT", "KSS", "INSM", "URBN", "CNTR", "CBOE", "EBAY", "NVDA", "TSLA"]
    _serve(monkeypatch, payload(universe, {"ULTA", "SPOT"}, stamp="t1"))
    momx_worker._momentum("Watchlist")
    _serve(monkeypatch, payload(universe, {"ULTA", "SPOT", "KSS", "INSM"}, stamp="t2"))
    events = momx_worker._momentum("Watchlist")["events"]
    assert sorted(e["symbol"] for e in events) == ["INSM", "KSS"]
    return universe


def test_pasting_a_smaller_list_drops_the_removed_tickers_chips(monkeypatch):
    _seed(monkeypatch)
    # The paste: the snapshot is now a WARMING placeholder for ["NVDA"].
    _serve(monkeypatch, payload(["NVDA"], set(), warming=True))
    out = momx_worker._momentum("Watchlist")
    assert out["events"] == []            # cleared at once, not after the rebuild
    # The rebuild lands: NVDA matches. One NEW chip, and NO LOST chips for
    # the nine tickers he removed.
    _serve(monkeypatch, payload(["NVDA"], {"NVDA"}, stamp="t3"))
    out = momx_worker._momentum("Watchlist")
    assert [(e["type"], e["symbol"]) for e in out["events"]] == [("new_match", "NVDA")]


def test_ticker_still_in_list_but_off_the_scan_is_still_a_loss(monkeypatch):
    universe = _seed(monkeypatch)
    _serve(monkeypatch, payload(universe, {"ULTA", "SPOT", "KSS"}, stamp="t3"))
    out = momx_worker._momentum("Watchlist")
    assert ("lost_match", "INSM") in [(e["type"], e["symbol"]) for e in out["events"]]


def test_clear_empties_the_strip_but_keeps_the_baseline(monkeypatch):
    universe = _seed(monkeypatch)
    out = momx_worker._clear_momentum("Watchlist")
    assert out["events"] == [] and out["cleared"] is True
    assert momx_worker._momentum("Watchlist")["events"] == []
    # Same build again: nothing replays.
    assert momx_worker._momentum("Watchlist")["events"] == []
    # A genuinely new build after the clear reports only what changed since.
    _serve(monkeypatch, payload(universe, {"ULTA", "SPOT", "KSS", "INSM", "CBOE"}, stamp="t3"))
    out = momx_worker._momentum("Watchlist")
    assert [(e["type"], e["symbol"]) for e in out["events"]] == [("new_match", "CBOE")]


def test_clear_on_an_unknown_list_is_harmless(monkeypatch):
    momx_worker._LEDGERS.clear()
    _serve(monkeypatch, payload(["NVDA"], set(), warming=True))
    out = momx_worker._clear_momentum("Watchlist")
    assert out["events"] == [] and out["list"] == "Watchlist"


# ------------------------------------------------------------- BEAR (spec 2026-09-24)

def test_bear_momentum_ledger_is_separate_from_the_bull_one(monkeypatch):
    momx_worker._LEDGERS.clear()
    universe = ["ULTA", "SPOT", "KSS"]
    boards = {"bull": payload(universe, {"ULTA"}, stamp="t1"), "bear": dict(payload(universe, {"KSS"}, stamp="t1"), direction="bear")}
    monkeypatch.setattr(service, "snapshot", lambda list_name=None, direction="bull": boards["bear" if direction == "bear" else "bull"])
    momx_worker._momentum("Watchlist")
    momx_worker._momentum("Watchlist", "bear")
    boards["bull"] = payload(universe, {"ULTA", "SPOT"}, stamp="t2")
    boards["bear"] = dict(payload(universe, {"KSS", "ULTA"}, stamp="t2"), direction="bear")
    bull = momx_worker._momentum("Watchlist")
    bear = momx_worker._momentum("Watchlist", "bear")
    assert [e["symbol"] for e in bull["events"]] == ["SPOT"] and bull["direction"] == "bull"
    assert [e["symbol"] for e in bear["events"]] == ["ULTA"] and bear["direction"] == "bear"
    assert set(momx_worker._LEDGERS) == {"Watchlist", "Watchlist|bear"}
    momx_worker._clear_momentum("Watchlist")
    assert momx_worker._LEDGERS["Watchlist"]["events"] == [] and momx_worker._LEDGERS["Watchlist|bear"]["events"] == []


def test_direction_query_reads_dir_bear_only():
    assert momx_worker._direction({"dir": ["bear"]}) == "bear"
    assert momx_worker._direction({"dir": ["BEAR"]}) == "bear"
    assert momx_worker._direction({"dir": ["bull"]}) == "bull"
    assert momx_worker._direction({}) == "bull"
