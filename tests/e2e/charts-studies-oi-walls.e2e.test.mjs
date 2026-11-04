// Charts & OI -> High OI walls and gamma levels, end to end (2026-11-04 coverage
// pass, feature charts-high-oi-walls). The wall lines and axis chips are drawn on
// the canvas; the chart card publishes what it drew in data-oi-wall-count,
// data-oi-high-walls ("<side><strike>@<width>" for each flashing leader) and
// data-gamma-levels / data-gamma-empty-reason, which is what this spec reads.
// Live AAPL chain and candles; only settings saves are faked.
import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, closeIndicators, noPageErrors, setStudy, studyRow } from "./charts-studies-helpers.mjs";

const OI_LEVELS = "OI Levels / High OI";
const GAMMA = "Gamma concentration (estimate)";

async function wallsDrawn(app, card, minimum = 1) {
  await app.page.waitForFunction(
    ({ selector, min }) => Number(document.querySelector(selector)?.dataset.oiWallCount || 0) >= min,
    { selector: 'section.oi-finder-chart-card[data-chart-symbol="AAPL"]', min: minimum },
    { timeout: 90_000 },
  );
  return {
    count: Number(await card.getAttribute("data-oi-wall-count")),
    leaders: String(await card.getAttribute("data-oi-high-walls") || "").split(/\s+/).filter(Boolean),
  };
}

function assertLeaders(leaders) {
  assert.ok(leaders.length >= 1, "walls are drawn but none is marked as the High OI leader");
  for (const token of leaders) assert.match(token, /^(C|P|CALL|PUT)\d+(\.\d+)?@\d+(\.\d+)?$/i, `odd leader token ${token}`);
  const sides = leaders.map((token) => token[0].toUpperCase());
  assert.equal(new Set(sides).size, sides.length, `more than one flashing leader on a side: ${leaders.join(" ")}`);
}

e2eTest("with OI Levels on, AAPL draws walls with one flashing leader per side", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await setStudy(card, OI_LEVELS, true);
    await closeIndicators(card);
    const { count, leaders } = await wallsDrawn(app, card);
    assert.ok(count > 0 && count <= 20, `${count} walls for the default 10 per side`);
    assertLeaders(leaders);
    noPageErrors(app);
  });
});

e2eTest("choosing 3 walls per side caps the chart and is remembered after a reload", async () => {
  await withApp(chartAppOptions(), async (app) => {
    let card = await chartCard(app.page, "AAPL");
    await setStudy(card, OI_LEVELS, true);
    await closeIndicators(card);
    await wallsDrawn(app, card);
    const picker = app.page.locator('.charts-oi-walls-picker[role="group"][aria-label="Chart OI walls per side"]').first();
    await picker.waitFor({ state: "visible", timeout: 60_000 });
    await picker.locator("button", { hasText: /^3$/ }).click();
    await picker.locator('button.is-active[aria-pressed="true"]', { hasText: /^3$/ }).waitFor({ timeout: 10_000 });
    await app.page.waitForFunction(
      () => Number(document.querySelector('section.oi-finder-chart-card[data-chart-symbol="AAPL"]')?.dataset.oiWallCount || 99) <= 6,
      null, { timeout: 30_000 },
    );
    assert.equal(await app.page.evaluate(() => window.localStorage.getItem("oiChartWallsPerSide")), "3");

    await app.page.reload({ waitUntil: "domcontentloaded" });
    card = await chartCard(app.page, "AAPL");
    const { count, leaders } = await wallsDrawn(app, card);
    assert.ok(count <= 6, `${count} walls after reload with 3 per side`);
    assertLeaders(leaders);
    const again = app.page.locator('.charts-oi-walls-picker[aria-label="Chart OI walls per side"]').first();
    await again.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal((await again.locator("button.is-active").innerText()).trim(), "3");
    noPageErrors(app);
  });
});

e2eTest("Gamma concentration gives levels or says why there are none", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await setStudy(card, GAMMA, true);
    await closeIndicators(card);
    await app.page.waitForFunction(() => {
      const element = document.querySelector('section.oi-finder-chart-card[data-chart-symbol="AAPL"]');
      return element && (Number(element.dataset.gammaLevels || 0) > 0 || String(element.dataset.gammaEmptyReason || "").trim());
    }, null, { timeout: 90_000 });
    const levels = Number(await card.getAttribute("data-gamma-levels"));
    const reason = String(await card.getAttribute("data-gamma-empty-reason") || "").trim();
    assert.ok(levels > 0 || reason, "gamma shows neither levels nor a reason");
    if (levels > 0) assert.equal(await card.getAttribute("data-gamma-symbol"), "AAPL", "gamma levels belong to another ticker");
    noPageErrors(app);
  });
});

e2eTest("on a phone the chart still draws the walls without the chain panel open", async () => {
  await withApp(chartAppOptions({ viewport: PHONE }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await setStudy(card, OI_LEVELS, true);
    await closeIndicators(card);
    const { count, leaders } = await wallsDrawn(app, card);
    assert.ok(count > 0, "no walls on the phone chart");
    assertLeaders(leaders);
    assert.equal(await overflowsSideways(app.page), false);
    noPageErrors(app);
  });
});

e2eTest("switching Expirations to the selected expiry redraws walls without errors", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await setStudy(card, OI_LEVELS, true);
    await studyRow(card, OI_LEVELS).locator(`button[aria-label="${OI_LEVELS} settings"]`).click();
    const scope = card.locator(".oi-finder-indicator-settings label").filter({ has: app.page.locator('span:text-is("Expirations")') }).locator("select");
    await scope.waitFor({ timeout: 10_000 });
    assert.equal(await scope.inputValue(), "window", "default is all expirations combined (TOS script)");
    await wallsDrawn(app, card);
    await scope.selectOption("expiry");
    assert.equal(await scope.inputValue(), "expiry");
    await closeIndicators(card);
    const { count, leaders } = await wallsDrawn(app, card);
    assert.ok(count > 0, "the selected expiry drew no walls");
    assertLeaders(leaders);
    assert.equal(await card.locator("section.oi-chart-render-error").count(), 0);
    noPageErrors(app);
  });
});
