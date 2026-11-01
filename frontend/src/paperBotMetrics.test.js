import test from "node:test";
import assert from "node:assert/strict";
import { paperMetrics, paperTotals, paperTime, reviewLabel } from "./paperBotMetrics.js";

test("partial entry ROI uses actual fills, including legacy history", () => {
  const row = { status: "closed", qty: 10, buyFill: 2, held: 0, sells: [{ qty: 3, price: 1 }], pl: -300 };
  assert.equal(paperMetrics(row).filled, 3);
  assert.equal(paperMetrics(row).roi, -50);
  assert.equal(paperTotals([row]).roi, -50);
  assert.equal(paperMetrics({ ...row, sells: [], buyFilledQty: undefined }).cost, null);
});
test("partial exits keep realized and fresh open losses separate", () => {
  const row = { status: "open", qty: 10, buyFilledQty: 3, buyFill: 2, held: 2, sells: [{ qty: 1, price: 3 }], markMid: 1, markAt: "2026-10-30T14:00:00Z" };
  const now = Date.parse(row.markAt) + 30000;
  assert.equal(paperMetrics(row, now).realized, 100);
  assert.equal(paperMetrics(row, now).open, -200);
  assert.equal(paperMetrics(row, now).roi, null);
  assert.equal(paperTotals([row], now).realized, 100);
  assert.equal(paperTotals([row], now + 120000).open, null);
  assert.equal(paperTotals([row], now + 120000).unpriced, 1);
  assert.equal(paperMetrics({ ...row, markMid: 0 }, now).open, -400);
});
test("missing fill prices are not zero profit and unknown cost is not ordered cost", () => {
  const row = { status: "closed", qty: 10, buyFill: 2, sells: [{ qty: 2, price: null }], pl: null };
  assert.equal(paperMetrics(row).realized, null);
  assert.equal(paperTotals([row]).roi, null);
  assert.equal(paperMetrics({...row, pl: 100}).realized, null);
  assert.equal(paperTotals([row]).realized, null);
  assert.equal(paperTotals([row]).unpricedClosed, 1);
});

test("broker-confirmed expiration records the entire loss without an assumed fill", () => {
  const row = {status:"closed", buyFill:.01, buyFilledQty:300, held:0, sells:[{qty:300,price:0,kind:"expiration"}]};
  assert.equal(paperMetrics(row).realized,-300);
  assert.equal(paperMetrics(row).roi,-100);
  assert.equal(paperTotals([row]).losses,1);
  assert.equal(paperMetrics({...row,buyFilledQty:301,pl:0}).realized,null);
});
test("review states are per trade and downtime is not a successful review", () => {
  const row = { status: "closed" };
  assert.equal(reviewLabel(null, "a", row), "Review status unavailable");
  for (const [state, label] of Object.entries({pending:"Queued", running:"Reviewing · batch", complete:"Reviewed · batch", failed:"Review failed"}))
    assert.equal(reviewLabel({tradeStates:{a:state}}, "a", row), label);
  assert.equal(reviewLabel({enabled:true}, "a", row), "Not queued");
  assert.equal(reviewLabel({enabled:true}, "a", {status:"open"}), "Awaiting close");
});
test("order and fill timestamps display Eastern time across DST", () => {
  assert.equal(paperTime("2026-10-30T14:05:00Z"), "10:05");
  assert.equal(paperTime("2026-12-01T14:05:00Z"), "09:05");
  assert.equal(paperTime(null), "—");
});
