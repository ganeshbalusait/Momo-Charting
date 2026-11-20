// Ask (chatbot) fixes of 2026-11-19, end to end at desktop and phone width.
//  1. "Why AMD is down today" on the phone showed "Could not reach the app" while the app was up. A question that
//     gets no reply is now asked once more by itself; if that fails too, the message names the cause.
//  2. "See nvda leaps which strike is good?": 24 bearish events stacked into tall red blocks over the mini chart,
//     "Open NVDA chart" twice, and a closed section whose summary only repeated its title.
// Every chat reply is faked, so no AI and no data route is reached; the only write is the chat's own read.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { TECHNICAL } from "./analysis-fixtures.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const BUTTON = "[data-testid='chat-dock-button']";
const CHAT = "[data-testid='dock-chat']";
const INPUT = `${CHAT} input.chat-input`;
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

const DAILY = TECHNICAL.charts.daily;
const BUSY_DAYS = [DAILY.bars[12].time, DAILY.bars[13].time, DAILY.bars[14].time, DAILY.bars[DAILY.bars.length - 1].time];
// 24 bearish events over four candles, the shape that drew the red blocks.
const CROWDED = {
  ...TECHNICAL,
  charts: { daily: { ...DAILY, events: BUSY_DAYS.flatMap((time) => Array.from({ length: 6 }, (_, i) => ({ time, kind: "bearish", name: `Bearish event ${i + 1}` }))) } },
};
const LEAP_REPLY = {
  intent: "outlook_long", ticker: "NVDA", lines: ["Long term on NVDA, with the chart numbers filled in."],
  links: [
    { label: "Open NVDA chart", view: "Charts & OI", ticker: "NVDA", timeframe: "5m" },
    { label: "Open NVDA on Long Term", view: "Long Term", ticker: "NVDA" },
    { label: "Open NVDA daily chart", view: "Charts & OI", ticker: "NVDA", timeframe: "D" },
    { label: "Open Earnings Calendar", view: "Earnings Calendar" },
  ],
  sections: [
    { key: "technical", title: "Technical analysis", lines: [], technical: CROWDED },
    { key: "price", title: "Price and today's move", lines: ["Regular session: NVDA -3.21% to 146.01 (4:00 PM ET)."] },
    { key: "technicals", title: "Technicals", lines: ["5m: 146.43 is above the 21 EMA."] },
    { key: "fundamentals", title: "Fundamentals (Long Term)", lines: ["P/E 18.5 at 146.43."] },
    { key: "rules", title: "What the app's rules show", lines: ["What the app's rules show:", "Long term: 1 of 4 rule checks point up."] },
  ],
};

const chatWrites = (reply) => ({ [CHAT_WRITE]: (write) => {
  const body = JSON.parse(write.body || "{}");
  if (body.home) return { json: { home: { cards: [], chips: [], tickers: ["NVDA", "AMD"] } } };
  return { json: reply };
} });

async function ask(page, question, tap) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 90_000 });
  if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
  await page.locator(INPUT).waitFor({ timeout: 15_000 });
  await page.locator(INPUT).fill(question);
  await page.locator(INPUT).press("Enter");
  await page.locator(ANSWER).first().waitFor({ timeout: 20_000 });
  return page.locator(ANSWER).first();
}

/** Stop the question's request in the browser `times` times (a dropped connection); home and "more" reads pass. */
async function dropQuestions(page, times) {
  let dropped = 0;
  await page.route("**/api/bot-chat", (route) => {
    const body = JSON.parse(route.request().postData() || "{}");
    if (!body.question || dropped >= times) return route.fallback();
    dropped += 1;
    return route.abort("connectionfailed");
  });
  return () => dropped;
}

