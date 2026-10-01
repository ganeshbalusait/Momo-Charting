# Chart API Latency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop `/api/oi-finder-chart` from intermittently taking 20–48s, which makes Cloudflare return a 5xx HTML page and the phone show "API is unavailable right now (server error)".

**Architecture:** Three independent CPU reductions on the request path and the background path. The dominant cause is the watchlist chart warmer, which yields to an active session for at most 25s and then runs an expensive full build anyway, starving request threads through the GIL. Secondary costs are a per-leaf `pd.isna()` call during JSON serialization and a re-walk of the 6MB payload's signal list on every request.

**Tech Stack:** Python 3, `http.server.ThreadingHTTPServer`, pandas/numpy, pytest.

**Spec:** Diagnosis recorded in this plan's Background section; no separate design doc (this is a performance fix to existing behaviour, not a feature).

## Global Constraints

- Project root: `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2`.
- Python is `./.venv/Scripts/python.exe` — there is no global `python`.
- Git is **local-only**. Both remotes were deliberately removed and must NOT be re-added.
- Commit with **explicit paths**. `.venv` is tracked, so `git add -A` sweeps in thousands of `.pyc` files.
- Never run checkout-touching git commands while `api_server.py` is running.
- Do **not** bulk-hit `/api/oi-finder-chart` while testing; single spaced requests only.
- All three tasks must preserve existing response shapes exactly. This is a latency fix, not a contract change.

## Re-measured at the 2026-08-13 open (09:45-09:55 ET)

The trader's report was "charts are not loading fast - META at market open
takes too much time to load the current candle". Measured against the live
backend, single spaced requests:

- `?symbol=META&initial=true` (the seed that paints the first candles):
  **1.49 / 0.85 / 0.71 / 0.84 / 0.53s** for 129KB. The seed is not the problem.
- `?symbol=META&deep=true` (the deferred full indicator tape): **11.4s for
  4.13MB.** This is what the trader feels: the pane paints a thin seed quickly
  and then waits ~11s for the real tape and its studies.
- Backend CPU climbed 729 -> 3466 CPU-seconds between 09:00 and 09:55 ET, i.e.
  **~83% of one core sustained in background work** while the session was
  active. That is the GIL pressure Task 2 targets.

So the ordering of this plan still holds, but the payoff is concentrated in the
deep-tape response: Task 1 (`_serialize_value`, 1.45s CPU) and Task 3 (signal
re-walk) both bill against that 4.13MB payload, and Task 2 stops the warmer
from competing with it. Re-measure `?deep=true` as the headline number.

## Background (measured, 2026-08-13)

- `/api/oi-finder-chart?symbol=AAPL&initial=true`, single requests spaced 15s apart, via curl: **1.8s / 47.9s / 21.6s / 7.8s** for a 133KB response.
- `historyReady: true`, `refreshing: false` — so this is not a history rebuild.
- `_send_json` = `json.dumps(_serialize_value(payload))` costs **1.91s CPU** on the real 6.19MB AAPL payload (359,076 scalar leaves); `_serialize_value` alone is 1.45s, versus 0.46s for a plain dump.
- `_watchlist_chart_warmer_loop` (`api_server.py:9231`) waits for `oi_finder_interactive_until` **but caps the wait at 25s**, then calls `_build_oi_finder_chart_payload(symbol, fast_start=False)` regardless, then sleeps 10s. During an active session that is one heavy build every ~35s.

---

### Task 1: Scalar fast path in `_serialize_value`

`_serialize_value` recurses to a `pd.isna(value)` call for every scalar leaf. `pd.isna` is a dispatching pandas call; at 359k leaves it dominates serialization. Plain `str`/`bool`/`int`/`None` can never be NA and can return immediately. `float` needs a NaN check, but `value != value` is an inline C-level comparison rather than a pandas dispatch.

**Files:**
- Modify: `api_server.py` (the module-level `def _serialize_value(value)`)
- Test: `tests/test_serialize_value_fast_path.py` (create)

