# MAG7 Premarket Scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A dashboard table that lists, between 06:00 and 09:30 ET, which of the 9 scanned tickers show CALL2H/CALL4H (4x8 yellow or 9x20 cyan) or a squeeze fire on 1h/2h/4h/D, scored WEAK/MODERATE/STRONG.

**Architecture:** The scanner reads the LIVE streamed tape, not just the 30s-refreshed cache, so fires do not lag the chart. The table mirrors the 5-minute chart rather than recomputing it. CALL2H/CALL4H are read straight from the cached `mtfSignals` array the chart itself draws from, so that half cannot drift. Squeeze fire exists only in browser JavaScript today; it gets extracted into a small JS module and ported to Python, with a golden fixture pinning the two implementations together.

**Tech Stack:** Python 3 (stdlib only — no pandas in the new module), `node --test` for frontend tests, React + Vite.

**Spec:** `docs/superpowers/specs/2026-08-20-mag7-premarket-scanner-design.md`

## Global Constraints

- **Work in the LIVE repo:** `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2` (branch `OI-scanner-BOT`). The outer `AgenticAI-Trading 7` is a mirror where nothing executes. Mirror the final commits outward for git history.
- **Line numbers in the spec came from the mirror and differ in the live repo.** Locate every edit by symbol name (`grep -n`), never by line number.
- **Python interpreter:** `./.venv/Scripts/python.exe`. There is no global `python`.
- **Frontend tests:** `cd frontend; node --test src/*.test.js`
- **Never `git add -A`** — `.venv` is tracked and it sweeps in thousands of `.pyc` files. Commit with explicit paths only.
- **Never edit `App.jsx` with Python tooling** — it flips the file to CRLF and breaks the tests that regex functions out of its source. Use LF-preserving edits.
- **Re-read any `App.jsx` region immediately before editing it.** A second agent session frequently has uncommitted work in that file; line numbers shift between reads.
- **Do not restart the backend during market hours** and announce backend writes to the coordinating session first.
- Squeeze constants: period `20`, Bollinger `2` deviations, Keltner `1.5 * ATR`.
- Fire timeframes: `60` (1h), `120` (2h), `240` (4h), `1440` (D). 15m and 30m are excluded.
- Window: `06:00:00`–`09:30:00` ET, weekdays.
- **Deliberate simplification vs the spec:** the spec proposed a `premarket6` session key threaded through `_mag7_signal_session_window`. This plan instead adds a standalone `mag7_premarket_scanner_payload()`, so that shared function is never touched and the existing premarket table cannot regress. Same behaviour, smaller blast radius.
- Strength: 1 point per hit. `>3` STRONG, `==3` MODERATE, `<3` WEAK.
- **Scanner universe is exactly these 9** (user decision 2026-08-20), NOT
  `_mag7_option_underlyings()`, which is 22 symbols including leveraged ETFs:

  ```python
  PREMARKET_SCAN_SYMBOLS = ("AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX")
  ```

  Define this in `premarket_scanner.py` so it is one edit to change. All nine
  are already in `QUICK_STRIP_WARM_SYMBOLS` (`api_server.py:17068`), the
  priority set the chart warmer builds **without yielding to the trader** —
  so their tapes stay warm even though the user only opens a chart *after*
  the scanner flags something. Do not add a symbol outside that set without
  also adding it there, or its rows will silently never appear.

- **The scanner must NOT live behind the dashboard cache.**
  `dashboard_payload()` serves a payload cached for
  `DASHBOARD_FULL_CACHE_TTL_SECONDS = 60.0` (`api_server.py:287`). Putting
  the scanner there would make it up to 60 s stale — worse than the 30 s lag
  Task 3b exists to remove. It gets its own endpoint (Task 4).

- **The scanner endpoint must NOT call `touch_oi_finder_interactive_window()`.**
  That sets a 45 s "a human is watching" pause on the background collector.
  A 5 s poll calling it would hold that pause open permanently.

---

### Task 1: Extract squeeze-release detection into its own JS module

Today `calculateMtfSqueezeReleaseClouds` lives inside `App.jsx` and cannot be imported by a test or a fixture generator. Split the pure detection out, leaving chart decoration (session cutoff, anchor price, bubble label) in `App.jsx`. This matches the codebase's established pattern of putting pure logic in small files next to `App.jsx`.

**Files:**
- Create: `frontend/src/squeezeRelease.js`
- Create: `frontend/src/squeezeRelease.test.js`
- Modify: `frontend/src/App.jsx` (function `calculateMtfSqueezeReleaseClouds`, plus `calculateRollingAverage` / `calculateRollingStdDev` / `calculateTrueRanges`)

**Interfaces:**
- Consumes: `aggregateChartBars` from `./chartAggregation`.
- Produces: `squeezeReleaseEvents(bars, minutes) -> [{minutes, bucketTime, closeTime, tone}]`, plus re-exported `calculateRollingAverage(values, period)`, `calculateRollingStdDev(values, period)`, `calculateTrueRanges(bars)`. Task 2 generates its fixture from `squeezeReleaseEvents`.

- [x] **Step 1: Write the failing test**

```js
// frontend/src/squeezeRelease.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { squeezeReleaseEvents, calculateRollingStdDev } from "./squeezeRelease.js";

// Population standard deviation with a partial leading window, matching the
// original App.jsx helper. A sample stdev would give 1.0 for the third entry.
test("rolling stdev is population, not sample, and fills partial windows", () => {
  const values = calculateRollingStdDev([1, 2, 3], 20);
  assert.equal(values[0], 0);
  assert.equal(values[1], 0.5);
  assert.ok(Math.abs(values[2] - 0.816496580927726) < 1e-12);
});

// 21 flat bars put the series in a squeeze (stdev 0, ATR 0 -> 0 <= 0), then a
// wide bar breaks it. The release must be reported on the breakout bucket.
test("reports a release when the squeeze breaks on a closed bucket", () => {
  const bars = [];
  for (let index = 0; index < 21; index += 1) {
    const time = 1_700_000_000 + index * 3600;
    bars.push({ time, open: 100, high: 100, low: 100, close: 100 });
  }
  bars.push({ time: 1_700_000_000 + 21 * 3600, open: 100, high: 140, low: 60, close: 130 });
  // One extra bar so the breakout bucket is CLOSED, not forming.
  bars.push({ time: 1_700_000_000 + 22 * 3600, open: 130, high: 131, low: 129, close: 130 });

  const events = squeezeReleaseEvents(bars, 60);
  assert.equal(events.length, 1);
  assert.equal(events[0].tone, "bull");
  assert.equal(events[0].minutes, 60);
  assert.equal(events[0].closeTime, events[0].bucketTime + 3600);
});

// Live commit 461d4af: a release on a still-forming bucket flickers, so it is
// not a release until the bucket closes.
test("a still-forming bucket produces no release", () => {
  const bars = [];
  for (let index = 0; index < 21; index += 1) {
    const time = 1_700_000_000 + index * 3600;
    bars.push({ time, open: 100, high: 100, low: 100, close: 100 });
  }
  bars.push({ time: 1_700_000_000 + 21 * 3600, open: 100, high: 140, low: 60, close: 130 });

  assert.deepEqual(squeezeReleaseEvents(bars, 60), []);
});
```

- [x] **Step 2: Run the test to verify it fails**

Run: `cd frontend; node --test src/squeezeRelease.test.js`
Expected: FAIL — `Cannot find module './squeezeRelease.js'`

- [x] **Step 3: Create the module**

