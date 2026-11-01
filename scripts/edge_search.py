"""Which scanner conditions predicted the next hour's direction? (his ask 2026-10-02 "I want to catch
those trades" -> "yes" to a no-guesswork search.)

CALLS only: the bull Watchlist History (artifacts/momx_history/Watchlist, Sep 2 - Oct 2). The bear
History starts Sep 25 - too short to split.

* Every History snapshot (arrival + each signal change) 09:35-15:00 ET is a candidate entry,
  at most one per symbol per 30 minutes.
* Entry = open of the first 5m Alpaca bar starting at/after the snapshot minute. Outcomes:
  net % after 60 minutes (close of the bar ending 60 min later) and to the 15:55 close.
  STOCK moves only - the bot's old option prices were unreliable (stale chains).
* Features: only fields present ALL month (RVOL, Skittles, squeeze, range position, % change,
  news, scan reasons, price) plus facts computed from the bars every day (VWAP, gap, vs open,
  opening-range high, SPY above its VWAP / SPY % today). m5 / grade fields began ~Sep 22 and
  are left out so both periods see the same facts.
* SEARCH on Sep 2-19, CHECK on Sep 22 - Oct 2 (never seen by the search). Single conditions
  and every pair. A condition is reported as SURVIVING only if it is positive (net 60m and
  more than half up) in BOTH periods with n >= MIN_N_SEARCH / MIN_N_CHECK. Everything else
  is listed as failing - with ~2,500 conditions tried, some look good in one half by luck.

    python scripts/edge_search.py
Writes artifacts/edge_search/{samples.json, report.json, report.txt}.
"""
from __future__ import annotations

import itertools
import json
import os
import statistics as st
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
ET = ZoneInfo("America/New_York")
HIST = ROOT / "artifacts" / "momx_history" / "Watchlist"
OUT = ROOT / "artifacts" / "edge_search"
SEARCH_END = "2026-09-19"
CHECK_START = "2026-09-22"
MIN_N_SEARCH, MIN_N_CHECK = 150, 80
DEDUPE_MIN = 30
LIT = ("cyan", "green", "lime", "dark_green", "yellow", "orange", "magenta", "red")


def num(v):
    try:
        f = float(v)
        return f if f == f else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ bars
def load_bars(symbols: list[str]) -> dict:
    cache = OUT / "bars.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
        if set(symbols) <= set(raw):
            return {s: [(datetime.fromisoformat(b[0]), *b[1:]) for b in v] for s, v in raw.items()}
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    from momx import feed
    out = {}
    for i in range(0, len(symbols), 40):
        res = feed.fetch_5m(symbols[i:i + 40], days=40, schwab_volume=False, use_cache=False)
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
                if 570 <= t.hour * 60 + t.minute < 960:
                    rows.append((t, float(r[cols["open"]]), float(r[cols["high"]]), float(r[cols["low"]]),
                                 float(r[cols["close"]]), float(r[cols["volume"]])))
            out[s] = rows
        print(f"  bars {min(i + 40, len(symbols))}/{len(symbols)}", flush=True)
    OUT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({s: [[b[0].isoformat(), *b[1:]] for b in v] for s, v in out.items()}), encoding="utf-8")
    return out


def by_day(bars: list) -> dict:
    d = defaultdict(list)
    for b in bars:
        d[b[0].date().isoformat()].append(b)
    return d


def bar_facts(day_bars: list, idx: int, prev_close: float | None) -> dict:
    """Facts known at the START of bar idx (only bars before it)."""
    past = day_bars[:idx]
    if not past:
        return {}
    pv = sum((b[2] + b[3] + b[4]) / 3 * b[5] for b in past)
    vol = sum(b[5] for b in past)
    vwap = pv / vol if vol else None
    last = past[-1][4]
    open_ = day_bars[0][1]
    or_high = max(b[2] for b in day_bars[:min(3, len(day_bars))]) if idx >= 3 else None   # 09:30-09:45 range
    hi = max(b[2] for b in past)
    return {"vwap_d": (last / vwap - 1) * 100 if vwap else None, "vs_open": (last / open_ - 1) * 100,
            "gap": (open_ / prev_close - 1) * 100 if prev_close else None,
            "above_or": (last > or_high) if or_high else None, "off_high": (hi / last - 1) * 100,
            "last5_up": past[-1][4] > past[-1][1],
            "vol_vs_avg": (past[-1][5] / (vol / len(past))) if vol else None}


