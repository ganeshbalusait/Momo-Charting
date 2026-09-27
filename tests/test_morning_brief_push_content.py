"""What the morning brief PUSHES, and when it is worth pushing at all.

2026-09-02. The 05:31 ET notification on his phone read "No signals on the
nine scanner tickers yet. / Quiet tape so far" while the app at 07:53 read
"DELL +7.9% (UPGRADE...), MDB -12.7%". Two faults, pinned here:

* it fired on the 05:30 build, which is empty by construction (no premarket
  quotes, and Schwab publishes no current-day bars before 07:00), spending
  the morning's early slot on a message that carried nothing;
* the body was `lines[:2]`, and the brief's first two lines are always the
  boilerplate ones, so the movers -- the only lines with a ticker and a
  number -- were always the ones cut.

The distinction these tests exist to hold: "no signals YET" is the absence
of content, not content. A quiet tape at 07:30 is an observation and does
push; the same words at 05:30 mean the data has not arrived and must not.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import api_server
import morning_briefing as mb

ET = ZoneInfo("America/New_York")

MOVERS = "Watchlist gainers: **DELL** +8.4% (UPGRADE: raises PT to $600 (26m ago))."
LOSERS = "Watchlist losers: **MDB** -11.7%, **CRDO** -10.7%."
TAPE = "SPY +0.0%, QQQ -0.1% premarket - tape is flat."
NO_SIGNALS = "No signals on the nine scanner tickers yet."
QUIET = "Quiet tape so far - no scanner signals and no big watchlist moves."
SIGNAL = "Strongest setup: AAPL - 2H cross, STRONG (88). Catalyst - upgrade."


def brief(*, market=None, scanner=(), movers=(), watch=QUIET):
    """A payload in the shape build_briefing returns."""
    lines = ([market] if market else []) + list(scanner or [NO_SIGNALS]) + list(movers) + [watch]
    return {
        "date": "2026-09-02",
        "lines": lines,
        "sections": {
            "market": market,
            "scanner": list(scanner),
            "movers": list(movers),
            "watch": watch,
        },
    }


# ----------------------------------------------------------------------
# has_substance: the absence of news is not news
# ----------------------------------------------------------------------

def test_the_0530_brief_has_no_substance():
    # Exactly what his phone received: no market line, the placeholder
    # scanner sentence, no movers.
    assert mb.has_substance(brief()) is False


def test_a_flat_tape_alone_is_still_not_substance():
    # "tape is flat" could have been written last night.
    assert mb.has_substance(brief(market=TAPE)) is False


def test_a_watchlist_mover_is_substance():
    assert mb.has_substance(brief(market=TAPE, movers=[MOVERS])) is True


def test_a_scanner_signal_is_substance():
    assert mb.has_substance(brief(scanner=[SIGNAL])) is True


def test_substance_of_an_unknown_payload_shape_falls_back_to_lines():
    # An older payload, or a test double, cannot be judged - do not silence
    # a push over a shape this function does not understand.
    assert mb.has_substance({"lines": ["something"]}) is True
    assert mb.has_substance({"lines": []}) is False
    assert mb.has_substance(None) is False


# ----------------------------------------------------------------------
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


def test_there_is_no_scanner_footer_when_nothing_matched():
    # The watch line already says "No scanner signals yet - ...", and a
    # "Scanner: no signals yet" under it said the same thing twice.
    body = mb.push_message(brief(market=TAPE, movers=[MOVERS]))
    assert "Scanner:" not in body
    assert QUIET in body


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
    assert mb.push_message({"lines": ["first", "second"]}) == "first\nsecond"


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
        "SPY -0.7%, QQQ -1.4% premarket - tape leans bearish.\n"
        "Strongest setup: NFLX - signals, STRONG (2). No fresh catalyst found.\n"
        "Watchlist gainers: LIDR +42.8% (NEWS: AEye's Apollo Long-Range Lidar Selected By "
        "Lunar Outpost For Integration Onto Pegasus Lunar Terrain Vehicle (2h ago)), "
        "SOXS +6.8%, USO +2.6%.\n"
        "Watchlist losers: SOXL -6.5%, HL -4.8%.\n"
        "\n"
        "Scanner: NFLX\n"
        "\n"
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
        brief(market=TAPE, movers=["Watchlist gainers: **LIDR** +42.8% (" + long_news + ")."])
    )
    assert long_news in body
    assert "\u2026" not in body


def test_a_runaway_payload_sheds_catalysts_before_it_cuts_text():
    body = mb.push_message(brief(movers=[MOVERS, LOSERS], market=TAPE), budget=140)
    assert "DELL +8.4%" in body and "MDB -11.7%" in body
    assert "UPGRADE" not in body
    assert len(body) <= 140


def test_going_one_character_over_does_not_shed_the_whole_brief():
    """The cliff the 2026-09-02 review found.

    Shedding every catalyst at once meant a body barely over the ceiling came
    back hundreds of characters UNDER it, losing information it had room for.
    One line is shed, the rest survive.
    """
    payload = brief(market=TAPE, scanner=[SIGNAL], movers=[MOVERS, LOSERS])
    full = mb.push_message(payload, budget=10_000)
    body = mb.push_message(payload, budget=len(full) - 1)

    assert len(body) <= len(full) - 1
    # It shed the ONE line that got it under, not everything.
    assert len(body) > len(full) - 120, "shed far more than it needed to"
    # The scanner score is a parenthetical too, and must outlive the catalysts.
    assert "STRONG (88)" in body
    assert "DELL +8.4%" in body and "MDB -11.7%" in body


def test_the_biggest_saving_is_shed_first():
    # The line with the long catalyst goes; the short one keeps its own.
    short = "Watchlist losers: **MDB** -11.7% (PT cut)."
    payload = brief(market=TAPE, movers=[MOVERS, short])
    full = mb.push_message(payload, budget=10_000)
    body = mb.push_message(payload, budget=len(full) - 1)
    assert "UPGRADE" not in body, "the long catalyst should have gone first"
    assert "(PT cut)" in body, "the short one did not need to go"


def test_an_unfittable_payload_is_cut_not_dropped():
    payload = brief(movers=["Watchlist gainers: " + "X" * 2000 + "."])
    body = mb.push_message(payload, budget=80)
    assert body.endswith("\u2026")
    assert len(body) <= 80


# ----------------------------------------------------------------------
# build_briefing still renders the app exactly as before
# ----------------------------------------------------------------------

def test_lines_are_unchanged_and_sections_describe_them():
    quotes = {
        "SPY": {"change_pct": 0.05},
        "QQQ": {"change_pct": -0.1},
        "DELL": {"change_pct": 8.4},
        "MDB": {"change_pct": -11.7},
    }
    payload = mb.build_briefing({}, quotes, datetime(2026, 9, 2, 8, 3, tzinfo=ET))
    lines, sections = payload["lines"], payload["sections"]
    assert lines[0].startswith("SPY")
    assert lines[1] == NO_SIGNALS
    assert sections["market"] == lines[0]
    assert sections["scanner"] == [], "the placeholder is the ABSENCE of a section"
    assert sections["movers"] == [l for l in lines if l.startswith("Watchlist")]
    assert sections["watch"] == lines[-1]
    assert mb.has_substance(payload) is True


# ----------------------------------------------------------------------
# the gate, driven through the real _build_morning_briefing
# ----------------------------------------------------------------------

@pytest.fixture
def rig(monkeypatch):
    """DashboardState with the expensive parts stubbed, plus a push log.

    ``rig.payload`` is what the brief build returns; a test swaps it to
    simulate the tape filling in as the morning goes on.
    """
    state = object.__new__(api_server.DashboardState)
    state._morning_briefing_payload = None
    pushes: list[dict] = []
    box = {"payload": brief()}

    monkeypatch.setattr(
        api_server, "_push_phone_notification",
        lambda title, body, **k: pushes.append({"title": title, "body": body}),
    )
    monkeypatch.setattr(api_server.DashboardState, "premarket_scanner_payload", lambda self: {})
    monkeypatch.setattr(
        api_server.DashboardState, "_record_morning_movers", lambda self, *a, **k: None
    )
    monkeypatch.setattr(api_server, "_live_quote_client", lambda profile: _Quotes())
    monkeypatch.setattr(api_server, "news_credentials", lambda: None)
    monkeypatch.setattr(
        api_server, "morning_build_briefing",
        lambda scanner, quotes, now_et, catalysts: dict(box["payload"]),
    )
    monkeypatch.setattr(api_server, "premarket_history_record_briefing", lambda lines, now: None)
    return state, pushes, box


class _Quotes:
    def get_quotes(self, symbols, **k):
        return {"SPY": {"change_pct": 0.1, "last_price": 500.0}}


def at(hour, minute):
    return datetime(2026, 9, 2, hour, minute, tzinfo=ET)


def test_the_empty_0530_build_does_not_buzz_his_phone(rig):
    state, pushes, _ = rig
    for minute in (30, 35, 40, 45, 50, 55):
        state._build_morning_briefing(at(5, minute))
    assert pushes == [], "the emptiest brief of the day was pushed anyway"


def test_the_early_push_fires_as_soon_as_there_is_something_to_say(rig):
    state, pushes, box = rig
    state._build_morning_briefing(at(5, 30))
    assert pushes == []
    box["payload"] = brief(market=TAPE, movers=[MOVERS])   # 07:05, quotes arrive
    state._build_morning_briefing(at(7, 5))
    assert len(pushes) == 1
    assert "DELL" in pushes[0]["body"]
    assert "Morning Brief" in pushes[0]["title"]


def test_holding_the_slot_does_not_lose_it(rig):
    # A held build must leave the slot OPEN, or the morning is silent.
    state, pushes, box = rig
    for minute in (30, 40, 50):
        state._build_morning_briefing(at(5, minute))
    box["payload"] = brief(scanner=[SIGNAL])
    state._build_morning_briefing(at(6, 0))
    assert len(pushes) == 1


def test_a_genuinely_quiet_morning_still_pushes_at_0730(rig):
    # By 07:30 the premarket tape is real, so "quiet" is an observation.
    state, pushes, _ = rig
    state._build_morning_briefing(at(7, 25))
    assert pushes == []
    state._build_morning_briefing(at(7, 30))
    assert len(pushes) == 1
    assert QUIET in pushes[0]["body"]


def test_the_early_push_still_only_fires_once(rig):
    state, pushes, box = rig
    box["payload"] = brief(market=TAPE, movers=[MOVERS])
    for minute in (0, 5, 10, 15):
        state._build_morning_briefing(at(7, minute))
    assert len(pushes) == 1


def test_the_0900_push_is_never_held_even_on_a_dead_quiet_tape(rig):
    # The half-hour-to-the-open brief he asked for goes out regardless.
    state, pushes, _ = rig
    state._build_morning_briefing(at(9, 0))
    assert len(pushes) == 1
    assert "30 min to the open" in pushes[0]["title"]


def test_both_slots_still_fire_across_a_normal_morning(rig):
    state, pushes, box = rig
    state._build_morning_briefing(at(5, 30))          # held: nothing to say
    box["payload"] = brief(market=TAPE, movers=[MOVERS])
    state._build_morning_briefing(at(7, 10))          # early
    box["payload"] = brief(market=TAPE, movers=[MOVERS, LOSERS])
    state._build_morning_briefing(at(9, 0))           # late
    assert len(pushes) == 2
    assert pushes[0]["title"] != pushes[1]["title"]
    assert "MDB" in pushes[1]["body"]
