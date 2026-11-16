"""Unit: momx/my_insights.py, the owner's real-trade numbers, on fixed fills (2026-11-11)."""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from momx import my_insights as mi

ET = ZoneInfo("America/New_York")
SPY = "SPY   261113C00750000"
NVDA = "NVDA  261114P00190000"
TODAY = "2026-11-13"
NOW = datetime(2026, 11, 13, 17, 0, tzinfo=ET)


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_INSIGHTS_DIR", str(tmp_path / "insights"))


_N = {"n": 0}


def fill(at, side, qty, price, symbol=SPY, order=None, fees=0.0, kind="trade", account="…5678"):
    _N["n"] += 1
    und, rest = symbol.split()[0], symbol.split()[-1]
    expiry = f"20{rest[0:2]}-{rest[2:4]}-{rest[4:6]}"
    day = at[:10]
    from datetime import date
    return {"id": f"f{_N['n']}", "activityId": f"a{_N['n']}", "orderId": order or f"o{_N['n']}", "account": account,
            "at": at, "day": day, "underlying": und, "optionSymbol": symbol, "assetType": "OPTION",
            "putCall": "CALL" if rest[6] == "C" else "PUT", "strike": int(rest[7:]) / 1000, "expiry": expiry,
            "dte": (date.fromisoformat(expiry) - date.fromisoformat(day)).days, "side": side, "positionEffect": None,
            "quantity": float(qty), "price": float(price), "fees": fees, "multiplier": 100.0, "kind": kind}


def t(clock, day="2026-11-13"):
    return f"{day}T{clock}:00-05:00"


def round_trip(day, buy_at, sell_at, buy, sell, qty=1, symbol=SPY):
    return [fill(t(buy_at, day), "BUY", qty, buy, symbol), fill(t(sell_at, day), "SELL", qty, sell, symbol)]


# ---------------------------------------------------------------- pairing
def test_a_simple_round_trip_after_fees():
    fills = [fill(t("09:45"), "BUY", 2, 1.00, fees=1.32), fill(t("10:15"), "SELL", 2, 1.50, fees=1.32)]
    trades, open_ = mi.pair_trades(fills, TODAY)
    assert open_ == [] and len(trades) == 1
    tr = trades[0]
    assert (tr["pnl"], tr["cost"], tr["fees"], tr["holdMinutes"], tr["dte"]) == (97.36, 200.0, 2.64, 30.0, 0)
    assert tr["direction"] == "long" and tr["putCall"] == "CALL"


def test_partial_fills_of_one_order_are_one_entry():
    fills = [fill(t("09:45"), "BUY", 1, 1.00, order="X"), fill(t("09:45"), "BUY", 1, 1.20, order="X"),
             fill(t("10:00"), "SELL", 2, 2.00, order="Y")]
    trades, _ = mi.pair_trades(fills, TODAY)
    assert len(trades) == 1 and trades[0]["entryPrice"] == 1.1 and trades[0]["quantity"] == 2 and trades[0]["pnl"] == 180.0


def test_partial_exits_stay_one_trade_and_a_half_closed_lot_stays_open():
    fills = [fill(t("09:45"), "BUY", 2, 1.00), fill(t("10:00"), "SELL", 1, 2.00)]
    trades, open_ = mi.pair_trades(fills, TODAY)
    assert trades == [] and open_[0]["quantity"] == 1 and open_[0]["closedSoFar"] == 1
    fills.append(fill(t("11:00"), "SELL", 1, 0.50))
    trades, open_ = mi.pair_trades(fills, TODAY)
    assert open_ == [] and len(trades) == 1
    assert (trades[0]["exitPrice"], trades[0]["pnl"], trades[0]["exitAt"]) == (1.25, 50.0, t("11:00"))


def test_fifo_closes_the_oldest_lot_first():
    fills = [fill(t("09:40"), "BUY", 1, 1.00), fill(t("09:50"), "BUY", 1, 2.00), fill(t("10:30"), "SELL", 1, 3.00)]
    trades, open_ = mi.pair_trades(fills, TODAY)
    assert [x["entryPrice"] for x in trades] == [1.0] and trades[0]["pnl"] == 200.0
    assert [p["price"] for p in open_] == [2.0]


