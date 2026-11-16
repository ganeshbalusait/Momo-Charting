// The chat's ticker report, two-part news answer and type-ahead (2026-11-11), end to end at desktop and phone width.
// Every chat reply is faked (POST /api/bot-chat answers replies captured from the real route, see analysis-fixtures.mjs), so no AI and no data route is reached;
// the only thing the screen may write is the chat's own read.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CAPTURED } from "./analysis-fixtures.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const BUTTON = "[data-testid='chat-dock-button']";
const PANEL = "[data-testid='chat-dock-panel']";
const CHAT = "[data-testid='dock-chat']";
const INPUT = `${CHAT} input.chat-input`;
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const SUGGEST = `${CHAT} [data-testid='chat-suggestion']`;
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// The reason line the real route sends (chatReport.serverReply.json, closed text): category, headline, source, time.
const REASON = /Product or business news: "Chipmaker announces a thing" \(Wire, 15:30 ET\)/;

/** A faked chat server. `replies` maps a question to its reply; `more` answers the follow-up (after `moreDelay` ms). */
function chatServer({ replies = {}, more = null, moreStatus = 200, moreDelay = 600 } = {}) {
  const asked = [];
  const moreAsked = [];
  return { asked, moreAsked, writes: { [CHAT_WRITE]: async (write) => {
    const body = JSON.parse(write.body || "{}");
    if (body.home) return { json: { home: { cards: [], chips: ["which sector is hot"], tickers: ["TSLA", "NVDA", "ZETA"] } } };
    if (body.more) {
      moreAsked.push(body.more);
      await wait(moreDelay);
      return moreStatus === 200 ? { json: more || {} } : { status: moreStatus, json: { error: "busy" } };
    }
    asked.push(body.question);
    return { json: replies[body.question] || { intent: "help", lines: [`Answer to ${body.question}`] } };
  } } };
}

async function openChat(page, tap) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 90_000 });
  if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
  await page.locator(INPUT).waitFor({ timeout: 15_000 });
}

async function ask(page, question, tap) {
  await openChat(page, tap);
  const earlier = await page.locator(ANSWER).count();
  await page.locator(INPUT).fill(question);
  await page.locator(INPUT).press("Enter");
  await page.locator(ANSWER).nth(earlier).waitFor({ timeout: 15_000 });
  return page.locator(ANSWER).nth(earlier);
}

const onlyChatWrites = (app) => {
  const background = new Set(["/api/bot-chat", "/api/news-feed/refresh", "/api/client-timing", "/api/chart-indicator-profiles"]);
  assert.deepEqual([...new Set(app.writes.map((w) => w.path))].filter((p) => !background.has(p)), []);
};

const fitsInside = (page, selector) => page.locator(selector).first().evaluate((el) => {
  const box = el.getBoundingClientRect();
  return box.left >= -1 && box.right <= window.innerWidth + 1;
});

