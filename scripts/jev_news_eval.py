"""Does Jev's headline label mean anything for the price? (his ask 2026-10-02 "yes do it").

For every headline TypeSafe Jev labelled (news_jev.py cache) that came out on DAY between 09:30
and 15:00 ET, measure the stock from the open of the first 5m candle that starts at/after the
headline: +30 min, +60 min and to the 15:55 close, raw and MINUS SPY over the same window
("vs SPY", so a market-wide move is not credited to the news). Also the best and worst move
in the first 60 minutes. One story repeated by several sources counts once per ticker per 30
minutes (the first one). Stock moves only - no option prices.

    python scripts/jev_news_eval.py [YYYY-MM-DD]     (default: today, ET)

Writes artifacts/jev_news_eval/<day>.json, appends one line per day to
artifacts/jev_news_eval/history.jsonl, prints the day and the running totals.
Alpaca 5m bars only (schwab_volume=False - never Schwab). READ-ONLY toward the app.
NOT proof on its own: a few days of headlines is a small sample.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
ET = ZoneInfo("America/New_York")
CACHE = ROOT / "artifacts" / "momx_jev_news.json"
OUT = ROOT / "artifacts" / "jev_news_eval"
LABELS = ("bullish", "bearish", "not_important")
DEDUPE_MINUTES = 30


def _items(day: str) -> list[dict]:
    data = json.loads(CACHE.read_text(encoding="utf-8")).get("items") or {}
    have = {k: v for k, v in data.items() if v.get("label") in LABELS}
    # Older cache entries (before 2026-10-02 evening) lack publishedAt: fill from the live feed.
    missing_tickers = sorted({v.get("ticker") for v in have.values() if not v.get("publishedAt") and v.get("ticker")})
    if missing_tickers:
        try:
            from news_jev import item_key
            url = "http://127.0.0.1:3001/api/news-feed/latest?symbols=" + ",".join(missing_tickers)
            with urllib.request.urlopen(url, timeout=120) as resp:
                feed = json.loads(resp.read().decode("utf-8")).get("feed") or []
            for it in feed:
                k = item_key(it)
                if k in have and not have[k].get("publishedAt"):
                    have[k].update(publishedAt=it.get("publishedAt"), headline=it.get("headline"), source=it.get("source"))
        except Exception as exc:  # noqa: BLE001
            print("feed fill failed:", type(exc).__name__)
    out = []
    for v in have.values():
        raw = str(v.get("publishedAt") or "")
        try:
            t = datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
        except ValueError:
            continue
        if t.date().isoformat() != day or not (9 * 60 + 30 <= t.hour * 60 + t.minute <= 15 * 60):
            continue
        out.append({**v, "t": t})
    out.sort(key=lambda x: x["t"])
    kept, last = [], {}
    for it in out:                       # one story per ticker per 30 min
        prev = last.get(it["ticker"])
        if prev is not None and it["t"] - prev < timedelta(minutes=DEDUPE_MINUTES):
            continue
        last[it["ticker"]] = it["t"]
        kept.append(it)
    return kept


def _bars(symbols: list[str], day: str) -> dict:
    from momx import feed
    out = {}
    for i in range(0, len(symbols), 50):
        res = feed.fetch_5m(symbols[i:i + 50], days=3, schwab_volume=False, use_cache=False)
        for s, df in res.bars.items():
            d = df.reset_index()
            cols = {str(c).lower(): c for c in d.columns}
            tcol = cols.get("time") or cols.get("timestamp") or d.columns[0]
            rows = []
            for r in d.to_dict("records"):
                tv = r[tcol]
                tv = tv.timestamp() if hasattr(tv, "timestamp") else float(tv)
                if tv > 1e12:
                    tv /= 1000
                t = datetime.fromtimestamp(tv, ET)
                if t.date().isoformat() == day and 570 <= t.hour * 60 + t.minute < 960:
                    rows.append({"t": t, "o": float(r[cols["open"]]), "h": float(r[cols["high"]]),
                                 "l": float(r[cols["low"]]), "c": float(r[cols["close"]])})
            out[s] = sorted(rows, key=lambda b: b["t"])
    return out


def _move(bars: list[dict], start: datetime) -> dict | None:
    idx = next((i for i, b in enumerate(bars) if b["t"] >= start), None)
    if idx is None:
        return None
    entry = bars[idx]["o"]
    at = lambda minutes: next((b["c"] for b in bars[idx:] if b["t"] + timedelta(minutes=5) >= bars[idx]["t"] + timedelta(minutes=minutes)), None)
    close = next((b["c"] for b in reversed(bars) if b["t"].hour * 60 + b["t"].minute <= 955), None)
    window = [b for b in bars[idx:] if b["t"] < bars[idx]["t"] + timedelta(minutes=60)]
    pct = lambda x: None if x is None else (x / entry - 1) * 100
    return {"entryAt": bars[idx]["t"].strftime("%H:%M"), "entry": round(entry, 4), "r30": pct(at(30)), "r60": pct(at(60)),
            "rClose": pct(close), "up60": pct(max(b["h"] for b in window)) if window else None,
            "down60": pct(min(b["l"] for b in window)) if window else None}


def main(day: str) -> None:
    items = _items(day)
    if not items:
        print(f"{day}: no Jev-labelled headlines published 09:30-15:00 ET")
        return
    tickers = sorted({it["ticker"] for it in items} | {"SPY"})
    bars = _bars(tickers, day)
    rows = []
    for it in items:
        start = it["t"].replace(second=0, microsecond=0)
        start += timedelta(minutes=(5 - start.minute % 5) % 5)
        m = _move(bars.get(it["ticker"]) or [], start)
        spy = _move(bars.get("SPY") or [], start)
        if not m:
            continue
        for k in ("r30", "r60", "rClose"):
            m[k + "VsSpy"] = None if m[k] is None or not spy or spy[k] is None else m[k] - spy[k]
        rows.append({"ticker": it["ticker"], "label": it["label"], "p": it.get("p"), "at": it["t"].strftime("%H:%M"),
                     "headline": str(it.get("headline") or "")[:140], **m})
    OUT.mkdir(parents=True, exist_ok=True)
    summary = {"day": day, "groups": {}}
    for label in LABELS + ("all",):
        g = [r for r in rows if label == "all" or r["label"] == label]
        def avg(key):
            v = [r[key] for r in g if r.get(key) is not None]
            return round(st.mean(v), 3) if v else None
        summary["groups"][label] = {
            "n": len(g), "r30": avg("r30"), "r60": avg("r60"), "rClose": avg("rClose"),
            "r30VsSpy": avg("r30VsSpy"), "r60VsSpy": avg("r60VsSpy"), "rCloseVsSpy": avg("rCloseVsSpy"),
            "up60": avg("up60"), "down60": avg("down60"),
            "pctUp60": round(100 * sum(1 for r in g if (r.get("r60") or 0) > 0) / len(g), 1) if g else None,
        }
    (OUT / f"{day}.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1, default=str), encoding="utf-8")
    hist = OUT / "history.jsonl"
    lines = [ln for ln in (hist.read_text(encoding="utf-8").splitlines() if hist.exists() else []) if f'"day": "{day}"' not in ln]
    lines.append(json.dumps(summary))
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"JEV NEWS CHECK {day}  (stock % from the first 5m candle after the headline; 'vs SPY' = minus SPY same window)")
    for label in LABELS + ("all",):
        s = summary["groups"][label]
        print(f"  {label:13s} n={s['n']:4d}  +30m {s['r30']}%  +60m {s['r60']}%  close {s['rClose']}%  | vs SPY: +60m {s['r60VsSpy']}%  close {s['rCloseVsSpy']}%"
              f"  | up at 60m {s['pctUp60']}%  best/worst 60m {s['up60']}/{s['down60']}%")
    days = [json.loads(ln) for ln in hist.read_text(encoding="utf-8").splitlines() if ln.strip()]
    print(f"RUNNING ({len(days)} day(s)):")
    for label in LABELS:
        tot_n = sum(d["groups"][label]["n"] for d in days)
        def wavg(key):
            pts = [(d["groups"][label][key], d["groups"][label]["n"]) for d in days if d["groups"][label].get(key) is not None]
            w = sum(n for _, n in pts)
            return round(sum(v * n for v, n in pts) / w, 3) if w else None
        print(f"  {label:13s} n={tot_n:5d}  +60m vs SPY {wavg('r60VsSpy')}%  close vs SPY {wavg('rCloseVsSpy')}%  up at 60m {wavg('pctUp60')}%")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else datetime.now(ET).date().isoformat())
