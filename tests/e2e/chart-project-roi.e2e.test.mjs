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

// The toolbar: ROI turns the plan on/off; Call / Put / Both (shown while
// on) pick the side, and tapping the picked side opens or closes the card.
const side = (chart, name) => chart.locator(`button.oi-finder-chart-roi-side[aria-label="ROI ${name} side"]`);

async function openCard(chart) {
  const roiButton = chart.locator("button.oi-finder-chart-roi");
  if (await chart.locator(ROI).isVisible()) return chart.locator(ROI);
  if ((await roiButton.getAttribute("aria-pressed")) === "true") {
    await chart.locator('button.oi-finder-chart-roi-side[aria-pressed="true"]').click();
  } else {
    await roiButton.click();
  }
  const roi = chart.locator(ROI);
  await roi.waitFor({ state: "visible", timeout: 30_000 });
  // The card fills once the chart's option chain arrives; on a loaded evening
  // (2026-11-11) that took over 60 s after the candles, so allow 2 minutes.
  await roi.locator(".chart-project-roi-title").waitFor({ state: "visible", timeout: 120_000 });
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

// The overlay reports which label boxes it drew inside the pane. Every line
// of the plan (entry, each target, stop) must be on screen - the chart keeps
// the plan in view.
async function waitAllLabelsDrawn(page, chart) {
  const handle = await chart.locator(LINES).elementHandle();
  await page.waitForFunction(
    (node) => {
      const planned = String(node?.dataset.lines || "").split("|").map((item) => item.split(":")[0]).sort().join("|");
      const drawn = String(node?.dataset.drawn || "").split("|").sort().join("|");
      return Boolean(planned) && planned === drawn;
    },
    handle,
    { timeout: 20_000 },
  );
}

async function plans(page) {
  return JSON.parse((await readStorage(page, PLAN_KEY)) || "{}");
}

e2eTest("the chart ROI card fills from the live chain and draws Entry / Target / Stop", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roi = await openCard(chart);
    // One side (the call) by default; the toolbar shows Call / Put / Both.
    await chart.locator(LINES).waitFor({ state: "attached", timeout: 60_000 });
    const single = (await drawnLines(chart)).map((line) => line.key);
    assert.ok(single.every((key) => !key.startsWith("c-") && !key.startsWith("p-")), "one side by default");
    assert.ok(single.includes("t1") && single.includes("target"), "call targets drawn");
    assert.equal(await side(chart, "call").getAttribute("aria-pressed"), "true");
    await side(chart, "both").click();
    await page.waitForFunction((sel) => /\|c-/.test(document.querySelector(sel)?.dataset.lines || ""), `section.oi-finder-chart-card[data-chart-symbol="AAPL"] ${LINES}`);

    assert.match(await roi.locator(".chart-project-roi-title b").innerText(), /^AAPL \d+(\.\d+)?[CP] \d+\/\d+$/);
    assert.match(await roi.locator(".chart-project-roi-title small").innerText(), /chain ENTRY|strike from|first OTM strike/);
    assert.match(await roi.locator(".chart-project-roi-hero strong").innerText(), /^[+−]\d+%$/);
    const ladder = roi.locator(".chart-project-roi-strike");
    assert.ok((await ladder.count()) >= 2, "the strike ladder lists the ATM and OTM strikes");
    assert.equal(await roi.locator(".chart-project-roi-strike.is-picked").count(), 1);
    assert.equal(await roi.locator(".chart-project-roi-fields small").first().innerText(), "chain price");

    // A new plan draws both sides (2026-11-11): one Entry naming the call and
    // the put, call T1-T3 above it, put T1-T3 below it, a stop for each, and
    // every target box carries its contract's price for the broker.
    const lines = await drawnLines(chart);
    assert.equal(lines[0].key, "entry");
    assert.match(lines[0].title, /^Entry \d+\.\d\d {2}\d+(\.\d+)?C \$\d+\.\d\d · \d+(\.\d+)?P \$\d+\.\d\d( {2}⚠ quotes old)?$/);
    const sideTargets = (prefix, letter) => {
      const found = lines.filter((line) => line.key.startsWith(prefix) && !line.key.endsWith("stop"));
      assert.ok(found.length >= 3, `only ${found.length} ${prefix} targets drawn`);
      found.forEach((line, index) => {
        assert.match(line.title, new RegExp(`^T${index + 1} \\d+\\.\\d\\d {2}[+−]\\d+% {2}\\d+(\\.\\d+)?${letter} \\$\\d+\\.\\d\\d$`));
      });
      return found;
    };
    const callTargets = sideTargets("c-", "C");
    const putTargets = sideTargets("p-", "P");
    assert.ok(callTargets.every((line) => line.price > lines[0].price), "call targets sit above the entry");
    assert.ok(putTargets.every((line) => line.price < lines[0].price), "put targets sit below the entry");
    const stops = lines.filter((line) => line.key.endsWith("stop"));
    stops.forEach((line) => assert.match(line.title, /^Stop \d+\.\d\d {2}−50% {2}\d+(\.\d+)?[CP] \$\d+\.\d\d$/));
    // The card: broker prices for the side being edited and for the other side.
    const orders = roi.locator(".chart-project-roi-orders");
    assert.equal(await orders.count(), 2);
    for (const text of await orders.allInnerTexts()) {
      assert.match(text, /Buy\s*\$\d+\.\d\d[\s\S]*Stop\s*\$\d+\.\d\d[\s\S]*Sell T1\s*\$\d+\.\d\d[\s\S]*Sell T2\s*\$\d+\.\d\d[\s\S]*Sell T3\s*\$\d+\.\d\d/);
    }
    assert.equal(await side(chart, "both").getAttribute("aria-pressed"), "true");
    await waitAllLabelsDrawn(page, chart);

    // A ladder tap picks that strike.
    const other = ladder.filter({ hasNot: page.locator(".is-picked") }).filter({ hasNotText: "ATM" }).first();
    const otherStrike = (await other.locator("b").innerText()).trim();
    await other.click();
    assert.match(await roi.locator(".chart-project-roi-title b").innerText(), new RegExp(`^AAPL ${otherStrike.replace(".", "\\.")} `));

    // A typed fill sticks, moves the Entry label, and "↺ ask" puts the ask back.
    const linesTag = `section.oi-finder-chart-card[data-chart-symbol="AAPL"] ${LINES}`;
    const entry = roi.locator('input[aria-label="Entry option price"]');
    await entry.fill("1.23");
    await entry.press("Enter");
    await roi.locator(".chart-project-roi-fields small", { hasText: "your fill" }).waitFor();
    assert.equal((await plans(page)).AAPL.entry, 1.23);
    await page.waitForFunction((sel) => /C \$1\.23 · /.test(document.querySelector(sel)?.dataset.lines?.split("|")[0] || ""), linesTag);
    await roi.locator(".chart-project-roi-ask").click();
    await roi.locator(".chart-project-roi-fields small", { hasText: "chain price" }).waitFor();

    // Put edits the put side; the call keeps its picked strike.
    await roi.locator(".chart-project-roi-side button", { hasText: "Put" }).click();
    assert.match(await roi.locator(".chart-project-roi-title b").innerText(), /P \d+\/\d+$/);
    const saved = (await plans(page)).AAPL;
    assert.equal(saved.side, "PUT");
    assert.equal(String(saved.other.strike), otherStrike.replace(/C$/, ""));

    // Put on the toolbar: only the put side is drawn, with the single-side keys.
    await side(chart, "put").click();
    await page.waitForFunction((sel) => {
      const keys = String(document.querySelector(sel)?.dataset.lines || "").split("|").map((item) => item.split(":")[0]);
      return keys.length > 1 && !keys.some((key) => key.startsWith("c-") || key.startsWith("p-"));
    }, linesTag);
    const putLines = await drawnLines(chart);
    const onlyPut = putLines.filter((line) => /^T\d /.test(line.title));
    assert.ok(onlyPut.length >= 3 && onlyPut.every((line) => line.price < putLines[0].price && / \d+(\.\d+)?P \$/.test(line.title)), "put targets sit below the entry price");
    assert.equal(await roi.locator(".chart-project-roi-orders").count(), 1);
    await side(chart, "both").click();

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
}, { timeout: 420_000 });

