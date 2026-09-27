"""Is the row-reuse optimisation invisible? Frozen tapes, reuse ON vs OFF.

Prompted by agenticai-trading-7-1a: my scan changes are intentional behaviour
changes and a parity run says nothing about them, but "display columns reused
between builds" is supposed to be INVISIBLE - same inputs, same output, just
fewer of them - and that is exactly the class where a stale-reuse bug shows up
as a handful of symbols carrying an old cell rather than as a crash. They also
flagged the specific risk: my reuse is keyed on the SYMBOL, not on the bars.

THE PROPERTY UNDER TEST, and it is stronger than "close enough":

    On IDENTICAL input tapes, a build that reuses rows must equal a build that
    computes them. Reusing a row derived from the same bars can only give the
    same values - so any difference at all is a bug, not a rounding artefact.

That is the whole point of freezing the tapes: it removes the one legitimate
reason a reused cell could differ (the tape moved on), leaving only defects.

METHOD
    1. build_board on frozen frames, no reuse            -> FULL (the truth)
    2. build_board on the SAME frames, reuse enabled,
       seeded from FULL exactly as service._reuse_kwargs
       would seed it                                     -> REUSED
    3. flatten both payloads and diff every field of every row

Not the same as artifacts/_wf_full_parity.py, which compares OLD code against
NEW code through board._scan_one. This compares one code path against another
inside the SAME build, which is what the reuse change actually is. (That
harness also needs its unpack widened - _scan_one returns `last` now as well.)
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
FRAMES = HERE / "_reuse_frames.pkl"


class FrozenFeed:
    """Hands back the captured frames, so both builds see identical bars."""

    def __init__(self, five, thirty, daily):
        self.five, self.thirty, self.daily = five, thirty, daily

    class _Result:
        def __init__(self, bars):
            self.bars = bars
            self.errors = {}

    def fetch_5m(self, symbols, **kwargs):
        return self._Result({s: self.five[s] for s in symbols if s in self.five})

    def fetch_30m(self, symbols, **kwargs):
        return self._Result({s: self.thirty[s] for s in symbols if s in self.thirty})

    def fetch_daily(self, symbols, **kwargs):
        return self._Result({s: self.daily[s] for s in symbols if s in self.daily})


def capture():
    syms = [str(s).upper() for s in board.load_universe("Watchlist")]
    print("fetching %d symbols..." % len(syms), flush=True)
    five = feed.fetch_5m(syms).bars
    thirty = feed.fetch_30m(syms).bars
    daily = feed.fetch_daily(syms).bars
    with FRAMES.open("wb") as handle:
        pickle.dump((syms, five, thirty, daily), handle)
    print("captured %d symbols -> %s" % (len(syms), FRAMES.name))


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


def rows_of(payload):
    out = {}
    for bucket in ("rows", "rest"):
        for row in payload.get(bucket) or []:
            if row.get("symbol"):
                out[row["symbol"]] = row
    return out


def run():
    syms, five, thirty, daily = pickle.loads(FRAMES.read_bytes())
    stub = FrozenFeed(five, thirty, daily)

    t0 = time.process_time()
    full = board.build_board(syms, feed_module=stub, limit=None)
    cpu_full = time.process_time() - t0
    first = rows_of(full)

    # Seed the reuse exactly as service._reuse_kwargs does.
    cache = dict(first)
    matched = {s for s, r in first.items() if r.get("scanPass")}
    print("full build: %d rows, %d matching, %.1fs cpu" % (len(first), len(matched), cpu_full))

    t0 = time.process_time()
    reused = board.build_board(
        syms, feed_module=stub, limit=None,
        full_rows_for=matched, reuse_rows=cache,
    )
    cpu_reuse = time.process_time() - t0
    second = rows_of(reused)
    print("reuse build: %d rows, %.1fs cpu  (%.0f%% of the full build)"
          % (len(second), cpu_reuse, 100 * cpu_reuse / max(cpu_full, 1e-9)))

    print("\nsame symbol set: %s" % (set(first) == set(second)))
    missing = sorted(set(first) - set(second))
    if missing:
        print("  MISSING FROM THE REUSE BUILD: %s" % ", ".join(missing[:20]))

    diffs = []
    for symbol in sorted(set(first) & set(second)):
        a, b = flatten(first[symbol]), flatten(second[symbol])
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                diffs.append((symbol, key, a.get(key), b.get(key)))
    print("field diffs: %d" % len(diffs))
    for symbol, key, x, y in diffs[:30]:
        print("   %-6s %-30s full=%-16s reused=%s" % (symbol, key, x, y))
    print("\nVERDICT: %s" % (
        "IDENTICAL - the reuse is invisible on frozen tapes"
        if not diffs and not missing else "DIFFERENT - investigate before trusting it"))
    return 1 if (diffs or missing) else 0


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "run"
    if action == "capture":
        capture()
    else:
        sys.exit(run())
