// Bot page -> "Scanner → BOT capture" (2026-11-24, loss audit bug 2): a signal whose
// option had no buyers (bid $0) is not bought; it is counted as not entered with a
// plain reason that says what really happened, and the entered trades with no
// 0.20-delta option (the contract the paper accounts buy) are counted per cause:
// no strike / no quote / no buyers / no sellers / quote not usable.
// Reads are canned; nothing is written.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const LEARNING = "section.bot-jevbook-note[aria-label='Post-trade learning team']";
// The BOT's own words (momx/option_track.py quote_gap_final / the daily cap reason).
const NO_BUYERS_ONCE = "No buyers when it was checked (bid $0): no trade taken";
const NO_BUYERS_WINDOW = "No buyers for this option (bid $0) in the 10 minutes after the signal: no trade taken";
const NO_QUOTE = "No quote for this option when it was checked: no trade taken";
const CAP = "Daily signal cap for this rule";

const LEG_LINES = [
  "12 entered trades have no 0.20-delta option with buyers (bid $0), so the paper accounts skip them.",
  "1 entered trade has no 0.20-delta option with sellers (no ask), so the paper accounts skip it.",
  "5 entered trades have a 0.20-delta option whose quote is not usable (buyers far below sellers, or crossed), so the paper accounts skip them.",
  "2 entered trades had no quote for the 0.20-delta option, so the paper accounts skip them.",
  "3 entered trades have no strike under 0.20 delta on that expiry, so the paper accounts skip them.",
];

const CAPTURE = {
  bull: { day: "2026-11-24", signals: 45, entered: 30, pending: 1, notEntered: 14,
          entryLeg: { none: 3, missing: 2, buyers: 12, sellers: 1, unusable: 5 },
          reasons: { [CAP]: 2, [NO_BUYERS_ONCE]: 7, [NO_BUYERS_WINDOW]: 4, [NO_QUOTE]: 1 }, examples: [] },
  // The bear book has trades with no 0.20-delta option, but NOT ONE of them for a $0 bid.
  bear: { day: "2026-11-24", signals: 5, entered: 5, pending: 0, notEntered: 0,
          entryLeg: { none: 1, missing: 0, buyers: 0, sellers: 0, unusable: 0 }, reasons: {}, examples: [] },
};

function canned(capture) {
  return {
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: { ...TEAM, capture } },
    [AI_READ]: { json: AI_REPORT },
    [MIRROR_READ]: { json: MIRRORS },
  };
}

async function openCapture(app) {
  const card = app.page.locator(LEARNING).first();
  await card.waitFor({ timeout: 60_000 });
  const capture = card.locator("details", { has: app.page.locator("summary", { hasText: "Scanner → BOT capture" }) });
  await capture.locator("summary").click();
  return capture;
}

async function texts(locator) {
  return (await locator.allInnerTexts()).map((s) => s.trim());
}

e2eTest("the capture list gives each reason its own line and count, in plain words", async () => {
  await withApp({ view: "Bot", fakeReads: canned(CAPTURE) }, async (app) => {
    const capture = await openCapture(app);
    // Biggest group first; "checked once" and "re-checked for 10 minutes" are separate lines.
    assert.deepEqual(await texts(capture.locator("[data-testid='bot-capture-reason']")),
      [`7 · ${NO_BUYERS_ONCE}`, `4 · ${NO_BUYERS_WINDOW}`, `2 · ${CAP}`, `1 · ${NO_QUOTE}`]);
    assert.match(await capture.innerText(), /BULL · 2026-11-24: 45 recorded signals · 30 entered · 1 waiting for quotes · 14 not entered\./);
    const legs = await texts(capture.locator("[data-testid='bot-capture-entry-leg']"));
    // Five bull lines, then the bear book's single "no strike" line.
    assert.deepEqual(legs, [...LEG_LINES, "1 entered trade has no strike under 0.20 delta on that expiry, so the paper accounts skip it."]);
    // "bid $0" is said only for the 12 trades whose bid really was $0.
    assert.deepEqual(legs.filter((line) => line.includes("bid $0")), [LEG_LINES[0]]);
    assert.deepEqual(await texts(capture.locator("[data-testid='bot-capture-entry-leg'][data-kind='buyers']")), [LEG_LINES[0]]);
    assert.match(await capture.innerText(), /An option with no buyers \(bid \$0\), no sellers or no quote is never bought/);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a server that does not send the new counts yet still shows the capture list", async () => {
  const older = { bull: { day: "2026-11-24", signals: 3, entered: 2, pending: 0, notEntered: 1, reasons: { [CAP]: 1 } } };
  await withApp({ view: "Bot", fakeReads: canned(older) }, async (app) => {
    const capture = await openCapture(app);
    assert.deepEqual(await texts(capture.locator("[data-testid='bot-capture-reason']")), [`1 · ${CAP}`]);
    assert.equal(await capture.locator("[data-testid='bot-capture-entry-leg']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: every reason line wraps inside the screen", async () => {
  await withApp({ view: "Bot", viewport: PHONE, fakeReads: canned(CAPTURE) }, async (app) => {
    const capture = await openCapture(app);
    const reason = capture.locator("[data-testid='bot-capture-reason']").first();
    await reason.scrollIntoViewIfNeeded();
    assert.equal((await reason.innerText()).trim(), `7 · ${NO_BUYERS_ONCE}`);
    const width = app.page.viewportSize().width;
    const lines = capture.locator("[data-testid='bot-capture-reason'], [data-testid='bot-capture-entry-leg']");
    assert.equal(await lines.count(), 4 + 6);
    for (let i = 0; i < 10; i += 1) {
      const box = await lines.nth(i).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= width + 1, `line ${i} cut off: ${JSON.stringify(box)} in ${width}`);
    }
    assert.deepEqual(await texts(capture.locator("[data-testid='bot-capture-entry-leg']")),
      [...LEG_LINES, "1 entered trade has no strike under 0.20 delta on that expiry, so the paper accounts skip it."]);
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.writes, []);
    assert.deepEqual(app.pageErrors, []);
  });
});
