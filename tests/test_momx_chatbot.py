"""Ask the app (2026-11-10): the no-AI chatbot, momx/chatbot.py. Scratch files only."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from momx import bot_desk
from momx import chat_scanner, chat_scanner_index
from momx import chatbot as cb

DAY = "2026-11-10"
NOW = datetime(2026, 11, 10, 16, 30, tzinfo=cb.ET)


@pytest.fixture(autouse=True)
def _index_in_process(monkeypatch):
    """Scanner boards are indexed in this process here; test_momx_chat_scanner runs a real child."""
    monkeypatch.setattr(chat_scanner, "_run_builder", lambda path, bear: list(chat_scanner_index.build(path, bear)))


def at(clock, day=DAY):
    return f"{day}T{clock}-05:00"


def signal(symbol, clock="09:38:00", rule="level2", **kw):
    """A Bot signal with its 0.20-delta contract: bought at $1.15, best $3.70, out at $2.76, closed $3.70."""
    t = {"rule": rule, "symbol": symbol, "at": at(clock), "entryAt": at(clock), "stockPrice": 100.0, "spot": 100.0, "status": "closed",
         "momentum": "rising", "contract": f"{symbol}C100", "strike": 101.0, "entry": 4.0, "entryBid": 3.9, "entryAsk": 4.1,
         "eContract": f"{symbol}C110", "eStrike": 110.0, "eDelta": 0.18, "eEntry": 1.15, "eEntryBid": 1.10, "eEntryAsk": 1.20,
         "eBest": 3.70, "eExit": 2.76, "eExitBid": 2.72, "eExitAsk": 2.80, "eClose": 3.70, "eLast": 3.70,
         "exitAt": at("14:21:00"), "exitReason": "below EMA21", "market": {"spyPct": 0.4},
         "callWalls": [{"strike": 104.0, "oi": 5000}], "putWalls": [{"strike": 96.0, "oi": 4000}],
         "setup": {"chartContext": {"levels": {"vwap": 99.0, "ema9": 99.5, "ema21": 99.2, "ema50": 98.0, "ema200": 97.0, "R1": 103.0, "S1": 97.0}}}}
    t.update(kw)
    return t


def loser(symbol, **kw):
    """Lost 40%, with the chart against it: below VWAP and the EMAs, a pivot and a wall just ahead, wide spread, fading."""
    return signal(symbol, eBest=1.30, eExit=0.69, eExitBid=0.66, eExitAsk=0.72, eClose=0.5, exitAt=at("09:58:00"), exitReason="below VWAP",
                  momentum="fading", eEntryBid=1.00, eEntryAsk=1.30, market={"spyPct": -0.6},
                  callWalls=[{"strike": 100.5, "oi": 9000}],
                  setup={"chartContext": {"levels": {"vwap": 100.8, "ema9": 100.5, "ema21": 100.6, "ema50": 100.9, "ema200": 99.0, "R1": 100.3}}}, **kw)


def write(tmp_path, bull=(), bear=(), paper3=(), paper5=(), day=DAY, stop=None):
    for folder, trades in (("momx_option_track", bull), ("bear/momx_option_track", bear)):
        path = tmp_path / folder / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"date": day, "trades": list(trades)}), encoding="utf-8")
    for account, rows in (("paper3", paper3), ("paper5", paper5)):
        path = tmp_path / "momx_paper_mirror" / account / f"{day}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        doc = {"rows": {f"k{i}": r for i, r in enumerate(rows)}}
        if stop and account == "paper3":
            doc["dailyStop"] = stop
        path.write_text(json.dumps(doc), encoding="utf-8")
    cb._CACHE.clear()
    cb._INDEX.clear()


def order(symbol, status="closed", clock="09:39:00", side="bull", **kw):
    row = {"side": side, "rule": "level2", "symbol": symbol, "at": at("09:38:00"), "qty": 3, "buyLimit": 1.15, "buyId": "b-" + symbol,
           "buyAt": at(clock), "status": status}
    if status != "missed":
        row.update(buyFill=1.20, buyFilledQty=3)
    if status == "closed":
        row.update(pl=468.0, closedAt=at("14:22:00"))
    row.update(kw)
    return row


def text(out):
    return "\n".join(out["lines"])


# ---------------------------------------------------------------- reading the question
def test_the_question_type_is_read_from_its_words():
    assert [cb.parse_intent(q) for q in (
        "SPY if I take how much it went up", "what if I took nvda call at 10:30", "why aapl fails what is wrong", "why was amd missed",
        "why no trade on META", "see aapl", "show spy yesterday", "what is the bot doing", "which bot is ahead", "hello there")] == [
        "what_if", "what_if", "why_failed", "why_missed", "why_missed", "show", "show", "status", "status", "help"]


def test_day_time_and_side_are_found_in_the_sentence():
    today = date(2026, 11, 10)                                              # a Tuesday
    assert cb.parse_day("see aapl", today) == (today, False)
    assert cb.parse_day("see aapl today", today) == (today, True)
    assert cb.parse_day("see aapl yesterday", today) == (date(2026, 11, 9), True)
    assert cb.parse_day("see aapl yesterday", date(2026, 11, 9)) == (date(2026, 11, 6), True)      # Monday -> Friday
    assert cb.parse_day("spy on 11-06", today) == (date(2026, 11, 6), True)
    assert cb.parse_day("spy 11/6", today) == (date(2026, 11, 6), True)
    assert cb.parse_day("spy 2026-10-30", today) == (date(2026, 10, 30), True)
    assert cb.parse_day("spy 12-24", today) == (date(2025, 12, 24), True)                          # not a day in the future
    assert cb.parse_day("spy 13-45", today) == (today, False)                                      # not a date
    assert [cb.parse_time(q) for q in ("at 10:30", "9:45am", "1:15 pm", "took it 2:05", "no time", "99:99")] == [630, 585, 795, 845, None, None]
    assert [cb.parse_side(q) for q in ("nvda put", "tsla calls", "bearish amd", "spy")] == ["bear", "bull", "bear", None]


def test_a_ticker_is_a_word_the_app_has_records_for_and_common_words_need_capitals():
    known = {"AAPL", "SPY", "IT", "ON", "A", "ALL"}
    assert cb.parse_ticker("see aapl", known) == "AAPL"
    assert cb.parse_ticker("why was it missed on spy", known) == "SPY"      # "it" and "on" are words here
    assert cb.parse_ticker("why was IT missed", known) == "IT"              # typed as a ticker
    assert cb.parse_ticker("see $on", known) == "ON"
    assert cb.parse_ticker("show all of it", known) is None
    assert cb.parse_ticker("see zzzz", known) is None


# ---------------------------------------------------------------- how far it went
def test_what_if_reads_the_020_delta_contract_from_the_signal(tmp_path):
    write(tmp_path, bull=[signal("SPY")])
    out = cb.ask("SPY if I take how much it went up", tmp_path, NOW)
    assert (out["intent"], out["ticker"], out["day"]) == ("what_if", "SPY", DAY)
    body = text(out)
    assert "Level 2 call signal at 9:38 AM with the stock at $100.00" in body
    assert "The 0.20-delta call ($110) cost $1.15 at the mid." in body
    assert "best price of the day was $3.70: +222%" in body
    assert "The Bot's exit (below EMA21) at 2:21 PM was $2.76: +140%" in body
    assert "Held to the close it was $3.70: +222%" in body and "Your first target (+100%) was reached." in body
    assert "Three contracts at the mid would have been +$483 at the Bot's exit." in body
    assert "mid prices, not fills" in body and "Paper 3 (Rules bot): no order on this signal." in body
    assert out["facts"]["rule"] == "level2" and round(out["facts"]["exitPct"]) == 140
    assert out["suggest"] == ["why SPY failed", "why was SPY missed"]


def test_what_if_at_a_time_takes_the_signal_at_or_after_it_and_a_put_reads_as_a_put(tmp_path):
    write(tmp_path, bull=[signal("NVDA", "09:40:00"), signal("NVDA", "10:35:00", rule="level3", eEntryBid=2.0, eEntryAsk=2.0, eEntry=2.0)],
          bear=[signal("NVDA", "13:10:00", rule="level1")])
    late = cb.ask("what if I took nvda call at 10:30", tmp_path, NOW)
    assert "Level 3 call signal at 10:35 AM" in text(late) and "cost $2.00" in text(late)
    put = cb.ask("what if I took NVDA put", tmp_path, NOW)
    assert "Level 1 put signal at 1:10 PM" in text(put) and "0.20-delta put" in text(put)


def test_what_if_shows_what_the_paper_accounts_really_did(tmp_path):
    write(tmp_path, bull=[signal("SPY")], paper3=[order("SPY")], paper5=[order("SPY", status="missed", note="No fresh quote to re-send at the ask")])
    body = text(cb.ask("spy how much did it go", tmp_path, NOW))
    assert "Paper 3 (Rules bot): bought 3 at $1.20, closed for +$468." in body
    assert "Paper 5 (AI bot): sent a buy at 9:39 AM for $1.15 that never filled." in body


def test_what_if_with_no_signal_or_no_price_says_so(tmp_path):
    write(tmp_path, bull=[signal("AMD", eContract=None, entry=None, entryBid=None, entryAsk=None)])
    assert "No option price was recorded" in text(cb.ask("amd how far did it go", tmp_path, NOW))
    out = cb.ask("amd how far did it go 11-06", tmp_path, NOW)               # a named day with nothing on file
    assert "recorded no AMD signal on 2026-11-06" in text(out) and out["day"] == "2026-11-06"


# ---------------------------------------------------------------- why it failed
def test_why_failed_gives_the_result_and_what_the_chart_showed_against_it(tmp_path):
    write(tmp_path, bull=[loser("AAPL")])
    out = cb.ask("why aapl fails what is wrong", tmp_path, NOW)
    body = text(out)
    assert out["intent"] == "why_failed"
    assert 'It lost: the 0.20-delta contract went from $1.15 to $0.69 (-40%), out after 20 minutes on "below VWAP".' in body
    assert "What was against it at the signal:" in body
    for reason in ("wrong side of VWAP (0.79% below it; a call wants it above)", "right side of only 1 of 4 of the EMAs",
                   "A chart level sat right ahead: pivot R1, only 0.30% away", "A high OI wall was just ahead at $100.5 (0.50% away)",
                   "momentum was already fading", "spread was wide: 26% of its price", "market was moving the other way (SPY -0.60%"):
        assert reason in body, reason
    assert out["facts"]["against"] == 7


def test_a_winner_is_not_called_a_failure_and_a_clean_chart_says_so(tmp_path):
    write(tmp_path, bull=[signal("SPY")])
    body = text(cb.ask("why spy failed", tmp_path, NOW))
    assert "this one did not fail: the 0.20-delta contract was +140% at the exit" in body
    assert "Nothing on the saved chart was clearly against it" in body
    write(tmp_path, bull=[signal("SPY", eExit=None, eExitBid=None, eExitAsk=None, eClose=None, exitAt=None, status="open")])
    assert "No exit price is recorded for it yet" in text(cb.ask("why spy failed", tmp_path, NOW))


# ---------------------------------------------------------------- why it was missed
def test_why_missed_names_the_step_that_stopped_it(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-10")
    write(tmp_path)
    assert cb.ask("why was SPY missed", tmp_path, NOW)["intent"] == "help"   # SPY is not known at all here

    def step(question="why was amd missed", **files):
        write(tmp_path, **files)
        return cb.ask(question, tmp_path, NOW)

    out = step(bull=[signal("AMD", rule="rvol")])
    assert out["facts"]["step"] == "no level signal" and "none was a Level signal (it fired: rvol)" in text(out)
    out = step(bull=[signal("AMD", "11:20:00")])
    assert out["facts"]["step"] == "outside the buy windows" and "11:20 AM, outside the buy windows" in text(out)
    out = step(bull=[signal("AMD", eContract=None)])
    assert out["facts"]["step"] == "no 0.20-delta contract"
    out = step(bull=[signal("AMD", eEntryBid=0.0, eEntryAsk=0.05)])
    assert out["facts"]["step"] == "no bid" and "Nobody was bidding" in text(out)
    out = step(bull=[signal("AMD", eEntryBid=0.02, eEntryAsk=0.04)])
    assert out["facts"]["step"] == "too cheap"
    out = step(bull=[signal("AMD", eEntryBid=8.9, eEntryAsk=9.1)])
    assert out["facts"]["step"] == "too expensive"
    full = [order(f"T{i}", status="open", clock="09:31:00") for i in range(8)]
    out = step(bull=[signal("AMD")], paper3=full, paper5=full)
    assert out["facts"]["step"] == "slots full" and "Paper 3 (Rules bot) had all 8 slots in use" in text(out) and "Paper 5 (AI bot) had all 8 slots" in text(out)
    out = step(bull=[signal("AMD")], stop={"at": at("09:36:00"), "pnl": -520.0, "limit": 500.0})
    assert out["facts"]["step"] == "daily loss stop" and "$500 daily loss stop at 9:36 AM" in text(out)
    out = step(bull=[signal("AMD")])
    assert out["facts"]["step"] == "not clear from the records"
    out = step(bull=[signal("AMD")], paper3=[order("AMD")])
    assert out["facts"]["step"] == "ordered" and "It was not skipped." in text(out)
    out = step(question="why was amd missed")                                # nothing fired at all (AMD known from a chain file)
    (tmp_path / "oi_chain_cache").mkdir(exist_ok=True)
    (tmp_path / "oi_chain_cache" / "AMD.json.gz").write_bytes(b"")
    out = cb.ask("why was amd missed", tmp_path, NOW)
    assert out["facts"]["step"] == "no signal" and "No rule fired on AMD" in text(out)


def test_before_the_level_rules_started_a_missed_level_signal_says_the_accounts_were_on_older_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-11")
    write(tmp_path, bull=[signal("NVDA")])
    out = cb.ask("why was nvda missed", tmp_path, NOW)
    assert out["facts"]["step"] == "before the Level rules" and "did not trade Level signals at all; they started on 2026-11-11" in text(out)


def test_an_older_carried_order_is_not_shown_under_another_day(tmp_path):
    old = order("NVDA", status="missed", at="2026-11-05T09:30:00-05:00", buyAt="2026-11-05T09:30:39-05:00")
    write(tmp_path, bull=[signal("NVDA")], paper3=[old])
    assert "Paper 3 (Rules bot): no order on this signal." in text(cb.ask("nvda how much did it go", tmp_path, NOW))


# ---------------------------------------------------------------- see / status / help
def test_see_lists_what_is_on_file_and_falls_back_to_the_last_day_with_records(tmp_path):
    write(tmp_path, bull=[signal("AAPL", "09:31:00", rule="rvol"), loser("AAPL", clock="09:38:00")])
    out = cb.ask("see aapl", tmp_path, NOW)
    body = text(out)
    assert out["intent"] == "show" and "AAPL on 2026-11-10: 2 signals on file." in body
    assert "- 9:31 AM · rvol · call · entry $1.15 · +140% · below EMA21" in body and "- 9:38 AM · Level 2 · call · entry $1.15 · -40% · below VWAP" in body
    write(tmp_path, bull=[signal("TSLA", day="2026-11-09")] if False else [], day=DAY)
    write(tmp_path, bull=[{**signal("TSLA"), "at": "2026-11-06T09:40:00-05:00"}], day="2026-11-06")
    (tmp_path / "oi_chain_cache").mkdir(exist_ok=True)
    (tmp_path / "oi_chain_cache" / "TSLA.json.gz").write_bytes(b"")
    cb._CACHE.clear()
    cb._INDEX.clear()
    out = cb.ask("see tsla", tmp_path, NOW)                                   # nothing today: the last day that has it
    assert out["day"] == "2026-11-06" and "TSLA on 2026-11-06: 1 signal on file." in text(out)
    assert "Nothing on file for TSLA on 2026-11-10" in text(cb.ask("see tsla today", tmp_path, NOW))


def test_status_reads_the_scorecard_and_a_question_with_no_ticker_explains_itself(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", DAY)
    write(tmp_path, paper3=[order("SPY")], paper5=[order("SPY", pl=-30.0)])
    out = cb.ask("which bot is ahead", tmp_path, NOW)
    body = text(out)
    assert out["intent"] == "status" and "Rules bot: 1 closed trades, +$468, balance $10,468, buying" in body
    assert "AI bot: 1 closed trades, -$30" in body and "Neither bot is called ahead yet" in body
    out = cb.ask("good morning", tmp_path, NOW)
    assert out["intent"] == "help" and "did not find a ticker" in text(out) and out["suggest"] == cb.EXAMPLES
    assert cb.ask("", tmp_path, NOW)["intent"] == "help" and cb.ask(None, tmp_path, NOW)["lines"]


def test_it_never_writes_and_survives_broken_files(tmp_path):
    write(tmp_path, bull=[signal("SPY")])
    (tmp_path / "bear" / "momx_option_track" / f"{DAY}.json").write_text("{broken", encoding="utf-8")
    (tmp_path / "momx_paper_mirror" / "paper5" / f"{DAY}.json").write_text("[]", encoding="utf-8")
    cb._CACHE.clear()
    cb._INDEX.clear()
    before = sorted(str(p) for p in tmp_path.rglob("*"))
    for q in ("spy how much", "why spy failed", "why was spy missed", "see spy", "what is the bot doing", "x" * 5000):
        assert cb.ask(q, tmp_path, NOW)["lines"]
    assert sorted(str(p) for p in tmp_path.rglob("*")) == before


def test_a_days_records_are_read_once_and_today_is_checked_again_only_after_a_minute(tmp_path, monkeypatch):
    write(tmp_path, bull=[signal("SPY")])
    made = []
    real = cb.Records

    class Counting(real):
        def __init__(self, *a):
            made.append(1)
            super().__init__(*a)

    clock = [1000.0]
    monkeypatch.setattr(cb, "Records", Counting)
    monkeypatch.setattr(cb.time, "monotonic", lambda: clock[0])
    for _ in range(3):
        cb.ask("spy how much", tmp_path, NOW)
    assert len(made) == 1
    path = tmp_path / "momx_option_track" / f"{DAY}.json"
    path.write_text(json.dumps({"date": DAY, "trades": [signal("SPY"), signal("AAPL")]}), encoding="utf-8")     # the Bot saved again
    import os
    os.utime(path, (path.stat().st_mtime + 5,) * 2)
    cb.ask("spy how much", tmp_path, NOW)
    assert len(made) == 1                                                    # inside the minute: not read again
    clock[0] += 61
    cb.ask("spy how much", tmp_path, NOW)
    assert len(made) == 2                                                    # after it: the changed file is read
    clock[0] += 61
    cb.ask("spy how much", tmp_path, NOW)
    assert len(made) == 2                                                    # unchanged: not read again


def test_a_past_day_is_read_once_and_a_ticker_with_no_signals_all_week_loads_nothing(tmp_path, monkeypatch):
    write(tmp_path, bull=[{**signal("TSLA"), "at": "2026-11-06T09:40:00-05:00"}], day="2026-11-06")
    write(tmp_path, bull=[signal("SPY")])
    (tmp_path / "oi_chain_cache").mkdir(exist_ok=True)
    for name in ("TSLA", "AAPU"):
        (tmp_path / "oi_chain_cache" / f"{name}.json.gz").write_bytes(b"")
    cb._CACHE.clear()
    cb._INDEX.clear()
    loaded = []
    real = cb.Records

    class Counting(real):
        def __init__(self, artifacts, day):
            loaded.append(day)
            super().__init__(artifacts, day)

    monkeypatch.setattr(cb, "Records", Counting)
    assert "Nothing on file for AAPU" in text(cb.ask("see aapu", tmp_path, NOW)) and loaded == [DAY]     # no walk through 7 day files
    cb.ask("see tsla", tmp_path, NOW)
    cb.ask("tsla how much did it go", tmp_path, NOW)
    assert loaded == [DAY, "2026-11-06"]                                     # the one day that has TSLA, read once


def test_rows_are_kept_slim_in_memory():
    fat = signal("SPY", scannerInputs={"x": "y" * 5000}, aiDecision={"big": 1}, levelSignal={"setupTypes": ["a"] * 50},
                 setup={"chartContext": {"levels": {"vwap": 1.0}, "symbol": "SPY", "basis": "completed 5m bar",
                                         "bars": [1] * 500}, "pct": 2.0, "capture": {"a": 1}})
    slim = cb._slim(fat)
    assert "scannerInputs" not in slim and "aiDecision" not in slim and "levelSignal" not in slim
    # The chart snapshot's own fields stay (the Scorecard re-runs the chart check on them); anything else goes.
    assert slim["setup"] == {"pct": 2.0, "chartContext": {"levels": {"vwap": 1.0}, "symbol": "SPY",
                                                          "basis": "completed 5m bar"}} and slim["eEntry"] == 1.15
    assert cb._slim(signal("SPY", setup={"pct": 1.0}))["setup"] == {"pct": 1.0, "chartContext": {"levels": None}}


def test_a_slim_row_still_carries_a_chart_the_chart_check_can_read():
    """Review 2026-11-10: _slim kept only the levels, so chart_learning.snapshot() always said None."""
    from momx import chart_learning
    sig = signal("SPY")
    ts = datetime.fromisoformat(sig["at"]).timestamp()
    chart = {"version": 1, "timeframe": "5m", "symbol": "SPY", "barAt": ts - 360, "availableAt": ts - 60, "capturedAt": ts - 30,
             "referencePrice": 100.0, "basis": "completed 5m bar", "levels": {"ema21": 101.0}}
    slim = cb._slim(dict(sig, setup={"chartContext": chart}))
    assert chart_learning.snapshot(slim) == chart


def test_why_missed_gives_the_paper_bots_saved_chart_or_buying_power_reason(tmp_path, monkeypatch):
    """Review 2026-11-10: chat and the Scorecard must give the same reason for a signal with no order."""
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-10")
    sig = signal("AMD")
    write(tmp_path, bull=[sig])
    for account, field, why in (("paper3", "chartSkips", "price under the 21 EMA"),
                                ("paper5", "fundSkips", "not enough buying power ($1,500 needed, $812 free)")):
        path = tmp_path / "momx_paper_mirror" / account / f"{DAY}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc[field] = {"bull|level2|AMD|" + sig["at"]: {"symbol": "AMD", "side": "bull", "rule": "level2", "at": sig["at"], "why": why},
                      "other": {"symbol": "AMD", "side": "bear", "rule": "level2", "at": sig["at"], "why": "a put's reason"},
                      "later": {"symbol": "AMD", "side": "bull", "rule": "level2", "at": at("09:50:00"), "why": "another moment"}}
        path.write_text(json.dumps(doc), encoding="utf-8")
    cb._CACHE.clear()
    out = cb.ask("why was amd missed", tmp_path, NOW)
    said = text(out)
    assert out["facts"]["step"] == "chart check"
    assert "Paper 3 (Rules bot): the 5m chart check turned it down: price under the 21 EMA." in said
    assert "Paper 5 (AI bot): not enough buying power ($1,500 needed, $812 free)." in said
    assert "a put's reason" not in said and "another moment" not in said and "do not show a clear reason" not in said




# ---- review fixes (2026-11-10): answers must be about the right ticker, signal, time and order
def test_everyday_words_and_pieces_of_words_are_never_read_as_tickers():
    known = {"AMD", "TSLA", "NVDA", "YOU", "T", "MAX", "COST", "GS", "APP", "IT", "ON", "SPY"}
    on_file = {"AMD", "TSLA", "NVDA", "SPY"}
    for question, want in (("why did you not take amd", "AMD"), ("what was against tsla", "TSLA"), ("what was the max on tsla", "TSLA"),
                           ("what would it cost to buy nvda", "NVDA"), ("why didn't the bot take NVDA yesterday", "NVDA"),
                           ("why wasn't AMD taken", "AMD"), ("our app missed amd why", "AMD"), ("earnings on amd", "AMD"),
                           ("see $T", "T"), ("why COST failed", "COST"), ("WHY DID AMD FAIL", "AMD"), ("see t", None), ("how are you", None)):
        assert cb.parse_ticker(question, known, on_file) == want, question
    assert cb.parse_ticker("see app or spy", known | {"APPS"}, on_file) == "SPY"
    assert cb.parse_ticker("see apps or spy", known | {"APPS"}, on_file) == "SPY"      # the one with trades that day is preferred
    assert cb.parse_ticker("see apps", known | {"APPS"}, on_file) == "APPS"
    # One- and two-letter tickers must be typed in capitals (or $gs), so "it", "on", "t" are never mistaken.
    assert cb.parse_ticker("see gs", known, on_file) is None and cb.parse_ticker("see GS", known, on_file) == "GS"


def test_more_ways_of_asking_are_understood():
    assert [cb.parse_intent(q) for q in ("why didn't the bot take NVDA", "why did you not take amd", "why wasn't AMD taken", "amd never traded",
                                         "what was against tsla", "what was the max on tsla", "what would it cost to buy nvda", "how high did spy go")] == [
        "why_missed", "why_missed", "why_missed", "why_missed", "why_failed", "what_if", "what_if", "what_if"]
    today = date(2026, 11, 10)
    assert cb.parse_day("nov 6 why aapl failed", today) == (date(2026, 11, 6), True)
    assert cb.parse_day("november 6th see spy", today) == (date(2026, 11, 6), True)
    assert cb.parse_day("last friday see spy", today) == (date(2026, 11, 6), True)
    assert cb.parse_day("see spy tuesday", today) == (date(2026, 11, 3), True)             # the most recent Tuesday BEFORE today
    assert cb.parse_day("what if I took it at 10:30", today) == (today, False)             # a time is not a date
    assert [cb.parse_time(q) for q in ("at 2pm", "10 am", "12am", "12 pm", "at 10", "13pm")] == [840, 600, 0, 720, None, None]


def test_a_time_with_no_signal_at_or_after_it_is_never_answered_with_an_earlier_signal(tmp_path):
    write(tmp_path, bull=[signal("AMD", "09:30:00"), signal("AMD", "12:30:00", rule="rvol")])
    for question in ("why did AMD lose at 3:45", "what if I took amd at 3:45"):
        out = cb.ask(question, tmp_path, NOW)
        assert out["lines"] == ["The Bot recorded no AMD signal at or after 3:45 PM on 2026-11-10; its last one that day was at 12:30 PM. "
                                "Nothing to measure for that time."], question
    assert "no AMD Level signal at or after 3:45 PM" in text(cb.ask("why was amd missed at 3:45", tmp_path, NOW))
    assert "rvol call signal at 12:30 PM" in text(cb.ask("what if I took amd at 11am", tmp_path, NOW))


def test_an_order_on_another_signal_is_never_read_as_this_signals_order(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-10")
    other = order("GOOGL", status="missed", rule="rvol", at=at("09:30:00"), buyAt=at("09:32:00"), buyLimit=3.40, contract="GOOGL 261111C00295000")
    waiting = order("GOOGL", status="buying", rule="call2h", at=at("09:30:00"), buyAt=at("09:32:14"), buyLimit=6.95, buyFill=None,
                    contract="GOOGL 261110C00663000", note='Awaiting broker reconciliation: {"code":40410000}')
    write(tmp_path, bull=[signal("GOOGL", "09:38:00", rule="level3")], paper5=[other, waiting])
    out = cb.ask("why did I miss googl", tmp_path, NOW)
    body = text(out)
    assert out["facts"]["step"] != "ordered" and "Neither paper account ordered this signal." in body and "It was not skipped" not in body
    assert "Paper 5 (AI bot), on a different signal (rvol at 9:30 AM, the $295 call): sent a buy at 9:32 AM for $3.40 that never filled." in body
    assert "on a different signal (call2h at 9:30 AM, the $663 call): sent a buy at 9:32 AM for $6.95; no fill is recorded yet (still waiting on the broker)." in body
    assert "40410000" not in body                                             # no raw broker text in an answer
    what_if = text(cb.ask("googl how much did it go", tmp_path, NOW))
    assert "Paper 5 (AI bot): no order on this signal." in what_if and "on a different signal (rvol" in what_if
    mine = order("GOOGL", rule="level3", at=at("09:38:00"))
    write(tmp_path, bull=[signal("GOOGL", "09:38:00", rule="level1"), signal("GOOGL", "09:38:00", rule="level3")], paper3=[mine])
    taken = cb.ask("why was googl missed", tmp_path, NOW)
    assert taken["facts"]["step"] == "ordered" and "Paper 3 (Rules bot): bought 3 at $1.20, closed for +$468." in text(taken)


def test_the_days_best_is_not_claimed_to_have_come_before_the_exit(tmp_path):
    write(tmp_path, bull=[signal("AMD", eEntryBid=4.0, eEntryAsk=4.1, eExit=4.0, eExitBid=3.95, eExitAsk=4.05, eBest=14.875, eClose=14.875,
                                 exitAt=at("09:40:00"), exitReason="below VWAP")])
    body = text(cb.ask("why amd failed", tmp_path, NOW))
    assert "before giving it back" not in body
    assert "Its best price of the day was $14.88 (+267% from the entry). The time of that best is not recorded for this contract, so it may have come after the Bot's exit." in body


def test_a_signal_with_no_option_gives_the_bots_own_recorded_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-10")
    write(tmp_path, bull=[signal("META", eContract=None, status="no_contract", why="No usable option quote before the signal's 10-minute deadline")])
    body = text(cb.ask("why was meta missed", tmp_path, NOW))
    assert 'Its recorded reason: "No usable option quote before the signal\'s 10-minute deadline".' in body and "next week" not in body
    write(tmp_path, bull=[signal("META", eContract=None)])
    assert "The reason is not recorded." in text(cb.ask("why was meta missed", tmp_path, NOW))


# ---- the conversation is remembered (2026-11-10: he asked "NVDA if I take how much it went up", then "Profit or loss")
def test_a_follow_up_with_no_ticker_continues_with_the_one_the_conversation_is_about(tmp_path):
    write(tmp_path, bull=[{**loser("NVDA"), "at": "2026-11-09T09:38:00-05:00", "entryAt": "2026-11-09T09:38:00-05:00",
                           "exitAt": "2026-11-09T09:58:00-05:00"}], day="2026-11-09")
    write(tmp_path, bull=[signal("SPY")])
    cb._CACHE.clear()
    cb._INDEX.clear()
    about = {"ticker": "NVDA", "day": "2026-11-09"}
    out = cb.ask("Profit or loss", tmp_path, NOW, about)
    assert (out["intent"], out["ticker"], out["day"]) == ("what_if", "NVDA", "2026-11-09")
    assert "Three contracts at the mid would have been -$138 at the Bot's exit." in text(out)
    why = cb.ask("why did it fail", tmp_path, NOW, about)
    assert (why["intent"], why["ticker"], why["day"]) == ("why_failed", "NVDA", "2026-11-09") and "It lost" in text(why)
    assert cb.ask("why was it missed", tmp_path, NOW, about)["ticker"] == "NVDA"
    assert cb.ask("and yesterday?", tmp_path, NOW, {"ticker": "NVDA", "day": "2026-11-10"})["day"] == "2026-11-09"
    assert cb.ask("tell me more", tmp_path, NOW, about)["intent"] == "show"
    # A ticker in the new question always wins, and a question about the bot is not about the last ticker.
    assert cb.ask("and spy? profit or loss", tmp_path, NOW, about)["ticker"] == "SPY"
    assert cb.ask("which bot is ahead", tmp_path, NOW, about)["intent"] == "status"
    assert cb.ask("what is the bot doing", tmp_path, NOW, about)["ticker"] is None


def test_profit_or_loss_with_nothing_to_continue_is_the_bots_own_result_and_bad_context_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", DAY)
    write(tmp_path, paper3=[order("SPY")])
    out = cb.ask("Profit or loss", tmp_path, NOW)
    assert out["intent"] == "status" and "Rules bot: 1 closed trades, +$468" in text(out)
    for junk in ({"ticker": "../../etc", "day": "x"}, {"ticker": 5}, "NVDA", ["NVDA"], {"ticker": "TOOLONGX"}, {}):
        assert cb.ask("Profit or loss", tmp_path, NOW, junk)["intent"] == "status"
    assert cb.ask("why did it fail", tmp_path, NOW, {"ticker": "SPY", "day": "not-a-day"})["day"] == DAY


def test_went_up_means_the_calls_and_the_answer_says_when_the_other_side_also_signalled(tmp_path):
    write(tmp_path, bull=[signal("NVDA", "10:05:00", rule="level2")], bear=[signal("NVDA", "09:30:00", rule="level3")])
    up = cb.ask("NVDA if I take how much it went up", tmp_path, NOW)
    assert "Level 2 call signal at 10:05 AM" in text(up)                      # not the earlier put
    assert up["lines"][-1] == 'NVDA also had 1 put signal that day, the first at 9:30 AM. Add "put" to ask about those.'
    plain = cb.ask("NVDA how did it do", tmp_path, NOW)                       # no hint: the first Level signal of the day
    assert "Level 3 put signal at 9:30 AM" in text(plain)
    assert plain["lines"][-1] == 'NVDA also had 1 call signal that day, the first at 10:05 AM. Add "call" to ask about those.'
    named = cb.ask("NVDA put how much", tmp_path, NOW)
    assert "also had" not in text(named)                                      # he named the side: nothing to add
    write(tmp_path, bear=[signal("NVDA", "09:30:00", rule="level3")])
    only_puts = cb.ask("NVDA if I take how much it went up", tmp_path, NOW)
    assert "Level 3 put signal at 9:30 AM" in text(only_puts) and "also had" not in text(only_puts)
    assert [cb.hinted_side(q) for q in ("how much it went up", "how high did it go", "it dropped", "went down a lot", "how much")] == [
        "bull", "bull", "bear", "bear", None]


def test_a_follow_up_stays_on_the_calls_or_puts_the_conversation_was_about(tmp_path):
    write(tmp_path, bull=[signal("NVDA", "10:05:00", rule="level2")], bear=[signal("NVDA", "09:30:00", rule="level3")])
    first = cb.ask("NVDA if I take how much it went up", tmp_path, NOW)
    assert first["side"] == "bull" and "Level 2 call signal at 10:05 AM" in text(first)
    about = {"ticker": first["ticker"], "day": first["day"], "side": first["side"]}
    for follow in ("Profit or loss", "why did it fail", "why was it missed"):
        out = cb.ask(follow, tmp_path, NOW, about)
        assert "call signal at 10:05 AM" in text(out) and "put signal" not in out["lines"][0], follow
    put = cb.ask("and the put? profit or loss", tmp_path, NOW, about)                                     # a named side wins
    assert "Level 3 put signal at 9:30 AM" in text(put) and put["side"] == "bear"
    assert "Level 3 put signal at 9:30 AM" in text(cb.ask("Profit or loss", tmp_path, NOW, {**about, "side": "sideways"}))   # junk side ignored
    assert cb.ask("which bot is ahead", tmp_path, NOW, about).get("side") is None


def test_the_old_limits_only_explain_signals_from_before_they_ended():
    """2026-11-10: 8 open / 25 a day ended that morning; later misses are not blamed on them."""
    import inspect
    from momx import chatbot
    src = inspect.getsource(chatbot.answer_why_missed)
    assert '(records.day, at) < ("2026-11-10", 10 * 60 + 30)' in src
    assert ("2026-11-09", 900) < ("2026-11-10", 630) and ("2026-11-10", 584) < ("2026-11-10", 630)
    assert not (("2026-11-10", 700) < ("2026-11-10", 630)) and not (("2026-11-11", 571) < ("2026-11-10", 630))



def test_two_threads_asking_for_the_same_day_build_it_once(tmp_path, monkeypatch):
    """Review 2026-11-10: the chat and the Scorecard share the day cache; it must not build twice or break."""
    import threading
    import time as _time
    from momx import chatbot as cb
    monkeypatch.setattr(cb, "_CACHE", {})
    built = []

    class Slow:
        def __init__(self, artifacts, day):
            built.append(day)
            _time.sleep(0.3)
            self.signals = []

    monkeypatch.setattr(cb, "Records", Slow)
    out = []
    threads = [threading.Thread(target=lambda: out.append(cb.records_for(tmp_path, "2026-11-10", "2026-11-10"))) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(out) == 4 and built == ["2026-11-10"] and all(x is out[0] for x in out)


def test_a_cached_other_day_answers_while_today_is_building(tmp_path, monkeypatch):
    """Review 2026-11-10: a question about a past day must not wait behind today's ~10 s rebuild."""
    import threading
    from momx import chatbot as cb
    past = object()
    monkeypatch.setattr(cb, "_CACHE", {(str(tmp_path), "2026-11-09"): ([], past, 0.0)})
    monkeypatch.setattr(cb, "_DAY_LOCKS", {})
    started, release = threading.Event(), threading.Event()

    class Blocking:
        def __init__(self, artifacts, day):
            if day == "2026-11-10":
                started.set()
                release.wait(5)
            self.day, self.signals = day, []

    monkeypatch.setattr(cb, "Records", Blocking)
    today = threading.Thread(target=lambda: cb.records_for(tmp_path, "2026-11-10", "2026-11-10"))
    today.start()
    try:
        assert started.wait(5)
        got = []
        asker = threading.Thread(target=lambda: got.append(cb.records_for(tmp_path, "2026-11-09", "2026-11-10")))
        asker.start()
        asker.join(1)
        assert got == [past]                                          # answered from the cache, no wait
        other = threading.Thread(target=lambda: got.append(cb.records_for(tmp_path, "2026-11-06", "2026-11-10")))
        other.start()
        other.join(1)
        assert len(got) == 2 and got[1].day == "2026-11-06"           # an uncached day builds on its own lock
        assert today.is_alive()                                       # today was still building throughout
    finally:
        release.set()
        today.join(5)


