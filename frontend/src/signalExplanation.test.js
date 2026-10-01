import test from "node:test";
import assert from "node:assert/strict";

import {
  explainCallSignal,
  explainCatalyst,
  explainFire,
  explainScannerRow,
  explainStrength,
} from "./signalExplanation.js";

test("cyan and yellow calls name their EMAs and timeframe", () => {
  const cyan = explainCallSignal("CALL2H", "9x20");
  assert.match(cyan, /CALL2H \(cyan\)/);
  assert.match(cyan, /2-hour chart/);
  assert.match(cyan, /9-period average crossed above the 20-period/);
  const yellow = explainCallSignal("CALL4H", "4x8");
  assert.match(yellow, /CALL4H \(yellow\)/);
  assert.match(yellow, /4-hour chart/);
  assert.match(yellow, /4-period average crossed above the 8-period/);
});

test("fires name the timeframe and are labelled weakest-class", () => {
  const line = explainFire("1h", "");
  assert.match(line, /1-hour chart/);
  assert.match(line, /weakest signal class/);
  assert.match(explainFire("D", ""), /daily chart/);
});

test("strength restates the trader's own rules, forming warns of repaint", () => {
  assert.match(explainStrength("STRONG", 5, false), /cyan and yellow agree/);
  assert.match(explainStrength("MODERATE", 2, false), /one real confirmation/);
  assert.match(explainStrength("WEAK", 1, false), /ones to skip/);
  assert.match(explainStrength("MODERATE", 2, true), /can still repaint away/);
  assert.doesNotMatch(explainStrength("STRONG", 5, false), /repaint/);
});

test("catalyst line shows the headline or says the move is technical-only", () => {
  assert.match(
    explainCatalyst({ tag: "EARNINGS", headline: "Beats Estimates", ageMinutes: 180 }),
    /EARNINGS: Beats Estimates \(3h ago\)/,
  );
  assert.match(explainCatalyst(null), /technical only/);
});

test("whole-row explanation is ordered: cyan, yellow, fires, strength, catalyst", () => {
  const lines = explainScannerRow({
    signals920: ["CALL2H"],
    signals48: ["CALL4H"],
    fires: ["1h"],
    fireDates: { "1h": "2026-08-24T06:30:00-04:00" },
    strength: "STRONG",
    score: 5,
    forming: false,
    catalyst: { tag: "NEWS", headline: "Hikes", ageMinutes: 30 },
  });
  assert.equal(lines.length, 5);
  assert.match(lines[0], /CALL2H \(cyan\)/);
  assert.match(lines[1], /CALL4H \(yellow\)/);
  assert.match(lines[2], /🔥1h/);
  assert.match(lines[3], /STRONG \(score 5\)/);
  assert.match(lines[4], /News behind the move/);
  assert.deepEqual(explainScannerRow(null), []);
});

test("D-M labels explain their timeframe and lead the whole-row order", async () => {
  const { explainCyanHigherSignal, explainYellowHigherSignal, explainMacdHigherSignal } =
    await import("./signalExplanation.js");
  assert.match(explainCyanHigherSignal("CALLD", ""), /daily chart/);
  assert.match(explainCyanHigherSignal("CALLW", ""), /weekly chart/);
  assert.match(explainCyanHigherSignal("CALLM", ""), /monthly chart/);
  assert.match(explainCyanHigherSignal("CALL2D", ""), /2-day chart/);
  assert.match(explainYellowHigherSignal("CALLD", ""), /4-period average crossed above the 8-period/);
  assert.match(explainMacdHigherSignal("MACD-D", ""), /MACD turned bullish on the daily chart/);
  const lines = explainScannerRow({
    signalsCyanHigher: ["CALLD"],
    signalsYellowHigher: ["CALLW"],
    signalsMacdHigher: ["MACD-D"],
    higherSignalTimes: { CALLD: "2026-08-24T01:00:00-04:00" },
    signals920: ["CALL2H"],
    signals48: [],
    fires: [],
    strength: "STRONG",
    score: 4,
  });
  assert.match(lines[0], /CALLD \(cyan, higher timeframe\)/);
  assert.match(lines[0], /printed 2026-08-24T01:00:00-04:00/);
  assert.match(lines[1], /CALLW \(yellow, higher timeframe\)/);
  assert.match(lines[2], /MACD-D/);
  assert.match(lines[3], /CALL2H \(cyan\)/);
});
