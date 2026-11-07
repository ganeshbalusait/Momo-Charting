"""Long Term: EDGAR company-facts -> one annual table per company.

Fact selection, the tag table, per-company tag resolution, units and split
inference, TTM and date stamps: pure functions over the parsed companyfacts
JSON. `EdgarClient` is the only network code (paced, injectable getter).

Rules (spec "Fact selection rules (EDGAR)"):
* annual row = form in ANNUAL_FORMS and 350-380 days long (instants: end only);
  never fy/fp/frame; key by period end; latest `filed` wins, then accession.
* one unit per concept per company: most annual years; USD only on >= coverage.
* tag resolution per company: the primary is a candidate covering the latest
  annual year (most coverage among those; table order for the ORDERED_CONCEPTS
  debt, capex, revenue, eps, cfo, shares); another candidate fills missing years
  only if it agrees within 2% in every overlap year (ordered concepts: within
  25%, R36), else the year stays blank and the series is a "tag break". Debt
  and capex take the LARGER value where candidates differ by > 25% (R41, noted);
  net income prefers a to-common / parent tag over ProfitLoss (minority interest).
* EPS derived (R36) = net income to common / diluted weighted shares (else the
  period-end share count) for a year with no EPS tag, noted; never for a year
  that has one.
* splits are inferred from the same period carrying different EPS in
  different filings of the same tag (clean ratios only), confirmed by the share
  counts, plus a 10-Q split-ratio fact the annual filings do not reflect yet;
  every per-share candidate is brought to today's basis BEFORE comparison.
* a 10-Q comparative instant at a fiscal year end counts as the annual balance;
  a debt tag that ended at an explicit 0 means 0 in the later years no tag covers.
* TTM flow = latest annual + YTD - prior YTD of the same 10-Q accession, else
  the annual ("TTM = FY"); balance items = latest instant of any period form.
* facts split across CIKs (XOM's successor, NEE's co-registrant) are unioned by
  `merge_companyfacts` (EXTRA_CIKS); the service fetches the extra CIKs.
"""
from __future__ import annotations

import copy
import gzip
import itertools
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
QUARTERLY_FORMS = frozenset({"10-Q", "10-Q/A"})
MIN_DAYS, MAX_DAYS = 350, 380
AGREE = 0.02                      # fill tolerance between candidate tags (net_income, equity, ...)
FILL_MAX_DIFF = 0.25              # R36: ordered concepts fill from a candidate within 25% in every overlap year
SPLIT_TOL = 0.03                  # relative tolerance when snapping a ratio to a clean split
SPLIT_MIN_EPS = 0.10              # below this, cent rounding makes EPS ratios meaningless
# 8 is not in the spec's list but 1-for-8 reverse splits are common (and the brief's test uses one).
# audit fix E1 (B4, 2026-11-07): 6 (DECK 2024 6-for-1), 9, 15, 25, 30, 40, 100 are real split ratios too;
# a pair spanning two splits (2:1 then 3:1 = 6) is merged away in _split_scan, never applied twice.
_CLEAN = (1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 100.0)
CLEAN_RATIOS = tuple(sorted(set(_CLEAN) | {1.0 / r for r in _CLEAN}))

