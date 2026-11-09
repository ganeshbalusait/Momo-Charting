"""Download the quarterly 13F stock holdings of well-known billionaire investors (SEC, free).

A 13F lists every US stock a big manager held at quarter end. It is filed up to 45
days later; ``filed`` is kept so a back-test can only copy what was public.
CUSIPs are turned into tickers with OpenFIGI's free mapping API.

Run off-hours: ``python -m ai_picks.download_13f``.
"""
from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).resolve().parent.parent / "artifacts" / "ai_picks_data"
UA = {"User-Agent": "AGX-Research research@example.com"}
SINCE = "2015-06-01"

MANAGERS = {
    1067983: "Warren Buffett (Berkshire Hathaway)",
    1336528: "Bill Ackman (Pershing Square)",
    1536411: "Stanley Druckenmiller (Duquesne)",
    1656456: "David Tepper (Appaloosa)",
    1006438: "David Tepper (Appaloosa, older filer)",
    1649339: "Michael Burry (Scion)",
    1061768: "Seth Klarman (Baupost)",
    1079114: "David Einhorn (Greenlight)",
    1040273: "Dan Loeb (Third Point)",
    1709323: "Li Lu (Himalaya)",
    1549575: "Mohnish Pabrai (Dalal Street)",
    1112520: "Chuck Akre (Akre Capital)",
    1167483: "Chase Coleman (Tiger Global)",
    1135730: "Philippe Laffont (Coatue)",
    1103804: "Andreas Halvorsen (Viking)",
    1061165: "Stephen Mandel (Lone Pine)",
    1166559: "Bill Gates (Gates Foundation Trust)",
    1029160: "George Soros (Soros Fund)",
}


def _get(url: str, **kw) -> requests.Response:
    for attempt in range(6):
        resp = requests.get(url, headers=UA, timeout=60, **kw)
        time.sleep(0.15)  # SEC asks for under 10 requests a second
        if resp.status_code in (429, 503):
            time.sleep(5 * (attempt + 1))
            continue
        return resp
    return resp


def filings(cik: int) -> list[dict]:
    doc = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json").json()
    blocks = [doc["filings"]["recent"]]
    for extra in doc["filings"].get("files", []):
        blocks.append(_get(f"https://data.sec.gov/submissions/{extra['name']}").json())
    out = []
    for b in blocks:
        for form, acc, filed, period in zip(b["form"], b["accessionNumber"], b["filingDate"], b["reportDate"]):
            # amendments are skipped: most only add late pages or confidential positions
            if form == "13F-HR" and filed >= SINCE:
                out.append({"acc": acc, "filed": filed, "period": period})
    return out


def holdings(cik: int, acc: str) -> list[dict]:
    folder = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}"
    items = _get(f"{folder}/index.json").json()["directory"]["item"]
    xmls = [i["name"] for i in items if i["name"].lower().endswith(".xml") and "primary_doc" not in i["name"].lower()]
    rows = []
    for name in xmls:
        text = _get(f"{folder}/{name}").content
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            continue
        for node in root.iter():
            if not node.tag.endswith("infoTable"):
                continue
            get = {child.tag.split("}")[-1]: child for child in node}
            if "putCall" in get:
                continue  # options are not stock holdings
            shares = next((c.text for c in node.iter() if c.tag.endswith("sshPrnamt")), None)
            kind = next((c.text for c in node.iter() if c.tag.endswith("sshPrnamtType")), "SH")
            if kind != "SH":
                continue
            rows.append({"issuer": get["nameOfIssuer"].text, "cusip": (get["cusip"].text or "").upper(),
                         "value": float(get["value"].text or 0), "shares": float(shares or 0)})
    return rows


def figi_tickers(cusips: list[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for i in range(0, len(cusips), 10):
        jobs = [{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c in cusips[i:i + 10]]
        for attempt in range(8):
            resp = requests.post("https://api.openfigi.com/v3/mapping", json=jobs, timeout=60)
            if resp.status_code == 429:
                time.sleep(15)
                continue
            break
        resp.raise_for_status()
        for cusip, res in zip(cusips[i:i + 10], resp.json()):
            data = [d for d in res.get("data") or [] if d.get("marketSector") == "Equity"]
            if data:
                out[cusip] = data[0]["ticker"].replace("/", ".").upper()
        time.sleep(2.5)  # anonymous limit: 25 requests a minute
        if i % 500 == 0:
            print(f"figi {i}/{len(cusips)}", flush=True)
    return out


def main() -> None:
    rows = []
    for cik, who in MANAGERS.items():
        fl = filings(cik)
        print(f"{who}: {len(fl)} filings", flush=True)
        for f in fl:
            for h in holdings(cik, f["acc"]):
                rows.append({"manager": who, "cik": cik, **f, **h})
    df = pd.DataFrame(rows)
    cache = DATA / "cusip_tickers.json"
    known = json.loads(cache.read_text()) if cache.exists() else {}
    todo = sorted(set(df["cusip"]) - set(known))
    known.update(figi_tickers(todo))
    cache.write_text(json.dumps(known))
    df["symbol"] = df["cusip"].map(known)
    df.to_parquet(DATA / "holdings_13f.parquet", index=False)
    print(f"done: {len(df)} rows, {df['cusip'].nunique()} securities, "
          f"{df['symbol'].notna().mean() * 100:.0f}% of rows mapped to a ticker")


if __name__ == "__main__":
    main()