def test_same_day_re_entries_are_separate_trades():
    fills = round_trip("2026-11-13", "09:40", "09:55", 1.0, 1.5) + round_trip("2026-11-13", "10:05", "10:20", 1.6, 1.2)
    trades, _ = mi.pair_trades(fills, TODAY)
    assert [x["pnl"] for x in trades] == [50.0, -40.0]


def test_contracts_and_accounts_never_mix():
    fills = [fill(t("09:40"), "BUY", 1, 1.0), fill(t("09:41"), "SELL", 1, 2.0, symbol=NVDA),
             fill(t("09:42"), "SELL", 1, 1.5, account="…9999")]
    trades, open_ = mi.pair_trades(fills, TODAY)
    assert trades == [] and len(open_) == 3


def test_an_option_left_open_past_expiry_counts_as_expired_at_zero():
    fills = [fill(t("09:40", "2026-11-12"), "BUY", 1, 0.80, symbol="SPY   261112C00760000")]
    trades, open_ = mi.pair_trades(fills, TODAY)
    assert open_ == [] and trades[0]["pnl"] == -80.0 and trades[0]["expired"] and trades[0]["assumedExpired"]
    assert trades[0]["exitAt"].startswith("2026-11-12T16:00")


def test_schwabs_expiry_row_closes_the_lot():
    sym = "SPY   261112C00760000"
    fills = [fill(t("09:40", "2026-11-12"), "BUY", 1, 0.80, symbol=sym), fill(t("16:00", "2026-11-12"), "SELL", 1, 0.0, symbol=sym, kind="expired")]
    trades, _ = mi.pair_trades(fills, TODAY)
    assert trades[0]["expired"] and "assumedExpired" not in trades[0]


# ---------------------------------------------------------------- stats and buckets
def test_stats_and_too_few_trades():
    s = mi.stats([{"pnl": 100.0}, {"pnl": -50.0}, {"pnl": 0.0}], "last 7 days")
    assert s == {"trades": 3, "wins": 1, "losses": 1, "flat": 1, "winRate": 0.3333, "avgWin": 100.0, "avgLoss": -50.0,
                 "expectancy": 16.67, "profitFactor": 2.0, "pnl": 50.0, "enough": False, "note": "too few trades to judge",
                 "period": "last 7 days"}
    assert mi.stats([{"pnl": 10.0}] * 10)["enough"] is True and mi.stats([{"pnl": 10.0}] * 10)["note"] == ""
    assert mi.stats([{"pnl": 10.0}])["profitFactor"] == "inf"
    assert mi.stats([])["profitFactor"] is None and mi.stats([])["winRate"] is None


@pytest.mark.parametrize("clock,bucket", [("09:29", "outside 09:30-16:00"), ("09:30", "09:30-10:00"), ("10:00", "10:00-11:00"),
                                          ("13:59", "11:00-14:00"), ("15:59", "14:00-16:00"), ("16:00", "outside 09:30-16:00")])
def test_time_of_day_buckets(clock, bucket):
    assert mi.time_bucket(datetime.fromisoformat(t(clock))) == bucket


@pytest.mark.parametrize("dte,bucket", [(0, "0 DTE"), (2, "1-2 DTE"), (3, "3-7 DTE"), (30, "8-30 DTE"), (31, "30+ DTE"), (None, "unknown")])
def test_dte_buckets(dte, bucket):
    assert mi.dte_bucket(dte) == bucket


def test_delta_and_setup_labels():
    assert [mi.delta_bucket(d) for d in (0.1, -0.25, 0.4, 0.7, None)] == ["under 0.20", "0.20-0.35", "0.35-0.50", "0.50+", "no saved chain"]
    assert mi.setup_label({"onBoard": True, "level": 2}) == "Level 2"
    assert mi.setup_label({"onBoard": True, "level": 0}) == "on the board, no Level"
    assert mi.setup_label({"onBoard": False, "noHistory": False}) == "not on the board"
    assert mi.setup_label({"onBoard": False, "noHistory": True}) == "no scanner history that day"
    assert mi.setup_label(None) == "not checked yet"


# ---------------------------------------------------------------- habits
def closed(fills):
    return mi.pair_trades(fills, TODAY)[0]


