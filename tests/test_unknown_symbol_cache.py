"""A ticker the broker does not recognise must stop being rebuilt.

WHY THIS EXISTS (2026-09-01, a live outage)
    A saved chart panel held the typo GOOGLE. It passes the symbol-FORMAT
    check because it looks like a ticker; the broker simply has no such
    security. The build failed, nothing remembered, and the panel asked again:
    715 rebuilds, one of them 46.96s. A full build holds the GIL, so
    /api/auth/status stalled 15-19s and the trader watched "Loading secure
    workspace" during market hours. Fixing the data was not durable -- another
    browser re-saved GOOGLE the same afternoon.

THE FAILURE MODE THESE TESTS GUARD AGAINST
    The naive fix, "no bars -> blacklist", is far more dangerous than the bug.
    During a broker outage EVERY symbol returns no bars, so it would blacklist
    the whole universe and keep charts dark long after the broker recovered --
    turning a five-minute outage into an all-day one. So the check asks about
    the suspect symbol ALONGSIDE a known-good control in the same request, and
    refuses to conclude anything unless the control answers.

    Most of what follows tests that REFUSAL, not the happy path.
"""

from __future__ import annotations

import time

import pytest

import api_server


class Broker:
    """A Schwab-shaped quote client. ``known`` is what the broker recognises."""

    configured = True

    def __init__(self, known, *, raises=False):
        self.known = set(known)
        self.raises = raises
        self.calls = []

    def get_quotes(self, symbols, **kwargs):
        self.calls.append(list(symbols))
        if self.raises:
            raise OSError("schwab unreachable")
        return {s: {"last_price": 1.0} for s in symbols if s in self.known}


@pytest.fixture
def state(monkeypatch):
    """A DashboardState we can call the guard on, without booting the app."""
    obj = object.__new__(api_server.DashboardState)
    api_server._UNKNOWN_CHART_SYMBOLS.clear()
    yield obj
    api_server._UNKNOWN_CHART_SYMBOLS.clear()


def use(monkeypatch, broker):
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [broker])


# ---------------------------------------------------------------------------
# the discriminator
# ---------------------------------------------------------------------------

def test_a_symbol_the_broker_omits_is_reported_unknown(monkeypatch, state):
    use(monkeypatch, Broker({"SPY", "GOOGL", "AAPL"}))
    assert state._symbol_unknown_to_broker("GOOGLE") is True


def test_a_symbol_the_broker_returns_is_reported_known(monkeypatch, state):
    use(monkeypatch, Broker({"SPY", "GOOGL"}))
    assert state._symbol_unknown_to_broker("GOOGL") is False


def test_the_control_symbol_rides_in_the_same_request(monkeypatch, state):
    # Two separate requests could straddle an outage and disagree; one request
    # is what makes the control meaningful.
    broker = Broker({"SPY", "AAPL"})
    use(monkeypatch, broker)
    state._symbol_unknown_to_broker("NOPE")
    assert broker.calls == [["NOPE", api_server.DashboardState.UNKNOWN_SYMBOL_CONTROL]]


# ---------------------------------------------------------------------------
# the refusals -- the outage-safety story
# ---------------------------------------------------------------------------

def test_a_broker_outage_concludes_NOTHING(monkeypatch, state):
    """The control is missing too, so the suspect cannot be judged.

    This is the test that matters. Returning True here would blacklist every
    symbol on the board during an outage.
    """
    use(monkeypatch, Broker(set()))          # broker answers, knows nothing
    assert state._symbol_unknown_to_broker("AAPL") is None


def test_a_raising_broker_concludes_nothing(monkeypatch, state):
    use(monkeypatch, Broker({"SPY"}, raises=True))
    assert state._symbol_unknown_to_broker("GOOGLE") is None


def test_no_configured_client_concludes_nothing(monkeypatch, state):
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [])
    assert state._symbol_unknown_to_broker("GOOGLE") is None


def test_an_unusable_response_concludes_nothing(monkeypatch, state):
    class Weird(Broker):
        def get_quotes(self, symbols, **kwargs):
            return None

    use(monkeypatch, Weird({"SPY"}))
    assert state._symbol_unknown_to_broker("GOOGLE") is None


