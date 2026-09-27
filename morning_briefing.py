"""Morning briefing: five plain-English lines before the open.

Turns the structures the backend already has (premarket scanner rows with
catalysts, watchlist quotes) into short sentences a trader reads on a phone
in ten seconds. Deliberately template-generated, not an LLM: every line is
traceable to a number the app measured, so the briefing can never invent a
setup that is not on the chart.

Stdlib-only and pure (all inputs injected), like premarket_scanner.py, so
tests can pin every sentence.
"""
from __future__ import annotations

from datetime import datetime

# A watchlist name only counts as a "mover" past this premarket change.
MOVER_THRESHOLD_PCT = 2.0
MAX_GAINERS = 3
MAX_LOSERS = 2

#: A safety ceiling on the notification body, NOT a design target. A phone
#: expands a notification, so the whole brief is readable; this only stops a
#: pathological payload (a runaway headline) from being pushed whole. He
#: asked for the untruncated brief explicitly on 2026-09-02.
#:
#: 2000, not 1200: real briefs out of the archive measure 448-847 characters
#: and a 20k-day simulation over this project's own captured headline lengths
#: puts p95 near 1000, so 1200 was close enough for a newsy morning to reach.
#: ntfy carries far more than either number.
PUSH_BUDGET_CHARS = 2000

#: The regular session opens at 09:30 ET.
MARKET_OPEN_MINUTE = 9 * 60 + 30