e2eTest("on a phone the ROI card fits the screen and scrolls inside itself", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roi = await openCard(chart);
    const box = await roi.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width, "the card stays inside the screen");
    assert.equal(await overflowsSideways(page), false);
    await waitAllLabelsDrawn(page, chart);
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
      (sel) => /C \$2\.34( · |$| {2}⚠)/.test(document.querySelectorAll(sel)[1]?.querySelector('[data-testid="chart-project-roi-lines"]')?.dataset.lines?.split("|")[0] || ""),
      "section.oi-finder-chart-card[data-tape-length]",
      { timeout: 10_000 },
    );
    noPageErrors(app);
  });
});

e2eTest("a saved plan without a Both choice draws one side, and Both is one tap on the toolbar", async () => {
  // Saved by an earlier version of the card: one side, no `both` field.
  const old = { AAPL: { side: "CALL", contracts: 1, createdAt: Date.now() - 60 * 60_000 } };
  await withApp(chartAppOptions({ storage: { [PLAN_KEY]: JSON.stringify(old) } }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    await chart.locator(LINES).waitFor({ state: "attached", timeout: 60_000 });
    const tag = `section.oi-finder-chart-card[data-chart-symbol="AAPL"] ${LINES}`;
    await page.waitForFunction((sel) => String(document.querySelector(sel)?.dataset.lines || "").includes("target:"), tag, { timeout: 60_000 });
    const keys = (await drawnLines(chart)).map((line) => line.key);
    assert.ok(keys.every((key) => !key.startsWith("c-") && !key.startsWith("p-")), "one side only");
    assert.equal(await chart.locator("button.oi-finder-chart-roi").getAttribute("aria-pressed"), "true", "ROI shows as on");
    await side(chart, "both").click();
    await page.waitForFunction((sel) => {
      const list = String(document.querySelector(sel)?.dataset.lines || "").split("|").map((item) => item.split(":")[0]);
      return list.some((key) => key.startsWith("c-")) && list.some((key) => key.startsWith("p-"));
    }, tag, { timeout: 15_000 });
    const lines = await drawnLines(chart);
    assert.ok(lines.some((line) => line.key.startsWith("c-") && line.price > lines[0].price), "no call target above");
    assert.ok(lines.some((line) => line.key.startsWith("p-") && line.price < lines[0].price), "no put target below");
    noPageErrors(app);
  });
});

