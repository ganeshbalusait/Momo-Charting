"""Long Term: per-ticker numbers, growth windows and the 11-point checklist.

Pure functions over an EDGAR `Snapshot` and a `FinnhubExtract` (spec sections "Numbers computed
per ticker", "The checklist", "Fact selection rules (EDGAR)" growth windows and date stamps).

Everything fails safe: an unknown, stale or inconsistent number is never a pass. Unknown gate
numbers (ROIC now, debt, FCF, EPS) put the row in CHECK_DATA instead of letting an n/a through.

Definitions (binding):
* ROIC (Town's) = net income / (equity + total debt); n/a when the denominator <= 0 or < 10% of
  total assets. Financial rule set: ROE = net income / equity replaces it.
* FCF = cfo - capex; owner earnings = FCF - stock comp (missing stock comp counts as 0, noted).
* Growth: up to 10 fiscal years ending at the latest annual row, >= 5 complete rows, start at the
  first year with revenue >= 5% of the latest and after any > 5x revenue jump, no gap > 400 days.
  CAGR over 3-year averaged endpoints and the real days between the two centre dates.
* A "now" ratio never mixes a TTM numerator with an FY-only denominator: two flows with different
  stamps both fall back to the latest FY. Balances older than the latest annual end (- 7 days)
  are stale and treated as absent.
"""
from __future__ import annotations

import copy
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from long_term.edgar import Snapshot, is_financial_sic
from long_term.finnhub import FinnhubExtract

ROIC_GATE = 0.15                  # check 1 (ROIC or ROE)
CONSISTENCY_MIN = 0.10            # check 2: a year counts at >= 10% ...
CONSISTENCY_SHARE = 0.80          # ... in at least 80% of the computable years
CONSISTENCY_MIN_YEARS = 5
GROWTH_GATE = 0.10                # checks 3-6
MAX_WINDOW = 10
MIN_YEARS = 5
FULL_YEARS = 8                    # below this a row is "young"
GAP_DAYS = 400
START_REVENUE_SHARE = 0.05
JUMP = 5.0
ASSET_FLOOR = 0.10                # ROIC denominator must be >= 10% of total assets
DEBT_FCF_MULT = 3.0
NO_DEBT_INTEREST_SHARE = 0.01
NO_DEBT_DE = 0.05
EPS_FALL = 0.8
PRICE_TOL = 0.15
EPS_TOL_REL, EPS_TOL_ABS = 0.25, 0.10
ROIC_TOL_PTS, ROIC_TOL_REL = 0.05, 0.25
GROWTH_TOL = 0.10
PERIOD_MATCH_DAYS = 10            # Finnhub period vs EDGAR period end
PERIOD_DIFFER_DAYS = 100          # "more than one quarter apart"
BALANCE_SLACK_DAYS = 7
STALE_ANNUAL_MONTHS = 15
STALE_QUARTER_MONTHS = 7
STALE_FETCH_DAYS = 4
SHARE_SCALES = ((1e3, "1e3"), (1e6, "1e6"))
SHARE_SCALE_TOL = 0.20
FCF_MARGIN_AGREE = 0.10           # Finnhub margin x revenue vs EDGAR FCF in overlap years
FCF_MARGIN_MIN_OVERLAP = 2        # agreeing overlap years needed before the margin series is trusted
LISTING_FACTOR_DRIFT = 0.20       # factor moved more than this between refreshes -> Check data

COMPLETE = ("revenue", "net_income", "eps", "equity", "fcf")
COMPLETE_FIN = ("revenue", "net_income", "eps", "equity")
CHECK_KEYS = ("roic_now", "roic_consistency", "sales_growth", "eps_growth", "equity_growth",
              "fcf_growth", "debt", "earning", "history", "eps_not_falling", "sources")
BIG5 = ("roic_consistency", "sales_growth", "eps_growth", "equity_growth", "fcf_growth")
LABELS = {"roic_now": "ROIC now", "roic_consistency": "ROIC consistency", "sales_growth": "Sales growth",
          "eps_growth": "EPS growth", "equity_growth": "Equity growth", "fcf_growth": "FCF growth",
          "debt": "Debt payable", "earning": "Earning money now", "history": "History and freshness",
          "eps_not_falling": "Earnings not falling", "sources": "Sources agree"}
THRESHOLDS = {"roic_now": "≥ 15%", "roic_consistency": "≥ 10% in 80% of years",
              "sales_growth": "≥ 10%/yr", "eps_growth": "≥ 10%/yr", "equity_growth": "≥ 10%/yr",
              "fcf_growth": "≥ 10%/yr", "debt": "debt ≤ 3× FCF, or no debt",
              "earning": "EPS TTM > 0 and FCF TTM > 0", "history": "≥ 5 complete years, not stale",
              "eps_not_falling": "EPS TTM ≥ 0.8 × last FY EPS", "sources": "EDGAR and Finnhub agree"}


@dataclass
class Check:
    key: str
    label: str
    value: float | None
    threshold: str
    passed: bool | None           # None = n/a
    detail: str


