"""How much a regular user may ask the chat (2026-11-11). The owner is never limited.

In memory, per account, thread safe. A restart clears it, which only ever gives someone a fresh allowance.
"""
from __future__ import annotations

import os
import threading
import time
from collections import deque
from datetime import datetime
from typing import Callable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WINDOW_SECONDS = 600
QUESTIONS_PER_WINDOW = 30
AI_ANSWERS_PER_DAY = 20
#: Heavy scanner files an account may cause to be read. One such read holds the app's single reader for up to two minutes.
READS_PER_WINDOW = 3
READS_PER_DAY = 20
#: How often an account's chat may open its home cards. Those are cached reads, but a stuck screen must not spin on them.
HOME_PER_WINDOW = 40
#: All the chat's real AI calls in one ET day, for everyone together. The free service's quota is shared with other parts of the app.
#: The real number comes from AI Studio; set it with AGX_CHAT_AI_CALLS_PER_DAY. Regular users stop at half of it, so they cannot use up the owner's.
AI_CALLS_PER_DAY_DEFAULT = 400
OVER_LIMIT_LINE = "You have asked a lot of questions in the last few minutes. Please try again in a few minutes."
_MAX_ACCOUNTS = 2000


def _et_day() -> str:
    return datetime.now(ET).date().isoformat()


def ai_calls_per_day() -> int:
    try:
        return max(int(os.environ.get("AGX_CHAT_AI_CALLS_PER_DAY", "") or AI_CALLS_PER_DAY_DEFAULT), 0)
    except ValueError:
        return AI_CALLS_PER_DAY_DEFAULT


class ChatLimits:
    def __init__(self, clock: Callable[[], float] = time.monotonic, today: Callable[[], str] = _et_day) -> None:
        self._clock, self._today = clock, today
        self._lock = threading.Lock()
        self._asked: dict[str, deque] = {}
        self._ai: dict[str, tuple[str, int]] = {}
        self._reads: dict[str, deque] = {}
        self._reads_today: dict[str, tuple[str, int]] = {}
        self._home: dict[str, deque] = {}
        self._calls: tuple[str, int] = ("", 0)

    def take_home(self, account: str) -> bool:
        """Count one opening of the home cards; False when this account has opened them too often just now."""
        now = self._clock()
        with self._lock:
            if len(self._home) > _MAX_ACCOUNTS:
                self._home = {k: v for k, v in self._home.items() if v and now - v[-1] < WINDOW_SECONDS}
            opened = self._home.setdefault(account, deque())
            while opened and now - opened[0] >= WINDOW_SECONDS:
                opened.popleft()
            if len(opened) >= HOME_PER_WINDOW:
                return False
            opened.append(now)
            return True

    def ai_call_allowed(self, scope: str) -> bool:
        """Is there room for another real AI request today? Counts nothing: the request is counted when it is sent."""
        today, cap = self._today(), ai_calls_per_day()
        with self._lock:
            day, used = self._calls
            used = used if day == today else 0
            return used < (cap if scope == "owner" else cap // 2)

    def take_ai_call(self, scope: str) -> bool:
        """Count one real AI request against the day's cap, just before it is sent. Regular users stop at half of it."""
        today, cap = self._today(), ai_calls_per_day()
        with self._lock:
            day, used = self._calls
            used = used if day == today else 0
            if used >= (cap if scope == "owner" else cap // 2):
                return False
            self._calls = (today, used + 1)
            return True

    def take_read(self, account: str) -> bool:
        """Count one heavy file read; False when this account has used up its reads for now or for today."""
        now, today = self._clock(), self._today()
        with self._lock:
            if len(self._reads) > _MAX_ACCOUNTS:
                self._reads = {k: v for k, v in self._reads.items() if v and now - v[-1] < WINDOW_SECONDS}
                self._reads_today = {k: v for k, v in self._reads_today.items() if v[0] == today}
            reads = self._reads.setdefault(account, deque())
            while reads and now - reads[0] >= WINDOW_SECONDS:
                reads.popleft()
            day, used = self._reads_today.get(account, (today, 0))
            used = used if day == today else 0
            if len(reads) >= READS_PER_WINDOW or used >= READS_PER_DAY:
                return False
            reads.append(now)
            self._reads_today[account] = (today, used + 1)
            return True

    def take_question(self, account: str) -> bool:
        """Count one question; False when this account has used up the last ten minutes' allowance."""
        now = self._clock()
        with self._lock:
            if len(self._asked) > _MAX_ACCOUNTS:
                self._asked = {k: v for k, v in self._asked.items() if v and now - v[-1] < WINDOW_SECONDS}
            asked = self._asked.setdefault(account, deque())
            while asked and now - asked[0] >= WINDOW_SECONDS:
                asked.popleft()
            if len(asked) >= QUESTIONS_PER_WINDOW:
                return False
            asked.append(now)
            return True

    def take_ai(self, account: str) -> bool:
        """Count one AI-assisted answer; False when this account has had today's."""
        today = self._today()
        with self._lock:
            if len(self._ai) > _MAX_ACCOUNTS:
                self._ai = {k: v for k, v in self._ai.items() if v[0] == today}
            day, used = self._ai.get(account, (today, 0))
            used = used if day == today else 0
            if used >= AI_ANSWERS_PER_DAY:
                return False
            self._ai[account] = (today, used + 1)
            return True

    def reset(self) -> None:
        with self._lock:
            self._asked.clear()
            self._ai.clear()
            self._reads.clear()
            self._reads_today.clear()
            self._home.clear()
            self._calls = ("", 0)


LIMITS = ChatLimits()


def over_limit_reply() -> dict:
    return {"intent": "help", "ticker": None, "day": None, "scope": "scanner", "facts": {}, "lines": [OVER_LIMIT_LINE], "suggest": []}
