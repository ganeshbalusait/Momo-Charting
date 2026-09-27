import assert from "node:assert/strict";
import test from "node:test";

import {
  OI_AUTO_ALERT_SYMBOL_PATTERN,
  activeLevelsForPrice,
  buildOiAutoAlertMirror,
  compactOiAmount,
  findNewOiAutoAlertEvents,
  mergeOiAutoAlertMirror,
  normalizeOiAutoAlertSymbol,
  oiAutoAlertDistanceText,
  oiAutoAlertEventLabel,
  oiAutoAlertLevelText,
  oiAutoAlertPollDelay,
  oiAutoAlertRowLastEvent,
  oiAutoAlertSideSummary,
  sortOiAutoAlertRows,
  splitOiAutoAlertMessage,
  strikeText,
} from "./oiAutoAlerts.js";

test("symbol normalisation mirrors the server rule", () => {
  assert.equal(normalizeOiAutoAlertSymbol(" tsla "), "TSLA");
  assert.equal(normalizeOiAutoAlertSymbol("brk.b"), "BRK.B");
  assert.equal(normalizeOiAutoAlertSymbol("1abc"), "");
  assert.equal(normalizeOiAutoAlertSymbol("a b"), "");
  assert.equal(normalizeOiAutoAlertSymbol(""), "");
  assert.ok(OI_AUTO_ALERT_SYMBOL_PATTERN.test("AMD"));
});

test("compact amounts and strikes read like the chart bubbles", () => {
  assert.equal(compactOiAmount(6541), "6.5K");
  assert.equal(compactOiAmount(17753), "17.8K");
  assert.equal(compactOiAmount(120000), "120K");
  assert.equal(compactOiAmount(950), "950");
  assert.equal(compactOiAmount(1_250_000), "1.3M");
  assert.equal(strikeText(345), "345");
  assert.equal(strikeText(357.5), "357.5");
  assert.equal(strikeText("357.50"), "357.5");
});

test("level text carries strike, OI and strength", () => {
  assert.equal(
    oiAutoAlertLevelText({ strike: 350, openInterest: 17753, strength: "strong", expiry: "2026-08-21" }),
    "350 · 17.8K OI · strong · 8/21",
  );
  assert.equal(oiAutoAlertLevelText(null), "--");
});

test("distance text formats dollars and percent", () => {
  assert.equal(oiAutoAlertDistanceText({ distance: 4.75, distancePercent: 1.3758 }), "$4.75 / 1.38% away");
  assert.equal(oiAutoAlertDistanceText({ distance: null }), "");
  assert.equal(oiAutoAlertDistanceText(null), "");
});

test("side summary maps the server states to labels and tones", () => {
  assert.deepEqual(oiAutoAlertSideSummary("armed"), { label: "Armed", tone: "armed" });
  assert.deepEqual(oiAutoAlertSideSummary("touched"), { label: "Touched · not confirmed", tone: "touched" });
  assert.deepEqual(oiAutoAlertSideSummary("confirmed"), { label: "5m close confirmed", tone: "confirmed" });
  assert.deepEqual(oiAutoAlertSideSummary("done"), { label: "Ladder complete", tone: "done" });
  assert.deepEqual(oiAutoAlertSideSummary("empty"), { label: "No OI level", tone: "empty" });
  assert.deepEqual(oiAutoAlertSideSummary("whatever"), { label: "Armed", tone: "armed" });
});

test("new events are the unseen ids, oldest first, so tones fire in order", () => {
  const events = [
    { id: "c", kind: "confirm" },
    { id: "b", kind: "touch" },
    { id: "a", kind: "touch" },
  ];
  assert.deepEqual(findNewOiAutoAlertEvents(events, new Set(["a"])).map((event) => event.id), ["b", "c"]);
  assert.deepEqual(findNewOiAutoAlertEvents(events, new Set(["a", "b", "c"])), []);
  assert.deepEqual(findNewOiAutoAlertEvents(null, new Set()), []);
  // A seen set that is empty means "first load": nothing is new — the caller
  // seeds the set instead of firing a burst of old alerts.
  assert.deepEqual(findNewOiAutoAlertEvents(events, null), []);
});

test("messages split into a headline and detail", () => {
  assert.deepEqual(
    splitOiAutoAlertMessage("TSLA ↑ CALL OI 345 CONFIRMED · 5m close 345.25 · Next OI target 350 (17.8K OI) · $4.75 / 1.38% away"),
    { title: "TSLA ↑ CALL OI 345 CONFIRMED", detail: "5m close 345.25 · Next OI target 350 (17.8K OI) · $4.75 / 1.38% away" },
  );
  assert.deepEqual(splitOiAutoAlertMessage(""), { title: "OI auto alert", detail: "" });
});

test("poll delay is faster during regular hours", () => {
  assert.equal(oiAutoAlertPollDelay(true), 10_000);
  assert.equal(oiAutoAlertPollDelay(false), 30_000);
});

