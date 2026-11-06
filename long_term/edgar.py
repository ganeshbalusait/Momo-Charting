"""Long Term: EDGAR company-facts -> one annual table per company.

Fact selection, the tag table, per-company tag resolution, units and split
inference, TTM and date stamps: pure functions over the parsed companyfacts
JSON. `EdgarClient` is the only network code (paced, injectable getter).

Rules (spec "Fact selection rules (EDGAR)"):
* annual row = form in ANNUAL_FORMS and 350-380 days long (instants: end only);
  never fy/fp/frame; key by period end; latest `filed` wins, then accession.
* one unit per concept per company: most annual years; USD only on >= coverage.
* tag resolution per company: the primary is a candidate covering the latest
  annual year (most coverage among those; table order for debt/capex); another
  candidate fills missing years only if it agrees within 2% in every overlap
  year, else the year stays blank and the series is a "tag break" (debt and
  capex fill without the 2% rule, noted).
* splits are inferred from the same period carrying different EPS in
  different filings (clean ratios only).
* TTM flow = latest annual + YTD - prior YTD of the same 10-Q accession, else
  the annual ("TTM = FY"); balance items = latest instant of any period form.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F"})
MIN_DAYS, MAX_DAYS = 350, 380
AGREE = 0.02                      # fill tolerance between candidate tags
SPLIT_TOL = 0.03                  # relative tolerance when snapping a ratio to a clean split
SPLIT_MIN_EPS = 0.10              # below this, cent rounding makes EPS ratios meaningless
# 8 is not in the spec's list but 1-for-8 reverse splits are common (and the brief's test uses one).
_CLEAN = (1.5, 2.0, 3.0, 4.0, 5.0, 7.0, 8.0, 10.0, 20.0, 50.0)
CLEAN_RATIOS = tuple(sorted(set(_CLEAN) | {1.0 / r for r in _CLEAN}))

IFRS = "ifrs-full:"
# One table, us-gaap first then ifrs-full. Bare names are us-gaap; "ifrs-full:" names are IFRS.
# A tuple is a sum recipe (all non-optional parts must exist that year).
CONCEPTS: dict[str, list[str | tuple[str, ...]]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                ("InterestAndDividendIncomeOperating", "NoninterestIncome"), IFRS + "Revenue"],
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                   IFRS + "ProfitLossAttributableToOwnersOfParent", IFRS + "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasic",
            IFRS + "DilutedEarningsLossPerShare", IFRS + "BasicEarningsLossPerShare"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               IFRS + "EquityAttributableToOwnersOfParent", IFRS + "Equity"],
    "debt": [("LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"),
             ("LongTermDebt", "ShortTermBorrowings", "CommercialPaper"),
             ("DebtCurrent", "LongTermDebtNoncurrent"),
             "LongTermDebtAndCapitalLeaseObligations",
             (IFRS + "NoncurrentBorrowings", IFRS + "CurrentBorrowings"),
             (IFRS + "LongtermBorrowings", IFRS + "ShorttermBorrowings"),
             IFRS + "Borrowings",
             # audit 2026-11-06 (first pass): real tags on filers the recipes above missed
             "NotesPayable",                                                        # DHI, GAP
             ("ConvertibleLongTermNotesPayable", "ConvertibleNotesPayableCurrent"),  # DASH, DDOG, AEVA
             "DebtAndCapitalLeaseObligations",                                      # F
             "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",    # GM
             "LoansPayable",                                                        # GAP
             "OtherNotesPayableCurrent",                                            # AEVA
             # audit round 2: real debt the proof would otherwise call "no debt"
             "LongTermDebtNoncurrent",                                              # CRWD, REGN, MRNA, TEAM
             ("ConvertibleDebtNoncurrent", "ConvertibleDebtCurrent"),               # PANW
             "ConvertibleNotesPayable",                                             # DHI
             # SPOT: exchangeable notes sit in current borrowings (LongtermBorrowings is 0)
             (IFRS + "LongtermBorrowings", IFRS + "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings")],
    "op_leases": ["OperatingLeaseLiability"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             IFRS + "CashAndCashEquivalents"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            IFRS + "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment",                     # LLY
              IFRS + "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"],
    "sbc": ["ShareBasedCompensation"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock"],
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "op_income": ["OperatingIncomeLoss", IFRS + "ProfitLossFromOperatingActivities"],
    "assets": ["Assets", IFRS + "Assets"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfSharesOutstandingBasic",
               IFRS + "WeightedAverageNumberOfDilutedSharesOutstanding"],
    "split_ratio": ["StockholdersEquityNoteStockSplitConversionRatio1"],   # informational only
}
# Revenue: when several of these exist in a year, take the largest (one combined candidate).
LARGER_OF = {"revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                         "RevenueFromContractWithCustomerIncludingAssessedTax")}
# Recipe parts that count as 0 when missing (ifrs ShorttermBorrowings is the IFRS twin of ShortTermBorrowings).
OPTIONAL_PARTS = frozenset({"ShortTermBorrowings", "CommercialPaper", IFRS + "ShorttermBorrowings",
                            "ConvertibleNotesPayableCurrent", "ConvertibleDebtCurrent"})
# Fill-only candidates: they may fill missing years under the 2% rule but are never primary
# (unless nothing else exists for that concept).
FILL_ONLY = frozenset({"NetIncomeLossAvailableToCommonStockholdersBasic", "EarningsPerShareBasic",
                       "WeightedAverageNumberOfSharesOutstandingBasic", IFRS + "BasicEarningsLossPerShare"})
# Every concept's primary candidate is one that covers the LATEST annual year (R19). Among those,
# ordered concepts take table order (the debt recipes are listed best-first), the rest the most
# coverage. Ordered concepts also fill the primary's missing years from the other candidates in
# table order WITHOUT the 2% rule (the tags measure the same thing with structural differences:
# commercial paper and leases for debt; AMZN's PP&E vs productive-assets capex), noted, no tag break.
ORDERED_CONCEPTS = frozenset({"debt", "capex"})
CAPEX_FILL_MAX_DIFF = 0.25        # capex: a candidate more than 25% off in an overlap year never fills
# Single-part debt recipes that can miss part of the debt (R22): noted when they carry the latest year.
PARTIAL_DEBT = frozenset({"NotesPayable", "LoansPayable", "OtherNotesPayableCurrent", "LongTermDebtNoncurrent"})
SHARES_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding", IFRS + "WeightedAverageNumberOfDilutedSharesOutstanding")
INSTANT_CONCEPTS = frozenset({"equity", "debt", "op_leases", "cash", "assets"})
MAGNITUDE_CONCEPTS = frozenset({"capex"})
TABLE_CONCEPTS = tuple(c for c in CONCEPTS if c != "split_ratio")
# Fetched (fixtures/trimmer) but not resolved into a concept here.
EXTRA_TAGS = {"us-gaap": ["Liabilities", "DepreciationDepletionAndAmortization", "IncomeTaxExpenseBenefit"],
              "ifrs-full": ["PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", "Liabilities"],
              "dei": ["EntityCommonStockSharesOutstanding"]}


def _split_name(name: str) -> tuple[str, str]:
    return ("ifrs-full", name[len(IFRS):]) if name.startswith(IFRS) else ("us-gaap", name)


def _tags_by_namespace() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"us-gaap": [], "ifrs-full": [], "dei": []}
    for cands in CONCEPTS.values():
        for cand in cands:
            for name in (cand if isinstance(cand, tuple) else (cand,)):
                ns, tag = _split_name(name)
                if tag not in out[ns]:
                    out[ns].append(tag)
    for ns, tags in EXTRA_TAGS.items():
        out[ns].extend(t for t in tags if t not in out[ns])
    return out


TAGS_BY_NAMESPACE: dict[str, list[str]] = _tags_by_namespace()
ALL_TAGS: frozenset[str] = frozenset(t for tags in TAGS_BY_NAMESPACE.values() for t in tags)


@dataclass
class Fact:
    end: str
    start: str | None
    val: float
    unit: str
    tag: str
    form: str
    accn: str
    filed: str


@dataclass
class Series:
    """One resolved concept: every value in one unit."""
    values: dict[str, float]
    unit: str
    tag: str                                   # primary candidate label
    tag_break: bool = False
    filed: dict[str, str] = field(default_factory=dict)    # end -> filed date of the value used
    source: dict[str, str] = field(default_factory=dict)   # end -> candidate label that supplied it
    disagree: dict[str, int] = field(default_factory=dict)  # candidate label -> overlap years off by > 2%
    unchecked: dict[str, list[str]] = field(default_factory=dict)  # debt/capex: label -> ends filled without 2% rule
    basic_only: bool = False                   # primary is a fill-only tag (nothing better had data)
    far_off: list[str] = field(default_factory=list)  # capex: candidates refused for differing > 25%
    forms: dict[str, str] = field(default_factory=dict)    # end -> form of the fact used (10-K, 20-F...)


@dataclass
class Snapshot:
    symbol: str
    cik: int
    name: str
    sic: int | None
    currency: str | None
    annual: dict[str, dict[str, float]]
    units: dict[str, str]
    tags: dict[str, str]
    tag_breaks: list[str]
    ttm: dict[str, float]
    ttm_period_end: str | None
    ttm_is_fy: bool
    latest_annual_end: str | None
    latest_period_end: str | None
    fetched_at: str
    split_factor: float
    notes: list[str]
    # Per-concept stamps (Task 3 review): flow -> (period end, is_fy); balance/shares -> date used.
    ttm_ends: dict[str, tuple[str, bool]] = field(default_factory=dict)
    balance_ends: dict[str, str] = field(default_factory=dict)
    # Form of the latest annual net_income fact ("10-K", "10-K/A", "20-F", "40-F"...); None if unknown.
    annual_form: str | None = None


# ---------- fact selection ----------

def _days(start: str, end: str) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def to_facts(facts_by_unit: dict, tag: str = "") -> list[Fact]:
    """Every row of one tag's `units` block as Facts (rows without an end or a numeric val skipped)."""
    out = []
    for unit, rows in (facts_by_unit or {}).items():
        for r in rows or []:
            val = _num(r.get("val")) if isinstance(r, dict) else None
            if val is None or r.get("end") is None:
                continue
            out.append(Fact(str(r["end"]), r.get("start"), val, unit, tag,
                            str(r.get("form", "")), str(r.get("accn", "")), str(r.get("filed", ""))))
    return out