@dataclass
class Metrics:
    symbol: str
    financial: bool = False
    years: int = 0
    window: list = field(default_factory=list)
    currency: str | None = None
    roic_now: float | None = None
    roic_series: dict = field(default_factory=dict)
    roe_now: float | None = None
    net_income_ttm: float | None = None
    fcf_ttm: float | None = None
    owner_earnings_ttm: float | None = None
    eps_ttm_edgar: float | None = None
    eps_last_fy: float | None = None
    eps_3yr_avg: float | None = None
    growth: dict = field(default_factory=dict)            # sales, eps, equity, fcf
    growth_source: dict = field(default_factory=dict)
    debt: float | None = None
    cash: float | None = None
    equity: float | None = None
    assets: float | None = None
    pe_hist_median: float | None = None
    market_cap_usd: float | None = None
    listing_factor: float | None = None
    checks: dict = field(default_factory=dict)
    big5: int = 0
    big5_na: int = 0
    rule1_business: bool = False
    status: str | None = None
    status_reason: str = ""
    young: bool = False
    eps_ttm_usd: float | None = None
    notes: list = field(default_factory=list)


# ---------- small helpers ----------

def _d(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def _ok(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.month - 1 + n, 12)
    y, m = d.year + y, m + 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(d.day, last))


def _pct(v: float | None, cap: bool = False) -> str:
    if v is None:
        return "n/a"
    if cap and v >= 1.0:
        return "100%+"
    return f"{v * 100:.0f}%"


