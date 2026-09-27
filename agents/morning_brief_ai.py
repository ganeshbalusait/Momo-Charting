"""AI synthesis layer on top of the deterministic morning briefing.

`morning_briefing.build_briefing` is the source of truth and does not change:
every one of its lines is traceable to a number the app measured, which is why
it is template-generated rather than written by a model. This module takes
those finished lines as INPUT and adds one short paragraph on top - "what today
looks like and what to watch" - plus at most three symbols worth watching.

The deterministic lines are still rendered, and the paragraph is returned with
`label` = AI_LABEL so the UI can mark it as model-written. The trader must
always be able to tell which half of the screen was measured and which half was
written by an AI.

Guard rails, because a model that invents a level is worse than no paragraph:

* The system prompt forbids inventing prices, levels and predictions.
* Every symbol the model returns is checked against the symbols that actually
  appear in the input, and dropped when it does not. A hallucinated ticker
  never reaches the trader.
* No SDK is imported at module import time; `agents.ai_provider` is imported
  lazily inside the call so this module loads (and its tests run) with no keys
  and no SDK installed.

Stdlib-only and pure - every input is injected, so tests can pin the prompt.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone

# Shown next to the paragraph so the trader always knows which lines were
# measured and which were written by a model.
AI_LABEL = "AI summary - written by a model from the lines above, not measured data."

MAX_WATCHLIST = 3

SYSTEM_PROMPT = """You write one short morning note for an active options trader.

You are given the app's own briefing lines, its premarket scanner rows, and
sometimes open-interest context. Those lines were measured from live market
data. Your note sits underneath them and is labelled as AI-written.

These rules are absolute:
1. Never invent a price, a level, a strike, a percentage, or any other number.
   You may only repeat numbers that appear in the input, exactly as written.
2. Never predict where anything will go. No targets, no stops, no price
   forecasts, no buy or sell instructions. Describe what the data already shows
   and what the trader should watch.
3. Every symbol you name MUST appear in the input. If you are not certain a
   symbol is in the input, leave it out. Never add a ticker from memory.
4. Do not contradict the input lines and do not add facts, catalysts or news
   that are not in them.
5. Plain English a trader reads in ten seconds. No markdown, no ** markers, no
   bullet points, no headings.

Reply with JSON only:
{"paragraph": "3 to 4 sentences", "watchlist": ["SYM", "SYM"]}

