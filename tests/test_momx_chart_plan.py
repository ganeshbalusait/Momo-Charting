"""momx/chart_plan.py against the chart's real JavaScript (2026-11-12).

The paper bots and the Bot's tracked trades take their entry, stop and three
targets from the chart's Project ROI plan. The Python port must give the SAME
numbers as the chart, so this runs frontend/src/chartProjectRoi.js itself
(tests/chart_plan_truth.mjs, in node) over saved option chains and made-up edge
chains and requires zero difference.

Fixtures: tests/fixtures/chart_plan/*.json.gz are real chain copies from
artifacts/oi_chain_history (2026-11-10..12), cut to the three nearest expiries
and the fields the plan reads.
"""
from __future__ import annotations

import functools
import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from momx import chart_plan as cp

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "chart_plan"
TRUTH = ROOT / "tests" / "chart_plan_truth.mjs"
#: fixture -> the day it was saved (the chart's "now": 10:30 ET that morning).
DAYS = {"NVDA": "2026-11-12", "SPY": "2026-11-12", "ACHR": "2026-11-11", "AMD": "2026-11-10"}


@functools.lru_cache(maxsize=None)
def _chain(symbol: str) -> dict:
    return json.loads(gzip.decompress((FIXTURES / f"{symbol}.json.gz").read_bytes()))


def _row(strike, side="CALL", expiry="2026-11-20", bid=1.0, ask=1.2, delta=0.2, gamma=0.03, oi=100, em=4.0, **more):
    return {"symbol": f"X{side[0]}{strike}", "side": side, "expiry": expiry, "strike": strike, "bid": bid, "ask": ask,
            "mark": round((bid + ask) / 2, 4), "last": bid, "delta": delta if side == "CALL" else -abs(delta), "gamma": gamma,
            "open_interest": oi, "expected_move": em, "days_to_expiration": 8, **more}


