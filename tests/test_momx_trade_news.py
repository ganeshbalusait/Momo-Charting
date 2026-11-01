"""Bot "News trade" book: each trade gets Jev's read of the ticker's news at entry."""

from __future__ import annotations

import pytest

import json
import sqlite3

from momx import trade_news
from news_jev import item_key


@pytest.fixture(autouse=True)
def _no_company_names(monkeypatch):
    """These made-up headlines name no company: the relevance gate (news_relevance,
    2026-10-10) is tested in test_news_relevance.py; here no name = not checked."""
    from momx import etf_list
    monkeypatch.setattr(etf_list, "security_name", lambda symbol: None)


def _setup(tmp_path, monkeypatch, headlines, readings):
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE catalyst_items (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, "
              "symbol TEXT, headline TEXT, url TEXT, published_at TEXT)")
    c.executemany("INSERT INTO catalyst_items (symbol, headline, url, published_at) VALUES (?,?,?,?)", headlines)
    c.commit(); c.close()
    jev = tmp_path / "jev.json"
    items = {}
    for (symbol, headline, url, _), (label, judged) in zip(headlines, readings):
        if label:
            items[item_key({"symbol": symbol, "headline": headline, "url": url})] = {"label": label, "p": 0.9, "judgedAt": judged}
    jev.write_text(json.dumps({"items": items}), encoding="utf-8")
    monkeypatch.setenv("AGX_MOMX_JEV_NEWS_PATH", str(jev))
    monkeypatch.setenv("AGX_MOMX_TRADE_NEWS_DIR", str(tmp_path / "store"))
    trade_news._JEV.update(mtime=None, items={}, oldest=None)
    return str(db)


def test_newest_read_headline_before_entry_sets_the_tone(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch, [
        ("NVDA", "Old upgrade", "https://a/1", "2026-10-06T13:00:00+00:00"),
        ("NVDA", "Fresh downgrade", "https://a/2", "2026-10-07T13:00:00+00:00"),
        ("NVDA", "After entry", "https://a/3", "2026-10-07T15:00:00+00:00"),
    ], [("bullish", "2026-10-06T14:00:00+00:00"), ("bearish", "2026-10-07T13:05:00+00:00"),
        ("bullish", "2026-10-07T15:05:00+00:00")])
    days = [{"day": "2026-10-07", "trades": [{"rule": "go", "symbol": "NVDA", "at": "2026-10-07T10:30:00-04:00"}]}]
    trade_news.annotate(days, db_path=db)
    assert days[0]["trades"][0]["newsJev"] == "bearish"          # newest before 14:30 UTC, not the later one
    assert days[0]["trades"][0]["newsHeadline"] == "Fresh downgrade"


def test_no_read_headline_in_24h_means_no_tone_and_matches_are_kept(tmp_path, monkeypatch):
    db = _setup(tmp_path, monkeypatch, [
        ("AMD", "Two days old", "https://b/1", "2026-10-05T12:00:00+00:00"),
        ("TSLA", "Beat", "https://b/2", "2026-10-07T12:00:00+00:00"),
    ], [("bullish", "2026-10-05T12:05:00+00:00"), ("bullish", "2026-10-07T12:05:00+00:00")])
    days = [{"day": "2026-10-07", "trades": [
        {"rule": "go", "symbol": "AMD", "at": "2026-10-07T10:30:00-04:00"},
        {"rule": "go", "symbol": "TSLA", "at": "2026-10-07T10:30:00-04:00"},
    ]}]
    trade_news.annotate(days, db_path=db)
    assert "newsJev" not in days[0]["trades"][0]
    assert days[0]["trades"][1]["newsJev"] == "bullish"
    # Jev's own file forgets; the saved match must not.
    (tmp_path / "jev.json").write_text(json.dumps({"items": {"X|0": {"label": "bullish", "p": 0.9, "judgedAt": "2026-10-07T00:00:00+00:00"}}}), encoding="utf-8")
    again = [{"day": "2026-10-07", "trades": [{"rule": "go", "symbol": "TSLA", "at": "2026-10-07T10:30:00-04:00"}]}]
    trade_news.annotate(again, db_path=db)
    assert again[0]["trades"][0]["newsJev"] == "bullish"


def test_a_broken_source_leaves_the_trades_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_JEV_NEWS_PATH", str(tmp_path / "missing.json"))
    trade_news._JEV.update(mtime=None, items={}, oldest=None)
    days = [{"day": "2026-10-07", "trades": [{"rule": "go", "symbol": "NVDA", "at": "2026-10-07T10:30:00-04:00"}]}]
    assert trade_news.annotate(days, db_path=str(tmp_path / "nope.db")) == days
    assert "newsJev" not in days[0]["trades"][0]


