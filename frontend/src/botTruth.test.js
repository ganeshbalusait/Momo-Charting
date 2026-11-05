import test from "node:test";
import assert from "node:assert/strict";
import { truthGapLabel, truthHeadline, truthMoney, truthPct, truthAccountLabel } from "./botTruth.js";

test("headline says the fill rate and the BOT vs paper ROI in plain words", () => {
  const acct = { orders: 319, filled: 105, missed: 212, matched: { n: 105, botRoi: -6.3, paperRoi: -10.7 } };
  assert.equal(truthHeadline(acct),
    "Your mid orders filled 105 of 317 (33%). On the 105 trades that filled and closed, the BOT says −6.3%, your paper account got −10.7%.");
});

test("headline with no orders or nothing closed yet", () => {
  assert.equal(truthHeadline(null), "No paper orders yet.");
  assert.equal(truthHeadline({ orders: 0 }), "No paper orders yet.");
  assert.equal(truthHeadline({ orders: 2, filled: 0, missed: 0, matched: { n: 0 } }),
    "No order has filled or missed yet. No filled trade has closed yet to compare.");
  assert.equal(truthHeadline({ orders: 3, filled: 1, missed: 2, matched: { n: 0 } }),
    "Your mid orders filled 1 of 3 (33%). No filled trade has closed yet to compare.");
});

test("gap label: better, worse, matches, unknown", () => {
  assert.equal(truthGapLabel(27.9, 11.6), "BOT looks 16.3 pts better than real");
  assert.equal(truthGapLabel(-1.8, 12.1), "BOT looks 13.9 pts worse than real");
  assert.equal(truthGapLabel(5.0, 5.4), "BOT matches paper");
  assert.equal(truthGapLabel(null, 3), "Not enough closed trades");
});

test("formatters", () => {
  assert.equal(truthPct(231.6), "+231.6%");
  assert.equal(truthPct(-10.7), "−10.7%");
  assert.equal(truthPct(null), "-");
  assert.equal(truthMoney(-3116.5), "−$3,117");
  assert.equal(truthMoney(1075.5), "+$1,076");
  assert.equal(truthAccountLabel("paper5"), "Paper 5");
});

test("new order rules line: hidden before any v2 order, then fill rate and ROI", async () => {
  const { truthNewRulesLine } = await import("./botTruth.js");
  assert.equal(truthNewRulesLine({ orders: 3 }), "");
  assert.equal(truthNewRulesLine({ newRules: { orders: 0 } }), "");
  assert.equal(truthNewRulesLine({ newRules: { orders: 4, filled: 3, missed: 1, matched: { n: 2, botRoi: 12, paperRoi: 8.5 } } }),
    "Since the new order rules: filled 3 of 4 (75%) · BOT +12.0% vs paper +8.5% on 2 closed.");
  assert.equal(truthNewRulesLine({ newRules: { orders: 2, filled: 0, missed: 0, matched: { n: 0 } } }),
    "Since the new order rules: 2 orders, none finished yet.");
});
