"""The Ask chat's answer (2026-11-11): his "Use AI to reply the chat", then "use LLM or rag search engine".

A question goes through these steps, and stops at the first that answers:

1. SURE MATCH, no AI. A ticker with a known word (scanner, chain, wall, level, news ...) or "where / open / take me".
2. SIGNAL RULE. "signal" with a chart, a timeframe or a CALL/PUT label is the chart; "bot signal" is the Bot.
3. RECORDS. The Bot's own question forms (owner) and the saved scanner, read by the no-AI chatbot (momx/chatbot.py).
4. PLANNER, one AI call. It picks up to 3 tools from those this session may use. Python checks the arguments
   and runs the tools; the AI never sees a file and never writes a link.
5. GLOSSARY for a general idea ("what is theta", "explain how calls work"): a fixed entry from
   momx/chat_glossary.json, else the app's help text, else one plain line (momx/chat_glossary.py). No AI writes it.
6. SEARCH CARD when the AI is down or has nothing: links for any ticker, a page by name, the help text. Never a dead end.

No AI-written prose is ever shown (2026-11-11 design change, the trader's rule that the chat never gives
buy/sell/hold advice: three rechecks found advice phrasings a word filter missed). The AI only routes: its output
is tool names and checked arguments, never displayed. The tool lines are the answer. The why-answer's AI returns
only categories and headline numbers, and the screen line is built from the saved headline (chat_why.py).

Numbers come only from tools, each with its time. The chat only reads: it can never place, change or
cancel a trade. No advice to buy or sell: "is NVDA a buy" gets facts and links. Off with AGX_CHAT_AI=0
(step 4 goes, the rest stay).

A regular user's question runs with scope="scanner": owner tools are not offered, its prompts say nothing about
anyone's trades, and a question about them gets one plain line.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable, Mapping

#: The AI gets this long; after that the chat answers without it.
TIMEOUT_SECONDS = 8.0
#: After the AI service refuses or times out, the chat does not ask it again for this long, so a
#: used-up free quota costs one slow answer, not every answer. Longer when the refusal is a quota.
REST_SECONDS = 120.0
REST_QUOTA_SECONDS = 900.0
_REST = {"until": 0.0, "why": ""}
#: Regular users rest on their own: a failed call of theirs can never switch the AI off for the owner.
_REST_SCANNER = {"until": 0.0, "why": ""}
#: A plan is reused this long for the same question from the same session type, to spare the free quota.
PLAN_SECONDS = 60.0
_PLAN_MAX = 200
_PLANS: "OrderedDict[tuple, tuple[float, dict]]" = OrderedDict()
_PLANS_LOCK = threading.Lock()
MAX_CALLS = 3


def resting(scope: str = "owner") -> str:
    """Why the AI is not being asked right now ("" = it is being asked). When it is resting for
    the owner it is resting for everyone; a regular user's rest stops only regular users."""
    now = time.monotonic()
    if now < _REST["until"]:
        return _REST["why"]
    return _REST_SCANNER["why"] if scope != "owner" and now < _REST_SCANNER["until"] else ""


def _rest(why: str, scope: str = "owner") -> None:
    """Leave the AI alone for a while, for as long as this cause deserves. One unreadable reply is no reason at all."""
    from momx import chat_provider
    cause = chat_provider.cause_of(why)
    if cause == "bad_reply":
        return
    quota = cause in ("quota", "credit")
    (_REST if scope == "owner" else _REST_SCANNER).update(until=time.monotonic() + (REST_QUOTA_SECONDS if quota else REST_SECONDS),
                                                          why="the AI service is over its free limit" if quota else "the AI service did not answer")


REPLY_MAX = 700

UNDERSTAND_SYSTEM = (
    "You route a trader's question to the lookups of his trading app. Reply with JSON only. calls: at most 3 lookups, each {tool, args}, "
    "chosen only from TOOLS below, with only the args each one lists; leave it empty when none fits. general: true only when the question "
    "is about a general market or options idea and needs nothing from the app. ticker: the stock ticker in capitals; if the question has "
    "none, use conversationIsAbout, and pageTicker when given. day: YYYY-MM-DD if the question names a day ('yesterday', 'friday', 'nov 6' "
    "- today's date is given). time: HH:MM in 24-hour ET if a time is named. Do not guess a ticker that is not there."
    " A question about where a price is going (predict, forecast, tomorrow, next week, up or down, how high or low, in any wording or broken English) is price_outlook, never help_search. Read the meaning, not the exact words: typos and missing words are normal.")
SCANNER_UNDERSTAND_SYSTEM = (
    "You route a question about market data to the lookups of a stock market app. Reply with JSON only. calls: at most 3 lookups, each "
    "{tool, args}, chosen only from TOOLS below, with only the args each one lists; leave it empty when none fits. general: true only when "
    "the question is about a general market or options idea and needs nothing from the app. ticker: the stock ticker in capitals; if the "
    "question has none, use conversationIsAbout, and pageTicker when given. day: YYYY-MM-DD if the question names a day ('yesterday', 'friday', "
    "'nov 6' - today's date is given). time: HH:MM in 24-hour ET if a time is named. Do not guess a ticker that is not there."
    " A question about where a price is going (predict, forecast, tomorrow, next week, up or down, how high or low, in any wording or broken English) is price_outlook, never help_search. Read the meaning, not the exact words: typos and missing words are normal.")

def enabled() -> bool:
    return os.environ.get("AGX_CHAT_AI", "1").strip().lower() not in {"0", "false", "no", "off"}


#: Whose call the side thread is making, so each request it sends is counted against the right share of the day's cap.
_CALLER = threading.local()


def _default_ask(system: str, user: str, schema: dict) -> dict:
    from momx import chat_limits, chat_provider
    scope = getattr(_CALLER, "scope", "scanner")
    return chat_provider.complete(system, user, schema, seconds=TIMEOUT_SECONDS, take=lambda: chat_limits.LIMITS.take_ai_call(scope))


def _bounded(ask: Callable[[str, str, dict], dict], system: str, user: str, schema: dict, seconds: float, scope: str = "owner",
             rest: bool = True) -> dict | None:
    """Run one AI call on a side thread and give up after `seconds`: the chat never waits on a slow AI.
    The app's own AI service (not a test's stand-in) is left alone for a while after it refuses. The day's cap
    is checked here and counted per request sent (chat_provider), since one call can send more than one.
    rest=False (the owner's AI summary): a failure of this call does not switch the AI off for the chat's other uses."""
    real = ask is _default_ask
    if real and resting(scope):
        return None
    if real:
        from momx import chat_limits
        if not chat_limits.LIMITS.ai_call_allowed(scope):
            return None
    box: dict = {}

    def run() -> None:
        _CALLER.scope = scope
        try:
            box["out"] = ask(system, user, schema)
        except Exception as exc:  # noqa: BLE001 - the chat must answer whatever the AI does
            box["out"] = {"ok": False, "error": type(exc).__name__}

    worker = threading.Thread(target=run, name="chat-ai", daemon=True)
    worker.start()
    worker.join(seconds)
    out = box.get("out")
    if real and rest and not (isinstance(out, Mapping) and out.get("ok")):
        _rest(str(out.get("error") if isinstance(out, Mapping) else "no answer in time"), scope)
    return out if isinstance(out, Mapping) else None


# ---------------------------------------------------------------- what the screen sent
_PAGE_WORDS = re.compile(r"\b(?:chart|this|here|screen|page)\b")


def _context(context: Mapping | None, artifacts: Any, now: Any, scope: str, account: str | None) -> dict:
    """The handlers' ctx, every value checked: all of it comes from the screen and could hold anything."""
    from momx import chat_pages, chatbot
    last, ticker, day = chatbot._conversation(context)
    page = str(last.get("pageTicker") or "").upper()
    page = page if re.fullmatch(r"[A-Z.]{1,6}", page) else ""
    if last.get("fromPage"):
        page, ticker = page or ticker, ""             # that ticker is the one on the screen, not what was being talked about
        day = ""
    side = last.get("side") if last.get("side") in ("bull", "bear") else None
    return {"scope": scope, "account": account or "", "pageTicker": page or None, "pageName": chat_pages.valid_page_name(last.get("pageName")),
            "ticker": ticker or None, "day": day or None, "side": side, "now": now, "artifacts": Path(artifacts)}


def _records_context(ctx: Mapping) -> dict:
    """What the records chatbot understands: the conversation's ticker, or the screen's marked as such."""
    if ctx.get("ticker"):
        return {"ticker": ctx["ticker"], "day": ctx.get("day") or "", "side": ctx.get("side")}
    return {"ticker": ctx["pageTicker"], "day": "", "fromPage": True} if ctx.get("pageTicker") else {}


def _about(context: Mapping | None) -> dict:
    """What the conversation is about, for the AI: checked values only. The context comes from the screen, so it can hold anything."""
    from momx import chatbot
    last, ticker, day = chatbot._conversation(context)
    if last.get("fromPage"):
        return {}                                     # the ticker on the screen is not what an unrecognised question ("hello") is about
    side = last.get("side")
    return {key: value for key, value in (("ticker", ticker), ("day", day), ("side", side if side in ("bull", "bear", "call", "put") else "")) if value}


# ---------------------------------------------------------------- step 4: the planner
def planner_args(scope: str = "owner") -> dict:
    """Every argument any offered tool takes, as one flat object. With no properties the model filled
    args with nothing at all (8 real calls, 2026-11-11), so every tool fell back to the page's ticker.
    Python still keeps only the arguments each tool declares (chat_tools.validate)."""
    from momx import chat_tools
    props: dict = {}
    for _, entry in sorted(chat_tools.registry(scope).items()):
        for name, spec in ((entry.get("args") or {}).get("properties") or {}).items():
            spec = spec if isinstance(spec, Mapping) else {}
            kind = spec.get("type") if spec.get("type") in ("string", "integer", "number", "boolean") else "string"
            props.setdefault(name, {"type": kind})
    return {"type": "object", "properties": props}


def planner_schema(scope: str = "owner") -> dict:
    from momx import chat_tools
    return {"type": "object", "properties": {
        "calls": {"type": "array", "maxItems": MAX_CALLS, "items": {"type": "object", "properties": {
            "tool": {"type": "string", "enum": sorted(chat_tools.registry(scope))}, "args": planner_args(scope)}, "required": ["tool", "args"]}},
        "general": {"type": "boolean"}}, "required": ["calls", "general"]}


def planner_system(scope: str = "owner") -> str:
    from momx import chat_tools
    base = UNDERSTAND_SYSTEM if scope == "owner" else SCANNER_UNDERSTAND_SYSTEM
    return base + "\nTOOLS: " + json.dumps(chat_tools.planner_catalog(scope), separators=(",", ":"))


def clear_plans() -> None:
    with _PLANS_LOCK:
        _PLANS.clear()


def _held_plan(key: tuple) -> dict | None:
    with _PLANS_LOCK:
        held = _PLANS.get(key)
        return held[1] if held and time.monotonic() - held[0] < PLAN_SECONDS else None


def _keep_plan(key: tuple, plan: dict) -> None:
    with _PLANS_LOCK:
        _PLANS[key] = (time.monotonic(), plan)
        _PLANS.move_to_end(key)
        while len(_PLANS) > _PLAN_MAX:
            _PLANS.popitem(last=False)


def _clean_plan(data: Any, scope: str) -> dict | None:
    """The AI's plan with only the tools this session may use and only arguments that check out. None if nothing is left."""
    from momx import chat_tools
    if not isinstance(data, Mapping):
        return None
    tools = chat_tools.registry(scope)
    calls = []
    for call in data.get("calls") if isinstance(data.get("calls"), list) else []:
        if len(calls) >= MAX_CALLS or not isinstance(call, Mapping) or call.get("tool") not in tools:
            continue
        calls.append({"tool": call["tool"], "args": chat_tools.validate(tools[call["tool"]].get("args"), call.get("args"))[0]})
    general = data.get("general") is True
    return {"calls": calls, "general": general} if calls or general else None


def understand(question: str, today: str, context: Mapping | None = None,
               ask: Callable[[str, str, dict], dict] | None = None, seconds: float = TIMEOUT_SECONDS, scope: str = "owner") -> dict | None:
    """What a question needs, as {"calls": [{"tool", "args"}], "general": bool}; None when the AI cannot say.
    The prompt carries the question, the date and checked context, nothing from any file."""
    from momx import chatbot
    text = str(question)[:300]
    about = _about(context)
    payload: dict = {"question": text, "today": today, "conversationIsAbout": about}
    page = chatbot._conversation(context)[0].get("pageTicker") if isinstance(context, Mapping) else None
    page = str(page or "").upper()
    if _PAGE_WORDS.search(text.lower()) and re.fullmatch(r"[A-Z.]{1,6}", page):
        payload["pageTicker"] = page
    chosen = ask if ask is not None else _default_ask
    key = (chosen, scope, " ".join(text.lower().split()), today, json.dumps(about, sort_keys=True), payload.get("pageTicker"))
    held = _held_plan(key)
    if held is not None:
        return held
    out = _bounded(chosen, planner_system(scope), json.dumps(payload, separators=(",", ":")), planner_schema(scope), seconds, scope)
    plan = _clean_plan(out.get("data") if out and out.get("ok") else None, scope)
    if plan is not None:
        _keep_plan(key, plan)
    return plan


# ---------------------------------------------------------------- step 5: the writer and its number check
#: A number with what is around it: an optional sign before it (never the dash of "9:30-11:00" or "2026-11-10") and an optional %.
_NUMBER = re.compile(r"(?<![\w.:%)\]])([+\-−])?\$?(\d[\d,]*(?:\.\d+)?)|(?<=[\w.:%)\]])()(\d[\d,]*(?:\.\d+)?)")
_PERCENT = re.compile(r"\s?(%|percent\b)", re.I)
_UP_WORD = re.compile(r"\b(?:up|gain(?:ed|s)?|rose|rise[sn]?|higher|profit(?:\s+of)?|made|jumped|climbed|above)\s*(?:about\s+|nearly\s+|by\s+|of\s+)?$", re.I)
_DOWN_WORD = re.compile(r"\b(?:down|lost|loss(?:\s+of)?|fell|fall(?:en|s)?|dropped|lower|declined|below)\s*(?:about\s+|nearly\s+|by\s+|of\s+)?$", re.I)


def _plain(raw: str) -> str:
    value = raw.replace(",", "")
    if "." in value:
        value = value.rstrip("0").rstrip(".")
    return value.lstrip("0") or "0"


def _tokens(text: str) -> list[tuple[str, str, bool, bool]]:
    """Every number in a text as (value, sign, is a percentage, was the sign written).
    "$1,350.50" -> ("1350.5", "", False, False); "down 5%" -> ("5", "-", True, False); "+5%" -> ("5", "+", True, True)."""
    out = []
    for m in _NUMBER.finditer(text):
        raw = m.group(2) or m.group(4)
        if raw is None:
            continue
        sign = {"+": "+", "-": "-", "−": "-"}.get(m.group(1) or "", "")
        implied = ""
        if not sign:
            before = text[max(0, m.start() - 24):m.start()]
            implied = "+" if _UP_WORD.search(before) else "-" if _DOWN_WORD.search(before) else ""
        out.append((_plain(raw), sign or implied, bool(_PERCENT.match(text, m.end())), bool(sign)))
    return out


_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10"}


def _numbers(text: str) -> set[str]:
    """Every number in a text, without thousands commas and trailing zeros ("$1,350.50" -> "1350.5")."""
    return {token[0] for token in _tokens(text)}


def _number_is_known(token: tuple, source: list[tuple], bare: set[str]) -> bool:
    value, sign, percent, written = token
    if value in bare:
        return True
    same = [item for item in source if item[0] == value and item[2] == percent]
    if not sign:
        return bool(same)
    # A sign the reply wrote itself has to be in the source. A word's direction ("up 5%") may also sit on an unsigned number.
    return any(item[1] == sign or (not written and not item[1]) for item in same)


