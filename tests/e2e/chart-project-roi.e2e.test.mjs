// Chart Project ROI card (2026-11-11), end to end in the real app.
//
// The ROI button on the chart opens a card filled from the chart's live chain:
// contract, entry (live ask), projected ROI at the full expected move, strike
// ladder, and Entry / Target / Stop lines on the chart. The lines are canvas
// drawn, so the card's hidden tag [data-testid=chart-project-roi-lines] lists
// what was drawn. The plan lives in this browser only
// (agx.chartProjectRoi.v1); nothing is sent to the server.
//
// Uses the real AAPL chart and chain (read only), so assertions check shape,
// not exact prices.
import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors, readStorage } from "./charts-studies-helpers.mjs";
import { card, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const PLAN_KEY = "agx.chartProjectRoi.v1";
const ROI = '[data-testid="chart-project-roi"]';
const LINES = '[data-testid="chart-project-roi-lines"]';

async function openCard(chart) {
  await chart.locator("button.oi-finder-chart-roi").click();
  const roi = chart.locator(ROI);
  await roi.waitFor({ state: "visible", timeout: 30_000 });
  await roi.locator(".chart-project-roi-title").waitFor({ state: "visible", timeout: 60_000 });
  return roi;
}

async function drawnLines(chart) {
  const tag = chart.locator(LINES);
  if (!(await tag.count())) return [];
  return String(await tag.getAttribute("data-lines")).split("|").map((item) => {
    const [key, price, title] = item.split(":");
    return { key, price: Number(price), title };
  });
}

async function plans(page) {
  return JSON.parse((await readStorage(page, PLAN_KEY)) || "{}");
}

e2eTest("the chart ROI card fills from the live chain and draws Entry / Target / Stop", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roi = await openCard(chart);

    assert.match(await roi.locator(".chart-project-roi-title b").innerText(), /^AAPL \d+(\.\d+)?[CP] \d+\/\d+$/);
    assert.match(await roi.locator(".chart-project-roi-title small").innerText(), /strike from|first OTM strike/);
    assert.match(await roi.locator(".chart-project-roi-hero strong").innerText(), /^[+−]\d+%$/);
    const ladder = roi.locator(".chart-project-roi-strike");
    assert.ok((await ladder.count()) >= 2, "the strike ladder lists the ATM and OTM strikes");
    assert.equal(await roi.locator(".chart-project-roi-strike.is-picked").count(), 1);
    assert.equal(await roi.locator(".chart-project-roi-fields small").first().innerText(), "live ask");

    const lines = await drawnLines(chart);
    assert.deepEqual(lines.map((line) => line.key).slice(0, 2), ["entry", "target"]);
    assert.match(lines[0].title, /^Entry \d+\.\d\d {2}\d+(\.\d+)?C \$\d+\.\d\d$/);
    assert.match(lines[1].title, /^Target \d+\.\d\d/);

    // A ladder tap picks that strike.
    const other = ladder.filter({ hasNot: page.locator(".is-picked") }).filter({ hasNotText: "ATM" }).first();
    const otherStrike = (await other.locator("b").innerText()).trim();
    await other.click();
    assert.match(await roi.locator(".chart-project-roi-title b").innerText(), new RegExp(`^AAPL ${otherStrike.replace(".", "\\.")} `));

    // A typed fill sticks, moves the Entry label, and "↺ ask" puts the ask back.
    const entry = roi.locator('input[aria-label="Entry option price"]');
    await entry.fill("1.23");
    await entry.press("Enter");
    await roi.locator(".chart-project-roi-fields small", { hasText: "your fill" }).waitFor();
    assert.equal((await plans(page)).AAPL.entry, 1.23);
    await page.waitForFunction((sel) => /\$1\.23$/.test(document.querySelector(sel)?.dataset.lines?.split("|")[0] || ""), `${'section.oi-finder-chart-card[data-chart-symbol="AAPL"]'} ${LINES}`);
    await roi.locator(".chart-project-roi-ask").click();
    await roi.locator(".chart-project-roi-fields small", { hasText: "live ask" }).waitFor();

    // Puts mirror the target below spot.
    await roi.locator(".chart-project-roi-side button", { hasText: "Put" }).click();
    assert.match(await roi.locator(".chart-project-roi-title b").innerText(), /P \d+\/\d+$/);
    const putLines = await drawnLines(chart);
    assert.ok(putLines[1].price < putLines[0].price, "a put target sits below the entry price");

    // Close keeps the lines; a reload keeps the plan; Delete clears both.
    await roi.locator(".chart-project-roi-close").click();
    await roi.waitFor({ state: "detached" });
    assert.ok((await drawnLines(chart)).length >= 2, "closing the card keeps the lines");
    await page.reload();
    const reloaded = await chartCard(page, "AAPL");
    await reloaded.locator(LINES).waitFor({ state: "attached", timeout: 60_000 });
    const again = await openCard(reloaded);
    await again.locator(".chart-project-roi-delete").click();
    await reloaded.locator(LINES).waitFor({ state: "detached" });
    assert.equal((await plans(page)).AAPL, undefined);

    assert.equal(app.writes.filter((write) => /roi/i.test(write.path)).length, 0, "the plan never goes to the server");
    noPageErrors(app);
  });
});

e2eTest("on a phone the ROI card fits the screen and scrolls inside itself", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roi = await openCard(chart);
    const box = await roi.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width, "the card stays inside the screen");
    assert.equal(await overflowsSideways(page), false);
    const scrollable = await roi.evaluate((node) => node.scrollHeight > node.clientHeight);
    if (scrollable) {
      await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
      await page.mouse.wheel(0, 600);
      await page.waitForFunction((sel) => document.querySelector(sel).scrollTop > 50, ROI, { timeout: 10_000 });
    }
    await roi.locator(".chart-project-roi-delete").scrollIntoViewIfNeeded();
    await roi.locator(".chart-project-roi-delete").click();
    noPageErrors(app);
  });
});

e2eTest("two AAPL charts share one plan: a fill typed in one moves the other's Entry line", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 0, symbol: "AAPL" });
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    const first = card(page, 0);
    const second = card(page, 1);
    const roi = await openCard(first);
    const entry = roi.locator('input[aria-label="Entry option price"]');
    await entry.fill("2.34");
    await entry.press("Enter");
    await second.locator(LINES).waitFor({ state: "attached", timeout: 30_000 });
    await page.waitForFunction(
      (sel) => /\$2\.34$/.test(document.querySelectorAll(sel)[1]?.querySelector('[data-testid="chart-project-roi-lines"]')?.dataset.lines?.split("|")[0] || ""),
      "section.oi-finder-chart-card[data-tape-length]",
      { timeout: 10_000 },
    );
    noPageErrors(app);
  });
});
