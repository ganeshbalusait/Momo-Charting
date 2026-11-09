"""AI Picks scoring and portfolio rules: plain, fixed-in-advance, no tuning.

Four sub-scores, each the average of 0-100 percentile ranks across that month's
universe; the total is the plain average of the four.
"""
from __future__ import annotations

import math
import re

import pandas as pd

TOP_N = 20
KEEP_RANK = 40
MIN_PRICE = 5.0
MIN_ADV = 10_000_000.0
COST = 0.001  # per unit of turnover

# metric -> (sub-score, higher_is_better)
METRICS = {
    "ebitda_ev": ("value", True),
    "earn_yield": ("value", True),
    "fcf_yield": ("value", True),
    "rev_growth": ("growth", True),
    "eps_growth": ("growth", True),
    "op_margin": ("quality", True),
    "net_debt_ebitda": ("quality", False),
    "profitable": ("quality", True),
    "mom_12_1": ("momentum", True),
    "mom_6": ("momentum", True),
}
SUBSCORES = ("value", "growth", "quality", "momentum")

_SUFFIX = re.compile(
    r"\b(common stock|ordinary shares?|class [a-c]|shares of beneficial interest|american depositary shares?|"
    r"depositary shares?|ads|new|com|inc|incorporated|corp|corporation|co|company|ltd|limited|plc|holdings?|"
    r"group|the|lp|llc|n\.?v|s\.?a|ag|se|sa)\b")


def normalize_name(name: str) -> str:
    """'Twitter, Inc. Common Stock' -> 'twitter' (for matching Alpaca names to SEC names)."""
    s = (name or "").lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = _SUFFIX.sub(" ", s)
    return " ".join(s.split())


def _div(a, b):
    if a is None or b is None or b == 0 or (isinstance(b, float) and math.isnan(b)):
        return None
    return a / b


def metrics(snap: dict, mcap: float, mom_12_1: float | None, mom_6: float | None) -> dict:
    """The ten raw numbers for one stock on one rebalance day."""
    ebitda = None
    if snap.get("op_income") is not None:
        ebitda = snap["op_income"] + (snap.get("dna") or 0.0)
    cash = snap.get("cash") or 0.0
    debt = snap.get("debt") or 0.0
    ev = mcap + debt - cash
    fcf = None
    if snap.get("cfo") is not None:
        fcf = snap["cfo"] - abs(snap.get("capex") or 0.0)
    rev, rev_prev = snap.get("revenue"), snap.get("revenue_prev")
    eps, eps_prev = snap.get("eps"), snap.get("eps_prev")
    net_debt_ebitda = None
    if ebitda is not None:
        # losing money on EBITDA is treated as the worst possible leverage
        net_debt_ebitda = (debt - cash) / ebitda if ebitda > 0 else float("inf")
    ni = snap.get("net_income")
    ebit = snap.get("op_income")
    # Greenblatt return on capital: EBIT / (net working capital + net fixed assets)
    capital = None
    if snap.get("ppe") is not None and snap.get("assets_current") is not None and snap.get("liab_current") is not None:
        capital = max(snap["assets_current"] - snap["liab_current"], 0.0) + snap["ppe"]
    years = snap.get("ni_years") or []
    equity = snap.get("equity")
    extra = {
        "roc": _div(ebit, capital) if capital and capital > 0 else None,
        "ebit_ev": _div(ebit, ev) if ev > 0 else None,
        "roe_5y": snap.get("roe_5y"),
        "debt_equity": _div(debt, equity) if equity and equity > 0 else None,
        "profit_years": sum(1 for v in years[:5] if v > 0) if len(years) >= 5 else None,
        "ni_growth_5y": ((years[0] / years[4]) ** 0.25 - 1) if len(years) >= 5 and years[0] > 0 and years[4] > 0 else None,
    }
    return extra | {
        "ebitda_ev": _div(ebitda, ev) if ev > 0 else None,
        "earn_yield": _div(ni, mcap),
        "fcf_yield": _div(fcf, mcap),
        "rev_growth": _div(rev - rev_prev, abs(rev_prev)) if rev is not None and rev_prev else None,
        "eps_growth": _div(eps - eps_prev, abs(eps_prev)) if eps is not None and eps_prev else None,
        "op_margin": _div(snap.get("op_income"), rev) if rev and rev > 0 else None,
        "net_debt_ebitda": net_debt_ebitda,
        "profitable": None if ni is None else float(ni > 0),
        "mom_12_1": mom_12_1,
        "mom_6": mom_6,
    }