#: A web address or a bare domain ("agx-verify.example/login"), an e-mail address, and words that ask for a login.
_LINKISH = re.compile(r"https?://|\bwww\.|\b[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|example|app|co|ai|xyz|dev|info|biz|us|me|gg|ly|to|tv|test|site|"
                      r"online|link|click|top|cc|ru|cn|uk|de|live|pro|shop|store|page|cloud|tech)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_ACCOUNT_BAIT = re.compile(r"\b(?:sign(?:ing)?[\s-]*(?:in|on|up)|log(?:ging)?[\s-]*(?:in|on)|login|password|passcode|credentials?|e-?mail|"
                           r"send\s+(?:us|me|your|it)|verify\s+your|account\s+details|click\s+(?:here|the|this))\b", re.I)
#: Telling the reader to trade, in any form.
_TRADE_ORDER = re.compile(r"\b(?:you\s+(?:should|could|might\s+want\s+to|need\s+to|must|ought\s+to)|i\s+(?:recommend|suggest|would)|load\s+up|"
                          r"get\s+in(?:to)?|go\s+(?:long|short)|good\s+(?:time|entry|buy|sell)|time\s+to\s+(?:buy|sell)|consider\s+(?:buying|selling|shorting))\b"
                          r"|\b(?:hold|keep)\s+(?:it|onto|on\s+to|the\s+stock|your\s+shares|shares)\b", re.I)
#: Every form of a trading verb, the passive ones too ("SPY can be bought near 580", "SPY is buyable"; 2026-11-11 review rv4).
_TRADE_VERB = re.compile(r"\b(?:buy|buys|buying|bought|buyable|sell|sells|selling|sold|sellable|short|shorts|shorting|shorted|"
                         r"purchas\w*|acquir\w*)\b", re.I)
#: A trade done to the stock in the passive ("Profits can be taken at 590", "where profits get booked").
_PASSIVE_TRADE = re.compile(r"\b(?:can|could|should|may|might|must|will|would)\s+be\s+(?:bought|sold|taken|booked|added|owned|held|shorted|purchased)\b"
                            r"|\bprofits?\s+(?:get|gets|got|are|is|can\s+be|could\s+be|should\s+be)\s+(?:taken|booked)\b", re.I)
#: A forecast: where a price is going ("SPY will hit 590", "is headed to 590", "upside to 590", "bulls win above 590").
_FORECAST = re.compile(
    r"\b(?:will|won'?t)\s+(?:hit|reach|go|break|rally|run|drop|fall|test|make|climb|rise|sink|slide|bounce|pop|tank|move|get)\b|"
    r"\b(?:is|are|looks?|seems?|'s)\s+(?:going|headed|heading|set\s+up|setting\s+up|primed|poised|ready|on\s+track|about)\s+(?:to|for)\b|"
    r"\b(?:upside|downside)\s+(?:to|toward|towards|of)\b|\bwins?\s+(?:above|below|over|under)\b|\b(?:a\s+)?run\s+(?:to|toward|towards|at)\b|"
    r"\broom\s+to\b|\bbreak\s*out\s+to\b", re.I)
#: Value words: a judgement of a price or a trade ("a bargain", "on sale", "the trade", "a solid play", "upside outweighs downside").
_JUDGEMENT = re.compile(
    r"\b(?:bargains?|gifts?|on\s+sale|deals?|steals?|winners?|deserv\w*|primed|nicely|upside|downside|outweigh\w*|"
    r"the\s+(?:trade|move|play)|(?:is|are|'s)\s+the\s+one|solid|chances?|one\s+to\s+watch|(?:names?|stocks?)\s+to\s+watch|worth\s+a\s+look)\b", re.I)
#: The same value words a general explanation may not use. "Upside", "downside", "deal" and "the move" describe an
#: option there ("a covered call caps the upside", "the size of the move").
_JUDGEMENT_GENERAL = re.compile(
    r"\b(?:bargains?|gifts?|on\s+sale|steals?|deserv\w*|primed|nicely|outweigh\w*|the\s+play|solid|"
    r"one\s+to\s+watch|(?:names?|stocks?)\s+to\s+watch|worth\s+a\s+look)\b", re.I)
#: An option contract in shorthand ("SPY 590C", "580P"): a position word.
_CONTRACT_SHORTHAND = re.compile(r"\b\d+(?:\.\d+)?[CP]\b")

# The advice backstop is a rule about the reply's SHAPE, not a list of bad sentences (2026-11-11 review: 23 of 24
# instruction-style replies such as "Get long SPY into the close" passed a phrase list). A reply may only use
# trade-action or recommendation wording that the facts themselves use; it may not tell the reader to do anything
# to a trade (a sentence that starts with a trading verb); and it may not name a ticker the facts never named.

#: Recommendation wording. On the records path it is shown only when the facts say it themselves; a general
#: explanation may never use it (only "could"/"would" with a reader as the subject count there: "buyers would lose" is a definition).
_RECOMMEND = re.compile(
    r"\b(?:should|ought\s+to|could|would|(?:might|may)\s+want\s+to|recommend\w*|suggest\w*|i'?d|i\s+(?:would|like|think|believe|feel|prefer)|"
    r"avoid\w*|looks?\s+(?:smart|good|great|attractive|cheap|interesting|compelling|strong|bullish|bearish|like\s+a)|"
    r"(?:smart|good|great|attractive|cheap|compelling|ideal|perfect|nice|solid)\s+(?:move|idea|setup|set-up|entry|price|level|spot|trade|bet|buy|sell|place|time|chance)|"
    r"worth(?!less)\w*|opportunit\w+|makes?\s+sense|consider(?:s|ed|ing)?\s+(?:buy|sell|short|add|tak|enter|get|go|open|own|grab|load|trim|exit|clos)\w*|"
    r"(?:time|moment)\s+to(?!\s+expir)|now\s+is\s+the|my\s+(?:pick|view|call|take)|"
    r"(?:be|being)\s+a\s+(?:buyer|seller)|dip\s+to\s+(?:own|buy)|back\s+up\s+the\s+truck|load\s+the\s+boat|don'?t\s+miss|"
    r"(?:can'?t|cannot)\s+go\s+wrong|no[\s-]?brainer|undervalued|overvalued|now['\u2019]s|deserv\w*|solid|"
    r"(?:one|name|stock)s?\s+to\s+watch|(?:likes?|loves?)\s+(?:it|them|this\s+(?:one|stock|name|setup|trade)|(?-i:\$?[A-Z]{2,5}\b)))\b", re.I)
_RECOMMEND_GENERAL = re.compile(
    r"\b(?:you|one|traders?|investors?|i|we|they)\s+(?:could|would|can)\s+(?:buy|sell|short|get|go|open|add|enter|take|load|grab|own|start)\b"
    # "Traders like to put them on SPX ahead of earnings", "selling them ... is the way to collect premium" (2026-11-11 review rv4).
    r"|\b(?:traders?|investors?|people|many|most|some|they|pros)\s+(?:like|love|prefer|choose|want)\s+to\s+(?:put|buy|sell|open|use|trade|place|own|hold|short|write|enter|add|run)\b"
    r"|\bthe\s+(?:best\s+|easiest\s+|only\s+)?way\s+to\s+(?:collect|make|earn|profit|play|trade|get|capture|generate|go|bank)\b", re.I)
#: Entry and exit wording: telling someone to get into, add to, ride or get out of a trade. A general explanation
#: may not use it either ("scale in now" is not a definition).
_ENTRY = re.compile(
    r"\b(?:(?:go(?:ing)?|get(?:ting)?|stay(?:ing)?|got|went|be|being|remain(?:ing)?)\s+(?:long|short)\b|"
    r"(?:go(?:ing)?|get(?:ting)?|stay(?:ing)?|got|went|remain(?:ing)?)\s+(?:in|out|with)\b|"
    r"(?<!\bcame\s)(?<!\bcome\s)(?<!\bcomes\s)(?<!\bclosed\s)(?<!\bsold\s)(?<!\bpriced\s)(?:in|out)\s+(?:at|near|above|below|under)\b|"
    r"exit\w*|stop(?:ped)?\s+(?:out|loss)|stops?\s+(?:at|under|below|above|near)|(?:set|place|put|move|use|keep)\s+(?:a\s+|your\s+|the\s+)?stops?|"
    r"target\w*|trim\w*|lock\w*\s+in|rid(?:e|es|ing)\b|bet(?:s|ting)?\b|initiat\w*|open(?:ing)?\s+(?:a|an|new|some|up)\b|"
    r"step(?:s|ping)?\s+in|load(?:ing)?\s+(?:up|in)|accumulat\w*|scal(?:e|es|ing)\s+(?:in|out|into|up)|"
    r"add(?:s|ing)?\s+(?:to|some|more|exposure|on|here|a|an|the)\b|take\s+(?:a\s+)?(?:profits?|gains?|the\s+trade|some\s+off|a\s+position)|"
    r"(?<!\bis\s)(?<!\bare\s)(?<!\bwas\s)(?<!\bwere\s)pick(?:ing)?\s+up|the\s+play|go\s+for|cut\s+(?:it|your|losses|the\s+(?:position|trade|losses?))\b|enter\w*|entr(?:y|ies)|buy(?:ing)?\s+the\s+dip|chas(?:e|ing))", re.I)
#: Position words: fine in a definition ("a long option position loses theta"), but on the records path only when the
#: facts use them too ("Long SPY here", "add exposure", "Buyers have a good setup").
_POSITION_TALK = re.compile(
    r"\b(?:(?<!as\s)(?<!how\s)long(?![\s-]*(?:term|dated|time|run|as|before|ago)\b)|short(?![\s-]*(?:term|dated|time|interest|squeeze)\b)|"
    r"own(?:s|ed|ing)?|exposure|buyers?|sellers?|positions?|calls?|puts?|dip)\b", re.I)
#: A trading verb at the start of a sentence: an order to the reader ("Trim near 590.", "Set a stop at 580.").
_ORDER_VERBS = frozenset("""buy sell short cover get go exit enter take add load trim lock ride bet initiate stay hold keep set target
scale accumulate grab avoid consider wait pick cut sit play place try aim own pile start look watch put stop step lean fade chase
protect hedge roll book flip dump unload tighten move rotate switch eye snag""".split())
#: What makes a verb-looking first word a noun instead ("Short interest is ...", "Open interest", "Close was 585", "Put options give ...").
_NOUN_NEXT = frozenset("""is are was were has have had means mean meant refers measures shows tells interest options option contracts contract
price prices wall walls side ratio volume order orders squeeze premium premiums decay sellers buyers of and or positions position spread spreads
strike strikes time value window windows signal signals rule rules level levels filled fill fills ran came went call calls term dated
loss""".split())
#: Index symbols a general explanation may name ("the VIX is worked out from SPX option prices").
_INDEX_NAMES = frozenset("SPX SPY QQQ NDX VIX DJI DIA IWM RUT XSP".split())
#: Words that start a factual sentence before a ticker ("Today SPY rose"); anything else right before a ticker is an order ("Watch SPY").
_NOT_ORDER_LEAD = frozenset("""today yesterday now here there at on in by since after before then so and but also both while when as earlier overall
meanwhile currently still only the a an this that its his their our my your for from of to with without near above below under over between into
across per about around against during until through within like unlike whereas although though if because once where either neither each every
all most some many few no not even just yet shares stock stocks ticker tickers monday tuesday wednesday thursday friday saturday sunday
january february march april may june july august september october november december last next index""".split())


#: Bare verbs that make a clause after "so", "then", a comma or a semicolon an order ("..., so put one on this week").
#: Only for a clause inside a sentence: a sentence's own start is judged by its shape (_order_start), not by a list.
_CLAUSE_VERBS = _ORDER_VERBS | frozenset("""hit pull jump snap think copy make use favor favour prefer do don't dont never hop dive pounce lift bid
scoop swing pick try grab sell buy write wait sit hold open own""".split())
#: Words that mark a clause as having a subject and a finite verb ("Volume is ...", "Delta measures ...", "Investors sold ...").
_FINITE = frozenset("""is are was were am has have had does did can could will would may might must shall should means mean meant
refers measures shows tells gives give gains loses earns profits sells buys rises rose falls fell closed opened sits sat trades traded moved
moves ran runs came went goes happens happened stays stayed holds held lets makes made caps caps limits lost won led saw said says
sank slid jumped dropped climbed slipped surged gained added cut beat missed hit stands stood ended finished opened sank leads lags
remains remained helps helped pushed pulled weighed lifted dragged""".split())
#: Plurals that are not verbs, so "Favor calls above 580" does not read as a subject and a verb.
_PLURAL_NOUNS = frozenset("""calls puts options shares gains losses contracts trades stocks names tickers dips walls strikes spreads premiums
profits levels points bulls bears buyers sellers hands sides bids offers stops targets entries exits moves days weeks hours minutes
dollars cents funds etfs futures""".split())
#: A sentence may start with one of these and still be a statement ("Up $1,350 on 12 contracts", "Higher IV makes ...").
_SUBJECT_LEAD = _NOT_ORDER_LEAD | frozenset("""up down flat higher lower more less much roughly nearly almost about yes it's its it they we he she
there's here's that's what's who which whose how what why where when volume price market markets bulls bears investors traders""".split())
#: Telling the reader something in the second person, or giving the writer's own opinion.
_SECOND_PERSON = re.compile(r"\b(?:you|your|yours|yourself|you'?d|you'?re|you'?ll|you'?ve|y'all)\b", re.I)
#: "We like SPY here", "our pick", "let's": the plural is an opinion too (2026-11-11 review rv4). "US" in capitals is the country.
_FIRST_PERSON = re.compile(r"(?i:\b(?:I'?m|I'?d|I'?ve|I'?ll|my|mine|me|we|we['’](?:re|d|ve|ll)|our|ours|ourselves|let['’]s)\b)"
                           r"|\b(?:us|Us)\b|(?<![\w'-])I(?![\w'-])")
#: The reader as the one acting in a general explanation ("you could", "you'd", "your best move"); "a stock you own" is fine.
_READER_ACTS = re.compile(r"\b(?:you'?d|you'?re|you'?ll|yourself|your|you\s+(?:can|could|should|may|might|must|need|want|will|would|ought|have\s+to|too)\b)", re.I)
#: Evaluative and comparative wording: a judgement of a trade, never a fact from a record.
_EVALUATIVE = re.compile(
    r"\b(?:best|(?<!\bor\s)better|smarter|smartest|smart|safer|safest|cheap(?:er|est|ly)?|expensive|prudent|wise(?:ly|r|st)?|ideal(?:ly)?|"
    r"edge|favou?r(?:s|ed|ing|able)?|prefer\w*|way\s+to\s+(?:go|play)|green\s+light|reasonable|bullish|bearish|line\s+in\s+the\s+sand|"
    r"choice|strong\s+candidate|great|perfect|work(?:s|ed)?\s+best|(?:might|may)\s+want|don'?t|do\s+not|never|"
    r"risk\b[^.;!?]*\breward|reward\b[^.;!?]*\brisk|"
    r"(?:calls?|puts?|trades?|positions?|entry|condors?|spreads?|straddles?|strangles?|options?)\s+(?:right\s+)?now|now\s+is)\b", re.I)
#: The same judgements a general explanation may not make. Comparatives of price ("more expensive") and outlook words
#: describe an option there ("Higher IV makes calls more expensive", "a bullish strategy"); risk and reward define a spread.
_EVALUATIVE_GENERAL = re.compile(
    r"\b(?:best|(?<!\bor\s)better|smarter|smartest|smart|safer|safest|prudent|wise(?:ly|r|st)?|ideal(?:ly)?|edge|favou?r(?:s|ed|ing|able)?|"
    r"prefer\w*|way\s+to\s+(?:go|play)|green\s+light|reasonable|choice|strong\s+candidate|great|perfect|work(?:s|ed)?\s+best|"
    r"(?:might|may)\s+want|don'?t\s+(?:fight|miss|wait|chase|sell|buy|hold|forget)|line\s+in\s+the\s+sand|"
    # 2026-11-11 review rv4: "a good hedge", "the most consistent way to make money", "pays off", "a winning trade" said of a strategy.
    r"(?:good|consistent|reliable|easy|solid|safe)\s+(?:hedges?|trades?|ways?|money|income|bets?|plays?|strateg\w+)|"
    r"(?:is|are|be|makes?|made|making)\b[^.;!?,]{0,30}?\bwinning\s+(?:trades?|bets?|plays?)|pays?\s+off|paid\s+off|(?:to|will|can|could|would|helps?)\s+make\s+(?:easy\s+|good\s+|steady\s+)?money)\b", re.I)
#: A time word beside a strategy or a ticker in a general explanation ("ideal right now", "put one on this week").
_TIME_WORD = re.compile(r"\b(?:right\s+now|now|here|today|tonight|tomorrow|this\s+(?:week|morning|afternoon|month)|currently|at\s+the\s+moment)\b", re.I)
_STRATEGY_NOUN = re.compile(r"\b(?:calls?|puts?|options?|condors?|spreads?|straddles?|strangles?|butterfl\w+|trades?|positions?|premium|shares?|stocks?)\b", re.I)
#: A "call wall"/"put wall" names a level, not an option a reader would trade.
_WALL_WORD = re.compile(r"\b(?:call|put)s?(?=\s+walls?\b)", re.I)
_FILLERS = ("so", "then", "just", "now", "maybe", "perhaps", "please", "and", "but", "ok", "okay", "yes", "honestly", "simply", "or")


def _sentences(text: str) -> list[str]:
    return [part.strip(" \"'()") for part in re.split(r"(?<=[.!?;:])\s+|\s+[-–—]\s+", text) if part.strip(" \"'()")]


def _clauses(sentence: str) -> list[str]:
    """The parts of a sentence after "so", "then", a comma or a semicolon ("..., so put one on")."""
    return [part.strip() for part in re.split(r"[,;]\s*|\s+(?:so|then)\s+", sentence)[1:] if part.strip()]


def _words(text: str) -> list[str]:
    words = [word.rstrip(".") for word in re.findall(r"[A-Za-z$0-9][A-Za-z'$.0-9-]*", text)]
    while words and words[0].lower() in _FILLERS:
        words = words[1:]
    return words


#: Bare verbs that are finite after a plural noun subject ("Put buyers profit", "Call sellers gain").
_BARE_AFTER_PLURAL = frozenset({"profit", "gain", "lose", "earn"})


def _is_finite(word: str) -> bool:
    low = word.lower()
    if low in _FINITE:
        return True
    if low in _PLURAL_NOUNS or low in _CLAUSE_VERBS or len(low) <= 3:
        return False
    # "-ed" ("Investors cheered"), or a third-person "-s" ("Chipmaker trims guidance") that is not a plural above.
    return low.endswith("ed") or (len(low) > 4 and low.endswith("s") and not low.endswith(("ss", "us", "is", "ys")))


def _order_start(words: list[str], source_low: str, tickers: set[str], starts: set[str]) -> bool:
    """Does a sentence that begins with these words tell the reader to do something? Judged by its shape: a statement
    starts with a subject (a ticker, a number, a company, a determiner or pronoun, a time word, a word the facts start a
    sentence with) or reaches a finite verb within its first four words ("Volume is ...", "Implied volatility is ...").
    Anything else ("Snap up SPY", "Hit the bid", "Don't fight the trend", "Make sure ...", "Use 580 as ...") is an order."""
    if not words:
        return False
    raw = words[0]
    first, second = re.sub(r"'s$", "", raw.lower()), (words[1].lower() if len(words) > 1 else "")
    if source_low and _in_words(" ".join(words[:2]), source_low):
        return False                                       # the facts' own wording
    if first in _CLAUSE_VERBS and second not in _NOUN_NEXT:
        return True
    if first in ("don't", "dont", "never", "always", "let's", "lets", "do"):
        return True
    bare = raw.lstrip("$")
    if (re.fullmatch(r"[\d$.,%+-]+", raw) or bare.upper() in tickers or re.fullmatch(r"[A-Z]{2,6}", bare) or first in _SUBJECT_LEAD
            or first in starts or _COMPANY.match(" ".join(words[:3]))):
        return False
    # A gerund is a subject that may reach its verb anywhere ("Being short a stock means ..."); a plural noun subject
    # takes a bare verb ("Put buyers profit when ...", "Call sellers gain if ..."). 2026-11-11 review rv4.
    gerund = first.endswith("ing") and len(first) > 4 and first not in _CLAUSE_VERBS
    reach = words[1:] if gerund else words[1:4]
    for index, word in enumerate(reach):
        if _is_finite(word):
            return False
        before = (reach[index - 1] if index else raw).lower()
        if word.lower() in _BARE_AFTER_PLURAL and (before in _PLURAL_NOUNS or before.endswith(("ers", "ors"))):
            return False
    return True


def _orders(text: str, source_low: str, tickers: set[str], starts: set[str] = frozenset()) -> bool:
    """Does any sentence or clause tell the reader to do something? A sentence by its shape (_order_start); a clause
    after "so", "then", a comma or a semicolon when it starts with a bare verb."""
    for sentence in _sentences(text):
        words = _words(sentence)
        label = sentence.endswith(":") and len(words) <= 2 and bool(words) and words[0].lower() not in _CLAUSE_VERBS   # "Profit:"
        if not label and _order_start(words, source_low, tickers, starts):
            return True
        for clause in _clauses(sentence):
            words = _words(clause)
            if words and words[0].lower() in _CLAUSE_VERBS and (len(words) < 2 or words[1].lower() not in _NOUN_NEXT) \
                    and not (source_low and _in_words(" ".join(words[:2]), source_low)):
                return True
    return False


def _sentence_starts(source: str) -> set[str]:
    """The words the facts themselves start a sentence with ("Volume", "Paper"): a reply may start with them too."""
    return {words[0].lower() for words in (_words(s) for s in _sentences(source)) if words}
#: Capitals that are words or units, not tickers, when a reply writes them.
_NOT_A_NAME = frozenset("""ET AM PM EMA EMAS SMA VWAP OI IV ATM OTM ITM ETF ETFS CPI PPI FOMC FED EPS PE RSI MACD DTE EM HOD LOD NYSE SEC GDP USD CEO
H L HL RVOL SQZ AI OK US UK EU Q1 Q2 Q3 Q4 YTD MTD WTD""".split())


def _in_words(phrase: str, source_low: str) -> bool:
    """Is a phrase (or its singular/plural) in the source as whole words?"""
    phrase = " ".join(phrase.lower().split())
    forms = {phrase, phrase[:-1] if phrase.endswith("s") else phrase + "s"}
    return any(re.search(r"(?<![\w$])" + r"\s+".join(map(re.escape, form.split())) + r"(?![\w])", source_low) for form in forms if form)


def _reply_tickers(text: str) -> list[str]:
    """What a reply writes in a ticker's shape: capitals or $word, less words and units."""
    from momx import chatbot
    return [word for word in re.findall(r"(?<![A-Za-z0-9])\$?([A-Z]{2,5})(?![A-Za-z0-9'])", text)
            if word not in _NOT_A_NAME and word not in chatbot.COMMON and word not in _NOT_TICKER]


def _stranger_name(text: str, source: str, question: str, allowed: frozenset = frozenset()) -> bool:
    """Does a reply name a ticker or a company that is in neither the facts nor the question? `allowed` names
    pass anyway (index names in a general explanation)."""
    seen = (source + " " + question).lower()
    for found in _COMPANY.finditer(text):
        name = found.group(1).lower()
        if not _in_words(name, seen) and not _in_words(COMPANY_NAMES[name], seen):
            return True
    for word in _reply_tickers(text):
        if word in allowed:
            continue
        if not re.search(r"(?<![a-z0-9])\$?" + re.escape(word.lower()) + r"(?![a-z0-9])", seen):
            return True
    return False


def _root(word: str) -> str:
    """A word without its ending, so the facts' "trims" covers the reply's "trimmed"."""
    word = word.lower()
    for end in ("ming", "ting", "ping", "ing", "med", "ted", "ped", "ed", "es", "s"):
        if word.endswith(end) and len(word) - len(end) >= 3:
            return word[:-len(end)]
    return word


def _unused(pattern: re.Pattern, text: str, source_low: str) -> bool:
    """Does the reply use wording of this kind that the facts do not use themselves (in any form of the same words)?"""
    for found in pattern.finditer(text):
        phrase = found.group(0)
        if _in_words(phrase, source_low):
            continue
        if not all(re.search(r"(?<![\w$])" + re.escape(_root(word)), source_low) for word in phrase.split()):
            return True
    return False


#: The describing words a records reply may use beyond the facts' and the question's own words: grammar, time, direction,
#: size and where a price sits. Nothing here buys, sells, holds, targets, forecasts or judges (no "can", "will", "good",
#: "move", "trade", "play", "setup", "watch", "bargain", "spot", "chance", "upside", "room", "ready", "solid", "bulls").
_NEUTRAL_WORDS = frozenset("""
a an the this that these those it its it's they them their there there's here here's that's what what's which who whose how why when
where while whereas as than then so and but or nor also both either neither each every all any some no not none nothing only just still yet
even again already too very quite rather more most less least much many few several other another same such of in on at to from by for
with without into onto over under above below beneath between among near nearby around about after before since until till through
throughout during within across against per via versus vs along toward towards past beyond up down off out is are was were be been being
am has have had having do does did done isn't aren't wasn't weren't hasn't haven't hadn't doesn't didn't if because though although
whether one two three four five six seven eight nine ten first second third last next previous prior earlier later lately recently
currently now today yesterday tonight morning afternoon evening midday noon night session sessions day days week weeks month months year
years hour hours minute minutes seconds time times ago far so
rose rise rises rising risen fell fall falls falling fallen dropped drop drops dropping declined decline declines declining climbed
climbs climbing gained gain gains gaining lost loss losses losing slipped slips slid slide slides sliding dipped dips edged jumped jumps
surged sank tumbled rallied rebounded recovered advanced retreated eased firmed steadied held holds holding stayed stays remained remains
sits sat sitting stood stands standing trades traded trading closed close closes closing opened open opens opening ended ends finished
finishes started starts began moved moves moving changed change changes unchanged flat higher lower high low highs lows highest lowest
level levels price prices priced quote quoted latest newest current percent pct point points cents cent dollar dollars share shares
volume volumes average range ranged narrow wide tight gap gapped calm quiet mixed steady choppy busy active light heavy total net
roughly nearly almost approximately exactly slightly modestly sharply little small smaller large larger big bigger biggest largest
smallest farther further closer closest nearer nearest middle midway halfway apart away inside outside top bottom ahead behind beside
picking pickup intraday premarket market markets index indexes indices stock stocks ticker tickers company companies sector sectors
fund funds etf stream live candle candles bar bars data chart charts record records file saved shows show showed shown says said reads
read lists listed reports reported report headline headlines news story stories source wire account strike strikes expiry expiration
expirations chain contract contracts option options oi interest wall walls value values mid et am pm est edt utc according indicates
noted mentions mentioned respectively overall together compared while went came come comes made led leads lagged lags trailed sit
sitting stand plain sentence figure figures asked profit profits""".split())


def _vocabulary_words(text: str) -> list[str]:
    return [re.sub(r"['’]s$", "", word.lower()) for word in re.findall(r"[A-Za-z]+(?:['’][A-Za-z]+)?", text)]


def _outside_vocabulary(text: str, source: str, question: str) -> bool:
    """Does a records reply use a word that is in neither the facts, the question nor the describing words above?
    A word counts as the facts' own in another form ("trimmed" for "trims", "exited" for "exit")."""
    own = set(_vocabulary_words(source + " " + str(question)))
    roots = {_root(word) for word in own}
    text = re.sub(r"\byou\s+(?:asked|mentioned|said|wrote)\b", " ", text, flags=re.I)       # "You asked about ..." points back
    for word in _vocabulary_words(text):
        if word in _NEUTRAL_WORDS or word in own or _root(word) in roots:
            continue
        if word.endswith("n't") and word[:-3] in _NEUTRAL_WORDS:
            continue
        return True
    return False


def _unsafe(text: str, source: str, question: str, closed: bool = True) -> bool:
    """A records reply the prompt alone cannot be trusted to keep off the screen: a link, an e-mail address, a login
    request; any trade-action or recommendation wording the facts do not use themselves (a buy/sell word, "long",
    "own", "exposure", "exit", "target", "trim", "should", "worth", "looks smart" ...); a sentence that tells the
    reader to do something; or a ticker or company the facts and the question never named."""
    low = text.lower()
    if _LINKISH.search(text) or _EMAIL.search(text) or _ACCOUNT_BAIT.search(text) or _TRADE_ORDER.search(text) or _ADVICE_REPLY.search(low):
        return True
    said = {word.lower() for word in _TRADE_VERB.findall(source)}
    if any(word.lower() not in said for word in _TRADE_VERB.findall(text)):
        return True
    source_low = source.lower()
    if _unused(_RECOMMEND, text, source_low) or _unused(_ENTRY, text, source_low) or _unused(_EVALUATIVE, text, source_low):
        return True
    # Passive trades, forecasts and value words the facts do not use (2026-11-11 review rv4: "SPY can be bought near 580",
    # "SPY will hit 590 tomorrow", "SPY is a bargain at 585.20"), and an option contract in shorthand ("SPY 590C").
    if (_unused(_PASSIVE_TRADE, text, source_low) or _unused(_FORECAST, text, source_low) or _unused(_JUDGEMENT, text, source_low)
            or any(found.group(0).lower() not in source_low for found in _CONTRACT_SHORTHAND.finditer(text))):
        return True
    # A closed vocabulary: every word must come from the facts, the question or a fixed list of describing words.
    # A word lists cannot foresee ("a gift at 585.20", "a spot in the portfolio") is refused, not let through.
    if closed and _outside_vocabulary(text, source, question):
        return True
    # An option noun counts as the facts' own only where they use it as one: "Call wall 590" is a level, not calls.
    if _unused(_POSITION_TALK, _WALL_WORD.sub(" ", text), _WALL_WORD.sub(" ", source_low)):
        return True
    # Talking to the reader ("your best move", "you'd be in calls") or giving an opinion ("I'm bullish") the facts never do.
    # "You asked about ..." only points back at the question.
    if (_unused(_SECOND_PERSON, re.sub(r"\byou\s+(?:asked|mentioned|said|wrote)\b", " ", text, flags=re.I), source_low)
            or _unused(_FIRST_PERSON, text, source_low)):
        return True
    named = {word.lstrip("$") for word in _reply_tickers(text)} | {word.lstrip("$") for word in _reply_tickers(source)}
    if _orders(text, source_low, named, _sentence_starts(source)):
        return True
    return _stranger_name(text, source, question)


def _unsafe_general(text: str, question: str) -> bool:
    """A general explanation has no facts, so the rule is narrower but never looser about advice: no link or login
    request, no recommendation, entry or exit wording, no order to the reader, and no ticker the question did not
    name (index names may explain a concept). Plain option nouns (call, put, long, short, position, strike, premium,
    buyer, seller, buying, selling) are what a definition is made of, so they stay."""
    if _LINKISH.search(text) or _EMAIL.search(text) or _ACCOUNT_BAIT.search(text) or _ADVICE_REPLY.search(text.lower()):
        return True
    q_low = str(question).lower()
    rec = [found.group(0) for found in _RECOMMEND.finditer(text) if found.group(0).lower() not in ("could", "would")]
    if any(not _in_words(word, q_low) for word in rec) or _RECOMMEND_GENERAL.search(text) or _TRADE_ORDER.search(text):
        return True
    # The same judgements, opinions and reader-as-actor wording as the records path; the question's words stand in
    # for the facts. "A stock you already own" defines; "your best move", "you'd be in calls", "I'm bullish" advise.
    if _unused(_EVALUATIVE_GENERAL, text, q_low) or _READER_ACTS.search(text) or _FIRST_PERSON.search(text):
        return True
    tickers = {word.lstrip("$") for word in _reply_tickers(text)}
    if _orders(text, "", tickers):
        return True
    for sentence in _sentences(text):
        # A definition may name the entry it defines ("what is an entry") or describe one in the third person
        # ("closing the contract and opening a new one", "it locks in gains"); entry wording with the reader as its
        # subject ("traders should open a ...") is advice. An order with entry wording is caught by _orders above.
        if _unused(_ENTRY, sentence, q_low) and re.search(
                r"\b(?:you|traders?|investors?|we|one)\s+(?:\w+\s+)?(?:should|could|can|must|need|ought|may|might|will|would)\b", sentence, re.I):
            return True
        # A time word beside a strategy or a ticker is a call to act now ("ideal right now since SPX is range-bound").
        if _unused(_TIME_WORD, sentence, q_low) and (_STRATEGY_NOUN.search(sentence) or any(t in sentence for t in tickers)):
            return True
        # A forecast about a ticker, a price or a time ("Iron condors on SPX will make money this week"); "an option's value
        # will fall each day" without one is a definition (2026-11-11 review rv4).
        named = any(re.search(r"(?<![A-Za-z])\$?" + re.escape(t) + r"(?![A-Za-z])", sentence) for t in tickers | _INDEX_NAMES)
        if _unused(_FORECAST, sentence, q_low) and (named or re.search(r"\d", sentence) or _TIME_WORD.search(sentence)):
            return True
    if _unused(_JUDGEMENT_GENERAL, text, q_low):
        return True
    return _stranger_name(text, "", str(question), _INDEX_NAMES)


def checked(reply: Any, lines: list[str], question: str = "", trade_words: bool = False, closed: bool = True) -> str | None:
    """The AI's reply if it is safe to show: plain text of sane length whose every number is in the record
    lines (or was in the question itself), with the same sign and the same %. "Three contracts" in a line
    lets the reply say "3 contracts". Never a link, an e-mail address, a login request or advice to trade;
    trade-action and recommendation wording only if the lines use it. `trade_words` marks a general explanation:
    option nouns and buy/sell words may explain the idea, advice and entry wording may not. `closed` (the default) also
    holds a records reply to a closed vocabulary: the facts' words, the question's and the describing words. Only a
    headline's reason (chat_why) turns it off, since it says what a headline means in other words; every list check
    above still applies to it."""
    text = " ".join(str(reply or "").split())
    if not text or len(text) > REPLY_MAX or "```" in text:
        return None
    if _unsafe_general(text, question) if trade_words else _unsafe(text, " ".join(lines), question, closed):
        return None                      # the prompt said no; this makes sure
    source = " ".join(lines)
    known = _tokens(source)
    bare = _numbers(str(question)) | {digit for word, digit in _WORDS.items() if re.search(r"\b%s\b" % word, (source + " " + str(question)).lower())}
    if not all(_number_is_known(token, known, bare) for token in _tokens(text)):
        return None                      # it wrote a figure the records do not contain
    return text


# ---------------------------------------------------------------- steps 1 and 2: the sure matcher
_NAV = re.compile(r"\b(?:where|link|take\s+me|go\s+to|bring\s+me|navigate|jump\s+to)\b|^(?:please\s+|pls\s+|can\s+you\s+)?open\b")
_SHOW_ME = re.compile(r"\bshow\s+me\b")
_SCAN = re.compile(r"\b(?:scan(?:ner|ners|s|ned)?|boards?|momx)\b")
_CHART = re.compile(r"\bcharts?\b")
_LEVEL = re.compile(r"\b(?:levels?(?!\s*[0-3]\b)|pivots?|vwap|emas?\d*|smas?\d*|support|resistance)\b")
_SIGNAL = re.compile(r"\bsignals?\b")
_LABEL = re.compile(r"\b(call|put)(m|w|d|\d{1,2}h|\d{1,2}m)\b", re.I)
_CHAIN = re.compile(r"\bchains?\b")
_OPTIONS = re.compile(r"\boptions?\b")
_WALL = re.compile(r"\bwalls?\b|\bhigh\s*oi\b")
_EM = re.compile(r"\bexpected\s+moves?\b|\bem\b")
_MAX_PAIN = re.compile(r"\bmax\s*pain\b")
_NEWS = re.compile(r"\b(?:news|headlines?)\b")
_EARN = re.compile(r"\bearnings?\b")
_SECTOR = re.compile(r"\bsectors?\b|\bheat\s?map\b")
_PREMARKET = re.compile(r"\bpre\s?-?market\b")
_LONG_TERM = re.compile(r"\blong\s*term\b|\bbuffett\b")
#: Open interest on expiries months out (2026-11-16, "Nvda check the leap which one had more OI", "I want any stocks 1 months
#: to 12 months which strikes have high oi with delta"): both words, an OI word and a months word, or it is not this.
_OI_WORD = re.compile(r"\b(?:oi|open\s+interest)\b")
_MONTHS_SPAN = re.compile(r"\bleaps?\b|\blong[\s-]*dated\b|\bmonths?\b|\bmonthly\s+expir\w*\b|\b(?:1|one)\s*(?:yr|years?)\b")
#: A LEAP question about strikes with no OI word (2026-11-19, "See nvda leaps which strike is good?" got the Long Term
#: report with no strike in it). The chat never picks a strike; the strikes holding the most open interest are the fact
#: it has, so the question goes to the same long-dated board.
_LEAP_WORD = re.compile(r"\bleaps?\b")
_STRIKE_WORD = re.compile(r"\bstrikes?\b|\bcontracts?\b")
#: "Leap which one is good" (2026-11-20, SPCX on screen) has no strike word and went to the AI, which did not answer,
#: so he got release notes. A LEAP question asking which one / good / best is the same strikes question.
_LEAP_CHOICE = re.compile(r"\bwhich\b|\bgood\b|\bbest\b|\bbetter\b|\bpick\b|\bchoose\b")
#: Open interest on a ticker with no months word (2026-11-19, "Oi for nvda buyers or sellers?" got only the list of
#: things the chat can answer, because the AI that reads unsure questions was over its free limit). The words are
#: certain enough: the High OI board, and one plain line when the question asks who is buying or selling.
_OI_SIDE_WORD = re.compile(r"\bbuy(?:ers?|ing)?\b|\bsell(?:ers?|ing)?\b|\bbought\b|\bsold\b|\bbullish\b|\bbearish\b")
_OI_ELSEWHERE = re.compile(r"\b(?:page|scanner|finder|board|bots?|chains?|max\s*pain|alerts?|go\s+to|take\s+me)\b")
OI_NO_SIDE_LINE = ("Open interest counts the contracts still open. Each one has a buyer and a seller, so it cannot show "
                   "who is buying or selling, or a direction. Here is where the open interest sits.")
#: "If 135 broke below", "nvda breaks down under 140", "what if it goes above 150", "if spy loses 700" (2026-11-20).
_BREAK_SIDE = re.compile(r"(?P<below>\bbelow\b|\bunder\b|\bbr(?:eaks?|oke|eaking)\s+down\b|\bbreakdown\b|\blos(?:es?|t|ing)\b|\bfalls?\b|\bdrops?\b)"
                         r"|(?P<above>\babove\b|\bover\b|\bbr(?:eaks?|oke|eaking)\s+out\b|\bbreakout\b|\bclears?\b|\breclaims?\b)")
_BREAK_PRICE = re.compile(r"(?<![\d:.])\$?(\d{1,6}(?:\.\d{1,2})?)(?![\d:%])(?!\s*(?:%|percent|days?|weeks?|months?|ema|sma|dma|ma\b|delta|dte|k\b|m\b))")
_BREAK_NOT = re.compile(r"\b(?:ema|sma|vwap|moving\s+average|scanner|stocks|tickers|rsi|percent|delta|oi|open\s+interest|volume|strike|strikes|calls?|puts?|options?)\b")
_NUMBER_WORDS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
                 "ten": "10", "eleven": "11", "twelve": "12"}


