// Chat links (2026-11-11): the buttons in a chat answer take the trader to the
// exact page, ticker, day and time, and "Nvda today scanner" opens the whole day
// in the scanner's own columns. The chat's answers and the scanner archive are
// fixtures shaped like the server's, so every step has a known answer. A link
// only moves the screen, so these specs also check it writes nothing.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { card, waitForTape, withCharts } from "./charts-core.helpers.mjs";
import { CHAIN_READS, chainReply, sleep } from "./options-fixtures.mjs";
import { BOARD_STORAGE, boardReads, boardReady, listsFixture } from "./scanner-board-fixtures.mjs";

const CHAT_WRITE = "POST /api/bot-chat";
const BUTTON = "[data-testid='chat-dock-button']";
const PANEL = "[data-testid='chat-dock-panel']";
const CHAT = "[data-testid='dock-chat']";
const ANSWER = `${CHAT} [data-testid='chat-answer']`;
const LOOKING = `${CHAT} [data-testid='chat-looking']`;
const LINK = `${ANSWER} [data-testid='chat-link']`;
const SHEET = "[data-testid='chat-scanner-day']";
const SECTION = `${SHEET} [data-testid='chat-day-section']`;
const HISTORY_ROWS = "table.momx-history-table:not(.momx-day-table) tr.momx-row.momx-history-row";
const SEARCH = "input.momx-history-search[aria-label='Search history for a ticker']";
const DAY_PICKER = "[data-testid='momx-history-day']";
const STATUS = "GET /api/auth/status";
const HISTORY = "GET /api/momx-scanner/history";
const member = { json: { bootstrapRequired: false, bootstrapRequiresToken: false, accessStatus: "", accessEmail: "",
  user: { id: "e2e-user-1", email: "e2e.member@example.com", displayName: "Member", role: "user", isAdmin: false, isActive: true, hasPassword: true, mustChangePassword: false } } };

const DAY = "2026-11-10";
const EARLIER = "2026-11-09";
const SCANNER = "MomX Scanner";
const DAY_TABLE = {
  kind: "scanner_day", title: "NVDA on the scanner", ticker: "NVDA", day: DAY,
  sections: [{ list: "Mag7", side: "bull" }, { list: "Watchlist", side: "bull" }, { list: "Mag7", side: "bear" }, { list: "Watchlist", side: "bear" }],
};

function snapshot(day, clock, symbol, hits, extra = {}) {
  const at = `${day}T${clock}:00-05:00`;
  return {
    symbol, at, date: day, isArrival: clock === "09:31", changed: [], firstSeenAt: at, lastSeenAt: at, hits, truncated: false, peakRvol: 3.2,
    row: {
      symbol, scanPass: true, pctChange: 2.4, last: 101.25, prevClose: 98.88, industry: "Semiconductors", scanReasons: ["rvol:5m"],
      rvol: { "5m": { value: 3.2, bg: "cyan", fg: "black" } }, sqz: {}, skittles: {}, highLow: { value: 0.6, bg: "green" }, color: { bg: "green" },
      sparkline: [100, 100.5, 101.25], quoteTrend: [],
    },
    ...extra,
  };
}