def is_annual(f: Fact, forms=ANNUAL_FORMS, *, instant: bool | None = None) -> bool:
    """`instant`: True/False when the concept is known to be a balance/flow item, None = infer."""
    if f.form not in forms:
        return False
    if not f.start:
        return instant is not False           # instant: by end only; a flow row without start is junk
    try:
        return MIN_DAYS <= _days(f.start, f.end) <= MAX_DAYS
    except ValueError:
        return False


def _newer(a: Fact, b: Fact) -> bool:
    return (a.filed, a.accn) > (b.filed, b.accn)


def annual_facts(facts_by_unit: dict, *, forms=ANNUAL_FORMS, tag: str = "",
                 instant: bool | None = None) -> dict[str, dict[str, Fact]]:
    """unit -> period end -> latest-filed annual Fact (tie-break: accession)."""
    out: dict[str, dict[str, Fact]] = {}
    for f in to_facts(facts_by_unit, tag):
        if not is_annual(f, forms, instant=instant):
            continue
        per = out.setdefault(f.unit, {})
        cur = per.get(f.end)
        if cur is None or _newer(f, cur):
            per[f.end] = f
    return {u: dict(sorted(p.items())) for u, p in out.items() if p}


# ---------- units ----------

def _is_usd(unit: str) -> bool:
    return unit == "USD" or unit.startswith("USD/")


