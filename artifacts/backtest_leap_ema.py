"""LEAP entry back-test: 21 vs 50 EMA on Daily / Weekly / Monthly.

Question (Ganesh 2026-10-03): for a LEAP call, which entry is better -
21 EMA or 50 EMA, on the monthly, weekly or daily chart? "No guess work."

Data: Yahoo daily history since 2000 (split-adjusted), 48 large optionable
names incl. laggards (INTC, BA, PFE, T, ...) plus SPY/QQQ/IWM.
Weekly/monthly bars are built from the daily tape. EMA = TOS ExpAverage.

Two entry styles per EMA/timeframe, judged on the COMPLETED bar:
  touch   - prior bar closed above the EMA, this bar's low dipped to/below it,
            closed back above it, EMA still rising (pullback that held)
  reclaim - prior bar closed below the EMA, this bar closed above it
Entry = next trading day's close. One open trade per ticker per rule.

Outcome: a ~1-year ATM call (and a 10% ITM call) priced with Black-Scholes
(IV = trailing 60-day realised vol, floor 15%, r = 4%), sold after 6 months.
Baseline = the same call bought on every trading day.
"""
from __future__ import annotations

import json
import math
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

TICKERS = [
    "SPY", "QQQ", "IWM",
    "AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA", "AMD", "NFLX",
    "CSCO", "ORCL", "IBM", "QCOM", "MU", "INTC", "CAT", "HD", "MCD", "UNH",
    "JNJ", "MRK", "PFE", "KO", "WMT", "JPM", "BAC", "C", "GS", "XOM", "CVX",
    "BA", "DIS", "NKE", "SBUX", "MMM", "GE", "F", "T", "VZ", "CVS", "AMGN",
    "PYPL", "ADBE", "CRM", "AVGO",
]
CACHE = Path(__file__).with_name("leap_ema_daily_cache")
OUT = Path(__file__).with_name("leap_ema_results.json")
HOLD = 126          # trading days held (6 months)
DTE = 252           # call bought with ~1 year left
RATE = 0.04


def fetch(sym: str) -> pd.DataFrame:
    CACHE.mkdir(exist_ok=True)
    path = CACHE / f"{sym}.json"
    if not path.exists() or time.time() - path.stat().st_mtime > 86400:
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
               f"?period1=946684800&period2={int(time.time())}&interval=1d")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
        time.sleep(0.4)
    res = json.loads(path.read_text())["chart"]["result"][0]
    q = res["indicators"]["quote"][0]
    df = pd.DataFrame({k: q[k] for k in ("open", "high", "low", "close")},
                      index=pd.to_datetime(res["timestamp"], unit="s").normalize())
    df = df.dropna()
    return df[~df.index.duplicated(keep="last")]


