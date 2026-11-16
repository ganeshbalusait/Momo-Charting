"""Ask the app, scanner history (2026-11-11): momx/chat_scanner.py through momx/chatbot.ask. Scratch files only."""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta

import pytest

from momx import chat_scanner as cs
from momx import chat_scanner_index as index
from momx import chatbot as cb
from momx.history import board_dir

DAY = "2026-11-10"
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=cb.ET)


def at(clock, day=DAY):
    return f"{day}T{clock}-05:00"


def snap(clock, hl, last=100.0, pct=1.0, level=1, setups=3, day=DAY, stamp=None, **row):
    inputs = {"version": 1, "highestLevel": level, "setupCount": setups, "setupTypes": [{"key": f"k{i}", "text": f"Setup{i}"} for i in range(setups or 0)]}
    body = {"scanPass": True, "highLow": {"value": round(hl, 2), "degree": hl}, "last": last, "pctChange": pct, "scannerInputs": inputs, **row}
    return {"at": stamp if stamp is not None else at(clock, day), "changed": [], "row": body}


def entry(snaps, last_seen="15:50:00", day=DAY):
    return {"firstSeenAt": snaps[0]["at"], "lastSeenAt": at(last_seen, day), "hits": len(snaps), "truncated": False, "snapshots": snaps}


def write_board(base, name, rows, day=DAY, bear=False, indent=None):
    root = base / "momx_history"
    path = board_dir(root / "bear" if bear else root, name) / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"list": name, "date": day, "rows": rows}, indent=indent), encoding="utf-8")
    return path


def ask(base, question, now=NOW, context=None):
    return cb.ask(question, base, now, context)


def text(out):
    return "\n".join(out["lines"])


def bullets(out):
    return [line for line in out["lines"] if line.startswith("- ")]


def in_process(path, bear):
    """The index builder without a child process, for speed. test_a_real_child... runs the real one."""
    return list(index.build(path, bear))


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(cs, "_run_builder", in_process)
    cs._CACHE.clear()
    cs._REGULAR_CACHE.clear()
    cb._CACHE.clear()
    cb._INDEX.clear()
    cs._CHILD_REST["until"] = 0.0
    cs._NOW_CACHE.clear()
    yield
    for _ in range(500):                                                      # a background read must not outlive the fake builder
        if not cs._JOBS:
            break
        cs.time.sleep(0.01)
    cs._CACHE.clear()
    cs._REGULAR_CACHE.clear()
    cs._CHILD_REST["until"] = 0.0


# ---------------------------------------------------------------- who was on the board at a time
def test_a_ticker_whose_last_match_was_long_before_is_not_carried_into_the_list(tmp_path):
    """The trap: MO's last match was 9:15, so a replay of old values would keep it at the top all day."""
    write_board(tmp_path, "Watchlist", {
        "MO": entry([snap("09:15:00", 0.99)]),
        "ZETA": entry([snap("09:28:00", 0.93, last=10.0, pct=2.1, level=2, setups=3), snap("10:30:00", 0.95, last=11.13), snap("15:00:00", 0.9, last=11.11)]),
        "GONE": entry([snap("09:29:00", 0.97)], last_seen="09:29:30"),
    })
    out = ask(tmp_path, "from the scanner on watchlist at 9:30 give me the list of tickers")
    assert out["intent"] == "scanner_list" and out["day"] == DAY and out["ticker"] is None
    assert bullets(out) == ["- #1 ZETA H/L +0.93 · +2.1% · Level 2 · 3 setups · then best +11.3%, last +11.1%"]
    earlier = ask(tmp_path, "watchlist list at 9:20")
    assert [b.split()[2] for b in bullets(earlier)] == ["MO"]                  # 5 minutes old: it does count then


def test_the_ten_minute_edge_and_the_end_of_the_minute(tmp_path):
    write_board(tmp_path, "Mag7", {"AAA": entry([snap("09:30:59", 0.5)])})
    assert bullets(ask(tmp_path, "mag7 list at 9:30"))                        # saved during 9:30: it counts for 9:30
    assert not bullets(ask(tmp_path, "mag7 list at 9:29"))
    assert bullets(ask(tmp_path, "mag7 list at 9:40"))                        # exactly 10 minutes later
    assert not bullets(ask(tmp_path, "mag7 list at 9:41"))


def test_puts_rank_lowest_first_and_ties_go_by_symbol(tmp_path):
    write_board(tmp_path, "Mag7", {s: entry([snap("09:59:00", hl)]) for s, hl in (("BBB", -0.5), ("AAA", -0.5), ("CCC", -0.9), ("DDD", 0.2))}, bear=True)
    out = ask(tmp_path, "what tickers were on the bear scanner mag7 at 10:00")
    assert [b.split()[2] for b in bullets(out)] == ["CCC", "AAA", "BBB", "DDD"] and out["side"] == "bear"
    assert "Mag7 puts at 10:00 AM" in text(out)


def test_the_later_move_is_in_the_direction_of_the_trade(tmp_path):
    rows = {"DOWN": entry([snap("10:00:00", -0.8, last=100.0), snap("11:00:00", -0.8, last=90.0), snap("12:00:00", -0.8, last=95.0)])}
    write_board(tmp_path, "Mag7", rows, bear=True)
    assert "then best +10.0%, last +5.0%" in bullets(ask(tmp_path, "mag7 puts list at 10:00"))[0]


def test_the_degree_ranks_and_falls_back_to_the_value_and_a_row_with_no_hl_has_no_rank(tmp_path):
    no_degree = snap("09:59:00", 0.5)
    no_degree["row"]["highLow"] = {"value": 0.7}
    none = snap("09:59:00", 0.1)
    none["row"]["highLow"] = {"value": None}
    write_board(tmp_path, "Mag7", {"AAA": entry([snap("09:59:00", 0.6)]), "BBB": entry([no_degree]), "CCC": entry([none])})
    out = ask(tmp_path, "mag7 list at 10:00 CCC")
    assert [b.split()[2] for b in bullets(out)] == ["BBB", "AAA"]
    assert "no H/L was saved" in text(out)


def test_board_choice_defaults_to_mag7_and_watchlist_each_in_its_own_block(tmp_path):
    for name, sym in (("Mag7", "AAPL"), ("Watchlist", "ZETA"), ("Movers", "MOVE"), ("Liquid Movers", "LIQ"), ("21 EMA Touch Weekly", "EMAW"), ("50 EMA Touch Monthly", "EMAM")):
        write_board(tmp_path, name, {sym: entry([snap("09:59:00", 0.5)])})
    both = ask(tmp_path, "from the scanner at 10:00 give me the list of tickers")
    assert [b.split()[2] for b in bullets(both)] == ["AAPL", "ZETA"]
    assert [f["board"] for f in both["facts"]["boards"]] == ["Mag7", "Watchlist"]
    for question, sym in (("mag7 list at 10:00", "AAPL"), ("mag 7 list at 10:00", "AAPL"), ("watch list list at 10:00", "ZETA"), ("movers list at 10:00", "MOVE"),
                          ("liquid movers list at 10:00", "LIQ"), ("21 ema list at 10:00", "EMAW"), ("50 ema touch monthly list at 10:00", "EMAM")):
        assert [b.split()[2] for b in bullets(ask(tmp_path, question))] == [sym], question


def test_top_n_and_the_default_ten_say_how_many_more(tmp_path):
    write_board(tmp_path, "Watchlist", {f"T{chr(65 + i)}XX": entry([snap("09:59:00", 0.9 - i / 100)]) for i in range(14)})
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    top = ask(tmp_path, "top 7 on watchlist at 10")
    assert len(bullets(top)) == 7 and "... and 7 more." in top["lines"]
    plain = ask(tmp_path, "watchlist list at 10:00")
    assert len(bullets(plain)) == 10 and "... and 4 more." in plain["lines"]
    assert len(bullets(ask(tmp_path, "mag7 top three at 10:00"))) == 1


def test_level_and_setup_filters_keep_the_boards_own_rank(tmp_path):
    write_board(tmp_path, "Watchlist", {"AAA": entry([snap("09:59:00", 0.9, level=1, setups=2)]), "BBB": entry([snap("09:59:00", 0.8, level=3, setups=4)]),
                                        "CCC": entry([snap("09:59:00", 0.7, level=2, setups=3)])})
    write_board(tmp_path, "Mag7", {})
    level = ask(tmp_path, "which tickers had level 3 at 10")
    assert [b.split()[:3] for b in bullets(level)] == [["-", "#2", "BBB"]] and "1 of 3 tickers on the board had Level 3." in text(level)
    assert [b.split()[2] for b in bullets(ask(tmp_path, "watchlist list level 2 or higher at 10:00"))] == ["BBB", "CCC"]
    assert [b.split()[2] for b in bullets(ask(tmp_path, "watchlist setup 3 at 10:00"))] == ["BBB", "CCC"]


def test_a_level_that_was_not_saved_says_so_and_is_not_counted_as_a_match(tmp_path):
    bare = snap("09:59:00", 0.5)
    del bare["row"]["scannerInputs"]
    write_board(tmp_path, "Mag7", {"AAA": entry([bare])})
    assert "Level not saved · setups not saved" in bullets(ask(tmp_path, "mag7 list at 10:00"))[0]
    assert not bullets(ask(tmp_path, "mag7 level 0 at 10:00"))


