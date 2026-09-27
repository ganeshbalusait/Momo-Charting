"""Old code vs new code on IDENTICAL frozen tapes, all 357 symbols, every field.

The gate for any change inside the scan/column maths: the board must come out
bit-for-bit the same, because these values are the thinkorswim parity.

Usage:
    python artifacts/_wf_full_parity.py capture   # once, from the live feed
    python artifacts/_wf_full_parity.py run OLD   # with the baseline checked out
    python artifacts/_wf_full_parity.py run NEW   # with the change applied
    python artifacts/_wf_full_parity.py compare
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momx import board, feed  # noqa: E402

HERE = Path(__file__).resolve().parent
FRAMES = HERE / "_wf_full_frames.pkl"


def flatten(value, prefix=""):
    out = {}
    if isinstance(value, dict):
        for key, item in value.items():
            out.update(flatten(item, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            out.update(flatten(item, f"{prefix}[{index}]"))
    else:
        out[prefix] = repr(value)
    return out


def capture():
    syms = board.load_universe("Watchlist")
    print("fetching tapes for %d symbols..." % len(syms), flush=True)
    # feed.fetch_* return a FeedResult object, not a (bars, errors) tuple.
    five = feed.fetch_5m(syms).bars
    thirty = feed.fetch_30m(syms).bars
    daily = feed.fetch_daily(syms).bars
    work = [
        (s, five.get(s), thirty.get(s), daily.get(s), board.TWO_HOUR_ANCHOR, None)
        for s in syms
    ]
    with FRAMES.open("wb") as handle:
        pickle.dump(work, handle)
    print("captured %d frames -> %s" % (len(work), FRAMES.name))


def run(label):
    with FRAMES.open("rb") as handle:
        work = pickle.load(handle)
    out = {}
    t0 = time.process_time()
    for item in work:
        # _scan_one gained a 7th return value (`last`) on 2026-09-03 when
        # display columns started being reused between builds - a reused row
        # keeps its studies but must take this cycle's price. Unpack loosely so
        # this harness survives the next field too.
        symbol, row, pct, passed, reasons, failure, *_extra = board._scan_one(item)
        out[symbol] = {
            "row": row,
            "pct": pct,
            "passed": passed,
            "reasons": list(reasons or ()),
            "failure": failure,
        }
    cpu = time.process_time() - t0
    path = HERE / ("_wf_parity_%s.json" % label)
    path.write_text(json.dumps(out, default=repr, sort_keys=True), encoding="utf-8")
    print("%s: %d symbols, %.1fs cpu -> %s" % (label, len(out), cpu, path.name))


def compare():
    old = json.loads((HERE / "_wf_parity_OLD.json").read_text(encoding="utf-8"))
    new = json.loads((HERE / "_wf_parity_NEW.json").read_text(encoding="utf-8"))
    print("symbols: old %d  new %d  same set: %s" % (len(old), len(new), set(old) == set(new)))
    diffs = []
    for symbol in sorted(set(old) & set(new)):
        a = flatten(old[symbol])
        b = flatten(new[symbol])
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                diffs.append((symbol, key, a.get(key), b.get(key)))
    print("field diffs: %d" % len(diffs))
    for symbol, key, x, y in diffs[:25]:
        print("   %-6s %-34s old=%s new=%s" % (symbol, key, x, y))
    print("\nVERDICT: %s" % ("IDENTICAL - safe to ship" if not diffs else "DIFFERENT - DO NOT SHIP"))
    return 1 if diffs else 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if action == "capture":
        capture()
    elif action == "run":
        run(sys.argv[2])
    else:
        sys.exit(compare())