test("event labels say which level fired and what is next", () => {
  assert.equal(
    oiAutoAlertEventLabel({ side: "CALL", kind: "confirm", level: { strike: 345 }, crossedLevels: [{ strike: 345 }], nextTarget: { strike: 350 } }),
    "CALL 345 confirmed → next 350",
  );
  assert.equal(
    oiAutoAlertEventLabel({ side: "CALL", kind: "confirm", crossedLevels: [{ strike: 345 }, { strike: 350 }], nextTarget: { strike: 355 } }),
    "CALL 345 → 350 confirmed → next 355",
  );
  assert.equal(
    oiAutoAlertEventLabel({ side: "PUT", kind: "confirm", level: { strike: 340 }, nextTarget: null }),
    "PUT 340 confirmed · ladder complete",
  );
  assert.equal(
    oiAutoAlertEventLabel({ side: "CALL", kind: "touch", level: { strike: 345 } }),
    "CALL 345 touched · not confirmed",
  );
  assert.equal(oiAutoAlertEventLabel(null), "");
});

test("mirror builds 2 lines per ticker plus today's confirmations as triggered entries", () => {
  const payload = {
    enabled: true,
    rows: [
      {
        symbol: "TSLA", sessionDate: "2026-08-17", levelsUpdatedAt: "2026-08-17T09:15:00-04:00",
        activeCall: { strike: 350, openInterest: 17753, strength: "strong" }, nextCall: { strike: 355 },
        activePut: { strike: 340, openInterest: 5910, strength: "weak" }, nextPut: { strike: 330 },
        touchedCallStrikes: [], touchedPutStrikes: [340],
      },
      { symbol: "XXXX", status: "unavailable" },
    ],
    events: [
      { id: "e1", kind: "confirm", symbol: "TSLA", side: "CALL", at: "2026-08-17T09:40:00-04:00", price: 345.25, level: { strike: 345 }, crossedLevels: [{ strike: 345 }], nextTarget: { strike: 350 } },
      { id: "e0", kind: "confirm", symbol: "TSLA", side: "CALL", at: "2026-08-14T15:00:00-04:00", price: 330, level: { strike: 330 }, nextTarget: null },  // Friday: ignored
      { id: "t1", kind: "touch", symbol: "TSLA", side: "PUT", at: "2026-08-17T09:36:00-04:00", price: 339.9, level: { strike: 340 } },
    ],
  };
  const records = buildOiAutoAlertMirror(payload);
  assert.deepEqual(records.map((r) => [r.id, r.status, r.condition, r.price]), [
    ["auto:TSLA:CALL:2026-08-17:350", "active", "above", 350],
    ["auto:TSLA:PUT:2026-08-17:340", "active", "below", 340],
    ["auto:TSLA:CALL:2026-08-17:345", "triggered", "above", 345],
  ]);
  assert.equal(records[0].levelLabel, "Auto CALL OI");
  assert.equal(records[0].note, "17.8K OI · strong · next 355");
  assert.equal(records[1].levelLabel, "Auto PUT OI · touched");
  assert.equal(records[1].autoState, "touched");
  assert.equal(records[2].hideOnChart, true);
  assert.equal(records[2].enabled, false);
  assert.equal(records[2].triggeredPrice, 345.25);
  assert.equal(records[2].note, "5m close 345.25 → next 350");
  assert.ok(records.every((r) => r.source === "auto"));
  // Master switch off pauses the armed lines; dismissed ids stay out.
  const paused = buildOiAutoAlertMirror({ ...payload, enabled: false }, { dismissedIds: new Set(["auto:TSLA:CALL:2026-08-17:345"]) });
  assert.deepEqual(paused.map((r) => [r.id, r.enabled]), [
    ["auto:TSLA:CALL:2026-08-17:350", false],
    ["auto:TSLA:PUT:2026-08-17:340", false],
  ]);
});

test("mirror tracks live price: call above, put below, even after a gap", () => {
  // AAPL built overnight at 305.77 (active call 305, put 302.5); price is now
  // 307. The drawn call must move to the nearest wall ABOVE (307.5) and the
  // put stay the nearest BELOW (302.5) — not sit below price at 305.
  const row = {
    symbol: "AAPL", sessionDate: "2026-08-17", levelsUpdatedAt: "2026-08-17T09:15:00-04:00",
    activeCall: { strike: 305, openInterest: 9500, strength: "moderate" }, nextCall: { strike: 307.5 },
    activePut: { strike: 302.5, openInterest: 916, strength: "weak" }, nextPut: { strike: 300 },
    callLevels: [
      { strike: 305, openInterest: 9500, strength: "moderate" },
      { strike: 307.5, openInterest: 4700, strength: "weak" },
      { strike: 310, openInterest: 31900, strength: "strong" },
      { strike: 312.5, openInterest: 4500, strength: "weak" },
    ],
    putLevels: [
      { strike: 302.5, openInterest: 916, strength: "weak" },
      { strike: 300, openInterest: 28300, strength: "strong" },
      { strike: 297.5, openInterest: 805, strength: "weak" },
    ],
    confirmedCallStrikes: [], confirmedPutStrikes: [], touchedCallStrikes: [], touchedPutStrikes: [],
  };
  const picked = activeLevelsForPrice(row, 307);
  assert.equal(picked.activeCall.strike, 307.5);
  assert.equal(picked.nextCall.strike, 310);
  // 305 is BELOW 307, so it is the support/put line — not skipped because it
  // happened to be built into the call list.
  assert.equal(picked.activePut.strike, 305);
  assert.equal(picked.nextPut.strike, 302.5);
  // Through the mirror with a live-price map.
  const records = buildOiAutoAlertMirror({ enabled: true, rows: [row], events: [] }, { livePrices: new Map([["AAPL", 307]]) });
  const call = records.find((r) => r.autoSide === "CALL");
  const put = records.find((r) => r.autoSide === "PUT");
  assert.equal(call.price, 307.5);
  assert.equal(put.price, 305);
  assert.ok(call.note.includes("next 310"));
  // No live price -> falls back to the server's build-time active (305).
  const stale = buildOiAutoAlertMirror({ enabled: true, rows: [row], events: [] });
  assert.equal(stale.find((r) => r.autoSide === "CALL").price, 305);
});

