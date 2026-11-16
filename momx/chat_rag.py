"""Local search for the Ask chat (2026-11-11). "Use LLM or rag search engine": no outside calls, no paid embeddings.

search() ranks with SQLite FTS5 (BM25) and forgives typos by correcting a word against the index's own
vocabulary. The index is built by a child process (momx/chat_rag_index.py), below normal priority and at most
every 10 minutes, and only when somebody searches.

A regular user's search opens only search_public.db, a file that never held owner-only text; a result is
checked again on the way out. Search explains things: it never answers a number, since prices and levels
come only from tools that show their time. Questions are never stored here (past questions live on the device).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping

from momx import chat_rag_index as index

ROOT = Path(__file__).resolve().parents[1]
INDEX_SCRIPT = Path(index.__file__)
HELP_JSON = Path(__file__).with_name("chat_help_index.json")
RELEASE_NOTES = ROOT / "frontend" / "public" / "release-notes.json"
NEWS_DB = ROOT / "database" / "trades.db"
INDEX_DIR_ENV = "CHAT_SEARCH_INDEX_DIR"
NEWS_DB_ENV = "CHAT_SEARCH_NEWS_DB"
REBUILD_SECONDS = 600
#: Below normal priority on a busy PC it can take a minute or more, and nobody waits on it past the first build.
CHILD_TIMEOUT_SECONDS = 300
#: With no index at all, the first search gives the build this long.
FIRST_BUILD_WAIT_SECONDS = 3.0
CREATE_NO_WINDOW, BELOW_NORMAL_PRIORITY_CLASS = 0x08000000, 0x00004000
MAX_QUERY_CHARS = 200
MAX_TERMS = 8
MAX_LIMIT = 10
SNIPPET_CHARS = 280

STOP_WORDS = set("""a an and are as at be been but by can could did do does for from get give had has have how i if in into is it its me my of on or our please should
    show so tell than that the their them then there these they this to was we were what when where which who why will with would you your""".split())
#: Explaining the app beats a passing mention in a headline.
SOURCE_WEIGHT = {"glossary": 1.8, "column": 1.6, "page": 1.5, "learn": 1.3, "setup": 1.3, "rule": 1.2, "release": 0.6, "news": 0.5}
SOURCE_LABEL = {"glossary": "Glossary", "column": "Scanner column", "page": "Page", "learn": "Learn", "setup": "Setup", "rule": "Scanner rule", "release": "Release note", "news": "News"}
LEARN_LINK = {"label": "Open Learn + Setup", "view": "Learn + Setup"}
#: "price of NVDA", "rvol for spy": a question for a number, which search does not answer.
_NUMBER_WORD = r"(?:price|worth|value|quote|vwap|pivots?|rvol|open\s+interest|oi|iv|delta|expected\s+move|max\s+pain|walls?|levels?|ema|sma)"
_GENERIC_AFTER = {"a", "an", "the", "this", "that", "my", "me", "it", "options", "option", "calls", "puts", "stocks", "stock", "charts", "chart", "beginners", "scanner", "app", "page"}
_ASKS_NUMBER = (re.compile(rf"\b{_NUMBER_WORD}\s+(?:of|for|on|at)\s+(?:the\s+)?\$?([a-z]{{1,5}})\b", re.I), re.compile(rf"\$?\b([a-z]{{1,5}})\s+{_NUMBER_WORD}\b", re.I),
                re.compile(r"\bhow\s+much\s+is\s+\$?([a-z]{1,5})\b", re.I))
_EXPLAINING = re.compile(r"^\s*(?:what|how)\s+(?:is|are|does|do)\s+(?:an?\s+|the\s+)?" + _NUMBER_WORD + r"\s*(?:mean|work|\??)?\s*\??\s*$", re.I)

_LOCK = threading.Lock()
_BUILD = {"running": False, "startedAt": None, "done": None}


def is_test_run() -> bool:
    return "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules


def index_dir(override: Path | str | None = None) -> Path:
    """The app's cache folder; an argument or the environment moves it, and a test run can never reach the live one."""
    chosen = override or os.environ.get(INDEX_DIR_ENV, "").strip()
    if chosen:
        return Path(chosen)
    if is_test_run():
        return Path(tempfile.gettempdir()) / f"agx-chat-search-test-{os.getpid()}"
    return ROOT / "artifacts" / "chat_search_cache"


def news_db() -> Path | None:
    override = os.environ.get(NEWS_DB_ENV, "").strip()
    if override:
        return Path(override)
    return None if is_test_run() else NEWS_DB


def _child_flags() -> int:
    return CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0


def child_command(folder: Path) -> list[str]:
    command = [sys.executable, "-I", str(INDEX_SCRIPT), "--out", str(folder), "--help-json", str(HELP_JSON), "--release-notes", str(RELEASE_NOTES)]
    db = news_db()
    return command + (["--news-db", str(db)] if db is not None else [])


