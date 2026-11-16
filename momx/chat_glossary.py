"""The Ask chat's glossary (2026-11-11 design change: the chat never shows AI-written prose).

"what is theta", "explain how calls work", "what is an iron condor", "short squeeze": the answer is a fixed entry
from momx/chat_glossary.json, written once and checked by tests/test_momx_chat_glossary.py against the chat's
advice rules, or failing that the app's own help text (chat_rag, never news). No AI writes any of it, so there is
no wording for a filter to miss. A term that is in neither gets one plain line and the Learn + Setup link.

Matching forgives typos the way chat_rag's search does: trigram overlap picks the candidates, and chat_rag's
edit distance (a swap of neighbours counts once) decides.
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Mapping

GLOSSARY_JSON = Path(__file__).with_name("chat_glossary.json")
UNKNOWN_LINE = "That term isn't in the app's glossary yet. Learn + Setup explains every page."
LEARN_LINK = {"label": "Open Learn + Setup", "view": "Learn + Setup"}
MAX_PHRASE = 60

_lock = threading.Lock()
_cache: dict[str, Any] = {}

#: What a definition question looks like around its term.
_ASK_HEAD = re.compile(
    r"^(?:(?:can|could|would)\s+you\s+|please\s+|pls\s+)?(?:"
    r"what\s+(?:is|are|was|does|do)\b|what'?s\b|whats\b|what\s+is\s+meant\s+by\b|"
    r"explain(?:\s+to\s+me)?\b|define\b|definition\s+of\b|meaning\s+of\b|tell\s+me\s+what\b|"
    r"how\s+(?:does|do|is|are)\b|describe\b)\s*", re.I)
_ASK_TAIL = re.compile(r"\s*(?:\b(?:mean|means|meant|work|works|working|stand\s+for|is|are|in\s+options?|in\s+trading|in\s+stocks|"
                       r"in\s+the\s+app|on\s+the\s+scanner|on\s+the\s+chart|options?\s+term|exactly|again|please|pls)\b\s*)+$", re.I)
_ARTICLE = re.compile(r"^(?:(?:how|what)\s+|(?:an?|the)\s+)+", re.I)


def _norm(text: Any) -> str:
    low = str(text or "").lower().replace("’", "'")
    low = re.sub(r"[‐-―-]", " ", low)
    low = re.sub(r"[^a-z0-9+/&%. ]", " ", low)
    low = re.sub(r"(?<![a-z0-9])\.|\.(?![a-z0-9])", " ", low)
    return " ".join(low.split())


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _key(text: Any) -> str:
    return " ".join(_singular(word) for word in _norm(text).split())


def _grams(text: str) -> set[str]:
    padded = f" {text} "
    return {padded[i:i + 3] for i in range(len(padded) - 2)}


def entries() -> list[dict]:
    """Every entry, read once: [{term, aliases, text, scope, number}]. number: the app reports this term per ticker."""
    with _lock:
        if "entries" not in _cache:
            try:
                raw = json.loads(GLOSSARY_JSON.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                raw = {}
            rows = []
            for item in raw.get("terms") if isinstance(raw, Mapping) and isinstance(raw.get("terms"), list) else []:
                if isinstance(item, Mapping) and isinstance(item.get("term"), str) and isinstance(item.get("text"), str):
                    aliases = [a for a in item.get("aliases") or [] if isinstance(a, str)]
                    rows.append({"term": item["term"], "aliases": aliases, "text": " ".join(item["text"].split()),
                                 "scope": "owner" if item.get("scope") == "owner" else "all",
                                 "number": item.get("number") is True})
            keys: dict[str, int] = {}
            for index, row in enumerate(rows):
                for name in (row["term"], *row["aliases"]):
                    keys.setdefault(_key(name), index)
            _cache.update(entries=rows, keys=keys, grams={key: _grams(key) for key in keys})
        return _cache["entries"]


def _allowed(row: Mapping, scope: str) -> bool:
    from momx import chat_rag_index
    return scope == "owner" or (row["scope"] == "all" and not chat_rag_index.OWNER_ONLY.search(f"{row['term']} {row['text']}"))


def lookup(phrase: Any, scope: str = "scanner", fuzzy: bool = True) -> dict | None:
    """The entry for a term, forgiving a typo or two; None when nothing is close."""
    from momx import chat_rag
    rows = entries()
    key = _key(phrase)[:MAX_PHRASE]
    if not key:
        return None
    index = _cache["keys"].get(key)
    # The singular form and the words as typed ("cals" singular is "cal", too short to correct; "cals" is near "call").
    for typed in dict.fromkeys((key, _norm(phrase)[:MAX_PHRASE])):
        if index is not None or not fuzzy or len(typed) < 4:
            continue
        most = 1 if len(typed) <= 5 else 2 if len(typed) <= 12 else 3
        mine = _grams(typed)
        best = None
        for other, grams in _cache["grams"].items():
            if abs(len(other) - len(typed)) > most or len(other) < 4:
                continue
            overlap = len(mine & grams) / max(len(mine | grams), 1)
            if overlap < 0.25:
                continue
            distance = chat_rag._distance(typed, other, most)
            if distance <= most and (best is None or (distance, -overlap, other) < best):
                best = (distance, -overlap, other)
        index = _cache["keys"][best[2]] if best else None
    if index is None or not _allowed(rows[index], scope):
        return None
    return rows[index]


def asked_term(text: Any) -> str | None:
    """The term a definition question asks about ("what is an iron condor" -> "iron condor",
    "explain how calls work" -> "calls"), or None when the question is not shaped like one."""
    raw = " ".join(str(text or "").split()).strip(" ?.!")
    head = _ASK_HEAD.match(raw)
    if not head:
        return None
    rest = raw[head.end():]
    for _ in range(3):
        before = rest
        rest = _ARTICLE.sub("", rest.strip(" ?.!,"))
        rest = _ASK_TAIL.sub("", rest).strip(" ?.!,")
        if rest == before:
            break
    return rest or None


def term_of(text: Any, scope: str = "scanner") -> dict | None:
    """The entry a question asks for: a definition question, or the bare term on its own ("short squeeze")."""
    asked = asked_term(text)
    if asked:
        return lookup(asked, scope)
    return lookup(text, scope, fuzzy=len(_key(text)) >= 6)


def mentioned(text: Any, scope: str = "scanner") -> dict | None:
    """The longest glossary term named anywhere in a question, as whole words ("tell me in general terms what a call
    option is all about" -> Call). Only for a question the planner already marked as a general idea."""
    entries()
    words = f" {_key(text)} "
    found = [key for key in _cache["keys"] if (len(key) >= 4 or " " in key) and f" {key} " in words]
    for key in sorted(found, key=len, reverse=True):
        row = _cache["entries"][_cache["keys"][key]]
        if _allowed(row, scope):
            return row
    return None


def line_of(entry: Mapping) -> str:
    return f"{entry['term']}: {entry['text']}"


def define(args: Mapping, ctx: Mapping) -> dict:
    """The glossary tool: the entry; else the app's own help text (never news); else one plain line."""
    from momx import chat_rag
    args = args if isinstance(args, Mapping) else {}
    ctx = ctx if isinstance(ctx, Mapping) else {}
    scope = str(ctx.get("scope") or "")
    if scope not in ("owner", "scanner"):
        return {"lines": ["That is not available in this chat."], "facts": {"refused": True}, "links": [], "table": None, "asOf": None}
    query = " ".join(str(args.get("query") or args.get("term") or "").split())[:200]
    entry = (term_of(query, scope) or mentioned(query, scope)) if query else None
    if entry:
        return {"lines": [line_of(entry)], "facts": {"term": entry["term"], "glossary": True}, "links": [dict(LEARN_LINK)], "table": None, "asOf": None}
    results = []
    if query and not chat_rag.asks_for_a_number(query):
        try:
            results = [r for r in chat_rag.search(asked_term(query) or query, scope, 3, ctx.get("searchDir")) if r.get("source") != "news"]
        except Exception:  # noqa: BLE001 - a lookup must never fail the chat
            results = []
    if results:
        return {"lines": [chat_rag._line(r) for r in results], "facts": {"glossary": False, "results": [{"source": r["source"], "title": r["title"]} for r in results]},
                "links": chat_rag._links(results) or [dict(LEARN_LINK)], "table": None, "asOf": None}
    return {"lines": [UNKNOWN_LINE], "facts": {"glossary": False, "unknown": True}, "links": [dict(LEARN_LINK)], "table": None, "asOf": None}


TOOLS = {
    "define": {"scope": "all", "handler": define,
               "description": "What a market, options or app term means (theta, iron condor, max pain, Skittles, SQZ, H/L), from the app's fixed glossary. "
                              "Use it for any general-knowledge question about an idea rather than a number.",
               "args": {"type": "object", "properties": {"query": {"type": "string", "maxLength": 200, "description": "The term or the question"}},
                        "required": ["query"], "additionalProperties": False}},
}
