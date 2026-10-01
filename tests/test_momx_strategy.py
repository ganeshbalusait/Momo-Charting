"""momx/strategy.py - the V2 / V3 / Daily 2 rules the FILTERS Strategy choices read."""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

from momx import strategy

ET = ZoneInfo("America/New_York")


def at(hhmm: str, day: str = "2026-09-23") -> datetime:
    h, m = hhmm.split(":")
    return datetime.fromisoformat(f"{day}T{int(h):02d}:{int(m):02d}:00").replace(tzinfo=ET)


def row(symbol="ARM", letter="A+", state="extended", rvol=("5m", "15m"), sqz=("D",), pct=1.0, last=100.0,
        signal=None):
    """A board row. ``signal`` = the datetime its first ``letter`` of the day was
    latched by the grade recorder (gradeFresh.firstToday) - None = no signal."""
    first = {"A+": None, "A": None, "B": None}
    if signal is not None:
        first[letter] = {"at": signal.isoformat(), "price": last}
    return {
        "symbol": symbol,
        "last": last,
        "pctChange": pct,
        "grade": {"letter": letter, "checks": {"pushRvol": list(rvol), "sqzFired": list(sqz)}},
        "m5": {"state": state},
        "gradeFresh": {"firstToday": first},
    }


def s3(r):
    """The V2 / V3 / Daily 2 part of row["strategy"] (G rides beside it)."""
    return {k: r["strategy"][k] for k in ("v2", "v3", "daily2")}


# ------------------------------------------------------------------ the rules

def test_v2_needs_every_rule():
    now = at("09:40")
    assert strategy.evaluate(row(), now) == {"v2": True, "v3": True, "daily2": True}
    assert strategy.evaluate(row(letter="B"), now)["v2"] is False
    assert strategy.evaluate(row(letter="A"), now)["v2"] is True
    assert strategy.evaluate(row(state="fading"), now)["v2"] is False
    assert strategy.evaluate(row(state="building"), now)["v2"] is False
    assert strategy.evaluate(row(rvol=()), now)["v2"] is False
    assert strategy.evaluate(row(pct=10.0), now)["v2"] is False
    assert strategy.evaluate(row(pct=9.99), now)["v2"] is True


def test_not_before_0935_and_not_after_the_close():
    assert strategy.evaluate(row(), at("09:34"))["v2"] is False
    assert strategy.evaluate(row(), at("09:35"))["v2"] is True
    assert strategy.evaluate(row(), at("15:59"))["v2"] is True
    assert strategy.evaluate(row(), at("16:00"))["v2"] is False


def test_v3_adds_squeeze_fired():
    assert strategy.evaluate(row(sqz=()), at("10:00")) == {"v2": True, "v3": False, "daily2": True}


def test_daily2_needs_rvol_on_two_timeframes():
    now = at("10:00")
    assert strategy.evaluate(row(rvol=("5m",)), now)["daily2"] is False
    assert strategy.evaluate(row(rvol=("5m", "30m")), now)["daily2"] is True


def test_a_missing_cell_never_passes():
    now = at("10:00")
    assert strategy.evaluate({"symbol": "X"}, now) == {"v2": False, "v3": False, "daily2": False}
    bad = row()
    bad["pctChange"] = None
    assert strategy.evaluate(bad, now)["v2"] is False
    bad = row()
    bad["m5"] = None
    assert strategy.evaluate(bad, now)["v2"] is False


# --------------------------------------------- judged at the SIGNAL, then kept

def board(*rows):
    return {"rows": list(rows), "rest": []}


def test_a_row_is_judged_only_when_its_first_letter_of_the_day_fires(tmp_path):
    # The back-test judged each ticker at its first A/A+ signal. Judging every
    # build instead put 21 tickers on Daily 2 by 10:02 on 2026-09-22 (RVOL pins
    # high on most of the board in the first minutes) - not the tested rule.
    book = strategy.StrategyBook(tmp_path)
    old = row("MU", signal=at("09:36"))  # its signal was an earlier build
    p = board(old)
    book.apply("Watchlist", p, at("09:50"))
    assert s3(p["rows"][0]) == {"v2": False, "v3": False, "daily2": None}


def test_a_signal_before_0935_is_never_judged_again_later(tmp_path):
    # HOOD 2026-09-22: A+ at 09:34 -> outside the rule; still A+ and Extended
    # at 09:35, but that is not a new signal, so it never qualifies.
    book = strategy.StrategyBook(tmp_path)
    book.apply("Watchlist", board(row("HOOD", signal=at("09:34"))), at("09:34"))
    p = board(row("HOOD", signal=at("09:34")))
    book.apply("Watchlist", p, at("09:35"))
    assert p["rows"][0]["strategy"]["v2"] is False


