// News Feed -> "USD News Today" Forex Factory panel (Today / Week calendar),
// end to end in the real app (2026-11-04 coverage). The calendar read is
// answered with a fixture built around today's ET date, so past/future,
// impact and the error state are exact and nothing reaches faireconomy.media.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const VIEW = "News Feed";
const PANEL = 'section.forex-us-news-panel[aria-label="USD economic news today"]';

const etParts = () => Object.fromEntries(new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short",
}).formatToParts(new Date()).map((part) => [part.type, part.value]));
const today = () => { const p = etParts(); return `${p.year}-${p.month}-${p.day}`; };
const isWeekday = () => !["Sat", "Sun"].includes(etParts().weekday);
const mondayOf = (iso) => {
  const day = new Date(`${iso}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() - ((day.getUTCDay() + 6) % 7));
  return day.toISOString().slice(0, 10);
};
const plusDays = (iso, days) => {
  const day = new Date(`${iso}T12:00:00Z`);
  day.setUTCDate(day.getUTCDate() + days);
  return day.toISOString().slice(0, 10);
};

function calendar({ errors = [], live = true } = {}) {
  const date = today();
  const weekStart = mondayOf(date);
  const past = { id: "past", title: "CPI m/m", time: `${date}T08:30:00-05:00`, timeET: "8:30 AM", date, impact: "High", forecast: "0.3%", previous: "0.4%", isPast: true };
  const future = { id: "future", title: "Crude Oil Inventories", time: `${date}T22:30:00-05:00`, timeET: "10:30 PM", date, impact: "Medium", forecast: "", previous: "", isPast: false };
  const friday = { id: "fri", title: "Nonfarm Payrolls", time: `${plusDays(weekStart, 4)}T08:30:00-05:00`, timeET: "8:30 AM", date: plusDays(weekStart, 4), impact: "High", forecast: "150K", previous: "120K", isPast: false };
  const events = live && !errors.length ? [past, future] : [];
  return {
    live: live && !errors.length,
    source: "Forex Factory calendar",
    sourceUrl: "https://www.forexfactory.com/calendar",
    informationOnly: true,
    date,
    dateLabel: "Testday, Nov 04",
    weekStart,
    weekEnd: plusDays(weekStart, 4),
    timezone: "ET",
    updatedAt: new Date().toISOString(),
    events,
    weeklyEvents: live && !errors.length ? [past, future, friday].filter((e, i, all) => all.findIndex((x) => x.id === e.id) === i) : [],
    errors,
  };
}

const fakes = (payload = calendar(), delayForce = 0) => ({
  "GET /api/forex-factory-us-news": async (read) => {
    if (delayForce && read.search.includes("force=true")) await new Promise((resolve) => setTimeout(resolve, delayForce));
    return { json: payload };
  },
});

async function panel(app) {
  const element = app.page.locator(PANEL);
  await element.waitFor({ state: "visible", timeout: 60_000 });
  await app.page.locator(`${PANEL} footer span`, { hasText: /CALENDAR/ }).waitFor();
  await app.page.waitForFunction((selector) => !/Loading calendar/.test(document.querySelector(`${selector} h2`)?.textContent || ""), PANEL);
  return element;
}

e2eTest("the panel names today in ET and says the calendar is live", async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const element = await panel(app);
    assert.equal((await element.locator(".forex-us-news-head h2").innerText()).trim(), "USD News Today — Testday, Nov 04");
    assert.match(await element.locator("footer").innerText(), /^LIVE CALENDAR/);
    const link = element.locator(".forex-us-news-actions a", { hasText: "Forex Factory" });
    assert.equal(await link.getAttribute("href"), "https://www.forexfactory.com/calendar");
    assert.equal(await link.getAttribute("target"), "_blank");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("today's past event is dimmed and a high-impact one shows its forecast", async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const element = await panel(app);
    const events = element.locator(".forex-us-news-events article.forex-us-news-event");
    assert.equal(await events.count(), 2);
    const cpi = events.filter({ hasText: "CPI m/m" });
    assert.match(await cpi.getAttribute("class"), /\bimpact-high\b/);
    assert.match(await cpi.getAttribute("class"), /\bis-past\b/);
    assert.match(await cpi.locator(".forex-us-event-time").innerText(), /8:30 AM ET[\s\S]*Forecast: 0\.3% · Prev: 0\.4%/);
    assert.equal(await cpi.locator(".forex-us-impact-dot").getAttribute("aria-label"), "High impact");
    const crude = events.filter({ hasText: "Crude Oil Inventories" });
    assert.match(await crude.getAttribute("class"), /\bimpact-medium\b/);
    assert.doesNotMatch(await crude.getAttribute("class"), /\bis-past\b/);
    assert.match(await crude.locator(".forex-us-event-time").innerText(), /No forecast/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Week calendar shows Monday to Friday with today marked; Today hides it again", async () => {
  await withApp({ view: VIEW, fakeReads: fakes() }, async (app) => {
    const element = await panel(app);
    const tabs = element.locator('[role="group"][aria-label="USD news calendar view"]');
    await tabs.locator("button", { hasText: "Week calendar" }).click();
    assert.match(await tabs.locator("button", { hasText: "Week calendar" }).getAttribute("class"), /is-active/);
    const days = element.locator(".forex-us-week-grid article.forex-us-week-day");
    assert.equal(await days.count(), 5);
    assert.deepEqual((await days.locator("header b").allInnerTexts()).map((t) => t.trim()), ["Mon", "Tue", "Wed", "Thu", "Fri"]);
    assert.equal(await element.locator("small.forex-us-week-title").isVisible(), true);
    assert.equal(await element.locator(".forex-us-week-day.is-today").count(), isWeekday() ? 1 : 0);
    assert.equal(await days.nth(4).locator("article.forex-us-week-event", { hasText: "Nonfarm Payrolls" }).count(), 1);
    assert.equal(await element.locator(".forex-us-news-events").isHidden(), true);
    await tabs.locator("button", { hasText: "Today" }).click();
    assert.equal(await element.locator(".forex-us-week-grid").count(), 0);
    assert.equal(await element.locator(".forex-us-news-events").isVisible(), true);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Refresh calendar forces a fresh read and says Refreshing meanwhile", async () => {
  await withApp({ view: VIEW, fakeReads: fakes(calendar(), 1_500) }, async (app) => {
    const element = await panel(app);
    const button = element.locator(".forex-us-news-actions > button");
    assert.equal((await button.innerText()).trim(), "Refresh calendar");
    await button.click();
    await app.page.waitForFunction((selector) => /Refreshing/.test(document.querySelector(`${selector} .forex-us-news-actions > button`)?.textContent || ""), PANEL);
    assert.equal(await button.isDisabled(), true);
    await app.page.waitForFunction((selector) => /Refresh calendar/.test(document.querySelector(`${selector} .forex-us-news-actions > button`)?.textContent || ""), PANEL, { timeout: 10_000 });
    assert.ok(app.reads.some((read) => read.path === "/api/forex-factory-us-news" && read.search === "?force=true"));
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a calendar outage shows the reason and CALENDAR UNAVAILABLE", async () => {
  const reason = "Forex Factory calendar unavailable: URLError.";
  await withApp({ view: VIEW, fakeReads: fakes(calendar({ errors: [reason], live: false })) }, async (app) => {
    const element = await panel(app);
    assert.equal((await element.locator(".forex-us-news-error").innerText()).trim(), reason);
    assert.match(await element.locator("footer").innerText(), /^CALENDAR UNAVAILABLE/);
    assert.equal((await element.locator(".forex-us-news-empty").innerText()).trim(), "No medium or high-impact USD events are scheduled for today.");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone: the tabs, the link and Refresh fit without sideways scroll", async () => {
  await withApp({ view: VIEW, viewport: PHONE, fakeReads: fakes() }, async (app) => {
    const element = await panel(app);
    assert.equal(await overflowsSideways(app.page), false);
    for (const control of await element.locator(".forex-us-news-actions button, .forex-us-news-actions a").all()) {
      await control.scrollIntoViewIfNeeded();
      const box = await control.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `control at ${JSON.stringify(box)} is cut off`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});
