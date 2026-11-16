"""How the Ask chat talks to the AI services (2026-11-11). Chat only: agents/ai_provider.py is shared and stays as it is.

The shared provider is built for one slow, important call; the chat makes many small ones and must never
wait. So here, for the chat alone:

* One budget for the whole call (8 seconds by default), passed to the HTTP request itself.
* No retry on a refusal or a dropped connection. A 429 is an answer, not a hiccup. A 400 is final too:
  the shared provider's second "compat" try would spend another request of the free quota.
* Every request sent is counted against the chat's day cap (chat_limits), not every question:
  one question can send a request to Claude and one to the free service.
* A Claude reply saying the credit is used up is final: that call moves straight on to the free service,
  and Claude is skipped for hours. Before this, one question could burn four calls finding that out.

It only asks for text. It cannot reach a broker and is given no account data.
"""
from __future__ import annotations

import re
import threading
import time
from typing import Any

BUDGET_SECONDS = 8.0
#: Claude's share of the budget when another service is there to try after it.
FIRST_SHARE = 0.6
CREDIT_REST_SECONDS = 6 * 3600.0
#: How long Claude is left alone after it times out or errors, while the free service carries on.
CLAUDE_TROUBLE_REST_SECONDS = 120.0
_CREDIT = re.compile(r"credit\s+balance|insufficient\s+(?:credit|funds|balance)|out\s+of\s+credit|billing", re.I)
_QUOTA = re.compile(r"\b429\b|quota|rate.?limit|resource.?exhausted|too\s+many\s+requests", re.I)
_BAD_REPLY = re.compile(r"valid\s+json|not\s+json|empty\s+response|declined\s+to\s+answer", re.I)
_NO_KEY = re.compile(r"no\s+(?:ai\s+|\w+\s+)?key", re.I)
_STATE = {"claude_until": 0.0, "claude_why": ""}
_LOCK = threading.Lock()


def cause_of(error: Any) -> str:
    """Why a call failed, as one word: credit, quota, bad_reply, no_key, or trouble (no answer, a network error, a server error)."""
    text = str(error or "")
    for name, pattern in (("credit", _CREDIT), ("quota", _QUOTA), ("bad_reply", _BAD_REPLY), ("no_key", _NO_KEY)):
        if pattern.search(text):
            return name
    return "trouble"


def claude_resting() -> str:
    """Why Claude is being skipped ("" = it is being asked)."""
    with _LOCK:
        return _STATE["claude_why"] if time.monotonic() < _STATE["claude_until"] else ""


def _rest_claude(seconds: float, why: str) -> None:
    with _LOCK:
        _STATE.update(claude_until=time.monotonic() + seconds, claude_why=why)


def reset() -> None:
    with _LOCK:
        _STATE.update(claude_until=0.0, claude_why="")


class _Stop(Exception):
    """Raised through the shared provider to end its call at once; its text becomes the error."""


#: What a _Stop says when the day's cap is reached; cause_of() reads it as a quota.
CAP_REACHED = "the chat's AI quota for today is used up"


def _one_shot(deadline: float, send, take=None):
    """A transport for the shared provider: never past `deadline`, and a refusal or a dropped
    connection ends the call rather than being tried again. `take()` is asked before every request
    that really goes out; when it says no, nothing is sent."""
    def transport(url: str, *, headers: dict, body: bytes, timeout: float) -> tuple[int, str]:
        left = deadline - time.monotonic()
        if left <= 0.2:
            raise _Stop("no time left")
        if take is not None and not take():
            raise _Stop(CAP_REACHED)
        try:
            status, text = send(url, headers=headers, body=body, timeout=min(timeout, left))
        except (OSError, ConnectionError) as exc:
            if isinstance(exc, TimeoutError):
                raise
            raise _Stop(f"could not reach the service ({type(exc).__name__})") from exc
        if status == 429 or status >= 500 or status in (400, 409, 425) or (status != 200 and _CREDIT.search(text or "")):
            raise _Stop(f"HTTP {status}: {str(text)[:200]}")
        return status, text
    return transport


def complete(system: str, user: str, schema: dict | None, seconds: float = BUDGET_SECONDS, max_tokens: int = 600, take=None) -> dict:
    """One answer from the first service that gives one, inside `seconds`. Never raises.
    `take()` is asked before each request goes out (the day's cap); None counts nothing.
    Returns the shared provider's result: {"ok", "data", "text", "provider", "error", ...}."""
    from agents import ai_provider as shared
    deadline = time.monotonic() + seconds
    claude = shared.ClaudeProvider(effort="low", timeout=seconds, transport=_one_shot(deadline, shared._urllib_transport, take))
    free = shared.OpenAIProvider(timeout=seconds, transport=_one_shot(deadline, shared._urllib_transport, take))
    last: dict | None = None
    if claude.available() and not claude_resting():
        share = deadline if not free.available() else time.monotonic() + seconds * FIRST_SHARE
        claude._transport = _one_shot(share, shared._urllib_transport, take)
        out = _ask(claude, system, user, schema, max_tokens)
        if out.get("ok"):
            return out
        last = out
        cause = cause_of(out.get("error"))
        if CAP_REACHED in str(out.get("error") or ""):
            return out                                  # nothing was sent; the free service would be refused the same way
        if cause == "credit":
            _rest_claude(CREDIT_REST_SECONDS, "Claude has no credit left")
        elif cause != "bad_reply":
            _rest_claude(CLAUDE_TROUBLE_REST_SECONDS, "Claude did not answer")
    if free.available():
        return _ask(free, system, user, schema, max_tokens)
    return last if last is not None else {"ok": False, "provider": "none", "data": None, "text": "", "error": "No AI key set."}


def _ask(provider: Any, system: str, user: str, schema: dict | None, max_tokens: int) -> dict:
    try:
        out = provider.complete(system=system, user=user, max_tokens=max_tokens, schema=schema)
    except Exception as exc:  # noqa: BLE001 - the chat must answer whatever the service does
        return {"ok": False, "provider": getattr(provider, "name", "?"), "data": None, "text": "", "error": type(exc).__name__}
    return out if isinstance(out, dict) else {"ok": False, "provider": getattr(provider, "name", "?"), "data": None, "text": "", "error": "no answer"}
