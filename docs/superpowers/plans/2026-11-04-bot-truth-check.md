# BOT Truth Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show Ganesh, on the BOT page, what really happened to his Alpaca paper mid orders next to what the virtual BOT claims. Also stop scoring options with no buyer as sellable.

**Architecture:** A pure module `momx/truth_check.py` reads the paper-mirror day files and the BOT day files (bull and bear), then joins each paper order to its BOT trade on (side, rule, symbol, signal time). api_server serves it read-only at `/api/momx-scanner/bot-truth` (admin only, 60 s cache), the same way `/api/momx-scanner/bot` is served from files today. BotPanel shows a "Truth check" card. The $0-bid rule goes into `momx/rule_record.py` (Record column) and `frontend/src/momxBot.js` (BOT page).

**Tech Stack:** Python 3 stdlib, React (Vite), pytest, node --test, e2e harness `tests/e2e/harness.mjs`.

**Spec:** `docs/superpowers/specs/2026-11-04-agentic-ai-trading-design.md` (Stage 1, revised).

## Global Constraints

- Work in the live repo `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2` on `main`. Commit only your own paths (`git commit -- <paths>`).
- The BOT page stays mid-only. Ganesh: "Remove all use only mid order" (2026-10-16).
- Read-only: the truth check never writes files and never calls Alpaca. It reads only the saved day files.
- Unit + integration + e2e tests for the feature, plus a `tests/FEATURE_TESTS.md` entry.
- A release-notes entry at the top of `frontend/public/release-notes.json`, stamped in ET and written in trader language.
- No full suites between 09:30 and 16:00 ET.

## Review Focus

1. A paper row carried across days (`carried: true`, the same key in several day files): count it once, using the latest file's status.
2. A paper order with no matching BOT trade, or a BOT trade marked `badQuote`: count it in orders/fill rate but leave it out of the P/L comparison, and report it as `unmatched`.
3. Orders still live (`buying`, `open`, `selling`, `exit_due`): neither filled-closed nor missed. Report them as `open`.
4. A BOT exit with `exitBid == 0`: value it at $0 (unsellable) in the truth check, the Record column and the BOT page alike.
5. No paper files at all (a fresh install): the route returns zeros with `available: false`, and the card says "No paper orders yet" instead of failing.

---

### Task 1: truth_check module

**Files:**
- Create: `momx/truth_check.py`
- Test: `tests/test_momx_truth_check.py`

**Interfaces:**
- Produces:
  - `bot_entry_value(t: dict) -> float | None`: the mid of `entryBid/entryAsk` when both are valid, else `entry`.
  - `bot_exit_value(t: dict) -> float | None`: `0.0` when `exitBid == 0` (unsellable); else the mid of `exitBid/exitAsk` when valid; else `exitPrice`; `None` when there's no exit.
  - `compare(paper: dict[str, list[dict]], bot: dict[tuple, dict]) -> dict`: `paper` maps account to its rows (deduped); the `bot` key is `(side, rule, symbol, at)`.
  - `build(artifacts: Path, days: int = 30, today: str | None = None) -> dict`: reads `artifacts/momx_paper_mirror/<account>/*.json` (newest first, first key wins) and `artifacts/momx_option_track/<day>.json` (bull) + `artifacts/bear/momx_option_track/<day>.json` (bear), and keeps orders whose `at` day falls within the last `days` calendar days.
  - Report shape: `{"available": bool, "from": str, "through": str, "accounts": {acct: {"orders", "filled", "missed", "open", "unmatched", "fillRatePct", "matched": {"n", "botPnl", "botRoi", "paperPnl", "paperRoi"}, "missedBotPnl", "biggestMissed": [{"day","symbol","side","rule","botEntry","limit","botPct"}] (top 5 by botPct), "byRule": [{"side","rule","orders","missed","fillRatePct","botRoi","paperRoi"}] (sorted by orders desc)}}}`.
  - `fillRatePct = filled/(filled+missed)*100`, where filled = orders that got a buy fill (`buyFill` set). `botPnl` = Σ (exit−entry)·100·qty over matched closed; `paperPnl` = Σ `pl`; ROI = P/L ÷ cost (BOT cost = entry·100·qty; paper cost = buyFill·100·qty). `missedBotPnl` = Σ (exit−entry)·100·qty over missed orders with a priced BOT trade. Round money to 2 dp and percentages to 1 dp.

