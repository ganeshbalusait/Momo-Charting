# Process split: `agx-pipeline` + `agx-api`

**Date:** 2026-08-19
**Status:** Approved in chat (design); implementation staged A1 → A2 → A3
**Owner:** Ganesh (trader) / Claude (implementation)

## Problem

One Python process serves the trading UI **and** runs the entire data pipeline
(chart warmer, OI scanner, alert evaluator, learning agent, Schwab stream,
background chain/chart builds). Everything shares one GIL.

Measured on 2026-08-19:

- Warm chart serves are 0.3–2.5 s — until a full build runs beside them, then
  3–7 s (quick-strip sweep: 9/14 passed a 3 s bar during a `full 120.95s`
  build; the same five symbols passed at 1.2–2.2 s seconds later, alone).
- The lock-wait probe proved requests are not blocked (`lockwait=0.00s`);
  they are merely starved of CPU by the pipeline's pure-Python work.
- Every server restart (9 that day) cold-started the pipeline: warm caches,
  Schwab stream, and in-flight builds all died with the process, and the
  first request per symbol paid for it.
- Test runs that `import api_server` boot the whole app (`STATE` is created
  at module level, line ~15246) and contend on the live SQLite database —
  observed as `sqlite3.OperationalError: database is locked` during pytest
  collection.

## Constraints (non-negotiable)

1. **Exactly one process may touch Schwab tokens** — REST, stream, or refresh.
   Two refreshers race and revoke the refresh token (has happened; recorded in
   project memory). The stream is a module-level `SchwabMarketStream` today
   and must move with the token owner.
2. **Public topology stays put.** cloudflared and the vite proxies point at
   `:3001`; that must keep working unchanged.
3. **The alert engine's behavior is frozen.** One CALL + one PUT per ticker
   per session off the 9:15 ET build; no change to evaluation cadence or
   persistence as part of this split.
4. **SQLite gets a single writer.** The `database is locked` class of failure
   ends here, not merely moves.

## Decision

Two long-lived processes, split by *ownership* rather than by rewriting the
existing server:

```
cloudflared / vite proxy ──► :3001  agx-api      (NEW, small, stateless)
                                      │  GET chart/chain/dashboard → serve the
                                      │      pipeline's gz blobs (disk/memory)
                                      │  everything else → reverse-proxy :3002
                                      │  SSE → bridge from the pipeline
                             :3002  agx-pipeline (today's api_server, port moved,
                                                  otherwise UNCHANGED in A1)
```

The interchange format **already exists**: the pipeline persists complete
response payloads to `artifacts/oi_chart_cache/<SYM>.json.gz` and
`artifacts/oi_chain_cache/<SYM>.json.gz`. The gateway serves those bytes.

### Ownership map

| Concern | Owner | Rationale |
|---|---|---|
| Schwab REST + stream + token file | pipeline only | constraint 1; `agx-api` never imports `SchwabClient` |
| Schedulers: warmer, scanner, alert evaluator, learning, snapshots | pipeline | unchanged code |
| All chart/chain/dashboard builds | pipeline | unchanged code |
| SQLite writes (alerts, snapshots, settings, events) | pipeline | constraint 4 |
| HTTP GET serving | api | pre-built gz blobs; no pandas, no broker, no GIL contention |
| Mutations (alert add/remove/rearm/clear, settings, refresh-now) | api → proxy → pipeline | one owner of state transitions |
| SSE live bars (`/api/live-market-stream`, `/api/live-option-stream`) | pipeline produces; api fans out | stream lives with the token |
| Auth/session checks on requests | api (read-only DB or proxied) | gateway must not write |

### Failure model

- **Pipeline down:** api serves last-known blobs stamped `warming`/`refreshing`
  — flags the frontend already renders honestly (badges, retry loops built
  2026-08-18/19). `/api/health` reports `pipeline: down`. Alerts pause and say
  so; today they pause invisibly when the single process dies.
- **Api down:** watchdog restarts it; it is stateless and returns in seconds.
  The pipeline keeps building and keeps the Schwab stream alive — a UI deploy
  no longer cold-starts anything.
- **Both down:** exactly today's behavior; the watchdog already handles it.

### Deploy model

- Frontend/dist changes: no process restart at all (unchanged).
- Gateway changes: restart `agx-api` (~2 s, stateless, pipeline warm).
- Pipeline changes: restart `agx-pipeline`; the api keeps serving last blobs
  with honest staleness flags while it boots — the "restart cost you the
  open" failure mode (2026-08-17) becomes a visible warming window instead of
  an outage.

## Staging

### A1 — gateway in front, pipeline unchanged (~1 day, reversible in minutes)

1. New module `gateway.py` (`agx-api`). Must not import `api_server`
   (module-level `STATE` boots the app — verified 2026-08-19). Stdlib
   `ThreadingHTTPServer` + `urllib` reverse proxy; no new dependencies.