def test_a_named_ticker_gets_the_list_and_a_line_saying_where_it_stood(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:28:00", 0.4)]), "AAPL": entry([snap("09:28:00", 0.8)])})
    write_board(tmp_path, "Watchlist", {"ZETA": entry([snap("09:28:00", 0.9)]), "NVDA": entry([snap("10:15:00", 0.5)])})
    out = ask(tmp_path, "from the scanner NVDA at 9:30 give me the list of tickers")
    assert (out["intent"], out["ticker"]) == ("scanner_list", "NVDA") and len(bullets(out)) == 3
    stood = next(line for line in out["lines"] if line.startswith("NVDA at"))
    assert "Mag7: #2 of 2, H/L +0.40" in stood and "Watchlist: not on the board then (nearest time it was: 10:15 AM)" in stood
    assert 'Add "puts" to check the bear scanner.' in stood
    assert out["suggest"][0] == "NVDA at 9:30 AM in the scanner"


def test_a_time_before_anything_was_on_the_board_says_the_first_time(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:41:00", 0.5)])})
    assert "Nothing was on Mag7 calls yet at 9:30 AM on 2026-11-10. The first ticker came on at 9:41 AM." in text(ask(tmp_path, "mag7 list at 9:30"))
    assert "The last saved time that day was 3:50 PM" in text(ask(tmp_path, "mag7 list at 18:00"))


def test_open_close_and_latest(tmp_path):
    write_board(tmp_path, "Mag7", {"OPN": entry([snap("09:30:00", 0.5)], last_seen="09:30:30"), "CLS": entry([snap("15:55:00", 0.5)], last_seen="16:10:00"),
                                   "LAT": entry([snap("16:05:00", 0.6)], last_seen="16:10:00")})
    assert [b.split()[2] for b in bullets(ask(tmp_path, "mag7 list at the open"))] == ["OPN"]
    assert [b.split()[2] for b in bullets(ask(tmp_path, "mag7 list at the close"))] == ["CLS"]
    latest = ask(tmp_path, "mag7 list")
    assert [b.split()[2] for b in bullets(latest)] == ["LAT"] and "the latest saved time, 4:10 PM," in text(latest)


def test_an_epoch_timestamp_reads_on_the_et_clock(tmp_path):
    stamp = datetime(2026, 11, 10, 9, 59, tzinfo=cb.ET).timestamp()
    write_board(tmp_path, "Mag7", {"SEC": entry([snap("", 0.5, stamp=stamp)]), "MSX": entry([snap("", 0.4, stamp=stamp * 1000)])})
    assert [b.split()[2] for b in bullets(ask(tmp_path, "mag7 list at 10:00"))] == ["SEC", "MSX"]


def test_the_days_are_read_from_the_sentence(tmp_path):
    write_board(tmp_path, "Mag7", {"OLD": entry([snap("09:59:00", 0.5, day="2026-11-09")], day="2026-11-09")}, day="2026-11-09")
    write_board(tmp_path, "Mag7", {"FRI": entry([snap("09:59:00", 0.5, day="2026-11-06")], day="2026-11-06")}, day="2026-11-06")
    for question, sym, day in (("mag7 list 10:00 yesterday", "OLD", "2026-11-09"), ("mag7 list at 10:00 friday", "FRI", "2026-11-06"),
                               ("mag7 list at 10:00 on 2026-11-06", "FRI", "2026-11-06"), ("mag7 list at 10:00 nov 6", "FRI", "2026-11-06")):
        out = ask(tmp_path, question)
        assert ([b.split()[2] for b in bullets(out)], out["day"], out["ticker"]) == ([sym], day, None), question


def test_with_no_day_named_a_time_still_to_come_or_a_weekend_reads_the_last_session():
    monday_early = datetime(2026, 11, 9, 8, 0, tzinfo=cb.ET)
    assert cs.last_session(monday_early.date(), False, 570, monday_early) == date(2026, 11, 6)
    assert cs.last_session(monday_early.date(), True, 570, monday_early) == monday_early.date()
    assert cs.last_session(monday_early.date(), False, 420, monday_early) == monday_early.date()
    saturday = datetime(2026, 11, 7, 12, 0, tzinfo=cb.ET)
    assert cs.last_session(saturday.date(), False, None, saturday) == date(2026, 11, 6)


def test_a_missing_file_a_broken_file_and_an_empty_day_are_plain_lines(tmp_path):
    out = ask(tmp_path, "from the scanner at 9:30 give me the list of tickers")
    assert out["intent"] == "scanner_list"
    assert "Mag7 calls: 2026-11-10 has no scanner history (it is kept 30 days)." in out["lines"]
    assert out["lines"][-1] == cs.SOURCE
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    path.write_text(path.read_text(encoding="utf-8")[:40], encoding="utf-8")
    write_board(tmp_path, "Watchlist", {})
    broken = ask(tmp_path, "scanner list at 10:00")
    assert "Mag7 calls: the saved history for 2026-11-10 could not be read." in broken["lines"]
    assert "Watchlist calls: nothing was saved on 2026-11-10." in broken["lines"]


@pytest.mark.parametrize("junk", ['{"rows": []}', '[]', '{"rows": {"AAA": 5, "BBB": {"snapshots": [7, {"at": "soon", "row": {"scanPass": true}}, {"row": null}]}}}'])
def test_odd_shapes_never_raise(tmp_path, junk):
    path = write_board(tmp_path, "Mag7", {})
    path.write_text(junk, encoding="utf-8")
    assert ask(tmp_path, "mag7 list at 10:00")["lines"][-1] == cs.SOURCE


def test_the_walker_reads_a_file_with_spaces_the_same_as_a_compact_one(tmp_path):
    rows = {"AAA": entry([snap("09:59:00", 0.5)]), "BBB": entry([snap("09:58:00", 0.6)])}
    spaced = cs._parse(write_board(tmp_path, "Mag7", rows, indent=2), "Mag7", False, DAY)
    tight = cs._parse(write_board(tmp_path, "Watchlist", rows), "Watchlist", False, DAY)
    assert not spaced.broken and list(spaced.tickers) == list(tight.tickers) == ["AAA", "BBB"]
    assert spaced.tickers["AAA"].points == tight.tickers["AAA"].points


# ---------------------------------------------------------------- one ticker
def lit_row():
    return {"rvol": {"5m": {"value": 3.2, "bg": "cyan", "fg": "black"}, "15m": {"value": 1.2, "bg": "black", "fg": "green"},
                     "30m": {"value": 2.5, "bg": "red", "fg": "black"}, "1h": {"value": 0.1, "bg": "black", "fg": "black"}, "2h": {"value": None, "bg": "cyan"}},
            "sqz": {"2h": {"value": "1", "bg": "cyan", "fg": "black"}, "4h": {"value": "-", "bg": "black", "fg": "black"}, "D": {"value": "1", "bg": "magenta"}, "Wk": {"value": "5", "bg": "orange"}},
            "skittles": {"2h": {"value": 50, "bg": "green"}, "4h": {"value": 50, "bg": "red"}, "D": {"value": 50, "bg": "black", "fg": "cyan"}, "M": {"value": 50, "bg": "dark_green"}}}


def test_a_ticker_at_a_time_says_rank_columns_setups_and_the_move_after(tmp_path):
    write_board(tmp_path, "Watchlist", {"CMCSA": entry([snap("11:20:00", 0.81, last=25.0, pct=1.2, level=2, setups=2, **lit_row()), snap("12:00:00", 0.8, last=26.0), snap("13:00:00", 0.8, last=25.5)]),
                                        "ZETA": entry([snap("11:20:00", 0.9)])})
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("11:20:00", 0.5)])})
    for question in ("CMCSA at 11:23 in the scanner", "what did the scanner show for CMCSA at 11:23", "cmcsa at 11:23 in the scanner"):
        out = ask(tmp_path, question)
        assert (out["intent"], out["ticker"], out["facts"]["onBoard"]) == ("scanner_ticker", "CMCSA", True), question
        assert out["lines"] == ["CMCSA on Watchlist calls at 11:23 AM on 2026-11-10: #2 of 2, H/L +0.81, +1.2%, $25.00 (row saved 11:20 AM).",
                                "- RVOL lit: 5m, 15m", "- SQZ lit: 2h (blank: 4h)", "- Skittles lit: 2h, M",
                                "- 2 setups (Setup0, Setup1) · Level 2, as saved", "- After that: best +4.0% ($26.00), last +2.0% ($25.50)", cs.SOURCE]


def test_the_bear_side_reads_the_mirror_colours(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("11:20:00", -0.8, **lit_row())])}, bear=True)
    said = text(ask(tmp_path, "NVDA puts at 11:23 in the scanner mag7"))
    assert "- RVOL lit: 30m" in said and "- SQZ lit: D (blank: 4h)" in said and "- Skittles lit: 4h" in said


def test_a_ticker_only_on_the_puts_is_answered_from_the_puts_when_no_side_is_named(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:59:00", -0.5)])}, bear=True)
    out = ask(tmp_path, "NVDA at 10:00 in the scanner mag7")
    assert out["side"] == "bear" and "NVDA on Mag7 puts at 10:00 AM" in text(out)
    named = ask(tmp_path, "NVDA calls at 10:00 in the scanner mag7")
    assert named["side"] == "bull" and "NVDA was not on Mag7 calls at all on 2026-11-10." in text(named)


def test_a_ticker_not_on_the_board_then_says_the_nearest_time(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:50:00", 0.5), snap("13:00:00", 0.5)])})
    assert "The nearest time it was on: 10:00 AM." in text(ask(tmp_path, "AAPL at 10:30 in the scanner mag7 calls"))
    assert "The nearest time it was on: 1:00 PM." in text(ask(tmp_path, "AAPL at 12:30 in the scanner mag7 calls"))


def test_a_ticker_only_the_scanner_knows_is_recognised(tmp_path):
    write_board(tmp_path, "Mag7", {})
    write_board(tmp_path, "Watchlist", {"QQXZ": entry([snap("09:59:00", 0.5)])})
    assert ask(tmp_path, "qqxz at 10:00 in the scanner")["ticker"] == "QQXZ"
    missing = ask(tmp_path, "ABCD at 10:00 in the scanner")
    assert (missing["intent"], missing["ticker"]) == ("scanner_ticker", "ABCD") and "ABCD was not on Watchlist calls at all" in text(missing)


def test_scanner_words_and_board_names_are_not_tickers(tmp_path):
    write_board(tmp_path, "Mag7", {"MAG": entry([snap("09:59:00", 0.5)]), "LIST": entry([snap("09:59:00", 0.4)]), "NOV": entry([snap("09:59:00", 0.3)]),
                                   "OPEN": entry([snap("09:59:00", 0.2)])})
    for question in ("mag7 top 3 at 10:00", "MAG7 list at 10:00", "scanner list mag7 at 10:00 nov 10"):
        assert ask(tmp_path, question)["ticker"] is None, question
    assert ask(tmp_path, "$OPEN at 10:00 in the scanner mag7")["ticker"] == "OPEN"
    assert ask(tmp_path, "OPEN at 10:00 in the scanner mag7")["ticker"] == "OPEN"


def test_short_tickers_are_read_the_way_the_chat_reads_them_elsewhere(tmp_path):
    write_board(tmp_path, "Mag7", {"T": entry([snap("09:59:00", 0.5)]), "GS": entry([snap("09:59:00", 0.4)])})
    assert ask(tmp_path, "$T at 10:00 in the scanner mag7")["ticker"] == "T"
    assert ask(tmp_path, "GS at 10:00 in the scanner mag7")["ticker"] == "GS"


# ---------------------------------------------------------------- who stayed on top
def minutes(start, count, hl, last=100.0):
    """One saved row every 5 minutes from start (HH:MM), so the ticker stays fresh for the whole span."""
    h, m = map(int, start.split(":"))
    out = []
    for i in range(0, count, 5):
        total = h * 60 + m + i
        out.append(snap(f"{total // 60:02d}:{total % 60:02d}:00", hl, last=last))
    return out


def test_the_top_counts_minutes_runs_and_holds_per_board(tmp_path):
    a = minutes("09:30", 20, 0.9, last=100.0) + [snap("11:00:00", 0.9, last=110.0), snap("15:00:00", 0.9, last=105.0)]
    write_board(tmp_path, "Mag7", {"AAA": entry(a, last_seen="15:00:30"), "BBB": entry([snap("09:30:00", 0.1)], last_seen="09:31:00")})
    write_board(tmp_path, "Watchlist", {"ZZZ": entry([snap("10:00:00", 0.5)], last_seen="10:02:30")})
    out = ask(tmp_path, "which tickers stayed on top today")
    assert out["intent"] == "scanner_top" and out["ticker"] is None
    stats = cs.top_stats(cs._board(tmp_path, "Mag7", False, DAY, DAY), 3, cs.SESSION_END_MIN)
    # AAA: 9:30-9:54 (the 9:45 row stays fresh 10 minutes), then 11:00-11:09, then 15:00 until it left.
    assert stats["AAA"]["minutes"] == 25 + 10 + 1 and stats["AAA"]["longest"] == 25 and stats["AAA"]["holds"] == 2
    assert stats["BBB"]["minutes"] == 2 and stats["BBB"]["holds"] == 0
    assert out["lines"][0] == "Mag7 calls, 9:30 AM to 4:00 PM on 2026-11-10: 2 tickers reached the top 3."
    # 9:55-11:00 and 11:10-15:00 have no saved row, so it may have been there longer than the rows show.
    assert out["lines"][1] == "- AAA: at least 36 min in the top 3 · longest saved run 25 min · 2 holds of 5+ min · first in at 9:30 AM · then best +10.0%, last +5.0%"
    assert "Watchlist calls, 9:30 AM to 4:00 PM on 2026-11-10: 1 ticker reached the top 7." in out["lines"]
    assert out["lines"][-1] == cs.SOURCE


def test_top_uses_his_three_and_seven(tmp_path):
    names = {f"T{chr(65 + i)}XX": entry([snap("09:30:00", 0.9 - i / 100)], last_seen="09:31:00") for i in range(9)}
    write_board(tmp_path, "Mag7", names)
    write_board(tmp_path, "Watchlist", names)
    assert "reached the top 3." in ask(tmp_path, "who repeated in the top")["lines"][0]
    out = ask(tmp_path, "who repeated in the top")
    assert "7 tickers reached the top 7." in text(out) and "3 tickers reached the top 3." in text(out)


def test_only_the_top_eight_are_named(tmp_path):
    write_board(tmp_path, "Mag7", {})
    write_board(tmp_path, "Watchlist", {f"T{chr(65 + i)}XX": entry([snap("09:30:00", 0.9 - i / 100)], last_seen="09:31:00") for i in range(5)}
                | {f"U{chr(65 + i)}XX": entry([snap("09:40:00", 0.9 - i / 100)], last_seen="09:41:00") for i in range(5)})
    out = ask(tmp_path, "which tickers stayed on top")
    assert len(bullets(out)) == 8 and "... and 2 more." in out["lines"]


def test_how_long_was_one_ticker_in_the_top(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:30:00", 0.5)], last_seen="09:31:00")})
    write_board(tmp_path, "Watchlist", {"CMCSA": entry(minutes("09:30", 10, 0.9), last_seen="09:40:00")})
    out = ask(tmp_path, "how long was CMCSA in the top 7")
    assert (out["intent"], out["ticker"]) == ("scanner_top", "CMCSA")
    assert out["lines"] == ["CMCSA on Watchlist calls, 9:30 AM to 4:00 PM on 2026-11-10: 11 min in the top 7 · longest run 11 min · 1 hold of 5+ min · "
                            "first in at 9:30 AM · then best +0.0%, last +0.0%.", cs.SOURCE]
    assert out["facts"]["boards"] == [{"board": "Mag7", "top": 7, "minutes": 0}, {"board": "Watchlist", "top": 7, "minutes": 11}]


def test_a_ticker_never_in_the_top_says_so(tmp_path):
    write_board(tmp_path, "Mag7", {f"T{chr(65 + i)}XX": entry([snap("09:30:00", 0.9)], last_seen="15:00:00") for i in range(3)} | {"LOW": entry([snap("09:30:00", 0.1)], last_seen="09:39:00")})
    write_board(tmp_path, "Watchlist", {})
    assert "LOW on Mag7 calls, 9:30 AM to 4:00 PM on 2026-11-10: never in the top 3." in text(ask(tmp_path, "how long was LOW in the top"))


def test_today_is_only_counted_up_to_now(tmp_path):
    now = datetime(2026, 11, 10, 9, 34, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"AAA": entry([snap("09:30:00", 0.9)], last_seen="09:34:00")})
    write_board(tmp_path, "Watchlist", {})
    assert "5 min in the top 3" in text(ask(tmp_path, "who stayed on top", now=now))


# ---------------------------------------------------------------- reading the sentence
def test_times_in_the_sentence():
    assert [cs.read_minute(q, cb.parse_time(q)) for q in ("at 11", "at 10", "at 3", "at 9:45", "2pm", "at the open", "at the close", "no time")] == [660, 600, 900, 585, 840, 570, 959, None]
    assert cs.read_minute("list on 11/6", cb.parse_time("list on 11/6")) is None


@pytest.mark.parametrize("question,ticker,follow,want", [
    ("from the scanner at 9:30 give me the list of tickers", False, False, "scanner_list"),
    ("scanner list 10:00 yesterday", False, False, "scanner_list"),
    ("top 7 on watchlist at 11", False, False, "scanner_list"),
    ("mag7 top 3 at 9:45", False, False, "scanner_list"),
    ("what tickers were on the bear scanner at 10:30", False, False, "scanner_list"),
    ("which tickers had level 3 at 10", False, False, "scanner_list"),
    ("from the scanner NVDA at 9:30 give me the list of tickers", True, False, "scanner_list"),
    ("from the scanner at 9:30 give me the list of tickers", False, True, "scanner_list"),
    ("NVDA at 9:30 in the scanner", True, False, "scanner_ticker"),
    ("what did the scanner show for CMCSA at 11:23", True, False, "scanner_ticker"),
    ("where was it in the scanner", False, True, "scanner_ticker"),
    ("what was the rvol", False, True, "scanner_ticker"),
    ("which tickers stayed on top today", False, False, "scanner_top"),
    ("who repeated in the top", False, False, "scanner_top"),
    ("how long was CMCSA in the top 7", True, False, "scanner_top"),
    ("see SPY", True, False, None),
    ("SPY at 10:30", True, False, None),
    ("what is the rvol", False, False, None),
    ("hello there", False, False, None),
    ("how is level 3 doing", False, False, None),
    ("level 1 profit today", False, False, None),
    ("what were the top 3 trades today", False, False, None),
    ("best 3 trades today", False, False, None),
    ("top 5 winning trades", False, False, None),
    ("see NVDA level 3", True, False, None),
    ("see nvda level 3", False, False, None),
    ("top 3 trades at 10:00", False, False, None),
    ("top 3 at 9:45", False, False, "scanner_list"),
])
def test_routing(question, ticker, follow, want):
    assert cs.route(question, ticker, follow, cs.read_minute(question, cb.parse_time(question))) == want


# ---------------------------------------------------------------- the cost
def test_each_file_is_read_once_and_a_past_day_is_kept(tmp_path, monkeypatch):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day="2026-11-09")], day="2026-11-09")}, day="2026-11-09")
    write_board(tmp_path, "Watchlist", {}, day="2026-11-09")
    parses = []
    real = cs._parse
    monkeypatch.setattr(cs, "_parse", lambda *a: parses.append(a[1]) or real(*a))
    for _ in range(3):
        ask(tmp_path, "scanner list at 10:00 yesterday")
    assert sorted(parses) == ["Mag7", "Watchlist"]


