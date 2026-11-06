"""Long Term: sticker price, Buffett value, Safety Buy, payback, the price guard and the signal.

Pure functions over a `Metrics` row (spec sections "Valuation" and "Signals"). The trader acts on
BUY and LEAPS, so every path fails safe: a missing, non-finite or non-positive input never yields a
level, and a row without a Safety Buy is never BUY or LEAPS.

Binding rules:
* g = lower of EPS growth and equity growth; equity n/a -> lower of EPS and FCF growth; either side
  missing -> no g, no sticker. Floored at 0, capped at 15%.
* sticker EPS = min(EPS TTM in USD per listed share, 1.5 x 3-year average EPS x listing factor).
* future P/E = min(2 x g in %, historical annual P/E median), floor 5, cap 40. Never today's P/E,
  so the sticker cannot move with today's price.
* sticker = sticker EPS x (1+g)^10 x future P/E / 1.15^10.
* Buffett value (not for financials) = owner earnings per listed share grown 10 years at
  min(FCF growth, 15%) floored at 0, discounted at 10%, plus a 3% Gordon terminal value.
* fair value = lower of sticker and Buffett (sticker alone for financials or when Buffett is n/a).
  Safety Buy = fair value x (1 - MOS). LEAPS band = 0.8 x sticker (does not move with MOS).
* Previous trading day for the price guard = the last weekday before today's ET date. Market
  holidays are treated as weekdays (a quote from the day before a holiday Monday is "stale" on the
  Tuesday), which errs on the safe side.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from long_term.finnhub import FinnhubExtract
from long_term.metrics import LABELS, Metrics

ET = ZoneInfo("America/New_York")
YEARS = 10
G_CAP = 0.15
HURDLE = 0.15                 # Rule #1 minimum acceptable return
PE_FLOOR, PE_CAP = 5.0, 40.0
EPS_AVG_MULT = 1.5
BUFFETT_DISCOUNT = 0.10
BUFFETT_TERMINAL = 0.03
PAYBACK_CAP = 50
LEAPS_BAND = 0.8
LEAPS_ROIC = 0.20
QUALITY_ROIC = 0.20
BUY_YEARS = 8
DEFAULT_MOS = 50
MOS_OPTIONS = (30, 50)
AVOID_GATES = ("roic_now", "debt", "earning", "history", "sources")
PRICE_REF_TOL = 0.25
FUTURE_SLACK = timedelta(days=1)       # a quote time further ahead than this is garbage
ROUND = 4

BIG5_WORDS = {"roic_consistency": "ROIC consistency", "sales_growth": "sales growth",
              "eps_growth": "EPS growth", "equity_growth": "equity growth", "fcf_growth": "FCF growth"}


@dataclass
class Valuation:
    sticker: float | None = None
    buffett: float | None = None
    fair_value: float | None = None
    g: float | None = None
    g_source: str = ""
    future_pe: float | None = None
    pe_source: str = ""
    sticker_eps: float | None = None
    payback_years: float | None = None
    explain: list = field(default_factory=list)


# ---------- helpers ----------

def _ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pos(v) -> bool:
    return _ok(v) and v > 0


def _money(v: float) -> str:
    a = abs(v)
    if a < 10:
        s = f"{a:,.2f}"
    elif a < 100:
        s = f"{a:,.1f}"
    else:
        s = f"{a:,.0f}"
    return ("-$" if v < 0 else "$") + s


def _pct(v: float | None) -> str:
    return "n/a" if v is None else f"{v * 100:.0f}%"


def _num(v: float) -> str:
    return f"{v:.0f}" if abs(v - round(v)) < 0.05 else f"{v:.1f}"


def _clamp_g(g: float) -> float:
    return min(max(g, 0.0), G_CAP)


# ---------- the formulas ----------

def sticker_price(sticker_eps: float, g: float, pe_hist_median: float | None) -> tuple[float, float]:
    """(sticker, future P/E). g is clamped to [0, 15%]; the P/E never uses today's price."""
    g = _clamp_g(g)
    pe = round(2 * g * 100, 6)
    if _pos(pe_hist_median):              # a median <= 0 or non-finite is no median
        pe = min(pe, pe_hist_median)
    pe = min(max(pe, PE_FLOOR), PE_CAP)
    future_eps = sticker_eps * (1 + g) ** YEARS
    return future_eps * pe / (1 + HURDLE) ** YEARS, pe


def buffett_value(owner_earnings_per_share: float, g_fcf: float, *, discount=BUFFETT_DISCOUNT,
                  terminal=BUFFETT_TERMINAL, years=YEARS) -> float:
    """Owner earnings grown `years` at g_fcf, discounted, plus a Gordon terminal value on year `years`."""
    total, oe = 0.0, owner_earnings_per_share
    for t in range(1, years + 1):
        oe = owner_earnings_per_share * (1 + g_fcf) ** t
        total += oe / (1 + discount) ** t
    return total + (oe * (1 + terminal) / (discount - terminal)) / (1 + discount) ** years