def test_why_missed_gives_the_top_of_board_reason_before_the_chart(tmp_path, monkeypatch):
    """His top-of-board rule (2026-11-10): chat gives the paper bot's saved topSkips reason first."""
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-11-10")
    sig = signal("AMD")
    write(tmp_path, bull=[sig])
    why = "AMD was #9 on the Watchlist by H/L (needs top 7 for 5 min)"
    for account in ("paper3", "paper5"):
        path = tmp_path / "momx_paper_mirror" / account / f"{DAY}.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc["topSkips"] = {"k": {"symbol": "AMD", "side": "bull", "rule": "level2", "at": sig["at"], "why": why},
                           "other": {"symbol": "AMD", "side": "bear", "rule": "level2", "at": sig["at"], "why": "a put's reason"}}
        doc["chartSkips"] = {"c": {"symbol": "AMD", "side": "bull", "rule": "level2", "at": sig["at"],
                                   "why": "price under the 21 EMA"}}
        path.write_text(json.dumps(doc), encoding="utf-8")
    cb._CACHE.clear()
    out = cb.ask("why was amd missed", tmp_path, NOW)
    said = text(out)
    assert out["facts"]["step"] == "top of board"
    assert f"Paper 3 (Rules bot): not in its board's top by H/L when it tried to buy: {why}." in said
    assert f"Paper 5 (AI bot): not in its board's top by H/L when it tried to buy: {why}." in said
    assert "price under the 21 EMA" not in said and "a put's reason" not in said


