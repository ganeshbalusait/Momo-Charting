# Feature tests map

Every feature, with its unit, integration and end-to-end test files (see the
`feature-testing` skill). Add a row in the same commit as the feature.

| Feature | Unit | Integration | End to end |
|---|---|---|---|
| BOT: Truth check (BOT vs real paper fills) and $0-bid exits count as $0 | `tests/test_momx_truth_check.py`, `tests/test_momx_rule_record.py`, `frontend/src/botTruth.test.js`, `frontend/src/momxBot.test.js` | `tests/test_momx_bot_truth_api.py` | `tests/e2e/trading-bot-truth.e2e.test.mjs` |
