"""Why is this ticker moving? Tie a mover to the headline that actually explains it.

The trader sees "CRWD +20%" on the scanner and wants one sentence he can act
on. This module writes that sentence -- but only when the news in hand really
does explain the move.

Design rules, in the order they matter:

1. HONESTY OVER COVERAGE. An invented explanation for an unexplained 20% move
   is worse than no feature at all: it makes the trader confident about a move
   he does not understand. When the headlines do not explain the move the
   answer is category UNKNOWN, confidence low, and "No news explains this
   move." That instruction is in the system prompt AND enforced here after the
   model answers, because a prompt is a request and this is a requirement.
2. NO I/O. ``explain()`` takes headlines as an argument, so it is testable and
   can never add a network dependency to a caller. ``cached_headlines()`` is
   the thin helper that reads what the server's existing news pipeline
   (catalyst_news.CatalystCache, filled by the background refresher thread)
   has already fetched. No new news provider is introduced.
3. NO SDK IMPORT AT MODULE LEVEL. The provider is resolved lazily through
   agents.ai_provider. With no key configured the result is
   ``available: false`` with a plain-English reason -- a normal result, not an
   error. Nothing in this module raises.
4. NO MODEL CALL WITH NOTHING TO SAY. Zero headlines cannot explain anything,
   so the UNKNOWN verdict is produced locally. We do not pay a model to tell
   us it does not know.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

# The categories the UI knows how to render. Anything else the model invents is
# clamped to UNKNOWN rather than passed through.
CATEGORIES: tuple[str, ...] = (
    "EARNINGS", "UPGRADE", "DOWNGRADE", "CONTRACT", "PRODUCT", "FDA", "DEAL", "OFFERING", "LEGAL", "MACRO", "UNKNOWN",
)
CONFIDENCES: tuple[str, ...] = ("high", "medium", "low")
# Which way the news pushes the stock, judged on the NEWS, not on the tape
# (2026-09-24, the scanner's NEWS tag: a stock offering is bearish even on a
# green day). UNKNOWN is always neutral.
DIRECTIONS: tuple[str, ...] = ("bullish", "bearish", "neutral")

# The sentence shown when nothing in the news explains the move. Kept as a
# constant so the prompt, the local no-headlines path, and the tests all say
# exactly the same thing.
NO_NEWS_SUMMARY = "No news explains this move."

# More than this and the prompt is mostly noise; the news pipeline already
# ranks headlines newest-and-most-relevant first.
MAX_HEADLINES = 8

# A one-sentence answer. Anything longer is truncated rather than rejected.
MAX_SUMMARY_CHARS = 240

SYSTEM_PROMPT = (
    "You are a trading desk analyst. A stock has made a large move and the trader wants to know "
    "which news caused it, in one sentence he can act on.\n"
    "\n"
    "You will be given the symbol, its percent change, and a numbered list of recent headlines. "
    "You may ONLY credit a headline from that list. Never mention, invent, or assume any news "
    "item that is not in the list, and never guess at an earnings report, an analyst action, or "
    "a deal that the list does not contain.\n"
    "\n"
    "THE MOST IMPORTANT RULE: if the listed headlines do not actually explain a move of this size "
    "and direction, say so. Return category UNKNOWN, confidence low, and the summary "
    "\"" + NO_NEWS_SUMMARY + "\" A confident invented explanation is worse than admitting you do "
    "not know -- it makes the trader certain about a move he does not understand. Routine "
    "coverage, a stale story, a market wrap-up that merely lists the ticker, and news whose "
    "direction contradicts the move are all UNKNOWN.\n"
    "\n"
    "Answer as JSON only:\n"
    "  summary       - one sentence, plain English, no hedging boilerplate\n"
    "  category      - one of EARNINGS, UPGRADE, DOWNGRADE, CONTRACT, PRODUCT, FDA, DEAL, OFFERING, "
    "LEGAL, MACRO, UNKNOWN (DEAL = a merger or acquisition; OFFERING = the company selling new stock "
    "or convertibles, i.e. dilution; FDA = a drug approval, rejection or trial result)\n"
    "  direction     - bullish, bearish, or neutral: which way the NEWS itself pushes the stock "
    "(an offering or a downgrade is bearish even on an up day); UNKNOWN is neutral\n"
    "  confidence    - high, medium, or low\n"
    "  headlineIndex - the number of the headline you credit, or null for none\n"
    "  headline      - that headline's title copied EXACTLY, or null for none\n"
    "\n"
    "high confidence means the headline plainly accounts for a move of this size; medium means it "
    "is the likely driver; low means you are unsure. UNKNOWN is always low."
)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "confidence": {"type": "string", "enum": list(CONFIDENCES)},
        "direction": {"type": "string", "enum": list(DIRECTIONS)},
        "headline": {"type": ["string", "null"]},
        "headlineIndex": {"type": ["integer", "null"]},
    },
    "required": ["summary", "category", "confidence"],
    "additionalProperties": False,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(value: object) -> float | None:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return None


def _result(available: bool, reason: str, *, provider: str | None = None, **payload: Any) -> dict:
    """The shape every AI feature module returns."""
    base = {
        "available": bool(available),
        "reason": str(reason or ""),
        "generatedAt": _now_iso(),
        "provider": provider,
    }
    base.update(payload)
    return base


# ---------------------------------------------------------------- headlines --

_TITLE_NOISE = re.compile(r"[^a-z0-9]+")


def _title_key(title: object) -> str:
    return _TITLE_NOISE.sub(" ", str(title or "").casefold()).strip()


def _age_minutes(item: dict) -> int | None:
    for key in ("ageMinutes", "age_minutes"):
        raw = item.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            return max(0, int(float(raw)))
        except (TypeError, ValueError):
            continue
    for key in ("publishedAt", "published_at", "created_at", "updated_at"):
        text = str(item.get(key) or "").strip()
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - parsed).total_seconds()
        return max(0, int(delta // 60))
    return None


def normalize_headlines(headlines: object) -> list[dict]:
    """Coerce any of the app's headline shapes into {title, url, ageMinutes}.

    The news pipeline speaks three dialects -- Alpaca/CatalystCache verdicts
    ("headline" / "publishedAt"), CatalystEngine rows ("headline" /
    "published_at") and plain UI rows ("title" / "url") -- so accept all of
    them and drop anything without a title. Duplicates are collapsed: the same
    story often arrives from two feeds and must not use up two prompt slots.
    """
    rows: list[dict] = []
    seen: set[str] = set()
    for item in headlines if isinstance(headlines, (list, tuple)) else []:
        if isinstance(item, str):
            item = {"title": item}
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("headline") or "").strip()
        if not title:
            continue
        key = _title_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        url = str(item.get("url") or item.get("link") or "").strip() or None
        rows.append({"title": title, "url": url, "ageMinutes": _age_minutes(item)})
        if len(rows) >= MAX_HEADLINES:
            break
    return rows


def cached_headlines(symbol: str, cache: object) -> list[dict]:
    """Headlines the running server has ALREADY fetched for this symbol.

    ``cache`` is the api_server's catalyst_news.CatalystCache instance, whose
    ``get()`` never touches the network (a background thread does all the
    fetching). This helper exists so a caller does not have to know that: it
    keeps ``explain()`` itself free of I/O and free of any api_server import.
    Returns [] for an unknown symbol, an empty cache, or any cache error --
    "no headlines" is a valid answer that the UNKNOWN path handles.
    """
    target = str(symbol or "").strip().upper()
    if not target or cache is None:
        return []
    try:
        entry = cache.get(target)
    except Exception:
        return []
    if entry is None:
        return []
    return normalize_headlines(entry if isinstance(entry, (list, tuple)) else [entry])


def explain_from_cache(symbol: str, change_pct: float, cache: object, *, provider=None) -> dict:
    """explain() sourced from the existing news cache. Convenience for callers."""
    return explain(symbol, change_pct, cached_headlines(symbol, cache), provider=provider)


# ------------------------------------------------------------- model answer --

def _credited_headline(data: dict, rows: list[dict]) -> dict | None:
    """Resolve the model's credited headline against the ones we PASSED IN.

    A model that names a headline we never showed it is hallucinating its
    evidence, and the honest response is to credit nothing. So a title that
    matches none of ours resolves to None -- and we deliberately do NOT fall
    back to the index in that case, because the index would launder an
    invented claim into a real-looking citation.
    """
    claimed = data.get("headline")
    if isinstance(claimed, dict):
        claimed = claimed.get("title") or claimed.get("headline")
    title = str(claimed or "").strip()
    if title:
        key = _title_key(title)
        for row in rows:
            row_key = _title_key(row["title"])
            if row_key == key:
                return dict(row)
            # A model that echoes a truncated title still points at a real
            # story; require a long overlap so a bare company name cannot match.
            if len(key) >= 20 and (row_key.startswith(key) or key.startswith(row_key)):
                return dict(row)
        return None

    index = data.get("headlineIndex")
    if index is None or isinstance(index, bool):
        return None
    try:
        position = int(index)
    except (TypeError, ValueError):
        return None
    if 1 <= position <= len(rows):
        return dict(rows[position - 1])
    return None


def _clean_category(value: object) -> str:
    candidate = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return candidate if candidate in CATEGORIES else "UNKNOWN"


def _clean_confidence(value: object) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in CONFIDENCES else "low"


def _clean_summary(value: object) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > MAX_SUMMARY_CHARS:
        text = text[: MAX_SUMMARY_CHARS - 1].rstrip() + "…"
    return text


def _parse_model_payload(response: object) -> dict | None:
    """The model's object, from a structured `data` or from JSON in `text`."""
    if not isinstance(response, dict):
        return None
    data = response.get("data")
    if isinstance(data, dict):
        return data
    text = str(response.get("text") or "").strip()
    if not text:
        return None
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start:end + 1])
        except (TypeError, ValueError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _resolve_provider(provider):
    if provider is not None:
        return provider
    try:
        from agents.ai_provider import get_provider  # lazy: never import an SDK at module load
    except Exception:
        try:
            from ai_provider import get_provider  # type: ignore[no-redef]
        except Exception:
            return None
    try:
        return get_provider()
    except Exception:
        return None


def _provider_available(provider) -> bool:
    checker = getattr(provider, "available", None)
    if not callable(checker):
        return True  # a hand-injected fake without the method is usable
    try:
        return bool(checker())
    except Exception:
        return False


def _provider_name(provider) -> str | None:
    try:
        name = getattr(provider, "name", None)
    except Exception:
        return None
    return str(name) if name else None


def _format_change(change_pct: object) -> str:
    value = _safe_float(change_pct)
    return "an unknown amount" if value is None else f"{value:+.1f}%"


def _build_user_prompt(symbol: str, change_pct: object, rows: list[dict]) -> str:
    lines = [
        f"Symbol: {symbol}",
        f"Move today: {_format_change(change_pct)}",
        "",
        "Recent headlines (the ONLY ones you may credit):",
    ]
    for position, row in enumerate(rows, start=1):
        age = row.get("ageMinutes")
        stamp = f" [{age} min ago]" if isinstance(age, int) else ""
        lines.append(f"{position}. {row['title']}{stamp}")
    lines.append("")
    lines.append(
        f"Does any of that explain {symbol} moving {_format_change(change_pct)} today? "
        "If not, answer UNKNOWN."
    )
    return "\n".join(lines)


# ------------------------------------------------------------------ feature --

def explain(symbol: str, change_pct: float, headlines: list[dict], *, provider=None) -> dict:
    """Tie a mover to its catalyst, or say plainly that nothing explains it.

    Returns the standard feature contract plus summary / category / confidence
    / headline. Never raises and never performs I/O of its own.
    """
    ticker = str(symbol or "").strip().upper()
    rows = normalize_headlines(headlines)
    common = {
        "symbol": ticker,
        "changePct": _safe_float(change_pct),
        "headlinesConsidered": len(rows),
        "summary": "",
        "category": "UNKNOWN",
        "confidence": "low",
        "direction": "neutral",
        "headline": None,
    }

    resolved = _resolve_provider(provider)
    if resolved is None or not _provider_available(resolved):
        return _result(
            False,
            "No AI key set, so moves are not explained. Add OPENAI_API_KEY to .env.",
            **common,
        )
    name = _provider_name(resolved)

    if not rows:
        # Nothing to reason over. Answering locally is both honest and free:
        # never spend a model call to be told there is no news.
        return _result(
            True,
            "No recent headlines were available for this symbol.",
            provider=name,
            **{**common, "summary": NO_NEWS_SUMMARY},
        )

    try:
        response = resolved.complete(
            system=SYSTEM_PROMPT,
            user=_build_user_prompt(ticker, change_pct, rows),
            max_tokens=500,
            schema=RESPONSE_SCHEMA,
        )
    except Exception as exc:  # a provider is contracted not to raise; trust nothing
        return _result(False, f"The AI call failed: {exc}", provider=name, **common)

    if not isinstance(response, dict) or not response.get("ok"):
        detail = str(response.get("error") or "").strip() if isinstance(response, dict) else ""
        return _result(
            False,
            f"The AI could not explain this move: {detail}" if detail else "The AI call did not succeed.",
            provider=name,
            **common,
        )

    data = _parse_model_payload(response)
    summary = _clean_summary(data.get("summary")) if isinstance(data, dict) else ""
    if not isinstance(data, dict) or not summary:
        return _result(
            False,
            "The AI returned a response we could not read, so no explanation is shown.",
            provider=name,
            **common,
        )

    category = _clean_category(data.get("category"))
    confidence = _clean_confidence(data.get("confidence"))
    direction = str(data.get("direction") or "").strip().lower()
    direction = direction if direction in DIRECTIONS else "neutral"
    credited = _credited_headline(data, rows)
    reason = "Explained from recent headlines."

    if credited is None and category != "UNKNOWN":
        # The model claimed a cause but cited a headline we never showed it.
        # Keep its wording, strip the fake citation, and stop calling it
        # confident: an uncited explanation is a guess.
        confidence = "low"
        reason = "The AI cited a headline that was not in the news we passed it, so no source is credited."

    if category == "UNKNOWN":
        # Unexplained means unexplained: no credited source, never confident.
        credited = None
        confidence = "low"
        direction = "neutral"
        reason = "The recent headlines do not explain this move."

    # The provider that really answered (a fallback chain reports which one).
    answered_by = response.get("provider") if isinstance(response.get("provider"), str) else name
    return _result(
        True,
        reason,
        provider=answered_by,
        **{
            **common,
            "summary": summary,
            "category": category,
            "confidence": confidence,
            "direction": direction,
            "headline": credited,
        },
    )