IFRS = "ifrs-full:"
OPT = "?"                         # recipe part prefix: optional (counts as 0 when the year lacks it)
# One table, us-gaap first then ifrs-full. Bare names are us-gaap; "ifrs-full:" names are IFRS.
# A tuple is a sum recipe (all non-optional parts must exist that year; "?Name" parts are optional).
CONCEPTS: dict[str, list[str | tuple[str, ...]]] = {
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                ("InterestAndDividendIncomeOperating", "NoninterestIncome"), IFRS + "Revenue",
                # no-data fix 2026-11-07 (R37): IFRS filers that never tag ifrs-full:Revenue
                IFRS + "RevenueFromContractsWithCustomers",                            # KGC, NOK, BLDP, INFY, ACB
                IFRS + "RevenueFromSaleOfGoods",                                       # KGC 2016
                IFRS + "RevenueAndOperatingIncome"],                                   # HSBC (financial SIC only)
    "net_income": ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic",
                   IFRS + "ProfitLossAttributableToOwnersOfParent", IFRS + "ProfitLoss"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted",                # HAL, CLSK, ASTS, APPS
            # HAL, FCX, CLSK, ENPH, DELL: continuing + discontinued operations per diluted share = EPS
            ("IncomeLossFromContinuingOperationsPerDilutedShare",
             OPT + "IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare",
             OPT + "DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerDilutedShare"),
            "IncomeLossFromContinuingOperationsPerBasicAndDilutedShare",                # ENPH 2010-2018
            "NetIncomeLossNetOfTaxPerOutstandingLimitedPartnershipUnitDiluted",         # ET (partnership units)
            "NetIncomeLossPerOutstandingLimitedPartnershipUnitDiluted",                 # ET 2008-2011
            "EarningsPerShareBasic", "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax",
            IFRS + "DilutedEarningsLossPerShare", IFRS + "DilutedEarningsLossPerShareFromContinuingOperations",  # SHEL
            IFRS + "BasicEarningsLossPerShare", IFRS + "BasicEarningsLossPerShareFromContinuingOperations"],
    "equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               IFRS + "EquityAttributableToOwnersOfParent", IFRS + "Equity"],
    # audit fix E2 (B1/B2, 2026-11-07): every recipe that carries the current part ranks before a bare
    # noncurrent tag. The filer's own totals (LHX/COP/CLS ...IncludingCurrentMaturities, MAR/COP
    # DebtAndCapitalLeaseObligations) come right after (a) and before LongTermDebt (b), and
    # LongTermDebtAndCapitalLeaseObligations - noncurrent by definition - only ever appears with its
    # current part. Recipes in CURRENT_MAX take DebtCurrent (total current debt) when it is larger.
    "debt": [("LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"),
             ("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
              OPT + "ShortTermBorrowings", OPT + "CommercialPaper"),                   # GM, LHX, COP, CLS
             "DebtAndCapitalLeaseObligations",                                      # F, MAR, COP
             ("LongTermDebt", "ShortTermBorrowings", "CommercialPaper"),
             ("DebtCurrent", "LongTermDebtNoncurrent"),
             # MAR, LHX, XOM: (d) plus the current portion; a filer that tags the current part only in
             # 10-Ks (VZ) dates its balance at the annual end (_latest_instant)
             ("LongTermDebtAndCapitalLeaseObligations", OPT + "LongTermDebtAndCapitalLeaseObligationsCurrent"),
             (IFRS + "NoncurrentBorrowings", IFRS + "CurrentBorrowings"),
             (IFRS + "LongtermBorrowings", IFRS + "ShorttermBorrowings"),
             IFRS + "Borrowings",
             # audit 2026-11-06 (first pass): real tags on filers the recipes above missed
             "NotesPayable",                                                        # DHI, GAP
             ("ConvertibleLongTermNotesPayable", "ConvertibleNotesPayableCurrent"),  # DASH, DDOG, AEVA
             "LoansPayable",                                                        # GAP
             "OtherNotesPayableCurrent",                                            # AEVA
             # audit round 2: real debt the proof would otherwise call "no debt"
             "LongTermDebtNoncurrent",                                              # CRWD, REGN, MRNA, TEAM
             ("ConvertibleDebtNoncurrent", "ConvertibleDebtCurrent"),               # PANW
             "ConvertibleNotesPayable",                                             # DHI
             # SPOT: exchangeable notes sit in current borrowings (LongtermBorrowings is 0)
             (IFRS + "LongtermBorrowings", IFRS + "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"),
             # no-data fix 2026-11-07 (R37/R41 recipes, from the debt probe)
             "DebtLongtermAndShorttermCombinedAmount",                              # ORCL, STX (issuer total)
             ("LongTermNotesPayable", OPT + "DebtCurrent"),                         # ORCL 2009-2014, HL
             ("OtherLongTermDebtNoncurrent", OPT + "OtherLongTermDebtCurrent",
              OPT + "SecuredDebtCurrent", OPT + "LongTermLineOfCredit"),             # XYZ
             ("LongTermNotesAndLoans", OPT + "ShortTermBorrowings"),                # NTRA (to 2023)
             "ShortTermBorrowings",                                                 # NTRA (UBS line)
             "LinesOfCreditCurrent", "LongTermLineOfCredit",                        # LASR, QMCO
             (IFRS + "NoncurrentPortionOfNoncurrentBondsIssued",
              OPT + IFRS + "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued",
              OPT + IFRS + "LongtermBorrowings", OPT + IFRS + "CurrentPortionOfLongtermBorrowings"),   # TSM
             (IFRS + "NotesAndDebenturesIssued",
              OPT + IFRS + "CurrentNotesAndDebenturesIssuedAndCurrentPortionOfNoncurrentNotesAndDebenturesIssued")],  # TME
    "op_leases": ["OperatingLeaseLiability"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
             IFRS + "CashAndCashEquivalents"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities", "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            IFRS + "CashFlowsFromUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets",
              "PaymentsToAcquireOtherPropertyPlantAndEquipment",                     # LLY
              "PaymentsToAcquireOtherProductiveAssets",                              # VZ 2017+
              ("PaymentsForCapitalImprovements", OPT + "PaymentsForConstructionInProcess"),  # PENN, NVAX, KITT; WELL (REIT)
              "PaymentsForProceedsFromProductiveAssets",                             # APPS (net of proceeds)
              IFRS + "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              IFRS + "PurchaseOfOtherLongtermAssetsClassifiedAsInvestingActivities",  # SHEL
              IFRS + "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets"],  # ACB
    "sbc": ["ShareBasedCompensation"],
    "buybacks": ["PaymentsForRepurchaseOfCommonStock"],
    "dividends": ["PaymentsOfDividends", "PaymentsOfDividendsCommonStock",
                  "PaymentsOfOrdinaryDividends"],                                      # QCOM 2013+ (audit fix E4)
    "interest": ["InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"],
    "op_income": ["OperatingIncomeLoss", IFRS + "ProfitLossFromOperatingActivities"],
    "assets": ["Assets", IFRS + "Assets"],
    "shares": ["WeightedAverageNumberOfDilutedSharesOutstanding",
               "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",             # CLSK, ASTS, ENPH
               "WeightedAverageLimitedPartnershipUnitsOutstandingDiluted",           # ET
               "WeightedAverageNumberOfSharesOutstandingBasic",
               IFRS + "WeightedAverageNumberOfDilutedSharesOutstanding", IFRS + "WeightedAverageShares"],  # SHEL
    "split_ratio": ["StockholdersEquityNoteStockSplitConversionRatio1"],   # 10-Q split ratio (CVNA), see _ratio_events
    # internal (not in the annual table): inputs of the derived EPS (R36)
    "ni_common": ["NetIncomeLossAvailableToCommonStockholdersDiluted", "NetIncomeLossAvailableToCommonStockholdersBasic",
                  "NetIncomeLoss", IFRS + "ProfitLossAttributableToOwnersOfParent"],   # never ProfitLoss (minority interest)
    "shares_outstanding": ["CommonStockSharesOutstanding"],                # HSY: period-end shares when no weighted tag
}
# Revenue: when several of these exist in a year, take the largest (one combined candidate).
# audit fix E5 (J9): SalesRevenueNet joins it (LHX 2010-13 tagged a 102-420M sub-line as Revenues).
LARGER_OF = {"revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                         "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet")}
# audit fix E2 (B2): recipe label -> its noncurrent base part. The recipe's current block becomes the
# larger of its own current parts and DebtCurrent (the filer's total current debt, which already holds
# the current maturities, short-term borrowings and commercial paper: never summed with them) at the
# same instant: QCOM 2026-09-27 DebtCurrent 2,492 vs LongTermDebtCurrent 1,993; XOM 2018+ notes and
# loans payable beside the noncurrent (d). Recipe (b) only under LTD_EXCLUDES_CURRENT (ADBE).
DEBT_CURRENT = "DebtCurrent"
DEBT_COMBINED = "DebtLongtermAndShorttermCombinedAmount"
CURRENT_MAX = {"LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings+CommercialPaper": "LongTermDebtNoncurrent",
               "LongTermDebt+ShortTermBorrowings+CommercialPaper": "LongTermDebt",
               "LongTermDebtAndCapitalLeaseObligations+LongTermDebtAndCapitalLeaseObligationsCurrent":
                   "LongTermDebtAndCapitalLeaseObligations"}
# LongTermDebt is defined to include current maturities; a filer that tags it as the noncurrent line
# (ADBE) shows it: DebtCurrent + LongTermDebt reproduces its combined total, or the two trade off
# between quarter-ends (a maturity moving to current). Only then is DebtCurrent added to it, only at
# instants with no LongTermDebtNoncurrent, and never off the combined total by more than 25%.
LTD_EXCLUDES_CURRENT = frozenset({"LongTermDebt+ShortTermBorrowings+CommercialPaper"})
TRADE_OFF_MIN_MOVE = 0.10             # LongTermDebt moved >= 10% between consecutive quarter-ends ...
TRADE_OFF_MATCH = 0.05                # ... and DebtCurrent moved the other way within 5% of it
TRADE_OFF_MAX_DAYS = 120
# Candidates only some industries may use: label -> "financial" (is_financial_sic must hold).
SIC_GATED = {IFRS + "RevenueAndOperatingIncome": "financial"}   # non-banks (ABVX) tag grants/other income with it
# Recipe parts that count as 0 when missing (ifrs ShorttermBorrowings is the IFRS twin of ShortTermBorrowings).
OPTIONAL_PARTS = frozenset({"ShortTermBorrowings", "CommercialPaper", IFRS + "ShorttermBorrowings",
                            "ConvertibleNotesPayableCurrent", "ConvertibleDebtCurrent"})
# Fill-only candidates: they may fill missing years under the fill rule but are never primary
# (unless nothing else exists for that concept).
FILL_ONLY = frozenset({"NetIncomeLossAvailableToCommonStockholdersBasic", "EarningsPerShareBasic",
                       "WeightedAverageNumberOfSharesOutstandingBasic", IFRS + "BasicEarningsLossPerShare",
                       "NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax",
                       IFRS + "BasicEarningsLossPerShareFromContinuingOperations"})
# net_income: ProfitLoss includes minority interest, so a to-common / parent tag covering the latest
# year outranks it regardless of coverage (FCX: 4,152M ProfitLoss vs 2,197M to common; IBKR's Up-C).
LAST_RESORT = frozenset({"ProfitLoss", IFRS + "ProfitLoss"})
# Every concept's primary candidate is one that covers the LATEST annual year (R19). Among those,
# ordered concepts take table order (best-first), the rest the most coverage. Ordered concepts also
# fill the primary's missing years from the other candidates in table order under the 25% rule
# (R36: a candidate more than 25% off in an overlap year never fills), noted, no tag break for
# debt/capex (structural differences: commercial paper and leases for debt; AMZN's PP&E vs
# productive-assets capex).
ORDERED_CONCEPTS = frozenset({"debt", "capex", "revenue", "eps", "cfo", "shares", "ni_common"})
# R41: debt and capex are fail-safe magnitudes: in a year where another candidate is > 25% LARGER
# than the chosen value, the larger value is used and noted ("candidates differ, larger used").
LARGER_WINS = frozenset({"debt", "capex"})
CAPEX_FILL_MAX_DIFF = FILL_MAX_DIFF   # kept for callers; capex uses the common 25% rule
# Single-part debt recipes that can miss part of the debt (R22): noted when they carry the latest year.
PARTIAL_DEBT = frozenset({"NotesPayable", "LoansPayable", "OtherNotesPayableCurrent", "LongTermDebtNoncurrent",
                          "ShortTermBorrowings", "LinesOfCreditCurrent", "LongTermLineOfCredit"})
# Diluted share-count tags: confirm inferred splits and divide the derived EPS.
SHARES_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding", "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
               "WeightedAverageLimitedPartnershipUnitsOutstandingDiluted",
               IFRS + "WeightedAverageNumberOfDilutedSharesOutstanding", IFRS + "WeightedAverageShares")
