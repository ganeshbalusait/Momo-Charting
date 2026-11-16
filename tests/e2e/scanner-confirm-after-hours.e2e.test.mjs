// Confirm L1/L2/L3 after hours, end to end (his report 2026-11-10: "in setup
// column i dont see the Confirm L1,L2,L3 in Live"). Every row's last candle is
// hours old, as on any evening, so the Live board judges it as of that candle:
//   NVDA  RVOL 1h/2h/4h cyan, 2h SQZ blank, Daily Skittles box      -> L3
//   AAPL  RVOL 1h/2h/4h cyan, every 2h-D SQZ orange, no box         -> L1
//   SPY   the same colours as NVDA but its last candle is 6 days old -> no level

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady, boardFor, rowSymbols } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const HOUR = 3_600_000;
const FIVE_MIN = 300_000;
const candleStart = (ago) => Math.floor((Date.now() - ago) / FIVE_MIN) * FIVE_MIN;
const BLANK = { value: "-", bg: "black", fg: "black" };
const ORANGE = { value: 5, bg: "orange", fg: "black" };

function setRow(row, barMs) {
  const barAt = barMs / 1000;
  row.scanPass = true;
  row.marketDataAt = new Date(barMs).toISOString();
  // Three Setup types that do not depend on the clock: Explosive, MX A+, FLIP.
  row.grade = row.grade || { letter: "A", checks: { skit: 6, skitBullish: ["D"], pushRvol: ["1h"] }, reasons: ["fixture"] };
  row.momoxAPlus = { at: new Date(barMs - 600_000).toISOString(), weeklies: true, sqz: ["4h"] };
  row.m5 = { ...row.m5, pattern: "explosive", sqzMom: { barAt, value: 2, prev: 1, color: "black" } };
  row.adx = { "5m": { closedCross: "bull", closedCrossAt: barAt } };
  for (const tf of ["1h", "2h", "4h"]) row.rvol[tf] = { value: 3.2, bg: "cyan", fg: "black" };
}

function afterHoursBoard() {
  const board = boardFor("Mag7", "bull");
  const by = (symbol) => board.rows.find((row) => row.symbol === symbol);
  setRow(by("NVDA"), candleStart(3 * HOUR));
  by("NVDA").sqz["2h"] = BLANK;
  setRow(by("AAPL"), candleStart(3 * HOUR));
  for (const tf of ["2h", "4h", "D"]) by("AAPL").sqz[tf] = ORANGE;
  by("AAPL").skittles = {};
  setRow(by("SPY"), candleStart(6 * 24 * HOUR));
  by("SPY").sqz["2h"] = BLANK;
  by("SPY").skittles.D = { value: 1, bg: "cyan", fg: "cyan" };
  return board;
}

const reads = () => ({ ...boardReads(), "GET /api/momx-scanner": () => ({ json: afterHoursBoard() }) });
const rowOf = (page, symbol) => page.locator(`tr.momx-row:has([data-testid='momx-symbol-link']:text-is('${symbol}'))`);
const levelOf = (page, symbol) => rowOf(page, symbol).locator("[data-testid='momx-confirmation-level']");

async function assertLevels(page) {
  await levelOf(page, "NVDA").waitFor({ timeout: 45_000 });
  await levelOf(page, "AAPL").waitFor({ timeout: 10_000 });
  assert.equal((await levelOf(page, "NVDA").innerText()).trim(), "Confirm L3");
  assert.equal((await levelOf(page, "AAPL").innerText()).trim(), "Confirm L1");
  const hover = await levelOf(page, "NVDA").getAttribute("title");
  assert.match(hover, /^Not live: from the last candle at \d\d:\d\d ET on \d+\/\d+\./);
  assert.match(hover, /Setup types at that candle: [^.]*Explosive/, "the hover names what the level was judged on");
  const opacity = await levelOf(page, "NVDA").evaluate((node) => Number(getComputedStyle(node).opacity));
  assert.ok(opacity < 1, `the level is dimmed, opacity ${opacity}`);
  assert.equal(await rowOf(page, "SPY").count(), 1, "the dead ticker is still listed");
  assert.equal(await levelOf(page, "SPY").count(), 0, "a 6-day-old candle shows no level");
}

async function chooseRule(page, name) {
  const header = page.locator("button.momx-setup-header-button[aria-label='Choose Setup column rules']");
  await header.scrollIntoViewIfNeeded();
  await header.click();
  const picker = page.locator("[role='dialog'][aria-label='Setup column rules']");
  await picker.waitFor({ timeout: 10_000 });
  await picker.locator(".momx-setup-rule-grid label", { hasText: name }).locator("input[type='checkbox']").check();
  await picker.getByRole("button", { name: "Done" }).click();
}

// The board comes back empty and warming, so the screen draws the saved copy,
// whose rows never went through the live observer. NVDA's candle is current.
function savedCopyStorage() {
  const board = boardFor("Mag7", "bull");
  const nvda = board.rows.find((row) => row.symbol === "NVDA");
  setRow(nvda, candleStart(FIVE_MIN));
  nvda.sqz["2h"] = BLANK;
  return { ...BOARD_STORAGE, "momx.board.Mag7": JSON.stringify(board) };
}

for (const [label, viewport] of [["desktop", undefined], ["phone", PHONE]]) {
  e2eTest(`a fresh row drawn from the saved board copy still shows its Confirm level (${label})`, async () => {
    await withApp({ view: VIEW, storage: savedCopyStorage(),
      fakeReads: { ...boardReads(), "GET /api/momx-scanner": () => ({ json: { rows: [], rest: [], warming: true } }) },
      ...(viewport ? { viewport } : {}) }, async (app) => {
      await levelOf(app.page, "NVDA").waitFor({ timeout: 45_000 });
      assert.equal((await levelOf(app.page, "NVDA").innerText()).trim(), "Confirm L3");
      assert.doesNotMatch(await levelOf(app.page, "NVDA").getAttribute("title"), /^Not live/);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 18 * 60_000 });
}

for (const [label, viewport] of [["desktop", undefined], ["phone", PHONE]]) {
  e2eTest(`after hours the Live Setup column shows Confirm from the last candle, and the Confirm filter keeps those rows (${label})`, async () => {
    await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: reads(), ...(viewport ? { viewport } : {}) }, async (app) => {
      await boardReady(app.page);
      await assertLevels(app.page);
      await chooseRule(app.page, "Confirm L1+");
      await app.page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length === 2);
      assert.deepEqual((await rowSymbols(app.page)).sort(), ["AAPL", "NVDA"]);
      if (viewport) assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
    // Waiting for the shared browser (up to 15 min) must not count against the test.
  }, { timeout: 18 * 60_000 });
}
