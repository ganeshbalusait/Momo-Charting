import assert from "node:assert/strict";
import test from "node:test";
import {
  createLiveChartQuotePoller,
  createLiveMarketStreamHub,
  liveEquityPrice,
  mergeLiveOptionRows,
} from "./liveMarketStream.js";

class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.closed = false;
    this.onerror = null;
  }

  addEventListener(type, listener) {
    this.listeners.set(type, listener);
  }

  emit(type, packet) {
    this.listeners.get(type)?.({ data: JSON.stringify(packet) });
  }

  close() {
    this.closed = true;
  }
}

function fakeScheduler() {
  let nextId = 0;
  const tasks = new Map();
  return {
    schedule(callback) {
      nextId += 1;
      tasks.set(nextId, callback);
      return nextId;
    },
    cancel(handle) {
      tasks.delete(handle);
    },
    flush() {
      const pending = [...tasks.values()];
      tasks.clear();
      pending.forEach((callback) => callback());
    },
    async flushAsync() {
      const pending = [...tasks.values()];
      tasks.clear();
      await Promise.all(pending.map((callback) => callback()));
    },
  };
}

test("batches visible charts into one one-second quote fallback", async () => {
  const scheduler = fakeScheduler();
  const requests = [];
  const packets = [];
  const poller = createLiveChartQuotePoller({
    requestJson: async (url) => {
      requests.push(url);
      return {
        rows: [
          { symbol: "AAPL", lastPrice: 205.25 },
          { symbol: "MSFT", lastPrice: 410.5 },
        ],
      };
    },
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
    now: () => 1_786_375_440_000,
    intervalMs: 1_000,
  });
  const unsubscribeAapl = poller.subscribe("AAPL", (packet) => packets.push(packet));
  const unsubscribeMsft = poller.subscribe("MSFT", (packet) => packets.push(packet));

  await scheduler.flushAsync();

  assert.deepEqual(requests, ["/api/live-chart-quotes?symbols=AAPL%2CMSFT"]);
  assert.deepEqual(packets.map((packet) => [
    packet.symbol,
    packet.data.last,
    packet.data.source,
    packet.data.tradeTime,
  ]), [
    ["AAPL", 205.25, "schwab-rest-1s", 1_786_375_440_000],
    ["MSFT", 410.5, "schwab-rest-1s", 1_786_375_440_000],
  ]);

  unsubscribeAapl();
  unsubscribeMsft();
});

test("quote fallback keeps a one-second start-to-start cadence", async () => {
  let clock = 10_000;
  const scheduled = [];
  const callbacks = [];
  const poller = createLiveChartQuotePoller({
    requestJson: async () => {
      clock += 350;
      return { rows: [{ symbol: "UBER", lastPrice: 77.65 }] };
    },
    schedule: (callback, delay) => {
      callbacks.push(callback);
      scheduled.push(delay);
      return callbacks.length;
    },
    cancel: () => {},
    now: () => clock,
    intervalMs: 1_000,
  });
  const unsubscribe = poller.subscribe("UBER", () => {});
  assert.equal(scheduled[0], 0);
  await callbacks.shift()();
  assert.equal(scheduled[1], 650);
  unsubscribe();
});

test("multiplexes every active ticker through one EventSource", () => {
  const scheduler = fakeScheduler();
  const sources = [];
  const hub = createLiveMarketStreamHub({
    createEventSource: (url) => {
      const source = new FakeEventSource(url);
      sources.push(source);
      return source;
    },
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
  });
  const received = [];
  const unsubscribeAaplOne = hub.subscribe("AAPL", {
    equity: (packet) => received.push(`aapl-one:${packet.data.last}`),
  });
  const unsubscribeAaplTwo = hub.subscribe("aapl", {
    equity: (packet) => received.push(`aapl-two:${packet.data.last}`),
  });
  const unsubscribeMsft = hub.subscribe("MSFT", {
    option: (packet) => received.push(`msft-option:${packet.symbol}`),
  });

  scheduler.flush();
  assert.equal(sources.length, 1);
  assert.equal(sources[0].url, "/api/live-market-stream?symbols=AAPL%2CMSFT");

  sources[0].emit("equity", { symbol: "AAPL", data: { last: 205 } });
  sources[0].emit("option", { symbol: "MSFT  260101C00400000", underlying: "MSFT", data: {} });
  assert.deepEqual(received, [
    "aapl-one:205",
    "aapl-two:205",
    "msft-option:MSFT  260101C00400000",
  ]);

  unsubscribeAaplOne();
  unsubscribeAaplTwo();
  scheduler.flush();
  assert.equal(sources[0].closed, true);
  assert.equal(sources[1].url, "/api/live-market-stream?symbols=MSFT");
  unsubscribeMsft();
  scheduler.flush();
  assert.equal(sources[1].closed, true);
  assert.deepEqual(hub.activeSymbols(), []);
});