Copy the three helpers verbatim out of `App.jsx` (find them with `grep -n "^function calculateRollingAverage" frontend/src/App.jsx`) so behaviour is provably unchanged, then add the detector.

```js
// frontend/src/squeezeRelease.js
import { aggregateChartBars } from "./chartAggregation";

export const SQUEEZE_LENGTH = 20;

export function calculateRollingAverage(values, period) {
  const length = Math.max(1, Number(period) || 1);
  let total = 0;
  const window = [];
  return (Array.isArray(values) ? values : []).map((value) => {
    const numeric = Number(value || 0);
    window.push(numeric);
    total += numeric;
    if (window.length > length) total -= window.shift();
    return total / window.length;
  });
}

export function calculateRollingStdDev(values, period) {
  const length = Math.max(1, Number(period) || 1);
  return (Array.isArray(values) ? values : []).map((_, index) => {
    const window = values.slice(Math.max(0, index - length + 1), index + 1).map((value) => Number(value || 0));
    const average = window.reduce((sum, value) => sum + value, 0) / Math.max(window.length, 1);
    return Math.sqrt(window.reduce((sum, value) => sum + (value - average) ** 2, 0) / Math.max(window.length, 1));
  });
}

export function calculateTrueRanges(bars) {
  return (Array.isArray(bars) ? bars : []).map((bar, index, source) => {
    const high = Number(bar?.high || 0);
    const low = Number(bar?.low || 0);
    if (index === 0) return high - low;
    const previousClose = Number(source[index - 1]?.close || 0);
    return Math.max(high - low, Math.abs(high - previousClose), Math.abs(low - previousClose));
  });
}

/**
 * Squeeze releases for one aggregation, with NO chart-session cutoff.
 *
 * The cutoff, anchor price and bubble label stay in App.jsx: they are chart
 * presentation. This function is the part the Python scanner mirrors, so it
 * must contain only facts about the tape.
 */
export function squeezeReleaseEvents(bars, minutes) {
  const source = Array.isArray(bars) ? bars : [];
  const span = Math.max(1, Number(minutes) || 1);
  const timeframeBars = aggregateChartBars(source, span);
  if (timeframeBars.length <= SQUEEZE_LENGTH) return [];

  const closes = timeframeBars.map((bar) => Number(bar.close || 0));
  const average = calculateRollingAverage(closes, SQUEEZE_LENGTH);
  const standardDeviation = calculateRollingStdDev(closes, SQUEEZE_LENGTH);
  const averageTrueRange = calculateRollingAverage(calculateTrueRanges(timeframeBars), SQUEEZE_LENGTH);
  const inSqueeze = timeframeBars.map((_, index) => (
    average[index] + 2 * standardDeviation[index] - (average[index] + 1.5 * averageTrueRange[index]) <= 0
  ));

  const lastSourceTime = Number(source[source.length - 1]?.time || 0);
  return timeframeBars.flatMap((bar, index) => {
    if (index < SQUEEZE_LENGTH || inSqueeze[index] || !inSqueeze[index - 1]) return [];
    const bucketTime = Number(bar.time);
    const closeTime = bucketTime + span * 60;
    if (closeTime > lastSourceTime) return [];
    return [{
      minutes: span,
      bucketTime,
      closeTime,
      tone: Number(bar.close) > Number(timeframeBars[index - 1]?.close) ? "bull" : "bear",
    }];
  });
}
```

- [x] **Step 4: Run the test to verify it passes**

Run: `cd frontend; node --test src/squeezeRelease.test.js`
Expected: PASS, 3 tests

- [x] **Step 5: Rewrite `calculateMtfSqueezeReleaseClouds` in App.jsx to use the module**

Add `import { squeezeReleaseEvents, calculateRollingAverage, calculateRollingStdDev, calculateTrueRanges } from "./squeezeRelease";` alongside the other local imports, then **delete** the three helper definitions from `App.jsx` (other studies such as `calculateMtfSqueeze410Study` keep working via the import) and replace the body of the definitions loop:

```js
  return definitions.flatMap((definition) => {
    if (definition.minutes < displayedMinutes || options[definition.optionKey] === false) return [];
    const timeframeBars = aggregateChartBars(source, definition.minutes);
    const barsByTime = new Map(timeframeBars.map((bar) => [Number(bar.time), bar]));
    return squeezeReleaseEvents(source, definition.minutes).flatMap((event) => {
      if (event.bucketTime < cutoffTime) return [];
      const bar = barsByTime.get(event.bucketTime);
      const anchor = event.tone === "bull" ? Number(bar?.low) : Number(bar?.high);
      if (!Number.isFinite(anchor)) return [];
      return [{
        key: `${definition.key}-${event.bucketTime}-${event.tone}`,
        timeframe: definition.label,
        startTime: event.bucketTime,
        endTime: event.closeTime,
        anchor,
        tone: event.tone,
        family: "mtf-squeeze-release",
        bubbleLabel: `🔥${definition.bubbleLabel}`,
      }];
    });
  });
```

- [x] **Step 6: Run the whole frontend suite to prove nothing regressed**

Run: `cd frontend; node --test src/*.test.js`
Expected: PASS — the pre-existing count (475 as of live commit `461d4af`) plus the 3 new tests. **If any previously-passing test now fails, the extraction changed behaviour — stop and fix before continuing.**

- [x] **Step 7: Commit**

```bash
git add frontend/src/squeezeRelease.js frontend/src/squeezeRelease.test.js frontend/src/App.jsx
git commit -m "refactor(chart): extract squeeze-release detection into its own module

Pure detection splits out of App.jsx so the Python scanner can be pinned
against it by a golden fixture. Chart presentation - session cutoff, anchor
price, bubble label - stays in App.jsx."
```

---

### Task 2: Port squeeze release to Python, pinned by a golden fixture

**Files:**
- Create: `premarket_scanner.py`
- Create: `tests/test_premarket_scanner.py`
- Create: `scripts/generate_squeeze_fixture.mjs`
- Create: `tests/fixtures/squeeze_release_bars.json`, `tests/fixtures/squeeze_release_expected.json`

**Interfaces:**
- Consumes: `squeezeReleaseEvents` from Task 1 (via the fixture generator only).
- Produces: `chart_bucket_time(timestamp, minutes)`, `aggregate_chart_bars(bars, minutes)`, `rolling_average(values, period)`, `rolling_stdev(values, period)`, `true_ranges(bars)`, `squeeze_release_events(bars, minutes) -> [{"minutes","bucketTime","closeTime","tone"}]`. Task 3 calls `squeeze_release_events`.

- [ ] **Step 1: Capture a real bar series for the fixture**

The backend must be running. This reads the cache; it does not start a broker fetch.

```bash
curl -s "http://127.0.0.1:3001/api/oi-finder-chart?symbol=MSTR" \
  | ./.venv/Scripts/python.exe -c "import json,sys; print(json.dumps(json.load(sys.stdin)['studyBars']))" \
  > tests/fixtures/squeeze_release_bars.json
```

Verify it is a non-trivial series: `./.venv/Scripts/python.exe -c "import json;d=json.load(open('tests/fixtures/squeeze_release_bars.json'));print(len(d), d[0], d[-1])"` — expect several hundred bars. If it returns fewer than 100, the chart cache is cold; wait and retry rather than proceeding with a thin tape.

- [x] **Step 2: Generate the expected events from the real JavaScript**