def test_daily2_latches_in_order_and_stays_on_the_list(tmp_path):
    book = strategy.StrategyBook(tmp_path)
    p1 = board(row("ARM", last=140.0, signal=at("09:38")), row("MU", rvol=("5m",), signal=at("09:38")))
    book.apply("Watchlist", p1, at("09:38"))
    arm, mu = p1["rows"]
    assert s3(arm) == {
        "v2": True, "v3": True,
        "daily2": {"rank": 1, "at": "2026-09-23T09:38:00-04:00", "price": 140.0},
    }
    assert s3(mu) == {"v2": True, "v3": True, "daily2": None}, "V2 but RVOL on one timeframe"

    # Later: ARM turns fading - it stays V2 and Daily 2 #1 (the day's entry).
    p2 = board(row("ARM", state="fading", signal=at("09:38")), row("STX", last=50.0, sqz=(), signal=at("09:44")))
    book.apply("Watchlist", p2, at("09:44"))
    arm, stx = p2["rows"]
    assert arm["strategy"]["v2"] is True and arm["strategy"]["daily2"]["rank"] == 1
    assert s3(stx) == {
        "v2": True, "v3": False,
        "daily2": {"rank": 2, "at": "2026-09-23T09:44:00-04:00", "price": 50.0},
    }
    assert [d["symbol"] for d in p2["strategyDaily2"]] == ["ARM", "STX"]


def test_a_later_a_plus_signal_can_qualify_a_symbol_whose_a_did_not(tmp_path):
    book = strategy.StrategyBook(tmp_path)
    p = board(row("NVDA", letter="A", state="fading", signal=at("09:40")))
    book.apply("Mag7", p, at("09:40"))
    assert p["rows"][0]["strategy"]["v2"] is False
    later = row("NVDA", letter="A+", signal=at("10:10"))
    later["gradeFresh"]["firstToday"]["A"] = {"at": at("09:40").isoformat(), "price": 100.0}
    p = board(later)
    book.apply("Mag7", p, at("10:10"))
    assert p["rows"][0]["strategy"]["v2"] is True
    assert p["rows"][0]["strategy"]["daily2"]["rank"] == 1


def test_rank_is_shared_across_boards(tmp_path):
    book = strategy.StrategyBook(tmp_path)
    book.apply("Watchlist", board(row("ARM", signal=at("09:38"))), at("09:38"))
    # ARM's own first letter on Mag7 is a separate recorder latch - no new rank.
    mag = board(row("NVDA", signal=at("09:40")), row("ARM", signal=at("09:40")))
    book.apply("Mag7", mag, at("09:40"))
    assert mag["rows"][0]["strategy"]["daily2"]["rank"] == 2
    assert mag["rows"][1]["strategy"]["daily2"]["rank"] == 1


def test_latch_survives_a_restart_and_resets_next_day(tmp_path):
    strategy.StrategyBook(tmp_path).apply("Watchlist", board(row("ARM", sqz=(), signal=at("09:38"))), at("09:38"))
    saved = json.loads((tmp_path / "momx_strategy" / "2026-09-23.json").read_text(encoding="utf-8"))
    assert [d["symbol"] for d in saved["daily2"]] == ["ARM"]
    assert [d["symbol"] for d in saved["v2"]] == ["ARM"]
    assert saved["v3"] == []

    again = strategy.StrategyBook(tmp_path)
    p = board(row("ARM", sqz=(), signal=at("09:38")), row("STX", signal=at("10:05")))
    again.apply("Watchlist", p, at("10:05"))
    assert s3(p["rows"][0]) == {
        "v2": True, "v3": False, "daily2": {"rank": 1, "at": "2026-09-23T09:38:00-04:00", "price": 100.0},
    }
    assert p["rows"][1]["strategy"]["daily2"]["rank"] == 2

    tomorrow = board(row("STX", signal=at("09:40", day="2026-09-24")))
    again.apply("Watchlist", tomorrow, at("09:40", day="2026-09-24"))
    assert tomorrow["rows"][0]["strategy"]["daily2"]["rank"] == 1


def test_nothing_latches_outside_the_window(tmp_path):
    book = strategy.StrategyBook(tmp_path)
    p = board(row("ARM", signal=at("09:31")))
    book.apply("Watchlist", p, at("09:31"))
    assert s3(p["rows"][0]) == {"v2": False, "v3": False, "daily2": None}
    assert not (tmp_path / "momx_strategy").exists()


# ------------------------------------------------------------ Strategy G

