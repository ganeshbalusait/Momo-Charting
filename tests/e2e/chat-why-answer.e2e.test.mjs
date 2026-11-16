// The "why did it move" card in the chat (2026-11-14), end to end at desktop and phone width.
// His ask: "Why spy went down this morning?" should read like Google's answer: a short answer on top, "Key reasons"
// each with its news source (tap to read) and a Positive / Negative / Neutral tag, and the numbers below, closed.
// Both POST /api/bot-chat calls (the fast part and the one "more" read) are faked with the server's contract shape,
// so no AI, no Google News and no data route is reached; the only write the screen may make is the chat's own read.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const STATUS = "GET /api/auth/status";
const BUTTON = "[data-testid='chat-dock-button']";
const CHAT = "[data-testid='dock-chat']";
const INPUT = `${CHAT} input.chat-input`;
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
// The first test may queue up to 15 min for the one shared browser (harness.mjs lock) before it starts.
const TIMEOUT = 20 * 60_000;

const LEAD = "This morning (9:30 AM-12:00 PM ET, Fri Nov 13): SPY -1.36% from the open, -0.98% from Thursday's close.";
const CHECKING = "Checking Google News and reading the headlines...";
const FAST = {
  intent: "why_move", ticker: "SPY", day: "2026-11-13", more: "why_tok_abcdefghij", links: [], suggest: [],
  lines: [LEAD, "Regular session: SPY -0.39% to 754.25 (Fri Nov 13, 4:00 PM ET)", CHECKING],
  headlines: [{ title: "Stock Market Rebounds Even As Nvidia CEO Jensen Huang Resigns", source: "IBD", atText: "10:06 AM ET",
    url: "https://www.investors.com/news/stock-market-today", tone: "positive" }],
};
const ANSWER_AI = "SPY fell about 1.4% this morning, dragged by Nvidia after its CEO resigned and Treasury yields topping 5%.";
const ANSWER_CODE = "SPY fell 1.36% this morning (9:30 AM-12:00 PM ET, Fri Nov 13). The biggest drags among its largest stocks were NVDA -5.89%, AVGO -3.83% and GOOGL -3.00%.";
const REASONS = [
  { title: "Nvidia CEO resigns", text: "Nvidia fell 5.89% after its CEO stepped down.",
    sources: [{ source: "Benzinga", title: "Nasdaq futures fall after Nvidia CEO resigns", atText: "9:58 AM", url: "https://www.benzinga.com/markets/a", tone: "negative" }] },
  { title: "Yields hit 5%", text: "Treasury yields hit 5% as the Nasdaq plunged at midday.",
    sources: [{ source: "IBD", title: "Stock Market Midday: Nasdaq Plunges as Treasury Yields Hit 5%", atText: "12:15 PM", url: "https://www.investors.com/b", tone: "negative" },
      { source: "Yahoo Finance", title: "Bonds", atText: "11:40 AM", url: null, tone: "neutral" }] },
];
const why = (by) => ({ answer: by === "ai" ? ANSWER_AI : ANSWER_CODE, by, note: "",
  window: { label: "This morning", text: "9:30 AM-12:00 PM ET, Fri Nov 13" }, reasons: REASONS,
  drags: [{ symbol: "NVDA", pct: -5.89 }, { symbol: "AVGO", pct: -3.83 }, { symbol: "GOOGL", pct: -3.0 }] });

/** A faked chat server: the fast why answer at once, then (after a moment) the "more" part with the why card. */
function chatServer(by) {
  const moreAsked = [];
  return { moreAsked, writes: { [CHAT_WRITE]: async (write) => {
    const body = JSON.parse(write.body || "{}");
    if (body.home) return { json: { home: { cards: [], chips: [] } } };
    if (body.more) {
      moreAsked.push(body.more);
      await wait(2500);
      return { json: { more: { done: true, lines: [], articles: [], reasons: [], why: why(by) } } };
    }
    return { json: FAST };
  } } };
}

async function ask(page, question, tap) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 90_000 });
  if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
  await page.locator(INPUT).waitFor({ timeout: 15_000 });
  await page.locator(INPUT).fill(question);
  await page.locator(INPUT).press("Enter");
  await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
  return page.locator(ANSWER).first();
}

async function insideScreen(locator) {
  return locator.evaluate((el) => { const b = el.getBoundingClientRect(); return b.left >= -1 && b.right <= window.innerWidth + 1; });
}

