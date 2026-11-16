// Charts & OI -> extended-hours bad prints, end to end (2026-11-11). SPY's
// 16:55 ET minute opened at a bad $711.46 print while SPY traded at ~$760, and
// the chart drew a five-minute candle from $711 to $760. The fixture is the
// real SPY tape from that evening, cut right after the bad minute so it is the
// newest candle on screen; the OHLC strip shows that candle's prices.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { PHONE, e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors } from "./charts-studies-helpers.mjs";

const spy = JSON.parse(readFileSync(new URL("../fixtures/spy_2026_11_11_bad_prints.json", import.meta.url), "utf8")).bars;
const BAD_MINUTE = Date.UTC(2026, 10, 11, 21, 55) / 1000; // 16:55 ET
const bars = spy.filter((bar) => bar.time <= BAD_MINUTE);

function fiveMinute(rows) {
  const buckets = new Map();
  for (const bar of rows) {
    const key = bar.time - (bar.time % 300);
    const held = buckets.get(key);
    buckets.set(key, held
      ? { ...held, high: Math.max(held.high, bar.high), low: Math.min(held.low, bar.low), close: bar.close, volume: held.volume + bar.volume }
      : { ...bar, time: key });
  }
  return [...buckets.values()];
}

const chart = (read) => (/historyStatus=true/.test(read.search)
  ? { json: { symbol: "AAPL", historyReady: true, refreshing: false, hasBars: true, warming: false } }
  : { json: {
    symbol: "AAPL", source: "e2e fixture", bars, fineStudyBars: fiveMinute(bars), studyBars: [], dailyBars: [],
    historyLoading: false, refreshing: false, error: "", tapeStale: false, updatedAt: new Date().toISOString(),
  } });

const options = (extra = {}) => chartAppOptions({
  ...extra,
  fakeReads: {
    "GET /api/oi-finder-chart": chart,
    "GET /api/live-chart-quotes": { json: { rows: [] } },
    "GET /api/live-market-stream": { status: 503, json: { error: "e2e: no live stream for the fixture chart" } },
  },
});

async function strip(card) {
  const ohlc = card.locator(".oi-finder-chart-ohlc");
  await ohlc.waitFor({ state: "visible", timeout: 30_000 });
  const text = (await ohlc.innerText()).replace(/\s+/g, " ");
  const price = (label) => {
    const match = new RegExp("(?:^| )" + label + " [^0-9]*([0-9][0-9,]*[.]?[0-9]*)").exec(text);
    return match ? Number(match[1].replace(/,/g, "")) : Number.NaN;
  };
  return { text, open: price("O"), low: price("L"), close: price("C") };
}

// The daily seed (2026-11-13): the saved 11-11 daily row was built from the same unfiltered minutes and kept
// low 711.4601, so the D candle (and W/M, the week low, pivots) still reached $711 after the minute candles
// had dropped the print. The minute tape here starts the evening before, so 11-11 is a whole day on it.
const et = (day, hour, minute) => Date.UTC(2026, 10, day, hour + 5, minute) / 1000;
const wholeDay = [
  { time: et(10, 19, 0), open: 754.4, high: 754.6, low: 754.3, close: 754.5, volume: 1000 },
  { time: et(11, 10, 0), open: 755, high: 755.2, low: 753.98, close: 755.1, volume: 5000 },
  ...spy,
];
const seed = [
  { time: et(9, 0, 0), open: 739.93, high: 754.11, low: 734.1, close: 753.79, volume: 101189 },
  { time: et(10, 0, 0), open: 753.71, high: 755.31, low: 751.42, close: 754.42, volume: 66143 },
  { time: et(11, 0, 0), open: 754.5, high: 762.51, low: 711.4601, close: 756.81, volume: 28934859 },
];
const dailyChart = (read) => (/historyStatus=true/.test(read.search)
  ? { json: { symbol: "AAPL", historyReady: true, refreshing: false, hasBars: true, warming: false } }
  : { json: {
    symbol: "AAPL", source: "e2e fixture", bars: wholeDay, fineStudyBars: fiveMinute(wholeDay), studyBars: [], dailyBars: seed,
    historyLoading: false, refreshing: false, error: "", tapeStale: false, updatedAt: new Date().toISOString(),
  } });

for (const [name, extra] of [["desktop", {}], ["phone", { viewport: PHONE }]]) {
  e2eTest(`${name}: on D the 11-11 daily candle no longer reaches down to $711`, async () => {
    await withApp(chartAppOptions({ ...extra, fakeReads: { ...options().fakeReads, "GET /api/oi-finder-chart": dailyChart } }), async (app) => {
      let card = await chartCard(app.page, "AAPL");
      if (extra.viewport) {
        await card.locator('select.oi-finder-timeframe-select[aria-label="Chart timeframe"]').selectOption("D");
      } else {
        await card.locator('.oi-finder-timeframes[aria-label="Chart timeframe"] button', { hasText: /^D$/ }).click();
      }
      card = await chartCard(app.page, "AAPL", { timeframe: "D" });
      let shown = await strip(card);
      for (let tries = 0; tries < 30 && !(shown.close > 700 && / · D/.test(shown.text)); tries += 1) {
        await app.page.waitForTimeout(500);
        shown = await strip(card);
      }
      assert.match(shown.text, / · D/, `the strip is not on D: ${shown.text}`);
      assert.ok(shown.low > 750, `the 11-11 daily candle still reaches down to ${shown.low}: ${shown.text}`);
      noPageErrors(app);
    });
  });
}

for (const [name, extra] of [["desktop", {}], ["phone", { viewport: PHONE }]]) {
  e2eTest(`${name}: the bad $711.46 print does not draw a candle down to $711`, async () => {
    await withApp(options(extra), async (app) => {
      const card = await chartCard(app.page, "AAPL");
      let shown = await strip(card);
      // The strip fills in once the tape lands; wait for the fixture's close.
      for (let tries = 0; tries < 30 && !(shown.close > 700); tries += 1) {
        await app.page.waitForTimeout(500);
        shown = await strip(card);
      }
      assert.ok(shown.close > 759 && shown.close < 761, `strip did not show the fixture candle: ${shown.text}`);
      assert.ok(shown.low > 759, `the candle still reaches down to ${shown.low}: ${shown.text}`);
      assert.ok(shown.open > 759, `the candle still opens at ${shown.open}: ${shown.text}`);
      noPageErrors(app);
    });
  });
}
