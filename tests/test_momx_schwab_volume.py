"""Taking the live tail's VOLUME from Schwab instead of Alpaca's IEX slice.

WHY THIS EXISTS (measured live 2026-09-01)
    Alpaca SIP carries real consolidated volume but 403s on bars newer than
    ~20 minutes, so momx fills the tail from IEX -- one venue, ~2% of US
    volume. The tape therefore ended with bars holding a few percent of their
    real volume while RVOL divided them by an average of full SIP bars:

        NVDA 11:20   Alpaca 37,652   Schwab 691,459    5.4%
        NVDA 11:35   Alpaca 19,136   Schwab 620,889    3.1%

    Every short-timeframe RVOL sat near 0.0 permanently, so the momentum
    alert -- which watches exactly those timeframes -- fired ZERO times on a
    day CRML printed 23.4x.

THE DANGEROUS DIRECTION
    A volume correction that guesses is worse than none: it reaches the
    trader's phone as a real alert. So most of these tests pin the REFUSALS --
    the cases where the code must leave the tape alone rather than write a
    plausible number. No test here touches the network; the correction is a
    pure function over two frames.
"""

from __future__ import annotations

import pandas as pd
import pytest

from momx import feed


def frame(rows):
    """``rows`` = (iso timestamp, volume, feed-mark)."""
    return pd.DataFrame(
        {
            "timestamp": [pd.Timestamp(r[0]) for r in rows],
            "open": [1.0] * len(rows),
            "high": [1.0] * len(rows),
            "low": [1.0] * len(rows),
            "close": [1.0] * len(rows),
            "volume": [float(r[1]) for r in rows],
            "feed": [r[2] for r in rows],
        }
    )


def truth(pairs):
    """``{epoch: volume}`` keyed the way the correction keys it."""
    return {int(pd.Timestamp(t).timestamp()): float(v) for t, v in pairs}


SIP_BAR = ("2026-09-01T15:20:00Z", 500_000.0, "sip")
THIN_BAR = ("2026-09-01T15:25:00Z", 33_110.0, "iex")


# ---------------------------------------------------------------------------
# the correction doing its job
# ---------------------------------------------------------------------------

def test_the_thin_iex_tail_is_replaced_with_the_real_volume():
    out = feed._apply_schwab_volume(
        frame([SIP_BAR, THIN_BAR]),
        truth([("2026-09-01T15:25:00Z", 592_455.0)]),
    )
    assert list(out["volume"]) == [500_000.0, 592_455.0]
    # and the bar now says where that number came from
    assert list(out["feed"]) == ["sip", "schwab"]


def test_the_settled_sip_body_is_never_overwritten():
    # Two sources disagreeing by a few percent on settled bars would make the
    # tape jitter as each bar aged from one owner to the other.
    out = feed._apply_schwab_volume(
        frame([SIP_BAR, THIN_BAR]),
        truth([
            ("2026-09-01T15:20:00Z", 999_999.0),   # offered for the SIP bar
            ("2026-09-01T15:25:00Z", 592_455.0),
        ]),
    )
    assert out["volume"].iloc[0] == 500_000.0     # untouched
    assert out["feed"].iloc[0] == "sip"


def test_prices_are_never_touched_only_volume():
    original = frame([SIP_BAR, THIN_BAR])
    out = feed._apply_schwab_volume(
        original, truth([("2026-09-01T15:25:00Z", 592_455.0)])
    )
    for column in ("open", "high", "low", "close", "timestamp"):
        assert list(out[column]) == list(original[column])


# ---------------------------------------------------------------------------
# the refusals -- each one is a way to ship a wrong number
# ---------------------------------------------------------------------------

def test_a_bar_schwab_has_no_row_for_is_left_alone():
    # Halted or illiquid names must not get an invented volume.
    out = feed._apply_schwab_volume(
        frame([SIP_BAR, THIN_BAR]), truth([("2026-09-01T14:00:00Z", 1.0)])
    )
    assert out["volume"].iloc[1] == 33_110.0
    assert out["feed"].iloc[1] == "iex"


