# MomX Scanner Grade (A+/A/B) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show an experimental Draft-2 scanner grade + momentum state on the MomX Watchlist/Mag7 board (two columns, why-panel, sort, filter), let the trader rearrange/hide columns, put the same grade on bullish chart CALL labels, and record every grade event with outcomes so the rule can be judged on data.

**Architecture:** A pure `momx/grade.py` (Draft 2) and `momx/momentum.py` (5m candle states) are computed per row inside the existing board build; a stateful `momx/grade_log.py` in the MomX worker adds freshness/latch/age, writes a 5-minute grade tape and first-per-letter events, and a nightly job fills outcomes and the track record. The frontend reads `row.grade`; the chart reads the tape through a new worker endpoint (auto-proxied by api_server).

**Tech Stack:** Python 3 stdlib + pandas (existing feed frames), pytest; React (Vite) + `node --test`; Lightweight Charts custom primitive.

**Spec:** `docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md` (approved 2026-09-22)

## Global Constraints

- The baseline rule is **Draft 2, unchanged** — thresholds A+ SKIT ≥ 7 + H/L green, A ≥ 6, B ≥ 5; SQZ OK = any 4h/D/W bg `cyan` or none bg `orange`; Push = RVOL bg `cyan`/`green` on 5m/15m/30m/1h/2h/4h at that scan, or news ≤ 12 h old. No hold, no smoothing.
- SKIT bullish = fg `cyan`/`dark_green` **or** bg `cyan`/`green`/`lime`/`dark_green`; fg and bg are also counted separately.
- On-screen name: **"Scanner grade"**. Every why-panel and chart hover carries: "A+ means the defined conditions align. It is not a recommendation and does not guarantee profit."
- Colour meanings come only from `momx/columns.py`; never invent a new one.
- Grading never breaks a row: any exception → `grade: None`, row still ships. A missing cell never passes.
- All file writes: temp file + `os.replace`. Never truncate a source file in place.
- History: `grade` is stored with snapshots but is **not** a snapshot trigger.
- Browser storage wrapped in try/catch; blocked storage → default layout.
- Release note entry at the top of `frontend/public/release-notes.json` in the **same commit** as any app-code change (pre-commit hook enforces).
- Python tests: `.venv/Scripts/python.exe -m pytest <path> -q`. JS tests: from `frontend/`, `node --test src/<file>.test.js`.
- Tests never touch the real `artifacts/` archive (temp dirs + env overrides).
- Never restart `api_server` during market hours (09:30–16:00 ET). Restarting only `momx_worker` is allowed after 16:00 ET or before 09:00 ET.
- App.jsx edits via the Edit tool only (Python rewrites flip it to CRLF and break source-lifting tests).

## File structure

| File | Responsibility |
|---|---|
| `momx/grade.py` (new) | Draft 2 checks + letter + reasons from one row. Pure. |
| `momx/momentum.py` (new) | Trigger level, 5m chart state, momentum state from 5m bars + row RVOL cells. Pure. |
| `momx/grade_log.py` (new) | Worker-side memory: freshness (bg changes, RVOL/SQZ/news appearing), first-per-letter latch, signal age; 5-min tape + event files; nightly outcomes + track record. |
| `momx/columns.py` (modify) | `build_row` adds `m5` (momentum summary) and `sqzRaw`. |
| `momx/board.py` (modify) | `_contract_row` carries `m5`, `sqzRaw`, `grade`; `build_board` grades rows + rest after news. |
| `momx/service.py` (modify) | After each build: `grade_log.apply(name, payload, now)`; nightly `grade_log.nightly(...)`; status fields. |
| `momx_worker.py` (modify) | `GET /api/momx-scanner/grade-tape`, `GET /api/momx-scanner/grade-record`. |
| `scripts/momx_grade_backtest.py` (new) | Re-runnable back-test over the History archive; writes the seed record. |
| `frontend/src/momxGrade.js` (new) | Display helpers: setup text/colour, fresh text, sort values, tooltip/why data, chart tape lookup. Pure. |
| `frontend/src/momxColumnLayout.js` (new) | Column manager model: apply/move/hide/reset/normalize + storage. Pure. |
| `frontend/src/MomxGradeWhy.jsx` (new) | The "why" popover. |
| `frontend/src/MomxColumnManager.jsx` (new) | The Columns dialog. |
| `frontend/src/MomxScannerPanel.jsx` (modify) | Two columns, cell rendering, sort, filters, layout wiring. |
| `frontend/src/momxFilters.js` (modify) | Setup/Momentum filter fields. |
| `frontend/src/App.jsx` (modify) | Fetch tape for the chart symbol; attach `grade` to bullish CALL bubbles. |
| `frontend/src/tosNativeChartPrimitive.js` (modify) | Copy `grade` into label geometry; draw the lettered circle. |

---

### Task 1: Test fixtures from the real 2026-09-21 History

**Files:**
- Create: `tests/fixtures/momx_grade_rows.json`
- Create: `scripts/extract_grade_fixtures.py` (one-off, kept for reproducibility)

**Interfaces:**
- Produces: `tests/fixtures/momx_grade_rows.json` = `{"<LABEL>": {"at": "<iso>", "row": {...scanner row...}}}` with labels `META_0933`, `QCOM_0942`, `QCOM_1041`, `RIOT_0902`, `XOVR_0001`, `INTC_0933`.

- [ ] **Step 1: Write the extractor**

```python
"""Extract the grade test fixtures from the real MomX History archive.

Run once from the repo root:
    .venv/Scripts/python.exe scripts/extract_grade_fixtures.py
The output is committed so tests never read artifacts/.
"""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "artifacts" / "momx_history" / "Watchlist"
WANT = [
    ("META_0933", "2026-09-21", "META", "09:33"),
    ("INTC_0933", "2026-09-21", "INTC", "09:33"),
    ("QCOM_0942", "2026-09-21", "QCOM", "09:42"),
    ("QCOM_1041", "2026-09-21", "QCOM", "10:41"),
    ("RIOT_0902", "2026-09-21", "RIOT", "09:02"),
    ("XOVR_0001", "2026-09-22", "XOVR", "00:01"),
]


def main() -> None:
    out = {}
    cache = {}
    for label, day, symbol, hhmm in WANT:
        doc = cache.setdefault(day, json.loads((HIST / f"{day}.json").read_text(encoding="utf-8")))
        snaps = doc["rows"][symbol]["snapshots"]
        snap = next(s for s in snaps if s["at"][11:16] == hhmm)
        out[label] = {"at": snap["at"], "row": snap["row"]}
    target = ROOT / "tests" / "fixtures" / "momx_grade_rows.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)
    print("wrote", target, "labels:", ", ".join(out))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `.venv/Scripts/python.exe scripts/extract_grade_fixtures.py`
Expected: `wrote ...momx_grade_rows.json labels: META_0933, INTC_0933, QCOM_0942, QCOM_1041, RIOT_0902, XOVR_0001`

- [ ] **Step 3: Commit** (no release note needed: `tests/` and a `scripts/` file named for extraction — if the hook objects to the script, add it in Task 2's commit instead)

```bash
git add tests/fixtures/momx_grade_rows.json scripts/extract_grade_fixtures.py
git commit -m "test(momx): real 2026-09-21 History rows as grade fixtures"
```

---

### Task 2: `momx/grade.py` — Draft 2 baseline

**Files:**
- Create: `momx/grade.py`
- Test: `tests/test_momx_grade.py`

**Interfaces:**
- Produces: `grade_row(row: Mapping, now: datetime) -> dict | None` returning
  `{"letter": "A+"|"A"|"B"|None, "checks": {"sqzOK": bool, "sqzFired": [tf...], "sqzCoiling": [tf...], "push": bool, "pushRvol": [tf...], "pushNews": bool, "newsAgeHours": float|None, "skit": int, "skitTrend": int, "skitCross": int, "skitBullish": [tf...], "aboveMid": bool, "hlDegree": float|None}, "reasons": [str, ...], "pctChange": float|None}`; returns `None` only if `row` is not a mapping.
- Produces constants: `SQZ_TFS=("4h","D","Wk")`, `PUSH_RVOL_TFS=("5m","15m","30m","1h","2h","4h")`, `SKIT_TFS=("2h","4h","D","2D","3D","4D","Wk","M")`, `NEWS_MAX_HOURS=12`, `A_PLUS_SKIT=7`, `A_SKIT=6`, `B_SKIT=5`, `LETTER_RANK={"A+":3,"A":2,"B":1,None:0}`.

- [ ] **Step 1: Write the failing tests**

```python
import json
from datetime import datetime
from pathlib import Path

import pytest

from momx import grade

FIX = json.loads((Path(__file__).parent / "fixtures" / "momx_grade_rows.json").read_text(encoding="utf-8"))


def _g(label):
    item = FIX[label]
    return grade.grade_row(item["row"], datetime.fromisoformat(item["at"]))


def test_meta_0933_is_a_plus():
    g = _g("META_0933")
    assert g["letter"] == "A+"
    assert g["checks"]["skit"] == 8
    assert set(g["checks"]["sqzFired"]) >= {"Wk"}
    assert "5m" in g["checks"]["pushRvol"]