async function checkCard(app, answer, tap, by) {
  const { page } = app;
  // The heading says what this is.
  const kind = (await answer.locator("[data-testid='chat-kind']").innerText()).trim();
  assert.match(kind, /why it moved/i);
  assert.doesNotMatch(kind, /what i can answer/i);

  // While the second part is on its way: "Writing the answer..." in the top slot, no "checking" line, numbers closed.
  const placeholder = answer.locator("[data-testid='chat-why-wait']");
  await placeholder.waitFor({ timeout: 5_000 });
  assert.equal((await placeholder.innerText()).trim(), "Writing the answer...");
  assert.equal(await answer.locator("[data-testid='chat-more-wait']").count(), 0, "no second 'getting latest news' line");
  assert.ok(!(await answer.innerText()).includes(CHECKING), "the checking line is replaced by the top slot");
  assert.equal(await answer.locator("[data-testid='chat-numbers-toggle']").getAttribute("aria-expanded"), "false");

  // The answer lands: paragraph, Key reasons with titles, source chips with https links and tone tags.
  const card = answer.locator("[data-testid='chat-why']");
  await card.waitFor({ timeout: 15_000 });
  assert.equal(await placeholder.count(), 0, "the placeholder is gone");
  assert.equal((await card.locator("[data-testid='chat-why-answer']").innerText()).trim(), by === "ai" ? ANSWER_AI : ANSWER_CODE);
  assert.match(await card.innerText(), /Key reasons/i);
  assert.deepEqual((await card.locator("[data-testid='chat-why-title']").allInnerTexts()).map((t) => t.trim()), REASONS.map((r) => r.title));
  const links = card.locator("[data-testid='chat-why-source-link']");
  assert.equal(await links.count(), 2, "two linkable sources (the third has no link)");
  for (let i = 0; i < 2; i += 1) {
    const link = links.nth(i);
    assert.match(await link.getAttribute("href"), /^https:\/\//);
    assert.equal(await link.getAttribute("target"), "_blank");
    assert.match(await link.getAttribute("rel"), /noopener/);
  }
  assert.match((await links.first().innerText()).trim(), /^Benzinga · 9:58 AM/);
  assert.deepEqual((await card.locator("[data-testid='chat-tone']").allInnerTexts()).map((t) => t.trim()), ["Negative", "Negative", "Neutral"]);
  const note = (await card.locator("[data-testid='chat-why-note']").innerText()).trim();
  assert.equal(note, by === "ai" ? "Written by AI from the headlines and prices below. Not confirmed." : "From the headlines and prices below. Not confirmed.");

  // The numbers sit closed under the answer; opened, the fact lines and the tagged headline come below the paragraph.
  const toggle = answer.locator("[data-testid='chat-numbers-toggle']");
  assert.equal(await toggle.getAttribute("aria-expanded"), "false");
  assert.equal(await answer.getByText(LEAD).count(), 0, "the fact lines are hidden until asked for");
  await (tap ? toggle.tap() : toggle.click());
  assert.equal(await toggle.getAttribute("aria-expanded"), "true");
  const lead = answer.locator(".chat-line", { hasText: "from Thursday's close" }).first();
  await lead.waitFor({ timeout: 5_000 });
  const paragraph = await card.locator("[data-testid='chat-why-answer']").boundingBox();
  const leadBox = await lead.boundingBox();
  assert.ok(paragraph.y + paragraph.height <= leadBox.y + 1, "the answer paragraph is above the fact lines");
  const headline = answer.locator("[data-testid='chat-headline']");
  assert.equal(await headline.count(), 1);
  assert.equal((await headline.locator("[data-testid='chat-tone']").innerText()).trim(), "Positive");
  assert.match(await answer.locator("[data-testid='chat-why-drags']").innerText(), /NVDA -5\.89%, AVGO -3\.83%, GOOGL -3\.00%/);

  assert.ok(await insideScreen(card), "the card fits the screen");
  assert.equal(await overflowsSideways(page), false, "nothing scrolls sideways");
  assert.deepEqual(app.pageErrors, []);
}

for (const [label, viewport] of WIDTHS) {
  const tap = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`the owner at ${label} gets the AI-written why card on top, with tagged sources and the numbers closed`, async () => {
    const server = chatServer("ai");
    await withApp({ view: "Release Notes", ...size, fakeWrites: server.writes }, async (app) => {
      const answer = await ask(app.page, "Why spy went down this morning?", tap);
      await checkCard(app, answer, tap, "ai");
      assert.deepEqual(server.moreAsked, [FAST.more], "the follow-up is asked once");
    });
  }, { timeout: TIMEOUT });

  e2eTest(`a regular user at ${label} gets the code-built why card, and never an AI-written one`, async () => {
    const server = chatServer("code");
    await withApp({ view: "Release Notes", ...size, fakeReads: { [STATUS]: member, "GET /api/momx-scanner/rule-record": { json: {} } }, fakeWrites: server.writes }, async (app) => {
      const answer = await ask(app.page, "Why spy went down this morning?", tap);
      await checkCard(app, answer, tap, "code");
      assert.doesNotMatch(await app.page.locator(CHAT).innerText(), /Written by AI/);
    });
  }, { timeout: TIMEOUT });
}
