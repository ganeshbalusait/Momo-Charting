// Ask chat as an app-wide search (2026-11-11), the whole journey at desktop and phone width.
// The answers on screen are the REAL server's: before the specs run, the acceptance test asks the real
// /api/bot-chat on scratch files (AI faked) and saves what came back, and the browser plays those replies.
// So this proves the screen copes with what the server truly sends, not with what a spec author imagined.
// A link only moves the screen, so these specs also check that nothing but the chat's own read is posted.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { before } from "node:test";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, withApp } from "./harness.mjs";
import { waitForTape, withCharts } from "./charts-core.helpers.mjs";
import { CHAIN_READS, chainReply } from "./options-fixtures.mjs";
import { BOARD_STORAGE, boardReads } from "./scanner-board-fixtures.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const CHAT_WRITE = "POST /api/bot-chat";
const BUTTON = "[data-testid='chat-dock-button']";
const PANEL = "[data-testid='chat-dock-panel']";
const CHAT = "[data-testid='dock-chat']";
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const LINK = `${ANSWER} [data-testid='chat-link']`;
const TRAY = `${CHAT} [data-testid='chat-tray']`;
const SHEET = "[data-testid='chat-scanner-day']";
const SECTION = `${SHEET} [data-testid='chat-day-section']`;
const HISTORY_ROWS = "table.momx-history-table:not(.momx-day-table) tr.momx-row.momx-history-row";
const SEARCH = "input.momx-history-search[aria-label='Search history for a ticker']";
const DAY_PICKER = "[data-testid='momx-history-day']";
const STATUS = "GET /api/auth/status";
const HISTORY = "GET /api/momx-scanner/history";
const OWNER_WORDS = /\b(bots?|paper|scorecard)\b/i;
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-chat-search-")), "replies.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_chat_search_acceptance.py", "-q", "-p", "no:cacheprovider", "-k", "capture_real_replies"],
    { cwd: REPO, encoding: "utf8", timeout: 480_000, env: { ...process.env, CHAT_SEARCH_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 500_000 });

function snapshot(day, clock, symbol, hits) {
  const at = `${day}T${clock}:00-05:00`;
  return { symbol, at, date: day, isArrival: false, changed: [], firstSeenAt: at, lastSeenAt: at, hits, truncated: false, peakRvol: 3.2,
    row: { symbol, scanPass: true, pctChange: 2.4, last: 101.25, prevClose: 98.88, industry: "Semiconductors", scanReasons: ["rvol:5m"],
      rvol: { "5m": { value: 3.2, bg: "cyan", fg: "black" } }, sqz: {}, skittles: {}, highLow: { value: 0.6, bg: "green" }, color: { bg: "green" }, sparkline: [100, 100.5, 101.25], quoteTrend: [] } };
}

// momx/history.py's history_response. NVDA is on every board but Watchlist puts.
function archive(read) {
  const params = new URLSearchParams(read.search);
  const list = params.get("list");
  const bear = params.get("dir") === "bear";
  const symbol = (params.get("symbol") || "").toUpperCase();
  const day = params.get("date") || real.day;
  const base = { list, days: [real.day], retentionDays: 30, date: day, offset: 0, pageSize: 400, hasMore: false, truncated: false };
  const clocks = ["09:30", "10:00", "12:00"];
  if (symbol) {
    const rows = symbol === "NVDA" && !(list === "Watchlist" && bear) ? clocks.map((c) => snapshot(day, c, "NVDA", 5)) : [];
    return { json: { ...base, symbol, rows, totalRows: rows.length, returnedRows: rows.length } };
  }
  const rows = clocks.map((c) => snapshot(day, c, "NVDA", 5));
  return { json: { ...base, rows, totalRows: 3, returnedRows: 3, timeIndex: clocks.map((c, i) => [c, i, 1]), sessions: [["Open", 3]] } };
}

// The real replies, for the owner or a regular user.
// A real reply that carries a "more" token (news, long term, "is NVDA a buy") makes the screen read the second part
// once. The capture saves only the first parts, so that read is answered "busy" (the route's own refusal shape) and
// written down; these specs test links and layout, chat-report covers the second part with its captured reply.
// Before this, the read hit the assert below after the spec had moved on and failed the file with "asynchronous
// activity after the test ended" (2026-11-11).
function server(who) {
  const asked = [];
  const moreAsked = [];
  return { asked, moreAsked, writes: { [CHAT_WRITE]: (write) => {
    const body = JSON.parse(write.body);
    if (body.home) return { json: real[`${who}Home`] };
    if (body.more) {
      assert.ok(Object.values(real[who]).some((r) => r.more === body.more), `a second-part read for a token the server never sent: ${body.more}`);
      moreAsked.push(body.more);
      return { status: 503, json: { error: "busy" } };
    }
    asked.push(body.question);
    const reply = real[who][body.question];
    assert.ok(reply, `no real reply captured for "${body.question}"`);
    return { json: reply };
  } } };
}