PER_SHARE_CONCEPTS = frozenset({"eps"})                       # split factor applies
SHARE_COUNT_CONCEPTS = frozenset({"shares", "shares_outstanding"})   # inverse split factor applies
INSTANT_CONCEPTS = frozenset({"equity", "debt", "op_leases", "cash", "assets", "shares_outstanding"})
MAGNITUDE_CONCEPTS = frozenset({"capex"})
INTERNAL_CONCEPTS = frozenset({"split_ratio", "ni_common", "shares_outstanding"})
TABLE_CONCEPTS = tuple(c for c in CONCEPTS if c not in INTERNAL_CONCEPTS)
# Fetched (fixtures/trimmer) but not resolved into a concept here.
EXTRA_TAGS = {"us-gaap": ["Liabilities", "DepreciationDepletionAndAmortization", "IncomeTaxExpenseBenefit"],
              "ifrs-full": ["PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities", "Liabilities"],
              "dei": ["EntityCommonStockSharesOutstanding"]}
# Filers whose facts live under more than one CIK (R42): symbol -> extra CIKs whose facts are merged
# into the primary CIK's companyfacts with `merge_companyfacts` (the service fetches them).
# XOM: the SEC map moved XOM to ExxonMobil Holdings (2115436), which carries the 2026 10-Qs but no
# history; 34088 (CIK_OVERRIDES) has the history. NEE: its combined 10-Qs with Florida Power & Light
# were attributed to FPL's CIK 37634 for Q2/Q3 2026; only NEE's own accessions may be taken from it
# (pass `allowed_accns` from NEE's submissions index, see EdgarClient.accessions).
EXTRA_CIKS: dict[str, tuple[int, ...]] = {"XOM": (2115436,), "NEE": (37634,)}
NEEDS_ACCESSION_FILTER = frozenset({"NEE"})


def _part_name(name: str) -> str:
    return name[len(OPT):] if name.startswith(OPT) else name


def _split_name(name: str) -> tuple[str, str]:
    name = _part_name(name)
    return ("ifrs-full", name[len(IFRS):]) if name.startswith(IFRS) else ("us-gaap", name)


def _cand_label(cand) -> str:
    return "+".join(_part_name(p) for p in cand) if isinstance(cand, tuple) else cand


def _recipe_optional() -> dict[str, frozenset[str]]:
    """recipe label -> parts marked optional with the "?" prefix."""
    out: dict[str, frozenset[str]] = {}
    for cands in CONCEPTS.values():
        for cand in cands:
            if isinstance(cand, tuple):
                opt = frozenset(_part_name(p) for p in cand if p.startswith(OPT))
                if opt:
                    out[_cand_label(cand)] = opt
    return out


RECIPE_OPTIONAL: dict[str, frozenset[str]] = _recipe_optional()


def _optional(name: str, label: str | None = None) -> bool:
    """Whether `name` counts as 0 when missing: globally optional, or optional inside recipe `label`."""
    return name in OPTIONAL_PARTS or (label is not None and name in RECIPE_OPTIONAL.get(label, ()))


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
    larger: dict[str, list[str]] = field(default_factory=dict)  # debt/capex: label -> ends where it was the larger value
    # debt (audit fix E2/E3): ends whose value took DebtCurrent as the current block, and
    # "part" -> ends where that optional part copied the noncurrent part (filer error, not added)
    current_max: list[str] = field(default_factory=list)
    self_copy: dict[str, list[str]] = field(default_factory=dict)


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
                 instant: bool | None = None, year_ends: set[str] | None = None) -> dict[str, dict[str, Fact]]:
    """unit -> period end -> latest-filed annual Fact (tie-break: accession).

    `year_ends` (balance items only): a 10-Q comparative instant dated exactly at one of these fiscal
    year ends counts as the annual fact too (CHWY's FY-end debt exists only in 10-Q comparatives)."""
    out: dict[str, dict[str, Fact]] = {}
    for f in to_facts(facts_by_unit, tag):
        if not is_annual(f, forms, instant=instant):
            if not (instant and year_ends and f.form in QUARTERLY_FORMS and not f.start and f.end in year_ends):
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

Adjust = Callable[[Fact], Fact] | None


def _node_rows(gaap: dict, ifrs: dict, name: str, instant: bool | None = None, *, adjust: Adjust = None,
               year_ends: set[str] | None = None) -> dict[str, dict[str, Fact]]:
    ns, tag = _split_name(name)
    src = ifrs if ns == "ifrs-full" else gaap
    node = (src or {}).get(tag)
    if not node:
        return {}
    rows = annual_facts(node.get("units") or {}, tag=_part_name(name), instant=instant, year_ends=year_ends)
    if adjust is not None:
        rows = {u: {e: adjust(f) for e, f in r.items()} for u, r in rows.items()}
    return rows


def _self_copy(gaap: dict, ifrs: dict, base: str, f: Fact, cache: dict | None = None) -> bool:
    """Audit fix E3 (B3): optional part `f` repeats the noncurrent base part to the dollar in the same
    filing (XOM 2010-12-31 ...CapitalLeaseObligationsCurrent = 12,227M = the noncurrent value, one
    Q1-2011 10-Q): a filer tagging error, the part is not added."""
    if f.val == 0:
        return False
    if cache is None or base not in cache:
        rows = {(g.end, g.accn, g.unit): g.val for g in _node_facts(gaap, ifrs, base) if not g.start}
        if cache is None:
            return rows.get((f.end, f.accn, f.unit)) == f.val
        cache[base] = rows
    return cache[base].get((f.end, f.accn, f.unit)) == f.val


def _instant_map(gaap: dict, ifrs: dict, name: str) -> dict[tuple[str, str], Fact]:
    """(unit, end) -> latest-filed instant fact of one tag from any period form."""
    out: dict[tuple[str, str], Fact] = {}
    for f in _node_facts(gaap, ifrs, name):
        if f.form in PERIOD_FORMS and not f.start:
            cur = out.get((f.unit, f.end))
            if cur is None or _newer(f, cur):
                out[(f.unit, f.end)] = f
    return out


def ltd_excludes_current(gaap: dict, ifrs: dict | None = None) -> bool:
    """Whether this filer tags LongTermDebt as the NONCURRENT line (ADBE), see LTD_EXCLUDES_CURRENT.

    The latest instant carrying LongTermDebt, a positive DebtCurrent and the combined total decides:
    LongTermDebt + DebtCurrent must sit within 25% of the total and closer to it than LongTermDebt
    alone (ADBE FY2019: 989 + 3,149 = 4,138). Without such an instant, a quarter-end pair where
    LongTermDebt moved >= 10% and DebtCurrent moved the other way by the same amount (within 5%)
    shows the maturity being reclassified (ADBE 2019-03 -> 2019-05: -2,249 / +2,253)."""
    ifrs = ifrs or {}
    ltd, dc = _instant_map(gaap, ifrs, "LongTermDebt"), _instant_map(gaap, ifrs, DEBT_CURRENT)
    comb = _instant_map(gaap, ifrs, DEBT_COMBINED)
    full = sorted(k for k in ltd if k in dc and k in comb and dc[k].val > 0)
    if full:
        k = max(full, key=lambda k: k[1])
        total, alone, both = comb[k].val, ltd[k].val, ltd[k].val + dc[k].val
        return _off_by(both, total) <= FILL_MAX_DIFF and abs(both - total) < abs(alone - total)
    pairs = sorted(k for k in ltd if k in dc)
    for a, b in zip(pairs, pairs[1:]):
        if a[0] != b[0] or (date.fromisoformat(b[1]) - date.fromisoformat(a[1])).days > TRADE_OFF_MAX_DAYS:
            continue
        dl, dd = ltd[b].val - ltd[a].val, dc[b].val - dc[a].val
        if abs(dl) >= TRADE_OFF_MIN_MOVE * max(abs(ltd[a].val), abs(ltd[b].val)) and abs(dl + dd) <= TRADE_OFF_MATCH * abs(dl):
            return True
    return False


