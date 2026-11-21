// Chart pattern on Daily / Weekly / Monthly charts (2026-11-20, Ganesh: "in
// scanner any tickers can we do in chart patterns in D/W/M when we open the
// chart? That would be good so I know which pattern").
// The chart reads GET /api/momx-scanner/chart-pattern?symbol=X and draws the
// pattern's lines, the dashed target and a tag at the line that broke. The
// pattern read is a fixture whose breakout candle is THIS month / today's
// last session, so it lands on the real live tape; the tape itself is live.
// Read-only: indicator profile saves are answered by charts-core.helpers.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways } from "./harness.mjs";
import { card, openIndicators, profilePosts, studyCheckbox, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const STUDY = "Chart pattern (breakout)";
const PATTERN = "GET /api/momx-scanner/chart-pattern";

function etParts(date) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(date).map(({ type, value }) => [type, value]));
  return { year: parts.year, month: parts.month, day: parts.day };
}

const thisMonth = () => {
  const { year, month } = etParts(new Date());
  return `${year}-${month}`;
};

function monthsBefore(period, count) {
  const [year, month] = period.split("-").map(Number);
  const index = year * 12 + (month - 1) - count;
  return `${Math.floor(index / 12)}-${String((index % 12) + 1).padStart(2, "0")}`;
}

// A monthly symmetrical-triangle breakout on this month's (open) candle.
function monthlyTriangle(symbol) {
  const to = thisMonth();
  return {
    ok: true,
    symbol,
    source: "scanner",
    patterns: {
      Monthly: {
        up: {
          pattern: "SYM", label: "Symmetrical triangle", direction: "up", status: "live", period: to,
          level: 150, target: 220,
          lines: [
            { from: monthsBefore(to, 20), fromPrice: 190, to, toPrice: 150, bars: 20, role: "falling highs" },
            { from: monthsBefore(to, 16), fromPrice: 90, to, toPrice: 120, bars: 16, role: "rising lows" },
          ],
        },
        down: null,
      },
      Weekly: null,
      Daily: null,
    },
  };
}

const patternTags = (page, index = 0) => card(page, index).getAttribute("data-chart-pattern");
const patternLines = async (page, index = 0) => Number(await card(page, index).getAttribute("data-chart-pattern-lines"));
const patternReads = (app) => app.reads.filter((read) => read.path === "/api/momx-scanner/chart-pattern");

e2eTest("a Monthly chart draws the triangle's two lines, the dashed target and a tag naming the pattern", async () => {
  const fakeReads = { [PATTERN]: (read) => ({ json: monthlyTriangle(new URLSearchParams(read.search).get("symbol")) }) };
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["M"] }, fakeReads }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "M" });
    await page.waitForFunction((selector) => /Triangle/.test(document.querySelector(selector)?.dataset.chartPattern || ""),
      "section.oi-finder-chart-card[data-tape-length]", { timeout: 60_000 });
    const month = new Date().toLocaleString("en-US", { timeZone: "America/New_York", month: "short" });
    const year = thisMonth().slice(2, 4);
    assert.equal(await patternTags(page), `M ▲ Triangle 150.00 (${month} ${year} open) | M Triangle target 220.00`);
    assert.equal(await patternLines(page), 3, "two pattern lines + the dashed target");
    assert.ok(patternReads(app).some((read) => /symbol=NVDA/.test(read.search)), "the chart asked for NVDA's pattern");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("the study is on by default on a Daily chart; switching it off removes the pattern and saves the D profile, on brings it back", async () => {
  let daily = null;
  const fakeReads = {
    [PATTERN]: (read) => ({
      json: {
        ok: true,
        symbol: new URLSearchParams(read.search).get("symbol"),
        source: "fetched",
        patterns: { Monthly: null, Weekly: null, Daily: daily },
      },
    }),
  };
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["D"] }, fakeReads }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL", timeframe: "D" });
    assert.equal(await patternTags(page), "", "no daily break in the first answer: nothing drawn");
    // A range breakdown on the last session on the chart.
    const last = Number(await card(page).getAttribute("data-tape-last"));
    const { year, month, day } = etParts(new Date((last + 12 * 3600) * 1000));
    const to = `${year}-${month}-${day}`;
    daily = {
      up: null,
      down: {
        pattern: "MIDDLE", label: "Range", direction: "down", status: "confirmed", period: to,
        level: 180, target: null,
        lines: [
          { from: to, fromPrice: 230, to, toPrice: 230, bars: 30, role: "resistance" },
          { from: to, fromPrice: 180, to, toPrice: 180, bars: 30, role: "support" },
        ],
      },
    };
    const popover = await openIndicators(page);
    const box = studyCheckbox(popover, STUDY);
    assert.equal(await box.isChecked(), true, "Chart pattern is on by default");
    const earlier = profilePosts(app).length;
    await box.click();
    await page.waitForFunction(() => {
      const node = document.querySelector("section.oi-finder-chart-card[data-tape-length]");
      return node && node.dataset.chartPatternLines === "0";
    }, null, { timeout: 15_000 });
    const posts = profilePosts(app).slice(earlier);
    assert.equal(posts.at(-1)?.profile?.indicatorSettings?.chartPattern, false);
    assert.equal(posts.at(-1)?.key, "D");
    const readsBefore = patternReads(app).length;
    await box.click();
    await page.waitForFunction(() => /Range/.test(document.querySelector("section.oi-finder-chart-card[data-tape-length]")?.dataset.chartPattern || ""),
      null, { timeout: 30_000 });
    assert.ok(patternReads(app).length > readsBefore, "switching it on reads the pattern again");
    assert.equal(await patternTags(page), "D ▼ Range 180.00", "a confirmed break with no target: one tag");
    assert.equal(await patternLines(page), 2);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a minute chart never asks for a pattern", async () => {
  const fakeReads = { [PATTERN]: (read) => ({ json: monthlyTriangle(new URLSearchParams(read.search).get("symbol")) }) };
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["5m"] }, fakeReads }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "5m" });
    await page.waitForTimeout(1_500);
    assert.equal(patternReads(app).length, 0);
    assert.equal(await patternTags(page), "");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("on a phone the Monthly pattern is drawn with nothing pushed off the side", async () => {
  const fakeReads = { [PATTERN]: (read) => ({ json: monthlyTriangle(new URLSearchParams(read.search).get("symbol")) }) };
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"], timeframes: ["M"] }, fakeReads }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "M" });
    await page.waitForFunction(() => /Triangle/.test(document.querySelector("section.oi-finder-chart-card[data-tape-length]")?.dataset.chartPattern || ""),
      null, { timeout: 60_000 });
    assert.equal(await patternLines(page), 3);
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
