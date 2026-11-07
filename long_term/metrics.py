"""Long Term: per-ticker numbers, growth windows and the 11-point checklist.

Pure functions over an EDGAR `Snapshot` and a `FinnhubExtract` (spec sections "Numbers computed
per ticker", "The checklist", "Fact selection rules (EDGAR)" growth windows and date stamps).

Everything fails safe: an unknown, stale or inconsistent number is never a pass. A row may only
BUY / LEAPS when every input is known and the two sources agree; but a row whose data already says
AVOID or WAIT says so instead of hiding behind "no data" (the 2026-11-06 "no data" rulings):
* R38: when the sources disagree, the hard checks (EPS sign, ROIC, debt / FCF) are re-run with the
  LESS favourable source; a failure is a scored AVOID with the reason (`Metrics.forced_avoid`).
  CHECK_DATA remains only when the row would otherwise be WAIT or BUY.
* R40: a MISSING cross-check (Finnhub EPS / ROIC absent, periods differ by one FY, EPS growth
  disagreement -> lower growth used, ROIC not measurable, FCF unknown) is scored from EDGAR and
  capped at WAIT (`Metrics.wait_cap` names the gap); never BUY / LEAPS.
* R35: a loss-making company without any revenue (latest FY and later) is AVOID "no revenue yet".
  A loss-maker with a short or broken history is AVOID "losing money" (the loss is known).
* R41: EDGAR debt unresolved / understated -> Finnhub D/E x equity (over-stated: it includes
  leases) is the fail-safe larger debt, noted and capped at WAIT; an estimate the interest expense
  cannot carry, or one above total liabilities, is not used.
* R39: price sanity is a note for USD 10-K filers (Schwab's price is authoritative; Finnhub's
  EPS x P/E lags); it stays a blocker for non-USD / 20-F / 40-F filers (the listing factor needs it).

Definitions (binding):
* ROIC (Town's) = net income / (equity + total debt); n/a when the denominator <= 0 or < 10% of
  total assets. Financial rule set: ROE = net income / equity replaces it.
* FCF = cfo - capex; owner earnings = FCF - stock comp (missing stock comp counts as 0, noted).
* Growth: up to 10 fiscal years ending at the latest annual row, >= 3 complete rows (R33; BUY still
  needs 8), start at the first year with revenue >= 5% of the latest and after any > 5x revenue jump
  from under 100M USD-equivalent (R34: shells, not COVID troughs), no gap > 400 days; a row ending
  < 350 days after the previously picked one (a second entity's fiscal year) is dropped. CAGR over
  3-year averaged endpoints (single endpoints for 3-4 rows) and the real days between the dates.
* A "now" ratio never mixes a TTM numerator with an FY-only denominator: two flows with different
  stamps both fall back to the latest FY. Balances older than the latest annual end (- 7 days)
  are stale and treated as absent.
"""
from __future__ import annotations

import copy
import math
import re
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
MIN_YEARS = 3                     # R33: rows score from 3 years (BUY / LEAPS still need FULL_YEARS)
AVERAGED_ROWS = 5                 # 3-year averaged CAGR endpoints need this many rows; 3-4 use single endpoints
FULL_YEARS = 8                    # below this a row is "young"
GAP_DAYS = 400
DUPLICATE_DAYS = 350              # a row ending closer than this to the next one is a second entity's year
START_REVENUE_SHARE = 0.05
JUMP = 5.0
JUMP_FLOOR = 100_000_000          # R34: a > 5x jump breaks the window only from under this (USD-equivalent)
# Coarse FX for the JUMP_FLOOR only (units of the reporting currency per USD; a shell is a shell at
# any plausible rate). Unlisted currencies count 1:1, which errs towards breaking the window.
FX_PER_USD = {"USD": 1.0, "EUR": 0.92, "GBP": 0.78, "CHF": 0.88, "CAD": 1.37, "AUD": 1.52, "JPY": 150.0,
              "CNY": 7.2, "HKD": 7.8, "TWD": 32.0, "KRW": 1400.0, "INR": 85.0, "SGD": 1.34, "SEK": 10.5,
              "NOK": 10.8, "DKK": 6.9, "BRL": 5.5, "MXN": 18.0, "ILS": 3.7, "ZAR": 18.0}