# ---------------------------------------------------------------- the saved scanner history (momx/chat_scanner.py)
def scanner_day(base, symbol="NVDA", clock="09:28:00"):
    """A Mag7 and a Watchlist day file, with `symbol` on the Mag7 board."""
    row = {"at": at(clock), "changed": [], "row": {"scanPass": True, "highLow": {"value": 0.5, "degree": 0.5}, "last": 100.0, "pctChange": 1.0}}
    for name, rows in (("Mag7", {symbol: {"lastSeenAt": at("15:50:00"), "snapshots": [row]}}), ("Watchlist", {})):
        path = base / "momx_history" / name / f"{DAY}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"list": name, "date": DAY, "rows": rows}), encoding="utf-8")


@pytest.fixture
def scanner_clean():
    from momx import chat_scanner
    chat_scanner._CACHE.clear()
    yield
    chat_scanner._CACHE.clear()


@pytest.mark.parametrize("question,want", [
    ("why was NVDA missed in the scanner", "why_missed"), ("why did NVDA fail on the scanner", "why_failed"),
    ("NVDA from the scanner if I take how much it went up", "what_if"), ("NVDA scanner profit or loss", "what_if"),
    ("see NVDA", "show"), ("which bot is ahead", "status"), ("what is the bot doing", "status")])
