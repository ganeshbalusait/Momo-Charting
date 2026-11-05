# Feature tests map

Every feature, with its unit, integration and end-to-end test files (see the
`feature-testing` skill). Add a row in the same commit as the feature.

| Feature | Unit | Integration | End to end |
|---|---|---|---|
| BOT: Truth check (BOT vs real paper fills) and $0-bid exits count as $0 | `tests/test_momx_truth_check.py`, `tests/test_momx_rule_record.py`, `frontend/src/botTruth.test.js`, `frontend/src/momxBot.test.js` | `tests/test_momx_bot_truth_api.py` | `tests/e2e/trading-bot-truth.e2e.test.mjs` |
| Scanner: 21 / 50 EMA Touch lists (Monthly/Weekly/Daily), SPAC flat-lead fix; EMA swing back-test engine | `tests/test_momx_ema_touch.py`, `tests/test_backtest_ema_swing.py` | `tests/test_momx_ema_touch_placeholder_api.py`, `tests/test_cov_scanner-board_ema_touch_api.py` | `tests/e2e/scanner-board-lists.e2e.test.mjs` (EMA chart dropdown) |
| Scanner: switching an EMA list's timeframe retires the old list's builds; EMA lists keep their rebuild priority on any timeframe | `tests/test_momx_service_parallel.py` (vanished list, unreadable file, priority) | `tests/test_momx_ema_timeframe_switch_api.py` | `tests/e2e/scanner-board-lists.e2e.test.mjs` (EMA chart dropdown) |
| Scanner: list picker shows at once on open (aborted first read is retried) | `frontend/src/scannerRequestScope.test.js` | `tests/test_cov_scanner-board_ema_touch_api.py` (lists route) | `tests/e2e/scanner-board-lists.e2e.test.mjs` (picker, Set on a 10-ticker list) |
| BOT: paper order rules v2 (live-quote mid entry within seconds; unfilled buys re-sent at the ask, no spread filter) | `tests/test_momx_paper_mirror.py`, `tests/test_momx_option_track.py`, `tests/test_momx_truth_check.py`, `frontend/src/botTruth.test.js` | `tests/test_momx_bot_truth_api.py` | `tests/e2e/trading-bot-truth.e2e.test.mjs` |
| BOT: Paper 5 rules pass a statistics check (all tries counted) before switching on; optional Approve / Reject | `tests/test_momx_paper_experiments.py`, `frontend/src/botExperiment.test.js` | `tests/test_momx_paper_experiment_decision_api.py` | `tests/e2e/trading-bot-experiment-approval.e2e.test.mjs` |
| Settings: Renew both Schwab logins (on this PC) | `frontend/src/schwabRenewal.test.js`, `tests/test_schwab_oauth_callback.py` | `tests/test_schwab_renew_both_api.py`, `tests/test_cov_shell_schwab_oauth_api.py` | `tests/e2e/settings-schwab-renew.e2e.test.mjs`, `tests/e2e/shell-schwab-oauth.e2e.test.mjs` |
| Settings: renew Schwab by pasting from app.agxtrade.com; full app keys shown | `frontend/src/schwabManualRenewal.test.js`, `tests/test_schwab_renew_by_paste.py` | `tests/test_schwab_renew_by_paste_api.py` | `tests/e2e/settings-schwab-renew-paste.e2e.test.mjs` |
