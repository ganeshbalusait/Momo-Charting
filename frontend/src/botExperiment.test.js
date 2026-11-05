import test from "node:test";
import assert from "node:assert/strict";
import { awaitingLines, experimentRuleText } from "./botExperiment.js";

test("nothing waiting -> no approval box", () => {
  assert.equal(awaitingLines(null), null);
  assert.equal(awaitingLines({}), null);
});

test("a waiting rule reads in plain words with the forward test and the statistics check", () => {
  const lines = awaitingLines({
    id: "cand1", screen: { spec: { kind: "spread_ceiling", value: 0.1 } },
    evaluation: { trades: 104, observedDays: 10, candidateNet: 1250.4, baselineNet: -310 },
    statsCheck: { passed: true, trials: 7, lower: 12.6 },
  });
  assert.equal(lines.title, "A new Paper 5 rule is waiting for your OK");
  assert.equal(lines.rule, "Only take entries with a spread no wider than 10% of the price");
  assert.equal(lines.test, "Forward test on real Paper 3 fills: 104 trades over 10 days · with the rule $1,250 vs −$310 without it.");
  assert.equal(lines.stats, "Statistics check passed: still better after counting all 7 rules the planner has tried (worst-case daily gain $13).");
  assert.match(lines.note, /stays on its current rules until you approve/);
});

test("rule texts for every supported experiment", () => {
  assert.equal(experimentRuleText({ kind: "premium_floor", value: 0.5 }), "Only take options priced at least $0.50");
  assert.equal(experimentRuleText({ kind: "entry_after", value: 585 }), "Only take entries from 09:45 ET");
  assert.equal(experimentRuleText(null), "Unknown rule");
});