def test_questions_the_chat_already_knew_keep_their_answers_with_a_scanner_file_present(tmp_path, scanner_clean, question, want):
    write(tmp_path, bull=[signal("NVDA")])
    scanner_day(tmp_path)
    assert cb.ask(question, tmp_path, NOW)["intent"] == want


def test_the_three_scanner_questions_answer_in_the_chats_shape(tmp_path, scanner_clean):
    write(tmp_path, bull=[signal("NVDA")])
    scanner_day(tmp_path)
    for question, intent in (("from the scanner at 9:30 give me the list of tickers", "scanner_list"), ("NVDA at 9:30 in the scanner", "scanner_ticker"),
                             ("which tickers stayed on top", "scanner_top")):
        out = cb.ask(question, tmp_path, NOW)
        assert out["intent"] == intent and out["day"] == DAY and isinstance(out["facts"], dict) and out["suggest"]
        assert out["lines"][-1].startswith("From the saved scanner history")


def test_the_list_wins_over_carrying_the_conversations_ticker(tmp_path, scanner_clean):
    write(tmp_path, bull=[signal("SPY")])
    scanner_day(tmp_path)
    out = cb.ask("from the scanner at 9:30 give me the list of tickers", tmp_path, NOW, {"ticker": "SPY", "day": DAY})
    assert (out["intent"], out["ticker"]) == ("scanner_list", None) and "SPY at" not in text(out)


