# OI Auto Alerts — design

**Date:** 2026-08-16 (Sunday night; first live session is Monday 2026-08-17)
**Repo checkout:** `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2` on `OI-scanner-BOT` (what the :3001 API, :5173 dev server and :4173 preview actually run).

## Goal

No more hand-made price alerts on OI walls. Every trading morning the server
builds, per ticker, the same directional High-OI strike ladder the Charts & OI
indicator draws, arms it, and then reports — with the *next* OI target — when
price confirms through a level. MAG7 is on by default; the trader adds any
other ticker from a new left-side "Auto Alert" panel on the Charts & OI page.

## Rules the trader agreed to (from the conversation)

| Rule | Behaviour |
|---|---|
| Ladder source | Schwab/TOS option chain, 0–31 DTE, expirations through the next monthly OPEX (fallback: all in window). Reported `openInterest` only — no Gamma×OI. |
| Ladder shape | Calls **above** spot ascending, puts **below** spot descending. Delta band 0.14–0.50 **or** OI ≥ 30 % of that side's max (dominant walls never vanish). One row per strike at its dominant expiry (largest OI wins, never summed). Top 15 by OI per side; strength strong ≥ 66 %, moderate ≥ 33 %, else weak, relative to the side leader. |
| Build time | 9:15 AM ET on trading days (Alpaca clock decides holidays; weekday fallback). Also on server start when today's ladders are missing, when a ticker is added, and on the panel's "Refresh levels" button. |
| Touch | A wick through the active level (high ≥ call strike / low ≤ put strike) is an early warning: "TSLA touching CALL OI 345 · not confirmed". It never advances the ladder. Reported once per level. Checked every minute from 1-minute bars during RTH and from each completed 5-minute bar. |
| Confirm | Only a **completed** regular-hours 5-minute candle (09:30–16:00 ET, first close 09:35) whose close is above the call level / below the put level. Then the next pending level becomes the active target. Gaps confirm every level the close cleared in one event ("345 → 350 CONFIRMED"). Same bar is never processed twice. |
| Message | `TSLA ↑ CALL OI 345 CONFIRMED · 5m close 345.25 · Next OI target 350 (17.8K OI) · $4.75 / 1.38% away`; puts say `BROKEN`; the last level says `No further OI target in the ladder`. |
| Persistence | Ladders, progress and the last 200 events live in `app_settings` (SQLite) so monitoring continues without the chart open and survives a restart. |

## Architecture

```
oi_auto_alerts.py (pure)                api_server.py (DashboardState)                 App.jsx
─────────────────────────               ───────────────────────────────                ─────────────────
build_oi_ladder(rows, spot)   ◄──────── _oi_auto_alert_refresh_symbol()  ◄─9:15 ET──  OiAutoAlertPanel
apply_completed_five_minute_bar          _oi_auto_alert_worker_loop()  ─5m/1m RTH─►    polls GET /api/oi-auto-alerts
apply_intrabar_touch                     oi_finder_payload(force,compact,bg)            every 10 s, diffs event ids,
decorate_alert_row / messages            market_data_client.get_chart_bars(1Min)        plays playPriceAlertTone(),
next_refresh_at / bar helpers            repository.set_app_setting(...)                shows Notification, toast
```

### `oi_auto_alerts.py` (done, 21 unit tests)
Pure functions, no I/O: symbol normalisation, `next_monthly_opex`, `build_oi_ladder`, `new_alert_row`, `apply_completed_five_minute_bar`, `apply_intrabar_touch`, `decorate_alert_row`, `format_event_message`, `next_refresh_at`, `latest_completed_five_minute_bar_end`, `five_minute_bar_from_minute_bars`.

Row shape (persisted per symbol):
```
symbol, sourceGroup ('mag7'|'manual'), source, status, message, sessionDate, levelsUpdatedAt,
spot, monthlyExpiry, callLevels[], putLevels[],
confirmedCallStrikes[], confirmedPutStrikes[], touchedCallStrikes[], touchedPutStrikes[],
lastCallEvent, lastPutEvent, lastProcessedBar, lastClose, lastBar, lastTouchCheckAt
```
`decorate_alert_row` adds `activeCall/nextCall/activePut/nextPut` (with `distance`, `distancePercent` from last close or spot) and `callState/putState ∈ armed|touched|confirmed|done|empty`.

