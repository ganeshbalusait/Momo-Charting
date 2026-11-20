// Ask (chatbot) fixes of 2026-11-19, from Ganesh's phone:
//  1. "Why AMD is down today" showed "Could not reach the app" while the app was up: one retry, then the real reason.
//  2. "See nvda leaps which strike is good?": tall red blocks over the mini chart (24 bearish events stacked on a
//     few candles) and a closed section whose summary only repeated its title. ("Open NVDA chart" twice is fixed
//     on the server: the daily one is now "Open NVDA daily chart", tests/test_momx_chat_report*.py.)
import assert from "node:assert/strict";
import test from "node:test";

import { CHAT_DROPPED, CHAT_OFFLINE, CHAT_SIGNED_OUT, chatAnswer, chatFailureReason, chatSend } from "./botChat.js";
import { sectionSummary } from "./chatReport.js";
import { placeMarkers } from "./miniEventChart.js";

const noSleep = { sleep: async () => {} };
const ok = { ok: true, status: 200, type: "basic" };

test("a question that gets a reply is sent once, whatever its status", async () => {
  const calls = [];
  const reply = await chatSend(async (url) => { calls.push(url); return { ok: false, status: 500 }; }, "/api/bot-chat", {}, noSleep);
  assert.equal(reply.status, 500);
  assert.deepEqual(calls, ["/api/bot-chat"]);
});

test("no reply the first time: it waits and asks once more by itself, and the trader sees the answer", async () => {
  const calls = [];
  const waits = [];
  const fetchImpl = async (url) => { calls.push(url); if (calls.length === 1) throw new TypeError("Load failed"); return ok; };
  const reply = await chatSend(fetchImpl, "/api/bot-chat", {}, { sleep: async (ms) => { waits.push(ms); } });
  assert.equal(reply, ok);
  assert.deepEqual(calls, ["/api/bot-chat", "/api/bot-chat"]);
  assert.equal(waits.length, 1);
});

test("no reply twice: the message names the cause (sign-in ran out, no connection, or the answer was dropped)", async () => {
  const failing = (probe) => async (url) => { if (url === "/api/auth/status") return probe(); throw new TypeError("Load failed"); };
  const message = (probe) => chatSend(failing(probe), "/api/bot-chat", {}, noSleep).then(() => "answered", (e) => e.message);
  assert.equal(await message(() => ({ type: "opaqueredirect", status: 0 })), CHAT_SIGNED_OUT);
  assert.equal(await message(() => ({ type: "basic", status: 401 })), CHAT_SIGNED_OUT);
  assert.equal(await message(() => { throw new TypeError("offline"); }), CHAT_OFFLINE);
  assert.equal(await message(() => ok), CHAT_DROPPED);
});

test("the sign-in check never follows the tunnel's login redirect and sends the cookie", async () => {
  let seen = null;
  await chatFailureReason(async (url, init) => { seen = { url, init }; return ok; });
  assert.equal(seen.url, "/api/auth/status");
  assert.equal(seen.init.redirect, "manual");
  assert.equal(seen.init.credentials, "include");
});

test("the failure reasons are plain sentences the trader can act on, and it is never retried more than once", async () => {
  let calls = 0;
  await chatSend(async (url) => { if (url === "/api/auth/status") return ok; calls += 1; throw new Error("x"); }, "/api/bot-chat", {}, noSleep).catch(() => {});
  assert.equal(calls, 2);
  for (const line of [CHAT_SIGNED_OUT, CHAT_OFFLINE, CHAT_DROPPED]) assert.match(line, /ask again\.$/i);
});

const day = (d, o, h, l, c) => ({ time: d, open: o, high: h, low: l, close: c, volume: 1000 });
const bars = [day("2026-11-17", 10, 12, 9, 11), day("2026-11-18", 11, 13, 10, 12), day("2026-11-19", 12, 14, 11, 13)];

test("many bearish events on one candle share ONE marker that names them (no tall red block)", () => {
  const events = ["MACD cross down", "9/20 cross down", "PUT5", "PUT15", "Squeeze fired down", "50 EMA lost"].map((name) => ({ time: "2026-11-19", kind: "bearish", name }));
  const m = placeMarkers(bars, events);
  assert.equal(m.length, 1);
  assert.equal(m[0].stack, 0);
  assert.equal(m[0].count, 6);
  assert.equal(m[0].name, "6 bearish events: MACD cross down; 9/20 cross down; PUT5; PUT15; and 2 more");
  assert.equal(m[0].when, "2026-11-19");
});

test("a candle never stacks deeper than its kinds: 24 bearish and 1 neutral are two markers", () => {
  const events = Array.from({ length: 24 }, (_, i) => ({ time: "2026-11-18", kind: "bearish", name: `Event ${i}` }));
  events.push({ time: "2026-11-18", kind: "neutral", name: "Inside day" }, { time: "2026-11-18", kind: "bullish", name: "MACD cross up" });
  const m = placeMarkers(bars, events);
  assert.deepEqual(m.map((x) => [x.kind, x.side, x.stack, x.count]), [["bearish", "above", 0, 24], ["bullish", "below", 0, 1], ["neutral", "above", 1, 1]]);
  assert.equal(Math.max(...m.map((x) => x.stack)), 1);
  assert.equal(m[1].name, "MACD cross up", "a single event keeps its own name");
});

test("the same event named twice on a candle is listed once", () => {
  const m = placeMarkers(bars, [{ time: "2026-11-17", kind: "bearish", name: "PUT5" }, { time: "2026-11-17", kind: "bearish", name: "PUT5" }]);
  assert.equal(m[0].name, "2 bearish events: PUT5");
});

test("a closed section's summary skips a first line that only repeats its title", () => {
  assert.equal(sectionSummary("What the app's rules show", ["What the app's rules show:", "Long term: 1 of 4 rule checks point up."]), "Long term: 1 of 4 rule checks point up.");
  assert.equal(sectionSummary("Earnings date", ["NVDA has no earnings date."]), "NVDA has no earnings date.");
  assert.equal(sectionSummary("Sentiment", []), "");
  const answer = chatAnswer({ intent: "outlook_long", ticker: "NVDA", lines: ["Long term on NVDA."], sections: [
    { key: "rules", title: "What the app's rules show", lines: ["What the app's rules show:", "Long term: 1 of 4 rule checks point up."] },
  ] }, true);
  assert.equal(answer.sections[0].summary, "Long term: 1 of 4 rule checks point up.");
});