def _money(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    for div, suf in ((1e12, "T"), (1e9, "B"), (1e6, "M")):
        if a >= div:
            txt = f"{a / div:.1f}"
            return f"{sign}${txt[:-2] if txt.endswith('.0') else txt}{suf}"
    return f"{sign}${a:,.0f}"


def _match(series: dict, end: str, tol: int = PERIOD_MATCH_DAYS):
    """Value of the series point nearest to `end` within `tol` days, else None."""
    if not series:
        return None
    target = _d(end)
    best = None
    for p, v in series.items():
        try:
            gap = abs((_d(p) - target).days)
        except ValueError:
            continue
        if gap <= tol and _ok(v) and (best is None or gap < best[0]):
            best = (gap, v)
    return best[1] if best else None


def _with_fcf(annual: dict) -> dict:
    out = {}
    for e, r in annual.items():
        r = dict(r)
        if "fcf" not in r and _ok(r.get("cfo")) and _ok(r.get("capex")):
            r["fcf"] = r["cfo"] - abs(r["capex"])
        out[e] = r
    return out


with_fcf = _with_fcf    # public name (the Long Term service's detail table uses it)


# ---------- growth window and CAGR ----------

def _window(annual: dict, require=COMPLETE) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    ends = sorted(annual)
    picked: list[str] = []
    for e in reversed(ends):
        r = annual[e]
        if not all(_ok(r.get(k)) for k in require):
            if not picked:
                missing = [k for k in require if not _ok(r.get(k))]
                notes.append(f"window: latest annual row {e} incomplete ({', '.join(missing)})")
            break
        if picked and (_d(picked[-1]) - _d(e)).days > GAP_DAYS:
            notes.append(f"gap in filings: {e} to {picked[-1]}")
            break
        picked.append(e)
        if len(picked) == MAX_WINDOW:
            break
    picked.reverse()
    if not picked:
        return [], notes
    start = 0
    for i in range(1, len(picked)):
        a, b = annual[picked[i - 1]]["revenue"], annual[picked[i]]["revenue"]
        if b > 0 and (a <= 0 or b > JUMP * a):
            start = i
    if start:
        notes.append(f"window: starts {picked[start]} after a > 5× revenue jump")
    latest = annual[picked[-1]]["revenue"]
    first = next((i for i in range(start, len(picked))
                  if latest > 0 and annual[picked[i]]["revenue"] >= START_REVENUE_SHARE * latest), None)
    if first is None:
        notes.append("window: latest revenue not positive")
        return [], notes
    if first > start:
        notes.append(f"window: starts {picked[first]} (earlier revenue < 5% of latest)")
    return picked[first:], notes


def growth_window(annual: dict[str, dict[str, float]], *, require=COMPLETE) -> list[str]:
    """Period ends of the growth window (oldest first); fewer than 5 means short history."""
    return _window(_with_fcf(annual), require)[0]


def cagr(values: dict[str, float], window: list[str]) -> float | None:
    """Growth per year between the mean of the first three and the mean of the last three values,
    over the real days between the two centre (middle) period ends. None when a window value is missing, fewer
    than 5 rows, or either mean <= 0."""
    window = sorted(window)
    if len(window) < MIN_YEARS or not all(_ok(values.get(e)) for e in window):
        return None
    first, last = window[:3], window[-3:]
    a = statistics.fmean(values[e] for e in first)
    b = statistics.fmean(values[e] for e in last)
    if a <= 0 or b <= 0:
        return None
    days = (_d(last[1]) - _d(first[1])).days          # centre (middle) period ends of the two triples
    if days <= 0:
        return None
    return (b / a) ** (365.25 / days) - 1


# ---------- ROIC, ROE, adjusted equity ----------

def _roic(ni, equity, debt, assets) -> float | None:
    if not all(_ok(v) for v in (ni, equity, debt, assets)):
        return None
    den = equity + debt
    if den <= 0 or den < ASSET_FLOOR * assets:
        return None
    return ni / den


def roic_series(annual: dict[str, dict[str, float]]) -> dict[str, float]:
    """Net income / (equity + debt) per year; years with an unknown part or a guarded
    denominator (<= 0 or < 10% of assets) are left out."""
    out = {}
    for e, r in sorted(annual.items()):
        v = _roic(r.get("net_income"), r.get("equity"), r.get("debt"), r.get("assets"))
        if v is not None:
            out[e] = v
    return out


def _roe_series(annual: dict) -> dict[str, float]:
    return {e: r["net_income"] / r["equity"] for e, r in sorted(annual.items())
            if _ok(r.get("net_income")) and _ok(r.get("equity")) and r["equity"] > 0}


def adjusted_equity(annual: dict[str, dict[str, float]], window: list[str]) -> dict[str, float]:
    """Equity plus buybacks and dividends paid after the window start (capital returned)."""
    out, cum = {}, 0.0
    for i, e in enumerate(sorted(window)):
        r = annual.get(e) or {}
        if i:
            cum += sum(abs(r[k]) for k in ("buybacks", "dividends") if _ok(r.get(k)))
        if _ok(r.get("equity")):
            out[e] = r["equity"] + cum
    return out


def listing_factor(price: float | None, fh: FinnhubExtract | None) -> float | None:
    """price / (Finnhub epsTTM x peTTM): 1.0 when within 15%, else the ratio (ADR ratio / FX)."""
    if fh is None or not _ok(price) or price <= 0 or not _ok(fh.eps_ttm) or not _ok(fh.pe_ttm):
        return None
    implied = fh.eps_ttm * fh.pe_ttm
    if implied <= 0:
        return None
    ratio = price / implied
    return 1.0 if abs(ratio - 1.0) <= PRICE_TOL else ratio


# ---------- snapshot preparation ----------

def _rescale_shares(table: dict, ttm: dict, notes: list[str]) -> None:
    """MCD-style unit slips: shares reported in thousands/millions under unit "shares"."""
    used: set[str] = set()
    for e, r in table.items():
        sh, ni, eps = r.get("shares"), r.get("net_income"), r.get("eps")
        if not (_ok(sh) and _ok(ni) and _ok(eps)) or sh <= 0 or eps == 0:
            continue
        expected = ni / eps
        if expected <= 0:
            continue
        for f, lab in SHARE_SCALES:
            if abs(expected / sh / f - 1) <= SHARE_SCALE_TOL:
                r["shares"] = sh * f
                used.add(lab)
                break
    ref_end = max((e for e, r in table.items() if _ok(r.get("shares"))), default=None)
    tsh = ttm.get("shares")
    if ref_end and _ok(tsh) and tsh > 0:
        ref = table[ref_end]["shares"]
        for f, lab in SHARE_SCALES:
            if abs(ref / tsh / f - 1) <= SHARE_SCALE_TOL:
                ttm["shares"] = tsh * f
                used.add(lab)
                break
    for f, lab in SHARE_SCALES:
        if lab in used:
            notes.append(f"shares: rescaled ×{lab}")


def _fcf_from_margin(table: dict, fh: FinnhubExtract | None) -> dict[str, float]:
    if fh is None or not getattr(fh, "fcf_margin_annual", None):
        return {}
    out = {}
    for e, r in table.items():
        m = _match(fh.fcf_margin_annual, e)
        if m is not None and _ok(r.get("revenue")):
            out[e] = m * r["revenue"]
    return out


def _choose_fcf(table: dict, fh, financial: bool, notes: list[str]) -> str:
    """EDGAR cfo - capex, or Finnhub fcfMargin x EDGAR revenue for the whole series when EDGAR
    misses window years that the margin covers and both agree where they overlap."""
    if financial:
        return "n/a"
    w0, _ = _window(table, COMPLETE_FIN)
    missing = [e for e in w0 if not _ok(table[e].get("fcf"))]
    if not missing:
        return "edgar"
    alt = _fcf_from_margin(table, fh)
    if not all(e in alt for e in w0):
        notes.append(f"fcf: EDGAR missing {', '.join(e[:4] for e in missing)}; no Finnhub margin fallback")
        return "edgar"
    off = [e for e in w0 if _ok(table[e].get("fcf")) and
           abs(alt[e] - table[e]["fcf"]) > max(FCF_MARGIN_AGREE * abs(table[e]["fcf"]), 0.01 * abs(table[e]["revenue"]))]
    if off:
        notes.append(f"fcf: Finnhub margin disagrees with EDGAR in {', '.join(e[:4] for e in off)}; not used")
        return "edgar"
    overlap = [e for e in w0 if _ok(table[e].get("fcf"))]
    if len(overlap) < FCF_MARGIN_MIN_OVERLAP:
        notes.append(f"fcf: Finnhub margin overlaps EDGAR in only {len(overlap)} year(s); not used")
        return "edgar"
    for e, r in table.items():
        r.pop("fcf", None)
        if e in alt:
            r["fcf"] = alt[e]
    notes.append(f"fcf: series from Finnhub fcfMargin × revenue (EDGAR missing {', '.join(e[:4] for e in missing)})")
    return "finnhub_margin"


class _Now:
    """The "now" numbers of one snapshot with per-concept basis (TTM vs FY) and stale balances."""

    def __init__(self, snap: Snapshot, table: dict, ttm: dict, notes: list[str]):
        self.s, self.table, self.ttm, self.notes = snap, table, ttm, notes
        self.lae = snap.latest_annual_end
        self.fy = table.get(self.lae) or {}

    def balance(self, c: str):
        """(value, detail); stale or missing -> (None, reason)."""
        if not _ok(self.ttm.get(c)):
            if _ok(self.fy.get(c)):
                return self.fy[c], f"FY {self.lae}"
            return None, f"{c} absent"
        end = self.s.balance_ends.get(c)
        if end and self.lae and _d(end) < _d(self.lae) - timedelta(days=BALANCE_SLACK_DAYS):
            msg = f"{c} balance stale ({end})"
            if msg not in self.notes:
                self.notes.append(msg)
            return None, msg
        return self.ttm[c], f"at {end or self.lae}"

    def flow(self, c: str):
        """(value, basis label) of a single flow."""
        st = self.s.ttm_ends.get(c)
        if _ok(self.ttm.get(c)) and st:
            return self.ttm[c], ("FY " if st[1] else "TTM ") + st[0]
        if _ok(self.fy.get(c)):
            return self.fy[c], f"FY {self.lae}"
        return None, "missing"

    def flows(self, *cs: str):
        """Values of several flows on ONE basis: TTM when all share a stamp, else all FY."""
        stamps = {self.s.ttm_ends.get(c) for c in cs}
        if len(stamps) == 1 and None not in stamps and all(_ok(self.ttm.get(c)) for c in cs):
            (end, fy), = stamps
            return [self.ttm[c] for c in cs], ("FY " if fy else "TTM ") + end
        if all(_ok(self.fy.get(c)) for c in cs):
            return [self.fy[c] for c in cs], f"FY {self.lae}"
        return None, "missing " + ", ".join(c for c in cs if not _ok(self.fy.get(c)) and not _ok(self.ttm.get(c)))


def _stale(snap: Snapshot, today: date) -> str | None:
    lae = snap.latest_annual_end
    if lae and _add_months(_d(lae), STALE_ANNUAL_MONTHS) < today:
        return f"Stale filing (annual {lae})"
    # Only 10-K filers owe 10-Qs (20-F / 40-F filers report annually, even in USD: ARM, INFY, SHOP).
    # When the annual form is unknown, a USD reporting currency stands in for "10-K filer".
    form = (getattr(snap, "annual_form", None) or "").upper()
    quarterly = form.startswith("10-K") if form else snap.currency == "USD"
    if quarterly and snap.latest_period_end and \
            _add_months(_d(snap.latest_period_end), STALE_QUARTER_MONTHS) < today:
        return f"Stale filing (quarter {snap.latest_period_end})"
    try:
        fetched = datetime.fromisoformat(str(snap.fetched_at)).date()
    except (TypeError, ValueError):
        return "Stale data (fetch time unreadable)"
    if (today - fetched).days > STALE_FETCH_DAYS:
        return f"Stale data (fetched {fetched.isoformat()})"
    return None


def _empty(symbol: str, status: str, reason: str) -> Metrics:
    checks = {k: Check(k, LABELS[k], None, THRESHOLDS[k], None, reason) for k in CHECK_KEYS}
    return Metrics(symbol=symbol, checks=checks, status=status, status_reason=reason)


# ---------- the checklist ----------

def compute_metrics(snapshot: Snapshot | None, fh: FinnhubExtract | None, *, today: date,
                    is_etf: bool, price: float | None, prior_listing_factor: float | None = None) -> Metrics:
    """`prior_listing_factor`: the factor stored at the previous refresh; a move of more than 20%
    (relative) since then is "Check data: listing factor moved N%". None (first night) = no check."""
    symbol = (snapshot.symbol if snapshot else None) or (fh.symbol if fh else "") or ""
    if is_etf:
        return _empty(symbol, "ETF", "ETF")
    if snapshot is None or not snapshot.annual or not snapshot.latest_annual_end:
        return _empty(symbol, "NO_FILINGS", "No filings")

    notes: list[str] = list(snapshot.notes)
    fin = is_financial_sic(snapshot.sic)
    table = _with_fcf(copy.deepcopy(snapshot.annual))
    ttm = dict(snapshot.ttm)
    _rescale_shares(table, ttm, notes)
    fcf_source = _choose_fcf(table, fh, fin, notes)
    window, wnotes = _window(table, COMPLETE_FIN if fin else COMPLETE)
    notes.extend(wnotes)
    years = len(window)
    now = _Now(snapshot, table, ttm, notes)
    lae = snapshot.latest_annual_end
    usd = snapshot.currency == "USD"
    check_data: list[str] = []          # reasons, most important first
    checks: dict[str, Check] = {}

    def put(key, value, passed, detail, label=None, threshold=None):
        checks[key] = Check(key, label or LABELS[key], value, threshold or THRESHOLDS[key], passed, detail)

    # --- net income now ---
    ni_now, ni_basis = now.flow("net_income")
    ni_fy = ni_basis.startswith("FY")

    # --- debt resolution (the 0-debt rule) ---
    equity_now, eq_d = now.balance("equity")
    assets_now, as_d = now.balance("assets")
    cash_now, _ = now.balance("cash")
    debt_raw, debt_d = now.balance("debt")
    interest_ok, int_detail = _interest_small(now)
    de = fh.debt_to_equity if fh else None
    proof = interest_ok and _ok(de) and de < NO_DEBT_DE
    debt_unresolved = False
    if _ok(debt_raw) and debt_raw > 0:
        debt_now, debt_detail = debt_raw, f"debt {debt_raw:,.0f} {debt_d}"
    elif proof:
        debt_now, debt_detail = 0.0, f"no debt ({debt_d}; {int_detail}; Finnhub D/E {de:.2f})"
    else:
        debt_now = None
        de_s = f"{de:.2f}" if _ok(de) else "n/a"
        debt_detail = f"debt unresolved ({debt_d}; {int_detail}; Finnhub D/E {de_s})"
        debt_unresolved = not fin
    if proof:     # years without a debt figure count as debt-free only with the same proof per year
        for r in table.values():
            if not _ok(r.get("debt")) and _interest_small_row(r):
                r["debt"] = 0.0

    # --- ROIC / ROE now ---
    roic_now = _roic(ni_now, equity_now, debt_now, assets_now)
    roe_now = ni_now / equity_now if _ok(ni_now) and _ok(equity_now) and equity_now > 0 else None
    roic_detail = (f"NI {ni_basis} / (equity {eq_d} + {debt_detail}); assets {as_d}"
                   if roic_now is not None else
                   f"n/a: NI {ni_basis}; equity {eq_d}; {debt_detail}; assets {as_d}")
    if fin:
        gate = roe_now
        put("roic_now", roe_now, None if roe_now is None else roe_now >= ROIC_GATE,
            f"ROE {_pct(roe_now, True)}: NI {ni_basis} / equity {eq_d}", label="ROE now")
        if roe_now is None:
            check_data.append("ROE n/a")
    else:
        gate = roic_now
        put("roic_now", roic_now, None if roic_now is None else roic_now >= ROIC_GATE,
            f"ROIC {_pct(roic_now, True)}: {roic_detail}")
        if roic_now is None and not debt_unresolved:
            check_data.append("ROIC n/a")

    # --- ROIC / ROE consistency ---
    rs = roic_series(table)
    series = _roe_series(table) if fin else rs
    name = "ROE" if fin else "ROIC"
    vals = [series[e] for e in window if e in series]
    if len(vals) < CONSISTENCY_MIN_YEARS:
        put("roic_consistency", None, None, f"only {len(vals)} computable {name} years in the window",
            label=f"{name} consistency")
    else:
        share = sum(v >= CONSISTENCY_MIN for v in vals) / len(vals)
        put("roic_consistency", share, share >= CONSISTENCY_SHARE,
            f"{name} ≥ 10% in {sum(v >= CONSISTENCY_MIN for v in vals)} of {len(vals)} years",
            label=f"{name} consistency")

    # --- growth ---
    growth: dict[str, float | None] = {}
    source: dict[str, str] = {"sales": "edgar", "equity": "edgar"}
    growth["sales"] = cagr({e: r["revenue"] for e, r in table.items() if _ok(r.get("revenue"))}, window)
    edgar_eps = {e: r["eps"] for e, r in table.items() if _ok(r.get("eps"))}
    g_eps_edgar = cagr(edgar_eps, window)
    fh_eps = {e: _match(fh.eps_annual, e) for e in window} if fh and fh.eps_annual else {}
    fh_full = bool(window) and bool(fh_eps) and all(v is not None for v in fh_eps.values())
    g_eps_fh = cagr(fh_eps, window) if fh_full else None
    if fh_full:
        growth["eps"], source["eps"], eps_series = g_eps_fh, "finnhub", fh_eps
    else:
        growth["eps"], source["eps"], eps_series = g_eps_edgar, "edgar", edgar_eps
        if fh and fh.eps_annual:
            notes.append("eps: Finnhub series does not cover the window; EDGAR split-corrected used")
    adj = adjusted_equity(table, window)
    gaps = [e[:4] for e in window[1:] if not all(_ok(table[e].get(k)) for k in ("buybacks", "dividends"))]
    if gaps and window:
        notes.append(f"equity: buybacks/dividends missing in {', '.join(gaps)} (counted as 0)")
    eq_start_bad = len(window) >= MIN_YEARS and any(adj.get(e, 0.0) <= 0 for e in window[:3])
    growth["equity"] = None if eq_start_bad else cagr(adj, window)
    if fin:
        growth["fcf"], source["fcf"] = None, "n/a"
    else:
        growth["fcf"] = cagr({e: r["fcf"] for e, r in table.items() if _ok(r.get("fcf"))}, window)
        source["fcf"] = fcf_source

    def growth_check(key, g, what):
        if years < MIN_YEARS:
            put(key, g, None, f"short history ({years} years)")
        elif g is None:
            put(key, None, False, f"{what}: undefined (an averaged endpoint ≤ 0 or a missing year)")
        else:
            put(key, g, g >= GROWTH_GATE, f"{what} {_pct(g)}/yr over {years} years ({window[0][:4]}–{window[-1][:4]})")

    growth_check("sales_growth", growth["sales"], "sales")
    growth_check("eps_growth", growth["eps"], f"EPS ({source['eps']})")
    if years >= MIN_YEARS and growth["equity"] is None and eq_start_bad:
        put("equity_growth", None, None, "n/a (buybacks): capital-returned-adjusted equity ≤ 0 at the start")
    else:
        growth_check("equity_growth", growth["equity"], "adjusted equity")
    if fin:
        put("fcf_growth", None, None, "n/a (financial)")
    else:
        growth_check("fcf_growth", growth["fcf"], f"FCF ({fcf_source})")

    # --- FCF and owner earnings now ---
    pair, fcf_basis = now.flows("cfo", "capex")
    fcf_now = pair[0] - abs(pair[1]) if pair else None
    owner = None
    if fcf_now is not None:
        sbc_pair, sbc_basis = now.flows("cfo", "capex", "sbc")
        fy_sbc = now.fy.get("sbc")
        if sbc_pair and sbc_basis == fcf_basis:
            owner = fcf_now - abs(sbc_pair[2])
        elif _ok(fy_sbc):        # stamps differ: never drop a known cost, take the FY figure
            owner = fcf_now - abs(fy_sbc)
            notes.append(f"sbc: FY {lae} value used against FCF {fcf_basis}")
        else:
            owner = fcf_now
            notes.append(f"sbc: missing ({fcf_basis}); owner earnings treat it as 0")

    # --- debt check ---
    if fin:
        put("debt", None, None, f"n/a (financial); {debt_detail}")
    elif debt_unresolved:
        put("debt", None, None, debt_detail)
        check_data.insert(0, "debt unresolved")
    elif debt_now == 0:
        put("debt", 0.0, True, debt_detail)
    elif fcf_now is None:
        put("debt", None, None, f"{debt_detail}; FCF unknown ({fcf_basis})")
        check_data.append("FCF unknown")
    elif fcf_now <= 0:
        put("debt", None, False, f"{debt_detail}; FCF {fcf_now:,.0f} ≤ 0 ({fcf_basis})")
    else:
        ratio = debt_now / fcf_now
        put("debt", ratio, ratio <= DEBT_FCF_MULT, f"debt {ratio:.1f}× FCF ({debt_detail}; FCF {fcf_basis})")

    # --- EDGAR EPS now (NI / diluted shares on the NI basis) ---
    if ni_fy:
        sh, sh_d = table.get(lae, {}).get("shares"), f"FY {lae}"
    else:
        sh, sh_d = now.balance("shares")
    eps_edgar = ni_now / sh if _ok(ni_now) and _ok(sh) and sh > 0 else None

    # --- earning money now ---
    eps_now = fh.eps_ttm if fh and _ok(fh.eps_ttm) else eps_edgar
    eps_src = "Finnhub" if fh and _ok(fh.eps_ttm) else "EDGAR"
    if fin:
        if eps_now is None:
            put("earning", None, None, "EPS unknown", threshold="EPS TTM > 0")
            check_data.append("EPS unknown")
        else:
            put("earning", eps_now, eps_now > 0, f"EPS {eps_now:.2f} ({eps_src})", threshold="EPS TTM > 0")
    elif eps_now is None or fcf_now is None:
        put("earning", eps_now, None, f"EPS {eps_now if eps_now is not None else 'unknown'}; FCF {fcf_basis}")
        check_data.append("earnings unknown")
    elif fcf_now > 0 and owner is not None and owner <= 0:
        # R15: positive FCF that stock comp more than eats is not "earning money"
        put("earning", eps_now, False,
            f"owner earnings negative after stock comp (FCF {_money(fcf_now)} − SBC {_money(fcf_now - owner)}); "
            f"EPS {eps_now:.2f} ({eps_src}); {fcf_basis}")
    else:
        put("earning", eps_now, eps_now > 0 and fcf_now > 0,
            f"EPS {eps_now:.2f} ({eps_src}); FCF {fcf_now:,.0f} ({fcf_basis}); owner earnings "
            + (f"{owner:,.0f}" if owner is not None else "n/a"))

    # --- history and freshness ---
    stale = _stale(snapshot, today)
    put("history", float(years), years >= MIN_YEARS and stale is None,
        f"{years} complete years" + (f"; {stale}" if stale else "; fresh"))

    # --- earnings not falling ---
    fh_last = None
    if fh and fh.eps_annual:
        p = max(fh.eps_annual)
        fh_last = fh.eps_annual[p]
    if fh and _ok(fh.eps_ttm) and fh_last is not None:
        e_ttm, e_fy, src = fh.eps_ttm, fh_last, "Finnhub"
    else:
        e_ttm, e_fy, src = eps_edgar, table.get(lae, {}).get("eps"), "EDGAR"
    eps_last_fy = e_fy if _ok(e_fy) else None
    ni_stamp = snapshot.ttm_ends.get("net_income")
    if src == "EDGAR" and (ni_stamp is None or ni_stamp[1]):
        # EDGAR's "TTM" is the FY itself: comparing it with the FY would always pass
        put("eps_not_falling", None, None, "TTM unavailable (TTM = FY)")
    elif e_ttm is None or eps_last_fy is None:
        put("eps_not_falling", None, None, f"EPS TTM or last FY unknown ({src})")
    else:
        ok = e_ttm > 0 and (eps_last_fy <= 0 or e_ttm >= EPS_FALL * eps_last_fy)
        put("eps_not_falling", e_ttm / eps_last_fy if eps_last_fy > 0 else None, ok,
            f"EPS TTM {e_ttm:.2f} vs last FY {eps_last_fy:.2f} ({src})")

    # --- sources agree ---
    lf = listing_factor(price, fh)
    src_fail, src_notes, price_unchecked = _sources(snapshot, fh, fin=fin, usd=usd, gate=gate, eps_edgar=eps_edgar,
                                   ni_basis=ni_basis, g_eps_fh=g_eps_fh if fh_full else None,
                                   fh_full=fh_full, g_eps_edgar=g_eps_edgar, years=years, price=price, lf=lf)
    if _ok(lf) and lf > 0 and _ok(prior_listing_factor) and prior_listing_factor > 0:
        moved = abs(lf - prior_listing_factor) / prior_listing_factor
        if moved > LISTING_FACTOR_DRIFT:
            src_fail.append(f"listing factor moved {moved * 100:.0f}%")
            src_notes.append(f"listing factor {prior_listing_factor:.4f} -> {lf:.4f} (moved {moved * 100:.0f}%)")
    put("sources", None, False if src_fail else (None if price_unchecked else True), "; ".join(src_notes))
    if fh is None:
        check_data.insert(0, "Finnhub: no data")
    else:
        pos = 1 if check_data[:1] == ["debt unresolved"] else 0
        check_data[pos:pos] = src_fail
        if price_unchecked:
            check_data.append("price sanity not checked")   # last: only shows when nothing else is wrong

    # --- Big 5 and the business test ---
    five = [checks[k].passed for k in BIG5]
    big5, big5_na = five.count(True), five.count(None)
    rule1 = five.count(False) <= 1 and checks["roic_consistency"].passed is True

    # --- status ---
    if stale:
        status, reason = "STALE", stale
    elif years < MIN_YEARS:
        status, reason = "SHORT_HISTORY", f"Short history ({years} year{'s' if years != 1 else ''})"
    elif check_data:
        r0 = check_data[0]
        status, reason = "CHECK_DATA", r0 if r0.startswith("Finnhub") else f"Check data: {r0}"
    else:
        status, reason = None, ""
    notes.extend(f"check data: {r}" for r in check_data[1:])

    eps_window = [eps_series[e] for e in window if _ok(eps_series.get(e))]
    pe_vals = [v for v in (_match(fh.pe_annual, e) for e in window) if v is not None] if fh else []
    if usd:
        eps_usd = fh.eps_ttm if fh and _ok(fh.eps_ttm) else None
    else:
        eps_usd = fh.eps_ttm * lf if fh and _ok(fh.eps_ttm) and lf is not None else None
    return Metrics(
        symbol=symbol, financial=fin, years=years, window=window, currency=snapshot.currency,
        roic_now=roic_now, roic_series=rs, roe_now=roe_now, net_income_ttm=ni_now, fcf_ttm=fcf_now,
        owner_earnings_ttm=owner, eps_ttm_edgar=eps_edgar, eps_last_fy=eps_last_fy,
        eps_3yr_avg=statistics.fmean(eps_window[-3:]) if len(eps_window) >= 3 else None,
        growth=growth, growth_source=source, debt=debt_now, cash=cash_now, equity=equity_now,
        assets=assets_now, pe_hist_median=statistics.median(pe_vals) if pe_vals else None,
        market_cap_usd=fh.market_cap_usd if fh and usd else None,   # R16: Finnhub's cap currency is unknown off-USD
        listing_factor=lf if lf is not None or not usd else (1.0 if price is None else None),
        checks=checks, big5=big5, big5_na=big5_na, rule1_business=rule1, status=status,
        status_reason=reason, young=MIN_YEARS <= years < FULL_YEARS, eps_ttm_usd=eps_usd, notes=notes)


def _interest_small(now: _Now) -> tuple[bool, str]:
    """Interest absent, or < 1% of operating income on one basis."""
    i_val, _ = now.flow("interest")
    if i_val is None:
        return True, "no interest expense"
    pair, basis = now.flows("interest", "op_income")
    if not pair or pair[1] <= 0:
        return False, f"interest with operating income unknown or ≤ 0 ({basis})"
    share = abs(pair[0]) / pair[1]
    return share < NO_DEBT_INTEREST_SHARE, f"interest {share * 100:.1f}% of operating income ({basis})"


def _interest_small_row(r: dict) -> bool:
    if not _ok(r.get("interest")):
        return True
    return _ok(r.get("op_income")) and r["op_income"] > 0 and abs(r["interest"]) / r["op_income"] < NO_DEBT_INTEREST_SHARE


def _sources(snap: Snapshot, fh: FinnhubExtract | None, *, fin, usd, gate, eps_edgar, ni_basis,
             g_eps_fh, fh_full, g_eps_edgar, years, price, lf) -> tuple[list[str], list[str], bool]:
    """(failure reasons, detail parts, price sanity not checked) for check 11."""
    if fh is None:
        return ["Finnhub: no data"], ["Finnhub: no data"], False
    fails, parts = [], []
    # ROIC (ROE for financials): same side of 15%, or within max(5 points, 25% relative)
    name = "ROE" if fin else "ROIC"
    theirs = fh.roe_ttm if fin else fh.roi_ttm
    if gate is None:
        parts.append(f"{name}: EDGAR n/a")
    elif not _ok(theirs):
        fails.append(f"{name}: no Finnhub value")
        parts.append(fails[-1])
    else:
        b = theirs / 100.0
        agree = (gate >= ROIC_GATE) == (b >= ROIC_GATE) or abs(gate - b) <= max(ROIC_TOL_PTS, ROIC_TOL_REL * abs(b))
        msg = f"{name} {_pct(gate, True)} vs Finnhub {_pct(b, True)}"
        parts.append(msg + (" ok" if agree else ""))
        if not agree:
            fails.append(msg)
    # EPS (USD filers): EDGAR NI / diluted shares vs Finnhub, same period
    if not usd:
        parts.append("EPS: skipped (non-USD filer)")
    else:
        fh_eps = fh.eps_incl_ttm if _ok(fh.eps_incl_ttm) else fh.eps_ttm
        # Finnhub gives no TTM period date, so its latest ANNUAL period (eps or pe series) stands
        # in for "Finnhub's period"; > 100 days from EDGAR's latest annual end = periods differ.
        periods = [p for p in list(fh.eps_annual) + list(fh.pe_annual)]
        fh_end = max(periods) if periods else None
        if fh_end and snap.latest_annual_end and \
                abs((_d(fh_end) - _d(snap.latest_annual_end)).days) > PERIOD_DIFFER_DAYS:
            fails.append(f"periods differ (Finnhub FY {fh_end} vs EDGAR {snap.latest_annual_end})")
            parts.append(fails[-1])
        elif eps_edgar is None or not _ok(fh_eps):
            fails.append("EPS: cannot compare (EDGAR shares or Finnhub EPS missing)")
            parts.append(fails[-1])
        else:
            agree = abs(eps_edgar - fh_eps) <= max(EPS_TOL_REL * abs(fh_eps), EPS_TOL_ABS)
            msg = f"EPS {eps_edgar:.2f} ({ni_basis}) vs Finnhub {fh_eps:.2f}"
            parts.append(msg + (" ok" if agree else ""))
            if not agree:
                fails.append(msg)
    # EPS growth: Finnhub series vs EDGAR split-corrected
    if not fh_full and not fin:
        # R17: a non-financial row's EPS growth needs Finnhub's series too; EDGAR alone is one source
        fails.append("EPS growth: one source only")
        parts.append(fails[-1])
    elif not fh_full:
        parts.append("EPS growth: no Finnhub series (EDGAR used)")
    elif years >= MIN_YEARS:
        a, b = g_eps_fh, g_eps_edgar
        if a is None and b is None:
            agree = True
        elif a is None or b is None:
            agree = False
        else:
            agree = abs(a - b) <= GROWTH_TOL or (a >= GROWTH_GATE and b >= GROWTH_GATE)
        msg = f"EPS growth Finnhub {_pct(a)} vs EDGAR {_pct(b)}"
        parts.append(msg + (" ok" if agree else ""))
        if not agree:
            fails.append(msg)
    # Price sanity: a missing input is never a silent pass
    unchecked = False
    if not _ok(price) or price <= 0:
        parts.append("price sanity: not checked (no price)")
        unchecked = True
    elif lf is None:
        parts.append("price sanity: not checked (no P/E)")
        unchecked = True
    elif lf == 1.0:
        parts.append("price sanity ok")
    elif usd:
        fails.append(f"price sanity (price {price:.2f} vs Finnhub EPS × P/E {fh.eps_ttm * fh.pe_ttm:.2f})")
        parts.append(fails[-1])
    else:
        parts.append(f"listing factor {lf:.4f} (ADR ratio ÷ FX)")
    return fails, parts, unchecked