### `DashboardState` additions (api_server.py)
* State: `oi_auto_alert_lock`, `_enabled`, `_include_mag7`, `_manual_symbols` (max 20), `_rows{}`, `_events[]` (≤200), `_status/_message`, `_last_refresh_date`, `_last_run/_next_run`, `_last_error`, `_wakeup` Event, refresh request flags. Loaded from `app_settings` keys `oi_auto_alert_*` in `__init__`, worker started with the other loops.
* `_oi_auto_alert_symbols()` → MAG7 (`MAGNIFICENT_SEVEN`) when included + manual list.
* `_oi_auto_alert_refresh_levels(symbols=None, reason)`: for each symbol `oi_finder_payload(sym, force=True, compact=True, background_snapshot=True)` → rows = `selectedExpiryChainRows`, spot = `underlyingPrice` → `build_oi_ladder` → `new_alert_row`, preserving today's confirmed/touched sets when the symbol already had a row for the same session. 0.35 s pause between symbols. Failures leave a row with `status: unavailable` and the error message.
* `_oi_auto_alert_worker_loop()` (daemon thread, 1 s ticks, wakeable):
  1. Startup / requested refresh: build missing ladders.
  2. `now ≥ next_refresh_at` and trading day → morning refresh, resets progress.
  3. During RTH every 60 s: `get_chart_bars(sym, "1Min", days_back=1)`; run `apply_intrabar_touch` over the completed 1-minute bars since the last check; when `latest_completed_five_minute_bar_end` advanced past `lastProcessedBar`, aggregate with `five_minute_bar_from_minute_bars` and run `apply_completed_five_minute_bar`. New events get `message` and are appended, persisted, and logged via `repository.log_bot_event`.
  4. Persist after every change (debounced to one write per pass).
* Trading-day check: `self.client.get_clock()` — `is_open` or `next_open` on the same ET date; on error, weekday.
* Endpoints (`ApiHandler`, behind the normal auth gate):
  * `GET /api/oi-auto-alerts` → `{enabled, includeMag7, manualSymbols, mag7Symbols, status, message, lastRefreshAt, nextRefreshAt, lastEvaluatedAt, lastError, session:{...}, rows:[decorated], events:[latest 60, newest first]}`
  * `POST /api/oi-auto-alerts/settings` `{enabled?, includeMag7?, manualSymbols?}`
  * `POST /api/oi-auto-alerts/symbols` `{add?: "AMD", remove?: "AMD"}` — add builds its ladder immediately (async)
  * `POST /api/oi-auto-alerts/refresh` `{symbol?}` — rebuild now
  Every POST returns the same payload as GET.

### Frontend (`FullChartsAndOiBoard`, App.jsx + index.css)
* New grid child `<aside class="charts-oi-auto-alerts">` before the chart; root gets `is-alerts-open` / `is-alerts-closed`. Desktop grid gains a 300 px first column (260 px at ≤ 1220 px); collapsed state is a 34 px rail with a bell + unseen-event badge. Open/closed persisted in `localStorage.chartsOiAutoAlertPanelOpen` (default open on desktop). Hidden with `display:none !important` on phones (≤ 760 px) for now — the mobile bottom-tab variant is a follow-up.
* Panel content: header (AUTO ALERT, status pill, next refresh), controls (Enabled, MAG7 toggles, Refresh, Notifications/sound enable), add-ticker input (Enter/Add, validated by the existing symbol regex, chips with ×), ticker cards (spot, CALL row and PUT row each showing active target with OI/strength/state pill and next target, confirmed strikes ticked), event feed (newest first, click card → `onLinkedSymbolChange(symbol)`).
* Polling `GET /api/oi-auto-alerts` every 10 s; new event ids (not seen since mount) → `playPriceAlertTone()`, `new Notification(...)` when permitted, and a 8 s in-panel toast.
* Helper module `frontend/src/oiAutoAlerts.js` (+ node:test) for event diffing, formatting and level state labels.

## Error handling
* Chain unavailable → row `status: "unavailable"` with the provider error; other tickers unaffected; retried at the next refresh or manually.
* Bars unavailable → skip that symbol this pass; `lastError` shows in the panel; no state change.
* Persist failures are logged and retried next pass; in-memory state remains authoritative until then.
* Server restart mid-session: rows/events reload from `app_settings`; the worker resumes at the next 1-minute tick; a bar that closed while the server was down is still evaluated as long as it is the latest completed one (older missed bars are skipped, not replayed).

