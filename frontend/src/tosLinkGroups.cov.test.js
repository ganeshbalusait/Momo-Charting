// Coverage addition (2026-11-04) for tosLinkGroups.js: WHERE a scanner
// ticker click goes. In the main app it is a window event that App.jsx
// handles; from the detached scanner window (?popout=momx) it must travel
// over the BroadcastChannel to the main window instead - and never be
// dispatched in the pop-out itself, which would loop.
import test from "node:test";
import assert from "node:assert/strict";

import { CHART_LINK_CHANNEL, CHART_LINK_EVENT, sendTickerToCharts, warmTickerForCharts } from "./tosLinkGroups.js";

function fakeWindow(search) {
  const target = new EventTarget();
  const seen = [];
  target.addEventListener(CHART_LINK_EVENT, (event) => seen.push(event.detail));
  target.location = { search };
  return { target, seen };
}

async function inWindow(search, fn) {
  const previous = globalThis.window;
  const fake = fakeWindow(search);
  globalThis.window = fake.target;
  try {
    return { ...(await fn()), local: fake.seen };
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
}

function listen() {
  const channel = new BroadcastChannel(CHART_LINK_CHANNEL);
  const heard = [];
  channel.onmessage = (event) => heard.push(event.data);
  return { channel, heard };
}

const settle = () => new Promise((resolve) => setTimeout(resolve, 50));

test("in the main app a click is a window event for App.jsx, not a broadcast", async () => {
  const { channel, heard } = listen();
  try {
    const { sent, local } = await inWindow("?view=momx", async () => {
      const sent = sendTickerToCharts("nvda", 2);
      await settle();
      return { sent };
    });
    assert.deepEqual([sent.symbol, sent.linkGroup, sent.intent], ["NVDA", 2, undefined]);
    assert.deepEqual(local.map((m) => m.id), [sent.id]);
    assert.deepEqual(heard, [], "a second app window must not jump");
  } finally {
    channel.close();
  }
});

test("from the detached scanner window the click is broadcast to the main window only", async () => {
  const { channel, heard } = listen();
  try {
    const { sent, warm, local } = await inWindow("?popout=momx&list=Mag7", async () => {
      const sent = sendTickerToCharts("AAPL", 1);
      const warm = warmTickerForCharts("SPY", 1);
      await settle();
      return { sent, warm };
    });
    assert.deepEqual(local, [], "the pop-out never handles its own click");
    assert.deepEqual(heard.map((m) => [m.symbol, m.linkGroup, m.intent]), [["AAPL", 1, undefined], ["SPY", 1, "warm"]]);
    assert.equal(heard[0].id, sent.id);
    assert.equal(warm.intent, "warm");
  } finally {
    channel.close();
  }
});

test("a junk ticker or colour sends nothing anywhere", async () => {
  const { channel, heard } = listen();
  try {
    const { results, local } = await inWindow("?popout=momx", async () => {
      const results = [sendTickerToCharts("", 1), sendTickerToCharts("AAPL", 0), warmTickerForCharts("SPY", 12)];
      await settle();
      return { results };
    });
    assert.deepEqual(results, [null, null, null]);
    assert.deepEqual(local, []);
    assert.deepEqual(heard, []);
  } finally {
    channel.close();
  }
});

test("without a window (server render) a click is a quiet no-op", () => {
  const previous = globalThis.window;
  delete globalThis.window;
  try {
    assert.equal(sendTickerToCharts("AAPL", 1), null);
  } finally {
    if (previous !== undefined) globalThis.window = previous;
  }
});