- [ ] **Step 1: Write failing tests**: `test_exit_zero_bid_is_unsellable` (`bot_exit_value({"exitPrice":0.01,"exitBid":0,"exitAsk":0.01}) == 0.0`); `test_exit_uses_mid` (bid 1.0 / ask 1.2 → 1.1); `test_compare_counts_missed_filled_open` (one closed, one missed, one buying → filled 1, missed 1, open 1, fillRatePct 50.0); `test_compare_matched_pnl` (BOT 1.00→1.50 qty 3 → botPnl 150.0, botRoi 50.0; paper buyFill 1.05, pl 90 → paperRoi 28.6); `test_unmatched_and_bad_quote_excluded`; `test_build_dedupes_carried_rows_latest_wins` (tmp dirs, key in 11-03 as buying and 11-04 as closed → closed); `test_build_reads_bear_dir`; `test_build_empty_is_unavailable`.
- [ ] **Step 2:** `.venv\Scripts\python.exe -m pytest tests/test_momx_truth_check.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement `momx/truth_check.py` (stdlib only, never raises on a bad file: skip it).
- [ ] **Step 4:** Same command → PASS.

### Task 2: $0-bid rule in Record and BOT page

**Files:**
- Modify: `momx/rule_record.py` (`summarize`: when `exitBid == 0`, gross uses an exit of 0; keep the trade, don't exclude it)
- Modify: `frontend/src/momxBot.js` (`botLegResult`, "exit"/"close" path with mid fills: a recorded `bid === 0` → price 0)
- Test: `tests/test_momx_rule_record.py` (create if missing) `test_zero_bid_exit_scores_zero`; `frontend/src/momxBot.test.js` `zero exit bid is unsellable`.

- [ ] **Step 1:** Write the two failing tests. Python: an entry 0.005 with exit 0.01 / bid 0 / ask 0.01 gives `grossPnl == -0.5`. JS: `botLegResult({entry:0.5, entryBid:0.45, entryAsk:0.55, exitPrice:0.01, exitBid:0, exitAsk:0.02, status:"closed", exitAt:"…"}, "main","exit","mid").pct === -100`.
- [ ] **Step 2:** Run both and expect them to FAIL.
- [ ] **Step 3:** Implement. In `rule_record.exclusion`, the `invalid_midpoint` check must skip the exit leg when `exitBid == 0`.
- [ ] **Step 4:** Run both and expect them to PASS, plus the existing `momxBot.test.js` and rule-record tests.

### Task 3: route `/api/momx-scanner/bot-truth`

**Files:**
- Modify: `api_server.py`: a new branch just before `if parsed.path == "/api/momx-scanner/bot":`. Admin only (403 `{"error":"admin only"}` otherwise). `days` query clamped 1–60, default 30. `truth_check.build(ARTIFACTS_DIR, days)` cached 60 s per `days` in a module dict under a lock.
- Test: `tests/test_momx_bot_truth_api.py` (`running_api()`): seed scratch artifacts with one paper3 day file + one BOT day file and assert 200 with the matched numbers. A non-admin relayed visitor (`RELAYED` header) gets 403.

- [ ] Steps: failing test → implement → pass.

### Task 4: Truth check card on the BOT page

**Files:**
- Create: `frontend/src/botTruth.js`: `truthHeadline(acct) -> string` (plain language: "Your mid orders filled 34 of 100 (34%). On the trades that filled, the BOT says −5.0%, your paper account got −10.7%.") and `truthGapLabel(botRoi, paperRoi) -> string`.
- Modify: `frontend/src/BotPanel.jsx`: `TruthCheckCard` fetches `/api/momx-scanner/bot-truth?days=30` (polled every 60 s), rendered at the top of the page above the books. Per account: headline, KPIs (Fill rate, BOT ROI, Paper ROI, BOT profit on missed orders), a "Biggest BOT wins you never got" list, and a collapsible per-rule table. Empty → "No paper orders yet".
- Modify: `frontend/src/index.css` (card styles reuse the existing `.bot-*` classes; wrap the table in a horizontal scroller at phone width).
- Test: `frontend/src/botTruth.test.js`; `tests/e2e/trading-bot-truth.e2e.test.mjs` (fixtures answer `/api/momx-scanner/bot-truth`, then assert the card's headline at desktop and 375 px with no horizontal page scroll).

- [ ] Steps: failing tests → implement → pass.

### Task 5: Ship

- [ ] Add a `tests/FEATURE_TESTS.md` row and a release-notes entry (top, ET stamp).
- [ ] Run the focused Python tests, `node --test frontend/src/momxBot.test.js frontend/src/botTruth.test.js`, the e2e spec, and `npm run build` in `frontend`.
- [ ] Check in the browser: BOT page at desktop and 375 px, numbers match the scratch audit (Paper 3: 212 missed / 105 closed; Paper 5: 53 / 22, before date filtering).
- [ ] Commit only these paths.