def test_today_is_looked_at_every_five_minutes_and_reread_only_when_it_changed(tmp_path, monkeypatch):
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    clock = [1000.0]
    parses = []
    real = cs._parse
    monkeypatch.setattr(cs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(cs, "_parse", lambda *a: parses.append(1) or real(*a))
    now_day = date.fromisoformat(DAY)
    assert now_day == NOW.date()
    first = cs._board(tmp_path, "Mag7", False, DAY, DAY)
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)]), "NEWER": entry([snap("09:59:00", 0.6)])})
    clock[0] += 299
    assert cs._board(tmp_path, "Mag7", False, DAY, DAY) is first and len(parses) == 1          # within the five minutes: not looked at
    clock[0] += 2
    newer = cs._board(tmp_path, "Mag7", False, DAY, DAY)
    assert newer is not first and "NEWER" in newer.tickers and len(parses) == 2
    clock[0] += 301
    assert cs._board(tmp_path, "Mag7", False, DAY, DAY) is newer and len(parses) == 2          # looked at, unchanged
    assert path.exists()


def test_a_past_days_file_that_changed_is_read_again(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    first = cs._board(tmp_path, "Mag7", False, DAY, "2026-11-20")
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)]), "MORE": entry([snap("09:59:00", 0.6)])})
    assert "MORE" in cs._board(tmp_path, "Mag7", False, DAY, "2026-11-20").tickers and first is not None


def test_the_cache_holds_two_days_of_calls_and_puts_and_no_full_rows(tmp_path):
    assert cs.CACHE_FILES >= 10
    for i in range(1, cs.CACHE_FILES + 3):
        day = f"2026-11-{i:02d}"
        write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day=day, sparkline=[1] * 50)], day=day)}, day=day)
        board = cs._board(tmp_path, "Mag7", False, day, "2026-11-20")
    assert len(cs._CACHE) == cs.CACHE_FILES and list(cs._CACHE)[-1].endswith(f"2026-11-{cs.CACHE_FILES + 2:02d}.json")
    assert isinstance(board.tickers["AAPL"].points[0], cs.Point) and not any(isinstance(v, (dict, list)) for v in board.tickers["AAPL"].points[0])


def test_a_busy_rebuild_serves_the_copy_in_hand(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    first = cs._board(tmp_path, "Mag7", False, DAY, DAY)
    key = next(iter(cs._CACHE))
    cs._CACHE[key][2] = -1e9                                                                  # due for a look
    with cs._file_lock(key):
        assert cs._board(tmp_path, "Mag7", False, DAY, DAY) is first


def test_it_only_reads(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")}
    for question in ("scanner list at 10:00", "AAPL at 10:00 in the scanner", "who stayed on top"):
        ask(tmp_path, question)
    assert {p: p.stat().st_mtime_ns for p in tmp_path.rglob("*")} == before


# ---------------------------------------------------------------- the row-saving cap (the real file shape)
def capped(snaps, last_seen, hits=494, day=DAY):
    """What momx/history.py leaves at the 120-row cap: truncated, no more rows, lastSeenAt and hits still advancing."""
    return {"firstSeenAt": snaps[0]["at"], "lastSeenAt": at(last_seen, day), "hits": hits, "truncated": True, "snapshots": snaps}


def nvda_capped():
    return capped([snap("09:30:00", -0.9, last=100.0), snap("09:49:00", -0.8, last=95.0)], "15:11:00")


def test_a_capped_ticker_is_never_said_to_be_off_the_board(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped(), "TSLA": entry([snap("12:55:00", -0.5)])}, bear=True)
    write_board(tmp_path, "Watchlist", {}, bear=True)
    out = ask(tmp_path, "NVDA at 13:00 in the scanner puts")
    assert out["facts"]["onBoard"] is None and out["side"] == "bear"
    assert out["lines"][0] == ("NVDA on Mag7 puts at 1:00 PM on 2026-11-10: row saving stopped at 9:49 AM; last seen at 3:11 PM. "
                               "Whether it was on the board at 1:00 PM is not known.")
    assert "not on" not in text(out).replace("not known", "") and "still on" not in text(out)
    assert out["lines"][1] == "- Last saved row, 9:49 AM: H/L -0.80, +1.0%"


def test_after_a_capped_ticker_was_last_seen_it_really_is_off_the_board(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped()}, bear=True)
    out = ask(tmp_path, "NVDA at 15:30 in the scanner mag7 puts")
    assert "NVDA was not on Mag7 puts at 3:30 PM" in text(out) and out["facts"]["onBoard"] is False


def test_a_fresh_row_before_the_cap_still_answers_with_its_rank(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped()}, bear=True)
    out = ask(tmp_path, "NVDA at 9:55 in the scanner mag7 puts")
    assert out["lines"][0].startswith("NVDA on Mag7 puts at 9:55 AM on 2026-11-10: #1 of 1")
    assert any("saved prices stop at the row-saving cap, 9:49 AM" in line or "After that: no later saved price" in line for line in out["lines"])


def test_the_list_names_capped_tickers_and_says_ranks_could_be_off(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped(), "TSLA": entry([snap("12:55:00", -0.5)])}, bear=True)
    write_board(tmp_path, "Watchlist", {}, bear=True)
    out = ask(tmp_path, "from the scanner NVDA puts at 13:00 give me the list of tickers")
    assert [b.split()[2] for b in bullets(out)] == ["TSLA"]
    assert ("Not counted on Mag7 puts at 1:00 PM: row saving stopped (time in brackets), so it is not known whether these were on the board then: "
            "NVDA (9:49 AM). Ranks could be off.") in out["lines"]
    assert "Mag7: not known, row saving stopped at 9:49 AM, last seen 3:11 PM" in text(out)
    assert out["facts"]["boards"][0]["capped"] == ["NVDA"]


def test_a_board_of_only_capped_tickers_still_names_them(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped()}, bear=True)
    assert "NVDA (9:49 AM). Ranks could be off." in text(ask(tmp_path, "mag7 puts list at 13:00"))


def test_another_tickers_rank_warns_about_a_capped_one(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped(), "TSLA": entry([snap("12:55:00", -0.5)])}, bear=True)
    assert any(line.startswith("- Rank could be off:") and "NVDA (9:49 AM)" in line for line in ask(tmp_path, "TSLA puts at 13:00 in the scanner mag7")["lines"])


def test_top_says_at_least_for_capped_and_stops_the_move_at_the_cap(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped(), "TSLA": entry(minutes("13:00", 60, -0.5), last_seen="14:00:00")}, bear=True)
    write_board(tmp_path, "Watchlist", {}, bear=True)
    out = ask(tmp_path, "which tickers stayed on top puts")
    line = next(b for b in bullets(out) if b.startswith("- NVDA"))
    assert ": at least " in line and "longest saved run" in line and "(saved prices stop at the row-saving cap, 9:49 AM)" in line
    assert not next(b for b in bullets(out) if b.startswith("- TSLA")).count("at least")
    assert "their minutes" in text(out).lower() and "could be too high" in text(out)
    one = ask(tmp_path, "how long was NVDA in the top puts")
    assert "at least " in one["lines"][0]


def test_a_capped_ticker_outside_the_top_is_not_called_never(tmp_path):
    rows = {f"T{chr(65 + i)}XX": entry(minutes("09:30", 30, -0.95), last_seen="10:00:00") for i in range(3)}
    write_board(tmp_path, "Mag7", {**rows, "NVDA": capped([snap("09:30:00", -0.1), snap("09:49:00", -0.1)], "15:11:00")}, bear=True)
    said = text(ask(tmp_path, "how long was NVDA in the top 3 puts mag7"))
    assert "not in the top 3 while its rows were saved" in said and "never" not in said


# ---------------------------------------------------------------- questions the scanner must leave alone
@pytest.mark.parametrize("question", ["how is level 3 doing", "level 1 profit today", "what were the top 3 trades today", "best 3 trades today", "top 5 winning trades"])
def test_questions_about_the_bot_do_not_become_a_board_and_read_no_board(tmp_path, monkeypatch, question):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    monkeypatch.setattr(cs, "_parse", lambda *a: pytest.fail("a question about the bot must not read a board"))
    assert not ask(tmp_path, question)["intent"].startswith("scanner")


@pytest.mark.parametrize("question", ["see NVDA level 3", "see nvda level 3", "what is the rvol", "see SPY", "SPY at 10:30"])
def test_the_route_is_decided_before_any_file_is_read(tmp_path, monkeypatch, question):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:59:00", 0.5)])})
    monkeypatch.setattr(cs, "_parse", lambda *a: pytest.fail("this is not a scanner question"))
    monkeypatch.setattr(cs, "_board", lambda *a: pytest.fail("this is not a scanner question"))
    assert not ask(tmp_path, question)["intent"].startswith("scanner")


# ---------------------------------------------------------------- times, sides and days in a scanner question
def test_bare_six_and_seven_are_the_morning_for_the_scanner_and_padded_times_are_literal():
    scanner = [cb.parse_time(q, scanner=True) for q in ("at 7:30", "6:45", "07:30", "06:05", "3:30", "1:15", "01:30", "13:00", "7:30 pm", "6pm")]
    assert scanner == [450, 405, 450, 365, 930, 795, 90, 780, 1170, 1080]
    assert [cb.parse_time(q) for q in ("at 7:30", "6:45", "07:30")] == [1170, 1125, 1170]         # other questions: unchanged
    assert [cs.read_minute(q, None) for q in ("at 7", "at 6", "at 07", "at 3", "at 5", "at 03")] == [420, 360, 420, 900, 1020, 180]


def test_scanner_list_at_7_30_reads_that_morning(tmp_path):
    now = datetime(2026, 11, 10, 10, 0, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"PRE": entry([snap("07:28:00", 0.5)], last_seen="07:45:00")})
    for question in ("scanner list at 7:30", "scanner list at 07:30"):
        out = ask(tmp_path, question, now=now)
        assert out["day"] == DAY and "at 7:30 AM on 2026-11-10" in text(out) and [b.split()[2] for b in bullets(out)] == ["PRE"], question


@pytest.mark.parametrize("sentence,want", [("how long was NVDA in the top", None), ("give me a short list from the scanner", None), ("long list of tickers", None),
                                           ("scanner puts", "bear"), ("scanner calls", "bull"), ("if I go short", "bear"), ("go long", "bull"), ("the short side", "bear")])
def test_how_long_and_short_list_are_not_a_side(sentence, want):
    assert cb.scanner_side(sentence) == want


def test_how_long_finds_the_ticker_on_the_puts_when_no_side_is_named(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry(minutes("09:30", 10, 0.9), last_seen="09:40:00")})
    write_board(tmp_path, "Mag7", {"NVDA": entry(minutes("09:30", 10, -0.9), last_seen="09:40:00")}, bear=True)
    out = ask(tmp_path, "how long was NVDA in the top")
    assert out["side"] == "bear" and out["lines"][0].startswith("NVDA on Mag7 puts, 9:30 AM to 4:00 PM on 2026-11-10: 11 min in the top 3")
    named = ask(tmp_path, "how long was NVDA in the top calls")
    assert named["side"] == "bull" and "NVDA was not on Mag7 calls at all" in text(named)