for (const [label, viewport] of WIDTHS) {
  const tap = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`type-ahead at ${label}: "why tsla" offers "why TSLA went up today", and a tap asks it`, async () => {
    const server = chatServer();
    await withApp({ view: "Release Notes", ...size, fakeWrites: server.writes }, async (app) => {
      const { page } = app;
      await openChat(page, tap);
      await page.locator(INPUT).click();
      await page.locator(INPUT).pressSequentially("why tsla", { delay: 30 });
      const first = page.locator(SUGGEST).first();
      await first.waitFor({ timeout: 5_000 });
      assert.equal((await first.innerText()).trim(), "why TSLA went up today");
      assert.ok((await page.locator(SUGGEST).count()) <= 6);
      // The list sits above the ask box and never covers it.
      const list = await page.locator(`${CHAT} [data-testid='chat-suggest']`).boundingBox();
      const box = await page.locator(INPUT).boundingBox();
      assert.ok(list.y + list.height <= box.y + 1, "the list covers the ask box");
      assert.equal(await overflowsSideways(page), false);
      await (tap ? first.tap() : first.click());
      await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
      assert.deepEqual(server.asked, ["why TSLA went up today"]);
      assert.equal(await page.locator(INPUT).inputValue(), "", "the box is cleared after asking");
      assert.equal(await page.locator(SUGGEST).count(), 0, "the list closes after asking");
      onlyChatWrites(app);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`two-part answer at ${label} (captured server reply): fast part at once, ONE follow-up adds the reason and keeps the article`, async () => {
    // The real route's replies (CAPTURED): articles as {title, source, atText, url}, the second part wrapped in {"more": {...}}.
    const { fast, more } = CAPTURED["why is nvda up today"];
    // 3 s before the second part lands, so a slow phone-width page still sees the "getting latest news" line.
    const server = chatServer({ replies: { "why is NVDA up today": fast }, more, moreDelay: 3000 });
    await withApp({ view: "Release Notes", ...size, fakeWrites: server.writes }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "why is NVDA up today", tap);
      // Changed on purpose (2026-11-14, his "crisp clear answer"): while a why answer's second part is on its way the card's
      // top slot says "Writing the answer..." (not "getting latest news...") and the fast fact lines and articles wait,
      // closed, under "Show the numbers". This captured second part is the older shape with no "why" card, so once it
      // lands the lines open again as before (checked below).
      await answer.locator("[data-testid='chat-why-wait']", { hasText: /Writing the answer\.\.\./ }).waitFor({ timeout: 5_000 });
      assert.equal(await answer.locator("[data-testid='chat-more-wait']").count(), 0);
      const toggle = answer.locator("[data-testid='chat-numbers-toggle']");
      assert.equal(await toggle.getAttribute("aria-expanded"), "false");
      await (tap ? toggle.tap() : toggle.click());
      assert.match(await answer.innerText(), /NVDA \+1\.00% today at 100\.00/);
      // The saved article shows at once, as a button that opens it in a new tab, named by its source.
      const links = answer.locator("[data-testid='chat-headline-link']");
      await links.first().waitFor({ timeout: 5_000 });
      assert.equal(await links.count(), 1);
      assert.equal(await links.first().getAttribute("target"), "_blank");
      assert.match(await links.first().getAttribute("rel"), /noopener/);
      assert.match(await links.first().innerText(), /Wire/);
      assert.equal(await links.first().getAttribute("href"), "https://example.test/a");
      assert.match(await answer.locator("[data-testid='chat-headline']").first().innerText(), /Chipmaker announces a thing[\s\S]*Wed Nov 11 15:30 ET/);
      // The second part adds what the news points to, naming its headline, source and time; the article stays once.
      // Closed-text design (2026-11-11): the line is the server's own category + headline + source + time, never AI-written.
      await answer.getByText(REASON).first().waitFor({ timeout: 15_000 });
      assert.equal(await answer.locator("[data-testid='chat-more-wait']").count(), 0);
      assert.equal(await answer.locator("[data-testid='chat-why-wait']").count(), 0, "the placeholder goes with the second part");
      assert.equal(await answer.locator("[data-testid='chat-numbers-toggle']").count(), 0, "no why card came, so the lines are open as before");
      assert.match(await answer.innerText(), /NVDA \+1\.00% today at 100\.00/);
      assert.doesNotMatch(await answer.innerText(), /Checking Google News and reading the headlines/, "the pending line clears with the second part");
      assert.match(await answer.innerText(), /What the news points to/);
      assert.equal(await links.count(), 1, "the same article from the second part is not shown twice");
      assert.equal((await answer.innerText()).match(new RegExp(REASON.source, "g")).length, 1, "the reason is shown once");
      assert.doesNotMatch(await answer.innerText(), /load up|reach 200/, "no AI-written sentence reaches the screen");
      await wait(1500);
      assert.deepEqual(server.moreAsked, [fast.more], "the follow-up is asked exactly once");
      assert.equal(await overflowsSideways(page), false);
      onlyChatWrites(app);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`two-part news answer at ${label}: a failed follow-up shows one plain line and is not asked again`, async () => {
    const reply = { intent: "news", ticker: "NVDA", lines: ["2 saved headlines on NVDA today"], more: "news_tok_2" };
    const server = chatServer({ replies: { "any news on NVDA": reply }, moreStatus: 503, moreDelay: 200 });
    await withApp({ view: "Release Notes", ...size, fakeWrites: server.writes }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "any news on NVDA", tap);
      await answer.locator("[data-testid='chat-more-failed']").waitFor({ timeout: 15_000 });
      assert.equal(await answer.locator("[data-testid='chat-more-failed']").count(), 1);
      assert.match(await answer.innerText(), /2 saved headlines on NVDA today/, "the fast part stays");
      await wait(1500);
      assert.deepEqual(server.moreAsked, ["news_tok_2"]);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`full ticker report at ${label} (captured server reply): sections in order, first three open, Technical card, parts fill in, fits the width`, async () => {
    // The real route's report (CAPTURED): sections with no view tag, views = one verdict per term, miniChart in the second part.
    const { fast, more } = CAPTURED["nvda full report"];
    const server = chatServer({ replies: { "nvda full report": fast }, more, moreDelay: 1200 });
    await withApp({ view: "Release Notes", ...size, fakeWrites: server.writes }, async (app) => {
      const { page } = app;
      const answer = await ask(page, "nvda full report", tap);
      const report = answer.locator("[data-testid='chat-report']");
      await report.waitFor({ timeout: 10_000 });
      // Only the report's header line sits above the cards; the sections are not repeated as flat lines (F4).
      assert.deepEqual(await answer.locator(":scope > .chat-line").allInnerTexts(), [fast.lines[0]]);
      assert.equal((await answer.innerText()).split("Market: SPY +1.00% (15:55 ET).").length - 1, 1, "an open section's line shows once, on its card");
      const keys = await report.locator("[data-testid='chat-section']").evaluateAll((els) => els.map((el) => [el.dataset.key, el.open]));
      // The second part may add a section on the end (the owner's "bot" part), so only the fast part's are compared.
      assert.deepEqual(keys.slice(0, fast.sections.length), fast.sections.map((s, i) => [s.key, i < 3]));
      // The server sends no Short / Long grouping of sections, so there are no view tabs: the terms are the card's pills.
      assert.equal(await answer.locator("[data-testid^='chat-view-tab-']").count(), 0);
      // A closed section shows its one-line summary.
      assert.match(await report.locator("[data-key='options'] [data-testid='chat-section-summary']").innerText(), /expected move/);
      assert.doesNotMatch(await answer.innerText(), /vwap/i);
      // The Technical card: Short / Mid / Long-Term pills from the server's verdicts.
      const ta = report.locator("[data-testid='technical-card']");
      await ta.waitFor({ timeout: 10_000 });
      for (const key of ["short", "mid", "long"]) assert.equal(await ta.locator(`[data-testid='ta-pill-${key}']`).count(), 1, key);
      // The one follow-up: the short-term verdict and the mini chart with the events marked on it.
      await ta.locator("[data-testid='mini-chart-marker']").first().waitFor({ timeout: 15_000 });
      const short = more.more.views.find((v) => v.view === "short");
      assert.match(await ta.locator("[data-testid='ta-verdict']").innerText(), new RegExp(short.verdict, "i"));
      assert.doesNotMatch(await ta.locator("[data-testid='ta-verdict']").innerText(), /not advice/, "the not-advice label is not the verdict word");
      assert.match(await ta.locator("[data-testid='ta-pill-short']").getAttribute("class"), /is-up/);
      const kinds = await ta.locator("[data-testid='mini-chart-marker']").evaluateAll((els) => els.map((el) => el.dataset.kind));
      assert.ok(kinds.includes("bullish"), `markers: ${kinds}`);
      await (tap ? ta.getByTestId("ta-frame-weekly").tap() : ta.getByTestId("ta-frame-weekly").click());
      await ta.locator("[data-testid='mini-chart-marker']").first().waitFor({ timeout: 10_000 });
      // The pending chart numbers fill the Technicals section, and the article sits on "Why it moved".
      assert.equal(await answer.locator("[data-testid='chat-section-loading']").count(), 0);
      const tech = report.locator("[data-key='technicals']");
      await (tap ? tech.locator("summary").tap() : tech.locator("summary").click());
      assert.equal(await tech.evaluate((el) => el.open), true);
      assert.match(await tech.innerText(), /21 EMA/);
      const why = report.locator("[data-key='why']");
      assert.equal(await why.locator("[data-testid='chat-headline-link']").count(), 1);
      assert.match(await why.innerText(), REASON);
      assert.doesNotMatch(await answer.innerText(), /load up|reach 200/, "no AI-written sentence reaches the screen");
      assert.doesNotMatch(await answer.innerText(), /Checking Google News and reading the headlines/, "no pending line left once the second part is in");
      assert.deepEqual(await answer.locator(":scope > .chat-line").allInnerTexts(), more.more.lines, "the second part's header replaces the fast one");
      assert.equal(await answer.locator("[data-testid='chat-headline-link']").count(), 1, "the article shows once, on its section");
      assert.deepEqual(server.moreAsked, [fast.more]);
      assert.equal(await overflowsSideways(page), false);
      assert.equal(await fitsInside(page, `${ANSWER} [data-testid='technical-card']`), true);
      assert.equal(await page.locator(PANEL).isVisible(), true);
      onlyChatWrites(app);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 300_000 });
}

e2eTest("type-ahead: arrow keys light a suggestion, Enter asks it, and Esc closes only the list", async () => {
  const server = chatServer();
  await withApp({ view: "Release Notes", fakeWrites: server.writes }, async (app) => {
    const { page } = app;
    await openChat(page, false);
    await page.locator(INPUT).click();
    await page.locator(INPUT).pressSequentially("tsla", { delay: 30 });
    await page.locator(SUGGEST).first().waitFor({ timeout: 5_000 });
    await page.keyboard.press("Escape");
    assert.equal(await page.locator(SUGGEST).count(), 0, "Esc closes the list");
    assert.equal(await page.locator(PANEL).isVisible(), true, "Esc did not close the chat window");
    assert.equal(await page.locator(INPUT).inputValue(), "tsla", "the typed text stays");
    await page.locator(INPUT).pressSequentially(" ca", { delay: 30 });
    await page.locator(SUGGEST).first().waitFor({ timeout: 5_000 });
    await page.keyboard.press("ArrowDown");
    const lit = page.locator(`${SUGGEST}[aria-selected='true']`);
    const text = (await lit.innerText()).trim();
    assert.match(text, /TSLA/);
    await page.keyboard.press("Enter");
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    assert.deepEqual(server.asked, [text]);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });
