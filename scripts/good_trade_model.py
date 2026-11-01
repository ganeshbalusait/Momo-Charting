"""Learn what a GOOD scanner trade looked like, from every History row (his ask 2026-10-02:
"take a look scanner history all the tickers, see which one is good trade ... train yourself;
see all the columns - RVOL, SQZ, Skittles").

* Rows: bull Watchlist History, Sep 2 - Oct 2, 09:35-15:00 ET, one per symbol per 15 min.
* STALE ROWS DROPPED: the row's price must match the real 5m candle at that minute within
  0.5% (APLD / CRDO 10-02 09:44 rows held 09:05 bars and an old price - the first search
  learned from those).
* GOOD TRADE = from the next 5m open, the stock reaches +2% within 60 minutes BEFORE it
  touches -1% (a bar that does both counts as not good). Stock prices only.
* Every column as a number: RVOL value + painted on 7 timeframes, Skittles value + bullish on
  8, squeeze fired on 4, range position, % change, price, time of day, plus from the candles:
  vs VWAP, vs open, gap, above the 09:30-09:45 high, distance from the day high, last candle
  green, last candle volume vs average, SPY vs its VWAP and its open.
* Logistic model (numpy, standardised features, L2), TRAINED on Sep 2-19 only, TESTED on
  Sep 22 - Oct 2. Reported: base good-rate, test AUC, good-rate of the model's top 10% / 5%,
  which features push a row up or down, and the Oct 2 scores of APLD / CRDO / HPE / SMCI / NVDA.

    python scripts/good_trade_model.py
"""
from __future__ import annotations

import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import edge_search as E  # noqa: E402

OUT = ROOT / "artifacts" / "good_trade_model"
SEARCH_END, CHECK_START = "2026-09-19", "2026-09-22"
TFS_RV = ("5m", "15m", "30m", "1h", "2h", "4h", "D")
TFS_SK = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
TFS_SQ = ("2h", "4h", "D", "Wk")


def _first(window: list, up: float, down: float, want_up: bool) -> bool:
    for b in window:
        hit_up, hit_dn = b[2] >= up, b[3] <= down
        if want_up:
            if hit_dn:
                return False
            if hit_up:
                return True
        else:
            if hit_up:
                return False
            if hit_dn:
                return True
    return False


def label(dbars: list, idx: int) -> dict:
    """good = +2% before -1%; mirror = -2% before +1% (the same test the other way:
    if the model's picks hit the mirror as often, it only learned JUMPY, not UP)."""
    e = dbars[idx][1]
    end = dbars[idx][0] + timedelta(minutes=60)
    w = [b for b in dbars[idx:] if b[0] < end]
    close = next((b[4] for b in reversed(dbars) if b[0].hour * 60 + b[0].minute <= 955), dbars[-1][4])
    return {"good": _first(w, e * 1.02, e * 0.99, True), "mirror": _first(w, e * 1.01, e * 0.98, False),
            "r60": (w[-1][4] / e - 1) * 100, "rclose": (close / e - 1) * 100}


def features(row: dict, f: dict, spy: dict, minute: int) -> list[float]:
    n = E.num
    rv = row.get("rvol") or {}
    sk = row.get("skittles") or {}
    sq = row.get("sqz") or {}
    x = []
    for tf in TFS_RV:
        c = rv.get(tf) or {}
        x.append(max(-3.0, min(10.0, n(c.get("value")) or 0.0)))
        x.append(1.0 if c.get("bg") not in (None, "black") else 0.0)
    for tf in TFS_SK:
        c = sk.get(tf) or {}
        x.append((n(c.get("value")) or 50.0) / 100.0)
        bull = c.get("bg") in ("cyan", "green", "lime", "dark_green") or (c.get("bg") in (None, "black") and c.get("fg") in ("cyan", "green"))
        x.append(1.0 if bull else 0.0)
    for tf in TFS_SQ:
        x.append(1.0 if (sq.get(tf) or {}).get("bg") in ("cyan", "green") else 0.0)
    hl = n((row.get("highLow") or {}).get("value"))
    pct = n(row.get("pctChange")) or 0.0
    price = n(row.get("last")) or 1.0
    x += [hl if hl is not None else 0.5, max(-15, min(30, pct)), math.log(max(price, 0.5)), (minute - 570) / 390.0,
          max(-5, min(5, f.get("vwap_d") or 0.0)), max(-10, min(15, f.get("vs_open") or 0.0)), max(-10, min(15, f.get("gap") or 0.0)),
          1.0 if f.get("above_or") else 0.0, min(10, f.get("off_high") or 0.0), 1.0 if f.get("last5_up") else 0.0,
          min(10, f.get("vol_vs_avg") or 1.0), max(-2, min(2, spy.get("vwap_d") or 0.0)), max(-3, min(3, spy.get("vs_open") or 0.0)),
          1.0 if (row.get("news") or {}).get("headline") else 0.0, 1.0 if row.get("scanPass") else 0.0]
    return x


NAMES = ([f"RVOL {tf} value" if i == 0 else f"RVOL {tf} painted" for tf in TFS_RV for i in (0, 1)]
         + [f"SKIT {tf} value" if i == 0 else f"SKIT {tf} bullish" for tf in TFS_SK for i in (0, 1)]
         + [f"SQZ {tf} fired" for tf in TFS_SQ]
         + ["range position (H/L)", "% change today", "price (log)", "time of day", "vs VWAP %", "vs open %", "gap %",
            "above 09:30-45 high", "% below day high", "last 5m candle green", "last 5m volume vs avg", "SPY vs VWAP %",
            "SPY vs open %", "news in row", "scan pass"])