test("a wall just below price is the put line, not a skipped level (AMZN 265)", () => {
  // AMZN 265.61: the 265 wall (built into the call ladder at the 9:15 pivot)
  // is now BELOW price, so it is the support/put trigger; 267.5 is the call.
  const row = {
    symbol: "AMZN",
    callLevels: [
      { strike: 265, openInterest: 10700 },
      { strike: 267.5, openInterest: 2400 },
      { strike: 270, openInterest: 44000 },
    ],
    putLevels: [
      { strike: 262.5, openInterest: 1700 },
      { strike: 260, openInterest: 10800 },
    ],
    confirmedCallStrikes: [], confirmedPutStrikes: [],
  };
  const picked = activeLevelsForPrice(row, 265.61);
  assert.equal(picked.activeCall.strike, 267.5);
  assert.equal(picked.activePut.strike, 265);
  assert.equal(picked.nextPut.strike, 262.5);
  // NVDA the other way round: at 224.72 the 225 wall is ABOVE price -> call.
  const nvda = {
    symbol: "NVDA",
    callLevels: [{ strike: 225, openInterest: 46800 }, { strike: 227.5, openInterest: 15900 }],
    putLevels: [{ strike: 222.5, openInterest: 4000 }, { strike: 220, openInterest: 13300 }],
    confirmedCallStrikes: [], confirmedPutStrikes: [],
  };
  const nv = activeLevelsForPrice(nvda, 224.72);
  assert.equal(nv.activeCall.strike, 225);
  assert.equal(nv.activePut.strike, 222.5);
});

test("mirror merge keeps manual alerts and skips no-op writes", () => {
  const manual = { id: "m1", symbol: "AAPL", price: 300, condition: "above", status: "active" };
  const auto1 = { id: "auto:TSLA:CALL:2026-08-17:350", source: "auto", price: 350, condition: "above", status: "active", enabled: true, levelLabel: "Auto CALL OI", note: "n", triggeredAt: null, hideOnChart: false, autoState: "armed" };
  const merged = mergeOiAutoAlertMirror([manual], [auto1]);
  assert.deepEqual(merged, [manual, auto1]);
  assert.equal(mergeOiAutoAlertMirror(merged, [auto1]), null);
  const touched = { ...auto1, levelLabel: "Auto CALL OI · touched", autoState: "touched" };
  assert.deepEqual(mergeOiAutoAlertMirror(merged, [touched]), [manual, touched]);
  assert.deepEqual(mergeOiAutoAlertMirror(merged, []), [manual]);
});

test("rows that fired recently sort first, newest first", () => {
  const now = Date.parse("2026-08-17T10:00:00-04:00");
  const rows = [
    { symbol: "AAPL" },
    { symbol: "MSFT", lastCallEvent: { id: "m", at: "2026-08-17T09:40:00-04:00" } },
    { symbol: "NVDA", lastPutEvent: { id: "n", at: "2026-08-17T09:55:00-04:00" } },
    { symbol: "TSLA", lastCallEvent: { id: "t", at: "2026-08-17T09:00:00-04:00" } },  // older than 30 min
  ];
  const sorted = sortOiAutoAlertRows(rows, now);
  assert.deepEqual(sorted.map((item) => item.row.symbol), ["NVDA", "MSFT", "AAPL", "TSLA"]);
  assert.deepEqual(sorted.map((item) => item.recent), [true, true, false, false]);
  assert.equal(sorted[0].lastEvent.id, "n");
  assert.equal(oiAutoAlertRowLastEvent(rows[0]), null);
  assert.equal(
    oiAutoAlertRowLastEvent({ lastCallEvent: { id: "c", at: "2026-08-17T09:40:00-04:00" }, lastPutEvent: { id: "p", at: "2026-08-17T09:45:00-04:00" } }).id,
    "p",
  );
});