# ------------------------------------------------------------- samples
def build_samples() -> list[dict]:
    days = sorted(p.stem for p in HIST.glob("2026-*.json"))
    snaps = []
    syms = set()
    for day in days:
        if datetime.fromisoformat(day).weekday() >= 5:
            continue
        rows = json.loads((HIST / f"{day}.json").read_text(encoding="utf-8"))["rows"]
        for sym, v in rows.items():
            last_t = None
            for s in v.get("snapshots") or []:
                t = datetime.fromisoformat(s["at"]).astimezone(ET)
                m = t.hour * 60 + t.minute
                if not (575 <= m <= 900):
                    continue
                if last_t and (t - last_t).total_seconds() < DEDUPE_MIN * 60:
                    continue
                last_t = t
                snaps.append((day, sym, t, s["row"]))
                syms.add(sym)
    print(f"{len(snaps)} snapshot entries over {len(days)} days, {len(syms)} symbols", flush=True)
    bars = load_bars(sorted(syms | {"SPY"}))
    bd = {s: by_day(b) for s, b in bars.items()}
    out = []
    for day, sym, t, row in snaps:
        dbars = bd.get(sym, {}).get(day)
        if not dbars:
            continue
        start = t.replace(second=0, microsecond=0) + timedelta(minutes=(5 - t.minute % 5) % 5)
        idx = next((i for i, b in enumerate(dbars) if b[0] >= start), None)
        if idx is None or idx == 0:
            continue
        entry = dbars[idx][1]
        end60 = [b for b in dbars[idx:] if b[0] + timedelta(minutes=5) <= dbars[idx][0] + timedelta(minutes=60)]
        if not end60:
            continue
        r60 = (end60[-1][4] / entry - 1) * 100
        close = next((b[4] for b in reversed(dbars) if b[0].hour * 60 + b[0].minute <= 955), dbars[-1][4])
        rclose = (close / entry - 1) * 100
        prev_days = sorted(d for d in bd[sym] if d < day)
        prev_close = bd[sym][prev_days[-1]][-1][4] if prev_days else None
        f = bar_facts(dbars, idx, prev_close)
        spyb = bd.get("SPY", {}).get(day) or []
        sidx = next((i for i, b in enumerate(spyb) if b[0] >= start), None)
        sp = bar_facts(spyb, sidx, None) if sidx else {}
        out.append({"day": day, "sym": sym, "min": t.hour * 60 + t.minute, "r60": r60, "rclose": rclose,
                    "row": row, "f": f, "spy": sp})
    return out