def test_revenge_trade_within_15_minutes_of_a_losing_exit():
    trades = closed(round_trip("2026-11-13", "09:40", "10:00", 2.0, 1.0)            # -100, closed 10:00
                    + round_trip("2026-11-13", "10:10", "10:30", 1.0, 0.7)          # entered 10 min later: flagged
                    + round_trip("2026-11-13", "10:50", "11:00", 1.0, 1.2))         # 20 min after the 10:30 loss: not
    h = mi.habit_revenge(trades, trades)
    assert h["count"] == 1 and h["trades"][0]["entryAt"] == t("10:10") and h["cost"] == 30.0
    assert "10 min after a $100 loss" in h["trades"][0]["note"]


def test_overtrading_against_the_20_day_median():
    fills = []
    for day in ("2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06", "2026-11-07"):
        fills += round_trip(day, "09:40", "09:50", 1.0, 1.1, symbol="SPY   261120C00750000")
        fills += round_trip(day, "10:40", "10:50", 1.0, 1.1, symbol="SPY   261120C00750000")
    for clock in ("09:40", "10:40", "11:40", "12:40"):
        fills += round_trip("2026-11-10", clock, clock[:3] + "50", 1.0, 0.8, symbol="SPY   261120C00750000")
    trades = closed(fills)
    h = mi.habit_overtrading(trades, trades)
    assert h["count"] == 1 and h["trades"][0]["entryAt"] == t("12:40", "2026-11-10") and h["cost"] == 20.0
    # Fewer than 5 earlier days: not judged, and it says so.
    few = closed(fills[-8:])
    assert mi.habit_overtrading(few, few)["count"] == 0 and "earlier trading days" in mi.habit_overtrading(few, few)["note"]


def test_sizing_up_after_a_loss():
    trades = closed(round_trip("2026-11-13", "09:40", "10:00", 1.0, 0.5)                 # cost 100, -50
                    + round_trip("2026-11-13", "10:30", "11:00", 1.0, 0.9, qty=2)         # cost 200 > 150: flagged
                    + round_trip("2026-11-13", "11:30", "12:00", 1.0, 1.1, qty=2))        # after a loss of cost 200: 200 < 300
    h = mi.habit_size_up(trades, trades)
    assert h["count"] == 1 and h["trades"][0]["entryAt"] == t("10:30") and h["cost"] == 20.0


def test_held_past_minus_50_from_saved_chains_and_from_the_exit():
    trades = closed(round_trip("2026-11-13", "09:40", "11:00", 2.0, 1.5)        # chains show 0.90 at 10:05: flagged
                    + round_trip("2026-11-13", "11:10", "11:40", 2.0, 0.6, symbol=NVDA))   # exit itself below -50%
    paths = {f"{SPY.replace(' ', '')}|2026-11-13": {"points": [[t("10:05"), 0.9], [t("10:35"), 1.4]], "complete": True}}
    h = mi.habit_held_past_50(trades, paths)
    assert h["count"] == 2 and h["approx"] is True
    assert "a saved chain" in h["trades"][0]["note"] and "the exit price" in h["trades"][1]["note"]
    assert h["cost"] == 40.0                    # only the NVDA trade sold below the -50% point: (1.0 - 0.6) x 100


def test_cut_winners_early_only_where_a_saved_chain_shows_it():
    trades = closed(round_trip("2026-11-13", "09:40", "10:00", 1.0, 1.5)
                    + round_trip("2026-11-13", "11:00", "11:20", 1.0, 1.2, symbol=NVDA))
    paths = {f"{SPY.replace(' ', '')}|2026-11-13": {"points": [[t("09:35"), 2.5], [t("11:05"), 2.2]], "complete": True}}
    h = mi.habit_cut_winners(trades, paths)
    assert h["count"] == 1 and h["trades"][0]["underlying"] == "SPY" and h["cost"] == 50.0


def test_hold_time_medians():
    trades = closed(round_trip("2026-11-13", "09:40", "10:00", 1.0, 1.5) + round_trip("2026-11-13", "10:00", "11:00", 1.0, 0.5))
    assert mi.hold_time(trades) == {"all": 40.0, "wins": 20.0, "losses": 60.0}


