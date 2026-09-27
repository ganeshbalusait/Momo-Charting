"""AI triage for the MomX scanner board: "which 5 of these deserve a chart, and why".

The trader's bottleneck is not finding matches, it is that a 355-symbol board can
return 18 of them at 09:31 and he can only chart four or five. This module reads a
FINISHED board (``momx.board.build_board``'s payload) and ranks the matches.

*** IT IS NEVER IN THE SCAN PATH. ***
The scan stays pure deterministic maths so that a disagreement with thinkorswim is
always a real, debuggable bug and never model variance. Nothing here can change a
row, a cell, or a pass/fail verdict -- it only re-orders and explains what the scan
already decided. See docs/momx/SPEC.md, "Phase 3 - AI triage layer".

WHAT IS SENT TO THE MODEL, AND WHY SO LITTLE
--------------------------------------------
A full 355-row board is roughly a megabyte of JSON. Sending it would be slow,
expensive, and mostly noise. The prompt carries ONLY:

* the rows where ``scanPass`` is true -- typically 5-20 of 355,
* per row: ``symbol``, ``industry``, ``pctChange``, ``scanReasons``, and the
  ``rvol`` / ``sqz`` / ``skittles`` VALUES.

Deliberately dropped:

* every colour key on every cell. The model cannot see colour, and those two keys
  are most of the bytes in a cell -- carrying them roughly triples the payload for
  zero information. ``momx.columns`` resolves colour for the browser; the model
  gets the number the colour was derived from.
* ``sparkline`` and ``quoteTrend``. Raw price and direction arrays, tens of numbers
  per row, that the model cannot read better than the indicators already summarise.
* ``last``, ``highLow``, ``color``, ``badge``, and every failing row.

THE HALLUCINATION GUARD IS THE POINT OF THIS MODULE
---------------------------------------------------
A language model asked to rank tickers will occasionally return a ticker that was
never on the board -- a name it associates with the others. On a trading desk that
is the worst possible failure: a confident, well-argued case for charting something
the scanner never flagged. Every returned pick is therefore checked against the set
of symbols actually sent, and anything else is DROPPED, not repaired and not
surfaced. ``dropped`` counts them so the behaviour is visible rather than silent.

NEVER RAISES. No key, a dead network, a malformed response and a board that is not
even a dict all come back as ``available: false`` with a plain-English ``reason``
that the panel renders as-is. ``available: false`` is a normal result, not an error.
"""

from __future__ import annotations

import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

__all__ = [
    "triage",
    "compact_rows",
    "build_prompt",
    "PICKS_SCHEMA",
    "SYSTEM_PROMPT",
    "DEFAULT_LIMIT",
]

#: How many charts the trader can actually look at in one sitting.
DEFAULT_LIMIT = 5

#: Hard ceiling on how many matches are described to the model. A board that
#: returns 200 matches is a broken scan, not a busy morning; truncating keeps one
#: bad build from turning into one enormous bill.
MAX_ROWS_SENT = 60

#: ``why`` / ``risk`` are ONE short sentence each. Anything longer is truncated
#: rather than dropped -- a clipped sentence is still readable, a missing one is not.
MAX_SENTENCE_CHARS = 180

_TOKEN_LIMIT = 1400

_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s")
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

#: Cell blocks whose VALUES are forwarded (colour keys stripped).
_CELL_BLOCKS = ("rvol", "sqz", "skittles")


SYSTEM_PROMPT = """\
You are triaging a momentum scanner for one experienced discretionary trader. He \
already trusts the scan: every symbol you are given passed it. His problem is time \
-- the scan returned more names than he can pull up charts for before the open.

Your job is to RANK the matches and say, in one line each, what is interesting and \
what is wrong with each one. You are choosing which charts he opens. You are not \
choosing trades.

Rules, in order of importance:

1. Use only the numbers you are given. Never state a number, level, date, earnings \
event or piece of news that is not in the data. If you want to say a move is large, \
say it with the number that is in front of you.
2. No trading advice. No buy, sell, long, short, entry, exit, stop, size or price \
target. No prediction of where price goes next. You rank and explain; you do not trade.
3. Commit to your reading. No hedging, no "may", "could", "potentially", "it is \
possible that", and no disclaimers. If the evidence is thin, say plainly that it is \
thin -- that is a judgement, not a hedge.
4. One short sentence for "why" and one short sentence for "risk". Plain English, no \
jargon the data does not already use, no restating raw field names back at him.
5. Rank strongest first, starting at 1. Return only symbols that appear in the data.

A good "why" names the agreement across timeframes that makes the setup worth a \
chart. A good "risk" names the specific thing in THIS row that could make it a bad \
chart -- an extended stochastic, a signal on only one timeframe, ordinary relative \
volume, a squeeze that fired many bars ago."""