## Testing
* `tests/test_oi_auto_alerts.py` — 21 unit tests (ladder, touch, confirm, gaps, dedupe, messages, schedule, aggregation).
* `tests/test_oi_auto_alert_state.py` — DashboardState-level tests with a fake chain payload / bar frame: refresh builds rows, evaluation emits events, settings round-trip through a temp repository.
* `frontend/src/oiAutoAlerts.test.js` — node:test for the UI helper.
* Manual: restart :3001 (Sunday, no market impact), `GET /api/oi-auto-alerts` shows 7 MAG7 ladders from Friday's chain, panel renders on :5173 and :4173, add/remove ticker, toggles, sound test; production `vite build` passes.

## Amendments (2026-08-17, after the first live checks)

* **UI placement (user decision):** not inside Charts & OI and not a drawer — a
  dedicated **"Auto Alert" tab** in the left nav (below Charts & OI) rendering the
  page, plus an **Alerts** tab in the phone bottom bar. The feed hook is mounted
  app-wide (tone/toast/notification/badge on every page).
* **Chart + bell integration:** the current call/put target per ticker (2 lines)
  and today's confirmations are mirrored into the manual price-alert list
  (`source: "auto"`, server-evaluated; tick evaluator skips them). Auto lines
  draw amber; off-screen targets get amber edge badges with the % distance.
* **Ladder = chart parity:** `build_high_oi_walls` is a port of
  `buildHighOiContractList` (the rule behind the chart's OI lines: monthly-OPEX
  scope, delta band 0.14–0.50 or ≥30 % of the side leader, dominant expiry,
  top 15 per side by OI, both sides of price). The ladder is the directional
  split of those walls and is **re-split at every completed 5-minute close**
  (`augment_ladder`), so a wall the open gapped through, or one price fell back
  under, becomes the nearest target — nothing drawn on the chart is skipped.
  Unknown deltas (0/missing/±999 feed sentinel) never exclude a wall — in the
  ladder and in the chart helper alike.
* **Track charted tickers:** opening a ticker on the chart adds it to Auto Alert
  (toggle, default on; manual cap raised to 30).
* **Alert Center on phones:** "Clear all" uses an in-popover confirm (no native
  dialog), clears manual alerts + OAuth notices only; auto rows fold into a
  collapsible group.

## Amendment (2026-08-20): daily-sheet side-by-spot convention

Decided after the MSFT loss: the 9:35 PUT alert fired at 482.5 quoting 24.2K
OI — that 24.2K was the deep-ITM **call** contract at 480/482.5, while the
Trading Alphas / MomoX daily sheet printed the 480 **put** wall at 8.4K and no
482.5 put wall at all. Validated against their full 18-ticker 2026-08-20
sheets, the convention (now shared by `buildHighOiContractList`,
`build_high_oi_walls`, the chart's window-scope walls, and the alert ladder):

* **Side split at spot.** Strikes above spot are CALL walls with the call
  contract's OI; strikes at/below spot are PUT walls with the put contract's
  OI. ITM contracts never appear on either board.
* **No delta filter; candidates stay within ±6×EM of spot.** (Dropping the
  band entirely was tried the same evening and immediately drifted the top-8
  to far monthly mega-walls — MSFT 550/570 calls displacing 485/490; with
  the sheet's own ExMo 8.21 the ±6·EM band reproduces its panel exactly.)
  The ±EM values also place the +EM / BMO / −EM separator rows, which the
  High OI tab renders inline with the inside-EM zone highlighted and columns
  `C/P · Imp · Price · OI · Expiry`.
* **Expiry window** = through the next monthly OPEX, rolled to the following
  monthly when the near one is < 7 days out (`high_oi_window_end`; the 8/20
  sheets list 9/18 walls beside the 8/21 weeklies).
* **Ladder = the sheet's walls exactly** (top 8 per side by OI, dominant
  expiry per strike). `DEFAULT_LEVELS_PER_SIDE` 15→8, the 40-wall universe
  and `directional_split`'s cross-side pooling are gone; `augment_ladder` is
  same-side only. A call wall price sits above is *not* a put trigger — the
  put board only ever arms put contracts. Header Call/Put OI = sum of the
  printed walls.

## Out of scope / follow-ups
* Mobile "Alerts" tab in `mobile-terminal-action-bar`.
* Drawing the auto-alert ladder as chart lines (the High OI indicator already draws the same levels).
* Email/SMS delivery.
* Alert-confirmation lifecycle (retest entry, reclaim/failed-break exit,
  target-hit scale bell) — discussed 2026-08-20, not yet approved.
