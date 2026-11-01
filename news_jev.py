"""TypeSafe Jev reads each new MomX headline (2026-10-02, his ask "News - can we use Jev AI").

Jev (TypeSafe System One, https://docs.typesafe.ai) returns a TYPED judgment, not text:
one Choice per headline - which way does this item push the ticker today:

    bullish | bearish | not_important      (+ the probability of each, + confidence)

The 8-source headline store (news_feed_store.py) keeps scraping as before. This module
only ANNOTATES what the store serves: ``annotate(payload)`` attaches ``item["jev"]`` to
every feed / latest item already judged, and queues the ones not judged yet (published
in the last JUDGE_WINDOW_HOURS). One daemon thread judges the queue, one HTTP call per
headline (~0.2 s, ~520 input tokens = ~$0.00002 at the published $0.042 / M input),
and keeps every answer in a JSON file so a restart never pays twice.

Rules that keep it harmless:
* No key (TYPESAFE_API_KEY unset) or AGX_JEV_NEWS=0 -> nothing is queued or sent.
* The key is read from the environment and never logged or returned.
* 429 / 529 -> exponential backoff; any other failure skips that headline for the
  day. Never raises into the server.
* MAX_PER_DAY caps the calls (a flood guard, ~10x a normal day).

NOT proven: Jev's label is a reading of the headline, not a forecast. It is shown,
recorded on bot trades and checked against what the stock did before it is trusted.
"""
from __future__ import annotations

import hashlib
import json
import os
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

API_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-latest"
JUDGE_WINDOW_HOURS = 48
KEEP_DAYS = 7
MAX_PER_DAY = 10000
SAVE_EVERY = 20
LABELS = ("bullish", "bearish", "not_important")
#: Bumped whenever QUESTION's wording changes; answers to an older wording are
#: re-asked (recent headlines) instead of being shown. v2 (2026-10-02): routine
#: SEC paperwork (Goldman's 424B2 note prospectuses read as "Bearish" in v1).
QUESTION_VERSION = 2

QUESTION = {
    "type": "choice",
    "instructions": ("This is a news item about the stock `ticker`. For a trader holding short-dated "
                     "options on `ticker` today, which way does this news push `ticker`'s stock price?"),
    "criteria": {
        "bullish": ("Good news specifically for this company that can push the stock up today: analyst "
                    "upgrade, earnings beat, raised guidance, big contract or partnership, product launch, "
                    "approval, buyback, takeover offer."),
        "bearish": ("Bad news specifically for this company that can push the stock down: downgrade, "
                    "earnings miss, lowered guidance, a new share offering that dilutes stockholders (not "
                    "routine note or debt programs), lawsuit or probe, recall, lost contract, executive exit."),
        "not_important": ("Routine or minor item, a general market or sector round-up, a list article, an "
                          "opinion piece with no new facts, or barely about this company; also routine "
                          "regulatory paperwork such as 424B2 structured-note or debt prospectuses, Form 4 "
                          "insider filings, 13F/13G holdings reports or exhibit-only 8-K filings that carry no "
                          "new business news; unlikely to move the stock today."),
    },
}


def shown_reading(reading: dict | None, item: dict) -> dict | None:
    """Jev's reading as the app SHOWS and TRADES it (2026-10-10): off-topic
    headlines and Bullish / Bearish under 70% become Not important, with the
    raw read and the reason kept (momx/news_relevance.py). Never raises."""
    try:
        from momx import etf_list, news_relevance

        sym = str(item.get("symbol") or item.get("ticker") or "").upper()
        return news_relevance.effective(reading, sym, item.get("headline"), etf_list.security_name(sym),
                                        is_etf=etf_list.is_etf(sym))
    except Exception:  # noqa: BLE001
        return dict(reading) if isinstance(reading, dict) else None


def off_topic(item: dict) -> str | None:
    """"not about X" when the headline clearly is not about its ticker, else None."""
    try:
        from momx import etf_list, news_relevance

        sym = str(item.get("symbol") or item.get("ticker") or "").upper()
        about = news_relevance.about_ticker(sym, item.get("headline"), etf_list.security_name(sym),
                                            is_etf=etf_list.is_etf(sym))
        return "not about " + sym if about is False else None
    except Exception:  # noqa: BLE001
        return None


def item_key(item: dict) -> str:
    """One headline = one judgment: the URL when there is one, else symbol + headline."""
    base = str(item.get("url") or "").strip() or (str(item.get("symbol") or "") + "|" + str(item.get("headline") or ""))
    return str(item.get("symbol") or "").upper() + "|" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _published(item: dict) -> datetime | None:
    raw = str(item.get("publishedAt") or "").strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_answer(answer: dict) -> dict | None:
    """The API's Choice answer -> {"label", "p", "confidence", "probs"}; None if malformed."""
    if not isinstance(answer, dict) or answer.get("choice") not in LABELS:
        return None
    probs = {k: round(float(v), 3) for k, v in (answer.get("probabilities") or {}).items() if k in LABELS}
    label = answer["choice"]
    return {"label": label, "p": probs.get(label), "confidence": round(float(answer.get("confidence") or 0), 3),
            "probs": probs}


