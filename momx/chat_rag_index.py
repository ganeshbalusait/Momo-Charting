"""Builds the Ask chat's local search index (2026-11-11): SQLite FTS5, no outside calls.

Run by momx/chat_rag.py as a short-lived child process, below normal priority, at most every 10 minutes:

    python -I chat_rag_index.py --out <folder> --help-json <file> --release-notes <file> [--news-db <file>]

It writes two separate files, so a regular user's search has no way to reach owner-only text:

    search_public.db   help, page and column meanings, release notes and news a regular user may read
    search_owner.db    all of that plus the owner-only text

Prices and other market numbers never go in: they are taken out of news text here, because a number
in the chat must come from a tool that shows its time. Users' questions are never stored here.

Run as a plain script with -I, so it imports nothing but the standard library. It only reads its sources.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

PUBLIC_FILE = "search_public.db"
OWNER_FILE = "search_owner.db"
NEWS_DAYS = 14
NEWS_LIMIT = 6000
NEWS_TEXT_CHARS = 400
RELEASES_MAX = 400

#: App-written text naming any of these is owner-only. News comes from outside the app and says nothing about his accounts.
#: "A trade" and "my trades" count; the verb does not ("how much traded at each level", "the stock trades at $3" explain the market).
OWNER_ONLY = re.compile(r"\b(?:bots?|paper|scorecard|p\s?[&/]\s?l|pnl|profits?|hermes|openclaw|ai\s+picks?"
                        r"|(?:a|the|this|that|each|every|per|my|your|our|his|these|those|losing|winning|open|closed|best|worst|real|\d+)\s+trades?"
                        r"|trades?\s+(?:on|record|list|review|log|results?|history)|(?:order|mid|real|limit)\s+fills?|fill\s+rate|filled\s+(?:at|trades?|orders?))\b", re.I)
#: User-facing text never names a person.
PERSON = re.compile(r"\bganesh\b", re.I)
#: Market numbers in news: dollars, percents, decimals, and amounts like 1.2B. A year or "5 things" stays.
MARKET_NUMBER = re.compile(r"[$€£]\s?\d[\d,]*(?:\.\d+)?\s?(?:[kmbt]\b|million|billion|trillion)?|[+-]?\d[\d,]*(?:\.\d+)?\s?%|\b\d+\.\d+\b|\b\d[\d,]*\s?(?:million|billion|trillion)\b", re.I)
HIDDEN_NUMBER = "(figure in the article)"
WORD = re.compile(r"[a-z0-9]+")


def mask_numbers(text: str) -> str:
    return MARKET_NUMBER.sub(HIDDEN_NUMBER, text)


def is_public(doc: Mapping) -> bool:
    """May a regular user read this? News always; app-written text only when marked so and naming nothing of the owner's."""
    if doc.get("scope") != "all":
        return False
    return doc.get("source") == "news" or not OWNER_ONLY.search(f"{doc.get('title', '')} {doc.get('text', '')}")


def _doc(source: str, title: Any, text: Any, date: Any, link: Mapping, scope: str) -> dict:
    return {"source": source, "title": " ".join(str(title or "").split()), "text": " ".join(str(text or "").split()), "date": str(date or "")[:10], "link": dict(link), "scope": scope}


def help_docs(path: Path) -> list[dict]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = raw.get("docs") if isinstance(raw, Mapping) else None
    docs = []
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping) and isinstance(item.get("link"), Mapping):
            docs.append(_doc(str(item.get("source") or "help"), item.get("title"), item.get("text"), item.get("date"), item["link"], "all" if item.get("scope") == "all" else "owner"))
    return docs


def release_docs(path: Path) -> list[dict]:
    """One document per line of a release note, so one line about the owner's pages does not hide the rest."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    releases = raw.get("releases") if isinstance(raw, Mapping) else None
    docs = []
    link = {"label": "Open Release Notes", "view": "Release Notes"}
    for release in (releases if isinstance(releases, list) else [])[:RELEASES_MAX]:
        if not isinstance(release, Mapping):
            continue
        heading = release.get("heading")
        owner_heading = bool(OWNER_ONLY.search(str(heading or "")))
        items = release.get("items")
        for item in items if isinstance(items, list) else []:
            if isinstance(item, str):
                docs.append(_doc("release", heading, mask_dollars(item), release.get("at"), link, "owner" if owner_heading else "all"))
    return docs


def mask_dollars(text: str) -> str:
    """A release note can quote a price from the day it was written; it is not a price to answer with."""
    return re.sub(r"\$\s?\d[\d,]*(?:\.\d+)?", HIDDEN_NUMBER, text)


def news_docs(db_path: Path | None, now: datetime, days: int = NEWS_DAYS, limit: int = NEWS_LIMIT) -> tuple[list[dict], str | None]:
    """Stored headlines, newest first, read-only. The app's own store: nothing here reaches the internet."""
    if db_path is None or not Path(db_path).exists():
        return [], None
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=days)).isoformat()
    try:
        conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        return [], type(exc).__name__
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(catalyst_items)")}
        summary = "summary" if "summary" in columns else "''"
        rows = conn.execute(f"SELECT symbol, headline, {summary}, published_at FROM catalyst_items WHERE published_at >= ? ORDER BY published_at DESC LIMIT ?", (cutoff, limit)).fetchall()
    except sqlite3.Error as exc:
        return [], type(exc).__name__
    finally:
        conn.close()
    docs = []
    for symbol, headline, body, published in rows:
        ticker = str(symbol or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,9}", ticker) or not headline:
            continue
        link = {"label": f"Open {ticker} news", "view": "News Feed", "ticker": ticker}
        docs.append(_doc("news", f"{ticker}: {mask_numbers(str(headline))}", mask_numbers(str(body or ""))[:NEWS_TEXT_CHARS], published, link, "all"))
    return docs, None