**Interfaces:**
- Consumes: nothing from other tasks.
- Produces: no signature change. `_serialize_value(value) -> object` behaves identically.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from api_server import _serialize_value


def test_plain_scalars_pass_through():
    assert _serialize_value("AAPL") == "AAPL"
    assert _serialize_value(7) == 7
    assert _serialize_value(True) is True
    assert _serialize_value(None) is None
    assert _serialize_value(301.40) == 301.40


def test_float_nan_and_inf_become_none():
    # pd.isna(float("nan")) is True, so the old path returned None. Keep that.
    assert _serialize_value(float("nan")) is None
    # pd.isna(inf) is False, so inf must survive as-is.
    assert _serialize_value(float("inf")) == float("inf")


def test_numpy_and_pandas_values_still_convert():
    assert _serialize_value(np.int64(5)) == 5
    assert _serialize_value(np.float64(1.5)) == 1.5
    assert _serialize_value(pd.NaT) is None
    assert _serialize_value(pd.NA) is None


def test_nested_structures_still_recurse():
    payload = {"bars": [{"time": 1, "close": float("nan")}, {"time": 2, "close": 3.5}]}
    assert _serialize_value(payload) == {
        "bars": [{"time": 1, "close": None}, {"time": 2, "close": 3.5}]
    }


def test_bool_is_not_treated_as_int_shortcut():
    # bool is a subclass of int; make sure the fast path keeps it a bool.
    result = _serialize_value(False)
    assert result is False
    assert isinstance(result, bool)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_serialize_value_fast_path.py -v`

Expected: the file imports fine and most assertions pass against the *current* implementation — this test is a **characterization test** that locks in today's behaviour before the optimization. If any assertion fails now, the current behaviour differs from what this plan assumes: stop and report it rather than "fixing" the test.

- [ ] **Step 3: Add the fast path**

Find the module-level `_serialize_value` and insert the scalar checks immediately after the `tuple` branch and before the `datetime`/`np.generic` branches:

```python
def _serialize_value(value):
    if isinstance(value, pd.DataFrame):
        return _frame_records(value)
    if isinstance(value, dict):
        return {key: _serialize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize_value(item) for item in value]
    # Fast path: these are the overwhelming majority of leaves in a chart
    # payload (~359k of them for one symbol) and none of them can be NA, so
    # they must not pay for a pd.isna() dispatch. bool is checked before int
    # because bool is an int subclass and must stay a bool.
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        # NaN is the only float pd.isna() calls NA; inf is not. `!=` is an
        # inline comparison rather than a pandas dispatch.
        return None if value != value else value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    if pd.isna(value):
        return None
    return value
```

- [ ] **Step 4: Run the test to verify it still passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_serialize_value_fast_path.py -v`

Expected: PASS — identical behaviour, fewer pandas calls.

- [ ] **Step 5: Run the full python test suite for regressions**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`

Expected: no new failures versus the pre-change baseline. Record the baseline first if you have not already.

- [ ] **Step 6: Measure the improvement**

Run this against the real cached payload (read-only, touches nothing the server owns):

```bash
./.venv/Scripts/python.exe - <<'PY'
import gzip, json, time
from api_server import _serialize_value
p = r"artifacts\oi_chart_cache\AAPL.json.gz"
with gzip.open(p, "rt", encoding="utf-8") as f:
    payload = json.load(f)
