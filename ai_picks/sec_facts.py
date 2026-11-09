"""Pull the handful of financial-statement numbers AI Picks needs out of SEC's bulk
companyfacts.zip into one small table.

Every row keeps ``filed`` (the day the number became public), so later code can
ask "what did the market know on date X" without look-ahead.

Run off-hours: ``python -m ai_picks.sec_facts``.
"""
from __future__ import annotations

import json
import zipfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "artifacts" / "ai_picks_data"

# concept -> SEC tags tried in order (first tag that has data for a given period wins)
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet", "SalesRevenueGoodsNet"),
    "op_income": ("OperatingIncomeLoss",),
    "dna": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
            "DepreciationAmortizationAndAccretionNet", "Depreciation"),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "eps": ("EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted", "EarningsPerShareBasic"),
    "cfo": ("NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"),
    "debt_total": ("LongTermDebt", "LongTermDebtAndCapitalLeaseObligations", "DebtInstrumentCarryingAmount"),
    "debt_noncurrent": ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligationsNoncurrent"),
    "debt_current": ("LongTermDebtCurrent", "DebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent"),
    "short_borrow": ("ShortTermBorrowings", "CommercialPaper"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash"),
    "shares": ("EntityCommonStockSharesOutstanding", "CommonStockSharesOutstanding"),
    # balance sheet for Buffett/Greenblatt-style return-on-capital checks
    "equity": ("StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"),
    "assets_current": ("AssetsCurrent",),
    "liab_current": ("LiabilitiesCurrent",),
    "ppe": ("PropertyPlantAndEquipmentNet",),
    # dual-class companies (META, HOOD) report cover-page shares only per class, which
    # companyfacts leaves out; their diluted share count is the fallback
    "shares_wavg": ("WeightedAverageNumberOfDilutedSharesOutstanding",
                    "WeightedAverageNumberOfSharesOutstandingBasic"),
}
FORMS = {"10-K", "10-Q", "10-K/A", "10-Q/A", "20-F", "40-F", "10-KT", "10-QT"}
TAG_TO_CONCEPT = {tag: (concept, rank) for concept, tags in CONCEPTS.items() for rank, tag in enumerate(tags)}


def extract_company(doc: dict) -> list[tuple]:
    """Rows (cik, concept, rank, start, end, val, filed, form) for one companyfacts document."""
    cik = int(doc.get("cik") or 0)
    out = []
    for taxonomy in ("us-gaap", "dei"):
        for tag, body in (doc.get("facts", {}).get(taxonomy) or {}).items():
            hit = TAG_TO_CONCEPT.get(tag)
            if not hit:
                continue
            concept, rank = hit
            for unit, facts in (body.get("units") or {}).items():
                is_shares = concept in ("shares", "shares_wavg")
                if is_shares and unit != "shares":
                    continue
                if concept == "eps" and unit != "USD/shares":
                    continue
                if not is_shares and concept != "eps" and unit != "USD":
                    continue
                for f in facts:
                    if f.get("form") not in FORMS or f.get("val") is None or not f.get("filed"):
                        continue
                    if is_shares and not float(f["val"]) > 0:
                        continue
                    out.append((cik, concept, rank, f.get("start"), f["end"], float(f["val"]), f["filed"],
                                f["form"]))
    return out


def _work(args: tuple[str, list[str]]) -> tuple[list[tuple], list[tuple]]:
    path, names = args
    rows, names_out = [], []
    with zipfile.ZipFile(path) as zf:
        for name in names:
            try:
                doc = json.loads(zf.read(name))
            except Exception:
                continue
            got = extract_company(doc)
            if got:
                rows.extend(got)
                names_out.append((int(doc.get("cik") or 0), doc.get("entityName") or ""))
    return rows, names_out


def main() -> None:
    path = str(DATA / "companyfacts.zip")
    with zipfile.ZipFile(path) as zf:
        names = [n for n in zf.namelist() if n.endswith(".json")]
    batches = [(path, names[i:i + 400]) for i in range(0, len(names), 400)]
    rows, entities = [], []
    with ProcessPoolExecutor(max_workers=6) as pool:
        for i, (r, e) in enumerate(pool.map(_work, batches)):
            rows.extend(r)
            entities.extend(e)
            if i % 10 == 0:
                print(f"{(i + 1) * 400}/{len(names)} files, {len(rows)} rows", flush=True)
    df = pd.DataFrame(rows, columns=["cik", "concept", "rank", "start", "end", "val", "filed", "form"])
    for col in ("start", "end", "filed"):
        df[col] = pd.to_datetime(df[col], errors="coerce")
    df.to_parquet(DATA / "facts.parquet", index=False)
    pd.DataFrame(entities, columns=["cik", "name"]).to_parquet(DATA / "entities.parquet", index=False)
    print(f"done: {len(df)} rows, {len(entities)} companies")


if __name__ == "__main__":
    main()
