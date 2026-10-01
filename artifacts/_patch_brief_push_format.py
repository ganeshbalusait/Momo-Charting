"""The phone push takes the format he asked for: yesterday's, in the app's own order.

2026-09-02, comparing the two notifications side by side: "yesterday is
better I want same as yesterday format".

Yesterday's (09:17 ET, composed by hand in a chat session -- "13 min to the
open" and "(brief built ...)" have never existed anywhere in this repo, so
there was nothing to restore, only something to build):

    AGX Morning Brief - 13 min to the open
    SPY -0.7%, QQQ -1.4% premarket - tape leans bearish.
    Strongest setup: NFLX - signals, STRONG (2). No fresh catalyst found.
    Watchlist gainers: LIDR +42.8% (NEWS: AEye's Apollo ... (2h ago)), SOXS +6.8%, USO +2.6%.
    Watchlist losers: SOXL -6.5%, HL -4.8%.

    Scanner: NFLX

    (brief built 09:13:20 ET)

Today's, which this replaces:

    AGX Morning Brief
    Watchlist gainers: EOSE +14.5%, ... / Watchlist losers: MDB -12.5%, ... /
    No scanner signals yet - ... / SPY +0.1%, QQQ +0.0% premarket - tape is
    flat.  - open AGX for the full brief

Four differences, and his read is right on every one:

* ONE LINE PER LINE, not " / "-joined. A phone expands a notification, so
  the paragraph bought nothing and cost all the scannability.
* THE BRIEF'S OWN ORDER (tape, setup, gainers, losers). Yesterday's fix
  re-sorted movers to the front because the body was capped at two lines and
  the boilerplate was eating both. With the whole brief in the push there is
  nothing to make room for, and re-ordering only moves things away from
  where he reads them in the app.
* NO TRUNCATION. The LIDR headline is the reason LIDR is up 42.8%; cutting
  it to fit 260 characters threw away the only part he cannot get from the
  number. The budget is now a safety ceiling (1200), not a design.
* THE FOOTER: the scanner's matches as a bare list, and the build time. A
  brief that does not say when it was built cannot be told from a stale one
  sitting in the notification tray.

The title carries a countdown to 09:30 rather than a fixed slot name, which
is also what makes the two daily pushes distinguishable without naming the
slots (they are minutes apart in wording by construction).

What this does NOT change: the substance gate and the 07:30 deadline from
c0e59fa (the 05:30 push had nothing in it), the two slots, and `lines` --
the app's card is untouched.
"""
import io

# ---------------------------------------------------------------------------
# morning_briefing.py
# ---------------------------------------------------------------------------
p = "morning_briefing.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = """#: Roughly what a phone shows of a notification body before it truncates.
#: Not a hard limit of ntfy - a limit of the lock screen, which is where this
#: is actually read.
PUSH_BUDGET_CHARS = 260
"""
NEW = """#: A safety ceiling on the notification body, NOT a design target. A phone
#: expands a notification, so the whole brief is readable; this only stops a
#: pathological payload (a runaway headline) from being pushed whole. He
#: asked for the untruncated brief explicitly on 2026-09-02.
PUSH_BUDGET_CHARS = 1200