def _current_alt(label: str, base: Fact | None, dc: Fact | None, ltdn: Fact | None, comb: Fact | None,
                 gated_ok: bool) -> float | None:
    """The CURRENT_MAX alternative value (base part + DebtCurrent) at one instant, or None."""
    if base is None or dc is None or label not in CURRENT_MAX:
        return None
    if label in LTD_EXCLUDES_CURRENT:
        if not gated_ok or ltdn is not None:
            return None
        if comb is not None and _off_by(base.val + dc.val, comb.val) > FILL_MAX_DIFF:
            return None
    return base.val + dc.val


def _candidate(gaap: dict, ifrs: dict, cand, instant: bool | None = None, *, adjust: Adjust = None,
               year_ends: set[str] | None = None, log: dict | None = None) -> dict[str, dict[str, Fact]]:
    """unit -> end -> Fact (recipes yield synthetic Facts whose val is the sum).

    `log` (debt): collects ("current_max", label, end) and ("self_copy", label, end, part) events."""
    if not isinstance(cand, tuple):
        return _node_rows(gaap, ifrs, cand, instant, adjust=adjust, year_ends=year_ends)
    label = _cand_label(cand)
    names = [_part_name(p) for p in cand]
    parts = {p: _node_rows(gaap, ifrs, p, instant, adjust=adjust, year_ends=year_ends) for p in names}
    required = [p for p in names if not _optional(p, label)]
    units = set.intersection(*(set(parts[p]) for p in required)) if required else set()
    alt = label in CURRENT_MAX and instant
    if alt:
        base_name = CURRENT_MAX[label]
        rows_of = {n: _node_rows(gaap, ifrs, n, instant, year_ends=year_ends)
                   for n in (base_name, DEBT_CURRENT, "LongTermDebtNoncurrent", DEBT_COMBINED)}
        gated_ok = label not in LTD_EXCLUDES_CURRENT or ltd_excludes_current(gaap, ifrs)
    cache: dict = {}
    out: dict[str, dict[str, Fact]] = {}
    for u in units:
        ends = set.intersection(*(set(parts[p][u]) for p in required))
        for e in sorted(ends):
            used = []
            for p in names:
                f = parts[p].get(u, {}).get(e)
                if f is None:
                    continue
                if p != names[0] and _optional(p, label) and _self_copy(gaap, ifrs, names[0], f, cache):
                    if log is not None:
                        log.setdefault("self_copy", set()).add((label, e, p))
                    continue
                used.append(f)
            total = sum(f.val for f in used)
            last = max(used, key=lambda f: (f.filed, f.accn))
            if alt:
                at = {n: r.get(u, {}).get(e) for n, r in rows_of.items()}
                v = _current_alt(label, at[base_name], at[DEBT_CURRENT], at["LongTermDebtNoncurrent"],
                                 at[DEBT_COMBINED], gated_ok)
                if v is not None and v > total:
                    total = v
                    last = max([last, at[DEBT_CURRENT]], key=lambda f: (f.filed, f.accn))
                    if log is not None:
                        log.setdefault("current_max", set()).add((label, e))
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


def _gated_out(label: str, sic) -> bool:
    gate = SIC_GATED.get(label)
    return gate == "financial" and not is_financial_sic(sic)


def _candidates(gaap: dict, ifrs: dict, concept: str, *, adjust: Adjust = None, sic=None,
                year_ends: set[str] | None = None, log: dict | None = None
                ) -> list[tuple[str, dict[str, dict[str, Fact]]]]:
    cands = CONCEPTS[concept]
    instant = None if concept == "split_ratio" else concept in INSTANT_CONCEPTS
    pair = LARGER_OF.get(concept)
    out = []
    for cand in cands:
        if pair and cand in pair[1:]:
            continue                                      # folded into pair[0]
        label = _cand_label(cand)
        if _gated_out(label, sic):
            continue
        rows = _candidate(gaap, ifrs, cand, instant, adjust=adjust, year_ends=year_ends if instant else None, log=log)
        if pair and cand == pair[0]:
            for other in pair[1:]:
                rows = _larger_of(rows, _candidate(gaap, ifrs, other, instant, adjust=adjust))
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


def _primary(ranked: list, concept: str) -> list:
    """Reorder `ranked` so the primary candidate comes first (see the module docstring and R19)."""
    fill_only = FILL_ONLY if concept != "ni_common" else frozenset()   # to-common basic NI is first-class there
    pool = [c for c in ranked if c[0] not in fill_only] or ranked
    latest = max(e for _, rows in pool for e in rows)
    covering = [c for c in pool if latest in c[1]]
    if all(c[0] in LAST_RESORT for c in covering):
        # net_income: a fill-only to-common tag covering the latest year beats ProfitLoss (IBKR, FCX)
        covering = [c for c in ranked if c[0] in fill_only and latest in c[1]] + covering
    if concept in ORDERED_CONCEPTS:
        # table order: the first candidate covering the latest year; the rest fill in table order
        # a recipe showing 0 in the latest year never outranks a later one showing a value there:
        # a 0 from a partial recipe is "a tag missed", not proof of no debt (SPOT)
        first = next((c for c in covering if c[1][latest].val != 0), covering[0])
        return [first] + [c for c in ranked if c is not first]
    # most coverage among those covering the latest year, last-resort tags behind the rest (ties keep
    # table order: sorted is stable); the rest by coverage, fill-only tags last
    first = sorted(covering, key=lambda c: (c[0] in LAST_RESORT, -len(c[1])))[0]
    rest = sorted((c for c in ranked if c is not first), key=lambda c: -len(c[1]))
    return [first] + [c for c in rest if c[0] not in fill_only] + [c for c in rest if c[0] in fill_only]


