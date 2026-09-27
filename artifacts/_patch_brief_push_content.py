"""The morning brief push: wait until it says something, then say the part that matters.

2026-09-02, from his phone: the 05:31 ET push read

    "No signals on the nine scanner tickers yet. / Quiet tape so far - no
     scanner signals and no big watchlist moves."

while the app at 07:53 read "DELL +7.9% (UPGRADE...), MDB -12.7% (...)".
Two independent faults, both in the push, neither in the brief itself:

1. TIMING. The push fired on the first build of the window, 05:30 ET. There
   are no premarket quotes that early and Schwab publishes no current-day
   bars before 07:00 (the known 04:00-07:00 hole), so the 05:30 brief is
   empty BY CONSTRUCTION -- and firing on it spent the morning's early slot.
   The old code pushed when the brief reached READY, but READY means "the
   build finished", not "the brief has anything in it". That is this repo's
   recurring truthy-but-meaningless-flag shape.

   Now the early slot waits for substance -- a scanner signal or a real
   watchlist mover -- with a hard deadline at 07:30 ET so a genuinely quiet
   morning still gets its push. A "quiet tape" line at 07:30 is an
   observation; at 05:30 it only means the data has not arrived.

2. CONTENT. The body was `lines[:2]`, the first two lines of the brief. The
   brief's order is fixed for the APP, which is read top to bottom: tape
   line, scanner line, movers, watch line. So the two lines a notification
   carried were always the two most boilerplate ones ("tape is flat", "no
   signals yet") and the movers -- the only lines with a name and a number --
   were always cut. Measured on the live 08:03 brief: lines 3 and 4 held
   EOSE +9.9%, DELL +8.4% (upgrade), MDB -11.7%, CRDO -10.7%; none of it
   would have been pushed.

   The push now orders by what he would act on (signals, movers, watch,
   tape) rather than by the app's reading order, and fits as many as the
   notification budget allows, dropping the parenthetical catalyst text
   before it drops a whole line.

`lines` is UNCHANGED, so the app renders exactly as before. build_briefing
additionally labels the same content under "sections", because recovering
which line is which by string-matching "Watchlist gainers:" back out of the
rendered text would be a guess that breaks the first time the wording moves.
"""
import io

# ---------------------------------------------------------------------------
# morning_briefing.py: label the sections, and choose what a phone gets
# ---------------------------------------------------------------------------
p = "morning_briefing.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = """# A watchlist name only counts as a "mover" past this premarket change.
MOVER_THRESHOLD_PCT = 2.0
MAX_GAINERS = 3
MAX_LOSERS = 2
"""
NEW = """# A watchlist name only counts as a "mover" past this premarket change.
MOVER_THRESHOLD_PCT = 2.0
MAX_GAINERS = 3
MAX_LOSERS = 2

#: Roughly what a phone shows of a notification body before it truncates.
#: Not a hard limit of ntfy - a limit of the lock screen, which is where this
#: is actually read.
PUSH_BUDGET_CHARS = 260
"""
assert s.count(OLD) == 1, "constants anchor"
s = s.replace(OLD, NEW)

OLD = '''def build_briefing(scanner_payload: object, quotes: dict, now_et: datetime,
                   catalysts: dict | None = None) -> dict:
    """Assemble the whole briefing. Every line comes from measured data."""
    scanner_rows = (scanner_payload or {}).get("rows") if isinstance(scanner_payload, dict) else []
    scanner_symbols = {
        str(row.get("symbol") or "").upper()
        for row in (scanner_rows or [])
        if isinstance(row, dict)
    }
    lines: list[str] = []
    market = market_line(quotes or {})
    if market:
        lines.append(market)
    lines.extend(scanner_lines(scanner_payload))
    lines.extend(mover_lines(quotes or {}, catalysts, exclude=scanner_symbols))
    lines.append(watch_line(scanner_payload, quotes or {}))
    return {
        "date": now_et.date().isoformat(),
        "generatedAt": now_et.isoformat(),
        "lines": lines,
    }
'''
NEW = '''def build_briefing(scanner_payload: object, quotes: dict, now_et: datetime,
                   catalysts: dict | None = None) -> dict:
    """Assemble the whole briefing. Every line comes from measured data."""
    scanner_rows = (scanner_payload or {}).get("rows") if isinstance(scanner_payload, dict) else []
    real_rows = [row for row in (scanner_rows or []) if isinstance(row, dict)]
    scanner_symbols = {str(row.get("symbol") or "").upper() for row in real_rows}
    market = market_line(quotes or {})
    scanner_block = scanner_lines(scanner_payload)
    mover_block = mover_lines(quotes or {}, catalysts, exclude=scanner_symbols)
    watch = watch_line(scanner_payload, quotes or {})

    lines: list[str] = []
    if market:
        lines.append(market)
    lines.extend(scanner_block)
    lines.extend(mover_block)
    lines.append(watch)
    return {
        "date": now_et.date().isoformat(),
        "generatedAt": now_et.isoformat(),
        "lines": lines,
        # The SAME content, labelled. `lines` is untouched - the app reads it
        # top to bottom and must not change. But a phone notification shows
        # two or three lines and has to be able to lead with the movers
        # instead of the boilerplate, and string-matching "Watchlist gainers:"
        # back out of the rendered text would be a guess that breaks the first
        # time the wording moves. This is the producer saying which is which.
        "sections": {
            "market": market,
            # scanner_lines() returns a "No signals on the nine scanner
            # tickers yet." placeholder when there are no rows. That is the
            # ABSENCE of a section, not a section, and a push must never
            # count it as something to say.
            "scanner": list(scanner_block) if real_rows else [],
            "movers": list(mover_block),
            "watch": watch,
        },
    }


def _plain(line: object) -> str:
    """The bold marker is for the app's renderer; a notification is text."""
    return str(line or "").replace("**", "").strip()