// momx/history.py's history_response, for Mag7 and Watchlist. NVDA is on every
// board but Watchlist puts; Mag7 calls has a second page.
function archive(read) {
  const params = new URLSearchParams(read.search);
  const list = params.get("list");
  const bear = params.get("dir") === "bear";
  const symbol = (params.get("symbol") || "").toUpperCase();
  const date = params.get("date");
  const base = { list, days: [DAY, EARLIER], retentionDays: 30 };
  if (symbol) {
    const offset = Number(params.get("offset") || 0);
    let rows = [];
    if (symbol === "NVDA" && !(list === "Watchlist" && bear)) {
      const hits = bear ? 120 : list === "Mag7" ? 172 : 182;
      rows = [snapshot(date || DAY, "09:31", "NVDA", hits), snapshot(date || DAY, "09:45", "NVDA", hits), snapshot(date || DAY, "12:10", "NVDA", hits)];
    }
    const paged = list === "Mag7" && !bear && rows.length;
    if (paged && offset) rows = [snapshot(date || DAY, "15:01", "NVDA", 172)];
    const total = paged ? 4 : rows.length;
    return { json: { ...base, date: date || null, symbol, rows, totalRows: total, returnedRows: rows.length, offset, pageSize: 400, hasMore: Boolean(paged && !offset), truncated: false } };
  }
  const day = date || DAY;
  const rows = [snapshot(day, "09:31", "AAPL", 3), snapshot(day, "09:45", "NVDA", 3), snapshot(day, "12:10", "AAPL", 3)];
  return { json: { ...base, date: day, rows, totalRows: 3, returnedRows: 3, offset: 0, pageSize: 400, hasMore: false, truncated: false,
    timeIndex: [["09:31", 0, 1], ["09:45", 1, 1], ["12:10", 2, 1]], sessions: [["Open", 2], ["Midday", 1]] } };
}

const scannerReads = (extra = {}) => ({ ...boardReads(), [HISTORY]: archive, ...extra });
const EARNINGS = { json: { rows: [{ symbol: "NVDA", date: "2026-11-19", daysUntil: 8, timingCode: "amc", timing: "After close" }] } };

// One reply for every question, unless the question has its own.
function chat(replies) {
  const homes = [];
  return {
    homes,
    writes: { [CHAT_WRITE]: (write) => {
      const body = JSON.parse(write.body);
      if (body.home) { homes.push(body); return { json: { home: { cards: [], chips: [] } } }; }
      return { json: { intent: "help", ticker: null, day: null, suggest: [], lines: ["Here you go."], ...(replies[body.question] || {}) } };
    } },
  };
}

const replyWith = (...links) => ({ go: { links } });

async function ask(page, question, tap = false) {
  const button = page.locator(BUTTON);
  await button.waitFor({ timeout: 90_000 });
  if (!(await page.locator(CHAT).isVisible().catch(() => false))) await (tap ? button.tap() : button.click());
  const input = page.locator(`${CHAT} input.chat-input`);
  await input.waitFor({ timeout: 15_000 });
  await input.fill(question);
  await input.press("Enter");
  await page.locator(ANSWER).last().waitFor({ timeout: 15_000 });
}

async function follow(page, label, tap = false) {
  const link = page.locator(LINK, { hasText: label }).last();
  await link.waitFor({ timeout: 15_000 });
  await (tap ? link.tap() : link.click());
}

const historyReads = (app) => app.reads.filter((read) => read.path === "/api/momx-scanner/history").map((read) => new URLSearchParams(read.search));
const onlyChatWrites = (app) => {
  const background = new Set(["/api/bot-chat", "/api/news-feed/refresh", "/api/client-timing", "/api/chart-indicator-profiles"]);
  assert.deepEqual([...new Set(app.writes.map((w) => w.path))].filter((p) => !background.has(p)), []);
};