#: The response contract. Requested through ``schema=`` so picks come back as
#: structured JSON; prose would have to be parsed, and a parser for model prose is
#: a bug generator.
PICKS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "symbol": {
                        "type": "string",
                        "description": "Ticker, exactly as spelled in the data.",
                    },
                    "rank": {
                        "type": "integer",
                        "description": "1 is the chart to open first.",
                    },
                    "why": {
                        "type": "string",
                        "description": "One short sentence: what makes this worth a chart.",
                    },
                    "risk": {
                        "type": "string",
                        "description": "One short sentence: what is wrong with it.",
                    },
                },
                "required": ["symbol", "rank", "why", "risk"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Board -> prompt payload
# ---------------------------------------------------------------------------

def _rows_of(board_payload: Any) -> list[Mapping[str, Any]]:
    """The row list, from a board payload, a bare row list, or anything else."""
    rows: Any = board_payload
    if isinstance(board_payload, Mapping):
        rows = board_payload.get("rows")
        if rows is None:
            nested = board_payload.get("board")
            rows = nested.get("rows") if isinstance(nested, Mapping) else None
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _number(value: Any, digits: int = 2) -> float | int | None:
    """A JSON-safe rounded number, or ``None``. NaN and inf never reach the model."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    rounded = round(number, digits)
    return int(rounded) if rounded == int(rounded) else rounded


def _cell_values(block: Any) -> dict:
    """``{tf: {"value": v, <two colour keys>}}`` -> ``{tf: v}``, nulls dropped.

    This is where the colour keys die. They are never copied out, so no later step
    has to remember to strip them.
    """
    if not isinstance(block, Mapping):
        return {}
    out: dict[str, Any] = {}
    for key, cell in block.items():
        value = cell.get("value") if isinstance(cell, Mapping) else cell
        if value is None or isinstance(value, bool):
            continue
        if isinstance(value, str):
            text = value.strip()
            if text and text != "-":
                out[str(key)] = text
            continue
        number = _number(value)
        if number is not None:
            out[str(key)] = number
    return out


def compact_rows(board_payload: Any, *, max_rows: int = MAX_ROWS_SENT) -> list[dict]:
    """The scan matches, reduced to the only fields worth spending tokens on.

    Rows where ``scanPass`` is not true are not included -- a failing row is not a
    candidate for a chart, and sending the other 337 of them would be the entire
    cost of the call.
    """
    ceiling = max(1, int(max_rows or MAX_ROWS_SENT))
    compacted: list[dict] = []
    for row in _rows_of(board_payload):
        if not row.get("scanPass"):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if not symbol:
            continue
        entry: dict[str, Any] = {"symbol": symbol}
        industry = row.get("industry")
        if isinstance(industry, str) and industry.strip():
            entry["industry"] = industry.strip()
        pct = _number(row.get("pctChange"))
        if pct is not None:
            entry["pctChange"] = pct
        reasons = row.get("scanReasons")
        if isinstance(reasons, Sequence) and not isinstance(reasons, (str, bytes)):
            entry["scanReasons"] = [str(item) for item in reasons if item]
        else:
            entry["scanReasons"] = []
        for block in _CELL_BLOCKS:
            values = _cell_values(row.get(block))
            if values:
                entry[block] = values
        compacted.append(entry)
        if len(compacted) >= ceiling:
            break
    return compacted


def build_prompt(rows: Sequence[Mapping[str, Any]], limit: int) -> str:
    """The user message: what the signals MEAN, then the data.

    The glossary is not padding. ``sqzfired:2h`` and a relative-volume Z-SCORE of
    2.4 are thinkorswim conventions; a model with no context reads the Z-score as a
    ratio ("2.4x normal volume") and is then confidently wrong about the one number
    the trader cares most about.
    """
    count = len(rows)
    data = json.dumps(list(rows), separators=(",", ":"), sort_keys=False)
    return f"""\
{count} symbol(s) passed the trader's momentum scan just now. Pick the {limit} that \
most deserve a chart.

HOW TO READ THE DATA

pctChange -- percent change on the day.

scanReasons -- the exact scanner conditions that fired, each with its timeframe \
after the colon:
  macd:4h      MACD(6,12,8) crossed above its signal line on the 4-hour chart
  ema9x20:D    the 9 EMA crossed above the 20 EMA on the daily chart
  ema4x8:2h    the 4 EMA crossed above the 8 EMA on the 2-hour chart
  sqzfired:D   a volatility squeeze released on the daily chart
  rvol:30m     the relative-volume condition fired on the 30-minute chart
Timeframes run 5m, 15m, 30m, 1h, 2h, 4h, D (day), 2D/3D/4D (multi-day), Wk, M. \
Several conditions firing across SEVERAL timeframes is agreement and counts for more \
than several firing on one timeframe. Slower timeframes carry more weight than faster \
ones.

rvol -- relative volume as a Z-SCORE, not a ratio, one per timeframe. 0 is an \
ordinary session. 2 is two standard deviations above normal and is notable. 4 is \
extreme. A value of 2 does NOT mean twice normal volume.

skittles -- a 0-to-100 weighted stochastic, one per timeframe. Near 100 the move is \
already extended; near 0 it is washed out. Middle readings have the most room left.

sqz -- how the squeeze reads on that timeframe. A number is how many bars ago the \
squeeze fired, so smaller is fresher. A leading star means a HIGH-compression \
squeeze: "*2" is a high-compression squeeze that fired 2 bars ago.

DATA

{data}

Return the {limit} strongest as ranked picks. Rank 1 is the chart he opens first. \
Return fewer than {limit} if fewer than {limit} are genuinely worth his time."""


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

_NO_PROVIDER_MESSAGE = (
    "No AI key set. Add OPENAI_API_KEY to .env to turn on scanner triage."
)


def _resolve_provider(provider: Any) -> tuple[Any, str]:
    """``(provider, reason)``. A non-empty reason means no usable provider."""
    if provider is None:
        try:
            # Imported here, never at module import time: the SDKs may not be
            # installed and importing this module must stay free.
            from agents.ai_provider import get_provider
        except Exception:  # noqa: BLE001 - no module, no SDK, no problem
            return None, _NO_PROVIDER_MESSAGE
        try:
            provider = get_provider()
        except Exception as exc:  # noqa: BLE001
            return None, f"AI is not available right now ({type(exc).__name__})."
        if provider is None:
            return None, _status_message()
    try:
        is_available = bool(provider.available())
    except Exception:  # noqa: BLE001 - a provider that cannot answer is not usable
        is_available = False
    if not is_available:
        return None, _status_message()
    return provider, ""


def _status_message() -> str:
    """The provider layer's own trader-facing message, if it has one."""
    try:
        from agents.ai_provider import provider_status

        status = provider_status()
        message = status.get("message") if isinstance(status, Mapping) else None
        if isinstance(message, str) and message.strip():
            return message.strip()
    except Exception:  # noqa: BLE001
        pass
    return _NO_PROVIDER_MESSAGE


def _provider_name(provider: Any) -> str | None:
    name = getattr(provider, "name", None)
    return name if isinstance(name, str) and name else None


# ---------------------------------------------------------------------------
# Response -> picks
# ---------------------------------------------------------------------------

def _one_sentence(value: Any) -> str:
    """One short sentence: whitespace collapsed, first sentence kept, then clipped."""
    if not isinstance(value, str):
        return ""
    text = _WHITESPACE_RE.sub(" ", value).strip()
    if not text:
        return ""
    first = _SENTENCE_END_RE.split(text, maxsplit=1)[0].strip()
    if len(first) > MAX_SENTENCE_CHARS:
        first = first[: MAX_SENTENCE_CHARS - 1].rstrip() + "…"
    return first


def _payload_picks(result: Mapping[str, Any]) -> list | None:
    """The picks array out of ``data``, or out of ``text`` if the model sent prose."""
    data = result.get("data")
    if data is None:
        text = result.get("text")
        if isinstance(text, str) and text.strip():
            try:
                data = json.loads(_FENCE_RE.sub("", text.strip()))
            except (TypeError, ValueError):
                return None
    if isinstance(data, Mapping):
        picks = data.get("picks")
        if picks is None:
            picks = data.get("results")
        data = picks
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes)):
        return list(data)
    return None