def bs_call(s, k, t, vol, r=RATE):
    if t <= 0:
        return max(s - k, 0.0)
    d1 = (math.log(s / k) + (r + vol * vol / 2) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    n = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
    return s * n(d1) - k * math.exp(-r * t) * n(d2)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    if rule == "D":
        out = df.copy()
        out["last_day"] = out.index
        return out
    g = df.resample(rule)
    out = pd.DataFrame({"open": g["open"].first(), "high": g["high"].max(),
                        "low": g["low"].min(), "close": g["close"].last()})
    out["last_day"] = df["close"].resample(rule).apply(lambda s: s.index[-1] if len(s) else pd.NaT)
    out = out.dropna()
    # drop the still-forming final bar (not completed yet)
    today = df.index[-1]
    if rule.startswith("W") and today.weekday() < 4:
        out = out.iloc[:-1]
    if rule == "ME" and out["last_day"].iloc[-1] == today:
        out = out.iloc[:-1]
    return out


def signals(bars: pd.DataFrame, n: int, style: str) -> list[pd.Timestamp]:
    ema = bars["close"].ewm(span=n, adjust=False).mean()
    c, lo = bars["close"], bars["low"]
    pc, pe = c.shift(1), ema.shift(1)
    if style == "touch":
        hit = (pc > pe) & (lo <= ema) & (c > ema) & (ema > pe)
    else:
        hit = (pc < pe) & (c > ema)
    hit.iloc[: 3 * n] = False          # EMA warm-up
    return list(bars.loc[hit, "last_day"])


def trade(df, vol, i, moneyness):
    """Buy at day i close, sell HOLD days later. Returns dict or None."""
    j = i + HOLD
    if j >= len(df):
        return None
    s0, s1 = df["close"].iat[i], df["close"].iat[j]
    v0, v1 = vol.iat[i], vol.iat[j]
    if not (np.isfinite(v0) and np.isfinite(v1)):
        return None
    k = s0 * moneyness
    c0 = bs_call(s0, k, DTE / 252, v0)
    c1 = bs_call(s1, k, (DTE - HOLD) / 252, v1)
    worst = df["close"].iloc[i:j + 1].min() / s0 - 1
    return {"opt": c1 / c0 - 1, "stk": s1 / s0 - 1, "worst": worst}


def summarize(rows):
    if not rows:
        return None
    o = np.array([r["opt"] for r in rows]); s = np.array([r["stk"] for r in rows])
    w = np.array([r["worst"] for r in rows])
    return {"n": len(rows), "win": float((o > 0).mean()), "avg": float(o.mean()),
            "median": float(np.median(o)), "bigLoss": float((o < -0.5).mean()),
            "stockAvg": float(s.mean()), "stockWorstAvg": float(w.mean())}


def main():
    data = {}
    for sym in TICKERS:
        try:
            data[sym] = fetch(sym)
        except Exception as exc:  # noqa: BLE001
            print("skip", sym, exc, file=sys.stderr)
    print("tickers", len(data), file=sys.stderr)

    rules = [(tf, n, st) for tf in ("D", "W-FRI", "ME") for n in (21, 50) for st in ("touch", "reclaim")]
    results = {}
    for money_name, money in (("ATM", 1.0), ("ITM10", 0.9)):
        per_rule = {r: {"all": [], "early": [], "late": []} for r in rules}
        base = {"all": [], "early": [], "late": []}
        for sym, df in data.items():
            vol = np.log(df["close"]).diff().rolling(60).std() * math.sqrt(252)
            vol = vol.clip(lower=0.15)
            pos = {d: i for i, d in enumerate(df.index)}
            start = df.index[0] + pd.Timedelta(days=365 * 5)   # same window for everyone
            # baseline: every trading day in the window
            for i in range(len(df)):
                if df.index[i] < start:
                    continue
                t = trade(df, vol, i, money)
                if t:
                    era = "early" if df.index[i].year <= 2014 else "late"
                    base["all"].append(t); base[era].append(t)
            for rule in rules:
                tf, n, st = rule
                busy_until = -1
                for d in signals(resample(df, tf), n, st):
                    i = pos.get(d)
                    if i is None:
                        continue
                    i += 1                      # enter next trading day
                    if i <= busy_until or df.index[min(i, len(df) - 1)] < start:
                        continue
                    t = trade(df, vol, i, money)
                    if not t:
                        continue
                    busy_until = i + HOLD
                    era = "early" if df.index[i].year <= 2014 else "late"
                    per_rule[rule]["all"].append(t); per_rule[rule][era].append(t)
        results[money_name] = {
            "baseline": {k: summarize(v) for k, v in base.items()},
            "rules": {f"{tf.split('-')[0].replace('ME','M')} {n}EMA {st}": {k: summarize(v) for k, v in d.items()}
                      for (tf, n, st), d in per_rule.items()},
        }
    OUT.write_text(json.dumps(results, indent=1))
    for money_name, res in results.items():
        b = res["baseline"]["all"]
        print(f"\n=== {money_name} 1-yr call, held 6 months ===")
        print(f"{'rule':26s} {'n':>5s} {'win%':>6s} {'avg':>7s} {'med':>7s} {'<-50%':>6s} {'stk':>6s} | {'early avg':>9s} {'late avg':>9s}")
        print(f"{'BASELINE every day':26s} {b['n']:5d} {b['win']*100:6.1f} {b['avg']*100:7.1f} {b['median']*100:7.1f} {b['bigLoss']*100:6.1f} {b['stockAvg']*100:6.1f} | "
              f"{res['baseline']['early']['avg']*100:9.1f} {res['baseline']['late']['avg']*100:9.1f}")
        for name, d in res["rules"].items():
            a = d["all"]
            if not a:
                print(name, "no trades"); continue
            e = d["early"]["avg"] * 100 if d["early"] else float("nan")
            l = d["late"]["avg"] * 100 if d["late"] else float("nan")
            print(f"{name:26s} {a['n']:5d} {a['win']*100:6.1f} {a['avg']*100:7.1f} {a['median']*100:7.1f} {a['bigLoss']*100:6.1f} {a['stockAvg']*100:6.1f} | {e:9.1f} {l:9.1f}")


if __name__ == "__main__":
    main()