def test_qcom_morning_climbs_from_b_to_a_plus():
    assert _g("QCOM_0942")["letter"] == "B"
    late = _g("QCOM_1041")
    assert late["letter"] == "A+"
    assert late["checks"]["pushNews"] is True        # the 05:05 headline
    assert late["checks"]["pushRvol"] == []            # no volume push at 10:41


def test_riot_coiling_weekly_squeeze_blocks_grade():
    g = _g("RIOT_0902")
    assert g["letter"] is None
    assert "Wk" in g["checks"]["sqzCoiling"]


def test_overnight_xovr_has_alignment_but_no_push():
    g = _g("XOVR_0001")
    assert g["letter"] is None
    assert g["checks"]["skit"] == 8
    assert g["checks"]["push"] is False


def test_dark_green_skit_fg_counts_bullish():
    row = {"skittles": {"2D": {"value": 94, "bg": "black", "fg": "dark_green"}}}
    g = grade.grade_row(row, datetime(2026, 9, 21, 9, 33))
    assert g["checks"]["skitBullish"] == ["2D"]
    assert g["checks"]["skitTrend"] == 1 and g["checks"]["skitCross"] == 0


def test_missing_cells_never_pass():
    g = grade.grade_row({}, datetime(2026, 9, 21, 9, 33))
    assert g["letter"] is None
    c = g["checks"]
    assert c["push"] is False and c["skit"] == 0 and c["aboveMid"] is False
    assert c["sqzOK"] is True  # no squeeze ON anywhere = OK, per Draft 2


def test_news_age_limit_is_12_hours():
    row = {"news": {"headline": "x", "at": "2026-09-21T09:00:00+00:00"}}
    assert grade.grade_row(row, datetime.fromisoformat("2026-09-21T20:59:00+00:00"))["checks"]["pushNews"] is True
    assert grade.grade_row(row, datetime.fromisoformat("2026-09-21T21:01:00+00:00"))["checks"]["pushNews"] is False


def test_future_dated_news_does_not_count():
    row = {"news": {"headline": "x", "at": "2026-09-21T12:00:00+00:00"}}
    assert grade.grade_row(row, datetime.fromisoformat("2026-09-21T11:00:00+00:00"))["checks"]["pushNews"] is False


def test_not_a_mapping_returns_none():
    assert grade.grade_row(None, datetime(2026, 9, 21)) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_grade.py -q`
Expected: FAIL — `ImportError: cannot import name 'grade' from 'momx'`

- [ ] **Step 3: Implement**

```python
"""MomX scanner grade - Draft 2 baseline (experimental).

Spec: docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md.
"A+" means the defined conditions align. It is not a recommendation and does
not guarantee profit. The rule is frozen as back-tested; change it only as a
new draft compared against this one on recorded data.

Colour meanings are momx/columns.py's, not ours:
  SQZ bg cyan  = medium squeeze released, momentum rising (lights 2 bars)
  SQZ bg orange= medium squeeze ON, or a high-compression squeeze just fired
  SKIT fg cyan / dark_green = EMA9 > EMA20 (dark green: and FastD >= 90)
  SKIT bg cyan/green/lime/dark_green = a bullish cross on this bar
  RVOL bg cyan (z>=3) / green (z>=2) with the bar closing in its upper half
  H/L bg green = close above the midpoint of the last 8 two-hour bars
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

SQZ_TFS = ("4h", "D", "Wk")
PUSH_RVOL_TFS = ("5m", "15m", "30m", "1h", "2h", "4h")
SKIT_TFS = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
NEWS_MAX_HOURS = 12
A_PLUS_SKIT = 7
A_SKIT = 6
B_SKIT = 5
LETTER_RANK = {"A+": 3, "A": 2, "B": 1, None: 0}

RVOL_BULL_BG = frozenset({"cyan", "green"})
SKIT_TREND_FG = frozenset({"cyan", "dark_green"})
SKIT_CROSS_BG = frozenset({"cyan", "green", "lime", "dark_green"})
TF_LABEL = {"Wk": "W", "M": "Mo"}


def _cell(row: Mapping, section: str, tf: str) -> Mapping:
    part = row.get(section)
    cell = part.get(tf) if isinstance(part, Mapping) else None
    return cell if isinstance(cell, Mapping) else {}


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _news_age_hours(row: Mapping, now: datetime) -> float | None:
    news = row.get("news")
    if not isinstance(news, Mapping) or not news.get("headline") or not news.get("at"):
        return None
    try:
        published = _aware(datetime.fromisoformat(str(news["at"]).replace("Z", "+00:00")))
    except ValueError:
        return None
    return (_aware(now) - published).total_seconds() / 3600.0


def _label(tf: str) -> str:
    return TF_LABEL.get(tf, tf)


def grade_row(row: Any, now: datetime) -> dict | None:
    if not isinstance(row, Mapping):
        return None

    fired = [tf for tf in SQZ_TFS if _cell(row, "sqz", tf).get("bg") == "cyan"]
    coiling = [tf for tf in SQZ_TFS if _cell(row, "sqz", tf).get("bg") == "orange"]
    sqz_ok = bool(fired) or not coiling

    push_rvol = [tf for tf in PUSH_RVOL_TFS if _cell(row, "rvol", tf).get("bg") in RVOL_BULL_BG]
    age = _news_age_hours(row, now)
    push_news = age is not None and 0 <= age <= NEWS_MAX_HOURS
    push = bool(push_rvol) or push_news

    bullish, trend, cross = [], 0, 0
    for tf in SKIT_TFS:
        cell = _cell(row, "skittles", tf)
        is_trend = cell.get("fg") in SKIT_TREND_FG
        is_cross = cell.get("bg") in SKIT_CROSS_BG
        trend += is_trend
        cross += is_cross
        if is_trend or is_cross:
            bullish.append(tf)
    skit = len(bullish)

    hl = row.get("highLow") if isinstance(row.get("highLow"), Mapping) else {}
    above_mid = hl.get("bg") == "green"
    try:
        hl_degree = float(hl.get("value")) if hl.get("value") is not None else None
    except (TypeError, ValueError):
        hl_degree = None

    letter = None
    if sqz_ok and push:
        if skit >= A_PLUS_SKIT and above_mid:
            letter = "A+"
        elif skit >= A_SKIT:
            letter = "A"
        elif skit >= B_SKIT:
            letter = "B"

    reasons = []
    if fired:
        reasons.append("SQZ fired " + "+".join(_label(tf) for tf in fired))
    elif coiling:
        reasons.append("SQZ coiling " + "+".join(_label(tf) for tf in coiling))
    else:
        reasons.append("no squeeze ON")
    for tf in push_rvol:
        value = _cell(row, "rvol", tf).get("value")
        reasons.append(f"RVOL {tf} {value}")
    if push_news:
        reasons.append(f"news {age:.1f}h ago")
    reasons.append(f"SKIT {skit}/8")
    reasons.append("above 16h midpoint" if above_mid else "below 16h midpoint")
    pct = row.get("pctChange")
    if isinstance(pct, (int, float)):
        reasons.append(f"already {pct:+.1f}% today")

    return {
        "letter": letter,
        "checks": {
            "sqzOK": sqz_ok,
            "sqzFired": fired,
            "sqzCoiling": coiling,
            "push": push,
            "pushRvol": push_rvol,
            "pushNews": push_news,
            "newsAgeHours": None if age is None else round(age, 2),
            "skit": skit,
            "skitTrend": trend,
            "skitCross": cross,
            "skitBullish": bullish,
            "aboveMid": above_mid,
            "hlDegree": hl_degree,
        },
        "reasons": reasons,
        "pctChange": pct if isinstance(pct, (int, float)) else None,
    }


def safe_grade_row(row: Any, now: datetime) -> dict | None:
    """grade_row that can never raise - a grading bug costs a grade, not a row."""
    try:
        return grade_row(row, now)
    except Exception:  # noqa: BLE001
        return None
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_grade.py -q`
Expected: `9 passed`. If `QCOM_0942` is not `B` or `QCOM_1041` not `A+`, STOP: the fixture or the rule differs from the back-test — compare against `scratchpad grade2.py` semantics before changing anything.

- [ ] **Step 5: Commit** (with a release-notes entry: "Scanner grade engine added (not visible yet)")

```bash
git add momx/grade.py tests/test_momx_grade.py frontend/public/release-notes.json
git commit -m "feat(momx): Draft 2 scanner grade engine (experimental, not yet shown)"
```

---

### Task 3: Back-test script sharing `momx/grade.py`

**Files:**
- Create: `scripts/momx_grade_backtest.py`
- Test: `tests/test_momx_grade_backtest.py`

**Interfaces:**
- Consumes: `grade.grade_row`, `grade.LETTER_RANK`.
- Produces: `run(history_dir: Path, days: list[str] | None = None) -> dict` → `{"days": [...], "letters": {"A+": {"count", "pctHigherClose", "avgToClose", "medianToClose", "up3", "down3"}, ...}, "events": [{"day","time","symbol","letter","price","close","toClose"}]}`; CLI writes `artifacts/momx_grade_record_backtest.json` (atomic).
- Close for day D = the first snapshot of the next day with ≥ 500 snapshots: `last / (1 + pctChange/100)`; the latest day uses no close (excluded).

- [ ] **Step 1: Write the failing test** (tiny synthetic archive in `tmp_path`)

```python
import json
from pathlib import Path

import scripts.momx_grade_backtest as bt


def _snap(at, last, pct, letter_row):
    return {"at": at, "row": dict(letter_row, last=last, pctChange=pct)}


A_PLUS_ROW = {
    "sqz": {"Wk": {"bg": "cyan"}},
    "rvol": {"5m": {"bg": "cyan", "value": 7.0}},
    "skittles": {tf: {"fg": "cyan", "bg": "black"} for tf in ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")},
    "highLow": {"bg": "green", "value": 0.9},
}


def test_backtest_scores_first_a_plus_to_next_day_close(tmp_path: Path):
    wl = tmp_path / "Watchlist"
    wl.mkdir()
    day1 = {"rows": {"AAA": {"snapshots": [
        _snap("2026-09-21T09:40:00-04:00", 100.0, 1.0, A_PLUS_ROW),
        _snap("2026-09-21T09:50:00-04:00", 101.0, 2.0, A_PLUS_ROW),   # not first -> ignored
    ]}}}
    filler = [_snap("2026-09-22T04:00:00-04:00", 110.0, 10.0, {})] * 500  # close = 100
    day2 = {"rows": {"AAA": {"snapshots": filler}}}
    (wl / "2026-09-21.json").write_text(json.dumps(day1), encoding="utf-8")
    (wl / "2026-09-22.json").write_text(json.dumps(day2), encoding="utf-8")

    out = bt.run(wl)
    a_plus = out["letters"]["A+"]
    assert a_plus["count"] == 1
    assert out["events"][0]["price"] == 100.0
    assert round(out["events"][0]["close"], 6) == 100.0
    assert a_plus["pctHigherClose"] == 0.0
```

- [ ] **Step 2: Run to verify failure** — `.venv/Scripts/python.exe -m pytest tests/test_momx_grade_backtest.py -q` → FAIL (module missing). If `scripts` is not importable, add an empty `scripts/__init__.py` only if one does not exist and `tests/conftest.py` does not already put the repo root on `sys.path` (check first: `grep -n sys.path tests/conftest.py`).

- [ ] **Step 3: Implement**

```python
"""Re-runnable back-test of the Draft 2 scanner grade over the MomX History archive.

    .venv/Scripts/python.exe scripts/momx_grade_backtest.py [Watchlist|Mag7]