ASSET_FLOOR = 0.10                # ROIC denominator must be >= 10% of total assets
DEBT_FCF_MULT = 3.0
NO_DEBT_INTEREST_SHARE = 0.01
NO_DEBT_DE = 0.3                  # R18 (amended): Finnhub totalDebt/totalEquity includes leases
DEBT_UNDERSTATED_RATIO = 2.0      # R22: Finnhub D/E >= 2x ours ...
DEBT_UNDERSTATED_GAP = 0.2        # ... and more than 0.2 above it -> "debt understated?"
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
FCF_FILL_MAX_YEARS = 3            # R37: Finnhub margin may fill up to this many missing window years ...
FCF_FILL_MIN_EDGAR = 5            # ... when EDGAR has FCF in at least this many other window years
LISTING_FACTOR_DRIFT = 0.20       # factor moved more than this between refreshes -> Check data
FINNHUB_DE_SLACK = 1.02           # R41: D/E x equity above total liabilities (x this) is not reproducible
IMPLAUSIBLE_INTEREST_RATE = 0.25  # R41: interest / estimated debt above this -> the estimate is too small
# edgar snapshot notes parsed here (build_snapshot writes exactly these shapes; pinned in test_long_term_edgar)
CAPEX_LARGER_NOTE = "capex: candidates differ, larger used in "    # ... "2016, 2017, 2018 (<tag>)"
EPS_DERIVED_NOTE = "eps: "                                          # ... "2008, 2009 derived from NI/shares"

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
              "earning": "EPS TTM > 0 and FCF TTM > 0", "history": "≥ 3 complete years, not stale",
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
    # 2026-11-06 "no data" rulings: a scored row's verdict overrides (see the module docstring)
    forced_avoid: str = ""        # R35 / R38: the row is AVOID for this reason, whatever the gates say
    wait_cap: str = ""            # R40 / R41: the row can be WAIT at most; names the missing cross-check
    pre_revenue: bool = False     # R35: no revenue in the latest FY or later (and losing money)


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


def _note_years(note: str, prefix: str, suffix: str) -> list[str]:
    """The "YYYY, YYYY" years an edgar note lists between `prefix` and `suffix` (sorted), else []."""
    if not note.startswith(prefix):
        return []
    body = note[len(prefix):]
    cut = body.find(suffix)
    if cut < 0:
        return []
    return sorted(y for y in (p.strip() for p in body[:cut].split(",")) if re.fullmatch(r"\d{4}", y))


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

def _window(annual: dict, require=COMPLETE, currency: str | None = None) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    ends = sorted(annual)
    picked: list[str] = []
    for e in reversed(ends):
        if picked and (_d(picked[-1]) - _d(e)).days < DUPLICATE_DAYS:
            # a second entity's fiscal year (SPAC shell, pre-merger filer) next to the row kept
            notes.append(f"window: {e} dropped ({(_d(picked[-1]) - _d(e)).days} days before {picked[-1]})")
            continue
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
    floor = JUMP_FLOOR * FX_PER_USD.get(str(currency or "USD").upper(), 1.0)
    start = 0
    for i in range(1, len(picked)):
        a, b = annual[picked[i - 1]]["revenue"], annual[picked[i]]["revenue"]
        if b > 0 and (a <= 0 or (b > JUMP * a and a < floor)):      # R34: shells only
            start = i
    if start:
        notes.append(f"window: starts {picked[start]} after a > 5× revenue jump from under the "
                     f"{JUMP_FLOOR / 1e6:.0f}M {str(currency or 'USD').upper()} shell floor")
    latest = annual[picked[-1]]["revenue"]
    first = next((i for i in range(start, len(picked))
                  if latest > 0 and annual[picked[i]]["revenue"] >= START_REVENUE_SHARE * latest), None)
    if first is None:
        notes.append("window: latest revenue not positive")
        return [], notes
    if first > start:
        notes.append(f"window: starts {picked[first]} (earlier revenue < 5% of latest)")
    return picked[first:], notes


def growth_window(annual: dict[str, dict[str, float]], *, require=COMPLETE, currency: str | None = None) -> list[str]:
    """Period ends of the growth window (oldest first); fewer than 3 means short history."""
    return _window(_with_fcf(annual), require, currency)[0]


