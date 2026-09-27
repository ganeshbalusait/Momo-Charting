"""Re-point the push tests at the format he chose on 2026-09-02.

The section this replaces encoded the contract that shipped hours earlier
(movers hoisted to the front, " / "-joined, capped at 260 characters). He
compared the two notifications and said "yesterday is better I want same as
yesterday format", so that contract is gone and this pins the new one --
including his exact sample, character for character.
"""
import io

p = "tests/test_morning_brief_push_content.py"
s = io.open(p, encoding="utf-8", newline="").read()

START = """# ----------------------------------------------------------------------
# push order: the app reads top-to-bottom, a phone does not
# ----------------------------------------------------------------------"""
END = """# ----------------------------------------------------------------------
# build_briefing still renders the app exactly as before
# ----------------------------------------------------------------------"""
start = s.index(START)
end = s.index(END)

NEW = '''# ----------------------------------------------------------------------
# format: the whole brief, one line per line, in the APP'S order
#
# He compared the two notifications on 2026-09-02 - "yesterday is better I
# want same as yesterday format" - and the sample he pointed at is pinned
# verbatim in test_yesterdays_message_is_reproduced_exactly below. Everything
# in this section exists to keep that shape.
# ----------------------------------------------------------------------

def test_the_lines_are_separate_lines_not_a_joined_paragraph():
    body = mb.push_message(brief(market=TAPE, movers=[MOVERS, LOSERS]))
    assert " / " not in body
    assert body.splitlines()[0] == TAPE


def test_the_brief_keeps_the_apps_order_tape_setup_then_movers():
    # This inverts the rule that shipped hours earlier. That version hoisted
    # the movers because the body was capped at two lines; with the whole
    # brief pushed there is nothing to make room for, and re-ordering only
    # moved lines away from where he reads them on the card.
    body = mb.push_message(brief(market=TAPE, scanner=[SIGNAL], movers=[MOVERS]))
    assert body.index(TAPE) < body.index("Strongest setup") < body.index("DELL")


def test_the_watch_line_is_dropped_when_the_scanner_matched():
    # "Strongest setup:" plus the Scanner footer already say it.
    body = mb.push_message(brief(scanner=[SIGNAL], watch="Watch NFLX at the open - x."))
    assert "Watch NFLX at the open" not in body


def test_the_watch_line_stays_when_the_scanner_matched_nothing():
    # Then it is the only summary line there is.
    assert QUIET in mb.push_message(brief(market=TAPE, movers=[MOVERS]))


def test_the_scanner_footer_lists_the_matched_symbols():
    payload = brief(scanner=[SIGNAL])
    payload["sections"]["scannerSymbols"] = ["NFLX", "AAPL"]
    assert "Scanner: NFLX, AAPL" in mb.push_message(payload)


def test_the_scanner_footer_says_so_when_nothing_matched():
    assert "Scanner: no signals yet" in mb.push_message(brief(market=TAPE, movers=[MOVERS]))


def test_the_build_time_is_stamped_so_a_stale_tray_is_obvious():
    payload = brief(market=TAPE)
    payload["generatedAt"] = "2026-09-01T09:13:20-04:00"
    assert mb.push_message(payload).endswith("(brief built 09:13:20 ET)")


def test_the_build_time_falls_back_to_the_build_moment():
    body = mb.push_message(brief(market=TAPE), now_et=at(9, 13))
    assert "(brief built 09:13:00 ET)" in body


def test_there_is_no_open_agx_tagline():
    # The full brief IS the notification now.
    assert "open AGX" not in mb.push_message(brief(market=TAPE, movers=[MOVERS]))


def test_the_bold_markers_do_not_reach_the_phone():
    assert "**" not in mb.push_message(brief(movers=[MOVERS, LOSERS]))


def test_the_placeholder_line_is_never_pushed_as_scanner_content():
    assert NO_SIGNALS not in mb.push_message(brief(market=TAPE, movers=[MOVERS]))


def test_nothing_to_say_pushes_nothing():
    assert mb.push_message({"lines": [], "sections": None}) is None
    assert mb.push_message(None) is None


def test_a_payload_without_sections_is_sent_as_its_own_lines():
    assert mb.push_message({"lines": ["first", "second"]}) == "first\\nsecond"


def test_yesterdays_message_is_reproduced_exactly():
    """The sample he asked for, rebuilt from the inputs that produced it.

    Not a paraphrase: this is the text in his 2026-09-01 09:17 screenshot,
    character for character. If this test has to change, the format he chose
    is changing with it.
    """
    scanner = {"rows": [{"symbol": "NFLX", "strength": "STRONG", "score": 2,
                         "signals": "signals", "catalyst": None}]}
    quotes = {"SPY": {"change_pct": -0.7}, "QQQ": {"change_pct": -1.4},
              "LIDR": {"change_pct": 42.8}, "SOXS": {"change_pct": 6.8},
              "USO": {"change_pct": 2.6}, "SOXL": {"change_pct": -6.5},
              "HL": {"change_pct": -4.8}}
    catalysts = {"LIDR": {"headline": "AEye's Apollo Long-Range Lidar Selected By Lunar "
                                      "Outpost For Integration Onto Pegasus Lunar Terrain "
                                      "Vehicle", "kind": "NEWS", "ageMinutes": 120}}
    built = datetime(2026, 9, 1, 9, 13, 20, tzinfo=ET)
    payload = mb.build_briefing(scanner, quotes, built, catalysts)

    assert mb.push_title(datetime(2026, 9, 1, 9, 17, tzinfo=ET)) == (
        "AGX Morning Brief - 13 min to the open"
    )
    assert mb.push_message(payload, now_et=built) == (
        "SPY -0.7%, QQQ -1.4% premarket - tape leans bearish.\\n"
        "Strongest setup: NFLX - signals, STRONG (2). No fresh catalyst found.\\n"
        "Watchlist gainers: LIDR +42.8% (NEWS: AEye's Apollo Long-Range Lidar Selected By "
        "Lunar Outpost For Integration Onto Pegasus Lunar Terrain Vehicle (2h ago)), "
        "SOXS +6.8%, USO +2.6%.\\n"
        "Watchlist losers: SOXL -6.5%, HL -4.8%.\\n"
        "\\n"
        "Scanner: NFLX\\n"
        "\\n"
        "(brief built 09:13:20 ET)"
    )


# ----------------------------------------------------------------------
# the countdown title
# ----------------------------------------------------------------------

@pytest.mark.parametrize("hour,minute,expected", [
    (9, 17, "13 min to the open"),
    (9, 0, "30 min to the open"),
    (8, 30, "1h to the open"),
    (6, 47, "2h 43m to the open"),
    (5, 30, "4h to the open"),
    (9, 30, "market open"),
    (10, 5, "market open"),
])
def test_the_countdown_reads_naturally_at_any_hour(hour, minute, expected):
    assert mb.countdown_phrase(at(hour, minute)) == expected
    assert mb.push_title(at(hour, minute)) == "AGX Morning Brief - " + expected


def test_a_title_without_a_clock_still_has_a_name():
    assert mb.push_title(None) == "AGX Morning Brief"


# ----------------------------------------------------------------------
# the budget is a ceiling against a runaway headline, not a design
# ----------------------------------------------------------------------

def test_a_normal_brief_is_never_truncated():
    # The whole point of his 2026-09-02 complaint: the catalyst is WHY it
    # moved, and a 17-word headline survived intact yesterday.
    long_news = "NEWS: " + " ".join(["Lunar"] * 30)
    body = mb.push_message(
        brief(market=TAPE, movers=["Watchlist gainers: **LIDR** +42.8% (%s)." % long_news])
    )
    assert long_news in body
    assert "\\u2026" not in body


def test_a_runaway_payload_sheds_catalysts_before_it_cuts_text():
    body = mb.push_message(brief(movers=[MOVERS, LOSERS], market=TAPE), budget=140)
    assert "DELL +8.4%" in body and "MDB -11.7%" in body
    assert "UPGRADE" not in body
    assert len(body) <= 140


def test_an_unfittable_payload_is_cut_not_dropped():
    payload = brief(movers=["Watchlist gainers: " + "X" * 2000 + "."])
    body = mb.push_message(payload, budget=80)
    assert body.endswith("\\u2026")
    assert len(body) <= 80


'''

s = s[:start] + NEW + s[end:]
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("tests rewritten for the new format")