Uses momx/grade.py itself, so the app and this report can never disagree.
Limits (printed with the result): the archive only holds tickers while they
matched the scan, snapshots cap at 120/ticker/day, and the outcome is to the
close only - not a profitability measure.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from momx import grade  # noqa: E402

MIN_SNAPSHOTS_FOR_CLOSE = 500
SESSION = ("09:30", "15:30")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("rows", {})


def _prev_closes(rows: dict) -> dict:
    out = {}
    for symbol, rec in rows.items():
        for snap in rec.get("snapshots", []):
            r = snap.get("row", {})
            last, pct = r.get("last"), r.get("pctChange")
            if last and pct is not None:
                out[symbol] = last / (1 + pct / 100.0)
                break
    return out


def _stats(values: list[float]) -> dict:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "pctHigherClose": round(100.0 * sum(v > 0 for v in values) / len(values), 1),
        "avgToClose": round(statistics.mean(values), 3),
        "medianToClose": round(statistics.median(values), 3),
        "up3": sum(v >= 3 for v in values),
        "down3": sum(v <= -3 for v in values),
    }


def run(history_dir: Path, days: list[str] | None = None) -> dict:
    files = sorted(history_dir.glob("*.json"))
    names = [p.stem for p in files]
    if days:
        names = [d for d in names if d in days]
    events = []
    for index, day in enumerate(names):
        rows = _load(history_dir / f"{day}.json")
        closes = None
        for later in [p.stem for p in files][[p.stem for p in files].index(day) + 1:]:
            nrows = _load(history_dir / f"{later}.json")
            if sum(len(r.get("snapshots", [])) for r in nrows.values()) >= MIN_SNAPSHOTS_FOR_CLOSE:
                closes = _prev_closes(nrows)
                break
        if closes is None:
            continue
        for symbol, rec in rows.items():
            seen = set()
            for snap in rec.get("snapshots", []):
                hhmm = snap["at"][11:16]
                if not (SESSION[0] <= hhmm <= SESSION[1]):
                    continue
                g = grade.grade_row(snap["row"], datetime.fromisoformat(snap["at"]))
                letter = g and g["letter"]
                price = snap["row"].get("last")
                if not letter or letter in seen or not price or symbol not in closes:
                    continue
                seen.add(letter)
                close = closes[symbol]
                events.append({"day": day, "time": hhmm, "symbol": symbol, "letter": letter,
                               "price": price, "close": close,
                               "toClose": round((close / price - 1) * 100, 3)})
    letters = {L: _stats([e["toClose"] for e in events if e["letter"] == L]) for L in ("A+", "A", "B")}
    return {"days": sorted({e["day"] for e in events}), "letters": letters, "events": events}


def main() -> None:
    board = sys.argv[1] if len(sys.argv) > 1 else "Watchlist"
    out = run(ROOT / "artifacts" / "momx_history" / board)
    out["source"] = f"back-test from History archive ({len(out['days'])} days, {board})"
    target = ROOT / "artifacts" / "momx_grade_record_backtest.json"
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(out), encoding="utf-8")
    os.replace(tmp, target)
    for letter, s in out["letters"].items():
        print(letter, s)
    print("limits: scan-matched tickers only; 120 snapshots/ticker/day cap; to-close only")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run test, then the real script**

Run: `.venv/Scripts/python.exe -m pytest tests/test_momx_grade_backtest.py -q` → `1 passed`
Run: `.venv/Scripts/python.exe scripts/momx_grade_backtest.py` → prints A+/A/B stats. Record the A+ line in the commit message. It should be close to "370 signals, 46%" from the brainstorm (small differences are expected now that 2026-09-21 has a next-day close); if it differs by more than 10%, report the difference to the trader rather than tuning anything.

- [ ] **Step 5: Commit** (with release note item "Back-test script for the scanner grade")

---

### Task 4: `momx/momentum.py` — trigger, 5m chart state, momentum state

**Files:**
- Create: `momx/momentum.py`
- Test: `tests/test_momx_momentum.py`

**Interfaces:**
- Consumes: `momx.indicators.rvol_zscore(volumes, length)`, `momx.indicators.ema(values, length)` (existing), `momx.columns.RVOL_LENGTH`.
- Produces: `summarize(bars_5m: list[dict], rvol_cells: Mapping | None, *, now_epoch: float | None = None) -> dict | None`:
  `{"trigger": float|None, "chart": "below"|"breakout_provisional"|"breakout_confirmed"|"holding"|"failed", "state": "building"|"holding"|"fading"|"extended"|"quiet", "pattern": "explosive"|"steady"|None, "provisional": bool, "breakoutAt": int|None, "lastCompleted": {"time","open","high","low","close","volume"}|None, "atr14": float|None, "ema20": float|None}`.
  Bars are dicts with `time` (epoch s) or `timestamp`, and `open/high/low/close/volume`. The last bar is **developing** when `now_epoch - bar_time < 300` (default `now_epoch = time.time()`); otherwise all bars are completed.
- Constants: `TRIGGER_LOOKBACK=6`, `ATR_LENGTH=14`, `EMA_LENGTH=20`, `EXTENDED_ATR=2.0`, `BUILD_RVOL_BG={"cyan","green"}`, `FADE_RVOL_Z=1.0`.

