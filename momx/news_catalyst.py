"""The AI news reader: WHY a graded ticker is moving, on the scanner row.

Ganesh 2026-09-24, after BOIL (+15%) and GLND (+94%) ran on news the scanner
could not read: price and volume rules (GO / OPT) know THAT a stock moves,
not WHY. This reads each A+/A ticker's recent headlines with an AI model and
stamps ``row["catalyst"]``: category (EARNINGS, UPGRADE, FDA, OFFERING...),
direction (bullish / bearish / neutral, judged on the NEWS - an offering is
bearish even on a green day), confidence and one plain sentence.

The judging is agents.catalyst_explainer.explain - the same code behind the
"Why is it moving?" button, including its honesty rule: headlines that do
not explain the move come back UNKNOWN, never an invented reason.

Provider: Claude first, and when the Claude credit runs out (or Claude
errors or declines) the free Gemini key - his rule, same day
(agents.ai_provider.claude_first_provider).

Cost control, because every call spends his credit:
* news first: A+/A rows with a headline, then any ticker whose headline is
  about that company alone and fresh (FRESH_NEWS_HOURS) - newest first;
* one judgement per symbol per NEW headline, never more than every
  REJUDGE_MINUTES, only on weekdays 06:30-15:30 ET;
* a hard daily cap (AGX_CATALYST_MAX_CALLS, default 100) on model calls;
* zero headlines = UNKNOWN locally, no call (explain() already does this).
One background thread, never on the build path. Persisted per ET day so a
worker restart neither re-spends nor loses the day's reads. Never raises.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_catalyst"
REJUDGE_MINUTES = 20
DEFAULT_MAX_CALLS = 100
#: News-first (his order, 2026-09-24: "news read first, see it is positive
#: news, then momentum building, then A/A+"): any ticker whose headline is
#: about THAT company and is fresh gets read, graded or not.
FRESH_NEWS_HOURS = 18
OPEN_MIN, CLOSE_MIN = 6 * 60 + 30, 15 * 60 + 30
IDLE_SECONDS = 10
STAMP_FIELDS = ("category", "direction", "confidence", "summary", "headline", "provider", "at")


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def max_calls() -> int:
    try:
        return max(int(os.getenv("AGX_CATALYST_MAX_CALLS") or DEFAULT_MAX_CALLS), 0)
    except ValueError:
        return DEFAULT_MAX_CALLS


def _news_age_hours(news: Mapping, now: datetime) -> float | None:
    try:
        at = datetime.fromisoformat(str(news.get("at") or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    return (now - at).total_seconds() / 3600.0


def priority(row: Any, now: datetime) -> int | None:
    """Reading order, lowest first; None = not read.

    0  A+ / A with any headline (the tickers he already watches)
    1  any other ticker whose headline is SPECIFIC to it (the news names at
       most three tickers, momx.news scope) and at most FRESH_NEWS_HOURS old -
       news first, before any letter exists.
    Market round-ups ("10 stocks moving") are never read on their own: they
    name the ticker without explaining it, and would burn the daily cap.
    """
    if not isinstance(row, Mapping):
        return None
    news = _m(row.get("news"))
    if not news.get("headline"):
        return None
    if _m(row.get("grade")).get("letter") in ("A+", "A"):
        return 0
    age = _news_age_hours(news, now)
    if news.get("scope") == "specific" and age is not None and age <= FRESH_NEWS_HOURS:
        return 1
    return None


def is_candidate(row: Any, now: datetime | None = None) -> bool:
    return priority(row, now or datetime.now(ET)) is not None


def enabled() -> bool:
    """``AGX_CATALYST_READER=0`` turns the reader off (the test suite sets it:
    fixture boards must never spend real AI or news calls)."""
    return str(os.getenv("AGX_CATALYST_READER", "1")).strip().lower() not in {"0", "false", "no", "off"}


def in_window(local: datetime) -> bool:
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and OPEN_MIN <= minute <= CLOSE_MIN


def _default_headlines(symbol: str) -> list[dict]:
    from momx import news
    return news.recent_headlines(symbol)


def _default_explain(symbol: str, change_pct: Any, headlines: list[dict]) -> dict:
    from agents import catalyst_explainer
    from agents.ai_provider import claude_first_provider
    # Low effort: this is a short classification, and it runs many times a day.
    return catalyst_explainer.explain(symbol, change_pct, headlines, provider=claude_first_provider(effort="low"))


class CatalystBook:
    """Keeps today's AI news reads per symbol and stamps ``row["catalyst"]``."""

    def __init__(self, directory: Path, headlines: Callable | None = None, explain: Callable | None = None,
                 background: bool = True, always_on: bool = False) -> None:
        self.directory = Path(directory)
        self._always_on = always_on  # tests inject fakes and skip the env switch
        self._headlines = headlines or _default_headlines
        self._explain = explain or _default_explain
        self._background = background
        self._lock = threading.Lock()
        self._day = ""
        self._reads: dict[str, dict] = {}        # symbol -> last read (+ "key": the headline it read)
        self._calls = 0                           # model calls spent today
        self._wanted: dict[str, dict] = {}        # symbol -> {"key", "pct", "seen"}
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- public

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            with self._lock:
                self._apply(payload, now)
            if not self._background:
                self.run_once(now)
                with self._lock:
                    self._stamp(payload)
            elif self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="momx-news-catalyst", daemon=True)
                self._thread.start()
        except Exception:  # noqa: BLE001 - a news-reader bug never costs a scan
            return

    def run_once(self, now: datetime | None = None) -> None:
        """Read ONE due symbol (the loop's body). One at a time on purpose:
        each read is a paid model call and a slow provider must not pile up."""
        now = now or datetime.now(ET)
        local = now.astimezone(ET)
        if not in_window(local) or not (self._always_on or enabled()):
            return
        with self._lock:
            self._load(local.date().isoformat())
            if self._calls >= max_calls():
                return
            due = None
            for symbol, want in sorted(self._wanted.items(), key=lambda kv: (kv[1]["prio"], -kv[1]["newsTs"])):
                if local.timestamp() - want["seen"] > 30 * 60:
                    continue  # no longer on the board as A+/A
                held = self._reads.get(symbol)
                if held and held.get("key") == want["key"] and not held.get("failed"):
                    continue  # this headline is already read (a failed read retries below)
                if held and local.timestamp() - float(held.get("ts") or 0) < REJUDGE_MINUTES * 60:
                    continue  # new headline, but read too recently
                due = (symbol, dict(want))
                break
        if due is None:
            return
        symbol, want = due
        try:
            headlines = self._headlines(symbol)
        except Exception:  # noqa: BLE001
            headlines = []
        try:
            verdict = self._explain(symbol, want.get("pct"), headlines)
        except Exception:  # noqa: BLE001
            verdict = None
        spent = bool(headlines)  # explain() answers zero headlines locally, for free
        read = {
            "key": want["key"],
            "ts": local.timestamp(),
            "at": local.replace(microsecond=0).isoformat(),
        }
        if isinstance(verdict, Mapping) and verdict.get("available"):
            credited = _m(verdict.get("headline"))
            read.update({
                "category": verdict.get("category") or "UNKNOWN",
                "direction": verdict.get("direction") or "neutral",
                "confidence": verdict.get("confidence") or "low",
                "summary": verdict.get("summary") or "",
                "headline": credited.get("title"),
                "provider": verdict.get("provider"),
            })
        else:
            # No provider, or the call failed on every provider: remember the
            # attempt (so the next pass does not re-spend at once) but stamp
            # nothing - a failed read must never look like "no news".
            read["failed"] = str(_m(verdict).get("reason") or "no answer")
        with self._lock:
            if self._day != local.date().isoformat():
                return
            self._reads[symbol] = read
            if spent:
                self._calls += 1
            self._save()

    # ---------------------------------------------------------------- internal

    def _loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(IDLE_SECONDS)

    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day, self._reads, self._calls, self._wanted = day, {}, 0, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict):
                if isinstance(doc.get("reads"), dict):
                    self._reads = {k: v for k, v in doc["reads"].items() if isinstance(v, dict)}
                self._calls = int(doc.get("calls") or 0)
        except (OSError, ValueError, UnicodeDecodeError, TypeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": self._day, "calls": self._calls, "reads": self._reads}), encoding="utf-8")
        os.replace(tmp, path)

    def _rows(self, payload: Any) -> list[dict]:
        rows = []
        for section in ("rows", "rest"):
            part = _m(payload).get(section)
            if isinstance(part, list):
                rows.extend(r for r in part if isinstance(r, dict))
        return rows

    def _apply(self, payload: Any, now: datetime) -> None:
        local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        self._load(local.date().isoformat())
        for row in self._rows(payload):
            symbol = row.get("symbol")
            prio = priority(row, local) if isinstance(symbol, str) and symbol else None
            if prio is not None:
                age = _news_age_hours(_m(row.get("news")), local)
                self._wanted[symbol] = {"key": str(_m(row.get("news")).get("headline")),
                                        "pct": row.get("pctChange"), "seen": local.timestamp(), "prio": prio,
                                        "newsTs": local.timestamp() - (age or 0) * 3600}
        self._stamp(payload)

    def _stamp(self, payload: Any) -> None:
        for row in self._rows(payload):
            read = self._reads.get(row.get("symbol"))
            row["catalyst"] = ({k: read.get(k) for k in STAMP_FIELDS}
                               if read and not read.get("failed") and read.get("category") else None)
