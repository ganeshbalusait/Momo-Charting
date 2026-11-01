"""/api/option-quotes (2026-10-10): the bot's 15:50 close prices. Mid = live bid/ask
only - never a stale last trade; a client's gaps are filled from the next client."""
import api_server


class _Client:
    def __init__(self, data, boom=False):
        self.data, self.boom, self.asked = data, boom, []

    def get_quotes(self, symbols):
        self.asked.append(list(symbols))
        if self.boom:
            raise RuntimeError("down")
        return {s: self.data[s] for s in symbols if s in self.data}


def _state():
    return api_server.DashboardState.__new__(api_server.DashboardState)


def test_mid_is_live_bid_ask_only_and_gaps_use_the_next_client(monkeypatch):
    first = _Client({"A": {"bid": 1.0, "ask": 1.2, "last_price": 9.0},
                     "STALE": {"bid": 0.0, "ask": 0.0, "last_price": 4.0}})
    second = _Client({"B": {"bid": 0.0, "ask": 0.1}})
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [first, second])
    out = _state().option_quotes(["A", "B", "STALE"])["quotes"]
    assert out["A"]["mid"] == 1.1
    assert out["B"]["mid"] == 0.05 and out["B"]["bid"] == 0.0
    assert "STALE" not in out
    assert second.asked == [["B"]]  # only what the first client did not answer


def test_a_failing_client_falls_through(monkeypatch):
    monkeypatch.setattr(api_server, "_schwab_market_clients",
                        lambda: [_Client({}, boom=True), _Client({"A": {"bid": 2.0, "ask": 2.0}})])
    assert _state().option_quotes(["A"])["quotes"]["A"]["mid"] == 2.0
