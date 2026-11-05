"""21 / 50 EMA swing back-test - does any EMA setup beat buying on a random day?

Ganesh 2026-11-04: "Build and verify a 21 EMA / 50 EMA swing scanner. No
guesswork: every rule must be proven by a back-test before it goes on screen."

DATA  Yahoo daily bars since 2000 (split-adjusted; cache shared with
      backtest_leap_ema.py), every STOCK on watchlist.txt (ETFs left out -
      leveraged ETFs decay and are not what the rules are for). Weekly and
      monthly bars are built from the daily tape. EMA = TOS ExpAverage
      (alpha 2/(n+1), first close seeds it) = momx.ema_touch.ema.
      LIMIT: Yahoo has no bars for delisted tickers, so names that died are
      missing (survivor bias - results look a little better than reality).

SETUPS (judged on a COMPLETED D / W / M bar; entry = close of the next
      trading day, so nothing is known before it is printed):
  touch21 / touch50   uptrend (close > 50 EMA, 50 EMA rising, 21 > 50), the
                      bar's low reached the 21 (or 50) EMA, close held above it
  bounce21 / bounce50 a touch, then the NEXT bar closes above the touch bar's
                      high; enter after that bar
  cross               21 EMA crosses above the 50 EMA
  reclaim             previous close below the 50 EMA, this close above it
  M50 intramonth / M21 intramonth  (monthly only) a daily low reaches LAST
                      month's monthly EMA, last month closed above a rising
                      EMA; enter at that day's close (the current best rule)

EXITS  Two ways, both reported:
  plain    hold N trading days (5 / 10 / 20 / 40), sell at the close
  managed  stop = a daily close below the setup's 50 EMA level at entry;
           target = entry + 2 x (entry - stop); else sell after N days
BASELINE  buy at the close of EVERY trading day, plain hold N days. A second
          context line: every day the DAILY chart is in an uptrend.

OPTIONS  a call at the chosen delta (default 0.50 and 0.30), 45 DTE (60 DTE
         for the 40-day hold), Black-Scholes with IV = trailing 60-day realised
         vol (floor 15%) held FIXED at the entry value (no vol-crush luck).
         Bought at the ask, sold at the bid: half-spread = max(4% of mid,
         $0.025) each way. Modelled prices - not real quotes.

SPLIT  train = entries 2005-2018, test = 2019-2026 (never used to choose
       anything). VERDICT "beats random" = win % at least 2 points higher AND
       median higher than the baseline, in BOTH periods, with >= 30 trades in
       each. Judged on the plain hold so setup and baseline are measured the
       same way.

Run:  .venv\\Scripts\\python.exe artifacts\\backtest_ema_swing.py [--delta 0.5 0.3]
Out:  artifacts/ema_swing_results.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest_leap_ema as b  # noqa: E402  (fetch / resample / Yahoo cache)
from backtest_leap_ema_intramonth import intramonth_entries  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(__file__).with_name("ema_swing_results.json")
HOLDS = (5, 10, 20, 40)
START = pd.Timestamp("2005-01-01")
SPLIT_YEAR = 2018            # train <= 2018 < test
RATE = 0.04
MIN_TRADES = 30
WIN_MARGIN = 0.02
TIMEFRAMES = {"Daily": "D", "Weekly": "W-FRI", "Monthly": "ME"}
SETUPS = ("touch21", "touch50", "bounce21", "bounce50", "cross", "reclaim")


# --------------------------------------------------------------------------
# pricing
# --------------------------------------------------------------------------

def norm_cdf(x):
    """Standard normal CDF (Abramowitz-Stegun 7.1.26, error < 1.5e-7)."""
    x = np.asarray(x, dtype=float)
    z = np.abs(x) / math.sqrt(2.0)
    t = 1.0 / (1.0 + 0.3275911 * z)
    poly = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429))))
    erf = 1.0 - poly * np.exp(-z * z)
    return 0.5 * (1.0 + np.sign(x) * erf)


def norm_ppf(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(80):
        mid = (lo + hi) / 2
        if float(norm_cdf(mid)) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def bs_call(s, k, t, vol, r=RATE):
    s, k, t, vol = (np.asarray(a, dtype=float) for a in (s, k, t, vol))
    t_safe = np.maximum(t, 1e-9)
    d1 = (np.log(s / k) + (r + vol * vol / 2) * t_safe) / (vol * np.sqrt(t_safe))
    d2 = d1 - vol * np.sqrt(t_safe)
    value = s * norm_cdf(d1) - k * np.exp(-r * t_safe) * norm_cdf(d2)
    return np.where(t <= 0, np.maximum(s - k, 0.0), value)


def strike_for_delta(s, t, vol, delta, r=RATE):
    """K such that the call's N(d1) = delta."""
    d1 = norm_ppf(delta)
    return s * np.exp(-(d1 * vol * np.sqrt(t)) + (r + vol * vol / 2) * t)