def _pct(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result


def _fmt_pct(value: float) -> str:
    return f"{value:+.1f}%"


def market_line(quotes: dict) -> str | None:
    """One line on SPY/QQQ so the trader knows which way the tape leans."""
    parts: list[str] = []
    changes: list[float] = []
    for symbol in ("SPY", "QQQ"):
        change = _pct((quotes.get(symbol) or {}).get("change_pct"))
        if change is None:
            continue
        parts.append(f"{symbol} {_fmt_pct(change)}")
        changes.append(change)
    if not parts:
        return None
    average = sum(changes) / len(changes)
    if average >= 0.3:
        lean = "tape leans bullish"
    elif average <= -0.3:
        lean = "tape leans bearish"
    else:
        lean = "tape is flat"
    return f"{', '.join(parts)} premarket - {lean}."


def _catalyst_phrase(catalyst: object) -> str | None:
    if not isinstance(catalyst, dict):
        return None
    headline = str(catalyst.get("headline") or "").strip()
    if not headline:
        return None
    tag = str(catalyst.get("tag") or "NEWS").strip()
    age = catalyst.get("ageMinutes")
    if isinstance(age, (int, float)) and age >= 0:
        hours = int(age // 60)
        when = f"{hours}h ago" if hours >= 1 else f"{int(age)}m ago"
        return f"{tag}: {headline} ({when})"
    return f"{tag}: {headline}"


def _row_signal_phrase(row: dict) -> str:
    pieces: list[str] = []
    cyan = [str(s) for s in (row.get("signals920") or [])]
    yellow = [str(s) for s in (row.get("signals48") or [])]
    if cyan:
        pieces.append(f"{'+'.join(cyan)} cyan")
    if yellow:
        pieces.append(f"{'+'.join(yellow)} yellow")
    fires = [str(f) for f in (row.get("fires") or [])]
    if fires:
        pieces.append(" ".join(f"fire {f}" for f in fires))
    return ", ".join(pieces) if pieces else "signals"


def scanner_lines(scanner_payload: object) -> list[str]:
    """The scanner story: the strongest setup in full, the rest in one line."""
    rows = (scanner_payload or {}).get("rows") if isinstance(scanner_payload, dict) else None
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    if not rows:
        return ["No signals on the nine scanner tickers yet."]
    lead = rows[0]  # payload is already sorted strongest-first
    forming = " (still forming - can repaint)" if lead.get("forming") else ""
    line = (
        f"Strongest setup: {lead.get('symbol')} - {_row_signal_phrase(lead)}, "
        f"{str(lead.get('strength') or '').upper()} ({lead.get('score')}){forming}."
    )
    catalyst = _catalyst_phrase(lead.get("catalyst"))
    lines = [line + (f" Catalyst - {catalyst}" if catalyst else " No fresh catalyst found.")]
    rest = rows[1:]
    if rest:
        summary = ", ".join(
            f"{row.get('symbol')} ({str(row.get('strength') or '').upper()} {row.get('score')}"
            + (", forming" if row.get("forming") else "")
            + ")"
            for row in rest[:5]
        )
        more = f" and {len(rest) - 5} more" if len(rest) > 5 else ""
        lines.append(f"Also set up: {summary}{more}.")
    return lines


def mover_lines(quotes: dict, catalysts: dict | None = None,
                exclude: set | None = None) -> list[str]:
    """Watchlist names moving hardest premarket, with a catalyst when known."""
    skip = {str(s).upper() for s in (exclude or set())}
    movers: list[tuple[str, float]] = []
    for symbol, quote in (quotes or {}).items():
        name = str(symbol).upper()
        if name in skip or name in ("SPY", "QQQ"):
            continue
        change = _pct((quote or {}).get("change_pct"))
        if change is None or abs(change) < MOVER_THRESHOLD_PCT:
            continue
        movers.append((name, change))
    if not movers:
        return []
    gainers = sorted((m for m in movers if m[1] > 0), key=lambda m: -m[1])[:MAX_GAINERS]
    losers = sorted((m for m in movers if m[1] < 0), key=lambda m: m[1])[:MAX_LOSERS]

    def describe(name: str, change: float) -> str:
        # The ticker is wrapped in ** ** so the briefing renderer can bold it.
        # Marker rather than a structured line object on purpose: `lines` stays
        # a list of plain strings, so nothing that already consumes this payload
        # has to change. The renderer treats an unmatched ** as literal text.
        catalyst = _catalyst_phrase((catalysts or {}).get(name))
        return f"**{name}** {_fmt_pct(change)}" + (f" ({catalyst})" if catalyst else "")

    lines: list[str] = []
    if gainers:
        lines.append("Watchlist gainers: " + ", ".join(describe(*m) for m in gainers) + ".")
    if losers:
        lines.append("Watchlist losers: " + ", ".join(describe(*m) for m in losers) + ".")
    return lines


def watch_line(scanner_payload: object, quotes: dict) -> str:
    """The single 'if you only read one line' sentence."""
    rows = (scanner_payload or {}).get("rows") if isinstance(scanner_payload, dict) else None
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    if rows:
        lead = rows[0]
        has_catalyst = isinstance(lead.get("catalyst"), dict) and lead["catalyst"].get("headline")
        if has_catalyst:
            return (f"Watch {lead.get('symbol')} at the open - "
                    "signals and news agree.")
        return (f"Watch {lead.get('symbol')} at the open - strongest signals, "
                "but no news behind the move yet.")
    best = None
    for symbol, quote in (quotes or {}).items():
        name = str(symbol).upper()
        if name in ("SPY", "QQQ"):
            continue
        change = _pct((quote or {}).get("change_pct"))
        if change is not None and abs(change) >= MOVER_THRESHOLD_PCT:
            if best is None or abs(change) > abs(best[1]):
                best = (name, change)
    if best:
        return f"No scanner signals yet - biggest watchlist move is {best[0]} {_fmt_pct(best[1])}."
    return "Quiet tape so far - no scanner signals and no big watchlist moves."


def build_briefing(scanner_payload: object, quotes: dict, now_et: datetime,
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
            # The matched symbols on their own, for the push's "Scanner:"
            # footer. Board order, which is strongest-first.
            "scannerSymbols": [
                str(row.get("symbol") or "").upper()
                for row in real_rows
                if str(row.get("symbol") or "").strip()
            ],
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


def countdown_phrase(now_et: object) -> str:
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


def push_candidates(payload: object) -> list[str]:
    """The brief's lines in PUSH order: what he would act on comes first.

    THE APP'S ORDER: tape, strongest setup, gainers, losers. It briefly was
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


def push_message(payload: object, *, now_et: object = None,
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
        return "\n\n".join(["\n".join(body_lines)] + tail)

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
    return body[: max(1, budget - 1)].rstrip() + "\u2026"
