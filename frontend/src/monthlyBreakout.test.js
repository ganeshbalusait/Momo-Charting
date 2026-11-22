import assert from "node:assert/strict";
import test from "node:test";

import { breakoutChips, breakoutNote, chartTimeframeKey, monthName, pickerFull } from "./monthlyBreakout.js";

test("the list limit counts only the trader's own lists, not the computed ones", () => {
  // His screen on 2026-11-20: Mag7, Watchlist, 2 EMA lists, 2 of his own -
  // "List limit reached" although the server would take two more.
  const lists = [
    { name: "Mag7", derived: false },
    { name: "Watchlist", derived: false },
    { name: "21 EMA Touch Weekly", derived: true },
    { name: "50 EMA Touch Monthly", derived: true },
    { name: "Liquid Movers", derived: false },
    { name: "Movers", derived: false },
    { name: "Monthly Bull Breakout", derived: true },
    { name: "Monthly Bear Breakout", derived: true },
  ];
  assert.equal(pickerFull(lists, 6), false);
  assert.equal(pickerFull([...lists, { name: "A" }, { name: "B" }], 6), true);
  assert.equal(pickerFull(null, 6), false);
  assert.equal(pickerFull(lists, undefined), false);
});

test("month names", () => {
  assert.equal(monthName("2026-11"), "Nov 26");
  assert.equal(monthName("2026-01-15"), "Jan 26");
  assert.equal(monthName(""), "");
  assert.equal(monthName("2026-13"), "");
});

test("bull chips: live month first, then by symbol, with the line and status", () => {
  const chips = breakoutChips({
    breakout: "up",
    breakouts: {
      MA: { pattern: "MIDDLE", label: "Range", status: "confirmed", period: "2026-10", level: 601.77, target: 863.33 },
      ABC: { pattern: "BOTTOM", label: "Higher lows + resistance", status: "confirmed", period: "2026-10", level: 25.5 },
      ZZZ: { pattern: "SYM", label: "Symmetrical triangle", status: "live", period: "2026-11", level: 12.345 },
    },
  });
  assert.deepEqual(chips.map((c) => c.symbol), ["ZZZ", "ABC", "MA"]);
  assert.equal(chips[0].live, true);
  assert.equal(chips[0].when, "Nov 26 not closed");
  assert.equal(chips[0].text, "Triangle > 12.35");
  assert.equal(chips[2].text, "Range > 601.77");
  assert.equal(chips[2].when, "Oct 26 close");
  assert.equal(chips[2].arrow, "▲");
  assert.match(chips[2].title, /Measured target 863\.33/);
  assert.match(chips[2].title, /below the 50 EMA/);
  assert.doesNotMatch(chips[1].title, /Measured target/);   // no target sent
});

test("bear chips point down and carry no exit idea", () => {
  const [chip] = breakoutChips({
    breakout: "down",
    breakouts: { BABA: { pattern: "TOP", label: "Lower highs + support", status: "live", period: "2026-11", level: 91.99, target: null } },
  });
  assert.equal(chip.arrow, "▼");
  assert.equal(chip.text, "LH + Support < 91.99");
  assert.doesNotMatch(chip.title, /50 EMA/);
});

test("no direction or junk rows give no chips", () => {
  assert.deepEqual(breakoutChips({}), []);
  assert.deepEqual(breakoutChips({ breakout: "sideways", breakouts: { A: {} } }), []);
  assert.deepEqual(breakoutChips({ breakout: "up", breakouts: { A: null, B: "x" } }), []);
  assert.deepEqual(breakoutChips(null), []);
});

test("notes say what the back-test found", () => {
  assert.match(breakoutNote("up"), /62% for any month/);
  assert.match(breakoutNote("up"), /ALL of the following = the breakout \+ VOLUME 4h >= 1\.0% \+ CLOSE 1h >= 0\.5%/);
  assert.match(breakoutNote("down"), /ALL of the following = the breakdown/);
  assert.match(breakoutNote("down"), /Not a put signal/);
  assert.equal(breakoutNote(""), "");
});

// 2026-11-20 "I see only monthly where is D/W": Weekly / Daily charts.
test("periodName reads months, days and weeks", async () => {
  const { periodName } = await import("./monthlyBreakout.js");
  assert.equal(periodName("2026-11", "Monthly"), "Nov 26");
  assert.equal(periodName("2026-11-20", "Daily"), "Nov 20");
  assert.equal(periodName("2026-11-16", "Weekly"), "wk Nov 16");
  assert.equal(periodName("junk", "Daily"), "");
});

test("weekly chips say week, the note warns there is no back-test edge", async () => {
  const { breakoutChips, breakoutNote, breakoutEmpty } = await import("./monthlyBreakout.js");
  const meta = {
    breakout: "up",
    timeframe: "Weekly",
    breakouts: { WKB: { pattern: "BOTTOM", status: "live", period: "2026-11-16", level: 102, target: 120 } },
  };
  const [chip] = breakoutChips(meta);
  assert.equal(chip.when, "wk Nov 16 not closed");
  assert.match(chip.title, /weekly close above 102\.00/);
  assert.match(chip.title, /a week closing below the 50 EMA/);
  assert.doesNotMatch(chip.title, /12 months/);
  assert.match(breakoutNote("up", "Weekly"), /NO edge on weekly breakouts/);
  assert.match(breakoutNote("down", "Daily"), /close < 21 < 50/);
  assert.match(breakoutNote("up", "Monthly"), /69-78% higher/);
  assert.equal(breakoutEmpty({ breakout: "down", timeframe: "Daily" }), "No daily breakdowns on the Watchlist right now.");
  assert.equal(breakoutEmpty({ breakout: "up" }), "No monthly breakouts on the Watchlist right now.");
});

test("timeframeRequest sends the direction for breakout lists and the length for EMA lists", async () => {
  const { timeframeRequest } = await import("./monthlyBreakout.js");
  assert.deepEqual(timeframeRequest({ breakout: "down" }, "Daily"), { breakout: "down", timeframe: "Daily" });
  assert.deepEqual(timeframeRequest({ emaLength: 21 }, "Weekly"), { length: 21, timeframe: "Weekly" });
  assert.equal(timeframeRequest({ name: "Watchlist" }, "Weekly"), null);
  assert.equal(timeframeRequest({ breakout: "up" }, ""), null);
});

test("a Breakout list opens its chart on the list's timeframe; other lists keep the chart's own", () => {
  assert.equal(chartTimeframeKey({ breakout: "up", timeframe: "Monthly" }), "M");
  assert.equal(chartTimeframeKey({ breakout: "down", timeframe: "Weekly" }), "W");
  assert.equal(chartTimeframeKey({ breakout: "up", timeframe: "Daily" }), "D");
  assert.equal(chartTimeframeKey({ breakout: "up" }), "M");
  assert.equal(chartTimeframeKey({ emaLength: 21, timeframe: "Weekly" }), null);
  assert.equal(chartTimeframeKey({ name: "Watchlist" }), null);
});