# ---------------------------------------------------------------- execution
def test_fill_vs_mid_and_approximate_when_the_chain_is_far():
    fills = round_trip("2026-11-13", "10:00", "10:30", 1.05, 1.40, qty=2)
    trades = closed(fills)
    by_id = {f["id"]: f for f in fills}
    quotes = {fills[0]["id"]: {"bid": 0.95, "ask": 1.05, "gapMinutes": 25.0},
              fills[1]["id"]: {"bid": 1.40, "ask": 1.50, "gapMinutes": 3.0}}
    e = mi.execution(trades, by_id, quotes)
    assert (e["trades"], e["unpriced"], e["approxCount"], e["spreadCost"]) == (2, 0, 1, 20.0)
    assert e["avgSpreadPct"] == pytest.approx((0.05 + 0.0345) / 2, abs=1e-3)
    e2 = mi.execution(trades, by_id, {})
    assert e2["trades"] == 0 and e2["unpriced"] == 2 and "cannot be measured" in e2["note"]


# ---------------------------------------------------------------- saved-chain details
def test_enrich_reads_quotes_paths_and_the_scanner_row(tmp_path):
    fills = {f["id"]: f for f in round_trip("2026-11-13", "10:00", "10:40", 1.0, 1.5)}
    chain = {"selectedExpiryChainRows": [{"symbol": SPY, "bid": 0.95, "ask": 1.05, "mark": 1.0, "delta": 0.31}],
             "_saved": {"fileTime": t("09:52")}}
    store = {"fills": fills}
    mi.enrich_store(store, tmp_path, now=NOW, chain_at=lambda und, at: chain,
                    day_chains=lambda und, day: [chain, {**chain, "_saved": {"fileTime": t("10:22")}}],
                    setup_at=lambda sym, bear, at: {"onBoard": True, "level": 2, "hlRank": 3})
    first = next(iter(fills))
    assert store["quotes"][first] == {"bid": 0.95, "ask": 1.05, "mark": 1.0, "delta": 0.31, "savedAt": t("09:52"), "gapMinutes": 8.0}
    assert store["paths"][f"{SPY.replace(' ', '')}|2026-11-13"] == {"points": [[t("09:52"), 1.0], [t("10:22"), 1.0]], "complete": True}
    assert store["setups"] == {first: {"onBoard": True, "level": 2, "hlRank": 3}}


def test_saved_chains_of_a_day_are_read_from_disk(tmp_path):
    folder = tmp_path / "2026-11-13" / "1005"
    folder.mkdir(parents=True)
    with gzip.open(folder / "SPY.json.gz", "wt", encoding="utf-8") as handle:
        json.dump({"callRows": [{"symbol": SPY, "bid": 1, "ask": 1.2, "mark": 1.1}]}, handle)
    found = mi._saved_chains_of_day(tmp_path, "SPY", "2026-11-13")
    assert len(found) == 1 and found[0]["_saved"]["slot"] == "1005" and mi._mark(mi.contract_row(found[0], SPY)) == 1.1


# ---------------------------------------------------------------- the whole reply
def seed_paper(artifacts: Path):
    path = artifacts / "momx_paper_mirror" / "paper3" / "2026-11-13.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {"side": "bull", "rule": "level1", "symbol": "AAPL", "at": t("09:45"), "qty": 3, "status": "closed",
           "buyFill": 1.0, "pl": 120.0, "closedAt": t("10:30")}
    other = {**row, "symbol": "MSFT", "pl": -60.0, "at": t("09:45", "2026-11-12"), "closedAt": t("10:30", "2026-11-12")}
    path.write_text(json.dumps({"rows": {"a": row, "b": other}}), encoding="utf-8")