```js
// scripts/generate_squeeze_fixture.mjs
import { readFileSync, writeFileSync } from "node:fs";
import { squeezeReleaseEvents } from "../frontend/src/squeezeRelease.js";

const bars = JSON.parse(readFileSync("tests/fixtures/squeeze_release_bars.json", "utf8"));
const expected = {};
for (const minutes of [60, 120, 240, 1440]) {
  expected[String(minutes)] = squeezeReleaseEvents(bars, minutes);
}
writeFileSync("tests/fixtures/squeeze_release_expected.json", `${JSON.stringify(expected, null, 2)}\n`);
console.log(Object.entries(expected).map(([k, v]) => `${k}: ${v.length}`).join(", "));
```

Run: `node scripts/generate_squeeze_fixture.mjs`
Expected: prints a per-timeframe count. **At least one timeframe must be non-zero** — an all-zero fixture proves nothing. If all are zero, pick a more volatile symbol (NVDA, TSLA) and redo Step 1.

- [x] **Step 3: Write the failing test**

```python
# tests/test_premarket_scanner.py
import json
from pathlib import Path

from premarket_scanner import (
    aggregate_chart_bars,
    chart_bucket_time,
    rolling_average,
    rolling_stdev,
    squeeze_release_events,
    true_ranges,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _bars():
    return json.loads((FIXTURES / "squeeze_release_bars.json").read_text(encoding="utf-8"))


def _expected():
    return json.loads((FIXTURES / "squeeze_release_expected.json").read_text(encoding="utf-8"))


def test_matches_the_javascript_chart_event_for_event():
    """The scanner may never disagree with the flame the chart draws."""
    bars = _bars()
    expected = _expected()
    assert any(expected[key] for key in expected), "fixture has no releases; regenerate it"
    for minutes in (60, 120, 240, 1440):
        assert squeeze_release_events(bars, minutes) == expected[str(minutes)], minutes


def test_rolling_stdev_is_population_with_partial_windows():
    values = rolling_stdev([1, 2, 3], 20)
    assert values[0] == 0
    assert values[1] == 0.5
    assert abs(values[2] - 0.816496580927726) < 1e-12


def test_true_range_seeds_from_high_low_then_uses_previous_close():
    bars = [
        {"high": 10.0, "low": 9.0, "close": 9.5},
        {"high": 12.0, "low": 11.0, "close": 11.5},
    ]
    assert true_ranges(bars) == [1.0, 3.0]


def test_four_hour_buckets_use_the_tos_central_clock():
    """TOS aggregates equity bars from midnight Central, so the boundaries
    visible on this Eastern chart are 01:00 / 05:00 / 09:00 / 13:00."""
    from datetime import datetime as _dt

    def _at(hour, minute=0):
        return int(_dt(2026, 8, 20, hour, minute, tzinfo=EASTERN).timestamp())

    def _bucket_hour(hour, minute=0):
        return _dt.fromtimestamp(chart_bucket_time(_at(hour, minute), 240), tz=EASTERN).strftime("%H:%M")

    assert _bucket_hour(9, 30) == "09:00"
    assert _bucket_hour(8, 59) == "06:00"
    assert _bucket_hour(5, 0) == "06:00"
    assert _bucket_hour(0, 30) == "21:00"  # previous day's bucket


def test_daily_and_two_hour_buckets_anchor_to_eastern_midnight():
    from datetime import datetime as _dt

    stamp = int(_dt(2026, 8, 20, 7, 15, tzinfo=EASTERN).timestamp())
    assert _dt.fromtimestamp(chart_bucket_time(stamp, 1440), tz=EASTERN).strftime("%H:%M") == "00:00"
    assert _dt.fromtimestamp(chart_bucket_time(stamp, 120), tz=EASTERN).strftime("%H:%M") == "06:00"


def test_aggregation_takes_first_open_extremes_and_last_close():
    bars = [
        {"time": 1_700_000_000, "open": 1.0, "high": 5.0, "low": 0.5, "close": 2.0},
        {"time": 1_700_000_060, "open": 2.0, "high": 9.0, "low": 1.5, "close": 3.0},
    ]
    merged = aggregate_chart_bars(bars, 60)
    assert len(merged) == 1
    assert (merged[0]["open"], merged[0]["high"], merged[0]["low"], merged[0]["close"]) == (1.0, 9.0, 0.5, 3.0)


def test_rolling_average_fills_partial_windows():
    assert rolling_average([2, 4], 20) == [2.0, 3.0]
```

