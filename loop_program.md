# AgenticAI-Trading Loop Program

## Goal

Improve measured repository performance without changing trading behavior, weakening tests, or touching live services.

## Inner loop

1. Read the latest experiment history and current shadow-workspace code.
2. Choose one small hypothesis.
3. Edit only the configured allowlist.
4. Let the immutable verifier run.
5. Keep the change only when every correctness gate passes and the score improves by the configured minimum.
6. Otherwise restore the exact pre-experiment snapshot.

## Outer loop

After repeated failures, inspect `experiments.jsonl` and revise this search plan. Avoid repeating the same optimization family. Prefer a new bottleneck, data structure, call path, or test-runtime hypothesis. The outer loop may change this program, but it may not change code, tests, verifier logic, live configuration, credentials, databases, or services.

## Non-negotiable constraints

- Never edit `.env`, credentials, databases, tests, verifier files, order execution, risk limits, or live bot settings.
- Never restart, deploy, commit, merge, place orders, cancel orders, or change broker state.
- Never make a test easier or delete coverage to improve the score.
- One hypothesis per inner iteration.
- Stop at the configured time or iteration limit.
- Final output is a patch for human review, not an automatic application to the live checkout.