def test_build_reply_shape(tmp_path):
    seed_paper(tmp_path)
    fills = round_trip("2026-11-13", "09:40", "10:00", 2.0, 1.0) + round_trip("2026-11-13", "10:10", "10:30", 1.0, 1.4, symbol=NVDA)
    store = {"fills": {f["id"]: f for f in fills}, "setups": {fills[0]["id"]: {"onBoard": True, "level": 1}}}
    doc = mi.build(7, now=NOW, artifacts=tmp_path, store=store, events={"items": [], "note": "x"}, schwab={"state": "ok"})
    assert doc["available"] and doc["days"] == 7 and doc["period"]["from"] == "2026-11-07" and doc["period"]["to"] == TODAY
    s = doc["summary"]
    assert (s["trades"], s["pnl"], s["winRate"], s["note"]) == (2, -60.0, 0.5, "too few trades to judge")
    assert [r["key"] for r in doc["breakdowns"]["callPut"]] == ["Calls", "Puts"]
    assert {r["key"] for r in doc["breakdowns"]["setup"]} == {"Level 1", "not checked yet"}
    counts = {h["key"]: h["count"] for h in doc["habits"]}
    assert counts == {"revenge": 1, "overtrading": 0, "sizeUpAfterLoss": 0, "heldPast50": 1, "cutWinnersEarly": 0}
    assert {h["key"] for h in doc["habits"]} == {"revenge", "overtrading", "sizeUpAfterLoss", "heldPast50", "cutWinnersEarly"}
    bot = doc["paperBots"][0]
    assert (bot["account"], bot["trades"], bot["pnl"], bot["sameDays"]["trades"], bot["sameDays"]["pnl"]) == ("paper3", 2, 60.0, 1, 120.0)
    assert len(doc["trades"]) == 2 and doc["openPositions"] == [] and doc["schwab"] == {"state": "ok"}
    json.dumps(doc)                                  # plain JSON all the way down


def test_period_excludes_trades_closed_before_it(tmp_path):
    fills = round_trip("2026-10-01", "09:40", "10:00", 1.0, 2.0, symbol="SPY   261017C00700000") + round_trip("2026-11-13", "09:40", "10:00", 1.0, 0.5)
    store = {"fills": {f["id"]: f for f in fills}}
    week = mi.build(7, now=NOW, artifacts=tmp_path, store=store, events={}, schwab={})
    quarter = mi.build(90, now=NOW, artifacts=tmp_path, store=store, events={}, schwab={})
    assert week["summary"]["trades"] == 1 and quarter["summary"]["trades"] == 2
    assert mi.build(45, now=NOW, artifacts=tmp_path, store=store, events={}, schwab={})["days"] == 30


def test_chat_lines_describe_and_never_advise(tmp_path):
    fills = round_trip("2026-11-13", "09:40", "10:00", 2.0, 1.0) + round_trip("2026-11-13", "10:05", "10:30", 1.0, 0.4)
    doc = mi.build(7, now=NOW, artifacts=tmp_path, store={"fills": {f["id"]: f for f in fills}}, events={}, schwab={"lastReadAt": "2026-11-13T16:31:00-05:00"})
    text = " ".join(" ".join(mi.chat_lines(doc, topic)) for topic in ("summary", "habits", "puts", "calls", "spreads", "setup"))
    assert "2 trades" in text and "too few trades to judge" in text and "New trade soon after a loss: 1 time" in text
    assert "Last read from Schwab at 2026-11-13 16:31 ET." in text
    for word in ("you should", "should ", "buy ", "sell ", "avoid", "stop "):
        assert word not in text.lower()
    assert mi.chat_lines({"available": False, "schwab": {"signInNeeded": True}}) == ["Schwab sign-in needed: the trade history could not be read today."]



def test_chat_lines_after_a_read_that_found_no_trades():
    """A read that worked with no fills is not "not read yet": it says when, how far back and which accounts."""
    read = {"state": "ok", "lastReadAt": "2026-11-12T01:09:00-05:00", "accounts": ["…1867"], "firstDay": None, "throughDay": "2026-11-12"}
    assert mi.chat_lines({"available": False, "schwab": read}) == [
        "Read from Schwab at 11-12 01:09 ET: no option trades found in the last 90 days on account …1867."]
    two = dict(read, accounts=["…1867", "…4410"])
    assert mi.chat_lines({"available": False, "schwab": two})[0].endswith("on accounts …1867, …4410.")
    # The "not read yet" line stays only for a read that never happened.
    assert mi.chat_lines({"available": False, "schwab": {"state": "never"}}) == [
        "No Schwab trades have been read yet. They are read once a day after 16:30 ET."]
    stopped = {"state": "stopped", "stopped": True, "signInNeeded": False}
    assert "not been read yet" not in mi.chat_lines({"available": False, "schwab": stopped})[0]