- [x] **Step 4: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'premarket_scanner'`

- [x] **Step 5: Write the implementation**

```python
# premarket_scanner.py
"""Premarket Mag7 scanner primitives.

Mirrors what the 5-minute chart draws. The CALL2H/CALL4H half is read
straight from the cached ``mtfSignals`` array, so only squeeze release is
reimplemented here — and it is pinned to the JavaScript by a golden fixture
(``tests/test_premarket_scanner.py``). Stdlib only: this runs on the
dashboard request path and must not pull pandas in.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

SQUEEZE_LENGTH = 20
# 15m and 30m are deliberately absent: they fire near-continuously and would
# drown the strength score.
FIRE_TIMEFRAMES = ((60, "1h"), (120, "2h"), (240, "4h"), (1440, "D"))
# The nine the trader actually watches -- NOT the 22-symbol Mag7 option
# watchlist, which carries leveraged ETFs he does not scan. Every one of
# these is in QUICK_STRIP_WARM_SYMBOLS, so its chart tape is warmed in the
# background; a symbol added here but not there would never produce a row.
PREMARKET_SCAN_SYMBOLS = (
    "AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX",
)


def _eastern_midnight(timestamp: int) -> int:
    moment = datetime.fromtimestamp(int(timestamp), tz=EASTERN)
    return int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def chart_bucket_time(timestamp: object, minutes: object) -> int | None:
    """Bucket start for the CHART's aggregation clocks.

    These are deliberately not the backend MTF engine's clocks; see the note
    in frontend/src/chartAggregation.js. Getting this wrong silently
    misplaces every fire.
    """
    try:
        time_value = int(timestamp or 0)
    except (TypeError, ValueError):
        return None
    if time_value <= 0:
        return None
    span = max(int(minutes or 1), 1)
    if span == 1440:
        return _eastern_midnight(time_value)
    if span == 240:
        # TOS aggregates equity bars from midnight CENTRAL, one hour behind
        # Eastern, so the visible ET boundaries are 01:00/05:00/09:00/...
        anchor = _eastern_midnight(time_value) + 3600
        four_hours = 240 * 60
        return anchor + ((time_value - anchor) // four_hours) * four_hours
    if span == 120:
        # Anchored to exchange midnight so 04:00 stays 04:00 across DST.
        anchor = _eastern_midnight(time_value)
        seconds = span * 60
        return anchor + ((time_value - anchor) // seconds) * seconds
    seconds = span * 60
    return (time_value // seconds) * seconds


def aggregate_chart_bars(bars: object, minutes: object) -> list[dict]:
    """First open, extreme high/low, last close per bucket. Input must be
    time-ascending, which every cached chart tape already is."""
    buckets: dict[int, dict] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        try:
            time_value = int(bar["time"])
            open_value = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        bucket_time = chart_bucket_time(time_value, minutes)
        if not bucket_time or bucket_time <= 0:
            continue
        current = buckets.get(bucket_time)
        if current is None:
            buckets[bucket_time] = {
                "time": bucket_time,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
            }
            continue
        current["high"] = max(current["high"], high)
        current["low"] = min(current["low"], low)
        current["close"] = close
    return [buckets[key] for key in sorted(buckets)]


def rolling_average(values: object, period: object) -> list[float]:
    """Running-total average with a partial leading window.

    Replicates the JavaScript's running-total-with-subtraction exactly,
    including its float accumulation order — recomputing each window instead
    would drift from the chart in the last decimal places.
    """
    length = max(int(period or 1), 1)
    output: list[float] = []
    window: list[float] = []
    total = 0.0
    for value in values or []:
        numeric = float(value or 0)
        window.append(numeric)
        total += numeric
        if len(window) > length:
            total -= window.pop(0)
        output.append(total / len(window))
    return output


def rolling_stdev(values: object, period: object) -> list[float]:
    """POPULATION standard deviation (divide by N), partial leading window."""
    length = max(int(period or 1), 1)
    numbers = [float(value or 0) for value in (values or [])]
    output: list[float] = []
    for index in range(len(numbers)):
        window = numbers[max(0, index - length + 1): index + 1]
        count = max(len(window), 1)
        average = sum(window) / count
        output.append((sum((item - average) ** 2 for item in window) / count) ** 0.5)
    return output


def true_ranges(bars: object) -> list[float]:
    source = list(bars or [])
    output: list[float] = []
    for index, bar in enumerate(source):
        high = float((bar or {}).get("high") or 0)
        low = float((bar or {}).get("low") or 0)
        if index == 0:
            output.append(high - low)
            continue
        previous_close = float((source[index - 1] or {}).get("close") or 0)
        output.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return output


def squeeze_release_events(bars: object, minutes: object) -> list[dict]:
    """Squeeze releases on one aggregation, matching the chart's flames.

    A release is a bucket-CLOSE fact (live commit 461d4af): the forming
    bucket's bands and ATR move with every tick, so treating it as live made
    the flame flicker in and out.
    """
    source = [bar for bar in (bars or []) if isinstance(bar, dict)]
    span = max(int(minutes or 1), 1)
    timeframe_bars = aggregate_chart_bars(source, span)
    if len(timeframe_bars) <= SQUEEZE_LENGTH:
        return []

    closes = [float(bar["close"]) for bar in timeframe_bars]
    average = rolling_average(closes, SQUEEZE_LENGTH)
    deviation = rolling_stdev(closes, SQUEEZE_LENGTH)
    keltner = rolling_average(true_ranges(timeframe_bars), SQUEEZE_LENGTH)
    in_squeeze = [
        (average[index] + 2 * deviation[index]) - (average[index] + 1.5 * keltner[index]) <= 0
        for index in range(len(timeframe_bars))
    ]

    try:
        last_source_time = int(source[-1]["time"]) if source else 0
    except (KeyError, TypeError, ValueError):
        last_source_time = 0

    events: list[dict] = []
    for index in range(SQUEEZE_LENGTH, len(timeframe_bars)):
        if in_squeeze[index] or not in_squeeze[index - 1]:
            continue
        bucket_time = int(timeframe_bars[index]["time"])
        close_time = bucket_time + span * 60
        if close_time > last_source_time:
            continue
        events.append({
            "minutes": span,
            "bucketTime": bucket_time,
            "closeTime": close_time,
            "tone": "bull" if closes[index] > closes[index - 1] else "bear",
        })
    return events
```

- [x] **Step 6: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -v`
Expected: PASS, 7 tests. If `test_matches_the_javascript_chart_event_for_event` fails, the port is wrong — **do not adjust the fixture to match the Python.** The fixture is the chart; the Python is what must move.

- [x] **Step 7: Commit**

```bash
git add premarket_scanner.py tests/test_premarket_scanner.py scripts/generate_squeeze_fixture.mjs tests/fixtures/squeeze_release_bars.json tests/fixtures/squeeze_release_expected.json
git commit -m "feat(scanner): port squeeze release to Python, pinned to the chart

Golden fixture generated from the real frontend module, so a future change
that makes the table disagree with the chart's flame fails a test."
```

---

### Task 3: Window filter, match rule and strength score

**Files:**
- Modify: `premarket_scanner.py`
- Modify: `tests/test_premarket_scanner.py`

**Interfaces:**
- Consumes: `squeeze_release_events`, `FIRE_TIMEFRAMES` from Task 2.
- Produces: `premarket_window(now_et) -> (start_epoch, end_epoch)`, `window_call_signals(mtf_signals, start, end) -> list[dict]`, `window_fires(bars, start, end) -> list[dict]`, `score_strength(calls, fires) -> (int, str)`, `premarket_scan_row(symbol, payload, now_et) -> dict | None`. Task 4 calls `premarket_scan_row`.

- [x] **Step 1: Write the failing test**

```python
# append to tests/test_premarket_scanner.py
from datetime import datetime

from premarket_scanner import (
    EASTERN,
    premarket_scan_row,
    premarket_window,
    score_strength,
    window_call_signals,
)


def _now():
    return datetime(2026, 8, 20, 8, 15, tzinfo=EASTERN)


def _call(label, family, when):
    return {
        "label": label,
        "family": family,
        "color": "yellow" if family == "4x8" else "cyan",
        "direction": "CALL",
        "time": int(when.timestamp()),
        "liveForming": False,
    }


def test_window_spans_0600_to_0930_eastern():
    start, end = premarket_window(_now())
    assert datetime.fromtimestamp(start, tz=EASTERN).strftime("%H:%M") == "06:00"
    assert datetime.fromtimestamp(end, tz=EASTERN).strftime("%H:%M") == "09:30"


def test_window_boundaries_are_inclusive():
    start, end = premarket_window(_now())
    inside = [
        _call("CALL2H", "4x8", datetime.fromtimestamp(start, tz=EASTERN)),
        _call("CALL4H", "9x20", datetime.fromtimestamp(end, tz=EASTERN)),
    ]
    outside = [
        _call("CALL2H", "4x8", datetime(2026, 8, 20, 4, 59, 59, tzinfo=EASTERN)),
        _call("CALL2H", "4x8", datetime(2026, 8, 20, 9, 30, 1, tzinfo=EASTERN)),
    ]
    assert len(window_call_signals(inside + outside, start, end)) == 2


def test_only_confirmed_call_labels_count():
    """C2H/C4H mean the higher timeframe is not confirming; the user asked
    for CALL specifically, so they are context, never score."""
    start, end = premarket_window(_now())
    when = datetime(2026, 8, 20, 7, 0, tzinfo=EASTERN)
    signals = [
        _call("CALL2H", "4x8", when),
        _call("C2H", "4x8", when),
        _call("C4H", "9x20", when),
        {**_call("CALL2H", "4x8", when), "direction": "PUT"},
    ]
    kept = window_call_signals(signals, start, end)
    assert [signal["label"] for signal in kept] == ["CALL2H"]


def test_strength_thresholds():
    def calls(count):
        return [_call("CALL2H", "4x8", _now())] * count

    assert score_strength(calls(1), [])[1] == "WEAK"
    assert score_strength(calls(2), [])[1] == "WEAK"
    assert score_strength(calls(3), [])[1] == "MODERATE"
    assert score_strength(calls(2), [{"minutes": 60}])[1] == "MODERATE"
    assert score_strength(calls(2), [{"minutes": 60}, {"minutes": 120}]) == (4, "STRONG")


def test_a_lone_fire_is_enough_to_produce_a_row():
    payload = {"bars": [{"time": 1, "close": 100.0}], "mtfSignals": []}
    fire = {"minutes": 1440, "closeTime": 1, "tone": "bull", "label": "D"}
    row = premarket_scan_row("MSTR", payload, _now(), fires=[fire])
    assert row is not None
    assert row["strength"] == "WEAK"
    assert row["fires"] == ["D"]


def test_no_signals_means_no_row():
    payload = {"bars": [{"time": 1, "close": 100.0}], "mtfSignals": []}
    assert premarket_scan_row("MSTR", payload, _now(), fires=[]) is None


def test_a_cold_payload_yields_no_row():
    assert premarket_scan_row("MSTR", {}, _now()) is None
```

- [x] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -v`
Expected: FAIL — `ImportError: cannot import name 'premarket_window'`

- [x] **Step 3: Implement**

```python
# append to premarket_scanner.py

WINDOW_START_MINUTE = 6 * 60          # 06:00 ET
WINDOW_END_MINUTE = 9 * 60 + 30       # 09:30 ET
CALL_LABELS = ("CALL2H", "CALL4H")
STRONG_THRESHOLD = 3                  # "more than 3" -> STRONG


def premarket_window(now_et: datetime) -> tuple[int, int]:
    day = now_et.astimezone(EASTERN)
    start = day.replace(hour=6, minute=0, second=0, microsecond=0)
    end = day.replace(hour=9, minute=30, second=0, microsecond=0)
    return int(start.timestamp()), int(end.timestamp())


def window_call_signals(mtf_signals: object, start: int, end: int) -> list[dict]:
    """CALL2H/CALL4H from the chart's own array, inside the window.

    Read-only over ``mtfSignals`` — the exact array the chart draws its
    boxes from — so these can never disagree with the chart.
    """
    kept: list[dict] = []
    for signal in mtf_signals or []:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("direction") or "").upper() != "CALL":
            continue
        if str(signal.get("label") or "").upper() not in CALL_LABELS:
            continue
        try:
            when = int(signal.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if start <= when <= end:
            kept.append(signal)
    return kept


def window_fires(bars: object, start: int, end: int) -> list[dict]:
    """Fires the chart is showing this premarket.

    1h/2h/4h qualify on their bucket CLOSE landing in the window. Daily is
    special: a daily candle does not close until midnight, so a 🔥D visible
    during premarket is always the previous session's release. The chart
    draws it, so it counts — carrying its own date.
    """
    fires: list[dict] = []
    for minutes, label in FIRE_TIMEFRAMES:
        events = squeeze_release_events(bars, minutes)
        if not events:
            continue
        if minutes == 1440:
            latest = events[-1]
            if latest["closeTime"] <= end:
                fires.append({**latest, "label": label})
            continue
        fires.extend(
            {**event, "label": label}
            for event in events
            if start <= event["closeTime"] <= end
        )
    return fires


def score_strength(calls: object, fires: object) -> tuple[int, str]:
    """One point per hit. Max 8: four CALL slots and four fire timeframes."""
    score = len(list(calls or [])) + len(list(fires or []))
    if score > STRONG_THRESHOLD:
        return score, "STRONG"
    if score == STRONG_THRESHOLD:
        return score, "MODERATE"
    return score, "WEAK"


def premarket_scan_row(
    symbol: str,
    payload: object,
    now_et: datetime,
    *,
    fires: object = None,
) -> dict | None:
    """One scanner row, or None when the symbol has nothing (or is cold)."""
    if not isinstance(payload, dict):
        return None
    bars = payload.get("bars")
    if not bars:
        return None

    start, end = premarket_window(now_et)
    calls = window_call_signals(payload.get("mtfSignals"), start, end)
    matched_fires = list(fires) if fires is not None else window_fires(
        payload.get("studyBars") or bars, start, end
    )
    if not calls and not matched_fires:
        return None

    score, strength = score_strength(calls, matched_fires)
    times = [int(signal["time"]) for signal in calls]
    times += [int(fire["closeTime"]) for fire in matched_fires]
    first = min(times) if times else None

    def labels(family: str) -> list[str]:
        seen: list[str] = []
        for signal in calls:
            if str(signal.get("family") or "") != family:
                continue
            label = str(signal.get("label") or "")
            if label and label not in seen:
                seen.append(label)
        return seen

    try:
        last_price = round(float(bars[-1].get("close") or 0), 2)
    except (AttributeError, TypeError, ValueError):
        last_price = 0.0

    return {
        "symbol": str(symbol or "").upper(),
        "signalAt": datetime.fromtimestamp(first, tz=EASTERN).isoformat() if first else None,
        "lastPrice": last_price,
        "signals48": labels("4x8"),
        "signals920": labels("9x20"),
        "fires": [fire["label"] for fire in matched_fires],
        "fireDates": {
            fire["label"]: datetime.fromtimestamp(fire["closeTime"], tz=EASTERN).isoformat()
            for fire in matched_fires
        },
        "score": score,
        "strength": strength,
        # A forming higher-timeframe cross can still repaint away. Say so
        # rather than letting a row vanish unexplained.
        "forming": any(signal.get("liveForming") is True for signal in calls),
    }
```

- [x] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -v`
Expected: PASS, 14 tests

- [x] **Step 5: Commit**

```bash
git add premarket_scanner.py tests/test_premarket_scanner.py
git commit -m "feat(scanner): premarket window, match rule and strength score

06:00-09:30 ET. CALL2H/CALL4H only (C2H/C4H are context, never score).
Fires qualify on bucket close; daily carries the prior session's date
because a daily candle cannot close during premarket."
```

---

### Task 3b: Extend the scanner tape with live streamed bars

**Why:** without this the scanner's fires lag the chart by up to 30 s (the
chart computes them in-browser off the streamed tape; the server cache
refreshes every 30 s). The user trades options and rejected that delay.
`SchwabMarketStream.chart_history(symbol)` already keeps live minute bars per
symbol and is tested — it has simply never been read by any request path.

**Files:**
- Modify: `premarket_scanner.py`
- Modify: `tests/test_premarket_scanner.py`

**Interfaces:**
- Produces: `merge_live_tail(cached_bars, live_bars) -> list[dict]`. Task 4
  passes `MARKET_STREAM.chart_history(symbol)` as `live_bars`.

- [x] **Step 1: Write the failing test**

```python
# append to tests/test_premarket_scanner.py
from premarket_scanner import merge_live_tail


def test_live_tail_extends_the_cached_tape():
    cached = [{"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    live = [{"time": 160, "open": 1.5, "high": 3.0, "low": 1.4, "close": 2.9}]
    assert [bar["time"] for bar in merge_live_tail(cached, live)] == [100, 160]


def test_live_bars_overlapping_the_cached_tape_are_ignored():
    """studyBars can be a 30-minute tape. Letting a 1-minute live bar replace
    a 30-minute bar at the same timestamp would silently discard that
    bucket's real high/low, so only the strictly-newer tail is appended."""
    cached = [
        {"time": 100, "open": 1.0, "high": 9.0, "low": 0.5, "close": 1.5},
        {"time": 200, "open": 1.5, "high": 8.0, "low": 1.0, "close": 2.0},
    ]
    live = [
        {"time": 200, "open": 1.9, "high": 2.1, "low": 1.9, "close": 2.0},
        {"time": 260, "open": 2.0, "high": 2.5, "low": 2.0, "close": 2.4},
    ]
    merged = merge_live_tail(cached, live)
    assert [bar["time"] for bar in merged] == [100, 200, 260]
    assert merged[1]["high"] == 8.0  # the cached 30m high survives