def test_news_rule_is_the_scanners_news_list(tmp_path, monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    db = _setup(tmp_path, monkeypatch, [
        ("MPC", "Refiner upgraded", "https://c/1", "2026-10-08T12:00:00+00:00"),
        ("ZM", "Zoom routine filing", "https://c/2", "2026-10-08T12:00:00+00:00"),
        ("NOPE", "Not a match", "https://c/3", "2026-10-08T12:00:00+00:00"),
        ("SOXL", "ETF up", "https://c/4", "2026-10-08T12:00:00+00:00"),
        ("NEWER", "Old good news", "https://c/5", "2026-10-08T11:00:00+00:00"),
        ("DOWN", "Downgrade", "https://c/6", "2026-10-08T12:00:00+00:00"),
    ], [("bullish", "2026-10-08T12:01:00+00:00"), ("not_important", "2026-10-08T12:01:00+00:00"),
        ("bullish", "2026-10-08T12:01:00+00:00"), ("bullish", "2026-10-08T12:01:00+00:00"),
        ("bullish", "2026-10-08T11:01:00+00:00"), ("bearish", "2026-10-08T12:01:00+00:00")])
    rows = [
        {"symbol": "MPC", "scanPass": True},
        {"symbol": "ZM", "scanPass": True},
        {"symbol": "NOPE", "scanPass": False},
        {"symbol": "SOXL", "scanPass": True, "etf": True},
        # the worker's own headline is newer -> it fills the News cell, no Jev read
        {"symbol": "NEWER", "scanPass": True, "news": {"headline": "Fresh wire", "publishedAt": "2026-10-08T12:30:00+00:00"}},
        {"symbol": "DOWN", "scanPass": True},
    ]
    now = datetime(2026, 10, 8, 9, 45, tzinfo=ZoneInfo("America/New_York"))
    assert trade_news.news_rule_symbols(rows, False, now, db_path=db) == {"MPC"}
    assert trade_news.news_rule_symbols(rows, True, now, db_path=db) == {"DOWN"}


def _old_newest(db, symbols, now):
    """The pre-2026-10-16 query: every row in the window, newest picked in Python."""
    import sqlite3
    from datetime import timezone
    con = sqlite3.connect(db)
    cutoff = (now - trade_news.STORE_LOOKBACK).astimezone(timezone.utc).isoformat()
    fresh = {}
    for symbol, headline, url, published, created, row_id in con.execute(
            "SELECT symbol, headline, url, published_at, created_at, id FROM catalyst_items "
            f"WHERE symbol IN ({','.join('?' for _ in symbols)}) AND COALESCE(published_at, created_at) >= ?",
            (*symbols, cutoff)):
        if not str(headline or "").strip():
            continue
        rank = (str(published or ""), str(created or ""), int(row_id or 0))
        if symbol not in fresh or rank > fresh[symbol][0]:
            fresh[symbol] = (rank, headline)
    con.close()
    return {s: v[1] for s, v in fresh.items()}


def test_newest_stored_picks_the_same_headline_as_the_full_scan(tmp_path):
    """2026-10-16: only each symbol's newest rows leave SQLite now (the GIL is
    re-taken per row); the pick must equal the old fetch-everything pick."""
    import random
    import sqlite3
    from datetime import datetime, timedelta, timezone
    db = str(tmp_path / "n.db")
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE catalyst_items (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, "
                "symbol TEXT NOT NULL, headline TEXT NOT NULL, url TEXT, published_at TEXT)")
    now = datetime(2026, 10, 16, 15, 0, tzinfo=timezone.utc)
    rnd = random.Random(7)
    symbols = [f"S{i}" for i in range(40)]
    for _ in range(3000):
        s = rnd.choice(symbols)
        created = (now - timedelta(minutes=rnd.randint(0, 12 * 24 * 60))).isoformat()
        published = None if rnd.random() < 0.2 else (now - timedelta(minutes=rnd.randint(0, 12 * 24 * 60))).isoformat()
        if rnd.random() < 0.05:
            published = created                      # ties fall to created_at, then id
        headline = rnd.choice(["  ", "", "\t"]) if rnd.random() < 0.08 else f"h{rnd.randint(0, 10**6)}"
        con.execute("INSERT INTO catalyst_items (created_at, symbol, headline, url, published_at) VALUES (?,?,?,?,?)",
                    (created, s, headline, "", published))
    # Newest rows blank: falls through to the next non-blank one.
    con.execute("INSERT INTO catalyst_items (created_at, symbol, headline, url, published_at) VALUES (?,?,?,?,?)",
                (now.isoformat(), "S0", " ", "", now.isoformat()))
    con.commit()
    con.close()
    got = trade_news._newest_stored(set(symbols), now, db_path=db)
    assert {s: v["headline"] for s, v in got.items()} == _old_newest(db, symbols, now)