// Waits for the History tab to be showing `ticker` on `day`, then for the row at `clock` to light up.
async function landedOn(page, { ticker, day, clock }) {
  await page.waitForFunction(([search, want]) => document.querySelector(search)?.value === want, [SEARCH, ticker], { timeout: 30_000 });
  await page.waitForFunction(([picker, want]) => document.querySelector(picker)?.value === want, [DAY_PICKER, day], { timeout: 30_000 });
  await page.locator(`${HISTORY_ROWS}[data-at="${clock}"].is-jump-target`).waitFor({ timeout: 30_000 });
}

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`"Nvda today scanner" at ${label}: the sheet opens by itself in the scanner's own columns, every board, two reads at a time`, async () => {
    const { writes } = chat({ "Nvda today scanner": { intent: "scanner_day", ticker: "NVDA", day: DAY, lines: ["Mag7 calls: first 09:31, last 12:10."], table: DAY_TABLE } });
    let running = 0;
    let peak = 0;
    const slowArchive = async (read) => {
      running += 1;
      peak = Math.max(peak, running);
      await sleep(250);
      running -= 1;
      return archive(read);
    };
    // A saved layout carries its version (momxColumnLayout.js); without it the app ignores the whole layout.
    const hidden = JSON.stringify({ version: 1, order: [], hidden: ["spread"] });
    await withApp({
      view: "Release Notes", ...(viewport ? { viewport } : {}),
      storage: { "momx.scanner.columnLayout": hidden },
      fakeReads: { [HISTORY]: slowArchive, "GET /api/earnings-calendar": EARNINGS, "GET /api/momx-scanner/rule-record": { json: {} } }, fakeWrites: writes,
    }, async (app) => {
      const { page } = app;
      await ask(page, "Nvda today scanner", viewport === PHONE);
      const sheet = page.locator(SHEET);
      await sheet.waitFor({ timeout: 30_000 });
      await page.locator(`${SECTION}[data-list="Watchlist"][data-side="bear"][data-folded="true"]`).waitFor({ timeout: 30_000 });
      assert.equal((await page.locator(`${SECTION}[data-folded="true"]`).innerText()).trim(), "Not on Watchlist puts that day");

      assert.deepEqual((await page.locator(`${SECTION} [data-testid='chat-day-counts']`).allInnerTexts()).map((t) => t.trim()),
        ["172 scans, 4 saved rows", "182 scans, 3 saved rows", "120 scans, 3 saved rows"]);
      const heads = await page.locator(`${SHEET} table.momx-day-table`).first().locator("thead tr.momx-head-row th[data-col]").evaluateAll((ths) => ths.map((th) => th.getAttribute("data-col")));
      for (const key of ["industry", "symbol", "time", "setup", "fresh", "pctChange", "price", "news", "jev", "event", "sparkline", "highLow"]) {
        assert.ok(heads.includes(key), `the sheet has no ${key} column: ${heads}`);
      }
      assert.equal(heads.includes("spread"), false, "a column he hid is back");
      assert.equal(heads.indexOf("time"), heads.indexOf("symbol") + 1, "Time follows Symbol");
      await page.locator(`${SHEET} td[data-col='event'].momx-eventcol`, { hasText: "11/19" }).first().waitFor({ timeout: 15_000 });

      const seen = historyReads(app);
      assert.equal(seen.length, 4);
      assert.deepEqual(seen.map((p) => [p.get("list"), p.get("dir"), p.get("symbol"), p.get("date")]),
        [["Mag7", null, "NVDA", DAY], ["Watchlist", null, "NVDA", DAY], ["Mag7", "bear", "NVDA", DAY], ["Watchlist", "bear", "NVDA", DAY]]);
      assert.equal(peak, 2, "reads at once");

      const scrollers = await page.locator(SHEET).evaluate((root) => [...root.querySelectorAll("*")].filter((node) => {
        const style = getComputedStyle(node);
        return ["auto", "scroll"].includes(style.overflowX) || ["auto", "scroll"].includes(style.overflowY);
      }).map((node) => node.getAttribute("data-testid")));
      assert.deepEqual(scrollers, ["chat-scanner-day-scroller"], "the sheet must have exactly one scroller");

      const rows = page.locator(`${SECTION}[data-list="Mag7"][data-side="bull"] + .momx-table-scroll tr.momx-row`);
      assert.equal(await rows.count(), 3);
      await page.locator("[data-testid='chat-day-more']").click();
      await page.waitForFunction((selector) => document.querySelectorAll(selector).length === 4, `${SECTION}[data-list="Mag7"][data-side="bull"] + .momx-table-scroll tr.momx-row`, { timeout: 15_000 });
      assert.equal(await page.locator("[data-testid='chat-day-more']").count(), 0, "everything is loaded");
      assert.equal(await overflowsSideways(page), false);

      if (viewport === PHONE) {
        await page.locator("[data-testid='chat-scanner-day-close']").tap();
      } else {
        await page.keyboard.press("Escape");
      }
      await sheet.waitFor({ state: "detached", timeout: 5_000 });
      assert.equal(await page.locator(PANEL).isVisible(), true, "closing the sheet closed the chat too");
      assert.deepEqual(app.pageErrors, []);
      onlyChatWrites(app);
    });
  }, { timeout: 240_000 });
}

