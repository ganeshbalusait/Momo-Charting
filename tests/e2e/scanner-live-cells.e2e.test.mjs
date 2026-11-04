// MomX Scanner -> the TOS-parity colour cells (RVOL / SQZ / Skittles), end to
// end on the live Mag7 board. Every painted cell must use a colour from his
// thinkScript palette (momxCells.THINKSCRIPT_COLORS) - a colour outside it
// means a cell the board invented - and no cell may print NaN or undefined.

import assert from "node:assert/strict";

import { THINKSCRIPT_COLORS } from "../../frontend/src/momxCells.js";
import { PHONE, e2eTest, withApp } from "./harness.mjs";

const SCANNER = { "momx.scanner.activeList": "Mag7", "momx.scanner.direction": "bull", "momx.scanner.activeView": "live", "momx-auto-cards-on": "0" };
const ROWS = "section[data-testid='momx-scanner-view'] .momx-table-scroll tr.momx-row";
const CELLS = ["rvol.", "sqz.", "skittles."].map((prefix) => `${ROWS} td[data-col^='${prefix}']`).join(", ");

const rgb = (hex) => {
  const n = Number.parseInt(hex.slice(1), 16);
  return `rgb(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255})`;
};
const PALETTE = new Set(Object.values(THINKSCRIPT_COLORS).map(rgb));

async function wholeBoard(page) {
  await page.locator("[data-testid='momx-scanner-panel']").first().waitFor({ timeout: 90_000 });
  const matchesOnly = page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input");
  if (await matchesOnly.isChecked()) await matchesOnly.uncheck();
  await page.locator(ROWS).nth(1).waitFor({ timeout: 120_000 });
}

async function paintedCells(page) {
  return page.locator(CELLS).evaluateAll((cells) => cells.map((td) => {
    const pill = td.querySelector(".momx-pill");
    return {
      col: td.getAttribute("data-col"),
      text: td.textContent.trim(),
      background: pill ? getComputedStyle(pill).backgroundColor : null,
      ink: pill ? getComputedStyle(pill).color : null,
    };
  }));
}

e2eTest("every RVOL / SQZ / Skittles cell is painted from his thinkScript palette and prints a real number", async () => {
  await withApp({ view: "MomX Scanner", storage: SCANNER }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    const cells = await paintedCells(page);
    const rows = await page.locator(ROWS).count();
    assert.ok(cells.length >= rows * 10, `${cells.length} colour cells for ${rows} rows`);
    const painted = cells.filter((cell) => cell.background);
    assert.ok(painted.length > 0, "a live board paints at least one cell");
    for (const cell of painted) {
      assert.ok(PALETTE.has(cell.background), `${cell.col} "${cell.text}" painted ${cell.background}, outside the palette`);
    }
    for (const cell of cells) {
      assert.doesNotMatch(cell.text, /NaN|undefined|null|Infinity/, `${cell.col} printed "${cell.text}"`);
      if (cell.col.startsWith("rvol.") && cell.text) assert.match(cell.text, /^-?\d+\.\d$/, `${cell.col} "${cell.text}" is one decimal like TOS`);
      if (cell.col.startsWith("skittles.") && cell.text) assert.match(cell.text, /^-?\d+$/, `${cell.col} "${cell.text}" is rounded like TOS`);
    }
    // The headers the trader reads them by.
    const groups = (await page.locator("section[data-testid='momx-scanner-view'] .momx-table thead").first().innerText()).replace(/\s+/g, " ");
    for (const label of ["RVOL", "SQZ", "SKIT"]) assert.ok(groups.includes(label), `missing ${label} header group`);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the colour cells keep the same palette", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: SCANNER }, async (app) => {
    const { page } = app;
    await wholeBoard(page);
    for (const cell of (await paintedCells(page)).filter((c) => c.background)) {
      assert.ok(PALETTE.has(cell.background), `${cell.col} painted ${cell.background}`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
