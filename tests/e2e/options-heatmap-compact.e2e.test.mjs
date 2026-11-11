// Fast Options -> Heatmap is compact (2026-11-10). Ganesh: "heatmap its big of
// the screen reduce the size. each column have big box make it compact".
// Rows were 78px and columns at least 118px; now rows are about 52px and
// columns at least 98px, with every number still fully inside its box and the
// grid scrolling sideways inside its own box on a phone (never the page).

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CHAIN_READS, STRIKES, chainReply, expiryIn, optionChain, symbolOf } from "./options-fixtures.mjs";

const HEATMAP = "section.oi-finder-strike-heatmap.is-live-dte";
// The real screen had 10 expiries and six-figure numbers, so the fixture does too:
// the shared chain fixture has an empty heatmap, and short numbers would hide clipping.
const DTES = [1, 3, 6, 8, 10, 13, 15, 17, 24, 31];
function liveHeatmap() {
  const date = expiryIn(0);
  const capturedAt = new Date().toISOString();
  const rowsFor = (side) => DTES.flatMap((dte) => STRIKES.map((strike) => ({
    date, capturedAt, strike, expiry: expiryIn(dte),
    delta: Math.min(0.98, Math.max(0.02, 0.5 - (side === "CALL" ? strike - 100 : 100 - strike) * 0.04)),
    openInterest: 91_478 + strike * 1_000, volume: 231_240 + strike * 100,
    liveOpenInterestChange: 12_345, liveVolumeChange: 23_456,
  })));
  return { days: [date], liveOnly: true, call: rowsFor("CALL"), put: rowsFor("PUT") };
}
const researchReply = (read) => {
  const { expiries, expiryExpectedMoves, tosScriptLevels } = optionChain(symbolOf(read), { dtes: DTES });
  return chainReply(read, {
    researchSection: new URLSearchParams(read.search).get("section") || "",
    expiries, expiryExpectedMoves, tosScriptLevels, dailyLiquidityHeatmap: liveHeatmap(),
  });
};
const RESEARCH = { "GET /api/oi-finder": researchReply };

async function openHeatmap(page) {
  await page.locator('[data-testid="mobile-quick-options"] .mobile-quick-options-title b').waitFor({ state: "visible", timeout: 60_000 });
  await page.locator('[data-testid="mobile-options-section-heatmap"]').click();
  await page.locator(`${HEATMAP} .oi-finder-heatmap-cell:not(:disabled)`).first().waitFor({ timeout: 60_000 });
}

const measure = (page) => page.locator(HEATMAP).evaluate((section) => {
  const grid = section.querySelector(".oi-finder-heatmap-grid");
  const columns = getComputedStyle(grid).gridTemplateColumns.split(" ").map(parseFloat);
  const rows = [...section.querySelectorAll(".oi-finder-heatmap-strike")].map((cell) => cell.getBoundingClientRect().height);
  let clipped = 0;
  const spilled = [];
  const touching = [];
  let covered = 0;
  const hit = (a, b) => a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
  for (const cell of section.querySelectorAll(".oi-finder-heatmap-cell:not(:disabled)")) {
    const box = cell.getBoundingClientRect();
    for (const text of cell.querySelectorAll("b, em, small, strong")) {
      const r = text.getBoundingClientRect();
      if (r.width && (r.left < box.left - 0.5 || r.right > box.right + 0.5 || r.top < box.top - 0.5 || r.bottom > box.bottom + 0.5)) { clipped += 1; if (spilled.length < 4) spilled.push(`${text.tagName}.${text.className}: "${text.textContent}" ${Math.round(r.width)}x${Math.round(r.height)} in ${Math.round(box.width)}x${Math.round(box.height)}`); }
    }
    const pill = cell.querySelector(".oi-finder-change-first-metric > span:first-child, .oi-finder-combined-single");
    for (const tag of cell.querySelectorAll(".oi-level-strength-badges, .oi-finder-exp-strike-marker")) {
      if (pill && hit(tag.getBoundingClientRect(), pill.getBoundingClientRect())) { covered += 1; if (touching.length < 3) { const a = tag.getBoundingClientRect(); const b = pill.getBoundingClientRect(); touching.push(`"${tag.textContent}" [${Math.round(a.left - box.left)},${Math.round(a.top - box.top)} ${Math.round(a.width)}x${Math.round(a.height)}] over "${pill.textContent}" [${Math.round(b.left - box.left)},${Math.round(b.top - box.top)} ${Math.round(b.width)}x${Math.round(b.height)}] in ${Math.round(box.width)}x${Math.round(box.height)}`); } }
    }
  }
  return { strikeColumn: columns[0], dataColumns: columns.slice(1), tallestRow: Math.max(...rows), clipped, covered, spilled, touching };
});

for (const [label, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  e2eTest(`on ${label} the heatmap rows are short, columns readable, and no number is cut off or covered`, async () => {
    await withApp({ view: "Quick Options", viewport, fakeReads: { ...CHAIN_READS, ...RESEARCH } }, async (app) => {
      await openHeatmap(app.page);
      for (const total of [false, true]) {
        if (total) await app.page.locator(`${HEATMAP} button`, { hasText: /^Total$/ }).click();
        const m = await measure(app.page);
        assert.equal(m.strikeColumn, 64, "strike column");
        assert.equal(m.dataColumns.length, DTES.length, "one column per expiry");
        assert.ok(m.dataColumns.every((w) => w >= 97.5), `columns too narrow: ${m.dataColumns}`);
        assert.ok(m.tallestRow <= 56, `rows must stay compact (was 78px), got ${m.tallestRow}`);
        assert.equal(m.clipped, 0, `a number spills out of its box: ${m.spilled.join(" | ")}`);
        assert.equal(m.covered, 0, `${total ? "Total" : "Change"} view: an EXP / OI tag covers the main number: ${m.touching.join(" | ")}`);
      }
      assert.equal(await overflowsSideways(app.page), false, "the page itself must not scroll sideways");
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
