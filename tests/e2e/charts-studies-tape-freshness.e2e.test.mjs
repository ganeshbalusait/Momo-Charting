// Charts & OI -> tape freshness notices, end to end (2026-11-04 coverage pass,
// feature charts-tape-freshness). When a chart opens on a tape that ends in a
// previous session, the OHLC strip must say "PREV SESSION · updating…" instead
// of silently drawing yesterday as today (premarket 2026-08-21). The notice is
// only shown 04:00-20:00 ET on weekdays (outside that window an old tape is the
// newest data that exists), so these specs skip themselves outside it.
import assert from "node:assert/strict";

import { etParts, etWallClockMs } from "../../frontend/src/marketSession.js";
import { e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors } from "./charts-studies-helpers.mjs";

const PREV = ".oi-finder-chart-ohlc small.is-prev-session";

function noticeWindowOpen(now = Date.now()) {
  const parts = etParts(now);
  return !["Sat", "Sun"].includes(parts.weekday) && parts.hour >= 4 && parts.hour < 20;
}

// 09:30-15:59 ET one-minute bars of the most recent weekday before today.
function previousSessionBars(now = Date.now()) {
  let cursor = now - 24 * 3600 * 1000;
  let parts = etParts(cursor);
  while (["Sat", "Sun"].includes(parts.weekday)) {
    cursor -= 24 * 3600 * 1000;
    parts = etParts(cursor);
  }
  const open = etWallClockMs(parts.year, parts.month, parts.day, 9, 30) / 1000;
  let price = 200;
  return Array.from({ length: 390 }, (_, index) => {
    const open_ = price;
    price = Math.round((price + Math.sin(index / 7) * 0.08) * 100) / 100;
    return { time: open + index * 60, open: open_, high: Math.max(open_, price) + 0.05, low: Math.min(open_, price) - 0.05, close: price, volume: 10_000 + index };
  });
}

function fiveMinute(bars) {
  const buckets = new Map();
  for (const bar of bars) {
    const key = bar.time - (bar.time % 300);
    const held = buckets.get(key);
    buckets.set(key, held
      ? { ...held, high: Math.max(held.high, bar.high), low: Math.min(held.low, bar.low), close: bar.close, volume: held.volume + bar.volume }
      : { ...bar, time: key });
  }
  return [...buckets.values()];
}

function yesterdayChart(bars) {
  const payload = {
    symbol: "AAPL", source: "e2e fixture", bars, fineStudyBars: fiveMinute(bars), studyBars: [], dailyBars: [],
    historyLoading: false, refreshing: false, error: "", tapeStale: false, updatedAt: new Date().toISOString(),
  };
  return (read) => (/historyStatus=true/.test(read.search)
    ? { json: { symbol: "AAPL", historyReady: true, refreshing: false, hasBars: true, warming: false } }
    : { json: payload });
}

e2eTest("a chart that opens on yesterday's tape says PREV SESSION · updating…", async (t) => {
  if (!noticeWindowOpen()) return t.skip("the notice only shows 04:00-20:00 ET on weekdays");
  const bars = previousSessionBars();
  await withApp(chartAppOptions({
    fakeReads: {
      "GET /api/oi-finder-chart": yesterdayChart(bars),
      // No live candle may land on top of the fixture and make it "today".
      "GET /api/live-chart-quotes": { json: { rows: [] } },
      "GET /api/live-market-stream": { status: 503, json: { error: "e2e: no live stream for the fixture chart" } },
    },
  }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const notice = card.locator(PREV);
    await notice.waitFor({ state: "visible", timeout: 30_000 });
    assert.match((await notice.innerText()).trim(), /PREV SESSION · updating…/);
    assert.match(await notice.getAttribute("title"), /previous session/);
    assert.equal(Number(await card.getAttribute("data-tape-last")), bars.at(-1).time - (bars.at(-1).time % 300),
      "the card should be drawing the fixture's last 5m bucket");
    noPageErrors(app);
  });
});

e2eTest("a live tape that reaches today shows no PREV SESSION notice", async (t) => {
  if (!noticeWindowOpen()) return t.skip("the notice only shows 04:00-20:00 ET on weekdays");
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    // Give the background refresh time to splice today's bars if the cache was behind.
    await app.page.waitForFunction(() => !document.querySelector('section.oi-finder-chart-card[data-chart-symbol="AAPL"] .oi-finder-chart-ohlc small.is-prev-session'), null, { timeout: 60_000 });
    const last = Number(await card.getAttribute("data-tape-last"));
    const parts = etParts(last * 1000);
    const today = etParts(Date.now());
    assert.deepEqual([parts.year, parts.month, parts.day], [today.year, today.month, today.day], "AAPL's newest candle is not from today");
    noPageErrors(app);
  });
});
