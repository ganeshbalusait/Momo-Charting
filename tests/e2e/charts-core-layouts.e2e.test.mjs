// Charts & OI: multi-chart layouts (2026-11-04 coverage). Every layout in the
// picker builds its grid, 6 across keeps the 320px width floor (the strike
// labels were clipped below it), the quick count, the keyboard resize handle,
// Widen panel, and the layout surviving a reload. Every panel shows AAPL with
// the ticker linked, so a 10-panel grid still costs one chart build.
// MAG7 is left out on purpose: it opens seven tickers (spec limit is two).

import assert from "node:assert/strict";
import test from "node:test";

import { DESKTOP, e2eTest, marketHoursBlock } from "./harness.mjs";
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
      // The menu closes after a pick; its buttons stay in the page but are
      // hidden, which getByRole skips unless told to include them.
      const pressed = page.locator(".oi-chart-layout-menu").getByRole("button", { name: label, exact: true, includeHidden: true });
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

// KNOWN FAILURE (2026-11-11), kept as a todo: with the option chain open at
// 1440 px the six columns get ~150 px each. A fixed 320 px chart floor was
// REMOVED on purpose on 2026-08-24 (it clipped the right price scale, where the
// strike labels are, off the panel), so the app fits charts to the space
// instead of scrolling sideways. Whether 6 across should scroll sideways at a
// 320 px minimum is Ganesh's call; until then the next test checks what the
// August fix guarantees - the price scale stays inside every panel.
test("6 across at 1440 px keeps every chart at the 320 px floor", {
  skip: marketHoursBlock() || false,
  todo: "no 320 px floor by design since 2026-08-24 (fit-to-host); 6 across with the chain open gives ~150 px panels - waiting on Ganesh",
  timeout: 300_000,
}, async () => {
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
});

e2eTest("6 across at 1440 px keeps each chart's price scale inside its panel", async () => {
  await withCharts({ viewport: DESKTOP, workspace: { layoutId: "six-columns", symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForLayout(page, "six-columns", 6);
    await page.waitForFunction(() => document.querySelectorAll(".oi-chart-progressive-placeholder").length === 0, null, { timeout: 120_000 });
    await waitForTape(page, { index: 5, symbol: "AAPL", timeout: 120_000 });
    const panels = await page.locator("section.oi-finder-chart-card[data-tape-length] .oi-finder-candle-chart").evaluateAll(
      (nodes) => nodes.map((node) => {
        const host = node.getBoundingClientRect();
        const canvas = node.querySelector(".tv-lightweight-charts")?.getBoundingClientRect();
        return { hostRight: host.right, canvasRight: canvas ? canvas.right : null };
      }),
    );
    assert.equal(panels.length, 6);
    panels.forEach((panel, index) => {
      assert.ok(panel.canvasRight != null, `panel ${index + 1} has no chart canvas`);
      assert.ok(panel.canvasRight <= panel.hostRight + 1, `panel ${index + 1}: price scale clipped (canvas ends ${Math.round(panel.canvasRight)} px, panel ${Math.round(panel.hostRight)} px)`);
    });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

// 2026-11-11: at 1440 px with the option chain open, the toolbar's layout
// buttons (Mag7, layout picker, count...) shrank into a 274 px box. Being
// right-aligned, the overflow spilled LEFT under the quick-ticker row, so the
// layout picker sat beneath the NFLX button and could not be clicked. The
// ticker row is the part that should give way (it scrolls).
e2eTest("at 1440 px the ticker row never covers the layout buttons", async () => {
  await withCharts({ viewport: DESKTOP, workspace: { layoutId: "six-columns", symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForLayout(page, "six-columns", 6);
    await waitForTape(page, { symbol: "AAPL", timeout: 120_000 });
    const layout = await page.evaluate(() => {
      const nav = document.querySelector("nav.oi-chart-quick-tickers").getBoundingClientRect();
      const actions = [...document.querySelectorAll(".oi-chart-layout-actions > *")]
        .map((node) => ({ name: String(node.className).split(" ")[0], left: node.getBoundingClientRect().left }));
      return { navRight: nav.right, actions };
    });
    for (const action of layout.actions) {
      assert.ok(action.left >= layout.navRight - 1, `${action.name} starts at ${Math.round(action.left)} px, under the ticker row (ends ${Math.round(layout.navRight)} px)`);
    }
    // The pop-up toasts (Schwab login, alerts) close themselves; wait them out.
    await page.locator(".global-price-alert-toast").waitFor({ state: "detached", timeout: 15_000 }).catch(() => {});
    await chooseLayout(page, "4 grid");
    await waitForLayout(page, "quad", 4);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

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
