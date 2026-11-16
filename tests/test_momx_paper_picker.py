"""AI bot (2026-11-10): an AI ranks the Level signals waiting for one of Paper 5's slots.

momx/paper_picker.py and the paper mirror's use of its ranking. The AI and the
broker are fakes; nothing leaves the PC.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from momx import paper_mirror as pm
from momx import paper_picker as pp

ET = pm.ET
NOW = datetime(2026, 11, 10, 10, 10, tzinfo=ET)     # Tuesday, inside the first buy window


class FakeClient:
    def __init__(self):
        self.credentials = SimpleNamespace(paper=True)
        self.orders = {}
        self.calls = []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        self.calls.append((intent, symbol, qty, limit))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled", filled_avg_price=limit, filled_qty=qty)

    def get_order_by_client_id(self, cid):
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "canceled"


class Book:
    def __init__(self, trades):
        self.items = list(trades)

    def trades(self):
        return [dict(t) for t in self.items]


def signal(symbol, seconds_old, rule="level2", now=NOW, **kw):
    at = (now - timedelta(seconds=seconds_old)).isoformat()
    t = {"rule": rule, "symbol": symbol, "at": at, "entryAt": at, "status": "open", "virtualOnly": True,
         "levelSignal": {"highestLevel": int(rule[-1]), "setupTypes": [{"key": "tagRvol", "text": "RVOL"}, {"key": "tagSqz", "text": "SQZ"}]},
         "contract": f"{symbol}261113C00100000", "eContract": f"{symbol}261113C00110000", "eStrike": 110.0, "eDelta": 0.18,
         "eEntry": 0.60, "eLastBid": 0.58, "eLastAsk": 0.62, "eStatus": "open", "spot": 100.0, "stockPrice": 100.0,
         "expectedMove": 4.0, "momentum": "rising", "callWalls": [{"strike": 105.0, "oi": 900}, {"strike": 120.0, "oi": 500}],
         "market": {"spyPct": 0.42, "breadth": 0.61}, "lastAt": (now - timedelta(seconds=10)).isoformat()}
    t.update(kw)
    # A Level signal is bought only when the Bot gave it a chart plan (paper_mirror.has_plan,
    # 2026-11-13); plan=None in a test = the Bot gave it none.
    t.setdefault("plan", {"strike": t.get("eStrike")})
    return t


def ai_bot(tmp_path, trades, client, name="paper5"):
    book = Book(trades)
    m = pm.PaperMirror({"bull": book}, tmp_path / name, client_factory=lambda: client, off=lambda: False, account=name,
                       jev_cut=None, rules=pm.ACCOUNT_RULES[name], leg=pm.PAPER_LEG)
    m._load(NOW.date().isoformat())
    return m, book


def answer(*ids, provider="openai"):
    return lambda system, user: {"ok": True, "provider": provider, "data": {"ranking": [{"id": i, "why": f"reason {i}"} for i in ids]}}


def bought(client):
    return [c[1][:4].rstrip("2") for c in client.calls if c[0] == "buy_to_open"]


# ---------------------------------------------------------------- what the AI is shown
def test_a_signal_is_summarised_as_plain_facts():
    s = pp.summarize(3, "bull", signal("NVDA", 45), NOW.timestamp())
    assert s == {"id": 3, "ticker": "NVDA", "side": "calls", "level": 2, "setups": ["RVOL", "SQZ"], "momentum": "rising",
                 "secondsOld": 45, "optionPrice": 0.6, "spreadPct": 6.7, "delta": 0.18, "strikeAwayPct": 10.0,
                 "expectedMovePct": 4.0, "roomToWallPct": 5.0, "marketSpyPct": 0.42, "marketBreadth": 0.61,
                 "chart": {"highOiWallsAhead": [{"strike": 105.0, "awayPct": 5.0, "openInterest": 900},
                                                {"strike": 120.0, "awayPct": 20.0, "openInterest": 500}]}}
    put = pp.summarize(1, "bear", signal("TSLA", 5, putWalls=[{"strike": 90.0, "oi": 1}, {"strike": 96.0, "oi": 1}], eStrike=92.0), NOW.timestamp())
    assert (put["side"], put["roomToWallPct"], put["strikeAwayPct"]) == ("puts", 4.0, 8.0)       # nearest put wall BELOW the price
    bare = pp.summarize(1, "bull", {"symbol": "X", "rule": "level1"}, NOW.timestamp())
    assert bare["optionPrice"] is None and bare["spreadPct"] is None and bare["setups"] == [] and bare["secondsOld"] is None
    assert bare["chart"] == {}                                        # no chart saved: nothing is made up


# ---------------------------------------------------------------- the ranking
def test_the_ais_order_is_used_and_each_pick_keeps_its_reason():
    cands = [("kA", "bull", signal("AAA", 5)), ("kB", "bull", signal("BBB", 20)), ("kC", "bull", signal("CCC", 40))]
    seen = []
    def ask(system, user):
        seen.append((system, json.loads(user)))
        return answer(3, 1, 2)(system, user)
    out = pp.rank(cands, ask, NOW.timestamp())
    assert out == {"order": ["kC", "kA", "kB"], "why": {"kC": "reason 3", "kA": "reason 1", "kB": "reason 2"}, "source": "openai"}
    assert [s["ticker"] for s in seen[0][1]["signals"]] == ["AAA", "BBB", "CCC"] and "best first" in seen[0][0]


def test_signals_the_ai_left_out_keep_their_place_behind_and_invented_ids_are_ignored():
    cands = [("kA", "bull", signal("AAA", 5)), ("kB", "bull", signal("BBB", 20)), ("kC", "bull", signal("CCC", 40))]
    out = pp.rank(cands, lambda s, u: {"ok": True, "provider": "x", "data": {"ranking": [
        {"id": 9, "why": "not a signal"}, {"id": 2, "why": "best"}, {"id": 2, "why": "again"}, {"id": True, "why": "bool"}, "junk"]}}, NOW.timestamp())
    assert out["order"] == ["kB", "kA", "kC"] and out["why"] == {"kB": "best"}


@pytest.mark.parametrize("reply", [{"ok": False, "error": "rate limited"}, {"ok": True, "data": {"ranking": []}},
                                   {"ok": True, "data": {"ranking": [{"id": 7, "why": "x"}]}}, {"ok": True, "data": None}, None, "text"])
def test_a_failed_or_empty_ai_answer_is_an_error_not_a_ranking(reply):
    cands = [("kA", "bull", signal("AAA", 5)), ("kB", "bull", signal("BBB", 20))]
    out = pp.rank(cands, lambda s, u: reply, NOW.timestamp())
    assert "order" not in out and out["error"]


def test_an_ai_that_raises_is_an_error_not_a_crash():
    def boom(*a):
        raise TimeoutError("slow")
    assert pp.rank([("kA", "bull", signal("AAA", 5)), ("kB", "bull", signal("BBB", 9))], boom, NOW.timestamp()) == {"error": "TimeoutError"}


# ---------------------------------------------------------------- the AI bot's orders
def test_with_one_free_slot_the_ai_bot_buys_the_signal_the_ai_ranked_first(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, _ = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20), signal("CCC", 40)], client)
    picker = pp.Picker(m, ask=answer(3, 2, 1))                       # the AI likes CCC best; the rules bot would take AAA
    m.picker = picker
    assert picker.refresh(NOW) is True
    m.tick(NOW + timedelta(seconds=1))
    assert bought(client) == ["CCC"]
    row = m.status()["rows"][0]
    assert row["aiPick"] == {"rank": 1, "of": 3, "why": "reason 3", "source": "openai"}
    assert m.status()["picker"]["on"] is True and m.status()["picker"]["source"] == "openai"


def test_the_rules_bot_with_the_same_signals_buys_the_newest(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, _ = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20), signal("CCC", 40)], client, name="paper3")
    m.tick(NOW + timedelta(seconds=1))
    assert bought(client) == ["AAA"]
    assert "aiPick" not in m.status()["rows"][0] and m.status()["picker"] == {"on": False}


def test_when_the_ai_has_no_answer_the_ai_bot_uses_the_rules_order_and_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, _ = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20)], client)
    picker = pp.Picker(m, ask=lambda s, u: {"ok": False, "error": "quota"})
    m.picker = picker
    assert picker.refresh(NOW) is False and picker.status()["error"] == "quota"
    m.tick(NOW + timedelta(seconds=1))
    assert bought(client) == ["AAA"]                                  # a slot is never left waiting for the AI
    assert m.status()["rows"][0]["aiPick"] is None


def test_a_stale_ranking_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, book = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20)], client)
    m.picker = pp.Picker(m, ask=answer(2, 1))
    m.picker.refresh(NOW)
    later = NOW + timedelta(seconds=pm.RANK_FRESH_SECONDS + 5)
    book.items = [signal("AAA", 5, now=later), signal("BBB", 20, now=later)]
    m.tick(later)
    assert bought(client) == ["AAA"] and m.status()["rows"][0]["aiPick"] is None


def test_a_signal_that_arrived_after_the_ranking_waits_behind_the_ranked_ones(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, book = ai_bot(tmp_path, [signal("AAA", 30), signal("BBB", 50)], client)
    m.picker = pp.Picker(m, ask=answer(2, 1))                         # BBB first
    m.picker.refresh(NOW)
    book.items.append(signal("NEW", 1))                               # newer than both, not ranked yet
    m.tick(NOW + timedelta(seconds=2))
    assert bought(client) == ["BBB"]


def test_the_ai_is_asked_only_when_there_is_a_real_choice_and_a_free_slot(tmp_path, monkeypatch):
    calls = []
    def ask(system, user):
        calls.append(json.loads(user))
        return answer(1, 2)(system, user)
    client = FakeClient()
    m, book = ai_bot(tmp_path, [signal("AAA", 5)], client)
    picker = pp.Picker(m, ask=ask)
    assert picker.refresh(NOW) is False and calls == []                # one signal: nothing to choose
    book.items.append(signal("BBB", 20))
    assert picker.refresh(NOW) is True and len(calls) == 1
    assert picker.refresh(NOW + timedelta(seconds=30)) is False        # same two signals: not asked again
    assert len(calls) == 1
    book.items.append(signal("CCC", 2))
    assert picker.refresh(NOW + timedelta(seconds=pp.MIN_GAP_SECONDS - 5)) is False     # a new signal, but too soon after the last call
    assert picker.refresh(NOW + timedelta(seconds=pp.MIN_GAP_SECONDS + 5)) is True and len(calls) == 2
    assert picker.refresh(datetime(2026, 11, 10, 12, 0, tzinfo=ET)) is False            # midday: outside the buy windows
    from momx import paper_control
    paper_control.set_paused(tmp_path, "paper5", True, "me", NOW.isoformat())
    m.tick(NOW + timedelta(minutes=6))                                                   # the mirror notices the pause
    assert picker.refresh(NOW + timedelta(minutes=7)) is False and len(calls) == 2       # paused: no call


def test_the_daily_call_limit_stops_asking_and_falls_back(tmp_path, monkeypatch):
    monkeypatch.setattr(pp, "MAX_CALLS_PER_DAY", 1)
    monkeypatch.setattr(pp, "MIN_GAP_SECONDS", 0.0)
    m, book = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20)], FakeClient())
    picker = pp.Picker(m, ask=answer(1, 2))
    assert picker.refresh(NOW) is True
    book.items.append(signal("CCC", 1))
    assert picker.refresh(NOW + timedelta(seconds=5)) is False and "limit" in picker.status()["error"]
    assert picker.calls == 1


def test_one_signal_with_three_level_rows_is_one_choice_for_the_ai(tmp_path):
    trio = [signal("AAA", 5, rule=r) for r in ("level1", "level2", "level3")] + [signal("BBB", 20)]
    m, _ = ai_bot(tmp_path, trio, FakeClient())
    waiting = m.candidates(NOW)
    assert [(t["symbol"], t["rule"]) for _, _, t in waiting] == [("AAA", "level3"), ("BBB", "level2")]


def test_only_the_ai_account_gets_a_picker():
    a, b = SimpleNamespace(account="paper3", picker=None), SimpleNamespace(account="paper5", picker=None)
    assert [x for x in (a, b) if x.account == pp.AI_ACCOUNT] == [b]
    assert pm.ACCOUNTS["paper3"][0].startswith("Rules bot") and pm.ACCOUNTS["paper5"][0].startswith("AI bot")


# ---------------------------------------------------------------- the chart the AI is shown
CHART = {"chartContext": {"levels": {"vwap": 99.0, "ema9": 99.5, "ema21": 99.2, "ema50": 98.0, "ema200": 101.0,
                                      "prevHigh": 100.4, "prevLow": 96.0, "premarketHigh": 99.8, "premarketLow": 97.5,
                                      "P": 98.5, "R1": 102.0, "R2": 104.0, "S1": 97.0, "S2": 95.0}},
         "pct": 3.35, "rvol5m": 7.0, "rvol15m": 5.5, "rvol30m": 0.6, "rvol1h": -0.2, "adx5m": 22.8, "adx30m": 19.31, "sqzOn": 2, "hiLo": 0.85}


def test_a_call_is_shown_vwap_the_emas_the_next_level_ahead_and_the_high_oi_walls():
    t = signal("NVDA", 30, setup=CHART, callWalls=[{"strike": 110.0, "oi": 8209}, {"strike": 101.0, "oi": 7502}, {"strike": 95.0, "oi": 400}],
               putWalls=[{"strike": 98.0, "oi": 7700}, {"strike": 90.0, "oi": 300}])
    c = pp.summarize(1, "bull", t, NOW.timestamp())["chart"]
    assert c["vsVwapPct"] == 1.01 and c["emasOnRightSide"] == "3 of 4"              # above VWAP; above 9/21/50, below the 200
    assert c["nextChartLevelAhead"] == {"level": "yesterday's high", "awayPct": 0.4}   # resistance right overhead
    assert c["nearestChartLevelBehind"] == {"level": "premarket high", "awayPct": 0.2}
    assert c["highOiWallsAhead"] == [{"strike": 101.0, "awayPct": 1.0, "openInterest": 7502},
                                     {"strike": 110.0, "awayPct": 10.0, "openInterest": 8209}]    # the 95 wall is behind a call
    assert c["highOiWallBehindPct"] == 2.0                                          # the 98 put wall is the support
    assert (c["dayMovePct"], c["rvol5m"], c["adx5m"], c["squeezesOn"], c["placeInDayRange"]) == (3.35, 7.0, 22.8, 2.0, 0.85)


def test_a_put_reads_the_same_chart_the_other_way_round():
    t = signal("NVDA", 30, setup=CHART, putWalls=[{"strike": 98.0, "oi": 7700}, {"strike": 90.0, "oi": 300}],
               callWalls=[{"strike": 101.0, "oi": 7502}])
    c = pp.summarize(1, "bear", t, NOW.timestamp())["chart"]
    assert c["emasOnRightSide"] == "1 of 4"                                         # only below the 200 EMA
    assert c["nextChartLevelAhead"] == {"level": "premarket high", "awayPct": 0.2}  # the first level BELOW the price
    assert c["nearestChartLevelBehind"] == {"level": "yesterday's high", "awayPct": 0.4}
    assert c["highOiWallsAhead"][0] == {"strike": 98.0, "awayPct": 2.0, "openInterest": 7700}
    assert c["highOiWallBehindPct"] == 1.0


def test_missing_chart_pieces_are_left_out_not_guessed():
    part = {"chartContext": {"levels": {"vwap": None, "ema9": 99.5, "R1": None}}, "rvol5m": None, "adx5m": "n/a"}
    c = pp.summarize(1, "bull", signal("NVDA", 30, setup=part, callWalls=[], putWalls=None), NOW.timestamp())["chart"]
    assert c == {"emasOnRightSide": "1 of 1"}
    assert pp.chart_facts("bull", {"setup": "broken"}, 100.0) == {} and pp.chart_facts("bull", {"setup": CHART}, None).get("vsVwapPct") is None


def test_the_ai_is_told_to_use_the_chart_levels_and_walls():
    assert "VWAP" in pp.SYSTEM and "pivot" in pp.SYSTEM and "open-interest" in pp.SYSTEM and "premarket" in pp.SYSTEM
    seen = []
    pp.rank([("kA", "bull", signal("AAA", 5, setup=CHART)), ("kB", "bull", signal("BBB", 9))],
            lambda s, u: seen.append(json.loads(u)) or {"ok": True, "provider": "x", "data": {"ranking": [{"id": 1, "why": "w"}]}}, NOW.timestamp())
    assert seen[0]["signals"][0]["chart"]["nextChartLevelAhead"]["level"] == "yesterday's high"


# ---- review fixes: the ranking has to be ready BEFORE a slot frees
def test_the_ai_ranks_the_waiting_signals_while_every_slot_is_full(tmp_path, monkeypatch):
    """A freed slot is refilled in the same tick it frees, so the ranking must already be there."""
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, book = ai_bot(tmp_path, [signal("HELD", 30)], client)
    m.picker = pp.Picker(m, ask=answer(2, 1))
    m.tick(NOW)                                                       # the only slot is taken by HELD
    m.tick(NOW + timedelta(seconds=10))
    assert bought(client) == ["HELD"] and m.status()["totals"]["open"] == 1
    t1 = NOW + timedelta(seconds=30)
    book.items = [signal("HELD", 60, now=t1), signal("AAA", 5, now=t1), signal("BBB", 20, now=t1)]
    assert [t["symbol"] for _, _, t in m.candidates(t1)] == ["AAA", "BBB"]            # waiting for the NEXT free slot
    assert m.picker.refresh(t1) is True                               # ranked while full: BBB first
    t2 = t1 + timedelta(seconds=20)
    book.items = [dict(signal("HELD", 80, now=t2), status="closed", exitPrice=0.9, exitBid=0.88, exitAsk=0.92, exitReason="below EMA21"),
                  signal("AAA", 25, now=t2), signal("BBB", 40, now=t2)]
    m.tick(t2)                                                        # the Bot left HELD: the sell goes out
    m.tick(t2 + timedelta(seconds=10))                                # sell seen as filled -> slot free -> refilled in this tick
    assert bought(client) == ["HELD", "BBB"]
    assert next(r for r in m.status()["rows"] if r["symbol"] == "BBB")["aiPick"]["rank"] == 1


def test_the_same_waiting_signals_are_ranked_again_before_the_ranking_goes_stale(tmp_path):
    calls = []
    m, _ = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20)], FakeClient())
    picker = pp.Picker(m, ask=lambda s, u: calls.append(1) or answer(1, 2)(s, u))
    assert picker.refresh(NOW) is True
    assert picker.refresh(NOW + timedelta(seconds=pp.RERANK_SECONDS - 5)) is False and len(calls) == 1
    assert picker.refresh(NOW + timedelta(seconds=pp.RERANK_SECONDS + 5)) is True and len(calls) == 2
    assert pp.RERANK_SECONDS < pm.RANK_FRESH_SECONDS                   # renewed before the mirror stops trusting it


def test_a_busy_order_loop_is_not_read_as_nothing_waiting(tmp_path):
    calls = []
    m, _ = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20)], FakeClient())
    picker = pp.Picker(m, ask=lambda s, u: calls.append(1) or answer(1, 2)(s, u))
    assert picker.refresh(NOW) is True
    assert m._lock.acquire()                                          # the order loop is mid-tick
    try:
        assert m.candidates(NOW + timedelta(seconds=30)) is None
        assert picker.refresh(NOW + timedelta(seconds=30)) is False
    finally:
        m._lock.release()
    assert picker.refresh(NOW + timedelta(seconds=40)) is False and len(calls) == 1    # same signals: not asked again


def test_after_an_ai_error_the_same_signals_are_asked_again(tmp_path):
    replies = [{"ok": False, "error": "quota"}, answer(2, 1)(None, None)]
    m, _ = ai_bot(tmp_path, [signal("AAA", 5), signal("BBB", 20)], FakeClient())
    picker = pp.Picker(m, ask=lambda s, u: replies.pop(0))
    assert picker.refresh(NOW) is False and picker.error == "quota"
    assert picker.refresh(NOW + timedelta(seconds=5)) is False        # not before the minimum gap
    assert picker.refresh(NOW + timedelta(seconds=pp.MIN_GAP_SECONDS + 1)) is True and picker.error == ""


def test_a_signal_newer_than_the_ranking_goes_ahead_and_an_older_unranked_one_waits_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "MAX_OPEN", 1)
    client = FakeClient()
    m, book = ai_bot(tmp_path, [signal("AAA", 30), signal("BBB", 50)], client)
    m.picker = pp.Picker(m, ask=answer(2, 1))
    m.picker.refresh(NOW)
    later = NOW + timedelta(seconds=20)
    book.items = [signal("AAA", 50, now=later), signal("BBB", 70, now=later), signal("NEW", 3, now=later)]   # arrived after the ranking
    m.tick(later)
    assert bought(client) == ["NEW"] and m.status()["rows"][0]["aiPick"] is None     # the rules order took it: not an AI pick


def test_odd_wall_and_setup_data_never_breaks_the_ranking():
    odd = signal("AAA", 5, callWalls=[{"strike": "105", "oi": "x"}, "junk", {"oi": 3}], putWalls="broken",
                 levelSignal={"highestLevel": 2, "setupTypes": "not a list"}, setup={"chartContext": {"levels": ["bad"]}})
    facts = pp.summarize(1, "bull", odd, NOW.timestamp())
    assert facts["roomToWallPct"] == 5.0 and facts["setups"] == []
    assert facts["chart"]["highOiWallsAhead"] == [{"strike": 105.0, "awayPct": 5.0, "openInterest": None}]
    out = pp.rank([("kA", "bull", odd), ("kB", "bull", signal("BBB", 9))], answer(2, 1), NOW.timestamp())
    assert out["order"] == ["kB", "kA"]
    assert "error" in pp.rank([("kA", "bull", None), ("kB", "bull", signal("BBB", 9))], answer(1, 2), NOW.timestamp())   # an error, not a crash
