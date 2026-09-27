# MomX BEAR scanner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a BEAR view of every MomX list — the bull scan, grade, momentum, strategies, chart arrows, recorder and screen mirrored for puts — without changing one byte of the bull board.

**Architecture:** One board build per list paints the columns once and scores each row twice (`direction="bull"` / `"bear"`); the worker publishes two payloads per list and serves the bear one on `?dir=bear`. Every rule module takes a `direction` argument (default `"bull"`, so all existing callers and tests are untouched). Bear recording lives under its own root (`artifacts/bear/`) so the bull track record and scorecard never see a bear event. The frontend reads `row.direction` to flip its own rules, palette and wording.

**Tech Stack:** Python 3 (worker `momx_worker.py`, package `momx/`), pytest; React/Vite frontend, `node --test`; JSON artifacts on disk.

**Spec:** `docs/superpowers/specs/2026-09-24-momx-bear-scanner-design.md` (read it first; the colour tables in §3 are the source of truth for every set below).

## Global Constraints

- Work in the LIVE repo `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2` on `main`, no worktree (peer sessions share this checkout; a `vite build --watch` serves whatever is on disk to :4173). Commit after every task.
- Python: `.venv/Scripts/python.exe -m pytest <file> -q` from the repo root. Frontend tests: `cd frontend; node --test src/<file>.test.js`. Build: `cd frontend; npm run build`.
- Every `direction` parameter defaults to `"bull"`; any value other than `"bear"` is bull. Existing tests must pass unchanged at every task.
- Bear rows/payloads carry `"direction": "bear"`; bull rows/payloads carry `"direction": "bull"`.
- Bear colour sets (from the spec): RVOL bg `{"magenta","red"}`; SKIT trend fg `{"magenta","plum"}`; SKIT cross bg `{"magenta","red","light_red"}`; SQZ fired bg `"magenta"`; H/L bg `"red"`.
- The pre-commit hook refuses any commit touching shipping code (momx/, frontend/src, scripts/, momx_worker.py) unless `frontend/public/release-notes.json` is staged with a NEW top entry stamped within 24 h (ET). Tests/docs-only commits are exempt. So: each task that ships code adds/updates one release-notes entry (see Task 1 step 6 for the shape; later tasks may edit the same top entry's `at` and items rather than adding a new one per commit).
- Never restart the worker during market hours (09:30–16:00 ET). Announce a restart to peer sessions first (memory: multi-session moratorium).
- No em-dashes in user-facing strings? Not a rule here; keep wording plain and short.

## Review Focus

1. **Bull payload byte-identical.** After Task 4/5 the served bull board must have no `bear` key on any row and the same rows in the same order. Test in Task 5 (`test_bull_snapshot_carries_no_bear_key_and_same_rows`).
2. **A bear-only new match must not reuse stale columns.** `board.build_board`'s reuse path rebuilds a row only when the BULL scan newly matches. Test in Task 4 (`test_a_newly_bear_matching_symbol_never_shows_last_cycles_columns`).
3. **Bear outcomes scored in the trade's favour.** A stock that falls 5% after a bear A+ must show `close=+5.0`, `maxFav` from the LOWEST low, `maxAdv` from the HIGHEST high. Test in Task 6.
4. **Put option plan geometry.** ENTRY strike below spot, target wall ABOVE the entry strike and BELOW spot, target hit when price falls TO the wall. Tests in Task 7.
5. **Direction switch must not show bull rows under a bear header.** The panel clears its in-memory board and the cache key includes the direction. Test in Task 13 (`boardCacheKey`) + browser smoke.

---

### Task 1: `momx/scan.py` — direction on every scan rule + ascending rank

**Files:**
- Modify: `momx/scan.py` (momentum_cross_names :323, momentum_cross :377, sqz_fired :387, rvol_scan :489, price_change_gate :241, scan_symbol :543, rank_board :677, rank_rows :725)
- Create: `tests/momx_mirror.py` (shared mirror helpers)
- Test: `tests/test_momx_scan.py`

**Interfaces:**
- Consumes: `ganesh_higher_timeframe_signals._crossed_below(prev_fast, prev_slow, fast, slow)` (exists at :437).
- Produces: `scan_symbol(row_tapes, last, direction="bull") -> (bool, list[str])`; `momentum_cross_names(bars, direction="bull")`; `sqz_fired(bars, direction="bull")`; `rvol_scan(bars, num_dev, direction="bull")`; `price_change_gate(bars, source, min_pct, bars_ago=2, direction="bull")`; `rank_board(rows, limit, ascending=False)`; `rank_rows(rows, limit, ascending=False)`; `is_bear(direction) -> bool`.

- [ ] **Step 1: Write the mirror helper**

`tests/momx_mirror.py`:
```python
"""Mirror helpers: a bear tape is a bull tape reflected in price.

p' = 2*PIVOT - p, high/low swapped, volume kept. EMAs, MACD and the squeeze
midline are linear in price, Bollinger/Keltner widths are invariant, so every
bull cross/fire on the original is the same bear cross/fire on the mirror.
Colour mirrors follow momx/columns.py's own ladders.
"""
from __future__ import annotations

PIVOT = 1000.0

SKIT_BG = {"cyan": "magenta", "green": "red", "lime": "light_red", "dark_green": "plum",
           "magenta": "cyan", "red": "green", "light_red": "lime", "plum": "dark_green"}
SKIT_FG = {"cyan": "magenta", "dark_green": "plum", "downtick": "violet",
           "magenta": "cyan", "plum": "dark_green", "violet": "downtick"}
RVOL_BG = {"cyan": "magenta", "green": "red", "magenta": "cyan", "red": "green"}
RVOL_FG = {"cyan": "magenta", "green": "red", "dark_green": "dark_red",
           "magenta": "cyan", "red": "green", "dark_red": "dark_green"}
SQZ_BG = {"cyan": "magenta", "magenta": "cyan"}
HL_BG = {"green": "red", "red": "green"}


def mirror_price(value, pivot: float = PIVOT):
    return None if value is None else 2 * pivot - float(value)


def mirror_bars(bars, pivot: float = PIVOT) -> list[dict]:
    out = []
    for b in bars:
        c = dict(b)
        c["open"] = 2 * pivot - float(b["open"])
        c["close"] = 2 * pivot - float(b["close"])
        c["high"] = 2 * pivot - float(b["low"])
        c["low"] = 2 * pivot - float(b["high"])
        out.append(c)
    return out


def mirror_tapes(tapes: dict, pivot: float = PIVOT) -> dict:
    return {key: mirror_bars(bars, pivot) for key, bars in tapes.items()}


def _cell(cell: dict, bg_map: dict, fg_map: dict) -> dict:
    if not isinstance(cell, dict):
        return cell
    c = dict(cell)
    if "bg" in c:
        c["bg"] = bg_map.get(c["bg"], c["bg"])
    if "fg" in c:
        c["fg"] = fg_map.get(c["fg"], c["fg"])
    return c


def mirror_row(row: dict) -> dict:
    """The bear twin of a graded board row (colours only; prices untouched)."""
    r = dict(row)
    for section, bg_map, fg_map in (("skittles", SKIT_BG, SKIT_FG), ("rvol", RVOL_BG, RVOL_FG), ("sqz", SQZ_BG, {})):
        part = row.get(section)
        if isinstance(part, dict):
            r[section] = {tf: _cell(cell, bg_map, fg_map) for tf, cell in part.items()}
    if isinstance(row.get("highLow"), dict):
        r["highLow"] = _cell(row["highLow"], HL_BG, {})
    return r
```

- [ ] **Step 2: Write the failing scan tests**

Append to `tests/test_momx_scan.py`:
```python
from momx_mirror import mirror_bars, mirror_tapes, mirror_price, PIVOT  # tests/ is on sys.path via conftest


def test_bear_momentum_cross_is_the_mirror_of_the_bull_cross():
    closes = [100.0 - i * 0.5 for i in range(30)] + [85.0 + i * 2.0 for i in range(1, 6)]
    bars = _bars_from_closes(closes)
    assert scan.momentum_cross_names(bars)  # bull fires on the rise
    assert scan.momentum_cross_names(mirror_bars(bars), direction="bear") == scan.momentum_cross_names(bars)
    assert scan.momentum_cross_names(bars, direction="bear") == []


def test_bear_rvol_scan_wants_sellers():
    bars = _bars_from_closes([100.0] * 60, volume=1000.0)
    spike = dict(bars[-1], volume=100_000.0, high=101.0, low=99.0, close=99.2)  # closed near the low
    bars[-1] = spike
    assert scan.rvol_scan(bars, 3.0) is False
    assert scan.rvol_scan(bars, 3.0, direction="bear") is True


def test_bear_close_gate_is_a_drop():
    bars = _bars_from_closes([100.0, 100.0, 99.5])
    assert scan.price_change_gate(bars, "close", 0.3, 2) is False
    assert scan.price_change_gate(bars, "close", 0.3, 2, direction="bear") is True


def test_scan_symbol_bear_mirrors_bull_on_a_reflected_tape():
    """The whole scan, reflected: same reasons, same verdict (volume gate is neutral)."""
    closes = [100.0 - i * 0.5 for i in range(30)] + [85.0 + i * 2.0 for i in range(1, 6)]
    tapes = {"2h": _bars_from_closes(closes), "4h": _bars_from_closes([100.0 + i for i in range(40)], volume=5000.0),
             "1h": _bars_from_closes([100.0, 100.0, 101.0])}
    bull = scan.scan_symbol(tapes, 90.0)
    bear = scan.scan_symbol(mirror_tapes(tapes), mirror_price(90.0), direction="bear")
    assert bull[0] is True and bear == bull


def test_rank_rows_ascending_puts_the_biggest_loser_first():
    rows = [{"symbol": "A", "pctChange": -1.0}, {"symbol": "B", "pctChange": -8.0}, {"symbol": "C", "pctChange": None}]
    assert [r["symbol"] for r in scan.rank_rows(rows, None, ascending=True)] == ["B", "A", "C"]
    assert [r["symbol"] for r in scan.rank_board(
        [dict(r, scanPass=r["symbol"] == "A") for r in rows], 2, ascending=True)] == ["A", "B"]
```
Note `_bars_from_closes` builds hourly bars; if the 4h volume gate needs `bars_ago=3` positive growth, the 4h tape above uses rising volume only through the constant 5000 — adjust to `volume` growing per bar (`[dict(b, volume=5000 + i) for i, b in enumerate(...)]`) if the gate blocks; the assertion is on the *equality* of the two verdicts and on `bull[0] is True`.

- [ ] **Step 3: Run to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_scan.py -q -k "bear or ascending"`
Expected: FAIL with `TypeError: ... unexpected keyword argument 'direction'`.

- [ ] **Step 4: Implement**

In `momx/scan.py`:
```python
from ganesh_higher_timeframe_signals import (
    _crossed_above as _ghts_crossed_above,
    _crossed_below as _ghts_crossed_below,
    _source_values as _ghts_source_values,
)

def is_bear(direction: Any) -> bool:
    """The one place the word is spelled. Anything but "bear" is bull."""
    return str(direction or "").lower() == "bear"
```
`price_change_gate(..., direction="bull")`: after computing `pct`, `return pct <= -float(min_pct) if is_bear(direction) else pct >= float(min_pct)` (keep the zero-baseline False path).

`momentum_cross_names(bars, direction="bull")`: `crossed = _ghts_crossed_below if is_bear(direction) else _ghts_crossed_above` and use `crossed(...)` for the three pairs. `momentum_cross(bars, direction="bull")` forwards.

`sqz_fired(bars, direction="bull")`: `bear = is_bear(direction)`; replace
`higher = index >= 1 and highs[index] > closes[index - 1]` with
```python
        higher = index >= 1 and (
            lows[index] < closes[index - 1] if bear else highs[index] > closes[index - 1]
        )
```
and the two `_rising` expressions with `(histogram[index] < histogram[index-1]) if bear else (histogram[index] > histogram[index-1])`.

`rvol_scan(bars, num_dev=RVOL_NUM_DEV, direction="bull")`: last line `return selling > buying if is_bear(direction) else buying > selling`.

`scan_symbol(row_tapes, last, direction="bull")`: pass `direction=direction` to the 1h close gate (NOT the 4h volume gate), to `momentum_cross_names`, `sqz_fired`, `rvol_scan`.

`rank_rows(rows, limit=RANK_LIMIT, ascending=False)`: sort key `(0, pct) if ascending else (0, -pct)`. `rank_board(rows, limit=RANK_LIMIT, ascending=False)`: forward `ascending` to every `rank_rows` call.

Add `"is_bear"` to `__all__`.

- [ ] **Step 5: Run the whole scan file**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_scan.py -q`
Expected: all pass (53 old + 5 new).

- [ ] **Step 6: Release-notes entry + commit**

Add at the TOP of `frontend/public/release-notes.json` `releases`:
```json
{
  "at": "<now, ET, e.g. 2026-09-25T01:10:00-04:00>",
  "heading": "BEAR scanner: the bull scan mirrored for puts",
  "items": [
    "The scanner toolbar's BULL label is now a BULL / BEAR switch. Bear mode shows the same lists scored for the downside: crosses below, squeezes firing down, selling volume, biggest losers first.",
    "Bear A+/A/B grade, momentum (Breakdown / Building down / Extended down), Best setups (GO = gap down and go, OPT = 2h+4h cross down) and Strategy G with PUT plans.",
    "Bear results are recorded separately (its own track record starts today) so the bull numbers are unchanged. Chart: red A+/A circles on PUT labels."
  ]
}
```
Then:
```bash
git add momx/scan.py tests/momx_mirror.py tests/test_momx_scan.py frontend/public/release-notes.json
git commit -m "feat(momx): scan rules take a direction - the bull scan mirrored for bear"
```
(Later tasks re-stage the same notes file; if the hook complains the entry is older than 24 h, bump `at`.)

---

### Task 2: `momx/grade.py` — bear grade

**Files:**
- Modify: `momx/grade.py`
- Test: `tests/test_momx_grade.py`

**Interfaces:**
- Produces: `grade_row(row, now, direction="bull")`, `safe_grade_row(row, now, direction="bull")`, and the exported sets `RVOL_BEAR_BG`, `SKIT_BEAR_TREND_FG`, `SKIT_BEAR_CROSS_BG`, `SQZ_FIRED_BG = {"bull": "cyan", "bear": "magenta"}`, `HL_BG = {"bull": "green", "bear": "red"}`, plus `sets_for(direction) -> dict` (keys `rvol_bg`, `skit_fg`, `skit_bg`, `sqz_fired`, `hl_bg`).

- [ ] **Step 1: Failing tests**

Append to `tests/test_momx_grade.py`:
```python
from momx_mirror import mirror_row


def test_every_fixture_grades_the_same_letter_on_its_bear_mirror():
    for label, item in FIX.items():
        bull = grade.grade_row(item["row"], datetime.fromisoformat(item["at"]))
        bear = grade.grade_row(mirror_row(item["row"]), datetime.fromisoformat(item["at"]), direction="bear")
        assert bear["letter"] == bull["letter"], label
        assert bear["checks"]["skit"] == bull["checks"]["skit"], label
        assert bear["checks"]["aboveMid"] is False or bull["checks"]["aboveMid"] is True


def test_bear_grade_ignores_bull_colours_and_reads_bg_plum_as_no_cross():
    item = FIX["META_0933"]
    bull_row = item["row"]
    at = datetime.fromisoformat(item["at"])
    assert grade.grade_row(bull_row, at, direction="bear")["letter"] is None
    r = mirror_row(bull_row)
    r["skittles"]["4h"] = {"value": 20, "bg": "plum", "fg": "violet"}   # MACD down against the EMA state
    assert "4h" not in grade.grade_row(r, at, direction="bear")["checks"]["skitBullish"]


def test_bear_reasons_say_below_midpoint_and_carry_direction():
    item = FIX["META_0933"]
    g = grade.grade_row(mirror_row(item["row"]), datetime.fromisoformat(item["at"]), direction="bear")
    assert "below 16h midpoint" in g["reasons"]
    assert g["direction"] == "bear"
    assert grade.grade_row(item["row"], datetime.fromisoformat(item["at"]))["direction"] == "bull"
```

- [ ] **Step 2: Run, expect TypeError on `direction`**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_grade.py -q -k bear`

- [ ] **Step 3: Implement**

In `momx/grade.py` add after the bull sets:
```python
RVOL_BEAR_BG = frozenset({"magenta", "red"})
SKIT_BEAR_TREND_FG = frozenset({"magenta", "plum"})     # fg plum = EMA9 < EMA20 and FastD <= 10
SKIT_BEAR_CROSS_BG = frozenset({"magenta", "red", "light_red"})  # bg plum excluded (cross against EMA state)
SQZ_FIRED_BG = {"bull": "cyan", "bear": "magenta"}
HL_BG = {"bull": "green", "bear": "red"}


def is_bear(direction) -> bool:
    return str(direction or "").lower() == "bear"


def sets_for(direction="bull") -> dict:
    if is_bear(direction):
        return {"rvol_bg": RVOL_BEAR_BG, "skit_fg": SKIT_BEAR_TREND_FG, "skit_bg": SKIT_BEAR_CROSS_BG,
                "sqz_fired": "magenta", "hl_bg": "red", "direction": "bear"}
    return {"rvol_bg": RVOL_BULL_BG, "skit_fg": SKIT_TREND_FG, "skit_bg": SKIT_CROSS_BG,
            "sqz_fired": "cyan", "hl_bg": "green", "direction": "bull"}
```
In `grade_row(row, now, direction="bull")`: `S = sets_for(direction)`; `fired = [... == S["sqz_fired"]]`; `push_rvol = [... in S["rvol_bg"]]`; `is_trend = cell.get("fg") in S["skit_fg"]`; `is_cross = cell.get("bg") in S["skit_bg"]`; `above_mid = hl.get("bg") == S["hl_bg"]` (variable keeps its name; for bear it means "below midpoint"). Reasons: `reasons.append(("below 16h midpoint" if above_mid else "above 16h midpoint") if is_bear(direction) else ("above 16h midpoint" if above_mid else "below 16h midpoint"))`. Add `"direction": S["direction"]` to the returned dict. `safe_grade_row(row, now, direction="bull")` forwards.

- [ ] **Step 4: Run the grade file, then the scan file (unchanged)**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_grade.py tests/test_momx_scan.py -q` — all pass.

- [ ] **Step 5: Commit**
```bash
git add momx/grade.py tests/test_momx_grade.py frontend/public/release-notes.json
git commit -m "feat(momx): bear grade - Draft 2 mirrored on the bearish paints"
```

---

### Task 3: `momx/momentum.py` — bear momentum, cross down, gap-down-and-go

**Files:**
- Modify: `momx/momentum.py`
- Test: `tests/test_momx_momentum.py`

**Interfaces:**
- Produces: `summarize(bars_5m, rvol_cells, *, now_epoch=None, direction="bull")`. Both directions now emit BOTH `crossUpAt` and `crossDownAt`; `gapGo` carries `orHigh` AND `orLow`; the bear summary's `chart` uses `"above"` where bull uses `"below"`; `direction` key on the summary.

- [ ] **Step 1: Failing tests**

Append to `tests/test_momx_momentum.py`:
```python
from momx_mirror import mirror_bars, mirror_price, PIVOT

RVOL_DOWN = {"5m": {"bg": "magenta"}}


def test_bear_summary_is_the_mirror_of_the_bull_summary():
    up = [100.0 + i * 0.3 for i in range(40)]
    b = bars(up, vols=[1000] * 30 + [9000] * 10)
    bull = momentum.summarize(b, RVOL_UP, now_epoch=b[-1]["time"] + 600)
    bear = momentum.summarize(mirror_bars(b), RVOL_DOWN, now_epoch=b[-1]["time"] + 600, direction="bear")
    assert bear["direction"] == "bear" and bull["direction"] == "bull"
    assert bear["state"] == bull["state"] and bear["chart"] == bull["chart"] and bear["pattern"] == bull["pattern"]
    assert abs(bear["trigger"] - mirror_price(bull["trigger"])) < 1e-6
    assert abs(bear["ema20"] - mirror_price(bull["ema20"])) < 1e-6
    assert bear["breakoutAt"] == bull["breakoutAt"]


def test_bear_chart_state_with_no_breakdown_is_above():
    b = bars(FLAT)
    assert momentum.summarize(b, None, now_epoch=DONE, direction="bear")["chart"] == "above"
    assert momentum.summarize(b, None, now_epoch=DONE)["chart"] == "below"


def test_cross_down_at_is_reported_in_both_directions():
    up = [100.0 + i * 0.5 for i in range(30)]
    down = [up[-1] - i * 2.0 for i in range(1, 8)]
    b = bars(up + down)
    bull = momentum.summarize(b, None, now_epoch=b[-1]["time"] + 600)
    bear = momentum.summarize(b, None, now_epoch=b[-1]["time"] + 600, direction="bear")
    assert bull["crossDownAt"] == bear["crossDownAt"] is not None
    assert bull["crossUpAt"] is None


def test_bear_extended_is_two_atr_below_ema20():
    drop = [100.0] * 25 + [100.0 - i * 1.5 for i in range(1, 8)]
    b = bars(drop)
    assert momentum.summarize(b, None, now_epoch=b[-1]["time"] + 600, direction="bear")["state"] == "extended"


def test_gap_down_and_go_marks_the_first_candle_below_vwap_open_and_or_low():
    prev = bars([100.0] * 78, start=T0 - 86400)                     # yesterday's session
    pre = bars([97.0], start=T0 - 300)                              # 09:25: gapped -3%
    session = bars([97.5, 96.0, 95.0], vols=[1000, 2000, 2000])     # 09:30 candle low 97.3; 09:35 closes 96 < everything
    b = prev + pre + session
    g = momentum.summarize(b, None, now_epoch=b[-1]["time"] + 600, direction="bear")["gapGo"]
    assert g["gap"] < -2 and g["goAt"] == session[1]["time"] and "orLow" in g and "orHigh" in g
    assert momentum.summarize(b, None, now_epoch=b[-1]["time"] + 600)["gapGo"]["goAt"] is None
```

- [ ] **Step 2: Run, expect failures**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_momentum.py -q -k "bear or cross_down or gap_down"`

- [ ] **Step 3: Implement**

`momx/momentum.py`:
```python
BEAR_RVOL_BG = frozenset({"magenta", "red"})


def _is_bear(direction) -> bool:
    return str(direction or "").lower() == "bear"


def _rvol_push(cells, direction="bull") -> bool:
    wanted = BEAR_RVOL_BG if _is_bear(direction) else BUILD_RVOL_BG
    ...  # same loop, `in wanted`
```
`_pattern(completed, z, chart, ema20, direction="bull")`: bear explosive = `_position(b) <= 0.5`; bear steady: `completed[-1]["close"] >= ema20 -> None`, `rises = sum(b["high"] < a["high"] and b["low"] < a["low"] ...)`.

Replace `_last_cross_up` with `_last_cross(completed, closes, direction)` returning the newest completed bar TODAY where `e9 > e20 and e9_prev <= e20_prev` (bull) or `e9 < e20 and e9_prev >= e20_prev` (bear). In `summarize` emit `"crossUpAt": _last_cross(completed, closes, "bull")` and `"crossDownAt": _last_cross(completed, closes, "bear")`.

`_gap_go(completed, direction="bull")`: `or_high`, `or_low = float(session[0]["low"])`; bear condition `gap <= -GAP_GO_MIN_GAP` and `c < pv / vol and c < open_ and c < or_low`; return `{"gap", "open", "orHigh", "orLow", "goAt"}`.

`summarize(..., direction="bull")`: `bear = _is_bear(direction)`;
- `trigger_before(i)`: `min(b["low"] ...)` when bear;
- breakdown test: `completed[i]["close"] < level` when bear; provisional `last_price < trigger`; the no-breakout chart state `"above" if bear else "below"`; holding: `all(b["close"] <= breakout_trigger ...)` when bear;
- `rvol = _rvol_push(rvol_cells, direction)`;
- extended: `last_price < ema20 - EXTENDED_ATR * atr14` when bear;
- building: `_position(last) <= 1/3` when bear; fading: `_position(b) > 0.5` when bear;
- `"pattern": _pattern(completed, z, chart, ema20, direction)`, `"gapGo": _gap_go(completed, direction)`, `"direction": "bear" if bear else "bull"`.

- [ ] **Step 4: Run momentum + grade + scan tests** — all pass.

- [ ] **Step 5: Commit**
```bash
git add momx/momentum.py tests/test_momx_momentum.py frontend/public/release-notes.json
git commit -m "feat(momx): bear momentum - breakdown trigger, extended down, gap-down-and-go, 9x20 cross down"
```

---

### Task 4: `momx/columns.py` + `momx/board.py` — score twice, `bear_view`, `strip_bear`

**Files:**
- Modify: `momx/columns.py` (build_row :1306), `momx/board.py` (_scan_one :387, _scan_one_guarded :451, build_board _absorb/reuse/_finished :600-760, _contract_row :808)
- Test: `tests/test_momx_board.py`, `tests/test_momx_columns.py`

**Interfaces:**
- Produces on every `_contract_row`: `"direction": "bull"` and `"bear": {"scanPass": bool, "scanReasons": list, "m5": dict|None, "grade": dict|None}`; `build_board` payload gains `"direction": "bull"`.
- `board.bear_view(payload, limit=DEFAULT_LIMIT) -> dict`: a NEW payload `{generatedAt, tapeAsOf, universe, universeCount, rows, rest, errors, list?, direction:"bear"}` whose rows are shallow copies with the bear fields promoted, keys `bear, matchedSince, gradeFresh, strategy, chartSignals, solo, hotLeader, sectorRotation` removed, ranked ascending.
- `board.strip_bear(payload) -> None`: pops `bear` from every row in `rows`/`rest` in place.
- `_scan_one` returns a 9-tuple `(symbol, row, pct, passed, reasons, failure, last, bear_passed, bear_reasons)`.

- [ ] **Step 1: Failing tests**

`tests/test_momx_board.py`: add `"direction", "bear"` to `ROW_KEYS` and `"direction"` to `PAYLOAD_KEYS`; append:
```python
def test_every_row_scores_both_directions_and_the_bear_view_promotes_them():
    stub = StubFeed(
        five={"NVDA": _intraday_bars(120, minutes=5)},
        thirty={"NVDA": _intraday_bars(120, minutes=30)},
        daily={"NVDA": _daily_bars([100.0 + index for index in range(60)])},
    )
    payload = board.build_board(["NVDA"], feed_module=stub)
    (row,) = payload["rows"]
    assert row["direction"] == "bull" and payload["direction"] == "bull"
    assert set(row["bear"]) == {"scanPass", "scanReasons", "m5", "grade"}
    assert row["bear"]["m5"]["direction"] == "bear"
    bear = board.bear_view(payload)
    assert bear["direction"] == "bear" and bear["universe"] == ["NVDA"]
    (b,) = bear["rows"] + bear["rest"]
    assert b["direction"] == "bear" and "bear" not in b
    assert b["scanPass"] == row["bear"]["scanPass"] and b["m5"] is row["bear"]["m5"]
    assert b["rvol"] is row["rvol"]              # columns shared, not copied
    board.strip_bear(payload)
    assert "bear" not in payload["rows"][0]


def test_bear_view_ranks_losers_first_matches_first_and_drops_bull_book_keys():
    rows = [
        {"symbol": "UP", "pctChange": 5.0, "scanPass": True, "matchedSince": "x", "strategy": {}, "gradeFresh": {},
         "chartSignals": [], "solo": None, "hotLeader": None, "sectorRotation": None,
         "bear": {"scanPass": False, "scanReasons": [], "m5": None, "grade": None}},
        {"symbol": "DN", "pctChange": -6.0, "scanPass": False,
         "bear": {"scanPass": True, "scanReasons": ["macd:4h"], "m5": None, "grade": {"letter": "A"}}},
        {"symbol": "MID", "pctChange": -1.0, "scanPass": False,
         "bear": {"scanPass": False, "scanReasons": [], "m5": None, "grade": None}},
    ]
    payload = {"generatedAt": "t", "tapeAsOf": None, "universe": ["UP", "DN", "MID"], "universeCount": 3,
               "rows": rows[:1], "rest": rows[1:], "errors": {}, "list": "Mag7", "strategyDaily2": []}
    bear = board.bear_view(payload, limit=2)
    assert [r["symbol"] for r in bear["rows"]] == ["DN", "UP"]      # match first, then biggest loser? no: UP is +5
    assert [r["symbol"] for r in bear["rest"]] == ["MID"]
    assert bear["rows"][0]["scanReasons"] == ["macd:4h"] and bear["rows"][0]["grade"] == {"letter": "A"}
    assert not any(k in bear["rows"][1] for k in ("matchedSince", "strategy", "gradeFresh", "chartSignals", "solo", "hotLeader", "sectorRotation"))
    assert "strategyDaily2" not in bear and bear["list"] == "Mag7"
```
Fix the expectation in the second test before running: with `limit=2`, matches first (`DN`), then the remaining room filled ascending by pctChange: `MID` (−1) before `UP` (+5). So `rows == ["DN", "MID"]`, `rest == ["UP"]`. Write it that way.

Reuse test (Review Focus 2) — model on `test_a_NEWLY_matching_symbol_never_shows_last_cycles_columns` (:482): copy that test, rename `..._bear_matching_...`, and monkeypatch `scan.scan_symbol` so the BULL call returns `(False, [])` and the BEAR call returns `(True, ["macd:4h"])` for the symbol (`lambda tapes, last, direction="bull": (direction == "bear", ["macd:4h"] if direction == "bear" else [])`); assert the returned row's `rvol` is NOT the stale cached one.

`tests/test_momx_columns.py`: add
```python
def test_build_row_carries_a_bear_momentum_summary():
    # reuse the file's existing tape fixture for build_row; any 5m tape >= MIN_BARS works
    row = columns.build_row("X", tapes)  # `tapes` = the fixture the file already builds for build_row
    assert row["m5Bear"]["direction"] == "bear" and row["m5"]["direction"] == "bull"
```
(look at how the existing build_row test in that file names its tapes and reuse it.)

- [ ] **Step 2: Run, expect failures** (`KeyError: 'bear'`, unexpected tuple length).

- [ ] **Step 3: Implement**

`columns.build_row`: after `m5`, add
```python
    try:
        m5_bear = momentum.summarize(tapes.get("5m") if isinstance(tapes, Mapping) else None, rvol, direction="bear")
    except Exception:  # noqa: BLE001
        m5_bear = None
```
and `"m5Bear": m5_bear` in the returned dict.

`board._scan_one`: after `passed, reasons = scan.scan_symbol(tapes, last)` add `bear_passed, bear_reasons = scan.scan_symbol(tapes, last, direction="bear")`; both `return` statements append `, bear_passed, bear_reasons`. `_scan_one_guarded` failure tuple: `return symbol, None, None, False, (), f"...", None, False, ()`.

`build_board._absorb`: `symbol, row, pct, passed, reasons, failure, last, bear_passed, bear_reasons = result`; record adds `"bearPass": bear_passed, "_bearReasons": bear_reasons`. Reuse block: `if record["scanPass"] or record["bearPass"]:` builds fresh. `_finished`: `_contract_row(row, record["scanPass"], record["_reasons"], _badge(...), record["bearPass"], record["_bearReasons"])`. Grade loop: also `row["bear"]["grade"] = grade.safe_grade_row(row, graded_at, "bear")`. Return dict adds `"direction": "bull"`.

`_contract_row(row, passed, reasons, badge=None, bear_passed=False, bear_reasons=None)`: add
```python
        "direction": "bull",
        "bear": {
            "scanPass": bool(bear_passed),
            "scanReasons": list(bear_reasons or []),
            "m5": row.get("m5Bear"),
            "grade": None,
        },
```
New functions in `board.py`:
```python
BEAR_DROPPED_ROW_KEYS = ("bear", "matchedSince", "gradeFresh", "strategy", "chartSignals",
                         "solo", "hotLeader", "sectorRotation")
BEAR_PAYLOAD_KEYS = ("generatedAt", "tapeAsOf", "universe", "universeCount", "errors", "list")


def bear_view(payload: Mapping[str, Any], limit: int | None = DEFAULT_LIMIT) -> dict:
    """The BEAR board of a built payload: same columns, bear verdicts promoted."""
    everything = []
    for section in ("rows", "rest"):
        for row in payload.get(section) or []:
            if not isinstance(row, Mapping):
                continue
            b = row.get("bear") if isinstance(row.get("bear"), Mapping) else {}
            copy = {k: v for k, v in row.items() if k not in BEAR_DROPPED_ROW_KEYS}
            copy.update({
                "direction": "bear",
                "scanPass": bool(b.get("scanPass")),
                "scanReasons": list(b.get("scanReasons") or []),
                "m5": b.get("m5"),
                "grade": b.get("grade"),
            })
            everything.append(copy)
    rows = scan.rank_board(everything, limit, ascending=True)
    shipped = {r["symbol"] for r in rows}
    rest = scan.rank_rows([r for r in everything if r["symbol"] not in shipped], None, ascending=True)
    out = {k: payload.get(k) for k in BEAR_PAYLOAD_KEYS if k in payload}
    out.update({"rows": rows, "rest": rest, "direction": "bear"})
    return out


def strip_bear(payload: Any) -> None:
    for section in ("rows", "rest"):
        for row in (payload.get(section) or []) if isinstance(payload, Mapping) else []:
            if isinstance(row, dict):
                row.pop("bear", None)
```
`rank_board` uses `_scan_passed(row)` → reads the promoted `scanPass`, correct. `limit=None` in `bear_view` when the bull build shipped everything (`len(payload["rest"]) == 0` and the caller passed `limit=None`) — keep the parameter and let service pass the same limit it uses (DEFAULT).

- [ ] **Step 4: Run board + columns + scan + grade + momentum tests** — all pass. `test_payload_key_set_matches_the_contract_exactly` must pass with the two new keys.

- [ ] **Step 5: Commit**
```bash
git add momx/columns.py momx/board.py tests/test_momx_board.py tests/test_momx_columns.py frontend/public/release-notes.json
git commit -m "feat(momx): one build scores bull and bear - bear_view promotes the bear verdicts"
```

---

### Task 5: `momx/service.py` — publish, cache, dirs, books and snapshot per direction

**Files:**
- Modify: `momx/service.py`
- Test: `tests/test_momx_service.py`

**Interfaces:**
- Produces: `snapshot(list_name=None, direction="bull")`; `history_dir(direction="bull")`; `grade_dir(direction="bull")` (bear = root / "bear"); `_cache_file(name, direction="bull")` (`<safe>.bear.json`); `_write_disk(name, payload, direction="bull")`, `_load_disk(name, direction="bull")`; `_record_history(name, payload, direction="bull")`; `_ListState.bear`, `_ListState.bear_matched`; module books `_BEAR_GRADE_LOG`, `_BEAR_STRATEGY`, `_BEAR_CHART_SIGNALS` (constructed with `direction="bear"` — Tasks 6–8 add that kwarg; until then construct them WITHOUT the kwarg and add it in those tasks). `status()["gradeBear"]`.
- Consumes: `board.bear_view`, `board.strip_bear` (Task 4).

- [ ] **Step 1: Failing tests**

Append to `tests/test_momx_service.py` (the `StubBuilder` returns rows without `bear`; extend it: each row `{"symbol": s, "pctChange": 1.0, "scanPass": True, "bear": {"scanPass": False, "scanReasons": [], "m5": None, "grade": None}}` and a second row `{"symbol": "DN", "pctChange": -3.0, "scanPass": False, "bear": {"scanPass": True, "scanReasons": ["rvol:5m"], "m5": None, "grade": None}}`; existing tests read `rows[0]["symbol"] == wanted[0]` so keep the first row first):
```python
def test_a_build_publishes_a_bull_and_a_bear_board(builder):
    service._build_once("Mag7")
    bull = service.snapshot("Mag7")
    bear = service.snapshot("Mag7", "bear")
    assert bull["direction"] == "bull" and bear["direction"] == "bear" and bear["list"] == "Mag7"
    assert all("bear" not in r for r in bull["rows"])               # Review Focus 1
    assert [r["symbol"] for r in bear["rows"]][0] == "DN"           # the bear match leads
    assert bear["rows"][0]["scanPass"] is True and bear["rows"][0].get("matchedSince")
    assert bull["rows"][0].get("matchedSince")


def test_bear_board_survives_a_restart_from_its_own_disk_file(builder, tmp_path):
    service._build_once("Mag7")
    assert service._cache_file("Mag7", "bear").name == "Mag7.bear.json"
    service._STATES.clear()
    disk = service.snapshot("Mag7", "bear")
    assert disk["fromDisk"] is True and disk["direction"] == "bear"


def test_bear_dirs_hang_under_the_bear_root(monkeypatch, tmp_path):
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setenv("AGX_MOMX_HISTORY_DIR", str(tmp_path / "h"))
    assert service.grade_dir("bear") == tmp_path / "bear"
    assert service.history_dir("bear") == tmp_path / "h" / "bear"
    assert service.grade_dir() == tmp_path


def test_unknown_direction_is_bull(builder):
    service._build_once("Mag7")
    assert service.snapshot("Mag7", "sideways")["direction"] == "bull"
```

- [ ] **Step 2: Run, expect failures.**

- [ ] **Step 3: Implement**

- `_ListState.__slots__` += `"bear", "bear_matched"`; init `self.bear = None`, `self.bear_matched = None`.
- `_cache_file(name, direction="bull")`: `suffix = ".bear.json" if scan.is_bear(direction) else ".json"` (import `scan` from momx).
- `_write_disk(name, payload, direction="bull")`, `_load_disk(name, direction="bull")` (sets `payload["direction"] = "bear" if bear else payload.get("direction", "bull")`), `_delete_disk(name)` unlinks both files.
- `history_dir(direction="bull")` / `grade_dir(direction="bull")`: `base / "bear"` when bear.
- `_seed_matched_from_disk(name, direction)` reads the direction's file; `_apply_matched_since(name, payload, direction="bull")` uses `state.bear_matched` for bear.
- Books: `_BEAR_GRADE_LOG = grade_log.GradeLog(grade_dir("bear"))`, `_BEAR_STRATEGY = strategy.StrategyBook(grade_dir("bear"))`, `_BEAR_CHART_SIGNALS = chart_signals.ChartSignalBook(grade_dir("bear"))` (add `direction="bear"` in Tasks 6–8).
- `_reuse_kwargs`: also walk `state.bear` rows/rest and add `scanPass` symbols to `matched`.
- `_build_once` after `_SOLO.apply(...)`:
```python
    bear = board.bear_view(payload)
    board.strip_bear(payload)
    bear["list"] = name
    _apply_matched_since(name, bear, "bear")
    _BEAR_GRADE_LOG.apply(name, bear, stamped_at)
    _BEAR_STRATEGY.apply(name, bear, stamped_at)
    _BEAR_CHART_SIGNALS.apply(name, bear, stamped_at)
    with _LOCK:
        state.payload = payload
        state.bear = bear
        ...
    _write_disk(name, payload)
    _write_disk(name, bear, "bear")
    _record_history(name, payload)
    _record_history(name, bear, "bear")
```
(`_record_history(name, payload, direction="bull")` passes `directory=history_dir(direction)`.)
- `snapshot(list_name=None, direction="bull")`: `bear = scan.is_bear(direction)`; read `state.bear if bear else state.payload`; disk fallback `_load_disk(name, "bear" if bear else "bull")`; warming payload gets `"direction"`.
- `_grade_nightly`: also `_BEAR_GRADE_LOG.nightly(...)` in its own try.
- `status()`: `"gradeBear": _BEAR_GRADE_LOG.status()`.
- Every `state.payload = None` site (:1030, :1074, :1102): also `state.bear = None; state.bear_matched = None`.
- `_warming_payload(name, message)`: add `"direction": "bull"`; snapshot overrides to "bear" for the bear branch.

- [ ] **Step 4: Run service tests + the whole momx suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_service.py tests/test_momx_board.py tests/test_momx_scan.py tests/test_momx_grade.py tests/test_momx_momentum.py -q` — all pass.

- [ ] **Step 5: Commit**
```bash
git add momx/service.py tests/test_momx_service.py frontend/public/release-notes.json
git commit -m "feat(momx): the worker publishes a bear board per list under its own cache, history and grade roots"
```

---

### Task 6: `momx/grade_log.py` — bear recorder, outcomes in the trade's favour

**Files:**
- Modify: `momx/grade_log.py` (imports :168, GradeLog.__init__ :515, _row :710-760, _nightly :981, :1010, outcome_for :1116, build_record :1235, record_response :1347)
- Modify: `momx/service.py` (pass `direction="bear"` to `_BEAR_GRADE_LOG`)
- Test: `tests/test_momx_grade_log.py`

**Interfaces:**
- Produces: `GradeLog(directory, direction="bull")`; `outcome_for(event, bars, direction="bull")`; `build_record(directory, direction="bull")` → adds `"direction"`, and `byAdx` keys `bearCross` / `bearCrossRising` / `none` for bear; `record_response(directory, direction="bull")` adds `"direction"`.

- [ ] **Step 1: Failing tests**

Append to `tests/test_momx_grade_log.py`:
```python
def _bars5(prices, start=None, highs=None, lows=None):
    start = start or datetime(2026, 9, 21, 9, 30, tzinfo=ET)
    out = []
    for i, p in enumerate(prices):
        t = start + timedelta(minutes=5 * i)
        out.append({"time": int(t.timestamp()), "open": p, "high": (highs[i] if highs else p), "low": (lows[i] if lows else p), "close": p, "volume": 100})
    return out


def test_bear_outcome_is_scored_in_the_trades_favour():
    event = {"at": datetime(2026, 9, 21, 9, 32, tzinfo=ET).isoformat(), "price": 100.0}
    prices = [100.0] + [100.0 - 0.1 * i for i in range(1, 78)]        # drifts to ~92.3 by 15:55
    bars = _bars5(prices, highs=[p + 1.0 for p in prices], lows=[p - 2.0 for p in prices])
    bear = grade_log_module.outcome_for(event, bars, direction="bear")
    bull = grade_log_module.outcome_for(event, bars)
    assert bear["close"] == -bull["close"] > 0
    assert bear["maxFav"] == -bull["maxAdv"] and bear["maxAdv"] == -bull["maxFav"]
    assert bear["p15"] == -bull["p15"]


def test_bear_log_reads_bear_colours_for_freshness_and_squeeze(tmp_path):
    log = GradeLog(tmp_path, direction="bear")
    base = {"symbol": "AAA", "last": 100.0, "skittles": {"4h": {"bg": "black", "fg": "magenta"}},
            "rvol": {"5m": {"bg": "black", "value": 0.5}}, "sqz": {"4h": {"bg": "orange"}}, "news": None,
            "m5": {"chart": "above", "state": "quiet", "trigger": 99.0}, "grade": {"letter": None, "checks": {}, "reasons": []}}
    log.apply("Watchlist", {"rows": [dict(base)], "rest": []}, NOW)
    p = {"rows": [dict(base, skittles={"4h": {"bg": "magenta", "fg": "black"}},
                       rvol={"5m": {"bg": "magenta", "value": 3.2}}, sqz={"4h": {"bg": "magenta"}})], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(seconds=30))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["icons"] == ["SKIT", "RVOL", "SQZ"]
    bull = GradeLog(tmp_path / "b")
    bull.apply("Watchlist", {"rows": [dict(base)], "rest": []}, NOW)
    q = {"rows": [dict(p["rows"][0])], "rest": []}
    bull.apply("Watchlist", q, NOW + timedelta(seconds=30))
    assert q["rows"][0]["gradeFresh"]["icons"] == []


def test_bear_record_response_names_its_direction(tmp_path):
    assert grade_log_module.record_response(tmp_path, "bear")["direction"] == "bear"
    assert grade_log_module.record_response(tmp_path).get("direction", "bull") == "bull"
```

- [ ] **Step 2: Run, expect TypeErrors.**

- [ ] **Step 3: Implement**

- Import: `from momx.grade import PUSH_RVOL_TFS, SKIT_TFS, sets_for` (keep the two old names importable if anything else imports them: `RVOL_BULL_BG, SKIT_CROSS_BG` stay imported).
- `GradeLog.__init__(self, directory, direction="bull")`: `self.direction = "bear" if str(direction).lower() == "bear" else "bull"`; `self._sets = sets_for(self.direction)`.
- `_row`: `SKIT_CROSS_BG` → `self._sets["skit_bg"]`; `RVOL_BULL_BG` → `self._sets["rvol_bg"]`; `bg == "cyan"` for SQZ → `bg == self._sets["sqz_fired"]`; M5 text `"5m breakdown confirmed" if bear else "5m breakout confirmed"`.
- `_nightly` (:981): `outcome_for(event, bars[symbol], self.direction)`; (:1010) `build_record(self.directory, self.direction)`.
- `outcome_for(event, bars, direction="bull")`: compute as today into `out`; if bear: `for k in ("p5","p15","p30","p60","close"): out[k] = None if out[k] is None else round(-out[k], 3)`; then `fav, adv = out["maxFav"], out["maxAdv"]; out["maxFav"] = None if adv is None else round(-adv, 3); out["maxAdv"] = None if fav is None else round(-fav, 3)`.
- `build_record(directory, direction="bull")`: `kind = "bear" if bear else "bull"`; `crossed = [e for e in adx_events if _adx_has_cross(e, kind)]`; `byAdx = {f"{kind}Cross": ..., f"{kind}CrossRising": ..., "none": ...}`; add `"direction": kind`.
- `record_response(directory, direction="bull")`: every returned dict gets `"direction"`.
- `service.py`: `_BEAR_GRADE_LOG = grade_log.GradeLog(grade_dir("bear"), direction="bear")`.

- [ ] **Step 4: Run** `tests/test_momx_grade_log.py tests/test_momx_service.py` — pass.

- [ ] **Step 5: Commit**
```bash
git add momx/grade_log.py momx/service.py tests/test_momx_grade_log.py frontend/public/release-notes.json
git commit -m "feat(momx): bear grade recorder - bear colours, outcomes scored in the trade's favour, own record"
```

---

### Task 7: `momx/strategy_g.py` + `momx/strategy.py` — bear rules, put plans, short tracking

**Files:**
- Modify: `momx/strategy_g.py`, `momx/strategy.py`, `momx/service.py` (`_BEAR_STRATEGY` kwarg)
- Test: `tests/test_momx_strategy_g.py`, `tests/test_momx_strategy.py`

**Interfaces:**
- Produces: `strategy_g.rules(row, now, prev_rvol, direction="bull")`; `strategy_g.option_plan(chain, direction="bull")` (bear: `side="P"`, puts below spot); `strategy.evaluate(row, now, direction="bull")`; `StrategyBook(directory, chain_fetcher=None, background=True, direction="bull")`; `G_ROW_FIELDS` gains `"side"`.

- [ ] **Step 1: Failing tests**

`tests/test_momx_strategy_g.py`:
```python
def bear_row(**over):
    base = {
        "symbol": "PLTR",
        "rvol": {"30m": {"value": 3.4, "bg": "magenta"}, "1h": {"value": 1.2, "bg": "black"}},
        "sqz": {"2h": {"bg": "black"}, "4h": {"bg": "magenta"}},
        "skittles": {"2h": {"bg": "magenta", "barAt": ep("09:00")}, "4h": {"bg": "magenta", "barAt": ep("08:00")},
                     "D": {"bg": "black", "barAt": ep("00:00")}},
        "m5": {"ema20": 189.0, "vwap": 188.5, "crossUpAt": None, "crossDownAt": None,
               "lastCompleted": {"close": 186.0, "time": ep("09:50")}},
    }
    base.update(over)
    return base


def test_bear_rules_pass_on_the_mirrored_row():
    v = g.rules(bear_row(), at("09:55"), {"30m": 3.0}, direction="bear")
    assert v["pass"] and v["rising"] == ["30m"]
    assert not g.rules(bear_row(), at("09:55"), {"30m": 3.0})["pass"]          # bull reading fails it
    assert not g.rules(row(), at("09:55"), {"30m": 3.0}, direction="bear")["pass"]


def test_bear_rule_4_accepts_a_9x20_cross_down_in_30_minutes():
    r = bear_row(m5={"ema20": 180.0, "vwap": 180.0, "crossUpAt": None, "crossDownAt": ep("09:40"),
                     "lastCompleted": {"close": 190.0, "time": ep("09:50")}})
    assert g.rules(r, at("09:55"), {"30m": 3.0}, direction="bear")["r4"]


def put(strike, delta, bid, ask, oi, expiry="2026-09-25", dte=2):
    return {"side": "PUT", "strike": strike, "delta": -delta, "bid": bid, "ask": ask, "open_interest": oi,
            "expiry": expiry, "days_to_expiration": dte, "symbol": f"PLTR{expiry}P{strike}", "gamma": 0.01}


def test_bear_option_plan_picks_a_put_below_spot_and_a_wall_between():
    chain = {"underlyingPrice": 190.0, "selectedExpiryChainRows": [
        put(185, 0.30, 2.0, 2.2, 5000), put(180, 0.18, 1.0, 1.1, 12000), put(175, 0.10, 0.4, 0.5, 20000),
        put(170, 0.05, 0.2, 0.3, 1000),
        {"side": "CALL", "strike": 195, "delta": 0.3, "bid": 1, "ask": 1.2, "open_interest": 99999,
         "expiry": "2026-09-25", "days_to_expiration": 2, "symbol": "C195"}]}
    plan = g.option_plan(chain, direction="bear")
    assert plan["ok"] and plan["side"] == "P"
    assert plan["entryStrike"] == 175.0           # |delta| closest under 0.20 ... 0.18 is 180; 0.10 is 175 -> 180 wins
```
Fix before running: the ENTRY sort key is `abs(0.20 - |delta|)` so 180 (0.18) wins over 175 (0.10). Then the target = nearest strong put wall (OI ≥ 66% of the max put OI below spot = 20000·0.66 = 13200) with strike ABOVE the entry strike 180 and below spot: 185 has 5000 (weak), so no strong wall between → `pass False`, `why` mentions "no strong put wall". Write the assertions as: `plan["entryStrike"] == 180.0`; `plan["pass"] is False and "put wall" in plan["why"]`. Add a second chain where `put(185, 0.30, 2.0, 2.2, 15000)` → `plan["target"] == 185.0`, `plan["roi"] == round((2.1 - 1.05) / 1.05 * 100, 1)`, `plan["pass"] is True` (ROI 100% < 150 → False! make the 185 put priced `bid 2.6 ask 2.7` → mid 2.65 → ROI 152.4% → pass True).

`tests/test_momx_strategy.py`:
```python
def test_bear_v2_wants_down_less_than_ten_percent():
    r = row(pct=-4.0, signal=at("09:40"))
    assert strategy.evaluate(r, at("09:40"), direction="bear")["v2"] is True
    assert strategy.evaluate(row(pct=-12.0, signal=at("09:40")), at("09:40"), direction="bear")["v2"] is False
    assert strategy.evaluate(r, at("09:40"))["v2"] is True   # bull: -4% is "up less than 10"


def test_bear_book_marks_the_target_when_price_falls_to_the_wall(tmp_path):
    # Build a StrategyBook(direction="bear", background=False) with a chain_fetcher returning the passing
    # put chain from test_momx_strategy_g; apply a bear G row at 09:55, then a row with last=184.0 at 10:00
    # and assert row["strategy"]["g"]["targetHit"] is True and ["side"] == "P".
```
Write the second test fully by copying the file's existing Strategy G book test (search `def test_g_` in `tests/test_momx_strategy.py`) and flipping: row skittles/rvol/sqz to the bear colours, `m5` close below ema20/vwap, chain = the passing put chain, and the "target hit" row at `last=184.0` (≤ 185).

- [ ] **Step 2: Run, expect failures.**

- [ ] **Step 3: Implement**

`strategy_g.py`:
```python
SQZ_OK_BEAR = frozenset({"magenta", "black", "", None})


def _is_bear(direction) -> bool:
    return str(direction or "").lower() == "bear"


def rules(row, now, prev_rvol, direction="bull"):
    bear = _is_bear(direction)
    fired = "magenta" if bear else "cyan"
    ok_set = SQZ_OK_BEAR if bear else SQZ_OK
    # r1: bg == fired ; r2: in ok_set ; r3: bg == fired for 2h and 4h/D
    # r4:
    if bear:
        above = close is not None and e20 is not None and vwap is not None and close < e20 and close < vwap
        cross = _num(m5.get("crossDownAt"))
    ...
    why texts: "RVOL: no magenta 30m-D reading rising", "5m: not below EMA20 + VWAP and no 9x20 cross down in 30 min"
```
`_gamma_roi(spot, strike, entry, target, delta, gamma, direction="bull")`: `move = (spot - target) if bear else (target - spot)`; intrinsic `max(strike - target, 0.0)` for bear.

`option_plan(chain, direction="bull")`: bear branch:
```python
        side = "PUT" if bear else "CALL"
        legs = [r for r in rows if ... r.get("side") == side ...]
        expiry = min(...)
        beyond = [r for r in legs if (_num(r["strike"]) < spot if bear else _num(r["strike"]) > spot)]
        if not beyond: return {"ok": False, "why": "no strikes below the price" if bear else "no strikes above the price"}
        otm = ... same delta test ...
        entry = sorted(otm, key=...)[0]
        biggest = max(OI over beyond); strong = sorted(..., key=strike, reverse=bear)   # nearest to spot first
        target_row = next((r for r in strong if (_num(r["strike"]) > entry_strike if bear else _num(r["strike"]) < entry_strike)), None)
        plan["side"] = "P" if bear else "C"
        why: "no strong put wall between the price and the ENTRY strike"
```
`strategy.py`: `evaluate(row, now, direction="bull")`: `pct > -MAX_UP_PCT if bear else pct < MAX_UP_PCT`. `StrategyBook.__init__(..., direction="bull")` stores `self.direction`; `_g_row` → `strategy_g.rules(row, now, prev_vals, self.direction)`; entry gains `"side": plan.get("side", "C")`; `_store_chain` → `strategy_g.option_plan(payload, self.direction)`; `_g_track`: `hit = ... and (last <= entry["target"] if bear else last >= entry["target"])`, `reached` likewise with `spot`; `_apply` → `evaluate(row, now, self.direction)`. `G_ROW_FIELDS += ("side",)`. `service.py`: `_BEAR_STRATEGY = strategy.StrategyBook(grade_dir("bear"), direction="bear")`.

- [ ] **Step 4: Run** `tests/test_momx_strategy_g.py tests/test_momx_strategy.py tests/test_momx_service.py` — pass.

- [ ] **Step 5: Commit**
```bash
git add momx/strategy_g.py momx/strategy.py momx/service.py tests/test_momx_strategy_g.py tests/test_momx_strategy.py frontend/public/release-notes.json
git commit -m "feat(momx): bear strategies - Strategy G on the sell side with PUT plans, V2/V3 mirrored"
```

---

### Task 8: `momx/chart_signals.py` — PUT2H / PUT4H on the bear row

**Files:**
- Modify: `momx/chart_signals.py`, `momx/service.py`
- Test: `tests/test_momx_chart_signals.py`

**Interfaces:**
- Produces: `today_signals(payload, day, direction="bull")` (`today_calls(payload, day)` stays as the bull alias); `ChartSignalBook(directory, fetch=None, compute=None, background=True, direction="bull")`.

- [ ] **Step 1: Failing test**
```python
def test_today_signals_bear_keeps_only_todays_put_2h_4h():
    payload = {"signals": [
        sig("PUT2H", "2H", "10:00", direction="PUT"), sig("PUT4H", "4H", "13:00", family="9x20", direction="PUT"),
        sig("P2H", "2H", "07:00", direction="PUT"),                    # compact, against the higher trend
        sig("CALL2H", "2H", "13:00"),
        sig("PUT2H", "2H", "15:00", direction="PUT", day="2026-09-22"),
    ]}
    got = cs.today_signals(payload, "2026-09-23", direction="bear")
    assert [(s["label"], s["at"][11:16]) for s in got] == [("PUT2H", "10:00"), ("PUT4H", "13:00")]
    assert cs.today_signals(payload, "2026-09-23") == cs.today_calls(payload, "2026-09-23")


def test_bear_book_stamps_put_arrows(tmp_path):
    # copy the file's existing ChartSignalBook stamping test, construct with direction="bear" and a compute
    # stub returning the payload above; assert row["chartSignals"][0]["label"] == "PUT2H".
```

- [ ] **Step 2: Run, expect AttributeError.**

- [ ] **Step 3: Implement**
```python
def today_signals(payload, day, direction="bull"):
    bear = str(direction or "").lower() == "bear"
    want_dir, prefix = ("PUT", "PUT") if bear else ("CALL", "CALL")
    ... s.get("direction") != want_dir ... not str(label).startswith(prefix) ...


def today_calls(payload, day):
    return today_signals(payload, day, "bull")
```
`ChartSignalBook.__init__(..., direction="bull")` → `self.direction`; `run_once`: `results[s] = today_signals(payload, day, self.direction)`. `service.py`: `_BEAR_CHART_SIGNALS = chart_signals.ChartSignalBook(grade_dir("bear"), direction="bear")`.

- [ ] **Step 4: Run** the chart_signals + service tests — pass.

- [ ] **Step 5: Commit**
```bash
git add momx/chart_signals.py momx/service.py tests/test_momx_chart_signals.py frontend/public/release-notes.json
git commit -m "feat(momx): PUT2H / PUT4H chart arrows on the bear row"
```

---

### Task 9: `momx_worker.py` — `?dir=bear` routes and bear momo alerts

**Files:**
- Modify: `momx_worker.py` (do_GET :305-425, `_momentum` :206, `_momo_evaluate` :74)
- Test: `tests/test_momx_worker_momentum.py` (existing; add a bear case) — inspect how it drives `_momentum` and mirror it with `direction="bear"`.

**Interfaces:**
- Produces: `_direction(query: dict) -> str` ("bear" only when `dir=bear`); `_momentum(list_name, direction="bull")` keyed `name + "|bear"` in `_LEDGERS`; every GET route listed in the spec §2 reads `_direction(...)`; `_momo_evaluate` iterates `("Mag7","Watchlist") × ("bull","bear")`, keying `_MOMO["stamps"]/["prev"]` by `name if bull else name + " BEAR"` and passing `{**current, "list": key}` to `momo_alert.detect`.

- [ ] **Step 1: Failing test** — in `tests/test_momx_worker_momentum.py` add a test that stubs `service.snapshot` to return a bear payload for `("Mag7","bear")` and asserts `_momentum("Mag7", "bear")["list"] == "Mag7"` and that the ledgers for bull and bear are independent (`_LEDGERS` has both keys).

- [ ] **Step 2: Run, expect TypeError.**

- [ ] **Step 3: Implement**
```python
def _direction(query: dict) -> str:
    return "bear" if str(query.get("dir", [""])[0]).strip().lower() == "bear" else "bull"
```
In `do_GET`: `query = parse_qs(parsed.query)`; `direction = _direction(query)`; `service.snapshot(wanted, direction)`; `_momentum(wanted, direction)`; history/history-query `directory=service.history_dir(direction)`; grade-tape/grade-record `service.grade_dir(direction)` (+ `record_response(dir, direction)`). `_momentum`: `key = name + ("|bear" if direction == "bear" else "")` for `_LEDGERS`; response adds `"direction"`. `_clear_momentum(list_name)` clears both keys. `_momo_evaluate` as in Interfaces.

- [ ] **Step 4: Run** `tests/test_momx_worker_momentum.py tests/test_momx_momo_alert.py` — pass.

- [ ] **Step 5: Commit**
```bash
git add momx_worker.py tests/test_momx_worker_momentum.py frontend/public/release-notes.json
git commit -m "feat(momx): worker serves ?dir=bear on every board route and alerts on bear boards"
```

---

### Task 10: `scripts/momx_grade_backtest.py --direction bear`

**Files:**
- Modify: `scripts/momx_grade_backtest.py`
- Test: `tests/test_momx_grade_backtest.py` (existing — read how it drives `run()`; add a bear case)

- [ ] **Step 1: Failing test** — build a two-day mini archive with one bull-A+ snapshot row and its `mirror_row`; call `run(dir, direction="bear")` and assert the bear event's `toClose` is `-(close/price-1)*100` of the bull run and letters counts match.

- [ ] **Step 2: Implement** — `run(history_dir, days=None, direction="bull")`: `grade.safe_grade_row(row, ts, direction)`; `sign = -1.0 if bear else 1.0`; `toClose = round(sign * (close/price - 1) * 100, 3)`; `main()`: `--direction {bull,bear}`; default history dir `ROOT/"artifacts"/("bear/" if bear)/"momx_history"/board`; target `ROOT/"artifacts"/("bear" if bear)/"momx_grade_record_backtest.json"`; `out["direction"] = direction`.

- [ ] **Step 3: Run** `tests/test_momx_grade_backtest.py` — pass. Commit:
```bash
git add scripts/momx_grade_backtest.py tests/test_momx_grade_backtest.py frontend/public/release-notes.json
git commit -m "feat(momx): grade back-test can run the bear side against the bear History root"
```

---

### Task 11: `frontend/src/momxFilters.js` — direction-aware rules

**Files:**
- Modify: `frontend/src/momxFilters.js`
- Test: `frontend/src/momxFilters.test.js`

**Interfaces:**
- Produces: `isBearRow(row)`; `optionsSetup`, `gapAndGo`, `earlyOpt` (latch key `momx-opt-latch-bear-v1`), `newsMomentum` (bearish news), `fiveMinuteCross` (crossDownAt), `adxCyan` (bear: `minus > 25`, returns `side: "minus"`), `rvolGroupResult` (bearish-only when bear), `strategyRules(direction)`, `STRATEGY_RULES_BEAR`; `gPlanText(g)` prints `g.side || "C"`.

- [ ] **Step 1: Failing tests** (node:test style as the file uses; append):
```js
test("bear OPT reads the bearish cross block, sellers on 30m and extended down", () => {
  const bear = { direction: "bear", grade: { letter: "A+" }, skittles: { "2h": { bg: "magenta" }, "4h": { bg: "red" } },
    adx: { "30m": { plus: 10, minus: 30 } }, m5: { state: "extended" } };
  assert.equal(optionsSetup(bear), true);
  assert.equal(optionsSetup({ ...bear, direction: "bull" }), false);
  assert.equal(optionsSetup({ ...bear, adx: { "30m": { plus: 30, minus: 10 } } }), false);
});

test("bear GO wants sellers on 30m", () => {
  const t = Math.floor(Date.now() / 1000) - 600;
  const row = { direction: "bear", grade: { letter: "A" }, m5: { gapGo: { gap: -2.5, goAt: t } }, adx: { "30m": { plus: 5, minus: 20 } } };
  assert.ok(gapAndGo(row));
  assert.equal(gapAndGo({ ...row, adx: { "30m": { plus: 20, minus: 5 } } }), null);
});

test("bearish-only RVOL filter on a bear row keeps selling cells", () => {
  const config = coerceFilters({ ...DEFAULT_MOMX_FILTERS, rvol: { ...DEFAULT_MOMX_FILTERS.rvol, timeframes: { ...DEFAULT_MOMX_FILTERS.rvol.timeframes, "1h": { on: true, min: 2 } } } });
  const cell = (bg) => ({ direction: "bear", rvol: { "1h": { value: 3, bg } } });
  assert.equal(rvolGroupResult(cell("magenta"), config), true);
  assert.equal(rvolGroupResult(cell("cyan"), config), false);
  assert.equal(rvolGroupResult({ ...cell("cyan"), direction: "bull" }, config), true);
});

test("bear tags: OPT latch is separate, gPlanText prints P, 5m cross reads crossDownAt", () => {
  resetOptLatch();
  assert.equal(gPlanText({ target: 185, entryStrike: 180, entryPrice: 1.05, roi: 152, side: "P" }).includes("180P"), true);
  assert.ok(fiveMinuteCross({ direction: "bear", m5: { crossDownAt: Math.floor(Date.now() / 1000) - 60 } }));
  assert.equal(fiveMinuteCross({ direction: "bull", m5: { crossDownAt: Math.floor(Date.now() / 1000) - 60 } }), null);
});
```

- [ ] **Step 2: Run** `cd frontend; node --test src/momxFilters.test.js` — expect failures.

- [ ] **Step 3: Implement** — `export function isBearRow(row) { return Boolean(row && row.direction === "bear"); }`; `const SKIT_CROSS_BEAR = new Set(["magenta", "red", "light_red", "plum"]);` in `optionsSetup`: `const set = isBearRow(row) ? SKIT_CROSS_BEAR : SKIT_CROSS_BULL; ... (isBearRow(row) ? minus > plus : plus > minus)`; `sellers30(row)` mirror of `buyers30`; `gapAndGo`: `(isBearRow(row) ? sellers30(row) : buyers30(row))`; `earlyOpt`: `loadOptLatch(day, isBearRow(row))` with key `OPT_LATCH_KEY_BEAR = "momx-opt-latch-bear-v1"` and a second in-memory latch; `resetOptLatch` clears both; `newsMomentum`: wanted direction `isBearRow(row) ? "bearish" : "bullish"`; `fiveMinuteCross`: `row.m5[isBearRow(row) ? "crossDownAt" : "crossUpAt"]`; `adxCyan`: bear reads `c.minus` and returns `{ adx, plus: <the DI it read>, fresh, side: isBearRow(row) ? "minus" : "plus" }`; `rvolGroupResult`: `const side = rvolIsBullish(cell); if (isBearRow(row) ? side === true : side === false) ok = false;`; `gPlanText`: `strikeText(g.entryStrike) + (g.side || "C")`; `STRATEGY_RULES_BEAR` = the bear wording for `best`, `go`, `opt`, `g`, `chart`, `v2`, `v3`, `daily2` (mirror each sentence: "gapped down 2% or more ... below VWAP, the open and the first 5-minute low - sellers in control on 30m"; "a fresh Skittles cross DOWN on BOTH 2h and 4h, sellers in control on 30m (-DI above +DI) and 5m momentum Extended down"; G: "RVOL magenta and rising ... Skittles 2h magenta cross ... 5m below EMA20 and VWAP or a PUT5 arrow ... strong put-OI wall as target ..."; chart: "PUT2H / PUT4H"); `export function strategyRules(direction) { return direction === "bear" ? { ...STRATEGY_RULES, ...STRATEGY_RULES_BEAR } : STRATEGY_RULES; }`; `strategyTags(row, ...)` uses `strategyRules(isBearRow(row) ? "bear" : "bull")` for titles and `"gapped " + go.gap.toFixed(1) + "%"` (sign comes from the value).

- [ ] **Step 4: Run** the filters tests — all pass (old + new).

- [ ] **Step 5: Commit**
```bash
git add frontend/src/momxFilters.js frontend/src/momxFilters.test.js frontend/public/release-notes.json
git commit -m "feat(momx): filter rules read the row's direction - bear OPT/GO/G, bearish-only RVOL, PUT plans"
```

---

### Task 12: `frontend/src/momxGrade.js` — bear tones, labels, track-record wording

**Files:**
- Modify: `frontend/src/momxGrade.js`, `frontend/src/index.css` (:21115-21118 area)
- Test: `frontend/src/momxGrade.test.js`

**Interfaces:**
- Produces: `setupTone(row)` → `"aplus-bear"`, `"a-bear"` for bear letters (B stays `"b"`); `momentumTone(row)` → bear building `"down-strong"`? No: keep `"up"`/`"down"` semantics as COLOURS: bear building = `"down"` (red), bear fading = `"up"` (green). `setupText(row)` bear labels `Building ↓`, `Fading ↑`; `chartLabel(chart, direction)` (`above` → "Above trigger", breakout_* → "Breakdown ..."); `trackRecordLine(record, letter)` → "closed lower" when `record.direction === "bear"`; `isBearishPutLabel(text, direction)`.

- [ ] **Step 1: Failing tests**
```js
test("bear rows get the red tones and flipped momentum words", () => {
  const row = { direction: "bear", grade: { letter: "A+" }, m5: { state: "building" } };
  assert.equal(setupTone(row), "aplus-bear");
  assert.equal(momentumTone(row), "down");
  assert.equal(setupText(row), "A+ · Building ↓");
  assert.equal(setupText({ ...row, m5: { state: "fading" } }), "A+ · Fading ↑");
  assert.equal(chartLabel("breakout_confirmed", "bear"), "Breakdown confirmed");
  assert.equal(chartLabel("above", "bear"), "Above trigger");
});

test("bear track record says closed lower", () => {
  const record = { direction: "bear", source: "recorded", letters: { "A+": { count: 3, pctHigherClose: 66.7, avgToClose: 1.2 } } };
  assert.match(trackRecordLine(record, "A+"), /closed lower/);
});

test("isBearishPutLabel", () => {
  assert.equal(isBearishPutLabel("PUT2H", "PUT"), true);
  assert.equal(isBearishPutLabel("P4H", "PUT"), true);
  assert.equal(isBearishPutLabel("CALL2H", "CALL"), false);
});
```

- [ ] **Step 2: Run** `node --test src/momxGrade.test.js` — expect failures.

- [ ] **Step 3: Implement** — `MOMENTUM_LABEL_BEAR = { ...MOMENTUM_LABEL, building: "Building ↓", fading: "Fading ↑" }`; `CHART_LABEL_BEAR = { above: "Above trigger", breakout_provisional: "Breakdown – provisional", breakout_confirmed: "Breakdown confirmed", holding: "Holding", failed: "Failed" }`; `chartLabel(chart, direction = "bull")`; `setupText` picks the label table by `row.direction`; `LETTER_TONE_BEAR = { "A+": "aplus-bear", A: "a-bear", B: "b" }`; `momentumTone`: bear building → "down", bear fading → "up"; `trackRecordLine`: `const word = record.direction === "bear" ? "closed lower" : "closed higher"`; `isBearishPutLabel(text, direction)`: `direction === "PUT"` or text starts with `PUT` or `/^P\d/`.
CSS (`index.css` next to `.is-aplus`): `.momx-scanner-panel .momx-setup.is-aplus-bear .momx-setup-letter { color: #ff4d6d; font-weight: 700; }` and `.is-a-bear ... { color: #ff8fa3; font-weight: 600; }`.

- [ ] **Step 4: Run** — pass. Commit:
```bash
git add frontend/src/momxGrade.js frontend/src/momxGrade.test.js frontend/src/index.css frontend/public/release-notes.json
git commit -m "feat(momx): bear palette and wording for the Setup cell and track record"
```

---

### Task 13: `MomxScannerPanel.jsx` — BULL/BEAR switch, `dir=` on every fetch, cache keys, labels

**Files:**
- Modify: `frontend/src/MomxScannerPanel.jsx` (:182-232 endpoints, :258-300 cache helpers, :725 readStoredList, :1618-1640 RVOL checkbox, :3562-3578 state, :4020 runFetch, :4098 rebuild, :4185 momentum, :4478 grade record, :3855-3872 history-query, :3908 history, :5416-5426 BULL label), `frontend/src/momxHistory.js` (:125 historyRequest), `frontend/src/index.css` (:16650 `.momx-bull`)
- Test: `frontend/src/momxHistory.test.js` (historyRequest with `dir`), `frontend/src/momxCells.test.js` if a pure helper is extracted.

**Interfaces:**
- Produces: localStorage `momx.scanner.direction` (`"bull"|"bear"`); `boardCacheKey(list, direction)` = `list` or `list + "|bear"`; `historyRequest(list, { ..., direction })` adds `dir=bear` and prefixes the key with `bear|`; a `<button className="momx-bull is-bear?" onClick=toggle>` replacing the span; every fetch above carries `&dir=bear` in bear mode; the RVOL checkbox label reads "Bearish only" in bear mode; the SCAN button title and empty-state text name the direction.

- [ ] **Step 1: Failing test** (momxHistory.test.js):
```js
test("historyRequest carries the direction", () => {
  const r = historyRequest("Mag7", { direction: "bear" });
  assert.match(r.url, /dir=bear/);
  assert.equal(r.key.startsWith("bear|Mag7"), true);
  assert.equal(historyRequest("Mag7", {}).url.includes("dir="), false);
});
```
Extract `export function boardCacheKey(list, direction) { return direction === "bear" ? list + "|bear" : list; }` into `momxHistory.js` (already imported by the panel) and test it too.

- [ ] **Step 2: Run** `node --test src/momxHistory.test.js` — fail.

- [ ] **Step 3: Implement**
- `momxHistory.js`: `historyRequest`: `const direction = opts.direction === "bear" ? "bear" : "bull"; if (direction === "bear") params.set("dir", "bear");` key prefix `(direction === "bear" ? "bear|" : "")`. Add `boardCacheKey`.
- Panel: `const MOMX_DIRECTION_STORAGE_KEY = "momx.scanner.direction";` `readStoredDirection()` / `writeStoredDirection()` wrapped like `readStoredList`. State `const [direction, setDirection] = useState(readStoredDirection)`; `const bear = direction === "bear"`; `const dirQuery = bear ? "&dir=bear" : "";`
- `runFetch`: `MOMX_ENDPOINT + "?list=" + encodeURIComponent(listName) + dirQuery` (and `MOMX_ENDPOINT + "?dir=bear"` when no list). Add `direction` to the `useCallback` deps. Every `readCachedBoard/hasCachedBoard/writeCachedBoard(list...)` call → `boardCacheKey(list, direction)` (grep the call sites; there are ~6).
- On direction change: `useEffect(() => { setBoard(null); runFetch(false, activeList); loadMomentum(); }, [direction])` plus the momentum/grade-record/history fetches append `dirQuery` (`historyRequest(activeList, { ..., direction })`; history-query `params.set("dir","bear")` when bear).
- Replace the BULL span with:
```jsx
        <button
          type="button"
          className={"momx-bull" + (bear ? " is-bear" : "")}
          onClick={() => setDirection((d) => (d === "bear" ? "bull" : "bear"))}
          title={bear ? "BEAR scan: the bull scan mirrored for puts. Click for BULL." : "BULL scan (AlertX Bull Momo). Click for BEAR."}
          aria-pressed={bear}
          data-testid="momx-direction-toggle"
        >
          {bear ? "BEAR" : "BULL"}
          {bear ? <ArrowDown size={11} aria-hidden="true" /> : <ArrowUp size={11} aria-hidden="true" />}
        </button>
```
(import `ArrowDown` from the same icon package as `ArrowUp`.)
- RVOL checkbox label: `{bear ? "Bearish only" : "Bullish only"}`; SCAN title `"Run the " + (bear ? "bear" : "bull") + " scan again now"`; the empty-state message (:5220) mentions the direction.
- `gateSelect("setup", ...)` labels unchanged; the strategy help text uses `strategyRules(direction)` where the panel prints `STRATEGY_RULES[...]` (grep `STRATEGY_RULES` in the panel).
- Where the panel prints the ADX tag text ("cyan"), use `tag.side === "minus" ? "magenta" : "cyan"`.
- CSS: `.momx-scanner-panel .momx-bull { cursor: pointer; }` and `.momx-scanner-panel .momx-bull.is-bear { border-color: rgba(244, 114, 208, 0.45); background: rgba(244, 114, 208, 0.10); color: var(--momx-down); }`.

- [ ] **Step 4: Run** `node --test src/*.test.js` and `npm run build` — pass, build succeeds.

- [ ] **Step 5: Commit**
```bash
git add frontend/src/MomxScannerPanel.jsx frontend/src/momxHistory.js frontend/src/momxHistory.test.js frontend/src/index.css frontend/public/release-notes.json
git commit -m "feat(momx): BULL / BEAR switch on the scanner - every fetch, cache key and label follows it"
```

---

### Task 14: `App.jsx` — red A+/A circles on PUT labels from the bear tape

**Files:**
- Modify: `frontend/src/App.jsx` (:12905 scannerGradeForBubble, :15492-15540 tape loader, call sites :21543, :21587, :21630, :21644, :21697, step markers :21723-21739, deps :22389)
- Test: none automatable beyond `node --test src/gradeMarkers.test.js` (unchanged); browser smoke in Task 15.

- [ ] **Step 1: Implement**
- Duplicate the tape loader as a second state `bearGradeTape` fetching `...grade-tape?symbol=...&days=5&dir=bear`; `const bearGradeTapeEntries = ...`.
- `scannerGradeForBubble(entries, text, direction, ...)`: remove the `direction === "PUT"` early return; accept PUT when `isBearishPutLabel(text, direction)`; the caller passes the RIGHT entries: add `const gradeEntriesFor = (direction, label) => (direction === "PUT" || /^P(UT)?\d/i.test(String(label || ""))) ? bearGradeTapeEntries : gradeTapeEntries;` and use it at the five call sites.
- Step markers: a second loop over `gradeStepMarkers(bearGradeTapeEntries)` pushing `position: "aboveBar"`, `color: step.letter === "A+" ? "#ef4444" : "#fca5a5"`, `direction: "PUT"`, key `${markerTime}-grade-bear-${step.letter}`.
- Where a bubble's `grade` is painted (grep `grade.letter` / `.grade &&` in the marker drawing code), give PUT-direction bubbles the red pair.
- Add `bearGradeTapeEntries` to the effect deps at :22389 and to the signature at :20817.

- [ ] **Step 2: Run** `node --test src/*.test.js` and `npm run build` — pass.

- [ ] **Step 3: Commit**
```bash
git add frontend/src/App.jsx frontend/public/release-notes.json
git commit -m "feat(chart): scanner bear grade circles on PUT labels"
```

---

### Task 15: Verification, deploy, handoff (AGENTS.md checklist)

**Files:** none new except `artifacts/` scratch outputs.

- [ ] **Step 1: Full Python suite for momx**
Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_*.py -q` — all pass. Record the count.

- [ ] **Step 2: Bull unchanged on frozen tapes**
Run `artifacts/momx_parallel_frozen.py` (see memory `momx-scanner-process-isolation`) or, if its interface differs, a 20-line script: load `artifacts/momx_board_cache/Watchlist.json` from BEFORE the change (copy it aside first, `git stash` is not enough — the file is untracked), build with the same frozen tapes on the new code, and diff every row field except `direction`/`bear`. Expected: 0 differences.

- [ ] **Step 3: Frontend tests + production build**
`cd frontend; node --test src/*.test.js; npm run build` — pass, build succeeds.

- [ ] **Step 4: Worker restart (outside 09:30–16:00 ET only)**
Announce to peer sessions (`mcp__ccd_session_mgmt__send_message`), then restart `momx_worker.py` the way the watchdog does (kill the pid listening on :3010; `scripts/scanner_watchdog.ps1` relaunches it) and confirm:
```bash
curl -s "http://127.0.0.1:3010/api/momx-scanner?list=Mag7&dir=bear" | head -c 400
curl -s "http://127.0.0.1:3010/api/momx-scanner/status" | grep -o '"gradeBear[^}]*}'
```
Expected: a payload with `"direction": "bear"`, rows sorted ascending by `pctChange`; status shows the bear tape block.

- [ ] **Step 5: Browser smoke on :5173** (desktop and 375 px, in-app Browser)
- Toggle BULL → BEAR: header/label flips, rows reload, biggest losers first, red letters.
- FILTERS: "Bearish only" label; "Best setups" and "Chart CALL2H / CALL4H" choices work (bear rows show PUT2H/PUT4H tags when present).
- History tab in bear mode loads (empty is fine on day one) and switches back cleanly.
- Chart of a bear A+ symbol: red circle on a PUT label if one fired today; bull circles unchanged.
- Console: no errors; switching direction never shows bull rows under the BEAR label.

- [ ] **Step 6: Release notes** — confirm the top entry's `at` is within 24 h of the final commit and its items match what shipped; amend if needed and commit.

- [ ] **Step 7: Handoff** — report: tests run (counts), the frozen-tape diff result, what was smoke-tested and on which widths, the worker restart time, and the two limitations: no bear History/back-test until days accumulate, and the scheduled scorecard's bear run is a follow-up.