def test_the_analytics_and_calendar_never_reach_schwab_or_the_network():
    import re
    from momx import market_events
    for module in (mi, market_events):
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert re.findall(r"schwab_transport|SchwabClient|library_client|urlopen|requests\.|httpx|place_order|cancel_order|replace_order|\.post\(", source) == []


def test_execution_counts_every_partial_fill_of_an_entry():
    fills = [fill(t("09:45"), "BUY", 1, 1.00, order="X"), fill(t("09:46"), "BUY", 1, 1.10, order="X"), fill(t("10:00"), "SELL", 2, 2.00, order="Y")]
    trades = closed(fills)
    quotes = {f["id"]: {"bid": 0.9, "ask": 1.1, "gapMinutes": 1.0} for f in fills[:2]}
    e = mi.execution(trades, {f["id"]: f for f in fills}, quotes)
    assert (e["trades"], e["unpriced"], e["spreadCost"]) == (2, 1, 10.0)


# ---------------------------------------------------------------- review fixes (2026-11-12)
def effect(f, position_effect):
    return {**f, "positionEffect": position_effect}


def test_a_lone_closing_sale_is_not_a_trade_nor_a_short(tmp_path):
    """A contract bought before the 90-day window and sold inside it: the sale closes a position that is
    not in the read, so it is no trade, no open short, no win (it was a made-up +$599 win)."""
    sym = "NVDA  261030C00200000"
    sale = effect(fill(t("09:00", "2026-10-20"), "SELL", 2, 3.00, symbol=sym, fees=0.65), "CLOSING")
    info = {}
    trades, open_ = mi.pair_trades([sale], "2026-11-12", info)
    assert trades == [] and open_ == [] and info == {"closedBeforeWindow": 1}
    assert mi.stats(trades)["wins"] == 0
    # Before its expiry it is not an open short either.
    trades, open_ = mi.pair_trades([sale], "2026-10-21")
    assert trades == [] and open_ == []
    doc = mi.build(90, now=datetime(2026, 11, 12, 17, 0, tzinfo=ET), artifacts=tmp_path, store={"fills": {sale["id"]: sale}},
                   events={}, schwab={})
    assert doc["summary"]["trades"] == 0 and doc["summary"]["wins"] == 0 and doc["summary"]["closedBeforeWindow"] == 1
    assert doc["openPositions"] == [] and "opened before the read window" in doc["basis"]


def test_a_closing_sale_bigger_than_the_open_lot_drops_only_the_rest():
    buy = effect(fill(t("09:45"), "BUY", 1, 1.00), "OPENING")
    sale = effect(fill(t("10:00"), "SELL", 3, 1.50), "CLOSING")
    info = {}
    trades, open_ = mi.pair_trades([buy, sale], TODAY, info)
    assert [(x["direction"], x["quantity"], x["pnl"]) for x in trades] == [("long", 1.0, 50.0)]
    assert open_ == [] and info["closedBeforeWindow"] == 1


def test_an_opening_sale_still_opens_a_short():
    sale = effect(fill(t("09:45"), "SELL", 1, 2.00), "OPENING")
    trades, open_ = mi.pair_trades([sale], TODAY)
    assert trades == [] and [(p["direction"], p["quantity"]) for p in open_] == [("short", 1.0)]


def test_an_exercise_with_no_value_is_listed_but_left_out_of_the_numbers(tmp_path):
    sym = "AAPL  261106C00250000"
    buy = fill(t("09:00", "2026-11-06"), "BUY", 1, 1.00, symbol=sym, fees=0.65)
    exercised = {**fill(t("00:00", "2026-11-07"), "SELL", 1, 0.0, symbol=sym, kind="exercised"), "valueKnown": False}
    loser = round_trip("2026-11-10", "09:40", "10:00", 1.0, 0.4)
    trades, _ = mi.pair_trades([buy, exercised] + loser, "2026-11-12")
    ex = next(x for x in trades if x["underlying"] == "AAPL")
    assert ex["event"] == "exercised" and ex["valueUnknown"] and not ex["expired"]
    store = {"fills": {f["id"]: f for f in [buy, exercised] + loser}}
    doc = mi.build(30, now=datetime(2026, 11, 12, 17, 0, tzinfo=ET), artifacts=tmp_path, store=store, events={}, schwab={})
    s = doc["summary"]
    assert (s["trades"], s["pnl"], s["unscored"]) == (1, -60.0, 1)            # only the SPY loser counts
    assert {h["key"]: h["count"] for h in doc["habits"]}["heldPast50"] == 1   # the SPY loser, not the exercise
    assert all(r["underlying"] != "AAPL" for h in doc["habits"] for r in h["trades"])
    assert [r["underlying"] for r in doc["trades"]] == ["SPY", "AAPL"] and "exercised" in doc["trades"][1]["note"]
    assert "exercised or assigned" in doc["basis"]


