from __future__ import annotations

"""A user's own Alpaca key, actually used for their prices.

The MY DATA PROVIDER KEYS card has always saved an Alpaca key to the user's
encrypted vault row and reported it configured - and nothing has ever read it.
Both credential lookups in api_server pick "first active admin", so every
user's data came off the owner's key while their own sat unused. Unhiding that
card without this would just restore a decoy.

Mirrors user_schwab deliberately: same cache key shape, same never-shared
invariant, same no-silent-fallback rule.
"""

import threading
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import pytest

from user_alpaca import ALPACA_PROVIDER, UserAlpacaClients, last_prices, previous_closes


class FakeVault:
    def __init__(self, rows=None):
        self.rows = dict(rows or {})

    def __call__(self, user_id, provider):
        return dict(self.rows.get((user_id, provider), {}))


class FakeClient:
    def __init__(self, key, secret):
        self.key = key
        self.secret = secret


ALICE = {"id": "alice", "role": "user", "isAdmin": False}
BOB = {"id": "bob", "role": "user", "isAdmin": False}
KEYS_A = {"key_id": "AKID_ALICE", "secret_key": "SECRET_ALICE"}
KEYS_B = {"key_id": "AKID_BOB", "secret_key": "SECRET_BOB"}


def _registry(vault):
    built = []

    def factory(key, secret):
        built.append((key, secret))
        return FakeClient(key, secret)

    registry = UserAlpacaClients(credentials_reader=vault, client_factory=factory)
    registry.built = built
    return registry


# --------------------------------------------------------------------------
# Whose key
# --------------------------------------------------------------------------


def test_the_provider_slot_is_the_one_the_vault_already_uses():
    assert ALPACA_PROVIDER == "alpaca_market_data"


def test_a_user_without_a_key_gets_no_client():
    registry = _registry(FakeVault())

    assert registry.for_user(ALICE) is None
    assert registry.built == []


@pytest.mark.parametrize("partial", [
    {"key_id": "AKID"},
    {"secret_key": "SECRET"},
    {"key_id": "", "secret_key": "SECRET"},
    {"key_id": "AKID", "secret_key": "   "},
])
def test_a_half_filled_key_is_not_a_client(partial):
    registry = _registry(FakeVault({("alice", ALPACA_PROVIDER): partial}))

    assert registry.for_user(ALICE) is None


def test_the_client_is_built_from_the_users_own_key():
    registry = _registry(FakeVault({("alice", ALPACA_PROVIDER): KEYS_A}))

    client = registry.for_user(ALICE)

    assert client.key == "AKID_ALICE"
    assert client.secret == "SECRET_ALICE"


def test_a_client_is_never_shared_between_users():
    registry = _registry(FakeVault({
        ("alice", ALPACA_PROVIDER): KEYS_A,
        ("bob", ALPACA_PROVIDER): KEYS_B,
    }))

    assert registry.for_user(ALICE).key == "AKID_ALICE"
    assert registry.for_user(BOB).key == "AKID_BOB"
    assert registry.for_user(ALICE) is not registry.for_user(BOB)


# --------------------------------------------------------------------------
# Caching
# --------------------------------------------------------------------------


def test_the_client_is_reused_rather_than_rebuilt_per_request():
    registry = _registry(FakeVault({("alice", ALPACA_PROVIDER): KEYS_A}))

    first = registry.for_user(ALICE)
    for _ in range(20):
        assert registry.for_user(ALICE) is first

    assert len(registry.built) == 1


def test_saving_a_new_key_invalidates_the_cached_client():
    vault = FakeVault({("alice", ALPACA_PROVIDER): KEYS_A})
    registry = _registry(vault)
    assert registry.for_user(ALICE).key == "AKID_ALICE"

    vault.rows[("alice", ALPACA_PROVIDER)] = {"key_id": "NEW", "secret_key": "NEWSEC"}
    registry.invalidate("alice")

    assert registry.for_user(ALICE).key == "NEW"


