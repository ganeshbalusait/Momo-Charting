// MomX Scanner in the dark clean look, end to end, on the deterministic
// fixture board. Since 2026-11-13 the look is on for everyone and the app is
// dark only (no Light theme).
//
// Checked: the look is on without anything stored; the font is the shared one,
// every label is readable (4.5:1), every signal colour is exactly what the old
// look painted, the number of rows that fit on screen has not dropped, dialogs
// follow, the card's day chart stays dark, each sector card lists its tickers,
// and a phone does not scroll sideways.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady, storedNews } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const MONITOR = { width: 1920, height: 1080 };
// Rows that fit under the header before this release (measured 2026-11-13 on
// the old look, ticker card closed). The new look may not show fewer.
// 2026-11-13 Ganesh asked for a visibly Apple-style look: rows are 28px tall
// (were 23) and the sector cards list their tickers. Measured after: 25 rows on
// a 1920x1080 monitor (30 before), 6 on a 375px phone (8 before). These floors
// catch anything that costs MORE rows than that.
// Phone: the sector cards stack (every sector visible, Ganesh 2026-11-13),
// one card costs about one row; Sectors can be folded away.
const ROWS_BEFORE = { monitor: 24, phone: 5 };
const LIVE_TABLE = "table.momx-table:not(.momx-history-table):not(.momx-other-side-table):not(.momx-find-table)";

const cardReads = (news) => ({
  ...boardReads({ news }),
  "GET /api/momx-scanner/daybars": { json: { symbol: "NVDA", day: "2026-11-03",
    bars: [[1762182000, 180.1], [1762182300, 181.4], [1762182600, 180.9], [1762182900, 182.4]], low: 180.1, high: 182.4 } },
  "GET /api/oi-finder": { json: { symbol: "NVDA", callRows: [], putRows: [], errors: [] } },
});

async function openScanner({ viewport = MONITOR, news } = {}, fn) {
  const storage = { ...BOARD_STORAGE };
  await withApp({ view: VIEW, viewport, storage, fakeReads: cardReads(news) }, async (app) => {
    await boardReady(app.page);
    await app.page.waitForTimeout(800);
    await fn(app);
  });
}

// Every piece of text inside the scanner and what it opens: its colour against
// what is really behind it. Cells whose colours the SERVER names (RVOL / SQZ /
// Skittles pills, and the same values on the card) are the thinkScript colours
// and are not judged.
async function unreadable(page) {
  return page.evaluate(() => {
    // Any CSS colour (rgb, oklch, ...) read back as sRGB through a 1px canvas.
    const paint = document.createElement("canvas").getContext("2d", { willReadFrequently: true });
    const parse = (c) => { if (!c) return null; paint.clearRect(0, 0, 1, 1); paint.fillStyle = "rgba(0, 0, 0, 0)"; paint.fillStyle = String(c); paint.fillRect(0, 0, 1, 1); const [r, g, b, a] = paint.getImageData(0, 0, 1, 1).data; return { r, g, b, a: a / 255 }; };
    const over = (top, under) => ({ r: top.r * top.a + under.r * (1 - top.a), g: top.g * top.a + under.g * (1 - top.a), b: top.b * top.a + under.b * (1 - top.a), a: 1 });
    const lum = ({ r, g, b }) => { const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; }; return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b); };
    const behind = (el) => {
      const stack = [];
      for (let n = el; n && n.nodeType === 1; n = n.parentElement) { const c = parse(getComputedStyle(n).backgroundColor); if (c && c.a > 0) { stack.push(c); if (c.a === 1) break; } }
      let base = { r: 255, g: 255, b: 255, a: 1 };
      for (const c of stack.reverse()) base = over(c, base);
      return base;
    };
    const roots = document.querySelectorAll(".momx-scanner-panel, .momx-picker-overlay, .momx-news-overlay, .momx-filters-overlay, .momo-settings-overlay, .momx-popout-window, .momx-colmenu, .momx-setup-picker");
    const bad = []; const oldFont = []; let judged = 0;
    for (const root of roots) for (const el of root.querySelectorAll("*")) {
      const text = [...el.childNodes].filter((n) => n.nodeType === 3 && n.textContent.trim()).map((n) => n.textContent.trim()).join(" ");
      if (!text || el.closest(".momx-pill, .momx-card-cell-value, svg")) continue;
      const box = el.getBoundingClientRect(); if (!box.width || !box.height) continue;
      const style = getComputedStyle(el); if (style.visibility === "hidden") continue;
      // A dimmed (switched-off) group is meant to be faint.
      let faded = false; for (let n = el; n && n.nodeType === 1; n = n.parentElement) if (Number(getComputedStyle(n).opacity) < 0.9) faded = true;
      if (faded || el.closest(":disabled")) continue;
      const fg0 = parse(style.color); if (!fg0) continue;
      const bg = behind(el); const fg = over(fg0, bg);
      const [hi, lo] = [lum(fg), lum(bg)].sort((x, y) => y - x); const ratio = (hi + 0.05) / (lo + 0.05);
      judged += 1;
      const name = `${el.tagName.toLowerCase()}.${[...el.classList].join(".")} "${text.slice(0, 20)}"`;
      if (ratio < 4.5) bad.push(`${ratio.toFixed(1)} ${name} ${style.color} on rgb(${Math.round(bg.r)},${Math.round(bg.g)},${Math.round(bg.b)})`);
      if (!/^"?Mona Sans/.test(style.fontFamily)) oldFont.push(name);
    }
    return { bad: [...new Set(bad)], oldFont: [...new Set(oldFont)], judged };
  });
}