def pick_unit(per_unit: dict[str, dict], *, prefer: str | None = None) -> str | None:
    """The unit covering the most annual years; USD only if it covers >= the best local unit.

    `prefer` (the company's reporting currency, used by build_snapshot) wins when it
    covers at least as many years as any other unit, so one company's money
    concepts do not end up in two currencies on a coverage tie.
    """
    counts = {u: len(v or {}) for u, v in (per_unit or {}).items() if v}
    if not counts:
        return None
    if prefer in counts and counts[prefer] >= max(counts.values()):
        return prefer
    usd = [u for u in counts if _is_usd(u)]
    local = [u for u in counts if not _is_usd(u)]
    best_local = max(local, key=lambda u: counts[u]) if local else None   # max keeps first on ties
    best_usd = max(usd, key=lambda u: counts[u]) if usd else None
    if best_usd and (best_local is None or counts[best_usd] >= counts[best_local]):
        return best_usd
    return best_local


# ---------- tag resolution ----------

def _cand_label(cand) -> str:
    return "+".join(cand) if isinstance(cand, tuple) else cand


def _node_rows(gaap: dict, ifrs: dict, name: str, instant: bool | None = None) -> dict[str, dict[str, Fact]]:
    ns, tag = _split_name(name)
    src = ifrs if ns == "ifrs-full" else gaap
    node = (src or {}).get(tag)
    if not node:
        return {}
    return annual_facts(node.get("units") or {}, tag=name, instant=instant)


def _candidate(gaap: dict, ifrs: dict, cand, instant: bool | None = None) -> dict[str, dict[str, Fact]]:
    """unit -> end -> Fact (recipes yield synthetic Facts whose val is the sum)."""
    if not isinstance(cand, tuple):
        return _node_rows(gaap, ifrs, cand, instant)
    parts = {p: _node_rows(gaap, ifrs, p, instant) for p in cand}
    required = [p for p in cand if p not in OPTIONAL_PARTS]
    units = set.intersection(*(set(parts[p]) for p in required)) if required else set()
    label = _cand_label(cand)
    out: dict[str, dict[str, Fact]] = {}
    for u in units:
        ends = set.intersection(*(set(parts[p][u]) for p in required))
        for e in sorted(ends):
            used = [parts[p][u][e] for p in cand if e in parts[p].get(u, {})]
            total = sum(f.val for f in used)
            last = max(used, key=lambda f: (f.filed, f.accn))
            out.setdefault(u, {})[e] = Fact(e, last.start, total, u, label, last.form, last.accn, last.filed)
    return out


def _larger_of(a: dict[str, dict[str, Fact]], b: dict[str, dict[str, Fact]]) -> dict[str, dict[str, Fact]]:
    out: dict[str, dict[str, Fact]] = {}
    for u in set(a) | set(b):
        ra, rb = a.get(u, {}), b.get(u, {})
        for e in set(ra) | set(rb):
            fa, fb = ra.get(e), rb.get(e)
            out.setdefault(u, {})[e] = fa if fb is None else fb if fa is None else (fa if fa.val >= fb.val else fb)
    return out


def _candidates(gaap: dict, ifrs: dict, concept: str) -> list[tuple[str, dict[str, dict[str, Fact]]]]:
    cands = CONCEPTS[concept]
    instant = None if concept == "split_ratio" else concept in INSTANT_CONCEPTS
    pair = LARGER_OF.get(concept)
    out = []
    for cand in cands:
        if pair and cand in pair[1:]:
            continue                                      # folded into pair[0]
        rows = _candidate(gaap, ifrs, cand, instant)
        label = _cand_label(cand)
        if pair and cand == pair[0]:
            for other in pair[1:]:
                rows = _larger_of(rows, _candidate(gaap, ifrs, other, instant))
            label = "|".join(pair)
        if rows:
            out.append((label, rows))
    return out


def _off_by(a: float, b: float) -> float:
    scale = max(abs(a), abs(b))
    return 0.0 if scale == 0 else abs(abs(a) - abs(b)) / scale


def _agrees(a: float, b: float) -> bool:
    scale = max(abs(a), abs(b))
    return scale == 0 or abs(a - b) <= AGREE * scale