e2eTest("tapping a time in the sheet lands on that board's History at that moment, even from another list and side", async () => {
  const { writes } = chat({ "Nvda today scanner": { intent: "scanner_day", ticker: "NVDA", day: DAY, lines: ["Mag7 puts."], table: { ...DAY_TABLE, sections: [{ list: "Mag7", side: "bear" }] } } });
  await withApp({ view: SCANNER, storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist" }, fakeReads: scannerReads({ "GET /api/earnings-calendar": EARNINGS, "GET /api/momx-scanner/rule-record": { json: {} } }), fakeWrites: writes }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await ask(page, "Nvda today scanner");
    const time = page.locator(`${SHEET} [data-testid='momx-day-time-link']`, { hasText: "09:45" }).first();
    await time.waitFor({ timeout: 30_000 });
    await time.click();
    await page.locator(SHEET).waitFor({ state: "detached", timeout: 5_000 });
    await landedOn(page, { ticker: "NVDA", day: DAY, clock: "09:45" });
    assert.ok(historyReads(app).some((p) => p.get("list") === "Mag7" && p.get("dir") === "bear" && p.get("symbol") === "NVDA" && p.get("date") === DAY));
    // The History tab has no BULL/BEAR button (that sits on the Live toolbar), so the side shows in what History read last.
    const last = historyReads(app).at(-1);
    assert.deepEqual([last.get("list"), last.get("dir"), last.get("symbol"), last.get("date")], ["Mag7", "bear", "NVDA", DAY]);
    assert.deepEqual(app.pageErrors, []);
    onlyChatWrites(app);
  });
}, { timeout: 240_000 });

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`a scanner link at ${label} changes list, keeps the day and ticker it asked for, and moves to the time`, async () => {
    const link = { label: "Open NVDA on Mag7 History, 09:45", view: SCANNER, list: "Mag7", side: "bull", tab: "history", day: EARLIER, ticker: "NVDA", time: "09:45" };
    const { writes } = chat(replyWith(link));
    const tap = viewport === PHONE;
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), storage: { ...BOARD_STORAGE, "momx.scanner.activeList": "Watchlist", "momx.scanner.activeView": "live" }, fakeReads: scannerReads(), fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "go", tap);
      await follow(page, "Open NVDA on Mag7 History", tap);
      await landedOn(page, { ticker: "NVDA", day: EARLIER, clock: "09:45" });
      // The trap: choosing the list wipes the day and the search a moment later.
      await page.waitForTimeout(1500);
      assert.equal(await page.locator(SEARCH).inputValue(), "NVDA");
      assert.equal(await page.locator(DAY_PICKER).inputValue(), EARLIER);
      assert.ok(historyReads(app).some((p) => p.get("list") === "Mag7" && p.get("symbol") === "NVDA" && p.get("date") === EARLIER));
      if (tap) await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
      onlyChatWrites(app);
    });
  }, { timeout: 240_000 });
}

