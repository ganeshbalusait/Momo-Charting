// Charts & OI: MTF CALL/PUT labels and D->M signals (2026-11-04 coverage).
// The bubbles and arrows are drawn on the canvas (TosNativeChartPrimitive), so
// this checks what the DOM can prove: the study ladder finishes, "Loading
// indicators…" clears, the MTF Cloud Label lower pane shows its labels, a D->M
// per-timeframe choice survives a reload, and the removed D->M strip stays gone.
// Exact bubble placement still needs a TOS screenshot comparison (manual).

import assert from "node:assert/strict";

import { e2eTest } from "./harness.mjs";
import {
  CARD, card, cardData, openIndicators, profilePosts, studyCheckbox, studyRow, waitForTape, withCharts,
} from "./charts-core.helpers.mjs";

const STUDY_STAGE_MAX = 20; // OI_CHART_STUDY_STAGE_MAX in App.jsx

e2eTest("with the 4x8 and 9x20 signals on, the indicator ladder finishes and the pending note clears", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    for (const label of ["Call & Put Signals — 4×8", "Call & Put Signals — 9×20"]) {
      const box = studyCheckbox(popover, label);
      if (!(await box.isChecked())) await box.click();
      assert.equal(await box.isChecked(), true, label);
    }
    await popover.getByRole("button", { name: "Close indicators editor" }).click();
    await page.waitForFunction(({ selector, max }) => {
      const node = document.querySelector(selector);
      return node && Number(node.dataset.studyStage) >= max
        && !node.querySelector(".oi-finder-chart-ohlc small.is-history-pending");
    }, { selector: CARD, max: STUDY_STAGE_MAX }, { timeout: 120_000 });
    const data = await cardData(page);
    assert.ok(Number(data.studyRuns) > 0, "no study ever ran");
    // The persistent D->M strip was removed on the trader's request (c82f153).
    assert.equal(await page.locator(".oi-finder-dtom-status").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("MTF Cloud Label (Lower) draws its timeframe labels in the lower pane", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const popover = await openIndicators(page);
    const box = studyCheckbox(popover, "MTF Cloud Label (Lower)");
    if (!(await box.isChecked())) await box.click();
    await popover.getByRole("button", { name: "Close indicators editor" }).click();
    const pane = card(page).locator(".oi-finder-mtf-cloud-labels");
    await pane.locator("span").first().waitFor({ timeout: 120_000 });
    const labels = (await pane.locator("span").allInnerTexts()).map((text) => text.trim()).filter(Boolean);
    // TOS shared_MTF_CloudLabel25: 5M..W, in order; a timeframe without
    // enough candles for EMA 20 is left out, never mislabelled.
    const ORDER = ["5M", "15M", "30M", "1H", "2H", "4H", "D", "W"];
    assert.ok(labels.length >= 4, `only ${labels.length} cloud labels: ${labels.join(",")}`);
    assert.deepEqual(labels, ORDER.filter((label) => labels.includes(label)), `labels out of order or unknown: ${labels.join(",")}`);
    for (const span of await pane.locator("span").all()) {
      assert.match(await span.getAttribute("class"), /\bis-(bull|bear)\b/);
    }
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("unticking the 4x8 D->M CALL for D survives a reload", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    const dCall = async (popover) => {
      await popover.getByRole("button", { name: "4×8 D→M Signals settings", exact: true }).click();
      const day = studyRow(popover, "4×8 D→M Signals").locator(".oi-finder-indicator-settings .oi-finder-mtf-setting")
        .filter({ has: page.locator("b", { hasText: /^D$/ }) });
      return day.locator("label", { hasText: /^CALL$/ }).locator('input[type="checkbox"]');
    };
    let popover = await openIndicators(page);
    let call = await dCall(popover);
    if (!(await call.isChecked())) {
      await call.click();
      await page.waitForTimeout(400);
    }
    const before = profilePosts(app).length;
    await call.click();
    await page.waitForTimeout(600);
    assert.equal(await call.isChecked(), false);
    assert.equal(profilePosts(app).length, before + 1, "the D->M choice was not saved");
    assert.equal(profilePosts(app).at(-1).profile.indicatorOptions.ganesh48ShowCallD, false);
    await page.reload({ waitUntil: "domcontentloaded" });
    await waitForTape(page, { symbol: "AAPL" });
    popover = await openIndicators(page);
    call = await dCall(popover);
    assert.equal(await call.isChecked(), false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