t = time.perf_counter(); _serialize_value(payload); print(f"_serialize_value: {time.perf_counter()-t:.2f}s")
PY
```

Expected: well under the 1.45s baseline. Report the actual number; do not claim a factor you did not measure.

- [ ] **Step 7: Commit**

```bash
git add api_server.py tests/test_serialize_value_fast_path.py
git commit -m "perf(api): skip pd.isna dispatch for plain scalars when serializing"
```

---

### Task 2: The watchlist warmer defers to an active session

This is the task that fixes the reported symptom. The warmer currently caps its yield at 25s and then builds regardless, so an active session eats a heavy build every ~35s.

The existing cap exists for a real reason, recorded in the code comment: an unbounded wait meant "only ~8 symbols/hour built while the trader was active". So the fix must not reintroduce unbounded starvation. The approach: keep a cap, but make it much longer, and guarantee forward progress with a floor — at least one build every `WARMER_FORCE_PROGRESS_SECONDS` no matter how busy the session is.

**Files:**
- Modify: `api_server.py` — `_watchlist_chart_warmer_loop` (currently around line 9231; **re-read the region immediately before editing**, a concurrent session may have shifted it)
- Test: `tests/test_watchlist_warmer_backoff.py` (create)

**Interfaces:**
- Consumes: `self.oi_finder_interactive_until` (float, `time.monotonic()` deadline), already set by `touch_oi_finder_interactive_window()`.
- Produces: two new class constants on `DashboardState`:
  - `WARMER_INTERACTIVE_WAIT_SECONDS: float = 180.0`
  - `WARMER_FORCE_PROGRESS_SECONDS: float = 900.0`
  and one new method `_warmer_should_wait(self, now: float, last_build_at: float) -> bool`.

- [ ] **Step 1: Write the failing test**

The decision is extracted into a pure predicate so it can be tested without threads or sleeps.

```python
from __future__ import annotations

from api_server import DashboardState


class _Stub(DashboardState):
    def __init__(self):  # bypass the real, expensive __init__
        self.oi_finder_interactive_until = 0.0


def test_waits_while_a_session_is_active():
    state = _Stub()
    state.oi_finder_interactive_until = 1000.0
    # Session active, and we built recently -> wait.
    assert state._warmer_should_wait(now=900.0, last_build_at=880.0) is True


def test_does_not_wait_when_no_session_is_active():
    state = _Stub()
    state.oi_finder_interactive_until = 0.0
    assert state._warmer_should_wait(now=900.0, last_build_at=880.0) is False


def test_forces_progress_even_during_a_long_session():
    state = _Stub()
    state.oi_finder_interactive_until = 10_000.0
    # Session still active, but nothing has been built for longer than the
    # force-progress floor -> build anyway, so the warmer cannot be starved.
    stale = 900.0 + DashboardState.WARMER_FORCE_PROGRESS_SECONDS + 1
    assert state._warmer_should_wait(now=stale, last_build_at=900.0) is False


def test_never_built_yet_does_not_force_immediately():
    state = _Stub()
    state.oi_finder_interactive_until = 10_000.0
    # last_build_at == 0 means "no build yet this loop"; treat `now` as the
    # reference so a fresh boot during an active session still defers.
    assert state._warmer_should_wait(now=100.0, last_build_at=100.0) is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_watchlist_warmer_backoff.py -v`

Expected: FAIL with `AttributeError: 'DashboardState' object has no attribute '_warmer_should_wait'`.

- [ ] **Step 3: Add the constants and the predicate**

Add the two constants alongside the other `DashboardState` class constants, and the predicate as a method:

```python
    # The warmer used to yield to an active session for only 25s and then
    # build regardless. One full build is CPU-heavy enough to starve request
    # threads through the GIL, which showed up as 20-48s chart responses and
    # Cloudflare 5xx on phones. Wait far longer now, but keep a floor so a
    # continuously-used app cannot stop the warmer entirely (the unbounded
    # wait this replaced managed only ~8 symbols/hour while the trader worked).
    WARMER_INTERACTIVE_WAIT_SECONDS: float = 180.0
    WARMER_FORCE_PROGRESS_SECONDS: float = 900.0

    def _warmer_should_wait(self, now: float, last_build_at: float) -> bool:
        """True when the warmer should hold off on its next expensive build."""
        interactive = now < float(getattr(self, "oi_finder_interactive_until", 0.0))
        if not interactive:
            return False
        # Guarantee forward progress: however busy the app is, build something
        # once every WARMER_FORCE_PROGRESS_SECONDS.
        return (now - float(last_build_at)) < self.WARMER_FORCE_PROGRESS_SECONDS
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_watchlist_warmer_backoff.py -v`

Expected: PASS.

- [ ] **Step 5: Use the predicate in the loop**

Re-read the loop first. Replace the existing bounded wait:

```python
                    waited = 0.0
                    while (
                        time.monotonic() < float(getattr(self, "oi_finder_interactive_until", 0.0))
                        and waited < 25.0
                    ):
                        time.sleep(5)
                        waited += 5.0