def _rank_key(entry: Any, position: int) -> tuple[int, int]:
    """Sort by the model's own rank when it gave a usable one, else keep its order."""
    if isinstance(entry, Mapping):
        try:
            rank = int(entry.get("rank"))
        except (TypeError, ValueError):
            rank = 0
        if rank > 0:
            return (rank, position)
    return (10_000 + position, position)


def _clean_picks(
    raw: Sequence[Any], allowed: Mapping[str, str], limit: int
) -> tuple[list[dict], int]:
    """Validated picks, and the count of picks dropped as not-on-the-board.

    ``allowed`` maps upper-case symbol -> the spelling that was sent. A pick for
    anything outside it is discarded: the model has invented a ticker, and a
    ranked, well-argued case for charting a name the scanner never flagged is the
    single most dangerous thing this module could hand a trader.
    """
    ordered = sorted(enumerate(raw), key=lambda pair: _rank_key(pair[1], pair[0]))
    picks: list[dict] = []
    seen: set[str] = set()
    dropped = 0
    for _, entry in ordered:
        if isinstance(entry, Mapping):
            symbol_raw = entry.get("symbol") or entry.get("ticker")
            why = _one_sentence(entry.get("why") or entry.get("reason"))
            risk = _one_sentence(entry.get("risk"))
        elif isinstance(entry, str):
            symbol_raw, why, risk = entry, "", ""
        else:
            dropped += 1
            continue
        symbol = str(symbol_raw or "").strip().upper()
        if not symbol or symbol not in allowed:
            dropped += 1
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        if len(picks) >= limit:
            continue
        picks.append(
            {
                "symbol": allowed[symbol],
                "rank": len(picks) + 1,
                "why": why,
                "risk": risk,
            }
        )
    return picks, dropped