def payback_years(owner_earnings_total: float, g: float, market_cap: float) -> float | None:
    """Whole years of owner earnings (year t = OE x (1+g)^t) needed to add up to the market cap; cap 50."""
    if not _pos(owner_earnings_total) or not _ok(g) or not _pos(market_cap):
        return None
    g = max(g, 0.0)
    total = 0.0
    for t in range(1, PAYBACK_CAP + 1):
        total += owner_earnings_total * (1 + g) ** t
        if total >= market_cap:
            return float(t)
    return float(PAYBACK_CAP)


def fair_value(sticker: float | None, buffett: float | None, financial: bool) -> float | None:
    """Lower of sticker and Buffett; sticker alone for financials or when Buffett is n/a."""
    if not _pos(sticker):
        return None
    if financial or not _pos(buffett):
        return sticker
    return min(sticker, buffett)


def _growth(m: Metrics) -> tuple[float | None, str, str]:
    """(raw g before clamping, source, words)."""
    gr = m.growth or {}
    eps, eq, fcf = gr.get("eps"), gr.get("equity"), gr.get("fcf")
    if not _ok(eps):
        return None, "none", "EPS growth unknown"
    if _ok(eq):
        other, name = eq, "equity"
    elif _ok(fcf) and not m.financial:
        other, name = fcf, "fcf"
    else:
        return None, "none", "equity and FCF growth unknown"
    label = "equity" if name == "equity" else "FCF"
    if eps <= other:
        return eps, "eps", f"lower of EPS {_pct(eps)} and {label} {_pct(other)}"
    return other, name, f"lower of EPS {_pct(eps)} and {label} {_pct(other)}"


