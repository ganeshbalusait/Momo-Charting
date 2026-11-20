// The High OI board's "B/S" cell (2026-11-19, "I want OI, they are buyers or sellers"): the app's estimate from where
// trades printed between the bid and the ask. Short on the board, the whole story in the tooltip, never called a fact.
import assert from "node:assert/strict";
import test from "node:test";

import { buildHighOiContractList } from "./highOiContractList.js";
import { OI_SIDE_NOTE, oiSideCell } from "./oiSideEstimate.js";

const est = (over) => ({ bought: 620, sold: 380, mid: 100, unseen: 400, volume: 1500, share: 0.62, label: "mostly bought", oiChange: null, oiSince: null, day: "2026-11-19", ...over });

test("mostly bought reads as a percent with B, and the tooltip gives the counts and says it is an estimate", () => {
  const cell = oiSideCell(est());
  assert.equal(cell.text, "62% B");
  assert.equal(cell.tone, "bought");
  assert.equal(cell.title, "mostly bought: 620 bought, 380 sold of 1,500 traded (2026-11-19). Estimate, not exchange data.");
});

test("mostly sold shows the SOLD share, not the bought one", () => {
  const cell = oiSideCell(est({ bought: 100, sold: 400, share: 0.2, label: "mostly sold", volume: 600 }));
  assert.equal(cell.text, "80% S");
  assert.equal(cell.tone, "sold");
});

test("a near even split reads mixed", () => {
  const cell = oiSideCell(est({ bought: 260, sold: 240, share: 0.52, label: "mixed" }));
  assert.deepEqual([cell.text, cell.tone], ["mixed", "mixed"]);
});

test("open interest change rides in the tooltip, and is the cell itself when there is no bought/sold read", () => {
  assert.match(oiSideCell(est({ oiChange: 2500, oiSince: "2026-11-18" })).title, /open interest up 2,500 since 2026-11-18/);
  const only = oiSideCell(est({ label: null, share: null, bought: 0, sold: 0, oiChange: -12400, oiSince: "2026-11-18" }));
  assert.deepEqual([only.text, only.tone], ["OI -12k", "oi"]);
  assert.match(only.title, /open interest down 12,400 since 2026-11-18/);
  assert.equal(oiSideCell(est({ label: null, share: null, oiChange: 900, oiSince: "2026-11-18" })).text, "OI +900");
});

test("no estimate is a dash that says why, never a zero", () => {
  for (const nothing of [null, undefined, "x", {}, est({ label: null, share: null })]) {
    const cell = oiSideCell(nothing);
    assert.equal(cell.text, "-");
    assert.match(cell.title, /No estimate yet/);
  }
});

test("the note under the board says it is an estimate and not exchange data", () => {
  assert.match(OI_SIDE_NOTE, /estimate/);
  assert.match(OI_SIDE_NOTE, /not exchange data/);
});

test("the High OI board keeps a row's estimate, and rows without one are unchanged", () => {
  const now = new Date(Date.UTC(2026, 10, 19));
  const row = (side, strike, oi, extra = {}) => ({ side, strike, expiry: "2026-11-20", open_interest: oi, volume: 100, delta: 0.3, last: 1.1, days_to_expiration: 1, ...extra });
  const model = buildHighOiContractList({ rows: [row("CALL", 150, 900, { sideEst: est() }), row("CALL", 155, 500), row("PUT", 140, 700)], underlyingPrice: 146, now });
  const call150 = model.calls.find((r) => r.strike === 150);
  assert.equal(call150.sideEst.label, "mostly bought");
  assert.equal("sideEst" in model.calls.find((r) => r.strike === 155), false);
  assert.equal("sideEst" in model.puts[0], false);
});
