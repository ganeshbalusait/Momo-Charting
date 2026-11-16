// The chat's "whole day" sheet: what it asks the server, what it says, and how
// many reads it runs at once. The sheet's plain logic is lifted out of
// ChatScannerDay.jsx, the way other tests here read App.jsx (needs LF endings).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { historyRequest } from "./momxHistory.js";

const source = readFileSync(new URL("./ChatScannerDay.jsx", import.meta.url), "utf8");
const panel = readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
const app = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const block = source.match(/\/\/ <day-sheet-logic>[^\n]*\n([\s\S]*?)\/\/ <\/day-sheet-logic>/);
assert.ok(block, "the sheet's logic block is missing");
const { daySections, sectionName, sectionUrl, sectionCounts, notOnBoard, sectionIsEmpty, nextOffset, addPage, createLimiter, momentLink, FETCHES_AT_ONCE } = new Function(
  `${block[1]}; return { daySections, sectionName, sectionUrl, sectionCounts, notOnBoard, sectionIsEmpty, nextOffset, addPage, createLimiter, momentLink, FETCHES_AT_ONCE };`,
)();

const table = {
  kind: "scanner_day", title: "NVDA on the scanner", ticker: "NVDA", day: "2026-11-10",
  sections: [{ list: "Mag7", side: "bull" }, { list: "Watchlist", side: "bull" }, { list: "Mag7", side: "bear" }, { list: "Watchlist", side: "bear" }],
};

test("the boards keep the server's order, without repeats or junk", () => {
  assert.deepEqual(daySections(table).map(sectionName), ["Mag7 calls", "Watchlist calls", "Mag7 puts", "Watchlist puts"]);
  const putsFirst = { sections: [{ list: "Mag7", side: "bear" }, { list: "Mag7", side: "bull" }] };
  assert.deepEqual(daySections(putsFirst).map(sectionName), ["Mag7 puts", "Mag7 calls"]);
  assert.deepEqual(daySections({ sections: [null, { list: "" }, { list: 5 }, { list: "Mag7" }, { list: "Mag7", side: "bull" }] }).map((s) => s.id), ["bull|Mag7"]);
  assert.deepEqual(daySections(null), []);
  assert.equal(daySections({ sections: Array.from({ length: 20 }, (_, i) => ({ list: `L${i}` })) }).length, 8);
});

test("puts ask the bear archive with dir=bear, calls do not, and the day and ticker are in the request", () => {
  const [calls, , puts] = daySections(table);
  const params = (section, offset) => new URLSearchParams(sectionUrl(historyRequest, table, section, offset).split("?")[1]);
  assert.equal(params(puts).get("dir"), "bear");
  assert.equal(params(puts).get("direction"), null, "direction= is ignored by the route and returns the calls");
  assert.equal(params(calls).get("dir"), null);
  assert.equal(params(calls).get("symbol"), "NVDA");
  assert.equal(params(calls).get("date"), "2026-11-10");
  assert.equal(params(calls).get("list"), "Mag7");
  assert.equal(params(calls, 400).get("offset"), "400");
  assert.equal(params(calls, 0).get("offset"), null);
});

test("the header says scans and saved rows, and never passes saved rows off as every scan", () => {
  const rows = [{ hits: 172 }, { hits: 172 }];
  assert.equal(sectionCounts({ rows, totalRows: 94 }), "172 scans, 94 saved rows");
  assert.equal(sectionCounts({ rows: [{ hits: 1 }], totalRows: 1 }), "1 scan, 1 saved row");
  assert.equal(sectionCounts({ rows: [{}, {}] }), "2 saved rows", "with no scan count it claims none");
});

test("a board he was not on folds to one line", () => {
  assert.equal(notOnBoard({ list: "Watchlist", side: "bear" }), "Not on Watchlist puts that day");
  assert.equal(sectionIsEmpty({ rows: [] }), true);
  assert.equal(sectionIsEmpty({}), true);
  assert.equal(sectionIsEmpty(null), true);
  assert.equal(sectionIsEmpty({ rows: [{}] }), false);
});

test("paging walks by the rows already held and stops when the server says so", () => {
  assert.equal(nextOffset({ hasMore: false, offset: 0, rows: [1] }), null);
  assert.equal(nextOffset(null), null);
  assert.equal(nextOffset({ hasMore: true, offset: 0, rows: new Array(400) }), 400);
  const held = { rows: [1, 2], offset: 0, hasMore: true, totalRows: 3 };
  const merged = addPage(held, { rows: [3], offset: 2, hasMore: false, totalRows: 3 });
  assert.deepEqual(merged.rows, [1, 2, 3]);
  assert.equal(merged.hasMore, false);
  assert.equal(nextOffset({ ...addPage(held, { rows: [3], offset: 2, hasMore: true }) }), 3, "the next page follows everything held");
});