def _without_parentheticals(line: str) -> str:
    """Drop "(UPGRADE: ... )" catalyst asides, keeping the name and number."""
    kept: list[str] = []
    depth = 0
    for character in line:
        if character == "(":
            depth += 1
            continue
        if character == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            kept.append(character)
    text = " ".join("".join(kept).split())
    return text.replace(" ,", ",").replace(" .", ".")


def push_candidates(payload: object) -> list[str]:
    """The brief's lines in PUSH order: what he would act on comes first.

    Deliberately NOT the app's order. The app shows every line, so it leads
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
    return [_plain(line) for line in ordered if _plain(line)]


def has_substance(payload: object) -> bool:
    """True when the brief says something that was not true an hour ago.

    A scanner signal or a real watchlist mover is substance. "Tape is flat"
    and "no signals yet" are not: before ~07:00 ET they mean the DATA has not
    arrived (Schwab publishes no current-day premarket bars before then), not
    that the morning is quiet. Pushing on them spends the morning's early
    slot on a message that could have been written the night before.
    """
    sections = payload.get("sections") if isinstance(payload, dict) else None
    if not isinstance(sections, dict):
        # Cannot tell the lines apart: keep the old behaviour rather than
        # silence a push on a payload shape this function does not understand.
        return bool((payload or {}).get("lines") if isinstance(payload, dict) else False)
    return bool(sections.get("scanner")) or bool(sections.get("movers"))


def push_message(payload: object, *, budget: int = PUSH_BUDGET_CHARS) -> str | None:
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
    return body
'''
assert s.count(OLD) == 1, "build_briefing anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("morning_briefing.py: sections, has_substance, push_message")

# ---------------------------------------------------------------------------
# api_server.py: hold the early push until it is worth sending
# ---------------------------------------------------------------------------
p = "api_server.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = "MORNING_BRIEF_LATE_PUSH_MINUTE = 9 * 60"
NEW = '''MORNING_BRIEF_LATE_PUSH_MINUTE = 9 * 60

#: The early push waits for the brief to have something in it, but not past
#: this (07:30 ET). By then the premarket tape is real, so "quiet" is an
#: observation rather than "the data has not arrived yet".
MORNING_BRIEF_EARLY_DEADLINE_MINUTE = 7 * 60 + 30'''
assert s.count(OLD) == 1, "late-minute anchor"
s = s.replace(OLD, NEW)

OLD = "from morning_briefing import build_briefing as morning_build_briefing"
NEW = """from morning_briefing import build_briefing as morning_build_briefing
from morning_briefing import has_substance as morning_has_substance
from morning_briefing import push_message as morning_push_message"""
assert s.count(OLD) == 1, "import anchor"
s = s.replace(OLD, NEW)

OLD = '''            if (day_key, slot) not in pushed:
                pushed.add((day_key, slot))
                # Keep the old single-slot attribute in step: other code and
                # the tests still read it as "did today's brief push at all".
                self._brief_pushed_day = day_key
                lines = [str(l) for l in (payload.get("lines") or []) if l][:2]
                if lines:
                    _push_phone_notification(
                        title,
                        " / ".join(lines).replace("**", "") + "  - open AGX for the full brief",
                        tags="newspaper",
                    )'''
NEW = '''            if (day_key, slot) not in pushed:
                # The early slot WAITS for the brief to have something to
                # say. It used to fire on the first build of the window,
                # 05:30 ET, which is the emptiest brief of the day - no
                # premarket quotes, and no Schwab bars before 07:00 - so his
                # phone got "No signals yet / Quiet tape so far" and the
                # morning's early push was spent on it (2026-09-02). Past the
                # deadline it goes out regardless: a quiet tape at 07:30 is a
                # real observation. The 09:00 slot is never held - it is the
                # half-hour-to-the-open brief he asked for.
                ready = (
                    slot == "late"
                    or minute_of_day >= MORNING_BRIEF_EARLY_DEADLINE_MINUTE
                    or morning_has_substance(payload)
                )
                body = morning_push_message(payload) if ready else None
                # The slot is claimed only when a push ACTUALLY goes out, so
                # a held or empty build leaves the slot open for the next one.
                if body:
                    pushed.add((day_key, slot))
                    # Keep the old single-slot attribute in step: other code
                    # and the tests read it as "did today's brief push at all".
                    self._brief_pushed_day = day_key
                    _push_phone_notification(
                        title,
                        body + "  - open AGX for the full brief",
                        tags="newspaper",
                    )'''
assert s.count(OLD) == 1, "push block anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("api_server.py: early push held until the brief has substance")