for (const [label, viewport] of WIDTHS) {
  const tap = Boolean(viewport);

  e2eTest(`Ask: a crowded mini chart shows one marker per candle, no two buttons alike (${label})`, async () => {
    await withApp({ view: "Release Notes", viewport, fakeWrites: chatWrites(LEAP_REPLY) }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "See nvda leaps which strike is good?", tap);
      const chart = answer.locator("[data-testid='mini-chart']");
      await chart.waitFor({ timeout: 20_000 });
      const marks = chart.locator("[data-testid='mini-chart-marker']");
      await marks.first().waitFor({ timeout: 20_000 });
      assert.equal(await marks.count(), BUSY_DAYS.length, "one marker per busy candle, not six");
      // No marker sits above the chart's own box (the red blocks ran up over the text above it).
      const outside = await chart.evaluate((el) => {
        const box = el.getBoundingClientRect();
        return [...el.querySelectorAll("[data-testid='mini-chart-marker']")].filter((m) => {
          const r = m.getBoundingClientRect();
          return r.top < box.top - 1 || r.bottom > box.bottom + 1 || r.left < box.left - 1 || r.right > box.right + 1;
        }).length;
      });
      assert.equal(outside, 0);
      // The markers hold still (measured 2026-11-19: same boxes 700 ms apart). The tap is forced because the chat's own
      // scroller sits between the marker and the browser's "scrolled into view and stable" check.
      const boxes = () => marks.evaluateAll((els) => els.map((el) => { const r = el.getBoundingClientRect(); return `${Math.round(r.left)},${Math.round(r.top)}`; }).join(" "));
      const before = await boxes();
      await page.waitForTimeout(500);
      assert.equal(await boxes(), before, "markers do not move once drawn");
      await (tap ? marks.first().tap({ force: true }) : marks.first().click({ force: true }));
      assert.match(await chart.locator("[data-testid='mini-chart-pick']").innerText(), /6 bearish events: Bearish event 1; Bearish event 2/);

      const labels = await answer.locator("[data-testid='chat-link']").allInnerTexts();
      assert.equal(new Set(labels.map((text) => text.trim())).size, labels.length, `no two buttons read the same: ${labels.join(" | ")}`);
      assert.ok(labels.some((text) => /Open NVDA daily chart/.test(text)), labels.join(" | "));
      const rules = answer.locator("[data-testid='chat-section'][data-key='rules'] [data-testid='chat-section-summary']");
      assert.equal((await rules.innerText()).trim(), "Long term: 1 of 4 rule checks point up.");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`Ask: a dropped question is asked again by itself and answered (${label})`, async () => {
    await withApp({ view: "Release Notes", viewport, fakeWrites: chatWrites({ intent: "help", lines: ["AMD closed down 7.69%."] }) }, async (app) => {
      const { page } = app;
      const dropped = await dropQuestions(page, 1);
      const answer = await ask(page, "Why AMD is down today", tap);
      assert.match(await answer.innerText(), /AMD closed down 7\.69%/);
      assert.equal(dropped(), 1);
      assert.doesNotMatch(await answer.innerText(), /Could not reach|ask again/i);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  // 3. "Oi for nvda buyers or sellers?" got only "What I can answer" while the AI was over its free limit. The server now
  //    answers it from the High OI board (tests/test_momx_chat_market_api.py); here the trader sees that answer whole.
  e2eTest(`Ask: an OI buyers-or-sellers question shows the plain line and the walls (${label})`, async () => {
    const reply = { intent: "walls", ticker: "NVDA", lines: [
      "Open interest counts the contracts still open. Each one has a buyer and a seller, so it cannot show who is buying or selling, or a direction. Here is where the open interest sits.",
      "NVDA High OI board, price 146.01.", "Biggest call wall: 150 (OI 91,204, exp 2026-11-20).", "Biggest put wall: 140 (OI 88,310, exp 2026-11-20)."],
      links: [{ label: "Open NVDA High OI", view: "Quick Options", ticker: "NVDA", sub: "high-oi" }] };
    await withApp({ view: "Release Notes", viewport, fakeWrites: chatWrites(reply) }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "Oi for nvda buyers or sellers?", tap);
      const text = await answer.innerText();
      assert.match(text, /Each one has a buyer and a seller, so it cannot show who is buying or selling/);
      assert.match(text, /Biggest call wall: 150/);
      assert.match(text, /Biggest put wall: 140/);
      assert.doesNotMatch(text, /What I can answer|could not help read/i);
      const box = await answer.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1, "the answer is cut off at the side");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  // 4. "Nvda leap oi buyers or sellers?" (2026-11-19): the plain line, the biggest NEW open interest on top, and a Change
  //    column. The reply is the server's shape (tests/test_momx_chat_long_oi_api.py proves the server builds it).
  e2eTest(`Ask: a LEAP buyers-or-sellers question shows new open interest on top with its change (${label})`, async () => {
    const reply = { intent: "long_oi", ticker: "NVDA", lines: [
      "Open interest counts the contracts still open. Each one has a buyer and a seller, so it cannot show who is buying or selling, or a direction. Here is where the open interest sits.",
      "NVDA strikes with the most open interest, expiries 3 to 12 months out. Price 144.69 (Schwab, 22:43 ET).",
      "Biggest new open interest since the Nov 18 2026 reading: 175 call Feb 19 2027, +52,569 (now 62,247); 130 put Jun 17 2027, +24,032 (now 46,441). New open interest means positions were opened; it cannot show whether they were bought or sold."],
      table: { kind: "rows", title: "NVDA most open interest, 3 to 12 months out",
        columns: [{ key: "contract", label: "Contract" }, { key: "expiry", label: "Expiry" }, { key: "days", label: "Days", align: "right" },
          { key: "oi", label: "OI", align: "right" }, { key: "change", label: "Opened or closed since Nov 18 2026", align: "right" }, { key: "delta", label: "Delta", align: "right" },
          { key: "bid", label: "Bid", align: "right" }, { key: "ask", label: "Ask", align: "right" }],
        rows: [
          { contract: "175 Call · NEW", expiry: "2027-02-19", days: "92", oi: "62,247", change: "+52,569 opened", delta: "0.26", bid: "4.65", ask: "4.85" },
          { contract: "130 Put · NEW", expiry: "2027-06-17", days: "210", oi: "46,441", change: "+24,032 opened", delta: "-0.31", bid: "12.05", ask: "12.30" },
          { contract: "185 Call · ENTRY", expiry: "2027-02-19", days: "92", oi: "108,672", change: "2,166 closed", delta: "0.19", bid: "3.00", ask: "3.15" },
          { contract: "140 Put", expiry: "2027-06-17", days: "210", oi: "98,663", change: "+1,136 opened", delta: "-0.39", bid: "16.10", ask: "16.40" }],
        note: "NEW rows first: the biggest new open interest since the Nov 18 2026 reading." },
      links: [{ label: "Open NVDA chart", view: "Charts & OI", ticker: "NVDA", timeframe: "5m" }] };
    await withApp({ view: "Release Notes", viewport, fakeWrites: chatWrites(reply) }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "Nvda leap oi buyers or sellers?", tap);
      const text = await answer.innerText();
      assert.match(text, /Each one has a buyer and a seller/);
      assert.match(text, /Biggest new open interest since the Nov 18 2026 reading: 175 call Feb 19 2027, \+52,569/);
      const first = text.indexOf("175 Call");
      assert.ok(first >= 0 && first < text.indexOf("185 Call"), "the new 175 call comes before the biggest wall");
      assert.match(text, /Opened or closed since Nov 18 2026[\s\S]{0,40}\+52,569 opened/);
      assert.match(text, /2,166 closed/);
      const box = await answer.boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= page.viewportSize().width + 1, "the answer is cut off at the side");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  // 5. "Why chatbot not showing support and resistance?" (2026-11-20): the levels answer now says which are which, in
  //    plain words, before the full list (server: tests/test_momx_chat_chart_api.py).
  e2eTest(`Ask: support and resistance are named at the top of the levels answer (${label})`, async () => {
    const reply = { intent: "chart_levels", ticker: "NVDA", lines: [
      "NVDA chart levels, 5m. Newest candle Thu Nov 19 23:50 ET, close 143.74.",
      "Support = price levels BELOW NVDA's 143.74 that traders watch as a floor. Resistance = price levels ABOVE it that traders watch as a ceiling. They are levels to watch, not a forecast.",
      "Nearest resistance above 143.74: 147.59 (Day pivot S1); 148.19 (SMA 200); 149.88 (Previous day low).",
      "Nearest support below 143.74: 143.00 (Today's low).",
      "EMA 9 143.79, EMA 21 143.94, EMA 50 144.32, SMA 200 148.19 (chart defaults 9/21/50 and SMA 200)."],
      links: [{ label: "Open NVDA chart, 5m", view: "Charts & OI", ticker: "NVDA", timeframe: "5m" }] };
    await withApp({ view: "Release Notes", viewport, fakeWrites: chatWrites(reply) }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "What is support and resistance for nvda?", tap);
      const text = await answer.innerText();
      const sr = text.indexOf("Support = price levels BELOW");
      assert.ok(sr >= 0 && sr < text.indexOf("EMA 9"), "support and resistance come before the full list");
      assert.match(text, /Nearest resistance above 143\.74: 147\.59 \(Day pivot S1\)/);
      assert.match(text, /Nearest support below 143\.74: 143\.00 \(Today's low\)/);
      assert.match(text, /not a forecast/);
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`Ask: dropped twice with the app up says so plainly (${label})`, async () => {
    await withApp({ view: "Release Notes", viewport, fakeWrites: chatWrites({ intent: "help", lines: ["never shown"] }) }, async (app) => {
      const { page } = app;
      const dropped = await dropQuestions(page, 2);
      const answer = await ask(page, "Why AMD is down today", tap);
      assert.match(await answer.innerText(), /The app is up, but the answer did not come back\. Ask again\./);
      assert.equal(dropped(), 2, "asked twice, never a third time");
      assert.equal(await overflowsSideways(page), false);
    });
  }, { timeout: 240_000 });
}
