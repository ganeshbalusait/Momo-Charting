// Coverage (2026-11-04): the browser side of /api/live-market-stream.
//
// WIRE is byte-for-byte what api_server writes for one packet
// ("id: N\nevent: <type>\ndata: <json.dumps(packet)>\n\n", plus ": keepalive"
// comments) - the same literal the Python side pins in
// tests/test_cov_charts-core_live_stream_api.py. A spec-faithful EventSource
// parser turns it into events, and the hub must route each one to the chart
// that asked for that ticker.
import test from "node:test";
import assert from "node:assert/strict";

import { createLiveMarketStreamHub, liveEquityPrice } from "./liveMarketStream.js";

const WIRE = [
  ": keepalive\n\n",
  'id: 41\nevent: equity\ndata: {"symbol": "AAPL", "data": {"symbol": "AAPL", "last": 230.25, "chartTime": 1793800800, "volume": 1200}, "receivedAt": "2026-11-04T15:00:01Z"}\n\n',
  'id: 42\nevent: chart\ndata: {"symbol": "SPY", "data": {"time": 1793800800, "open": 580, "high": 581, "low": 579.5, "close": 580.5, "volume": 900}, "receivedAt": "2026-11-04T15:00:02Z"}\n\n',
  'id: 43\nevent: option\ndata: {"symbol": "AAPL  261120C00230000", "data": {"bid": 2.1, "ask": 2.2}, "receivedAt": "2026-11-04T15:00:03Z", "underlying": "AAPL"}\n\n',
  'id: 44\nevent: status\ndata: {"symbol": "", "data": {"connected": true, "source": "schwab"}, "receivedAt": "2026-11-04T15:00:04Z"}\n\n',
].join("");

// Minimal EventSource per the HTML spec's event-stream interpretation.
class WireEventSource {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    this.closed = false;
    this.lastEventId = "";
  }

  addEventListener(type, listener) {
    const list = this.listeners.get(type) || [];
    list.push(listener);
    this.listeners.set(type, list);
  }

  close() { this.closed = true; }

  feed(text) {
    for (const block of text.split("\n\n")) {
      let type = "message";
      const data = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon < 0 ? line : line.slice(0, colon);
        const value = colon < 0 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") type = value;
        else if (field === "data") data.push(value);
        else if (field === "id") this.lastEventId = value;
      }
      if (!data.length) continue;
      const event = { type, data: data.join("\n"), lastEventId: this.lastEventId };
      (this.listeners.get(type) || []).forEach((listener) => listener(event));
    }
  }
}

function hubWithWire() {
  const sources = [];
  const tasks = [];
  const hub = createLiveMarketStreamHub({
    createEventSource: (url) => { const source = new WireEventSource(url); sources.push(source); return source; },
    schedule: (callback) => { tasks.push(callback); return tasks.length; },
    cancel: () => {},
    now: () => 0,
  });
  // Run only what is queued now: the silence watchdog re-arms itself, so
  // draining until empty would never end.
  const flush = () => { tasks.splice(0).forEach((callback) => callback()); };
  return { hub, sources, flush };
}

test("each wire frame reaches only the chart of its ticker, by event type", () => {
  const { hub, sources, flush } = hubWithWire();
  const aapl = { equity: [], chart: [], option: [], status: [] };
  const spy = { equity: [], chart: [], option: [], status: [] };
  const record = (bucket) => Object.fromEntries(Object.keys(bucket).map((type) => [type, (packet) => bucket[type].push(packet)]));
  hub.subscribe("aapl", record(aapl));
  hub.subscribe("SPY", record(spy));
  flush();
  assert.equal(sources.length, 1, "one connection for the two charts");
  assert.equal(decodeURIComponent(sources[0].url), "/api/live-market-stream?symbols=AAPL,SPY");

  sources[0].feed(WIRE);

  assert.equal(aapl.equity.length, 1);
  assert.equal(liveEquityPrice(aapl.equity[0]), 230.25);
  assert.equal(aapl.equity[0].receivedAt, "2026-11-04T15:00:01Z");
  assert.deepEqual(spy.equity, []);
  assert.equal(spy.chart.length, 1);
  assert.equal(spy.chart[0].data.close, 580.5);
  assert.deepEqual(aapl.chart, []);
  // Option ticks route by their underlying, not the OCC contract symbol.
  assert.equal(aapl.option.length, 1);
  assert.deepEqual(spy.option, []);
  // A status frame is for every chart on that connection.
  assert.equal(aapl.status.length, 1);
  assert.equal(spy.status.length, 1);
  assert.equal(sources[0].lastEventId, "44", "Last-Event-ID the browser resends on reconnect");
});

test("keepalive comments and garbage frames never reach a chart", () => {
  const { hub, sources, flush } = hubWithWire();
  const got = [];
  hub.subscribe("SPY", { equity: (packet) => got.push(packet), chart: (packet) => got.push(packet) });
  flush();
  sources[0].feed(": keepalive\n\n: keepalive\n\nid: 9\nevent: equity\ndata: {not json\n\n");
  assert.deepEqual(got, []);
});

test("the last chart to close a ticker closes the connection", () => {
  const { hub, sources, flush } = hubWithWire();
  const off = hub.subscribe("SPY", { equity: () => {} });
  flush();
  assert.equal(sources[0].closed, false);
  off();
  flush();
  assert.equal(sources[0].closed, true);
  assert.deepEqual(hub.activeSymbols(), []);
});
