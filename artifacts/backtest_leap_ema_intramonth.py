"""LEAP entry back-test, part 2: buy DURING the month when price touches the
monthly 21/50 EMA, instead of waiting for the monthly candle to close.

Ganesh 2026-10-03 bought MSFT near the monthly 50 EMA mid-month and sold the
day the close-based rule would have entered. This tests his way.

Level = the EMA from the last COMPLETED month (known on day 1 of the month,
no look-ahead). Entry = close of the first day whose low reaches the level.
  uptrend - previous month closed above the EMA and the EMA was rising
  any     - no trend filter
Same pricing / hold / baseline as backtest_leap_ema.py.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import backtest_leap_ema as b

OUT = Path(__file__).with_name("leap_ema_intramonth_results.json")


def intramonth_entries(df: pd.DataFrame, n: int, trend: bool) -> list[int]:
    m = b.resample(df, "ME")
    ema = m["close"].ewm(span=n, adjust=False).mean()
    ok = (m["close"] > ema) & (ema > ema.shift(1)) if trend else pd.Series(True, index=m.index)
    ok.iloc[: 3 * n] = False
    month = df.index.to_period("M")
    levels = pd.Series(ema.values, index=m.index.to_period("M"))
    allowed = pd.Series(ok.values, index=m.index.to_period("M"))
    out, done = [], set()
    for i, (p, lo) in enumerate(zip(month, df["low"].values)):
        prev = p - 1
        if p in done or prev not in levels.index or not allowed.get(prev, False):
            continue
        if lo <= levels[prev]:
            out.append(i)
            done.add(p)
    return out


def main():
    data = {s: b.fetch(s) for s in b.TICKERS}
    results = {}
    for money_name, money in (("ATM", 1.0), ("ITM10", 0.9)):
        res = {}
        for n in (21, 50):
            for trend in (True, False):
                rows = {"all": [], "early": [], "late": []}
                for sym, df in data.items():
                    vol = (np.log(df["close"]).diff().rolling(60).std() * math.sqrt(252)).clip(lower=0.15)
                    start = df.index[0] + pd.Timedelta(days=365 * 5)
                    busy = -1
                    for i in intramonth_entries(df, n, trend):
                        if i <= busy or df.index[i] < start:
                            continue
                        t = b.trade(df, vol, i, money)
                        if not t:
                            continue
                        busy = i + b.HOLD
                        era = "early" if df.index[i].year <= 2014 else "late"
                        rows["all"].append(t); rows[era].append(t)
                res[f"M {n}EMA intramonth {'uptrend' if trend else 'any'}"] = {k: b.summarize(v) for k, v in rows.items()}
        results[money_name] = res
    OUT.write_text(json.dumps(results, indent=1))
    for money_name, res in results.items():
        print(f"\n=== {money_name} ===")
        for name, d in res.items():
            a, e, l = d["all"], d["early"], d["late"]
            print(f"{name:32s} n={a['n']:4d} win={a['win']*100:5.1f} med={a['median']*100:6.1f} avg={a['avg']*100:6.1f} <-50%={a['bigLoss']*100:5.1f} "
                  f"| early win={e['win']*100:4.0f} med={e['median']*100:4.0f} | late n={l['n']} win={l['win']*100:4.0f} med={l['median']*100:4.0f} <-50%={l['bigLoss']*100:3.0f}")


if __name__ == "__main__":
    main()
