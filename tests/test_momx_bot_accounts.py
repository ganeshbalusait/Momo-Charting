"""Unit: Bot accounts (momx/bot_accounts.py, 2026-11-26) - starting money, reset and past
runs for Paper 3 / Paper 5 / Research / Ticker - and the one thing the live paper bot reads
from it (momx/paper_mirror.py: per-trade money and free money of a run a reset created).
Every file lives in tmp_path; the broker is the paper-mirror tests' fake."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from momx import bot_accounts as ba
from momx import bot_desk, paper_mirror as pm, ticker_trader
from test_momx_paper_mirror import NOW as MIRROR_NOW, Book, FakeClient, _trade

ET = ba.ET
NOW = datetime(2026, 11, 20, 11, 0, tzinfo=ET)       # a Friday
DAY = "2026-11-20"


def stamp(hour, minute=0, day=DAY):
    return f"{day}T{hour:02d}:{minute:02d}:00-05:00"


# ---------------------------------------------------------------- journal
def test_journal_counts_wins_losses_days_and_leaves_break_even_out():
    made = ba.journal([{"day": "2026-11-19", "pl": 100.004}, {"day": "2026-11-19", "pl": -40.0}, {"day": "2026-11-18", "pl": -10.0},
                       {"day": "2026-11-20", "pl": 0.0}, {"day": "2026-11-20", "pl": 50.0}])
    assert made["tiles"] == {"netPnl": 100.0, "closed": 5, "wins": 2, "losses": 2, "winPct": 40.0, "profitFactor": 3.0,
                             "greenDays": 2, "redDays": 1, "dayWinPct": 66.7, "avgWin": 75.0, "avgLoss": -25.0}
    assert made["days"] == [{"day": "2026-11-18", "pnl": -10.0, "trades": 1}, {"day": "2026-11-19", "pnl": 60.0, "trades": 2},
                            {"day": "2026-11-20", "pnl": 50.0, "trades": 2}]


def test_journal_never_returns_infinity_and_handles_nothing():
    only_wins = ba.journal([{"day": DAY, "pl": 10.0}])["tiles"]
    only_losses = ba.journal([{"day": DAY, "pl": -10.0}])["tiles"]
    empty = ba.journal([])
    assert only_wins["profitFactor"] is None and only_wins["avgLoss"] is None and only_wins["winPct"] == 100.0
    assert only_losses["profitFactor"] is None and only_losses["avgWin"] is None
    assert empty["days"] == [] and empty["tiles"]["closed"] == 0 and empty["tiles"]["winPct"] is None
    assert empty["tiles"]["dayWinPct"] is None and empty["tiles"]["netPnl"] == 0.0


def test_a_contract_reads_in_a_few_words():
    assert ba.contract_text("AAPL  261204C00150000") == "150C · Dec 4"
    assert ba.contract_text("NVDA261120P00096500") == "96.5P · Nov 20"
    assert ba.contract_text(None, 104.0, "CALL", "2026-11-20") == "104C · Nov 20"
    assert ba.contract_text("odd text") == "odd text"


# ---------------------------------------------------------------- Research
def vt(symbol="AAPL", entry=1.0, exit_=None, at=10, out=None, **kw):
    t = {"rule": "level1", "symbol": symbol, "at": stamp(at), "entryAt": stamp(at), "status": "open",
         "eContract": f"{symbol:<6}261127C00150000", "eEntry": entry, "eLast": entry}
    if exit_ is not None:
        t.update(status="closed", eExit=exit_, exitAt=stamp(out or at + 1), eExitPriceAt=stamp(out or at + 1))
    t.update(kw)
    return t


def test_a_virtual_trade_is_priced_per_share_at_the_mid_like_the_research_page():
    plain = ba.virtual_trade(vt(entry=1.0, exit_=1.5), "bull", DAY, DAY)
    mids = ba.virtual_trade(vt(entry=9.0, exit_=9.0, eEntryBid=0.9, eEntryAsk=1.1, eExitBid=1.9, eExitAsk=2.1), "bull", DAY, DAY)
    zero_bid = ba.virtual_trade(vt(entry=1.0, exit_=0.4, eExitBid=0.0, eExitAsk=0.8), "bull", DAY, DAY)
    still_open = ba.virtual_trade(vt(entry=1.0, eLastBid=1.1, eLastAsk=1.3), "bull", DAY, DAY)
    assert (plain.entry, plain.exit, plain.mark) == (1.0, 1.5, None)
    assert (mids.entry, mids.exit) == (1.0, 2.0)                       # the bid / ask mid wins over the stored price
    assert zero_bid.exit == 0.0                                          # nobody would buy it: worth $0.00
    assert still_open.exit is None and still_open.mark == 1.2
    # No result: a bad quote, no 0.20-delta contract, an exit with no price, an old day left open.
    assert ba.virtual_trade(vt(exit_=1.5, badQuote=True), "bull", DAY, DAY) is None
    assert ba.virtual_trade({"rule": "level1", "symbol": "AAPL", "at": stamp(10), "status": "no_contract"}, "bull", DAY, DAY) is None
    assert ba.virtual_trade(vt(status="closed", exitAt=stamp(11)), "bull", DAY, DAY) is None
    assert ba.virtual_trade(vt(), "bull", "2026-11-19", DAY) is None


def trades_of(*raw):
    return [v for v in (ba.virtual_trade(t, "bull", DAY, DAY) for t in raw) if v]


def test_the_first_research_run_counts_every_trade_one_contract_each():
    run = {"n": 1, "money": 10000.0, "perTrade": None, "startedAt": stamp(0), "capped": False}
    books = ba.research_books(trades_of(vt("AAPL", 1.0, 1.5), vt("MSFT", 20.0, 19.0), vt("NVDA", 2.0, eLast=2.5)), run)
    body = ba.account_body("research", run, books, [], DAY)
    assert sorted(t["pl"] for t in books["closed"]) == [-100.0, 50.0]        # (exit - entry) x 100 x 1 contract
    assert body["totalPnl"] == 0.0 and body["openPnl"] == 50.0 and body["value"] == 10000.0
    assert body["invested"] == 200.0 and body["free"] == 9750.0 and body["skippedNoMoney"] == 0
    assert body["positions"] == [{"symbol": "NVDA", "contract": "150C · Nov 27", "qty": 1, "entry": 2.0, "mark": 2.5,
                                  "pnl": 50.0, "pct": 25.0, "at": stamp(10)}]


def test_a_capped_research_run_sizes_by_per_trade_money_and_skips_what_it_cannot_pay_for():
    run = {"n": 2, "money": 1000.0, "perTrade": 400.0, "startedAt": stamp(9, 30), "capped": True}
    books = ba.research_books(trades_of(
        vt("OLD", 1.0, 2.0, at=9, out=10),                 # before the run started: not its trade
        vt("AAA", 1.0, 1.5, at=10, out=12),                # 4 contracts, $400 -> +$200 at 12:00
        vt("BBB", 2.0, 1.0, at=10, minute=0, out=13),      # 2 contracts, $400 -> -$200 at 13:00
        vt("BIG", 5.0, 9.0, at=10),                        # one contract is $500, more than $400 a trade
        vt("CCC", 3.0, 6.0, at=11, out=14),                # 1 contract, $300 > the $200 free: no money
        vt("DDD", 1.5, at=12),                             # after AAA sold: $800 free, 2 contracts, still open
    ), run)
    body = ba.account_body("research", run, books, [], DAY)
    assert [t["pl"] for t in books["closed"]] == [200.0, -200.0]
    assert books["skippedNoMoney"] == 1 and books["skippedTooBig"] == 1
    assert [(p["symbol"], p["qty"]) for p in books["positions"]] == [("DDD", 2)]
    assert body["invested"] == 300.0 and body["free"] == 700.0 and body["value"] == 1000.0
    assert [o["status"] for o in books["orders"] if o["symbol"] in ("BIG", "CCC")] == \
        ["Skipped: no free money", "Skipped: costs more than the per-trade money"]


def test_a_per_trade_change_applies_from_when_it_was_made():
    run = {"n": 2, "money": 5000.0, "perTrade": 200.0, "startedAt": stamp(9, 30), "capped": True,
           "perTradeLog": [{"at": stamp(9, 30), "perTrade": 400.0}, {"at": stamp(11), "perTrade": 200.0}]}
    books = ba.research_books(trades_of(vt("AAA", 1.0, 2.0, at=10), vt("BBB", 1.0, 2.0, at=12)), run)
    assert [t["pl"] for t in books["closed"]] == [400.0, 200.0]            # 4 contracts, then 2


# ---------------------------------------------------------------- paper rows
def paper_row(symbol="AAPL", status="closed", at=stamp(10), **kw):
    r = {"side": "bull", "rule": "level1", "symbol": symbol, "at": at, "contract": f"{symbol:<6}261127C00150000", "qty": 3,
         "buyLimit": 1.0, "buyFill": 1.0, "buyAt": at, "buyFillAt": at, "status": status, "exitPlan": pm.EXIT_PLAN}
    r.update(kw)
    return r


def test_paper_books_split_closed_open_and_waiting_by_the_run():
    run = {"n": 2, "money": 2000.0, "perTrade": 300.0, "startedAt": stamp(9, 30), "capped": True}
    rows = [
        paper_row("WIN", pl=150.0, closedAt=stamp(11)),
        paper_row("OLD", pl=999.0, at=stamp(10, day="2026-11-19"), closedAt=stamp(15, day="2026-11-19")),    # closed before the run
        paper_row("CARRY", pl=-60.0, at=stamp(10, day="2026-11-19"), closedAt=stamp(10, 5)),                 # bought before, closed inside
        paper_row("HELD", status="open", held=1, markMid=1.5, sells=[{"qty": 2, "price": 2.0, "why": "T1"}]),
        paper_row("NOMARK", status="open", buyFill=2.0),
        paper_row("WAIT", status="buying", buyFill=None, buyFillAt=None, buyLimit=0.5, qty=4),
        paper_row("MISS", status="missed", buyFill=None),
        paper_row("SKIP", status="skipped", buyFill=None),
    ]
    books = ba.paper_books(rows, run)
    body = ba.account_body("paper3", run, books, [], DAY)
    assert sorted(t["pl"] for t in books["closed"]) == [-60.0, 150.0]
    held = next(p for p in body["positions"] if p["symbol"] == "HELD")
    assert held == {"symbol": "HELD", "contract": "150C · Nov 27", "qty": 1, "entry": 1.0, "mark": 1.5, "pnl": 250.0,
                    "pct": 83.3, "at": stamp(10)}                              # 2 sold at +$1.00, 1 held at +$0.50
    nomark = next(p for p in body["positions"] if p["symbol"] == "NOMARK")
    assert nomark["mark"] is None and nomark["pnl"] is None and nomark["qty"] == 3
    assert body["invested"] == 700.0 and body["openPnl"] == 250.0 and body["totalPnl"] == 340.0 and body["value"] == 2340.0
    # $2,000 + $90 closed + $200 banked - $700 held - $200 waiting to fill
    assert body["free"] == 1390.0 and body["reserved"] == 200.0
    assert body["dayPnl"] == 340.0
    statuses = {o["symbol"]: o["status"] for o in body["orders"]}
    assert statuses == {"WIN": "Closed", "CARRY": "Closed", "HELD": "Open", "NOMARK": "Open", "WAIT": "Waiting to fill",
                        "MISS": "Not filled"}


def test_the_first_paper_run_counts_what_the_scorecard_counts(monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-13")
    run = {"n": 1, "money": 10000.0, "perTrade": 300.0, "startedAt": "2026-11-13T00:00:00-05:00", "capped": False}
    rows = [paper_row("NEW", pl=40.0, closedAt=stamp(11)),
            paper_row("OLDPLAN", pl=-591.0, closedAt=stamp(11), exitPlan=None),                               # the plan before
            paper_row("BEFORE", pl=-70.0, at=stamp(10, day="2026-11-12"), closedAt=stamp(10))]                # signalled before the start
    assert ba.paper_books(rows, run)["closed"] == [{"day": DAY, "pl": 40.0}]


# ---------------------------------------------------------------- the accounts
class Trader:
    """A stand-in Ticker trader: only what the accounts read and call."""

    def __init__(self):
        self.size, self.resets, self.fail = 10000.0, [], None
        self.runs = [{"id": "NVDA-CALL", "createdAt": stamp(9, 40), "trades": [
            {"ticker": "NVDA", "side": "CALL", "contract": "NVDA  261120C00104000", "qty": 3, "entry": 1.0, "pl": 120.0,
             "boughtAt": stamp(9, 45), "closedAt": stamp(10, 30), "day": DAY}],
            "open": {"ticker": "NVDA", "side": "CALL", "contract": "NVDA  261120C00105000", "qty": 3, "held": 2, "entry": 2.0,
                     "bid": 2.5, "pl": 30.0, "cost": 600.0, "boughtAt": stamp(10, 40)}}]

    def snapshot(self):
        return {"runs": self.runs, "account": {"startBalance": self.size, "resetAt": None}, "asOf": stamp(11)}

    def reset(self, run_id=None, size=None):
        if self.fail:
            raise self.fail
        self.resets.append((run_id, size))
        self.size, self.runs = size, []


def write_day(folder, day, rows):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps({"day": day, "rows": rows}), encoding="utf-8")


@pytest.fixture
def desk(tmp_path, monkeypatch):
    """The accounts over scratch files: one closed paper3 trade, two Research trades, a Ticker trade."""
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-13")
    paper, grade, trader = tmp_path / "paper", tmp_path / "grade", Trader()
    write_day(paper / "paper3", DAY, {"k1": paper_row("AAPL", pl=-90.0, closedAt=stamp(10, 30)),
                                     "k2": paper_row("MSFT", status="open", held=3, markMid=1.2)})
    track = grade / "momx_option_track"
    track.mkdir(parents=True)
    (track / "2026-11-19.json").write_text(json.dumps({"trades": [vt("AAPL", 1.0, 1.6)]}).replace("2026-11-20", "2026-11-19"),
                                           encoding="utf-8")
    (track / f"{DAY}.json").write_text(json.dumps({"trades": [vt("MSFT", 2.0, 1.5), vt("NVDA", 1.0, eLast=1.4, at=10)]}), encoding="utf-8")
    book = ba.BotAccounts(tmp_path / "store", lambda account: paper / account,
                          lambda: [("bull", grade), ("bear", grade / "bear")], lambda: trader, clock=lambda: NOW)
    book.trader, book.paper, book.grade = trader, paper, grade
    return book


def test_every_account_starts_on_an_implicit_first_run(desk):
    snap = desk.snapshot()
    a = snap["accounts"]
    assert snap["ok"] is True and snap["order"] == ["paper3", "paper5", "research", "ticker"] and snap["asOf"] == "2026-11-20T11:00:00-05:00"
    assert a["paper3"]["run"] == {"n": 1, "money": 10000.0, "perTrade": pm.BUDGET, "startedAt": "2026-11-13T00:00:00-05:00", "capped": False}
    assert a["paper3"]["value"] == 9970.0 and a["paper3"]["totalPnl"] == -30.0 and a["paper3"]["openPnl"] == 60.0
    assert a["paper3"]["journal"]["tiles"]["closed"] == 1 and a["paper3"]["journal"]["days"] == [{"day": DAY, "pnl": -90.0, "trades": 1}]
    assert a["paper5"]["value"] == 10000.0 and a["paper5"]["positions"] == [] and a["paper5"]["error"] is None
    # Research: from the first saved day, every trade one contract. +$60, -$50 closed, +$40 open.
    assert a["research"]["run"] == {"n": 1, "money": 10000.0, "perTrade": None, "startedAt": "2026-11-19T00:00:00-05:00", "capped": False}
    assert a["research"]["totalPnl"] == 50.0 and a["research"]["dayPnl"] == -10.0 and a["research"]["journal"]["tiles"]["closed"] == 2
    # Ticker: whatever the Ticker trader reports. +$120 closed, open: $30 banked + 2 x $0.50.
    assert a["ticker"]["run"] == {"n": 1, "money": 10000.0, "perTrade": 1000.0, "startedAt": stamp(9, 40), "capped": True}
    assert a["ticker"]["value"] == 10250.0 and a["ticker"]["invested"] == 400.0 and a["ticker"]["free"] == 9750.0
    assert [k for k, v in a.items() if v["canTradeTicker"]] == ["ticker"]
    assert all(v["resets"] == 0 and v["pastRuns"] == [] and v["kind"] and v["note"] and v["label"] for v in a.values())
    assert not (desk.directory / ba.RUNS_FILE).exists()                       # reading never writes


def test_a_reset_freezes_the_run_and_starts_the_next_with_the_new_money(desk):
    after = desk.reset("paper3", 5000, 250, who="owner@example.com")
    assert after["run"] == {"n": 2, "money": 5000.0, "perTrade": 250.0, "startedAt": "2026-11-20T11:00:00-05:00", "capped": True}
    assert after["resets"] == 1 and after["value"] == 5060.0 and after["journal"]["tiles"]["closed"] == 0
    past = after["pastRuns"][0]
    assert {k: past[k] for k in ("n", "money", "perTrade", "startedAt", "endedAt", "endValue", "pnl", "pnlPct", "trades")} == {
        "n": 1, "money": 10000.0, "perTrade": pm.BUDGET, "startedAt": "2026-11-13T00:00:00-05:00",
        "endedAt": "2026-11-20T11:00:00-05:00", "endValue": 9970.0, "pnl": -30.0, "pnlPct": -0.3, "trades": 1}
    assert past["days"] == [{"day": DAY, "pnl": -90.0, "trades": 1}] and past["tiles"]["losses"] == 1
    # Frozen: the saved rows changing later does not change the past run.
    write_day(desk.paper / "paper3", DAY, {"k1": paper_row("AAPL", pl=-9000.0, closedAt=stamp(10, 30))})
    assert desk.account("paper3")["pastRuns"][0]["pnl"] == -30.0
    # Saved through a temp file and read back by a fresh start.
    saved = json.loads((desk.directory / ba.RUNS_FILE).read_text(encoding="utf-8"))
    assert saved["accounts"]["paper3"]["run"]["n"] == 2 and saved["log"][-1]["who"] == "owner@example.com"
    assert not list(desk.directory.glob("*.tmp"))
    again = ba.BotAccounts(desk.directory, lambda account: desk.paper / account, lambda: [], lambda: None, clock=lambda: NOW)
    assert again.account("paper3")["run"]["n"] == 2 and again.account("paper3")["pastRuns"][0]["endValue"] == 9970.0
    # A second reset: newest past run first.
    later = desk.reset("paper3", 2000, now=NOW + timedelta(hours=1))
    assert [p["n"] for p in later["pastRuns"]] == [2, 1] and later["run"]["n"] == 3 and later["run"]["perTrade"] == 250.0


def test_a_reset_research_run_is_capped_from_its_reset_time(desk, monkeypatch):
    monkeypatch.setattr(ba, "RESEARCH_REUSE_SECONDS", 0.0)                    # today's file is re-read at once
    today = desk.grade / "momx_option_track" / f"{DAY}.json"
    later = today.read_text(encoding="utf-8")
    today.write_text(json.dumps({"trades": []}), encoding="utf-8")            # 9:45: nothing traded yet today
    desk.reset("research", 1000, 100, now=datetime(2026, 11, 20, 9, 45, tzinfo=ET))
    today.write_text(later, encoding="utf-8")
    after = desk.account("research")
    assert after["run"]["capped"] is True and after["pastRuns"][0]["trades"] == 1 and after["pastRuns"][0]["pnl"] == 60.0
    # Both of today's trades start at 10:00, after the reset: MSFT ($200 a contract) costs more than $100 a trade;
    # NVDA at $1.00 is one contract, still open at $1.40.
    assert after["skippedTooBig"] == 1 and after["journal"]["tiles"]["closed"] == 0
    assert after["positions"][0]["symbol"] == "NVDA" and after["openPnl"] == 40.0 and after["free"] == 900.0


def test_resetting_the_ticker_account_resets_the_ticker_trader_and_keeps_the_run(desk):
    after = desk.reset("ticker", 2000)
    assert desk.trader.resets == [(None, 2000.0)]
    assert after["run"]["n"] == 2 and after["run"]["money"] == 2000.0 and after["value"] == 2000.0 and after["positions"] == []
    assert after["pastRuns"][0]["endValue"] == 10250.0 and after["pastRuns"][0]["trades"] == 1 and after["pastRuns"][0]["openAtEnd"] == 1


def test_a_ticker_reset_that_fails_changes_nothing(desk):
    desk.trader.fail = ticker_trader.TraderError(409, "The Ticker trader is busy.")
    with pytest.raises(ba.AccountsError) as refused:
        desk.reset("ticker", 2000)
    assert refused.value.status == 409 and "Nothing was reset" in refused.value.message
    assert desk.account("ticker")["run"]["n"] == 1 and desk.account("ticker")["pastRuns"] == []
    assert json.loads((desk.directory / ba.RUNS_FILE).read_text(encoding="utf-8"))["accounts"] == {}


@pytest.mark.parametrize("account, money, per_trade, words", [
    ("paper9", 5000, None, "Pick one of the four accounts"),
    (None, 5000, None, "Pick one of the four accounts"),
    ("paper3", 499, None, "between $500 and $1,000,000"),
    ("paper3", 1_000_001, None, "between $500 and $1,000,000"),
    ("paper3", "lots", None, "must be a number of dollars"),
    ("paper3", True, None, "must be a number of dollars"),
    ("paper3", 5000, 49, "between $50 and the account's $5,000"),
    ("paper3", 5000, 5001, "between $50 and the account's $5,000"),
])
def test_a_bad_reset_is_refused_in_plain_words_and_changes_nothing(desk, account, money, per_trade, words):
    with pytest.raises(ba.AccountsError) as refused:
        desk.reset(account, money, per_trade)
    assert refused.value.status == 400 and words in refused.value.message
    assert not (desk.directory / ba.RUNS_FILE).exists()


def test_money_per_trade_changes_only_in_a_run_a_reset_started(desk):
    with pytest.raises(ba.AccountsError) as first:
        desk.set_per_trade("paper3", 200)
    with pytest.raises(ba.AccountsError) as ticker:
        desk.set_per_trade("ticker", 200)
    assert first.value.status == 409 and "has not been reset yet" in first.value.message
    assert ticker.value.status == 409 and "sizes each trade itself" in ticker.value.message
    desk.reset("paper3", 5000, 250)
    changed = desk.set_per_trade("paper3", "400", now=NOW + timedelta(minutes=5))
    assert changed["run"]["perTrade"] == 400.0 and changed["run"]["n"] == 2 and changed["resets"] == 1
    with pytest.raises(ba.AccountsError) as too_big:
        desk.set_per_trade("paper3", 5001)
    assert too_big.value.status == 400 and "between $50 and the account's $5,000" in too_big.value.message
    assert desk.account("paper3")["run"]["perTrade"] == 400.0


def test_one_account_failing_does_not_fail_the_others(desk):
    desk._ticker = lambda: None

    def broken(account):
        if account == "paper5":
            raise RuntimeError("disk")
        return desk.paper / account

    desk._paper_folder = broken
    a = desk.snapshot()["accounts"]
    assert a["ticker"]["error"] == "The Ticker trader is not running yet." and a["ticker"]["value"] == 10000.0
    assert a["paper5"]["error"] == "This account could not be read (RuntimeError)." and a["paper5"]["positions"] == []
    assert a["paper3"]["error"] is None and a["paper3"]["value"] == 9970.0 and a["research"]["error"] is None
    with pytest.raises(ba.AccountsError) as refused:
        desk.reset("ticker", 2000)
    assert refused.value.status == 503


def test_research_says_so_while_its_history_is_still_being_read(desk):
    (desk.grade / "momx_option_track" / "2026-11-18.json").write_text(json.dumps({"trades": []}), encoding="utf-8")
    ticks = iter(range(0, 1000, 10))                                          # a slow disk: one file a read
    desk._research = ba.ResearchReader(lambda: [("bull", desk.grade)], clock=lambda: next(ticks), budget=5.0)
    first = desk.account("research")
    assert first["error"] == ba.STILL_READING and first["journal"]["tiles"]["closed"] == 0
    with pytest.raises(ba.AccountsError) as refused:
        desk.reset("research", 1000)
    assert refused.value.status == 409 and "Nothing was reset" in refused.value.message
    done = desk.account("research")                                           # the next read carries on
    assert done["error"] is None and done["journal"]["tiles"]["closed"] == 2


# ---------------------------------------------------------------- the live paper bot (momx/paper_mirror.py)
def test_the_paper_bot_gets_no_cap_until_a_reset(desk):
    assert desk.paper_cap("paper3", desk.paper / "paper3", DAY) is None
    assert desk.paper_cap("research", desk.grade, DAY) is None
    desk.reset("paper5", 2000, 400)
    assert desk.paper_cap("paper3", desk.paper / "paper3", DAY) is None
    assert ba.paper_cap("paper5", desk.paper / "paper5", DAY) is None          # the accounts are not running here


def test_a_runs_free_money_is_its_money_and_closed_pnl_less_what_is_held_or_on_order(desk):
    desk.reset("paper3", 2000, 400, now=datetime(2026, 11, 19, 12, 0, tzinfo=ET))
    write_day(desk.paper / "paper3", "2026-11-19", {
        "y1": paper_row("YES", pl=100.0, at=stamp(13, day="2026-11-19"), closedAt=stamp(14, day="2026-11-19")),
        "y0": paper_row("PRE", pl=-500.0, at=stamp(10, day="2026-11-19"), closedAt=stamp(11, day="2026-11-19"))})   # before the reset
    cap = desk.paper_cap("paper3", desk.paper / "paper3", DAY)
    today = {"k1": paper_row("AAPL", pl=-90.0, closedAt=stamp(10, 30)),
             "k2": paper_row("MSFT", status="open", held=3, markMid=1.2),
             "k3": paper_row("WAIT", status="buying", buyFill=None, buyLimit=0.5, qty=4)}
    assert cap.per_trade == 400.0
    assert cap.free(today) == 2000.0 + 100.0 - 90.0 - 300.0 - 200.0


def run_mirror(folder, client, run_cap, trades=None):
    trades = trades if trades is not None else [_trade(), _trade(symbol="MSFT", contract="MSFT261023C00400000")]
    mirror = pm.PaperMirror({"bull": Book(trades)}, folder, client_factory=lambda: client, off=lambda: False, run_cap=run_cap)
    mirror.tick(MIRROR_NOW)
    mirror.tick(MIRROR_NOW + timedelta(seconds=10))
    return mirror


def test_with_no_capped_run_the_paper_bot_does_exactly_what_it_did(tmp_path):
    """No reset = the same orders and the same saved file, byte for byte: with no cap wired
    (as before this feature), with the accounts running, and with another account reset."""
    day = MIRROR_NOW.date().isoformat()
    book = ba.BotAccounts(tmp_path / "store", lambda account: tmp_path / "c" / account, lambda: [], lambda: None,
                          clock=lambda: MIRROR_NOW)
    book.reset("paper5", 600, 50)                                             # another account: not this one's cap
    results = []
    for name, cap in (("a", None), ("b", lambda *_: None), ("c", book.paper_cap)):
        client = FakeClient()
        mirror = run_mirror(tmp_path / name / "paper3", client, cap)
        results.append((client.calls, (mirror.directory / f"{day}.json").read_bytes()))
    assert results[0] == results[1] == results[2]
    assert [c[:4] for c in results[0][0]] == [("limit", "AAPL261023C00250000", 3, 1.25), ("limit", "MSFT261023C00400000", 3, 1.25)]


def test_a_cap_that_cannot_be_read_never_stops_the_paper_bot(tmp_path):
    def broken(*_):
        raise RuntimeError("runs file unreadable")

    client = FakeClient()
    run_mirror(tmp_path / "paper3", client, broken)
    assert len(client.calls) == 2


def capped_book(tmp_path, money, per_trade):
    book = ba.BotAccounts(tmp_path / "store", lambda account: tmp_path / "mirror" / account, lambda: [], lambda: None,
                          clock=lambda: MIRROR_NOW - timedelta(hours=1))
    book.reset("paper3", money, per_trade)
    return book


def test_a_capped_run_buys_with_its_per_trade_money(tmp_path):
    book, client = capped_book(tmp_path, 5000, 1000), FakeClient()
    mirror = run_mirror(tmp_path / "mirror" / "paper3", client, book.paper_cap)
    # $1,000 a trade at the $1.25 ask = 8 contracts (it was 3 on the $300 budget).
    assert [c[1:4] for c in client.calls] == [("AAPL261023C00250000", 8, 1.25), ("MSFT261023C00400000", 8, 1.25)]
    assert mirror._fund_skips == {}
    body = book.account("paper3", MIRROR_NOW + timedelta(seconds=20))
    assert body["invested"] == 2000.0 and body["free"] == 3000.0 and len(body["positions"]) == 2


def test_a_capped_run_skips_a_buy_it_has_no_free_money_for_and_says_why(tmp_path):
    book, client = capped_book(tmp_path, 500, 50), FakeClient()
    mirror = run_mirror(tmp_path / "mirror" / "paper3", client, book.paper_cap)
    # The first buy (3 contracts, the least it buys) takes $375 of the run's $500; the second needs $375 too.
    assert len(client.calls) == 1 and client.calls[0][2] == 3
    skips = list(mirror._fund_skips.values())
    assert len(skips) == 1 and skips[0]["why"] == "no free money in this run ($375 needed, $125 free)"
    saved = json.loads((mirror.directory / f"{MIRROR_NOW.date().isoformat()}.json").read_text(encoding="utf-8"))
    assert list(saved["fundSkips"].values())[0]["why"] == skips[0]["why"] and len(saved["rows"]) == 1


def test_a_capped_run_with_no_free_money_still_sells(tmp_path):
    book, client = capped_book(tmp_path, 500, 50), FakeClient()
    trades = [_trade()]
    mirror = run_mirror(tmp_path / "mirror" / "paper3", client, book.paper_cap, trades)
    trades[0].update(exitPrice=1.6, exitBid=1.5, exitAsk=1.7, exitReason="target")
    mirror.tick(MIRROR_NOW + timedelta(seconds=20))
    assert client.calls[-1] == ("limit", "AAPL261023C00250000", 3, 1.6, "sell_to_close")
    mirror.tick(MIRROR_NOW + timedelta(seconds=30))
    body = book.account("paper3", MIRROR_NOW + timedelta(seconds=40))
    assert body["journal"]["tiles"]["netPnl"] == 105.0 and body["free"] == 605.0 and body["positions"] == []


# ---------------------------------------------------------------- the Research history reader
def test_older_research_days_are_kept_as_totals_and_give_the_same_numbers(desk, monkeypatch):
    whole = desk.account("research")
    monkeypatch.setattr(ba, "RESEARCH_FULL_DAYS", 1)                          # only today trade by trade
    desk._research, desk._research_memo = ba.ResearchReader(lambda: [("bull", desk.grade)]), None
    slim = desk.account("research")
    kept = {Path(k).stem: v[3] is not None for k, v in desk._research._files.items()}
    assert kept == {"2026-11-19": False, DAY: True}
    assert slim["journal"] == whole["journal"] and slim["value"] == whole["value"] and slim["positions"] == whole["positions"]
    assert slim["journal"]["days"] == [{"day": "2026-11-19", "pnl": 60.0, "trades": 1}, {"day": DAY, "pnl": -50.0, "trades": 1}]


def test_a_run_started_by_a_reset_never_reads_the_days_before_it(desk):
    desk.reset("research", 1000, 100)
    desk._research = ba.ResearchReader(lambda: [("bull", desk.grade)])
    desk.account("research")
    assert [Path(k).stem for k in desk._research._files] == [DAY]


def test_the_history_is_read_ahead_one_file_at_a_time(desk):
    reader, pauses = ba.ResearchReader(lambda: [("bull", desk.grade)]), []
    desk._research = reader
    reader.warm(lambda: DAY, desk._research_run, sleep=pauses.append)
    assert len(pauses) == 1 and len(reader._files) == 2                       # two files: one pause between them
    assert desk.account("research")["error"] is None