def _run_child(folder: Path, done: threading.Event) -> None:
    try:
        child = subprocess.Popen(child_command(folder), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=_child_flags())
        try:
            child.communicate(timeout=CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
    except OSError:
        pass
    finally:
        with _LOCK:
            _BUILD["running"] = False
        done.set()


def _file_for(scope: str, folder: Path) -> Path:
    """Fails closed: only the exact word "owner" opens the owner's file."""
    return folder / (index.OWNER_FILE if scope == "owner" else index.PUBLIC_FILE)


def refresh(folder: Path | None = None) -> threading.Event | None:
    """Start a rebuild if one is due, without waiting. Returns the event of the build running, or None."""
    folder = index_dir(folder)
    public = folder / index.PUBLIC_FILE
    with _LOCK:
        if _BUILD["running"]:
            return _BUILD["done"]
        try:
            age = time.time() - public.stat().st_mtime
        except OSError:
            age = float("inf")
        since = float("inf") if _BUILD["startedAt"] is None else time.monotonic() - _BUILD["startedAt"]
        if age < REBUILD_SECONDS or since < REBUILD_SECONDS:
            return None
        done = threading.Event()
        _BUILD.update(running=True, startedAt=time.monotonic(), done=done)
    threading.Thread(target=_run_child, args=(folder, done), name="chat-search-build", daemon=True).start()
    return done


def _open(path: Path) -> sqlite3.Connection:
    """Read-only and immutable: a search takes no lock, and can write nothing."""
    return sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro&immutable=1", uri=True, timeout=1.0)


def terms_of(query: str) -> list[str]:
    words = index.WORD.findall(str(query or "")[:MAX_QUERY_CHARS].lower())
    kept = list(dict.fromkeys(word for word in words if word not in STOP_WORDS))
    return kept[:MAX_TERMS]


def _distance(a: str, b: str, most: int) -> int:
    """Edit distance counting a swap of neighbours as one, or most + 1 when it is further than that."""
    if abs(len(a) - len(b)) > most:
        return most + 1
    before, row = None, list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        now = [i]
        for j, cb in enumerate(b, 1):
            cost = min(row[j] + 1, now[j - 1] + 1, row[j - 1] + (ca != cb))
            if before is not None and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb:
                cost = min(cost, before[j - 2] + 1)
            now.append(cost)
        before, row = row, now
    return row[-1] if row[-1] <= most else most + 1


def _known(conn: sqlite3.Connection, term: str) -> bool:
    return conn.execute("SELECT 1 FROM words WHERE words MATCH ? LIMIT 1", (f'"{term}"',)).fetchone() is not None


def _corrected(conn: sqlite3.Connection, term: str) -> str | None:
    """The word as the index knows it; the nearest word it knows for a typo; None when nothing is near."""
    if _known(conn, term):
        return term
    if len(term) < 4 or term.isdigit():
        return None
    most = 1 if len(term) <= 5 else 2
    candidates = conn.execute("SELECT term, df FROM vocab WHERE term >= ? AND term < ? AND length(term) BETWEEN ? AND ?",
                              (term[0], chr(ord(term[0]) + 1), len(term) - most, len(term) + most)).fetchall()
    grams = " OR ".join(f'"{term[i:i + 3]}"' for i in range(len(term) - 2))
    candidates += conn.execute("SELECT g.term, v.df FROM (SELECT term FROM grams WHERE grams MATCH ? ORDER BY rank LIMIT 200) g JOIN vocab v ON v.term = g.term", (grams,)).fetchall()
    best = None
    for word, df in candidates:
        distance = _distance(term, word, most)
        if distance <= most and (best is None or (distance, -df, word) < best):
            best = (distance, -df, word)
    return best[2] if best else None


def _rows(conn: sqlite3.Connection, match: str) -> list[tuple]:
    return conn.execute("SELECT d.id, d.source, d.title, d.text, d.date, d.link, bm25(words, 8.0, 1.0) AS r FROM words JOIN docs d ON d.id = words.rowid WHERE words MATCH ? ORDER BY r LIMIT 60", (match,)).fetchall()


def _snippet(text: str) -> str:
    if len(text) <= SNIPPET_CHARS:
        return text
    cut = text[:SNIPPET_CHARS]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return cut[:stop + 1] if stop >= SNIPPET_CHARS // 3 else cut.rsplit(" ", 1)[0] + "..."


def _result(row: tuple) -> dict:
    return {"source": row[1], "title": row[2], "text": _snippet(row[3]), "date": row[4] or None, "link": json.loads(row[5])}


def _allowed(row: tuple, owner: bool) -> bool:
    """The second lock, on the way out: app-written text naming the owner's things never reaches a regular user."""
    return owner or row[1] == "news" or not index.OWNER_ONLY.search(f"{row[2]} {row[3]}")


def search(query: str, scope: str, limit: int = 5, folder: Path | str | None = None) -> list[dict]:
    """The best matches, each {source, title, text, date, link}. Never raises; an empty list when nothing matches."""
    limit = max(1, min(int(limit) if isinstance(limit, int) else 5, MAX_LIMIT))
    base = index_dir(folder)
    path = _file_for(scope, base)
    build = refresh(base)
    if not path.exists() and build is not None:
        build.wait(FIRST_BUILD_WAIT_SECONDS)
    owner = scope == "owner"
    # A word of the owner's is not corrected into something else for a regular user ("paper" is not "Piper").
    terms = [term for term in terms_of(query) if owner or not index.OWNER_ONLY.fullmatch(term)]
    if not terms or not path.exists():
        return []
    try:
        conn = _open(path)
        try:
            words = [word for word in (_corrected(conn, term) for term in terms) if word]
            if not words:
                return []
            quoted = [f'"{word}"' for word in words]
            found = {row[0]: row for row in _rows(conn, " AND ".join(quoted))}
            whole = set(found)
            if len(found) < limit and len(quoted) > 1:
                found.update({row[0]: row for row in _rows(conn, " OR ".join(quoted)) if row[0] not in found})
        finally:
            conn.close()
    except sqlite3.Error:
        return []

    def score(row: tuple) -> float:
        title_words = set(index.WORD.findall(row[2].lower()))
        bonus = 1.5 if row[1] not in ("news", "release") and set(words) <= title_words else 1.0
        return -row[6] * SOURCE_WEIGHT.get(row[1], 1.0) * bonus * (2.0 if row[0] in whole else 1.0)

    ranked = sorted((row for row in found.values() if _allowed(row, owner)), key=lambda row: (-score(row), row[0]))
    return [_result(row) for row in ranked[:limit]]


def asks_for_a_number(query: str) -> bool:
    """Is this a question for a market number rather than for what something means?"""
    text = str(query or "")
    if _EXPLAINING.search(text):
        return False
    return any(m.group(1).lower() not in _GENERIC_AFTER and m.group(1).lower() not in STOP_WORDS for pattern in _ASKS_NUMBER for m in pattern.finditer(text))


def _line(result: Mapping) -> str:
    label = SOURCE_LABEL.get(result["source"], "Help")
    date = f", {result['date']}" if result.get("date") else ""
    return f"{label}{date}: {result['title']}. {result['text']}".rstrip(" .") + "."


def _links(results: list[dict]) -> list[dict]:
    seen, out = set(), []
    for result in results:
        key = json.dumps(result["link"], sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(result["link"])
    return out[:6]


def _reply(lines: list[str], facts: Mapping | None = None, links: list | None = None) -> dict:
    return {"lines": lines, "facts": dict(facts or {}), "links": links or [], "table": None, "asOf": None}


def help_search(args: Mapping, ctx: Mapping) -> dict:
    """What something means and where it is. Never raises, and never answers a market number."""
    args = args if isinstance(args, Mapping) else {}
    ctx = ctx if isinstance(ctx, Mapping) else {}
    scope = ctx.get("scope")
    if scope not in ("owner", "scanner"):
        return _reply(["That is not available in this chat."])
    query = str(args.get("query") or "").strip()
    if not query:
        return _reply(["What would you like to look up?"], links=[LEARN_LINK])
    if asks_for_a_number(query):
        return _reply(["Search does not answer numbers. Prices, levels and volume come only from the app's live data, with their time. Ask for it directly, for example \"NVDA price\" or \"chart levels NVDA\"."],
                      {"numbers": False}, [LEARN_LINK])
    try:
        results = search(query, scope, _limit(args.get("limit")), ctx.get("searchDir"))
    except Exception:  # noqa: BLE001 - a search must never fail the chat
        results = []
    if not results:
        return _reply(["I could not find that in the app's help. Learn + Setup explains every page."], {"results": []}, [LEARN_LINK])
    return _reply([_line(result) for result in results], {"results": [{"source": r["source"], "title": r["title"], "date": r["date"]} for r in results], "numbers": False}, _links(results))


def _limit(value) -> int:
    return value if type(value) is int and 1 <= value <= MAX_LIMIT else 3


TOOLS = {
    "help_search": {"scope": "all", "description": "Search the app's own help: what a word, column, scanner rule or page means, what changed in a release, and stored news headlines. Never use it for a price, level or other number.",
                    "args": {"type": "object", "properties": {"query": {"type": "string", "description": "What to look up"}, "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT}}, "required": ["query"], "additionalProperties": False},
                    "handler": help_search},
}
