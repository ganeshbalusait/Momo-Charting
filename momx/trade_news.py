"""News at entry for every BOT trade (2026-10-07, his ask: "add one more panel next
to Jev BOT - News trade", bull and bear).

For each virtual trade the Bot page gets ``newsJev`` = Jev's reading (bullish /
bearish / not_important) of the NEWEST stored headline about that ticker that was
published in the NEWS_WINDOW before the bot entered and that Jev has read. The
"News trade" book is then the BULL trades whose entry news read bullish and the
BEAR trades whose entry news read bearish.

Sources, both read-only and local (nothing here touches the network):
* headlines - the ticker-tagged news store, ``catalyst_items`` in the trading DB
  (news_feed_store.py), opened read-only (WAL: never waits on a writer);
* Jev's readings - ``artifacts/momx_jev_news.json`` (news_jev.py), keyed by
  news_jev.item_key; that file keeps only KEEP_DAYS (7) days.

Because Jev's file forgets after 7 days, every match found is written to one
small file per trading day (``artifacts/momx_trade_news/<day>.json``, kept as
long as the bot keeps its trades), so a past day's News-trade numbers do not
change once Jev's own file has dropped the reading. Trades with
no judged headline get no ``newsJev`` (they are simply not in the book).

Never raises into the Bot page: any failure returns the trades untouched.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from config import ARTIFACTS_DIR, DATABASE_PATH

NEWS_WINDOW = timedelta(hours=24)
LABELS = ("bullish", "bearish", "not_important")

_LOCK = threading.Lock()
_JEV: dict[str, Any] = {"mtime": None, "items": {}, "oldest": None, "readAt": 0.0}
#: Jev's file (~5 MB) is rewritten every ~20 judgments; re-parsing it on every
#: change cost 0.32 s inside the scanner build (2026-10-09, py-spy from the
#: TOS-parity session: news_rule_symbols held a Watchlist build 10+ s). A
#: reading at most this old is fine for a News-cell tone.
JEV_RELOAD_SECONDS = 60.0
#: Newest stored headline per symbol, reused this long (same reason).
NEWEST_CACHE_SECONDS = 60.0
_NEWEST: dict[str, tuple[float, dict | None]] = {}


def _shown(reading, symbol, headline):
    """Jev's read as the News column shows it: off-topic / under-70% -> Not
    important (news_jev.shown_reading, 2026-10-10), so the bot's News rule
    trades exactly the ▲ / ▼ the trader sees."""
    if not isinstance(reading, dict):
        return None
    from news_jev import shown_reading

    return shown_reading(reading, {"symbol": symbol, "headline": headline})


def jev_path() -> Path:
    return Path(os.environ.get("AGX_MOMX_JEV_NEWS_PATH") or (Path(ARTIFACTS_DIR) / "momx_jev_news.json"))


def store_dir() -> Path:
    return Path(os.environ.get("AGX_MOMX_TRADE_NEWS_DIR") or (Path(ARTIFACTS_DIR) / "momx_trade_news"))


def _when(raw: Any) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(raw or "").strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _jev_items() -> dict:
    """Jev's readings, re-read only when the file changed."""
    path = jev_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    with _LOCK:
        if _JEV["mtime"] != mtime and (_JEV["mtime"] is None or time.monotonic() - _JEV["readAt"] >= JEV_RELOAD_SECONDS):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                items = data.get("items") if isinstance(data, dict) else None
            except (OSError, ValueError, UnicodeDecodeError):
                items = None
            items = items if isinstance(items, dict) else {}
            stamps = [_when(v.get("judgedAt")) for v in items.values() if isinstance(v, dict)]
            stamps = [s for s in stamps if s is not None]
            _JEV.update(mtime=mtime, items=items, oldest=min(stamps) if stamps else None, readAt=time.monotonic())
        return _JEV["items"]


def _oldest_reading() -> datetime | None:
    _jev_items()
    return _JEV["oldest"]


