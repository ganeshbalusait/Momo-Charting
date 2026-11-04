"""Monthly Picks: back-test the rule, then print this month's 20 picks.

    .venv/Scripts/python.exe scripts/monthly_picks.py            # 10y back-test + today's picks
    .venv/Scripts/python.exe scripts/monthly_picks.py --years 15 --top 20 --keep 40
    .venv/Scripts/python.exe scripts/monthly_picks.py --picks-only   # skip the back-test
    .venv/Scripts/python.exe scripts/monthly_picks.py --refresh      # re-download bars

The rule lives in momx/monthly_picks.py (see its docstring). Bars come from the
app's own data provider (MARKET_DATA_PROVIDER in .env): Schwab daily bars are
split-adjusted; for Alpaca we ask for split+dividend adjusted bars, because the
app's AlpacaClient asks for RAW bars (right for intraday, wrong across years -
an un-adjusted 4:1 split reads as a -75% month).

Writes artifacts/monthly_picks/:
    backtest.json        every month, every add/drop with its reason
    current_picks.json   this month's list (next run marks keep/new/dropped)
    report.md            the human summary

Honest limits, printed in the report too:
    * the universe is TODAY's S&P 500 list. Companies that were removed or went
      bust are missing (survivorship bias), which flatters everything - that is
      why the picks are compared with an equal-weight basket of the same
      universe, not only with SPY.
    * price-only: no valuation / earnings / analyst data (no point-in-time
      history for those).
    * month-end close to month-end close; costs 10 bps per side by default;
      no taxes, no slippage beyond that.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from momx import monthly_picks as mp  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT = ROOT / "artifacts" / "monthly_picks"
UNIVERSE = ROOT / "data" / "universe_sp500.csv"
BENCHMARK = "SPY"


def load_universe(path: Path) -> tuple[list[str], dict[str, str], dict[str, pd.Timestamp]]:
    symbols, sectors, added = [], {}, {}
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            sym = row["symbol"].strip().upper()
            if not sym:
                continue
            symbols.append(sym)
            sectors[sym] = row.get("sector", "").strip() or "Unknown"
            if row.get("added"):
                added[sym] = pd.Timestamp(row["added"])
    return symbols, sectors, added


def _fetch_schwab(symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    from data.schwab_client import SchwabClient

    client = SchwabClient()
    if not client.configured:
        raise SystemExit("Schwab is not configured - connect it in the app or set MARKET_DATA_PROVIDER=alpaca")
    out: dict[str, pd.DataFrame] = {}
    for i, sym in enumerate(symbols, 1):
        frame = None
        # Class shares (BRK.B): try the dot spelling, then the TOS slash one.
        for spelling in dict.fromkeys([sym, sym.replace(".", "/")]):
            frame = next(iter(client.get_stock_bars([spelling], "1Day", start, end).values()), None)
            if frame is not None and not frame.empty:
                break
        if frame is not None and not frame.empty:
            out[sym] = frame
        if i % 50 == 0:
            print(f"  bars {i}/{len(symbols)}", flush=True)
    return out


def _fetch_alpaca(symbols: list[str], start: datetime, end: datetime) -> dict[str, pd.DataFrame]:
    from alpaca.data.enums import Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame

    from config import settings
    from data.alpaca_client import AlpacaClient

    data_client = AlpacaClient()._data_client
    out: dict[str, pd.DataFrame] = {}
    batch = 50
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        request = StockBarsRequest(
            symbol_or_symbols=chunk,
            timeframe=TimeFrame.Day,
            start=start,
            end=end,
            adjustment=Adjustment.ALL,
            feed=settings.alpaca_data_feed,
        )
        for attempt in range(3):
            try:
                df = data_client.get_stock_bars(request).df
                break
            except Exception as exc:  # network blip / rate limit
                if attempt == 2:
                    print(f"  batch {i}: {exc}", flush=True)
                    df = pd.DataFrame()
                time.sleep(2 ** attempt)
        if not df.empty and isinstance(df.index, pd.MultiIndex):
            for sym in df.index.get_level_values(0).unique():
                out[sym] = df.xs(sym).reset_index()
        print(f"  bars {min(i + batch, len(symbols))}/{len(symbols)}", flush=True)
    return out


def load_bars(symbols: list[str], years: int, refresh: bool) -> dict[str, pd.DataFrame]:
    cache = OUT / "daily_bars.pkl"
    today = datetime.now(ET).date().isoformat()
    if cache.exists() and not refresh:
        blob = pd.read_pickle(cache)
        if blob.get("date") == today and blob.get("years", 0) >= years and set(symbols) <= set(blob["bars"]) | set(blob.get("missing", [])):
            print(f"Using cached bars from {cache}")
            return blob["bars"]
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:
        pass
    from config import settings

    end = datetime.now(ET)
    # +1 year so the first back-test month already has 12 months of momentum history
    start = end - timedelta(days=int(365.25 * (years + 1)) + 10)
    provider = settings.market_data_provider
    print(f"Downloading {len(symbols)} daily histories from {provider} ({start.date()} -> {end.date()})...")
    bars = _fetch_schwab(symbols, start, end) if provider == "schwab" else _fetch_alpaca(symbols, start, end)
    missing = sorted(set(symbols) - set(bars))
    OUT.mkdir(parents=True, exist_ok=True)
    pd.to_pickle({"date": today, "years": years, "bars": bars, "missing": missing}, cache)
    if missing:
        print(f"  no bars for {len(missing)}: {', '.join(missing[:20])}{' ...' if len(missing) > 20 else ''}")
    return bars


def _clean(obj):
    """json.dumps cannot write NaN; the report must stay valid JSON."""
    if isinstance(obj, float):
        return None if math.isnan(obj) or math.isinf(obj) else obj
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def _p(v) -> str:
    return "n/a" if v is None or (isinstance(v, float) and math.isnan(v)) else f"{v * 100:+.1f}%"


def write_report(bt: dict | None, picks: dict, missing: int) -> str:
    lines = ["# Monthly Picks", ""]
    if bt and bt.get("stats"):
        s = bt["stats"]
        lines += [
            f"## Back-test {bt['start']} to {bt['end']} ({s['picks']['months']} months)",
            "",
            "| | Picks (top 20) | Whole universe, equal weight | SPY |",
            "|---|---|---|---|",
        ]
        for key, label in [("totalReturn", "Total return"), ("cagr", "Per year (CAGR)"),
                           ("volatility", "Volatility"), ("maxDrawdown", "Worst drawdown"),
                           ("worstMonth", "Worst month"), ("bestMonth", "Best month")]:
            lines.append(f"| {label} | {_p(s['picks'].get(key))} | {_p(s['universe'].get(key))} | {_p(s['benchmark'].get(key))} |")
        lines.append(f"| Sharpe | {s['picks'].get('sharpe')} | {s['universe'].get('sharpe')} | {s['benchmark'].get('sharpe')} |")
        lines += [
            "",
            f"* Beat SPY in **{bt['pctMonthsBeatBenchmark'] * 100:.0f}%** of months; "
            f"{bt['avgSwapsPerMonth']} of 20 names swapped per month on average; "
            f"{bt['pickHitRate'] * 100:.0f}% of position-months were up.",
            "",
            "### By year",
            "",
            "| Year | Picks | Universe | SPY |",
            "|---|---|---|---|",
        ]
        for year, row in bt["yearly"].items():
            lines.append(f"| {year} | {_p(row.get('picks'))} | {_p(row.get('universe'))} | {_p(row.get('benchmark'))} |")
        lines.append("")
    lines += [f"## Picks as of {picks['asOf']} ({picks['eligible']} eligible stocks)", "",
              "| # | Symbol | Sector | Price | 12-1 mom | Off 52w high | Status | Why |", "|---|---|---|---|---|---|---|---|"]
    for p in picks["picks"]:
        lines.append(f"| {p['rank']} | {p['symbol']} | {p['sector']} | {p['price']:.2f} | {_p(p['mom12_1'])} | "
                     f"{p['pctBelowHigh'] * 100:.1f}% | {p['status']} | {p['reason']} |")
    if picks["dropped"]:
        lines += ["", "### Dropped since last run", ""]
        lines += [f"* **{d['symbol']}**: {d['reason']}" for d in picks["dropped"]]
    lines += [
        "",
        "## Limits",
        "",
        "* Universe is today's S&P 500 list, so stocks that were removed or went bust are missing (survivorship bias). "
        "Compare the picks with the **universe** column, not only with SPY.",
        "* Price-based only: no valuation, earnings or analyst data yet.",
        "* Month-end close to month-end close, 10 bps per side costs, no taxes. Not a promise of future returns.",
    ]
    if missing:
        lines.append(f"* {missing} symbols had no bars from the data provider and were skipped.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--years", type=int, default=10)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--keep", type=int, default=40)
    ap.add_argument("--cost-bps", type=float, default=10.0)
    ap.add_argument("--universe", type=Path, default=UNIVERSE)
    ap.add_argument("--picks-only", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)

    symbols, sectors, added = load_universe(args.universe)
    bars = load_bars(sorted(set(symbols) | {BENCHMARK}), args.years, args.refresh)
    if BENCHMARK not in bars:
        print(f"WARNING: no {BENCHMARK} bars - benchmark column will be empty")
    config = mp.PickConfig(top_n=args.top, keep_rank=args.keep, cost_bps=args.cost_bps)
    panels = mp.build_panels(bars)

    OUT.mkdir(parents=True, exist_ok=True)
    bt = None
    if not args.picks_only:
        print("Back-testing...")
        bt = mp.backtest(panels, sectors, added, BENCHMARK, config)
        (OUT / "backtest.json").write_text(json.dumps(_clean(bt), indent=1), encoding="utf-8")

    prev_path = OUT / "current_picks.json"
    previous = []
    if prev_path.exists():
        try:
            previous = [p["symbol"] for p in json.loads(prev_path.read_text(encoding="utf-8")).get("picks", [])]
        except (ValueError, KeyError):
            previous = []
    picks = mp.current_picks(panels, sectors, added, previous, BENCHMARK, config)
    prev_path.write_text(json.dumps(_clean(picks), indent=1), encoding="utf-8")

    missing = len(set(symbols) - set(bars))
    report = write_report(bt, picks, missing)
    (OUT / "report.md").write_text(report, encoding="utf-8")
    print(report)
    print(f"Saved {OUT / 'report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