def resolve_series(gaap: dict, ifrs: dict, concept: str, *, year_ends: set[str] | None = None,
                   prefer_currency: str | None = None, adjust: Adjust = None, sic=None) -> Series | None:
    """Per-company resolution of one concept (see module docstring).

    `adjust` maps every candidate Fact before comparison (split normalisation of per-share facts);
    `sic` enables the SIC-gated candidates; `year_ends` restricts balance items to fiscal year ends
    (and lets 10-Q comparatives at those ends count)."""
    log: dict = {}
    cands = _candidates(gaap or {}, ifrs or {}, concept, adjust=adjust, sic=sic, year_ends=year_ends, log=log)
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
    ranked = _primary(ranked, concept)
    plabel, prim = ranked[0]
    chosen: dict[str, Fact] = dict(prim)
    source = {e: plabel for e in prim}
    refused: set[str] = set()                            # years a disagreeing candidate could have filled
    disagree: dict[str, int] = {}
    unchecked: dict[str, list[str]] = {}
    far_off: list[str] = []
    larger: dict[str, list[str]] = {}
    ordered = concept in ORDERED_CONCEPTS
    for lab, rows in ranked[1:]:
        overlap = [e for e in rows if e in prim]
        if not ordered:
            off = sum(1 for e in overlap if not _agrees(rows[e].val, prim[e].val))
            if off:
                disagree[lab] = off
        missing = [e for e in rows if e not in chosen]
        if ordered:
            # Same thing measured with structural differences (see ORDERED_CONCEPTS): missing years
            # fill in table order under the 25% rule; a candidate > 25% off in an overlap year never
            # fills (debt keeps filling: its recipes differ by construction, R22/R41 note the rest).
            far = [e for e in overlap if _off_by(rows[e].val, prim[e].val) > FILL_MAX_DIFF]
            if concept in LARGER_WINS:
                # a candidate that was the LARGER value in every far-off year is the fuller measure
                # (VZ's old capex tag beside the acquisitions line): it may fill; debt always fills
                far = [e for e in far if abs(rows[e].val) < abs(prim[e].val)] if concept != "debt" else []
            if far:
                far_off.append(lab)
                if concept not in LARGER_WINS:
                    refused.update(missing)
                continue
            if not missing:
                continue
            for e in missing:
                chosen[e] = rows[e]
                source[e] = lab
            unchecked[lab] = sorted(missing)
        elif not missing:
            continue
        elif overlap and not off:
            for e in missing:
                chosen[e] = rows[e]
                source[e] = lab
        else:
            refused.update(missing)                       # disagrees, or nothing to check it against
    if concept in LARGER_WINS:
        # R41 (fail-safe): per year, a candidate more than 25% larger than the chosen value replaces it
        for e in list(chosen):
            best = max((rows[e] for _, rows in ranked if e in rows), key=lambda f: abs(f.val))
            if abs(best.val) > abs(chosen[e].val) and _off_by(best.val, chosen[e].val) > FILL_MAX_DIFF:
                chosen[e], source[e] = best, best.tag
                larger.setdefault(best.tag, []).append(e)
    broke = any(e not in chosen for e in refused)         # a year left blank because tags disagree
    tag = plabel
    if "|" in plabel:                                     # combined pair: name the tag that supplied most years
        names = [f.tag for e, f in chosen.items() if source[e] == plabel]
        tag = max(set(names), key=names.count) if names else plabel
    mag = concept in MAGNITUDE_CONCEPTS
    ends = sorted(chosen)
    current_max = sorted(e for lab, e in log.get("current_max", ()) if source.get(e) == lab and e in chosen)
    self_copy: dict[str, list[str]] = {}
    for lab, e, part in log.get("self_copy", ()):
        if source.get(e) == lab and e in chosen:
            self_copy.setdefault(part, []).append(e)
    return Series(values={e: abs(chosen[e].val) if mag else chosen[e].val for e in ends}, unit=unit, tag=tag,
                  tag_break=broke, filed={e: chosen[e].filed for e in ends}, source={e: source[e] for e in ends},
                  disagree=disagree, unchecked=unchecked, basic_only=plabel in FILL_ONLY, far_off=far_off,
                  forms={e: chosen[e].form for e in ends}, larger={k: sorted(v) for k, v in larger.items()},
                  current_max=current_max, self_copy={k: sorted(v) for k, v in self_copy.items()})


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
    """(events, period ends whose EPS jump was contradicted by the share counts).

    Facts are compared per tag, unit and period end: the same period in two filings of the SAME
    tag (continuing-operations EPS never meets total EPS)."""
    look = _share_lookup(share_rows)
    unconfirmed: set[str] = set()
    by_end: dict[tuple[str, str, str], list[Fact]] = {}
    for f in eps_rows:
        if is_annual(f) and f.start:
            by_end.setdefault((f.tag, f.unit, f.end), []).append(f)
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
    return _drop_composites(sorted((lo, hi, r) for lo, hi, r in events)), sorted(unconfirmed)


def _drop_composites(events: list[tuple[str, str, float]]) -> list[tuple[str, str, float]]:
    """A period seen only before one split and after the next one shows their product (NVDA 4:1 then
    10:1 = 40; 2:1 then 3:1 = 6). That pair is not a third split: an event whose ratio is the product
    of two or more other events whose windows overlap its own is dropped (applied once, not twice)."""
    out = []
    for ev in events:
        inner = [o for o in events if o is not ev and max(ev[0], o[0]) < min(ev[1], o[1])]
        composite = False
        for n in range(2, len(inner) + 1):
            for combo in itertools.combinations(inner, n):
                if abs(math.prod(o[2] for o in combo) / ev[2] - 1.0) <= SPLIT_TOL:
                    composite = True
                    break
            if composite:
                break
        if not composite:
            out.append(ev)
    return out


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


def _node_facts(gaap: dict, ifrs: dict, name: str) -> list[Fact]:
    """Every fact (any form, any unit) of one tag, labelled with its full name."""
    ns, tag = _split_name(name)
    node = ((ifrs if ns == "ifrs-full" else gaap) or {}).get(tag) or {}
    return to_facts(node.get("units") or {}, _part_name(name))


def _per_share_rows(gaap: dict, ifrs: dict) -> tuple[list[Fact], list[Fact]]:
    """(annual facts of every EPS candidate tag, every fact of the diluted share-count tags)."""
    eps_rows: list[Fact] = []
    for cand in CONCEPTS["eps"]:
        for name in (cand if isinstance(cand, tuple) else (cand,)):
            eps_rows += [f for f in _node_facts(gaap, ifrs, name) if is_annual(f)]
    shares_rows: list[Fact] = []
    for name in SHARES_TAGS:
        shares_rows += _node_facts(gaap, ifrs, name)
    return eps_rows, shares_rows


def _adjuster(events: list[tuple[str, str, float]], *, inverse: bool = False) -> Adjust:
    """Fact -> Fact on today's share basis (per-share values x factor; share counts / factor)."""
    if not events:
        return None

    def adjust(f: Fact) -> Fact:
        k = factor_at(events, f.filed)
        return Fact(f.end, f.start, f.val / k if inverse else f.val * k, f.unit, f.tag, f.form, f.accn, f.filed)
    return adjust


RATIO_WINDOW_DAYS = 400               # a scanned split this close to the 10-Q ratio fact is the same split
SHARE_MOVE = 2.0                      # quarterly / annual diluted shares beyond this confirms a 10-Q ratio


def _share_move(gaap: dict, ifrs: dict, latest_annual_end: str) -> float | None:
    """Latest 10-Q diluted share count / the count at the latest annual end (first tag carrying both)."""
    for name in SHARES_TAGS:
        units = _units_of(gaap, ifrs, name)
        annual = annual_facts({"shares": units.get("shares") or []}, instant=False).get("shares", {}).get(latest_annual_end)
        q = _quarterly_shares_fact(units)
        if annual and q is not None and q.end > latest_annual_end and q.form in QUARTERLY_FORMS and annual.val > 0:
            return q.val / annual.val
    return None


def _ratio_events(gaap: dict, ifrs: dict, latest_annual_end: str | None,
                  events: list[tuple[str, str, float]]) -> list[tuple[str, str, float, str]]:
    """Splits so far reported only by a 10-Q StockholdersEquityNoteStockSplitConversionRatio1 fact
    (CVNA 5:1, May 2026): the ratio's date is after the latest annual end, it snaps to a clean ratio
    and the latest 10-Q diluted share count moved > 2x against the annual one in the same direction.
    Every per-share fact filed before that 10-Q is brought to the new basis. [(lo, hi, factor, note)]."""
    if not latest_annual_end:
        return []
    out: list[tuple[str, str, float, str]] = []
    for f in _node_facts(gaap, ifrs, "StockholdersEquityNoteStockSplitConversionRatio1"):
        if f.form not in QUARTERLY_FORMS or f.end <= latest_annual_end or f.val <= 0 or f.val == 1:
            continue
        m = _clean(max(f.val, 1.0 / f.val))
        if m is None:
            continue
        move = _share_move(gaap, ifrs, latest_annual_end)
        if move is None:
            continue
        if move > SHARE_MOVE:
            r, label = 1.0 / m, f"{m:g}:1"
        elif move < 1.0 / SHARE_MOVE:
            r, label = m, f"1:{m:g}"
        else:
            continue
        if any(ev[2] == r and abs((_date(ev[1]) - _date(f.filed)).days) <= RATIO_WINDOW_DAYS for ev in events
               if _date(ev[1]) and _date(f.filed)) or any(r == o[2] for o in out):
            continue
        out.append((f.filed, f.filed, r, f"split {label} from the 10-Q split ratio at {f.end}"))
    return out


