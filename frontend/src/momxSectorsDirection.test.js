import test from "node:test";
import assert from "node:assert/strict";
import { sectorsForDirection, bearSectorRead } from "./momxCells.js";

const row = (symbol, industry, pct, last, vwap) => ({ symbol, industry, pctChange: pct, last, m5: { vwap } });

test("bull: a 🔥 sector that turned negative today is left off the bull board", () => {
  const groups = [
    { name: "Networking", hot: true, late: false, today: -2.4, leaders: [] },
    { name: "Pharma", hot: true, late: false, today: 0.2, leaders: [] },
  ];
  const out = sectorsForDirection(groups, [], "bull");
  assert.deepEqual(out.map((g) => g.name), ["Pharma"]);
  assert.equal(out[0].hot, true);
});

test("bear: 🔥 goes to FALLING sectors (60% down and below VWAP, median <= -0.5%)", () => {
  const rows = [
    row("A", "Networking", -3, 9, 10), row("B", "Networking", -2, 9, 10), row("C", "Networking", -1, 9, 10), row("D", "Networking", 0.5, 11, 10),
    row("E", "Pharma", 0.5, 11, 10), row("F", "Pharma", 0.2, 11, 10), row("G", "Pharma", -0.1, 9, 10), row("H", "Pharma", 0.3, 11, 10),
  ];
  const read = bearSectorRead(rows);
  assert.equal(read.get("Networking").down, 3);
  const groups = [{ name: "Pharma", hot: true, late: false, today: 0.2, leaders: [] }];
  const out = sectorsForDirection(groups, rows, "bear");
  assert.equal(out[0].name, "Networking");
  assert.equal(out[0].bear, true);
  assert.deepEqual(out[0].leaders.map((l) => l.symbol), ["A", "B", "C"]);
  const pharma = out.find((g) => g.name === "Pharma");
  assert.equal(pharma.hot || pharma.late, false, "a bull-hot sector is not 🔥 on the bear board");
});
