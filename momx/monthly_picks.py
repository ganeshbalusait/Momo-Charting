"""Monthly Picks: a once-a-month, 20-stock, equal-weight momentum portfolio.

Ganesh 2026-11-04, after reading Investing.com's ProPicks pitch ("AI picks up
to 20 stocks, rebalanced monthly"): "Can we build our app how AI pick stocks?"
This is version 1. It is deliberately the part we can BACK-TEST HONESTLY with
data the app already has (daily bars): price momentum, closeness to the
52-week high, trend, calm-vs-choppy, and sector strength. Fundamentals
(valuation, earnings growth, debt, analyst views) come later, because we have
no point-in-time history for them and a back-test built on today's
fundamentals would be lying.

The rule, evaluated on the LAST trading day of each month at the close:

    eligible  = price >= $5, 20-day avg dollar volume >= $20M,
                >= 260 daily bars, already in the index on that date
    score     = weighted average of percentile ranks (0..1, higher better):
                  35%  12-1 momentum   (return t-252 -> t-21, skips last month)
                  15%  6-1 momentum    (return t-126 -> t-21)
                  20%  near 52w high   (close / 252-day high)
                  10%  trend           (close > SMA200, SMA50 > SMA200)
                  10%  calm            (low 63-day volatility)
                  10%  sector strength (sector's median 63-day return)
    portfolio = top 20 by score, equal weight; a stock already held is KEPT
                while it still ranks in the top 40 (cuts pointless churn)

Held from that close to the next month-end close. Costs are charged on every
name swapped. The back-test reports three lines so the result cannot flatter
itself: the picks, SPY, and an equal-weight basket of the WHOLE eligible
universe (if the picks do not beat their own universe, the "AI" adds nothing).

Pure pandas, no I/O: scripts/monthly_picks.py fetches the bars and writes the
report, so tests can drive this with synthetic prices.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

WEIGHTS: dict[str, float] = {
    "mom12_1": 0.35,
    "mom6_1": 0.15,
    "near_high": 0.20,
    "trend": 0.10,
    "calm": 0.10,
    "sector": 0.10,
}

FACTOR_LABELS = {
    "mom12_1": "12-month momentum",
    "mom6_1": "6-month momentum",
    "near_high": "near 52-week high",
    "trend": "uptrend (50/200 SMA)",
    "calm": "steady (low volatility)",
    "sector": "strong sector",
}

YEAR = 252
SKIP = 21          # skip the most recent month (short-term reversal)
HALF = 126
QUARTER = 63


@dataclass(frozen=True)
class PickConfig:
    top_n: int = 20
    keep_rank: int = 40
    min_price: float = 5.0
    min_dollar_volume: float = 20e6
    min_history: int = 260
    cost_bps: float = 10.0          # per side, per name swapped
    weights: Mapping[str, float] = field(default_factory=lambda: dict(WEIGHTS))


# ----------------------------------------------------------------- panels
def build_panels(bars: Mapping[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """Daily close / high / dollar-volume panels (dates x symbols).

    Accepts the app's bar frames (a ``timestamp`` column or a datetime index,
    lower-case ohlcv columns). Dates are normalised to calendar days so a
    Schwab (ET) frame and an Alpaca (UTC) frame line up.
    """
    closes, highs, dvols = {}, {}, {}
    for symbol, frame in bars.items():
        if frame is None or len(frame) == 0:
            continue
        df = frame.copy()
        if "timestamp" in df.columns:
            ts = pd.to_datetime(df["timestamp"])
        else:
            ts = pd.to_datetime(df.index)
        ts = pd.DatetimeIndex(ts)
        if ts.tz is not None:
            ts = ts.tz_convert("America/New_York").tz_localize(None)
        df.index = ts.normalize()
        df = df[~df.index.duplicated(keep="last")].sort_index()
        close = pd.to_numeric(df["close"], errors="coerce").where(lambda s: s > 0)
        closes[symbol] = close
        highs[symbol] = pd.to_numeric(df.get("high", df["close"]), errors="coerce").where(lambda s: s > 0)
        vol = pd.to_numeric(df.get("volume", pd.Series(0.0, index=df.index)), errors="coerce").fillna(0.0)
        dvols[symbol] = close * vol
    close = pd.DataFrame(closes).sort_index()
    return {
        "close": close,
        "high": pd.DataFrame(highs).reindex(close.index),
        "dollar_volume": pd.DataFrame(dvols).reindex(close.index),
    }


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Last trading day of every month present in ``index``."""
    if len(index) == 0:
        return []
    s = pd.Series(index, index=index)
    return list(s.groupby([index.year, index.month]).max())