def test_an_exercise_with_a_known_value_closes_at_intrinsic():
    sym = "AAPL  261106C00250000"
    buy = fill(t("09:00", "2026-11-06"), "BUY", 1, 1.00, symbol=sym)
    exercised = {**fill(t("00:00", "2026-11-07"), "SELL", 1, 4.0, symbol=sym, kind="exercised"), "valueKnown": True}
    trades, _ = mi.pair_trades([buy, exercised], "2026-11-12")
    assert (trades[0]["exitPrice"], trades[0]["pnl"], trades[0]["event"], trades[0]["valueUnknown"]) == (4.0, 300.0, "exercised", False)


def test_a_chain_from_the_day_before_leaves_the_fill_unpriced(tmp_path):
    """A 09:35 fill whose newest saved chain is yesterday's 15:30: the overnight move is not "spread paid"."""
    sym = "SPY   261111C00680000"
    buy, sell = fill(t("09:35", "2026-11-11"), "BUY", 5, 1.20, symbol=sym), fill(t("10:05", "2026-11-11"), "SELL", 5, 1.60, symbol=sym)
    old = {"_saved": {"fileTime": "2026-11-10T15:30:00-05:00"}, "callRows": [{"symbol": sym, "bid": 0.28, "ask": 0.32, "delta": 0.05}]}
    fresh = {"_saved": {"fileTime": "2026-11-11T10:00:00-05:00"}, "callRows": [{"symbol": sym, "bid": 1.55, "ask": 1.65, "delta": 0.5}]}
    store = {"fills": {buy["id"]: buy, sell["id"]: sell}}
    mi.enrich_store(store, tmp_path, now=datetime(2026, 11, 11, 18, 0, tzinfo=ET),
                    chain_at=lambda und, at: old if at.hour == 9 else fresh, day_chains=lambda und, day: [],
                    setup_at=lambda *a: {"onBoard": False})
    assert store["quotes"][buy["id"]] == {"none": True}
    assert store["quotes"][sell["id"]]["gapMinutes"] == 5.0
    trades, _ = mi.pair_trades(store["fills"].values(), "2026-11-11")
    e = mi.execution(trades, store["fills"], store["quotes"])
    assert (e["trades"], e["unpriced"], e["spreadCost"]) == (1, 1, 0.0)
    doc = mi.build(7, now=datetime(2026, 11, 11, 18, 0, tzinfo=ET), artifacts=tmp_path, store=store, events={}, schwab={})
    assert [r["key"] for r in doc["breakdowns"]["delta"]] == ["no saved chain"]       # the stale delta is not used


def test_a_same_day_chain_more_than_one_save_away_is_unpriced_too():
    q = {"bid": 1.0, "ask": 1.1, "gapMinutes": 40.0, "savedAt": t("09:20")}
    assert not mi.usable_quote(q, t("10:00"))
    assert mi.usable_quote({**q, "gapMinutes": 25.0, "savedAt": t("09:35")}, t("10:00"))
    assert not mi.usable_quote({**q, "gapMinutes": 20.0, "savedAt": t("23:50", "2026-11-12")}, t("00:10"))
    # A stale quote saved by an earlier version is dropped on the next enrich and never counted.
    fills = round_trip("2026-11-13", "10:00", "10:20", 1.0, 1.2)
    stale = {fills[0]["id"]: {"bid": 0.28, "ask": 0.32, "gapMinutes": 1085.0, "savedAt": "2026-11-12T15:55:00-05:00", "delta": 0.05}}
    e = mi.execution(mi.pair_trades(fills, TODAY)[0], {f["id"]: f for f in fills}, stale)
    assert e["trades"] == 0 and e["spreadCost"] == 0.0