def resolve_series(gaap: dict, ifrs: dict, concept: str, *, year_ends: set[str] | None = None,
                   prefer_currency: str | None = None) -> Series | None:
    """Per-company resolution of one concept (see module docstring)."""
    cands = _candidates(gaap or {}, ifrs or {}, concept)
    if year_ends is not None:
        cands = [(lab, {u: {e: f for e, f in r.items() if e in year_ends} for u, r in rows.items()})
                 for lab, rows in cands]
    union: dict[str, set] = {}
    for _, rows in cands:
        for u, r in rows.items():
            union.setdefault(u, set()).update(r)
    prefer = None
    if prefer_currency:
        prefer = next((u for u in union if u.split("/")[0] == prefer_currency), None)
    unit = pick_unit({u: dict.fromkeys(s) for u, s in union.items()}, prefer=prefer)
    if unit is None:
        return None
    ranked = [(lab, rows.get(unit, {})) for lab, rows in cands if rows.get(unit)]
    if not ranked:
        return None
    # R19: the primary covers the latest annual year any (non-fill-only) candidate has, so a tag
    # that stopped years ago never outranks the one the company files today (TEAM, AMZN capex).
    pool = [c for c in ranked if c[0] not in FILL_ONLY] or ranked
    latest = max(e for _, rows in pool for e in rows)
    covering = [c for c in pool if latest in c[1]]
    if concept in ORDERED_CONCEPTS:
        # table order: the first candidate covering the latest year; the rest fill in table order
        # a recipe showing 0 in the latest year never outranks a later one showing a value there:
        # a 0 from a partial recipe is "a tag missed", not proof of no debt (SPOT)
        first = next((c for c in covering if c[1][latest].val != 0), covering[0])
        ranked = [first] + [c for c in ranked if c is not first]
    else:
        # most coverage among those covering the latest year (ties keep table order: sorted is
        # stable); the rest by coverage, fill-only tags last
        first = sorted(covering, key=lambda c: -len(c[1]))[0]
        rest = sorted((c for c in ranked if c is not first), key=lambda c: -len(c[1]))
        ranked = [first] + [c for c in rest if c[0] not in FILL_ONLY] + [c for c in rest if c[0] in FILL_ONLY]
    plabel, prim = ranked[0]
    chosen: dict[str, Fact] = dict(prim)
    source = {e: plabel for e in prim}
    refused: set[str] = set()                            # years a disagreeing candidate could have filled
    disagree: dict[str, int] = {}
    unchecked: dict[str, list[str]] = {}
    far_off: list[str] = []
    for lab, rows in ranked[1:]:
        overlap = [e for e in rows if e in prim]
        off = sum(1 for e in overlap if not _agrees(rows[e].val, prim[e].val))
        if off and concept not in ORDERED_CONCEPTS:          # ordered concepts disagree by design
            disagree[lab] = off
        missing = [e for e in rows if e not in chosen]
        if not missing:
            continue
        if concept in ORDERED_CONCEPTS:
            # Same thing measured with structural differences (see ORDERED_CONCEPTS), so missing
            # years are filled in table order without the 2% rule; capex refuses a tag > 25% off.
            if concept == "capex" and any(_off_by(rows[e].val, prim[e].val) > CAPEX_FILL_MAX_DIFF for e in overlap):
                far_off.append(lab)
                continue
            for e in missing:
                chosen[e] = rows[e]
                source[e] = lab
            unchecked[lab] = sorted(missing)
        elif overlap and not off:
            for e in missing:
                chosen[e] = rows[e]
                source[e] = lab
        else:
            refused.update(missing)                       # disagrees, or nothing to check it against
    broke = any(e not in chosen for e in refused)         # a year left blank because tags disagree
    tag = plabel
    if "|" in plabel:                                     # combined pair: name the tag that supplied most years
        names = [f.tag for e, f in chosen.items() if source[e] == plabel]
        tag = max(set(names), key=names.count) if names else plabel
    mag = concept in MAGNITUDE_CONCEPTS
    ends = sorted(chosen)
    return Series(values={e: abs(chosen[e].val) if mag else chosen[e].val for e in ends}, unit=unit, tag=tag,
                  tag_break=broke, filed={e: chosen[e].filed for e in ends}, source={e: source[e] for e in ends},
                  disagree=disagree, unchecked=unchecked, basic_only=plabel in FILL_ONLY, far_off=far_off,
                  forms={e: chosen[e].form for e in ends})


def resolve_concept(gaap: dict, ifrs: dict, concept: str, *,
                    year_ends: set[str] | None = None) -> tuple[dict[str, float], str, str, bool] | None:
    """(end -> value, unit, primary tag, tag_break) or None when nothing resolves."""
    s = resolve_series(gaap, ifrs, concept, year_ends=year_ends)
    return None if s is None else (s.values, s.unit, s.tag, s.tag_break)


# ---------- splits ----------

def _clean(ratio: float) -> float | None:
    for c in CLEAN_RATIOS:
        if abs(ratio / c - 1.0) <= SPLIT_TOL:
            return c
    return None


def split_events(eps_rows: list[Fact], share_rows: list[Fact] | None = None) -> list[tuple[str, str, float]]:
    """[(last filed date before the split, first filed date after it, ratio)].

    A ratio comes from consecutive annual filings of the same period end
    (later value / earlier value) that snaps to a clean split ratio. The same
    split seen through several period ends is merged (its window narrowed).
    When diluted share counts for that period exist in both filings they must
    move by the inverse ratio (within 3%), else the pair is not a split.
    """
    return _split_scan(eps_rows, share_rows)[0]


def _share_lookup(share_rows: list[Fact] | None) -> dict[tuple[str, str], float]:
    """(period end, accession or filed date) -> diluted shares reported in that filing."""
    out: dict[tuple[str, str], float] = {}
    for f in share_rows or []:
        if is_annual(f, instant=False):
            out[(f.end, "a:" + f.accn)] = f.val
            out.setdefault((f.end, "f:" + f.filed), f.val)
    return out


def _shares_in(look: dict, f: Fact) -> float | None:
    return look.get((f.end, "a:" + f.accn), look.get((f.end, "f:" + f.filed)))