def g_row(symbol="PLTR", last=191.07, rvol30=3.4, t="09:50"):
    """A row passing G rules 1-4 (rule 1 needs a previous, lower 30m reading)."""
    def ep(hhmm):
        return int(at(hhmm).timestamp())
    return {
        "symbol": symbol, "last": last, "pctChange": 1.0,
        "grade": {"letter": None, "checks": {}},
        "rvol": {"30m": {"value": rvol30, "bg": "cyan"}},
        "sqz": {"2h": {"bg": "black"}, "4h": {"bg": "cyan"}},
        "skittles": {"2h": {"bg": "cyan", "barAt": ep("09:00")}, "4h": {"bg": "cyan", "barAt": ep("08:00")}},
        "m5": {"state": "holding", "ema20": 189.0, "vwap": 188.5, "crossUpAt": None,
               "lastCompleted": {"close": last, "time": ep(t)}},
    }


def chain(spot=191.07, entry_bid=0.66, entry_ask=0.70, t195=(1.75, 1.83)):
    def c(strike, oi, delta, bid, ask):
        return {"side": "CALL", "strike": strike, "open_interest": oi, "delta": delta, "gamma": 0.03,
                "bid": bid, "ask": ask, "expiry": "2026-09-25", "days_to_expiration": 2,
                "symbol": f"PLTR  260925C{int(strike * 1000):08d}"}
    return {"underlyingPrice": spot, "selectedExpiryChainRows": [
        c(192.5, 4946, 0.47, 2.73, 2.81), c(195.0, 13624, 0.35, *t195), c(200.0, 5867, 0.16, entry_bid, entry_ask)]}


class FakeChains:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def __call__(self, symbol):
        self.calls.append(symbol)
        return self.payload


def g_book(tmp_path, payload):
    fetch = FakeChains(payload)
    return strategy.StrategyBook(tmp_path, chain_fetcher=fetch, background=False), fetch


def test_g_latches_with_the_option_plan_once_rules_and_roi_pass(tmp_path):
    book, fetch = g_book(tmp_path, chain())
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("09:48"))       # first reading: not rising yet
    p = board(g_row(rvol30=3.4))
    book.apply("Watchlist", p, at("09:50"))                                # rising -> fetch chain -> latch
    g = p["rows"][0]["strategy"]["g"]
    assert g["rank"] == 1 and g["target"] == 195.0 and g["entryStrike"] == 200.0
    assert round(g["roi"]) == 163 and g["status"] == "open"
    assert fetch.calls == ["PLTR"]
    saved = json.loads((tmp_path / "momx_strategy" / "2026-09-23.json").read_text(encoding="utf-8"))
    assert saved["g"][0]["entrySymbol"] == "PLTR  260925C00200000"


def test_g_does_not_latch_when_roi_is_under_150(tmp_path):
    book, _ = g_book(tmp_path, chain(t195=(1.30, 1.34)))
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("09:48"))
    p = board(g_row(rvol30=3.4))
    book.apply("Watchlist", p, at("09:50"))
    assert p["rows"][0]["strategy"]["g"] is None


def test_g_exits_at_target_with_the_real_option_price(tmp_path):
    book, fetch = g_book(tmp_path, chain())
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("09:48"))
    book.apply("Watchlist", board(g_row(rvol30=3.4)), at("09:50"))
    fetch.payload = chain(spot=195.2, entry_bid=1.70, entry_ask=1.80)     # PLTR reached the 195 wall
    p = board(g_row(last=195.2, rvol30=3.4))
    book.apply("Watchlist", p, at("10:05"))
    g = p["rows"][0]["strategy"]["g"]
    assert g["status"] == "target" and abs(g["exitPrice"] - 1.75) < 1e-9
    assert round(g["returnPct"]) == round((1.75 / 0.68 - 1) * 100)


def test_g_stops_when_the_option_loses_half(tmp_path):
    book, fetch = g_book(tmp_path, chain())
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("09:48"))
    book.apply("Watchlist", board(g_row(rvol30=3.4)), at("09:50"))
    fetch.payload = chain(spot=188.0, entry_bid=0.30, entry_ask=0.32)
    p = board(g_row(last=188.0, rvol30=3.4))
    book.apply("Watchlist", p, at("09:56"))                               # 6 min later: re-priced
    g = p["rows"][0]["strategy"]["g"]
    assert g["status"] == "stop" and round(g["returnPct"]) == round((0.31 / 0.68 - 1) * 100)


def test_g_sells_at_the_close_otherwise(tmp_path):
    book, fetch = g_book(tmp_path, chain())
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("09:48"))
    book.apply("Watchlist", board(g_row(rvol30=3.4)), at("09:50"))
    fetch.payload = chain(spot=192.0, entry_bid=0.80, entry_ask=0.84)
    p = board(g_row(last=192.0, rvol30=3.4))
    book.apply("Watchlist", p, at("15:56"))
    assert p["rows"][0]["strategy"]["g"]["status"] == "close"


def test_g_never_fetches_chains_outside_the_session_or_for_non_candidates(tmp_path):
    book, fetch = g_book(tmp_path, chain())
    quiet = g_row(rvol30=3.4)
    quiet["rvol"]["30m"]["bg"] = "black"
    book.apply("Watchlist", board(quiet), at("10:00"))
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("08:00"))
    book.apply("Watchlist", board(g_row(rvol30=3.4)), at("08:05"))
    assert fetch.calls == []