# ---------------------------------------------------------------- scoring
def score_on(
    date: pd.Timestamp,
    panels: Mapping[str, pd.DataFrame],
    sectors: Mapping[str, str],
    added: Mapping[str, pd.Timestamp] | None = None,
    config: PickConfig = PickConfig(),
    exclude: Iterable[str] = (),
) -> pd.DataFrame:
    """Score every eligible symbol using ONLY data up to and including ``date``.

    Returns one row per eligible symbol, sorted best first, with the raw
    factor values, their percentile scores (``p_*``), ``score`` and ``rank``.
    """
    close_all = panels["close"]
    pos = close_all.index.get_indexer([date])[0]
    if pos < 0:
        raise KeyError(f"{date} is not a trading day in the panel")
    if pos < YEAR:
        return pd.DataFrame(columns=["symbol", "score", "rank"]).set_index("symbol")
    window = slice(max(0, pos - YEAR + 1), pos + 1)
    close = close_all.iloc[: pos + 1]
    excluded = set(exclude)

    rows = []
    for symbol in close.columns:
        if symbol in excluded:
            continue
        series = close[symbol]
        if pd.isna(series.iloc[-1]):
            continue
        if series.iloc[max(0, pos - config.min_history + 1):].notna().sum() < config.min_history:
            continue
        if added and symbol in added and pd.notna(added[symbol]) and added[symbol] > date:
            continue
        last = float(series.iloc[-1])
        if last < config.min_price:
            continue
        dvol = panels["dollar_volume"][symbol].iloc[max(0, pos - 19): pos + 1].mean()
        if not np.isfinite(dvol) or dvol < config.min_dollar_volume:
            continue
        c = series.values
        c_skip, c_year, c_half, c_q = c[pos - SKIP], c[pos - YEAR], c[pos - HALF], c[pos - QUARTER]
        if not all(np.isfinite(v) and v > 0 for v in (c_skip, c_year, c_half, c_q)):
            continue
        high_52 = panels["high"][symbol].iloc[window].max()
        sma50 = series.iloc[pos - 49: pos + 1].mean()
        sma200 = series.iloc[pos - 199: pos + 1].mean()
        rets = series.iloc[pos - QUARTER: pos + 1].pct_change().dropna()
        rows.append({
            "symbol": symbol,
            "sector": sectors.get(symbol, "Unknown"),
            "price": last,
            "mom12_1": c_skip / c_year - 1.0,
            "mom6_1": c_skip / c_half - 1.0,
            "near_high": last / high_52 if high_52 and high_52 > 0 else np.nan,
            "trend": 0.5 * float(last > sma200) + 0.5 * float(sma50 > sma200),
            "vol63": float(rets.std() * np.sqrt(YEAR)) if len(rets) > 10 else np.nan,
            "ret63": last / c_q - 1.0,
        })
    if not rows:
        return pd.DataFrame(columns=["symbol", "score", "rank"]).set_index("symbol")

    df = pd.DataFrame(rows).set_index("symbol")
    sector_ret = df.groupby("sector")["ret63"].median()
    df["sector_ret63"] = df["sector"].map(sector_ret)
    df["sector_rank"] = df["sector"].map(sector_ret.rank(ascending=False, method="min")).astype(int)
    df["sector_count"] = int(sector_ret.size)

    pct = lambda s: s.rank(pct=True, method="average").fillna(0.5)  # noqa: E731
    df["p_mom12_1"] = pct(df["mom12_1"])
    df["p_mom6_1"] = pct(df["mom6_1"])
    df["p_near_high"] = pct(df["near_high"])
    df["p_trend"] = df["trend"]
    df["p_calm"] = pct(-df["vol63"])
    df["p_sector"] = pct(df["sector_ret63"])

    weights = dict(config.weights)
    total = sum(weights.values()) or 1.0
    df["score"] = sum(df[f"p_{k}"] * w for k, w in weights.items()) / total
    df = df.sort_values(["score", "mom12_1"], ascending=False)
    df["rank"] = np.arange(1, len(df) + 1)
    return df


def select(previous: list[str], scored: pd.DataFrame, config: PickConfig = PickConfig()) -> list[str]:
    """Top-N with a hold buffer: keep a current holding while rank <= keep_rank."""
    if scored.empty:
        return []
    ranks = scored["rank"]
    kept = [s for s in previous if s in ranks.index and ranks[s] <= config.keep_rank]
    kept = sorted(kept, key=lambda s: ranks[s])[: config.top_n]
    for symbol in scored.index:
        if len(kept) >= config.top_n:
            break
        if symbol not in kept:
            kept.append(symbol)
    return sorted(kept, key=lambda s: ranks[s])


