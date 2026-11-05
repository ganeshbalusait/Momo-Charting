"""Check the live 21/50 EMA touch scanner's levels against a full-history EMA.

The scanner (momx/ema_touch.py) builds its EMA from Alpaca bars that start in
2015 (monthly), ~3 years back (weekly) or ~14 months back (daily). TOS seeds
ExpAverage from the first bar it loads, so a short history can leave the line
off by dollars. This script recomputes each level from Yahoo daily history
since 2000 (resampled to W/M) with the same TOS recurrence and prints the gap.

Usage: python artifacts/verify_ema_touch_levels.py AAPL NVDA OUST
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import backtest_leap_ema as b  # noqa: E402
from momx import ema_touch  # noqa: E402

RULE = {"Daily": "D", "Weekly": "W-FRI", "Monthly": "ME"}


def full_history_level(df, tf: str, n: int, period: str):
    bars = b.resample(df, RULE[tf]) if tf != "Daily" else df.assign(last_day=df.index)
    if tf == "Monthly":
        keys = [d.strftime("%Y-%m") for d in bars.index]
    elif tf == "Weekly":
        # Alpaca stamps a week by its Monday; resample stamps it by Friday.
        keys = [(d - __import__("pandas").Timedelta(days=4)).strftime("%Y-%m-%d") for d in bars.index]
    else:
        keys = [d.strftime("%Y-%m-%d") for d in bars.index]
    closes = [float(c) for c, k in zip(bars["close"], keys) if k < period]
    if not closes:
        return None, 0
    return ema_touch.ema(closes, n)[-1], len(closes)


def main(symbols):
    doc = json.loads((Path(__file__).with_name("momx_ema_touch.json")).read_text())
    periods = doc["periods"]
    print(f"scanner fetchedAt {doc['fetchedAt']}  periods {periods}")
    print(f"{'sym':6s} {'tf':8s} {'n':>3s} {'scanner':>10s} {'full-hist':>10s} {'gap $':>8s} {'gap %':>7s} {'bars':>5s} pass")
    worst = 0.0
    for sym in symbols:
        df = b.fetch(sym)
        row = doc["symbols"].get(sym) or {}
        for tf in ("Daily", "Weekly", "Monthly"):
            for n in (21, 50):
                st = row.get(f"{n}:{tf}")
                ref, count = full_history_level(df, tf, n, periods[tf])
                if st is None or ref is None:
                    print(f"{sym:6s} {tf:8s} {n:3d} {'unsettled':>10s} {ref or 0:10.2f}")
                    continue
                gap = st["level"] - ref
                pct = gap / ref * 100
                worst = max(worst, abs(pct))
                print(f"{sym:6s} {tf:8s} {n:3d} {st['level']:10.2f} {ref:10.2f} {gap:8.2f} {pct:7.2f} {count:5d} {st['pass']}")
    print(f"\nworst gap {worst:.2f}%")


if __name__ == "__main__":
    main([s.upper() for s in sys.argv[1:]] or ["AAPL", "NVDA", "OUST"])