def test_apply_never_raises(tmp_path):
    book = strategy.StrategyBook(tmp_path)
    book.apply("Watchlist", None, at("10:00"))
    book.apply("Watchlist", {"rows": "junk"}, at("10:00"))
    book.apply("Watchlist", {"rows": [None, 5, {"symbol": None}, {"symbol": "X", "gradeFresh": 3}]}, at("10:00"))


# ------------------------------------------------------------- BEAR (spec 2026-09-24)

def test_bear_v2_wants_down_less_than_ten_percent():
    r = row(pct=-4.0, signal=at("09:40"))
    assert strategy.evaluate(r, at("09:40"), direction="bear")["v2"] is True
    assert strategy.evaluate(row(pct=-12.0, signal=at("09:40")), at("09:40"), direction="bear")["v2"] is False
    assert strategy.evaluate(row(pct=4.0, signal=at("09:40")), at("09:40"), direction="bear")["v2"] is True
    assert strategy.evaluate(r, at("09:40"))["v2"] is True   # bull: -4% is "up less than 10"


def bear_g_row(symbol="PLTR", last=191.0, rvol30=3.4, t="09:50"):
    """A bear row passing G rules 1-4: price 191 is BELOW EMA20 193 and VWAP
    192.5 and still ABOVE the 187.5 put wall the plan will target."""
    def ep(hhmm):
        return int(at(hhmm).timestamp())
    return {
        "symbol": symbol, "last": last, "pctChange": -1.0, "direction": "bear",
        "grade": {"letter": None, "checks": {}},
        "rvol": {"30m": {"value": rvol30, "bg": "magenta"}},
        "sqz": {"2h": {"bg": "black"}, "4h": {"bg": "magenta"}},
        "skittles": {"2h": {"bg": "magenta", "barAt": ep("09:00")}, "4h": {"bg": "magenta", "barAt": ep("08:00")}},
        "m5": {"state": "holding", "ema20": 193.0, "vwap": 192.5, "crossUpAt": None, "crossDownAt": None,
               "lastCompleted": {"close": last, "time": ep(t)}},
    }


def put_chain(spot=191.07, entry_bid=1.00, entry_ask=1.10, wall=(2.60, 2.70)):
    def p(strike, oi, delta, bid, ask):
        return {"side": "PUT", "strike": strike, "open_interest": oi, "delta": -delta, "gamma": 0.03,
                "bid": bid, "ask": ask, "expiry": "2026-09-25", "days_to_expiration": 2,
                "symbol": f"PLTR  260925P{int(strike * 1000):08d}"}
    return {"underlyingPrice": spot, "selectedExpiryChainRows": [
        p(190.0, 4000, 0.45, 2.60, 2.70), p(187.5, 15000, 0.30, *wall), p(185.0, 20000, 0.18, entry_bid, entry_ask)]}


def test_bear_book_latches_a_put_plan_and_exits_when_price_falls_to_the_wall(tmp_path):
    fetch = FakeChains(put_chain())
    book = strategy.StrategyBook(tmp_path, chain_fetcher=fetch, background=False, direction="bear")
    book.apply("Watchlist", board(bear_g_row(rvol30=3.1)), at("09:48"))
    p = board(bear_g_row(rvol30=3.4))
    book.apply("Watchlist", p, at("09:50"))
    g = p["rows"][0]["strategy"]["g"]
    assert g["rank"] == 1 and g["side"] == "P" and g["target"] == 187.5 and g["entryStrike"] == 185.0
    assert g["status"] == "open"
    fetch.payload = put_chain(spot=187.3, entry_bid=2.50, entry_ask=2.60)   # fell TO the wall
    q = board(bear_g_row(last=187.3, rvol30=3.4))
    book.apply("Watchlist", q, at("10:05"))
    g = q["rows"][0]["strategy"]["g"]
    assert g["status"] == "target" and abs(g["exitPrice"] - 2.55) < 1e-9
    assert g["returnPct"] > 100


def test_bull_book_never_marks_a_target_on_a_fall(tmp_path):
    fetch = FakeChains(chain())
    book = strategy.StrategyBook(tmp_path, chain_fetcher=fetch, background=False)
    book.apply("Watchlist", board(g_row(rvol30=3.1)), at("09:48"))
    book.apply("Watchlist", board(g_row(rvol30=3.4)), at("09:50"))
    fetch.payload = chain(spot=185.0, entry_bid=0.60, entry_ask=0.64)
    q = board(g_row(last=185.0, rvol30=3.4))
    book.apply("Watchlist", q, at("10:05"))
    assert q["rows"][0]["strategy"]["g"]["status"] == "open"
