---
name: feature-testing
description: Use for ANY new feature or fix in the AgenticAI-Trading app (scanner, charts, options, settings, bot, any page or route). Every change ships with unit, integration AND end-to-end tests, in the same commit. Covers where each layer lives, the harnesses, what to fake, safety rules and when each may run.
---

# Three layers of tests for every feature

Ganesh's standing rule (2026-11-04): "Any new features do unit testing and integration testing and end to end testing." A change is not done until all three layers exist and pass. The handoff says which layers ran, and anything that could not run (e.g. end-to-end held for market hours).

## The three layers

| Layer | What it proves | Where | Run |
|---|---|---|---|
| Unit | One function/module is right on its own | `tests/test_*.py` (pytest); `frontend/src/<module>.test.js` (node:test) | `.venv/Scripts/python.exe -m pytest tests/test_x.py -q -p no:cacheprovider`; `cd frontend && node --test src/x.test.js` |
| Integration | The real route over HTTP, the auth gate, request parsing and the real modules behind it work together | `tests/test_<feature>_api.py` on `tests/api_harness.py` (`running_api()`); momx_worker routes through `momx_worker.Handler` | pytest, as above |
| End to end | The trader's real clicks in the real app give the right screen, at desktop and phone width | `tests/e2e/<feature>.e2e.test.mjs` on `tests/e2e/harness.mjs` | `node --test --test-concurrency=1 "tests/e2e/*.e2e.test.mjs"` from the repo root |

Worked example of all three, for "Renew both": `frontend/src/schwabRenewal.test.js` (unit), `tests/test_schwab_renew_both_api.py` (integration), `tests/e2e/settings-schwab-renew.e2e.test.mjs` (end to end).

## Writing each layer

**Unit.** Pure logic goes in a small module next to its caller (frontend: never inside App.jsx, so node:test can import it). Test the edge cases that bit before (empty vs missing vs failed, ET session boundaries, Friday/weekend, stale vs fresh).

**Integration.**
```python
from api_harness import api_server, running_api

def test_route_round_trip(monkeypatch):
    monkeypatch.setattr(api_server, "<function the route calls that leaves the PC>", fake)
    with running_api() as api:
        status, body = api.post("/api/route", {"field": "value"})
    assert status == 200 and body["field"] == "value"
```
- Scanner worker routes: `tests/worker_harness.py` `running_worker(tmp_path, monkeypatch)` serves the real `momx_worker.Handler` with scratch lists, boards, history, grade tape and Momo Alert files; `worker.seed_board(list, payload)` stores a finished board; `worker.as_admin(monkeypatch)` gives admin headers. Through the proxy: `with running_worker(...) as worker, running_api(worker_port=worker.port) as api:`.
- Both harnesses run under `tests/live_guard.py`: any connection that leaves the PC, or reaches the live :3001/:3002/:3010/:5173, is refused, and any write under `artifacts/`, `database/` or the live `.env` raises. A `ConnectionRefusedError` or `PermissionError` from a guard means the test forgot a fake or a scratch path. Fix the test, never the guard.
- `tests/conftest.py` points the database, scanner archives, grade tape and stream history at a scratch folder; `running_api()` also moves `.env`, credential health, chart profiles, earnings imports, the Momo Alert files, the scanner lists and the industry cache to a scratch folder.
- Fake everything that leaves the PC: Schwab, Alpaca, the broker, AI calls, news sites, ntfy. Monkeypatch the function the route calls, not the HTTP library.
- A tunnel visitor is `headers=RELAYED` (from api_harness). With no stamp, a loopback request counts as this PC and auto-logs in as the owner. The client keeps cookies, so sign-in flows carry their session; `api.last_headers` has the response headers. `seed_user(email, role)` adds a scratch user.
- Assert on what the screen reads (status code, JSON fields), plus the side effect (registry, saved row) where there is one.

**End to end.**
```js
import assert from "node:assert/strict";
import { PHONE, e2eTest, withApp } from "./harness.mjs";

e2eTest("what the trader does and sees", async () => {
  await withApp({ view: "MomX Scanner", fakeWrites: { "POST /api/x": { json: { ok: true } } } }, async (app) => {
    await app.page.locator(".real-class-from-App-jsx").first().waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
```
- `view` is the left-rail page key (`agenticActiveView`). The harness also marks the saved chart grid as applied, so a fresh test browser is not steered to Charts & OI.
- Read-only by design: every non-GET `/api` request is stopped in the browser and recorded in `app.writes`. Answer a save/start flow with `fakeWrites`, then assert the request (`app.writes`) and the screen that follows. `allowWrites` is only for read-only POSTs (searches, batched reads).
- `fakeReads: {"GET /api/path": {json}}` answers a read with a fixture: use it for states the live server rarely shows (an error, an empty board, a non-admin user) and for every `/api/ai/` read, which the harness refuses otherwise because each one costs an AI call. `app.reads` lists the reads made.
- `path: "/?popout=chart&symbol=SPY&timeframe=5m"` opens a pop-out window; `viewport: PHONE` turns on touch (`touch: false` to turn it off).
- Never let a spec reach a route that places or cancels orders, starts a Schwab login, or changes settings.
- Use real selectors from App.jsx (class, `data-testid`, `aria-label`). Never loosen one to `body` to make a test pass. Wait for state (`:enabled`, text) instead of sleeping.
- Cover desktop (default) and `viewport: PHONE`. Assert `app.pageErrors` is empty. On phone, check nothing is cut off (`overflowsSideways`, bounding boxes).
- Keep it light: few tickers (SPY, AAPL, NVDA), no sweeps across the watchlist. Cold chart builds are CPU-heavy.

## When tests may run (the trading laptop runs the live app)

- 09:30-16:00 ET weekdays: only the focused test files for the change. End-to-end specs skip themselves then (override `E2E_ALLOW_MARKET_HOURS=1` only with Ganesh's OK).
- Full suites (all `tests/`, all `frontend/src/*.test.js`, all `tests/e2e`) only after 16:00 ET or before 09:30. A full momx suite at 14:01 CT on 2026-10-16 took the app down.
- One browser at a time: the harness holds a lock file, so parallel runs queue instead of stacking Chromium.

## A real bug found by a test

Do not weaken the test. Fix the bug (with its own release note), or, when the fix is not yours to make now, keep the test and mark it as a known failure with the reason (`pytest.mark.xfail(strict=True, reason=...)` / `test.todo`), and tell Ganesh in plain words.

## Checklist before handoff

- [ ] Unit tests for the new logic, including the edge case that motivated the change.
- [ ] Integration test through the real route (or worker handler) with outside services faked.
- [ ] End-to-end spec for the trader's flow at desktop and phone width, no page errors.
- [ ] All three pass; full suites run outside market hours.
- [ ] `tests/FEATURE_TESTS.md` lists the feature with its three test files.
- [ ] Release note at the top of `frontend/public/release-notes.json` for app changes (tests alone need none).
- [ ] Handoff names the layers that ran and any that could not.