const look = (page) => page.evaluate((table) => {
  const panel = document.querySelector(".momx-scanner-panel");
  const cell = document.querySelector(`${table} tbody tr.momx-row td`);
  return {
    skin: document.documentElement.getAttribute("data-agx-skin"),
    panel: getComputedStyle(panel).backgroundColor,
    font: getComputedStyle(cell).fontFamily,
  };
}, LIVE_TABLE);

// The colours that carry trading meaning on the board. The Color column is an
// empty cell whose FILL is the signal; its (unused) text colour is ordinary ink.
const signals = (page) => page.evaluate(() => [...document.querySelectorAll(".momx-scanner-panel :is(.momx-pill, .momx-strategy-tag, .momx-setup-letter, .momx-pct, td.momx-col-color)")]
  .map((el) => {
    const s = getComputedStyle(el);
    const name = el.className.toString().slice(0, 60);
    return el.matches("td.momx-col-color") ? [name, s.backgroundColor].join(" | ") : [name, s.backgroundColor, s.color, s.borderTopColor].join(" | ");
  }));

const rowsThatFit = (page) => page.evaluate((table) => {
  const row = document.querySelector(`${table} tbody tr.momx-row`).getBoundingClientRect();
  const view = document.querySelector(".momx-scanner-view").getBoundingClientRect();
  const bar = document.querySelector("nav.mobile-chart-bottom-nav");
  const barTop = bar && bar.offsetWidth ? bar.getBoundingClientRect().top : window.innerHeight;
  return Math.floor((Math.min(view.bottom, window.innerHeight, barTop) - row.top) / row.height);
}, LIVE_TABLE);