# ---------------------------------------------------------------------------
# The one public entry point
# ---------------------------------------------------------------------------

def _generated_at() -> str:
    return datetime.now(timezone.utc).isoformat()


def _unavailable(reason: str, provider_name: str | None = None, **extra: Any) -> dict:
    payload: dict[str, Any] = {
        "available": False,
        "reason": reason,
        "generatedAt": _generated_at(),
        "provider": provider_name,
        "picks": [],
        "skipped": 0,
        "matches": 0,
        "dropped": 0,
    }
    payload.update(extra)
    return payload


def triage(board_payload: dict, *, limit: int = DEFAULT_LIMIT, provider: Any = None) -> dict:
    """Rank the scan matches on a finished MomX board: which few deserve a chart.

    ``board_payload`` is ``momx.board.build_board``'s document (a bare list of rows
    is accepted too). ``limit`` is how many picks to return. ``provider`` is both
    the test seam and the override -- when omitted,
    ``agents.ai_provider.get_provider()`` chooses.

    Returns the standard feature contract::

        {"available": bool, "reason": str, "generatedAt": iso, "provider": str|None,
         "picks": [{"symbol", "rank", "why", "risk"}], "skipped": int,
         "matches": int, "dropped": int}

    ``skipped`` is how many matches were considered and not picked -- the count the
    trader is trusting the model to have thrown away on his behalf. ``dropped``
    counts picks rejected because their symbol was not on the board.

    NOTE ON ORDERING: a board with ZERO matches short-circuits BEFORE the provider
    is consulted and returns ``available: true`` with no picks. "Nothing passed the
    scan" is a complete and truthful answer that costs nothing to give, and telling
    the trader his AI key is missing when there was nothing to rank anyway would be
    noise.
    """
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT

    try:
        rows = compact_rows(board_payload)
    except Exception as exc:  # noqa: BLE001 - a malformed board is not a crash
        return _unavailable(f"The scanner board could not be read ({type(exc).__name__}).")

    matches = len(rows)
    if matches == 0:
        return {
            "available": True,
            "reason": "No symbols passed the scan, so there is nothing to triage.",
            "generatedAt": _generated_at(),
            "provider": None,
            "picks": [],
            "skipped": 0,
            "matches": 0,
            "dropped": 0,
        }

    resolved, reason = _resolve_provider(provider)
    if resolved is None:
        return _unavailable(reason, matches=matches, skipped=matches)

    name = _provider_name(resolved)
    prompt = build_prompt(rows, limit or DEFAULT_LIMIT)

    try:
        result = resolved.complete(
            system=SYSTEM_PROMPT,
            user=prompt,
            max_tokens=_TOKEN_LIMIT,
            schema=PICKS_SCHEMA,
        )
    except Exception as exc:  # noqa: BLE001 - the contract says complete() never
        # raises; this module still has to survive a provider that breaks it.
        return _unavailable(
            f"AI triage failed: {type(exc).__name__}: {exc}",
            name,
            matches=matches,
            skipped=matches,
        )

    if not isinstance(result, Mapping):
        return _unavailable(
            "AI triage returned an unreadable response.", name, matches=matches, skipped=matches
        )

    if isinstance(result.get("provider"), str) and result.get("provider"):
        name = result["provider"]

    if not result.get("ok"):
        error = str(result.get("error") or "").strip() or "the AI provider did not answer"
        return _unavailable(
            f"AI triage failed: {error}", name, matches=matches, skipped=matches, error=error
        )

    raw = _payload_picks(result)
    if raw is None:
        return _unavailable(
            "AI triage returned no usable picks.", name, matches=matches, skipped=matches
        )

    allowed = {row["symbol"]: row["symbol"] for row in rows}
    picks, dropped = _clean_picks(raw, allowed, limit)

    return {
        "available": True,
        "reason": "",
        "generatedAt": _generated_at(),
        "provider": name,
        "model": result.get("model") if isinstance(result.get("model"), str) else None,
        "picks": picks,
        "skipped": max(0, matches - len(picks)),
        "matches": matches,
        "dropped": dropped,
    }
