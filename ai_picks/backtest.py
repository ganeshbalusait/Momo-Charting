"""AI Picks back-test: monthly top-20 by four fixed scores vs SPY, 2017 onward.

Run off-hours after download_prices and sec_facts:
    python -m ai_picks.backtest
Writes artifacts/ai_picks_data/backtest_*.parquet and artifacts/ai-picks-backtest.md.
"""
from __future__ import annotations

import bisect
import json
import re
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from ai_picks import model, pit
from momx import etf_list

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "artifacts" / "ai_picks_data"
REPORT = ROOT / "artifacts" / "ai-picks-backtest.md"
FIRST_MONTH = "2017-01"
GAP_DAYS = 45  # a pause in trading longer than this splits a ticker into separate listings
# delisted ETFs/funds are not in today's ETF directory, so their names are checked too
FUND_NAME = re.compile(r"\b(etf|etn|ishares|spdr|proshares|direxion|fund|commodity pool)\b", re.I)


# ---------------------------------------------------------------- prices
def load_prices():
    def wide(adj):
        bars = pd.read_parquet(DATA / f"bars_{adj}.parquet")
        return bars, bars.pivot(index="date", columns="symbol", values="close").sort_index()

    bars_raw, raw = wide("raw")
    _, adj = wide("all")
    vol = bars_raw.pivot(index="date", columns="symbol", values="volume").reindex_like(raw)
    adj = adj.reindex(index=raw.index, columns=raw.columns)
    return adj, raw, vol