e2eTest("the target switch shows 1 to 6 targets on each side", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roi = await openCard(chart);
    await side(chart, "both").click();
    const tag = `section.oi-finder-chart-card[data-chart-symbol="AAPL"] ${LINES}`;
    const countTargets = (prefix) => page.evaluate(({ sel, side }) => String(document.querySelector(sel)?.dataset.lines || "")
      .split("|").map((item) => item.split(":")[0]).filter((key) => key.startsWith(side) && !key.endsWith("stop")).length, { sel: tag, side: prefix });
    assert.equal(await roi.locator('[aria-label="Show 3 targets"]').getAttribute("aria-pressed"), "true", "three by default");
    for (const count of [6, 1, 4]) {
      await roi.locator(`[aria-label="Show ${count} target${count === 1 ? "" : "s"}"]`).click();
      await page.waitForFunction(async ({ sel, wanted }) => {
        const keys = String(document.querySelector(sel)?.dataset.lines || "").split("|").map((item) => item.split(":")[0]);
        return keys.filter((key) => key.startsWith("c-") && !key.endsWith("stop")).length === wanted
          && keys.filter((key) => key.startsWith("p-") && !key.endsWith("stop")).length === wanted;
      }, { sel: tag, wanted: count }, { timeout: 15_000 });
      assert.equal(await countTargets("c-"), count);
      assert.equal((await plans(page)).AAPL.targetCount, count);
      assert.equal(await roi.locator(".chart-project-roi-orders").first().locator("span.is-gain").count(), count, `broker box lists ${count} sells`);
    }
    await waitAllLabelsDrawn(page, chart);
    noPageErrors(app);
  });
});