def test_a_short_list_reads_the_calls(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("10:28:00", 0.9)])})
    assert ask(tmp_path, "give me a short list from the scanner at 10:30")["side"] == "bull"


def test_the_top_list_says_how_to_see_the_puts(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry(minutes("09:30", 10, 0.9), last_seen="09:40:00")})
    assert 'That is the calls side. Add "puts" to see the bear scanner.' in ask(tmp_path, "which tickers stayed on top")["lines"]
    write_board(tmp_path, "Mag7", {"AAPL": entry(minutes("09:30", 10, -0.9), last_seen="09:40:00")}, bear=True)
    assert 'That is the calls side. Add "puts" to see the bear scanner.' not in ask(tmp_path, "which tickers stayed on top puts")["lines"]


def test_top_before_the_open_means_the_last_session(tmp_path):
    early = datetime(2026, 11, 10, 8, 0, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"OLD": entry([snap("09:30:00", 0.9, day="2026-11-09")], last_seen="09:40:00", day="2026-11-09")}, day="2026-11-09")
    out = ask(tmp_path, "which tickers stayed on top", now=early)
    assert out["day"] == "2026-11-09" and any(b.startswith("- OLD") for b in bullets(out))
    named = ask(tmp_path, "which tickers stayed on top today", now=early)
    assert named["day"] == DAY


def test_a_word_like_market_is_not_a_month():
    today = date(2026, 11, 10)
    assert cb.parse_day("mag7 list at market 9:30", today) == (today, False)
    assert cb.parse_day("mag7 list at market 9", today) == (today, False)
    assert cb.parse_day("mar 9:30", today) == (today, False)
    assert cb.parse_day("may 10:15", today) == (today, False)
    assert cb.parse_day("mar 9 list", today) == (date(2026, 3, 9), True)
    assert cb.parse_day("march 9th", today) == (date(2026, 3, 9), True)
    assert cb.parse_day("sept 3 list", today) == (date(2026, 9, 3), True)
    assert cb.parse_day("june 3 list", today) == (date(2026, 6, 3), True)


# ---------------------------------------------------------------- the file is read outside this process
def test_a_real_child_builds_the_same_index_at_below_normal_priority_with_no_window(tmp_path, monkeypatch):
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, **lit_row())]), "NVDA": nvda_capped()})
    want = cs._parse(path, "Mag7", False, DAY)
    monkeypatch.setattr(cs, "_run_builder", cs._child_lines)

    def no_read(*a, **k):
        raise AssertionError("this process must not read the day file")
    monkeypatch.setattr(cs.Path, "read_text", no_read)
    monkeypatch.setattr(cs.Path, "read_bytes", no_read)
    got = cs._parse(path, "Mag7", False, DAY)
    monkeypatch.undo()
    assert not got.broken and not got.retry and list(got.tickers) == list(want.tickers) == ["AAPL", "NVDA"]
    assert got.tickers["AAPL"].points == want.tickers["AAPL"].points
    assert got.tickers["NVDA"].capped == want.tickers["NVDA"].capped == got.tickers["NVDA"].points[-1].ts
    first = json.loads(cs._child_lines(path, False)[0])
    if cs.os.name == "nt":
        assert first["priority"] == cs.BELOW_NORMAL_PRIORITY_CLASS
        assert cs._child_flags() == 0x08000000 | 0x00004000


def test_the_child_script_imports_nothing_from_the_app():
    import re as _re
    source = cs.INDEX_SCRIPT.read_text(encoding="utf-8")
    assert not _re.search(r"^\s*(from|import)\s+(momx|api_server|agents)\b", source, _re.M)


@pytest.mark.parametrize("cell", [{"degree": 0.51, "value": 0.5}, {"value": 0.5}, {"degree": "", "value": 0.4}, {"degree": None, "value": None}, {"value": ""}, {"degree": float("nan")}, None, 5, {}])
def test_the_child_ranks_hl_exactly_as_the_board_does(cell):
    from momx.option_track import _hl_sort_value
    assert index.hl_sort_value(cell) == _hl_sort_value(cell)


class FakeChild:
    running = 0
    most = 0
    returncode = 0
    killed = 0

    def __init__(self, args, **kw):
        self.args, self.kw = args, kw
        type(self).running += 1
        type(self).most = max(type(self).most, type(self).running)

    def communicate(self, timeout=None):
        import time as _t
        _t.sleep(0.05)
        type(self).running -= 1
        return b'{"done": true, "broken": false}\n', None

    def kill(self):
        type(self).killed += 1


def test_only_one_child_runs_at_a_time_and_it_is_started_with_the_right_flags(tmp_path, monkeypatch):
    import threading
    FakeChild.running = FakeChild.most = 0
    seen = []

    class Child(FakeChild):
        def __init__(self, args, **kw):
            seen.append((args, kw))
            super().__init__(args, **kw)
    Child.running = Child.most = 0
    monkeypatch.setattr(cs.subprocess, "Popen", Child)
    threads = [threading.Thread(target=cs._child_lines, args=(tmp_path / f"{i}.json", False)) for i in range(5)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert Child.most == 1 and len(seen) == 5
    args, kw = seen[0]
    assert args[0] == cs.sys.executable and args[1] == "-I" and args[2] == str(cs.INDEX_SCRIPT) and args[4] == "bull"
    assert kw["creationflags"] == cs._child_flags() and kw["stdout"] == cs.subprocess.PIPE


def child_that(behaviour):
    class Child:
        returncode = 1 if behaviour == "crash" else 0
        killed = []

        def __init__(self, args, **kw):
            pass

        def communicate(self, timeout=None):
            if behaviour == "slow" and timeout is not None:
                raise cs.subprocess.TimeoutExpired("x", timeout)
            return (b"" if behaviour != "cut" else b'["AAPL", 1.0, false, []]\n'), None

        def kill(self):
            type(self).killed.append(1)
            self.__class__.communicate = lambda s, timeout=None: (b"", None)
    return Child


@pytest.mark.parametrize("behaviour", ["slow", "crash", "cut"])
def test_a_child_that_fails_gives_a_plain_line_and_is_tried_again(tmp_path, monkeypatch, behaviour):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day="2026-11-09")], day="2026-11-09")}, day="2026-11-09")
    write_board(tmp_path, "Watchlist", {}, day="2026-11-09")
    monkeypatch.setattr(cs, "_run_builder", cs._child_lines)
    child = child_that(behaviour)
    monkeypatch.setattr(cs.subprocess, "Popen", child)
    out = ask(tmp_path, "mag7 list at 10:00 yesterday")
    assert out["lines"][0] == "Mag7 calls: the saved history for 2026-11-09 could not be read just now. Please try again in a minute."
    assert not cs._CACHE                                                      # a failed read is never remembered
    if behaviour == "slow":
        assert child.killed and cs._CHILD_REST["until"] > 0
    cs._CHILD_REST["until"] = 0.0
    monkeypatch.setattr(cs, "_run_builder", in_process)
    assert any("#1 AAPL" in line for line in ask(tmp_path, "mag7 list at 10:00 yesterday")["lines"])


def test_the_child_cannot_be_started_is_a_plain_line(tmp_path, monkeypatch):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})

    def refuse(*a, **k):
        raise OSError("no")
    monkeypatch.setattr(cs, "_run_builder", cs._child_lines)
    monkeypatch.setattr(cs.subprocess, "Popen", refuse)
    assert "could not be read just now" in ask(tmp_path, "mag7 list at 10:00")["lines"][0]


def test_an_old_copy_is_kept_when_the_new_read_fails(tmp_path, monkeypatch):
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    first = cs._board(tmp_path, "Mag7", False, DAY, DAY)
    next(iter(cs._CACHE.values()))[2] = -1e9
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.6)]), "NEW": entry([snap("09:59:00", 0.6)])})
    monkeypatch.setattr(cs, "_run_builder", lambda *a: (_ for _ in ()).throw(cs.BuildFailed("x")))
    assert cs._board(tmp_path, "Mag7", False, DAY, DAY) is first and path.exists()


def test_today_answers_as_of_the_copy_in_hand_and_says_so(tmp_path):
    now = datetime(2026, 11, 10, 10, 30, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("10:18:00", 0.5)], last_seen="10:20:00")})
    board = cs._board(tmp_path, "Mag7", False, DAY, DAY)
    board.saved_at = datetime(2026, 11, 10, 10, 20, tzinfo=cb.ET).timestamp()
    out = ask(tmp_path, "mag7 list at 10:28", now=now)
    assert any("the saved history I have ends at 10:20 AM" in line for line in out["lines"]) and [b.split()[2] for b in bullets(out)] == ["AAPL"]


# ---------------------------------------------------------------- regular users: the scanner only
PRIVATE_FILES = ("momx_option_track", "momx_paper_mirror", "oi_chain_cache", "scorecard", "learning")


def ask_regular(base, question, now=NOW, context=None):
    return cb.ask_scanner(question, base, now, context)


@pytest.fixture
def no_private(monkeypatch):
    """ask_scanner may not even call the functions that read the owner's files."""
    for name in ("records_for", "day_tickers", "known_tickers", "answer_status", "Records", "answer_what_if", "answer_show"):
        monkeypatch.setattr(cb, name, lambda *a, _n=name, **k: pytest.fail(f"ask_scanner called {_n}"))


def test_a_regular_user_gets_scanner_answers_and_no_owner_suggestions(tmp_path, no_private):
    write_board(tmp_path, "Mag7", {"AAPL": entry(minutes("09:30", 60, 0.8), last_seen="10:30:00")})
    write_board(tmp_path, "Watchlist", {})
    for question, intent in (("AAPL at 10:00 in the scanner", "scanner_ticker"), ("mag7 list at 10:00", "scanner_list"), ("how long was AAPL in the top", "scanner_top"), ("which tickers stayed on top", "scanner_top")):
        out = ask_regular(tmp_path, question)
        assert out["intent"] == intent and out["scope"] == "scanner", question
        assert out["suggest"] and not any(cb.scanner_refuses(s) for s in out["suggest"]), question


@pytest.mark.parametrize("question", ["why was NVDA missed", "what is the bot doing", "which bot is ahead", "paper 3 result", "scorecard", "SPY if I take how much it went up", "why AAPL failed",
                                      "how much profit today", "see my trades", "top 3 trades today", "NVDA trades in the scanner", "what did the scanner bot take", "did NVDA win"])
def test_a_regular_user_is_told_in_words_and_nothing_is_read(tmp_path, no_private, monkeypatch, question):
    monkeypatch.setattr(cs, "_board", lambda *a: pytest.fail("a refused question reads nothing"))
    out = ask_regular(tmp_path, question)
    assert out["lines"] == [cb.SCANNER_ONLY_LINE] and out["intent"] == "help" and out["ticker"] is None
    assert not re.search(r"\b(?:bots?|paper|trades?|profits?)\b", out["lines"][0], re.I) and out["suggest"] == cb.SCANNER_EXAMPLES[:3]


@pytest.mark.parametrize("question", ["hello", "", "   ", "see SPY", "tell me a joke"])
def test_anything_else_gets_the_same_plain_line(tmp_path, no_private, question):
    assert ask_regular(tmp_path, question)["lines"] == [cb.SCANNER_ONLY_LINE]


def test_scanner_examples_are_all_allowed_scanner_questions(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": entry(minutes("09:30", 30, 0.8), last_seen="10:00:00")})
    for example in cb.SCANNER_EXAMPLES:
        assert not cb.scanner_refuses(example), example
        assert ask_regular(tmp_path, example)["intent"].startswith("scanner"), example


def test_a_regular_user_only_gets_tickers_from_the_boards_or_typed_capitals(tmp_path, no_private):
    write_board(tmp_path, "Mag7", {"QQXZ": entry([snap("09:59:00", 0.5)])})
    assert ask_regular(tmp_path, "qqxz at 10:00 in the scanner mag7")["ticker"] == "QQXZ"
    assert ask_regular(tmp_path, "zzzzq at 10:00 in the scanner mag7")["ticker"] is None
    assert ask_regular(tmp_path, "ZZZZQ at 10:00 in the scanner mag7")["ticker"] == "ZZZZQ"


def test_a_regular_follow_up_continues_with_the_ticker_of_the_conversation(tmp_path, no_private):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:59:00", 0.5)])})
    assert ask_regular(tmp_path, "where was it in the scanner at 10:00", context={"ticker": "NVDA"})["ticker"] == "NVDA"


def test_while_another_regular_question_reads_a_new_file_is_a_plain_line(tmp_path, no_private):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    write_board(tmp_path, "Watchlist", {})
    with cs._REGULAR_SLOT:
        out = ask_regular(tmp_path, "mag7 list at 10:00")
    assert out["lines"] == [cb.SCANNER_BUSY_LINE] and out["intent"] == "help" and not cs._CACHE


def test_while_another_regular_question_reads_an_index_in_memory_still_answers(tmp_path, no_private):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day="2026-11-09")], day="2026-11-09")}, day="2026-11-09")
    ask_regular(tmp_path, "mag7 list at 10:00 yesterday")
    with cs._REGULAR_SLOT:
        out = ask_regular(tmp_path, "mag7 list at 10:00 yesterday")
    assert out["intent"] == "scanner_list" and any("#1 AAPL" in line for line in out["lines"])


