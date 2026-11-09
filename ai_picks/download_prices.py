"""Download daily bars for every US exchange-listed stock Alpaca knows, alive or dead.

Research-only, run off-hours: ``python -m ai_picks.download_prices``.
Writes artifacts/ai_picks_data/assets.json and bars_all.parquet / bars_raw.parquet
(adjustment=all is used for returns, raw for valuation at the filing date).
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import pandas as pd
import requests

DATA = Path(__file__).resolve().parent.parent / "artifacts" / "ai_picks_data"
EXCHANGES = {"NASDAQ", "NYSE", "AMEX", "ARCA", "BATS"}
BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"
START = "2016-01-01"


def _headers():
    from data.alpaca_client import AlpacaClient

    cred = AlpacaClient().credentials
    base = "https://paper-api.alpaca.markets" if cred.paper else "https://api.alpaca.markets"
    return base, {"APCA-API-KEY-ID": cred.key, "APCA-API-SECRET-KEY": cred.secret}


def fetch_assets(base: str, headers: dict) -> list[dict]:
    rows = []
    for status in ("active", "inactive"):
        resp = requests.get(f"{base}/v2/assets", params={"status": status, "asset_class": "us_equity"},
                            headers=headers, timeout=120)
        resp.raise_for_status()
        for a in resp.json():
            if a.get("exchange") in EXCHANGES:
                rows.append({"symbol": a["symbol"], "name": a.get("name") or "", "exchange": a["exchange"],
                             "status": status})
    return rows


def _get(params: dict, headers: dict) -> dict:
    for attempt in range(8):
        try:
            resp = requests.get(BARS_URL, params=params, headers=headers, timeout=120)
        except requests.RequestException:
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 429:
            time.sleep(15)
            continue
        if resp.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        if resp.status_code == 400:
            print(f"skip chunk (400): {resp.text[:120]}", flush=True)
            return {}
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Alpaca bars request kept failing")


def fetch_bars(symbols: list[str], adjustment: str, end: str, headers: dict) -> pd.DataFrame:
    frames = []
    for i in range(0, len(symbols), 100):
        chunk = symbols[i:i + 100]
        token = None
        rows = []
        while True:
            params = {"symbols": ",".join(chunk), "timeframe": "1Day", "start": START, "end": end,
                      "adjustment": adjustment, "feed": "sip", "limit": 10000}
            if token:
                params["page_token"] = token
            j = _get(params, headers)
            for sym, bars in (j.get("bars") or {}).items():
                for b in bars:
                    rows.append((sym, b["t"][:10], b["c"], b["v"]))
            token = j.get("next_page_token")
            if not token:
                break
        if rows:
            frames.append(pd.DataFrame(rows, columns=["symbol", "date", "close", "volume"]))
        print(f"{adjustment} {min(i + 100, len(symbols))}/{len(symbols)}", flush=True)
    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"])
    return out.drop_duplicates(["symbol", "date"])


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    base, headers = _headers()
    assets = fetch_assets(base, headers)
    (DATA / "assets.json").write_text(json.dumps(assets), encoding="utf-8")
    # real tickers only (Alpaca also lists CUSIP-coded rights/escrow shells)
    symbols = sorted({a["symbol"] for a in assets if re.fullmatch(r"[A-Z]{1,5}(\.[A-Z])?", a["symbol"])})
    print(f"{len(symbols)} symbols", flush=True)
    end = (pd.Timestamp.now(tz="America/New_York").normalize() - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    for adjustment in sys.argv[1:] or ["all", "raw"]:
        fetch_bars(symbols, adjustment, end, headers).to_parquet(DATA / f"bars_{adjustment}.parquet", index=False)


if __name__ == "__main__":
    main()