def half_spread(mid):
    return np.maximum(0.04 * mid, 0.025)


def option_returns(s0, s1, days_held, vol, delta, dte):
    """Buy at the ask, sell at the bid. Vectorised over trades."""
    t0 = dte / 252.0
    k = strike_for_delta(s0, t0, vol, delta)
    mid0 = bs_call(s0, k, t0, vol)
    mid1 = bs_call(s1, k, np.maximum(dte - days_held, 0) / 252.0, vol)
    ask = mid0 + half_spread(mid0)
    bid = np.maximum(mid1 - half_spread(mid1), 0.0)
    return bid / ask - 1.0


def dte_for(hold: int) -> int:
    return 60 if hold >= 40 else 45


# --------------------------------------------------------------------------
# signals
# --------------------------------------------------------------------------

def tos_ema(series: pd.Series, n: int) -> pd.Series:
    return series.ewm(span=n, adjust=False).mean()   # == momx.ema_touch.ema


def bar_signals(bars: pd.DataFrame) -> dict[str, pd.Series]:
    """Boolean per completed bar + the 50 EMA level, for every setup."""
    c, lo, hi = bars["close"], bars["low"], bars["high"]
    e21, e50 = tos_ema(c, 21), tos_ema(c, 50)
    up = (c > e50) & (e50 > e50.shift(1)) & (e21 > e50)
    touch21 = up & (lo <= e21) & (c > e21)
    touch50 = up & (lo <= e50) & (c > e50)
    out = {
        "touch21": touch21,
        "touch50": touch50,
        # the bounce fires on the bar AFTER the touch
        "bounce21": touch21.shift(1, fill_value=False) & (c > hi.shift(1)),
        "bounce50": touch50.shift(1, fill_value=False) & (c > hi.shift(1)),
        "cross": (e21 > e50) & (e21.shift(1) <= e50.shift(1)),
        "reclaim": (c.shift(1) < e50.shift(1)) & (c > e50),
    }
    warm = np.arange(len(bars)) < 100          # 2 x 50 bars before trusting the EMA
    for key in out:
        out[key] = out[key].astype(bool) & ~warm
    return out, e50


def daily_entries(df: pd.DataFrame, tf: str) -> dict[str, list[tuple[int, float]]]:
    """setup -> [(entry index into df, 50 EMA stop level)]"""
    rule = TIMEFRAMES[tf]
    bars = b.resample(df, rule)
    sig, e50 = bar_signals(bars)
    pos = {d: i for i, d in enumerate(df.index)}
    out = {}
    for name, hit in sig.items():
        rows = []
        for k in np.flatnonzero(hit.values):
            i = pos.get(bars["last_day"].iat[k])
            if i is None or i + 1 >= len(df):
                continue
            rows.append((i + 1, float(e50.iat[k])))
        out[name] = rows
    if tf == "Monthly":
        m = b.resample(df, "ME")
        for n in (50, 21):
            ema50_m = tos_ema(m["close"], 50)
            prev_level = pd.Series(ema50_m.values, index=m.index.to_period("M"))
            rows = []
            for i in intramonth_entries(df, n, True):
                month = df.index[i].to_period("M") - 1
                stop = prev_level.get(month)
                if stop is not None and np.isfinite(stop):
                    rows.append((i, float(stop)))
            out[f"M{n} intramonth"] = rows
    return out