def _derive_eps(series: dict[str, Series], internal: dict[str, Series], currency: str | None,
                notes: list[str]) -> list[str]:
    """R36: EPS = net income to common / diluted weighted shares for a year with no EPS tag (never for
    a year that has one); HSY-style fallback to the period-end share count. Not derived when NI/shares
    contradicts the EPS tag in the latest overlap years (mis-scaled share counts).

    Returns the period ends derived from the period-end share count (audit fix E6 extends that
    derivation to the TTM, see _derived_ttm_eps)."""
    ni = internal.get("ni_common")
    if not ni or not ni.values:
        return []
    eps = series.get("eps")
    unit = eps.unit if eps else (f"{currency}/shares" if currency else None)
    if unit is None or ni.unit != unit.split("/")[0]:
        return []
    sh, so = series.get("shares"), internal.get("shares_outstanding")
    if eps and sh:
        both = [e for e in sorted(eps.values) if e in ni.values and sh.values.get(e, 0) > 0
                and abs(eps.values[e]) >= SPLIT_MIN_EPS and eps.source.get(e) != "derived"][-3:]
        if any(_off_by(ni.values[e] / sh.values[e], eps.values[e]) > FILL_MAX_DIFF for e in both):
            notes.append("eps: NI/shares disagrees with the EPS tag; not derived")
            return []
    weighted, period_end, rescaled = [], [], []
    for e, v in ni.values.items():
        if eps and e in eps.values:
            continue
        count, fixed = _plausible_count(sh, e)
        if count is None:
            count, fixed = _plausible_count(so, e)
            bucket = period_end
        else:
            bucket = weighted
        if count is None:
            continue
        val = v / count
        if fixed:
            rescaled.append(f"{e[:4]} x{fixed:g}")
        if eps is None:
            eps = series["eps"] = Series(values={}, unit=unit, tag="derived")
        eps.values[e], eps.filed[e], eps.source[e], eps.forms[e] = val, ni.filed[e], "derived", ni.forms[e]
        bucket.append(e)
    if eps:
        eps.values = dict(sorted(eps.values.items()))
    if weighted:
        notes.append(f"eps: {', '.join(e[:4] for e in sorted(weighted))} derived from NI/shares")
    if period_end:
        notes.append(f"eps: {', '.join(e[:4] for e in sorted(period_end))} derived from NI/period-end shares")
    if rescaled:
        notes.append(f"eps: share count rescaled for the derived EPS ({', '.join(rescaled)})")
    return sorted(period_end)


SCALE_STEPS = (1e3, 1e6, 1e-3, 1e-6)
NEIGHBOUR_TOL = 2.0                   # a share count within x2 of its neighbours' median is plausible