def _split_scan(eps_rows: list[Fact], share_rows: list[Fact] | None = None
                ) -> tuple[list[tuple[str, str, float]], list[str]]:
    """(events, period ends whose EPS jump was contradicted by the share counts)."""
    look = _share_lookup(share_rows)
    unconfirmed: set[str] = set()
    by_end: dict[tuple[str, str], list[Fact]] = {}
    for f in eps_rows:
        if is_annual(f) and f.start:
            by_end.setdefault((f.unit, f.end), []).append(f)
    events: list[list] = []
    for rows in by_end.values():
        latest: dict[str, Fact] = {}
        for f in rows:                                    # one value per filing date
            if f.filed not in latest or _newer(f, latest[f.filed]):
                latest[f.filed] = f
        seq = [latest[k] for k in sorted(latest)]
        for a, b in zip(seq, seq[1:]):
            if min(abs(a.val), abs(b.val)) < SPLIT_MIN_EPS:
                continue
            r = _clean(b.val / a.val)
            if r is None or r == 1.0:
                continue
            sa, sb = _shares_in(look, a), _shares_in(look, b)
            if sa and sb and abs((sb / sa) * r - 1.0) > SPLIT_TOL:
                unconfirmed.add(a.end)                    # shares did not move inversely: not a split
                continue
            for ev in events:
                if ev[2] == r and max(ev[0], a.filed) < min(ev[1], b.filed):
                    ev[0], ev[1] = max(ev[0], a.filed), min(ev[1], b.filed)
                    break
            else:
                events.append([a.filed, b.filed, r])
    return sorted((lo, hi, r) for lo, hi, r in events), sorted(unconfirmed)


def factor_at(events: list[tuple[str, str, float]], filed: str) -> float:
    """Multiplier that brings a per-share value filed on `filed` to today's share basis."""
    f = 1.0
    for _lo, hi, r in events:
        if filed < hi:                                    # filed before the split was reflected
            f *= r
    return f


def infer_split_factors(eps_rows: list[Fact], share_rows: list[Fact] | None = None) -> dict[str, float]:
    """period end -> cumulative factor from that period's ORIGINAL report to today's share basis.

    GOOGL: FY2018-FY2021 were first reported before the 2022 20:1 split -> 1/20;
    FY2022+ -> 1.0. (A value already restated by a later filing needs the factor
    at ITS filing date instead: `factor_at(split_events(rows), fact.filed)`.)
    """
    events = split_events(eps_rows, share_rows)
    first: dict[str, str] = {}
    for f in eps_rows:
        if is_annual(f) and f.start and (f.end not in first or f.filed < first[f.end]):
            first[f.end] = f.filed
    return {e: factor_at(events, filed) for e, filed in sorted(first.items())}


def apply_split(series: dict[str, float], factors: dict[str, float]) -> dict[str, float]:
    """Multiply each value by its factor (missing factor = 1). For share counts pass 1/factor."""
    return {e: v * factors.get(e, 1.0) for e, v in series.items()}


# ---------- snapshot ----------

def _eps_rows(gaap: dict, ifrs: dict, s: Series) -> list[Fact]:
    """All annual facts (every filing) of the EPS primary tag, in the series' unit."""
    ns, tag = _split_name(s.tag)
    node = ((ifrs if ns == "ifrs-full" else gaap) or {}).get(tag) or {}
    return [f for f in to_facts({s.unit: (node.get("units") or {}).get(s.unit, [])}, s.tag) if is_annual(f)]


def build_snapshot(symbol: str, cik: int, companyfacts: dict, *, sic: int | None, name: str,
                   fetched_at: str) -> Snapshot:
    facts = (companyfacts or {}).get("facts") or {}
    gaap, ifrs = facts.get("us-gaap") or {}, facts.get("ifrs-full") or {}
    series: dict[str, Series] = {}
    # The reporting currency is net income's unit (fallback revenue, then equity); every other
    # money concept prefers it on a coverage tie.
    currency = None
    for c in ("net_income", "revenue", "equity"):
        s = resolve_series(gaap, ifrs, c)
        if s:
            currency = s.unit
            break
    for c in TABLE_CONCEPTS:
        if c not in INSTANT_CONCEPTS:
            s = resolve_series(gaap, ifrs, c, prefer_currency=currency)
            if s:
                series[c] = s
    # Balance-sheet instants only at fiscal year ends (drops e.g. lease-adoption dates).
    year_ends = set().union(*(series[c].values for c in ("revenue", "net_income", "cfo") if c in series)) or None
    for c in TABLE_CONCEPTS:
        if c in INSTANT_CONCEPTS:
            s = resolve_series(gaap, ifrs, c, year_ends=year_ends, prefer_currency=currency)
            if s:
                series[c] = s

    notes: list[str] = []
    split_factor = 1.0
    eps = series.get("eps")
    if eps:
        shares_rows: list[Fact] = []
        for sname in SHARES_TAGS:
            ns, stag = _split_name(sname)
            node = ((ifrs if ns == "ifrs-full" else gaap) or {}).get(stag) or {}
            shares_rows += to_facts(node.get("units") or {}, sname)
        events, unconfirmed = _split_scan(_eps_rows(gaap, ifrs, eps), shares_rows)
        notes.extend(f"eps: unconfirmed split at {e}" for e in unconfirmed)
        if events:
            for c in ("eps", "shares"):
                s = series.get(c)
                if not s:
                    continue
                f = {e: factor_at(events, s.filed[e]) for e in s.values}
                s.values = apply_split(s.values, f if c == "eps" else {e: 1.0 / v for e, v in f.items()})
            oldest = min(eps.values)
            split_factor = factor_at(events, eps.filed[oldest])
            notes.append("splits: " + ", ".join(f"x{r:g} after {lo}" for lo, _hi, r in events))

    for c, s in series.items():
        money = s.unit.split("/")[0]
        if currency and c != "shares" and money != currency:
            notes.append(f"{c}: unit {s.unit} differs from {currency}")
    for c, s in series.items():
        notes.extend(f"{c}: {lab} disagrees >2% in {n} overlap years" for lab, n in s.disagree.items())
        letters = {_cand_label(cand): chr(ord("a") + i) for i, cand in enumerate(CONCEPTS[c])}
        for lab, ends in s.unchecked.items():
            years = ", ".join(e[:4] for e in ends)
            notes.append(f"{c}: {years} from recipe ({letters.get(lab, '?')}) {lab}" if c == "debt"
                         else f"{c}: {years} from {lab}")
        notes.extend(f"{c}: {lab} differs >25%, not used" for lab in s.far_off)
        if s.basic_only:
            notes.append(f"{c}: basic-only")
    debt = series.get("debt")
    if debt and debt.values:
        lab = debt.source.get(max(debt.values), debt.tag)
        if lab in PARTIAL_DEBT:
            notes.append(f"debt: partial recipe {lab}")
    tag_breaks = [c for c, s in series.items() if s.tag_break]
    notes.extend(f"{c}: tag break" for c in tag_breaks)

    annual: dict[str, dict[str, float]] = {}
    for c, s in series.items():
        for e, v in s.values.items():
            annual.setdefault(e, {})[c] = v
    latest = None
    for c in ("net_income", "revenue", "equity"):
        if c in series and series[c].values:
            latest = max(series[c].values)
            break
    ttm, ttm_end, ttm_is_fy, period_end, ttm_notes, ttm_ends, balance_ends = _ttm_fields(gaap, ifrs, series, latest)
    notes.extend(ttm_notes)
    return Snapshot(symbol=symbol, cik=cik, name=name, sic=sic, currency=currency,
                    annual=dict(sorted(annual.items())), units={c: s.unit for c, s in series.items()},
                    tags={c: s.tag for c, s in series.items()}, tag_breaks=tag_breaks,
                    ttm=ttm, ttm_period_end=ttm_end, ttm_is_fy=ttm_is_fy, latest_annual_end=latest,
                    latest_period_end=period_end, fetched_at=fetched_at, split_factor=split_factor, notes=notes,
                    ttm_ends=ttm_ends, balance_ends=balance_ends,
                    annual_form=series["net_income"].forms.get(latest) if "net_income" in series else None)