def test_two_regular_questions_at_once_start_one_read(tmp_path, monkeypatch):
    import threading
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day="2026-11-09")], day="2026-11-09")}, day="2026-11-09")
    entered, leave, builds = threading.Event(), threading.Event(), []

    def slow(path, bear):
        builds.append(path)
        entered.set()
        leave.wait(5)
        return in_process(path, bear)
    monkeypatch.setattr(cs, "_run_builder", slow)
    first = []
    thread = threading.Thread(target=lambda: first.append(ask_regular(tmp_path, "mag7 list at 10:00 yesterday")))
    thread.start()
    assert entered.wait(5)
    second = ask_regular(tmp_path, "mag7 list at 10:00 yesterday")
    leave.set()
    thread.join(5)
    assert second["lines"] == [cb.SCANNER_BUSY_LINE] and len(builds) == 1 and first[0]["intent"] == "scanner_list"


def test_the_owner_is_not_turned_away_while_a_regular_question_reads(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    with cs._REGULAR_SLOT:
        assert ask(tmp_path, "mag7 list at 10:00")["intent"] == "scanner_list"


def test_a_regular_question_gives_up_before_the_tunnel_does():
    assert cs.REGULAR_QUESTION_SECONDS < 100
    with cs.turn(regular=True):
        assert cs._TURN.deadline - cs.time.monotonic() <= cs.REGULAR_QUESTION_SECONDS
    with cs.turn():
        assert cs._TURN.deadline - cs.time.monotonic() > cs.REGULAR_QUESTION_SECONDS
    assert cs._TURN.deadline is None and not cs._REGULAR_SLOT.locked()


# ---------------------------------------------------------------- what history cannot say
def calls_rows(start, count, hl=0.4):
    return minutes(start, count, hl)


def test_a_capped_ticker_with_fresh_rows_on_the_other_side_was_not_on_this_side(tmp_path):
    """NVDA's real shape on 11-09: capped on puts at 9:46, last seen 15:11, with call rows inside that window."""
    write_board(tmp_path, "Mag7", {"NVDA": capped([snap("09:30:00", -0.9), snap("09:46:00", -0.8)], "15:11:00"), "TSLA": entry(minutes("10:50", 20, -0.5), last_seen="11:10:00")}, bear=True)
    write_board(tmp_path, "Mag7", {"NVDA": entry(calls_rows("09:51", 240), last_seen="13:54:00")})
    write_board(tmp_path, "Watchlist", {}, bear=True)
    write_board(tmp_path, "Watchlist", {})
    cold = ask(tmp_path, "NVDA at 11:00 in the scanner mag7 puts")            # the calls file is read in the background, not waited for
    assert cold["facts"]["onBoard"] is None and "still on" not in text(cold)
    wait_for_builder()
    puts = ask(tmp_path, "NVDA at 11:00 in the scanner mag7 puts")
    assert puts["facts"]["onBoard"] is False and "still on" not in text(puts)
    assert puts["lines"][0].startswith("NVDA was not on Mag7 puts at 11:00 AM on 2026-11-10: a saved row at ") and "has it on Mag7 calls." in puts["lines"][0]
    assert ask(tmp_path, "NVDA at 11:00 in the scanner mag7")["side"] == "bull"
    listed = ask(tmp_path, "mag7 puts list at 11:00")
    assert "NVDA" not in text(listed) and listed["facts"]["boards"][0]["capped"] == []
    assert not any("NVDA" in line for line in ask(tmp_path, "TSLA puts at 11:00 in the scanner mag7")["lines"])


def test_a_capped_ticker_never_reads_as_still_on_the_board_anywhere(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped(), "TSLA": entry([snap("12:55:00", -0.5)])}, bear=True)
    for question in ("NVDA at 13:00 in the scanner mag7 puts", "mag7 puts list at 13:00", "TSLA puts at 13:00 in the scanner mag7", "from the scanner NVDA puts at 13:00 mag7 give me the list of tickers"):
        assert "still on" not in text(ask(tmp_path, question)), question


def test_a_quiet_ticker_with_no_row_nearby_is_not_called_absent(tmp_path):
    """MDLZ's real shape: rows are saved only when a column changes, and it was seen again later."""
    write_board(tmp_path, "Watchlist", {"MDLZ": entry([snap("09:30:00", 0.9), snap("15:00:00", 0.9)], last_seen="15:30:00"), "ZETA": entry(minutes("13:50", 20, 0.5), last_seen="14:10:00")})
    write_board(tmp_path, "Mag7", {})
    out = ask(tmp_path, "MDLZ at 14:00 in the scanner watchlist calls")
    assert out["lines"][0] == ("MDLZ on Watchlist calls at 2:00 PM on 2026-11-10: no saved row near that time, so it is not known whether it was on the board. "
                               "The nearest time it was on: 3:00 PM.")
    assert out["facts"]["onBoard"] is None and "was not on" not in text(out)
    listed = ask(tmp_path, "watchlist list at 14:00")
    assert [b.split()[2] for b in bullets(listed)] == ["ZETA"] and listed["facts"]["boards"][0]["unknown"] == ["MDLZ"]
    assert ("Not counted on Watchlist calls at 2:00 PM: no saved row near that time, so it is not known whether these were on the board then: MDLZ. Ranks could be off.") in listed["lines"]
    assert any(line.startswith("- Rank could be off: no saved row near that time") and "MDLZ" in line for line in ask(tmp_path, "ZETA at 14:00 in the scanner watchlist")["lines"])
    assert "Watchlist: no saved row near that time, so not known (nearest time it was: 3:00 PM)" in text(ask(tmp_path, "from the scanner MDLZ at 14:00 watchlist give me the list of tickers"))


def test_after_a_quiet_ticker_was_last_seen_and_before_it_came_it_really_was_not_on(tmp_path):
    write_board(tmp_path, "Watchlist", {"MDLZ": entry([snap("10:00:00", 0.9), snap("12:00:00", 0.9)], last_seen="12:30:00")})
    assert "MDLZ was not on Watchlist calls at 2:00 PM" in text(ask(tmp_path, "MDLZ at 14:00 in the scanner watchlist calls"))
    assert "MDLZ was not on Watchlist calls at 9:30 AM" in text(ask(tmp_path, "MDLZ at 9:30 in the scanner watchlist calls"))


def test_top_counts_a_quiet_ticker_as_a_minimum_and_never_says_never(tmp_path):
    rows = {f"T{chr(65 + i)}XX": entry(minutes("09:30", 390, 0.95), last_seen="16:00:00") for i in range(3)}
    write_board(tmp_path, "Mag7", {**rows, "MDLZ": entry([snap("09:30:00", 0.1), snap("15:00:00", 0.1)], last_seen="15:30:00")})
    write_board(tmp_path, "Watchlist", {})
    one = text(ask(tmp_path, "how long was MDLZ in the top 3 mag7 calls"))
    assert "not in the top 3 while it had saved rows" in one and "never" not in one
    day = "2026-11-09"
    write_board(tmp_path, "Mag7", {"MDLZ": entry([snap("09:30:00", 0.9, day=day), snap("15:00:00", 0.9, day=day)], last_seen="15:30:00", day=day)}, day=day)
    listed = ask(tmp_path, f"which tickers stayed on top mag7 calls on {day}")
    assert next(b for b in bullets(listed) if b.startswith("- MDLZ")).startswith("- MDLZ: at least ")
    assert "part of the session has no saved row for MDLZ" in text(listed) and "could be too high" in text(listed)


# ---------------------------------------------------------------- questions about what the bot did
@pytest.mark.parametrize("question", ["which tickers did level 2 take today", "which tickers did level 3 buy at 10:30", "list the stocks we bought at 10:30", "what names did the rules take at 10",
                                      "which names got entered at 11", "list of tickers level 2 took", "top 3 tickers level 1 picked", "best 3 stocks you picked today", "which stocks were we holding at 10:30"])
def test_what_the_bot_took_is_not_taken_over_by_the_scanner(tmp_path, monkeypatch, question):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    monkeypatch.setattr(cs, "_parse", lambda *a: pytest.fail("a question about the bot must not read a board"))
    assert cs.route(question, False, False, cs.read_minute(question, cb.parse_time(question))) is None
    assert not ask(tmp_path, question)["intent"].startswith("scanner")
    assert cb.scanner_refuses(question)


@pytest.mark.parametrize("question,want", [("what did the scanner take at 10:30", "scanner_list"), ("mag7 names we took at 10", "scanner_list"), ("which tickers kept holding the top", "scanner_top")])
def test_trade_words_on_a_named_board_or_the_top_stay_scanner_questions(question, want):
    assert cs.route(question, False, False, cs.read_minute(question, cb.parse_time(question))) == want


# ---------------------------------------------------------------- a lower-case ticker with a column name
@pytest.mark.parametrize("question", ["what was the rvol of nvda at 10 on the scanner", "sqz for tsla at 10:30 on mag7", "skittles on aapl at 11 on the scanner"])
def test_a_regular_user_can_type_a_lower_case_ticker_with_a_column(tmp_path, no_private, question):
    write_board(tmp_path, "Mag7", {s: entry(minutes("09:30", 120, 0.5), last_seen="11:30:00") for s in ("NVDA", "TSLA", "AAPL")})
    out = ask_regular(tmp_path, question)
    assert out["intent"] == "scanner_ticker" and out["ticker"] in ("NVDA", "TSLA", "AAPL") and out["lines"] != [cb.SCANNER_ONLY_LINE]


def test_a_column_with_no_ticker_at_all_is_still_refused_after_the_read(tmp_path, no_private):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:59:00", 0.5)])})
    assert ask_regular(tmp_path, "what was the rvol on the scanner")["lines"] == [cb.SCANNER_ONLY_LINE]


# ---------------------------------------------------------------- how long, with no "top"
def test_how_long_on_the_scanner_is_the_time_on_the_board_on_both_sides(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry(minutes("09:30", 10, 0.9), last_seen="09:40:00")})
    write_board(tmp_path, "Mag7", {"NVDA": entry(minutes("09:30", 60, -0.9), last_seen="10:30:00")}, bear=True)
    for question in ("how long was NVDA on the scanner", "how long was nvda on the mag7"):
        out = ask(tmp_path, question)
        assert (out["intent"], out["ticker"], out["side"]) == ("scanner_top", "NVDA", "bear"), question
        assert "NVDA on Mag7 puts on 2026-11-10: first saved row 9:30 AM, last seen 10:30 AM. Saved rows cover about 61 min." in out["lines"], question
        assert "was not on" not in text(out)


def test_how_long_says_a_minimum_when_rows_stop_or_have_gaps(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": nvda_capped(), "MDLZ": entry([snap("09:30:00", -0.5), snap("15:00:00", -0.5)], last_seen="15:05:00")}, bear=True)
    assert "Row saving stopped at 9:49 AM, so that is a minimum." in text(ask(tmp_path, "how long was NVDA on the scanner mag7 puts"))
    said = text(ask(tmp_path, "how long was MDLZ on the scanner mag7 puts"))
    assert "Between saved rows it is not known whether it stayed on the board, so that is a minimum." in said


def test_how_long_when_it_was_on_no_board(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    assert "ZZZZ was not on Mag7 calls at all on 2026-11-10." in text(ask(tmp_path, "how long was ZZZZ on the scanner mag7"))


def test_how_long_before_the_open_means_the_last_session(tmp_path):
    early = datetime(2026, 11, 10, 8, 0, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"OLD": entry([snap("09:30:00", 0.9, day="2026-11-09")], last_seen="09:40:00", day="2026-11-09")}, day="2026-11-09")
    assert ask(tmp_path, "how long was OLD on the scanner mag7", now=early)["day"] == "2026-11-09"


def test_a_ticker_answer_with_no_side_says_how_to_see_the_puts(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("10:00:00", 0.5)], last_seen="10:30:00")})
    out = ask(tmp_path, "NVDA at 14:00 in the scanner mag7")
    assert 'Add "puts" to check the bear scanner.' in out["lines"] and out["lines"][-1] == cs.SOURCE
    assert 'Add "puts"' not in text(ask(tmp_path, "NVDA calls at 14:00 in the scanner mag7"))


# ---------------------------------------------------------------- a clock time is not a count
@pytest.mark.parametrize("question", ["scanner 10:30 setups", "watchlist 2:15 setups", "scanner setups 10:30"])
def test_the_minutes_of_a_clock_time_are_not_a_setup_count(question):
    assert cs.read(question, cb.parse_time(question, scanner=True), None).setups is None


@pytest.mark.parametrize("question", ["scanner at 10:05 tickers", "scanner top 10:05", "scanner best 10:30"])
def test_the_minutes_of_a_clock_time_are_not_a_top_n(question):
    assert cs.read(question, cb.parse_time(question, scanner=True), None).top is None


def test_a_real_setup_count_and_top_n_still_read():
    assert cs.read("scanner at 10:30 3 setups", 630, None).setups == 3 and cs.read("scanner 3+ setups", None, None).setups == 3
    assert cs.read("scanner at 10:30 5 tickers", 630, None).top == 5 and cs.read("top 7 at 10:30", 630, None).top == 7