def edge_cases() -> list[dict]:
    """Made-up chains for the corners real chains rarely show."""
    now = "2026-11-12T15:30:00Z"
    calls = [_row(k, oi=oi) for k, oi in ((100, 50), (102, 500), (104, 900), (105, 900), (106, 100), (108, 3000), (110, 9000))]
    puts = [_row(k, "PUT", oi=oi) for k, oi in ((98, 50), (96, 400), (95, 400), (94, 100), (92, 700), (90, 9000))]
    base = {"now": now, "defaultExpiry": "2026-11-20", "moves": None}
    out = [
        # T3 = the biggest open interest past the full move, within two moves (108, not 110 which is at the edge + beyond).
        {**base, "name": "call oi wall", "rows": calls + puts, "side": "CALL", "expiry": "2026-11-20", "strike": 104, "entry": 1.2, "spot": 100.5},
        {**base, "name": "put oi wall", "rows": calls + puts, "side": "PUT", "expiry": "2026-11-20", "strike": 96, "entry": 1.2, "spot": 99.5},
        # Two strikes share the biggest open interest: the earlier chain row wins.
        {**base, "name": "oi tie", "rows": calls + puts, "side": "CALL", "expiry": "2026-11-20", "strike": 102, "entry": 0.9, "spot": 99.4},
        {**base, "name": "put oi tie", "rows": calls + puts, "side": "PUT", "expiry": "2026-11-20", "strike": 98, "entry": 0.9, "spot": 100.2},
        # No open interest past the move: 1.5x the expected move.
        {**base, "name": "no oi", "rows": [_row(k, oi=0) for k in (100, 102, 104, 106, 108)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 104, "entry": 1.1, "spot": 100.0},
        # No expected move anywhere: no targets at all, the stop still stands.
        {**base, "name": "no move", "rows": [_row(k, em=0) for k in (100, 102, 104)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 102, "entry": 1.1, "spot": 100.0},
        # The map names the expiry: it wins over the rows' own expected move.
        {**base, "name": "map move", "moves": {"2026-11-20": 2.5}, "rows": calls + puts, "side": "CALL",
         "expiry": "2026-11-20", "strike": 104, "entry": 1.2, "spot": 100.5},
        # No delta and no gamma: the option cannot be modelled down to half, deep in the money.
        {**base, "name": "no greeks itm", "rows": [_row(80, delta=0, gamma=0, bid=20, ask=21)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 80, "entry": 21, "spot": 100.0},
        {**base, "name": "no greeks otm", "rows": [_row(104, delta=0, gamma=0)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 104, "entry": 1.2, "spot": 100.0},
        # A big gamma: the delta runs out before the option halves.
        {**base, "name": "big gamma", "rows": [_row(104, delta=0.15, gamma=0.4)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 104, "entry": 1.2, "spot": 100.0},
        {**base, "name": "put big gamma", "rows": [_row(96, "PUT", delta=0.15, gamma=0.4)], "side": "PUT",
         "expiry": "2026-11-20", "strike": 96, "entry": 1.2, "spot": 100.0},
        # Half-cent prices: JavaScript rounds a tie up, Python's round() would not.
        {**base, "name": "half cents", "rows": [_row(k, em=1.005) for k in (100, 101, 102, 103)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 101, "entry": 0.125, "spot": 100.125},
        {**base, "name": "put half cents", "rows": [_row(k, "PUT", em=2.345) for k in (97, 98, 99)], "side": "PUT",
         "expiry": "2026-11-20", "strike": 98, "entry": 0.335, "spot": 100.005},
        # A move under a cent: half and full collapse into one level and the chart adds runner levels.
        {**base, "name": "tiny move", "rows": [_row(k, em=0.004, oi=0) for k in (100, 101, 102)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 101, "entry": 0.5, "spot": 100.0},
        # No fill price given: the chart falls back to the chain's own price (the bid/ask middle).
        {**base, "name": "no entry", "rows": calls + puts, "side": "CALL", "expiry": "2026-11-20", "strike": 104, "entry": None, "spot": 100.5},
        # A penny option.
        {**base, "name": "penny", "rows": [_row(110, bid=0.0, ask=0.01, delta=0.004, gamma=0.001)], "side": "CALL",
         "expiry": "2026-11-20", "strike": 110, "entry": 0.01, "spot": 100.0},
    ]
    return out


@functools.lru_cache(maxsize=None)
def _real_cases() -> tuple:
    return tuple(_build_real_cases())


def real_cases() -> list[dict]:
    return list(_real_cases())


def _build_real_cases() -> list[dict]:
    """Every tradable strike within 12% of the stock, both sides, every expiry of
    each saved chain, bought at its ask, at three stock prices."""
    out = []
    for symbol, day in DAYS.items():
        chain = _chain(symbol)
        spot = float(chain["underlyingPrice"])
        for shift in (1.0, 0.985, 1.02):
            price = round(spot * shift, 4)
            for row in chain["selectedExpiryChainRows"]:
                strike = float(row["strike"])
                if row["expiry"][:10] <= day or abs(strike / spot - 1) > 0.12 or not (row.get("ask") or 0) > 0:
                    continue
                out.append({"name": f"{symbol}|{shift}|{row['expiry'][:10]}|{row['side']}|{strike}", "chain": str(FIXTURES / f"{symbol}.json.gz"),
                            "symbol": symbol, "side": row["side"], "expiry": row["expiry"][:10], "strike": strike,
                            "entry": row["ask"], "spot": price, "now": f"{day}T15:30:00Z"})
    return out


def _mine(case: dict) -> dict | None:
    day = case["now"][:10]
    if case.get("chain"):
        plan = cp.from_chain(_chain(case["symbol"]), bear=case["side"] == "PUT", expiry=case["expiry"], strike=case["strike"],
                             entry=case["entry"], spot=case["spot"], today=day)
    else:
        plan = cp.build(case["rows"], side=case["side"], expiry=case["expiry"], strike=case["strike"], entry=case["entry"],
                        spot=case["spot"], expiry_expected_moves=case.get("moves"), first_live=day,
                        default_expiry=case.get("defaultExpiry", ""))
    return plan


@pytest.fixture(scope="module")
def truth(tmp_path_factory):
    """Run the chart's real code once for every case."""
    if shutil.which("node") is None:
        pytest.fail("node is required: this test compares against the chart's real JavaScript")
    folder = tmp_path_factory.mktemp("chart-plan-truth")
    cases = edge_cases() + real_cases()
    (folder / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    subprocess.run(["node", str(TRUTH), str(folder / "cases.json"), str(folder / "out.json")], check=True, timeout=300, cwd=str(ROOT))
    return {"cases": {c["name"]: c for c in cases}, "result": json.loads((folder / "out.json").read_text(encoding="utf-8"))}


COMPARED = ("side", "expiry", "strike", "entry", "entrySpot", "expectedMove", "delta", "gamma", "stopPremium", "stopStock",
            "targets", "chartExpiry", "chartStrike")


def _differences(truth, names) -> list:
    out = []
    for name in names:
        theirs, mine = truth["result"][name], _mine(truth["cases"][name])
        if not theirs["found"]:
            out.append((name, "the chart did not find the contract"))
            continue
        if mine is None:
            out.append((name, "the port returned nothing"))
            continue
        for key in COMPARED:
            if mine.get(key) != theirs.get(key):
                out.append((name, key, mine.get(key), theirs.get(key)))
    return out


def test_every_edge_chain_matches_the_chart_exactly(truth):
    names = [c["name"] for c in edge_cases()]
    assert len(names) == 16
    assert _differences(truth, names) == []


@pytest.mark.parametrize("symbol", sorted(DAYS))
def test_real_chains_match_the_chart_exactly(truth, symbol):
    names = [c["name"] for c in real_cases() if c["symbol"] == symbol]
    assert len(names) >= 30, "the saved chain must give a real spread of contracts"
    assert _differences(truth, names) == []


def test_the_comparison_is_not_empty(truth):
    """Guard against a vacuous pass: real stops and real open-interest targets were compared."""
    results = [truth["result"][c["name"]] for c in real_cases()]
    assert sum(1 for r in results if r["stopStock"]) > 500
    assert sum(1 for r in results if len(r["targets"]) == 3) > 500
    mine = [_mine(c) for c in real_cases()]
    assert sum(1 for p in mine if p and p["t3FromOi"]) > 100 and sum(1 for p in mine if p and not p["t3FromOi"]) > 0
    assert any(r["chartStrike"] for r in results)


def test_targets_and_stop_read_as_his_plan():
    """T1 half the move, T2 the full move, T3 the biggest open interest past it; the stop is the stock
    price where the option is worth half its entry."""
    rows = [_row(k, oi=oi) for k, oi in ((102, 500), (104, 900), (106, 100), (108, 3000), (110, 9000))]
    plan = cp.build(rows, side="CALL", expiry="2026-11-20", strike=104, entry=1.20, spot=100.0)
    assert (plan["t1"], plan["t2"], plan["t3"]) == (102.0, 104.0, 108.0)
    assert plan["t3FromOi"] is True and plan["stopPremium"] == 0.6
    assert plan["stopStock"] < 100.0
    worth = cp.option_value("CALL", 100.0, 1.20, 0.2, 0.03, 104, plan["stopStock"])
    assert abs(worth - 0.6) <= 0.01
    put = cp.build([_row(k, "PUT", oi=oi) for k, oi in ((98, 5), (96, 5), (94, 800), (92, 9000))],
                   side="PUT", expiry="2026-11-20", strike=96, entry=1.0, spot=100.0)
    assert (put["t1"], put["t2"], put["t3"]) == (98.0, 96.0, 92.0) and put["stopStock"] > 100.0


def test_without_open_interest_the_third_target_is_one_and_a_half_moves():
    plan = cp.build([_row(104, oi=0)], side="CALL", expiry="2026-11-20", strike=104, entry=1.0, spot=100.0)
    assert plan["t3"] == 106.0 and plan["t3FromOi"] is False


def test_no_expected_move_means_no_targets_and_nothing_invented():
    plan = cp.build([_row(104, em=0)], side="CALL", expiry="2026-11-20", strike=104, entry=1.0, spot=100.0)
    assert plan["targets"] == [] and plan["t1"] is None and plan["t2"] is None and plan["t3"] is None
    assert plan["expectedMove"] is None and plan["stopStock"] is not None


def test_a_contract_that_is_not_in_the_chain_gives_no_plan():
    assert cp.build([_row(104)], side="CALL", expiry="2026-11-20", strike=105, entry=1.0, spot=100.0) is None
    assert cp.build([_row(104)], side="CALL", expiry="2026-11-20", strike=104, entry=1.0, spot=0) is None
    assert cp.from_chain(None, bear=False, expiry="2026-11-20", strike=104, entry=1.0, spot=100.0) is None


def test_the_stop_moves_with_the_fill():
    """The paper fill comes a little after the signal: the same contract at another price and
    stock price gets its own stop, exactly as the chart does for a typed fill."""
    plan = cp.build([_row(104)], side="CALL", expiry="2026-11-20", strike=104, entry=1.0, spot=100.0)
    premium, stop = cp.restop(plan, 1.40, 101.0)
    assert premium == 0.7
    assert stop == cp.stop_stock("CALL", 101.0, 1.40, 0.2, 0.03, 104, 0.7)
    assert cp.restop(plan, None, 101.0) == (None, None)


def test_his_split_of_the_contracts():
    assert cp.split_contracts(3) == [1, 1, 1]
    assert cp.split_contracts(2) == [1, 1, 0]
    assert cp.split_contracts(1) == [0, 1, 0]
    assert cp.split_contracts(6) == [2, 2, 2]
    assert cp.split_contracts(12) == [4, 4, 4]
    assert cp.split_contracts(4) == [1, 1, 2] and cp.split_contracts(5) == [1, 1, 3]
    assert cp.split_contracts(0) == [0, 0, 0] and cp.split_contracts(None) == [0, 0, 0]


def test_javascript_rounding_not_pythons():
    assert cp.round2(0.125) == 0.13 and cp.round2(2.675) == 2.68 and cp.round2(-0.125) == -0.12
    assert cp.round2(1.005) == 1.0      # 1.005 * 100 is 100.49999999999999 in both languages


# ---------------------------------------------------------------------------
# rebase(): the paper order goes out a little after the signal, with the stock somewhere else.
# Entry, stop and targets must then start from that ONE stock price, as on the chart. rebase()
# works from what the plan itself keeps (delta, gamma, the expected move, the open-interest
# strikes), and must give exactly what the chart's formulas give from the full chain at that
# stock price. from_chain() is held to the real JavaScript above, so this is the same standard.

REBASED = ("entrySpot", "t1", "t2", "t3", "t3FromOi", "stopPremium", "stopStock", "expectedMove", "delta", "gamma")


@pytest.mark.parametrize("symbol", sorted(DAYS))
def test_rebase_gives_the_charts_numbers_from_the_order_time_stock_price(symbol):
    chain, day = _chain(symbol), DAYS[symbol]
    checked, wrong = 0, []
    for case in real_cases():
        if case["symbol"] != symbol or "|1.0|" not in case["name"]:
            continue
        common = dict(bear=case["side"] == "PUT", expiry=case["expiry"], strike=case["strike"], today=day)
        plan = cp.from_chain(chain, entry=case["entry"], spot=case["spot"], **common)
        if not plan or not plan.get("expectedMove"):
            continue
        kept = json.loads(json.dumps({k: v for k, v in plan.items() if k != "targets"}))      # as it is saved
        for moves in (-1.7, -0.6, 0.35, 1.1, 1.9):
            spot = round(case["spot"] + moves * plan["expectedMove"], 2)
            paid = round(case["entry"] * 1.07 + 0.01, 2)
            want = cp.from_chain(chain, entry=paid, spot=spot, **common)
            got = cp.rebase(kept, paid, spot)
            checked += 1
            if {k: got[k] for k in REBASED} != {k: want[k] for k in REBASED}:
                wrong.append((case["name"], moves, {k: (got[k], want[k]) for k in REBASED if got[k] != want[k]}))
    assert checked >= 50 and wrong == [], wrong[:5]


def test_rebase_at_the_signals_own_price_changes_nothing_and_keeps_the_oi_rule():
    rows = [_row(k, oi=oi) for k, oi in ((100, 50), (102, 500), (104, 900), (105, 900), (106, 100), (108, 3000), (110, 9000))]
    chain = {"selectedExpiryChainRows": rows, "currentAtm": {"expiry": "2026-11-20", "expectedMove": 4.0}}
    plan = cp.from_chain(chain, bear=False, expiry="2026-11-20", strike=104, entry=1.2, spot=100.0)
    assert plan["walls"] == [[100.0, 50.0], [102.0, 500.0], [104.0, 900.0], [105.0, 900.0], [106.0, 100.0], [108.0, 3000.0], [110.0, 9000.0]]
    same = cp.rebase(plan, 1.2, 100.0)
    assert {k: same[k] for k in REBASED} == {k: plan[k] for k in REBASED}
    # Stock already at 102.50 when the order goes out: T1 is half a move from THERE, and T3 is
    # the biggest open interest past the new full move and within two moves (110 now, not 108).
    later = cp.rebase(plan, 1.25, 102.5)
    assert (later["entrySpot"], later["t1"], later["t2"], later["t3"], later["t3FromOi"]) == (102.5, 104.5, 106.5, 110.0, True)
    assert later["stopStock"] == cp.stop_stock("CALL", 102.5, 1.25, 0.2, 0.03, 104, 0.63)


def test_rebase_invents_nothing():
    # No expected move on the plan: no targets, the stop still stands.
    bare = {"side": "CALL", "expiry": "2026-11-20", "strike": 104.0, "delta": 0.2, "gamma": 0.03, "expectedMove": None, "walls": []}
    out = cp.rebase(bare, 1.2, 100.0)
    assert (out["t1"], out["t2"], out["t3"]) == (None, None, None) and out["stopStock"] is not None
    # No open interest kept: the chart's own fallback, 1.5x the move.
    assert cp.rebase({**bare, "expectedMove": 4.0}, 1.2, 100.0)["t3"] == 106.0
    # A plan saved before the strikes were kept re-uses its own T3 strike while it still qualifies.
    old = {**bare, "expectedMove": 4.0, "t3": 108.0, "t3FromOi": True}
    del old["walls"]
    assert cp.rebase(old, 1.2, 101.0)["t3"] == 108.0 and cp.rebase(old, 1.2, 101.0)["t3FromOi"] is True
    assert cp.rebase(old, 1.2, 105.0)["t3"] == 111.0                     # 108 is inside the move from 105: 1.5x
    # No stock price, or not a plan: nothing.
    assert cp.rebase(old, 1.2, None) is None and cp.rebase(None, 1.2, 100.0) is None
    assert cp.walls([_row(104)], "CALL", "2026-11-20", 100.0, None) == []