async function openChat(page, tap) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 90_000 });
  if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
  await page.locator(`${CHAT} input.chat-input`).waitFor({ timeout: 15_000 });
}

async function ask(page, question, tap = false) {
  await openChat(page, tap);
  const earlier = await page.locator(ANSWER).count();
  const input = page.locator(`${CHAT} input.chat-input`);
  await input.fill(question);
  await input.press("Enter");
  await page.locator(ANSWER).nth(earlier).waitFor({ timeout: 15_000 });
}

async function follow(page, label, tap) {
  const link = page.locator(LINK, { hasText: label }).last();
  await link.waitFor({ timeout: 15_000 });
  await (tap ? link.tap() : link.click());
}

const inView = async (page, selector) => {
  const box = await page.locator(selector).boundingBox();
  const size = page.viewportSize();
  return Boolean(box) && box.y >= 0 && box.y + box.height <= size.height + 1 && box.x >= 0 && box.x + box.width <= size.width + 1;
};
const onlyChatWrites = (app) => {
  const background = new Set(["/api/bot-chat", "/api/news-feed/refresh", "/api/client-timing", "/api/chart-indicator-profiles"]);
  assert.deepEqual([...new Set(app.writes.map((w) => w.path))].filter((p) => !background.has(p)), []);
};
const closedOnPhone = async (page, tap) => { if (tap) await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 }); };