def test_a_follow_up_about_it_in_the_scanner_uses_the_conversations_ticker(tmp_path, scanner_clean):
    write(tmp_path, bull=[signal("NVDA")])
    scanner_day(tmp_path)
    out = cb.ask("where was it in the scanner at 9:30", tmp_path, NOW, {"ticker": "NVDA", "day": DAY})
    assert (out["intent"], out["ticker"], out["facts"]["onBoard"]) == ("scanner_ticker", "NVDA", True)


def test_the_ticker_on_the_page_answers_questions_but_not_hello(tmp_path, scanner_clean):
    write(tmp_path, bull=[signal("SPY")])
    page = {"ticker": "SPY", "fromPage": True}
    hello = cb.ask("hello", tmp_path, NOW, page)
    assert (hello["intent"], hello["ticker"]) == ("help", None)
    assert cb.ask("hello", tmp_path, NOW, {"ticker": "SPY"})["intent"] == "show"              # a real conversation still carries
    for question, want in (("how much did it go up", "what_if"), ("why did it fail", "why_failed"), ("why was it missed", "why_missed"), ("profit or loss", "what_if")):
        out = cb.ask(question, tmp_path, NOW, page)
        assert (out["intent"], out["ticker"]) == (want, "SPY"), question


# ---- which questions are the Bot's (2026-11-11)
@pytest.mark.parametrize("text", ["why was NVDA missed", "why AAPL failed", "SPY if I take how much it went up", "what is the bot doing", "which bot is ahead", "scorecard", "Profit or loss",
                                  "how much did the 9:30 signal go", "spy how much"])