def _headlines(symbols: set[str], since: datetime, until: datetime, db_path: str | None = None) -> dict[str, list[dict]]:
    """Stored headlines per symbol published in [since, until], newest first."""
    out: dict[str, list[dict]] = {}
    if not symbols:
        return out
    uri = Path(db_path or DATABASE_PATH).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    try:
        wanted = sorted(symbols)
        for start in range(0, len(wanted), 200):
            chunk = wanted[start:start + 200]
            rows = connection.execute(
                "SELECT symbol, headline, url, published_at FROM catalyst_items "
                f"WHERE symbol IN ({','.join('?' for _ in chunk)}) AND published_at >= ?",
                (*chunk, (since - timedelta(days=1)).isoformat()),
            ).fetchall()
            for symbol, headline, url, published in rows:
                when = _when(published)
                if when is None or when < since or when > until:
                    continue
                out.setdefault(str(symbol).upper(), []).append(
                    {"symbol": str(symbol).upper(), "headline": headline or "", "url": url or "", "at": when})
    finally:
        connection.close()
    for items in out.values():
        items.sort(key=lambda item: item["at"], reverse=True)
    return out


def _trade_key(trade: dict) -> str:
    return "|".join((str(trade.get("rule") or ""), str(trade.get("symbol") or ""), str(trade.get("at") or "")))


def _day_ok(day: str) -> bool:
    return len(day) == 10 and day[4] == "-" and day[7] == "-" and day.replace("-", "").isdigit()


def _load_day(day: str) -> dict:
    try:
        data = json.loads((store_dir() / f"{day}.json").read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, UnicodeDecodeError):
        return {}


def _save_day(day: str, saved: dict) -> None:
    path = store_dir() / f"{day}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(saved), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _prune(keep_days: int) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days + 2)).date().isoformat()
    try:
        for f in store_dir().glob("*.json"):
            if _day_ok(f.stem) and f.stem < cutoff:
                f.unlink(missing_ok=True)
    except OSError:
        pass


def annotate(days: list[dict], db_path: str | None = None, keep_days: int = 180) -> list[dict]:
    """Stamp ``newsJev`` (and ``newsHeadline``) on the trades of option_track.history()."""
    try:
        from news_jev import item_key

        saved_by_day: dict[str, dict] = {}
        pending: list[tuple[str, str, dict, datetime]] = []
        for day in days or []:
            name = str(day.get("day") or "")
            if not _day_ok(name):
                continue
            saved = saved_by_day.setdefault(name, _load_day(name))
            for trade in day.get("trades") or []:
                key = _trade_key(trade)
                hit = saved.get(key)
                if isinstance(hit, dict) and hit.get("label") in LABELS:
                    trade["newsJev"] = hit["label"]
                    trade["newsHeadline"] = hit.get("headline") or ""
                    continue
                at = _when(trade.get("at"))
                if at is not None and trade.get("symbol"):
                    pending.append((name, key, trade, at))
        if not pending:
            return days
        jev = _jev_items()
        if not jev:
            return days
        # Jev reads headlines up to 48 h old, and its file keeps 7 days: a trade
        # from before (oldest reading - 2 days) cannot match - skip the DB query.
        oldest = _oldest_reading()
        if oldest is not None:
            pending = [p for p in pending if p[3] >= oldest - timedelta(days=2)]
        if not pending:
            return days
        since = min(at for *_, at in pending) - NEWS_WINDOW
        until = max(at for *_, at in pending)
        by_symbol = _headlines({str(t["symbol"]).upper() for _, _, t, _ in pending}, since, until, db_path)
        changed: set[str] = set()
        for name, key, trade, at in pending:
            for item in by_symbol.get(str(trade["symbol"]).upper(), []):
                if item["at"] > at or at - item["at"] > NEWS_WINDOW:
                    continue
                reading = _shown(jev.get(item_key(item)), trade["symbol"], item["headline"])
                if isinstance(reading, dict) and reading.get("label") in LABELS:
                    trade["newsJev"] = reading["label"]
                    trade["newsHeadline"] = item["headline"]
                    saved_by_day[name][key] = {"label": reading["label"], "headline": item["headline"]}
                    changed.add(name)
                    break
        for name in changed:
            _save_day(name, saved_by_day[name])
        if changed:
            _prune(keep_days)
        return days
    except Exception:  # noqa: BLE001 - the Bot page never breaks over news
        return days


# --------------------------------------------------------------------------
# The bot's "news" rule (his ask 2026-10-08, "make sure bot also take the same"):
# the same tickers the scanner lists under NEWS ▲ / NEWS ▼ with "Scan matches
# only" ticked - a scan match whose News cell carries Jev's bullish (bear board:
# bearish) read. The News cell's headline is chosen exactly like the page does
# (momxNewsFeed.mergeStoredNews): the store's newest headline (7-day lookback)
# unless the worker's own headline is newer; only a store headline carries Jev.

STORE_LOOKBACK = timedelta(days=7)


