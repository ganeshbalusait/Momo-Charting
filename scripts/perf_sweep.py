"""End-to-end latency sweep: charts + indicators + option chain, whole universe.

Usage (from the repo root):
    .venv/Scripts/python.exe scripts/perf_sweep.py [--limit N] [--budget-ms 3000]

For every symbol in QUICK STRIP + watchlist it times:
  chart  GET /api/oi-finder-chart?symbol=S      (bars + studyBars + mtfSignals)
  chain  GET /api/oi-finder?symbol=S
It records whether the chart answered with real bars or a warming stub, and
whether the indicator tapes were present. A second pass measures warm latency.

Exit code 0 only when every symbol's WARM chart and chain are inside the
budget and the chart carries bars + indicators - that is the loop's exit bar.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "http://127.0.0.1:3001"
QUICK = ["SPY", "QQQ", "SLV", "AAPL", "AMZN", "GOOGL", "META",
         "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO", "PLTR"]


def universe(limit: int | None) -> list[str]:
    raw = Path("watchlist.txt").read_text(encoding="utf-8")
    syms = [t.strip().upper() for t in re.split(r"[,\s]+", raw)
            if re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", t.strip().upper())]
    ordered = list(dict.fromkeys(QUICK + syms))
    return ordered[:limit] if limit else ordered


def hit(path: str, timeout: float = 320.0) -> tuple[float, int, dict]:
    t0 = time.perf_counter()
    req = urllib.request.Request(BASE + path, headers={"Accept-Encoding": "identity"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            code = r.status
    except Exception as exc:  # noqa: BLE001
        return time.perf_counter() - t0, 0, {"error": str(exc)[:80]}
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        data = {}
    return time.perf_counter() - t0, code, data


def probe(sym: str) -> dict:
    ct, cc, cd = hit(f"/api/oi-finder-chart?symbol={sym}")
    ot, oc, od = hit(f"/api/oi-finder?symbol={sym}")
    bars = len(cd.get("bars") or [])
    return {
        "sym": sym,
        "chart_s": round(ct, 2), "chart_http": cc,
        "bars": bars,
        "warming": bool(cd.get("warming")),
        "indicators": bool(cd.get("mtfSignals")) or bool(cd.get("ganeshHigherTimeframeSignals")),
        "studyBars": len(cd.get("studyBars") or []),
        "chain_s": round(ot, 2), "chain_http": oc,
        "chain_live": bool(od.get("live")),
        "chain_rows": len(od.get("selectedExpiryChainRows") or []),
        "chain_src": str(od.get("source") or "")[:14],
    }


def summarize(label: str, rows: list[dict], budget_ms: int) -> bool:
    chart = [r["chart_s"] for r in rows]
    chain = [r["chain_s"] for r in rows]
    def pct(a, q):
        a = sorted(a); return a[min(len(a) - 1, int(len(a) * q))]
    print(f"\n=== {label}: {len(rows)} symbols ===")
    print(f"  chart  med {statistics.median(chart):.2f}s  p95 {pct(chart,.95):.2f}s  max {max(chart):.2f}s")
    print(f"  chain  med {statistics.median(chain):.2f}s  p95 {pct(chain,.95):.2f}s  max {max(chain):.2f}s")
    warming = [r["sym"] for r in rows if r["warming"]]
    nobars = [r["sym"] for r in rows if not r["warming"] and r["bars"] == 0]
    noind = [r["sym"] for r in rows if r["bars"] and not r["indicators"]]
    nochain = [r["sym"] for r in rows if not r["chain_live"] or r["chain_rows"] == 0]
    slow_chart = [r["sym"] for r in rows if r["chart_s"] * 1000 > budget_ms]
    slow_chain = [r["sym"] for r in rows if r["chain_s"] * 1000 > budget_ms]
    errs = [r["sym"] for r in rows if r["chart_http"] != 200 or r["chain_http"] != 200]
    print(f"  warming stubs      {len(warming):3d}  {warming[:8]}")
    print(f"  no bars (not warm) {len(nobars):3d}  {nobars[:8]}")
    print(f"  bars but no indic. {len(noind):3d}  {noind[:8]}")
    print(f"  chain not live/0   {len(nochain):3d}  {nochain[:8]}")
    print(f"  chart > {budget_ms}ms   {len(slow_chart):3d}  {slow_chart[:8]}")
    print(f"  chain > {budget_ms}ms   {len(slow_chain):3d}  {slow_chain[:8]}")
    print(f"  http errors        {len(errs):3d}  {errs[:8]}")
    ok = not (nobars or noind or nochain or slow_chart or slow_chain or errs)
    print(f"  PASS" if ok else f"  FAIL")
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--budget-ms", type=int, default=3000)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--skip-cold", action="store_true")
    ap.add_argument(
        "--during-full-build",
        metavar="SYMBOL",
        default="",
        help=(
            "Force a FULL rebuild of SYMBOL (refresh=true), then run the warm "
            "pass while it churns. This measures the process-split exit "
            "criterion: warm chart p95 < 1.0s while a full build runs "
            "(spec 2026-08-19-process-split-design.md). On the single-process "
            "server this is the case that fails; after A1 it must not."
        ),
    )
    args = ap.parse_args()
    syms = universe(args.limit)
    print(f"universe: {len(syms)} symbols, budget {args.budget_ms}ms, workers {args.workers}")

    if not args.skip_cold:
        t0 = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            cold = list(ex.map(probe, syms))
        print(f"cold pass took {time.perf_counter()-t0:.0f}s")
        summarize("COLD (first touch)", cold, args.budget_ms)
        Path("artifacts/perf_sweep_cold.json").write_text(json.dumps(cold, indent=1))

    if args.during_full_build:
        sacrifice = args.during_full_build.strip().upper()
        print(f"forcing full rebuild of {sacrifice}; sweeping while it runs")
        hit(f"/api/oi-finder-chart?symbol={sacrifice}&refresh=true", timeout=30.0)
        time.sleep(3.0)  # let the build enter the single lane before measuring

    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        warm = list(ex.map(probe, syms))
    print(f"warm pass took {time.perf_counter()-t0:.0f}s")
    ok = summarize("WARM (second touch)", warm, args.budget_ms)
    Path("artifacts/perf_sweep_warm.json").write_text(json.dumps(warm, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
