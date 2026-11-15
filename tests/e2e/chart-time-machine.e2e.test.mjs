// Chart Time Machine (2026-11-15), end to end in the real app.
//
// On the AAPL chart: ROI on, then Odds (toolbar on a desktop, the card's
// "Odds from here" button on a phone) shows the odds of target vs stop, the
// fan of replayed paths, the same trade on past days and the verdict. The
// replay answer (/api/time-machine) is faked so the numbers are fixed; the
// request it gets must carry the ROI card's own target and stop. The chart
// and chain are the real ones (read only).
import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, noPageErrors } from "./charts-studies-helpers.mjs";

const ROI = '[data-testid="chart-project-roi"]';
const ODDS = '[data-testid="chart-time-machine"]';

const answer = (read) => {
  const query = new URLSearchParams(read.search);
  const spot = Number(query.get("spot"));
  const target = Number(query.get("target"));
  const stop = Number(query.get("stop"));
  const steps = 6;
  const line = (end) => Array.from({ length: steps }, (_, i) => Math.round((spot + ((end - spot) * (i + 1)) / steps) * 100) / 100);
  return {
    json: {
      ok: true, symbol: query.get("symbol"), side: query.get("side"), spot, target, stop, mode: "today",
      startLabel: "10:15", flatLabel: "3:55 PM", steps, paths: 10000, sessions: 5, seed: 1,
      odds: { target: 41.2, stop: 27.5, neither: 31.3 },
      bands: { 10: line(stop), 25: line((spot + stop) / 2), 50: line(spot), 75: line((spot + target) / 2), 90: line(target) },
      sample: [{ outcome: "target", closes: line(target) }, { outcome: "stop", closes: line(stop).slice(0, 4) }],
      history: ["11-09", "11-10", "11-11", "11-12", "11-13"].map((day, i) => ({ day: `2026-${day}`, outcome: ["target", "stop", "target", "neither", "target"][i] })),
      historyCounts: { target: 3, stop: 1, neither: 1 },
      neitherMedianClose: spot, verdict: "Clear edge to the target",
    },
  };
};

function options(asked, extra = {}) {
  return chartAppOptions({
    ...extra,
    fakeReads: { "GET /api/time-machine": (read) => { asked.push(read.search); return answer(read); } },
  });
}

async function roiOn(chart) {
  const roiButton = chart.locator("button.oi-finder-chart-roi");
  if ((await roiButton.getAttribute("aria-pressed")) !== "true") await roiButton.click();
  const roi = chart.locator(ROI);
  await roi.waitFor({ state: "visible", timeout: 30_000 });
  await roi.locator(".chart-project-roi-title").waitFor({ state: "visible", timeout: 120_000 });
  return roi;
}

async function checkOdds(roi, asked) {
  const odds = roi.locator(ODDS);
  await odds.locator(".chart-time-machine-verdict").waitFor({ state: "visible", timeout: 30_000 });
  const rows = await odds.locator(".chart-time-machine-odds [role=listitem]").allInnerTexts();
  assert.deepEqual(rows.map((row) => row.replace(/\s+/g, " ").trim()), ["Target hit first 41%", "Stop hit first 28%", "Neither by 3:55 PM 31%"]);
  assert.equal(await odds.locator(".chart-time-machine-days i").count(), 5);
  assert.match(await odds.locator(".chart-time-machine-counts").innerText(), /Target 3 · Stop 1 · Neither 1/);
  assert.match(await odds.locator(".chart-time-machine-verdict").innerText(), /Clear edge to the target\. Odds only/);
  assert.equal(await odds.locator(".chart-time-machine-fan path.is-path").count(), 2);
  // The question carried the card's own target and stop.
  const query = new URLSearchParams(asked.at(-1));
  const target = Number(await roi.locator('input[aria-label="Target stock price input"]').inputValue());
  const stop = Number(await roi.locator('input[aria-label="Stop stock price"]').inputValue());
  assert.equal(query.get("symbol"), "AAPL");
  assert.equal(Number(query.get("target")), target);
  assert.equal(Number(query.get("stop")), stop);
  return odds;
}

e2eTest("desktop: Odds on the chart toolbar opens the ROI card with the odds from here", async () => {
  const asked = [];
  await withApp(options(asked), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    await roiOn(chart);
    const roi = chart.locator(ROI);
    await chart.locator('button.oi-finder-chart-roi-side[aria-pressed="true"]').click(); // close the card
    await roi.waitFor({ state: "hidden", timeout: 10_000 });
    const oddsButton = chart.locator("button.oi-finder-chart-roi-odds");
    await oddsButton.click();
    await roi.waitFor({ state: "visible", timeout: 10_000 });
    const odds = await checkOdds(roi, asked);
    assert.equal(await oddsButton.getAttribute("aria-pressed"), "true");
    // Run again asks once more; Hide odds closes the section, the card stays.
    const before = asked.length;
    await odds.locator(".chart-time-machine-rerun").click();
    for (let i = 0; i < 50 && asked.length <= before; i += 1) await page.waitForTimeout(100);
    assert.ok(asked.length > before, "Run again asked the server again");
    await odds.locator(".chart-time-machine-verdict").waitFor({ state: "visible" });
    await roi.locator(".chart-project-roi-odds-toggle").click();
    await roi.locator(ODDS).waitFor({ state: "detached", timeout: 10_000 });
    assert.ok(await roi.isVisible());
    noPageErrors(app);
  });
});

e2eTest("phone: the ROI card's Odds from here button shows the odds without sideways scroll", async () => {
  const asked = [];
  await withApp(options(asked, { viewport: PHONE }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    assert.equal(await chart.locator("button.oi-finder-chart-roi-odds").isVisible(), false, "toolbar Odds is desktop only");
    const roi = await roiOn(chart);
    await roi.locator(".chart-project-roi-odds-toggle").click();
    await checkOdds(roi, asked);
    assert.equal(await overflowsSideways(page), false);
    noPageErrors(app);
  });
});
