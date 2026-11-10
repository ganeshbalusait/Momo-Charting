"""AI for the Ask chat (2026-11-10): his "Use AI to reply the chat and fix the chat response".

The no-AI chatbot (momx/chatbot.py) stays the only source of facts. The AI does two things around it:

1. UNDERSTAND - when the chatbot does not recognise a question, the AI reads it and says which of the
   chatbot's question types it is, and the ticker / day / time / side in it. The chatbot then answers
   that, from the records.
2. REPLY - the AI turns the chatbot's factual lines into a short, direct answer to what was asked.
   It is given ONLY those lines. Its reply is thrown away unless every number in it is a number that
   appears in the lines, so it cannot invent or change a figure. The record lines are always shown
   under it.

No answer in time, an error, a quota refusal, or a reply that fails the check: the chat simply shows
the record lines, exactly as it did before. Off with AGX_CHAT_AI=0.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from typing import Any, Callable, Mapping

#: The AI gets this long; after that the chat answers without it.
TIMEOUT_SECONDS = 6.0
#: After the AI service refuses or times out, the chat does not ask it again for this long, so a
#: used-up free quota costs one slow answer, not every answer. Longer when the refusal is a quota.
REST_SECONDS = 120.0
REST_QUOTA_SECONDS = 900.0
_REST = {"until": 0.0, "why": ""}


def resting() -> str:
    """Why the AI is not being asked right now ("" = it is being asked)."""
    return _REST["why"] if time.monotonic() < _REST["until"] else ""


def _rest(why: str) -> None:
    quota = bool(re.search(r"429|quota|rate.?limit|credit", why.lower()))
    _REST.update(until=time.monotonic() + (REST_QUOTA_SECONDS if quota else REST_SECONDS),
                 why="the AI service is over its free limit" if quota else "the AI service did not answer")
REPLY_MAX = 700

UNDERSTAND_SCHEMA = {"type": "object", "properties": {
    "intent": {"type": "string", "enum": ["what_if", "why_failed", "why_missed", "show", "status", "none"]},
    "ticker": {"type": "string"}, "day": {"type": "string"}, "time": {"type": "string"},
    "side": {"type": "string", "enum": ["call", "put", ""]}},
    "required": ["intent", "ticker", "day", "time", "side"]}
UNDERSTAND_SYSTEM = (
    "You route a trader's question to one of five lookups in his trading app. Reply with JSON only. intent: 'what_if' = how far a "
    "signal or trade went, its profit or loss; 'why_failed' = why a trade lost or what was wrong with it; 'why_missed' = why the "
    "bot did not take a trade; 'show' = list what is on file for a ticker; 'status' = how the bot or the accounts are doing; "
    "'none' = anything else. ticker: the stock ticker in capitals, or '' (use the ticker the conversation is about if the question "
    "has none). day: YYYY-MM-DD if the question names a day ('yesterday', 'friday', 'nov 6' - today's date is given), else ''. "
    "time: HH:MM in 24-hour ET if a time is named, else ''. side: 'call', 'put' or ''. Do not guess a ticker that is not there.")

REPLY_SCHEMA = {"type": "object", "properties": {"reply": {"type": "string"}}, "required": ["reply"]}
REPLY_SYSTEM = (
    "You answer a trader's question about his own trades. You are given the question and FACTS read from his app's records. "
    "Answer the question directly in plain trader language, in at most four short sentences. Lead with the answer he asked for "
    "(for 'profit or loss' say profit or loss and how much). Use ONLY the facts given: never add a number, price, percentage, "
    "time or reason that is not in the facts, and copy numbers exactly as written. If the facts say something is not recorded, "
    "say so. No advice to buy or sell. No markdown. Reply with JSON only.")


def enabled() -> bool:
    return os.environ.get("AGX_CHAT_AI", "1").strip().lower() not in {"0", "false", "no", "off"}


def _default_ask(system: str, user: str, schema: dict) -> dict:
    from agents.ai_provider import claude_first_provider
    return claude_first_provider(effort="low").complete(system=system, user=user, max_tokens=600, schema=schema)


def _bounded(ask: Callable[[str, str, dict], dict], system: str, user: str, schema: dict, seconds: float) -> dict | None:
    """Run one AI call on a side thread and give up after `seconds`: the chat never waits on a slow AI.
    The app's own AI service (not a test's stand-in) is left alone for a while after it refuses."""
    real = ask is _default_ask
    if real and resting():
        return None
    box: dict = {}

    def run() -> None:
        try:
            box["out"] = ask(system, user, schema)
        except Exception as exc:  # noqa: BLE001 - the chat must answer whatever the AI does
            box["out"] = {"ok": False, "error": type(exc).__name__}

    worker = threading.Thread(target=run, name="chat-ai", daemon=True)
    worker.start()
    worker.join(seconds)
    out = box.get("out")
    if real and not (isinstance(out, Mapping) and out.get("ok")):
        _rest(str(out.get("error") if isinstance(out, Mapping) else "no answer in time"))
    return out if isinstance(out, Mapping) else None


def understand(question: str, today: str, context: Mapping | None = None,
               ask: Callable[[str, str, dict], dict] | None = None, seconds: float = TIMEOUT_SECONDS) -> dict | None:
    """What an unrecognised question is asking, as the chatbot's own terms; None when the AI cannot say."""
    about = {k: str(v) for k, v in (context or {}).items() if k in ("ticker", "day", "side") and isinstance(v, str)}
    user = json.dumps({"question": str(question)[:300], "today": today, "conversationIsAbout": about}, separators=(",", ":"))
    out = _bounded(ask if ask is not None else _default_ask, UNDERSTAND_SYSTEM, user, UNDERSTAND_SCHEMA, seconds)
    data = out.get("data") if out and out.get("ok") else None
    if not isinstance(data, Mapping) or data.get("intent") not in ("what_if", "why_failed", "why_missed", "show", "status"):
        return None
    ticker = str(data.get("ticker") or "").upper().strip()
    day = str(data.get("day") or "").strip()
    clock = str(data.get("time") or "").strip()
    return {"intent": data["intent"],
            "ticker": ticker if re.fullmatch(r"[A-Z.]{1,6}", ticker) else "",
            "day": day if re.fullmatch(r"20\d\d-\d\d-\d\d", day) else "",
            "time": clock if re.fullmatch(r"\d{1,2}:\d{2}", clock) else "",
            "side": data.get("side") if data.get("side") in ("call", "put") else ""}