def test_merge_survives_an_empty_or_missing_live_feed():
    cached = [{"time": 100, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}]
    assert merge_live_tail(cached, []) == cached
    assert merge_live_tail(cached, None) == cached
    assert merge_live_tail([], [{"time": 5, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}])[0]["time"] == 5
```

- [x] **Step 2: Run to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -k live -v`
Expected: FAIL — `ImportError: cannot import name 'merge_live_tail'`

- [x] **Step 3: Implement**

```python
# append to premarket_scanner.py

def merge_live_tail(cached_bars: object, live_bars: object) -> list[dict]:
    """Cached tape plus only the STRICTLY NEWER live streamed bars.

    Appending rather than merging by timestamp is deliberate. ``studyBars``
    has shipped at both five- and thirty-minute cadences; a one-minute live
    bar landing on the same timestamp as a thirty-minute cached bar would
    replace it and throw away that bucket's true high and low. Anything at or
    before the cached tape's last bar is therefore ignored.
    """
    cached = [bar for bar in (cached_bars or []) if isinstance(bar, dict)]
    live = [bar for bar in (live_bars or []) if isinstance(bar, dict)]
    if not live:
        return cached

    def bar_time(bar: dict) -> int:
        try:
            return int(bar.get("time") or 0)
        except (TypeError, ValueError):
            return 0

    cutoff = max((bar_time(bar) for bar in cached), default=0)
    tail = sorted(
        (bar for bar in live if bar_time(bar) > cutoff),
        key=bar_time,
    )
    return [*cached, *tail]
```

