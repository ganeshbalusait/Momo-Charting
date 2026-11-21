import assert from "node:assert/strict";
import test from "node:test";
import {
  PHONE_DOT_GROUPS,
  PHONE_SKITTLES_TFS,
  phoneDots,
  phoneRowSummary,
  phoneSkittlesCells,
  phoneSkittlesStyle,
} from "./momxPhoneCard.js";

const NOW = Date.parse("2026-11-20T15:00:00-05:00");
const sec = (iso) => Math.floor(Date.parse(iso) / 1000);

test("the phone card shows the eight Skittles timeframes of his sample, in order", () => {
  assert.deepEqual(PHONE_SKITTLES_TFS.map(([, label]) => label), ["2h", "4h", "D", "2D", "3D", "4D", "W", "Mo"]);
  assert.deepEqual(PHONE_SKITTLES_TFS.map(([tf]) => tf), ["2h", "4h", "D", "2D", "3D", "4D", "Wk", "M"]);
});

// 2026-11-21 "Revert the changes back to original": the card uses the TOS
// colours again, like the Table - no value scale, no yellow ring.
test("card colours are the TOS colours: painted = solid pill, unpainted = coloured number", () => {
  assert.deepEqual(phoneSkittlesStyle({ value: 70, bg: "cyan", fg: "black" }),
    { kind: "fill", color: "#00ffff", ink: "#000000" });
  assert.deepEqual(phoneSkittlesStyle({ value: 70, fg: "magenta" }),
    { kind: "text", color: "#ff00ff", ink: "#ff00ff" });
  assert.equal(phoneSkittlesStyle({ value: 70, bg: "black", fg: "cyan" }).kind, "text",
    "black background is the script's 'nothing here', not a fill");
  for (const value of [65, 68, 72, 74]) {
    assert.notEqual(phoneSkittlesStyle({ value, fg: "cyan" }).kind, "outline", "no yellow ring for " + value);
  }
});

test("a Skittles cell carries its TOS colour and whether TOS painted it", () => {
  const row = { skittles: {
    "2h": { value: 91.4, bg: "cyan", fg: "black" },
    "4h": { value: 23.6, fg: "magenta" },
    D: { value: 0.2, bg: "black", fg: "white" },
  } };
  const cells = phoneSkittlesCells(row, NOW);
  assert.equal(cells.length, 8);
  assert.equal(cells[0].text, "91");
  assert.equal(cells[0].painted, true);
  assert.equal(cells[0].kind, "fill");
  assert.equal(cells[0].color, "#00ffff");
  assert.equal(cells[1].text, "24");
  assert.equal(cells[1].painted, false);
  assert.equal(cells[1].kind, "text");
  assert.equal(cells[1].color, "#ff00ff");
  assert.equal(cells[2].text, "0", "black background is the script's 'nothing here', not a fill");
  assert.equal(cells[2].painted, false);
  assert.equal(cells[3].text, "", "a missing timeframe is blank, not zero");
  assert.equal(cells[3].kind, "empty");
});

test("a Skittles cell whose cross fired in the last 15 minutes is marked fresh (the glowing box)", () => {
  const row = { skittles: {
    "2h": { value: 80, bg: "cyan", fg: "black", barAt: sec("2026-11-20T14:50:00-05:00") },
    "4h": { value: 80, bg: "cyan", fg: "black", barAt: sec("2026-11-20T13:00:00-05:00") },
  } };
  const [fresh, old] = phoneSkittlesCells(row, NOW);
  assert.equal(fresh.fresh, true);
  assert.equal(old.fresh, false);
});

test("a deliberately hidden (black on black) cell prints nothing", () => {
  const row = { skittles: { "2h": { value: 40, bg: "black", fg: "black" } } };
  assert.equal(phoneSkittlesCells(row, NOW)[0].text, "");
});

test("the small dots are RVOL 5m-1h, RVOL 2h-D, and SQZ 2h-W, lit only where the script painted", () => {
  assert.deepEqual(PHONE_DOT_GROUPS.map((group) => [group.section, group.tfs.join(",")]), [
    ["rvol", "5m,15m,30m,1h"],
    ["rvol", "2h,4h,D"],
    ["sqz", "2h,4h,D,Wk"],
  ]);
  const row = {
    rvol: { "5m": { value: 3.1, bg: "cyan", fg: "black" }, "15m": { value: 0.4, bg: "black", fg: "black" } },
    sqz: { Wk: { value: 1, bg: "magenta", fg: "white" } },
  };
  const groups = phoneDots(row);
  assert.equal(groups.length, 3);
  assert.equal(groups[0].length, 4);
  assert.ok(groups[0][0].color, "5m RVOL lit");
  assert.equal(groups[0][1].color, null, "black is not lit");
  assert.equal(groups[1].every((dot) => dot.color === null), true);
  assert.ok(groups[2][3].color, "weekly squeeze lit");
});

test("row summary: % change text and tone, industry colour, news age", () => {
  const row = {
    symbol: "NVDA",
    pctChange: -2.345,
    industry: "Semis",
    news: { headline: "Chip story", publishedAt: "2026-11-20T14:42:00-05:00" },
  };
  const summary = phoneRowSummary(row, NOW);
  assert.equal(summary.symbol, "NVDA");
  assert.equal(summary.pctText, "-2.35%");
  assert.equal(summary.tone, "down");
  assert.equal(summary.industry, "Semis");
  assert.match(summary.industryColor, /^#|^rgb|^hsl|^oklch/);
  assert.equal(summary.newsAge, "18m");
  assert.equal(summary.skittles.length, 8);
  assert.equal(summary.dots.length, 3);
});

test("row summary copes with a row that has no data yet", () => {
  const summary = phoneRowSummary({ symbol: "ZZZ" }, NOW);
  assert.equal(summary.pctText, "");
  assert.equal(summary.tone, "");
  assert.equal(summary.industry, "");
  assert.equal(summary.newsAge, "");
  assert.equal(summary.skittles.every((cell) => cell.text === ""), true);
  assert.equal(phoneRowSummary(null, NOW), null);
});
