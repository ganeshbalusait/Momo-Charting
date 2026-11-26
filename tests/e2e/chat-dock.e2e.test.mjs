// The chat window on every page (2026-11-11): the round button at the bottom right opens the same chat as the
// Bot page's Ask tab, and it knows the ticker on screen. The answers are canned; the spec checks what is sent,
// what is shown, and that it never gets in the way of the chart, the bottom tabs or the scanner's TOP button.
// The owner gets the whole chat; a regular user gets scanner questions only (the server decides what they may ask).

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { card, openIndicators, waitForTape, withCharts } from "./charts-core.helpers.mjs";
import { CHAIN_READS, chainReply, chartStorage, expiryIn, sleep } from "./options-fixtures.mjs";
import { BOARD_STORAGE, boardReady } from "./scanner-board-fixtures.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const BUTTON = "[data-testid='chat-dock-button']";
const PANEL = "[data-testid='chat-dock-panel']";
const CHAT = "[data-testid='dock-chat']";
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const LOOKING = `${CHAT} [data-testid='chat-looking']`;
const CHIP = `${CHAT} .chat-tray .chat-chip`;
const STATUS = "GET /api/auth/status";
const RAIL = 'aside.sidebar[data-testid="primary-navigation"]';
const NAV = ".mobile-chart-bottom-nav";
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

const mustChange = { json: { ...member.json, user: { ...member.json.user, mustChangePassword: true } } };
// A regular user's window must not carry any of the owner's words.
const OWNER_WORDS = /\b(bot|paper|scorecard|trades?|profit|loss|p\/l)\b/i;
const DISMISS = ".auto-alert-toast button[aria-label='Dismiss alert']";
const CHAIN = "#charts-oi-option-chain";
const QUICK_TITLE = "[data-testid='mobile-quick-options'] .mobile-quick-options-title b";
const QUICK_RAIL = "[data-testid='mobile-quick-options'] nav[aria-label='Fast option tickers']";
const GRIDS_BUTTON = 'button.oi-chart-workspace-toggle[aria-label="Open the saved grids menu"]';
const GRIDS_MENU = '[role="menu"][aria-label="Saved grids"]';
const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

// What the server really answers a regular user's off-scanner question with, read from momx/chatbot.py and not copied here.
function serverRefusal() {
  return JSON.parse(execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-c", "import json; from momx import chatbot; print(json.dumps(chatbot._scanner_only()))"],
    { cwd: REPO, encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }));
}

const LONG_LIST = Array.from({ length: 60 }, (_, i) => `- TICK${i}: on the list at 9:30 AM with RVOL ${i + 1}.`);
const REPLIES = {
  "NVDA today scanner": { intent: "show", ticker: "NVDA", day: "2026-11-09", suggest: ["why was NVDA missed"], lines: ["NVDA on 2026-11-09: Level 2 call signal at 9:38 AM."] },
  "see NVDA": { intent: "show", ticker: "NVDA", day: "2026-11-09", suggest: ["why was NVDA missed"], lines: ["NVDA on 2026-11-09: Level 2 call signal at 9:38 AM."] },
  "why was NVDA missed": { intent: "why_missed", ticker: "NVDA", day: "2026-11-09", suggest: [], lines: ["The bot was full at the time."] },
  "NVDA at 9:30 in the scanner": { intent: "scanner_ticker", ticker: "NVDA", day: "2026-11-09", suggest: [], lines: ["NVDA was on the list at 9:30 AM."] },
  "scanner at 9:30 give me the list of tickers": { intent: "scanner_list", ticker: null, day: "2026-11-09", suggest: ["which tickers stayed on top"], lines: ["The scanner at 9:30 AM on 2026-11-09:", ...LONG_LIST] },
};

function answers() {
  return { [CHAT_WRITE]: (write) => {
    const { question } = JSON.parse(write.body);
    return { json: REPLIES[question] || { intent: "help", ticker: null, day: null, suggest: [], lines: ["I did not find a ticker I have records for in that question."] } };
  } };
}

const texts = async (locator) => (await locator.allTextContents()).map((s) => s.trim());
// The questions asked; the opening screen's one read is the same route with {"home": true}.
const allBodies = (app) => app.writes.filter((w) => w.path === "/api/bot-chat").map((w) => JSON.parse(w.body));
const bodies = (app) => allBodies(app).filter((b) => !b.home);
const homeReads = (app) => allBodies(app).filter((b) => b.home);
// The pages post these by themselves with or without the chat (the harness stops them all the same).
const PAGE_BACKGROUND_WRITES = new Set(["/api/news-feed/refresh", "/api/client-timing", "/api/chart-indicator-profiles"]);
const onlyChatWrites = (app) => assert.deepEqual([...new Set(app.writes.map((w) => w.path))].filter((p) => p !== "/api/bot-chat" && !PAGE_BACKGROUND_WRITES.has(p)), []);
const overlap = (a, b) => a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;

async function openDock(page, tap = false) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 60_000 });
  await (tap ? button.tap() : button.click());
  await page.locator(CHAT).waitFor({ timeout: 15_000 });
}

// The element really on top at the middle of a box, so "visible" means the trader can press it.
const onTop = (page, selector) => page.evaluate((sel) => {
  const node = document.querySelector(sel);
  const box = node.getBoundingClientRect();
  const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
  return node === hit || node.contains(hit);
}, selector);

// One alert feed: quiet until fire(), then one new confirmation, which plays the toast on any page.
function alertFeed() {
  const state = { polls: 0, fired: false };
  const event = { id: "e2e-dock-toast", kind: "confirm", symbol: "NVDA", side: "CALL", level: { strike: 150 }, price: 150.6,
    at: `${expiryIn(0)}T10:40:00-05:00`, sessionDate: expiryIn(0), message: "NVDA CALL OI 150 CONFIRMED · 5m close 150.6" };
  return {
    reads: {
      "GET /api/oi-auto-alerts": () => {
        state.polls += 1;
        return { json: { enabled: true, includeMag7: false, mag7Symbols: [], manualSymbols: [], maxManualSymbols: 30, status: "Armed", message: "", refreshing: false,
          lastError: "", pendingSymbols: [], rows: [], events: state.fired ? [event] : [], sessionAlerts: { days: [] },
          serverTime: `${expiryIn(0)}T11:00:00-05:00`, session: { isTradingDay: true, regularHours: true } } };
      },
      "GET /api/watchlist-quotes": { json: { rows: [] } },
    },
    async fire() {
      for (let waited = 0; state.polls < 1 && waited < 60_000; waited += 250) await sleep(250);
      assert.ok(state.polls >= 1, "the alert feed was never read");
      state.fired = true;
    },
  };
}

