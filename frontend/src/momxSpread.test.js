import assert from "node:assert/strict";
import test from "node:test";

import { etDate, spreadCell, spreadMapFrom, spreadSortValue } from "./momxSpread.js";

// 2026-11-03 12:00 ET
const NOON_ET = Date.parse("2026-11-03T17:00:00Z");

const entry = (over = {}) => ({
  pct: 11.76, usd: 0.05, class: "Wide", cheapAtmUsd: null, atmOi: 1240, readings: 2,
  session: "2026-11-03", asOf: "2026-11-03T11:45:00-05:00", expiries: ["2026-11-06", "2026-11-20"],
  ...over,
});

test("map keeps well-formed entries and drops junk", () => {
  const map = spreadMapFrom({ spreads: {
    aapl: entry({ pct: 1.2, class: "Tight" }),
    BAD1: { pct: "x", class: "Wide" },
    BAD2: { pct: 5, class: "Huge" },
    BAD3: null,
  } });
  assert.deepEqual([...map.keys()], ["AAPL"]);
  assert.equal(spreadMapFrom(null).size, 0);
  assert.equal(spreadMapFrom({ spreads: "nope" }).size, 0);
});

test("no reading is blank, never Tight", () => {
  assert.equal(spreadCell(undefined, NOON_ET), null);
  assert.equal(spreadCell({ pct: null, class: "Tight" }, NOON_ET), null);
  assert.equal(spreadSortValue(undefined), null);
});

test("cell text, class and advice", () => {
  const cell = spreadCell(entry(), NOON_ET);
  assert.equal(cell.text, "12%");
  assert.equal(cell.cls, "Wide");
  assert.equal(cell.stale, false);
  assert.match(cell.title, /^Wide - median spread 11\.8% of mid \(\$0\.05\)/);
  assert.match(cell.title, /mid \+ 25% of the spread/);
  assert.match(cell.title, /2 readings today, last 11:45 ET/);
  assert.equal(spreadCell(entry({ pct: 2.345, class: "Tight" }), NOON_ET).text, "2.3%");
  assert.equal(spreadCell(entry({ pct: 180, class: "Avoid" }), NOON_ET).text, "99+%");
});

test("yesterday's reading is dimmed and says so", () => {
  const cell = spreadCell(entry({ session: "2026-11-02", asOf: "2026-11-02T14:10:00-05:00" }), NOON_ET);
  assert.equal(cell.stale, true);
  assert.match(cell.title, /From 2026-11-02 14:10 ET - not read yet today/);
});

test("sort value is the percentage so descending = widest first", () => {
  const rows = [entry({ pct: 4 }), entry({ pct: 30 }), entry({ pct: 1 })];
  const sorted = rows.map(spreadSortValue).sort((a, b) => b - a);
  assert.deepEqual(sorted, [30, 4, 1]);
});

test("etDate is New York's date, not UTC's", () => {
  // 2026-11-04 03:00 UTC is still 2026-11-03 in New York
  assert.equal(etDate(Date.parse("2026-11-04T03:00:00Z")), "2026-11-03");
});