#: The regular session opens at 09:30 ET.
MARKET_OPEN_MINUTE = 9 * 60 + 30
"""
assert s.count(OLD) == 1, "budget anchor"
s = s.replace(OLD, NEW)

# --- sections gain the scanner's symbols ----------------------------------
OLD = '''            "scanner": list(scanner_block) if real_rows else [],
            "movers": list(mover_block),
            "watch": watch,'''
NEW = '''            "scanner": list(scanner_block) if real_rows else [],
            # The matched symbols on their own, for the push's "Scanner:"
            # footer. Board order, which is strongest-first.
            "scannerSymbols": [
                str(row.get("symbol") or "").upper()
                for row in real_rows
                if str(row.get("symbol") or "").strip()
            ],
            "movers": list(mover_block),
            "watch": watch,'''
assert s.count(OLD) == 1, "sections anchor"
s = s.replace(OLD, NEW)

# --- countdown + title ----------------------------------------------------
OLD = '''def push_candidates(payload: object) -> list[str]:'''
NEW = '''def countdown_phrase(now_et: object) -> str:
    """"13 min to the open" - how he wanted the notification to title itself.

    Past an hour out, minutes stop being readable at a glance ("163 min to
    the open"), so it switches to "2h 43m". After the bell there is no
    countdown left to give.
    """
    if not isinstance(now_et, datetime):
        return ""
    remaining = MARKET_OPEN_MINUTE - (now_et.hour * 60 + now_et.minute)
    if remaining <= 0:
        return "market open"
    if remaining < 60:
        return f"{remaining} min to the open"
    hours, minutes = divmod(remaining, 60)
    return f"{hours}h to the open" if minutes == 0 else f"{hours}h {minutes}m to the open"


def push_title(now_et: object = None) -> str:
    """The notification title, carrying the countdown.

    The countdown is also what keeps the day's two pushes distinguishable on
    the lock screen - they are always minutes apart in wording - so the slots
    do not have to name themselves.
    """
    phrase = countdown_phrase(now_et)
    return f"AGX Morning Brief - {phrase}" if phrase else "AGX Morning Brief"


def _built_stamp(payload: object, now_et: object = None) -> str:
    """"(brief built 09:13:20 ET)" - so a stale notification is obvious."""
    raw = payload.get("generatedAt") if isinstance(payload, dict) else None
    moment = None
    if isinstance(raw, str) and raw.strip():
        try:
            moment = datetime.fromisoformat(raw.strip())
        except ValueError:
            moment = None
    if moment is None and isinstance(now_et, datetime):
        moment = now_et
    if moment is None:
        return ""
    return f"(brief built {moment.strftime('%H:%M:%S')} ET)"


def push_candidates(payload: object) -> list[str]:'''
assert s.count(OLD) == 1, "push_candidates anchor"
s = s.replace(OLD, NEW)

# --- push_candidates: the brief's own order -------------------------------
OLD = '''    Deliberately NOT the app's order. The app shows every line, so it leads
    with the tape and ends with the summary; a notification shows two or
    three, so it must lead with the lines carrying a ticker and a number.

    A payload without ``sections`` (an older shape, or a test double) cannot
    be re-ordered honestly, so its own order is kept.
    """
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, dict):
        raw = (payload or {}).get("lines") if isinstance(payload, dict) else None
        return [_plain(line) for line in (raw or []) if _plain(line)]
    ordered: list[object] = []
    ordered.extend(sections.get("scanner") or [])
    ordered.extend(sections.get("movers") or [])
    for single in (sections.get("watch"), sections.get("market")):
        if single:
            ordered.append(single)
    return [_plain(line) for line in ordered if _plain(line)]'''
NEW = '''    THE APP'S ORDER: tape, strongest setup, gainers, losers. It briefly was
    not - movers were hoisted to the front on 2026-09-02 because the body was
    capped at two lines and the boilerplate ate both - and he said so the
    same morning ("yesterday is better"). With the whole brief in the push
    there is nothing to make room for, and re-ordering only moves lines away
    from where he reads them on the card.

    The watch line ("Watch NFLX at the open...") is dropped when the scanner
    matched something, because "Strongest setup:" and the "Scanner:" footer
    already say it. On a morning with no matches it is the only summary
    there is, so it stays.
    """
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, dict):
        raw = (payload or {}).get("lines") if isinstance(payload, dict) else None
        return [_plain(line) for line in (raw or []) if _plain(line)]
    scanner = list(sections.get("scanner") or [])
    ordered: list[object] = []
    if sections.get("market"):
        ordered.append(sections["market"])
    ordered.extend(scanner)
    ordered.extend(sections.get("movers") or [])
    if sections.get("watch") and not scanner:
        ordered.append(sections["watch"])
    return [_plain(line) for line in ordered if _plain(line)]'''
assert s.count(OLD) == 1, "candidates body anchor"
s = s.replace(OLD, NEW)

# --- push_message: the whole brief, one line per line ---------------------
OLD = '''def push_message(payload: object, *, budget: int = PUSH_BUDGET_CHARS) -> str | None:
    """The notification body, or None when there is nothing worth sending.

    Fits as many lines as the budget allows, in push order. A line that does
    not fit WITH its catalyst is retried without it - "DELL +8.4%" is worth
    more than the upgrade note that pushed it over the edge.
    """
    candidates = push_candidates(payload)
    if not candidates:
        return None
    body = ""
    for line in candidates:
        for text in (line, _without_parentheticals(line)):
            if not text:
                continue
            joined = text if not body else body + " / " + text
            if len(joined) <= budget:
                body = joined
                break
        else:
            break  # this line does not fit even trimmed; stop here
    if not body:
        # Even the single most important line is too long. Truncated beats
        # silent: the app holds the rest either way.
        return _without_parentheticals(candidates[0])[: max(1, budget - 1)].rstrip() + "\\u2026"
    return body'''
NEW = '''def push_message(payload: object, *, now_et: object = None,
                 budget: int = PUSH_BUDGET_CHARS) -> str | None:
    """The notification body, or None when there is nothing worth sending.

    The whole brief, ONE LINE PER LINE, then the scanner's matches, then the
    build time::

        SPY -0.7%, QQQ -1.4% premarket - tape leans bearish.
        Strongest setup: NFLX - signals, STRONG (2). No fresh catalyst found.
        Watchlist gainers: LIDR +42.8% (NEWS: ...), SOXS +6.8%, USO +2.6%.
        Watchlist losers: SOXL -6.5%, HL -4.8%.

        Scanner: NFLX

        (brief built 09:13:20 ET)

    Nothing is dropped to fit a line count - a phone expands a notification,
    and the catalyst in the parentheses is the only part of "LIDR +42.8%" he
    cannot read off the number. ``budget`` is a ceiling against a runaway
    headline, not a target: only past it are the catalysts dropped, and only
    then is the text cut.
    """
    lines = push_candidates(payload)
    if not lines:
        return None

    sections = payload.get("sections") if isinstance(payload, dict) else None
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
assert s.count(OLD) == 1, "push_message anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("morning_briefing.py: countdown title, full-brief body, Scanner + built-at footer")

# ---------------------------------------------------------------------------
# api_server.py: use the new title and body
# ---------------------------------------------------------------------------
p = "api_server.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = """from morning_briefing import has_substance as morning_has_substance
from morning_briefing import push_message as morning_push_message"""
NEW = """from morning_briefing import has_substance as morning_has_substance
from morning_briefing import push_message as morning_push_message
from morning_briefing import push_title as morning_push_title"""
assert s.count(OLD) == 1, "import anchor"
s = s.replace(OLD, NEW)

OLD = '''            minute_of_day = now_et.hour * 60 + now_et.minute
            if minute_of_day >= MORNING_BRIEF_LATE_PUSH_MINUTE:
                slot, title = "late", "AGX Brief - 30 min to the open"
            else:
                slot, title = "early", "AGX Morning Brief"'''
NEW = '''            minute_of_day = now_et.hour * 60 + now_et.minute
            # The slot decides WHETHER to push; the title is the same shape
            # for both and carries a countdown to 09:30, which is what keeps
            # the two distinguishable on the lock screen (2026-09-02: he
            # asked for the format of a hand-composed push that titled itself
            # "AGX Morning Brief - 13 min to the open").
            slot = "late" if minute_of_day >= MORNING_BRIEF_LATE_PUSH_MINUTE else "early"'''
assert s.count(OLD) == 1, "slot anchor"
s = s.replace(OLD, NEW)

OLD = '''                body = morning_push_message(payload) if ready else None'''
NEW = '''                body = morning_push_message(payload, now_et=now_et) if ready else None'''
assert s.count(OLD) == 1, "body anchor"
s = s.replace(OLD, NEW)

OLD = '''                    _push_phone_notification(
                        title,
                        body + "  - open AGX for the full brief",
                        tags="newspaper",
                    )'''
NEW = '''                    # No "open AGX for the full brief" tail any more: the
                    # full brief IS the notification now.
                    _push_phone_notification(
                        morning_push_title(now_et),
                        body,
                        tags="newspaper",
                    )'''
assert s.count(OLD) == 1, "push call anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("api_server.py: countdown title, untruncated body, no trailing tagline")