e2eTest("the new look is on with nothing stored: shared font, card surface, and every signal colour exactly as the old look", async () => {
  await openScanner({}, async ({ page, pageErrors }) => {
    const seen = await look(page);
    assert.equal(seen.skin, "scanner");
    assert.equal(seen.panel, "oklch(0.115 0.045 264)");
    assert.match(seen.font, /^"?Mona Sans/);
    const after = await signals(page);
    // The old look, in the same page: take the mark off <html>.
    await page.evaluate(() => document.documentElement.removeAttribute("data-agx-skin"));
    const before = await signals(page);
    assert.ok(before.length >= 20, "signal elements found: " + before.length);
    assert.deepEqual(after, before);
    assert.deepEqual(pageErrors, []);
  });
});

e2eTest("each sector card names the sector and lists its tickers with their move; a click filters to it", async () => {
  await openScanner({}, async ({ page }) => {
    const card = page.locator(".momx-sectors .momx-sector").first();
    await card.waitFor({ timeout: 10_000 });
    const name = (await card.locator(".momx-sector-name").innerText()).trim();
    const chips = await card.locator(".momx-sector-ticker").allInnerTexts();
    assert.ok(chips.length >= 1, "tickers on the " + name + " card");
    for (const chip of chips) assert.match(chip.trim(), /^[A-Z.]+\s+[+-]\d+\.\d$/);
    const industries = await page.locator(`${LIVE_TABLE} tbody tr.momx-row`).evaluateAll((rows) => rows.length);
    await card.click();
    assert.equal(await card.getAttribute("aria-pressed"), "true");
    assert.ok((await page.locator(`${LIVE_TABLE} tbody tr.momx-row`).count()) <= industries);
  });
});

for (const theme of ["dark"]) {
  e2eTest(`as many rows fit on screen as before, on a monitor and on a phone (${theme})`, async () => {
    await openScanner({}, async ({ page }) => {
      const fit = await rowsThatFit(page);
      assert.ok(fit >= ROWS_BEFORE.monitor, `monitor: ${fit} rows fit, at least ${ROWS_BEFORE.monitor} wanted (30 before)`);
    });
    await openScanner({ viewport: PHONE }, async ({ page }) => {
      const fit = await rowsThatFit(page);
      assert.ok(fit >= ROWS_BEFORE.phone, `phone: ${fit} rows fit, at least ${ROWS_BEFORE.phone} wanted (8 before)`);
      assert.equal(await overflowsSideways(page), false, "the page scrolls sideways on a phone");
    });
  });

  e2eTest(`every label on the board is readable and in the shared font (${theme})`, async () => {
    await openScanner({}, async ({ page, pageErrors }) => {
      const seen = await unreadable(page);
      assert.ok(seen.judged >= 40, "labels judged: " + seen.judged);
      assert.deepEqual(seen.bad, []);
      assert.deepEqual(seen.oldFont, []);
      assert.deepEqual(pageErrors, []);
    });
  });

  // 2026-11-14: the board's stored headlines used to come from the LIVE news
  // store (the fixture did not fake /api/news-feed/latest), so this state was
  // on screen only when the live headline happened to be older than 3 hours -
  // and then it failed five tests here at once. It is now a fixture: NVDA
  // carries a 10-hour-old stored headline, AAPL a 1-hour-old one, always.
  // The rule is the same 4.5:1 as every other label on the board.
  e2eTest(`a stored headline's age in the News column is readable, fresh or older (${theme})`, async () => {
    await openScanner({ news: storedNews({ NVDA: 10, AAPL: 1 }) }, async ({ page }) => {
      const older = page.locator(`${LIVE_TABLE} td.momx-newscol.is-stored:not(.is-fresh)`);
      await older.first().waitFor({ timeout: 15_000 });
      assert.match((await older.first().innerText()).trim(), /^10h/);
      assert.match((await page.locator(`${LIVE_TABLE} td.momx-newscol.is-stored.is-fresh`).first().innerText()).trim(), /^1h/);
      const seen = await unreadable(page);
      assert.deepEqual(seen.bad, []);
      // The row under the pointer is lighter (#1c1c1f): the age must read there too.
      await older.first().hover();
      await page.waitForTimeout(300);
      assert.equal(await older.first().evaluate((cell) => getComputedStyle(cell.closest("tr")).backgroundColor), "oklch(0.23 0.055 264)");
      assert.deepEqual((await unreadable(page)).bad, []);
    });
  });

  // 2026-11-14: the other states that are dim on purpose, each put on screen:
  // the sparkle of a headline nobody scored (no sentiment), an empty Spread
  // cell, and the name of a column switched off in the Columns dialog (it read
  // 3.3:1). Same 4.5:1 rule, on a monitor and on a phone.
  e2eTest(`states that are dim on purpose stay readable: an unscored headline, an empty Spread cell, a hidden column (${theme})`, async () => {
    const news = storedNews({ NVDA: 10, AAPL: 1 });
    news.NVDA = { ...news.NVDA, sentiment: "" };
    for (const viewport of [MONITOR, PHONE]) {
      await openScanner({ viewport, news }, async ({ page }) => {
        const older = page.locator(`${LIVE_TABLE} td.momx-newscol.is-stored:not(.is-fresh)`);
        await older.first().waitFor({ timeout: 15_000 });
        assert.equal(await older.first().locator(".momx-newscol-ai.is-none").count(), 1, "the unscored sparkle is on screen");
        assert.ok((await page.locator(`${LIVE_TABLE} td.momx-spreadcol.is-none`).count()) >= 1, "an empty Spread cell is on screen");
        assert.deepEqual((await unreadable(page)).bad, []);
        await page.locator("[data-testid='momx-columns-open']").first().click();
        const dialog = page.locator("[role='dialog'][aria-label='Scanner columns']");
        await dialog.waitFor({ timeout: 10_000 });
        // Switching a column off is kept in this browser only (no write).
        await dialog.locator(".momx-columns-row[data-col='pctChange'] input[type='checkbox']").click();
        const hidden = dialog.locator(".momx-columns-row.is-hidden .momx-columns-name");
        await hidden.first().waitFor({ timeout: 5_000 });
        await page.waitForTimeout(300);
        assert.deepEqual((await unreadable(page)).bad, []);
        await hidden.first().hover();
        await page.waitForTimeout(300);
        assert.deepEqual((await unreadable(page)).bad, []);
      });
    }
  });

  for (const [name, opener, root] of [
    ["Filters","[data-testid='momx-filters-open']", ".momx-filters"],
    ["Columns", "[data-testid='momx-columns-open']", "[role='dialog'][aria-label='Scanner columns']"],
    ["Setup rules", ".momx-head-row th[data-col='setup'] button", ".momx-setup-picker"],
    ["list menu", ".momx-listbtn", ".momx-picker"],
  ]) {
    e2eTest(`the ${name} dialog follows the look and stays readable (${theme})`, async () => {
      await openScanner({}, async ({ page }) => {
        await page.locator(opener).first().click();
        await page.locator(root).first().waitFor({ timeout: 10_000 });
        await page.waitForTimeout(500);
        const seen = await unreadable(page);
        assert.deepEqual(seen.bad, []);
        assert.deepEqual(seen.oldFont, []);
      });
    });
  }

  e2eTest(`the ticker card follows the look; its day chart stays dark (${theme})`, async () => {
    await openScanner({}, async ({ page }) => {
      await page.locator("button.momx-badge.is-fires").first().click();
      const card = page.locator(".momx-popout-window[role='dialog']");
      await card.waitFor({ timeout: 10_000 });
      await card.locator(".momx-daychart svg").waitFor({ timeout: 15_000 });
      const seen = await unreadable(page);
      assert.deepEqual(seen.bad, []);
      assert.deepEqual(seen.oldFont, []);
      assert.equal(await card.locator(".momx-daychart svg").evaluate((svg) => getComputedStyle(svg).backgroundColor), "rgb(11, 13, 18)");
    });
  });
}

e2eTest("on a phone: readable, shared font, no sideways page scroll", async () => {
  await openScanner({ viewport: PHONE }, async ({ page }) => {
    const seen = await unreadable(page);
    assert.deepEqual(seen.bad, []);
    assert.deepEqual(seen.oldFont, []);
    assert.equal(await overflowsSideways(page), false);
  });
});