_SCHEMA = """
    CREATE TABLE docs(id INTEGER PRIMARY KEY, source TEXT, title TEXT, text TEXT, date TEXT, link TEXT);
    CREATE VIRTUAL TABLE words USING fts5(title, text, content='docs', content_rowid='id', tokenize='porter unicode61 remove_diacritics 2');
    CREATE TABLE vocab(term TEXT PRIMARY KEY, df INTEGER) WITHOUT ROWID;
    CREATE VIRTUAL TABLE grams USING fts5(term, tokenize='trigram');
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def _connect(temp: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(temp)
    conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;")
    return conn


def _fill(conn: sqlite3.Connection, docs: Iterable[Mapping], first_id: int) -> int:
    """Adds documents and their words. Returns the last id used."""
    vocab: Counter = Counter()
    last = first_id
    for doc in docs:
        last += 1
        conn.execute("INSERT INTO docs VALUES (?,?,?,?,?,?)", (last, doc["source"], doc["title"], doc["text"], doc["date"], json.dumps(doc["link"], sort_keys=True)))
        conn.execute("INSERT INTO words(rowid, title, text) VALUES (?,?,?)", (last, doc["title"], doc["text"]))
        vocab.update(set(WORD.findall(f"{doc['title']} {doc['text']}".lower())))
    known = {row[0] for row in conn.execute("SELECT term FROM vocab")}
    conn.executemany("INSERT INTO vocab VALUES (?,?) ON CONFLICT(term) DO UPDATE SET df = df + excluded.df", vocab.items())
    conn.executemany("INSERT INTO grams(term) VALUES (?)", [(term,) for term in vocab if term not in known and len(term) >= 3 and not term.isdigit()])
    conn.commit()
    return last


def _write(out: Path, public: list[Mapping], owner_only: list[Mapping], built_at: str) -> tuple[int, int]:
    """Both index files, under temporary names, moved into place only when complete. The public file is
    written first and from public documents alone; the owner's is a copy of it with the rest added."""
    public_temp = out / f"{PUBLIC_FILE}.{os.getpid()}.tmp"
    owner_temp = out / f"{OWNER_FILE}.{os.getpid()}.tmp"
    public_temp.unlink(missing_ok=True)
    conn = _connect(public_temp)
    try:
        conn.executescript(_SCHEMA)
        last = _fill(conn, public, 0)
        conn.execute("INSERT INTO meta VALUES ('builtAt', ?)", (built_at,))
        conn.commit()
    finally:
        conn.close()
    shutil.copyfile(public_temp, owner_temp)
    conn = _connect(owner_temp)
    try:
        total = _fill(conn, owner_only, last)
    finally:
        conn.close()
    _move(owner_temp, out / OWNER_FILE)
    _move(public_temp, out / PUBLIC_FILE)
    return last, total


def _move(temp: Path, path: Path) -> None:
    """A search has the file open for a moment, and Windows refuses a replace during it: try for about 2 s."""
    for attempt in range(40):
        try:
            os.replace(temp, path)
            return
        except PermissionError:
            if attempt == 39:
                temp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _clean_old_temps(folder: Path) -> None:
    cutoff = time.time() - 3600
    for leftover in folder.glob("search_*.tmp"):
        try:
            if leftover.stat().st_mtime < cutoff:
                leftover.unlink()
        except OSError:
            pass


def build(out: Path, help_json: Path, release_notes: Path, news_db: Path | None, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    _clean_old_temps(out)
    news, news_error = news_docs(news_db, now)
    every = [doc for doc in help_docs(help_json) + release_docs(release_notes) + news
             if doc["title"] and (doc["source"] == "news" or not PERSON.search(f"{doc['title']} {doc['text']}"))]
    public, owner = _write(out, [doc for doc in every if is_public(doc)], [doc for doc in every if not is_public(doc)], now.isoformat())
    return {"public": public, "owner": owner, "news": len(news), "newsError": news_error}


def _priority_class() -> int | None:
    """What Windows really says this process runs at (None elsewhere). The handle types must be declared:
    left as ctypes' 32-bit default the pseudo-handle is cut short and the call fails without raising."""
    if sys.platform != "win32":
        return None
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel32.GetPriorityClass.restype = ctypes.c_uint
    return int(kernel32.GetPriorityClass(kernel32.GetCurrentProcess()))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--help-json", required=True)
    parser.add_argument("--release-notes", required=True)
    parser.add_argument("--news-db", default=None)
    args = parser.parse_args(argv)
    result = build(Path(args.out), Path(args.help_json), Path(args.release_notes), Path(args.news_db) if args.news_db else None)
    print(json.dumps({**result, "priority": _priority_class()}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