# -------------------------------------------------------------- rationale
def _pct(v: float) -> str:
    return f"{v * 100:+.0f}%"


def add_reason(row: pd.Series) -> str:
    """Plain-English 'why it is in' from the numbers, strongest factors first."""
    strengths = sorted(
        ((row[f"p_{k}"] * w, k) for k, w in WEIGHTS.items()), reverse=True
    )[:3]
    parts = []
    for _, k in strengths:
        if k == "mom12_1":
            parts.append(f"12-month momentum {_pct(row['mom12_1'])} (top {max(1, round((1 - row['p_mom12_1']) * 100))}%)")
        elif k == "mom6_1":
            parts.append(f"6-month momentum {_pct(row['mom6_1'])}")
        elif k == "near_high":
            gap = 1 - row["near_high"]
            parts.append("at its 52-week high" if gap < 0.005 else f"{gap * 100:.0f}% below its 52-week high")
        elif k == "trend":
            parts.append("above its 50 & 200-day averages" if row["trend"] >= 1 else "trend improving")
        elif k == "calm":
            parts.append(f"steady trend ({row['vol63'] * 100:.0f}% annualised volatility)")
        elif k == "sector":
            parts.append(f"{row['sector']} is #{int(row['sector_rank'])} of {int(row['sector_count'])} sectors over 3 months")
    return f"Rank #{int(row['rank'])}: " + "; ".join(parts)


def drop_reason(symbol: str, scored: pd.DataFrame, config: PickConfig = PickConfig()) -> str:
    if symbol not in scored.index:
        return "No longer eligible (price, liquidity or data filter)"
    row = scored.loc[symbol]
    weakest = min(WEIGHTS, key=lambda k: row[f"p_{k}"])
    return (
        f"Rank fell to #{int(row['rank'])} (keeps only while in top {config.keep_rank}); "
        f"weakest: {FACTOR_LABELS[weakest]}"
        + (f" ({_pct(row[weakest])})" if weakest in ("mom12_1", "mom6_1") else "")
    )