def months_asked(low: str) -> tuple[int, int]:
    """(from, to) months out. "1 months to 12 months", "3-12 months", "6 months" (1 to 6), "a year" (1 to 12), "leap" alone
    (3 to 12: the app's LEAP window, Ganesh 2026-11-06 "Leap I will take 3 months to 12 months"), else 1 to 12."""
    text = re.sub(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", lambda m: _NUMBER_WORDS[m.group(1)], low)
    both = re.search(r"\b(\d{1,2})\s*(?:months?|mo)?\s*(?:to|-|–|through|and|till|until)\s*(\d{1,2})\s*(?:months?|mo)\b", text)
    if both:
        lo, hi = sorted((int(both.group(1)), int(both.group(2))))
        return max(1, min(lo, 12)), max(1, min(hi, 12))
    one = re.search(r"\b(\d{1,2})\s*(?:months?|mo)\b", text)
    if one:
        return 1, max(1, min(int(one.group(1)), 12))
    if re.search(r"\b1\s*(?:yr|years?)\b", text):
        return 1, 12
    if re.search(r"\bleaps?\b", text):
        return 3, 12
    return 1, 12


def delta_asked(low: str) -> float | None:
    """A delta the question names: "0.20 delta", "delta .3", "20 delta". None when it names none."""
    found = re.search(r"(?<![\d.])0?\.(\d{1,2})\s*(?:delta|Δ)\b|\bdelta\s*(?:of|at|around|near|about|=|is)?\s*0?\.(\d{1,2})\b|\b(\d{1,2})\s*delta\b", low)
    if not found:
        return None
    if found.group(3):
        value = int(found.group(3)) / 100
    else:
        digits = found.group(1) or found.group(2)
        value = float("0." + digits)
    return value if 0.05 <= value <= 0.95 else None
_BRIEF = re.compile(r"\bmorning\s+brief\b|\bbrief(?:ing)?\b")
_QUOTE = re.compile(r"\b(?:price|quote)\b|\bhow\s+much\s+is\b|\btrading\s+at\b")
#: Where a price is going (2026-11-15, "Can we predict the prize of app?" got release notes that mention "predictions").
#: The chat never forecasts: the answer is one plain line, then the range the options market is pricing and the walls.
_PREDICT = re.compile(
    r"\bpredict\w*\b|\bforecast\w*\b|\bprojections?\b|\bup\s+or\s+down\b|\b(?:higher|lower)\s+or\s+(?:higher|lower)\b"
    r"|\bwhere\s+(?:will|would|is|does|do|can|could|might)\b.*\b(?:go|going|headed|heading|end|ends|close|closes|be|open|opens|land)\b"
    r"|\bwhat\s+will\b|\bwhat\s+(?:is|are)\s+\S+(?:\s+\S+)?\s+going\s+to\s+do\b"
    r"|\bwill\s+(?:\$?[a-z.&]{1,8}|the\s+\w+|it)\s+(?:go|hit|reach|touch|rise|fall|drop|close|open|break|rally|crash|bounce|recover|tank|"
    r"moon|pump|dump|keep|stay|hold|continue|be\s+(?:up|down|higher|lower|green|red))\b"
    r"|\b(?:is|are)\s+(?:\$?[a-z.&]{1,8}|the\s+\w+|it)\s+going\s+(?:to\s+(?:go|rise|fall|drop|crash|rally|bounce|recover|tank|moon)|up|down|higher|lower)\b"
    r"|\bhow\s+(?:high|low|far)\s+(?:can|could|will|would|might|may)\b|\bgoing\s+to\s+(?:go\s+)?(?:up|down)\b"
    # Broken English and short forms (2026-11-15, "Where spy go tomorrow?" opened the chart): "where spy go",
    # "where nvda going", "spy up tomorrow?", "what next for spy", "spy direction", "spy outlook tomorrow".
    r"|\bwhere\s+(?:\S+\s+){0,3}?(?:go|goes|going|headed|heading|end|ends|close|closes|land|lands)\b(?!\s+to\s+(?:the|my|a)\b)"
    r"|\bwhat(?:'?s|\s+is)?\s+next\s+(?:for|on|with)\b|\bnext\s+move\b|\bdirection\b|\bwhich\s+way\b"
    r"|\b(?:up|down|higher|lower|green|red|bullish|bearish|rally|crash|drop|fall|rise|bounce|go|going|move|do|happen)\b.*\b(?:tomorrow|next\s+(?:week|month|year))\b"
    r"|\b(?:tomorrow|next\s+(?:week|month|year))\b.*\b(?:up|down|higher|lower|green|red|bullish|bearish|rally|crash|drop|fall|rise|bounce|go|going|move|do|happen)\b"
    r"|^\s*\$?[a-z.]{1,6}\s+(?:tomorrow|next\s+(?:week|month|year))\s*[?.!]*\s*$|^\s*(?:tomorrow|next\s+(?:week|month|year))\s+\$?[a-z.]{1,6}\s*[?.!]*\s*$")
NO_PREDICT_LINE = ("No one can predict a price, and this app does not try. "
                   "Here is the range the options market is pricing and the biggest walls.")
NO_PREDICT_TICKER_LINE = ("No one can predict a price, and this app does not try. "
                          "Name a ticker (for example 'predict SPY') to see the range the options market is pricing.")
_WHY_MOVE = re.compile(r"\bwhy\b.*\b(?:up|down|moving|moved|drop\w*|rall\w+|fall\w*|fell|jump\w*|higher|lower|spik\w+)\b")
_ADVICE = re.compile(r"\ba\s+(?:good\s+)?(?:buy|sell|short)\b|\bshould\s+i\s+(?:buy|sell|short)\b|\bworth\s+buying\b")
#: The same in a reply, less the nouns that follow "a short" or "a buy" in a definition ("A short squeeze is ...",
#: "a short call", "a buy stop order"; 2026-11-11 review: "A short squeeze" was dropped as advice).
_ADVICE_REPLY = re.compile(r"\ba\s+(?:good\s+)?(?:buy|sell|short)\b(?![\s-]+(?:squeeze|calls?|puts?|positions?|sellers?|interest|sales?|stops?|orders?|"
                           r"limits?|side|signals?|straddles?|strangles?|spreads?|options?|stock|term|dated|vertical|iron|covered|to\s+(?:open|close)))"
                           r"|\bshould\s+i\s+(?:buy|sell|short)\b|\bworth\s+buying\b")
_HOW_DOING = re.compile(r"\bhow\s+(?:is|are|was)\b.*\b(?:doing|today)\b")
#: "how did nvda close", "how much did spy drop", "how high did nvda go", "nvda went up a lot": the stock's move, not a Bot signal.
_MOVE_QUESTION = re.compile(r"\bhow\s+(?:much\s+|high\s+|low\s+|far\s+|well\s+)?(?:did|has|have)\b.*\b(?:move[sd]?|moving|go|gone|went|do|done|close[sd]?|"
                            r"drop(?:ped)?|fall(?:en)?|fell|run|ran|rise|rose|gain(?:ed)?|jump(?:ed)?|rall(?:y|ied)|end(?:ed)?|finish(?:ed)?)\b"
                            r"|\b(?:went|gone|go(?:ing)?)\s+(?:up|down)\b")
#: Words that make a move question about the Bot's trades after all.
_TRADE_SUBJECT = re.compile(r"\b(?:bots?|trades?|traded|signals?|contracts?|options?|calls?|puts?|paper|profit\w*|loss\w*|entry|exit\w*|positions?|if\s+i|strateg\w+)\b")
_WHERE = re.compile(r"\bwhere(?:'s|\s+is|\s+are|\s+was|\s+were)?\b")
_GO_WORDS = re.compile(r"\b(?:link|take\s+me|go\s+to|open|bring\s+me|jump\s+to|navigate)\b")
#: Words that make "21 ema" a scanner board rather than the moving average on a chart.
_BOARD_WORDS = re.compile(r"\btouch\b|\blists?\b|\bboards?\b|\bscan")
#: A follow-up that is only a time ("at 10", "and at the close", "what about 11"): the conversation's ticker then.
_ONLY_A_TIME = re.compile(r"(?:and\s+|what\s+about\s+|how\s+about\s+|then\s+|ok\s+|so\s+)?(?:at\s+|@\s*)?(?:the\s+)?"
                          r"(?:\d{1,2}(?::\d{2}|\s\d{2}|\d{2})?\s*(?:am|pm|o\W?\s?clock)?|noon|open|close|opening|closing)(?:\s+(?:today|yesterday))?")
_HOT = re.compile(r"\bhot\b")
#: "why tsla went up today", "why is NVDA down", "what happened to tsla": the why-answer for THAT ticker (2026-11-11 live bug:
#: "Why tsla went up today" on the SPY page answered only a price).
#: More ways to ask why (2026-11-14, "Our bot is not very smart ... Add all words that will be good"): "how come",
#: "reason for", "what caused / made / drove", "what's driving", "explain", "what's up / wrong with".
_WHY_WORD = re.compile(r"\bwhy\b|\bwhy'?d\b|\bhow\s+come\b|\bwhat\s+happened\b|\bwhat'?s\s+(?:going\s+on|happening)\s+(?:with|to)\b"
                       r"|\breasons?\b|\bwhat\s+(?:caused|causes|made|makes|drove|drives|is\s+driving|was\s+driving|pushed|pulled|sent|knocked|hit)\b"
                       r"|\bwhat'?s\s+(?:driving|causing|pushing|pulling|behind|moving)\b|\bwhat\s+is\s+(?:causing|pushing|pulling|behind|moving)\b"
                       r"|\bbehind\s+(?:the|this|that|its|today'?s)\s+(?:move|drop|fall|rally|selloff|sell-?off|dump|pump|spike|jump|gap)\b"
                       r"|\bexplain\b|\bcatalyst\b|\bwhat\s+news\b|\bany\s+news\b|\bwhat'?s\s+(?:up|wrong)\s+with\b|\bwhat'?s\s+with\b")
_MOVE_WORD = re.compile(r"\b(?:up|down|mov(?:e|es|ed|ing)|drop\w*|fall\w*|fell|jump\w*|spik\w*|tank\w*|crash\w*|rall\w*|ris(?:e|es|ing)|rose|"
                        r"sank|sink\w*|plung\w*|soar\w*|surg\w*|pop(?:s|ped|ping)?|dump\w*|slid\w*|slump\w*|gain\w*|higher|lower|red|green|"
                        r"climb\w*|declin\w*|rebound\w*|sell-?off|selling\s+off|tumbl\w*|skyrocket\w*|run(?:ning)?\s+up|"
                        # slang and plain words a trader uses (2026-11-14)
                        r"sell(?:s|ing)?\s+off|sold\s+off|selloff|bleed\w*|bled|pump\w*|moon\w*|rip(?:s|ped|ping)?|ripper|rocket\w*|"
                        r"weak\w*|strong|strength|crater\w*|nose\s?div\w*|puk\w*|flush\w*|melt(?:s|ed|ing)?\s+(?:up|down)|meltdown|"
                        r"squeez\w*|break(?:s|ing)?\s+(?:out|down)|broke\s+(?:out|down)|breakdown|breakout|fl(?:y|ies|ying|ew)|"
                        r"hammer\w*|smoked|crush\w*|slam\w*|sinking|lagg\w*|underperform\w*|outperform\w*|volatil\w*|whipsaw\w*|"
                        r"reversal|revers\w*|fad(?:e|ed|es|ing)|recover\w*|bounc\w*|surge|spike|move|swing\w*|gap(?:s|ped|ping)?|"
                        r"collaps\w*|plummet\w*|nosediv\w*|drag\w*|pressure\w*|bid|offered|green\s+today|red\s+today|"
                        r"lose|losing|lost|loss|loses|wip(?:e|ed|ing)\s+out|cratering|sliding|dipp?(?:ed|ing|s)?|dip)\b")
_HAPPENED = re.compile(r"\bwhat\s+happened\s+(?:to|with|in)\b|\bwhat'?s\s+(?:going\s+on|happening)\s+(?:with|to|in)\b"
                       r"|\bwhat'?s\s+(?:up|wrong)\s+with\b|\bwhat'?s\s+with\b|\bwhat\s+is\s+(?:going\s+on|happening)\s+(?:with|to|in)\b"
                       r"|\bwhat'?s\s+driving\b|\bwhat\s+is\s+driving\b")
#: "explain" also asks what a word means ("explain rvol", "explain the red color"): a why-question only without one.
_EXPLAIN_ONLY = re.compile(r"^\s*(?:can\s+you\s+|please\s+)?explain\b")
#: The report family, first match wins. "long term" is the LEAP view; the Long Term page's own numbers are "buffett"/"long term page".
_REPORT_KINDS = (
    ("rules", re.compile(r"\bshould\s+i\s+(?:buy|sell|short|get)\b|\bis\s+(?:it|\S+)\s+a\s+(?:good\s+)?(?:buy|sell)\b|\bworth\s+buying\b|\bwhat\s+do\s+the\s+rules\s+say\b|\brules?\s+(?:show|say)\b")),
    ("analyst", re.compile(r"\banalysts?\b|\bprice\s+targets?\b|\banalyst\s+ratings?\b|\bratings?\b")),
    ("fundamentals", re.compile(r"\bfundamentals?\b|\bfundamental\s+analysis\b|\bintrinsic\s+value\b|\bvaluation\b|\bfinancials\b")),
    ("technicals", re.compile(r"\btechnicals?\b|\btechnical\s+analysis\b")),
    ("sentiment", re.compile(r"\bsentiment\b|\bput[\s/-]*call\s+ratio\b|\bhow\s+(?:are\s+)?traders\s+lean")),
    ("outlook_short", re.compile(r"\bshort[\s-]*term\b|\bfor\s+a\s+weekly\b|\bweekly\s+calls?\b|\bweekly\s+puts?\b")),
    ("outlook_mid", re.compile(r"\bmid[\s-]*term\b|\bmedium[\s-]*term\b|\bswing\b")),
    ("outlook_long", re.compile(r"\bleaps?\b|\blong[\s-]*term\s+(?:view|outlook|analysis|for)\b|^long[\s-]*term\s+\$?[a-z]{1,6}$|^\$?[a-z]{1,6}\s+long[\s-]*term$")),
    ("report", re.compile(r"\bfull\s+report\b|\breport\s+on\b|\beverything\s+(?:on|about)\b|\btell\s+me\s+(?:all\s+)?about\b|\ball\s+(?:the\s+)?info\w*\b|\bfull\s+analysis\b|\banaly[sz]e\b|\banalysis\s+(?:on|of)\b")),
)
#: The 21 or 50 EMA by name: its own answer on every timeframe (2026-11-11, "See the 21 and 50 ema information also required").
_EMA_ASK = re.compile(r"\b(?:21|50)\s*-?\s*(?:day\s+|period\s+)?emas?\b|\bemas?\s*-?\s*(?:21|50)\b|\bemas?\b.*\b21\b.*\b50\b")
_MARKET_WORD = re.compile(r"\b(?:market|markets|stocks|indexes|indices|s&p|nasdaq|dow)\b")
#: "how did the market do", "how is the market doing", "how far did the nasdaq fall": a market question with no ticker.
_MARKET_HOW = re.compile(r"\bhow\s+(?:did|is|are|was|were|has|have|much\s+did|far\s+did)\b.*?(?<![\w&])(?:market|markets|stocks|s&p|nasdaq|dow)(?![\w&])")
_HELP = re.compile(r"\b(?:what\s+is|what'?s|what\s+does|meaning\s+of|explain|define)\b.*\b(?:rvol|sqz|squeeze|skittles|skit|h/?l|color|fresh|setups?|record)\b|\bwrong\s+with\s+the\s+app\b|\bhow\s+do\s+i\b|\bhow\s+to\b")
_DEFINE = re.compile(r"\bwhat\s+is\b|\bwhat'?s\b|\bwhat\s+are\s+(?:an?\s+)?(?:call\s+|put\s+)?(?:walls?|pivots?|chains?)\s*[?.!]*$|\bmeaning\s+of\b|\bexplain\b|\bdefine\b|\bwhat\s+does\b.*\bmean\b")
_SETTINGS = re.compile(r"\bpassword\b|\brelease\s+notes\b|\bwhat'?s\s+new\b|\bwhat\s+changed\b|\bsettings\b")
_BARE_CLOCK = re.compile(r"\$?[a-z.]{1,6}\s+(?:at\s+|@\s*)?(?:\d{1,2}:\d{2}|\d{3,4}|\d{1,2}\s\d{2}|\d{1,2}\s*o\W?\s?clock|\d{1,2}\s*(?:am|pm)|noon)(?:\s*(?:am|pm))?(?:\s+(?:today|yesterday|calls?|puts?))*")
_TOOL_NOUN_ALIASES = {"chain", "option chain", "walls", "high oi", "news", "headlines", "news feed", "earnings", "premarket", "pre market", "sectors", "sector", "levels", "long term"}
_FRAMES = (("3m", r"3\s?m(?:in\w*)?"), ("5m", r"5\s?m(?:in\w*)?"), ("10m", r"10\s?m(?:in\w*)?"), ("15m", r"15\s?m(?:in\w*)?"), ("30m", r"30\s?m(?:in\w*)?"),
           ("1h", r"(?:1\s?h(?:ou)?r?|60\s?m(?:in\w*)?|hourly)"), ("2h", r"2\s?h(?:ou)?r?"), ("4h", r"4\s?h(?:ou)?r?"), ("D", r"(?:daily|day\s+chart|1d|d)"), ("W", r"(?:weekly|1w|w)"), ("M", r"(?:monthly|month\s+chart)"))
_FOCUS = (("vwap", r"\bvwap\b"), ("pivots", r"\bpivots?\b"), ("ema", r"\bemas?\d*\b"), ("sma", r"\bsmas?\d*\b"))
_NOUN_WORDS = {"scanner", "scan", "scans", "board", "boards", "momx", "chart", "charts", "level", "levels", "pivot", "pivots", "vwap", "ema", "emas", "sma", "support",
               "resistance", "signal", "signals", "chain", "chains", "wall", "walls", "news", "headline", "headlines", "earnings", "premarket", "price", "quote", "option", "options", "pain"}
_CONNECTORS = {"on", "for", "of", "in"}
_NOT_TICKER = frozenset("""HAVE HAD THIS THAT THESE THOSE THERE HERE WHERE WHEN WHICH WHO WHOM THEM THEY THEIR YOUR OUR FROM WITH WITHOUT GIVE GET GOT NEED WANT ABOUT ALSO JUST
LIKE PLEASE THANK THANKS COULD WOULD SHOULD WILL DOES BEEN INTO OVER UNDER ONLY SOME MORE MOST THAN THEN WERE WHAT WHATS HOWS FETCH SHOW LINK PAGE TAB
SCANNER SCAN SCANS BOARD BOARDS MOMX CHART CHARTS LEVEL LEVELS PIVOT PIVOTS VWAP EMA EMAS SMA SMAS SIGNAL SIGNALS CHAIN CHAINS WALL WALLS NEWS HEADLINE PRICE QUOTE OPTION OPTIONS
EARNING EARNINGS SECTOR SECTORS HEATMAP PREMARKET PRE MARKET TODAY MORNING NOON HOT LIST TICKER TICKERS STOCK STOCKS BRIEF LONG TERM PAIN EXPECTED RVOL SQZ SKIT MAG WATCHLIST
DAILY WEEKLY MONTHLY HOUR HOURLY MIN MINUTE BULL BEAR CALL PUT CALLS PUTS DOWN STAYED ANYTHING SOMETHING WILL GOES YES OK OKAY HELLO HI HEY SIR PLZ
DOING GOING MAKE MAKES TAKES LOOK LOOKS FIND RIGHT STILL EVERY AGAIN AFTER BEFORE THING THINK STAND WHOLE SAME EACH MANY MUCH ONLY OTHER SHOWS SHOWN GIVES SAID SAYS TOLD
TIME BIG NEW OLD FULL LIVE""".split())
_MAG_LOW = re.compile(r"\bmag\s?(?:7|seven)\b")
#: What traders call the big names. Read before any guess from the words around a noun ("headlines on apple" is AAPL, not APPLE).
COMPANY_NAMES = {"apple": "AAPL", "microsoft": "MSFT", "nvidia": "NVDA", "amazon": "AMZN", "google": "GOOGL", "alphabet": "GOOGL",
                 "facebook": "META", "tesla": "TSLA", "netflix": "NFLX", "broadcom": "AVGO", "oracle": "ORCL", "salesforce": "CRM",
                 "adobe": "ADBE", "intel": "INTC", "micron": "MU", "qualcomm": "QCOM", "palantir": "PLTR", "coinbase": "COIN",
                 "robinhood": "HOOD", "shopify": "SHOP", "snowflake": "SNOW", "disney": "DIS", "walmart": "WMT", "costco": "COST",
                 "boeing": "BA", "nike": "NKE", "starbucks": "SBUX", "mcdonalds": "MCD", "mcdonald's": "MCD", "pfizer": "PFE", "merck": "MRK",
                 "eli lilly": "LLY", "lilly": "LLY", "unitedhealth": "UNH", "jpmorgan": "JPM", "jp morgan": "JPM", "goldman sachs": "GS",
                 "goldman": "GS", "exxon": "XOM", "chevron": "CVX", "bank of america": "BAC", "wells fargo": "WFC", "home depot": "HD",
                 "comcast": "CMCSA", "tsmc": "TSM", "taiwan semi": "TSM", "taiwan semiconductor": "TSM", "supermicro": "SMCI",
                 "super micro": "SMCI", "zeta global": "ZETA", "sofi": "SOFI", "uber": "UBER", "meta platforms": "META"}
_COMPANY = re.compile(r"(?<![\w$])(" + "|".join(sorted((re.escape(name) for name in COMPANY_NAMES), key=len, reverse=True)) + r")(?:'s)?(?![\w])", re.I)


def company_ticker(text: str) -> str | None:
    found = _COMPANY.search(str(text or ""))
    return COMPANY_NAMES[found.group(1).lower()] if found else None


def _timeframe(low: str) -> str | None:
    label = _LABEL.search(low)
    if label:
        tail = label.group(2).lower()
        return {"d": "D", "w": "W", "m": "M"}.get(tail, tail)
    for key, pattern in _FRAMES:
        if re.search(r"(?<![\w.:])%s(?![\w:])" % pattern, low):
            return key
    return None


def _strip_for_ticker(text: str) -> str:
    """The sentence without what looks like a ticker but is not: CALL4H labels and timeframes."""
    text = _LABEL.sub(" ", text)
    return re.sub(r"(?<![\w.:])\d{1,2}\s?(?:m|min|mins|minutes?|h|hr|hrs|hours?)(?![\w:])", " ", text, flags=re.I)


def _beside_a_noun(low: str, known: frozenset | None = None) -> str | None:
    """A lower-case ticker right next to a known word ("zeta chain", "premarket on amd"). Five letters at most.
    Only in a sentence of three words or fewer, or after "on", "for", "of", "in": in a longer sentence the word beside
    the noun is just English ("what does the chart say", "what signals fired today"). With `known`, only a listed
    symbol counts: "headlines on apple" is not a ticker APPLE (2026-11-11)."""
    from momx import chatbot
    words = re.findall(r"[a-z0-9]+", low)            # numbers stay in, so nothing reaches across "level 3" to the word after it
    short = len(words) <= 3
    for index, word in enumerate(words):
        if word not in _NOUN_WORDS:
            continue
        for step in (-1, 1):
            at = index + step
            joined = 0 <= at < len(words) and words[at] in _CONNECTORS
            if joined:
                at += step
            if not joined and not short:
                continue
            if 0 <= at < len(words):
                candidate = words[at]
                if candidate.isalpha() and 2 <= len(candidate) <= 5 and candidate.upper() not in _NOT_TICKER and candidate.upper() not in chatbot.COMMON and candidate not in _NOUN_WORDS:
                    if known is None or candidate.upper() in known:
                        return candidate.upper()
    return None


def _unknown_beside(text: str, known: frozenset) -> str | None:
    """A word in a ticker's place beside a known word that is not a listed symbol ("news on oil"). Such a question
    is about that word, so it must not quietly answer for the ticker on screen or in the conversation."""
    if company_ticker(text):
        return None
    word = _beside_a_noun(_strip_for_ticker(text).lower())
    return word if word and word not in known else None


def _named_ticker(text: str, known: frozenset, position: bool = True) -> str | None:
    """The ticker a sentence names itself: typed with a $, in capitals, a listed name, or (with `position`) next to a known word."""
    from momx import chat_scanner, chatbot
    cleaned = _strip_for_ticker(chat_scanner.without_scanner_words(text))
    dollar = re.search(r"(?<![A-Za-z0-9])\$([A-Za-z]{1,6})\b", cleaned)
    if dollar:
        return dollar.group(1).upper()
    found = chatbot.parse_ticker(cleaned, set(known) - _NOT_TICKER)
    if found:
        return found
    if text != text.upper():
        for word in re.findall(r"(?<![A-Za-z$.'])([A-Z]{2,6})(?![A-Za-z'])", cleaned):
            if word not in chatbot.COMMON and word not in _NOT_TICKER:
                return word
    company = company_ticker(text)
    if company:
        return company
    return _beside_a_noun(_strip_for_ticker(text).lower(), known) if position else None


def _resolved(ticker: str | None, low: str, ctx: Mapping) -> str | None:
    """Which ticker a question means: its own; "chart", "this", "here" mean the screen's; else the conversation's, then the screen's."""
    if ticker:
        return ticker
    if _PAGE_WORDS.search(low) and ctx.get("pageTicker"):
        return ctx["pageTicker"]
    return ctx.get("ticker") or ctx.get("pageTicker")


def _call(tool: str, **args: Any) -> dict:
    return {"tool": tool, "args": {key: value for key, value in args.items() if value not in (None, "")}}


#: The index funds a past-day move question answers as the market's move.
_MARKET_FUNDS = frozenset({"SPY", "QQQ", "DIA", "IWM"})


def _past_move(ticker: str, past: str, text: str) -> dict:
    """A past session's move: why_move with that day (its regular-session close-to-close move), never the live quote."""
    if ticker in _MARKET_FUNDS:
        return _call("why_move", market=True, day=past, question=text)
    return _call("why_move", ticker=ticker, day=past, question=text)


#: A past period that is more than one session ("last week", "3 weeks ago", "in october") or a holiday by name: the
#: date parser cannot make one session of it, and the current session is never the answer (2026-11-11 review rv4).
_PAST_PERIOD = re.compile(
    r"\b(?:last|previous|prior|past|this\s+past)\s+(?:week|month|year|quarter)\b|\b(?:a|an|one|two|three|four|five|six|\d{1,2})\s+(?:weeks?|months?|years?)\s+ago\b"
    r"|\b(?:in|during|for|through|over|of|last|this)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\b(?!\s*\d)"
    r"|\b(?:halloween|thanksgiving|christmas(?:\s+eve)?|new\s+year'?s(?:\s+(?:day|eve))?|election\s+day|labor\s+day|memorial\s+day|independence\s+day|"
    r"july\s+4(?:th)?|fourth\s+of\s+july|juneteenth|mlk\s+day|martin\s+luther\s+king\s+day|presidents'?\s+day|veterans'?\s+day|columbus\s+day|"
    r"good\s+friday|black\s+friday|cyber\s+monday|easter)\b")
PERIOD_LINE = "The chat answers one session at a time. Ask about one day, for example 'yesterday' or 'on 11/06'."
NO_ADVICE_LINE = "I can't say whether to buy or sell. Here is what the app has."
NO_CAUSE_LINE = "The app can't say why it moved. Here is what it has."


_MY_TRADES = re.compile(
    r"\b(?:how\s+(?:did|do|am|have|was)\s+i\s+(?:trade[ds]?|trading|do|doing|done)\b"
    r"|my\s+(?:own\s+|real\s+)?(?:trades?|trading|win\s*rate|worst\s+habits?|habits?|results?|p\s?[&/]\s?l|pnl|spreads?|executions?|fills?)\b"
    r"|spreads?\s+(?:cost|costs|costing)\s+me\b|(?:cost|paid|lost)\s+me\s+(?:in|on)\s+spreads?\b|i\s+(?:paid|lost)\s+(?:in|on)\s+spreads?\b)")


def _my_topic(low: str) -> str:
    """Which part of My Trading the question asks for."""
    for topic, pattern in (("habits", r"\bhabits?\b|\bmistakes?\b"), ("spreads", r"\bspreads?\b|\bslippage\b|\bfills?\b|\bexecutions?\b"),
                           ("puts", r"\bputs?\b"), ("calls", r"\bcalls?\b"), ("setup", r"\bsetups?\b|\blevels?\b"),
                           ("time", r"\btime\s+of\s+day\b|\bmorning\b|\bafternoon\b"), ("dte", r"\bdte\b|\bexpir\w*\b|\b0dte\b"),
                           ("tickers", r"\btickers?\b|\bstocks?\b|\bsymbols?\b")):
        if re.search(pattern, low):
            return topic
    return "summary"


def _my_days(low: str) -> int:
    if re.search(r"\b(?:90\s+days?|quarter|3\s+months?|three\s+months?)\b", low):
        return 90
    if re.search(r"\b(?:month|30\s+days?)\b", low):
        return 30
    return 7


def sure_calls(text: str, ctx: Mapping, known: frozenset) -> dict | None:
    """The lookups for a question the words make certain, or None. {"calls", "lines", "links", "suggest"}. No AI, no file read."""
    from momx import chat_pages, chat_scanner, chat_tools, chatbot
    low = text.lower()
    scope = str(ctx.get("scope") or "")
    owner = scope == "owner"
    now = ctx["now"]
    wants_data = any(pattern.search(low) for pattern in (_LEVEL, _SIGNAL, _LABEL, _CHAIN, _WALL, _EM, _MAX_PAIN, _NEWS, _EARN, _SECTOR, _PREMARKET, _LONG_TERM, _BRIEF))
    # "where is the call wall on spy" asks for the level; "where" alone with a data word is not going somewhere.
    where_data = wants_data and bool(_WHERE.search(low)) and not _GO_WORDS.search(low)
    nav = not _QUOTE.search(low) and not where_data and bool(_NAV.search(low) or (_SHOW_ME.search(low) and not wants_data))

    if owner and re.fullmatch(r"(?:the\s+)?(?:score\s?card|desk)[?.! ]*", low):
        return {"calls": [_call("bot_desk")]}
    if owner and re.search(r"\b(?:show|see)\b.*\bpaper\b", low):
        return {"calls": [_call("paper")]}
    # The owner's OWN real trades (My Trading): "how did I trade this week", "how much did spreads cost me", "my worst
    # habit", "my win rate on puts". Checked before the Bot's records, whose "how did ..." form took these
    # (2026-11-11 review). A sentence naming the bots or paper stays with the Bot.
    if owner and _MY_TRADES.search(low) and not re.search(r"\b(?:bots?|paper|level\s*[123])\b", low):
        return {"calls": [_call("my_trades", topic=_my_topic(low), days=_my_days(low))]}

    ticker = _named_ticker(text, known)
    minute = chat_scanner.read_minute(text, chatbot.parse_time(text, scanner=True))
    clock = f"{minute // 60:02d}:{minute % 60:02d}" if minute is not None else None
    # Open interest months out ("nvda leap which one had more oi", "spy strikes with high oi 1 to 12 months with delta"):
    # the long-dated board, before "leap" reaches the Long Term view and "high oi" the short-dated High OI board.
    if ((_OI_WORD.search(low) and _MONTHS_SPAN.search(low)) or (_LEAP_WORD.search(low) and (_STRIKE_WORD.search(low) or _OI_SIDE_WORD.search(low) or _LEAP_CHOICE.search(low)))) \
            and minute is None and not re.search(r"\b(?:what\s+is|what\s+does|meaning\s+of|define)\b", low):
        months_from, months_to = months_asked(low)
        wants_puts, wants_calls = bool(re.search(r"\bputs?\b", low)), bool(re.search(r"\bcalls?\b", low))
        side = "bear" if wants_puts and not wants_calls else "bull" if wants_calls and not wants_puts else None
        plan = {"calls": [_call("long_oi", ticker=_resolved(ticker, low, ctx), months_from=months_from, months_to=months_to,
                                delta=delta_asked(low), side=side)]}
        # "nvda leap oi buyers or sellers?" (2026-11-19): the plain line first, then the strikes and what is new.
        return {**plan, "lines": [OI_NO_SIDE_LINE]} if _OI_SIDE_WORD.search(low) else plan
    # Open interest on one ticker, no months word: the High OI board ("Oi for nvda buyers or sellers?", "nvda oi").
    # A page, scanner, chain or Bot word keeps its own answer, and so does a definition.
    if _OI_WORD.search(low) and minute is None and not _OI_ELSEWHERE.search(low) and _resolved(ticker, low, ctx) \
            and not re.search(r"\b(?:what\s+is|what\s+does|meaning\s+of|define)\b", low):
        wants_puts, wants_calls = bool(re.search(r"\bputs?\b", low)), bool(re.search(r"\bcalls?\b", low))
        plan = {"calls": [_call("walls", ticker=_resolved(ticker, low, ctx),
                                side="put" if wants_puts and not wants_calls else "call" if wants_calls and not wants_puts else None)]}
        return {**plan, "lines": [OI_NO_SIDE_LINE]} if _OI_SIDE_WORD.search(low) else plan
    # A price level breaking ("If 135 broke below then", 2026-11-20, got the help list): the chart's next levels beyond
    # that price, for the ticker named, on screen or in the conversation. A clock time or a scanner / average word
    # keeps its own answer ("stocks above the 50 ema", "NVDA below 9:30 high").
    broke = _BREAK_SIDE.search(low)
    number = _BREAK_PRICE.search(low)
    if broke and number and minute is None and not _BREAK_NOT.search(low):
        target = _resolved(ticker, low, ctx)
        if target:
            side = "below" if broke.group("below") else "above"
            return {"calls": [_call("chart_levels", ticker=target, question=text, break_price=float(number.group(1)), break_side=side)]}
    # A definition whose term is a glossary entry longer than the ticker word ("what is an iron condor", "what is a gap
    # up", "what is the live bolt") is the glossary, even when IRON, GAP or BOLT is a real ticker (2026-11-11 review rv7).
    # Exact match only, and never the ticker word alone: "what is IRON" and "what is GAP" stay ticker questions.
    if ticker and minute is None and not _PAGE_WORDS.search(low):
        from momx import chat_glossary
        asked = chat_glossary.asked_term(text)
        # Also when the "ticker" was only guessed from capitals and is not a listed symbol: "what is ITM",
        # "What does OTM mean?", "what is IV" (2026-11-11 recheck). "what is GAP" stays a ticker question: GAP is listed.
        if asked and chat_glossary.lookup(asked, scope, fuzzy=False) and (
                chat_glossary._key(asked) != chat_glossary._key(ticker) or ticker not in known):
            return {"calls": [_call("define", query=text)]}
    asked_day, named = chatbot.parse_day(text, now.date())
    today_word = bool(re.search(r"\btoday\b", low))
    day = chat_tools.session_day(now, asked_day, named, minute, today_word).isoformat()
    day_arg = day if named or ticker else None
    side = chatbot.scanner_side(text)
    frame = _timeframe(low)
    scan = bool(_SCAN.search(low)) and not _PREMARKET.search(low)
    board = next((name for name, pattern in chat_scanner.BOARDS if re.search(pattern, low)), None)
    if board in ("21 EMA Touch Weekly", "50 EMA Touch Monthly") and not _BOARD_WORDS.search(low):
        board = None                                  # "nvda 21 ema" is the average on the chart, not the EMA Touch board
    level, signal = bool(_LEVEL.search(low)) and not board, bool(_SIGNAL.search(low) or _LABEL.search(low))

    # Why did it move: the why-answer for the ticker the question names, never the page's. A clock time on the
    # owner's question stays with the Bot ("why did AMD lose at 3:45").
    # A past session named ("yesterday", "last friday", "on 11/07") is passed on, and the answer is that session's move
    # from the saved daily bars, never today's (2026-11-11 review: "how did the market do yesterday" got today's move).
    past = day if named and day != chat_tools.session_day(now, None, False, None).isoformat() else None
    # A period of more sessions than one ("last week", "3 weeks ago", "in october") or a holiday by name is not one
    # session the parser can name, and the current session is never its answer: one plain line asks for a day
    # (2026-11-11 review rv4: "why did the market drop last week" got today's move). A holiday's weekday word
    # ("good friday") is not the most recent Friday.
    period = _PAST_PERIOD.search(low)
    holiday = bool(period) and not re.match(r"(?:last|previous|prior|past|this|in|during|for|through|over|of|a|an|one|two|three|four|five|six|\d)\b",
                                            period.group(0))
    if period and (not named or holiday) and not _TRADE_SUBJECT.search(low) and (
            (_WHY_WORD.search(low) and (_MOVE_WORD.search(low) or _HAPPENED.search(low))) or _MARKET_HOW.search(low)
            or (ticker and (_MOVE_QUESTION.search(low) or _QUOTE.search(low) or _HOW_DOING.search(low)))):
        return {"calls": [], "lines": [PERIOD_LINE]}
    # A clock time on the owner's question stays with the Bot ("why did AMD lose at 3:45") unless a ticker or the
    # market plainly moved ("why spy dropped at 10am", 2026-11-14).
    clock_move = bool(_MOVE_WORD.search(low)) and not re.search(r"\b(?:lose|losing|lost|loss|loses)\b", low) and (bool(ticker) or bool(_MARKET_WORD.search(low)))
    explain_help = bool(_EXPLAIN_ONLY.search(low) or re.search(r"\breasons?\b", low)) and not re.search(r"\bwhy\b", low) \
        and bool(_HELP.search(low))
    if _WHY_WORD.search(low) and (_MOVE_WORD.search(low) or _HAPPENED.search(low)) and not _TRADE_SUBJECT.search(low) \
            and not explain_help and (minute is None or not owner or clock_move):
        if ticker:
            return {"calls": [_call("why_move", ticker=ticker, day=past, question=text)]}
        if _MARKET_WORD.search(low):
            return {"calls": [_call("why_move", market=True, day=past, question=text)]}
        if _resolved(None, low, ctx):
            return {"calls": [_call("why_move", ticker=_resolved(None, low, ctx), day=past, question=text)]}
    # "how did the market do today", "how is the market doing": the market's move, the same answer as "what happened to the market".
    if not ticker and minute is None and not scan and _MARKET_HOW.search(low) and not _TRADE_SUBJECT.search(low):
        return {"calls": [_call("why_move", market=True, day=past, question=text)]}

    # "Can we predict the price of spy", "where will nvda go tomorrow", "will spy go up": never a forecast. One plain
    # line, then the options market's range and the walls for the ticker named, in the conversation or on the screen.
    # Analyst price targets, the Bot's own records, a clock time and a past day keep their own answers.
    if (_PREDICT.search(low) and minute is None and not past and not scan and not board and not wants_data
            and not re.search(r"\b(?:go\s+to|open|take\s+me|show\s+me|bring\s+up|pull\s+up)\b", low)
            and not _WHY_WORD.search(low) and not _TRADE_SUBJECT.search(low) and not _HELP.search(low)
            and not re.search(r"\bprice\s+targets?\b|\banalysts?\b|\bearnings\b|\bweather\b|\blevel\s*[123]\b|\bscore\s?card\b", low)):
        target = _resolved(ticker, low, ctx)
        if not target:
            return {"calls": [], "lines": [NO_PREDICT_TICKER_LINE]}
        when = next(iter(re.findall(r"\b(?:today|tomorrow|(?:mon|tues|wednes|thurs|fri)day|20\d\d-\d\d-\d\d|\d{1,2}/\d{1,2})\b", low)), None)
        return {"calls": [_call("price_outlook", ticker=target, expiry=when)], "suggest": [f"{target} chart levels", f"{target} news"]}

    # The report family (2026-11-11): full report, fundamentals, technicals, sentiment, analysts, the three views,
    # and "should I buy", which gets what the app's rules show, never advice.
    report_subject = ticker or (_resolved(None, low, ctx) if _PAGE_WORDS.search(low) else None)
    bare_text = low.strip(" ?.!")
    if ticker and bare_text in (f"{ticker.lower()} today", f"{ticker.lower()} this week", f"how is {ticker.lower()} this week"):
        return {"calls": [_call("outlook_short", ticker=ticker)]}
    if report_subject and minute is None and not nav and not _TRADE_SUBJECT.search(low.replace("weekly call", "")):
        kind = next((name for name, pattern in _REPORT_KINDS if pattern.search(low)), None)
        if kind == "rules" or (_ADVICE.search(low) and not kind):
            return {"calls": [_call("rules", ticker=report_subject)], "lines": [NO_ADVICE_LINE]}
        if kind:
            return {"calls": [_call(kind, ticker=report_subject)]}
        if ticker and bare_text in (ticker.lower(), f"${ticker.lower()}"):
            return {"calls": [_call("report", ticker=ticker)]}                 # "NVDA" alone: everything
    elif report_subject and _ADVICE.search(low) and not _TRADE_SUBJECT.search(low):
        return {"calls": [_call("rules", ticker=report_subject)], "lines": [NO_ADVICE_LINE]}

    # The stock's own move, for everyone: never the Bot's records. A past session named ("how did spy do yesterday",
    # "how did nvda close on friday") is that session's regular-session move, never today's quote (2026-11-11 review).
    if ticker and minute is None and not scan and not wants_data and _MOVE_QUESTION.search(low) and not _TRADE_SUBJECT.search(low):
        if past:
            return {"calls": [_past_move(ticker, past, text)], "links": [chat_tools.history_link(ticker, day)]}
        return {"calls": [_call("quote", ticker=ticker)], "links": [chat_tools.history_link(ticker, day)]}
    if owner and chatbot.explicit_bot(text) and not nav:
        return None
    if not ticker and _unknown_beside(text, known):
        return None                                   # "news on oil": a word the app does not know, never the screen's ticker

    # Going somewhere.
    if nav and scan and (ticker or clock):
        return {"calls": [_call("open_scanner", ticker=ticker, day=day, time=clock, side=side, list=board, tab="history")]}
    if nav and _CHART.search(low) and not level and not signal and ticker:
        return {"calls": [_call("open_chart", ticker=ticker, timeframe=frame or "5m")]}
    if nav:
        found = chat_pages.find(low, scope, loose=True)
        if found:
            page, implied = found
            return {"calls": [_call("open_page", page=page.view, ticker=ticker if "ticker" in page.deep else None, timeframe=frame if "timeframe" in page.deep else None, **implied)]}
        if ticker and not wants_data:
            # "where" alone is a guess at going somewhere ("Where spy go tomorrow?" opened the chart, 2026-11-15):
            # marked unsure, so the AI reads the meaning first and this stays the answer when it cannot.
            unsure = not _GO_WORDS.search(low) and not _CHART.search(low)
            return {"calls": [_call("open_chart", ticker=ticker, timeframe=frame or "5m")], **({"unsure": True} if unsure else {})}
    if _SETTINGS.search(low):
        found = chat_pages.find(low, scope, loose=True)
        if found:
            return {"calls": [_call("open_page", page=found[0].view, **found[1])]}
    bare = low.strip(" ?.!")
    if bare not in _TOOL_NOUN_ALIASES and any(bare in (page.view.lower(), page.label.lower(), *page.aliases) for page in chat_pages.visible(scope)):
        found = chat_pages.find(bare, scope, loose=True)
        if found:
            return {"calls": [_call("open_page", page=found[0].view, **found[1])]}

    # A general idea ("what is theta", "explain how calls work", "what is max pain", "short squeeze"): the glossary entry,
    # with no ticker named and no "here"/"this" pointing at the screen. A bare term only when no data word asks for
    # the screen's ticker ("vwap" alone is still that chart's VWAP), and a data word with a ticker on the screen or in the
    # conversation stays that ticker's number ("what is max pain" on the SPY page). 2026-11-11 design change: no AI prose.
    if not ticker and not _PAGE_WORDS.search(low) and minute is None and not nav:
        from momx import chat_glossary
        asked = chat_glossary.asked_term(text)
        data_word = bool(wants_data or scan or board or level or signal or _QUOTE.search(low) or _CHART.search(low) or _OPTIONS.search(low))
        entry = chat_glossary.term_of(text, scope)
        # A term the app reports per ticker (market cap, P/E, EPS, IV, float, open interest, delta, gamma exposure,
        # short interest) counts as a data word: "what is the market cap" on the NVDA page is NVDA's (2026-11-11 rv7).
        number_here = bool(data_word or (entry and entry.get("number"))) and _resolved(None, low, ctx) is not None
        if ((asked and not number_here) or (not asked and not data_word and not number_here)) and entry:
            return {"calls": [_call("define", query=text)]}

    # The chart.
    subject = _resolved(ticker, low, ctx)
    # "what is an option chain" with no ticker anywhere wants the meaning, not "which ticker?".
    if not subject and _DEFINE.search(low) and any(pattern.search(low) for pattern in (_LEVEL, _CHAIN, _WALL, _EM, _MAX_PAIN)):
        return {"calls": [_call("help_search", query=text)]}
    label = _LABEL.search(low)
    label = label.group(0).upper() if label else None
    focus = next((name for name, pattern in _FOCUS if re.search(pattern, low)), None)
    if _EMA_ASK.search(low) and not board and not signal:
        return {"calls": [_call("ema", ticker=subject, timeframe=frame)]}       # "nvda 21 ema", "is nvda above the 50 ema"
    if level and signal:
        return {"calls": [_call("chart_levels", ticker=subject, timeframe=frame, focus=focus, question=text), _call("chart_signals", ticker=subject, timeframe=frame, label=label, question=text)]}
    if signal:
        chips = [f"Bot's signals on {subject}"] if owner and subject and not _CHART.search(low) and not frame and not label else []
        return {"calls": [_call("chart_signals", ticker=subject, timeframe=frame, label=label, question=text)], "suggest": chips}
    if level and not scan:
        return {"calls": [_call("chart_levels", ticker=subject, timeframe=frame, focus=focus, question=text)]}

    # Options and market data.
    expiry = next((word for word in re.findall(r"\b(?:today|tomorrow|(?:mon|tues|wednes|thurs|fri)day|20\d\d-\d\d-\d\d|\d{1,2}/\d{1,2})\b", low)), None)
    if _MAX_PAIN.search(low):
        return {"calls": [_call("max_pain", ticker=subject, expiry=expiry)]}
    if _EM.search(low):
        return {"calls": [_call("expected_move", ticker=subject, expiry=expiry)]}
    if _WALL.search(low):
        return {"calls": [_call("walls", ticker=subject, side="put" if re.search(r"\bputs?\b", low) else "call" if re.search(r"\bcalls?\b", low) else None)]}
    if _CHAIN.search(low) or (_OPTIONS.search(low) and ticker and not _ADVICE.search(low)):
        return {"calls": [_call("chain", ticker=subject, expiry=expiry)]}
    if _WHY_MOVE.search(low) and ticker:
        return {"calls": [_call("news", ticker=ticker), _call("quote", ticker=ticker), _call("chain", ticker=ticker)], "lines": [NO_CAUSE_LINE]}
    if _ADVICE.search(low) and subject:
        return {"calls": [_call("rules", ticker=subject)], "lines": [NO_ADVICE_LINE], "links": chat_tools.ticker_links(subject, day, scope)[:4]}
    if _HOT.search(low) and clock and not _SECTOR.search(low):
        return {"calls": [_call("scanner_list", time=clock, day=day, side=side), _call("sectors")]}
    if (not ticker and clock and re.search(r"\btop\b", low) and chat_scanner._top_of(low) is None and not chat_scanner.is_top_question(text)
            and not board):
        return {"calls": [_call("scanner_list", time=clock, day=day, side=side)]}       # "what was on top at 10:30"
    if _SECTOR.search(low) or (_HOT.search(low) and not ticker):
        return {"calls": [_call("sectors", ticker=ticker)]}
    if _PREMARKET.search(low):
        return {"calls": [_call("premarket", day=day if named else None, ticker=ticker)]}
    if _NEWS.search(low):
        return {"calls": [_call("latest_news" if subject else "news", ticker=subject)]}
    if _EARN.search(low):
        return {"calls": [_call("earnings", ticker=ticker or (subject if not re.search(r"\b(?:next|week|upcoming|calendar)\b", low) else None))]}
    if _LONG_TERM.search(low):
        return {"calls": [_call("long_term", ticker=subject)]}
    if _BRIEF.search(low):
        return {"calls": [_call("morning_brief")]}
    if _QUOTE.search(low):
        if past and subject and minute is None:
            return {"calls": [_past_move(subject, past, text)]}            # "nvda price yesterday": that session's close
        return {"calls": [_call("quote", ticker=subject)]}
    if _HOW_DOING.search(low) and ticker:
        if past and minute is None:
            return {"calls": [_past_move(ticker, past, text)], "links": [chat_tools.history_link(ticker, day)]}
        return {"calls": [_call("quote", ticker=ticker)], "links": [chat_tools.history_link(ticker, day)]}
    if _HELP.search(low):
        return {"calls": [_call("help_search", query=text)]}

    # The scanner. The 21 or 50 EMA board named with a ticker ("21 ema touch nvda", "nvda on the 21 ema list"): that
    # board and that ticker, the same for the owner and a regular user.
    if board in ("21 EMA Touch Weekly", "50 EMA Touch Monthly") and ticker:
        if minute is None:
            return {"calls": [_call("scanner_list", list=board, ticker=ticker, day=day, side=side)]}
        return {"calls": [_call("scanner_ticker", list=board, ticker=ticker, time=clock, day=day, side=side)]}
    # A ticker with a scanner word and no clock is that ticker's whole day.
    if (scan or board) and ticker and minute is None and not chat_scanner.may_name_a_board_ticker(text):
        kind = chat_scanner.route(text, True, False, None)
        if kind in (None, "scanner_day", "scanner_ticker"):
            return {"calls": [_call("scanner_day", ticker=ticker, day=day, side=side)]}
    if ticker and clock and (_BARE_CLOCK.fullmatch(low.strip(" ?.!")) or not (wants_data or _QUOTE.search(low) or _CHART.search(low) or nav
                                                                            or re.search(r"\blist\b|\btickers\b|\btop\b", low))):
        return {"calls": [_call("scanner_ticker", ticker=ticker, time=clock, day=day, side=side)]}      # "nvda at 11", "nvda at the open"
    if not ticker and clock and ctx.get("ticker") and _ONLY_A_TIME.fullmatch(bare):
        return {"calls": [_call("scanner_ticker", ticker=ctx["ticker"], time=clock, day=day, side=side or ctx.get("side"))]}   # follow-up "at 10"
    if _CHART.search(low) and ticker:
        return {"calls": [_call("open_chart", ticker=ticker, timeframe=frame or "5m")]}
    if _CHART.search(low) and subject and not ticker and _PAGE_WORDS.search(low):
        return {"calls": [_call("chart_levels", ticker=subject, timeframe=frame)]}
    if ticker and bare == ticker.lower():
        return {"calls": [_call("quote", ticker=ticker)], "links": chat_tools.ticker_links(ticker, day, scope)}
    return None


# ---------------------------------------------------------------- putting an answer together
def _suggest(chips: list, ticker: str | None, ctx: Mapping, asked: str) -> list[str]:
    from momx import chat_pages, chatbot
    pool = [*chips, *chat_pages.chips_for(ctx.get("pageName"), ticker or ctx.get("pageTicker"), str(ctx.get("scope") or ""))]
    seen, out = {asked.lower()}, []
    for chip in pool:
        if chip.lower() in seen or (ctx.get("scope") != "owner" and chatbot.owner_question(chip)):
            continue
        seen.add(chip.lower())
        out.append(chip)
    return out[:5]


def _run_calls(plan: Mapping, text: str, ctx: Mapping) -> dict:
    """Run a plan's tools and make one reply of them."""
    from momx import chat_pages, chat_tools
    scope = str(ctx.get("scope") or "")
    done = [(call, chat_tools.run(call["tool"], call.get("args") or {}, ctx)) for call in plan.get("calls") or []]
    lines = list(plan.get("lines") or [])
    links, table, as_of, chips = [], None, None, list(plan.get("suggest") or [])
    for _, part in done:
        lines += part["lines"]
        links += part["links"]
        table = table or part["table"]
        as_of = as_of or part["asOf"]
        chips += part.get("suggest") or []
    first = done[0][0]["tool"] if done else "help"
    facts: dict = done[0][1]["facts"] if len(done) == 1 else {call["tool"]: part["facts"] for call, part in done}
    named = next((call["args"].get("ticker") for call, _ in done if call["args"].get("ticker")), None)
    ticker = named or next((part["facts"].get("ticker") for _, part in done if isinstance(part["facts"].get("ticker"), str)), None)
    day = next((part["facts"].get("day") for _, part in done if isinstance(part["facts"].get("day"), str)), None) or next(
        (call["args"].get("day") for call, _ in done if isinstance(call["args"].get("day"), str)), None)
    side = next((call["args"].get("side") for call, _ in done if call["args"].get("side") in ("bull", "bear")), None)
    out = {"intent": _intent_of(first), "ticker": ticker, "day": day, "side": side, "lines": lines, "facts": facts,
           "suggest": _suggest(chips, ticker, ctx, text), "links": chat_pages.clean_links([*links, *(plan.get("links") or [])], scope),
           "table": table, "asOf": as_of}
    for key in chat_tools.EXTRA_KEYS:                  # the first tool that brings one keeps it (one "more" token per answer)
        found = next((part[key] for _, part in done if part.get(key)), None)
        if found:
            out[key] = found
    if scope != "owner":
        return {**out, "scope": "scanner"}
    # A definition has no slow part of its own: for the owner, with a ticker in play, the one "more" call carries the
    # AI summary of that ticker (momx/chat_summary.py). The scope here is the session's, set by the server.
    subject = ctx.get("ticker") or ctx.get("pageTicker")
    if first == "define" and not out.get("more") and isinstance(subject, str) and subject:
        from momx import chat_why
        out["more"] = chat_why.start(lambda: {"lines": []}, ctx, wait=0.0, summary=subject)
    return out


def _intent_of(tool: str) -> str:
    from momx import chat_tools
    return chat_tools.INTENT_OF.get(tool, tool)


def _search_card(text: str, ctx: Mapping, known: frozenset) -> dict:
    """Step 6, the answer when the AI cannot help: buttons for any ticker named, a page by name, the app's help text."""
    from momx import chat_pages, chat_tools, chatbot
    scope = str(ctx.get("scope") or "")
    ticker = _named_ticker(text, known, position=False)
    now = ctx["now"]
    day = chat_tools.session_day(now, None, False, None).isoformat()
    links: list[dict] = chat_tools.ticker_links(ticker, day, scope) if ticker else []
    page = chat_pages.find(text, scope, loose=True)
    if page:
        links.append(chat_pages.link(page[0].view, f"Open {page[0].label}", **page[1]))
    lines = [f"Here is where to look at {ticker}."] if ticker else []
    help_links: list[dict] = []
    if "help_search" in chat_tools.registry(scope):
        found = chat_tools.run("help_search", {"query": text}, ctx)
        if found["facts"].get("results"):
            lines += found["lines"]
            help_links = found["links"]
    if not lines:
        lines = ["I did not understand that one. Try a ticker with what you want, like \"NVDA chart levels\" or \"NVDA today scanner\"."] if scope == "owner" else [chatbot.SCANNER_ONLY_LINE]
    if not links and not help_links:
        links = [chat_pages.link("Learn + Setup", "Open Learn + Setup"), chat_tools.history_link(None, day)]
    base = chatbot.EXAMPLES if scope == "owner" else chatbot.SCANNER_EXAMPLES[:3]
    out = {"intent": "help", "ticker": ticker, "day": None, "lines": lines, "facts": {}, "suggest": _suggest(list(base), ticker, ctx, text) if ticker or scope == "owner" else list(base),
           "links": chat_pages.clean_links([*links, *help_links], scope), "table": None, "asOf": None}
    return {**out, "scope": "scanner"} if scope != "owner" else out


class _Budget:
    """A regular user's AI answers are counted once per answer, at the first moment one is wanted."""

    def __init__(self, allowed: Callable[[], bool] | None) -> None:
        self.allowed, self.result = allowed, None

    def __call__(self) -> bool:
        if self.result is None:
            self.result = True if self.allowed is None else bool(self.allowed())
        return self.result


def _fill_tickers(calls: list[dict], text: str, ctx: Mapping, known: frozenset = frozenset()) -> list[dict]:
    """A planned tool with no ticker uses the one the question names itself; failing that, the one it means
    (the screen's for "chart", "this", "here")."""
    from momx import chat_tools
    tools = chat_tools.registry(str(ctx.get("scope") or ""))
    low = text.lower()
    named = _named_ticker(text, known)
    out = []
    for call in calls:
        args = dict(call["args"])
        schema = tools[call["tool"]].get("args") or {}
        if "ticker" in (schema.get("properties") or {}) and not args.get("ticker") and named:
            args["ticker"] = named
        elif "ticker" in (schema.get("properties") or {}) and not args.get("ticker") and ("ticker" in (schema.get("required") or ()) or _PAGE_WORDS.search(low)):
            subject = _resolved(None, low, ctx)
            if subject:
                args["ticker"] = subject
        out.append({"tool": call["tool"], "args": args})
    return out


_SCANNER_INTENTS = ("scanner_list", "scanner_ticker", "scanner_top", "scanner_day")


def _planned(text: str, today: str, context: Mapping | None, ctx: Mapping, ask, budget: _Budget, known: frozenset, scope: str) -> dict:
    """Steps 4, 5 and 6."""
    card_note = {"aiNote": resting()} if ask is None and scope == "owner" and enabled() and resting() else {}
    if not enabled() or (ask is None and resting(scope)) or not budget():
        return {**_search_card(text, ctx, known), **card_note}
    plan = understand(text, today, context, ask, scope=scope)
    if plan is None:
        out = _search_card(text, ctx, known)
        return {**out, "aiNote": resting()} if ask is None and scope == "owner" and resting() else out
    if not plan["calls"]:
        if not plan["general"]:
            return _search_card(text, ctx, known)
        # A general idea: the glossary, else the help text, else one plain line. Never AI-written prose.
        return {**_run_calls({"calls": [_call("define", query=text)]}, text, ctx), "understoodBy": "AI"}
    calls = _fill_tickers(plan["calls"], text, ctx, known)
    # The tool lines are the answer: no AI writer (2026-11-11 design change).
    return {**_run_calls({"calls": calls}, text, ctx), "understoodBy": "AI"}


def _second_opinion(plan: dict, text: str, today: str, context: Mapping | None, ctx: Mapping, ask, budget, known: frozenset, scope: str) -> dict | None:
    """A rule that only guessed (plan["unsure"]) lets the AI read the meaning first. Its lookups win when it names any;
    with the AI off, resting, over its limit or unsure itself, None: the rule's own answer stands."""
    if not plan.get("unsure") or not enabled() or (ask is None and resting(scope)) or not budget():
        return None
    understood = understand(text, today, context, ask, scope=scope)
    if not understood or not understood["calls"]:
        return None
    return {**_run_calls({"calls": _fill_tickers(understood["calls"], text, ctx, known)}, text, ctx), "understoodBy": "AI"}


def owner_subject(text: str) -> bool:
    """Does a question name something only the owner has: the Bot, trades, paper accounts, the Scorecard, profit
    and loss, a missed trade? Question forms alone ("how much did NVDA move", "tell me about NVDA", "went down")
    are market questions now (plan D1: privacy is the tool allow-list, not word blocking)."""
    from momx import chat_scanner, chatbot
    low = chatbot._MARKET_PHRASES.sub(" ", str(text or "").lower())
    return bool(chatbot.OWNER_TERMS.search(low) or chatbot._BOT_NOUNS.search(low) or re.search(chatbot.RESULT, low)
                or chat_scanner.about_trades(low) or re.search(chatbot.INTENTS[0][1], low))


def _refused(text: str, now, known: frozenset) -> dict:
    """The plain line for a question about the owner's things, with somewhere to go for any ticker it names."""
    from momx import chat_pages, chat_tools, chatbot
    out = chatbot._scanner_only()
    ticker = _named_ticker(text, known, position=False)
    if ticker:
        day = chat_tools.session_day(now, None, False, None).isoformat()
        out = {**out, "links": chat_pages.clean_links(chat_tools.ticker_links(ticker, day, "scanner"), "scanner")}
    return out


def _answer_scanner(text: str, artifacts, now, context: Mapping | None, ask, ai_allowed: Callable[[], bool] | None, account: str | None = None) -> dict:
    """A regular user's answer. A question about the owner's records is refused before anything is read."""
    from momx import chat_tools, chatbot
    if not text:
        return chatbot._scanner_only()
    known = chat_tools.market_tickers(Path(artifacts))
    if owner_subject(text):
        return _refused(text, now, known)
    ctx = _context(context, artifacts, now, "scanner", account)
    budget = _Budget(ai_allowed)
    ctx.update(ask=ask, aiAllowed=budget)               # for a tool that asks the AI on its own (the why-answer's reasons)
    plan = sure_calls(text, ctx, known)
    if plan is not None:
        return _second_opinion(plan, text, now.date().isoformat(), context, ctx, ask, budget, known, "scanner") or _run_calls(plan, text, ctx)
    out = chatbot.ask_scanner(text, artifacts, now, _records_context(ctx), account=account)
    if out.get("busy"):
        return out
    if out.get("intent") in _SCANNER_INTENTS:
        return out                                     # the saved scanner's lines are the answer; no AI writer
    return _planned(text, now.date().isoformat(), context, ctx, ask, budget, known, "scanner")


def answer_for_session(question: str, artifacts, is_owner: bool, account: str, context: Mapping | None = None) -> dict:
    """What /api/bot-chat answers. `is_owner` comes from the signed-in session and nothing else.
    A regular user is limited, and an over-limit question reads no file and asks no AI."""
    if is_owner:
        return finish(answer(question, artifacts, context=context, account=account), "owner")
    from momx import chat_limits
    if not chat_limits.LIMITS.take_question(account):
        return finish(chat_limits.over_limit_reply(), "scanner")
    return finish(answer(question, artifacts, context=context, scope="scanner", ai_allowed=lambda: chat_limits.LIMITS.take_ai(account), account=account), "scanner")


def more_for_session(token: Any, is_owner: bool, account: str) -> dict:
    """The slow part of an answer (Google News, the AI's reasons, chart numbers), for the session that asked:
    POST /api/bot-chat {"more": token}. A token from another session, or an old one, gets one plain line.
    Waits at most a few seconds; uses up no question."""
    from momx import chat_pages, chat_tools, chat_why
    scope = "owner" if is_owner else "scanner"
    got = chat_why.collect(token, account, scope)
    summary = got.pop("summary", None)
    got.update(chat_tools.extras({key: got.pop(key) for key in ("sections", "views", "miniChart") if key in got}, scope))
    got["links"] = chat_pages.clean_links(got.get("links"), scope)
    if is_owner:                                       # the owner's AI summary; a regular session never gets the field
        from momx import chat_summary
        made = chat_summary.clean(summary)
        if made:
            got["summary"] = made
    return {"more": got}


def finish(out: Mapping, scope: str) -> dict:
    """The reply as the screen expects it: every field there, links for a records answer, and only links this session may have."""
    from momx import chat_pages, chat_tools, chatbot
    reply_out = dict(out)
    # No AI-written prose reaches the screen (2026-11-11 design change): any such field is dropped here as well.
    for key in ("ai", "general"):
        reply_out.pop(key, None)
    # The owner's AI summary comes only through the "more" call (more_for_session), never in the fast part, for anyone.
    reply_out.pop("summary", None)
    intent, ticker, day, side = reply_out.get("intent"), reply_out.get("ticker"), reply_out.get("day"), reply_out.get("side")
    links = list(reply_out.get("links") or [])
    if not links and intent in ("what_if", "why_failed", "why_missed", "show", "status") and scope == "owner":
        links = [chat_pages.link("Bot", f"Open Bot for {ticker}" if ticker else "Open Bot")]
    if not links and intent in _SCANNER_INTENTS and day:
        links = [chat_tools.history_link(ticker if isinstance(ticker, str) else None, day, side if side in ("bull", "bear") else None)]
    refusal = list(reply_out.get("lines") or []) == [chatbot.SCANNER_ONLY_LINE]
    if not links and intent == "help" and not refusal:  # a help reply is never a dead end (a refusal stays a plain line)
        links = [chat_pages.link("Learn + Setup", "Open Learn + Setup"), chat_tools.history_link(None, day if isinstance(day, str) else None)]
    reply_out["links"] = chat_pages.clean_links(links, scope)
    reply_out["table"] = chat_tools.clean_table(reply_out.get("table"), scope)
    reply_out.setdefault("asOf", None)
    return reply_out


def answer(question: str, artifacts, now=None, context: Mapping | None = None,
           ask: Callable[[str, str, dict], dict] | None = None, scope: str = "owner",
           ai_allowed: Callable[[], bool] | None = None, account: str | None = None) -> dict:
    """The chat's full answer, down the steps at the top of this file. Any AI failure just falls to the next step.
    Anything but scope="owner" is a regular user's answer."""
    from datetime import datetime
    from momx import chat_tools, chatbot
    now = (now or datetime.now(chatbot.ET)).astimezone(chatbot.ET)
    text = " ".join(str(question or "").split())[:300]
    if scope != "owner":
        return _answer_scanner(text, artifacts, now, context, ask, ai_allowed, account)
    if not text:
        return chatbot.ask(question, artifacts, now, context)
    ctx = _context(context, artifacts, now, "owner", account)
    ctx.update(ask=ask, aiAllowed=None)
    today = now.date().isoformat()
    day, _named = chatbot.parse_day(text, now.date())
    on_file = set(chatbot.day_tickers(artifacts, day.isoformat(), today)) | set(chatbot.day_tickers(artifacts, today, today))
    records_known = chatbot.known_tickers(artifacts, day.isoformat(), today) | on_file
    known = chat_tools.market_tickers(Path(artifacts)) | frozenset(records_known)
    plan = sure_calls(text, ctx, known)
    if plan is not None:
        return _second_opinion(plan, text, today, context, ctx, ask, _Budget(None), known, "owner") or _run_calls(plan, text, ctx)
    records_ctx = _records_context(ctx)
    named_ticker = _named_ticker(text, known, position=False)
    # The Bot's records only for a question about the owner's things or a ticker on file: a question form alone
    # ("how did ...") is not one ("how did the market do today" got "no ticker I have records for", 2026-11-11).
    bot_subject = owner_subject(text) or bool(_TRADE_SUBJECT.search(text.lower())) or (named_ticker is not None and named_ticker in records_known)
    if (bot_subject and chatbot.explicit_bot(text, named_ticker is not None)) or chatbot.may_be_scanner(text, records_known, on_file, ctx.get("ticker") or ""):
        return chatbot.ask(text, artifacts, now, records_ctx)   # the records' lines are the answer; no AI writer
    return _planned(text, today, context, ctx, ask, _Budget(None), known, "owner")


# ---------------------------------------------------------------- the chat's home screen
def _useful(part: Mapping) -> bool:
    """A tool's answer counts as data only if it says something from a record, not just what is missing."""
    facts = part.get("facts") or {}
    return bool(part.get("lines") and facts) and not any(facts.get(key) for key in ("error", "missing", "needsTicker"))


def _ticker_card(ticker: str, ctx: Mapping) -> dict | None:
    from momx import chat_pages, chat_tools
    quote = chat_tools.run("quote", {"ticker": ticker}, ctx)
    levels = chat_tools.run("chart_levels", {"ticker": ticker, "timeframe": "5m"}, ctx)
    signals = chat_tools.run("chart_signals", {"ticker": ticker, "timeframe": "5m"}, ctx)
    parts = [part for part in (quote, levels, signals) if _useful(part)]
    if not parts:
        return None
    return {"kind": "ticker", "title": f"Keep looking at {ticker}", "lines": [line for part in parts for line in part["lines"][:2]][:5],
            "asOf": next((part["asOf"] for part in parts if part["asOf"]), None),
            "links": [chat_pages.link("Charts & OI", f"{ticker} chart", ticker=ticker, timeframe="5m"), chat_pages.link("Quick Options", f"{ticker} options", ticker=ticker)],
            "question": f"{ticker} chart levels"}


def _scanner_card(ctx: Mapping) -> dict | None:
    from momx import chat_scanner, chat_tools
    lines, links, as_of = [], [], None
    for board, count in (("Mag7", 3), ("Watchlist", 7)):
        try:
            top = chat_scanner.top_now(board, "bull", count, artifacts=ctx["artifacts"], now=ctx["now"])
        except Exception:  # noqa: BLE001 - a card that cannot read is left out
            top = None
        if not top or not top.get("rows"):
            continue
        names = ", ".join(str(row.get("symbol")) for row in top["rows"])
        lines.append(f"{board}: {names}")
        links += top.get("links") or []
        as_of = as_of or top.get("asOf")
    if not lines:
        return None
    from momx import chat_pages
    return {"kind": "scanner", "title": "Top of the scanner now", "lines": lines, "asOf": as_of,
            "links": chat_pages.clean_links(links, str(ctx.get("scope") or "")) or [chat_tools.history_link(None, None)], "question": "which tickers stayed on top today"}


def _walls_card(ticker: str, ctx: Mapping) -> dict | None:
    from momx import chat_pages, chat_tools
    walls = chat_tools.run("walls", {"ticker": ticker}, ctx)
    if not _useful(walls):
        return None
    return {"kind": "walls", "title": f"{ticker} high OI walls", "lines": walls["lines"][:4], "asOf": walls["asOf"],
            "links": walls["links"] or [chat_pages.link("Quick Options", f"{ticker} options", ticker=ticker, sub="high-oi")], "question": f"call wall on {ticker}"}


def _bot_card(ctx: Mapping) -> dict | None:
    """Owner only: orders the paper accounts hold now. The small day files, never the Bot's big ones."""
    from momx import chat_pages, chatbot
    day = ctx["now"].date().isoformat()
    lines = []
    for account, label in chatbot.ACCOUNTS:
        doc = chatbot._read(Path(ctx["artifacts"]) / "momx_paper_mirror" / account / f"{day}.json")
        rows = doc.get("rows") if isinstance(doc, Mapping) else None
        if not isinstance(rows, Mapping):
            continue
        open_rows = [r for r in rows.values() if isinstance(r, Mapping) and r.get("status") not in ("closed", "missed", "canceled", "cancelled")]
        names = ", ".join(sorted({str(r.get("symbol")) for r in open_rows if r.get("symbol")})[:6])
        lines.append(f"{label}: {len(open_rows)} open" + (f" ({names})" if names else ""))
    if not lines:
        return None
    return {"kind": "bot", "title": "Open bot trades", "lines": lines, "asOf": f"as of {ctx['now'].strftime('%H:%M')} ET",
            "links": [chat_pages.link("Bot", "Open Bot")], "question": "what is the bot doing"}


def home_for_session(artifacts, is_owner: bool, account: str, context: Mapping | None = None, now=None) -> dict:
    """The chat's opening screen. Cached reads only, and a card with no data is left out. Costs no question and no AI call."""
    from datetime import datetime
    from momx import chat_limits, chat_pages, chatbot
    now = (now or datetime.now(chatbot.ET)).astimezone(chatbot.ET)
    scope = "owner" if is_owner else "scanner"
    ctx = _context(context, artifacts, now, scope, account)
    ticker = ctx.get("pageTicker")
    chips = chat_pages.chips_for(ctx.get("pageName"), ticker, scope)
    if not is_owner and not chat_limits.LIMITS.take_home(account):
        return {"home": {"cards": [], "chips": chips}}
    if ticker:
        try:
            from momx import chat_why
            chat_why.warm(ticker)                     # one Google News fetch, cached 5 minutes, so a why-question next is instant
        except Exception:  # noqa: BLE001 - warming is a nicety
            pass
    builders: list = [lambda: _ticker_card(ticker, ctx) if ticker else None, lambda: _scanner_card(ctx), lambda: _walls_card(ticker, ctx) if ticker else None]
    if is_owner:
        builders.append(lambda: _bot_card(ctx))
    cards = []
    for build in builders:
        try:
            card = build()
        except Exception:  # noqa: BLE001 - one card must never blank the screen
            card = None
        if card:
            card["links"] = chat_pages.clean_links(card.get("links"), scope)
            cards.append(card)
    return {"home": {"cards": cards, "chips": chips}}