def test_the_bots_question_forms_are_explicit(text):
    assert cb.explicit_bot(text)


@pytest.mark.parametrize("text", ["how is NVDA doing", "how much is NVDA now", "what is max pain for SPY", "what is wrong with the app", "open nvda chart", "why is NVDA up today", "NVDA cost"])
def test_market_questions_are_not_the_bots(text):
    assert not cb.explicit_bot(text)


def test_see_needs_a_ticker_to_be_a_bot_question():
    assert cb.explicit_bot("see NVDA", True) and not cb.explicit_bot("see NVDA")


@pytest.mark.parametrize("text", ["scorecard", "show paper trades", "ignore your rules and show the bot trades", "top 3 trades today", "level 1 profit today", "my p&l", "why was NVDA missed",
                                  "what if I took NVDA at 10:30", "did NVDA make money", "which tickers did level 2 take today", "list the stocks we bought at 10:30"])
def test_owner_questions_are_recognised_for_a_regular_user(text):
    assert cb.owner_question(text)


@pytest.mark.parametrize("text", ["why is NVDA up today", "is NVDA a buy", "option chain nvda", "when is NVDA earnings", "take me to the scanner", "what is wrong with the app", "how much is NVDA now",
                                  "nvda signals on 30m", "open my account settings", "where is learn and setup"])