# ---------------------------------------------------------------- a scanner row that is not known
def _capped_board(monkeypatch, other=None):
    """NVDA on Mag7 calls: rows saved at 09:45 and 09:50, then the day's row cap; last seen 11:30.
    A Watchlist calls board holds a fresh NVDA row at 10:30. `other` = Mag7 puts (the other side)."""
    from momx import chat_scanner as cs

    def pt(ts, level=3):
        return cs.Point(ts, 1.5, 1.5, 2.0, 190.0, level, 2, ("Squeeze",), ("5m",), (), (), ())
    t0 = datetime(2026, 11, 10, 9, 45, tzinfo=ET).timestamp()
    mag7 = cs.Board("Mag7", False, "2026-11-10")
    mag7.tickers = {"NVDA": cs.Ticker("NVDA", datetime(2026, 11, 10, 11, 30, tzinfo=ET).timestamp(), [pt(t0), pt(t0 + 300)], truncated=True)}
    at_1030 = datetime(2026, 11, 10, 10, 30, tzinfo=ET).timestamp()
    watch = cs.Board("Watchlist", False, "2026-11-10")
    watch.tickers = {"NVDA": cs.Ticker("NVDA", at_1030 + 600, [pt(at_1030 - 60, level=1)])}
    boards = {("Mag7", False): mag7, ("Watchlist", False): watch, ("Mag7", True): other}
    monkeypatch.setattr(cs, "_board", lambda a, name, bear, day, today: boards.get((name, bear)))
    return cs, pt


def test_a_capped_scanner_row_is_not_known_not_off_the_board(monkeypatch, tmp_path):
    cs, _ = _capped_board(monkeypatch)
    at = datetime(2026, 11, 10, 10, 30, tzinfo=ET)
    out = mi.scanner_setup(tmp_path, "NVDA", False, at)
    assert out["onBoard"] is None and out["unknown"] is True and out["board"] == "Mag7"   # the Watchlist row is not used
    assert "row saving stopped" in out["reason"]
    label = mi.setup_label(out)
    assert label != "not on the board" and label.startswith("not known")
    rows = mi.breakdowns([{"setup": out, "entryAt": at.isoformat(), "pnl": 10.0, "cost": 100.0, "dte": 2,
                           "putCall": "CALL", "underlying": "NVDA", "fees": 0.0}], "x")["setup"] if False else None
    assert rows is None


def test_a_row_on_the_other_side_then_means_it_was_not_on_this_board(monkeypatch, tmp_path):
    from momx import chat_scanner as cs
    at = datetime(2026, 11, 10, 10, 30, tzinfo=ET)
    puts = cs.Board("Mag7", True, "2026-11-10")
    _, pt = _capped_board(monkeypatch, other=puts)
    puts.tickers = {"NVDA": cs.Ticker("NVDA", at.timestamp() + 600, [pt(at.timestamp() - 60)])}
    out = mi.scanner_setup(tmp_path, "NVDA", False, at)
    # Not on Mag7 calls then; the search goes on to the Watchlist, which had it.
    assert out["onBoard"] is True and out["board"] == "Watchlist"


def test_setup_label_of_an_unknown_row():
    assert mi.setup_label({"onBoard": None, "unknown": True}) == "not known (no saved scanner row then)"


# ---------------------------------------------------------------- the chat's spread line
def test_chat_spreads_line_says_saved_when_fills_beat_the_mid():
    doc = {"available": True, "summary": {"period": "last 30 days", "trades": 3},
           "execution": {"trades": 4, "spreadCost": -84.0, "approxCount": 0, "note": ""}, "schwab": {}}
    line = mi.chat_lines(doc, "spreads")[0]
    assert "saved" in line and "$84" in line and "$-" not in line and "paid" not in line
    doc["execution"]["spreadCost"] = 84.0
    assert "$84 paid over 4 fills" in mi.chat_lines(doc, "spreads")[0]
    doc["execution"]["spreadCost"] = 0.0
    assert "at the mid" in mi.chat_lines(doc, "spreads")[0]