def valuation(m: Metrics, fh: FinnhubExtract | None) -> Valuation:
    """Sticker, Buffett value, fair value and payback for one row, with the working in words."""
    v = Valuation()
    explain = v.explain
    lf = m.listing_factor if _pos(m.listing_factor) else None

    # EPS TTM in USD per listed share (Metrics computes it; Finnhub x factor as a fallback)
    eps_ttm = m.eps_ttm_usd if _ok(m.eps_ttm_usd) else None
    if eps_ttm is None and fh is not None and _ok(fh.eps_ttm) and lf is not None:
        eps_ttm = fh.eps_ttm * lf
    avg_usd = m.eps_3yr_avg * lf if _ok(m.eps_3yr_avg) and lf is not None else None
    cands = [x for x in (eps_ttm, EPS_AVG_MULT * avg_usd if avg_usd is not None else None) if x is not None]
    sticker_eps = min(cands) if cands else None
    v.sticker_eps = sticker_eps

    g_raw, g_source, g_words = _growth(m)
    v.g = _clamp_g(g_raw) if g_raw is not None else None
    v.g_source = g_source

    if sticker_eps is None:
        explain.append("No sticker: EPS TTM and 3-year average EPS unavailable")
    elif sticker_eps <= 0:
        explain.append(f"No sticker: sticker EPS {_money(sticker_eps)} is not positive")
    elif v.g is None:
        explain.append(f"No sticker: no growth rate ({g_words})")
    else:
        sticker, pe = sticker_price(sticker_eps, v.g, m.pe_hist_median)
        v.sticker, v.future_pe = sticker, pe
        two_g = round(2 * v.g * 100, 6)
        med = m.pe_hist_median if _pos(m.pe_hist_median) else None
        if med is not None and med < two_g:
            v.pe_source = "historical median"
        else:
            v.pe_source = "2 × g"
        if pe == PE_FLOOR and min(two_g, med if med is not None else two_g) < PE_FLOOR:
            v.pe_source += " (floor 5)"
        elif pe == PE_CAP and min(two_g, med if med is not None else two_g) > PE_CAP:
            v.pe_source += " (cap 40)"
        parts = [f"TTM {_money(eps_ttm)}" if eps_ttm is not None else "TTM n/a",
                 f"3-yr avg {_money(avg_usd)} × 1.5 = {_money(EPS_AVG_MULT * avg_usd)}" if avg_usd is not None else "3-yr avg n/a"]
        future_eps = sticker_eps * (1 + v.g) ** YEARS
        cap_note = " (capped at 15%)" if g_raw > G_CAP else " (floored at 0)" if g_raw < 0 else ""
        explain.append(f"Sticker EPS {_money(sticker_eps)} ({', '.join(parts)}) growing {_pct(v.g)}/yr"
                       f"{cap_note} for 10 years → {_money(future_eps)}; g = {g_words}")
        med_s = f"Historical P/E median {_num(med)}" if med is not None else "Historical P/E unavailable"
        explain.append(f"{med_s} → future P/E {_num(pe)} ({v.pe_source})")
        explain.append(f"Discounted 15%/yr → sticker {_money(sticker)}")

    # Buffett value: owner earnings per listed share (cancels currency and ADR ratio)
    if m.financial:
        explain.append("No Buffett value for a financial company (sticker only)")
    elif not _pos(m.net_income_ttm) or not _pos(m.owner_earnings_ttm):
        explain.append("No Buffett value: net income or owner earnings not positive")
    elif not _pos(eps_ttm):
        explain.append("No Buffett value: EPS TTM not positive")
    else:
        oe_ps = eps_ttm * (m.owner_earnings_ttm / m.net_income_ttm)
        g_fcf_raw = (m.growth or {}).get("fcf")
        g_fcf = _clamp_g(g_fcf_raw) if _ok(g_fcf_raw) else 0.0
        b = buffett_value(oe_ps, g_fcf)
        if _ok(b) and b > 0:
            v.buffett = b
            grow = f"growing {_pct(g_fcf)}/yr" if _ok(g_fcf_raw) else "growth unknown, 0%/yr assumed"
            explain.append(f"Buffett value {_money(b)} (owner earnings {_money(oe_ps)}/share {grow}, "
                           f"10% discount, 3% terminal)")
        else:
            explain.append("No Buffett value: calculation not finite")

    v.fair_value = fair_value(v.sticker, v.buffett, m.financial)
    if v.fair_value is not None:
        if v.buffett is not None and v.fair_value == v.buffett and v.buffett < v.sticker:
            explain.append(f"Fair value {_money(v.fair_value)} = lower of sticker and Buffett (Buffett)")
        elif v.buffett is not None:
            explain.append(f"Fair value {_money(v.fair_value)} = lower of sticker and Buffett (sticker)")
        else:
            explain.append(f"Fair value {_money(v.fair_value)} = sticker")
        explain.append(f"Safety Buy {_money(v.fair_value * 0.5)} at 50% margin "
                       f"({_money(v.fair_value * 0.7)} at 30%)")
        explain.append(f"LEAPS flag below {_money(LEAPS_BAND * v.sticker)} (80% of sticker)")
    else:
        explain.append("No fair value, so no Safety Buy")

    # R16: a non-USD filer's Finnhub cap is in an unknown currency (TSM's is TWD) -> no payback
    usd = m.currency == "USD"
    cap = (m.market_cap_usd if _pos(m.market_cap_usd) else (fh.market_cap_usd if fh is not None else None))         if usd else None
    v.payback_years = payback_years(m.owner_earnings_ttm, v.g, cap) if v.g is not None else None
    if v.payback_years is not None:
        more = "+" if v.payback_years >= PAYBACK_CAP else ""
        explain.append(f"Payback {v.payback_years:.0f}{more} years (owner earnings growing {_pct(v.g)}/yr "
                       f"vs market cap; Town buys at ≤ 8)")
    return v


# ---------- price guard ----------

def _prev_trading_day(d):
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def price_guard(price: float | None, quote_time_ms: float | None, ref_price: float | None, *,
                now: datetime) -> tuple[bool, str]:
    """(ok, reason). ok only for a positive finite price quoted on or after the previous trading day
    (ET) and within 25% of the nightly reference price (when there is one)."""
    if not _ok(price):
        return False, "no price"
    if price <= 0:
        return False, f"price {price:g}"
    if not _pos(quote_time_ms):
        return False, "no quote time"
    now_et = now.replace(tzinfo=ET) if now.tzinfo is None else now.astimezone(ET)
    try:
        q = datetime.fromtimestamp(quote_time_ms / 1000.0, tz=ET)
    except (OverflowError, OSError, ValueError):
        return False, "quote time invalid"
    if q > now_et + FUTURE_SLACK:
        return False, "quote time invalid"
    if q.date() < _prev_trading_day(now_et.date()):
        return False, f"quote stale ({q.date().isoformat()})"
    if _pos(ref_price):
        off = abs(price - ref_price) / ref_price
        if off > PRICE_REF_TOL + 1e-12:
            return False, f"price {off * 100:.0f}% from reference"
    return True, ""


# ---------- signal ----------

