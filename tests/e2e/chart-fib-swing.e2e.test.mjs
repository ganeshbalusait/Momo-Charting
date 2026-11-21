// Fib on Daily / Weekly / Monthly charts (2026-11-20, "Do both"):
//  * Big swing Fib - TradingView-style retracement of the lowest low to the
//    highest high of the last 252 days / 104 weeks / 60 months, 50%-61.8%
//    zone shaded, a "Fib 50% (price)" tag per level.
//  * AutoFib gains Weekly and Monthly timeframes.
// Read-only: the indicator profile POST is answered by charts-core.helpers.

import assert from "node:assert/strict";

import { PHONE, e2eTest } from "./harness.mjs";
import {
  card, openIndicators, profilePosts, studyCheckbox, studyRow, waitForTape, withCharts,
} from "./charts-core.helpers.mjs";

const BIG_SWING = "Big swing Fib";
const AUTOFIB = "AutoFib — Single TF";

async function fibTags(page) {
  return page.evaluate(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
    .filter((node) => node.children.length === 0 && /^(?:[DWM](?:\/[DWM])* )?Fib \d+(\.\d)?%(?: · [^(]+)? \(\d/.test(node.textContent.trim()))
    .map((node) => node.textContent.trim()));
}

async function waitForFibTags(page, atLeast = 3) {
  await page.waitForFunction((count) => [...document.querySelectorAll("section.oi-finder-chart-card *")]
    .filter((node) => node.children.length === 0 && /^(?:[DWM](?:\/[DWM])* )?Fib \d+(\.\d)?%(?: · [^(]+)? \(\d/.test(node.textContent.trim()))
    .length >= count, atLeast, { timeout: 150_000 });
}

e2eTest("NVDA monthly chart shows the Big swing Fib tags; switching it off removes them and saves the M profile", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["M"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "M" });
    // 2026-11-21: six lines (D / W / M at 50% and 61.8%). On a Monthly chart
    // the prices sit close together, so a tag beside another (NVDA M 50% at
    // 127.09 next to the 130.03 live price) can hide; its line stays.
    await waitForFibTags(page, 2);
    await page.waitForFunction(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
      .filter((node) => node.children.length === 0 && /(?:^|\/)M(?:\/[WD])* Fib \d|· M(?:\/[WD])* Fib \d/.test(node.textContent.trim())).length >= 1, null, { timeout: 90_000 });
    const tags = await fibTags(page);
    // NVDA monthly is a five-year RISE, so 100% sits at the low far below the
    // high (a short first-paint tape once drew 243 -> this month's low instead).
    // The Monthly swing's own tags (W and D draw their own swings beside it;
    // a tag sharing a price with another is hidden, so read what is shown).
    // A tag can be shared: "M/W Fib 0% · D Fib 100% (243.37)". Split it into
    // its parts, each with the tag's price.
    const parts = tags.flatMap((tag) => {
      const price = Number(tag.slice(tag.lastIndexOf("(") + 1, -1));
      return tag.slice(0, tag.lastIndexOf("(")).trim().split(" · ").map((text) => ({ text, price }));
    });
    const percent = (name) => parts.find(({ text }) => /^(?:[WD]\/)*M(?:\/[WD])* Fib /.test(text)
      && text.replace(/^[MWD/]+ Fib /, "") === name)?.price;
    const shownM = ["100%", "78.6%", "61.8%", "50%", "38.2%", "23.6%"].filter((name) => Number.isFinite(percent(name)));
    assert.ok(shownM.length >= 1, `M Fib tags shown: ${tags.join(" | ")}`);
    // Only the 50% and 61.8% lines, at the levels he confirmed (TradingView).
    assert.ok(shownM.every((name) => name === "50%" || name === "61.8%"), tags.join(" | "));
    if (Number.isFinite(percent("61.8%"))) assert.equal(percent("61.8%"), 99.65, tags.join(" | "));
    if (Number.isFinite(percent("50%"))) assert.equal(percent("50%"), 127.09, tags.join(" | "));
    // NVDA monthly is a five-year RISE: deeper levels sit at lower prices.
    for (let index = 1; index < shownM.length; index += 1) {
      assert.ok(percent(shownM[index - 1]) < percent(shownM[index]), `M swing should be a rise: ${tags.join(" | ")}`);
    }
    assert.ok(tags.some((tag) => /M(?:\/[WD])* Fib (61\.8|50)%/.test(tag)), tags.join(" | "));
    // "mention 'M fib'" / "I want to see in chart fib M/W/D": every tag names its swing.
    assert.ok(tags.every((tag) => /^[MWD](?:\/[MWD])* Fib /.test(tag)), tags.join(" | "));

    const popover = await openIndicators(page);
    const box = studyCheckbox(popover, BIG_SWING);
    assert.equal(await box.isChecked(), true, "Big swing Fib is on by default");
    const earlier = profilePosts(app).length;
    await box.click();
    await page.waitForFunction(() => ![...document.querySelectorAll("section.oi-finder-chart-card *")]
      .some((node) => node.children.length === 0 && /^(?:[DWM](?:\/[DWM])* )?Fib \d/.test(node.textContent.trim())), null, { timeout: 15_000 });
    const posts = profilePosts(app).slice(earlier);
    assert.equal(posts.at(-1)?.profile?.indicatorSettings?.fibSwing, false);
    assert.equal(posts.at(-1)?.key, "M");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("AutoFib settings list Weekly and Monthly, on by default; Big swing has its candle counts", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"], timeframes: ["W"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "W" });
    const popover = await openIndicators(page);
    await popover.getByRole("button", { name: `${AUTOFIB} settings`, exact: true }).click();
    const settings = studyRow(popover, AUTOFIB).locator(".oi-finder-indicator-settings");
    for (const label of ["Show Weekly", "Show Monthly"]) {
      const option = settings.locator("label", { hasText: label }).locator('input[type="checkbox"]');
      assert.equal(await option.isChecked(), true, label);
    }
    await popover.getByRole("button", { name: `${BIG_SWING} settings`, exact: true }).click();
    const swing = studyRow(popover, BIG_SWING).locator(".oi-finder-indicator-settings");
    // 2026-11-21: D and W over 20 candles (his TOS AutoFib window).
    assert.equal(await swing.locator("label", { hasText: "Daily candles" }).locator("input").inputValue(), "20");
    assert.equal(await swing.locator("label", { hasText: "Weekly candles" }).locator("input").inputValue(), "20");
    assert.equal(await swing.locator("label", { hasText: "Monthly candles" }).locator("input").inputValue(), "60");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

// 2026-11-21 "I want to see fib line 50% and 61.8% for D/W/M in 5 mins chart
// to all the timeframe charts": every chart draws D, W and M Fib at 50% and
// 61.8% only (D / W over 20 candles = his TOS AutoFib window, so AutoFib itself
// is off and its "D AutoFib" tags are gone). AAPL: its D / W levels sit inside
// the day's range (NVDA's are far above).
e2eTest("a 5-minute chart: D, W and M Fib at 50% and 61.8%; own candles and the intraday switch work", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL", timeframe: "5m" });
    const allTags = () => page.evaluate(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
      .filter((node) => node.children.length === 0 && /Fib \d/.test(node.textContent)).map((node) => node.textContent.trim()));
    await page.waitForFunction(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
      .some((node) => node.children.length === 0 && /(?:^|\/|· )D(?:\/[MW])* Fib (?:50|61\.8)% \(\d/.test(node.textContent)), null, { timeout: 150_000 });
    // (AAPL's W levels, 328.94 / 335.77, sit below the 5m chart's visible
    // prices, so their tags are off-screen; the W values are checked in
    // tests/test_chart_fib_swing_api.py.)
    const tags = await allTags();
    assert.ok(tags.every((tag) => !/AutoFib/.test(tag)), "AutoFib's D/W would repeat the lines: " + tags.join(" | "));
    assert.ok(tags.every((tag) => !/\b(?:0|23\.6|38\.2|78\.6|100)%/.test(tag)), "50% and 61.8% only: " + tags.join(" | "));
    const popover = await openIndicators(page);
    await popover.getByRole("button", { name: `${BIG_SWING} settings`, exact: true }).click();
    const settings = studyRow(popover, BIG_SWING).locator(".oi-finder-indicator-settings");
    const box = (text) => settings.locator("label", { hasText: text }).locator("input[type='checkbox']");
    for (const text of ["Monthly (M Fib)", "Weekly (W Fib)", "Daily (D Fib)"]) {
      assert.equal(await box(text).isChecked(), true, text + " on");
    }
    assert.equal(await box("This chart's own candles").isChecked(), false);
    assert.equal(await settings.locator("label", { hasText: "Daily candles" }).locator("input").inputValue(), "20");
    assert.equal(await settings.locator("label", { hasText: "Weekly candles" }).locator("input").inputValue(), "20");
    for (const text of ["Monthly (M Fib)", "Weekly (W Fib)", "Daily (D Fib)"]) await box(text).click();
    for (const text of ["Monthly (M Fib)", "Weekly (W Fib)", "Daily (D Fib)"]) {
      assert.equal(await box(text).isChecked(), false, text + " stays off");
    }
    // Switched off = its tags leave the chart (a stale merged tag once stayed).
    await page.waitForFunction(() => ![...document.querySelectorAll("section.oi-finder-chart-card *")]
      .some((node) => node.children.length === 0 && /[MWD] Fib \d/.test(node.textContent)), null, { timeout: 15_000 });
    await box("This chart's own candles").click();
    await page.waitForFunction(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
      // A tag can also be merged into a neighbour's chip ("4K 8/14 · 5-day Fib 50% (..)").
      // Tagged "5-day Fib", never a bare "Fib" (his 2026-11-20 phone screenshot:
      // "I see 2 fib but I don't which fib is that").
      .filter((node) => node.children.length === 0 && /(?:^|· )5-day Fib \d+(\.\d)?% \(\d/.test(node.textContent.trim())).length >= 1, null, { timeout: 90_000 });
    assert.ok((await allTags()).every((tag) => !/(?:^|· )Fib \d/.test(tag)), "no unlabelled Fib tag: " + (await allTags()).join(" | "));
    assert.equal(await settings.locator("label", { hasText: "Trading days" }).locator("input").inputValue(), "5");
    await settings.locator("label", { hasText: "Show on minute and hour charts" }).locator("input").click();
    await page.waitForFunction(() => ![...document.querySelectorAll("section.oi-finder-chart-card *")]
      .some((node) => node.children.length === 0 && /^(?:[DWM](?:\/[DWM])* |5-day )?Fib \d/.test(node.textContent.trim())), null, { timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

// Same rule on the Daily, Weekly and Monthly charts (and 1h): lettered D / W /
// M tags at 50% and 61.8% only, no AutoFib tags.
for (const timeframe of ["1h", "D", "W", "M"]) {
  e2eTest(`AAPL ${timeframe} chart: Fib tags are D / W / M at 50% and 61.8% only`, async () => {
    await withCharts({ workspace: { symbols: ["AAPL"], timeframes: [timeframe] } }, async (app) => {
      const { page } = app;
      await waitForTape(page, { symbol: "AAPL", timeframe });
      await page.waitForFunction(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
        .some((node) => node.children.length === 0 && /[MWD] Fib (?:50|61\.8)% \(\d/.test(node.textContent)), null, { timeout: 150_000 });
      const tags = await page.evaluate(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
        .filter((node) => node.children.length === 0 && /Fib \d/.test(node.textContent)).map((node) => node.textContent.trim()));
      assert.ok(tags.every((tag) => !/AutoFib/.test(tag)), tags.join(" | "));
      assert.ok(tags.every((tag) => !/\b(?:0|23\.6|38\.2|78\.6|100)%/.test(tag)), "50% and 61.8% only: " + tags.join(" | "));
      assert.ok(tags.every((tag) => !/(?:^|· )Fib \d/.test(tag)), "every tag names its swing: " + tags.join(" | "));
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}

// 2026-11-20 his NVDA 5m phone screenshot, then "Fix it": his saved settings
// still had the chart's own 5-day swing ticked, so an unlabelled "Fib 50%
// (134.39)" sat beside "M Fib 50% (127.09)". Saved settings like his are
// unticked once on the next load, at desktop and phone width.
for (const [label, extra] of [["desktop", {}], ["phone", { viewport: PHONE }]]) {
  e2eTest(`${label}: old saved settings with the 5-day swing ticked open the 5m chart without it`, async () => {
    const saved = JSON.stringify({ fibSwingShowOwn: true, fibSwingIntraday: true, fibSwingShowM: true });
    await withCharts({ ...extra, storage: { oiFinderChartIndicatorOptions: saved }, workspace: { symbols: ["NVDA"], timeframes: ["5m"] } }, async (app) => {
      const { page } = app;
      await waitForTape(page, { symbol: "NVDA", timeframe: "5m" });
      const tags = () => page.evaluate(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
        .filter((node) => node.children.length === 0 && /Fib \d/.test(node.textContent)).map((node) => node.textContent.trim()));
      await page.waitForFunction(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
        .some((node) => node.children.length === 0 && /M Fib \d+(\.\d)?% \(\d/.test(node.textContent)), null, { timeout: 150_000 });
      const shown = await tags();
      assert.ok(shown.every((tag) => !/(?:^|· )(?:5-day )?Fib \d/.test(tag)), "no 5-day / unlabelled Fib: " + shown.join(" | "));
      const popover = await openIndicators(page);
      await popover.getByRole("button", { name: `${BIG_SWING} settings`, exact: true }).click();
      const own = studyRow(popover, BIG_SWING).locator(".oi-finder-indicator-settings")
        .locator("label", { hasText: "This chart's own candles" }).locator("input[type='checkbox']");
      assert.equal(await own.isChecked(), false, "unticked by the one-time fix");
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}

e2eTest("phone: NVDA weekly chart shows Fib tags inside the chart, nothing cut off sideways", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"], timeframes: ["W"] } }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA", timeframe: "W" });
    await waitForFibTags(page, 2);
    const box = await card(page).boundingBox();
    const outside = await page.evaluate((cardBox) => [...document.querySelectorAll("section.oi-finder-chart-card *")]
      .filter((node) => node.children.length === 0 && /^(?:[DWM](?:\/[DWM])* )?Fib \d/.test(node.textContent.trim()))
      .map((node) => ({ text: node.textContent.trim(), rect: node.getBoundingClientRect() }))
      .filter(({ rect }) => rect.right > cardBox.x + cardBox.width + 1 || rect.left < cardBox.x - 1)
      .map(({ text, rect }) => `${text} [${Math.round(rect.left)}..${Math.round(rect.right)}]`), box);
    if (outside.length) {
      const where = await page.evaluate(() => [...document.querySelectorAll("section.oi-finder-chart-card *")]
        .filter((node) => node.children.length === 0 && /Fib 23\.6%/.test(node.textContent))
        .map((node) => [node.parentElement?.textContent, Math.round(node.parentElement?.getBoundingClientRect().width)].join(" | ")));
      console.log("WHERE", JSON.stringify(where));
    }
    assert.deepEqual(outside, [], `card ${Math.round(box.x)}..${Math.round(box.x + box.width)}`);
    const sideways = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    assert.equal(sideways, false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