def build() -> list[dict]:
    days = sorted(p.stem for p in E.HIST.glob("2026-*.json") if datetime.fromisoformat(p.stem).weekday() < 5)
    bars = E.load_bars([])                         # uses the edge_search cache (all symbols + SPY)
    bd = {s: E.by_day(b) for s, b in bars.items()}
    out, stale = [], 0
    for day in days:
        rows = json.loads((E.HIST / f"{day}.json").read_text(encoding="utf-8"))["rows"]
        spyb = bd.get("SPY", {}).get(day) or []
        for sym, v in rows.items():
            dbars = bd.get(sym, {}).get(day)
            if not dbars:
                continue
            last_t = None
            for s in v.get("snapshots") or []:
                t = datetime.fromisoformat(s["at"]).astimezone(E.ET)
                m = t.hour * 60 + t.minute
                if not 575 <= m <= 900 or (last_t and (t - last_t).total_seconds() < 900):
                    continue
                start = t.replace(second=0, microsecond=0) + timedelta(minutes=(5 - t.minute % 5) % 5)
                idx = next((i for i, b in enumerate(dbars) if b[0] >= start), None)
                if not idx:
                    continue
                price = E.num(s["row"].get("last"))
                real = dbars[idx - 1][4]
                if not price or abs(price / real - 1) > 0.005:
                    stale += 1
                    continue
                last_t = t
                lab = label(dbars, idx)
                f = E.bar_facts(dbars, idx, (bd[sym][max(d for d in bd[sym] if d < day)][-1][4]
                                             if any(d < day for d in bd[sym]) else None))
                sidx = next((i for i, b in enumerate(spyb) if b[0] >= start), None)
                sp = E.bar_facts(spyb, sidx, None) if sidx else {}
                out.append({"day": day, "sym": sym, "t": t.strftime("%H:%M"), **lab,
                            "x": features(s["row"], f, sp, m)})
    print(f"{len(out)} fresh rows; {stale} stale rows dropped", flush=True)
    return out


def fit(X, y, l2=1.0, iters=600, lr=0.1):
    w = np.zeros(X.shape[1]); b = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    return w, b


def auc(scores, y):
    order = np.argsort(scores)
    ranks = np.empty(len(scores)); ranks[order] = np.arange(1, len(scores) + 1)
    pos = y.sum(); neg = len(y) - pos
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2) / (pos * neg)) if pos and neg else float("nan")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    data = build()
    A = [d for d in data if d["day"] <= SEARCH_END]
    B = [d for d in data if d["day"] >= CHECK_START]
    XA = np.array([d["x"] for d in A]); yA = np.array([d["good"] for d in A], dtype=float)
    XB = np.array([d["x"] for d in B]); yB = np.array([d["good"] for d in B], dtype=float)
    mu, sd = XA.mean(0), XA.std(0) + 1e-9
    w, b = fit((XA - mu) / sd, yA)
    sB = 1 / (1 + np.exp(-(((XB - mu) / sd) @ w + b)))
    sA = 1 / (1 + np.exp(-(((XA - mu) / sd) @ w + b)))
    lines = [f"rows: train {len(A)} (Sep 2-19), test {len(B)} (Sep 22 - Oct 2)",
             f"GOOD (+2% within 60 min before -1%): train {100 * yA.mean():.1f}%, test {100 * yB.mean():.1f}%",
             f"AUC (0.5 = coin flip): train {auc(sA, yA):.3f}, TEST {auc(sB, yB):.3f}"]
    mB = np.array([d["mirror"] for d in B], dtype=float)
    r60B = np.array([d["r60"] for d in B]); rcB = np.array([d["rclose"] for d in B])
    lines.append(f"TEST overall: good {100 * yB.mean():.1f}% | mirror (-2% before +1%) {100 * mB.mean():.1f}% | "
                 f"net 60m {r60B.mean():+.2f}% | to close {rcB.mean():+.2f}%")
    for q in (0.10, 0.05, 0.02):
        k = max(1, int(len(sB) * q)); top = np.argsort(-sB)[:k]
        lines.append(f"TEST top {int(q * 100)}% by model ({k} rows): good {100 * yB[top].mean():.1f}% | mirror {100 * mB[top].mean():.1f}% | "
                     f"net 60m {r60B[top].mean():+.2f}% (up {100 * (r60B[top] > 0).mean():.0f}%) | to close {rcB[top].mean():+.2f}%")
    order = np.argsort(-np.abs(w))
    lines.append("strongest features (+ = more likely good, standardised weight):")
    for i in order[:16]:
        lines.append(f"   {NAMES[i]:28s} {w[i]:+.3f}")
    lines.append("Oct 2 examples (model score = chance of a good trade):")
    for d, s in zip(B, sB):
        if d["day"] == "2026-10-02" and d["sym"] in ("APLD", "CRDO", "HPE", "SMCI", "NVDA") and d["t"] < "11:00":
            lines.append(f"   {d['sym']:5s} {d['t']}  score {100 * s:4.1f}%  actual {'GOOD' if d['good'] else '-'}")
    (OUT / "report.txt").write_text("\n".join(lines), encoding="utf-8")
    json.dump({"w": w.tolist(), "b": b, "mu": mu.tolist(), "sd": sd.tolist(), "names": NAMES}, open(OUT / "model.json", "w"))
    print("\n".join(lines))


if __name__ == "__main__":
    main()