# --------------------------------------------------------------- backtest
def _period_return(close: pd.DataFrame, symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> float:
    """Close-to-close return; if the stock stops trading mid-period, its last close is the exit."""
    series = close[symbol].loc[start:end].dropna()
    if series.empty or series.index[0] != start:
        return 0.0
    return float(series.iloc[-1] / series.iloc[0] - 1.0)


def _max_drawdown(equity: pd.Series) -> float:
    peak = equity.cummax()
    return float((equity / peak - 1.0).min()) if len(equity) else 0.0


def _stats(monthly: pd.Series) -> dict:
    monthly = monthly.dropna()
    if monthly.empty:
        return {}
    equity = (1 + monthly).cumprod()
    years = len(monthly) / 12.0
    total = float(equity.iloc[-1] - 1.0)
    cagr = float(equity.iloc[-1] ** (1 / years) - 1.0) if years > 0 else 0.0
    vol = float(monthly.std() * np.sqrt(12)) if len(monthly) > 1 else 0.0
    return {
        "months": int(len(monthly)),
        "totalReturn": round(total, 4),
        "cagr": round(cagr, 4),
        "volatility": round(vol, 4),
        "sharpe": round(float(monthly.mean() * 12 / vol), 2) if vol > 0 else None,
        "maxDrawdown": round(_max_drawdown(pd.concat([pd.Series([1.0]), equity.reset_index(drop=True)])), 4),
        "bestMonth": round(float(monthly.max()), 4),
        "worstMonth": round(float(monthly.min()), 4),
        "pctMonthsUp": round(float((monthly > 0).mean()), 3),
    }


def backtest(
    panels: Mapping[str, pd.DataFrame],
    sectors: Mapping[str, str],
    added: Mapping[str, pd.Timestamp] | None = None,
    benchmark: str = "SPY",
    config: PickConfig = PickConfig(),
    start: pd.Timestamp | None = None,
) -> dict:
    """Walk forward month by month. Each rebalance only sees data up to its own close."""
    close = panels["close"]
    ends = [d for d in month_end_dates(close.index) if start is None or d >= start]
    holdings: list[str] = []
    months, trades, positions = [], [], []
    for i in range(len(ends) - 1):
        t0, t1 = ends[i], ends[i + 1]
        if close.index.get_indexer([t0])[0] < YEAR:
            continue
        scored = score_on(t0, panels, sectors, added, config, exclude=[benchmark])
        if len(scored) < config.top_n:
            continue
        new = select(holdings, scored, config)
        adds = [s for s in new if s not in holdings]
        drops = [s for s in holdings if s not in new]
        swapped = len(adds) if holdings else len(new)
        cost = (2 if holdings else 1) * swapped / config.top_n * config.cost_bps / 1e4
        rets = {s: _period_return(close, s, t0, t1) for s in new}
        pick_ret = float(np.mean(list(rets.values()))) - cost
        universe_ret = float(np.mean([_period_return(close, s, t0, t1) for s in scored.index]))
        bench_ret = _period_return(close, benchmark, t0, t1) if benchmark in close.columns else np.nan
        months.append({
            "date": t1.strftime("%Y-%m-%d"),
            "picks": pick_ret,
            "universe": universe_ret,
            "benchmark": bench_ret,
            "swapped": len(adds),
            "eligible": int(len(scored)),
        })
        for s in adds:
            trades.append({"date": t0.strftime("%Y-%m-%d"), "action": "add", "symbol": s, "reason": add_reason(scored.loc[s])})
        for s in drops:
            trades.append({"date": t0.strftime("%Y-%m-%d"), "action": "drop", "symbol": s, "reason": drop_reason(s, scored, config)})
        for s, r in rets.items():
            positions.append({"month": t1.strftime("%Y-%m"), "symbol": s, "return": round(r, 4)})
        holdings = new

    if not months:
        return {"config": _config_dict(config), "months": [], "error": "not enough history to run a single month"}

    frame = pd.DataFrame(months).set_index("date")
    frame.index = pd.to_datetime(frame.index)
    yearly = frame[["picks", "universe", "benchmark"]].groupby(frame.index.year).apply(lambda g: (1 + g).prod() - 1)
    pos = pd.DataFrame(positions)
    beat = frame["picks"] > frame["benchmark"]
    return {
        "config": _config_dict(config),
        "start": frame.index[0].strftime("%Y-%m"),
        "end": frame.index[-1].strftime("%Y-%m"),
        "stats": {
            "picks": _stats(frame["picks"]),
            "universe": _stats(frame["universe"]),
            "benchmark": _stats(frame["benchmark"]),
        },
        "pctMonthsBeatBenchmark": round(float(beat[frame["benchmark"].notna()].mean()), 3),
        "avgSwapsPerMonth": round(float(frame["swapped"].iloc[1:].mean()), 2) if len(frame) > 1 else None,
        "pickHitRate": round(float((pos["return"] > 0).mean()), 3) if len(pos) else None,
        "yearly": {int(y): {k: round(float(v), 4) for k, v in row.items()} for y, row in yearly.iterrows()},
        "bestPositions": pos.nlargest(10, "return").to_dict("records") if len(pos) else [],
        "worstPositions": pos.nsmallest(10, "return").to_dict("records") if len(pos) else [],
        "months": [
            {"date": d.strftime("%Y-%m-%d"), **{k: (round(float(v), 4) if isinstance(v, float) else v) for k, v in r.items()}}
            for d, r in frame.iterrows()
        ],
        "trades": trades,
        "finalHoldings": holdings,
    }


def current_picks(
    panels: Mapping[str, pd.DataFrame],
    sectors: Mapping[str, str],
    added: Mapping[str, pd.Timestamp] | None = None,
    previous: list[str] | None = None,
    benchmark: str = "SPY",
    config: PickConfig = PickConfig(),
    as_of: pd.Timestamp | None = None,
) -> dict:
    """Picks as of the latest close (or ``as_of``) with an add/keep/drop reason for each."""
    close = panels["close"]
    date = as_of if as_of is not None else close.index[-1]
    scored = score_on(date, panels, sectors, added, config, exclude=[benchmark])
    previous = list(previous or [])
    picks = select(previous, scored, config)
    out = []
    for s in picks:
        row = scored.loc[s]
        out.append({
            "symbol": s,
            "rank": int(row["rank"]),
            "score": round(float(row["score"]), 3),
            "sector": row["sector"],
            "price": round(float(row["price"]), 2),
            "mom12_1": round(float(row["mom12_1"]), 4),
            "mom6_1": round(float(row["mom6_1"]), 4),
            "pctBelowHigh": round(float(1 - row["near_high"]), 4),
            "status": "keep" if s in previous else ("new" if previous else "pick"),
            "reason": add_reason(row),
        })
    drops = [{"symbol": s, "reason": drop_reason(s, scored, config)} for s in previous if s not in picks]
    return {
        "asOf": pd.Timestamp(date).strftime("%Y-%m-%d"),
        "eligible": int(len(scored)),
        "picks": out,
        "dropped": drops,
        "config": _config_dict(config),
    }


def _config_dict(config: PickConfig) -> dict:
    d = asdict(config)
    d["weights"] = dict(config.weights)
    return d