# ------------------------------------------------------------ conditions
def conditions() -> dict:
    def rv(tf, lo):
        return lambda s: (num(((s["row"].get("rvol") or {}).get(tf) or {}).get("value")) or -9) >= lo

    def rvlit(tf):
        return lambda s: ((s["row"].get("rvol") or {}).get(tf) or {}).get("bg") not in (None, "black")

    def sk(tf):
        return lambda s: ((s["row"].get("skittles") or {}).get(tf) or {}).get("bg") in ("cyan", "green", "lime") or \
            (((s["row"].get("skittles") or {}).get(tf) or {}).get("bg") in (None, "black") and
             ((s["row"].get("skittles") or {}).get(tf) or {}).get("fg") in ("cyan", "green"))

    def skcount(k):
        return lambda s: sum(sk(tf)(s) for tf in ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")) >= k

    def sqz(tf):
        return lambda s: ((s["row"].get("sqz") or {}).get(tf) or {}).get("bg") in ("cyan", "green")

    def f(key, lo=None, hi=None):
        def g(s):
            v = s["f"].get(key)
            if v is None:
                return False
            return (lo is None or v >= lo) and (hi is None or v < hi)
        return g

    def pct(lo=None, hi=None):
        def g(s):
            v = num(s["row"].get("pctChange"))
            return v is not None and (lo is None or v >= lo) and (hi is None or v < hi)
        return g

    def hl(lo, hi):
        def g(s):
            v = num((s["row"].get("highLow") or {}).get("value"))
            return v is not None and lo <= v < hi
        return g

    def price(lo, hi):
        def g(s):
            v = num(s["row"].get("last"))
            return v is not None and lo <= v < hi
        return g

    c = {
        "time 09:35-10:00": lambda s: s["min"] < 600, "time 10:00-11:00": lambda s: 600 <= s["min"] < 660,
        "time 11:00-13:00": lambda s: 660 <= s["min"] < 780, "time 13:00-15:00": lambda s: s["min"] >= 780,
        "scanPass": lambda s: bool(s["row"].get("scanPass")),
        "news in row": lambda s: bool((s["row"].get("news") or {}).get("headline")),
        "pct <0": pct(None, 0), "pct 0-2": pct(0, 2), "pct 2-5": pct(2, 5), "pct 5+": pct(5),
        "range pos <0.3": hl(-1, 0.3), "range pos 0.3-0.7": hl(0.3, 0.7), "range pos >=0.7": hl(0.7, 2),
        "price <10": price(0, 10), "price 10-50": price(10, 50), "price 50-200": price(50, 200), "price 200+": price(200, 1e9),
        "above VWAP": f("vwap_d", 0), "below VWAP": f("vwap_d", None, 0), "VWAP +0-0.5%": f("vwap_d", 0, 0.5),
        "VWAP +0.5-1.5%": f("vwap_d", 0.5, 1.5), "VWAP +1.5%+": f("vwap_d", 1.5),
        "above open": f("vs_open", 0), "below open": f("vs_open", None, 0),
        "gap up 1%+": f("gap", 1), "gap down 1%+": f("gap", None, -1), "no gap": f("gap", -1, 1),
        "above 09:30-45 high": lambda s: s["f"].get("above_or") is True,
        "within 0.5% of day high": f("off_high", None, 0.5), "1.5%+ off day high": f("off_high", 1.5),
        "last 5m candle green": lambda s: s["f"].get("last5_up") is True,
        "last 5m candle red": lambda s: s["f"].get("last5_up") is False,
        "last 5m vol 2x avg": f("vol_vs_avg", 2),
        "SPY above VWAP": lambda s: (s["spy"].get("vwap_d") or -1) >= 0,
        "SPY below VWAP": lambda s: s["spy"].get("vwap_d") is not None and s["spy"]["vwap_d"] < 0,
        "SPY up today": lambda s: (s["spy"].get("vs_open") or -1) > 0,
        "SPY down today": lambda s: s["spy"].get("vs_open") is not None and s["spy"]["vs_open"] < 0,
    }
    for tf in ("5m", "15m", "30m", "1h", "2h", "4h", "D"):
        c[f"RVOL {tf} lit"] = rvlit(tf)
        c[f"RVOL {tf} >=3"] = rv(tf, 3)
    for tf in ("2h", "4h", "D", "Wk"):
        c[f"SKIT {tf} bullish"] = sk(tf)
    for k in (3, 6, 8):
        c[f"SKIT {k}+ of 8 bullish"] = skcount(k)
    for tf in ("2h", "4h", "D", "Wk"):
        c[f"SQZ {tf} fired"] = sqz(tf)
    return c


def stats(samples: list[dict]) -> dict | None:
    if not samples:
        return None
    v = [s["r60"] for s in samples]
    vc = [s["rclose"] for s in samples]
    return {"n": len(v), "avg60": round(st.mean(v), 3), "up60": round(100 * sum(x > 0 for x in v) / len(v), 1),
            "avgClose": round(st.mean(vc), 3), "upClose": round(100 * sum(x > 0 for x in vc) / len(vc), 1),
            "days": len({s["day"] for s in samples})}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    samples = build_samples()
    print(f"{len(samples)} samples with outcomes", flush=True)
    A = [s for s in samples if s["day"] <= SEARCH_END]
    B = [s for s in samples if s["day"] >= CHECK_START]
    conds = conditions()
    hit = {name: [fn(s) for s in samples] for name, fn in conds.items()}
    idxA = [i for i, s in enumerate(samples) if s["day"] <= SEARCH_END]
    idxB = [i for i, s in enumerate(samples) if s["day"] >= CHECK_START]
    results = []

    def evaluate(name, mask):
        a = [samples[i] for i in idxA if mask[i]]
        b = [samples[i] for i in idxB if mask[i]]
        if len(a) < MIN_N_SEARCH:
            return
        sa, sb = stats(a), stats(b)
        results.append({"cond": name, "search": sa, "check": sb})

    for name, m in hit.items():
        evaluate(name, m)
    names = list(hit)
    for x, y in itertools.combinations(names, 2):
        evaluate(f"{x} + {y}", [p and q for p, q in zip(hit[x], hit[y])])
    base = {"search": stats(A), "check": stats(B)}
    good_search = [r for r in results if r["search"]["avg60"] > 0 and r["search"]["up60"] > 50]
    good_search.sort(key=lambda r: -r["search"]["avg60"])
    surviving = [r for r in good_search if r["check"] and r["check"]["n"] >= MIN_N_CHECK
                 and r["check"]["avg60"] > 0 and r["check"]["up60"] > 50]
    report = {"baseline": base, "tried": len(results), "positiveInSearch": len(good_search),
              "surviving": surviving, "topSearchOnly": good_search[:40]}
    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    lines = [f"BASELINE (all snapshots): search {base['search']} | check {base['check']}",
             f"conditions tried (with n>={MIN_N_SEARCH} in search): {len(results)}; positive in search: {len(good_search)}; "
             f"ALSO positive in check (n>={MIN_N_CHECK}): {len(surviving)}", "", "SURVIVING (best search avg first):"]
    for r in surviving[:40]:
        lines.append(f"  {r['cond']:60s} search n={r['search']['n']:5d} 60m {r['search']['avg60']:+.2f}% up {r['search']['up60']}% "
                     f"close {r['search']['avgClose']:+.2f}% || check n={r['check']['n']:4d} 60m {r['check']['avg60']:+.2f}% "
                     f"up {r['check']['up60']}% close {r['check']['avgClose']:+.2f}%")
    lines += ["", "TOP 20 IN SEARCH and what happened in check:"]
    for r in good_search[:20]:
        c = r["check"] or {}
        lines.append(f"  {r['cond']:60s} search 60m {r['search']['avg60']:+.2f}% (n={r['search']['n']}) -> check "
                     f"{c.get('avg60', 0):+.2f}% (n={c.get('n', 0)}) up {c.get('up60', '-')}%")
    (OUT / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
