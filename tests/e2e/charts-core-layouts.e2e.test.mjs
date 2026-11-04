// Charts & OI: multi-chart layouts (2026-11-04 coverage). Every layout in the
// picker builds its grid, 6 across keeps the 320px width floor (the strike
// labels were clipped below it), the quick count, the keyboard resize handle,
// Widen panel, and the layout surviving a reload. Every panel shows AAPL with
// the ticker linked, so a 10-panel grid still costs one chart build.
// MAG7 is left out on purpose: it opens seven tickers (spec limit is two).

import assert from "node:assert/strict";

import { DESKTOP, e2eTest } from "./harness.mjs";
import { card, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const LAYOUTS = [
  ["single", "Single", 1], ["two-columns", "2 side by side", 2], ["two-rows", "2 stacked", 2],
  ["three-columns", "3 across", 3], ["three-grid", "3 grid", 3], ["three-rows", "3 stacked", 3],
  ["quad", "4 grid", 4], ["four-columns", "4 across", 4], ["four-rows", "4 stacked", 4],
  ["six-grid", "6 grid", 6], ["six-columns", "6 across", 6], ["seven-columns", "7 across", 7],
  ["eight-columns", "8 across", 8], ["eight-grid", "8 grid", 8], ["nine-grid", "9 grid", 9], ["ten-grid", "10 grid", 10],
];
const MULTI = 'section.oi-chart-multilayout[aria-label="TOS-style multi-chart layout"]';
// Real panels and progressive placeholders both count as a slot on screen.
const SLOTS = `${MULTI} .oi-chart-layout-grid section.oi-finder-chart-card`;

async function chooseLayout(page, label) {
  await page.locator('details.oi-chart-layout-picker > summary[aria-label="Choose chart layout"]').click();
  const menu = page.locator(".oi-chart-layout-menu");
  await menu.waitFor({ state: "visible", timeout: 10_000 });
  await menu.getByRole("button", { name: label, exact: true }).click();
}

async function waitForLayout(page, id, count) {
  await page.locator(`${MULTI}.layout-${id}.charts-${count}`).waitFor({ timeout: 30_000 });
}

e2eTest("every layout in the picker builds a grid with that many chart slots", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    for (const [id, label, count] of LAYOUTS) {
      await chooseLayout(page, label);
      await waitForLayout(page, id, count);
      const pressed = page.locator(".oi-chart-layout-menu").getByRole("button", { name: label, exact: true });
      assert.equal(await pressed.getAttribute("aria-pressed"), "true", label);
      await page.waitForFunction(({ selector, wanted }) => document.querySelectorAll(selector).length === wanted,
        { selector: SLOTS, wanted: count }, { timeout: 30_000 });
      assert.equal(await page.locator(`${MULTI}.is-dense`).count(), count >= 5 ? 1 : 0, `${label}: dense mode`);
    }
    // Progressive mounting finishes: the last grid ends with ten real charts.
    await page.waitForFunction(() => document.querySelectorAll(".oi-chart-progressive-placeholder").length === 0, null, { timeout: 120_000 });
    assert.equal(await page.locator("section.oi-finder-chart-card[data-tape-length]").count(), 10);
    assert.equal(await page.locator("section.oi-chart-render-error").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 600_000 });

e2eTest("6 across at 1440 px keeps every chart at the 320 px floor", async () => {
  await withCharts({ viewport: DESKTOP, workspace: { layoutId: "six-columns", symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForLayout(page, "six-columns", 6);
    assert.equal(await page.locator(`${MULTI}.is-dense`).count(), 1);
    await page.waitForFunction(() => document.querySelectorAll(".oi-chart-progressive-placeholder").length === 0, null, { timeout: 120_000 });
    await waitForTape(page, { index: 5, symbol: "AAPL", timeout: 120_000 });
    const widths = await page.locator("section.oi-finder-chart-card[data-tape-length]").evaluateAll(
      (nodes) => nodes.map((node) => Math.round(node.getBoundingClientRect().width)),
    );
    assert.equal(widths.length, 6);
    assert.ok(widths.every((width) => width >= 319), `chart widths ${widths.join(",")} fall under the 320 px floor`);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("the quick count select switches to 2 charts", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await page.locator('label.oi-chart-count-picker[aria-label="Quick chart count"] select').selectOption("2");
    await waitForLayout(page, "two-columns", 2);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 180_000 });

e2eTest("the two-chart split moves with the keyboard and survives a reload", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    const handle = page.locator('.oi-chart-panel-resize-handle[role="separator"][aria-label="Resize first and second charts"]');
    const before = Number(await handle.getAttribute("aria-valuenow"));
    const leftBefore = (await card(page, 0).boundingBox()).width;
    await handle.focus();
    for (let press = 0; press < 3; press += 1) await handle.press("ArrowRight");
    await page.waitForFunction(({ selector, was }) => Number(document.querySelector(selector)?.getAttribute("aria-valuenow")) > was,
      { selector: '[aria-label="Resize first and second charts"]', was: before }, { timeout: 10_000 });
    const after = Number(await handle.getAttribute("aria-valuenow"));
    assert.ok(after > before && after <= 75, `split ${before} -> ${after}`);
    await page.waitForTimeout(300);
    assert.ok((await card(page, 0).boundingBox()).width > leftBefore, "the first chart did not grow");
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    assert.equal(Number(await handle.getAttribute("aria-valuenow")), after);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("Widen makes one panel wide and pressing again restores it", async () => {
  await withCharts({ workspace: { layoutId: "three-columns", symbols: ["AAPL"], timeframes: ["5m", "15m", "1h"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 2, symbol: "AAPL", timeout: 120_000 });
    const first = card(page, 0);
    const narrow = (await first.boundingBox()).width;
    await first.getByRole("button", { name: "Widen AAPL", exact: true }).click();
    await page.locator(`${MULTI}.has-wide-panel`).waitFor({ timeout: 10_000 });
    assert.match(await first.getAttribute("class"), /\bis-wide-panel\b/);
    await page.waitForTimeout(300);
    assert.ok((await first.boundingBox()).width > narrow + 20, "the widened panel is not wider");
    await first.getByRole("button", { name: "Restore AAPL width", exact: true }).click();
    await page.waitForFunction((selector) => !document.querySelector(selector), `${MULTI}.has-wide-panel`, { timeout: 10_000 });
    assert.doesNotMatch(await first.getAttribute("class"), /\bis-wide-panel\b/);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a chosen layout survives a reload", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await chooseLayout(page, "4 grid");
    await waitForLayout(page, "quad", 4);
    await page.waitForFunction(() => {
      try { return JSON.parse(localStorage.getItem("oiFinderChartWorkspace") || "{}").layoutId === "quad"; } catch { return false; }
    }, null, { timeout: 15_000 });
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForLayout(page, "quad", 4);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
