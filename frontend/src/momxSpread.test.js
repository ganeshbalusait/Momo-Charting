import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { currentPrice, formatPrice, spreadCell, spreadMapFrom, spreadSortValue } from "./momxSpread.js";

const entry = (over = {}) => ({
  cls: "Wide", medPct: 11.2, medUsd: 0.18, contracts: 20, reads: 3, atmRule: false,
  chainTs: Date.UTC(2026, 10, 3, 16, 5) / 1000, expiries: { weekly: "2026-11-06", monthly: "2026-11-20" },
  note: "Mid orders rarely fill.", ...over,
});

test("map keeps identity of unchanged entries and drops junk", () => {
  const first = spreadMapFrom({ spreads: { nvda: entry(), BAD: { cls: "Huge" }, NUL: null } });
  assert.deepEqual([...first.keys()], ["NVDA"]);
  const second = spreadMapFrom({ spreads: { NVDA: entry() } }, first);
  assert.equal(second.get("NVDA"), first.get("NVDA"));
  const third = spreadMapFrom({ spreads: { NVDA: entry({ cls: "Avoid" }) } }, second);
  assert.notEqual(third.get("NVDA"), second.get("NVDA"));
  assert.equal(spreadMapFrom(null).size, 0);
});

test("cell shows the class with median % and $ in the tooltip", () => {
  const cell = spreadCell(entry());
  assert.equal(cell.text, "Wide");
  assert.equal(cell.tone, "wide");
  assert.match(cell.title, /Median spread 11\.2% of the mid \(\$0\.18\)/);
  assert.match(cell.title, /weekly 2026-11-06, monthly 2026-11-20/);
  assert.match(cell.title, /Chain read 11:05 AM ET · median of the last 3 reads/);
  assert.match(spreadCell(entry({ cls: "Avoid", atmRule: true })).title, /\$0\.30\+ wide/);
});

test("no fresh chain prints a dash", () => {
  const cell = spreadCell(undefined);
  assert.equal(cell.text, "-");
  assert.match(cell.title, /no fresh option chain/);
});

test("sort puts Avoid above Wide above OK above Tight, then by %", () => {
  const values = ["Tight", "OK", "Wide", "Avoid"].map((cls) => spreadSortValue(entry({ cls, medPct: 5 })));
  assert.deepEqual([...values].sort((a, b) => a - b), values);
  assert.ok(spreadSortValue(entry({ medPct: 14 })) > spreadSortValue(entry({ medPct: 9 })));
  assert.equal(spreadSortValue(null), null);
});

test("price prefers the live quote and formats pennies with 4 decimals", () => {
  assert.equal(currentPrice(101.5, { last: 100 }), 101.5);
  assert.equal(currentPrice(undefined, { last: 100 }), 100);
  assert.equal(currentPrice(null, {}), null);
  assert.equal(formatPrice(123.456), "123.46");
  assert.equal(formatPrice(0.51234), "0.5123");
  assert.equal(formatPrice(null), "");
});

test("the board declares Price and Spread right after % Chg", () => {
  const source = readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  const pct = source.indexOf('{ key: "pctChange"');
  const price = source.indexOf('{ key: "price", label: "Price", kind: "price"');
  const spread = source.indexOf('{ key: "spread", label: "Spread", kind: "spread"');
  assert.ok(pct > 0 && price > pct && spread > price);
});