# --------------------------------------------------------------------------
# trades
# --------------------------------------------------------------------------

def run_trades(close: np.ndarray, entries, hold: int, low: np.ndarray | None = None):
    """One open trade at a time per (ticker, setup, hold). Returns arrays.

    Every signal is traded (the plain-hold verdict must see all of them). If
    the entry close is already at/below the 50 EMA stop (a monthly 50 touch
    can close under the line), the managed stop is the entry day's low, or 3%
    under the entry when the low is no lower than the close.
    """
    rows = []
    busy = -1
    n = len(close)
    for i, stop in entries:
        if i <= busy or i + hold >= n:
            continue
        p = close[i]
        if not (p > stop > 0):
            day_low = low[i] if low is not None else p
            stop = day_low if day_low < p else p * 0.97
        target = p + 2 * (p - stop)
        path = close[i + 1:i + hold + 1]
        hit = np.flatnonzero((path <= stop) | (path >= target))
        j = i + 1 + hit[0] if hit.size else i + hold
        rows.append((i, j, close[i + hold] / p - 1, close[j] / p - 1,
                     path.min() / p - 1, close[i + hold], close[j]))
        busy = i + hold
    return rows


def stats(values: np.ndarray, dips: np.ndarray | None = None) -> dict | None:
    if values.size == 0:
        return None
    out = {
        "n": int(values.size),
        "win": float((values > 0).mean()),
        "median": float(np.median(values)),
        "avg": float(values.mean()),
        "worst10": float(np.percentile(values, 10)),
        "loseHalf": float((values < -0.5).mean()),
    }
    if dips is not None and dips.size:
        out["medianDip"] = float(np.median(dips))
        out["worstDip5"] = float(np.percentile(dips, 5))
    return out


def beats(setup: dict | None, base: dict | None) -> bool:
    if not setup or not base or setup["n"] < MIN_TRADES:
        return False
    return setup["win"] >= base["win"] + WIN_MARGIN and setup["median"] > base["median"]


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def load_universe() -> dict[str, pd.DataFrame]:
    syms = [s.strip().upper() for s in (ROOT / "watchlist.txt").read_text().split() if s.strip()]
    data, skipped = {}, []
    for sym in syms:
        try:
            meta = json.loads((b.CACHE / f"{sym}.json").read_text())["chart"]["result"][0]["meta"]
            if meta.get("instrumentType") != "EQUITY":
                skipped.append(sym)
                continue
            df = b.fetch(sym)
        except Exception:  # noqa: BLE001
            skipped.append(sym)
            continue
        if len(df) > 300:
            data[sym] = df
    print(f"stocks {len(data)}  skipped (ETF / no data) {len(skipped)}", file=sys.stderr)
    return data