def _avoid_reason(m: Metrics) -> str | None:
    c = m.checks or {}

    for k in AVOID_GATES:
        ch = c.get(k)
        if ch is None or ch.passed is None:
            if k == "debt" and m.financial:
                continue                       # financial rule set: debt is legitimately n/a
            label = ch.label if ch is not None and ch.label else LABELS.get(k, k)
            return f"Check data: {label} n/a"  # an unknown gate is never a pass
        if ch.passed is True:
            continue
        if k == "roic_now":
            name = "ROE" if m.financial else "ROIC"
            return f"{name} {_pct(ch.value)} < 15%" if _ok(ch.value) else f"{name} below 15%"
        if k == "debt":
            return f"debt {ch.value:.1f}× FCF" if _ok(ch.value) else "debt with no free cash flow"
        if k == "earning":
            return "losing money"
        if k == "history":
            if m.years < 5:
                return f"Short history ({m.years} year{'s' if m.years != 1 else ''})"
            return f"history: {ch.detail}"
        parts = [p for p in str(ch.detail or "").split("; ")
                 if p and not p.endswith(" ok") and "skipped" not in p]
        return f"Check data: {parts[0] if parts else 'sources disagree'}"
    if not m.rule1_business:
        items = []
        for k, words in BIG5_WORDS.items():
            ch = c.get(k)
            if k == "roic_consistency" and m.financial:
                words = "ROE consistency"
            if ch is None or (ch.passed is None and k == "roic_consistency"):
                items.append(f"{words} n/a")          # the business test needs consistency to pass
            elif ch.passed is False:
                items.append(f"{words} {_pct(ch.value)}" if _ok(ch.value) else f"{words} undefined")
        return "not a Rule #1 business" + (f": {', '.join(items)}" if items else "")
    return None


def _mos(mos) -> float:
    """Only the page's two options are honoured; anything else is the 50% default."""
    return float(mos) if _ok(mos) and mos in MOS_OPTIONS else float(DEFAULT_MOS)


def signal(m: Metrics, v: Valuation, *, price: float | None, price_ok: bool, mos: int,
           price_reason: str | None = None) -> dict:
    """The row's verdict. Order: status rows, AVOID, NO_PRICE, then BUY / WAIT; LEAPS is a flag.

    `price_reason` (optional) is the price_guard reason shown on a NO_PRICE row.
    """
    fair = v.fair_value if _pos(v.fair_value) else None
    sticker = v.sticker if _pos(v.sticker) else None
    safety = round(fair * (1 - _mos(mos) / 100.0), ROUND) if fair is not None else None
    leaps_below = round(LEAPS_BAND * sticker, ROUND) if sticker is not None else None
    gate = m.roe_now if m.financial else m.roic_now
    quality = "HIGH" if _ok(gate) and gate >= QUALITY_ROIC else "LOW"
    good_price = bool(price_ok) and _pos(price)
    out = {"signal": "", "leaps": False, "reason": "", "buy_below": safety, "leaps_below": leaps_below,
           "buy_zone_pct": (1 - price / safety) if good_price and safety else None,
           "safety_buy": safety, "quality": quality}

    if m.status is not None:              # no score, so no levels: the page shows none
        out.update(signal=str(m.status), reason=m.status_reason or str(m.status), buy_below=None,
                   leaps_below=None, safety_buy=None, buy_zone_pct=None)
        return out
    why = _avoid_reason(m)
    if why is None and safety is None:
        why = "no Safety Buy (no sticker price)"
    if why is not None:                   # an AVOID row can never fire, so it publishes no levels
        out.update(signal="AVOID", reason=why, buy_below=None, leaps_below=None, safety_buy=None,
                   buy_zone_pct=None)
        return out
    full = m.years >= BUY_YEARS
    nf = (m.checks or {}).get("eps_not_falling")
    not_falling = nf is not None and nf.passed is True
    can_buy = full and not_falling                       # the row can reach BUY at some price
    can_leaps = can_buy and _ok(gate) and gate >= LEAPS_ROIC
    if not can_buy:
        out["buy_below"] = None
    if not can_leaps:
        out["leaps_below"] = None
    leaps_below = out["leaps_below"]
    if not good_price:
        if price_reason:
            r = price_reason
        elif not _ok(price):
            r = "no price"
        elif price <= 0:
            r = f"price {price:g}"
        else:
            r = "price failed the guard"
        out.update(signal="NO_PRICE", reason=r)
        return out

    out["leaps"] = bool(can_leaps and leaps_below is not None and price <= leaps_below)
    if full and not_falling and price <= safety:
        out.update(signal="BUY", reason=f"price {_money(price)} ≤ Safety Buy {_money(safety)}")
    elif not full:
        out.update(signal="WAIT", reason=f"young: {m.years} years, BUY needs {BUY_YEARS}")
    elif not not_falling:
        out.update(signal="WAIT", reason="earnings falling" if nf is not None and nf.passed is False
                   else "earnings trend unknown")
    else:
        out.update(signal="WAIT", reason="price above safety buy")
    return out
