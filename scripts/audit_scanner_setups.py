"""Read-only Setup/H-L audit of actual boards and saved scanner observations.

Run with .venv/Scripts/python.exe scripts/audit_scanner_setups.py.
--regular requires a regular-session clock; it never simulates market hours.
No trades, settings, boards, history, or grade records are written.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from momx.grade import grade_row, RULE_VERSION  # noqa: E402

ET = ZoneInfo("America/New_York")


def audit_rows(board, at, direction="bull"):
    rows = [*board.get("rows", []), *board.get("rest", [])]
    seen, duplicates, differences, hl_invalid, samples = set(), [], [], [], []
    for row in rows:
        symbol = row.get("symbol")
        if symbol in seen:
            duplicates.append(symbol)
        seen.add(symbol)
        expected = grade_row(row, at, direction)
        actual = row.get("grade") or {}
        if expected and (actual.get("letter") != expected["letter"]
                         or (actual.get("checks") or {}).get("aboveMid") != expected["checks"]["aboveMid"]):
            differences.append({"symbol": symbol, "actual": actual.get("letter"),
                                "expected": expected["letter"], "highLow": row.get("highLow")})
        cell = row.get("highLow") or {}
        value = cell.get("value")
        if isinstance(value, (int, float)) and (not math.isfinite(value) or not -1.01 <= value <= 1.01):
            hl_invalid.append({"symbol": symbol, "highLow": cell})
        if symbol in {"SPY", "AAPL", "TSLA", "NVDA", "MSFT"}:
            samples.append({"symbol": symbol, "last": row.get("last"), "highLow": cell,
                            "marketDataAt": row.get("marketDataAt"), "grade": actual.get("letter"),
                            "ruleVersion": actual.get("ruleVersion"), "scanPass": row.get("scanPass"),
                            "scanReasons": row.get("scanReasons"), "m5": row.get("m5"),
                            "adx": row.get("adx")})
    return {"rows": len(rows), "duplicates": duplicates, "gradeDifferences": differences,
            "invalidHighLow": hl_invalid, "samples": samples}


def history_audit():
    result = {}
    for direction, base in [("bull", ROOT / "artifacts/momx_history"),
                            ("bear", ROOT / "artifacts/momx_history/bear")]:
        counts, examples = Counter(), []
        for board in ("Watchlist", "Mag7"):
            for path in sorted((base / board).glob("2026-10-*.json"))[-3:]:
                try:
                    records = json.loads(path.read_text(encoding="utf-8")).get("rows", {})
                except (OSError, ValueError):
                    continue
                counts["files"] += 1
                for symbol, record in records.items():
                    for snapshot in record.get("snapshots", []):
                        row = snapshot.get("row") or {}
                        stamp = snapshot.get("at")
                        if not stamp:
                            continue
                        at = datetime.fromisoformat(stamp)
                        fixed = grade_row(row, at, direction)
                        hl = row.get("highLow") or {}
                        old_row = {**row, "highLow": {**hl, "bg": "old-rejected-dark-shade"}}
                        old = grade_row(old_row, at, direction) if hl.get("bg") in {"dark_green", "dark_red"} else fixed
                        counts["snapshots"] += 1
                        if fixed["letter"] != old["letter"]:
                            counts["correctedAPlus"] += 1
                            if len(examples) < 6:
                                examples.append({"board": board, "symbol": symbol, "at": stamp,
                                                 "hl": hl, "old": old["letter"], "fixed": fixed["letter"]})
        result[direction] = {**counts, "examples": examples}
    return result


def get_json(url):
    start = time.perf_counter()
    with urllib.request.urlopen(url, timeout=45) as response:
        data = json.load(response)
    return data, round((time.perf_counter() - start) * 1000)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regular", action="store_true")
    parser.add_argument("--samples", type=int, default=1)
    parser.add_argument("--interval", type=int, default=15)
    parser.add_argument("--output", default="artifacts/scanner-setup-audit.json")
    args = parser.parse_args()
    now = datetime.now(ET)
    regular = now.weekday() < 5 and (9, 30) <= (now.hour, now.minute) < (16, 0)
    if args.regular and not regular:
        parser.error("Regular market hours have not been observed; no live verification claimed")
    report = {"capturedAt": now.isoformat(), "regularSession": regular,
              "ruleVersion": RULE_VERSION, "archive": history_audit(), "snapshots": []}
    failures = 0
    for index in range(max(1, min(args.samples, 10))):
        capture = {"at": datetime.now(ET).isoformat(), "boards": []}
        for name in ("Watchlist", "Mag7"):
            for direction in ("bull", "bear"):
                try:
                    board, ms = get_json(f"http://127.0.0.1:3010/api/momx-scanner?list={name}&dir={direction}")
                    if board.get("direction", "bull") != direction:
                        raise ValueError("Server returned the wrong scanner direction")
                    at = datetime.fromisoformat(board.get("generatedAt") or capture["at"])
                    audit = audit_rows(board, at, direction)
                    capture["boards"].append({"list": name, "direction": direction, "apiMs": ms,
                                               "generatedAt": board.get("generatedAt"), "freshness": board.get("freshness"), **audit})
                    failures += len(audit["gradeDifferences"]) + len(audit["duplicates"]) + len(audit["invalidHighLow"])
                except Exception as exc:
                    capture["boards"].append({"list": name, "direction": direction, "error": str(exc)})
                    failures += 1
        try:
            capture["quotes"], capture["quotesApiMs"] = get_json("http://127.0.0.1:3002/api/momx-live-quotes?symbols=SPY,AAPL,TSLA,NVDA,MSFT")
        except Exception as exc:
            capture["quoteError"] = str(exc)
            failures += 1
        report["snapshots"].append(capture)
        if index + 1 < args.samples:
            time.sleep(max(1, min(args.interval, 30)))
    output = ROOT / args.output
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "regularSession": regular,
                      "archive": report["archive"], "failures": failures,
                      "boards": [{**{k: b.get(k) for k in ("list", "direction", "rows", "apiMs", "error")},
                                  "gradeDifferences": len(b.get("gradeDifferences", []))}
                                 for b in report["snapshots"][-1]["boards"]]}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
