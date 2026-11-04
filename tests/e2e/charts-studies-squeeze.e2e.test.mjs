// Charts & OI -> squeeze studies, end to end (2026-11-04 coverage pass,
// feature charts-squeeze). The fire bubbles and histogram bars are canvas-drawn,
// so the spec reads the DOM the lower panes carry: the 4/10 row labels and the
// Squeeze Momentum value. Live AAPL candles; only the study-profile save is faked.
import assert from "node:assert/strict";

import { e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, closeIndicators, noPageErrors, openIndicators, setStudy, studyRow } from "./charts-studies-helpers.mjs";

const SQUEEZE_410 = "MTF Squeeze 4/10 (Lower 2)";
const MOMENTUM = "Squeeze Momentum (Lower)";
const ROW_LABELS = new Set(["5M", "15M", "30M", "1H↻", "2H↻", "4H", "D", "W"]);

e2eTest("MTF Squeeze 4/10 opens its own lower pane with one coloured label per squeeze row", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await setStudy(card, SQUEEZE_410, true);
    await closeIndicators(card);
    const pane = card.locator(".oi-finder-squeeze410-study");
    await pane.waitFor({ state: "visible", timeout: 60_000 });
    assert.match(await pane.locator(".oi-finder-squeeze410-study-title").innerText(), /shared_MTF_Squeeze410/);
    const labels = pane.locator(".oi-finder-squeeze410-labels span");
    await labels.first().waitFor({ timeout: 30_000 });
    const rows = await labels.evaluateAll((spans) => spans.map((span) => ({
      text: span.textContent.trim(), className: span.className, background: span.style.background || span.style.backgroundColor,
    })));
    assert.ok(rows.length >= 1 && rows.length <= 8, `${rows.length} squeeze rows`);
    for (const row of rows) {
      assert.ok(ROW_LABELS.has(row.text), `unexpected squeeze row "${row.text}"`);
      assert.match(row.className, /^is-[a-z]+/, `row ${row.text} has no tone class`);
      assert.ok(row.background, `row ${row.text} has no background colour`);
    }
    assert.equal(new Set(rows.map((row) => row.text)).size, rows.length, "a squeeze row is duplicated");
    assert.equal(await card.locator("section.oi-chart-render-error").count(), 0);
    noPageErrors(app);
  });
});

e2eTest("Squeeze Momentum shows the TTM value in its lower-pane title", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await setStudy(card, MOMENTUM, true);
    await closeIndicators(card);
    const title = card.locator(".oi-finder-lower-study-title").filter({ hasText: "shared_SqueezeChain24" });
    await title.waitFor({ state: "visible", timeout: 60_000 });
    const value = title.locator("b");
    await value.waitFor({ state: "visible", timeout: 30_000 });
    assert.match((await value.innerText()).trim(), /^-?\d+\.\d{4}$/, "momentum value is not a 4-decimal number");
    assert.match(await title.innerText(), /LINE_VS_POINTS/);
    noPageErrors(app);
  });
});

e2eTest("the 4/10 AP2 aggregation choice survives a reload", async () => {
  await withApp(chartAppOptions(), async (app) => {
    let card = await chartCard(app.page, "AAPL");
    await setStudy(card, SQUEEZE_410, true);
    await studyRow(card, SQUEEZE_410).locator(`button[aria-label="${SQUEEZE_410} settings"]`).click();
    const ap2 = () => card.locator(".oi-finder-indicator-settings label").filter({ has: app.page.locator('span:text-is("AP2")') }).locator("select");
    await ap2().waitFor({ timeout: 10_000 });
    assert.equal(await ap2().inputValue(), "15", "AP2 default is 15m (TOS script)");
    await ap2().selectOption("30");
    assert.equal(await ap2().inputValue(), "30");

    await app.page.reload({ waitUntil: "domcontentloaded" });
    card = await chartCard(app.page, "AAPL");
    await openIndicators(card);
    assert.equal(await studyRow(card, SQUEEZE_410).locator('input[type="checkbox"]').isChecked(), true);
    await studyRow(card, SQUEEZE_410).locator(`button[aria-label="${SQUEEZE_410} settings"]`).click();
    await ap2().waitFor({ timeout: 10_000 });
    assert.equal(await ap2().inputValue(), "30", "the AP2 aggregation reverted after reload");
    const profileSaves = app.writes.filter((write) => write.path === "/api/chart-indicator-profiles");
    assert.ok(profileSaves.some((write) => /"mtfSqueeze410Ap2":30/.test(write.body)), "the AP2 change was not saved to the (faked) profile");
    noPageErrors(app);
  });
});