for (const [label, viewport] of WIDTHS) {
  const tap = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`opening screen at ${label}: the server's real cards with their times, and the ask box in view`, async () => {
    const { writes } = server("owner");
    await withApp({ view: "Release Notes", ...size, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await openChat(page, tap);
      await page.locator(`${CHAT} .chat-hello`).waitFor({ timeout: 15_000 });
      const cards = page.locator(`${CHAT} [data-testid='chat-home-card']`);
      await cards.first().waitFor({ timeout: 15_000 });
      assert.ok((await cards.evaluateAll((els) => els.map((el) => el.getAttribute("data-kind")))).includes("scanner"));
      assert.match(await page.locator(`${CHAT} [data-testid='chat-home-card'][data-kind='scanner']`).innerText(), /Mag7: NVDA/);
      assert.equal(await inView(page, `${CHAT} input.chat-input`), true, "the ask box is off screen");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`a long real answer at ${label}: the tray and the ask box stay in view`, async () => {
    const { writes } = server("owner");
    await withApp({ view: "Release Notes", ...size, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "chart levels nvda", tap);
      await page.locator(TRAY).waitFor({ timeout: 15_000 });
      assert.ok(await page.locator(`${TRAY} [data-testid='chat-tray-link']`).count(), "the tray has the answer's links");
      assert.equal(await inView(page, TRAY), true, "the tray scrolled away");
      assert.equal(await inView(page, `${CHAT} input.chat-input`), true, "the ask box scrolled away");
      await page.locator(`${ANSWER} [data-testid='chat-as-of']`).first().waitFor({ timeout: 5_000 });
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`"Nvda today scanner" at ${label}: the real reference opens the sheet in the scanner's columns`, async () => {
    const { writes } = server("owner");
    await withApp({ view: "Release Notes", ...size, fakeReads: { [HISTORY]: archive, "GET /api/momx-scanner/rule-record": { json: {} } }, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "Nvda today scanner", tap);
      await page.locator(SHEET).waitFor({ timeout: 30_000 });
      await page.locator(`${SECTION}[data-list="Watchlist"][data-side="bear"][data-folded="true"]`).waitFor({ timeout: 30_000 });
      const heads = await page.locator(`${SHEET} table.momx-day-table`).first().locator("thead tr.momx-head-row th[data-col]").evaluateAll((ths) => ths.map((th) => th.getAttribute("data-col")));
      for (const key of ["industry", "symbol", "time", "setup", "fresh", "pctChange", "price", "news", "event", "highLow"]) assert.ok(heads.includes(key), `no ${key} column: ${heads}`);
      const sent = app.reads.filter((r) => r.path === "/api/momx-scanner/history").map((r) => new URLSearchParams(r.search));
      assert.ok(sent.some((p) => p.get("dir") === "bear" && p.get("symbol") === "NVDA" && p.get("date") === real.day), "puts must be read with dir=bear");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
      onlyChatWrites(app);
    });
  }, { timeout: 240_000 });

  e2eTest(`the real scanner link at ${label} lands on NVDA in History at 09:30`, async () => {
    const { writes } = server("owner");
    await withApp({ view: "Release Notes", ...size, storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist", "momx.scanner.activeView": "live" }, fakeReads: { ...boardReads(), [HISTORY]: archive }, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "take me to nvda on the scanner at 9:30", tap);
      await follow(page, "Mag7 History", tap);
      await page.waitForFunction(([s, want]) => document.querySelector(s)?.value === want, [SEARCH, "NVDA"], { timeout: 30_000 });
      await page.waitForFunction(([s, want]) => document.querySelector(s)?.value === want, [DAY_PICKER, real.day], { timeout: 30_000 });
      await page.locator(`${HISTORY_ROWS}[data-at="09:30"].is-jump-target`).waitFor({ timeout: 30_000 });
      await closedOnPhone(page, tap);
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
      onlyChatWrites(app);
    });
  }, { timeout: 240_000 });

  e2eTest(`the real option chain link at ${label} lands on that ticker's chain`, async () => {
    const { writes } = server("owner");
    const fakeReads = { ...CHAIN_READS, "GET /api/oi-finder": (read) => chainReply(read, { researchSection: new URLSearchParams(read.search).get("section") || "" }) };
    await withApp({ view: "Release Notes", ...size, fakeReads, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "option chain nvda", tap);
      await follow(page, "NVDA", tap);
      await page.locator("[data-testid='mobile-quick-options'] .mobile-quick-options-title b", { hasText: "NVDA LIVE CHAIN" }).waitFor({ timeout: 60_000 });
      await closedOnPhone(page, tap);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });

  e2eTest(`a regular user at ${label}: refused with no link, and no owner word or card anywhere`, async () => {
    const { writes } = server("regular");
    await withApp({ view: "Release Notes", ...size, fakeReads: { [STATUS]: member, [HISTORY]: archive, "GET /api/momx-scanner/rule-record": { json: {} } }, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await openChat(page, tap);
      await page.locator(`${CHAT} [data-testid='chat-home-card'][data-kind='scanner']`).waitFor({ timeout: 15_000 });
      assert.equal(await page.locator(`${CHAT} [data-testid='chat-home-card'][data-kind='bot']`).count(), 0);
      for (const question of ["scorecard", "show paper trades"]) {
        await ask(page, question, tap);
        assert.equal(await page.locator(ANSWER).last().locator("[data-testid='chat-link']").count(), 0, "a refusal has no link");
      }
      await ask(page, "chart levels nvda", tap);
      const answers = (await page.locator(ANSWER).allInnerTexts()).join(" ");
      assert.doesNotMatch(answers, OWNER_WORDS);
      const views = await page.locator(`${CHAT} [data-testid='chat-tray-link'], ${LINK}`).allInnerTexts();
      assert.doesNotMatch(views.join(" "), OWNER_WORDS);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}

e2eTest("the real chart link opens that ticker on Charts & OI", async () => {
  const { writes } = server("owner");
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await ask(page, "open nvda chart");
    await follow(page, "NVDA");
    await waitForTape(page, { symbol: "NVDA", timeframe: "5m", timeout: 120_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("the real news link searches that ticker", async () => {
  const { writes } = server("owner");
  await withApp({ view: "Release Notes", fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "any news on NVDA");
    await follow(page, "NVDA news");
    await page.locator("button.is-active", { hasText: "All Stored" }).waitFor({ timeout: 60_000 });
    assert.equal(await page.locator('input[aria-label="News search"]').first().inputValue(), "NVDA");
    assert.deepEqual(app.pageErrors, []);
  });
});

const longRow = (symbol, name) => ({ symbol, name, signal: "WAIT", leaps: false, price: 198, refPrice: 198, buyBelow: 68, leapsBelow: 108, buyZonePct: -1.9, safetyBuy: 68, reason: "price above safety buy",
  big5: 4, moat: "wide", mos: 50, roic: 0.3, debtFcf: 0.7, growth: 0.1, eps: 4, peg: 1, stale: false });
const longReads = {
  "GET /api/long-term": (read) => ({ json: { meta: { universe: 1, scored: 1, etfs: [], noFilings: [], refreshedAt: "2026-11-06T03:12:00-05:00", refreshing: null, deferredUntil: null, mos: Number(new URLSearchParams(read.search).get("mos") || 50) }, rows: [longRow("AAPL", "Apple Inc")] } }),
  "GET /api/long-term/AAPL": { json: { symbol: "AAPL", name: "Apple Inc", checks: [], years: [], unit: "USD", explain: ["Sticker note"], crossChecks: [], sources: [], listingFactor: 1 } },
};

e2eTest("the real Long Term link opens that company", async () => {
  const { writes } = server("owner");
  await withApp({ view: "Release Notes", fakeReads: longReads, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "long term aapl");
    await follow(page, "AAPL");
    await page.getByTestId("long-term-row-AAPL").waitFor({ timeout: 60_000 });
    assert.equal(await page.getByTestId("long-term-search").inputValue(), "AAPL");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the owner's real Scorecard link opens the Bot page's Scorecard", async () => {
  const { writes } = server("owner");
  const fakeReads = { "GET /api/momx-scanner/bot-desk": { json: { available: false, accounts: {}, limits: {} } } };
  await withApp({ view: "Release Notes", storage: { "bot.view": "research" }, fakeReads, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "scorecard");
    await follow(page, "Scorecard");
    await page.locator("[role='group'][aria-label='Page'] button[aria-pressed='true']", { hasText: "Scorecard" }).waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});
