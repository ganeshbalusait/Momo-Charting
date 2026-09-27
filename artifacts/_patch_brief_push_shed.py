"""Two fixes from the adversarial review of the push format.

1. THE CEILING WAS A CLIFF. push_message built the whole body and, if it was
   over PUSH_BUDGET_CHARS, rebuilt it with _without_parentheticals applied to
   EVERY line at once. All-or-nothing: measured, a body 3 characters over the
   1200 ceiling came back at 484 -- 716 characters UNDER the budget it was
   trying to respect -- having dropped every mover catalyst AND every scanner
   score ("(STRONG 3)" is a parenthetical too). That is exactly the loss this
   whole change exists to prevent, just moved onto a trigger he cannot see
   coming.

   Now it sheds ONE line at a time, biggest saving first, and stops the moment
   it fits. Mover catalysts are the long parentheticals so they go first;
   scanner scores are short, so they survive unless nothing else will do.

   The ceiling also moves 1200 -> 2000. Real briefs from the archive measure
   448-847 characters (p95 ~1000 in a 20k-day simulation over the project's
   own captured headline lengths), and ntfy carries far more than this, so
   2000 leaves the ladder as a genuine safety valve rather than something a
   newsy morning can reach.

2. NO "Scanner:" FOOTER WHEN NOTHING MATCHED. It read

       No scanner signals yet - biggest watchlist move is EOSE +12.8%.

       Scanner: no signals yet

   which says the same thing twice. The watch line is kept precisely when the
   scanner matched nothing, so it always covers that case; the footer now
   appears only when there is something to list, which is also exactly what
   his 2026-09-01 sample showed ("Scanner: NFLX").
"""
import io

p = "morning_briefing.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = """#: A safety ceiling on the notification body, NOT a design target. A phone
#: expands a notification, so the whole brief is readable; this only stops a
#: pathological payload (a runaway headline) from being pushed whole. He
#: asked for the untruncated brief explicitly on 2026-09-02.
PUSH_BUDGET_CHARS = 1200
"""
NEW = """#: A safety ceiling on the notification body, NOT a design target. A phone
#: expands a notification, so the whole brief is readable; this only stops a
#: pathological payload (a runaway headline) from being pushed whole. He
#: asked for the untruncated brief explicitly on 2026-09-02.
#:
#: 2000, not 1200: real briefs out of the archive measure 448-847 characters
#: and a 20k-day simulation over this project's own captured headline lengths
#: puts p95 near 1000, so 1200 was close enough for a newsy morning to reach.
#: ntfy carries far more than either number.
PUSH_BUDGET_CHARS = 2000
"""
assert s.count(OLD) == 1, "budget anchor"
s = s.replace(OLD, NEW)

OLD = '''    sections = payload.get("sections") if isinstance(payload, dict) else None
    blocks: list[str] = ["\\n".join(lines)]
    if isinstance(sections, dict):
        symbols = [
            str(symbol).strip().upper()
            for symbol in (sections.get("scannerSymbols") or [])
            if str(symbol or "").strip()
        ]
        blocks.append("Scanner: " + (", ".join(symbols) if symbols else "no signals yet"))
    stamp = _built_stamp(payload, now_et)
    if stamp:
        blocks.append(stamp)
    body = "\\n\\n".join(blocks)
    if len(body) <= budget:
        return body

    # Over the ceiling: shed the catalyst asides first - they are the long
    # part and the only optional one.
    blocks[0] = "\\n".join(_without_parentheticals(line) for line in lines)
    body = "\\n\\n".join(blocks)
    if len(body) <= budget:
        return body
    # Still over: cut, but never to nothing.
    return body[: max(1, budget - 1)].rstrip() + "\\u2026"'''
NEW = '''    sections = payload.get("sections") if isinstance(payload, dict) else None
    tail: list[str] = []
    if isinstance(sections, dict):
        symbols = [
            str(symbol).strip().upper()
            for symbol in (sections.get("scannerSymbols") or [])
            if str(symbol or "").strip()
        ]
        # Only when there is something to list. With no matches the watch
        # line is already in the body saying "No scanner signals yet...", and
        # a "Scanner: no signals yet" under it just said it twice.
        if symbols:
            tail.append("Scanner: " + ", ".join(symbols))
    stamp = _built_stamp(payload, now_et)
    if stamp:
        tail.append(stamp)

    def assemble(body_lines: list[str]) -> str:
        return "\\n\\n".join(["\\n".join(body_lines)] + tail)

    body = assemble(lines)
    if len(body) <= budget:
        return body

    # Over the ceiling: shed the catalyst asides ONE LINE AT A TIME, biggest
    # saving first, and stop the moment it fits. Shedding them all at once
    # (the first version of this) overshot wildly - a body 3 characters over
    # came back 716 characters UNDER, having thrown away every catalyst and
    # every scanner score to save 3. The mover catalysts are the long
    # parentheticals so they go first; "(STRONG 3)" is short and survives
    # unless nothing else will do.
    trimmed = list(lines)
    by_saving = sorted(
        range(len(lines)),
        key=lambda index: len(lines[index]) - len(_without_parentheticals(lines[index])),
        reverse=True,
    )
    for index in by_saving:
        if len(assemble(trimmed)) <= budget:
            break
        trimmed[index] = _without_parentheticals(trimmed[index])
    body = assemble(trimmed)
    if len(body) <= budget:
        return body
    # Still over: cut, but never to nothing.
    return body[: max(1, budget - 1)].rstrip() + "\\u2026"'''
assert s.count(OLD) == 1, "push_message tail anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("morning_briefing.py: incremental shed, budget 2000, no empty Scanner footer")

# --- tests follow the two decisions ---------------------------------------
p = "tests/test_morning_brief_push_content.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '''def test_the_scanner_footer_says_so_when_nothing_matched():
    assert "Scanner: no signals yet" in mb.push_message(brief(market=TAPE, movers=[MOVERS]))'''
NEW = '''def test_there_is_no_scanner_footer_when_nothing_matched():
    # The watch line already says "No scanner signals yet - ...", and a
    # "Scanner: no signals yet" under it said the same thing twice.
    body = mb.push_message(brief(market=TAPE, movers=[MOVERS]))
    assert "Scanner:" not in body
    assert QUIET in body'''
assert s.count(OLD) == 1, "scanner footer test anchor"
s = s.replace(OLD, NEW)

OLD = '''def test_a_runaway_payload_sheds_catalysts_before_it_cuts_text():
    body = mb.push_message(brief(movers=[MOVERS, LOSERS], market=TAPE), budget=140)
    assert "DELL +8.4%" in body and "MDB -11.7%" in body
    assert "UPGRADE" not in body
    assert len(body) <= 140'''
NEW = '''def test_a_runaway_payload_sheds_catalysts_before_it_cuts_text():
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
    assert "STRONG (2)" in body
    assert "DELL +8.4%" in body and "MDB -11.7%" in body


def test_the_biggest_saving_is_shed_first():
    # The line with the long catalyst goes; the short one keeps its own.
    short = "Watchlist losers: **MDB** -11.7% (PT cut)."
    payload = brief(market=TAPE, movers=[MOVERS, short])
    full = mb.push_message(payload, budget=10_000)
    body = mb.push_message(payload, budget=len(full) - 1)
    assert "UPGRADE" not in body, "the long catalyst should have gone first"
    assert "(PT cut)" in body, "the short one did not need to go"'''
assert s.count(OLD) == 1, "shed test anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("tests: cliff + shed-order pinned")