test("does not replace option rows when a repeated packet changes no quote field", () => {
  const rows = [{
    symbol: "AAPL  260101C00200000",
    bid: 2,
    ask: 2.2,
    last: 2.1,
    mark: 2.1,
    volume: 40,
    open_interest: 100,
    volume_oi_ratio: 0.4,
    delta: 0.5,
    gamma: 0.1,
    theta: -0.05,
    vega: 0.2,
  }];
  const packet = {
    symbol: rows[0].symbol,
    receivedAt: "2026-08-02T12:00:00Z",
    data: {
      bid: 2,
      ask: 2.2,
      last: 2.1,
      mark: 2.1,
      totalVolume: 40,
      openInterest: 100,
      delta: 0.5,
      gamma: 0.1,
      theta: -0.05,
      vega: 0.2,
    },
  };

  assert.equal(mergeLiveOptionRows(rows, packet), rows);
  const changed = mergeLiveOptionRows(rows, { ...packet, data: { ...packet.data, bid: 2.05 } });
  assert.notEqual(changed, rows);
  assert.equal(changed[0].bid, 2.05);
  assert.equal(changed[0].liveQuoteAt, packet.receivedAt);
});

test("shards more symbols than one server stream accepts", () => {
  const scheduler = fakeScheduler();
  const sources = [];
  const hub = createLiveMarketStreamHub({
    createEventSource: (url) => {
      const source = new FakeEventSource(url);
      sources.push(source);
      return source;
    },
    schedule: scheduler.schedule,
    cancel: scheduler.cancel,
    maxSymbolsPerSource: 2,
  });
  const unsubscribes = ["AAPL", "MSFT", "NVDA"].map((symbol) => hub.subscribe(symbol, {}));
  scheduler.flush();
  assert.deepEqual(sources.map((source) => source.url), [
    "/api/live-market-stream?symbols=AAPL%2CMSFT",
    "/api/live-market-stream?symbols=NVDA",
  ]);
  unsubscribes.forEach((unsubscribe) => unsubscribe());
  scheduler.flush();
  assert.equal(sources.every((source) => source.closed), true);
});

test("uses the best available live equity mark", () => {
  assert.equal(liveEquityPrice({ data: { last: 205.25, mark: 205.2 } }), 205.25);
  assert.equal(liveEquityPrice({ data: { bid: 205.1, ask: 205.3 } }), 205.2);
});

test("watchdog rebuilds a silently dead stream and leaves a live one alone", () => {
  // A server restart can drop the socket without firing `error`; the tab then
  // sits subscribed-but-dead and the chart falls back to 30s REST polling.
  let clock = 0;
  const tasks = new Map();
  let nextHandle = 1;
  const opened = [];
  const closed = [];

  const hub = createLiveMarketStreamHub({
    createEventSource: (url) => {
      const source = { url, listeners: new Map(), close: () => closed.push(url) };
      source.addEventListener = (type, handler) => source.listeners.set(type, handler);
      opened.push(source);
      return source;
    },
    schedule: (callback, delay) => {
      const handle = nextHandle++;
      tasks.set(handle, { callback, at: clock + delay });
      return handle;
    },
    cancel: (handle) => tasks.delete(handle),
    silenceTimeoutMs: 30_000,
    now: () => clock,
  });

  hub.subscribe("SPY", { equity: () => {} });
  const runDue = () => {
    for (const [handle, task] of [...tasks]) {
      if (task.at <= clock) {
        tasks.delete(handle);
        task.callback();
      }
    }
  };
  clock += 200;                   // clear the 120ms initial connect debounce
  runDue();
  assert.equal(opened.length, 1);

  // Traffic keeps arriving: no rebuild.
  clock += 15_000;
  opened[0].listeners.get("equity")({ data: JSON.stringify({ symbol: "SPY", data: { last: 1 } }) });
  clock += 15_000;
  runDue();
  assert.equal(opened.length, 1, "a live stream must not be torn down");

  // Silence past the timeout: rebuild exactly once.
  clock += 31_000;
  runDue();
  assert.equal(opened.length, 2, "a silent stream must be rebuilt");
  assert.equal(closed.length, 1);

  // The rebuilt stream starts a fresh silence window.
  clock += 10_000;
  runDue();
  assert.equal(opened.length, 2, "a freshly rebuilt stream gets its full window");
});

test("watchdog stops when the last subscriber leaves", () => {
  let clock = 0;
  const tasks = new Map();
  let nextHandle = 1;
  const opened = [];
  const hub = createLiveMarketStreamHub({
    createEventSource: (url) => {
      const source = { url, addEventListener: () => {}, close: () => {} };
      opened.push(source);
      return source;
    },
    schedule: (callback, delay) => {
      const handle = nextHandle++;
      tasks.set(handle, { callback, at: clock + delay });
      return handle;
    },
    cancel: (handle) => tasks.delete(handle),
    silenceTimeoutMs: 30_000,
    now: () => clock,
  });
  const unsubscribe = hub.subscribe("SPY", { equity: () => {} });
  for (const [handle, task] of [...tasks]) { tasks.delete(handle); task.callback(); }
  assert.equal(opened.length, 1);
  unsubscribe();
  clock += 60_000;
  for (const [handle, task] of [...tasks]) { if (task.at <= clock) { tasks.delete(handle); task.callback(); } }
  assert.equal(opened.length, 1, "no reconnect once nobody is subscribed");
});