def _default_post(body: dict, key: str, timeout: float = 20.0) -> dict:
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class JevNewsJudge:
    def __init__(self, path: Path, *, key: str | None = None, post: Callable[[dict, str], dict] | None = None,
                 background: bool = True, clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.path = Path(path)
        self._key = key if key is not None else (os.getenv("TYPESAFE_API_KEY") or "").strip()
        self.enabled = bool(self._key) and os.getenv("AGX_JEV_NEWS", "1") != "0"
        self._post = post or _default_post
        self._background = background
        self._clock, self._sleep = clock, sleep
        self._lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        self._queued: set[str] = set()
        self._failed: set[str] = set()
        self._queue: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._dirty = 0
        self._day = ""
        self._calls_today = 0
        self.stats = {"judged": 0, "errors": 0, "lastError": None, "lastJudgedAt": None}
        self._load()

    # ---------------------------------------------------------------- store
    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._cache = data.get("items") or {} if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001 - a missing / bad file just starts empty
            self._cache = {}

    def _save(self) -> None:
        cutoff = datetime.fromtimestamp(self._clock(), timezone.utc) - timedelta(days=KEEP_DAYS)
        with self._lock:
            keep = {k: v for k, v in self._cache.items()
                    if str(v.get("judgedAt") or "") >= cutoff.isoformat()}
            self._cache = keep
            self._dirty = 0
            payload = {"model": getattr(self, "model_id", MODEL), "items": dict(keep)}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception:  # noqa: BLE001
            pass

    # -------------------------------------------------------------- public
    def _cache_key(self, item: dict) -> str:
        return item_key(item)

    def _can_queue(self) -> bool:
        return self.enabled

    def annotate(self, payload: Any) -> Any:
        """Attach item["jev"] to judged items; queue the recent unjudged ones. Never raises."""
        try:
            if not isinstance(payload, dict):
                return payload
            items = [x for x in (payload.get("feed") or []) if isinstance(x, dict)]
            items += [x for x in (payload.get("latest") or {}).values() if isinstance(x, dict)]
            now = datetime.fromtimestamp(self._clock(), timezone.utc)
            fresh = []
            with self._lock:
                for it in items:
                    k = self._cache_key(it)
                    got = self._cache.get(k)
                    if got and got.get("qv") != QUESTION_VERSION:
                        recent = _published(it)
                        if recent is not None and now - recent <= timedelta(hours=JUDGE_WINDOW_HOURS) and self.enabled:
                            got = None          # an older wording: ask again (below)
                    if got:
                        it["jev"] = shown_reading(got, it)
                        continue
                    why = off_topic(it)
                    if why:
                        # Not about this ticker: no Jev call, no ▲ / ▼ (2026-10-10).
                        it["jev"] = {"label": "not_important", "p": None, "why": why, "offTopic": True}
                        continue
                    pub = _published(it)
                    if (self._can_queue() and k not in self._queued and k not in self._failed and pub is not None
                            and now - pub <= timedelta(hours=JUDGE_WINDOW_HOURS)):
                        self._queued.add(k)
                        fresh.append((k, it))
            for k, it in fresh:
                self._queue.put((k, {"ticker": str(it.get("symbol") or "").upper(),
                                     "headline": str(it.get("headline") or ""),
                                     "summary": str(it.get("summary") or "")[:1200],
                                     "source": str(it.get("source") or ""),
                                     "publishedAt": str(it.get("publishedAt") or "")}))
            if fresh:
                self._start()
            return payload
        except Exception:  # noqa: BLE001 - the news list never breaks over Jev
            return payload

    def status(self) -> dict:
        with self._lock:
            return {"enabled": self.enabled, "cached": len(self._cache), "queued": self._queue.qsize(),
                    "callsToday": self._calls_today, **self.stats}

    # -------------------------------------------------------------- worker
    def _start(self) -> None:
        if not self._background:
            self.drain()
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="momx-jev-news", daemon=True)
                self._thread.start()

    def drain(self) -> None:
        while True:
            try:
                k, state = self._queue.get_nowait()
            except queue.Empty:
                break
            self._judge(k, state)
        if self._dirty:
            self._save()

    def _run(self) -> None:
        while True:
            try:
                k, state = self._queue.get(timeout=60)
            except queue.Empty:
                if self._dirty:
                    self._save()
                continue
            self._judge(k, state)
            if self._dirty >= SAVE_EVERY:
                self._save()

    def _judge(self, k: str, state: dict) -> None:
        day = datetime.fromtimestamp(self._clock(), timezone.utc).date().isoformat()
        if day != self._day:
            self._day, self._calls_today = day, 0
        if self._calls_today >= MAX_PER_DAY:
            return
        body = {"model": MODEL, "state": state, "questions": {"impact": QUESTION}}
        delay = 1.0
        for _attempt in range(5):
            try:
                self._calls_today += 1
                out = self._post(body, self._key)
                ans = parse_answer(((out or {}).get("answers") or {}).get("impact"))
                if ans is None:
                    raise ValueError("unexpected answer shape")
                ans.update(qv=QUESTION_VERSION,
                           judgedAt=datetime.fromtimestamp(self._clock(), timezone.utc).isoformat(),
                           model=str((out or {}).get("model") or MODEL), ticker=state.get("ticker"),
                           # kept so scripts/jev_news_eval.py can time the move after the headline
                           headline=str(state.get("headline") or "")[:200], publishedAt=state.get("publishedAt"),
                           source=state.get("source"))
                with self._lock:
                    self._cache[k] = ans
                    self._queued.discard(k)
                    self._dirty += 1
                    self.stats["judged"] += 1
                    self.stats["lastJudgedAt"] = ans["judgedAt"]
                return
            except urllib.error.HTTPError as exc:
                if exc.code in (429, 529):
                    self._sleep(delay)
                    delay = min(delay * 2, 30.0)
                    continue
                self._fail(k, f"HTTP {exc.code}")
                return
            except Exception as exc:  # noqa: BLE001
                self._fail(k, type(exc).__name__)
                return
        self._fail(k, "rate limited")

    def _fail(self, k: str, why: str) -> None:
        with self._lock:
            self._queued.discard(k)
            self._failed.add(k)
            self.stats["errors"] += 1
            self.stats["lastError"] = why        # never the key, never the body


__all__ = ["JevNewsJudge", "item_key", "parse_answer", "QUESTION", "LABELS"]
