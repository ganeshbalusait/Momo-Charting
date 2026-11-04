// Charts & OI: live candles (2026-11-04 coverage). The OHLC strip reads
// STREAMING while the Schwab SSE feed talks, and REST FALLBACK - with the
// 1-second quote poll still running - when it drops.
//
// The feed is faked IN THE PAGE: an EventSource stand-in (installed before the
// app boots) speaks the same "status" frames /api/live-market-stream sends and
// can be told to drop. A network route cannot hold an SSE response open, so
// it is used only for the hard-failure case (the stream GET answers 503).

import assert from "node:assert/strict";

import { e2eTest } from "./harness.mjs";
import { card, waitForTape, withCharts } from "./charts-core.helpers.mjs";

function installFakeEventSource() {
  const state = { sources: [], dropped: false, opened: [] };
  window.__agxSse = state;
  class FakeEventSource {
    constructor(url) {
      this.url = String(url);
      this.readyState = 0;
      this.listeners = new Map();
      this.onerror = null;
      this.onopen = null;
      this.onmessage = null;
      state.sources.push(this);
      state.opened.push(this.url);
      if (state.dropped) {
        setTimeout(() => this.fail(), 20);
        return;
      }
      setTimeout(() => {
        this.readyState = 1;
        this.onopen?.(new Event("open"));
        this.tick();
      }, 20);
      this.timer = setInterval(() => this.tick(), 1_000);
    }

    tick() {
      if (this.readyState !== 1) return;
      this.emit("status", { symbol: "", data: { connected: true, source: "schwab" }, receivedAt: new Date().toISOString() });
    }

    emit(type, packet) {
      const event = new MessageEvent(type, { data: JSON.stringify(packet) });
      (this.listeners.get(type) || []).forEach((listener) => listener(event));
    }

    fail() {
      clearInterval(this.timer);
      this.readyState = 2;
      this.onerror?.(new Event("error"));
    }

    addEventListener(type, listener) {
      const list = this.listeners.get(type) || [];
      list.push(listener);
      this.listeners.set(type, list);
    }

    removeEventListener(type, listener) {
      this.listeners.set(type, (this.listeners.get(type) || []).filter((item) => item !== listener));
    }

    close() {
      clearInterval(this.timer);
      this.readyState = 2;
    }
  }
  FakeEventSource.CONNECTING = 0;
  FakeEventSource.OPEN = 1;
  FakeEventSource.CLOSED = 2;
  state.drop = () => {
    state.dropped = true;
    state.sources.forEach((source) => source.fail());
  };
  window.EventSource = FakeEventSource;
}

const stripText = (page) => card(page).locator(".oi-finder-chart-ohlc small").first().innerText();

async function waitForStrip(page, pattern, timeout = 30_000) {
  await page.waitForFunction(({ selector, source }) => new RegExp(source).test(
    document.querySelector(`${selector} .oi-finder-chart-ohlc small`)?.textContent || "",
  ), { selector: "section.oi-finder-chart-card[data-tape-length]", source: pattern.source }, { timeout });
}

const quotePolls = (app) => app.reads.filter((read) => read.path === "/api/live-chart-quotes"
  && decodeURIComponent(read.search).includes("AAPL")).length;

e2eTest("a talking stream reads STREAMING; when it drops the strip says REST FALLBACK and quotes keep coming", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await page.addInitScript(installFakeEventSource);
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { symbol: "AAPL" });
    const opened = await page.evaluate(() => window.__agxSse.opened);
    assert.ok(opened.some((url) => decodeURIComponent(url).startsWith("/api/live-market-stream?symbols=") && decodeURIComponent(url).includes("AAPL")),
      `the chart never subscribed AAPL: ${JSON.stringify(opened)}`);
    await waitForStrip(page, /STREAMING/);
    assert.match(await stripText(page), /^(Live|Crosshair) .* ET · STREAMING/);

    const pollsBefore = quotePolls(app);
    await page.evaluate(() => window.__agxSse.drop());
    await waitForStrip(page, /REST FALLBACK/, 15_000);
    await page.waitForTimeout(4_000);
    assert.ok(quotePolls(app) > pollsBefore, "the 1s REST quote fallback stopped polling");
    assert.match(await stripText(page), /REST FALLBACK/);
    assert.match(await card(page).locator(".oi-finder-chart-ohlc").innerText(), /C\s*\$?[\d,]+\.\d{2}/, "the strip lost its price");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a stream endpoint that answers 503 leaves the chart on REST FALLBACK, not broken", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    const stream = /\/api\/live-market-stream\?/;
    await page.route(stream, (route) => route.fulfill({ status: 503, contentType: "application/json", body: "{\"error\":\"stream down (end-to-end test)\"}" }));
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { symbol: "AAPL" });
    await waitForStrip(page, /REST FALLBACK/);
    const pollsBefore = quotePolls(app);
    await page.waitForTimeout(4_000);
    assert.ok(quotePolls(app) > pollsBefore, "no REST quote polling while the stream is down");
    assert.equal(await page.locator("section.oi-chart-render-error").count(), 0);
    assert.deepEqual(app.pageErrors, []);
    await page.unroute(stream);
  });
}, { timeout: 240_000 });