# ---------------------------------------------------------------- a read that failed is not a broken file
@pytest.mark.skipif(cs.os.name != "nt", reason="share mode 0 is a Windows lock")
def test_a_file_locked_for_a_moment_is_read_again_once_it_is_free(tmp_path):
    import ctypes
    from ctypes import wintypes
    day = "2026-11-09"
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day=day)], day=day)}, day=day)
    write_board(tmp_path, "Watchlist", {}, day=day)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.CreateFileW(str(path), 0x80000000, 0, None, 3, 0, None)                 # GENERIC_READ, no sharing, OPEN_EXISTING
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        locked = ask(tmp_path, "mag7 list at 10:00 yesterday")
    finally:
        kernel32.CloseHandle(handle)
    assert locked["lines"][0] == "Mag7 calls: the saved history for 2026-11-09 could not be read just now. Please try again in a minute."
    assert not any(k.endswith(f"Mag7\\{day}.json") or k.endswith(f"Mag7/{day}.json") for k in cs._CACHE)
    assert any("#1 AAPL" in line for line in ask(tmp_path, "mag7 list at 10:00 yesterday")["lines"])


def test_a_read_failure_keeps_todays_good_copy_and_a_corrupt_file_is_remembered(tmp_path, monkeypatch):
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    first = cs._board(tmp_path, "Mag7", False, DAY, DAY)
    next(iter(cs._CACHE.values()))[2] = -1e9
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.6)]), "NEW": entry([snap("09:59:00", 0.6)])})
    monkeypatch.setattr(cs, "_run_builder", lambda *a: ['{"done": true, "broken": true, "retry": true}'])
    assert cs._board(tmp_path, "Mag7", False, DAY, DAY) is first
    monkeypatch.setattr(cs, "_run_builder", lambda *a: ['{"done": true, "broken": true, "retry": false}'])
    assert cs._board(tmp_path, "Mag7", False, DAY, DAY).broken and path.exists()


@pytest.mark.parametrize("exc", [PermissionError, OSError, MemoryError])
def test_the_child_reports_a_failed_read_apart_from_a_corrupt_file(tmp_path, monkeypatch, exc):
    path = write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})

    def boom(*a, **k):
        raise exc("held")
    monkeypatch.setattr(index.Path, "read_text", boom)
    assert json.loads(list(index.build(path, False))[-1]) == {"done": True, "broken": True, "retry": True}


def test_the_child_says_a_corrupt_file_is_not_worth_retrying(tmp_path):
    path = write_board(tmp_path, "Mag7", {})
    path.write_text('{"rows": {"AAA"', encoding="utf-8")
    assert json.loads(list(index.build(path, False))[-1]) == {"done": True, "broken": True, "retry": False}


# ---------------------------------------------------------------- reads carry on after the question gives up
def wait_for_builder():
    import time as _t
    for _ in range(500):
        if not cs._JOBS:
            return
        _t.sleep(0.01)
    raise AssertionError("the builder never finished")


def test_a_read_longer_than_the_question_still_arrives(tmp_path, no_private, monkeypatch):
    import threading
    day = "2026-11-09"
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day=day)], day=day)}, day=day)
    go, builds = threading.Event(), []

    def slow(path, bear):
        builds.append(path)
        go.wait(10)
        return in_process(path, bear)
    monkeypatch.setattr(cs, "_run_builder", slow)
    monkeypatch.setattr(cs, "REGULAR_QUESTION_SECONDS", 0.2)
    first = ask_regular(tmp_path, "mag7 list at 10:00 yesterday")
    assert first["lines"][0] == "Mag7 calls: the saved history for 2026-11-09 is still loading. Please ask again in about a minute."
    joined = ask_regular(tmp_path, "mag7 list at 10:00 yesterday")                            # joins it, does not start another
    assert "still loading" in joined["lines"][0] and len(builds) == 1
    go.set()
    wait_for_builder()
    monkeypatch.setattr(cs, "_run_builder", lambda *a: pytest.fail("the index is already in memory"))
    assert any("#1 AAPL" in line for line in ask_regular(tmp_path, "mag7 list at 10:00 yesterday")["lines"])


def test_the_child_gets_its_own_time_whoever_asked(tmp_path, monkeypatch):
    seen = []

    class Child:
        returncode = 0

        def __init__(self, args, **kw):
            pass

        def communicate(self, timeout=None):
            seen.append(timeout)
            return b'{"done": true, "broken": false}\n', None
    monkeypatch.setattr(cs.subprocess, "Popen", Child)
    with cs.turn(regular=True):
        cs._TURN.deadline = cs.time.monotonic() + 5
        cs._child_lines(tmp_path / "x.json", False)
    assert seen == [cs.CHILD_TIMEOUT_SECONDS]


def test_a_regular_timeout_rests_the_child_too(tmp_path, no_private, monkeypatch):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    monkeypatch.setattr(cs, "_run_builder", cs._child_lines)
    monkeypatch.setattr(cs.subprocess, "Popen", child_that("slow"))
    ask_regular(tmp_path, "mag7 list at 10:00")
    assert cs._CHILD_REST["until"] > cs.time.monotonic()


def test_the_owner_goes_ahead_of_a_waiting_regular_read(tmp_path, monkeypatch):
    import threading
    import time as _t
    for name in ("Mag7", "Watchlist", "Movers"):
        write_board(tmp_path, name, {"AAPL": entry([snap("09:59:00", 0.5)])})
    entered, go, order = threading.Event(), threading.Event(), []

    def slow(path, bear):
        order.append(path.parent.name)
        entered.set()
        go.wait(10)
        return in_process(path, bear)
    monkeypatch.setattr(cs, "_run_builder", slow)

    def regular():
        with cs.turn(regular=True):
            cs._board(tmp_path, "Watchlist", False, DAY, "2026-11-20")
    threads = [threading.Thread(target=lambda: cs._board(tmp_path, "Mag7", False, DAY, "2026-11-20"))]
    threads[0].start()
    assert entered.wait(5)
    threads.append(threading.Thread(target=regular))
    threads[1].start()
    while not cs._LINE:
        _t.sleep(0.01)
    threads.append(threading.Thread(target=lambda: cs._board(tmp_path, "Movers", False, DAY, "2026-11-20")))
    threads[2].start()
    while len(cs._LINE) < 2:
        _t.sleep(0.01)
    go.set()
    [t.join(10) for t in threads]
    assert order == ["Mag7", "Movers", "Watchlist"]


# ---------------------------------------------------------------- what a regular user's reads can cost
@pytest.fixture
def budget(monkeypatch):
    from momx import chat_limits
    limits = chat_limits.ChatLimits(clock=lambda: 0.0, today=lambda: DAY)
    monkeypatch.setattr(chat_limits, "LIMITS", limits)
    monkeypatch.setattr(cs, "HEAVY_READ_BYTES", 0)
    return limits


def past_days(base, count):
    days = [f"2026-11-{i:02d}" for i in range(1, count + 1)]
    for day in days:
        write_board(base, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5, day=day)], day=day)}, day=day)
    return days


def test_a_regular_account_has_a_budget_of_heavy_reads_and_memory_is_free(tmp_path, no_private, budget):
    days = past_days(tmp_path, 4)
    ask_mag7 = lambda day, who: cb.ask_scanner(f"mag7 list at 10:00 on {day}", tmp_path, NOW, None, account=who)
    assert all(any("#1 AAPL" in line for line in ask_mag7(day, "a@x")["lines"]) for day in days[:3])
    over = ask_mag7(days[3], "a@x")
    assert over["lines"][0] == "Mag7 calls: you have read a lot of saved history lately, so this board was not read. Please try again in a few minutes."
    assert any("#1 AAPL" in line for line in ask_mag7(days[0], "a@x")["lines"])               # already in memory: free
    assert any("#1 AAPL" in line for line in ask_mag7(days[3], "b@x")["lines"])               # per account


def test_a_small_file_costs_nothing(tmp_path, no_private, budget, monkeypatch):
    monkeypatch.setattr(cs, "HEAVY_READ_BYTES", 16 * 1024 * 1024)
    for day in past_days(tmp_path, 5):
        assert any("#1 AAPL" in line for line in cb.ask_scanner(f"mag7 list at 10:00 on {day}", tmp_path, NOW, None, account="a@x")["lines"])


def test_the_owner_never_pays_for_a_read(tmp_path, budget):
    for day in past_days(tmp_path, 5):
        assert any("#1 AAPL" in line for line in ask(tmp_path, f"mag7 list at 10:00 on {day}")["lines"])


def test_reads_are_limited_per_ten_minutes_and_per_day():
    from momx import chat_limits
    clock, day = [0.0], [DAY]
    lim = chat_limits.ChatLimits(clock=lambda: clock[0], today=lambda: day[0])
    assert all(lim.take_read("a@x") for _ in range(chat_limits.READS_PER_WINDOW)) and not lim.take_read("a@x") and lim.take_read("b@x")
    clock[0] = chat_limits.WINDOW_SECONDS
    assert lim.take_read("a@x")
    for _ in range(chat_limits.READS_PER_DAY):
        clock[0] += chat_limits.WINDOW_SECONDS
        lim.take_read("a@x")
    clock[0] += chat_limits.WINDOW_SECONDS
    assert not lim.take_read("a@x")
    day[0] = "2026-11-11"
    assert lim.take_read("a@x")


def test_a_regular_question_reads_only_two_boards_and_says_which(tmp_path, no_private, monkeypatch):
    for name in cs.BOARD_NAMES:
        write_board(tmp_path, name, {"AAPL": entry(minutes("09:30", 30, 0.5), last_seen="10:00:00")})
    parsed = []
    real = cs._parse
    monkeypatch.setattr(cs, "_parse", lambda *a: parsed.append(a[1]) or real(*a))
    out = ask_regular(tmp_path, "how long was AAPL in the top mag7 watchlist liquid movers movers 21 ema 50 ema calls")
    assert sorted(set(parsed)) == ["Mag7", "Watchlist"] and len(parsed) == 2
    assert "I read Mag7 and Watchlist only. Ask about Liquid Movers, Movers, 21 EMA Touch Weekly, 50 EMA Touch Monthly in a separate question." in out["lines"]
    assert out["lines"][-1] == cs.SOURCE


def test_regular_reads_cannot_push_out_the_owners(tmp_path):
    days = past_days(tmp_path, cs.REGULAR_CACHE_FILES + 3)
    assert any("#1 AAPL" in line for line in ask(tmp_path, f"mag7 list at 10:00 on {days[0]}")["lines"])
    owners = list(cs._CACHE)
    for day in days[1:]:
        ask_regular(tmp_path, f"mag7 list at 10:00 on {day}")
    assert list(cs._CACHE) == owners and len(cs._REGULAR_CACHE) == cs.REGULAR_CACHE_FILES


def test_a_regular_question_uses_the_owners_copy_and_the_owner_keeps_a_regular_copy(tmp_path, monkeypatch):
    days = past_days(tmp_path, 2)
    ask(tmp_path, f"mag7 list at 10:00 on {days[0]}")
    ask_regular(tmp_path, f"mag7 list at 10:00 on {days[1]}")
    monkeypatch.setattr(cs, "_parse", lambda *a: pytest.fail("already in memory"))
    assert any("#1 AAPL" in line for line in ask_regular(tmp_path, f"mag7 list at 10:00 on {days[0]}")["lines"])
    assert any("#1 AAPL" in line for line in ask(tmp_path, f"mag7 list at 10:00 on {days[1]}")["lines"])
    assert len(cs._CACHE) == 2 and not cs._REGULAR_CACHE


def test_too_many_regular_reads_waiting_is_a_plain_line(tmp_path, no_private, monkeypatch):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    fake = [type("J", (), {"regular": True, "taken": False})() for _ in range(cs.REGULAR_QUEUE)]
    monkeypatch.setattr(cs, "_JOBS", {f"k{i}": j for i, j in enumerate(fake)})
    assert ask_regular(tmp_path, "mag7 list at 10:00")["lines"] == [cb.SCANNER_BUSY_LINE]


# ---------------------------------------------------------------- an hour after "at" is not a count
@pytest.mark.parametrize("question,minute", [("scanner at 10 setups", 600), ("mag7 at 2 setups", 840), ("scanner at 3 tickers", 900), ("watchlist at 11 names", 660), ("scanner at 9 30 tickers", 570)])
def test_an_hour_after_at_is_not_a_count(question, minute):
    read_at = cs.read_minute(question, cb.parse_time(question, scanner=True))
    asked = cs.read(question, read_at, None)
    assert read_at == minute and asked.setups is None and asked.top is None, question


def test_the_board_reads_the_hour_and_does_not_filter_on_it(tmp_path):
    write_board(tmp_path, "Mag7", {"AAA": entry([snap("09:59:00", 0.9, setups=1)])})
    write_board(tmp_path, "Watchlist", {})
    out = ask(tmp_path, "mag7 at 10 setups")
    assert out["intent"] == "scanner_list" and [b.split()[2] for b in bullets(out)] == ["AAA"] and "10:00 AM" in text(out)


def test_a_real_count_next_to_an_hour_still_reads():
    assert cs.read("scanner at 10 3 setups", 600, None).setups == 3 and cs.read("top 7 on watchlist at 10", 600, None).top == 7
    assert cs.read("scanner 5 tickers at 10", 600, None).top == 5