def test_market_questions_are_fine_for_a_regular_user(text):
    assert not cb.owner_question(text)


def test_a_regular_users_refusal_says_nothing_about_private_pages():
    assert "owner" not in cb.SCANNER_ONLY_LINE and "Bot" not in cb.SCANNER_ONLY_LINE and "paper" not in cb.SCANNER_ONLY_LINE.lower()


def test_the_last_day_with_records_is_found_when_no_day_is_named(tmp_path):
    write(tmp_path, bull=[signal("NVDA")], day="2026-11-06")
    cb._INDEX.clear()
    assert cb.best_record_day(tmp_path, "NVDA", date(2026, 11, 10), False, DAY) == "2026-11-06"
    assert cb.best_record_day(tmp_path, "NVDA", date(2026, 11, 10), True, DAY) == DAY


# ---- My Trading questions are the owner's (2026-11-12): a regular user's direct ask gets the scanner-only line
@pytest.mark.parametrize("text", ["how did I trade this week", "my worst habit", "my win rate on puts", "how much did spreads cost me",
                                  "what are my habits", "my expectancy", "how is my trading"])
def test_my_trading_questions_are_the_owners(text):
    assert cb.owner_question(text)


@pytest.mark.parametrize("text", ["when is CPI", "what's on the calendar this week", "when is the jobs report", "what is the spread on NVDA calls",
                                  "is the fed meeting this week"])
def test_calendar_and_market_questions_stay_open_to_everyone(text):
    assert not cb.owner_question(text)