`watchlist` holds at most 3 symbols, most interesting first, and every one of
them must appear in the input."""

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "paragraph": {
            "type": "string",
            "description": "3-4 sentences: what today looks like and what to watch.",
        },
        "watchlist": {
            "type": "array",
            "maxItems": MAX_WATCHLIST,
            "items": {"type": "string"},
            "description": "At most 3 symbols, each one appearing in the input.",
        },
    },
    "required": ["paragraph", "watchlist"],
}

# Upper-case tokens the deterministic briefing and scanner emit that are words,
# not tickers. Keeping them out of the allowed set means the model cannot smuggle
# a fake ticker in by echoing a headline word. Erring toward dropping a real
# symbol is deliberate: a missing name is a nuisance, an invented one is a trade.
_NOT_TICKERS = {
    "A", "AI", "AM", "AMC", "AN", "AND", "AT", "ATM", "BE", "BMO", "BUT", "BY",
    "CALL", "CEO", "CFO", "CPI", "EDT", "EM", "EPS", "ESG", "EST", "ET", "ETF",
    "EU", "FDA", "FOR", "GDP", "IF", "IN", "IPO", "IS", "IT", "ITM", "IV", "NEW",
    "NEWS", "NO", "NOT", "NOW", "OF", "OI", "ON", "OR", "OTM", "PM", "PUT",
    "Q1", "Q2", "Q3", "Q4", "SEC", "THE", "TO", "UK", "US", "USA", "WEAK", "YES",
}

_TICKER_TOKEN = re.compile(r"\b[A-Z]{1,5}\b")
_BOLD_TICKER = re.compile(r"\*\*([A-Za-z0-9.\-]{1,6})\*\*")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def strip_markers(text: object) -> str:
    """Drop the ** ** bold markers the briefing renderer uses.

    The markers are a UI instruction, not content. Sending them to the model
    invites it to copy them back out, and the paragraph is rendered as plain
    text, so they would show up literally on the trader's screen.
    """
    cleaned = str(text or "").replace("**", "")
    return re.sub(r"[ \t]+", " ", cleaned).strip()


def _result(available: bool, reason: str, *, provider: str | None = None,
            paragraph: str = "", watchlist: list[str] | None = None) -> dict:
    """The contract shape every AI feature module returns."""
    return {
        "available": available,
        "reason": reason,
        "generatedAt": _now_iso(),
        "provider": provider,
        "paragraph": paragraph,
        "watchlist": list(watchlist or []),
        "label": AI_LABEL,
    }


def _default_provider():
    """Resolve the shared provider, tolerating a repo with no AI wiring yet.

    Imported here rather than at module scope so importing this module never
    pulls in an SDK, and so the app still boots when `agents/ai_provider.py`
    is absent.
    """
    try:
        from agents.ai_provider import get_provider
    except Exception:
        return None
    try:
        return get_provider()
    except Exception:
        return None


def _provider_reason() -> str:
    """A plain-English 'why not' for the trader when no model is configured."""
    try:
        from agents.ai_provider import provider_status

        message = str((provider_status() or {}).get("message") or "").strip()
        if message:
            return message
    except Exception:
        pass
    return "No AI key set. Add OPENAI_API_KEY to .env to turn the AI summary on."


def _briefing_lines(briefing: object) -> list[str]:
    if not isinstance(briefing, dict):
        return []
    return [strip_markers(line) for line in (briefing.get("lines") or []) if strip_markers(line)]


def _scanner_rows(scanner_payload: object) -> list[dict]:
    rows = scanner_payload.get("rows") if isinstance(scanner_payload, dict) else None
    return [row for row in (rows or []) if isinstance(row, dict)]


def _symbols_in_text(text: str) -> set[str]:
    found = {match.upper() for match in _BOLD_TICKER.findall(text or "")}
    for token in _TICKER_TOKEN.findall(strip_markers(text)):
        if token not in _NOT_TICKERS:
            found.add(token)
    return found


def _collect_symbols(node: object, into: set) -> None:
    """Pull every 'symbol'-ish value out of an arbitrary nested payload."""
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str) and str(key).lower() in ("symbol", "ticker", "underlying"):
                cleaned = value.strip().upper().lstrip("$")
                if cleaned:
                    into.add(cleaned)
            else:
                _collect_symbols(value, into)
    elif isinstance(node, list):
        for item in node:
            _collect_symbols(item, into)


def allowed_symbols(briefing: object, scanner_payload: object,
                    oi_context: object = None) -> set:
    """Every symbol that genuinely appears in the input.

    This set is the hallucination guard: anything the model names that is not
    in here never reaches the trader.
    """
    symbols: set = set()
    for line in _briefing_lines(briefing):
        symbols |= _symbols_in_text(line)
    for line in (briefing.get("lines") if isinstance(briefing, dict) else None) or []:
        symbols |= {match.upper() for match in _BOLD_TICKER.findall(str(line))}
    for row in _scanner_rows(scanner_payload):
        name = str(row.get("symbol") or "").strip().upper()
        if name:
            symbols.add(name)
    _collect_symbols(oi_context, symbols)
    return {symbol for symbol in symbols if symbol}


def _row_summary(row: dict) -> str:
    bits = [str(row.get("symbol") or "?")]
    strength = str(row.get("strength") or "").upper()
    if strength:
        bits.append(f"{strength} {row.get('score')}")
    cyan = [str(s) for s in (row.get("signals920") or [])]
    yellow = [str(s) for s in (row.get("signals48") or [])]
    fires = [str(f) for f in (row.get("fires") or [])]
    if cyan:
        bits.append("cyan " + "+".join(cyan))
    if yellow:
        bits.append("yellow " + "+".join(yellow))
    if fires:
        bits.append("fires " + "+".join(fires))
    if row.get("forming"):
        bits.append("still forming")
    catalyst = row.get("catalyst")
    if isinstance(catalyst, dict) and catalyst.get("headline"):
        bits.append("catalyst " + strip_markers(catalyst.get("headline")))
    return " | ".join(bits)


def build_prompt(briefing: object, scanner_payload: object,
                 oi_context: object = None) -> str:
    """The user half of the call. Markers stripped, nothing invented."""
    sections: list[str] = []
    date = str((briefing or {}).get("date") or "") if isinstance(briefing, dict) else ""
    if date:
        sections.append(f"Date: {date}")
    lines = _briefing_lines(briefing)
    sections.append("Briefing lines the app measured:\n" + "\n".join(f"- {line}" for line in lines))
    rows = _scanner_rows(scanner_payload)
    if rows:
        sections.append(
            "Premarket scanner rows (strongest first):\n"
            + "\n".join(f"- {_row_summary(row)}" for row in rows[:9])
        )
    if oi_context:
        try:
            blob = json.dumps(oi_context, default=str)[:1500]
        except Exception:
            blob = str(oi_context)[:1500]
        sections.append("Open-interest context:\n" + strip_markers(blob))
    sections.append(
        "Symbols you are allowed to name: "
        + ", ".join(sorted(allowed_symbols(briefing, scanner_payload, oi_context)))
    )
    sections.append(
        "Write the JSON note now. Repeat no number that is not above, predict nothing, "
        "and name no symbol that is not in the allowed list."
    )
    return strip_markers("\n\n".join(sections))


def _clean_watchlist(raw: object, allowed: set) -> list[str]:
    """Keep order, drop anything not in the input, cap at three."""
    picked: list[str] = []
    for item in (raw if isinstance(raw, (list, tuple)) else []):
        symbol = str(item or "").strip().upper().lstrip("$")
        symbol = re.sub(r"[^A-Z0-9.\-]", "", symbol)
        if not symbol or symbol not in allowed or symbol in picked:
            continue
        picked.append(symbol)
        if len(picked) >= MAX_WATCHLIST:
            break
    return picked


def _payload_from(response: dict) -> dict:
    data = response.get("data")
    if isinstance(data, dict):
        return data
    text = str(response.get("text") or "").strip()
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            return {}
        if isinstance(parsed, dict):
            return parsed
        return {}
    return {"paragraph": text} if text else {}


def synthesize(briefing: dict, scanner_payload: dict, oi_context: dict | None = None,
               *, provider=None) -> dict:
    """Add one AI paragraph on top of the deterministic briefing.

    Returns the standard contract shape plus `paragraph` and `watchlist`.
    `available: false` with a readable `reason` is a normal result - no key, a
    briefing that has not been built yet, or a model call that failed all land
    there. Nothing in here raises.
    """
    lines = _briefing_lines(briefing)
    status = str((briefing or {}).get("status") or "").upper() if isinstance(briefing, dict) else ""
    if not lines or status == "WAITING":
        # No model call at all: there is nothing measured to summarise yet, and
        # a paragraph about an empty screen is worse than no paragraph.
        return _result(
            False,
            "The morning briefing has not been built yet - "
            "it runs weekday mornings 5:30-9:35 AM ET.",
        )

    if provider is None:
        provider = _default_provider()
    if provider is None:
        return _result(False, _provider_reason())
    try:
        usable = bool(provider.available())
    except Exception:
        usable = False
    if not usable:
        return _result(False, _provider_reason(), provider=getattr(provider, "name", None))

    name = getattr(provider, "name", None)
    prompt = build_prompt(briefing, scanner_payload, oi_context)
    try:
        response = provider.complete(
            system=SYSTEM_PROMPT, user=prompt, max_tokens=700, schema=RESPONSE_SCHEMA
        )
    except Exception as exc:  # a provider is contracted not to raise; belt and braces
        return _result(False, f"The AI summary could not be written: {exc}", provider=name)
    if not isinstance(response, dict) or not response.get("ok"):
        error = str((response or {}).get("error") or "").strip() if isinstance(response, dict) else ""
        return _result(
            False,
            f"The AI summary could not be written: {error}" if error
            else "The AI summary could not be written.",
            provider=name,
        )
    name = str(response.get("provider") or "") or name

    payload = _payload_from(response)
    paragraph = strip_markers(payload.get("paragraph"))
    paragraph = re.sub(r"\s*\n\s*", " ", paragraph).strip()
    if not paragraph:
        return _result(False, "The AI returned an empty summary.", provider=name)
    allowed = allowed_symbols(briefing, scanner_payload, oi_context)
    return _result(
        True,
        "",
        provider=name,
        paragraph=paragraph,
        watchlist=_clean_watchlist(payload.get("watchlist"), allowed),
    )
