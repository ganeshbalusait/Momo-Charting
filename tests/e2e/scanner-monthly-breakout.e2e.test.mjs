// Monthly Breakout / Monthly Breakdown lists, end to end (2026-11-20).
// Ganesh: "Build in scanner dropdown? For bull and bear?" - two built-in
// lists in the WATCHLISTS picker, a chip per member saying which pattern
// broke, at what line, and whether the month has closed. Also the picker's
// list limit: with his screen (Mag7, Watchlist, 2 EMA lists, 2 of his own)
// it said "List limit reached" although the server takes two more.
// Lists and boards are fixtures (fakeReads): nothing depends on the market.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { STAMP, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const PICKER = ".momx-picker[role='dialog'][aria-label='Watchlists']";
const ROW = "[data-testid='momx-breakout-row']";

const entry = (name, count, { builtIn = true, derived = false, ...extra } = {}) => ({
  name, count, builtAt: STAMP, warming: false, builtIn, derived, ...extra,
});

function hisLists(active) {
  return {
    active,
    maxLists: 6,
    lists: [
      entry("Mag7", 10),
      entry("Watchlist", 370),
      entry("21 EMA Touch Weekly", 111, { derived: true, emaLength: 21, timeframe: "Weekly", timeframes: ["Monthly", "Weekly", "Daily"] }),
      entry("50 EMA Touch Monthly", 49, { derived: true, emaLength: 50, timeframe: "Monthly", timeframes: ["Monthly", "Weekly", "Daily"] }),
      entry("Liquid Movers", 230, { builtIn: false }),
      entry("Movers", 1, { builtIn: false }),
      entry("Monthly Breakout", 2, {
        derived: true,
        breakout: "up",
        breakouts: {
          MA: { pattern: "MIDDLE", label: "Range", direction: "up", status: "confirmed", period: "2026-10", level: 601.77, target: 863.33 },
          NVDA: { pattern: "BOTTOM", label: "Higher lows + resistance", direction: "up", status: "live", period: "2026-11", level: 160.5, target: 210.1 },
        },
      }),
      entry("Monthly Breakdown", 1, {
        derived: true,
        breakout: "down",
        breakouts: {
          BABA: { pattern: "TOP", label: "Lower highs + support", direction: "down", status: "live", period: "2026-11", level: 91.99, target: null },
        },
      }),
    ],
  };
}

// A chip is one flex row; innerText puts a line break between its parts.
const chipTexts = async (locator) => (await locator.allInnerTexts()).map((text) => text.replace(/\s+/g, " ").trim());

const reads = (active) => boardReads({ lists: () => hisLists(active) });
const storage = (active) => ({ "momx.scanner.activeList": active, "momx-auto-cards-on": "0" });

async function openPicker(page) {
  await page.locator(".momx-listbtn[aria-haspopup='true']").click();
  const picker = page.locator(PICKER);
  await picker.waitFor({ timeout: 10_000 });
  return picker;
}

const item = (picker, name) => picker.locator(".momx-picker-list li").filter({
  has: picker.page().locator(".momx-picker-name", { hasText: new RegExp("^" + name + "$") }),
});

e2eTest("both lists are in the picker as built-ins, and the computed lists no longer fill the list limit", async () => {
  await withApp({ view: VIEW, storage: storage("Mag7"), fakeReads: reads("Mag7") }, async (app) => {
    await boardReady(app.page);
    const picker = await openPicker(app.page);
    const names = await picker.locator(".momx-picker-name").allInnerTexts();
    assert.deepEqual(names.slice(-2), ["Monthly Breakout", "Monthly Breakdown"]);
    for (const name of ["Monthly Breakout", "Monthly Breakdown"]) {
      const li = item(picker, name);
      assert.equal((await li.locator(".momx-picker-builtin").textContent()).trim(), "built-in");
      assert.equal(await li.getByRole("button", { name: "Rename" }).count(), 0);
      assert.equal(await li.getByRole("button", { name: /^Delete/ }).count(), 0);
    }
    // 8 lists on screen, 4 of them his own/seeded: room for 2 more.
    const input = picker.locator("input[aria-label='New list name']");
    assert.equal(await input.isEnabled(), true, "the picker must not say the limit is reached");
    assert.notEqual(await input.getAttribute("placeholder"), "List limit reached");
    assert.doesNotMatch(await picker.locator(".momx-picker-note").innerText(), /maximum/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("picking Monthly Breakout loads its board and shows each pattern, line and month status", async () => {
  await withApp({ view: VIEW, storage: storage("Mag7"), fakeReads: reads("Mag7") }, async (app) => {
    await boardReady(app.page);
    assert.equal(await app.page.locator(ROW).count(), 0, "no pattern row on an ordinary list");
    const picker = await openPicker(app.page);
    await item(picker, "Monthly Breakout").locator(".momx-picker-pick").click();
    await picker.waitFor({ state: "detached", timeout: 5_000 });
    await app.page.locator(ROW).waitFor({ timeout: 10_000 });
    const chips = await chipTexts(app.page.locator(`${ROW} [data-testid='momx-breakout-chip']`));
    assert.equal(chips.length, 2);
    assert.match(chips[0], /^NVDA ▲ HL \+ Resistance > 160\.50 Nov 26 not closed$/);   // live month first
    assert.match(chips[1], /^MA ▲ Range > 601\.77 Oct 26 close$/);
    assert.match(await app.page.locator(`${ROW} .momx-breakout-note`).innerText(), /62% for any month/);
    assert.match(await app.page.locator(`${ROW} .momx-breakout-chip`).first().getAttribute("title"), /can still fail/);
    assert.equal(await app.page.locator("[data-testid='momx-ema-tf']").count(), 0, "no EMA dropdown here");
    assert.ok(app.reads.some((read) => read.path === "/api/momx-scanner" && /list=Monthly(\+|%20)Breakout/.test(read.search)));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Monthly Breakdown shows red chips and says it is not a put signal", async () => {
  await withApp({ view: VIEW, storage: storage("Monthly Breakdown"), fakeReads: reads("Monthly Breakdown") }, async (app) => {
    await boardReady(app.page);
    const row = app.page.locator(`${ROW}.is-down`);
    await row.waitFor({ timeout: 10_000 });
    assert.deepEqual(await chipTexts(row.locator("[data-testid='momx-breakout-chip']")), ["BABA ▼ LH + Support < 91.99 Nov 26 not closed"]);
    assert.match(await row.locator(".momx-breakout-note").innerText(), /Not a put signal/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("on a phone the pattern chips wrap on screen with nothing cut off", async () => {
  await withApp({ view: VIEW, viewport: PHONE, storage: storage("Monthly Breakout"), fakeReads: reads("Monthly Breakout") }, async (app) => {
    await boardReady(app.page);
    const row = app.page.locator(ROW);
    await row.waitFor({ timeout: 10_000 });
    for (const chip of await row.locator("[data-testid='momx-breakout-chip']").all()) {
      const box = await chip.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `chip at ${JSON.stringify(box)} is cut off`);
    }
    const note = await row.locator(".momx-breakout-note").boundingBox();
    assert.ok(note && note.x + note.width <= PHONE.width + 1, "the note is cut off");
    assert.equal(await overflowsSideways(app.page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});