- [x] **Step 4: Run to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -v`
Expected: PASS, 17 tests

- [x] **Step 5: Commit**

```bash
git add premarket_scanner.py tests/test_premarket_scanner.py
git commit -m "feat(scanner): extend the scan tape with live streamed bars

Fires were up to 30s behind the chart, which computes them in-browser from
the streamed tape. Only the strictly-newer tail is appended so a 1m live bar
cannot clobber a 30m cached bucket's high/low."
```

---

### Task 4: Wire the scanner into the dashboard payload

**Files:**
- Modify: `api_server.py` (methods `_is_oi_finder_mag7_live_session`, `dashboard_payload`; add `mag7_premarket_scanner_payload`)

**Interfaces:**
- Consumes: `premarket_scan_row` from Task 3.
- Produces: dashboard key `mag7PremarketScanner` → `{status, date, timezone, windowLabel, rows, matchCount, readySymbols, pendingSymbols, generatedAt}`. Task 5 renders it.

- [ ] **Step 1: Widen the cache warmer to 06:00 ET**

Find it: `grep -n "_is_oi_finder_mag7_live_session" -A 8 api_server.py`. Change the lower bound from `8 * 60` to `6 * 60` and update the docstring to say the premarket scanner needs tapes warm from 06:00. Leave the 16:15 upper bound alone.

- [x] **Step 2: Add the payload builder**

Insert next to `mag7_chart_signals_payload`. It reads only the warm chart cache — it must never start a broker fetch or a chart build.

```python
    def mag7_premarket_scanner_payload(self) -> dict:
        """06:00-09:30 ET Mag7 scanner rows, mirroring the 5m chart.

        Reads ONLY the already-warm OI-finder chart cache. Memoized per
        (symbol, latest bar time) so a dashboard poll on an unchanged tape
        costs a dict lookup — the squeeze maths is cheap, but it should not
        re-run on every poll.
        """
        now_et = datetime.now(ZoneInfo(EASTERN_TZ))
        symbols = list(PREMARKET_SCAN_SYMBOLS)
        rows: list[dict] = []
        ready: list[str] = []
        pending: list[str] = []

        memo = getattr(self, "_mag7_premarket_scan_memo", None)
        if not isinstance(memo, dict):
            memo = {}
            self._mag7_premarket_scan_memo = memo

        for symbol in symbols:
            with self.oi_finder_chart_lock:
                cached = self.oi_finder_chart_cache.get(symbol)
                payload = dict(cached["payload"]) if cached and cached.get("payload") else None
            bars = payload.get("bars") if payload else None
            if not bars:
                pending.append(symbol)
                continue
            ready.append(symbol)
            # Live tail so fires do not lag the chart by a cache refresh.
            # chart_history() is in-memory and lock-guarded; never let a
            # streamer hiccup take the whole scanner down.
            try:
                live_bars = MARKET_STREAM.chart_history(symbol)
            except Exception:
                live_bars = []
            scan_tape = merge_live_tail(payload.get("studyBars") or bars, live_bars)
            payload = {**payload, "studyBars": scan_tape}
            # Memoize on the LIVE tape's last bar, not the cached one, or the
            # 30s-stale key would defeat the whole point of the live tail.
            latest_time = int(scan_tape[-1].get("time") or 0) if scan_tape else 0
            key = (symbol, latest_time, now_et.date().isoformat())
            if key in memo:
                row = memo[key]
            else:
                try:
                    row = premarket_scan_row(symbol, payload, now_et)
                except Exception:
                    row = None
                memo[key] = row
                if len(memo) > 120:
                    for stale in list(memo)[: len(memo) - 120]:
                        memo.pop(stale, None)
            if row:
                rows.append(row)

        rows.sort(key=lambda item: (-int(item["score"]), item["symbol"]))
        return {
            "status": "READY" if ready else "WARMING",
            "date": now_et.date().isoformat(),
            "timezone": EASTERN_TZ,
            "windowLabel": "6:00 AM - 9:30 AM ET",
            "rows": rows,
            "matchCount": len(rows),
            "readySymbols": ready,
            "pendingSymbols": pending,
            "generatedAt": now_et.isoformat(),
            "message": (
                f"{len(rows)} of {len(symbols)} MAG7 symbols match in this premarket window."
                if ready
                else "Warming MAG7 chart tapes; rows appear as each chart caches."
            ),
        }
```

- [x] **Step 3: Import and expose it**

Add `from premarket_scanner import PREMARKET_SCAN_SYMBOLS, merge_live_tail, premarket_scan_row` beside the existing `from oi_auto_alerts import ...` line (`grep -n "^from oi_auto_alerts" api_server.py`). `MARKET_STREAM` is already a module-level global in `api_server.py` — no import needed, but confirm the payload builder is defined *after* it (`grep -n "^MARKET_STREAM = " api_server.py`); it is referenced at call time, so definition order inside the class is fine.

Also make sure all 22 scanner symbols are actually subscribed, or `chart_history` returns empty for most of them. In the payload builder, before the symbol loop:

```python
        if hasattr(self.client, "ensure_streaming"):
            try:
                self.client.ensure_streaming(list(symbols) + ["SPY"])
            except Exception:
                pass
```

Then add a **dedicated GET route**, not a dashboard key — the dashboard payload is cached 60 s and would make the scanner staler than the lag Task 3b removes. Put it beside the other GET routes (`grep -n 'parsed.path == "/api/oi-auto-alerts"' api_server.py` shows the pattern):

```python
            if parsed.path == "/api/premarket-scanner":
                # Deliberately NOT behind dashboard_payload()'s 60s cache, and
                # deliberately NOT calling touch_oi_finder_interactive_window()
                # -- a 5s poll would hold the background warmer paused forever.
                self._send_json(HTTPStatus.OK, STATE.mag7_premarket_scanner_payload())
                return
```

Confirm the path is authenticated: it is not in `AUTH_PUBLIC_API_PATHS` (`grep -n "AUTH_PUBLIC_API_PATHS" api_server.py`), so it inherits the normal cookie check. Leave it that way.

- [ ] **Step 4: Verify the payload is served**

Restart the backend per the project procedure (`Stop-Process` the `api_server` parent PID; the watchdog respawns it in ~15s and it listens ~20-30s later). **Market must be closed.** Then:

```bash
curl -s http://127.0.0.1:3001/api/premarket-scanner \
  | ./.venv/Scripts/python.exe -c "import json,sys; d=json.load(sys.stdin); print(d['status'], d['windowLabel'], d['matchCount'], d['readySymbols'])"