2. Serve from blobs when fresh, else proxy:
   - `GET /api/oi-finder-chart?symbol=S` → if `oi_chart_cache/S.json.gz`
     exists and its newest bar reaches the current session (same rule as
     `tape_current`, commit `137758a`), decompress → apply the same slim/
     delta/deep shaping the pipeline applies (`initial`, `since`, `deep`
     params) → serve. Else proxy to `:3002` and pass the response through.
   - `GET /api/oi-finder`, `/api/oi-finder-chain` → same pattern over
     `oi_chain_cache/S.json.gz` (servability rule from `8c8fc34`).
   - `GET /api/dashboard*`, auth, health: proxy in A1 (dashboard blob comes
     in A3).
   - All POSTs: proxy verbatim (method, headers, body, cookies).
   - SSE endpoints: **streamed proxy** to `:3002` in A1 (chunked pass-through,
     no buffering); the dedicated bridge is A2.
3. `api_server` moves to `:3002` via `PORT` env/config; watchdog entry for
   the pipeline updates its probe URL; new watchdog entry for the gateway on
   `:3001` (health = `GET /api/health` returning gateway+pipeline status).
4. Rollback: stop gateway, set pipeline back to `:3001`. No config outside
   this repo changes in either direction.

**A1 exit criteria (measured, `scripts/perf_sweep.py` extended):**
- Warm chart GET p95 **< 1.0 s while a forced full build runs** on another
  symbol (the exact failing case: 3–7 s on 2026-08-19).
- Cold-open flow unchanged: stub < 1 s → candles ≤ ~7 s → indicators behind
  (`ecebf6c` behavior preserved through the proxy path).
- Alert mutation round-trip (add → rearm → clear) works through the proxy;
  one CALL/one PUT contract untouched.
- Exactly one process holds the Schwab token file open (verified by handle
  inspection); zero token-refresh errors across a trading day.

### A2 — real SSE bridge; gateway stops proxying hot GETs (~1 day)

1. Pipeline exposes one local publisher (TCP on `127.0.0.1` or named pipe):
   every live bar/option tick it already pushes to SSE clients is also
   written to the publisher. The api holds one subscription and fans out to
   N browser SSE connections. v1 fallback if the socket proves fiddly on
   win32: tail `artifacts/schwab_stream_chart_history.json.gz` appends.
2. Hot GETs (`oi-finder-chart`, `oi-finder`, `oi-finder-chain`) are served
   exclusively from blobs; a miss returns the warming shape and posts a
   build-now to the pipeline (`/internal/build?symbol=S`), mirroring the
   cold-path contract from `0c273ea`.
3. The pipeline's HTTP surface shrinks to internal + proxied-mutation routes;
   it stops being reachable except from localhost.

**A2 exit criteria:** browser SSE reconnects survive an api restart within
its retry window; tick latency api-vs-pipeline delta < 250 ms median; hot
GETs never touch `:3002` (verified from pipeline access logs).

### A3 — cleanup and the last CPU (~1 day, non-blocking)

1. Pipeline pre-serializes hot payloads as **response bytes** (gzip level 1)
   next to the json.gz blobs — chart, chain, and the dashboard payload the
   gateway still proxies in A1/A2 — killing the 0.4–0.5 s `json.dumps` per
   hit (measured on COIN: dumps 0.53 s + gzip 0.15 s on 3 MB).
2. `STATE = DashboardState()` moves under `if __name__ == "__main__"` (or a
   `create_app()` factory) so importing `api_server` stops booting the app —
   fixes the pytest/`database is locked` hazard and the profiling trap from
   2026-08-19.
3. Blob staleness stamps unified: every blob carries `builtAt`,
   `newestBarTime`, `studiesPending`, `historyLoading` so the gateway's
   freshness decisions are field-driven, not mtime-driven.

## Testing

- Unit (new, gateway): blob freshness rule; slim/delta/deep shaping parity
  against fixtures captured from the pipeline's own responses; proxy echoes
  method/body/cookies; pipeline-down degradation returns warming shapes.
- Existing suites run unchanged against the pipeline (it *is* the old
  server): 463 frontend + backend suites.
- `perf_sweep.py` gains `--during-full-build` (forces one full rebuild, then
  sweeps) — the A1 exit number comes from this, not from ad-hoc curls.
- Soak: one full trading session with both processes under the watchdog
  before A2 starts.

## Risks

- **Shaping parity drift** (slim/delta/deep re-implemented in the gateway):
  contained by fixture-parity tests generated from the pipeline itself, and
  by A1's proxy fallback for anything the gateway is unsure about.
- **SSE bridge** is the only genuinely new plumbing; staged to A2 with a
  file-tail fallback, and A1 ships with plain streamed proxying.
- **Two processes, one artifacts dir:** already the interchange today; the
  anti-clobber guard (`137758a`/chart, `8c8fc34`/chain) protects the racy
  class, and blob writes are atomic (`.tmp` + `os.replace`, existing code).
- **Windows spawn/import traps:** the gateway is a new module that imports
  neither `api_server` nor `SchwabClient`; enforced by a unit test that
  imports `gateway` and asserts neither lands in `sys.modules`.

## Explicitly out of scope

- Any change to alert semantics, ladder construction, or the 9:15 build.
- Moving off SQLite; changing brokers; changing cloudflared/vite topology.
- Rewriting `api_server` internals beyond the port, the `__main__` guard
  (A3), and the internal build endpoint (A2).
