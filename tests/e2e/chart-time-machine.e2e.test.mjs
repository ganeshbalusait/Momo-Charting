// Chart Time Machine (2026-11-15), end to end in the real app.
//
// On the AAPL chart: ROI on, then Odds (toolbar on a desktop, the Odds
// button on the chart on a phone, or the card's ROI / Odds / Both switch) shows the odds of target vs stop, the
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

const LINES = '[data-testid="chart-project-roi-lines"]';
async function drawn(chart) {
  return String(await chart.locator(LINES).getAttribute("data-lines")).split("|").map((item) => {
    const [key, price, ...rest] = item.split(":");
    return { key, price: Number(price), title: rest.join(":") };
  });
}
const pick = (roi, label) => roi.locator(`.chart-project-roi-show button`, { hasText: new RegExp(`^${label}$`) });
async function waitTitles(page, chart, test) {
  for (let i = 0; i < 100; i += 1) {
    if (await chart.locator(LINES).count() && test(await drawn(chart))) return drawn(chart);
    await page.waitForTimeout(100);
  }
  return drawn(chart);
}
const savedShow = (page) => page.evaluate(() => JSON.parse(localStorage.getItem("agx.chartProjectRoi.v1") || "{}").AAPL?.show);

e2eTest("desktop: Odds on the toolbar turns the odds on; the card switch picks ROI, Odds or Both on the chart", async () => {
  const asked = [];
  await withApp(options(asked), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const roi = await roiOn(chart);
    // ROI only by default: no odds asked, none on the lines.
    assert.equal(await roi.locator(ODDS).count(), 0);
    assert.equal(asked.length, 0, "no replay asked while the chart shows ROI only");
    assert.equal(await pick(roi, "ROI").getAttribute("aria-pressed"), "true");
    await chart.locator('button.oi-finder-chart-roi-side[aria-pressed="true"]').click(); // close the card
    await roi.waitFor({ state: "hidden", timeout: 10_000 });
    const oddsButton = chart.locator("button.oi-finder-chart-roi-odds");
    await oddsButton.click();
    await roi.waitFor({ state: "visible", timeout: 10_000 });
    const odds = await checkOdds(roi, asked);
    assert.equal(await oddsButton.getAttribute("aria-pressed"), "true");
    assert.equal(await pick(roi, "Both").getAttribute("aria-pressed"), "true");
    // Both: the ROI boxes, with the odds on Entry / Target / Stop only.
    let lines = await waitTitles(page, chart, (all) => all.some((line) => / · 41%$/.test(line.title)));
    assert.ok(lines.some((line) => line.key === "t1"), "ROI targets still drawn");
    assert.match(lines.find((line) => line.key === "target").title, /^T\d .* · 41%$/);
    assert.match(lines.find((line) => line.key === "stop").title, /−50% .* · 28%$/);
    assert.match(lines.find((line) => line.key === "entry").title, / · 31% neither$/);
    // Odds: just three lines, each only its price and odds.
    await pick(roi, "Odds").click();
    lines = await waitTitles(page, chart, (all) => all.length === 3);
    assert.deepEqual(lines.map((line) => line.key), ["entry", "target", "stop"]);
    assert.match(lines[0].title, /^Entry \d+\.\d\d  [\d.]+[CP]  \$\d+\.\d\d · 31% neither$/);
    assert.match(lines[1].title, /^Target \d+\.\d\d  [+−]\d+%  \$\d+\.\d\d · 41% odds$/);
    assert.match(lines[2].title, /^Stop \d+\.\d\d  −50%  \$\d+\.\d\d · 28% odds$/);
    assert.equal(await savedShow(page), "odds");
    // Run again asks once more.
    const before = asked.length;
    await odds.locator(".chart-time-machine-rerun").click();
    for (let i = 0; i < 50 && asked.length <= before; i += 1) await page.waitForTimeout(100);
    assert.ok(asked.length > before, "Run again asked the server again");
    // ROI: the ROI boxes without odds, the odds section goes, the toolbar Odds is off.
    await pick(roi, "ROI").click();
    await roi.locator(ODDS).waitFor({ state: "detached", timeout: 10_000 });
    lines = await waitTitles(page, chart, (all) => all.length > 3);
    assert.ok(lines.every((line) => !/ · \d+%( neither)?$/.test(line.title)), "no odds on the ROI boxes");
    assert.equal(await oddsButton.getAttribute("aria-pressed"), "false");
    noPageErrors(app);
  });
});

e2eTest("phone: the card switch shows the odds without sideways scroll", async () => {
  const asked = [];
  await withApp(options(asked, { viewport: PHONE }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    assert.equal(await chart.locator("button.oi-finder-chart-roi-odds").isVisible(), false, "toolbar Odds is desktop only");
    const roi = await roiOn(chart);
    await pick(roi, "Both").click();
    await checkOdds(roi, asked);
    assert.equal(await overflowsSideways(page), false);
    noPageErrors(app);
  });
});

// 2026-11-15 "I don't see in phone": with ROI already on (a saved plan), the
// phone toolbar shows only ROI and C, and nothing there opens the card. The
// chart itself has an Odds button on a phone; it opens the card with the odds.
e2eTest("phone, ROI already on: the Odds button on the chart opens the odds; Odds only stays after closing", async () => {
  const asked = [];
  const plan = { AAPL: { side: "CALL", both: false, contracts: 1, entrySpot: 0, createdAt: Date.now() - 60_000 } };
  await withApp(options(asked, { viewport: PHONE, storage: { "agx.chartProjectRoi.v1": JSON.stringify(plan) } }), async (app) => {
    const { page } = app;
    const chart = await chartCard(page, "AAPL");
    const fab = chart.locator("button.chart-project-roi-odds-fab");
    await fab.waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await chart.locator(ROI).isVisible(), false, "the card starts closed");
    await fab.click();
    const roi = chart.locator(ROI);
    await roi.locator(".chart-project-roi-title").waitFor({ state: "visible", timeout: 120_000 });
    await checkOdds(roi, asked);
    assert.equal(await fab.count(), 0, "the button goes while the card is open");
    await pick(roi, "Odds").click();
    await roi.locator(".chart-project-roi-close").click();
    await fab.waitFor({ state: "visible", timeout: 10_000 });
    const lines = await waitTitles(page, chart, (all) => all.length === 3 && / · 41% odds$/.test(all[1].title));
    assert.deepEqual(lines.map((line) => line.title.replace(/\d+\.\d\d/, "#").replace(/  [+−]\d+%/, "  %").replace(/  [\d.]+[CP]/, "  K").replace(/  \$\d+\.\d\d/, "  $")), ["Entry #  K  $ · 31% neither", "Target #  %  $ · 41% odds", "Stop #  %  $ · 28% odds"]);
    assert.equal(await overflowsSideways(page), false);
    noPageErrors(app);
  });
});

e2eTest("desktop: no floating Odds button (the toolbar has one)", async () => {
  const plan = { AAPL: { side: "CALL", both: false, contracts: 1, entrySpot: 0, createdAt: Date.now() - 60_000 } };
  await withApp(options([], { storage: { "agx.chartProjectRoi.v1": JSON.stringify(plan) } }), async (app) => {
    const chart = await chartCard(app.page, "AAPL");
    await chart.locator("button.oi-finder-chart-roi-odds").waitFor({ state: "visible", timeout: 60_000 });
    assert.equal(await chart.locator("button.chart-project-roi-odds-fab").isVisible(), false);
  });
});