def _newest_stored(symbols: set[str], now: datetime, db_path: str | None = None) -> dict[str, dict]:
    """Newest stored headline per symbol (7-day lookback), cached NEWEST_CACHE_SECONDS.

    Exact ``symbol IN`` (the store writes symbols upper-case) so SQLite uses the
    (symbol, url) index; the newest is picked here - the old UPPER()+ORDER BY
    over the whole window cost 0.28 s per call inside every scanner build."""
    out: dict[str, dict] = {}
    if not symbols:
        return out
    clock = time.monotonic()
    wanted = []
    for symbol in sorted(symbols):
        hit = _NEWEST.get(symbol) if db_path is None else None
        if hit is not None and clock - hit[0] < NEWEST_CACHE_SECONDS:
            if hit[1] is not None:
                out[symbol] = hit[1]
        else:
            wanted.append(symbol)
    if not wanted:
        return out
    uri = Path(db_path or DATABASE_PATH).resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=30.0)
    fresh: dict[str, tuple] = {}
    try:
        cutoff = (now - STORE_LOOKBACK).astimezone(timezone.utc).isoformat()
        for start in range(0, len(wanted), 200):
            chunk = wanted[start:start + 200]
            # Only each symbol's 3 newest rows leave SQLite (2026-10-16): the
            # sqlite3 module re-takes the GIL for EVERY row it steps, and the
            # momx worker's main process is GIL-bound, so fetching ~4,000 rows
            # took tens of seconds inside every scanner build (py-spy) while the
            # same query ran in 0.03 s on its own. The newest is still picked
            # below by the same rank; 3 rows, not 1, so a blank-headline newest
            # row still falls through to the next one exactly as before.
            rows = connection.execute(
                "SELECT symbol, headline, url, published_at, created_at, id FROM ("
                "SELECT symbol, headline, url, published_at, created_at, id, ROW_NUMBER() OVER ("
                "PARTITION BY symbol ORDER BY COALESCE(published_at, '') DESC, "
                "COALESCE(created_at, '') DESC, id DESC) AS rn FROM catalyst_items "
                f"WHERE symbol IN ({','.join('?' for _ in chunk)}) "
                "AND COALESCE(published_at, created_at) >= ?) WHERE rn <= 3",
                (*chunk, cutoff),
            ).fetchall()
            for symbol, headline, url, published, created, row_id in rows:
                if not str(headline or "").strip():
                    continue
                key = str(symbol).upper()
                rank = (str(published or ""), str(created or ""), int(row_id or 0))
                if key not in fresh or rank > fresh[key][0]:
                    fresh[key] = (rank, {"symbol": key, "headline": headline, "url": url or "",
                                         "at": _when(published)})
    finally:
        connection.close()
    for symbol in wanted:
        item = fresh[symbol][1] if symbol in fresh else None
        if db_path is None:
            _NEWEST[symbol] = (clock, item)
        if item is not None:
            out[symbol] = item
    if len(_NEWEST) > 2000:
        for key in sorted(_NEWEST, key=lambda k: _NEWEST[k][0])[:1000]:
            _NEWEST.pop(key, None)
    return out


def news_rule_symbols(rows: list, bear: bool, now: datetime, db_path: str | None = None) -> set[str]:
    """Scan-match symbols whose News cell reads Jev-bullish (bear: bearish). Never raises."""
    try:
        from news_jev import item_key

        want = "bearish" if bear else "bullish"
        matches = {str(r.get("symbol")).upper(): r for r in rows
                   if isinstance(r, dict) and r.get("scanPass") and r.get("symbol")
                   and (not r.get("etf") or str(r.get("symbol")).upper() in ("SPY", "QQQ"))}
        if not matches:
            return set()
        jev = _jev_items()
        if not jev:
            return set()
        stored = _newest_stored(set(matches), now, db_path)
        hits: set[str] = set()
        for symbol, row in matches.items():
            item = stored.get(symbol)
            if not item:
                continue
            own = row.get("news") if isinstance(row.get("news"), dict) else None
            if own and str(own.get("headline") or "").strip():
                own_at = _when(own.get("publishedAt") or own.get("at"))
                if own_at is not None and (item["at"] is None or own_at > item["at"]):
                    continue        # the worker's newer headline wins the cell - it has no Jev read
            reading = _shown(jev.get(item_key(item)), symbol, item.get("headline"))
            if isinstance(reading, dict) and reading.get("label") == want:
                hits.add(symbol)
        return hits
    except Exception:  # noqa: BLE001 - a news read never stops the bot
        return set()