e2eTest("the same link pressed twice, a whole-day time link, and a list that no longer exists", async () => {
  const again = { label: "NVDA 09:45", view: SCANNER, list: "Mag7", side: "bull", tab: "history", day: DAY, ticker: "NVDA", time: "09:45" };
  const board = { label: "Mag7 at 12:10", view: SCANNER, list: "Mag7", side: "bull", tab: "history", day: DAY, time: "12:10" };
  const gone = { label: "Gone list", view: SCANNER, list: "21 EMA Touch Weekly", side: "bull", tab: "history", day: DAY };
  const { writes } = chat(replyWith(again, board, gone));
  await withApp({ view: SCANNER, storage: { ...BOARD_STORAGE, "momx.scanner.activeView": "history" }, fakeReads: scannerReads(), fakeWrites: writes }, async (app) => {
    const { page } = app;
    await page.locator(".momx-history-bar").waitFor({ timeout: 90_000 });
    await ask(page, "go");
    await follow(page, "NVDA 09:45");
    await landedOn(page, { ticker: "NVDA", day: DAY, clock: "09:45" });
    await page.locator(`${HISTORY_ROWS}.is-jump-target`).waitFor({ state: "detached", timeout: 10_000 });
    await follow(page, "NVDA 09:45");
    await page.locator(`${HISTORY_ROWS}[data-at="09:45"].is-jump-target`).waitFor({ timeout: 15_000 });

    await follow(page, "Mag7 at 12:10");
    await page.waitForFunction((search) => document.querySelector(search)?.value === "", SEARCH, { timeout: 15_000 });
    await page.locator(`${HISTORY_ROWS}[data-at="12:10"].is-jump-target`).waitFor({ timeout: 15_000 });

    await follow(page, "Gone list");
    const note = page.locator("[data-testid='momx-link-note']");
    await note.waitFor({ timeout: 15_000 });
    assert.equal((await note.innerText()).trim(), "21 EMA Touch Weekly is not on the scanner any more, so this is Mag7.");
    assert.deepEqual(app.pageErrors, []);
    onlyChatWrites(app);
  });
}, { timeout: 240_000 });

