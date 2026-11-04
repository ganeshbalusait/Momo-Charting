// Charts & OI -> the scanner grade on the chart, end to end (2026-11-04 coverage
// pass, feature charts-grade-markers). The OHLC strip always shows the scanner's
// current letter or a muted dash - never blank (the trader read a blank GOOGL
// header as "broken" on his phone, twice). The A+/A step markers themselves are
// canvas-drawn; their on/off switch is what the DOM can prove.
import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";
import { chartAppOptions, chartCard, closeIndicators, noPageErrors, openIndicators, setStudy, studyRow } from "./charts-studies-helpers.mjs";

const GRADE_TAPE = "GET /api/momx-scanner/grade-tape";
const STUDY = "MomX Grade (A+/A)";

const tapeWith = (letter) => (read) => (/days=1(&|$)/.test(read.search)
  ? { json: { symbol: "AAPL", entries: letter ? [{ t: "2026-11-04T10:05:00-05:00", letter: "B" }, { t: "2026-11-04T10:10:00-05:00", letter }] : [] } }
  : { json: { symbol: "AAPL", entries: [] } });

async function gradeBadge(card) {
  const badge = card.locator(".oi-finder-chart-ohlc span.oi-chart-grade");
  await badge.waitFor({ state: "visible", timeout: 60_000 });
  return badge;
}

e2eTest("the live grade badge on AAPL is a letter or a muted dash, never blank", async () => {
  await withApp(chartAppOptions(), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const badge = await gradeBadge(card);
    // Give the once-a-minute grade poll its first answer.
    const asked = () => app.reads.some((read) => read.path === "/api/momx-scanner/grade-tape" && /days=1/.test(read.search));
    for (let waited = 0; !asked() && waited < 30_000; waited += 500) await app.page.waitForTimeout(500);
    await app.page.waitForTimeout(3_000);
    const text = (await badge.innerText()).trim();
    const className = await badge.getAttribute("class");
    assert.ok(text.length > 0, "the grade badge rendered empty");
    if (text === "–") assert.match(className, /\bis-none\b/);
    else assert.match(text, /^(A\+|A|B)$/, `unexpected grade "${text}"`);
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner/grade-tape" && /days=1/.test(read.search)));
    noPageErrors(app);
  });
});

e2eTest("an A+ on the scanner's newest grade sample shows as a green A+ badge", async () => {
  await withApp(chartAppOptions({ fakeReads: { [GRADE_TAPE]: tapeWith("A+") } }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    await gradeBadge(card);
    const aplus = card.locator(".oi-finder-chart-ohlc span.oi-chart-grade.is-aplus");
    await aplus.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal((await aplus.innerText()).trim(), "A+");
    assert.match(await aplus.getAttribute("title"), /Scanner setup grade right now: A\+/);
    noPageErrors(app);
  });
});

e2eTest("a scanner that is down or has no sample shows the dash, not an error", async () => {
  for (const reply of [{ status: 503, json: { error: "worker down" } }, { json: { warming: true, rows: [] } }, tapeWith(null)]) {
    await withApp(chartAppOptions({ fakeReads: { [GRADE_TAPE]: reply } }), async (app) => {
      const card = await chartCard(app.page, "AAPL");
      const none = card.locator(".oi-finder-chart-ohlc span.oi-chart-grade.is-none");
      await none.waitFor({ state: "visible", timeout: 30_000 });
      assert.equal((await none.innerText()).trim(), "–");
      noPageErrors(app);
    });
  }
});

e2eTest("unticking MomX Grade (A+/A) stays off after a reload", async () => {
  await withApp(chartAppOptions(), async (app) => {
    let card = await chartCard(app.page, "AAPL");
    await setStudy(card, STUDY, false);
    await closeIndicators(card);
    await app.page.reload({ waitUntil: "domcontentloaded" });
    card = await chartCard(app.page, "AAPL");
    await openIndicators(card);
    assert.equal(await studyRow(card, STUDY).locator('input[type="checkbox"]').isChecked(), false);
    const saved = JSON.parse(await app.page.evaluate(() => window.localStorage.getItem("oiFinderChartIndicators")));
    assert.equal(saved.momxGradeMarkers, false);
    noPageErrors(app);
  });
});

e2eTest("on a phone the grade badge is on the chart strip and inside the screen", async () => {
  await withApp(chartAppOptions({ viewport: PHONE, fakeReads: { [GRADE_TAPE]: tapeWith("A") } }), async (app) => {
    const card = await chartCard(app.page, "AAPL");
    const badge = card.locator(".oi-finder-chart-ohlc span.oi-chart-grade.is-a");
    await badge.waitFor({ state: "visible", timeout: 30_000 });
    assert.equal((await badge.innerText()).trim(), "A");
    const box = await badge.boundingBox();
    assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width, `grade badge at ${JSON.stringify(box)} is cut off`);
    noPageErrors(app);
  });
});