```

with a wait driven by the predicate:

```python
                    waited = 0.0
                    while (
                        self._warmer_should_wait(time.monotonic(), last_build_at)
                        and waited < self.WARMER_INTERACTIVE_WAIT_SECONDS
                    ):
                        time.sleep(5)
                        waited += 5.0
```

Initialise `last_build_at` once before the `for symbol in ordered:` loop:

```python
                last_build_at = time.monotonic()
```

and record a build right after a successful one, immediately following the
`self._save_oi_finder_chart_disk_payload(symbol, payload)` call site:

```python
                    last_build_at = time.monotonic()
```

Set it in the `except Exception: pass` path too, so a symbol that reliably
throws cannot spin the loop.

- [ ] **Step 6: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add api_server.py tests/test_watchlist_warmer_backoff.py
git commit -m "perf(api): let the chart warmer defer to an active session

It yielded for at most 25s and then ran a full build anyway, so an
active session ate a CPU-heavy build every ~35s. Through the GIL that
showed up as 20-48s chart responses and Cloudflare 5xx on phones.
Waits up to 3min now, with a 15min floor so it still makes progress."
```

---

### Task 3: Memoize the serve-time signal windowing

`_windowed_ganesh_chart_payload` rebuilds a filtered signal list on **every** request, walking the cached payload's full signal array even when the response is the 133KB slim variant. The result depends only on the cached payload, so it can be computed once per cache entry.

**Files:**
- Modify: `api_server.py` — `oi_finder_chart_payload` (the `payload = self._windowed_ganesh_chart_payload(payload)` call site) and the cache-write sites
- Test: `tests/test_windowed_payload_memo.py` (create)

**Interfaces:**
- Consumes: `DashboardState._windowed_ganesh_chart_payload(payload) -> dict` (existing, unchanged).
- Produces: `DashboardState._windowed_payload_for_cache_entry(entry: dict) -> dict`, which returns the windowed payload for a cache entry and stores it on that entry under the key `"windowed_payload"`.

- [ ] **Step 1: Write the failing test**

```python
from __future__ import annotations

from api_server import DashboardState


class _Stub(DashboardState):
    def __init__(self):
        pass


def _entry():
    newest = 1_786_377_600
    return {
        "cached_at": 0.0,
        "payload": {
            "bars": [{"time": newest, "close": 1.0}],
            "ganeshHigherTimeframeSignals": {
                "schemaVersion": 1,
                "signals": [
                    {"time": newest - 86_400, "kind": "old"},
                    {"time": newest, "kind": "visible"},
                ],
            },
        },
    }


def test_windows_signals_to_the_visible_bar_range():
    state = _Stub()
    entry = _entry()
    result = state._windowed_payload_for_cache_entry(entry)
    signals = result["ganeshHigherTimeframeSignals"]["signals"]
    assert [row["kind"] for row in signals] == ["visible"]


def test_second_call_reuses_the_stored_result():
    state = _Stub()
    entry = _entry()
    first = state._windowed_payload_for_cache_entry(entry)
    second = state._windowed_payload_for_cache_entry(entry)
    assert first is second, "the windowed payload must be computed once per cache entry"
    assert entry["windowed_payload"] is first


def test_a_replaced_payload_recomputes():
    state = _Stub()
    entry = _entry()
    state._windowed_payload_for_cache_entry(entry)
    # Simulate a refresh landing: new payload object, stale memo cleared.
    entry["payload"] = _entry()["payload"]
    entry.pop("windowed_payload", None)
    again = state._windowed_payload_for_cache_entry(entry)
    assert again is entry["windowed_payload"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_windowed_payload_memo.py -v`

Expected: FAIL with `AttributeError: ... has no attribute '_windowed_payload_for_cache_entry'`.

