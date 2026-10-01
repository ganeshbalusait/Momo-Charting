"""Prefix-reuse parity gate (2026-09-25): warm vs cold, all frozen symbols, every field.

    set MOMX_PREFIX_REUSE=0 && python artifacts/_wf_prefix_parity.py run COLD
    python artifacts/_wf_prefix_parity.py run WARM     # reuse on, previous build first
    python artifacts/_wf_prefix_parity.py compare

WARM runs each symbol twice in one process: first a "previous build" of its tapes
(the last two 5m and 30m bars dropped, the new last bar's close nudged, the last
daily bar's close nudged), then the real tapes - exactly the situation a live
build meets. Must print 0 diffs against COLD (reuse off, one clean run).
"""
from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momx import board  # noqa: E402

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


def previous(frame, drop):
    if frame is None or getattr(frame, "empty", True) or len(frame) <= drop + 1:
        return frame
    prev = frame.iloc[: len(frame) - drop].copy()
    col = "close"
    if col in prev.columns:
        prev.iloc[-1, prev.columns.get_loc(col)] = float(prev[col].iloc[-1]) * 0.997
    return prev


def run(label):
    work = pickle.load(FRAMES.open("rb"))
    out = {}
    t0 = time.process_time()
    for item in work:
        symbol, five, thirty, daily, anchor, industry = item[:6]
        if label == "WARM":
            board._scan_one((symbol, previous(five, 2), previous(thirty, 2), previous(daily, 0), anchor, industry))
        symbol, row, pct, passed, reasons, failure, *_ = board._scan_one(item)
        out[symbol] = {"row": row, "pct": pct, "passed": passed, "reasons": list(reasons or ()), "failure": failure}
    cpu = time.process_time() - t0
    (HERE / f"_wf_prefix_{label}.json").write_text(json.dumps(out, default=repr, sort_keys=True), encoding="utf-8")
    print(f"{label}: {len(out)} symbols, {cpu:.1f}s cpu")


def compare():
    cold = json.loads((HERE / "_wf_prefix_COLD.json").read_text(encoding="utf-8"))
    warm = json.loads((HERE / "_wf_prefix_WARM.json").read_text(encoding="utf-8"))
    diffs = []
    for symbol in sorted(set(cold) | set(warm)):
        a, b = flatten(cold.get(symbol)), flatten(warm.get(symbol))
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                diffs.append((symbol, key, a.get(key), b.get(key)))
    print(f"symbols cold {len(cold)} warm {len(warm)}; field diffs: {len(diffs)}")
    for d in diffs[:20]:
        print("  ", d)
    print("VERDICT:", "IDENTICAL - safe to ship" if not diffs else "DIFFERENT - DO NOT SHIP")
    return 1 if diffs else 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "compare"
    if action == "run":
        run(sys.argv[2])
    else:
        sys.exit(compare())
