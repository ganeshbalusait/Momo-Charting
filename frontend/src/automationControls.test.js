// The two controls that were stranded on the unreachable "Dashboard" view
// (2026-11-14): "Start / Pause Stock Bot" and "Scan MAG7 OI". One description
// each, used by the Dashboard strip AND by the page the trader can open
// (Stock Paper Trading, Mag7 Watchlist). Plus the menu order he ruled on
// 2026-11-08: Long Term directly under Bot, My Trading right below it.
// Run: node --test src/automationControls.test.js
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { MAG7_OI_PENDING, STOCK_BOT_PENDING, mag7OiScanControl, stockBotControl } from "./automationControls.js";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const POST = { method: "POST", headers: { "Content-Type": "application/json" } };

test("stockBotControl: a stopped or paused PAPER stock bot offers Start and asks for Running", () => {
  for (const state of ["Stopped", "Paused", "", undefined, null]) {
    const control = stockBotControl(state);
    assert.equal(control.running, false);
    assert.equal(control.label, "Start Stock Bot");
    assert.deepEqual(control.action, ["/api/bot-control", { ...POST, body: JSON.stringify({ state: "Running" }) }, STOCK_BOT_PENDING]);
  }
});

test("stockBotControl: a running bot offers Pause and asks for Paused, never Stopped", () => {
  const control = stockBotControl("Running");
  assert.equal(control.running, true);
  assert.equal(control.label, "Pause Stock Bot");
  assert.deepEqual(control.action, ["/api/bot-control", { ...POST, body: JSON.stringify({ state: "Paused" }) }, STOCK_BOT_PENDING]);
});

test("stockBotControl only ever names the paper stock bot route", () => {
  for (const state of ["Running", "Paused", "Stopped"]) {
    assert.equal(stockBotControl(state).action[0], "/api/bot-control");
  }
});

test("mag7OiScanControl: one MAG7 scan request, the label follows the pending key", () => {
  const idle = mag7OiScanControl("", { symbols: ["META", "AMD", "AAPL"] });
  assert.equal(idle.scanning, false);
  assert.equal(idle.label, "Scan MAG7 OI");
  assert.equal(idle.note, "3 symbols · Schwab/TOS");
  assert.deepEqual(idle.action, ["/api/oi-scan", { ...POST, body: JSON.stringify({ universe: "mag7" }) }, MAG7_OI_PENDING]);
  const busy = mag7OiScanControl(MAG7_OI_PENDING, { symbols: ["META"] });
  assert.equal(busy.scanning, true);
  assert.equal(busy.label, "Scanning MAG7...");
  // another command in flight is not this scan
  assert.equal(mag7OiScanControl(STOCK_BOT_PENDING, null).scanning, false);
});

test("mag7OiScanControl: with no symbol list yet it says 7, as the Dashboard did", () => {
  assert.equal(mag7OiScanControl("", undefined).note, "7 symbols · Schwab/TOS");
  assert.equal(mag7OiScanControl("", { symbols: [] }).note, "7 symbols · Schwab/TOS");
});

function navLabels() {
  const start = appSource.indexOf("const optionNavItems = [");
  assert.ok(start > 0, "optionNavItems in App.jsx");
  const block = appSource.slice(start, appSource.indexOf("\n];", start));
  return [...block.matchAll(/^\s*\{ label: "([^"]+)"/gm)].map((match) => match[1]);
}

test("menu order: Long Term directly under Bot, My Trading right below Long Term", () => {
  const labels = navLabels();
  const bot = labels.indexOf("Bot");
  assert.ok(bot >= 0, `Bot in the menu: ${JSON.stringify(labels)}`);
  assert.deepEqual(labels.slice(bot, bot + 3), ["Bot", "Long Term", "My Trading"]);
});

function viewBlock(view) {
  const start = appSource.indexOf(`{activeView === "${view}" &&`);
  assert.ok(start > 0, `${view} view in App.jsx`);
  const next = appSource.indexOf("{activeView === \"", start + 20);
  return appSource.slice(start, next > 0 ? next : undefined);
}

test("the Mag7 Watchlist page carries the Scan MAG7 OI control, wired to the shared description", () => {
  const block = viewBlock("Mag7 Scanner");
  assert.match(block, /data-testid="mag7-oi-scan"/);
  assert.match(block, /runAction\(\.\.\.mag7OiScan\.action\)/);
  assert.match(block, /\{mag7OiScan\.label\}/);
  assert.match(block, /className="dashboard-control-progress" role="status"/);
});

test("the Stock Paper Trading banner carries Start / Pause Stock Bot, wired to the shared description", () => {
  const block = viewBlock("Paper Trading");
  assert.match(block, /authUser\?\.isAdmin/, "the page stays owner only");
  assert.match(block, /runAction\(\.\.\.stockBot\.action\)/);
  assert.match(block, /\{stockBot\.label\}/);
});

test("the request bodies are written once: the Dashboard strip uses the same two descriptions", () => {
  const block = viewBlock("Dashboard");
  assert.match(block, /runAction\(\.\.\.stockBot\.action\)/);
  assert.match(block, /runAction\(\.\.\.mag7OiScan\.action\)/);
  assert.equal(appSource.split('"/api/oi-scan"').length - 1, 0, "no second copy of the scan request in App.jsx");
  assert.equal(appSource.split('"/api/bot-control"').length - 1, 0, "no second copy of the stock bot request in App.jsx");
});