- [ ] **Step 3: Implement the memo**

```python
    def _windowed_payload_for_cache_entry(self, entry: dict) -> dict:
        """Window a cache entry's signals once, not on every request.

        The windowed result depends only on the cached payload, so recomputing
        it per request walked the full signal array for every chart poll -
        including polls whose response is the 133KB slim variant.
        """
        memo = entry.get("windowed_payload")
        if memo is not None:
            return memo
        windowed = self._windowed_ganesh_chart_payload(entry.get("payload") or {})
        entry["windowed_payload"] = windowed
        return windowed
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_windowed_payload_memo.py -v`

Expected: PASS.

- [ ] **Step 5: Use the memo on the request path**

In `oi_finder_chart_payload`, the current sequence inside `if cached:` is:

```python
            payload = dict(cached["payload"])
            payload["cacheAgeSeconds"] = round(age_seconds, 2)
            payload["historyLoading"] = not history_ready
            payload["studySchemaStale"] = not self._chart_payload_has_ready_ganesh_signals(payload)
            payload["refreshing"] = refreshing
            payload = self._windowed_ganesh_chart_payload(payload)
```

Replace it with a version that windows the *cache entry* once and then layers
the per-request fields on top. The per-request fields must still be applied
after windowing so their values are unchanged:

```python
            windowed = self._windowed_payload_for_cache_entry(cached)
            payload = dict(windowed)
            payload["cacheAgeSeconds"] = round(age_seconds, 2)
            payload["historyLoading"] = not history_ready
            payload["studySchemaStale"] = not self._chart_payload_has_ready_ganesh_signals(payload)
            payload["refreshing"] = refreshing
```

- [ ] **Step 6: Invalidate the memo wherever the cache payload is replaced**

Every site that assigns `self.oi_finder_chart_cache[target] = {...}` builds a
fresh dict, so it carries no stale `windowed_payload` — no change needed there.
But any site that **mutates** an existing entry's `"payload"` in place must drop
the memo. Search for them:

```bash
grep -n 'oi_finder_chart_cache\[' api_server.py
grep -n '\["payload"\] *=' api_server.py
```

For each in-place assignment to an existing entry's `"payload"`, add
`entry.pop("windowed_payload", None)` immediately after. If the grep shows no
in-place payload mutation, record that in the commit message rather than adding
dead code.

- [ ] **Step 7: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest tests/ -q`

Expected: no new failures.

- [ ] **Step 8: Commit**

```bash
git add api_server.py tests/test_windowed_payload_memo.py
git commit -m "perf(api): window a chart cache entry's signals once, not per request"
```

---

### Task 4: Verify the fix end to end

**Files:** none modified.

- [ ] **Step 1: Restart the backend**

Per the environment notes, `api_server.py` runs as a supervisor+worker pair; killing either kills both. **Confirm with the user before restarting** — other sessions may be coordinating, and the restart needs ~30–60s to boot plus a few minutes of warmup churn.

```powershell
Start-Process .venv\Scripts\python.exe -ArgumentList "-u","api_server.py"
```

- [ ] **Step 2: Wait for warmup, then measure**

Do **not** bulk-hit the endpoint. Single requests, 15s apart, four of them:

```powershell
1..4 | ForEach-Object {
  & curl.exe -s -o NUL -w "%{http_code} %{time_total}s`n" --max-time 90 `
    "http://127.0.0.1:3001/api/oi-finder-chart?symbol=AAPL&initial=true"
  if ($_ -lt 4) { Start-Sleep -Seconds 15 }
}
```

Baseline to beat: **1.8s / 47.9s / 21.6s / 7.8s**.

Success criterion: no response over 5s. If any response still exceeds 10s, the
warmer is not the whole story — report the numbers and stop rather than adding
speculative fixes.

- [ ] **Step 3: Confirm on the phone**

Load app.agxtrade.com and confirm the "API is unavailable right now (server error)" banner is gone and candles render.

- [ ] **Step 4: Report actual measured numbers**

State the measured latencies. Do not claim success without them.
