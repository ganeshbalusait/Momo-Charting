import assert from "node:assert/strict";
import test from "node:test";

import { layoutChartAxisLabels, mergePivotAndOiAxisLabels } from "./chartAxisLabels.js";

test("keeps a pivot label visible when an OI wall shares the same price", () => {
  const labels = mergePivotAndOiAxisLabels([
    { key: "pivot-WEEK-R1", price: 272.5, title: "W P R1 272.50", color: "#ff0000" },
    { key: "oi-wall-0-272.5", price: 272.5, title: "C S 272.5", color: "#0099cc", preservePricePosition: true, priority: 5 },
  ]);
  assert.equal(labels.length, 1);
  assert.equal(labels[0].title, "W P R1 272.50 · C S 272.5");
  assert.equal(labels[0].preservePricePosition, true);
  assert.equal(labels[0].priority, 5);
  assert.deepEqual(labels[0].parts.map(({ color }) => color), ["#ff0000", "#0099cc"]);
});

test("does not merge nearby but different prices", () => {
  const labels = mergePivotAndOiAxisLabels([
    { key: "pivot-WEEK-R1", price: 272.5, title: "W P R1", color: "#ff0000" },
    { key: "oi-wall-0-272", price: 272, title: "C S 272", color: "#0099cc" },
  ]);
  assert.equal(labels.length, 2);
});

test("an opted-in blocked label merges into the chip that displaced it", () => {
  // 50sD at 510.08 lands ~1.5px from a 4K wall at 510.00. It sits in the
  // lowest priority band, so it can never win the row - it must merge instead.
  const labels = layoutChartAxisLabels([
    { key: "oi-wall-0-510", top: 100, title: "4K 8/14", color: "#0099cc", preservePricePosition: true, priority: 5 },
    { key: "mtf-ma-DAY-sma3", top: 101.5, title: "50sD", color: "#74bde8", priority: 1, mergeWhenBlocked: true },
  ], { paneHeight: 600 });

  assert.equal(labels.length, 1);
  assert.equal(labels[0].key, "oi-wall-0-510");
  assert.equal(labels[0].title, "4K 8/14 · 50sD");
  assert.deepEqual(labels[0].parts.map(({ title }) => title), ["4K 8/14", "50sD"]);
  // Each part keeps its own colour, so 50sD still reads as its ThinkScript blue.
  assert.deepEqual(labels[0].parts.map(({ color }) => color), ["#0099cc", "#74bde8"]);
});

test("a blocked label without the opt-in still hides, and an unblocked one stays separate", () => {
  const hidden = layoutChartAxisLabels([
    { key: "oi-wall-0-510", top: 100, title: "4K 8/14", preservePricePosition: true, priority: 5 },
    { key: "pivot-WEEK-R1", top: 101.5, title: "W P R1" },
  ], { paneHeight: 600 });
  assert.equal(hidden.length, 1);
  assert.equal(hidden[0].title, "4K 8/14");

  const roomy = layoutChartAxisLabels([
    { key: "oi-wall-0-510", top: 100, title: "4K 8/14", preservePricePosition: true, priority: 5 },
    { key: "mtf-ma-DAY-sma3", top: 400, title: "50sD", priority: 1, mergeWhenBlocked: true },
  ], { paneHeight: 600 });
  assert.equal(roomy.length, 2);
  assert.equal(roomy.find(({ key }) => key === "mtf-ma-DAY-sma3")?.title, "50sD");
});

test("keeps every surviving label fixed to its indicator line", () => {
  const labels = layoutChartAxisLabels([
    { key: "live-price", top: 100, preservePricePosition: true },
    { key: "oi-wall-1-100", top: 103, preservePricePosition: true, priority: 5 },
    { key: "session-level-dH", top: 101 },
    { key: "9eD-100", top: 102 },
    { key: "21eD-100", top: 104 },
  ], { paneHeight: 160, minimumGap: 18 });

  assert.equal(labels.find(({ key }) => key === "live-price")?.top, 100);
  assert.equal(labels.some(({ key }) => key === "oi-wall-1-100"), false);
  assert.ok(labels.every(({ top }) => [100, 101, 102, 104].includes(top)));
  for (let index = 1; index < labels.length; index += 1) {
    assert.ok(labels[index].top - labels[index - 1].top >= 18);
  }
});

test("uses a pane-size label budget and retains higher-value study labels", () => {
  const labels = layoutChartAxisLabels([
    { key: "generic-1", top: 41 },
    { key: "generic-2", top: 42 },
    { key: "generic-3", top: 43 },
    { key: "pivot-WEEK-R1", top: 44 },
    { key: "session-level-pmH", top: 45 },
  ], { paneHeight: 60, padding: 9, minimumGap: 18 });

  assert.ok(labels.length <= 3);
  assert.ok(labels.some(({ key }) => key === "session-level-pmH"));
  assert.equal(labels.find(({ key }) => key === "session-level-pmH")?.top, 45);
  assert.equal(labels.some(({ key }) => key === "pivot-WEEK-R1"), false);
  for (let index = 1; index < labels.length; index += 1) {
    assert.ok(labels[index].top - labels[index - 1].top >= 18);
  }
});

