// Coverage (2026-11-04): the ~30 s chart reconcile across the wire.
//
// The server answers `since=<epoch>` with api_server._delta_chart_payload:
// every row of bars/studyBars/fineStudyBars at or after (since - 180 s), the
// last 3 dailyBars, scalars passed through, delta=true and deltaSince=cutoff
// (pinned over HTTP by tests/test_cov_charts-core_chart_route_api.py
// test_the_30s_reconcile_is_a_tail_only_delta). serverDelta() below is that
// contract; these tests prove the browser's merge rebuilds the server's full
// tape exactly - no duplicate minute, no hole - poll after poll.
import test from "node:test";
import assert from "node:assert/strict";

import { chartDeltaRequestTime, mergeChartDeltaPayload } from "./oiChartDelta.js";

const OVERLAP_SECONDS = 180;
const START = 1_793_800_800 - 600 * 60; // 10 hours before a fixed tape end

function serverDelta(full, since) {
  const cutoff = Math.max(0, since - OVERLAP_SECONDS);
  const tail = (rows) => (Array.isArray(rows) ? rows.filter((row) => Number(row?.time || 0) >= cutoff) : []);
  const scalars = Object.fromEntries(Object.entries(full).filter(([key]) => !["bars", "studyBars", "fineStudyBars", "dailyBars"].includes(key)));
  return {
    ...scalars,
    delta: true,
    deltaSince: cutoff,
    bars: tail(full.bars),
    studyBars: tail(full.studyBars),
    fineStudyBars: tail(full.fineStudyBars),
    dailyBars: (full.dailyBars || []).slice(-3),
  };
}

const bar = (time, close, volume = 100) => ({ time, open: close - 0.1, high: close + 0.2, low: close - 0.3, close, volume });

// The server's whole payload after `minutes` one-minute candles, the newest
// still forming (its close/volume change until the next minute starts).
function serverPayload(minutes, formingTick = 0) {
  const bars = Array.from({ length: minutes }, (_, i) => bar(START + i * 60, 100 + i * 0.01, 100 + i));
  bars[bars.length - 1] = bar(START + (minutes - 1) * 60, 100 + (minutes - 1) * 0.01 + formingTick, 50 + formingTick);
  // Like extend_study_tape: the 5m and 30m tapes only ever gain COMPLETED
  // buckets (a half-built secondary candle repaints).
  const completed = (span) => {
    const rows = [];
    for (let i = 0; i + span <= bars.length - 1; i += span) {
      const bucket = bars.slice(i, i + span);
      rows.push(bar(bucket[0].time, bucket.at(-1).close, bucket.reduce((sum, row) => sum + row.volume, 0)));
    }
    return rows;
  };
  const fineStudyBars = completed(5);
  const studyBars = completed(30);
  const dailyBars = Array.from({ length: 30 }, (_, i) => bar(START - (29 - i) * 86_400, 90 + i));
  dailyBars[dailyBars.length - 1] = bar(dailyBars.at(-1).time, bars.at(-1).close, 999 + minutes);
  return {
    symbol: "SPY",
    historyLoading: false,
    updatedAt: `poll-${minutes}-${formingTick}`,
    mtfSignals: [{ key: `CALL-${minutes}`, time: bars.at(-1).time }],
    bars, fineStudyBars, studyBars, dailyBars,
  };
}

const stamps = (rows) => rows.map((row) => row.time);

// Every candle the browser holds is the server's candle for that time.
function assertHeldRowsMatch(held, server, key) {
  const byTime = new Map(server[key].map((row) => [row.time, row]));
  for (const row of held[key]) assert.deepEqual(row, byTime.get(row.time), `${key} ${row.time} differs from the server`);
}

function assertNoDuplicates(payload) {
  for (const key of ["bars", "fineStudyBars", "studyBars", "dailyBars"]) {
    const times = stamps(payload[key]);
    assert.equal(new Set(times).size, times.length, `${key} holds a duplicate candle`);
    assert.deepEqual(times, [...times].sort((a, b) => a - b), `${key} is out of order`);
  }
}

test("one reconcile rebuilds the server's tape exactly, forming candle included", () => {
  const held = serverPayload(600, 0);
  const since = chartDeltaRequestTime(held);
  assert.equal(since, held.bars.at(-1).time, "the reconcile asks from the newest held candle");
  const now = serverPayload(607, 3);                    // 6 closed minutes later, new forming candle
  const merged = mergeChartDeltaPayload(held, serverDelta(now, since));
  for (const key of ["bars", "dailyBars"]) assert.deepEqual(merged[key], now[key], key);
  for (const key of ["fineStudyBars", "studyBars"]) assertHeldRowsMatch(merged, now, key);
  assert.equal(merged.updatedAt, now.updatedAt);
  assert.deepEqual(merged.mtfSignals, now.mtfSignals);
  assert.equal(merged.delta, undefined);
  assert.equal(merged.deltaSince, undefined);
  assertNoDuplicates(merged);
});

test("poll after poll the browser's tape never drifts from the server's", () => {
  let held = serverPayload(600, 0);
  let minutes = 600;
  for (const [advance, tick] of [[0, 1], [1, 0], [3, 2], [0, 5], [10, 1], [2, 0]]) {
    minutes += advance;
    const now = serverPayload(minutes, tick);
    const delta = serverDelta(now, chartDeltaRequestTime(held));
    // A reconcile is a few KB: never the whole tape.
    assert.ok(delta.bars.length <= advance + 5, `delta shipped ${delta.bars.length} bars`);
    held = mergeChartDeltaPayload(held, delta);
    assert.deepEqual(stamps(held.bars), stamps(now.bars), `after +${advance} min`);
    assert.deepEqual(held.bars.at(-1), now.bars.at(-1), "forming candle");
    assertHeldRowsMatch(held, now, "fineStudyBars");
    assertHeldRowsMatch(held, now, "studyBars");
    assert.deepEqual(held.dailyBars, now.dailyBars);
    assertNoDuplicates(held);
  }
});

test("a woken tab that missed an hour still lands on the server's tape (no gap)", () => {
  const held = serverPayload(600, 0);
  const now = serverPayload(660, 0);
  const merged = mergeChartDeltaPayload(held, serverDelta(now, chartDeltaRequestTime(held)));
  assert.deepEqual(stamps(merged.bars), stamps(now.bars));
  const gaps = stamps(merged.bars).slice(1).map((time, i) => time - merged.bars[i].time);
  assert.deepEqual([...new Set(gaps)], [60]);
});

test("a slim first paint never reconciles by delta (it pulls the deep tape instead)", () => {
  const slim = { ...serverPayload(600, 0), historyLoading: true, initialSlim: true };
  assert.equal(chartDeltaRequestTime(slim), 0);
  assert.equal(chartDeltaRequestTime(serverPayload(600, 0), { forceFullTape: true }), 0);
});