# ---------- TTM and date stamps ----------

QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})
PERIOD_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS       # every form a latest instant / period end may come from
YTD_MIN_DAYS, YTD_MAX_DAYS = 80, 290
QTR_MIN_DAYS, QTR_MAX_DAYS = 80, 100
DATE_TOL = 7                                         # +-days when matching period starts/ends
TTM_FLOWS = ("revenue", "net_income", "cfo", "capex", "sbc", "interest", "op_income")
TTM_BALANCES = ("debt", "cash", "equity", "assets")


def _date(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None


def _year_back(d: date) -> date:
    try:
        return d.replace(year=d.year - 1)
    except ValueError:                               # Feb 29
        return d.replace(year=d.year - 1, day=28)


def _near(a: date | None, b: date | None) -> bool:
    return a is not None and b is not None and abs((a - b).days) <= DATE_TOL


def _ttm_parts(facts_by_unit: dict, unit: str, latest_annual_end: str
               ) -> tuple[Fact | None, Fact | None, Fact | None]:
    """(annual fact at the latest annual end, YTD fact, prior-year YTD fact of the same accession)."""
    rows = {unit: (facts_by_unit or {}).get(unit) or []}
    fy = annual_facts(rows, instant=False).get(unit, {}).get(latest_annual_end)
    anchor = _date(latest_annual_end)
    if fy is None or anchor is None:
        return fy, None, None
    anchor += timedelta(days=1)
    facts = to_facts(rows)
    ytds = []
    for f in facts:
        s, e = _date(f.start), _date(f.end)
        if f.form in QUARTERLY_FORMS and s and e and f.end > latest_annual_end and _near(s, anchor) \
                and YTD_MIN_DAYS <= (e - s).days <= YTD_MAX_DAYS:
            ytds.append(f)
    if not ytds:
        return fy, None, None
    ytd = max(ytds, key=lambda f: (f.end, f.filed, f.accn))
    ps, pe = _year_back(_date(ytd.start)), _year_back(_date(ytd.end))
    priors = [f for f in facts if f.accn == ytd.accn and f.form == ytd.form
              and _near(_date(f.start), ps) and _near(_date(f.end), pe)]
    if not priors:
        return fy, ytd, None
    prior = min(priors, key=lambda f: (abs((_date(f.start) - ps).days) + abs((_date(f.end) - pe).days), f.end))
    return fy, ytd, prior


def ttm_flows(facts_by_unit: dict, unit: str, latest_annual_end: str) -> tuple[float | None, str | None, bool]:
    """(value, period end, is_fy) for one flow tag: latest annual + YTD - prior YTD.

    YTD = the latest 10-Q fact starting the day after the annual end (+-7 days), 80-290 days long;
    prior YTD = the fact of the SAME accession one year earlier (start and end +-7 days). Anything
    missing -> the annual value (is_fy True); no annual fact at that end -> (None, None, True).
    """
    fy, ytd, prior = _ttm_parts(facts_by_unit, unit, latest_annual_end)
    if fy is None:
        return None, None, True
    if ytd is None or prior is None:
        return fy.val, fy.end, True
    return fy.val + ytd.val - prior.val, ytd.end, False


def _quarterly_shares_fact(facts: dict, unit: str = "shares") -> Fact | None:
    """The latest 80-100 day fact from a 10-Q/10-Q/A; if there is none, the latest annual fact."""
    rows = to_facts({unit: (facts or {}).get(unit) or []})
    quarters = []
    for f in rows:
        s, e = _date(f.start), _date(f.end)
        if f.form in QUARTERLY_FORMS and s and e and QTR_MIN_DAYS <= (e - s).days <= QTR_MAX_DAYS:
            quarters.append(f)
    pool = quarters or [f for f in rows if is_annual(f, instant=False)]
    return max(pool, key=lambda f: (f.end, f.filed, f.accn)) if pool else None


def latest_quarterly_shares(facts: dict, unit: str = "shares") -> float | None:
    """Diluted weighted-average shares: the latest 80-100 day 10-Q fact, else the latest annual.

    `facts` is one tag's `units` block (10-K quarter facts are ignored, per the Task 3 review ruling).
    """
    f = _quarterly_shares_fact(facts, unit)
    return None if f is None else f.val


def _units_of(gaap: dict, ifrs: dict, name: str) -> dict:
    ns, tag = _split_name(name)
    return (((ifrs if ns == "ifrs-full" else gaap) or {}).get(tag) or {}).get("units") or {}


def _label_names(label: str) -> list[str]:
    return [n for part in label.split("|") for n in part.split("+")]


def _flow_ttm(gaap: dict, ifrs: dict, concept: str, s: Series, end: str) -> tuple[float, str, bool]:
    """TTM for one resolved flow series, from the tag (or recipe) that supplied its latest annual value."""
    fy_val = s.values[end]
    mag = concept in MAGNITUDE_CONCEPTS
    label = s.source.get(end, s.tag)

    def same(v: float) -> bool:
        return math.isclose(abs(v) if mag else v, fy_val, rel_tol=1e-9, abs_tol=0.5)

    if "+" not in label:
        names = [s.tag] + [n for n in _label_names(label) if n != s.tag]
        for name in names:
            fy, ytd, prior = _ttm_parts(_units_of(gaap, ifrs, name), s.unit, end)
            if fy is None or not same(fy.val):
                continue                                   # this tag did not supply the annual value
            if ytd is None or prior is None:
                break
            v = fy.val + ytd.val - prior.val
            return (abs(v) if mag else v), ytd.end, False
        return fy_val, end, True
    total_fy, total, ends = 0.0, 0.0, set()                # sum recipe: every present part needs a TTM
    for name in label.split("+"):
        fy, ytd, prior = _ttm_parts(_units_of(gaap, ifrs, name), s.unit, end)
        if fy is None:
            if name in OPTIONAL_PARTS:
                continue
            return fy_val, end, True
        if ytd is None or prior is None:
            return fy_val, end, True
        total_fy += fy.val
        total += fy.val + ytd.val - prior.val
        ends.add(ytd.end)
    if len(ends) != 1 or not same(total_fy):
        return fy_val, end, True
    return (abs(total) if mag else total), ends.pop(), False


def _latest_instant(gaap: dict, ifrs: dict, label: str, unit: str,
                    notes: list[str] | None = None) -> tuple[str, float] | None:
    """(end, value) of the latest instant of any period form; recipe parts summed at the same instant.

    An optional part that IS tagged at the latest annual instant but missing at a later 10-Q
    instant is not counted as 0 there: the whole recipe falls back to the annual instant (noted)."""
    names = label.split("+")
    per: dict[str, dict[str, Fact]] = {}
    annual_ends: dict[str, set[str]] = {}
    for name in names:
        rows: dict[str, Fact] = {}
        ann: set[str] = set()
        for f in to_facts({unit: _units_of(gaap, ifrs, name).get(unit) or []}, name):
            if f.form in PERIOD_FORMS and not f.start and (f.end not in rows or _newer(f, rows[f.end])):
                rows[f.end] = f
            if f.form in ANNUAL_FORMS and not f.start:
                ann.add(f.end)
        per[name], annual_ends[name] = rows, ann
    required = [n for n in names if len(names) == 1 or n not in OPTIONAL_PARTS]
    ends = set.intersection(*(set(per[n]) for n in required)) if required else set()
    if not ends:
        return None
    e = max(ends)
    if len(names) > 1:
        ann_common = set.intersection(*(annual_ends[n] for n in required)) & ends
        ae = max(ann_common) if ann_common else None
        dropped = [n for n in names if n not in required and ae is not None and e > ae
                   and ae in annual_ends[n] and e not in per[n]]
        if dropped:
            if notes is not None:
                notes.append(f"{label}: {', '.join(dropped)} untagged at {e}; recipe dated at the annual end {ae}")
            e = ae
    return e, sum(per[n][e].val for n in names if e in per[n])


def _period_end(gaap: dict, ifrs: dict, s: Series) -> str | None:
    """Latest period end of any period form across the tags behind one series."""
    names = {s.tag}
    for lab in set(s.source.values()):
        names.update(_label_names(lab))
    ends = [f.end for n in names for f in to_facts({s.unit: _units_of(gaap, ifrs, n).get(s.unit) or []})
            if f.form in PERIOD_FORMS]
    ends += list(s.values)
    return max(ends) if ends else None


def _ttm_fields(gaap: dict, ifrs: dict, series: dict[str, Series], latest: str | None
                ):
    """(ttm, ttm_period_end, ttm_is_fy, latest_period_end, notes, ttm_ends, balance_ends)."""
    ttm: dict[str, float] = {}
    stamps: dict[str, tuple[str, bool]] = {}
    balance_ends: dict[str, str] = {}
    notes: list[str] = []
    if latest is None:
        return ttm, None, False, None, notes, stamps, balance_ends
    for c in TTM_FLOWS:
        s = series.get(c)
        if s and latest in s.values:
            v, e, fy = _flow_ttm(gaap, ifrs, c, s, latest)
            ttm[c], stamps[c] = v, (e, fy)
    for c in TTM_BALANCES:
        s = series.get(c)
        if not s or not s.values:
            continue
        end, val = max(s.values.items())
        got = _latest_instant(gaap, ifrs, s.tag, s.unit, notes)
        if got and got[0] >= end:
            end, val = got
        ttm[c], balance_ends[c] = val, end
    s = series.get("shares")
    if s and s.values:
        q = _quarterly_shares_fact(_units_of(gaap, ifrs, s.tag), s.unit)
        if q is not None:
            ttm["shares"], balance_ends["shares"] = q.val, q.end
        else:
            ttm["shares"], balance_ends["shares"] = s.values[max(s.values)], max(s.values)
    lead = next((c for c in ("net_income", "revenue") if c in stamps), None)
    # Latest period end of any allowed period form across EVERY resolved concept (flows and
    # instants): C and NEE file their latest 10-Q facts under tags other than net income's.
    ends = [e for e in (_period_end(gaap, ifrs, s) for s in series.values() if s.values) if e]
    period_end = max(ends) if ends else None
    if lead is None:
        return ttm, latest, True, period_end, notes, stamps, balance_ends
    ttm_end, ttm_is_fy = stamps[lead]
    if ttm_is_fy and period_end and period_end > ttm_end:
        notes.append(f"income TTM = FY; later 10-Q balance at {period_end}")
    for c, (e, fy) in stamps.items():
        if (e, fy) != (ttm_end, ttm_is_fy):
            notes.append(f"{c}: TTM = FY" if fy else f"{c}: TTM through {e}")
    for c, e in balance_ends.items():
        if e < ttm_end:
            notes.append(f"{c}: balance at {e}, older than TTM {ttm_end}")
    return ttm, ttm_end, ttm_is_fy, period_end, notes, stamps, balance_ends


# ---------- SIC ----------

# 6200-6299 (brokers) and 6300-6399 (insurers) sit inside 6000-6499; kept explicit on purpose.
FINANCIAL_SIC_RANGES = ((6000, 6499), (6200, 6299), (6300, 6399))


def is_financial_sic(sic) -> bool:
    """Banks, brokers, insurers and other finance SIC codes (Visa's 7389 is not one)."""
    if sic is None or isinstance(sic, bool):
        return False
    try:
        n = int(sic) if isinstance(sic, (int, float)) else int(str(sic).strip())
    except (TypeError, ValueError, OverflowError):
        return False
    return any(lo <= n <= hi for lo, hi in FINANCIAL_SIC_RANGES)


# ---------- the paced SEC client ----------

SEC_USER_AGENT = "AGX Trading App (admin@agxtrade.com)"
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
BACKOFF_S = (1, 2, 4, 8)          # sleeps before retries 1-4 on 403/429/5xx/network errors
BLOCK_AFTER_403 = 3               # consecutive 403 responses (across calls) that stop the pass
HTTP_TIMEOUT_S = 30
SUBMISSION_KEYS = ("name", "sic", "sicDescription", "fiscalYearEnd", "tickers")


class EdgarBlocked(RuntimeError):
    """SEC answered 403 three times in a row: stop the pass (fair-access block)."""


def _urllib_get(url: str, headers: dict) -> bytes:
    """Default getter: GET with the given headers, body gunzipped when the response says gzip."""
    req = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
        body = resp.read()
        enc = (resp.headers.get("Content-Encoding") or "").lower()
    return gzip.decompress(body) if "gzip" in enc else body


def _retryable(exc: Exception, code) -> bool:
    """403/429/5xx answers and network failures (URLError, timeouts, dropped connections) are retried."""
    if isinstance(code, int):
        return code in (403, 429) or 500 <= code <= 599
    return isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError))