test("no more than two reads run at once, first asked first run, and a failure frees its slot", async () => {
  assert.equal(FETCHES_AT_ONCE, 2);
  const limit = createLimiter(FETCHES_AT_ONCE);
  const started = [];
  const release = [];
  let running = 0;
  let peak = 0;
  const job = (name, fails = false) => limit(() => new Promise((resolve, reject) => {
    started.push(name);
    running += 1;
    peak = Math.max(peak, running);
    release.push(() => { running -= 1; (fails ? reject : resolve)(name); });
  }));
  const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
  const all = [job("a", true), job("b"), job("c"), job("d")].map((p) => p.catch((e) => `failed ${e}`));
  await tick();
  assert.deepEqual(started, ["a", "b"]);
  release[0]();
  await tick();
  assert.deepEqual(started, ["a", "b", "c"], "the failed read freed its slot");
  release[1]();
  await tick();
  release[2]();
  await tick();
  release[3]();
  assert.deepEqual(await Promise.all(all), ["failed a", "b", "c", "d"]);
  assert.equal(peak, 2);
});

test("a row's time becomes a History link to that moment, on that board and side", () => {
  const section = { list: "Watchlist", side: "bear" };
  assert.deepEqual(momentLink(table, section, "09:30"), {
    label: "Open NVDA on Watchlist History, 09:30", view: "MomX Scanner", list: "Watchlist", side: "bear", tab: "history",
    day: "2026-11-10", ticker: "NVDA", time: "09:30",
  });
  assert.equal(momentLink(table, section, "24:05").time, "00:05");
  assert.equal("time" in momentLink(table, section, ""), false);
});

test("the sheet draws the real History table: no second copy of the columns", () => {
  assert.match(source, /MomxDayTable/);
  assert.doesNotMatch(source, /MomxHistoryRow|columns:\s*\[/);
  const table = panel.slice(panel.indexOf("export const MomxDayTable"), panel.indexOf("export function loadMomxDayData"));
  assert.match(table, /applyLayout\(MOMX_HISTORY_COLUMNS, columnLayout\)/);
  assert.match(table, /readStoredLayout\(MOMX_COLUMN_KEYS\)/);
  assert.match(table, /<MomxHistoryRow /);
  assert.match(table, /observeConfirmationHistory\(annotateChanges\(flattenHistoryRows\(rows\)\)\)/);
});

test("the sheet fills the Event column itself: it is empty until the panel has mounted", () => {
  assert.match(source, /loadMomxDayData\(\)/);
  const loader = panel.slice(panel.indexOf("export function loadMomxDayData"), panel.indexOf("export function MomxDayExplain"));
  assert.match(loader, /EARNINGS_BY_SYMBOL = earningsMapFrom\(payload\)/);
  assert.match(loader, /if \(EARNINGS_BY_SYMBOL\.size === 0\)/, "a filled map must not be read again");
});

test("the link's three steps run in order, and only in the main window", () => {
  const start = panel.indexOf("A link from the chat");
  const code = panel.slice(start, panel.indexOf("Tell the chat which ticker", start));
  assert.match(code, /if \(embedded\) return undefined;[\s\S]*takePendingScannerLink\(\)/);
  const order = ["onChooseList(list)", "setHistoryDate(linkJump.day || null)", "setHistoryJump({ clock: linkJump.time"].map((text) => code.indexOf(text));
  assert.ok(order.every((at) => at > 0) && order[0] < order[1] && order[1] < order[2], `steps out of order: ${order}`);
  assert.match(code, /phase !== 2\) return;\s*if \(activeList !== linkJump\.list \|\| direction !== linkJump\.side\) return;/, "the day must wait for the board, or onChooseList wipes it");
  assert.match(code, /lists\.some\(\(item\) => item\.name === linkJump\.list\)/, "list names are checked");
});

test("App drops owner pages for a regular user before doing anything, and only listens in the main window", () => {
  const start = app.indexOf("OPEN_LINK_EVENT, onOpen");
  assert.ok(start > 0, "the link listener is missing");
  const code = app.slice(app.lastIndexOf("useEffect(() => {", start), start);
  assert.match(code, /if \(popoutConfig\.mode\) return undefined;/);
  assert.match(code, /linkAllowed\(link, isOwnerLatestRef\.current\)/);
  assert.ok(code.indexOf("linkAllowed") < code.indexOf("chooseMainPage"), "the check must come before any navigation");
});
