"""Prove momx.indicators.adx_lines == the chart's ADX study, on real bars.

The scanner now records ADX / +DI / -DI (momx/columns.py adx_cell), and the
whole point of recording it is that it is the SAME number the trader sees on
the chart. That claim is only worth something if it is measured, so this
script measures it:

    1. Fetch one symbol's bars from the live backend's chart endpoint - the
       same payload the chart itself renders.
    2. Run the Python port over them.
    3. Dump those bars to JSON so a node script holding a COPY of the two JS
       functions (App.jsx calculateMtfAdxAverage / calculateMtfAdxLines) can
       be run over the identical input and diffed.

Usage (from the repo root)::

    python scripts/validate_adx_against_chart.py INOD 5m
    python scripts/validate_adx_against_chart.py INOD 5m --dump bars.json

Prints the last five +DI / -DI / ADX values. With ``--compare <file.json>``
(the output of the node script: {"plus": [...], "minus": [...], "adx": [...]})
it also prints the maximum absolute difference per line and exits non-zero if
any of them exceeds the tolerance.

Read-only: one GET. It never writes to the backend and never loops.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momx.indicators import ADX_LENGTH, adx_lines  # noqa: E402

DEFAULT_BASE = "http://127.0.0.1:3001"
TOLERANCE = 1e-6


def fetch_bars(symbol: str, timeframe: str, base: str) -> list[dict]:
    url = f"{base}/api/oi-finder-chart?symbol={symbol}&timeframe={timeframe}"
    with urllib.request.urlopen(url, timeout=120) as response:
        payload = json.loads(response.read())
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars:
        raise SystemExit(f"no bars for {symbol} {timeframe}")
    return [bar for bar in bars if isinstance(bar, dict)]


def show(name: str, values: list, count: int = 5) -> None:
    tail = values[-count:]
    print(f"  {name:<5} " + " ".join(
        "None" if value is None else f"{value:12.6f}" for value in tail
    ))


def max_difference(left: list, right: list) -> tuple[float, int]:
    """Largest |a - b| over the two aligned lines, and its index.

    A None on ONE side only is an infinite difference: that is a real
    disagreement about whether the study has a reading at all, and it must not
    be averaged away or skipped.
    """
    if len(left) != len(right):
        raise SystemExit(f"length mismatch: python {len(left)} vs js {len(right)}")
    worst = 0.0
    where = -1
    for index, (a, b) in enumerate(zip(left, right)):
        if a is None and b is None:
            continue
        if a is None or b is None:
            return (math.inf, index)
        delta = abs(float(a) - float(b))
        if delta > worst:
            worst = delta
            where = index
    return (worst, where)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", nargs="?", default="INOD")
    parser.add_argument("timeframe", nargs="?", default="5m")
    parser.add_argument("--length", type=int, default=ADX_LENGTH)
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--dump", help="write the fetched bars here as JSON")
    parser.add_argument(
        "--bars",
        help="read bars from this JSON file instead of fetching. REQUIRED for a "
             "meaningful --compare: the live tape grows between two fetches, so "
             "re-fetching would diff two different bar sets.",
    )
    parser.add_argument("--compare", help="a JSON file of the JS result to diff against")
    args = parser.parse_args()

    bars = (
        json.loads(Path(args.bars).read_text(encoding="utf-8"))
        if args.bars
        else fetch_bars(args.symbol.upper(), args.timeframe, args.base)
    )
    print(f"{args.symbol.upper()} {args.timeframe}: {len(bars)} bars, ADX length {args.length}")

    if args.dump:
        Path(args.dump).write_text(json.dumps(bars), encoding="utf-8")
        print(f"bars written to {args.dump}")

    lines = adx_lines(
        [bar.get("high") for bar in bars],
        [bar.get("low") for bar in bars],
        [bar.get("close") for bar in bars],
        args.length,
    )
    print("last 5 (python):")
    show("+DI", lines["plus"])
    show("-DI", lines["minus"])
    show("ADX", lines["adx"])

    if not args.compare:
        return 0

    other = json.loads(Path(args.compare).read_text(encoding="utf-8"))
    print("last 5 (javascript):")
    show("+DI", other["plus"])
    show("-DI", other["minus"])
    show("ADX", other["adx"])

    worst = 0.0
    for name in ("plus", "minus", "adx"):
        delta, where = max_difference(lines[name], other[name])
        worst = max(worst, delta)
        print(f"max |python - js| {name:<5} = {delta:.3e} (bar {where})")
    print(f"MAX ABSOLUTE DIFFERENCE = {worst:.3e}  (tolerance {TOLERANCE:.0e})")
    return 0 if worst < TOLERANCE else 1


if __name__ == "__main__":
    raise SystemExit(main())