def _canon_ticker(t: str) -> str:
    return str(t).strip().upper().replace(".", "-").replace("/", "-")


class EdgarClient:
    """SEC EDGAR JSON endpoints: <= pace_per_sec requests/s, back-off on 403/429, abort on a block."""

    def __init__(self, get: Callable[[str, dict], bytes] | None = None, *, user_agent: str | None = None,
                 pace_per_sec: float = 5.0, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._get = get or _urllib_get
        self.user_agent = user_agent or os.environ.get("SEC_USER_AGENT") or SEC_USER_AGENT
        self._interval = 1.0 / pace_per_sec if pace_per_sec and pace_per_sec > 0 else 0.0
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None
        self._cik_map: dict[str, int] | None = None
        self.consecutive_403 = 0

    def _pace(self) -> None:
        if self._last is not None and self._interval:
            wait = self._interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def fetch(self, url: str) -> bytes:
        headers = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip",
                   "Host": urllib.parse.urlsplit(url).netloc}
        for attempt in range(len(BACKOFF_S) + 1):
            self._pace()
            try:
                body = self._get(url, headers)
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code == 403:
                    self.consecutive_403 += 1
                    if self.consecutive_403 >= BLOCK_AFTER_403:
                        raise EdgarBlocked(f"SEC returned 403 {self.consecutive_403} times in a row ({url})") from exc
                elif code is not None:
                    self.consecutive_403 = 0                 # any other HTTP answer breaks the streak
                if not _retryable(exc, code) or attempt >= len(BACKOFF_S):
                    raise
                self._sleep(BACKOFF_S[attempt])
                continue
            self.consecutive_403 = 0
            return body                                      # already decoded by the getter
        raise AssertionError("unreachable")

    def _json(self, url: str):
        return json.loads(self.fetch(url))

    def cik_map(self) -> dict[str, int]:
        """Canonical ticker (BRK.B -> BRK-B) -> CIK; fetched once per client."""
        if self._cik_map is None:
            out: dict[str, int] = {}
            for row in (self._json(TICKERS_URL) or {}).values():
                try:
                    out.setdefault(_canon_ticker(row["ticker"]), int(row["cik_str"]))
                except (KeyError, TypeError, ValueError):
                    continue
            self._cik_map = out
        return self._cik_map

    def company_facts(self, cik: int) -> dict:
        return self._json(FACTS_URL.format(cik=int(cik)))

    def submission(self, cik: int) -> dict:
        raw = self._json(SUBMISSIONS_URL.format(cik=int(cik))) or {}
        out = {k: raw.get(k) for k in SUBMISSION_KEYS}
        try:
            out["sic"] = int(str(out["sic"]).strip()) if out["sic"] not in (None, "") else None
        except ValueError:
            out["sic"] = None
        return out