Rules, evaluated on **today's** bars (same ET date as the last bar):
1. `trigger(i)` = max high of the 6 completed bars before bar i. `trigger` field = trigger for the next bar (max high of the last 6 completed).
2. Breakout candle = latest completed bar today whose close > its trigger(i). `chart`: none → `below` (or `breakout_provisional` if the developing bar's last price > `trigger`); breakout is the latest completed bar → `breakout_confirmed`; later completed bars all close ≥ the breakout's trigger → `holding`; any later completed close < it → `failed`.
4. `pattern` (added 2026-09-22, trader: "the watchlist should surface both patterns"): **`explosive`** = among the last 6 completed 5m bars at least one bar has per-bar RVOL z ≥ 3 with close in its upper half, and `chart` is `breakout_confirmed` or `holding`. Else **`steady`** = build 15-minute candles from today's completed 5m bars (groups of 3 aligned to :00/:15/:30/:45); of the last 6 completed 15m candles, at least 4 of the 5 transitions have a higher high **and** a higher low, and the last completed 5m close is above ema20. Else `None`. Constants `EXPLOSIVE_Z=3.0`, `STEADY_CANDLES=6`, `STEADY_MIN_RISES=4`.
3. `state` precedence: `failed` → `fading`; last price > ema20 + 2·atr14 → `extended`; chart in confirmed/holding **and** last completed bar closed in top third of its range **and** row RVOL 5m or 15m bg in {cyan, green} → `building`; chart holding/confirmed with last 2 completed bars closing in lower half and both bar z-scores < 1 → `fading`; chart holding/confirmed → `holding`; `breakout_provisional` with RVOL condition → `building` with `provisional: True`; else `quiet`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import datetime, timezone

from momx import momentum

T0 = int(datetime(2026, 9, 21, 13, 30, tzinfo=timezone.utc).timestamp())  # 09:30 ET


def bars(closes, *, highs=None, lows=None, vols=None, start=T0):
    out = []
    for i, c in enumerate(closes):
        h = highs[i] if highs else c + 0.2
        l = lows[i] if lows else c - 0.2
        out.append({"time": start + 300 * i, "open": c, "high": h, "low": l, "close": c,
                    "volume": (vols[i] if vols else 1000)})
    return out


FLAT = [100.0] * 8
DONE = T0 + 300 * 40          # "now" long after the bars: all completed
RVOL_UP = {"5m": {"bg": "cyan"}}


def test_trigger_is_max_high_of_last_six_completed():
    s = momentum.summarize(bars(FLAT), None, now_epoch=DONE)
    assert s["trigger"] == 100.2
    assert s["chart"] == "below" and s["state"] == "quiet"


def test_breakout_close_near_high_with_rvol_is_building():
    b = bars(FLAT + [101.0], highs=[100.2] * 8 + [101.05], lows=[99.8] * 8 + [100.1])
    s = momentum.summarize(b, RVOL_UP, now_epoch=DONE)
    assert s["chart"] == "breakout_confirmed"
    assert s["state"] == "building" and s["provisional"] is False


def test_breakout_without_rvol_is_holding_not_building():
    b = bars(FLAT + [101.0], highs=[100.2] * 8 + [101.05], lows=[99.8] * 8 + [100.1])
    assert momentum.summarize(b, None, now_epoch=DONE)["state"] == "holding"


def test_close_back_below_trigger_fails_and_fades():
    b = bars(FLAT + [101.0, 100.0])
    s = momentum.summarize(b, RVOL_UP, now_epoch=DONE)
    assert s["chart"] == "failed" and s["state"] == "fading"


def test_developing_bar_above_trigger_is_provisional():
    b = bars(FLAT + [101.0])
    now = b[-1]["time"] + 60          # last bar still forming
    s = momentum.summarize(b, RVOL_UP, now_epoch=now)
    assert s["chart"] == "breakout_provisional"
    assert s["state"] == "building" and s["provisional"] is True


def test_far_above_ema_is_extended():
    closes = [100.0] * 30 + [100.0 + 2 * i for i in range(1, 6)]
    s = momentum.summarize(bars(closes), RVOL_UP, now_epoch=T0 + 300 * 60)
    assert s["state"] == "extended"


def test_explosive_pattern_needs_big_volume_bar_and_breakout():
    vols = [1000] * 30 + [9000]
    closes = [100.0] * 30 + [101.0]
    b = bars(closes, highs=[100.2] * 30 + [101.05], lows=[99.8] * 30 + [100.1], vols=vols)
    assert momentum.summarize(b, RVOL_UP, now_epoch=T0 + 300 * 60)["pattern"] == "explosive"


def test_steady_pattern_is_rising_15m_highs_and_lows():
    closes = [100.0 + 0.05 * i for i in range(30)]          # slow grind, every 15m candle higher
    s = momentum.summarize(bars(closes), None, now_epoch=T0 + 300 * 60)
    assert s["pattern"] == "steady"


def test_flat_tape_has_no_pattern():
    assert momentum.summarize(bars([100.0] * 30), None, now_epoch=T0 + 300 * 60)["pattern"] is None


def test_too_few_bars_returns_none():
    assert momentum.summarize(bars([100.0] * 3), None, now_epoch=DONE) is None
```

- [ ] **Step 2: Run to verify failure** — `.venv/Scripts/python.exe -m pytest tests/test_momx_momentum.py -q` → FAIL (module missing).

- [ ] **Step 3: Implement**

```python
"""Momentum now - separate from the setup grade (experimental draft rules).

Spec: docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md, "Momentum now".
Pure: 5m bars + the row's RVOL cells in, a small summary out.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from momx.columns import RVOL_LENGTH
from momx.indicators import ema, rvol_zscore

TRIGGER_LOOKBACK = 6
ATR_LENGTH = 14
EMA_LENGTH = 20
EXTENDED_ATR = 2.0
BUILD_RVOL_BG = frozenset({"cyan", "green"})
FADE_RVOL_Z = 1.0
BAR_SECONDS = 300
MIN_BARS = TRIGGER_LOOKBACK + 1
ET = ZoneInfo("America/New_York")


def _t(bar: Mapping) -> int:
    raw = bar.get("time", bar.get("timestamp"))
    if hasattr(raw, "timestamp"):
        return int(raw.timestamp())
    return int(raw)


def _et_date(epoch: int):
    return datetime.fromtimestamp(epoch, ET).date()


def _atr(bars: list[Mapping], length: int) -> float | None:
    if len(bars) < length + 1:
        return None
    trs = []
    for prev, bar in zip(bars[:-1], bars[1:]):
        trs.append(max(bar["high"] - bar["low"], abs(bar["high"] - prev["close"]), abs(bar["low"] - prev["close"])))
    return sum(trs[-length:]) / length


def _rvol_push(cells: Mapping | None) -> bool:
    if not isinstance(cells, Mapping):
        return False
    return any(isinstance(cells.get(tf), Mapping) and cells[tf].get("bg") in BUILD_RVOL_BG for tf in ("5m", "15m"))


def _position(bar: Mapping) -> float:
    span = bar["high"] - bar["low"]
    return 0.5 if span <= 0 else (bar["close"] - bar["low"]) / span


EXPLOSIVE_Z = 3.0
STEADY_CANDLES = 6
STEADY_MIN_RISES = 4


def _pattern(completed: list[Mapping], z: list[float], chart: str, ema20: float | None) -> str | None:
    recent = list(zip(completed[-6:], z[-6:]))
    if chart in ("breakout_confirmed", "holding") and any(
        math.isfinite(v) and v >= EXPLOSIVE_Z and _position(b) >= 0.5 for b, v in recent
    ):
        return "explosive"
    today = _et_date(_t(completed[-1]))
    groups: dict[int, list[Mapping]] = {}
    for b in completed:
        if _et_date(_t(b)) == today:
            groups.setdefault(_t(b) // 900, []).append(b)
    candles = [
        {"high": max(x["high"] for x in g), "low": min(x["low"] for x in g)}
        for _, g in sorted(groups.items()) if len(g) == 3
    ][-STEADY_CANDLES:]
    if len(candles) < STEADY_CANDLES or ema20 is None or completed[-1]["close"] <= ema20:
        return None
    rises = sum(b["high"] > a["high"] and b["low"] > a["low"] for a, b in zip(candles, candles[1:]))
    return "steady" if rises >= STEADY_MIN_RISES else None


def summarize(bars_5m: Any, rvol_cells: Mapping | None, *, now_epoch: float | None = None) -> dict | None:
    if not isinstance(bars_5m, list) or len(bars_5m) < MIN_BARS:
        return None
    now = time.time() if now_epoch is None else float(now_epoch)
    developing = now - _t(bars_5m[-1]) < BAR_SECONDS
    completed = bars_5m[:-1] if developing else list(bars_5m)
    if len(completed) < MIN_BARS:
        return None
    last_price = float(bars_5m[-1]["close"])

    def trigger_before(i: int) -> float | None:
        window = completed[max(0, i - TRIGGER_LOOKBACK):i]
        return max(b["high"] for b in window) if len(window) == TRIGGER_LOOKBACK else None

    trigger = trigger_before(len(completed))
    today = _et_date(_t(completed[-1]))
    breakout_index, breakout_trigger = None, None
    for i in range(len(completed) - 1, -1, -1):
        if _et_date(_t(completed[i])) != today:
            break
        level = trigger_before(i)
        if level is not None and completed[i]["close"] > level:
            breakout_index, breakout_trigger = i, level
            break

    if breakout_index is None:
        chart = "breakout_provisional" if developing and trigger is not None and last_price > trigger else "below"
    elif breakout_index == len(completed) - 1:
        chart = "breakout_confirmed"
    elif all(b["close"] >= breakout_trigger for b in completed[breakout_index + 1:]):
        chart = "holding"
    else:
        chart = "failed"

    closes = [float(b["close"]) for b in completed]
    ema_series = ema(closes, EMA_LENGTH)
    ema20 = ema_series[-1] if ema_series and math.isfinite(ema_series[-1]) else None
    atr14 = _atr(completed, ATR_LENGTH)
    z = rvol_zscore([float(b.get("volume") or 0) for b in completed], RVOL_LENGTH)
    rvol = _rvol_push(rvol_cells)
    last = completed[-1]

    provisional = False
    if chart == "failed":
        state = "fading"
    elif ema20 is not None and atr14 and last_price > ema20 + EXTENDED_ATR * atr14:
        state = "extended"
    elif chart in ("breakout_confirmed", "holding") and _position(last) >= 2 / 3 and rvol:
        state = "building"
    elif chart in ("breakout_confirmed", "holding") and len(completed) >= 2 and all(
        _position(b) < 0.5 for b in completed[-2:]
    ) and all(math.isfinite(v) and v < FADE_RVOL_Z for v in z[-2:]):
        state = "fading"
    elif chart in ("breakout_confirmed", "holding"):
        state = "holding"
    elif chart == "breakout_provisional" and rvol:
        state, provisional = "building", True
    else:
        state = "quiet"

    return {
        "trigger": None if trigger is None else round(float(trigger), 4),
        "chart": chart,
        "state": state,
        "pattern": _pattern(completed, z, chart, ema20),
        "provisional": provisional,
        "breakoutAt": None if breakout_index is None else _t(completed[breakout_index]),
        "lastCompleted": {k: last.get(k) for k in ("open", "high", "low", "close", "volume")} | {"time": _t(last)},
        "atr14": None if atr14 is None else round(atr14, 4),
        "ema20": None if ema20 is None else round(ema20, 4),
    }
```

- [ ] **Step 4: Run to verify pass** — `.venv/Scripts/python.exe -m pytest tests/test_momx_momentum.py -q` → `10 passed`. Then run `summarize` on the real 2026-09-21 5m tapes for META, QCOM, SPY and RIOT (fetch via `momx.feed.fetch_5m([...], days=2)` + `board.frame_to_bars`, `now_epoch` = 11:00 ET that day) and put the four results in the report — expected shape: META explosive, QCOM/SPY steady or explosive, RIOT not building. If RIOT comes out `building`, report it; do not tune thresholds. If `ema`/`rvol_zscore` names differ in `momx/indicators.py`, use the actual names (grep `def ema\|def rvol_zscore momx/indicators.py`) — do not reimplement them.

- [ ] **Step 5: Commit** (release note item "Momentum engine for the scanner grade (not visible yet)")

---

### Task 5: Rows carry `m5`, `sqzRaw`, `grade`

**Files:**
- Modify: `momx/columns.py:1148` (`build_row`)
- Modify: `momx/board.py:798` (`_contract_row`), `momx/board.py:726-731` (after news)
- Test: `tests/test_momx_board.py` (append)

**Interfaces:**
- Consumes: `momentum.summarize`, `grade.safe_grade_row`, `columns.squeeze_column_series`.
- Produces on every shipped row (rows and rest): `row["m5"]` (momentum summary or None), `row["sqzRaw"]` = `{tf: {"ms": bool, "hs": bool, "msc": int, "hsc": int, "msf": int, "hsf": int, "mfd": bool, "lastReleaseAt": int|None}}` for `4h`, `D`, `Wk`, and `row["grade"]` (grade dict or None).

- [ ] **Step 1: Failing test** (append to `tests/test_momx_board.py`, reusing its `StubFeed`/`_intraday_bars` helpers — read lines 60-130 first to call them exactly as existing tests do):

```python
def test_rows_and_rest_carry_grade_m5_and_sqz_raw(monkeypatch):
    payload = _build_with_stub_feed(monkeypatch)   # use the same helper the file's other build tests use
    for row in payload["rows"] + payload["rest"]:
        assert "grade" in row and "m5" in row and "sqzRaw" in row
        if row["grade"] is not None:
            assert row["grade"]["letter"] in ("A+", "A", "B", None)


def test_grading_failure_leaves_row_intact(monkeypatch):
    from momx import grade
    monkeypatch.setattr(grade, "grade_row", lambda *a, **k: 1 / 0)
    payload = _build_with_stub_feed(monkeypatch)
    assert payload["rows"] and all(r["grade"] is None for r in payload["rows"])
```

If the file has no `_build_with_stub_feed` helper, write it at the top of the new tests by copying the body of the nearest existing test that calls `board.build_board(..., feed_module=StubFeed(...))`.

- [ ] **Step 2: Run to verify failure** — `.venv/Scripts/python.exe -m pytest tests/test_momx_board.py -q -k "grade"` → FAIL (`KeyError: 'grade'`).

- [ ] **Step 3: Implement**

In `momx/columns.py` `build_row`, after the row's `rvol` and `sqz` cells are built and before `return`:

```python
    from momx import momentum  # local import: momentum imports columns
    row["m5"] = momentum.summarize(tapes.get("5m") if isinstance(tapes, dict) else None, row.get("rvol"))
    row["sqzRaw"] = _sqz_raw(tapes)
```

and add near `squeeze_cell`:

```python
def _sqz_raw(tapes: Any) -> dict:
    """Raw squeeze states behind the SQZ cells, for the grade event log only."""
    out: dict[str, Any] = {}
    for tf in ("4h", "D", "Wk"):
        bars = tapes.get(tf) if isinstance(tapes, dict) else None
        try:
            series = squeeze_column_series(bars) if bars else None
        except Exception:  # noqa: BLE001 - logging aid, never fatal
            series = None
        if not series or not series["MS"]:
            out[tf] = None
            continue
        i = len(series["MS"]) - 1
        release = next((j for j in range(i, -1, -1) if series["MF"][j] or series["HF"][j]), None)
        times = [b.get("time", b.get("timestamp")) for b in bars]
        out[tf] = {
            "ms": bool(series["MS"][i]), "hs": bool(series["HS"][i]),
            "msc": int(series["MSC"][i]), "hsc": int(series["HSC"][i]),
            "msf": int(series["MSF"][i]), "hsf": int(series["HSF"][i]),
            "mfd": bool(series["MFD"][i]),
            "lastReleaseAt": None if release is None else times[release],
        }
    return out
```

Check how `tapes` keys the weekly bucket (`"Wk"` per `build_tapes` docstring at `board.py:261`); use the actual key.

In `momx/board.py` `_contract_row`, add after `"hourHighLow"`:

```python
        # Scanner grade inputs + result (spec 2026-09-21). grade is filled after
        # news in build_board; m5/sqzRaw come from build_row.
        "m5": row.get("m5"),
        "sqzRaw": row.get("sqzRaw"),
        "grade": None,
```

In `build_board`, right after the two `row["news"] = ...` loops:

```python
    graded_at = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    for row in rows:
        row["grade"] = grade.safe_grade_row(row, graded_at)
    for row in rest:
        row["grade"] = grade.safe_grade_row(row, graded_at)
```

with `from momx import grade` at the top and `datetime, timezone` imported if not already.

- [ ] **Step 4: Run tests** — `.venv/Scripts/python.exe -m pytest tests/test_momx_board.py tests/test_momx_columns.py tests/test_momx_grade.py -q` → all pass.

- [ ] **Step 5: History guard test** (append to `tests/test_momx_scanner_history.py`): record a board twice where only `row["grade"]` differs, >60 s apart, assert the symbol still has **1** snapshot. Run it; it should pass without code changes (grade is not in the fingerprint). If it fails, remove `grade`/`m5`/`sqzRaw` from whatever builds the fingerprint in `momx/history.py:~176`.

- [ ] **Step 6: Commit** (release note item: "Scanner rows now carry the grade (screen change comes next)")

---

### Task 6: `momx/grade_log.py` — freshness, latch, age, tape, events

**Files:**
- Create: `momx/grade_log.py`
- Modify: `momx/service.py:479-511` (`_build_once`), `momx/service.py:837` (`status`)
- Test: `tests/test_momx_grade_log.py`

**Interfaces:**
- Consumes: payload rows with `grade`, `m5`, `sqzRaw`, `skittles`, `rvol`, `news`, `last`.
- Produces:
  - `class GradeLog(directory: Path)` with `apply(board: str, payload: dict, now: datetime) -> None` — mutates every row in `payload["rows"]` and `payload["rest"]` adding `row["gradeFresh"] = {"icons": [str], "ageMinutes": int|None, "firstToday": {"A+": {"at": iso, "price": float}|None, "A": ..., "B": ...}, "timeline": [{"at": iso, "what": str}]}`; appends the 5-minute tape; appends first-per-letter events. Never raises.
  - `status() -> {"tapeLastWriteAt": iso|None, "tapeEntriesToday": int, "eventsToday": int}`.
  - Files: `<dir>/momx_grade_tape/<Board>/<YYYY-MM-DD>.json` = `{"board","date","entries":{SYMBOL:[{"t": iso, "last", "letter", "checks", "m5state", "chart", "trigger"}]}}`; `<dir>/momx_grade_events/<Board>/<YYYY-MM-DD>.json` = `{"events":[{...spec fields...}]}`.
  - `FRESH_MINUTES = 15`, `TAPE_EVERY_SECONDS = 300`, `RETENTION_DAYS = 30`.
- Freshness rules: a timeline item is added when, between consecutive scans for a symbol, a SKIT tf bg becomes bullish (`cyan/green/lime/dark_green`) → `"SKIT {tf} bg {colour}"`; a RVOL tf bg becomes `cyan/green` → `"RVOL {tf} {value}"`; a SQZ tf bg becomes `cyan` → `"SQZ {tf} released"`; the news headline changes → `"news"`; `m5.chart` becomes `breakout_confirmed` → `"5m breakout confirmed"`. `icons` = distinct kinds (`SKIT`, `RVOL`, `SQZ`, `NEWS`) with an item in the last 15 min. `ageMinutes` = minutes since `firstToday[current letter]`. On process start, today's event file is loaded so `firstToday` survives a worker restart; the timeline does not (blank until the next change).

- [ ] **Step 1: Failing tests** — use `tmp_path` and explicit `now` datetimes; construct two payloads that differ only in `skittles["4h"]["bg"]` (`black` → `green`):

```python
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from momx.grade_log import GradeLog

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 9, 21, 9, 32, tzinfo=ET)


def row(bg4h="black", letter=None, last=100.0):
    return {"symbol": "AAA", "last": last,
            "skittles": {"4h": {"bg": bg4h, "fg": "cyan"}},
            "rvol": {}, "sqz": {}, "news": None, "m5": {"chart": "below", "state": "quiet", "trigger": 101.0},
            "grade": {"letter": letter, "checks": {"skit": 7}, "reasons": ["r"]}}


def test_bg_change_becomes_fresh_icon_and_timeline(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    p = {"rows": [row("green")], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(seconds=30))
    fresh = p["rows"][0]["gradeFresh"]
    assert fresh["icons"] == ["SKIT"]
    assert fresh["timeline"][-1]["what"] == "SKIT 4h bg green"


def test_icon_expires_after_15_minutes(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW)
    log.apply("Watchlist", {"rows": [row("green")], "rest": []}, NOW + timedelta(minutes=1))
    p = {"rows": [row("green")], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(minutes=17))
    assert p["rows"][0]["gradeFresh"]["icons"] == []


def test_first_a_plus_is_latched_and_logged_once(tmp_path):
    log = GradeLog(tmp_path)
    log.apply("Watchlist", {"rows": [row(letter="A+", last=100.0)], "rest": []}, NOW)
    p = {"rows": [row(letter="A+", last=105.0)], "rest": []}
    log.apply("Watchlist", p, NOW + timedelta(minutes=5))
    first = p["rows"][0]["gradeFresh"]["firstToday"]["A+"]
    assert first["price"] == 100.0
    assert p["rows"][0]["gradeFresh"]["ageMinutes"] == 5
    events = (tmp_path / "momx_grade_events" / "Watchlist" / "2026-09-21.json").read_text()
    assert events.count('"letter": "A+"') == 1


def test_latch_survives_restart(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [row(letter="A+")], "rest": []}, NOW)
    p = {"rows": [row(letter="A+", last=110.0)], "rest": []}
    GradeLog(tmp_path).apply("Watchlist", p, NOW + timedelta(minutes=9))
    assert p["rows"][0]["gradeFresh"]["firstToday"]["A+"]["price"] == 100.0


def test_tape_written_at_most_every_5_minutes(tmp_path):
    log = GradeLog(tmp_path)
    for s in (0, 60, 120, 301):
        log.apply("Watchlist", {"rows": [row()], "rest": []}, NOW + timedelta(seconds=s))
    import json
    tape = json.loads((tmp_path / "momx_grade_tape" / "Watchlist" / "2026-09-21.json").read_text())
    assert len(tape["entries"]["AAA"]) == 2


def test_apply_never_raises_on_garbage(tmp_path):
    GradeLog(tmp_path).apply("Watchlist", {"rows": [None, 5, {"symbol": None}], "rest": None}, NOW)
```

- [ ] **Step 2: Run to verify failure** — FAIL (module missing).

- [ ] **Step 3: Implement `momx/grade_log.py`.** Keep state in memory keyed by `(board, symbol)`: `prev` (last seen cells: skittles bg per tf, rvol bg per tf, sqz bg per tf, news headline, m5 chart), `timeline` (list, trimmed to today), `first` (per-letter dict, loaded from the event file on first touch of a board/day). Event record = `{"board","symbol","letter","at","price","reasons","checks","push": {"rvol": {tf: {"value","bg","barAt"}}, "news": {"headline","at","ageHours"}}, "sqzRaw", "skittles": {tf: {"fg","bg","bgChangedAt"}}, "hlDegree", "lastCompleted5m", "m5", "pctChange"}` where `bgChangedAt` is the time of the latest timeline item for that tf (None if unseen since start). Write files with a helper:

```python
def _atomic_write_json(path: Path, doc: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass  # a lost write costs that write, never the build
```

Tape and event documents are held in memory per (board, date) and rewritten whole (tape ≈ 3–5 MB/day at 358 symbols × 12/h × 16 h — acceptable; if a write takes > 200 ms in the Step 4 timing check, switch the tape to one JSON line per 5-minute sample, `*.jsonl`). Prune files older than 30 days once per day. Wrap the whole body of `apply` in `try/except Exception: return`.

- [ ] **Step 4: Run tests + timing check** — tests pass; then time `apply` on a synthetic 358-row payload: `.venv/Scripts/python.exe -c "..."` must be < 50 ms for a non-tape scan and < 300 ms for a tape scan.

- [ ] **Step 5: Wire into the worker.** In `momx/service.py` add

```python
def grade_dir() -> Path:
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    return Path(override) if override else ARTIFACTS_DIR


_GRADE_LOG = grade_log.GradeLog(grade_dir())
```

and in `_build_once` **before** `_write_disk(name, payload)`:

```python
    _GRADE_LOG.apply(name, payload, datetime.now(ZoneInfo(EASTERN_TZ)))
```

(before `_write_disk` so the cached payload and History both carry `gradeFresh`). Add `"grade": _GRADE_LOG.status()` to the dict returned by `status()`.

- [ ] **Step 6: Run the MomX suites** — `.venv/Scripts/python.exe -m pytest tests/test_momx_*.py -q` → all pass.

- [ ] **Step 7: Commit** (release note item: "The scanner now records every grade event")

---

### Task 7: Nightly outcomes + track record + worker endpoints

**Files:**
- Modify: `momx/grade_log.py` (add `nightly`, `record_response`, `tape_response`)
- Modify: `momx/service.py` (call nightly from `_loop`)
- Modify: `momx_worker.py:305-412` (two GET routes)
- Test: `tests/test_momx_grade_log.py` (append), `tests/test_momx_worker_routes.py` if a routes test file exists (grep `do_GET` in tests), else in `test_momx_grade_log.py` test the response builders directly.

**Interfaces:**
- Produces:
  - `GradeLog.nightly(now: datetime, fetch_5m: Callable[[list[str]], dict[str, list[dict]]]) -> bool` — once per ET day after 16:15: for today's events lacking `outcome`, fill `outcome = {"p5","p15","p30","p60","close","maxFav","maxAdv"}` (percent moves from event price; from 5m bars after the event time up to the 16:00 bar: `pN` = close of the first bar starting ≥ event+N min; `close` = close of the 15:55 bar; `maxFav`/`maxAdv` = max high / min low of bars after the event). Then rebuild `<dir>/momx_grade_record.json` = `{"source": "recorded", "days": [...], "letters": {L: {"count","pctHigher15","pctHigher60","pctHigherClose","avgToClose","medianToClose","avgMaxFav","avgMaxAdv"}}, "byMomentum": {L: {state: {...same}}}, "byFresh": {L: {"fresh": {...}, "notFresh": {...}}}}` over all event files with outcomes. Returns True when it ran.
  - `record_response(directory) -> dict`: the recorded record if it has ≥ 1 day, else `artifacts/momx_grade_record_backtest.json` with `"source": "back-test from History archive (N days)"`, else `{"source": None}`.
  - `tape_response(directory, symbol, days=5) -> {"symbol", "entries": [{"t","letter","reasons","m5state","chart","trigger","board"}]}` merged across `Watchlist` and `Mag7` for the last `days` ET dates, Watchlist first on duplicate `t`.
  - Routes: `GET /api/momx-scanner/grade-tape?symbol=META&days=5` → `tape_response`; `GET /api/momx-scanner/grade-record` → `record_response`. Symbol missing → 400.
- Consumes: the real `feed.fetch_5m(symbols, days=1)` in service, converted with `board.frame_to_bars`.

- [ ] **Step 1: Failing tests** for outcome math with a hand-built 5m bar list (event at 10:00 @ 100; bars 10:00…15:55 with a 10:10 high of 103 and a 10:40 low of 98; 15:55 close 101): expect `p5`, `p15` from bar closes, `maxFav == 3.0`, `maxAdv == -2.0`, `close == 1.0`; record aggregation over two events; `record_response` falls back to the back-test file; `tape_response` merges boards and filters by symbol.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** as specified; nightly marks `<dir>/momx_grade_record.json` `"builtFor": "<date>"` so it runs once per day; any exception → return False.
- [ ] **Step 4: Service loop:** in `_loop` after each cycle call `_GRADE_LOG.nightly(datetime.now(ZoneInfo(EASTERN_TZ)), _fetch_5m_bars)` where `_fetch_5m_bars(symbols)` wraps `feed.fetch_5m(symbols, days=1)` → `{s: board.frame_to_bars(f)}` inside try/except returning `{}`.
- [ ] **Step 5: Routes** in `momx_worker.py`, mirroring the `/history` route (avoid a local named `query`):

```python
            if path == "/api/momx-scanner/grade-tape":
                tape_args = parse_qs(parsed.query)
                symbol = str(tape_args.get("symbol", [""])[0]).strip().upper()
                if not symbol:
                    self._json(HTTPStatus.BAD_REQUEST, {"error": "symbol is required"})
                    return
                self._json(HTTPStatus.OK, grade_log.tape_response(
                    service.grade_dir(), symbol, days=max(1, min(10, _int_or_zero(tape_args.get("days", ["5"])[0]) or 5))))
                return
            if path == "/api/momx-scanner/grade-record":
                self._json(HTTPStatus.OK, grade_log.record_response(service.grade_dir()))
                return
```

(`service.grade_dir()` returns the same directory `_GRADE_LOG` uses.)
- [ ] **Step 6: Run all MomX tests → pass.**
- [ ] **Step 7: Commit** (release note item: "Grade track record and chart grade data")

---

### Task 8: Frontend display helpers `momxGrade.js`

**Files:**
- Create: `frontend/src/momxGrade.js`
- Test: `frontend/src/momxGrade.test.js`

**Interfaces:**
- Produces:
  - `MOMENTUM_LABEL = {building: "Building ↑", holding: "Holding", fading: "Fading ↓", extended: "Extended", quiet: "Quiet"}`
  - `setupText(row) -> string` e.g. `"A+ ⚡ · Building ↑"`, `"A 📈 · Holding"`, `"– 📈 · Holding"` (no letter but a pattern), `"A · Fading ↓"`, `""` when neither grade letter nor pattern nor non-quiet momentum; provisional → `"… · Building ↑ (prov.)"`. `PATTERN_ICON = {explosive: "⚡", steady: "📈"}`.
  - `setupTone(row) -> "aplus"|"a"|"b"|"none"` and `momentumTone(row) -> "up"|"down"|"flat"`.
  - `setupSortValue(row) -> number` = `LETTER_RANK*10 + MOMENTUM_RANK` with `MOMENTUM_RANK = {building:4, holding:3, extended:2, fading:1, quiet:0}`; no grade → -1.
  - `freshText(row) -> string` — the latest timeline item per kind within 15 min, with its ET clock time, then the age: e.g. `"SKIT 4h 09:32 · RVOL 5m 09:33 · 2m"`, `"– · 18m"`, `""`.
  - `freshSortValue(row) -> number` = `-(ageMinutes)`; no age → `-Infinity`.
  - `GRADE_DISCLAIMER = "A+ means the defined conditions align. It is not a recommendation and does not guarantee profit."`
  - `trackRecordLine(record, letter) -> string` e.g. `"A+ · 370 signals · 46% closed higher · avg +0.22% (back-test from History archive (13 days))"`; missing → `"track record unavailable"`.
  - `gradeAtTime(entries, epochSeconds) -> entry|null` — the latest tape entry with `t` ≤ time and ≥ time − 300 s, else null. (`t` is ISO; compare via `Date.parse`.)
  - `isBullishCallLabel(text, direction) -> boolean` — true when `direction === "bullish"` or the upper-cased text starts with `CALL` or matches `/^C\d/`; false for `PUT…`/`P\d…`.

- [ ] **Step 1: Failing tests** (node:test) covering each function, including: `setupText` for `{grade:{letter:"A+"}, m5:{state:"building"}}` → `"A+ · Building ↑"`; sort order A+Building > A+Fading > A Building > none; `freshText` with icons and with only age; `gradeAtTime` picks the 09:35 entry for a 09:37 label, returns null for a label 6 min after the last entry and for a label before the first; `isBullishCallLabel("CALL5")`, `("c5")` true, `("P15")`, `("PUT1H")` false; `trackRecordLine` with a record and with `null`.
- [ ] **Step 2: Run** `cd frontend && node --test src/momxGrade.test.js` → FAIL.
- [ ] **Step 3: Implement** the functions exactly as listed (pure, no DOM).
- [ ] **Step 4: Run → pass.**
- [ ] **Step 5: Commit** (release note item folded into Task 9's, since nothing is visible yet — commit together with Task 9 if the hook requires a note for `frontend/src` changes).

---

### Task 9: Scanner columns, why-panel, sort, filter

**Files:**
- Modify: `frontend/src/MomxScannerPanel.jsx` (`MOMX_COLUMNS` ~259; `columnSortValue` ~506; `MomxRow` ~1828 chain; `MomxHistoryRow` ~2171 chain; fetch of `grade-record`)
- Create: `frontend/src/MomxGradeWhy.jsx`
- Modify: `frontend/src/momxFilters.js` (filters `setup`, `momentum`; bump `MOMX_FILTER_VERSION` to 4 with migration keeping existing fields)
- Modify: `frontend/src/index.css` (append at END of file — see memory: earlier @media blocks are outranked)
- Test: `frontend/src/momxFilters.test.js` (append), `frontend/src/momxHistory.test.js` (parity still passes)

**Interfaces:**
- Consumes: `momxGrade.js` helpers; row fields `grade`, `m5`, `gradeFresh`; `GET /api/momx-scanner/grade-record`.
- Produces: column defs `{ key: "setup", label: "Setup", kind: "setup", sortable: true }` and `{ key: "fresh", label: "Fresh", kind: "fresh", sortable: true }` inserted right after `symbol` in `MOMX_COLUMNS`; filter config fields `setup: "any"|"aAndUp"|"aPlus"`, `momentum: "any"|"building"`, `pattern: "any"|"explosive"|"steady"|"either"`.

- [ ] **Step 1: Failing filter tests** in `momxFilters.test.js`: `rowPasses` with `setup:"aPlus"` keeps only `grade.letter==="A+"`; `"aAndUp"` keeps A+ and A; `momentum:"building"` keeps `m5.state==="building"`; a v3 stored config migrates to v4 with `setup:"any", momentum:"any"` and its other fields intact.
- [ ] **Step 2: Run → FAIL; implement in `momxFilters.js`; run → pass.**
- [ ] **Step 3: Columns + cells.** Add the two defs after `symbol`. In `MomxRow`'s kind chain add:

```jsx
        if (column.kind === "setup") {
          const text = setupText(row);
          return (
            <td key={column.key} className={`momx-cell momx-setup is-${setupTone(row)} mom-${momentumTone(row)}`}
                title={row?.m5 ? `5m: ${row.m5.chart} · trigger ${row.m5.trigger ?? "–"}` : ""}>
              {text ? (
                <button type="button" className="momx-setup-button" onClick={(event) => { event.stopPropagation(); onOpenGrade?.(row); }}>
                  {text}
                </button>
              ) : null}
            </td>
          );
        }
        if (column.kind === "fresh") {
          return <td key={column.key} className="momx-cell momx-fresh">{freshText(row)}</td>;
        }
```

Add the same two branches to `MomxHistoryRow` (History rows carry the same fields). Thread `onOpenGrade` as a prop exactly the way the existing row click handlers are threaded (grep the props `MomxRow` receives and add one); keep `MomxRow`'s `memo` comparison working — if it has a custom comparator, include `onOpenGrade` stability by passing a `useCallback`.
- [ ] **Step 4: Sort.** In `columnSortValue` add before the fallback:

```js
  if (column.kind === "setup") return setupSortValue(row);
  if (column.kind === "fresh") return freshSortValue(row);
```

- [ ] **Step 5: Why-panel.** `MomxGradeWhy.jsx` renders a fixed-position dialog (reuse the class names/pattern of the existing FILTERS dialog `MomxFiltersPanel` ~1278 for overlay, close button, Escape key) showing: title `Scanner grade {letter} (experimental)`; "First {letter} today {HH:MM} @ ${price}"; sections Alignment (list of 8 SKIT tfs with ✓/✗ from `grade.checks.skitBullish`), Push (RVOL tfs + values; news headline + age, separately), Squeeze (`sqzFired`/`sqzCoiling`), Location (`hlDegree`), Momentum (`MOMENTUM_LABEL[m5.state]`, 5m chart, trigger), Timeline (`gradeFresh.timeline`), the track-record line, and `GRADE_DISCLAIMER`. Panel state: `const [gradeRow, setGradeRow] = useState(null)`; fetch `grade-record` once on mount and every 30 min.
- [ ] **Step 6: FILTERS UI.** In `MomxFiltersPanel` add two select rows ("Setup: Any / A and up / A+ only", "Momentum: Any / Building only") using the same markup as the existing rows.
- [ ] **Step 7: CSS** (append at end of `index.css`): `.momx-setup.is-aplus{color:#39ff88;font-weight:700}` `.is-a{color:#6fdc8c}` `.is-b{color:#9aa0a6}` `.mom-up .momx-setup-button::after{}` (arrow is in text; colour via `.mom-up{--mom:#39ff88}` `.mom-down{--mom:#ff5c7a}`), `.momx-setup-button{all:unset;cursor:pointer;white-space:nowrap}`, `.momx-fresh{white-space:nowrap;color:#9fd8ff}`, widths `min-width:110px` / `90px`.
- [ ] **Step 8: Run JS tests** `cd frontend && node --test src/momx*.test.js` → pass (history parity test must still pass: the two columns flow into History automatically).
- [ ] **Step 9: Build** `cd frontend && npm run build` → success.
- [ ] **Step 10: Commit** with release note: heading "Scanner grade (experimental): Setup and Fresh columns"; items in trader language, including the disclaimer sentence.

---

### Task 10: Column manager

**Files:**
- Create: `frontend/src/momxColumnLayout.js`, `frontend/src/momxColumnLayout.test.js`, `frontend/src/MomxColumnManager.jsx`
- Modify: `frontend/src/MomxScannerPanel.jsx` (~3306 column list selection; group spans; toolbar button)

**Interfaces:**
- Produces:
  - `LAYOUT_STORAGE_KEY = "momx.scanner.columnLayout"`, `LAYOUT_VERSION = 1`, `LOCKED_KEYS = new Set(["symbol"])`
  - `normalizeLayout(layout, defaultKeys) -> {version, order: string[], hidden: string[]}` — drops unknown keys, inserts missing default keys at their default index (relative to the nearest preceding default key present), removes locked keys from `hidden`.
  - `applyLayout(columns, layout) -> columns[]` — reorders by `order`, filters `hidden`; columns not in `order` (e.g. the Time column inserted by `liveColumns`/`historyColumns`) keep their position right after the column that precedes them in the input.
  - `moveKey(layout, key, delta) -> layout`, `toggleHidden(layout, key) -> layout`, `defaultLayout(defaultKeys) -> layout`
  - `readStoredLayout(defaultKeys)`, `writeStoredLayout(layout)` — try/catch, fall back to default.
- Consumes: `columnGroupSpans(columns)` (existing, ~306) recomputed from the applied list.

- [ ] **Step 1: Failing tests:** move right/left and at the edges; hide/show; `symbol` cannot be hidden; unknown stored key dropped; a new default key missing from a stored layout appears at its default spot; `applyLayout` keeps an unlisted `matchedSince`/`time` column after `symbol`; storage throwing → default; group spans for `[rvol.2h, highLow, rvol.5m]` after moving `highLow` to the end give one RVOL span of 2 then H/L.
- [ ] **Step 2: Run → FAIL; implement; run → pass.**
- [ ] **Step 3: Wire into the panel.** Replace the precomputed `MOMX_*_COLUMN_GROUPS` use at render with `useMemo(() => columnGroupSpans(applied), [applied])`, where `applied = useMemo(() => applyLayout(liveColumnList, layout), [liveColumnList, layout])`; do the same for the History table's column list. `layout` state: `useState(() => readStoredLayout(MOMX_COLUMNS.map(c => c.key)))` + `useEffect(() => writeStoredLayout(layout), [layout])`.
- [ ] **Step 4: Dialog.** `MomxColumnManager.jsx`: list of columns in current order, each row `label (group)` + ◀ ▶ buttons + checkbox (disabled for `symbol`), footer "Reset to default". Toolbar button "Columns" next to FILTERS, same button classes.
- [ ] **Step 5: JS tests + build → pass.**
- [ ] **Step 6: Commit** with release note: "Rearrange and hide scanner columns (Columns button). Saved per device."

---

### Task 11: Chart grade circles on bullish CALL labels

**Files:**
- Modify: `frontend/src/App.jsx` (~21297–21560 bubble builders; a fetch effect near other chart data effects)
- Modify: `frontend/src/tosNativeChartPrimitive.js` (~750–784 geometry; ~978–1037 drawing)
- Test: `frontend/src/tosNativeChartPrimitive.test.js` (append), `frontend/src/momxGrade.test.js` (lookup already covered)

**Interfaces:**
- Consumes: `GET /api/momx-scanner/grade-tape?symbol=&days=5`; `gradeAtTime`, `isBullishCallLabel`, `GRADE_DISCLAIMER`, `trackRecordLine`.
- Produces: bubble field `grade: {letter, reasons, at}`; primitive label geometry field `grade` (letter string or undefined).

- [ ] **Step 1: Primitive test (fails first):** geometry built from a signal with `grade: {letter: "A+"}` has `label.grade === "A+"`; a signal without it has `label.grade === undefined`. Find how existing tests construct geometry (grep `labels` in `tosNativeChartPrimitive.test.js`) and mirror it.
- [ ] **Step 2: Implement primitive.** In the label geometry map (~750–784) add `grade: typeof signal.grade?.letter === "string" ? signal.grade.letter : undefined`. In the drawing loop, after `context.fillText(label.text, ...)` and before `context.restore()`:

```js
        if (label.grade) {
          const radius = 7;
          const cx = left + boxWidth + radius + 3;
          const cy = top + boxHeight / 2;
          context.beginPath();
          context.arc(cx, cy, radius, 0, Math.PI * 2);
          context.fillStyle = "#0b1116";
          context.fill();
          context.lineWidth = 1.5;
          context.strokeStyle = label.grade === "A+" ? "#39ff88" : label.grade === "A" ? "#6fdc8c" : "#9aa0a6";
          context.stroke();
          context.fillStyle = context.strokeStyle;
          context.font = "bold 8px sans-serif";
          context.fillText(label.grade, cx, cy + 0.5);
        }
```

Make sure the label's horizontal clamp accounts for the extra `2*radius+3` px when `label.grade` is set (find the `left` clamp near the drawing code and widen the effective box by that amount).
- [ ] **Step 3: App.jsx fetch.** Add a `useEffect` keyed on `normalizedChartSymbol` that fetches `/api/momx-scanner/grade-tape?symbol=${encodeURIComponent(sym)}&days=5` with `{ cache: "no-store" }`, stores `entries` in a ref-backed state, refreshes every 60 s, and clears on symbol change (stale data after switching must never show). 404/failed fetch → empty entries, no error UI.
- [ ] **Step 4: Attach grades.** In each of the three bubble builders (tos-mtf ~21371, ganesh ~21446, cloudmax ~21502) add to the pushed object:

```js
          grade: isBullishCallLabel(label, signal.direction)
            ? (() => { const e = gradeAtTime(gradeTapeEntries, Number(signal.time)); return e && e.letter ? { letter: e.letter, reasons: e.reasons, at: e.t } : undefined; })()
            : undefined,
```

using each builder's own label/text variable and time field (`signal.time` / `sourceTime` for ganesh — use the time the label is drawn at). Add `gradeTapeEntries` to the memo/effect dependency list that builds the bubbles.
- [ ] **Step 5: Hover.** If the chart has an existing label hover/tooltip path, add "Scanner grade {letter} at {HH:MM} — not a chart-entry confirmation", the reasons, the track-record line and `GRADE_DISCLAIMER`. If no label hover exists, skip hover in this task and note it in the handoff (do not invent a tooltip system here).
- [ ] **Step 6: JS tests + build → pass.**
- [ ] **Step 7: Commit** with release note: "Chart: scanner-grade circles (A+/A/B) beside bullish CALL labels on Watchlist/Mag7 tickers."

---

### Task 12: Deploy + full verification (AGENTS.md)

- [ ] **Step 1: Full suites** — `.venv/Scripts/python.exe -m pytest tests -q -x` and `cd frontend && node --test src/*.test.js`. Record pass counts. Any failure unrelated to this work: report, do not "fix" silently.
- [ ] **Step 2: Production build** — `cd frontend && npm run build`.
- [ ] **Step 3: Restart only `momx_worker`** (after 16:00 or before 09:00 ET; watchdog relaunches it) — kill the two `momx_worker.py` python processes; confirm exactly one pair is back and `curl -s http://127.0.0.1:3010/api/momx-scanner/status` shows `grade`. Do **not** restart api_server (the proxy is a prefix match; no restart needed).
- [ ] **Step 4: Data checks** — `curl` the board: every row has `grade`, `m5`, `gradeFresh`; `grade-record` returns the back-test source; `grade-tape?symbol=META` returns entries after 5 minutes.
- [ ] **Step 5: Browser (:5173)** — scanner Live + History at desktop and ~800 px: Setup/Fresh render, click A+ opens why-panel with disclaimer, sort both columns, both filters, Columns dialog move/hide/reset persists after reload, group headers correct after moving an RVOL column. Chart: single, multi (2+ charts), big-screen, fullscreen; 5m/15m/1h/4h/D; two different tickers (one Watchlist, one not → no circles); switch ticker/timeframe (no stale circles); zoom, pan, crosshair unaffected; console free of errors.
- [ ] **Step 6: Live-collection check** — during the next market session: `status.grade.tapeLastWriteAt` advances every ~5 min, `eventsToday` grows; after 16:15 ET `grade-record` switches to `"source": "recorded"`.
- [ ] **Step 7: Handoff report** — what was tested, what could not be (e.g. phone width if resize fails), known limits (grades only for Watchlist/Mag7 and only from deploy day; outcomes from 5m bars, not 1m; freshness blank after a worker restart until the next change).