test("does not move labels away from the pane edge", () => {
  const labels = layoutChartAxisLabels([
    { key: "near-top", top: 4 },
    { key: "inside", top: 9 },
    { key: "near-bottom", top: 156 },
  ], { paneHeight: 160, padding: 9, minimumGap: 18 });

  assert.deepEqual(labels.map(({ key, top }) => ({ key, top })), [
    { key: "inside", top: 9 },
  ]);
});

// 2026-09-30 MNKD $3.97 on the phone: nine MTF MA levels within a few cents
// all merged into the LIVE chip, which grew wider than the pane and pushed
// "LIVE MNKD 3.97" off-screen; 200sD / 200eD appeared twice.
test("MTF MA names never swallow the LIVE chip and merged chips stay short", () => {
  const ma = (key, title, top) => ({ key: `mtf-ma-${key}`, top, title, priority: 1, mergeWhenBlocked: true });
  const positioned = layoutChartAxisLabels([
    { key: "live-price", top: 100, title: "LIVE MNKD 3.97", preservePricePosition: true },
    ma("DAY-sma200", "200sD", 102),
    ma("DAY-ema200", "200eD", 103),
    { key: "oi-wall-0-3.5", top: 300, title: "15 10/16", preservePricePosition: true },
    ma("WEEK-ema50", "50eWk", 301),
    ma("WEEK-ema200", "200eWk", 302),
    ma("MONTH-ema9", "9eMo", 303),
    ma("DAY-sma200b", "200sD", 304),
    ma("DAY-sma50", "50sD", 305),
    ma("WEEK-ema21", "21eWk", 306),
  ], { paneHeight: 600 });
  const live = positioned.find((label) => label.key === "live-price");
  assert.equal(live.title, "LIVE MNKD 3.97");
  assert.equal(live.parts, undefined);
  const wall = positioned.find((label) => label.key === "oi-wall-0-3.5");
  const titles = wall.parts.map((part) => part.title);
  assert.equal(new Set(titles).size, titles.length, "no duplicate names in one chip");
  const names = wall.parts.filter((part) => part.key !== "merged-overflow");
  assert.ok(names.length <= 3, `wall + at most 2 names, got ${titles.join(" · ")}`);
  assert.equal(wall.title, "15 10/16 · 50eWk · 200eWk · +4");
  assert.match(wall.title, /\+\d+$/, "hidden names are counted, not silently dropped");
});

// 2026-11-20: Fib tags were in the lowest band, so on a busy 5-minute chart
// every pivot / MA tag beat them and no Fib tag showed at all. They now sit
// just above the pivots (below OI walls and session levels).
test("a Fib tag wins its row over a pivot tag, and the pivot joins it", () => {
  const laid = layoutChartAxisLabels([
    { key: "pivot-day-R1", title: "D R1", top: 100, mergeWhenBlocked: true },
    { key: "fib-swing-127.09", title: "M Fib 50% (127.09)", top: 104 },
  ], { paneHeight: 400 });
  const chip = laid.find((label) => String(label.key).startsWith("fib-swing-") || (label.parts || []).some((part) => String(part.key).startsWith("fib-swing-")));
  assert.ok(chip, JSON.stringify(laid));
  assert.equal(chip.top, 104, "the Fib tag keeps its own row");
});

test("an OI wall still beats a Fib tag", () => {
  const laid = layoutChartAxisLabels([
    { key: "fib-swing-127.09", title: "M Fib 50% (127.09)", top: 104 },
    { key: "oi-wall-call-127", title: "4K 11/20", top: 100, preservePricePosition: true },
  ], { paneHeight: 400 });
  assert.ok(laid.some((label) => label.key === "oi-wall-call-127"));
  assert.ok(!laid.some((label) => label.key === "fib-swing-127.09"), "the Fib tag hides behind the wall");
});

// 2026-11-20 (his NVDA W chart on a phone): "M Fib 23.6% (188.49)", "W Fib
// 38.2% (183.49)" and "D Fib 50% (183.94)" joined into one 399px chip.
test("a Fib tag never joins another Fib tag; it still joins a wall", () => {
  const laid = layoutChartAxisLabels([
    { key: "fib-swing-188.49", title: "M Fib 23.6% (188.49)", top: 100, mergeWhenBlocked: true },
    { key: "fib-swing-183.49", title: "W Fib 38.2% (183.49)", top: 108, mergeWhenBlocked: true },
  ], { paneHeight: 400 });
  assert.equal(laid.length, 1);
  assert.equal((laid[0].parts || []).length || 1, 1, JSON.stringify(laid[0]));
  const walls = layoutChartAxisLabels([
    { key: "oi-wall-call-127", title: "4.3K 12/18", top: 100, preservePricePosition: true },
    { key: "fib-swing-127.09", title: "M Fib 50% (127.09)", top: 104, mergeWhenBlocked: true },
  ], { paneHeight: 400 });
  assert.equal(walls.length, 1);
  assert.ok(JSON.stringify(walls[0]).includes("M Fib 50%"), "the Fib name joins the wall chip");
});