# ---------------------------------------------------------------------------
# the cache the guard keeps
# ---------------------------------------------------------------------------

def test_a_known_bad_symbol_short_circuits_without_building(monkeypatch, state):
    built = []
    monkeypatch.setattr(
        api_server.DashboardState, "_build_oi_finder_chart_payload_impl",
        lambda self, symbol, **k: built.append(symbol) or {"bars": [], "dailyBars": []},
    )
    use(monkeypatch, Broker({"SPY", "AAPL"}))

    first = state._build_oi_finder_chart_payload("GOOGLE")
    assert built == ["GOOGLE"], "the first attempt should still build once"
    assert "not a ticker" in (first.get("error") or "")

    second = state._build_oi_finder_chart_payload("GOOGLE")
    assert built == ["GOOGLE"], "the second attempt must NOT rebuild"
    assert "not a ticker" in (second.get("error") or "")


def test_an_outage_does_not_poison_the_cache(monkeypatch, state):
    """An empty build during an outage must leave the symbol re-tryable."""
    calls = []
    monkeypatch.setattr(
        api_server.DashboardState, "_build_oi_finder_chart_payload_impl",
        lambda self, symbol, **k: calls.append(symbol) or {"bars": [], "dailyBars": []},
    )
    use(monkeypatch, Broker(set()))          # control missing -> outage
    state._build_oi_finder_chart_payload("AAPL")
    state._build_oi_finder_chart_payload("AAPL")
    assert calls == ["AAPL", "AAPL"], "AAPL was blacklisted during an outage"
    assert "AAPL" not in api_server._UNKNOWN_CHART_SYMBOLS


def test_a_working_symbol_is_never_cached_and_costs_no_extra_request(
    monkeypatch, state
):
    broker = Broker({"SPY", "AAPL"})
    use(monkeypatch, broker)
    monkeypatch.setattr(
        api_server.DashboardState, "_build_oi_finder_chart_payload_impl",
        lambda self, symbol, **k: {"bars": [{"t": 1}], "dailyBars": []},
    )
    out = state._build_oi_finder_chart_payload("AAPL")
    assert out["bars"], "a healthy build must pass straight through"
    assert broker.calls == [], "a healthy chart must not pay for a quote lookup"
    assert "AAPL" not in api_server._UNKNOWN_CHART_SYMBOLS


def test_a_symbol_that_starts_working_is_forgotten(monkeypatch, state):
    # A new listing, or a broker that was briefly confused, must recover.
    api_server._UNKNOWN_CHART_SYMBOLS["NEWCO"] = time.time()
    use(monkeypatch, Broker({"SPY", "NEWCO"}))
    monkeypatch.setattr(
        api_server.DashboardState, "_build_oi_finder_chart_payload_impl",
        lambda self, symbol, **k: {"bars": [{"t": 1}], "dailyBars": []},
    )
    # Expire the entry so the guard re-tries rather than short-circuiting.
    api_server._UNKNOWN_CHART_SYMBOLS["NEWCO"] = (
        time.time() - api_server.DashboardState.UNKNOWN_SYMBOL_TTL_SECONDS - 1
    )
    out = state._build_oi_finder_chart_payload("NEWCO")
    assert out["bars"]
    assert "NEWCO" not in api_server._UNKNOWN_CHART_SYMBOLS


def test_the_entry_expires_so_a_new_listing_starts_working(monkeypatch, state):
    built = []
    monkeypatch.setattr(
        api_server.DashboardState, "_build_oi_finder_chart_payload_impl",
        lambda self, symbol, **k: built.append(symbol) or {"bars": [], "dailyBars": []},
    )
    use(monkeypatch, Broker({"SPY"}))
    api_server._UNKNOWN_CHART_SYMBOLS["LATER"] = (
        time.time() - api_server.DashboardState.UNKNOWN_SYMBOL_TTL_SECONDS - 1
    )
    state._build_oi_finder_chart_payload("LATER")
    assert built == ["LATER"], "an expired entry must allow another attempt"