e2eTest("a live-board link picks out the ticker; the chat then knows the ticker on the scanner page and the page's name", async () => {
  const link = { label: "NVDA live", view: SCANNER, list: "Mag7", side: "bull", tab: "live", ticker: "NVDA" };
  const { writes, homes } = chat(replyWith(link));
  await withApp({ view: SCANNER, storage: BOARD_STORAGE, fakeReads: scannerReads(), fakeWrites: writes }, async (app) => {
    const { page } = app;
    await boardReady(page);
    await page.locator(BUTTON).click();
    await page.locator(CHAT).waitFor({ timeout: 15_000 });
    assert.equal(await page.locator(LOOKING).count(), 0, "nothing is picked out yet");
    for (let i = 0; i < 40 && !homes.length; i += 1) await sleep(250);
    assert.deepEqual(homes[0], { home: true, context: { pageTicker: "", pageName: SCANNER } });
    await ask(page, "go");
    await follow(page, "NVDA live");
    await page.locator(LOOKING, { hasText: "NVDA" }).waitFor({ timeout: 15_000 });
    await page.waitForFunction(() => {
      const rows = [...document.querySelectorAll("table.momx-table:not(.momx-history-table) tbody tr.momx-row td.momx-col-symbol")];
      return rows.length === 1 && rows[0].textContent.includes("NVDA");
    }, null, { timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 240_000 });

e2eTest("a chart link opens that ticker and timeframe, and switching ticker and timeframe by hand still works after it", async () => {
  const link = { label: "SPY 15m chart", view: "Charts & OI", ticker: "SPY", timeframe: "15m" };
  const { writes } = chat(replyWith(link));
  await withCharts({ workspace: { symbols: ["AAPL"] }, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await waitForTape(page, { symbol: "AAPL" });
    await ask(page, "go");
    await follow(page, "SPY 15m chart");
    await waitForTape(page, { symbol: "SPY", timeframe: "15m", timeout: 120_000 });
    await page.keyboard.press("Escape");
    await page.locator(PANEL).waitFor({ state: "hidden", timeout: 5_000 });
    const input = card(page).locator('input[aria-label="Chart ticker"]');
    await input.fill("aapl");
    await input.press("Enter");
    await waitForTape(page, { symbol: "AAPL", timeout: 120_000 });
    await page.waitForTimeout(1500);
    assert.equal(await card(page).getAttribute("data-chart-symbol"), "AAPL", "the link pulled the chart back");
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

e2eTest("a chart link from another page opens Charts & OI; a timeframe the chart does not draw opens on 5m", async () => {
  const link = { label: "NVDA 1m chart", view: "Charts & OI", ticker: "NVDA", timeframe: "1m" };
  const { writes } = chat(replyWith(link));
  await withCharts({ workspace: { symbols: ["NVDA"] }, storage: { agenticActiveView: "Release Notes" }, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await page.locator("[data-testid='release-notes-view']").waitFor({ timeout: 60_000 });
    await ask(page, "go");
    await follow(page, "NVDA 1m chart");
    await waitForTape(page, { symbol: "NVDA", timeframe: "5m", timeout: 120_000 });
    assert.deepEqual(app.pageErrors, []);
  });
}, { timeout: 300_000 });

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`a Quick Options link at ${label} lands on that ticker's chain; the same ticker again does not force a refresh`, async () => {
    const link = { label: "AAPL options", view: "Quick Options", ticker: "AAPL" };
    const { writes } = chat(replyWith(link));
    const tap = viewport === PHONE;
    const fakeReads = { ...CHAIN_READS, "GET /api/oi-finder": (read) => chainReply(read, { researchSection: new URLSearchParams(read.search).get("section") || "" }) };
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeReads, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "go", tap);
      await follow(page, "AAPL options", tap);
      const title = page.locator("[data-testid='mobile-quick-options'] .mobile-quick-options-title b", { hasText: "AAPL LIVE CHAIN" });
      await title.waitFor({ timeout: 60_000 });
      await page.waitForTimeout(1500);
      const forced = () => app.reads.filter((r) => /^\/api\/oi-finder/.test(r.path) && /[?&](refresh|force)=(1|true)/.test(r.search)).length;
      const before = forced();
      if (tap) await page.locator(BUTTON).tap(); else if (!(await page.locator(CHAT).isVisible())) await page.locator(BUTTON).click();
      await follow(page, "AAPL options", tap);
      await page.waitForTimeout(1500);
      assert.equal(forced(), before, "the second press forced a refresh");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}

e2eTest("a News link searches every stored story for that ticker", async () => {
  const { writes } = chat(replyWith({ label: "NVDA news", view: "News Feed", ticker: "NVDA" }));
  await withApp({ view: "Release Notes", fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "go");
    await follow(page, "NVDA news");
    await page.locator("button.is-active", { hasText: "All Stored" }).waitFor({ timeout: 60_000 });
    assert.equal(await page.locator('input[aria-label="News search"]').first().inputValue(), "NVDA");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a Premarket link opens that day in the history", async () => {
  const { writes } = chat(replyWith({ label: "Premarket day", view: "Premarket Scanner", day: EARLIER }));
  const history = { json: { days: [{ date: DAY, matches: [], tickerCount: 0 }, { date: EARLIER, matches: [], tickerCount: 0 }] } };
  await withApp({ view: "Release Notes", fakeReads: { "GET /api/premarket-scanner/history": history }, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "go");
    await follow(page, "Premarket day");
    await page.locator("[data-testid='premarket-scanner-view'] .scanner-day-count").waitFor({ timeout: 60_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

const longRow = (symbol, name) => ({ symbol, name, signal: "WAIT", leaps: false, price: 198, refPrice: 198, buyBelow: 68, leapsBelow: 108, buyZonePct: -1.9, safetyBuy: 68, reason: "price above safety buy",
  big5: 4, moat: "wide", mos: 50, roic: 0.3, debtFcf: 0.7, growth: 0.1, eps: 4, peg: 1, stale: false });
const longReads = {
  "GET /api/long-term": (read) => ({ json: { meta: { universe: 2, scored: 2, etfs: [], noFilings: [], refreshedAt: "2026-11-06T03:12:00-05:00", refreshing: null, deferredUntil: null, mos: Number(new URLSearchParams(read.search).get("mos") || 50) },
    rows: [longRow("AAPL", "Apple Inc"), longRow("NVDA", "Nvidia Corp")] } }),
  "GET /api/long-term/NVDA": { json: { symbol: "NVDA", name: "Nvidia Corp", checks: [], years: [], unit: "USD", explain: ["Sticker note"], crossChecks: [], sources: [], listingFactor: 1 } },
  "GET /api/long-term/AAPL": { json: { symbol: "AAPL", name: "Apple Inc", checks: [], years: [], unit: "USD", explain: ["Sticker note"], crossChecks: [], sources: [], listingFactor: 1 } },
};

e2eTest("a Long Term link opens that company, from another page and when the page is already open", async () => {
  const { writes } = chat(replyWith({ label: "NVDA long term", view: "Long Term", ticker: "NVDA" }, { label: "AAPL long term", view: "Long Term", ticker: "AAPL" }));
  await withApp({ view: "Release Notes", fakeReads: longReads, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "go");
    await follow(page, "NVDA long term");
    await page.getByTestId("long-term-row-NVDA").waitFor({ timeout: 60_000 });
    await page.getByTestId("long-term-drawer").waitFor({ timeout: 15_000 });
    assert.equal(await page.getByTestId("long-term-search").inputValue(), "NVDA");
    assert.equal(await page.getByTestId("long-term-row-AAPL").count(), 0);
    await follow(page, "AAPL long term");
    await page.getByTestId("long-term-row-AAPL").waitFor({ timeout: 15_000 });
    assert.equal(await page.getByTestId("long-term-search").inputValue(), "AAPL");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("the owner's Bot link opens the part it names, whether or not the page is already open", async () => {
  const { writes } = chat(replyWith({ label: "Desk", view: "Bot", sub: "desk" }, { label: "Research", view: "Bot", sub: "research" }));
  const fakeReads = { "GET /api/momx-scanner/bot-desk": { json: { available: false, accounts: {}, limits: {} } } };
  await withApp({ view: "Release Notes", storage: { "bot.view": "research" }, fakeReads, fakeWrites: writes }, async (app) => {
    const { page } = app;
    await ask(page, "go");
    await follow(page, "Desk");
    const switcher = "[data-testid='bh-panels'] button";
    await page.locator(`${switcher}[aria-pressed='true']`, { hasText: "Scorecard" }).waitFor({ timeout: 60_000 });
    await follow(page, "Research");
    await page.locator(`${switcher}[aria-pressed='true']`, { hasText: "Research" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`a regular user at ${label} cannot be taken to an owner page, even by a link pushed straight at the app`, async () => {
    const { writes } = chat(replyWith({ label: "Open the Bot", view: "Bot" }, { label: "Paper", view: "Paper Trading" }, { label: "Option paper", view: "Option Paper Trading" }, { label: "Sectors", view: "Sectors" }));
    const tap = viewport === PHONE;
    await withApp({ view: "Release Notes", ...(viewport ? { viewport } : {}), fakeReads: { [STATUS]: member }, fakeWrites: writes }, async (app) => {
      const { page } = app;
      await ask(page, "go", tap);
      assert.deepEqual((await page.locator(LINK).allInnerTexts()).map((t) => t.trim()), ["Sectors"], "owner links were shown");
      for (const view of ["Bot", "Paper Trading", "Option Paper Trading"]) {
        await page.evaluate((target) => window.dispatchEvent(new CustomEvent("agx:open-link", { detail: { label: "x", view: target } })), view);
      }
      await page.waitForTimeout(1000);
      assert.equal(await page.locator("[data-testid='release-notes-view']").count(), 1, "the page moved");
      assert.equal(await page.locator("[data-testid='bot-view']").count(), 0);
      await follow(page, "Sectors", tap);
      await page.locator("[data-testid='sectors-view']").waitFor({ timeout: 60_000 });
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("a pop-out window does not follow a link", async () => {
  await withApp({ path: "/?popout=momx&list=Mag7", storage: BOARD_STORAGE, fakeReads: scannerReads() }, async (app) => {
    const { page } = app;
    await page.locator("[data-testid='momx-popout-root']").waitFor({ timeout: 60_000 });
    await boardReady(page);
    await page.evaluate(() => window.dispatchEvent(new CustomEvent("agx:open-link", { detail: { label: "x", view: "Release Notes" } })));
    await page.waitForTimeout(1000);
    assert.equal(await page.locator("[data-testid='momx-popout-root']").count(), 1);
    assert.equal(await page.locator("[data-testid='release-notes-view']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

// listsFixture is used through boardReads; named here so a renamed list fails loudly.
assert.ok(listsFixture().lists.some((item) => item.name === "Mag7") && listsFixture().lists.some((item) => item.name === "Watchlist"));
