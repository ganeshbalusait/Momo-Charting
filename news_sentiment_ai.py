"""AI sentiment tags for scraped headlines.

The keyword scorer in ``catalyst_engine`` only recognises a short list of
words, so most headlines land on "Neutral" even when a trader would read
them as clearly good or bad for the stock. This module asks an LLM to label
each headline Positive, Negative or Neutral with a one-line reason.

It is information only: the label colours the News column and nothing else
reads it. When no key is configured, the request fails, or a headline is
missing from the reply, the keyword label stays in place - the news feed
never waits on, or breaks because of, the AI.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import json
import os
import re
import threading
import time
from typing import Any, Callable
from urllib import error, request

LABELS = ("Positive", "Negative", "Neutral")
HttpPoster = Callable[[str, dict[str, str], bytes, float], str]

SYSTEM_PROMPT = (
    "You label stock-market headlines for an equities trader. For each headline decide "
    "whether the news is likely good for that company's stock price (Positive), likely bad "
    "for it (Negative), or unclear / routine / not price-moving (Neutral). Judge the impact "
    "on the named ticker, not on the market in general: a downgrade, lawsuit, recall, miss, "
    "guidance cut, dilution or investigation is Negative; a beat, raise, upgrade, buyback, "
    "big contract or approval is Positive; a conference appearance, routine filing or "
    "generic market recap is Neutral. Return only JSON of the form "
    '{"items":[{"id":1,"sentiment":"Positive","reason":"<max 12 words>"}]} '
    "with exactly one entry per input id."
)


@dataclass(slots=True)
class AiSentiment:
    label: str
    reason: str
    model: str


def _urllib_http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> str:
    req = request.Request(url, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from exc


def normalize_headline_key(headline: str) -> str:
    return re.sub(r"\s+", " ", str(headline or "").strip().lower())


class NewsSentimentClassifier:
    def __init__(
        self,
        enabled: bool = True,
        model: str = "gpt-4o-mini",
        timeout_seconds: float = 12.0,
        batch_size: int = 20,
        api_key: str | None = None,
        base_url: str | None = None,
        http_post: HttpPoster | None = None,
        cache_size: int = 5000,
    ) -> None:
        self.enabled = bool(enabled)
        self.model = str(model or "gpt-4o-mini")
        self.timeout_seconds = max(float(timeout_seconds), 1.0)
        self.batch_size = max(1, min(int(batch_size), 50))
        self._api_key = api_key
        self._base_url = base_url
        self.http_post: HttpPoster = http_post or _urllib_http_post
        self._cache: OrderedDict[str, AiSentiment] = OrderedDict()
        self._cache_size = max(0, int(cache_size))
        self._lock = threading.Lock()
        self.last_error = ""
        self.last_run_at: float | None = None
        self.labeled_total = 0
        self.request_total = 0

    # ---------------------------------------------------------------- config
    @property
    def api_key(self) -> str:
        return str(self._api_key if self._api_key is not None else os.getenv("OPENAI_API_KEY", "") or "").strip()

    @property
    def base_url(self) -> str:
        return str(self._base_url or os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")

    @property
    def available(self) -> bool:
        return self.enabled and bool(self.api_key)

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "model": self.model if self.available else "",
            "reason": "" if self.available else ("disabled" if not self.enabled else "OPENAI_API_KEY not set"),
            "labeled": self.labeled_total,
            "requests": self.request_total,
            "cached": len(self._cache),
            "lastError": self.last_error,
            "lastRunAt": self.last_run_at,
        }

    # -------------------------------------------------------------- classify
    def classify(self, headlines: list[dict[str, Any]]) -> dict[str, AiSentiment]:
        """Label headlines. Each input is ``{"key", "headline", "summary"?}``.

        Returns ``{key: AiSentiment}`` for every headline that got a label.
        Anything the AI did not answer for is simply absent, so the caller
        keeps its keyword label; an API failure is recorded in ``last_error``.
        """
        if not self.available or not headlines:
            return {}
        results: dict[str, AiSentiment] = {}
        pending: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        with self._lock:
            for entry in headlines:
                key = str(entry.get("key") or "")
                headline = str(entry.get("headline") or "").strip()
                if not key or not headline or key in seen_keys:
                    continue
                seen_keys.add(key)
                cached = self._cache.get(normalize_headline_key(headline))
                if cached is not None:
                    self._cache.move_to_end(normalize_headline_key(headline))
                    results[key] = cached
                else:
                    pending.append({"key": key, "headline": headline[:300], "summary": str(entry.get("summary") or "")[:300]})
        if not pending:
            return results

        self.last_error = ""
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start:start + self.batch_size]
            try:
                labels = self._request_batch(batch)
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"[:240]
                break
            with self._lock:
                for entry, verdict in zip(batch, labels):
                    if verdict is None:
                        continue
                    results[entry["key"]] = verdict
                    self._remember(entry["headline"], verdict)
                    self.labeled_total += 1
        self.last_run_at = time.time()
        return results

    def _remember(self, headline: str, verdict: AiSentiment) -> None:
        if self._cache_size <= 0:
            return
        self._cache[normalize_headline_key(headline)] = verdict
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

    def _request_batch(self, batch: list[dict[str, Any]]) -> list[AiSentiment | None]:
        items = [
            {"id": index + 1, "headline": entry["headline"], **({"summary": entry["summary"]} if entry.get("summary") else {})}
            for index, entry in enumerate(batch)
        ]
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps({"items": items}, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        self.request_total += 1
        raw = self.http_post(f"{self.base_url}/chat/completions", headers, json.dumps(payload).encode("utf-8"), self.timeout_seconds)
        parsed = json.loads(raw)
        content = parsed.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        reply = json.loads(content) if isinstance(content, str) else content
        by_id: dict[int, dict[str, Any]] = {}
        for entry in (reply.get("items") if isinstance(reply, dict) else reply) or []:
            if not isinstance(entry, dict):
                continue
            try:
                by_id[int(entry.get("id"))] = entry
            except (TypeError, ValueError):
                continue
        verdicts: list[AiSentiment | None] = []
        for index in range(len(batch)):
            entry = by_id.get(index + 1)
            label = self._normalize_label(entry.get("sentiment") if entry else None)
            if label is None:
                verdicts.append(None)
                continue
            reason = re.sub(r"\s+", " ", str(entry.get("reason") or "")).strip()[:160]
            verdicts.append(AiSentiment(label=label, reason=reason, model=self.model))
        return verdicts

    @staticmethod
    def _normalize_label(value: Any) -> str | None:
        text = str(value or "").strip().lower()
        if text in {"positive", "bullish", "good", "strong"}:
            return "Positive"
        if text in {"negative", "bearish", "bad"}:
            return "Negative"
        if text in {"neutral", "mixed", "unclear", "none"}:
            return "Neutral"
        return None