def main(deltas: list[float]) -> dict:
    data = load_universe()
    instruments = ["stock"] + [f"call{int(d * 100)}" for d in deltas]
    # bucket[(tf, setup, hold, period, mode, instrument)] -> list of arrays
    bucket: dict[tuple, list] = {}
    base: dict[tuple, list] = {}

    def add(store, key, arr):
        store.setdefault(key, []).append(np.asarray(arr, dtype=float))

    for count, (sym, df) in enumerate(data.items(), 1):
        close = df["close"].to_numpy(float)
        lows = df["low"].to_numpy(float)
        years = df.index.year.to_numpy()
        vol = (np.log(df["close"]).diff().rolling(60).std() * math.sqrt(252)).clip(lower=0.15).to_numpy()
        live = (df.index >= START) & np.isfinite(vol)
        # baselines: every day, and every day the daily chart is in an uptrend
        e21d, e50d = tos_ema(df["close"], 21).to_numpy(), tos_ema(df["close"], 50).to_numpy()
        uptrend = (close > e50d) & (e50d > np.roll(e50d, 1)) & (e21d > e50d)
        uptrend[:100] = False
        for hold in HOLDS:
            idx = np.flatnonzero(live[: len(close) - hold])
            ret = close[idx + hold] / close[idx] - 1
            for period, mask in (("train", years[idx] <= SPLIT_YEAR), ("test", years[idx] > SPLIT_YEAR)):
                for group, gmask in (("any", mask), ("uptrend", mask & uptrend[idx])):
                    sel = idx[gmask]
                    add(base, (group, hold, period, "stock"), ret[gmask])
                    for d in deltas:
                        opt = option_returns(close[sel], close[sel + hold], hold, vol[sel], d, dte_for(hold))
                        add(base, (group, hold, period, f"call{int(d * 100)}"), opt)
        for tf in TIMEFRAMES:
            for setup, entries in daily_entries(df, tf).items():
                entries = [(i, s) for i, s in entries if live[i]]
                for hold in HOLDS:
                    rows = run_trades(close, entries, hold, lows)
                    if not rows:
                        continue
                    r = np.array(rows)
                    i, j = r[:, 0].astype(int), r[:, 1].astype(int)
                    for period, mask in (("train", years[i] <= SPLIT_YEAR), ("test", years[i] > SPLIT_YEAR)):
                        if not mask.any():
                            continue
                        key = (tf, setup, hold, period)
                        add(bucket, key + ("plain", "stock"), r[mask, 2])
                        add(bucket, key + ("managed", "stock"), r[mask, 3])
                        add(bucket, key + ("dip", "stock"), r[mask, 4])
                        for d in deltas:
                            name = f"call{int(d * 100)}"
                            s0 = close[i[mask]]
                            add(bucket, key + ("plain", name),
                                option_returns(s0, r[mask, 5], hold, vol[i[mask]], d, dte_for(hold)))
                            add(bucket, key + ("managed", name),
                                option_returns(s0, r[mask, 6], (j - i)[mask], vol[i[mask]], d, dte_for(hold)))
        if count % 50 == 0:
            print(f"  {count}/{len(data)}", file=sys.stderr)

    cat = lambda parts: np.concatenate(parts) if parts else np.array([])
    baseline = {}
    for (group, hold, period, inst), parts in base.items():
        baseline.setdefault(group, {}).setdefault(str(hold), {}).setdefault(period, {})[inst] = stats(cat(parts))

    setups = {}
    names = sorted({k[:2] for k in bucket})
    for tf, setup in names:
        entry = setups.setdefault(tf, {}).setdefault(setup, {})
        for hold in HOLDS:
            h = {}
            for period in ("train", "test"):
                key = (tf, setup, hold, period)
                dips = cat(bucket.get(key + ("dip", "stock"), []))
                p = {}
                for inst in instruments:
                    p[inst] = {
                        "plain": stats(cat(bucket.get(key + ("plain", inst), [])), dips if inst == "stock" else None),
                        "managed": stats(cat(bucket.get(key + ("managed", inst), [])), dips if inst == "stock" else None),
                    }
                h[period] = p
            verdict = {}
            for inst in instruments:
                verdict[inst] = all(
                    beats(h[period][inst]["plain"], baseline["any"][str(hold)][period][inst])
                    for period in ("train", "test")
                )
            h["beatsRandom"] = verdict
            entry[str(hold)] = h

    result = {
        "generated": pd.Timestamp.now(tz="America/New_York").isoformat(),
        "stocks": len(data),
        "deltas": deltas,
        "holds": list(HOLDS),
        "split": f"train <= {SPLIT_YEAR} < test",
        "rule": f"beats = win >= baseline + {int(WIN_MARGIN * 100)} pts AND higher median, train AND test, n >= {MIN_TRADES}",
        "baseline": baseline,
        "setups": setups,
    }
    OUT.write_text(json.dumps(result, indent=1))
    print(f"wrote {OUT}", file=sys.stderr)
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--delta", type=float, nargs="+", default=[0.5, 0.3])
    main(ap.parse_args().delta)
