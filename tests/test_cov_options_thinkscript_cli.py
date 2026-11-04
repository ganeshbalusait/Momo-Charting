"""Unit: scripts/generate_oi_thinkscript.py - the command-line OI ThinkScript
generator. Pure functions on hand-built chains; the provider walk runs against
fake Tradier/Schwab client classes, never a real one."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_oi_thinkscript.py"
_SPEC = importlib.util.spec_from_file_location("generate_oi_thinkscript_cov", SCRIPT)
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)


def row(side, strike, oi, expiry="2026-11-20", dte=16, volume=100, mark=1.0):
    return {"side": side, "expiry": expiry, "dte": dte, "strike": float(strike), "oi": float(oi),
            "volume": float(volume), "mark": mark}


def test_top_levels_rank_by_oi_keep_the_dominant_expiry_and_cap():
    rows = [row("CALL", 200 + i, 1000 * (20 - i)) for i in range(12)]
    rows.append(row("CALL", 201, 50_000, expiry="2026-11-13", dte=9))     # 201's dominant expiry
    rows.append(row("PUT", 190, 99_999))                                   # other side ignored
    levels = gen.top_levels(rows, "CALL", top=9)
    assert len(levels) == 9
    assert [level["strike"] for level in levels][:3] == [201.0, 200.0, 202.0]
    assert levels[0]["expiry"] == "2026-11-13" and levels[0]["oi"] == 50_000
    assert [level["tier"] for level in levels] == [5, 5, 5, 4, 4, 4, 3, 3, 3]
    assert len(gen.top_levels(rows, "CALL", top=99)) == 12              # never past the 15-plot bound
    assert gen.top_levels([row("CALL", 200, 0)], "CALL") == []           # zero OI is not a wall


@pytest.mark.xfail(strict=True, reason=(
    "The generator still sets tiers by RANK (top 3 = tier 5) while the chart "
    "(oiChartLevels.js / highOiImportance) and the alert ladder moved to the OI-ratio rule "
    "(breaks 0.125/0.225/0.35/0.50): a wall holding 10% of the leader's OI draws bold in the "
    "TOS study but thin on the app chart. The script's docstring still claims parity."))
def test_tiers_follow_the_charts_oi_ratio_rule():
    levels = gen.top_levels([row("CALL", 210, 10_000), row("CALL", 215, 9_000), row("CALL", 220, 1_000)], "CALL")
    assert [level["tier"] for level in levels] == [5, 5, 1]


def test_expected_move_is_the_nearest_expiry_atm_straddle():
    rows = [
        row("CALL", 100, 1, expiry="2026-11-06", dte=2, mark=2.1),
        row("PUT", 100, 1, expiry="2026-11-06", dte=2, mark=1.9),
        row("CALL", 105, 1, expiry="2026-11-06", dte=2, mark=0.6),
        row("CALL", 100, 1, expiry="2026-11-13", dte=9, mark=3.5),       # later expiry: not used
        row("PUT", 100, 1, expiry="2026-11-13", dte=9, mark=3.4),
    ]
    assert gen.expected_move(rows, 101.0) == (105.0, 97.0)
    assert gen.expected_move(rows, 0) == (0.0, 0.0)
    assert gen.expected_move([row("CALL", 100, 1)], 100.0) == (0.0, 0.0)  # no straddle: no EM lines


def test_momox_reference_levels_are_the_biggest_walls_of_the_two_heaviest_expiries():
    rows = [
        row("CALL", 120, 88_000, expiry="2026-08-14"), row("PUT", 90, 21_000, expiry="2026-08-14"),
        row("CALL", 115, 5_000, expiry="2026-08-14"),
        row("CALL", 125, 40_000, expiry="2026-09-18"), row("PUT", 85, 60_000, expiry="2026-09-18"),
        row("CALL", 130, 900, expiry="2026-08-21"),                         # light expiry: skipped
    ]
    levels = gen.momox_reference_levels(rows)
    assert [(level["side"], level["strike"], level["label"]) for level in levels] == [
        ("CALL", 120.0, "88k 8/14"), ("PUT", 90.0, "21k 8/14"),
        ("CALL", 125.0, "40k 9/18"), ("PUT", 85.0, "60k 9/18"),
    ]


def test_oi_number_formats():
    assert [gen.compact_oi(value) for value in (88_000, 12_400, 1_500, 1_000, 950)] == ["88k", "12k", "1.5k", "1k", "950"]
    assert [gen.fmt_oi_bubble(value) for value in (88_400, 2_000, 0)] == ["88.4", "2", "0"]
    assert [gen.fmt_price(value) for value in (230.0, 232.5, 0.125)] == ["230", "232.5", "0.12"]


def ticker(symbol, call, put, em=(0.0, 0.0)):
    return {"symbol": symbol, "provider": "tradier", "spot": 100.0,
            "calls": [{"strike": call, "oi": 50_000, "volume": 1, "expiry": "2026-11-20", "tier": 5}],
            "puts": [{"strike": put, "oi": 4_000, "volume": 1, "expiry": "2026-11-20", "tier": 1}],
            "emU": em[0], "emD": em[1], "momox": []}


def test_every_plot_and_bubble_is_guarded_by_its_ticker():
    script = gen.render_thinkscript([ticker("SPY", 580, 560, em=(585.5, 574.5)), ticker("AAPL", 240, 220)], 46)
    assert 'def isSPY = GetSymbol() == "SPY";' in script and 'def isAAPL = GetSymbol() == "AAPL";' in script
    branches = script.splitlines()[3]
    assert branches.startswith("if(isSPY){") and "}else if(isAAPL){" in branches
    assert branches.endswith("=Double.NaN;}"), "any other ticker draws nothing"
    spy_branch = branches.split("}else if(isAAPL){")[0]
    assert "call_5=580;" in spy_branch and "put_1=560;" in spy_branch and "emU=585.5;emD=574.5;" in spy_branch
    assert 'AddChartBubble(showBubbleEM and isSPY and highestBar,emU,"+EM"' in script
    assert "isAAPL and highestBar,emU" not in script, "no EM bubble for a ticker without an expected move"
    assert 'AddChartBubble(showBubbleBigOI and isSPY and highestBar,580,"50"' in script
    assert 'AddChartBubble(showBubbleSmallOI and isAAPL and highestBar,220,"4"' in script


class FakeTradier:
    configured = True
    error = ""
    asked: list[str] = []

    def get_option_chain_range(self, symbol, **_kw):
        FakeTradier.asked.append(symbol)
        if FakeTradier.error:
            raise RuntimeError(FakeTradier.error)
        return {"callExpDateMap": {"2026-11-20:16": {"100.0": [{"strikePrice": 100, "openInterest": 10}]}}}


class FakeSchwab:
    configured = True
    asked: list[str] = []

    def __init__(self, *_args):
        pass

    def get_option_chain(self, symbol, **_kw):
        FakeSchwab.asked.append(symbol)
        return {"putExpDateMap": {"2026-11-20:16": {"95.0": [{"strikePrice": 95, "openInterest": 20}]}}}


@pytest.fixture
def providers(monkeypatch):
    import data.schwab_client as schwab_client
    import data.tradier_client as tradier_client

    FakeTradier.error, FakeTradier.asked, FakeSchwab.asked = "", [], []
    monkeypatch.setattr(tradier_client, "TradierClient", FakeTradier)
    monkeypatch.setattr(schwab_client, "SchwabClient", FakeSchwab)
    return FakeTradier, FakeSchwab


def test_auto_provider_tries_tradier_then_falls_back_to_schwab(providers):
    tradier, schwab = providers
    chain, used = gen.fetch_chain("SPY", 46, "auto")
    assert used == "tradier" and "callExpDateMap" in chain and schwab.asked == []
    tradier.error = "401 token not approved"
    chain, used = gen.fetch_chain("SPY", 46, "auto")
    assert used == "schwab" and "putExpDateMap" in chain and schwab.asked == ["SPY"]
    with pytest.raises(RuntimeError, match="tradier: 401 token not approved"):
        gen.fetch_chain("SPY", 46, "tradier")
