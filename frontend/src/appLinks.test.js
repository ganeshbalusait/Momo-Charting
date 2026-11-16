import assert from "node:assert/strict";
import test from "node:test";

import { ADMIN_VIEWS, LINK_VIEWS, OPEN_LINK_EVENT, cleanLink, cleanLinks, linkAllowed, openAppLink, peekPendingScannerLink, subscribePendingScannerLink, takePendingScannerLink } from "./appLinks.js";

const scanner = { label: "Open NVDA on Mag7 History, 09:30", view: "MomX Scanner", list: "Mag7", side: "bull", tab: "history", day: "2026-11-10", ticker: "NVDA", time: "09:30" };

test("a full link comes through unchanged, and unknown fields are dropped", () => {
  assert.deepEqual(cleanLink(scanner), scanner);
  assert.deepEqual(cleanLink({ ...scanner, href: "https://evil.example", onClick: "x", script: "<script>" }), scanner);
  assert.deepEqual(cleanLink({ label: "Chart", view: "Charts & OI", ticker: " nvda ", timeframe: "1h" }), { label: "Chart", view: "Charts & OI", ticker: "NVDA", timeframe: "1h" });
  assert.deepEqual(cleanLink({ label: "High OI", view: "Quick Options", ticker: "SPY", sub: "high-oi" }), { label: "High OI", view: "Quick Options", ticker: "SPY", sub: "high-oi" });
  assert.deepEqual(cleanLink({ view: "Sectors" }), { label: "Sectors", view: "Sectors" });
  assert.deepEqual(cleanLink({ label: "Brk", view: "Long Term", ticker: "BRK.B", time: null, day: "" }), { label: "Brk", view: "Long Term", ticker: "BRK.B" });
});

test("anything off the allow-list makes the whole link invalid", () => {
  for (const bad of [null, undefined, "MomX Scanner", 5, [], {}, { view: "Dashboard" }, { view: "Pre Market Mag7" }, { view: "Option Watchlist" }, { view: "constructor" }, { label: 5, view: "Sectors" }, { label: " \n ", view: "Sectors" }]) {
    assert.equal(cleanLink(bad), null, JSON.stringify(bad));
  }
  const base = { label: "x", view: "MomX Scanner" };
  for (const bad of [{ ticker: "NVDA; DROP" }, { ticker: "<b>" }, { ticker: "TOOLONGTICKER12" }, { ticker: 5 }, { ticker: "1ABC" },
    { timeframe: "7m" }, { timeframe: "javascript:1" }, { list: "<img>" }, { list: "x".repeat(60) }, { list: 3 },
    { side: "up" }, { tab: "settings" }, { day: "2026-13-01" }, { day: "2026-02-30" }, { day: "11/10/2026" }, { time: "24:00" }, { time: "9:30" }, { time: "09:60" },
    { sub: "../etc" }, { sub: "a b" }]) {
    assert.equal(cleanLink({ ...base, ...bad }), null, JSON.stringify(bad));
  }
});

test("every timeframe the chart offers may be linked", () => {
  for (const timeframe of ["3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "D", "W", "M"]) assert.equal(cleanLink({ view: "Charts & OI", timeframe }).timeframe, timeframe);
});

test("the label is one plain line of at most 80 characters", () => {
  assert.equal(cleanLink({ label: " a\n\tb\u0007c ", view: "Sectors" }).label, "a b c");
  assert.equal(cleanLink({ label: "y".repeat(200), view: "Sectors" }).label.length, 80);
});

test("a regular user never gets an owner page; the owner gets all of them", () => {
  assert.deepEqual(ADMIN_VIEWS, ["Bot", "My Trading", "Option Paper Trading", "Paper Trading"]);
  assert.equal(linkAllowed({ view: "My Trading" }, false), false, "the owner's real trades never open for a regular user");
  for (const view of LINK_VIEWS) {
    assert.equal(linkAllowed({ view }, true), true);
    assert.equal(linkAllowed({ view }, false), !ADMIN_VIEWS.includes(view));
  }
  assert.equal(linkAllowed(null, true), false);
  const raw = [{ label: "Bot", view: "Bot" }, { label: "Paper", view: "Paper Trading" }, { label: "Option paper", view: "Option Paper Trading" }, { label: "News", view: "News Feed", ticker: "NVDA" }];
  assert.deepEqual(cleanLinks(raw, false), [{ label: "News", view: "News Feed", ticker: "NVDA" }]);
  assert.equal(cleanLinks(raw, true).length, 4);
});

test("a reply's links: broken ones dropped, repeats dropped, at most six", () => {
  assert.deepEqual(cleanLinks("nope"), []);
  assert.deepEqual(cleanLinks(null), []);
  assert.deepEqual(cleanLinks([{ view: "Nope" }, 7, null]), []);
  assert.equal(cleanLinks([{ label: "a", view: "Sectors" }, { label: "b", view: "Sectors" }]).length, 1);
  const many = Array.from({ length: 9 }, (_, i) => ({ label: `L${i}`, view: "Charts & OI", ticker: `T${"ABCDEFGHI"[i]}` }));
  assert.equal(cleanLinks(many).length, 6);
});

test("openAppLink tells the app, and a scanner link waits to be taken exactly once", () => {
  const seen = [];
  globalThis.window = { dispatchEvent: (event) => { seen.push(event); return true; } };
  try {
    let told = 0;
    const stop = subscribePendingScannerLink(() => { told += 1; });
    assert.equal(openAppLink({ view: "Not a page" }), null);
    assert.equal(seen.length, 0, "an invalid link must do nothing");
    assert.equal(peekPendingScannerLink(), null);

    assert.deepEqual(openAppLink({ label: "Chart", view: "Charts & OI", ticker: "SPY" }), { label: "Chart", view: "Charts & OI", ticker: "SPY" });
    assert.equal(seen.at(-1).type, OPEN_LINK_EVENT);
    assert.equal(OPEN_LINK_EVENT, "agx:open-link");
    assert.equal(seen.at(-1).detail.ticker, "SPY");
    assert.equal(peekPendingScannerLink(), null, "only scanner links wait");
    assert.equal(told, 0);

    openAppLink(scanner);
    assert.equal(told, 1);
    assert.deepEqual(peekPendingScannerLink(), scanner);
    assert.deepEqual(takePendingScannerLink(), scanner);
    assert.equal(told, 2);
    assert.equal(takePendingScannerLink(), null);
    assert.equal(told, 2, "nothing to take, nothing to tell");
    stop();
    openAppLink(scanner);
    assert.equal(told, 2);
    takePendingScannerLink();
  } finally {
    delete globalThis.window;
  }
});

test("without a window (a pop-out being torn down, a test) a link still does not throw", () => {
  assert.equal(openAppLink({ label: "x", view: "Sectors" }).view, "Sectors");
});