#: How each question type is worded for the chatbot once the AI has understood it.
_PHRASE = {"what_if": "how much did it go", "why_failed": "why did it fail", "why_missed": "why was it missed",
           "show": "see", "status": "what is the bot doing"}


def rephrase(understood: Mapping) -> str:
    """The understood question as a sentence the chatbot's own reader accepts ("$NVDA put how much did it go 2026-11-09 at 10:30")."""
    if understood.get("intent") == "status":
        return _PHRASE["status"]
    parts = ["$" + understood["ticker"]] if understood.get("ticker") else []
    if understood.get("side"):
        parts.append(understood["side"])
    parts.append(_PHRASE[understood["intent"]])
    if understood.get("day"):
        parts.append(understood["day"])
    if understood.get("time"):
        parts.append("at " + understood["time"])
    return " ".join(parts)


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    """Every number in a text, without thousands commas and trailing zeros ("$1,350.50" -> "1350.5")."""
    out = set()
    for raw in _NUMBER.findall(text):
        value = raw.replace(",", "")
        if "." in value:
            value = value.rstrip("0").rstrip(".")
        out.add(value.lstrip("0") or "0")
    return out


_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def checked(reply: Any, lines: list[str], question: str = "") -> str | None:
    """The AI's reply if it is safe to show: plain text of sane length whose every number is in the record
    lines (or was in the question itself). "Three contracts" in a line lets the reply say "3 contracts"."""
    text = " ".join(str(reply or "").split())
    if not text or len(text) > REPLY_MAX or "```" in text:
        return None
    source = " ".join(lines) + " " + str(question)
    allowed = _numbers(source) | {digit for word, digit in _WORDS.items() if re.search(r"\b%s\b" % word, source.lower())}
    if not _numbers(text) <= allowed:
        return None                      # it wrote a figure the records do not contain
    return text


def reply(question: str, lines: list[str], ask: Callable[[str, str, dict], dict] | None = None,
          seconds: float = TIMEOUT_SECONDS) -> dict | None:
    """{"text", "source"} - a direct answer written from the record lines - or None to show the lines alone."""
    facts = [str(x) for x in lines if isinstance(x, str) and x.strip()]
    if not facts:
        return None
    user = json.dumps({"question": str(question)[:300], "facts": facts}, separators=(",", ":"))
    out = _bounded(ask if ask is not None else _default_ask, REPLY_SYSTEM, user, REPLY_SCHEMA, seconds)
    data = out.get("data") if out and out.get("ok") else None
    text = checked(data.get("reply") if isinstance(data, Mapping) else None, facts, question)
    if not text:
        return None
    return {"text": text, "source": " ".join(str(out.get("provider") or "AI").split())[:40]}


def answer(question: str, artifacts, now=None, context: Mapping | None = None,
           ask: Callable[[str, str, dict], dict] | None = None) -> dict:
    """The chat's full answer: the chatbot's records answer, with the AI understanding an unrecognised
    question first and writing a short direct reply on top. Any AI failure leaves the records answer as it is."""
    from datetime import datetime
    from momx import chatbot
    now = (now or datetime.now(chatbot.ET)).astimezone(chatbot.ET)
    out = chatbot.ask(question, artifacts, now, context)
    if not enabled():
        return out
    if ask is None and resting():
        return {**out, "aiNote": resting()}          # the records answer at once; the AI is tried again later
    if out.get("intent") == "help":
        understood = understand(question, now.date().isoformat(), context, ask)
        if understood and (understood["ticker"] or understood["intent"] == "status"):
            again = chatbot.ask(rephrase(understood), artifacts, now, context)
            if again.get("intent") != "help":
                out = {**again, "understoodBy": "AI"}
    if out.get("intent") in ("help", "error"):
        return out
    written = reply(question, out.get("lines") or [], ask)
    if written:
        return {**out, "ai": written}
    return {**out, "aiNote": resting()} if ask is None and resting() else out