e2eTest("ROI on the toolbar: selected = lines on, tap again = off (the plan is kept)", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roiButton = chart.locator("button.oi-finder-chart-roi");
    assert.equal(await roiButton.getAttribute("aria-pressed"), "false");
    assert.equal(await chart.locator("button.oi-finder-chart-roi-side").count(), 0, "no side buttons while ROI is off");
    await openCard(chart);
    await chart.locator(LINES).waitFor({ state: "attached", timeout: 60_000 });
    assert.equal(await roiButton.getAttribute("aria-pressed"), "true");
    assert.equal(await chart.locator("button.oi-finder-chart-roi-side").count(), 3);
    const before = (await plans(page)).AAPL;
    // Off: lines and card go, side buttons hide, the plan stays.
    await roiButton.click();
    await chart.locator(LINES).waitFor({ state: "detached", timeout: 10_000 });
    await chart.locator(ROI).waitFor({ state: "detached", timeout: 10_000 });
    assert.equal(await roiButton.getAttribute("aria-pressed"), "false");
    assert.equal(await chart.locator("button.oi-finder-chart-roi-side").count(), 0);
    const hidden = (await plans(page)).AAPL;
    assert.equal(hidden.hidden, true);
    assert.equal(hidden.createdAt, before.createdAt, "the plan is kept");
    // On again: the same plan comes back.
    await roiButton.click();
    await chart.locator(LINES).waitFor({ state: "attached", timeout: 10_000 });
    await waitAllLabelsDrawn(page, chart);
    assert.equal((await plans(page)).AAPL.hidden, false);
    assert.equal((await plans(page)).AAPL.createdAt, before.createdAt);
    // Tapping the picked side closes and reopens the card.
    await side(chart, "call").click();
    await chart.locator(ROI).waitFor({ state: "detached", timeout: 10_000 });
    await side(chart, "call").click();
    await chart.locator(ROI).waitFor({ state: "visible", timeout: 10_000 });
    noPageErrors(app);
  });
});

e2eTest("on a phone ROI + one C/P/B button fit the toolbar row; the button steps Call, Put, Both", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    await chart.locator("button.oi-finder-chart-roi").click();
    const cycle = chart.locator("button.oi-finder-chart-roi-cycle");
    await cycle.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal(await chart.locator("button.oi-finder-chart-roi-side:visible").count(), 0, "the three desktop buttons are hidden on a phone");
    // The row must not overflow: an overflowing row is what slid it under the ticker box.
    const row = await chart.locator(".oi-finder-chart-actions").evaluate((node) => ({
      overflow: node.scrollWidth - node.clientWidth, vertical: node.scrollHeight - node.clientHeight, y: getComputedStyle(node).overflowY,
    }));
    assert.ok(row.overflow <= 1, `the toolbar row overflows by ${row.overflow}px`);
    assert.equal(row.y, "hidden");
    for (const [label, side] of [["P", "PUT"], ["B", "BOTH"], ["C", "CALL"]]) {
      await cycle.click();
      await page.waitForFunction((want) => {
        const plan = JSON.parse(localStorage.getItem("agx.chartProjectRoi.v1") || "{}").AAPL || {};
        return (plan.both === true ? "BOTH" : String(plan.side)) === want;
      }, side, { timeout: 10_000 });
      assert.equal((await cycle.innerText()).trim(), label);
    }
    assert.equal(await overflowsSideways(page), false);
    noPageErrors(app);
  });
});