# ---------------------------------------------------------------- how a time is typed
@pytest.mark.parametrize("sentence,want", [("nvda at 930", 570), ("nvda 930 scanner", 570), ("nvda at 1030", 630), ("at 130", 810), ("nvda 9 30 scanner", 570), ("at 2 15", 855), ("nvda 10 oclock on scanner", 600),
                                           ("nvda 10 o'clock", 600), ("what was hot at noon", 720), ("at the open", 570), ("the close", 959), ("at market open", 570)])
def test_the_ways_a_time_is_typed(sentence, want):
    assert cs.read_minute(sentence, None) == want


@pytest.mark.parametrize("sentence", ["open nvda chart", "open the scanner", "close this", "what is the open interest", "top 900", "above 930", "21 ema list", "nov 6 30", "2026", "mag7", "850 tickers"])
def test_what_is_not_a_time(sentence):
    assert cs.read_minute(sentence, None) is None


# ---------------------------------------------------------------- one ticker's whole day
@pytest.mark.parametrize("question,ticker,minute,want", [("Nvda today scanner", True, None, "scanner_day"), ("scanner nvda", True, None, "scanner_day"), ("was nvda on the board this morning", True, None, "scanner_day"),
                                                         ("nvda scanner yesterday", True, None, "scanner_day"), ("nvda puts scanner today", True, None, "scanner_day"), ("nvda on mag7", True, None, "scanner_day"),
                                                         ("nvda at 9:30 scanner", True, 570, "scanner_ticker"), ("nvda 10 oclock on scanner", True, 600, "scanner_ticker"), ("rvol of nvda on the scanner", True, None, "scanner_ticker")])
def test_a_ticker_and_a_scanner_word_with_no_clock_is_the_whole_day(question, ticker, minute, want):
    assert cs.route(question, ticker, False, minute) == want


def day_entry(snaps, hits, last_seen="15:55:00", extra=0):
    out = entry(snaps, last_seen=last_seen)
    out["hits"] = hits
    out["snapshots"] = out["snapshots"] + [{"at": at("15:56:00"), "changed": [], "row": {"scanPass": False}}] * extra
    return out


CTX = {"scope": "owner", "account": "owner@x", "pageTicker": None, "pageName": None, "ticker": None, "day": None, "side": None, "now": NOW}


def regular_ctx(**over):
    return {**CTX, "scope": "scanner", "account": "a@x", **over}


def tool(name, args, ctx=None, base=None):
    return cs.TOOLS[name]["handler"](args, {**(ctx or CTX), "artifacts": base})


def test_scanner_day_says_scans_and_saved_rows_and_returns_only_a_reference(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("06:22:00", 0.5, last=100.0), snap("15:50:00", 0.93, last=112.4, pct=2.1)], 172, extra=92)})
    write_board(tmp_path, "Watchlist", {"AAPL": entry([snap("09:59:00", 0.5)])})
    out = tool("scanner_day", {"ticker": "NVDA"}, base=tmp_path)
    said = "\n".join(out["lines"])
    assert "NVDA on Mag7 calls: first 6:22 AM, last 3:55 PM, 172 scans, 94 saved rows. Last saved row 3:50 PM: H/L +0.93, +2.1%, $112.40." in out["lines"]
    assert "Not on Watchlist calls that day." in out["lines"] and "every scan" not in said.replace("not every scan", "")
    assert out["table"] == {"kind": "scanner_day", "title": "NVDA on the scanner, 2026-11-10", "ticker": "NVDA", "day": DAY,
                            "sections": [{"list": "Mag7", "side": "bull"}, {"list": "Watchlist", "side": "bull"}, {"list": "Mag7", "side": "bear"}, {"list": "Watchlist", "side": "bear"}]}
    assert out["links"] == [{"label": "Open NVDA on Mag7 History", "view": "MomX Scanner", "list": "Mag7", "side": "bull", "tab": "history", "day": DAY, "ticker": "NVDA"}]
    assert out["asOf"] == "as of 15:55 ET" and out["facts"]["boards"][0]["scans"] == 172 and out["facts"]["boards"][0]["savedRows"] == 94
    assert "rows" not in out["table"]


