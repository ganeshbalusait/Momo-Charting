"""Build tests/fixtures/long_term/* from raw SEC company-facts files.

Usage: python scripts/long_term_fixtures.py <folder with raw <SYM>.json files>
       python scripts/long_term_fixtures.py --edgar-only <folder> [SYM ...]   (EDGAR_ONLY_CIKS: companyfacts only;
                                                                         naming symbols rebuilds only those)

Network is used by THIS script only (Finnhub metric + SEC submissions); the
tests never touch it. The Finnhub key is read from .env and never printed.
The --edgar-only set pins the tag-resolution recipes of the "no data" fix
(2026-11-07) and needs no Finnhub / submissions files.
"""
from __future__ import annotations

import gzip
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "long_term"

sys.path.insert(0, str(ROOT))
from long_term.edgar import ALL_TAGS, TAGS_BY_NAMESPACE  # noqa: E402  (the one tag table)

assert {"Revenues", "Revenue", "EntityCommonStockSharesOutstanding"} <= ALL_TAGS
FORMS = {"10-K", "10-K/A", "10-KT", "10-Q", "20-F", "20-F/A", "40-F"}
CIKS = {"AAPL": 320193, "ASML": 937966, "TSM": 1046179, "HOOD": 1783879, "MCD": 63908,
        "HD": 354950, "JPM": 19617, "GOOGL": 1652044, "NVDA": 1045810, "CRWV": 1769628}
# companyfacts-only fixtures (no-data fix 2026-11-07): HAL = EPS tag switch to continuing+discontinued
# per share; LHX = full-year revenue tagged with Q4 dates in the FY2025 10-K; HSY = two-class filer
# with no EPS tag (derived from NI / period-end shares); WELL = REIT capex (capital improvements +
# construction); MAR = LongTermDebt is a 23M component, the real debt sits in (d); XOM / XOM2 = the
# facts split between the old CIK 34088 and the successor CIK 2115436 (merge_companyfacts).
# DECK (audit fix E1, 2026-11-07) = the 6-for-1 split of 2024 (FY2017-FY2022 EPS only in pre-split filings).
EDGAR_ONLY_CIKS = {"HAL": 45012, "LHX": 202058, "HSY": 47111, "WELL": 766704, "MAR": 1048286,
                   "XOM": 34088, "XOM2": 2115436, "DECK": 910521}
UA = "AGX Trading App (admin@agxtrade.com)"
MAX_BYTES = 600_000


def trim_facts(doc: dict, floor: str = "2012-01-01") -> dict:
    facts: dict = {}
    for ns, tags in TAGS_BY_NAMESPACE.items():
        src = (doc.get("facts") or {}).get(ns) or {}
        for tag in tags:
            node = src.get(tag)
            if not node:
                continue
            units = {}
            for unit, rows in (node.get("units") or {}).items():
                keep = [r for r in rows if r.get("form") in FORMS and str(r.get("end", "")) >= floor]
                if keep:
                    units[unit] = keep
            if units:
                facts.setdefault(ns, {})[tag] = {**{k: v for k, v in node.items() if k != "units"}, "units": units}
    return {"cik": doc.get("cik"), "entityName": doc.get("entityName"), "facts": facts}


def _get(url: str, headers: dict) -> bytes:
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
        if resp.headers.get("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        return data


def _trim_to_file(sym: str, doc: dict) -> None:
    floor = "2012-01-01"
    trimmed = trim_facts(doc, floor)
    text = json.dumps(trimmed, separators=(",", ":"))
    if len(text.encode()) > MAX_BYTES:
        floor = "2014-01-01"
        trimmed = trim_facts(doc, floor)
        text = json.dumps(trimmed, separators=(",", ":"))
        print(f"{sym}: raised floor to {floor}")
    (OUT / f"companyfacts_{sym}.json").write_text(text, encoding="utf-8")
    print(f"{sym}: companyfacts {len(text.encode())} bytes")


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "--edgar-only":
        OUT.mkdir(parents=True, exist_ok=True)
        wanted = [s.upper() for s in argv[3:]] or list(EDGAR_ONLY_CIKS)
        unknown = [s for s in wanted if s not in EDGAR_ONLY_CIKS]
        if unknown:
            print(f"not in EDGAR_ONLY_CIKS: {', '.join(unknown)}")
            return 2
        for sym in wanted:
            _trim_to_file(sym, json.loads((Path(argv[2]) / f"{sym}.json").read_text(encoding="utf-8")))
        return 0
    if len(argv) != 2:
        print(__doc__)
        return 2
    raw_dir = Path(argv[1])
    OUT.mkdir(parents=True, exist_ok=True)
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    key = os.getenv("FINNHUB_API_KEY", "")
    for sym, cik in CIKS.items():
        _trim_to_file(sym, json.loads((raw_dir / f"{sym}.json").read_text(encoding="utf-8")))
        if key:
            body = _get(f"https://finnhub.io/api/v1/stock/metric?symbol={sym}&metric=all&token={key}", {})
            (OUT / f"finnhub_{sym}.json").write_bytes(body)
            time.sleep(1.0)
        sub = json.loads(_get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json",
                              {"User-Agent": UA, "Accept-Encoding": "gzip"}))
        keep = {k: sub.get(k) for k in ("name", "sic", "sicDescription", "fiscalYearEnd", "tickers")}
        (OUT / f"submissions_{sym}.json").write_text(json.dumps(keep, indent=1), encoding="utf-8")
        time.sleep(0.3)
    rows = [(s, c, doc_name) for s, c, doc_name in
            [(s, c, json.loads((OUT / f"submissions_{s}.json").read_text())["name"]) for s, c in CIKS.items()]]
    tickers = {str(i): {"cik_str": c, "ticker": s, "title": n} for i, (s, c, n) in enumerate(rows)}
    tickers[str(len(tickers))] = {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet Inc."}
    tickers[str(len(tickers))] = {"cik_str": 1067983, "ticker": "BRK-B", "title": "BERKSHIRE HATHAWAY INC"}
    (OUT / "company_tickers.json").write_text(json.dumps(tickers, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