def _plausible_count(s: Series | None, end: str) -> tuple[float | None, float | None]:
    """(share count for `end`, rescale factor applied or None): a count more than 2x away from the
    median of its two nearest years is mis-scaled (HSY tags FY2009 diluted shares in thousands);
    it is brought back by a power of 1000 when that lands within 2x, else the year is skipped."""
    if not s or s.values.get(end, 0) <= 0:
        return None, None
    val = s.values[end]
    others = sorted((abs(_days(e, end)) if e <= end else abs(_days(end, e)), v) for e, v in s.values.items()
                    if e != end and v > 0)[:2]
    if not others:
        return val, None
    ref = sorted(v for _, v in others)[len(others) // 2]
    if 1 / NEIGHBOUR_TOL <= val / ref <= NEIGHBOUR_TOL:
        return val, None
    for k in SCALE_STEPS:
        if 1 / NEIGHBOUR_TOL <= val * k / ref <= NEIGHBOUR_TOL:
            return val * k, k
    return None, None


YTD9_MIN_DAYS, YTD9_MAX_DAYS = 240, 290
MISDATED_MAX_RATIO = 1.5


def _rescue_misdated_revenue(gaap: dict, ifrs: dict, series: dict[str, Series], latest: str | None,
                             currency: str | None, notes: list[str]) -> None:
    """LHX: the FY2025 10-K tags the full-year revenue with the fourth quarter's dates, so no annual
    fact exists. A 10-K fact ending exactly at the latest annual year end (shorter than a year) is
    taken as the annual value when it is >= the same year's 9-month YTD 10-Q value of the same tag
    and <= 1.5x it (a quarter can never exceed the YTD)."""
    rev = series.get("revenue")
    if latest is None or (rev and latest in rev.values):
        return
    unit = rev.unit if rev else currency
    if not unit:
        return
    for name in (c for c in CONCEPTS["revenue"] if isinstance(c, str)):
        rows = [f for f in _node_facts(gaap, ifrs, name) if f.unit == unit and f.start]
        short = [f for f in rows if f.form in ANNUAL_FORMS and f.end == latest and _days(f.start, f.end) < MIN_DAYS]
        if not short:
            continue
        fy = max(short, key=lambda f: (f.filed, f.accn))
        ytds = [f for f in rows if f.form in QUARTERLY_FORMS and f.end < latest
                and YTD9_MIN_DAYS <= _days(f.start, f.end) <= YTD9_MAX_DAYS and _days(f.end, latest) <= 120]
        if not ytds:
            continue
        ytd = max(ytds, key=lambda f: (f.end, f.filed))
        if ytd.val > 0 and ytd.val <= fy.val <= MISDATED_MAX_RATIO * ytd.val:
            if rev is None:
                rev = series["revenue"] = Series(values={}, unit=unit, tag=name)
            rev.values[latest], rev.filed[latest], rev.source[latest], rev.forms[latest] = fy.val, fy.filed, name, fy.form
            rev.values = dict(sorted(rev.values.items()))
            notes.append(f"revenue: 10-K fact {fy.start}..{fy.end} taken as the annual value "
                         f"(above the 9-month YTD {ytd.start}..{ytd.end})")
            return


CARRY_ZERO_INTEREST_SHARE = 0.01      # = metrics.NO_DEBT_INTEREST_SHARE: interest < 1% of operating income reads as no debt


def _short_money(v: float) -> str:
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= div:
            return f"{v / div:g}{suf}"
    return f"{v:g}"


def _carry_zero_debt(series: dict[str, Series], year_ends: set[str] | None, notes: list[str]) -> None:
    """A debt tag that ended at an explicit 0 and was then dropped (notes repaid: MDB, OUST, SOUN)
    means 0 in the later fiscal years that no debt candidate covers - but only while the year's
    interest expense agrees (fail-safe): no interest value for the year, an explicit 0, or interest
    below CARRY_ZERO_INTEREST_SHARE of a positive operating income. The carry stops at the first
    year that fails the guard; that year and every later one stay blank (the row caps at WAIT)."""
    debt = series.get("debt")
    if not debt or not debt.values or not year_ends:
        return
    last = max(debt.values)
    later = sorted(e for e in year_ends if e > last)
    if not later or debt.values[last] != 0:
        return
    interest = series["interest"].values if "interest" in series else {}
    op_income = series["op_income"].values if "op_income" in series else {}
    carried, stopped = [], None
    for e in later:
        i, op = interest.get(e), op_income.get(e)
        if i is None or i == 0 or (op is not None and op > 0 and abs(i) / op < CARRY_ZERO_INTEREST_SHARE):
            debt.values[e], debt.filed[e], debt.source[e], debt.forms[e] = 0.0, debt.filed[last], "carried 0", debt.forms[last]
            carried.append(e)
            continue
        stopped = (f" (stopped at {e[:4]}: interest {_short_money(i)} vs operating income "
                   f"{_short_money(op) if op is not None else 'n/a'})")
        break
    if carried:
        notes.append(f"debt: 0 carried forward to {', '.join(e[:4] for e in carried)} (tag ended at 0 in {last[:4]}){stopped or ''}")
    else:
        notes.append(f"debt: 0 not carried forward (tag ended at 0 in {last[:4]}){stopped}")


def build_snapshot(symbol: str, cik: int, companyfacts: dict, *, sic: int | None, name: str,
                   fetched_at: str) -> Snapshot:
    facts = (companyfacts or {}).get("facts") or {}
    gaap, ifrs = facts.get("us-gaap") or {}, facts.get("ifrs-full") or {}
    series: dict[str, Series] = {}
    internal: dict[str, Series] = {}
    # The reporting currency is net income's unit (fallback revenue, then equity); every other
    # money concept prefers it on a coverage tie. Net income also dates the company.
    currency, latest = None, None
    for c in ("net_income", "revenue", "equity"):
        s = resolve_series(gaap, ifrs, c, sic=sic)
        if s and s.values:
            currency, latest = s.unit, max(s.values)
            break
    # Splits first (every per-share tag, confirmed by the diluted share counts, plus a 10-Q split
    # ratio the annual filings do not reflect yet), so every per-share candidate is compared and
    # assembled on today's share basis.
    eps_rows, shares_rows = _per_share_rows(gaap, ifrs)
    events, unconfirmed = _split_scan(eps_rows, shares_rows)
    notes: list[str] = [f"eps: unconfirmed split at {e}" for e in unconfirmed]
    ratio = _ratio_events(gaap, ifrs, latest, events)
    events = sorted(events + [(lo, hi, r) for lo, hi, r, _ in ratio])
    notes.extend(n for *_, n in ratio)
    adjust_for = {c: _adjuster(events, inverse=c in SHARE_COUNT_CONCEPTS) for c in PER_SHARE_CONCEPTS | SHARE_COUNT_CONCEPTS}
    for c in TABLE_CONCEPTS + ("ni_common",):
        if c not in INSTANT_CONCEPTS:
            s = resolve_series(gaap, ifrs, c, prefer_currency=currency, adjust=adjust_for.get(c), sic=sic)
            if s:
                (internal if c in INTERNAL_CONCEPTS else series)[c] = s
    _rescue_misdated_revenue(gaap, ifrs, series, latest, currency, notes)
    # Balance-sheet instants only at fiscal year ends (drops e.g. lease-adoption dates).
    year_ends = set().union(*(series[c].values for c in ("revenue", "net_income", "cfo") if c in series)) or None
    for c in TABLE_CONCEPTS + ("shares_outstanding",):
        if c in INSTANT_CONCEPTS:
            s = resolve_series(gaap, ifrs, c, year_ends=year_ends, prefer_currency=currency,
                               adjust=adjust_for.get(c), sic=sic)
            if s:
                (internal if c in INTERNAL_CONCEPTS else series)[c] = s
    period_end_eps = _derive_eps(series, internal, currency, notes)
    _carry_zero_debt(series, year_ends, notes)

    split_factor = 1.0
    eps = series.get("eps")
    if events and eps and eps.values:
        oldest = min(eps.values)
        split_factor = factor_at(events, eps.filed[oldest])
    if events:
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
        for lab, ends in s.larger.items():
            notes.append(f"{c}: candidates differ, larger used in {', '.join(e[:4] for e in ends)} ({lab})")
        if s.basic_only:
            notes.append(f"{c}: basic-only")
        if s.current_max:
            notes.append(f"{c}: {', '.join(e[:4] for e in s.current_max)} current part from DebtCurrent "
                         f"(total current debt), larger than the recipe's current parts")
        for part, ends in s.self_copy.items():
            notes.append(f"{c}: {', '.join(e[:4] for e in ends)} {part} equals the noncurrent part in the same "
                         f"filing (filer error), not added")
    debt = series.get("debt")
    tags = {c: s.tag for c, s in series.items()}
    if debt and debt.values:
        lab = debt.source.get(max(debt.values), debt.tag)
        if lab in PARTIAL_DEBT:
            notes.append(f"debt: partial recipe {lab}")
        # audit fix E7 (B6b): name the recipe that supplied the latest year (MAR: the total tag the
        # larger-value rule took, not the 23M LongTermDebt fragment that ranked first)
        real = [debt.source[e] for e in sorted(debt.values) if debt.source.get(e) not in (None, "carried 0")]
        if real:
            tags["debt"] = real[-1]
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
    derived = None
    if latest and latest in period_end_eps and "ni_common" in internal and "shares_outstanding" in internal:
        derived = (internal["ni_common"], internal["shares_outstanding"])
    ttm, ttm_end, ttm_is_fy, period_end, ttm_notes, ttm_ends, balance_ends = _ttm_fields(gaap, ifrs, series, latest,
                                                                                          events, derived)
    notes.extend(ttm_notes)
    return Snapshot(symbol=symbol, cik=cik, name=name, sic=sic, currency=currency,
                    annual=dict(sorted(annual.items())), units={c: s.unit for c, s in series.items()},
                    tags=tags, tag_breaks=tag_breaks,
                    ttm=ttm, ttm_period_end=ttm_end, ttm_is_fy=ttm_is_fy, latest_annual_end=latest,
                    latest_period_end=period_end, fetched_at=fetched_at, split_factor=split_factor, notes=notes,
                    ttm_ends=ttm_ends, balance_ends=balance_ends,
                    annual_form=series["net_income"].forms.get(latest) if "net_income" in series else None)


def merge_companyfacts(base: dict, extra: dict, *, allowed_accns: set[str] | None = None) -> dict:
    """`base`'s companyfacts plus `extra`'s rows from accessions `base` has nowhere (R42).

    XOM: the SEC moved the ticker to ExxonMobil Holdings (2115436), which holds the 2026 10-Qs and
    no history. NEE: Q2/Q3 2026 facts of its combined 10-Qs sit under Florida Power & Light's CIK.
    An accession already present in `base` is never taken (the same combined filing carries the
    other entity's numbers); `allowed_accns` keeps only the filer's own accessions.
    """
    out = copy.deepcopy(base or {})
    facts = out.setdefault("facts", {})
    have = {r.get("accn") for ns in facts.values() for node in (ns or {}).values()
            for rows in ((node or {}).get("units") or {}).values() for r in rows or []}
    for ns, tags in ((extra or {}).get("facts") or {}).items():
        for tag, node in (tags or {}).items():
            for unit, rows in ((node or {}).get("units") or {}).items():
                keep = [r for r in rows or [] if r.get("accn") not in have
                        and (allowed_accns is None or r.get("accn") in allowed_accns)]
                if not keep:
                    continue
                dst = facts.setdefault(ns, {}).setdefault(tag, {k: v for k, v in node.items() if k != "units"})
                dst.setdefault("units", {}).setdefault(unit, []).extend(copy.deepcopy(keep))
    return out


# ---------- TTM and date stamps ----------

PERIOD_FORMS = ANNUAL_FORMS | QUARTERLY_FORMS       # every form a latest instant / period end may come from
YTD_MIN_DAYS, YTD_MAX_DAYS = 80, 290
QTR_MIN_DAYS, QTR_MAX_DAYS = 80, 100
DATE_TOL = 7                                         # +-days when matching period starts/ends
TTM_FLOWS = ("revenue", "net_income", "cfo", "capex", "sbc", "interest", "op_income", "eps")
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


def _flow_ttm(gaap: dict, ifrs: dict, concept: str, s: Series, end: str,
              scale: Callable[[Fact], float] | None = None) -> tuple[float, str, bool]:
    """TTM for one resolved flow series, from the tag (or recipe) that supplied its latest annual value.

    `scale` (per-share flows): multiplier per fact, the split factor at its filing date."""
    fy_val = s.values[end]
    mag = concept in MAGNITUDE_CONCEPTS
    label = s.source.get(end, s.tag)

    def same(v: float) -> bool:
        return math.isclose(abs(v) if mag else v, fy_val, rel_tol=1e-9, abs_tol=0.5 if not scale else 0.005)

    def val(f: Fact) -> float:
        return f.val * scale(f) if scale else f.val

    if "+" not in label:
        names = [s.tag] + [n for n in _label_names(label) if n != s.tag]
        for name in names:
            fy, ytd, prior = _ttm_parts(_units_of(gaap, ifrs, name), s.unit, end)
            if fy is None or not same(val(fy)):
                continue                                   # this tag did not supply the annual value
            if ytd is None or prior is None:
                break
            v = val(fy) + val(ytd) - val(prior)
            return (abs(v) if mag else v), ytd.end, False
        return fy_val, end, True
    total_fy, total, ends = 0.0, 0.0, set()                # sum recipe: every present part needs a TTM
    for name in label.split("+"):
        fy, ytd, prior = _ttm_parts(_units_of(gaap, ifrs, name), s.unit, end)
        if fy is None:
            if _optional(name, label):
                continue
            return fy_val, end, True
        if ytd is None or prior is None:
            return fy_val, end, True
        total_fy += val(fy)
        total += val(fy) + val(ytd) - val(prior)
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
    required = [n for n in names if len(names) == 1 or not _optional(n, label)]
    ends = set.intersection(*(set(per[n]) for n in required)) if required else set()
    if not ends:
        return None
    e = max(ends)
    if len(names) > 1:
        ann_common = set.intersection(*(annual_ends[n] for n in required)) & ends
        ae = max(ann_common) if ann_common else None
        # an optional part whose annual value was an explicit 0 (LHX ShortTermBorrowings at FY2025)
        # counts as 0 at the later 10-Q too: nothing to carry, the fresh instant stands
        dropped = [n for n in names if n not in required and ae is not None and e > ae
                   and ae in annual_ends[n] and e not in per[n] and (ae not in per[n] or per[n][ae].val != 0)]
        if dropped and label in CURRENT_MAX and _current_alt(
                label, *(_instant_map(gaap, ifrs, n).get((unit, e)) for n in
                         (CURRENT_MAX[label], DEBT_CURRENT, "LongTermDebtNoncurrent", DEBT_COMBINED)),
                label not in LTD_EXCLUDES_CURRENT or ltd_excludes_current(gaap, ifrs)) is not None:
            dropped = []                  # DebtCurrent at the 10-Q instant holds the whole current block (XOM)
        if dropped:
            if notes is not None:
                notes.append(f"{label}: {', '.join(dropped)} untagged at {e}; recipe dated at the annual end {ae}")
            e = ae
    total = 0.0
    for n in names:
        f = per[n].get(e)
        if f is None:
            continue
        if n != names[0] and len(names) > 1 and _optional(n, label) and e in per[names[0]] \
                and _self_copy(gaap, ifrs, names[0], f):
            if notes is not None:
                notes.append(f"debt: {n} at {e} equals the noncurrent part in the same filing (filer error), not added")
            continue
        total += f.val
    if label in CURRENT_MAX:
        at = {n: _instant_map(gaap, ifrs, n).get((unit, e)) for n in
              (CURRENT_MAX[label], DEBT_CURRENT, "LongTermDebtNoncurrent", DEBT_COMBINED)}
        gated_ok = label not in LTD_EXCLUDES_CURRENT or ltd_excludes_current(gaap, ifrs)
        v = _current_alt(label, at[CURRENT_MAX[label]], at[DEBT_CURRENT], at["LongTermDebtNoncurrent"],
                         at[DEBT_COMBINED], gated_ok)
        if v is not None and v > total:
            if notes is not None:
                notes.append(f"debt: current part at {e} from DebtCurrent (total current debt, "
                             f"{_short_money(at[DEBT_CURRENT].val)}), larger than the recipe's current parts")
            total = v
    return e, total


def _period_end(gaap: dict, ifrs: dict, s: Series) -> str | None:
    """Latest period end of any period form across the tags behind one series."""
    names = {s.tag}
    for lab in set(s.source.values()):
        names.update(_label_names(lab))
    ends = [f.end for n in names for f in to_facts({s.unit: _units_of(gaap, ifrs, n).get(s.unit) or []})
            if f.form in PERIOD_FORMS]
    ends += list(s.values)
    return max(ends) if ends else None


def _derived_ttm_eps(gaap: dict, ifrs: dict, ni: Series, so: Series, latest: str,
                     events: list[tuple[str, str, float]] = ()) -> tuple[float, tuple[str, bool], str] | None:
    """Audit fix E6 (B7): a filer with no EPS tag and no diluted share count (HSY tags both per class)
    gets the TTM EPS the way its annual EPS was derived: NI to common TTM / the latest period-end
    CommonStockSharesOutstanding (split-adjusted, not older than the latest annual end, within x2 of
    the annual count). (value, stamp, note) or None."""
    if latest not in ni.values or latest not in so.values or so.values[latest] <= 0:
        return None
    v, end, is_fy = _flow_ttm(gaap, ifrs, "ni_common", ni, latest)
    facts = [f for (u, e), f in _instant_map(gaap, ifrs, so.source.get(latest, so.tag)).items()
             if u == so.unit and e >= latest and f.val > 0]
    if not facts:
        return None
    f = max(facts, key=lambda f: (f.end, f.filed, f.accn))
    count = f.val / (factor_at(events, f.filed) if events else 1.0)
    if not 1 / NEIGHBOUR_TOL <= count / so.values[latest] <= NEIGHBOUR_TOL:
        return None
    basis = "FY" if is_fy else "TTM"
    return v / count, (end, is_fy), f"eps TTM derived from NI/period-end shares ({basis} {end}, shares at {f.end})"


def _ttm_fields(gaap: dict, ifrs: dict, series: dict[str, Series], latest: str | None,
                events: list[tuple[str, str, float]] = (), derived: tuple[Series, Series] | None = None):
    """(ttm, ttm_period_end, ttm_is_fy, latest_period_end, notes, ttm_ends, balance_ends).

    Balances and the quarterly share count come from the candidate that supplied the latest annual
    year (a larger debt recipe, the basic share count filling the diluted one), not the primary."""
    ttm: dict[str, float] = {}
    stamps: dict[str, tuple[str, bool]] = {}
    balance_ends: dict[str, str] = {}
    notes: list[str] = []
    if latest is None:
        return ttm, None, False, None, notes, stamps, balance_ends
    scale = (lambda f: factor_at(events, f.filed)) if events else None
    for c in TTM_FLOWS:
        s = series.get(c)
        if s and latest in s.values:
            v, e, fy = _flow_ttm(gaap, ifrs, c, s, latest, scale if c in PER_SHARE_CONCEPTS else None)
            ttm[c], stamps[c] = v, (e, fy)
    if derived is not None and "eps" in ttm:
        got = _derived_ttm_eps(gaap, ifrs, *derived, latest, events)
        if got is not None:
            ttm["eps"], stamps["eps"], note = got
            notes.append(note)
    for c in TTM_BALANCES:
        s = series.get(c)
        if not s or not s.values:
            continue
        end, val = max(s.values.items())
        got = _latest_instant(gaap, ifrs, s.source.get(end, s.tag), s.unit, notes)
        if got and got[0] >= end:
            end, val = got
        ttm[c], balance_ends[c] = val, end
    s = series.get("shares")
    if s and s.values:
        last = max(s.values)
        q = _quarterly_shares_fact(_units_of(gaap, ifrs, s.source.get(last, s.tag)), s.unit)
        if q is not None:
            k = factor_at(events, q.filed) if events else 1.0
            ttm["shares"], balance_ends["shares"] = q.val / k, q.end
        else:
            ttm["shares"], balance_ends["shares"] = s.values[last], last
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

    def submission(self, cik: int, *, with_accessions: bool = False) -> dict:
        """Trimmed submissions record; `with_accessions` adds "accessions" (the recent filings' accession
        numbers, for `merge_companyfacts(allowed_accns=...)` of a co-registrant CIK)."""
        raw = self._json(SUBMISSIONS_URL.format(cik=int(cik))) or {}
        out = {k: raw.get(k) for k in SUBMISSION_KEYS}
        try:
            out["sic"] = int(str(out["sic"]).strip()) if out["sic"] not in (None, "") else None
        except ValueError:
            out["sic"] = None
        if with_accessions:
            recent = ((raw.get("filings") or {}).get("recent") or {}).get("accessionNumber") or []
            out["accessions"] = [str(a) for a in recent]
        return out