def segments(series: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    days = series.dropna().index
    if days.empty:
        return []
    out, start = [], days[0]
    for prev, cur in zip(days[:-1], days[1:]):
        if (cur - prev).days > GAP_DAYS:
            out.append((start, prev))
            start = cur
    out.append((start, days[-1]))
    return out


# ---------------------------------------------------------------- tickers <-> SEC companies
def build_mapping(adj: pd.DataFrame, facts: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    assets = json.loads((DATA / "assets.json").read_text(encoding="utf-8"))
    sec = json.loads((DATA / "company_tickers.json").read_text(encoding="utf-8"))
    ticker_cik = {v["ticker"].upper().replace("-", "."): int(v["cik_str"]) for v in sec.values()}
    entities = pd.read_parquet(DATA / "entities.parquet")
    by_name = defaultdict(set)
    for cik, name in zip(entities["cik"], entities["name"]):
        key = model.normalize_name(name)
        if key:
            by_name[key].add(int(cik))
    filed = facts.groupby("cik")["filed"].agg(["min", "max"])
    status = {a["symbol"]: a for a in assets}

    rows, how = [], defaultdict(int)
    for sym in adj.columns:
        a = status.get(sym)
        if a is None:
            continue
        if etf_list.is_etf(sym) or FUND_NAME.search(a["name"] or ""):
            how["etf_or_fund"] += 1
            continue
        cik, source = None, None
        if a["status"] == "active" and sym in ticker_cik:
            cik, source = ticker_cik[sym], "sec_ticker"
        else:
            hits = by_name.get(model.normalize_name(a["name"]), set())
            if len(hits) == 1:
                cik, source = next(iter(hits)), "name"
        if cik is None or cik not in filed.index:
            how["unmatched"] += 1
            continue
        lo = filed.loc[cik, "min"] - pd.Timedelta(days=30)
        hi = filed.loc[cik, "max"] + pd.Timedelta(days=400)
        segs = segments(adj[sym])
        if source == "sec_ticker":
            segs = segs[-1:]  # a reused ticker's older listing belongs to another company
        segs = [(s, e) for s, e in segs if e >= lo and s <= hi]
        if not segs:
            how["no_overlap"] += 1
            continue
        valid_days = int(sum(adj[sym].loc[s:e].notna().sum() for s, e in segs))
        rows.append({"symbol": sym, "cik": cik, "source": source, "status": a["status"], "segments": segs,
                     "valid_days": valid_days})
        how[source] += 1
    mapping = pd.DataFrame(rows)
    # one listing per company: the SEC's own ticker, then a plain ticker over a
    # dotted one (preferreds/units), then the longest history
    mapping["pref"] = [(0 if src == "sec_ticker" else 1) + (0 if "." not in s else 2)
                       for src, s in zip(mapping["source"], mapping["symbol"])]
    mapping = mapping.sort_values(["cik", "pref", "valid_days"], ascending=[True, True, False])
    how["share_class_dupes"] = int(mapping.duplicated("cik").sum())
    mapping = mapping.drop_duplicates("cik")
    return mapping, dict(how)


def mask_to_segments(frame: pd.DataFrame, mapping: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(np.nan, index=frame.index, columns=list(mapping["symbol"]))
    for sym, segs in zip(mapping["symbol"], mapping["segments"]):
        for s, e in segs:
            out.loc[s:e, sym] = frame.loc[s:e, sym]
    return out


# ---------------------------------------------------------------- finances
def _company(args):
    cik, rows = args
    concepts = defaultdict(list)
    for concept, rank, start, end, val, filed in rows:
        concepts[concept].append((rank, start, end, val, filed))
    snaps = pit.company_snapshots(concepts)
    return cik, [d for d, _ in snaps], [s for _, s in snaps]


def build_snapshots(facts: pd.DataFrame, ciks: set[int]) -> dict[int, tuple[list, list]]:
    f = facts[facts["cik"].isin(ciks) & (facts["end"] >= "2013-01-01")].copy()
    f = f.dropna(subset=["end", "filed"])
    for col in ("start", "end", "filed"):
        f[col] = [None if pd.isna(x) else x.date() for x in f[col]]
    jobs = [(int(cik), list(zip(g["concept"], g["rank"], g["start"], g["end"], g["val"], g["filed"])))
            for cik, g in f.groupby("cik")]
    out = {}
    with ProcessPoolExecutor(max_workers=6) as pool:
        for i, (cik, days, snaps) in enumerate(pool.map(_company, jobs, chunksize=20)):
            out[cik] = (days, snaps)
            if i % 1000 == 0:
                print(f"snapshots {i}/{len(jobs)}", flush=True)
    return out


def snapshot_asof(snaps, day: date):
    days, values = snaps
    i = bisect.bisect_right(days, day) - 1
    return values[i] if i >= 0 else None


# ---------------------------------------------------------------- monthly loop
def rebalance_days(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    s = pd.Series(index, index=index)
    firsts = s.groupby([index.year, index.month]).first()
    return [d for d in firsts if d.strftime("%Y-%m") >= FIRST_MONTH]


def universe(day_prev, adj, raw, vol, mapping, snaps) -> pd.DataFrame:
    i = adj.index.get_loc(day_prev)
    if i < 253:
        return pd.DataFrame()
    win_raw = raw.iloc[i - 19:i + 1]
    adv = (win_raw * vol.iloc[i - 19:i + 1]).mean()
    p_now = adj.iloc[i]
    p_21, p_252, p_126 = adj.iloc[i - 21], adj.iloc[i - 252], adj.iloc[i - 126]
    rows = []
    d = day_prev.date()
    for sym, cik in zip(mapping["symbol"], mapping["cik"]):
        price = raw.iat[i, raw.columns.get_loc(sym)]
        if not np.isfinite(price) or price < model.MIN_PRICE or not adv.get(sym, 0) >= model.MIN_ADV:
            continue
        snap = snapshot_asof(snaps.get(cik, ([], [])), d)
        if snap is None or not (snap.get("revenue") or 0) > 0 or not snap.get("shares"):
            continue
        sd = pd.Timestamp(snap["shares_date"])
        col_adj, col_raw = adj[sym], raw[sym]
        before = col_raw.loc[:sd].dropna()
        if before.empty:
            continue
        base_day = before.index[-1]
        if (sd - base_day).days > 10 or not np.isfinite(col_adj.get(base_day, np.nan)):
            continue
        mcap = snap["shares"] * before.iloc[-1] * (p_now[sym] / col_adj[base_day])
        if not mcap > 0:
            continue
        mom12 = p_21[sym] / p_252[sym] - 1 if p_252[sym] > 0 else None
        mom6 = p_now[sym] / p_126[sym] - 1 if p_126[sym] > 0 else None
        m = model.metrics(snap, mcap, mom12, mom6)
        m.update({"symbol": sym, "cik": cik, "price": price, "mcap": mcap, "adv": adv[sym],
                  "period_end": snap["period_end"]})
        rows.append(m)
    return pd.DataFrame(rows)


def period_returns(adj: pd.DataFrame, symbols: list[str], t0, t1) -> pd.Series:
    win = adj.loc[t0:t1, symbols]
    p0 = win.iloc[0]
    out = win.ffill().iloc[-1] / p0 - 1.0
    last_day = win.notna()[::-1].idxmax()
    for sym in last_day.index[(t1 - last_day).dt.days > 7]:
        out[sym] = model.holding_return(adj[sym], t0, t1)
    return out[p0.notna()]


STRATEGIES = ("total", *model.SUBSCORES, "buffett", "magic", "billionaires", "berkshire")
FUNDAMENTAL = ("total", *model.SUBSCORES, "buffett", "magic")
BERKSHIRE = "Warren Buffett (Berkshire Hathaway)"
MAX_13F_AGE = 200  # days after quarter end; older means the manager stopped filing


class ThirteenF:
    """The billionaires' latest public 13F portfolios on any given day."""

    def __init__(self, columns, names: dict[str, str]):
        path = DATA / "holdings_13f.parquet"
        self.ok = path.exists()
        if not self.ok:
            return
        h = pd.read_parquet(path)
        # foreign-registered (Chubb, Aon, Spotify) and delisted (Activision) securities have no
        # OpenFIGI US ticker: match the 13F issuer name to Alpaca's company names instead
        by_name: dict[str, set[str]] = defaultdict(set)
        for sym, name in names.items():
            if "." not in sym and sym in set(columns):
                by_name[model.normalize_name(name)].add(sym)
        missing = h["symbol"].isna()
        guess = h.loc[missing, "issuer"].map(lambda n: by_name.get(model.normalize_name(n), set()))
        h.loc[missing, "symbol"] = guess.map(lambda s: next(iter(s)) if len(s) == 1 else None)
        self.mapped_share = float(h["symbol"].notna().mean())
        h = h.dropna(subset=["symbol"])
        h["manager"] = h["manager"].str.replace(", older filer", "", regex=False)
        h["filed"] = pd.to_datetime(h["filed"])
        h["period"] = pd.to_datetime(h["period"])
        bad = {s for s in h["symbol"].unique() if etf_list.is_etf(s) or FUND_NAME.search(names.get(s, ""))}
        self.h = h[~h["symbol"].isin(bad) & h["symbol"].isin(set(columns))]
        self.filings = {m: sorted(set(zip(g["filed"], g["period"]))) for m, g in self.h.groupby("manager")}

    def portfolios(self, day) -> dict[str, pd.Series]:
        out = {}
        for manager, fl in self.filings.items():
            seen = [(f, p) for f, p in fl if f <= day]
            if not seen:
                continue
            period = max(p for _, p in seen)
            if (day - period).days > MAX_13F_AGE:
                continue
            filed = max(f for f, p in seen if p == period)
            g = self.h[(self.h["manager"] == manager) & (self.h["period"] == period) & (self.h["filed"] <= filed)]
            g = g[g["filed"] == g["filed"].max()]
            w = g.groupby("symbol")["value"].sum()
            w = w[w > 0]
            if w.sum() > 0:
                out[manager] = w / w.sum()
        return out


def _liquid(symbols, i, raw, vol) -> list[str]:
    syms = [s for s in symbols if s in raw.columns]
    price = raw.iloc[i][syms]
    adv = (raw.iloc[i - 19:i + 1][syms] * vol.iloc[i - 19:i + 1][syms]).mean()
    return [s for s in syms if price[s] >= model.MIN_PRICE and adv[s] >= model.MIN_ADV]


def targets(key, scored, ports, held, i, raw, vol) -> dict[str, float]:
    if key == "berkshire":
        port = ports.get(BERKSHIRE)
        if port is None:
            return {}
        keep = _liquid(port.index, i, raw, vol)
        w = port[keep]
        return (w / w.sum()).to_dict() if w.sum() > 0 else {}
    if key == "billionaires":
        if not ports:
            return {}
        conviction = pd.concat(ports.values(), axis=1).fillna(0.0).sum(axis=1)
        conviction = conviction[_liquid(conviction.index, i, raw, vol)]
        frame = pd.DataFrame({"symbol": conviction.index, "billionaires": conviction.values})
        picks = model.pick(frame, held, by="billionaires")
    else:
        if key not in scored or scored[key].notna().sum() < model.TOP_N:
            return {}
        picks = model.pick(scored, held, by=key)
    return {s: 1.0 / len(picks) for s in picks}


def run() -> dict:
    print("loading prices", flush=True)
    adj, raw, vol = load_prices()
    facts = pd.read_parquet(DATA / "facts.parquet")
    mapping, how = build_mapping(adj, facts)
    print("mapping", how, flush=True)
    adj_m = mask_to_segments(adj, mapping)
    raw_m = mask_to_segments(raw, mapping)
    vol_m = vol.reindex(columns=list(mapping["symbol"]))
    snaps = build_snapshots(facts, set(mapping["cik"]))
    names = {a["symbol"]: a["name"] for a in json.loads((DATA / "assets.json").read_text(encoding="utf-8"))}
    tf = ThirteenF(adj.columns, names)

    def score_all(uni):
        return model.score_magic(model.score_buffett(model.score(uni)))

    days = rebalance_days(adj.index)
    books = {k: {} for k in STRATEGIES}
    held = {k: [] for k in STRATEGIES}
    monthly, pick_log = [], []
    spy = adj["SPY"]
    for t0, t1 in zip(days[:-1], days[1:]):
        i_prev = adj.index.get_loc(t0) - 1
        day_prev = adj.index[i_prev]
        uni = universe(day_prev, adj_m, raw_m, vol_m, mapping, snaps)
        if uni.empty:
            continue
        scored = score_all(uni)
        ports = tf.portfolios(day_prev) if tf.ok else {}
        uni_ret = period_returns(adj_m, list(scored["symbol"]), t0, t1)
        row = {"month": t0, "spy": spy[t1] / spy[t0] - 1, "universe_ew": float(uni_ret.mean()),
               "universe_n": len(scored), "managers": len(ports)}
        for key in STRATEGIES:
            target = targets(key, scored, ports, held[key], i_prev, raw, vol)
            if not target:
                row[key] = np.nan
                continue
            prices = adj_m if key in FUNDAMENTAL else adj
            rets = period_returns(prices, list(target), t0, t1).dropna()
            w = {s: target[s] for s in rets.index}
            tot = sum(w.values())
            w = {s: v / tot for s, v in w.items()}
            turn = model.turnover(books[key], w)
            row[key] = float(sum(w[s] * rets[s] for s in w)) - turn * model.COST
            row[key + "_turnover"] = turn
            grown = {s: w[s] * (1 + rets[s]) for s in w}
            g = sum(grown.values())
            books[key] = {s: v / g for s, v in grown.items() if v > 0} if g > 0 else {}
            held[key] = [s for s in target if s in books[key]]
            for sym in w:
                pick_log.append({"strategy": key, "month": t0, "symbol": sym, "weight": w[sym],
                                 "return": rets[sym]})
        monthly.append(row)
        print(t0.date(), " ".join(f"{k}={row.get(k, np.nan):+.3f}" for k in ("total", "buffett", "magic",
                                                                              "billionaires", "berkshire", "spy")),
              flush=True)

    last = adj.index[-1]
    i_last = len(adj.index) - 1
    now = score_all(universe(last, adj_m, raw_m, vol_m, mapping, snaps))
    now.to_parquet(DATA / "current_universe.parquet", index=False)
    ports_now = tf.portfolios(last) if tf.ok else {}
    current = {}
    for key in ("total", "buffett", "magic", "billionaires", "berkshire"):
        target = targets(key, now, ports_now, held[key], i_last, raw, vol)
        current[key] = sorted(target.items(), key=lambda kv: -kv[1]) if key == "berkshire" else list(target)
    if ports_now:
        conviction = pd.concat(ports_now.values(), axis=1).fillna(0.0)
        holders = {s: [m for m in ports_now if ports_now[m].get(s, 0) > 0] for s in current["billionaires"]}
        current["billionaire_holders"] = holders
        current["billionaire_conviction"] = conviction.sum(axis=1).to_dict()

    result = pd.DataFrame(monthly).set_index("month")
    result.to_parquet(DATA / "backtest_monthly.parquet")
    pd.DataFrame(pick_log).to_parquet(DATA / "backtest_picks.parquet", index=False)
    (DATA / "current_picks.json").write_text(json.dumps(current, default=str), encoding="utf-8")
    return {"monthly": result, "current": current, "now": now, "how": how, "last_day": last,
            "managers": sorted(tf.filings) if tf.ok else []}


# ---------------------------------------------------------------- report
def stats(r: pd.Series, spy: pd.Series) -> dict:
    both = pd.concat([r, spy], axis=1).dropna()
    r, spy = both.iloc[:, 0], both.iloc[:, 1]
    eq = (1 + r).cumprod()
    excess = r - spy
    t = float(excess.mean() / (excess.std(ddof=1) / np.sqrt(len(excess)))) if len(excess) > 2 else float("nan")
    return {"cagr": model.cagr(r), "maxdd": model.drawdown(pd.concat([pd.Series([1.0]), eq])),
            "beat": float((excess > 0).mean()), "excess_m": float(excess.mean()), "t": t, "n": len(r),
            "growth": float(eq.iloc[-1]) if len(eq) else float("nan")}


LABELS = (
    ("total", "**AI Picks (value+growth+quality+momentum)**"),
    ("buffett", "**Buffett-style formula**"),
    ("magic", "**Greenblatt Magic Formula**"),
    ("billionaires", "**Copy the billionaires (top 20 of their 13Fs)**"),
    ("berkshire", "**Copy Berkshire's 13F exactly**"),
    ("value", "Value only"), ("growth", "Growth only"), ("quality", "Quality only"), ("momentum", "Momentum only"),
    ("universe_ew", "All eligible stocks, equal weight"),
    ("spy", "SPY"),
)


def write_report(res: dict) -> str:
    m = res["monthly"]
    periods = {"Whole test": m, "First half 2017-2021": m[m.index.year <= 2021],
               "Second half 2022-2026": m[m.index.year >= 2022]}
    lines = [f"# Stock-picking back-test ({date.today()})", "",
             "Every strategy holds about 20 stocks, rebalanced on the first trading day of each month "
             "(a holding stays until it drops out of the top 40). 0.1% cost per trade. Delisted stocks included. "
             "Only information public the day before each rebalance is used: SEC finances by filing date, "
             "13F holdings by filing date (up to 45 days after quarter end).", "",
             "Billionaires copied: " + "; ".join(res["managers"]) + ".", ""]
    for name, part in periods.items():
        lines += [f"## {name}", "",
                  "| Strategy | $10k became | Yearly return | Worst drop | Months beating SPY | t-stat vs SPY |",
                  "|---|---|---|---|---|---|"]
        for col, label in LABELS:
            if col not in part or part[col].notna().sum() < 6:
                continue
            s = stats(part[col], part["spy"])
            beat = "—" if col == "spy" else f"{s['beat'] * 100:.0f}%"
            tval = "—" if col == "spy" else f"{s['t']:.2f}"
            lines.append(f"| {label} | ${s['growth'] * 10000:,.0f} | {s['cagr'] * 100:+.1f}% | "
                         f"{s['maxdd'] * 100:.1f}% | {beat} | {tval} |")
        lines.append("")
    cols = [c for c, _ in LABELS[:5] if c in m] + ["spy"]
    yearly = m.groupby(m.index.year)[cols].apply(lambda g: (1 + g).prod() - 1)
    lines += ["## Year by year", "", "| Year | " + " | ".join(cols) + " |", "|---" * (len(cols) + 1) + "|"]
    for y, r in yearly.iterrows():
        lines.append(f"| {y} | " + " | ".join(f"{r[c] * 100:+.1f}%" for c in cols) + " |")
    how = res["how"]
    lines += ["", "## Data coverage", "",
              f"- Tickers matched to SEC companies: {how.get('sec_ticker', 0)} by current ticker, "
              f"{how.get('name', 0)} delisted/renamed by company name; {how.get('etf_or_fund', 0)} ETFs/funds removed.",
              f"- Eligible stocks per month: {int(m['universe_n'].min())}-{int(m['universe_n'].max())}.",
              f"- Billionaires with a current 13F per month: {int(m['managers'].min())}-{int(m['managers'].max())}.",
              "", f"## Current lists (scored on the {res['last_day'].date()} close)", ""]
    cur, now = res["current"], res["now"].set_index("symbol")
    for key, label in LABELS[:4]:
        lines.append(f"- {label.strip('*')}: " + ", ".join(cur.get(key) or []))
    lines.append("- Berkshire's 13F: " + ", ".join(f"{s} {w * 100:.0f}%" for s, w in (cur.get("berkshire") or [])[:12]))
    text = "\n".join(lines) + "\n"
    REPORT.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    print(write_report(run()))