def score(frame: pd.DataFrame) -> pd.DataFrame:
    """Add value/growth/quality/momentum/total (0-100) to a one-month universe frame."""
    out = frame.copy()
    parts: dict[str, list[pd.Series]] = {s: [] for s in SUBSCORES}
    for metric, (sub, higher) in METRICS.items():
        col = pd.to_numeric(out[metric], errors="coerce")
        ranks = col.rank(pct=True, ascending=higher) * 100.0
        parts[sub].append(ranks)
    for sub in SUBSCORES:
        out[sub] = pd.concat(parts[sub], axis=1).mean(axis=1, skipna=True)
    have = out[list(SUBSCORES)].notna().sum(axis=1)
    out["total"] = out[list(SUBSCORES)].mean(axis=1, skipna=True).where(have >= 3)
    return out


def _rank(col: pd.Series, higher: bool = True) -> pd.Series:
    return pd.to_numeric(col, errors="coerce").rank(pct=True, ascending=higher) * 100.0


def score_buffett(frame: pd.DataFrame) -> pd.DataFrame:
    """Buffett-style 'wonderful business at a fair price' (rules fixed before testing):
    only companies worth $2B+ that made a profit in each of the last 5 years with
    positive equity; then the average rank of 5-year return on equity, return on
    capital, low debt/equity, 5-year profit growth and EBIT/EV (price)."""
    out = frame.copy()
    ok = (out["mcap"] >= 2e9) & (out["profit_years"] == 5) & out["debt_equity"].notna()
    ranks = pd.concat([_rank(out["roe_5y"].where(ok)), _rank(out["roc"].where(ok)),
                       _rank(out["debt_equity"].where(ok), higher=False), _rank(out["ni_growth_5y"].where(ok)),
                       _rank(out["ebit_ev"].where(ok))], axis=1)
    out["buffett"] = ranks.mean(axis=1).where(ok & (ranks.notna().sum(axis=1) >= 4))
    return out


def score_magic(frame: pd.DataFrame) -> pd.DataFrame:
    """Joel Greenblatt's Magic Formula: rank return on capital + rank EBIT/EV, $1B+ companies."""
    out = frame.copy()
    ok = (out["mcap"] >= 1e9) & out["roc"].notna() & out["ebit_ev"].notna() & (out["ebit_ev"] > 0)
    out["magic"] = ((_rank(out["roc"].where(ok)) + _rank(out["ebit_ev"].where(ok))) / 2).where(ok)
    return out


def pick(scored: pd.DataFrame, held: list[str], top_n: int = TOP_N, keep_rank: int = KEEP_RANK,
         by: str = "total") -> list[str]:
    """Keep holdings still ranked within ``keep_rank``; fill the rest with the best new names."""
    ranked = scored.dropna(subset=[by]).sort_values([by, "symbol"], ascending=[False, True])
    order = list(ranked["symbol"])
    position = {s: i for i, s in enumerate(order)}
    kept = [s for s in held if s in position and position[s] < keep_rank]
    kept = sorted(kept, key=lambda s: position[s])[:top_n]
    for s in order:
        if len(kept) >= top_n:
            break
        if s not in kept:
            kept.append(s)
    return kept


def holding_return(prices: pd.Series, t0, t1) -> float | None:
    """Return from close t0 to close t1 for one stock (total-return prices).

    If the stock stops trading before t1 it is a delisting: after a fall of more than
    half in the prior 3 months it is counted as a total loss (failures rarely recover
    anything), otherwise it is sold at the last price (typically a buyout).
    """
    s = prices.dropna()
    s = s[(s.index >= t0) & (s.index <= t1)]
    if s.empty or s.index[0] != t0:
        return None
    last_day = s.index[-1]
    p0, p_last = float(s.iloc[0]), float(s.iloc[-1])
    if (t1 - last_day).days > 7:
        window = prices.dropna()
        window = window[window.index <= last_day].iloc[-64:]
        if len(window) > 1 and p_last < 0.5 * float(window.iloc[0]):
            return -1.0
    return p_last / p0 - 1.0


def turnover(old: dict[str, float], new: dict[str, float]) -> float:
    names = set(old) | set(new)
    return sum(abs(new.get(s, 0.0) - old.get(s, 0.0)) for s in names)


def drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def cagr(monthly: pd.Series) -> float:
    if monthly.empty:
        return float("nan")
    growth = float((1.0 + monthly).prod())
    years = len(monthly) / 12.0
    return growth ** (1.0 / years) - 1.0 if growth > 0 else -1.0