def test_invalidating_one_user_leaves_others_alone():
    registry = _registry(FakeVault({
        ("alice", ALPACA_PROVIDER): KEYS_A,
        ("bob", ALPACA_PROVIDER): KEYS_B,
    }))
    bob = registry.for_user(BOB)

    registry.invalidate("alice")

    assert registry.for_user(BOB) is bob


def test_concurrent_first_requests_build_one_client():
    registry = _registry(FakeVault({("alice", ALPACA_PROVIDER): KEYS_A}))
    start = threading.Event()

    def grab():
        start.wait(5)
        registry.for_user(ALICE)

    threads = [threading.Thread(target=grab, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(10)

    assert len(registry.built) == 1


def test_a_broken_vault_read_is_not_a_crash():
    def explode(user_id, provider):
        raise RuntimeError("database is locked")

    assert _registry(explode).for_user(ALICE) is None


def test_a_user_with_no_id_gets_nothing():
    registry = _registry(FakeVault())

    for nobody in [None, {}, {"role": "user"}]:
        assert registry.for_user(nobody) is None


# --------------------------------------------------------------------------
# Turning Alpaca's shape into the one the quote path already speaks
# --------------------------------------------------------------------------


class FakeTrade:
    def __init__(self, price):
        self.price = price


def test_last_prices_are_returned_in_the_shape_the_quote_path_expects():
    class Client:
        def get_stock_latest_trade(self, request):
            return {"AAPL": FakeTrade(312.85), "MSFT": FakeTrade(511.20)}

    assert last_prices(Client(), ["AAPL", "MSFT"]) == {
        "AAPL": {"last_price": 312.85},
        "MSFT": {"last_price": 511.20},
    }


def test_a_symbol_alpaca_does_not_answer_for_is_simply_absent():
    class Client:
        def get_stock_latest_trade(self, request):
            return {"AAPL": FakeTrade(312.85)}

    assert last_prices(Client(), ["AAPL", "NOSUCH"]) == {"AAPL": {"last_price": 312.85}}


def test_a_failing_alpaca_call_returns_nothing_rather_than_raising():
    class Client:
        def get_stock_latest_trade(self, request):
            raise RuntimeError("alpaca is down")

    assert last_prices(Client(), ["AAPL"]) == {}


def test_no_client_and_no_symbols_are_both_handled():
    assert last_prices(None, ["AAPL"]) == {}

    class Client:
        def get_stock_latest_trade(self, request):  # pragma: no cover - must not be called
            raise AssertionError("should not have been called")

    assert last_prices(Client(), []) == {}


def test_a_trade_without_a_usable_price_is_dropped():
    class Client:
        def get_stock_latest_trade(self, request):
            return {"AAPL": FakeTrade(None), "MSFT": FakeTrade(0), "NVDA": FakeTrade(209.9)}

    # A zero or missing price is not a price; passing it through would paint a
    # flat line at zero on the chart rather than showing no data.
    assert last_prices(Client(), ["AAPL", "MSFT", "NVDA"]) == {"NVDA": {"last_price": 209.9}}


# --------------------------------------------------------------------------
# previous_closes
#
# Added by another session for the quick-ticker percentage rails, which need a
# prior close to compute a day move. Tests written after the fact, by the owner
# of this module: an untested branch on the live price path is how a bad number
# reaches a chart.
# --------------------------------------------------------------------------


class FakeBar:
    """One daily bar. `timestamp` is the SESSION date, as alpaca-py returns."""

    def __init__(self, close, session):
        self.close = close
        self.timestamp = datetime.combine(session, dt_time(0, 0))


class FakeBars:
    def __init__(self, data):
        self.data = data


def _today_et():
    return datetime.now(ZoneInfo("America/New_York")).date()


def _sessions(*offsets):
    """Session dates counted back from today in market time."""
    today = _today_et()
    return [today - timedelta(days=n) for n in offsets]


def test_the_prior_close_is_returned_per_symbol():
    yesterday, before = _sessions(1, 2)

    class Client:
        def get_stock_bars(self, request):
            return FakeBars({
                "AAPL": [FakeBar(300.00, before), FakeBar(310.40, yesterday)],
                "MSFT": [FakeBar(500.00, before), FakeBar(508.15, yesterday)],
            })

    assert previous_closes(Client(), ["AAPL", "MSFT"]) == {"AAPL": 310.40, "MSFT": 508.15}


def test_todays_partial_bar_is_never_used_as_the_prior_close():
    """THE REGRESSION.

    The first implementation read the snapshot's `previous_daily_bar`, which
    resolves a different session boundary during extended hours. Measured
    2026-08-27 08:20 ET against Schwab's close_price for the same instant:
    snapshot said AAPL 309.895, daily bars and Schwab both said 313.45. The
    rail rendered AAPL +0.16% GREEN on a stock that was -1.08% red.

    A bar for the CURRENT session exists during regular hours and would compare
    a stock against itself. Only a completed session counts.
    """
    today = _today_et()
    yesterday = today - timedelta(days=1)

    class Client:
        def get_stock_bars(self, request):
            return FakeBars({"AAPL": [FakeBar(313.45, yesterday), FakeBar(309.89, today)]})

    assert previous_closes(Client(), ["AAPL"]) == {"AAPL": 313.45}


def test_the_newest_completed_session_wins_regardless_of_row_order():
    older, newer = _sessions(4, 1)

    class Client:
        def get_stock_bars(self, request):
            # Deliberately out of order: correctness must not rest on the
            # provider returning bars sorted.
            return FakeBars({"AAPL": [FakeBar(310.40, newer), FakeBar(280.00, older)]})

    assert previous_closes(Client(), ["AAPL"]) == {"AAPL": 310.40}


def test_a_long_weekend_still_resolves_to_the_last_traded_session():
    stale, = _sessions(4)

    class Client:
        def get_stock_bars(self, request):
            return FakeBars({"AAPL": [FakeBar(310.40, stale)]})

    assert previous_closes(Client(), ["AAPL"]) == {"AAPL": 310.40}


def test_a_symbol_the_response_omits_is_simply_absent():
    yesterday, = _sessions(1)

    class Client:
        def get_stock_bars(self, request):
            return FakeBars({"AAPL": [FakeBar(310.40, yesterday)]})

    assert previous_closes(Client(), ["AAPL", "NOSUCH"]) == {"AAPL": 310.40}


@pytest.mark.parametrize("bad", [0, -1.0, None])
def test_a_close_that_cannot_produce_a_percentage_is_dropped(bad):
    """Zero is not a close.

    A day move divides by this. Letting zero through is a division by zero or
    an infinite percentage painted onto the ticker rail; letting a negative
    through is a nonsense move. Absent is the honest answer.
    """
    yesterday, = _sessions(1)

    class Client:
        def get_stock_bars(self, request):
            return FakeBars({
                "AAPL": [FakeBar(bad, yesterday)],
                "NVDA": [FakeBar(207.10, yesterday)],
            })

    assert previous_closes(Client(), ["AAPL", "NVDA"]) == {"NVDA": 207.10}


def test_a_symbol_with_no_completed_session_at_all_is_dropped():
    class Client:
        def get_stock_bars(self, request):
            return FakeBars({"AAPL": []})

    assert previous_closes(Client(), ["AAPL"]) == {}


def test_a_failing_bars_call_returns_nothing_rather_than_raising():
    class Client:
        def get_stock_bars(self, request):
            raise RuntimeError("alpaca is down")

    assert previous_closes(Client(), ["AAPL"]) == {}


def test_no_client_and_no_symbols_are_both_handled():
    assert previous_closes(None, ["AAPL"]) == {}

    class Client:
        def get_stock_bars(self, request):  # pragma: no cover - must not be called
            raise AssertionError("should not have been called")

    assert previous_closes(Client(), []) == {}


def test_symbols_are_normalised_before_the_lookup():
    """A rail may pass "aapl"; the response is keyed "AAPL".

    Without this the caller silently gets no close for that symbol, and the
    percentage badge is simply absent with nothing explaining why.
    """
    yesterday, = _sessions(1)

    class Client:
        def get_stock_bars(self, request):
            return FakeBars({"AAPL": [FakeBar(310.40, yesterday)]})

    assert previous_closes(Client(), ["aapl", " msft "]) == {"AAPL": 310.40}