```

Then prove it is genuinely uncached — two calls a few seconds apart must
return different `generatedAt` values:

```bash
curl -s http://127.0.0.1:3001/api/premarket-scanner | grep -o '"generatedAt":"[^"]*"'
curl -s http://127.0.0.1:3001/api/premarket-scanner | grep -o '"generatedAt":"[^"]*"'
```

Identical timestamps mean it is being served from a cache — find and remove
that path before continuing, or the live-tape work in Task 3b is wasted.

Expected: a status of `READY` or `WARMING`, the window label, a match count, and the ready symbol list. Outside 06:00–09:30 a `matchCount` of `0` is correct, not a bug — confirm `readySymbols` is non-empty so you know the read path works.

- [x] **Step 5: Commit**

```bash
git add api_server.py
git commit -m "feat(scanner): serve the MAG7 premarket scanner payload

Reads only the warm chart cache, memoized per symbol and bar time. Warmer
now starts at 06:00 ET so tapes are ready when the window opens."
```

---

### Task 5: Render the scanner table

**Files:**
- Modify: `frontend/src/App.jsx`

**Interfaces:**
- Consumes: `dashboard.mag7PremarketScanner` from Task 4.

- [x] **Step 1: Poll the dedicated endpoint every 5 s**

The scanner does **not** ride the dashboard payload (that is cached 60 s).
Add its own poll near the other `useEffect` polls in the dashboard component:

```jsx
  const [premarketScanner, setPremarketScanner] = useState({
    status: "WARMING", windowLabel: "6:00 AM - 9:30 AM ET", rows: [],
    matchCount: 0, readySymbols: [], pendingSymbols: [], message: "",
  });

  useEffect(() => {
    if (popoutConfig.mode) return undefined;
    let cancelled = false;
    const load = async () => {
      try {
        const response = await fetch("/api/premarket-scanner", { cache: "no-store" });
        if (!response.ok) return;
        const payload = await response.json();
        if (!cancelled) setPremarketScanner(payload);
      } catch {
        // A dropped poll is not worth surfacing; the next one is 5s away.
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [popoutConfig.mode]);
```

- [x] **Step 2: Add the columns**

Place next to `mag7PremarketChartSignalColumns` (`grep -n "mag7PremarketChartSignalColumns" frontend/src/App.jsx`). `renderPremarketChartSignalBadges` already exists in this file and is reused.

```jsx
  const mag7PremarketScannerColumns = useMemo(() => [
    {
      key: "symbol",
      label: "Ticker",
      render: (value) => (
        <button
          className="symbol-pill"
          data-testid="mag7-premarket-scanner-symbol"
          onClick={() => openChartSignalChart(value, "5m", "mag7-premarket-scanner")}
          title={`Open ${value} on the 5-minute chart`}
          type="button"
        >
          {value}
        </button>
      ),
    },
    { key: "signalAt", label: "Date/Time (ET)", render: formatDateTime },
    {
      key: "signals48",
      label: "4/8",
      sortable: false,
      render: (value) => renderPremarketChartSignalBadges(value, "yellow"),
    },
    {
      key: "signals920",
      label: "9/20",
      sortable: false,
      render: (value) => renderPremarketChartSignalBadges(value, "cyan"),
    },
    {
      key: "fires",
      label: "Squeeze Fire",
      sortable: false,
      render: (value) => {
        const fires = Array.isArray(value) ? value : [];
        if (!fires.length) return "--";
        return (
          <div className="mtf-table-signals">
            {fires.map((label) => (
              <span className="mtf-table-signal mtf-table-signal-fire" key={`fire-${label}`}>
                {`🔥${label}`}
              </span>
            ))}
          </div>
        );
      },
    },
    {
      key: "strength",
      label: "Strength",
      render: (value, row) => (
        <span className={`premarket-strength is-${String(value || "weak").toLowerCase()}`}>
          {`${value} (${row?.score ?? 0})`}
          {row?.forming ? <em className="premarket-strength-forming"> FORMING</em> : null}
        </span>
      ),
    },
  ], [openChartSignalChart]);
```

- [x] **Step 3: Render the section**

Next to the existing premarket table's `<DataTable>` (`grep -n "mag7-premarket" frontend/src/App.jsx`), add:

```jsx
              <div className="table-toolbar">
                <span>MAG7 PREMARKET SCANNER</span>
                <span>{mag7PremarketScanner.windowLabel} · {mag7PremarketScanner.matchCount} match(es)</span>
              </div>
              <DataTable
                tableId="mag7-premarket-scanner"
                columns={mag7PremarketScannerColumns}
                rows={mag7PremarketScannerRows}
                emptyMessage={mag7PremarketScanner.message || "No MAG7 premarket matches yet."}
              />
```

with this beside it (`premarketScanner` comes from the Step 1 poll, not from `dashboard`):

```js
  const mag7PremarketScanner = premarketScanner;
  const mag7PremarketScannerRows = Array.isArray(premarketScanner.rows) ? premarketScanner.rows : [];
```

- [x] **Step 4: Add the strength styles**

Append to the END of `frontend/src/index.css` — a `@media` or later rule placed mid-file is silently outranked by the unconditional blocks below it.

```css
.premarket-strength.is-strong { color: #22d3ee; font-weight: 700; }
.premarket-strength.is-moderate { color: #facc15; }
.premarket-strength.is-weak { color: #94a3b8; }
.premarket-strength-forming { color: #94a3b8; font-size: 0.85em; font-style: italic; }
.mtf-table-signal-fire { background: rgba(249, 115, 22, 0.18); color: #fb923c; }
```

- [x] **Step 5: Verify it compiles and nothing regressed**

```bash
cd frontend; node --test src/*.test.js
```
Expected: PASS, same count as Task 1 Step 6.

Syntax-check `App.jsx` via the esbuild in `node_modules/.pnpm/` (bare `npx esbuild` is not installed):

```bash
node -e "const {transformSync}=require('./node_modules/.pnpm/'+require('fs').readdirSync('./node_modules/.pnpm').find(d=>d.startsWith('esbuild@'))+'/node_modules/esbuild');transformSync(require('fs').readFileSync('src/App.jsx','utf8'),{loader:'jsx'});console.log('App.jsx OK')"
```
Expected: `App.jsx OK`

- [x] **Step 6: Look at it in the browser**

Open `http://127.0.0.1:5173/` (dev server hot-reloads; no build needed) and find the MAG7 PREMARKET SCANNER table. Outside 06:00–09:30 expect the empty message — that is correct behaviour, not a failure. Confirm the table renders, the header shows `6:00 AM - 9:30 AM ET`, and no console errors.

- [x] **Step 7: Commit**

```bash
git add frontend/src/App.jsx frontend/src/index.css
git commit -m "feat(scanner): MAG7 premarket scanner table

Six columns: ticker, time, 4/8, 9/20, squeeze fire, strength. Sorted
strongest-first."
```

- [ ] **Step 8: Mirror the work into the outer repo**

The live repo is not the one GitHub sees. Copy the five new files and the four modified ones into `C:\GANESH\AgenticAI-Trading 7`, run the tests there, and commit with the same messages so the feature exists in git history.

---

## Verification before claiming done

- [x] `./.venv/Scripts/python.exe -m pytest tests/test_premarket_scanner.py -v` — 17 passing
- [x] `cd frontend; node --test src/*.test.js` — no regression against the pre-existing count
- [ ] `curl` on `/api/premarket-scanner` returns a non-empty `readySymbols` AND a changing `generatedAt` on repeat calls
- [x] The table renders at `:5173` with no console errors
- [ ] **The parity check that matters:** during a live premarket window, pick a row and open that ticker's 5m chart. Every CALL2H/CALL4H and every 🔥 in the row must be visible on the chart, and nothing on the chart in-window may be missing from the row. This cannot be run until a market morning — say so explicitly rather than implying it passed.

---

## Implementation record (2026-08-21, built overnight)

Shipped in the **live** repo `AgenticAI-Trading 2` (branch `OI-scanner-BOT`):

| Commit | What |
|---|---|
| `4a6920b` | Extract squeeze detection into `frontend/src/squeezeRelease.js` |
| `1cd2bdd` | Python port + golden fixture (72/32/13/4 real releases matched) |
| `260df77` | Window, match rule, strength score, live tail |
| `a9c3c7f` | `/api/premarket-scanner` route; warmer 08:00 -> 06:00 |
| `393ea5a` | Scanner view + mobile Scanner tab (Watchlist -> More) |
| `c07602f` | Drop the FORMING badge |

Final state: **18 Python tests, 481 frontend tests, all passing.**

### Three defects the build surfaced that the plan had wrong

1. **Stale daily fires.** The daily branch took the newest release anywhere in
   the tape, so an August scan reported a **2026-07-03** fire — a flame the
   chart would not draw. Now only the most recently *closed* daily candle
   counts. Regression test added.
2. **The FORMING badge could never be false.** `scanner.py:399` hardcodes
   `liveForming=True` on every signal (all 137 in a live payload). The badge
   would have shown on every row forever. Removed rather than faked.
3. **`node --test` needs explicit `.js` import extensions**; Vite does not.
   The plan's import would not have loaded under test.

### NOT DONE: mirroring code to the outer repo

Deliberately skipped. The mirror is far behind the live repo — App.jsx by
~2,710 lines, `api_server.py` by ~2,100, `index.css` by ~2,900, and it has no
`mobileOverflowNavigation.js` at all. Copying the live files across would
sweep thousands of lines of unrelated, unmerged work into a commit labelled
as this scanner change. That reconciliation is its own task and needs a human
deciding what belongs. **The running app is unaffected: it serves from the
live repo.**

### Not verifiable overnight

- **No browser tool in this session**, so the table was never seen rendered.
  Verified instead: the built bundle served from `:4173` contains every new
  identifier, the CSS shipped, and both suites pass.
- **The live-window parity check** — open a flagged ticker's 5m chart during
  a real premarket and confirm every CALL2H/CALL4H and flame in the row is on
  the chart — needs a market morning. It has not been run.

---

## Implementation record (2026-09-23, GitHub repo)

Rebuilt in the GitHub repo `Momo-Charting` (branch
`claude/upbeat-galileo-t09cu4`), a Linux cloud container, not the Windows
live repo. The three defects above were built correctly from the start.
Not pushed.

| Commit | What |
|---|---|
| `f3082e6` | Extract squeeze detection into `frontend/src/squeezeRelease.js` |
| `4552e4b` | Python port + golden fixture + generators (19/11/3/1 releases on 1h/2h/4h/D) |
| `42bb8ff` | Window, match rule, strength score; daily = last CLOSED daily only; no FORMING |
| `2ff2efc` | `merge_live_tail` (live stream tail) |
| `4933e44` | `GET /api/premarket-scanner` (`mag7_premarket_scanner_payload`) |
| `f3eaa90` | Scanner table in the OI Scanner view + `premarketScanner.js` helpers |

**Tests.** Python: 856 passed before, 883 after (+24 in
`tests/test_premarket_scanner.py`, +3 in `tests/test_premarket_scanner_payload.py`);
the 13 pre-existing failures and 5 collection errors are unchanged and
unrelated. Frontend: 337 before, 344 after (+3 `squeezeRelease.test.js`,
+4 `premarketScanner.test.js`), 0 failing. `vite build` passes.

**Fixture provenance: SYNTHETIC.** There was no backend, no broker
credentials and no cached intraday bars in this environment, so Task 2
Step 1 could not be run. `scripts/generate_squeeze_bars.mjs` writes a seeded
5-minute Ornstein-Uhlenbeck tape (45 weekdays, 04:00-20:00 ET, 2026-09-21 to
2026-11-20, crossing the 2026-11-01 DST change) with coil/expansion regimes;
`scripts/generate_squeeze_fixture.mjs` then runs the real JS
`squeezeReleaseEvents` over it. The Python matches it event-for-event, and
the chart bucket clock was also cross-checked against
`chartAggregationBucketTime` on all 8,640 bar times for 60/120/240/1440.
**Regenerate both fixture files from a real `studyBars` tape** (Step 1) when
a backend is available.

**Deviations from the plan.**
- This repo's `calculateMtfSqueezeReleaseClouds` did not yet have the
  closed-bucket guard (live `461d4af`); the extraction adds it, so the chart
  stops drawing a flame on a still-forming bucket.
- `squeezeRelease.js` imports `./chartAggregation.js` (explicit extension).
- Python aggregation also mirrors `normalizeChartCandleBars` (dedupe, sort,
  high/low repair), and `rolling_stdev` sums with plain loops rather than
  `sum()` (compensated summation on Python 3.12+ would differ in the last bit).
- Plan test errors fixed: the true-range example is `2.5`, not `3.0`; the
  4h clock asserts `08:59 -> 05:00` and `05:00 -> 05:00` (the plan said 06:00);
  the out-of-window CALL uses `05:59:59` per the spec.
- Daily fires carry the bucket's session date in `fireDates["D"]` (e.g.
  `2026-10-23`) rather than the Saturday-midnight close time.
- **Task 4 Step 1 (warmer 08:00 -> 06:00) not done.** Here
  `_is_oi_finder_mag7_live_session` gates only the option-chain snapshot
  collector, which never builds chart tapes, and `QUICK_STRIP_WARM_SYMBOLS`
  is defined but unused (there is no background chart warmer). Moving it
  would add broker load without warming anything. Chart tapes here are warm
  only after a chart is opened (or loaded from the disk cache), so expect
  cold symbols under `pendingSymbols`.
- Live tail subscription uses `MARKET_STREAM.watch(symbol)` once per process,
  not `self.client.ensure_streaming` (in this repo `self.client` is Alpaca and
  does not feed `MARKET_STREAM.chart_history`).
- The table sits in the **OI Scanner** view directly above the 4H premarket
  table (the default view; the `Scanner` view is hidden by default here).
  It polls only while that view is open. There is no
  `mobileOverflowNavigation.js` in this repo, so no mobile "More" entry.
- Fire badges and the row key live in `frontend/src/premarketScanner.js`.

**Verified.** Backend started with dummy creds and a scratch database:
`/api/premarket-scanner` returns the full shape, `status: WARMING`, all nine
symbols pending (no chart cache), and a new `generatedAt` on every call.
The Vite dev server rendered the table in Chromium (Playwright) with no
console errors, both against the real empty backend and with a mocked
three-row payload.

**Not verified.** A non-empty `readySymbols` from a warm cache, and the
live-window parity check against the 5m chart; both need a real market
morning with broker data. Worth a look during that check: with default
chart options the flame cutoff is the latest session's first bar at or after
05:00 ET, so a 2h 04:00-06:00 release (closes 06:00) or a prior-day daily
release may be counted by the scanner but not drawn on the chart.