def test_a_smaller_replacement_is_rejected_as_a_mismatch():
    # IEX volume is a strict SUBSET of consolidated volume, so a smaller
    # "truth" means the two sides are not describing the same bar. Applying it
    # would understate exactly the spike this feature exists to catch.
    out = feed._apply_schwab_volume(
        frame([SIP_BAR, THIN_BAR]), truth([("2026-09-01T15:25:00Z", 12.0)])
    )
    assert out["volume"].iloc[1] == 33_110.0


def test_an_empty_correction_returns_the_very_same_object():
    original = frame([SIP_BAR, THIN_BAR])
    assert feed._apply_schwab_volume(original, {}) is original


def test_a_frame_with_no_feed_column_is_untouched():
    # Nothing was ever marked as the thin tail, so nothing may be replaced.
    bare = frame([SIP_BAR, THIN_BAR]).drop(columns=["feed"])
    assert feed._apply_schwab_volume(bare, truth([("2026-09-01T15:25:00Z", 9e6)])) is bare


def test_an_empty_frame_survives():
    empty = frame([])
    assert feed._apply_schwab_volume(empty, truth([("2026-09-01T15:25:00Z", 1.0)])) is empty


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -5.0])
def test_a_non_finite_or_negative_truth_is_ignored(bad):
    out = feed._apply_schwab_volume(
        frame([SIP_BAR, THIN_BAR]),
        {int(pd.Timestamp("2026-09-01T15:25:00Z").timestamp()): bad},
    )
    assert out["volume"].iloc[1] == 33_110.0


# ---------------------------------------------------------------------------
# the fetch half: it must never be able to break a build
# ---------------------------------------------------------------------------

class Boom:
    configured = True

    def _get_price_history(self, *a, **k):
        raise OSError("schwab unreachable")


class Empty:
    configured = True

    def _get_price_history(self, *a, **k):
        return pd.DataFrame()


class Fine:
    configured = True

    def __init__(self):
        self.calls = []

    def _get_price_history(self, symbol, timeframe, start, end):
        self.calls.append((symbol, timeframe))
        return frame([("2026-09-01T15:25:00Z", 592_455.0, "x")])


@pytest.fixture
def enabled(monkeypatch):
    # The suite disables the correction globally (tests/conftest.py); these
    # tests are the ones that want it on, with an injected client.
    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", True)


def test_a_raising_schwab_yields_no_correction_not_an_exception(enabled):
    assert feed._schwab_volume_map(["AAPL"], "5Min", None, client=Boom()) == {"AAPL": {}}


def test_an_empty_schwab_response_yields_no_correction(enabled):
    assert feed._schwab_volume_map(["AAPL"], "5Min", None, client=Empty()) == {"AAPL": {}}


def test_the_map_is_keyed_by_epoch_and_symbol(enabled):
    got = feed._schwab_volume_map(["AAPL"], "5Min", None, client=Fine())
    assert got == {
        "AAPL": {int(pd.Timestamp("2026-09-01T15:25:00Z").timestamp()): 592_455.0}
    }


def test_the_daily_tape_is_never_corrected(enabled):
    # Daily bars are settled; there is no thin live tail to fix, and asking
    # would burn calls for nothing.
    client = Fine()
    assert feed._schwab_volume_map(["AAPL"], "1Day", None, client=client) == {}
    assert client.calls == []


def test_the_master_switch_stops_all_network_work(monkeypatch):
    monkeypatch.setattr(feed, "SCHWAB_VOLUME_ENABLED", False)
    client = Fine()
    assert feed._schwab_volume_map(["AAPL"], "5Min", None, client=client) == {}
    assert client.calls == []


def test_no_symbols_means_no_calls(enabled):
    client = Fine()
    assert feed._schwab_volume_map([], "5Min", None, client=client) == {}
    assert client.calls == []
