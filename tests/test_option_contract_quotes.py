"""api_server.option_contract_quotes - the MomX bot's entry at the signal (2026-10-08)."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta

import api_server
from api_server import DashboardState


class FakeClient:
    def __init__(self, quotes):
        self.quotes, self.asked = quotes, []

    def get_quotes(self, symbols):
        self.asked.append(list(symbols))
        return {s: self.quotes[s] for s in symbols if s in self.quotes}


def _state(rows):
    s = DashboardState.__new__(DashboardState)
    s.oi_finder_lock = threading.RLock()
    s.oi_finder_chain_cache = {"X": (datetime.now(), {"underlyingPrice": 90.0, "scannedAt": "old",
                                                       "selectedExpiryChainRows": rows})}
    return s


def test_contract_re_anchored_on_live_spot_and_priced_from_the_quote(monkeypatch):
    today = date.today()
    exp = (today + timedelta(days=3)).isoformat()
    old = (today - timedelta(days=1)).isoformat()
    rows = [{"symbol": f"X_C{k}", "side": "CALL", "strike": k, "expiry": exp, "bid": 0.5, "ask": 0.6, "delta": 0.3}
            for k in (95.0, 101.0, 105.0)]
    rows.append({"symbol": "X_OLD", "side": "CALL", "strike": 101.0, "expiry": old, "bid": 1, "ask": 1, "delta": 0.3})
    client = FakeClient({"X_C101.0": {"bid": 2.0, "ask": 2.2}})
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [client])
    out = _state(rows).option_contract_quotes([{"symbol": "X", "bear": False, "spot": 100.0}])["results"][0]
    assert out["ok"] is True
    assert out["pick"]["contract"] == "X_C101.0"      # nearest OTM above the LIVE 100, not the held 90
    assert out["pick"]["entry"] == 2.1 and out["pick"]["spot"] == 100.0
    assert "X_OLD" not in sum(client.asked, [])


def test_no_quote_or_no_chain_is_not_ok(monkeypatch):
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [FakeClient({})])
    s = _state([])
    s._load_oi_finder_chain_disk_payload = lambda sym: None
    res = s.option_contract_quotes([{"symbol": "X", "bear": False, "spot": 100.0},
                                    {"symbol": "Y", "bear": False, "spot": 10.0}])["results"]
    assert [r["ok"] for r in res] == [False, False]