def cagr(values: dict[str, float], window: list[str]) -> float | None:
    """Growth per year between the mean of the first three and the mean of the last three values,
    over the real days between the two centre (middle) period ends. With 3-4 rows (R33) the single
    first and last values and the days between them. None when a window value is missing, fewer
    than 3 rows, or either endpoint <= 0."""
    window = sorted(window)
    if len(window) < MIN_YEARS or not all(_ok(values.get(e)) for e in window):
        return None
    if len(window) < AVERAGED_ROWS:
        first, last = window[:1], window[-1:]
    else:
        first, last = window[:3], window[-3:]
    a = statistics.fmean(values[e] for e in first)
    b = statistics.fmean(values[e] for e in last)
    if a <= 0 or b <= 0:
        return None
    days = (_d(last[len(last) // 2]) - _d(first[len(first) // 2])).days   # centre period ends
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


def _choose_fcf(table: dict, fh, financial: bool, notes: list[str], currency: str | None = None) -> str:
    """EDGAR cfo - capex, or Finnhub fcfMargin x EDGAR revenue for the whole series when EDGAR
    misses window years that the margin covers and both agree where they overlap; or (R37) the
    margin fills up to 3 missing window years when EDGAR has FCF in at least 5 others."""
    if financial:
        return "n/a"
    w0, _ = _window(table, COMPLETE_FIN, currency)
    missing = [e for e in w0 if not _ok(table[e].get("fcf"))]
    if not missing:
        return "edgar"
    alt = _fcf_from_margin(table, fh)
    overlap = [e for e in w0 if _ok(table[e].get("fcf")) and e in alt]
    off = [e for e in overlap if
           abs(alt[e] - table[e]["fcf"]) > max(FCF_MARGIN_AGREE * abs(table[e]["fcf"]), 0.01 * abs(table[e]["revenue"]))]
    if all(e in alt for e in w0):
        if off:
            notes.append(f"fcf: Finnhub margin disagrees with EDGAR in {', '.join(e[:4] for e in off)}; not used")
            return "edgar"
        if len(overlap) < FCF_MARGIN_MIN_OVERLAP:
            notes.append(f"fcf: Finnhub margin overlaps EDGAR in only {len(overlap)} year(s); not used")
            return "edgar"
        for e, r in table.items():
            r.pop("fcf", None)
            if e in alt:
                r["fcf"] = alt[e]
        notes.append(f"fcf: series from Finnhub fcfMargin × revenue (EDGAR missing {', '.join(e[:4] for e in missing)})")
        return "finnhub_margin"
    fillable = [e for e in missing if e in alt]
    edgar_years = [e for e in w0 if _ok(table[e].get("fcf"))]
    if fillable == missing and len(missing) <= FCF_FILL_MAX_YEARS and len(edgar_years) >= FCF_FILL_MIN_EDGAR \
            and not off and len(overlap) >= FCF_MARGIN_MIN_OVERLAP:
        for e in fillable:
            table[e]["fcf"] = alt[e]
        notes.append(f"fcf: {', '.join(e[:4] for e in fillable)} from Finnhub fcfMargin × revenue "
                     f"(EDGAR capex missing; {len(edgar_years)} EDGAR years agree)")
        return "edgar+finnhub_margin"
    if off:
        notes.append(f"fcf: Finnhub margin disagrees with EDGAR in {', '.join(e[:4] for e in off)}; not used")
    elif fillable and len(overlap) < FCF_MARGIN_MIN_OVERLAP:
        notes.append(f"fcf: Finnhub margin overlaps EDGAR in only {len(overlap)} year(s); not used")
    else:
        notes.append(f"fcf: EDGAR missing {', '.join(e[:4] for e in missing)}; no Finnhub margin fallback")
    return "edgar"


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
    fcf_source = _choose_fcf(table, fh, fin, notes, snapshot.currency)
    window, wnotes = _window(table, COMPLETE_FIN if fin else COMPLETE, snapshot.currency)
    notes.extend(wnotes)
    years = len(window)
    now = _Now(snapshot, table, ttm, notes)
    lae = snapshot.latest_annual_end
    fy_row = table.get(lae) or {}
    usd = snapshot.currency == "USD"
    form = (getattr(snapshot, "annual_form", None) or "").upper()
    # R39: `tenk` = a USD 10-K filer (Schwab's price is authoritative, no listing factor). A 10-K
    # filer reporting in CAD / EUR still needs the factor, so the currency is part of the test.
    tenk = (usd and form.startswith("10-K")) if form else usd
    check_data: list[str] = []          # blockers (CHECK_DATA), most important first
    caps: list[str] = []                # R40 / R41: the row can be WAIT at most; first one names the gap
    checks: dict[str, Check] = {}

    def put(key, value, passed, detail, label=None, threshold=None):
        checks[key] = Check(key, label or LABELS[key], value, threshold or THRESHOLDS[key], passed, detail)

    def cap(reason: str) -> None:
        if reason not in caps:
            caps.append(reason)

    # --- net income now ---
    ni_now, ni_basis = now.flow("net_income")
    ni_fy = ni_basis.startswith("FY")

    # --- R35: pre-revenue (no revenue > 0 in the latest FY or later, and losing money) ---
    rev_fy, rev_ttm, ni_fy_val = fy_row.get("revenue"), ttm.get("revenue"), fy_row.get("net_income")
    pre_revenue = (not (_ok(rev_fy) and rev_fy > 0) and not (_ok(rev_ttm) and rev_ttm > 0)
                   and not any(_ok(r.get("revenue")) and r["revenue"] > 0 for e, r in table.items() if e > lae)
                   and _ok(ni_fy_val) and ni_fy_val <= 0)
    if pre_revenue:
        notes.append(f"revenue: none in FY {lae} or later (pre-revenue; net income {_money(ni_fy_val)})")

    # --- debt resolution (the 0-debt rule, R41 estimates) ---
    equity_now, eq_d = now.balance("equity")
    assets_now, as_d = now.balance("assets")
    cash_now, _ = now.balance("cash")
    debt_raw, debt_d = now.balance("debt")
    leases_now, _ = now.balance("op_leases")
    interest_ok, int_detail = _interest_small(now)
    i_val, _ = now.flow("interest")
    de = fh.debt_to_equity if fh else None
    de_s = f"{de:.2f}" if _ok(de) else "n/a"
    liab = assets_now - equity_now if _ok(assets_now) and _ok(equity_now) else None
    de_eff, fh_debt, de_usable = de, None, False
    if _ok(de) and de >= 0 and _ok(equity_now) and equity_now > 0:
        est = de * equity_now
        if liab is not None and est > liab * FINNHUB_DE_SLACK:
            # CAVA: Finnhub's D/E cannot be reproduced from the balance sheet; the non-lease
            # liabilities bound what debt could hide, so they stand in for the no-debt proof
            de_eff = max(0.0, liab - (abs(leases_now) if _ok(leases_now) else 0.0)) / equity_now
            notes.append(f"Finnhub D/E {de:.2f} not reproducible (liabilities/equity {liab / equity_now:.2f}); "
                         f"non-lease liabilities/equity {de_eff:.2f} used for the no-debt proof")
        else:
            fh_debt, de_usable = est, True
    # R18/R23 no-debt proof: interest absent or < 1% of operating income AND Finnhub D/E PRESENT and
    # in [0, 0.3); a missing D/E proves nothing, nor does a negative one (negative equity)
    proof = interest_ok and _ok(de_eff) and 0 <= de_eff < NO_DEBT_DE
    debt_unresolved = False
    debt_source = "edgar"
    if _ok(debt_raw) and debt_raw > 0:
        debt_now, debt_detail = debt_raw, f"debt {debt_raw:,.0f} {debt_d}"
    elif proof:
        debt_now, debt_detail = 0.0, f"no debt (proof: {int_detail}; Finnhub D/E {de_s}; {debt_d})"
    elif not fin and fh_debt is not None and _plausible_debt(fh_debt, i_val, notes, de_s):
        # R41: no EDGAR figure, so Finnhub's D/E x equity (leases included: over-stated) is the
        # fail-safe debt; one source only, so the row is capped at WAIT
        debt_now, debt_source = fh_debt, "finnhub"
        debt_detail = f"debt ≈ {fh_debt:,.0f} (Finnhub D/E {de_s} × equity {eq_d}; EDGAR {debt_d})"
        cap(f"debt from Finnhub D/E {de_s} × equity (EDGAR debt unresolved)")
    else:
        debt_now = None
        debt_detail = f"debt unresolved ({debt_d}; {int_detail}; Finnhub D/E {de_s})"
        debt_unresolved = not fin
    if proof and debt_now == 0.0:
        # R18: only on the "no debt" branch (never when debt is tagged today), years without a debt
        # figure count as 0 in the ROIC series so consistency is computable; a year whose own
        # interest is material stays out.
        zeroed = []
        for e, r in sorted(table.items()):
            if not _ok(r.get("debt")) and _interest_small_row(r):
                r["debt"] = 0.0
                zeroed.append(e[:4])
        if zeroed:
            notes.append(f"debt: {', '.join(zeroed)} counted as 0 (no-debt proof: {int_detail}; Finnhub D/E {de_s})")

    # --- R22 / R41: debt understated? (compared at the TTM and at the fiscal year end) ---
    understated = _debt_understated(fh, fin=fin, debt=debt_now, equity=equity_now, leases=leases_now,
                                    fy_debt=fy_row.get("debt"), fy_equity=fy_row.get("equity"),
                                    fy_leases=fy_row.get("op_leases"), theirs=de if de_usable else None)
    understated_blocker = None
    if understated:
        if fh_debt is not None and _ok(debt_now) and fh_debt > debt_now:
            ours = (debt_now + (abs(leases_now) if _ok(leases_now) else 0.0)) / equity_now
            debt_now, debt_source = fh_debt, "finnhub"
            debt_detail = f"debt ≈ {fh_debt:,.0f} (Finnhub D/E {de_s} × equity {eq_d}; EDGAR {debt_detail})"
            cap(f"debt: candidates differ, larger used (EDGAR D/E {ours:.2f} vs Finnhub {de:.2f})")
            notes.append(f"debt: {understated}; larger (Finnhub D/E × equity) used")
        else:
            understated_blocker = understated

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
            cap("ROE not measurable: " + ("negative equity" if _ok(equity_now) and equity_now <= 0
                                           else "net income unknown" if not _ok(ni_now) else "equity unknown"))
    else:
        gate = roic_now
        put("roic_now", roic_now, None if roic_now is None else roic_now >= ROIC_GATE,
            f"ROIC {_pct(roic_now, True)}: {roic_detail}")
        if roic_now is None and not debt_unresolved:
            # R40: an unmeasurable ROIC is a missing cross-check, not "no data"
            if not _ok(ni_now):
                cap("ROIC not measurable: net income unknown")
            elif not (_ok(equity_now) and _ok(assets_now)):
                cap("ROIC not measurable: equity or assets unknown")
            elif equity_now <= 0:
                cap("ROIC not measurable: negative equity")
            else:
                cap("ROIC not measurable: capital under 10% of assets")

    # --- ROIC / ROE consistency ---
    rs = roic_series(table)
    series = _roe_series(table) if fin else rs
    name = "ROE" if fin else "ROIC"
    vals = [series[e] for e in window if e in series]
    need = MIN_YEARS if years < CONSISTENCY_MIN_YEARS else CONSISTENCY_MIN_YEARS   # R33: 3-4 year rows need >= 3
    if len(vals) < need:
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
        if years >= MIN_YEARS and not _growth_agree(g_eps_fh, g_eps_edgar):
            # R40: a growth disagreement uses the LOWER growth and caps the row at WAIT
            growth["eps"] = None if g_eps_fh is None or g_eps_edgar is None else min(g_eps_fh, g_eps_edgar)
            notes.append(f"EPS growth: sources differ (Finnhub {_pct(g_eps_fh)} vs EDGAR {_pct(g_eps_edgar)}); lower used")
    else:
        growth["eps"], source["eps"], eps_series = g_eps_edgar, "edgar", edgar_eps
        if fh and fh.eps_annual:
            notes.append("eps: Finnhub series does not cover the window; EDGAR split-corrected used")
    adj = adjusted_equity(table, window)
    gaps = [e[:4] for e in window[1:] if not all(_ok(table[e].get(k)) for k in ("buybacks", "dividends"))]
    if gaps and window:
        notes.append(f"equity: buybacks/dividends missing in {', '.join(gaps)} (counted as 0)")
    endpoints = 3 if years >= AVERAGED_ROWS else 1
    eq_start_bad = years >= MIN_YEARS and any(adj.get(e, 0.0) <= 0 for e in window[:endpoints])
    growth["equity"] = None if eq_start_bad else cagr(adj, window)
    if fin:
        growth["fcf"], source["fcf"] = None, "n/a"
    else:
        growth["fcf"] = cagr({e: r["fcf"] for e, r in table.items() if _ok(r.get("fcf"))}, window)
        source["fcf"] = fcf_source

    # --- fail-safe: a window year whose capex came from the LARGER of two tags, or whose EPS was
    # derived from NI / shares, is one unconfirmed source. A larger early-year capex lowers the early
    # FCF and inflates the FCF growth; derived EPS is only cross-checked by a full Finnhub series.
    wyears = {e[:4] for e in window}
    for n in snapshot.notes:
        larger = [y for y in _note_years(n, CAPEX_LARGER_NOTE, " (") if y in wyears]
        if larger and not fin:
            span = larger[0] if len(larger) == 1 else f"{larger[0]}-{larger[-1]}"
            cap(f"not cross-checked: capex from two tags (larger used in {span})")
        derived = [y for y in _note_years(n, EPS_DERIVED_NOTE, " derived from NI/") if y in wyears]
        if derived and not fh_full:
            cap(f"not cross-checked: EPS derived in {', '.join(derived)}")

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
    cfo_now, cfo_basis = now.flow("cfo")
    if fcf_now is None and not fin and _ok(fy_row.get("fcf")) and fcf_source == "edgar+finnhub_margin" \
            and not _ok(fy_row.get("capex")):
        # R37: the latest FY's FCF came from Finnhub's margin x revenue (EDGAR capex missing)
        fcf_now, fcf_basis = fy_row["fcf"], f"FY {lae} (Finnhub fcfMargin × revenue)"
        cap(f"FCF FY {lae} from Finnhub fcfMargin × revenue (EDGAR capex missing)")
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
        cap(f"FCF unknown ({fcf_basis})")
    elif fcf_now <= 0:
        put("debt", None, False, f"{debt_detail}; FCF {fcf_now:,.0f} ≤ 0 ({fcf_basis})")
    else:
        ratio = debt_now / fcf_now
        put("debt", ratio, ratio <= DEBT_FCF_MULT, f"debt {ratio:.1f}× FCF ({debt_detail}; FCF {fcf_basis})")

    # --- EDGAR EPS now: the EPS tag's TTM when the snapshot carries one, else NI / diluted shares ---
    eps_stamp = snapshot.ttm_ends.get("eps")
    ni_stamp_end = (snapshot.ttm_ends.get("net_income") or (None,))[0]
    tag_fresh = bool(eps_stamp) and (ni_stamp_end is None or
                                     abs((_d(eps_stamp[0]) - _d(ni_stamp_end)).days) <= PERIOD_MATCH_DAYS)
    if _ok(ttm.get("eps")) and tag_fresh:
        # IBKR / MCHP / BYND: the EPS tag carries the parent share, preferred dividends and the
        # if-converted method that NI / diluted shares misses
        eps_edgar, eps_basis = ttm["eps"], ("FY " if eps_stamp[1] else "TTM ") + eps_stamp[0] + ", EPS tag"
    else:
        if ni_fy:
            sh = table.get(lae, {}).get("shares")
        else:
            sh, _ = now.balance("shares")
        eps_edgar = ni_now / sh if _ok(ni_now) and _ok(sh) and sh > 0 else None
        eps_basis = ni_basis
    eps_fy_edgar = fy_row.get("eps")
    if not _ok(eps_fy_edgar) and _ok(fy_row.get("net_income")) and _ok(fy_row.get("shares")) and fy_row["shares"] > 0:
        eps_fy_edgar = fy_row["net_income"] / fy_row["shares"]

    # --- earning money now ---
    eps_fh_now = fh.eps_ttm if fh and _ok(fh.eps_ttm) else None
    eps_now = eps_fh_now if eps_fh_now is not None else eps_edgar
    eps_src = "Finnhub" if eps_fh_now is not None else "EDGAR"
    if fin:
        if eps_now is None:
            put("earning", None, None, "EPS unknown", threshold="EPS TTM > 0")
            cap("earnings unknown: EPS unknown")
        else:
            put("earning", eps_now, eps_now > 0, f"EPS {eps_now:.2f} ({eps_src})", threshold="EPS TTM > 0")
    elif eps_now is None or fcf_now is None:
        put("earning", eps_now, None, f"EPS {eps_now if eps_now is not None else 'unknown'}; FCF {fcf_basis}")
        cap("earnings unknown: EPS unknown (no shares)" if eps_now is None else f"FCF unknown ({fcf_basis})")
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
    lf_raw = listing_factor(price, fh)
    lf = 1.0 if tenk and lf_raw is not None else lf_raw          # R39: a 10-K filer has no ADR factor
    src_fail, src_caps, src_notes, unchecked = _sources(
        snapshot, fh, fin=fin, usd=usd, tenk=tenk, gate=gate, eps_edgar=eps_edgar, eps_basis=eps_basis,
        eps_fy=eps_fy_edgar, g_eps_fh=g_eps_fh if fh_full else None, fh_full=fh_full, g_eps_edgar=g_eps_edgar,
        years=years, price=price, lf=lf_raw)
    if understated_blocker:
        src_fail.append(understated_blocker)
        src_notes.append(understated_blocker)
    elif understated:
        src_notes.append(understated + " (larger used)")
    if _ok(lf_raw) and lf_raw > 0 and _ok(prior_listing_factor) and prior_listing_factor > 0:
        moved = abs(lf_raw - prior_listing_factor) / prior_listing_factor
        if moved > LISTING_FACTOR_DRIFT:
            src_fail.append(f"listing factor moved {moved * 100:.0f}%")
            src_notes.append(f"listing factor {prior_listing_factor:.4f} -> {lf_raw:.4f} (moved {moved * 100:.0f}%)")
    for c in src_caps:
        cap(c)
    if unchecked and tenk:
        cap(f"not cross-checked: price sanity ({unchecked})")
    put("sources", None, False if src_fail else (None if (src_caps or unchecked or understated) else True),
        "; ".join(src_notes))
    if fh is None:
        check_data.insert(0, "Finnhub: no data")
    else:
        pos = 1 if check_data[:1] == ["debt unresolved"] else 0
        check_data[pos:pos] = src_fail
        if unchecked and not tenk:
            check_data.append("price sanity not checked")   # last: only shows when nothing else is wrong

    # --- Big 5 and the business test ---
    five = [checks[k].passed for k in BIG5]
    big5, big5_na = five.count(True), five.count(None)
    rule1 = five.count(False) <= 1 and checks["roic_consistency"].passed is True

    # --- R35 / R38: the verdict the LESS favourable source already settles ---
    forced = "no revenue yet" if pre_revenue else _worst_case(
        fin=fin, name=name, gate=gate, fh=fh, eps_edgar=eps_edgar, eps_fh=eps_fh_now, fcf_now=fcf_now,
        cfo_now=cfo_now, debt_now=debt_now, owner=owner,
        # R46: a row under 3 years still has a TTM ROIC; when the less favourable source already
        # fails it, AVOID with the reason beats "Short history" (NEE: no capex tag, ROIC 7%).
        hard=(bool(check_data or caps) and years >= MIN_YEARS) or years < MIN_YEARS)

    # --- status ---
    if stale:
        status, reason = "STALE", stale
    elif forced:
        status, reason = None, ""                       # scored: the signal is AVOID with `forced`
    elif years < MIN_YEARS:
        status, reason = "SHORT_HISTORY", f"Short history ({years} year{'s' if years != 1 else ''})"
    elif check_data:
        r0 = check_data[0]
        status, reason = "CHECK_DATA", r0 if r0.startswith("Finnhub") else f"Check data: {r0}"
    else:
        status, reason = None, ""
    wait_cap = caps[0] if (status is None and not forced and caps) else ""
    notes.extend(f"check data: {r}" for r in (check_data if status != "CHECK_DATA" else check_data[1:]))
    notes.extend(f"wait cap: {r}" for r in (caps if not wait_cap else caps[1:]))

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
        status_reason=reason, young=MIN_YEARS <= years < FULL_YEARS, eps_ttm_usd=eps_usd, notes=notes,
        forced_avoid=forced or "", wait_cap=wait_cap, pre_revenue=pre_revenue)


def _plausible_debt(est: float, interest, notes: list[str], de_s: str) -> bool:
    """R41: a Finnhub D/E x equity estimate that the tagged interest expense could not be paid on
    (implied rate > 25%/yr, or interest with a 0 estimate) is too small to trust."""
    if not _ok(interest) or interest == 0:
        return True
    rate = abs(interest) / est if est > 0 else math.inf
    if rate > IMPLAUSIBLE_INTEREST_RATE:
        notes.append(f"debt: Finnhub D/E {de_s} × equity = {est:,.0f} is implausible "
                     f"(interest {abs(interest):,.0f} would be {rate * 100:.0f}%/yr); not used")
        return False
    return True


def _growth_agree(a, b) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= GROWTH_TOL or (a >= GROWTH_GATE and b >= GROWTH_GATE)


def _roic_agree(ours, theirs) -> bool:
    return (ours >= ROIC_GATE) == (theirs >= ROIC_GATE) or abs(ours - theirs) <= max(ROIC_TOL_PTS, ROIC_TOL_REL * abs(theirs))


def _eps_agree(ours, theirs) -> bool:
    return abs(ours - theirs) <= max(EPS_TOL_REL * abs(theirs), EPS_TOL_ABS)


def _worst_case(*, fin, name, gate, fh, eps_edgar, eps_fh, fcf_now, cfo_now, debt_now, owner, hard) -> str | None:
    """R38: the first hard check the LESS favourable source fails, in words; None when none fails.

    The earnings sign (pre-revenue aside) is judged for every row: a loss is a loss whatever the
    history. The ROIC and debt gates (`hard`) only for rows that would otherwise be CHECK_DATA or
    WAIT-capped: a clean row keeps the ordinary gates and tolerances."""
    eps = [v for v in (eps_edgar, eps_fh) if _ok(v)]
    if eps and min(eps) <= 0:
        return "losing money" + (" (sources differ on the size)" if max(eps) > 0 else "")
    if not fin:
        if _ok(fcf_now) and fcf_now <= 0:
            return "no free cash flow" if eps else "losing money"
        if fcf_now is None and _ok(cfo_now) and cfo_now <= 0:
            return "cash from operations negative"           # capex unknown => FCF <= CFO <= 0
        if _ok(fcf_now) and _ok(owner) and owner <= 0:
            return "owner earnings negative after stock comp"
    if not hard:
        return None
    theirs = (fh.roe_ttm if fin else fh.roi_ttm) if fh is not None else None
    theirs = theirs / 100.0 if _ok(theirs) else None
    cands: list[tuple[float, str]] = []
    if _ok(gate):
        cands.append((gate, "EDGAR"))
        if theirs is not None and not _roic_agree(gate, theirs):
            cands.append((theirs, "Finnhub"))
        if _ok(eps_edgar) and _ok(eps_fh) and 0 < eps_fh < eps_edgar and not _eps_agree(eps_edgar, eps_fh):
            cands.append((gate * eps_fh / eps_edgar, "EPS"))       # ROIC on the lower EPS
    elif theirs is not None:
        cands.append((theirs, "Finnhub"))
    if cands:
        lo, src = min(cands, key=lambda c: c[0])
        if lo < ROIC_GATE:
            if src == "Finnhub":
                return f"{name} {_pct(lo, True)} < 15% (Finnhub; " + \
                    ("EDGAR not measurable)" if not _ok(gate) else f"sources differ on {name})")
            if src == "EPS":
                return f"{name} {_pct(lo, True)} < 15% (sources differ on EPS)"
            return f"{name} {_pct(lo, True)} < 15%"
    if not fin and _ok(debt_now) and debt_now > 0 and _ok(fcf_now) and fcf_now > 0:
        ratio = debt_now / fcf_now
        if ratio > DEBT_FCF_MULT:
            return f"debt {ratio:.1f}× FCF"
    return None


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


def _debt_understated(fh: FinnhubExtract | None, *, fin: bool, debt, equity, leases, fy_debt=None, fy_equity=None,
                      fy_leases=None, theirs="finnhub") -> str | None:
    """R22: our (debt + operating leases) / equity far below Finnhub's D/E means a debt tag was
    missed. Finnhub's figure is FY-end based (PL, STX): when the FY-end comparison agrees, an
    equity raise since then explains the gap and the flag is dropped. `theirs` None = Finnhub's D/E
    is not reproducible (already noted) and proves nothing."""
    if fin or fh is None or not _ok(fh.debt_to_equity) or not _ok(debt) or not _ok(equity) or equity <= 0:
        return None
    if theirs is None:
        return None
    theirs = fh.debt_to_equity

    def ours_of(d, e, l):
        if not (_ok(d) and _ok(e)) or e <= 0:
            return None
        return (d + (abs(l) if _ok(l) else 0.0)) / e

    def flagged(ours):
        return ours is not None and theirs >= DEBT_UNDERSTATED_RATIO * ours and theirs - ours > DEBT_UNDERSTATED_GAP

    ours_now = ours_of(debt, equity, leases)
    ours_fy = ours_of(fy_debt, fy_equity, fy_leases)
    if not flagged(ours_now):
        return None
    if ours_fy is not None and not flagged(ours_fy):
        return None
    return f"debt understated? EDGAR D/E {ours_now:.2f} vs Finnhub {theirs:.2f}"


def _interest_small_row(r: dict) -> bool:
    if not _ok(r.get("interest")):
        return True
    return _ok(r.get("op_income")) and r["op_income"] > 0 and abs(r["interest"]) / r["op_income"] < NO_DEBT_INTEREST_SHARE


def _sources(snap: Snapshot, fh: FinnhubExtract | None, *, fin, usd, tenk, gate, eps_edgar, eps_basis, eps_fy,
             g_eps_fh, fh_full, g_eps_edgar, years, price, lf) -> tuple[list[str], list[str], list[str], str | None]:
    """(failure reasons, R40 cap reasons, detail parts, price sanity not checked why) for check 11."""
    if fh is None:
        return ["Finnhub: no data"], [], ["Finnhub: no data"], None
    fails, caps, parts = [], [], []
    # ROIC (ROE for financials): same side of 15%, or within max(5 points, 25% relative)
    name = "ROE" if fin else "ROIC"
    theirs = fh.roe_ttm if fin else fh.roi_ttm
    if gate is None:
        parts.append(f"{name}: EDGAR n/a" + (f" (Finnhub {_pct(theirs / 100.0, True)})" if _ok(theirs) else ""))
    elif not _ok(theirs):
        caps.append(f"not cross-checked: Finnhub {name} missing")
        parts.append(f"{name}: no Finnhub value")
    else:
        b = theirs / 100.0
        agree = _roic_agree(gate, b)
        msg = f"{name} {_pct(gate, True)} vs Finnhub {_pct(b, True)}"
        parts.append(msg + (" ok" if agree else ""))
        if not agree:
            fails.append(msg)
    # EPS (USD filers): EDGAR EPS vs Finnhub, same period
    if not usd:
        parts.append("EPS: skipped (non-USD filer)")
    else:
        fh_eps = fh.eps_incl_ttm if _ok(fh.eps_incl_ttm) else fh.eps_ttm
        lae = snap.latest_annual_end
        fh_fy = _match(fh.eps_annual, lae) if lae and fh.eps_annual else None
        # Finnhub gives no TTM period date, so its latest ANNUAL period (eps or pe series) stands
        # in for "Finnhub's period"; > 100 days from EDGAR's latest annual end = periods differ.
        periods = [p for p in list(fh.eps_annual) + list(fh.pe_annual)]
        fh_end = max(periods) if periods else None
        if eps_basis.startswith("FY") and _ok(eps_fy) and fh_fy is not None:
            # an annual-only filer (20-F / 40-F, or a 10-K with no 10-Q yet): EDGAR's "TTM" is the FY,
            # Finnhub's TTM runs later; compare the FY with Finnhub's annual EPS for the same FY
            agree = _eps_agree(eps_fy, fh_fy)
            msg = f"EPS {eps_fy:.2f} (FY {lae}) vs Finnhub FY {fh_fy:.2f}"
            parts.append(msg + (" ok" if agree else ""))
            if not agree:
                fails.append(msg)
            elif fh_end and (_d(fh_end) - _d(lae)).days > PERIOD_DIFFER_DAYS:
                # R40: Finnhub already has a LATER fiscal year than EDGAR's latest annual row, so the
                # TTM in use, the growth series and the P/E median all run past anything EDGAR can
                # confirm, even when the FY EPS agree and the TTM is within tolerance
                parts.append(f"periods differ (Finnhub FY {fh_end} vs EDGAR {lae})")
                caps.append("not cross-checked: " + parts[-1])
            elif not _ok(fh_eps):
                caps.append("not cross-checked: Finnhub EPS missing")
            elif not _eps_agree(fh_eps, eps_fy):
                # the TTM in use (Finnhub's) is newer than anything EDGAR can confirm (WB)
                parts.append(f"Finnhub EPS TTM {fh_eps:.2f} newer than EDGAR FY {eps_fy:.2f}")
                caps.append("not cross-checked: " + parts[-1])
        elif fh_end and lae and abs((_d(fh_end) - _d(lae)).days) > PERIOD_DIFFER_DAYS:
            parts.append(f"periods differ (Finnhub FY {fh_end} vs EDGAR {lae})")
            caps.append("not cross-checked: " + parts[-1])
        elif not _ok(fh_eps):
            parts.append("EPS: Finnhub EPS missing")
            caps.append("not cross-checked: Finnhub EPS missing")
        elif eps_edgar is None:
            parts.append("EPS: EDGAR shares missing")
            caps.append("not cross-checked: EDGAR shares missing")
        else:
            agree = _eps_agree(eps_edgar, fh_eps)
            msg = f"EPS {eps_edgar:.2f} ({eps_basis}) vs Finnhub {fh_eps:.2f}"
            parts.append(msg + (" ok" if agree else ""))
            if not agree:
                fails.append(msg)
    # EPS growth: Finnhub series vs EDGAR split-corrected
    if not fh_full and not fin:
        # R17 / R40: a non-financial row's EPS growth needs Finnhub's series too; one source caps at WAIT
        parts.append("EPS growth: one source only")
        caps.append("not cross-checked: EPS growth: one source only")
    elif not fh_full:
        parts.append("EPS growth: no Finnhub series (EDGAR used)")
    elif years >= MIN_YEARS:
        a, b = g_eps_fh, g_eps_edgar
        agree = _growth_agree(a, b)
        msg = f"EPS growth Finnhub {_pct(a)} vs EDGAR {_pct(b)}"
        parts.append(msg + (" ok" if agree else " (lower used)"))
        if not agree:
            caps.append(f"not cross-checked: {msg} (lower used)")
    # Price sanity: a missing input is never a silent pass
    unchecked = None
    if _ok(fh.eps_ttm) and fh.eps_ttm <= 0:
        # no earnings means no P/E: nothing to check, and the earning check already fails the row
        parts.append("price sanity: n/a (no earnings)")
    elif not _ok(price) or price <= 0:
        parts.append("price sanity: not checked (no price)")
        unchecked = "no price"
    elif lf is None:
        parts.append("price sanity: not checked (no P/E)")
        unchecked = "no P/E"
    elif lf == 1.0:
        parts.append("price sanity ok")
    elif tenk:
        # R39: Schwab's price is authoritative for a USD 10-K filer; Finnhub's EPS x P/E lags
        parts.append(f"price sanity: price {price:.2f} vs Finnhub EPS × P/E {fh.eps_ttm * fh.pe_ttm:.2f} (note only, 10-K filer)")
    elif usd:
        fails.append(f"price sanity (price {price:.2f} vs Finnhub EPS × P/E {fh.eps_ttm * fh.pe_ttm:.2f})")
        parts.append(fails[-1])
    else:
        parts.append(f"listing factor {lf:.4f} (ADR ratio ÷ FX)")
    return fails, caps, parts, unchecked
