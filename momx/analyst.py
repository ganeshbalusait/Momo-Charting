"""Analyst ratings and price targets for one ticker (2026-11-11, the trader's ask with a broker app's "Analyst Rating" gauge).

Sources (each confirmed with one real call on his keys, 12:40 ET 2026-11-11):
* Finnhub /api/v1/stock/recommendation: monthly strongBuy/buy/hold/sell/strongSell counts.
* FMP /stable/price-target-consensus (high, low, consensus, median), /stable/price-target-summary (how many targets
  last month/quarter/year and their averages, publishers) and /stable/grades-consensus (counts and a consensus word).
  Finnhub's own price-target endpoint is not on his plan (403), so it is not used.

At most one fetch per ticker per day, cached on disk under the app's cache folder; a failed fetch keeps the
newest earlier copy and says its date. 8 s timeout per request. The two sources count analysts differently, so
their numbers are shown side by side and never merged into one invented figure.

Labelled everywhere: "Analyst views from Finnhub and FMP, not the app's rules and not advice."
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LABEL = "Analyst views from Finnhub and FMP, not the app's rules and not advice."
TIMEOUT_SECONDS = 8.0
#: Tickers fetched per day in all, to keep inside the free plans (FMP: 250 requests a day, 3 per ticker here).
MAX_FETCHES_PER_DAY = 60
MAX_COPIES_KEPT = 7
_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")
FINNHUB_URL = "https://finnhub.io/api/v1/stock/recommendation?{query}"
FMP_URLS = {"consensus": "https://financialmodelingprep.com/stable/price-target-consensus?{query}",
            "summary": "https://financialmodelingprep.com/stable/price-target-summary?{query}",
            "grades": "https://financialmodelingprep.com/stable/grades-consensus?{query}"}

_lock = threading.Lock()
_fetching: dict[str, threading.Event] = {}
_count = {"day": "", "n": 0}
#: symbol -> the day a fetch was last tried, so a failed one is not tried again the same day.
_tried: dict[str, str] = {}


def cache_dir() -> Path:
    override = os.environ.get("AGX_ANALYST_CACHE_DIR", "").strip()
    if override:
        return Path(override)
    grade = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    base = Path(grade) if grade else Path(__file__).resolve().parents[1] / "artifacts"
    return base / "analyst_cache"


def _key(name: str) -> str:
    from momx import chat_facts
    return chat_facts.env_key(name)


def _get_json(url: str, timeout: float = TIMEOUT_SECONDS) -> Any:
    """One GET. Tests replace this; under pytest the real one is refused."""
    if os.environ.get("PYTEST_CURRENT_TEST"):
        raise ConnectionRefusedError("no outside calls in tests")
    request = urllib.request.Request(url, headers={"User-Agent": "agx-analyst/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:      # noqa: S310 - fixed https hosts
        return json.loads(response.read(2_000_000).decode("utf-8"))


def _today() -> str:
    return datetime.now(ET).date().isoformat()


def _path(symbol: str, day: str) -> Path:
    return cache_dir() / symbol / f"{day}.json"


def newest_copy(symbol: str) -> dict | None:
    folder = cache_dir() / symbol
    try:
        names = sorted((p for p in folder.glob("*.json")), reverse=True)
    except OSError:
        return None
    for path in names:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            return doc
    return None


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _first(value: Any) -> dict:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return value if isinstance(value, dict) else {}


def fetch(symbol: str, get: Callable[[str], Any] | None = None) -> dict:
    """Ask both services now. Never raises; a part that fails is recorded as missing with its reason."""
    get = get or _get_json
    finnhub, fmp = _key("FINNHUB_API_KEY"), _key("FMP_API_KEY")
    jobs: dict[str, str] = {}
    if finnhub:
        jobs["recommendation"] = FINNHUB_URL.format(query=urllib.parse.urlencode({"symbol": symbol, "token": finnhub}))
    if fmp:
        for name, url in FMP_URLS.items():
            jobs[name] = url.format(query=urllib.parse.urlencode({"symbol": symbol, "apikey": fmp}))
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    if jobs:
        with ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="analyst") as pool:
            futures = {name: pool.submit(get, url) for name, url in jobs.items()}
            for name, future in futures.items():
                try:
                    results[name] = future.result(timeout=TIMEOUT_SECONDS + 1)
                except Exception as exc:  # noqa: BLE001 - one part missing, the rest still shown
                    errors[name] = type(exc).__name__          # never the URL: it holds the key
    if not finnhub:
        errors["recommendation"] = "no Finnhub key"
    if not fmp:
        errors.update({name: "no FMP key" for name in FMP_URLS})
    months = []
    for row in results.get("recommendation") if isinstance(results.get("recommendation"), list) else []:
        if isinstance(row, dict) and row.get("period"):
            counts = {k: int(_num(row.get(k)) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")}
            months.append({"period": str(row["period"])[:10], **counts})
    months.sort(key=lambda m: m["period"], reverse=True)
    consensus, summary, grades = _first(results.get("consensus")), _first(results.get("summary")), _first(results.get("grades"))
    publishers = summary.get("publishers")
    if isinstance(publishers, str):
        try:
            publishers = json.loads(publishers)
        except ValueError:
            publishers = [p.strip() for p in publishers.split(",")]
    return {
        "symbol": symbol, "fetchedAt": datetime.now(ET).isoformat(timespec="seconds"), "day": _today(),
        "finnhub": {"months": months[:6]} if months else None,
        "targets": {k: _num(consensus.get(k)) for k in ("targetHigh", "targetLow", "targetConsensus", "targetMedian")} if consensus else None,
        "summary": {k: _num(summary.get(k)) for k in ("lastMonthCount", "lastMonthAvgPriceTarget", "lastQuarterCount", "lastQuarterAvgPriceTarget",
                                                       "lastYearCount", "lastYearAvgPriceTarget")} | {"publishers": [str(p)[:40] for p in (publishers or [])][:8]}
        if summary else None,
        "grades": ({k: int(_num(grades.get(k)) or 0) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")} | {"consensus": str(grades.get("consensus") or "")[:20]})
        if grades else None,
        "errors": errors,
    }


def _save(doc: dict) -> None:
    path = _path(doc["symbol"], doc["day"])
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, path)
        for old in sorted(path.parent.glob("*.json"), reverse=True)[MAX_COPIES_KEPT:]:
            old.unlink(missing_ok=True)
    except OSError:
        pass


def _useful(doc: Mapping | None) -> bool:
    return bool(doc and (doc.get("finnhub") or doc.get("targets") or doc.get("grades")))


def get(symbol: Any, wait: float = TIMEOUT_SECONDS + 2, fetcher: Callable[[str], dict] | None = None) -> tuple[dict | None, str]:
    """(views, status). status: "today" (fetched today), "earlier" (a failed or skipped fetch kept an earlier copy),
    "busy" (a fetch is running and did not finish within `wait`), "missing", or "bad ticker"."""
    symbol = str(symbol or "").strip().upper()
    if not _TICKER.fullmatch(symbol):
        return None, "bad ticker"
    today = _today()
    held = newest_copy(symbol)
    if held and held.get("day") == today:
        return held, "today"
    with _lock:
        event = _fetching.get(symbol)
        if event is None and _tried.get(symbol) == today:
            return (held, "earlier") if _useful(held) else (None, "missing")
        start = event is None
        if start:
            if _count["day"] != today:
                _count.update(day=today, n=0)
            if _count["n"] >= MAX_FETCHES_PER_DAY:
                return (held, "earlier") if held else (None, "missing")
            _count["n"] += 1
            _tried[symbol] = today
            event = _fetching[symbol] = threading.Event()
    if start:
        def run() -> None:
            try:
                doc = (fetcher or fetch)(symbol)
                if _useful(doc):
                    _save(doc)
                elif held is None:
                    _save({**doc, "empty": True})          # one fetch a day even when the services know nothing
            finally:
                with _lock:
                    _fetching.pop(symbol, None)
                event.set()
        threading.Thread(target=run, name=f"analyst-{symbol}", daemon=True).start()
    if not event.wait(max(wait, 0.0)):
        return (held, "earlier") if held else (None, "busy")
    fresh = newest_copy(symbol)
    if fresh and fresh.get("day") == today and _useful(fresh):
        return fresh, "today"
    if held and _useful(held):
        return held, "earlier"
    return None, "missing"


def reset() -> None:
    with _lock:
        _fetching.clear()
        _tried.clear()
        _count.update(day="", n=0)


# ---------------------------------------------------------------- saying it
_WORDS = {"strongBuy": "Strong buy", "buy": "Buy", "hold": "Hold", "sell": "Sell", "strongSell": "Strong sell"}


def _mix(counts: Mapping) -> tuple[str, int]:
    total = sum(int(counts.get(k) or 0) for k in _WORDS)
    if not total:
        return "no ratings", 0
    return ", ".join(f"{word} {int(counts.get(k) or 0)} ({int(counts.get(k) or 0) / total * 100:.0f}%)" for k, word in _WORDS.items()), total


def lines_for(doc: Mapping | None, status: str, price: float | None = None, price_text: str | None = None) -> list[str]:
    """Plain lines, each saying its source and date. Nothing when there is nothing to say."""
    if not doc or not _useful(doc):
        return []
    out = [LABEL]
    if status == "earlier":
        out.append(f"These are from {doc.get('day')}; today's fetch has not answered.")
    months = (doc.get("finnhub") or {}).get("months") or []
    if months:
        text, total = _mix(months[0])
        buy_side = months[0]["strongBuy"] + months[0]["buy"]
        out.append(f"Finnhub ratings, {months[0]['period'][:7]} ({total} analysts): {text}.")
        trend = "; ".join(f"{m['period'][:7]}: {m['strongBuy'] + m['buy']} buy, {m['hold']} hold, {m['sell'] + m['strongSell']} sell" for m in months[:4])
        out.append(f"Month by month (Finnhub): {trend}.")
        if total:
            out.append(f"{buy_side} of {total} Finnhub analysts rate it buy or strong buy.")
    grades = doc.get("grades")
    if grades:
        text, total = _mix(grades)
        out.append(f"FMP grades ({total} grades): {text}; FMP's consensus word: {grades.get('consensus') or 'none'}.")
    targets = doc.get("targets") or {}
    if any(targets.get(k) for k in ("targetConsensus", "targetMedian", "targetHigh", "targetLow")):
        def vs(value: float | None) -> str:
            if not value:
                return "?"
            return f"{value:,.2f}" + (f" ({(value - price) / price * 100:+.1f}% from {price:,.2f})" if price else "")
        out.append(f"FMP price targets: average {vs(targets.get('targetConsensus'))}, median {vs(targets.get('targetMedian'))}, "
                   f"high {vs(targets.get('targetHigh'))}, low {vs(targets.get('targetLow'))}." + (f" Price {price_text}." if price and price_text else ""))
    summary = doc.get("summary") or {}
    if summary.get("lastYearCount") is not None:
        out.append(f"Targets published (FMP): {int(summary.get('lastMonthCount') or 0)} last month, {int(summary.get('lastQuarterCount') or 0)} last quarter, "
                   f"{int(summary.get('lastYearCount') or 0)} last year" + (f"; sources include {', '.join(summary.get('publishers') or [])}." if summary.get("publishers") else "."))
    if months and grades:
        out.append("Finnhub and FMP count analysts differently, so their totals do not match; they are shown side by side.")
    out.append(f"Fetched {str(doc.get('fetchedAt') or doc.get('day'))[:16].replace('T', ' ')} ET.")
    return out


def payload(symbol: Any, wait: float = TIMEOUT_SECONDS + 2, artifacts: Any = None, now: datetime | None = None) -> dict:
    """What GET /api/long-term/analyst answers. With `artifacts` (the app's folder) it also carries "technical": the
    Technical analysis card from the chat report's own verdict and mini-chart code (momx/chat_report.technical), worked
    out on a side thread while the analyst views are read, from the chart child's finished answers only. None when
    the app holds no chart numbers for the ticker."""
    clean = str(symbol or "").strip().upper()
    box: dict = {}
    worker = None
    if artifacts is not None and _TICKER.fullmatch(clean):
        def run() -> None:
            try:
                from momx import chat_report
                ctx = {"scope": "scanner", "now": (now or datetime.now(ET)).astimezone(ET), "artifacts": Path(artifacts)}
                box["technical"] = chat_report.technical(clean, ctx)
            except Exception:  # noqa: BLE001 - the card is left out, the analyst views still answer
                box["technical"] = None
        worker = threading.Thread(target=run, name=f"lt-analyst-technical-{clean}", daemon=True)
        worker.start()
    doc, status = get(symbol, wait)
    out = {"symbol": clean, "status": status, "label": LABEL, "views": doc, "lines": lines_for(doc, status)}
    if artifacts is not None:
        if worker is not None:
            from momx import chat_report
            worker.join(chat_report.TECHNICAL_WAIT_SECONDS + 1.0)
        out["technical"] = box.get("technical")
    return out