def test_nvda_today_scanner_answers_the_whole_day_on_both_boards(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", 0.5)], 10)})
    write_board(tmp_path, "Watchlist", {"NVDA": day_entry([snap("09:31:00", 0.6)], 20)})
    args = (tmp_path, DAY, DAY, NOW.timestamp(), cs.read("Nvda today scanner", None, None), "NVDA")
    out = cs.answer_day(*args)
    assert any(line.startswith("NVDA on Mag7 calls:") for line in out["lines"]) and any(line.startswith("NVDA on Watchlist calls:") for line in out["lines"])
    assert [link["list"] for link in out["links"]] == ["Mag7", "Watchlist"]


def test_puts_named_come_first_and_read_the_bear_files(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", -0.5)], 7)}, bear=True)
    out = tool("scanner_day", {"ticker": "NVDA", "side": "bear"}, base=tmp_path)
    assert out["table"]["sections"][:2] == [{"list": "Mag7", "side": "bear"}, {"list": "Watchlist", "side": "bear"}]
    assert out["lines"][0].startswith("NVDA on Mag7 puts:") and out["links"][0]["side"] == "bear"


def test_scanner_day_does_not_wait_for_a_slow_read_and_starts_only_the_side_asked(tmp_path, monkeypatch):
    import threading
    write_board(tmp_path, "Watchlist", {"NVDA": day_entry([snap("09:30:00", 0.5)], 3)})
    write_board(tmp_path, "Watchlist", {"NVDA": day_entry([snap("09:30:00", -0.5)], 3)}, bear=True)
    go, builds = threading.Event(), []

    def slow(path, bear):
        builds.append(bear)
        go.wait(10)
        return in_process(path, bear)
    monkeypatch.setattr(cs, "_run_builder", slow)
    monkeypatch.setattr(cs, "DAY_WAIT_SECONDS", 0.05)
    started = cs.time.monotonic()
    out = tool("scanner_day", {"ticker": "NVDA", "list": "Watchlist"}, base=tmp_path)
    took = cs.time.monotonic() - started
    go.set()
    wait_for_builder()
    assert took < 2 and out["table"]["kind"] == "scanner_day" and builds == [False]
    assert "Watchlist calls: the summary is still being read. The table has the whole day." in out["lines"] and out["links"][0]["list"] == "Watchlist"
    again = tool("scanner_day", {"ticker": "NVDA", "list": "Watchlist"}, base=tmp_path)
    assert any(line.startswith("NVDA on Watchlist calls:") for line in again["lines"])


def test_scanner_day_with_no_file_yet_today_falls_back_to_the_last_day_with_one(tmp_path):
    early = datetime(2026, 11, 11, 1, 0, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", 0.5)], 3)})
    out = tool("scanner_day", {"ticker": "NVDA"}, ctx={**CTX, "now": early}, base=tmp_path)
    assert out["table"]["day"] == DAY and out["facts"]["day"] == DAY and out["links"][0]["day"] == DAY


def test_a_past_day_with_no_file_is_not_moved(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", 0.5)], 3)})
    out = tool("scanner_day", {"ticker": "NVDA", "day": "2026-11-06"}, base=tmp_path)
    assert out["table"]["day"] == "2026-11-06" and "Not on Mag7 calls that day: nothing was saved." in out["lines"]


def test_scanner_day_takes_the_ticker_from_the_conversation_then_the_page_and_asks_when_there_is_none(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", 0.5)], 3), "AAPL": day_entry([snap("09:30:00", 0.5)], 3)})
    assert tool("scanner_day", {}, ctx={**CTX, "ticker": "NVDA", "pageTicker": "AAPL"}, base=tmp_path)["table"]["ticker"] == "NVDA"
    assert tool("scanner_day", {}, ctx={**CTX, "pageTicker": "AAPL"}, base=tmp_path)["table"]["ticker"] == "AAPL"
    none = tool("scanner_day", {}, base=tmp_path)
    assert none["lines"][0].startswith("Which ticker?") and none["table"] is None and none["links"][0]["view"] == "MomX Scanner"


# ---------------------------------------------------------------- tools: time links, tables, scope
def test_scanner_ticker_links_to_history_at_that_time(tmp_path):
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:29:00", 0.5)])})
    write_board(tmp_path, "Watchlist", {})
    out = tool("scanner_ticker", {"ticker": "NVDA", "time": "09:30"}, base=tmp_path)
    assert out["links"] == [{"label": "Open NVDA on Mag7 History, 09:30", "view": "MomX Scanner", "list": "Mag7", "side": "bull", "tab": "history", "day": DAY, "ticker": "NVDA", "time": "09:30"}]
    assert out["asOf"] == "as of 09:30 ET" and out["table"] is None and out["facts"]["onBoard"] is True


def test_scanner_list_returns_a_table_and_a_time_link(tmp_path):
    write_board(tmp_path, "Mag7", {f"T{chr(65 + i)}XX": entry([snap("10:14:00", 0.9 - i / 100, last=10.0 + i, level=2, setups=i)]) for i in range(6)})
    out = tool("scanner_list", {"list": "Mag7", "time": "10:15", "top": 5}, base=tmp_path)
    table = out["table"]
    assert table["kind"] == "rows" and len(table["rows"]) == 5 and [c["key"] for c in table["columns"]][:3] == ["board", "rank", "symbol"]
    assert table["rows"][0] == {"board": "Mag7", "rank": "1", "symbol": "TAXX", "hl": "+0.90", "pct": "+1.0%", "price": "$10.00", "level": "2", "setups": "0"}
    assert out["links"][0]["time"] == "10:15" and table["link"] == out["links"][0] and "ticker" not in out["links"][0]


def test_a_table_never_passes_forty_rows():
    rows = [{"symbol": str(i)} for i in range(60)]
    table = cs._rows_table("t", [("symbol", "Symbol", False)], rows, None)
    assert len(table["rows"]) == 40 and "40 of 60" in table["note"]


def test_scanner_top_has_a_table_and_links(tmp_path):
    write_board(tmp_path, "Mag7", {"AAA": entry(minutes("09:30", 20, 0.9), last_seen="09:50:00")})
    write_board(tmp_path, "Watchlist", {"ZZZ": entry(minutes("09:30", 20, 0.9), last_seen="09:50:00")})
    out = tool("scanner_top", {}, base=tmp_path)
    assert {r["symbol"] for r in out["table"]["rows"]} == {"AAA", "ZZZ"} and [link["list"] for link in out["links"]] == ["Mag7", "Watchlist"]


@pytest.mark.parametrize("name", ["scanner_day", "scanner_ticker", "scanner_list", "scanner_top"])
def test_every_tool_has_the_fixed_shape_and_never_raises(tmp_path, name):
    spec = cs.TOOLS[name]
    assert spec["scope"] == "all" and spec["description"] and spec["args"]["type"] == "object" and callable(spec["handler"])
    for args in ({}, {"ticker": "NVDA"}, {"ticker": "!!"}, {"list": "Nonsense"}, {"list": 5}, {"day": "soon", "time": "whenever", "top": "x", "level": 9, "side": "up"}, None):
        out = spec["handler"](args, {**CTX, "artifacts": tmp_path})
        assert set(out) >= {"lines", "facts", "links", "table", "asOf"} and out["lines"], args


@pytest.mark.parametrize("name", ["scanner_day", "scanner_ticker", "scanner_list", "scanner_top"])
def test_a_session_with_no_scope_gets_nothing(tmp_path, monkeypatch, name):
    monkeypatch.setattr(cs, "_board", lambda *a: pytest.fail("no scope reads nothing"))
    out = cs.TOOLS[name]["handler"]({"ticker": "NVDA"}, {"artifacts": tmp_path})
    assert out["lines"] == ["That is not available in this chat."] and not out["links"]


def test_a_regular_session_reads_two_boards_and_gets_no_owner_suggestions(tmp_path):
    for name in cs.BOARD_NAMES:
        write_board(tmp_path, name, {"AAPL": entry(minutes("09:30", 30, 0.5), last_seen="10:00:00")})
    out = tool("scanner_ticker", {"ticker": "AAPL", "time": "10:00"}, ctx=regular_ctx(), base=tmp_path)
    assert out["lines"] and not any("missed" in s for s in out["suggest"])
    assert not re.search(r"\b(?:bots?|paper|trades?|profits?|scorecard)\b", " ".join(out["lines"] + out["suggest"]), re.I)


def test_a_regular_session_that_finds_the_slot_taken_is_told_in_a_plain_line(tmp_path):
    write_board(tmp_path, "Mag7", {"AAPL": entry([snap("09:59:00", 0.5)])})
    with cs._REGULAR_SLOT:
        out = tool("scanner_list", {"list": "Mag7", "time": "10:00"}, ctx=regular_ctx(), base=tmp_path)
    assert out["lines"] == [cs.BUSY_LINE] and out["links"]


@pytest.mark.parametrize("bad", [("Nope", "bull", DAY, None, None), ("Mag7", "up", DAY, None, None), ("Mag7", "bull", "soon", None, None), ("Mag7", "bull", DAY, "<x>", None), ("Mag7", "bull", DAY, None, 99999)])
def test_a_link_is_built_only_from_valid_values(bad):
    assert cs.history_link(*bad) is None


# ---------------------------------------------------------------- the other side never holds up an answer
def test_a_slow_read_of_the_other_side_does_not_hold_up_a_finished_answer(tmp_path, monkeypatch):
    import threading
    write_board(tmp_path, "Mag7", {"NVDA": capped([snap("09:30:00", -0.9), snap("09:46:00", -0.8)], "15:11:00")}, bear=True)
    write_board(tmp_path, "Mag7", {"NVDA": entry(calls_rows("09:51", 240), last_seen="13:54:00")})
    bear_board = cs._board(tmp_path, "Mag7", True, DAY, DAY)
    assert bear_board.tickers
    go, builds = threading.Event(), []

    def slow(path, bear):
        builds.append(bear)
        go.wait(20)
        return in_process(path, bear)
    monkeypatch.setattr(cs, "_run_builder", slow)
    started = cs.time.monotonic()
    t = cs._at(DAY, 11 * 60)
    other = cs._other_side(tmp_path, bear_board, DAY, t, bear_board.tickers["NVDA"])
    took = cs.time.monotonic() - started
    assert other is None and took < 2
    for _ in range(300):
        if builds:
            break
        cs.time.sleep(0.01)
    assert builds == [False]                                                  # the read did start, in the background
    go.set()
    wait_for_builder()
    done = cs._other_side(tmp_path, bear_board, DAY, t, bear_board.tickers["NVDA"])
    assert done is not None and "NVDA" in done.tickers


def test_a_regular_user_never_starts_the_other_sides_read(tmp_path, monkeypatch):
    write_board(tmp_path, "Mag7", {"NVDA": capped([snap("09:30:00", -0.9)], "15:11:00")}, bear=True)
    write_board(tmp_path, "Mag7", {"NVDA": entry(calls_rows("09:51", 240), last_seen="13:54:00")})
    board = cs._board(tmp_path, "Mag7", True, DAY, DAY)
    monkeypatch.setattr(cs, "_run_builder", lambda *a: pytest.fail("a regular user must not start this read"))
    with cs.turn(regular=True):
        assert cs._other_side(tmp_path, board, DAY, cs._at(DAY, 11 * 60), board.tickers["NVDA"]) is None
    wait_for_builder()


# ---------------------------------------------------------------- the top of the board now
def write_now(base, name, rows, bear=False, tape=None):
    folder = base / "boards"
    folder.mkdir(exist_ok=True)
    path = folder / f"{name}{'.bear' if bear else ''}.json"
    path.write_text(json.dumps({"tapeAsOf": tape or "2026-11-10T19:30:00+00:00", "generatedAt": "2026-11-10T19:31:00+00:00", "rows": rows}), encoding="utf-8")
    return path


def now_row(symbol, hl, passed=True, last=10.0, pct=1.5):
    return {"symbol": symbol, "scanPass": passed, "highLow": {"value": hl, "degree": hl}, "last": last, "pctChange": pct}


@pytest.fixture
def boards(tmp_path, monkeypatch):
    monkeypatch.setenv("MOMX_BOARD_CACHE_DIR", str(tmp_path / "boards"))
    return tmp_path


def test_top_now_ranks_matches_only_and_shows_its_time(boards):
    write_now(boards, "Mag7", [now_row("LOW", 0.1), now_row("HIGH", 0.9), now_row("NOPE", 1.0, passed=False), now_row("MID", 0.5), now_row("LAST", 0.05)])
    out = cs.top_now("Mag7", "bull", None, boards, NOW)
    assert [r["symbol"] for r in out["rows"]] == ["HIGH", "MID", "LOW"] and out["count"] == 4 and out["rows"][0]["rank"] == 1       # Mag7's top is 3
    assert out["asOf"] == "as of 14:30 ET" and out["source"] == "board" and out["links"][0]["tab"] == "live"
    assert [r["symbol"] for r in cs.top_now("Mag7", "bull", 1, boards, NOW)["rows"]] == ["HIGH"]


def test_top_now_reads_the_puts_board_lowest_first(boards):
    write_now(boards, "Watchlist", [now_row("AAA", -0.2), now_row("BBB", -0.9)], bear=True)
    out = cs.top_now("Watchlist", "bear", None, boards, NOW)
    assert [r["symbol"] for r in out["rows"]] == ["BBB", "AAA"] and out["side"] == "bear"


@pytest.mark.parametrize("junk", ["", "{", "[]", '{"rows": 5}'])
def test_top_now_with_a_bad_file_or_none_is_none(boards, junk):
    assert cs.top_now("Mag7", "bull", None, boards, NOW) is None
    (boards / "boards").mkdir()
    (boards / "boards" / "Mag7.json").write_text(junk, encoding="utf-8")
    assert cs.top_now("Mag7", "bull", None, boards, NOW) is None


def test_top_now_refuses_an_unknown_board_or_side(boards):
    assert cs.top_now("Nope", "bull", None, boards, NOW) is None and cs.top_now("Mag7", "up", None, boards, NOW) is None


def test_top_now_ignores_a_board_days_old(boards):
    write_now(boards, "Mag7", [now_row("OLD", 0.5)], tape="2026-11-01T19:30:00+00:00")
    assert cs.top_now("Mag7", "bull", None, boards, NOW) is None


def test_top_now_never_starts_a_read_and_uses_only_memory_after_hours(boards, monkeypatch):
    write_now(boards, "Mag7", [now_row("NOPE", 0.5, passed=False)])
    write_board(boards, "Mag7", {"HELD": entry([snap("15:50:00", 0.7, last=50.0)])})
    monkeypatch.setattr(cs, "_run_builder", lambda *a: pytest.fail("top_now must not start a read"))
    assert cs.top_now("Mag7", "bull", None, boards, NOW) is None and not cs._JOBS
    monkeypatch.setattr(cs, "_run_builder", in_process)
    cs._board(boards, "Mag7", False, DAY, DAY)
    monkeypatch.setattr(cs, "_run_builder", lambda *a: pytest.fail("top_now must not start a read"))
    out = cs.top_now("Mag7", "bull", None, boards, NOW)
    assert out["source"] == "history" and out["rows"][0]["symbol"] == "HELD" and out["links"][0]["tab"] == "history" and out["asOf"] == "as of 15:50 ET"


def test_top_now_reads_a_file_once_until_it_changes(boards, monkeypatch):
    path = write_now(boards, "Mag7", [now_row("AAA", 0.5)])
    cs.top_now("Mag7", "bull", None, boards, NOW)
    real = cs.json.loads
    monkeypatch.setattr(cs.json, "loads", lambda *a, **k: pytest.fail("the same file is not parsed twice"))
    assert cs.top_now("Mag7", "bull", None, boards, NOW)["rows"][0]["symbol"] == "AAA"
    monkeypatch.setattr(cs.json, "loads", real)
    path.write_text(json.dumps({"rows": [now_row("BBB", 0.5)], "tapeAsOf": "2026-11-10T19:40:00+00:00"}), encoding="utf-8")
    assert cs.top_now("Mag7", "bull", None, boards, NOW)["rows"][0]["symbol"] == "BBB"


def test_the_index_carries_scans_and_saved_rows_through_a_real_child(tmp_path, monkeypatch):
    path = write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", 0.5)], 172, extra=93)})
    monkeypatch.setattr(cs, "_run_builder", cs._child_lines)
    held = cs._parse(path, "Mag7", False, DAY).tickers["NVDA"]
    assert (held.hits, held.saved, len(held.points)) == (172, 94, 1)


def test_before_the_first_scan_today_is_not_a_session_even_with_an_overnight_file(tmp_path):
    early = datetime(2026, 11, 11, 1, 30, tzinfo=cb.ET)
    write_board(tmp_path, "Mag7", {"NVDA": day_entry([snap("09:30:00", 0.5)], 3)})
    write_board(tmp_path, "Mag7", {"OVER": entry([snap("00:30:00", 0.5, day="2026-11-11")], day="2026-11-11", last_seen="00:40:00")}, day="2026-11-11")
    assert tool("scanner_day", {"ticker": "NVDA"}, ctx={**CTX, "now": early}, base=tmp_path)["table"]["day"] == DAY
    after = datetime(2026, 11, 11, 6, 30, tzinfo=cb.ET)
    assert tool("scanner_day", {"ticker": "NVDA"}, ctx={**CTX, "now": after}, base=tmp_path)["table"]["day"] == "2026-11-11"


def test_a_weekend_overnight_file_is_not_the_last_session(tmp_path):
    monday = datetime(2026, 11, 9, 1, 0, tzinfo=cb.ET)
    friday = "2026-11-06"
    write_board(tmp_path, "Mag7", {"NVDA": entry([snap("09:30:00", 0.5, day=friday)], day=friday)}, day=friday)
    write_board(tmp_path, "Mag7", {}, day="2026-11-08")
    assert tool("scanner_day", {"ticker": "NVDA"}, ctx={**CTX, "now": monday}, base=tmp_path)["table"]["day"] == friday


# ---------------------------------------------------------------- integration: the real route over HTTP
def previous_weekday():
    day = datetime.now(cb.ET).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day.isoformat()


@pytest.fixture
def over_http(tmp_path, monkeypatch):
    """The real /api/bot-chat on scratch files. AI is faked; no Schwab, no build."""
    from momx import chat_ai, chat_limits
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setenv("MOMX_BOARD_CACHE_DIR", str(tmp_path / "boards"))
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "no AI in tests"})
    chat_limits.LIMITS.reset()
    chat_ai.clear_plans()
    day = previous_weekday()
    rows = {"NVDA": {**entry([snap("09:30:00", 0.5, day=day, last=120.0), snap("15:50:00", 0.93, day=day, last=112.4, pct=2.1)], day=day, last_seen="15:55:00"), "hits": 172}}
    write_board(tmp_path, "Mag7", rows, day=day)
    write_board(tmp_path, "Watchlist", {"AAPL": entry([snap("09:30:00", 0.5, day=day)], day=day)}, day=day)
    yield day
    chat_limits.LIMITS.reset()


def api_accounts():
    from api_harness import RELAYED, running_api, seed_user
    from cf_access_auth import ACCESS_EMAIL_HEADER
    seed_user("scanner-owner@example.com", role="admin")
    seed_user("scanner-viewer@example.com", role="user")
    return running_api, {ACCESS_EMAIL_HEADER: "scanner-owner@example.com"}, {**RELAYED, ACCESS_EMAIL_HEADER: "scanner-viewer@example.com"}


@pytest.mark.parametrize("who", ["owner", "regular"])
def test_nvda_today_scanner_through_the_route(over_http, who):
    running_api, owner, regular = api_accounts()
    with running_api() as api:
        status, body = api.post("/api/bot-chat", {"question": "Nvda today scanner"}, headers=owner if who == "owner" else regular)
    assert status == 200 and body["table"]["kind"] == "scanner_day" and body["table"]["ticker"] == "NVDA" and body["table"]["day"] == over_http
    assert [(s["list"], s["side"]) for s in body["table"]["sections"]] == [("Mag7", "bull"), ("Watchlist", "bull"), ("Mag7", "bear"), ("Watchlist", "bear")]
    said = "\n".join(body["lines"])
    assert "172 scans, 2 saved rows" in said and "$112.40" in said and "Not on Watchlist calls that day." in said
    assert {"view": "MomX Scanner", "list": "Mag7", "side": "bull", "tab": "history", "day": over_http, "ticker": "NVDA"}.items() <= body["links"][0].items()


def test_a_time_link_comes_through_the_route(over_http):
    running_api, owner, _ = api_accounts()
    with running_api() as api:
        status, body = api.post("/api/bot-chat", {"question": f"NVDA at 9:30 in the scanner on {over_http}"}, headers=owner)
    assert status == 200 and body["links"][0]["time"] == "09:30" and body["links"][0]["day"] == over_http and body["links"][0]["ticker"] == "NVDA"


def test_the_home_card_reads_the_finished_board_through_the_route(over_http, tmp_path):
    running_api, owner, _ = api_accounts()
    fresh = datetime.now(cb.ET).isoformat()
    (tmp_path / "boards").mkdir()
    (tmp_path / "boards" / "Mag7.json").write_text(json.dumps({"tapeAsOf": fresh, "rows": [{"symbol": "TSLA", "scanPass": True, "highLow": {"value": 0.9, "degree": 0.9}, "last": 300.0, "pctChange": 1.0}]}), encoding="utf-8")
    with running_api() as api:
        status, body = api.post("/api/bot-chat", {"home": True, "context": {}}, headers=owner)
    cards = [c for c in body["home"]["cards"] if c["kind"] == "scanner"]
    assert status == 200 and cards and "Mag7: TSLA" in cards[0]["lines"] and cards[0]["asOf"]


def test_no_zero_length_sleep_yields_in_the_chat_readers():
    """2026-11-11 review: time.sleep(0) on CPython 3.12 drops and re-takes the interpreter lock at once, resetting a
    waiting thread's switch request, so the serving threads starved for 100-300 ms. The readers rely on small pieces."""
    import inspect
    from momx import chat_chart, chat_scanner as scanner
    for module in (scanner, chat_chart):
        assert not re.search(r"\bsleep\(\s*0\s*\)", re.sub(r'"""[\s\S]*?"""|#.*', "", inspect.getsource(module))), module.__name__