// With the window closed, a live alert's X must be what a click lands on.
async function toastCanBeDismissed(page, feed) {
  await page.locator(BUTTON).waitFor({ timeout: 60_000 });
  await feed.fire();
  const toast = page.locator(".auto-alert-toast");
  await toast.waitFor({ state: "visible", timeout: 45_000 });
  assert.equal(overlap(await page.locator(BUTTON).boundingBox(), await toast.boundingBox()), false, "the button sits on the alert");
  assert.equal(await onTop(page, DISMISS), true, "the alert's X is covered");
  await page.locator(DISMISS).click();
  await toast.waitFor({ state: "hidden", timeout: 5_000 });
  assert.equal(await page.locator(PANEL).count(), 0, "dismissing the alert opened the chat");
}

e2eTest("Charts & OI: it sees the ticker, sends it with the first question, and the conversation survives closing and changing page", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA" });
    assert.equal(await page.locator(PANEL).count(), 0, "the window starts closed");
    await openDock(page);
    assert.equal((await page.locator(`${PANEL} .chat-dock-head b`).textContent()).trim(), "Ask");
    assert.equal((await page.locator(LOOKING).textContent()).trim(), "Looking at NVDA");
    assert.deepEqual(await texts(page.locator(CHIP)), ["What level we have in chart", "NVDA signals on 5m", "NVDA today scanner", "call wall on NVDA", "why was NVDA missed"]);
    assert.equal(await page.locator(`${CHAT} input.chat-input`).getAttribute("placeholder"), "For example: NVDA at 9:30 in the scanner");
    const box = await page.locator(PANEL).boundingBox();
    assert.ok(Math.abs(box.width - 380) <= 1 && Math.abs(box.height - 560) <= 1, `panel is ${JSON.stringify(box)}`);

    await page.locator(CHIP, { hasText: "NVDA today scanner" }).click();
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    assert.equal((await page.locator(`${ANSWER} .chat-kind`).first().textContent()).trim(), "On file · NVDA · 2026-11-09");
    assert.deepEqual(bodies(app)[0], { question: "NVDA today scanner", context: { ticker: "NVDA", day: "", fromPage: true, pageTicker: "NVDA", pageName: "Charts & OI" } });

    await page.locator(`${CHAT} input.chat-input`).fill("profit or loss");
    await page.locator(`${CHAT} input.chat-input`).press("Enter");
    await page.locator(ANSWER).nth(1).waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[1], { question: "profit or loss", context: { ticker: "NVDA", day: "2026-11-09", pageTicker: "NVDA", pageName: "Charts & OI" } });   // its own subject wins now

    await page.locator("[data-testid='chat-dock-close']").click();
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    await page.locator(RAIL).locator('.nav-item[title="Release Notes"]').click();
    await page.locator(BUTTON).click();
    await page.locator(PANEL).waitFor({ state: "visible", timeout: 5_000 });
    assert.equal(await page.locator(ANSWER).count(), 2, "the conversation was lost");
    assert.equal(await page.locator(LOOKING).count(), 0, "this page has no ticker");
    await page.keyboard.press("Escape");
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(bodies(app).length, 2);
    onlyChatWrites(app);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("Charts & OI: changing the chart's ticker moves the window to the new ticker", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await openDock(page);
    assert.equal((await page.locator(LOOKING).textContent()).trim(), "Looking at AAPL");
    const input = card(page).locator('input[aria-label="Chart ticker"]');
    await input.fill("spy");
    await input.press("Enter");
    await waitForTape(page, { symbol: "SPY" });
    await page.locator(LOOKING, { hasText: "SPY" }).waitFor({ timeout: 15_000 });
    assert.deepEqual((await texts(page.locator(CHIP)))[1], "SPY signals on 5m");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("Quick Options: the window shows the ticker the page is on, follows the page's own ticker buttons, and sends the new one", async () => {
  const fakeReads = { ...CHAIN_READS, "GET /api/oi-finder": (read) => chainReply(read, { researchSection: new URLSearchParams(read.search).get("section") || "" }) };
  await withApp({ view: "Quick Options", fakeReads, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    const title = page.locator(QUICK_TITLE);
    await title.waitFor({ state: "visible", timeout: 60_000 });
    const start = (await title.innerText()).replace(/ live chain$/i, "").trim();
    await openDock(page);
    assert.equal((await page.locator(LOOKING).textContent()).trim(), `Looking at ${start}`);
    const target = start === "SPY" ? "NVDA" : "SPY";
    await page.locator(`${QUICK_RAIL} button`, { hasText: new RegExp(`^${target}$`) }).click();
    await page.locator(QUICK_TITLE, { hasText: `${target} LIVE CHAIN` }).waitFor({ timeout: 30_000 });
    await page.locator(LOOKING, { hasText: target }).waitFor({ timeout: 15_000 });
    assert.equal((await page.locator(LOOKING).textContent()).trim(), `Looking at ${target}`);
    await page.locator(CHIP, { hasText: `${target} today scanner` }).click();
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[0], { question: `${target} today scanner`, context: { ticker: target, day: "", fromPage: true, pageTicker: target, pageName: "Quick Options" } });
    onlyChatWrites(app);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("Charts & OI: moving the chart mid-conversation, a follow-up with no ticker is about the new ticker", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA" });
    await openDock(page);
    await page.locator(CHIP, { hasText: "NVDA today scanner" }).click();
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    const chart = card(page).locator('input[aria-label="Chart ticker"]');
    await chart.fill("spy");
    await chart.press("Enter");
    await waitForTape(page, { symbol: "SPY" });
    await page.locator(LOOKING, { hasText: "SPY" }).waitFor({ timeout: 15_000 });
    const input = page.locator(`${CHAT} input.chat-input`);
    await input.fill("why was it missed");
    await input.press("Enter");
    await page.locator(ANSWER).nth(1).waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[1], { question: "why was it missed", context: { ticker: "SPY", day: "", fromPage: true, pageTicker: "SPY", pageName: "Charts & OI" } });
    // That answer named no ticker, and the old NVDA answer must still not come back.
    await input.fill("and the day before");
    await input.press("Enter");
    await page.locator(ANSWER).nth(2).waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[2], { question: "and the day before", context: { ticker: "SPY", day: "", fromPage: true, pageTicker: "SPY", pageName: "Charts & OI" } });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a page with no ticker offers scanner questions, sends no ticker, and a long list scrolls inside the window", async () => {
  await withApp({ view: "Release Notes", fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await openDock(page);
    assert.equal(await page.locator(LOOKING).count(), 0);
    assert.deepEqual(await texts(page.locator(`${CHAT} .chat-tray .chat-chip`)), ["scanner at 9:30 give me the list of tickers", "which tickers stayed on top", "what is the bot doing"]);
    await page.locator(`${CHAT} .chat-tray .chat-chip`).first().click();
    const answer = page.locator(ANSWER).first();
    await answer.waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[0], { question: "scanner at 9:30 give me the list of tickers", context: { pageName: "Release Notes" } });
    assert.equal((await answer.locator(".chat-kind").textContent()).trim(), "Scanner list · 2026-11-09");
    const log = await page.locator(`${CHAT} .chat-log`).evaluate((node) => {
      const box = node.getBoundingClientRect();
      const question = node.querySelector(".chat-q").getBoundingClientRect();
      return { scrolls: node.scrollHeight > node.clientHeight, sideways: node.scrollWidth > node.clientWidth, fromTop: question.top - box.top };
    });
    assert.equal(log.scrolls, true, "a long list must scroll inside the window");
    assert.equal(log.sideways, false);
    assert.ok(log.fromTop >= -1 && log.fromTop < 40, `the list must be read from its first line, it is ${log.fromTop}px down`);
    const panel = await page.locator(PANEL).boundingBox();
    assert.ok(panel.height <= 561, "the window grew with the answer");
    assert.equal(await overflowsSideways(page), false);
    onlyChatWrites(app);
    assert.deepEqual(app.pageErrors, []);
  });
});

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`a regular user at ${label} gets the button and scanner questions only, and can ask`, async () => {
    const reply = answers()[CHAT_WRITE];
    // The 403 is what the server sent before its restart; the test below has the reply it sends after.
    const fakeWrites = { [CHAT_WRITE]: (write) => (JSON.parse(write.body).question === "show me more" ? { status: 403, json: { error: "admin only" } } : reply(write)) };
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeReads: { [STATUS]: member }, fakeWrites }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      assert.deepEqual(await texts(page.locator(`${CHAT} .chat-tray .chat-chip`)), ["scanner at 9:30 give me the list of tickers", "which tickers stayed on top"]);
      assert.equal((await page.locator(`${CHAT} .chat-note`).textContent()).trim(), "Answers come from the app's saved market data. It only reads.");
      assert.equal(await page.locator(BUTTON).getAttribute("title"), "Ask about a ticker or the scanner");
      const input = page.locator(`${CHAT} input.chat-input`);
      assert.match(await input.getAttribute("placeholder"), /scanner at 9:30/);
      assert.doesNotMatch([await page.locator(PANEL).innerText(), await page.locator(BUTTON).getAttribute("title"), await input.getAttribute("placeholder")].join(" "), OWNER_WORDS);

      const chip = page.locator(`${CHAT} .chat-tray .chat-chip`).first();
      await (viewport === PHONE ? chip.tap() : chip.click());
      await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
      assert.deepEqual(bodies(app)[0], { question: "scanner at 9:30 give me the list of tickers", context: { pageName: "Release Notes" } });
      assert.equal((await page.locator(`${ANSWER} .chat-kind`).first().textContent()).trim(), "Scanner list · 2026-11-09");

      await input.fill("show me more");
      await input.press("Enter");
      await page.locator(ANSWER, { hasText: "That is not open on this account. You can ask what the scanner showed." }).waitFor({ timeout: 15_000 });
      assert.doesNotMatch(await page.locator(PANEL).innerText(), OWNER_WORDS);
      assert.equal(await overflowsSideways(page), false);
      onlyChatWrites(app);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`a regular user at ${label} who asks something else sees the server's real answer, with none of the owner's words`, async () => {
    const refusal = serverRefusal();
    assert.equal(refusal.intent, "help");
    assert.equal(refusal.scope, "scanner");
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeReads: { [STATUS]: member }, fakeWrites: { [CHAT_WRITE]: { status: 200, json: refusal } } }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      const input = page.locator(`${CHAT} input.chat-input`);
      await input.fill("thanks");
      await input.press("Enter");
      const answer = page.locator(ANSWER).first();
      await answer.waitFor({ timeout: 15_000 });
      assert.equal((await answer.locator(".chat-kind").textContent()).trim(), "What I can answer");
      assert.ok((await answer.innerText()).includes(refusal.lines[0]), "the server's line is not on screen");
      assert.deepEqual(await texts(page.locator(CHIP)), refusal.suggest);      // the answer's questions sit in the tray, always in view
      assert.doesNotMatch(await page.locator(PANEL).innerText(), OWNER_WORDS);
      assert.equal(await overflowsSideways(page), false);
      onlyChatWrites(app);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("a regular user on a chart gets scanner questions about the ticker on screen", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"] }, fakeReads: { [STATUS]: member }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA" });
    await openDock(page);
    assert.equal((await page.locator(LOOKING).textContent()).trim(), "Looking at NVDA");
    assert.deepEqual(await texts(page.locator(`${CHAT} .chat-tray .chat-chip`)),
      ["What level we have in chart", "NVDA signals on 5m", "NVDA today scanner", "call wall on NVDA"]);
    assert.doesNotMatch(await page.locator(PANEL).innerText(), OWNER_WORDS);
    await page.locator(CHIP).first().click();
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[0], { question: "What level we have in chart", context: { ticker: "NVDA", day: "", fromPage: true, pageTicker: "NVDA", pageName: "Charts & OI" } });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("no chat during a forced password change or in a pop-out window", async () => {
  await withApp({ view: "Settings", fakeReads: { [STATUS]: mustChange } }, async (app) => {
    await app.page.locator(".account-security-notice").waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(1500);
    assert.equal(await app.page.locator(BUTTON).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
  await withApp({ path: "/?popout=chart&symbol=SPY&timeframe=5m" }, async (app) => {
    await app.page.locator("[data-testid='chart-popout-root']").waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(1500);
    assert.equal(await app.page.locator(BUTTON).count(), 0);
  });
  await withApp({ path: "/?popout=chart&symbol=SPY&timeframe=5m", fakeReads: { [STATUS]: member } }, async (app) => {
    await app.page.locator("[data-testid='chart-popout-root']").waitFor({ timeout: 60_000 });
    await app.page.waitForTimeout(1500);
    assert.equal(await app.page.locator(BUTTON).count(), 0);
  });
});

e2eTest("with the full-screen option chain over the window, one Esc leaves the chain and the chat keeps its own Esc", async () => {
  await withApp({ view: "Charts & OI", storage: await chartStorage(), fakeReads: CHAIN_READS, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await page.locator(`${CHAIN} > header`, { hasText: "Spot $100" }).waitFor({ timeout: 90_000 });
    await openDock(page);
    await page.locator(`${CHAIN} > header button[aria-label="Fill the screen with the option chain"]`).click();
    await page.locator(`${CHAIN}.is-fullscreen`).waitFor({ timeout: 10_000 });
    assert.equal(await onTop(page, `${CHAT} input.chat-input`), false, "the chain should be over the window");
    await page.keyboard.press("Escape");
    await page.waitForFunction((selector) => !document.querySelector(`${selector}.is-fullscreen`), CHAIN, { timeout: 5_000 });
    assert.equal(await page.locator(PANEL).isVisible(), true, "Esc closed the chat behind the chain");
    await page.keyboard.press("Escape");
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("with the saved grids menu open beside the window, one Esc closes the menu and the chat stays open", async () => {
  await withCharts({ workspace: { symbols: ["NVDA"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA" });
    await openDock(page);
    await page.locator(GRIDS_BUTTON).first().click();
    await page.locator(GRIDS_MENU).waitFor({ timeout: 10_000 });
    assert.equal(await onTop(page, PANEL), true, "the menu must not reach the middle of the window for this case");
    await page.keyboard.press("Escape");
    await page.locator(GRIDS_MENU).waitFor({ state: "detached", timeout: 5_000 });
    assert.equal(await page.locator(PANEL).isVisible(), true, "Esc closed the chat instead of the menu");
    await page.keyboard.press("Escape");
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`an alert's X can be pressed with the chat button on screen: ${label}`, async () => {
    const feed = alertFeed();
    await withApp({ view: "Watchlist", ...(viewport ? { viewport } : {}), fakeReads: { ...feed.reads, "GET /api/dashboard": { json: { watchlist: ["NVDA"] } } } }, async (app) => {
      await toastCanBeDismissed(app.page, feed);
      assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("an alert's X can be pressed on a full-screen chart", async () => {
  const feed = alertFeed();
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeReads: feed.reads }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await card(page, 0).getByRole("button", { name: "Maximize AAPL", exact: true }).click();
    await page.locator("section.oi-finder-chart-card.is-maximized").waitFor({ timeout: 10_000 });
    await toastCanBeDismissed(page, feed);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("the Bot page's Ask tab has its own chat, so the window stands aside there and comes back after", async () => {
  await withApp({ view: "Bot", storage: { "bot.view": "ask" }, fakeReads: { "GET /api/momx-scanner/bot-desk": { json: { available: false, accounts: {}, limits: {} } } } }, async (app) => {
    const { page } = app;
    await page.locator("[data-testid='bot-chat']").waitFor({ timeout: 60_000 });
    await page.waitForTimeout(1500);
    assert.equal(await page.locator(BUTTON).isVisible(), false);
    await page.locator("[data-testid='bh-panels'] button", { hasText: "Scorecard" }).click();
    await page.locator(BUTTON).waitFor({ state: "visible", timeout: 15_000 });
    await page.locator(BUTTON).click();
    await page.locator(PANEL).waitFor({ state: "visible", timeout: 15_000 });
    await page.locator("[data-testid='bh-panels'] button", { hasText: "Ask" }).click();
    await page.locator("[data-testid='bot-chat']").waitFor({ timeout: 15_000 });
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(await page.locator(BUTTON).isVisible(), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("big screen and full screen: the button clears the exit button and the chart's scrollbar, and Esc closes only the window", async () => {
  await withCharts({ workspace: { layoutId: "two-columns", symbols: ["AAPL"], timeframes: ["5m", "15m"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { index: 1, symbol: "AAPL" });
    await page.locator('button.oi-chart-workspace-toggle[aria-label="Open the big screen workspace"]').click();
    const workspace = page.locator("section.oi-chart-multilayout.is-workspace-maximized");
    await workspace.waitFor({ timeout: 10_000 });
    await page.waitForTimeout(500);
    const button = await page.locator(BUTTON).boundingBox();
    const exit = await page.locator(".oi-chart-multilayout.is-workspace-maximized .oi-chart-workspace-exit").boundingBox();
    if (exit) assert.equal(overlap(button, exit), false, "the button covers Exit");
    for (const bar of await page.locator(".oi-chart-scrollbar").evaluateAll((nodes) => nodes.map((n) => { const b = n.getBoundingClientRect(); return { x: b.x, y: b.y, width: b.width, height: b.height }; }))) {
      assert.equal(overlap(button, bar), false, "the button covers a chart scrollbar");
    }
    assert.equal(await onTop(page, BUTTON), true, "the button is behind the chart");
    await page.locator(BUTTON).click();
    await page.locator(PANEL).waitFor({ state: "visible", timeout: 15_000 });
    assert.equal(await onTop(page, `${CHAT} input.chat-input`), true, "the window is behind the chart");
    await page.keyboard.press("Escape");
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(await workspace.count(), 1, "Esc also left the big screen");
    assert.deepEqual(app.pageErrors, []);
  });
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await card(page, 0).getByRole("button", { name: "Maximize AAPL", exact: true }).click();
    await page.locator("section.oi-finder-chart-card.is-maximized").waitFor({ timeout: 10_000 });
    await page.waitForTimeout(500);
    const button = await page.locator(BUTTON).boundingBox();
    const bar = await page.locator("section.oi-finder-chart-card.is-maximized .oi-chart-scrollbar").boundingBox();
    assert.ok(bar && !overlap(button, bar), "the button covers the full-screen chart's scrollbar");
    assert.equal(await onTop(page, BUTTON), true, "the button is behind the full-screen chart");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("phone width on a chart: clear of the scrollbar, time axis and tabs; the window fits above the tabs and never sideways", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA" });
    await page.locator(BUTTON).waitFor({ timeout: 60_000 });
    const nav = await page.locator(NAV).boundingBox();
    const bar = await page.locator(".oi-chart-scrollbar").first().boundingBox();
    const button = await page.locator(BUTTON).boundingBox();
    assert.equal(overlap(button, nav), false, "the button covers the tabs");
    assert.ok(button.y + button.height <= bar.y - 24, `the button reaches the chart's time axis: ${JSON.stringify({ button, bar })}`);
    assert.equal(await onTop(page, BUTTON), true);

    await openDock(page, true);
    assert.equal((await page.locator(LOOKING).textContent()).trim(), "Looking at NVDA");
    await page.locator("[data-testid='chat-history-button']").waitFor({ timeout: 15_000 });
    await page.locator(`${CHAT} input.chat-input`).fill("scanner at 9:30 give me the list of tickers");
    await page.locator(`${CHAT} button.chat-send`).tap();
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    const panel = await page.locator(PANEL).boundingBox();
    const topBar = await page.locator(".topbar.trading-header").boundingBox();
    assert.ok(panel.x <= 1 && Math.abs(panel.width - PHONE.width) <= 1 && Math.abs(panel.y - (topBar ? topBar.y + topBar.height : 0)) <= 1, `not full screen under the top bar (a phone chart has none): ${JSON.stringify({ panel, topBar })}`);
    // The top bar and any alert hanging from it sit above the chat, so these are checked by what a tap would really land on.
    assert.equal(await onTop(page, "[data-testid='chat-dock-close']"), true, "Close is covered");
    assert.equal(await onTop(page, "[data-testid='chat-history-button']"), true, "History is covered");
    assert.ok(panel.y >= 0 && panel.y + panel.height <= nav.y && nav.y - (panel.y + panel.height) <= 12, `the window should end at the tabs: ${JSON.stringify({ panel, nav })}`);
    for (const target of [`${CHAT} form.chat-form`, `${CHAT} button.chat-send`, "[data-testid='chat-dock-close']"]) {
      const box = await page.locator(target).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1 && box.y + box.height <= nav.y, `${target} is cut off`);
    }
    assert.equal(await page.locator(`${CHAT} .chat-log`).evaluate((n) => n.scrollHeight > n.clientHeight && n.scrollWidth <= n.clientWidth), true);
    assert.equal(await overflowsSideways(page), false);
    await page.locator("[data-testid='chat-dock-close']").tap();
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    onlyChatWrites(app);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("phone full-screen chart: the button and the window are on top and clear of the scrollbar, time axis and tabs", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["NVDA"] }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "NVDA" });
    await card(page, 0).getByRole("button", { name: "Maximize NVDA", exact: true }).tap();
    await page.locator("section.oi-finder-chart-card.is-maximized").waitFor({ timeout: 10_000 });
    await page.waitForTimeout(500);
    await page.locator(BUTTON).waitFor({ timeout: 60_000 });
    assert.equal(await onTop(page, BUTTON), true, "the full-screen chart hides the button");
    const button = await page.locator(BUTTON).boundingBox();
    const nav = await page.locator(NAV).boundingBox();
    const bar = await page.locator("section.oi-finder-chart-card.is-maximized .oi-chart-scrollbar").boundingBox();
    assert.equal(overlap(button, nav), false, "the button covers the tabs");
    assert.ok(bar && button.y + button.height <= bar.y - 24, `the button reaches the chart's time axis: ${JSON.stringify({ button, bar })}`);

    await openDock(page, true);
    assert.equal(await onTop(page, `${CHAT} input.chat-input`), true, "the full-screen chart hides the window");
    assert.equal(await onTop(page, "[data-testid='chat-dock-close']"), true);
    const panel = await page.locator(PANEL).boundingBox();
    assert.ok(panel.y >= 0 && panel.y + panel.height <= nav.y, `the window covers the tabs: ${JSON.stringify({ panel, nav })}`);
    await page.locator("[data-testid='chat-dock-close']").tap();
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });

    await openIndicators(page);       // a sheet that fills the card: the button waits under it, as it does on an ordinary chart
    assert.equal(await onTop(page, BUTTON), false, "the button sits on the indicator sheet");
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("phone width on the scanner: the button does not sit on the TOP button", async () => {
  await withApp({ view: "MomX Scanner", viewport: PHONE, storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist" } }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await page.locator("label.momx-toggle", { hasText: "Scan matches only" }).locator("input").uncheck();
    await page.waitForFunction(() => document.querySelectorAll("tr.momx-row").length > 40, null, { timeout: 60_000 });
    await page.locator("[data-testid='momx-scanner-view']").evaluate((node) => { node.scrollTop = 900; node.dispatchEvent(new Event("scroll")); });
    const top = page.locator("[data-testid='momx-to-top']");
    await top.waitFor({ timeout: 5_000 });
    await page.locator(BUTTON).waitFor({ timeout: 60_000 });
    assert.equal(overlap(await page.locator(BUTTON).boundingBox(), await top.boundingBox()), false, "the button sits on TOP");
    assert.equal(await onTop(page, "[data-testid='momx-to-top']"), true);
    assert.equal(await onTop(page, BUTTON), true);
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a narrow desktop window still fits the chat window", async () => {
  await withApp({ view: "Release Notes", viewport: { width: 800, height: 500 }, fakeWrites: answers() }, async (app) => {
    const { page } = app;
    await openDock(page);
    const panel = await page.locator(PANEL).boundingBox();
    assert.ok(panel.x >= 0 && panel.y >= 0 && panel.x + panel.width <= 800 && panel.y + panel.height <= 500, `cut off: ${JSON.stringify(panel)}`);
    assert.equal(await overflowsSideways(page), false);
    assert.deepEqual(app.pageErrors, []);
  });
});

// ---- Ask as an app-wide search (2026-11-11): cards, tray, buttons to the page, history, tables. ----
const HOME_CARDS = [
  { kind: "page_ticker", title: "Keep looking at NVDA", lines: ["$190.10", "VWAP $188.40"], asOf: "as of 15:59 ET", links: [{ label: "Open the chart", view: "Sectors" }], question: "NVDA price" },
  { kind: "scanner_top", title: "Top of the scanner now", lines: ["Mag7: NVDA, AAPL, TSLA"], asOf: "as of 15:55 ET", links: [], question: "which tickers stayed on top today" },
  { kind: "walls", title: "NVDA high OI walls", lines: [], asOf: null, links: [], question: "call wall on NVDA" },
  { kind: "bot_trades", title: "Open bot trades", lines: ["2 open"], asOf: "as of 15:59 ET", links: [{ label: "Bot", view: "Bot" }], question: "what is the bot doing" },
];
const CHAT_LINK = { label: "Go to Sectors", view: "Sectors" };
const ROWS_TABLE = { kind: "rows", title: "Top 12 Mag7 at 10:15", note: "Saved board.", columns: [{ key: "symbol", label: "Symbol" }, { key: "price", label: "Price", align: "right" }, { key: "rvol", label: "RVOL", align: "right" }],
  rows: Array.from({ length: 12 }, (_, i) => ({ symbol: `T${"ABCDEFGHIJKL"[i]}`, price: `${100 + i}.00`, rvol: `${i + 1}.2` })), link: { label: "Open Mag7 History", view: "Sectors" } };
const DAY_TABLE = { kind: "scanner_day", title: "NVDA on the scanner", ticker: "NVDA", day: "2026-11-09", sections: [{ list: "Mag7", side: "bull" }] };
const SEARCH = {
  "where is sectors": { intent: "open_page", ticker: null, day: null, suggest: ["which sector is hot"], links: [CHAT_LINK], asOf: "as of 15:59 ET", lines: ["Sectors is one tap away."] },
  "long one": { intent: "scanner_list", ticker: null, day: "2026-11-09", suggest: ["short follow-up"], links: [CHAT_LINK], lines: LONG_LIST },
  "top 12": { intent: "scanner_list", ticker: null, day: "2026-11-09", suggest: [], links: [], table: ROWS_TABLE, lines: ["Twelve names."] },
  "NVDA day": { intent: "scanner_day", ticker: "NVDA", day: "2026-11-09", suggest: [], links: [CHAT_LINK], table: DAY_TABLE, lines: ["Mag7 calls: first 09:31, last 15:55."] },
  "poison": { intent: "help", ticker: null, day: null, suggest: [], links: [{ label: "Bot", view: "Bot" }, { label: "Paper", view: "Paper Trading" }, CHAT_LINK], lines: ["Market data only."] },
  // 2026-11-11 design change: a meaning is the glossary's line; a "general" field (AI text) from any server is never shown.
  "what is rvol": { intent: "define", ticker: null, day: null, suggest: [], links: [{ label: "Open Learn + Setup", view: "Learn + Setup" }],
    general: "Buy RVOL spikes now.", lines: ["RVOL: Relative volume (RVOL) measures how unusual a bar's volume is."] },
};

function searchAnswers(home = { cards: HOME_CARDS, chips: [] }) {
  return { [CHAT_WRITE]: (write) => {
    const body = JSON.parse(write.body);
    if (body.home) return { json: { home } };
    return { json: SEARCH[body.question] || REPLIES[body.question] || { intent: "help", ticker: null, day: null, suggest: [], lines: ["No match."] } };
  } };
}

async function ask(page, question) {
  const input = page.locator(`${CHAT} input.chat-input`);
  await input.fill(question);
  await input.press("Enter");
}

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`the opening screen at ${label}: heading, cards with their time, no empty card, one read, and a card asks its question`, async () => {
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeWrites: searchAnswers() }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      assert.equal((await page.locator(`${CHAT} .chat-hello`).textContent()).trim(), "How can I help?");
      const cards = page.locator(`${CHAT} [data-testid='chat-home-card']`);
      await cards.first().waitFor({ timeout: 15_000 });
      assert.deepEqual(await texts(page.locator(`${CHAT} .chat-card-title`)), ["Keep looking at NVDA", "Top of the scanner now", "Open bot trades"]);
      assert.equal((await cards.first().locator(".chat-card-time").textContent()).trim(), "as of 15:59 ET");
      assert.deepEqual(await texts(cards.first().locator(".chat-card-line")), ["$190.10"], "no VWAP on the cards (his rule, 2026-11-11)");
      assert.deepEqual(homeReads(app), [{ home: true, context: { pageTicker: "", pageName: "Release Notes" } }]);
      await page.waitForTimeout(3000);
      assert.equal(homeReads(app).length, 1, "the opening screen must not poll");
      assert.equal(bodies(app).length, 0, "the opening screen must not use up a question");
      assert.equal(await overflowsSideways(page), false);
      assert.equal(await page.locator(`${CHAT} form.chat-form`).isVisible(), true);
      const main = cards.first().locator(".chat-card-main");
      await (viewport === PHONE ? main.tap() : main.click());
      await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
      assert.equal(bodies(app)[0].question, "NVDA price");
      assert.equal(await page.locator(`${CHAT} .chat-hello`).count(), 0);
      assert.equal(homeReads(app).length, 1);
      await page.locator("[data-testid='chat-new']").click();
      await page.locator(`${CHAT} [data-testid='chat-home-card']`).first().waitFor({ timeout: 15_000 });
      for (let waited = 0; homeReads(app).length < 2 && waited < 5_000; waited += 100) await sleep(100);
      assert.equal(homeReads(app).length, 2, "a new chat reads the cards once more");
      onlyChatWrites(app);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`a regular user at ${label} sees no owner card, word or link, even if the server sent one`, async () => {
    const home = { cards: HOME_CARDS, chips: ["what is the bot doing", "which sector is hot"] };
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeReads: { [STATUS]: member }, fakeWrites: searchAnswers(home) }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      await page.locator(`${CHAT} [data-testid='chat-home-card']`).first().waitFor({ timeout: 15_000 });
      assert.deepEqual(await texts(page.locator(`${CHAT} .chat-card-title`)), ["Keep looking at NVDA", "Top of the scanner now"]);
      await page.locator(CHIP, { hasText: "which sector is hot" }).waitFor({ timeout: 5_000 });
      assert.deepEqual(await texts(page.locator(CHIP)), ["which sector is hot"]);
      assert.doesNotMatch(await page.locator(PANEL).innerText(), OWNER_WORDS);
      await ask(page, "poison");
      const answer = page.locator(ANSWER).first();
      await answer.waitFor({ timeout: 15_000 });
      assert.deepEqual(await texts(answer.locator("[data-testid='chat-link']")), ["Go to Sectors"]);
      assert.doesNotMatch(await page.locator(PANEL).innerText(), OWNER_WORDS);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`the tray and the ask box never scroll away at ${label}`, async () => {
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeWrites: searchAnswers() }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      await ask(page, "long one");
      await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
      const tray = page.locator(`${CHAT} [data-testid='chat-tray']`);
      assert.deepEqual(await texts(tray.locator("button")), ["Go to Sectors", "short follow-up"]);
      assert.deepEqual(await texts(page.locator(`${ANSWER} .chat-chip`)), [], "follow-ups no longer sit at the end of the answer");
      const before = await tray.boundingBox();
      const log = page.locator(`${CHAT} .chat-log`);
      assert.equal(await log.evaluate((n) => n.scrollHeight > n.clientHeight), true);
      await log.evaluate((n) => { n.scrollTop = n.scrollHeight; });
      await page.waitForTimeout(150);
      const after = await tray.boundingBox();
      assert.deepEqual([after.x, after.y], [before.x, before.y], "the tray moved with the conversation");
      const logBox = await log.boundingBox();
      const form = await page.locator(`${CHAT} form.chat-form`).boundingBox();
      const panel = await page.locator(PANEL).boundingBox();
      assert.ok(logBox.y + logBox.height <= after.y + 1 && after.y + after.height <= form.y + 1 && form.y + form.height <= panel.y + panel.height + 1, JSON.stringify({ logBox, after, form, panel }));
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`a button in an answer goes to the page at ${label}`, async () => {
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeWrites: searchAnswers() }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      await ask(page, "where is sectors");
      const answer = page.locator(ANSWER).first();
      await answer.waitFor({ timeout: 15_000 });
      assert.equal((await answer.locator("[data-testid='chat-as-of']").textContent()).trim(), "as of 15:59 ET");
      const link = answer.locator("[data-testid='chat-link']", { hasText: "Go to Sectors" });
      await (viewport === PHONE ? link.tap() : link.click());
      await page.waitForFunction(() => window.localStorage.getItem("agenticActiveView") === "Sectors", null, { timeout: 15_000 });
      if (viewport === PHONE) await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
      else assert.equal(await page.locator(PANEL).isVisible(), true, "a desktop keeps the chat beside the page");
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

// Changed on purpose (2026-11-11 design change): general knowledge is the glossary's line, never AI text.
e2eTest("a meaning is the glossary's line and AI-written general text is never shown", async () => {
  await withApp({ view: "Release Notes", fakeWrites: searchAnswers() }, async (app) => {
    const { page } = app;
    await openDock(page);
    await ask(page, "what is rvol");
    const answer = page.locator(ANSWER).last();
    await answer.locator(".chat-line").first().waitFor({ timeout: 15_000 });
    // textContent: the answer's kind label is drawn in capitals by the CSS, so innerText reads "GLOSSARY".
    assert.match(await answer.textContent(), /Glossary/);
    assert.match(await answer.innerText(), /Relative volume \(RVOL\) measures/);
    assert.equal(await answer.locator("[data-testid='chat-general']").count(), 0);
    assert.doesNotMatch(await answer.innerText(), /Buy RVOL spikes/);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the real wording: on AAPL's chart, after talking about ZETA, the screen's ticker goes apart from the conversation's", async () => {
  const fakeWrites = { [CHAT_WRITE]: (write) => {
    const body = JSON.parse(write.body);
    if (body.home) return { json: { home: { cards: [], chips: [] } } };
    return { json: body.question === "see ZETA" ? { intent: "show", ticker: "ZETA", day: "2026-11-09", suggest: [], lines: ["ZETA on file."] } : { intent: "chart_levels", ticker: "AAPL", day: null, suggest: [], lines: ["AAPL levels."] } };
  } };
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await openDock(page);
    await ask(page, "see ZETA");
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    await page.locator(CHIP).first().waitFor({ timeout: 5_000 });
    await ask(page, "What level we have in chart");
    await page.locator(ANSWER).nth(1).waitFor({ timeout: 15_000 });
    assert.deepEqual(bodies(app)[1], { question: "What level we have in chart", context: { ticker: "ZETA", day: "2026-11-09", pageTicker: "AAPL", pageName: "Charts & OI" } });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("moving the chart while the opening screen is up drops the old ticker's cards and reads the new one's", async () => {
  const fakeWrites = { [CHAT_WRITE]: (write) => {
    const body = JSON.parse(write.body);
    const symbol = body.context?.pageTicker || "none";
    return { json: { home: { cards: [{ kind: "page_ticker", title: `Keep looking at ${symbol}`, lines: ["x"], asOf: "as of 15:59 ET", links: [], question: `${symbol} price` }], chips: [] } } };
  } };
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await openDock(page);
    await page.locator(`${CHAT} .chat-card-title`, { hasText: "AAPL" }).waitFor({ timeout: 15_000 });
    const input = card(page).locator('input[aria-label="Chart ticker"]');
    await input.fill("spy");
    await input.press("Enter");
    await waitForTape(page, { symbol: "SPY" });
    await page.locator(`${CHAT} .chat-card-title`, { hasText: "SPY" }).waitFor({ timeout: 15_000 });
    assert.equal(await page.locator(`${CHAT} .chat-card-title`, { hasText: "AAPL" }).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("history: earlier questions come back, are searchable, and Esc closes only the list", async () => {
  await withApp({ view: "Release Notes", fakeWrites: searchAnswers() }, async (app) => {
    const { page } = app;
    await openDock(page);
    const button = page.locator("[data-testid='chat-history-button']");
    await button.waitFor({ timeout: 15_000 });
    await ask(page, "where is sectors");
    await page.locator(ANSWER).first().waitFor({ timeout: 15_000 });
    await ask(page, "top 12");
    await page.locator(ANSWER).nth(1).waitFor({ timeout: 15_000 });
    await button.click();
    const items = page.locator("[data-testid='chat-history-item']");
    await items.first().waitFor({ timeout: 5_000 });
    assert.equal(await items.count(), 2);
    assert.match(await items.first().innerText(), /top 12/);
    await page.locator("[data-testid='chat-history-search']").fill("sectors");
    assert.equal(await items.count(), 1);
    await page.keyboard.press("Escape");
    await page.locator("[data-testid='chat-history']").waitFor({ state: "hidden", timeout: 5_000 });
    assert.equal(await page.locator(PANEL).isVisible(), true, "Esc closed the window instead of the list");
    await button.click();
    await page.locator("[data-testid='chat-history-search']").fill("sectors");
    await items.first().click();
    await page.locator(ANSWER).nth(2).waitFor({ timeout: 15_000 });
    assert.equal(bodies(app)[2].question, "where is sectors");
    assert.equal(await page.locator("[data-testid='chat-history']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("history on a shared device: another account's questions are never shown", async () => {
  const other = JSON.stringify([{ q: "someone else's question", at: "" }]);
  const mine = JSON.stringify([{ q: "my own question", at: "" }]);
  await withApp({ view: "Release Notes", storage: { "agx.chat.history.other%40example.com": other, "agx.chat.history.e2e-user-1": mine }, fakeReads: { [STATUS]: member }, fakeWrites: searchAnswers() }, async (app) => {
    const { page } = app;
    await openDock(page);
    const button = page.locator("[data-testid='chat-history-button']");
    await button.waitFor({ timeout: 15_000 });
    await button.click();
    await page.locator("[data-testid='chat-history-item']").first().waitFor({ timeout: 5_000 });
    assert.deepEqual(await texts(page.locator("[data-testid='chat-history-item']")), ["my own question"]);
    assert.deepEqual(app.pageErrors, []);
  });
});

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`a table at ${label}: short cards in the answer with no scroller of their own, the whole table in a sheet with one`, async () => {
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeWrites: searchAnswers() }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      await ask(page, "top 12");
      const table = page.locator(`${ANSWER} [data-testid='chat-table']`);
      await table.waitFor({ timeout: 15_000 });
      assert.equal(await table.locator("[data-testid='chat-row-card']").count(), 5);
      assert.deepEqual(await table.locator("[data-testid='chat-row-link']").first().textContent().then((s) => s.trim()), "Open TA");
      const scrollers = await table.evaluate((root) => [root, ...root.querySelectorAll("*")].filter((n) => /auto|scroll/.test(getComputedStyle(n).overflowX + getComputedStyle(n).overflowY)).length);
      assert.equal(scrollers, 0, "a scroller inside the scrolling conversation");
      assert.equal(await page.locator(`${CHAT} .chat-log`).evaluate((n) => n.scrollWidth <= n.clientWidth), true);

      await table.locator("[data-testid='chat-table-open']").click();
      const sheet = page.locator("[data-testid='chat-table-sheet']");
      await sheet.waitFor({ timeout: 5_000 });
      assert.equal(await sheet.locator("tbody tr").count(), 12);
      assert.deepEqual(await texts(sheet.locator("thead th")), ["Symbol", "Price", "RVOL", ""]);
      assert.equal(await sheet.evaluate((root) => [...root.querySelectorAll("*")].filter((n) => /auto|scroll/.test(getComputedStyle(n).overflowX + getComputedStyle(n).overflowY)).length), 1, "the sheet must have exactly one scroller");
      assert.equal(await overflowsSideways(page), false);
      await page.keyboard.press("Escape");
      await sheet.waitFor({ state: "detached", timeout: 5_000 });
      assert.equal(await page.locator(PANEL).isVisible(), true, "Esc closed the window behind the sheet");
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`a scanner day at ${label}: the sheet opens once by itself, Esc returns to the chat, and the answer can reopen it`, async () => {
    const fakeReads = { "GET /api/momx-scanner/history": { json: { rows: [], totalRows: 0, hasMore: false } } };
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeReads, fakeWrites: searchAnswers() }, async (app) => {
      const { page } = app;
      await openDock(page, viewport === PHONE);
      await ask(page, "NVDA day");
      const sheet = page.locator("[data-testid='chat-scanner-day']");
      await sheet.waitFor({ timeout: 30_000 });
      await page.waitForTimeout(500);
      await page.keyboard.press("Escape");
      await sheet.waitFor({ state: "detached", timeout: 5_000 });
      assert.equal(await page.locator(PANEL).isVisible(), true, "Esc closed the window behind the sheet");
      await page.waitForTimeout(500);
      assert.equal(await sheet.count(), 0, "the sheet opened a second time by itself");
      await page.locator(`${ANSWER} [data-testid='chat-table-open']`).click();
      await sheet.waitFor({ timeout: 15_000 });
      await page.locator("[data-testid='chat-scanner-day-close']").click();
      await sheet.waitFor({ state: "detached", timeout: 5_000 });
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("phone width on a page with the top bar: the chat starts under it, and Close and History can be pressed even with an alert showing", async () => {
  await withApp({ view: "Release Notes", viewport: PHONE, fakeWrites: searchAnswers() }, async (app) => {
    const { page } = app;
    await openDock(page, true);
    await page.locator("[data-testid='chat-history-button']").waitFor({ timeout: 15_000 });
    const bar = await page.locator(".topbar.trading-header").boundingBox();
    const panel = await page.locator(PANEL).boundingBox();
    assert.ok(bar && Math.abs(panel.y - (bar.y + bar.height)) <= 1, `the chat should start under the top bar: ${JSON.stringify({ bar, panel })}`);
    assert.equal(await onTop(page, "[data-testid='chat-dock-close']"), true, "Close is covered");
    assert.equal(await onTop(page, "[data-testid='chat-history-button']"), true, "History is covered");
    assert.equal(await onTop(page, `${CHAT} input.chat-input`), true, "the ask box is covered");
    // Whether a real alert is up depends on the live accounts, so one is hung from the top bar here with the app's own class.
    await page.evaluate(() => {
      if (document.querySelector(".global-price-alert-toast")) return;
      const toast = document.createElement("button");
      toast.className = "global-price-alert-toast";
      toast.textContent = "Test alert";
      document.querySelector(".topbar.trading-header").appendChild(toast);
    });
    const head = await page.locator(`${PANEL} .chat-dock-head`).boundingBox();
    const toast = await page.locator(".global-price-alert-toast").first().boundingBox();
    assert.ok(toast.y >= head.y + head.height - 1, `the alert covers the chat's heading: ${JSON.stringify({ head, toast })}`);
    assert.equal(await onTop(page, "[data-testid='chat-dock-close']"), true, "an alert covers Close");
    assert.equal(await onTop(page, "[data-testid='chat-history-button']"), true, "an alert covers History");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the history button comes back on the next open if the app could not say who is signed in", async () => {
  let refuse = true;
  const status = () => (refuse ? { status: 503, json: {} } : member);
  await withApp({ view: "Release Notes", fakeWrites: searchAnswers() }, async (app) => {
    const { page } = app;
    await page.locator(BUTTON).waitFor({ timeout: 60_000 });
    await page.route("**/api/auth/status", (route) => { const reply = status(); return route.fulfill({ status: reply.status || 200, contentType: "application/json", body: JSON.stringify(reply.json) }); });
    await page.locator(BUTTON).click();
    await page.locator(CHAT).waitFor({ timeout: 15_000 });
    await page.waitForTimeout(1000);
    assert.equal(await page.locator("[data-testid='chat-history-button']").count(), 0, "no account known, no history");
    refuse = false;
    await page.locator("[data-testid='chat-dock-close']").click();
    await page.locator(BUTTON).click();
    await page.locator("[data-testid='chat-history-button']").waitFor({ timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
