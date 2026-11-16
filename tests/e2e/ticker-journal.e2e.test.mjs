// Bot page -> Research -> Ticker trader -> Journal and AI review (2026-11-13). Every route is canned here: the
// harness blocks any write a spec does not answer, the AI review is a fake answer, and nothing reaches a broker.
// Written during market hours and NOT run then (end-to-end specs skip themselves 09:30-16:00 ET); run after the close.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const READ = "GET /api/momx-scanner/ticker-trader";
const JOURNAL = "GET /api/momx-scanner/ticker-trader/journal";
const NOTE = "POST /api/momx-scanner/ticker-trader/note";
const REVIEW = "POST /api/momx-scanner/ticker-trader/review";
const PANEL = "[data-testid='ticker-trader']";
const OPEN_ON_IT = { "bot.tickerTrader": "1" };
const LABEL = "AI review, can be wrong. Only you see this.";
const REVIEW_TEXT = "NVDA Call has 2 closed trades with net +$290.00, and 1 of them reached T3.";

const STOPPED = {
  id: "NVDA-CALL-1-1", runId: "NVDA-CALL", day: "2026-11-13", ticker: "NVDA", side: "CALL", strike: 104, expiry: "2026-11-20", qty: 3,
  entry: 1.1, entrySpot: 100, stopStart: 98.4, stopStock: 98.4, t1: 102, t2: 104, t3: 106, pl: -180, plPct: -54.5,
  boughtAt: "2026-11-13T10:00:20-05:00", closedAt: "2026-11-13T10:00:30-05:00", outcome: "stopped", holdMinutes: 0.2,
  hits: [], targets: [{ label: "T1", stock: 102, reason: "half the expected move" }],
  events: [{ at: "2026-11-13T10:00:30-05:00", text: "Stop: stock traded through 98.40, sold 3 at $0.50." }], note: "", noteAt: null,
};
const WON = {
  ...STOPPED, id: "NVDA-CALL-2-2", pl: 470, plPct: 142.4, outcome: "T3", holdMinutes: 75, closedAt: "2026-11-13T11:16:00-05:00",
  hits: [{ target: "T1", at: "2026-11-13T10:01:00-05:00", sold: 1, price: 1.8 }, { target: "T2", at: "2026-11-13T10:40:00-05:00", sold: 1, price: 2.6 },
    { target: "T3", at: "2026-11-13T11:16:00-05:00", sold: 1, price: 3.6 }],
  events: [{ at: "2026-11-13T10:01:00-05:00", text: "T1 hit: sold 1 at $1.80, stop to break-even." }], note: "Waited for the pullback.",
};
const RUN = { id: "NVDA-CALL", ticker: "NVDA", side: "CALL", status: "idle", note: "Trade closed. Tap Trade again for the next one.",
  balance: 10290, startBalance: 10000, net: 290, tradeCount: 2, wins: 1, open: null, trades: [STOPPED, WON] };
const SPY = { id: "SPY-PUT", ticker: "SPY", side: "PUT", status: "idle", note: "No trades yet", balance: 5000, startBalance: 5000, net: 0,
  tradeCount: 0, wins: 0, open: null, trades: [] };

const IMPROVE = [
  { key: "lossTime", title: "Losses by time of day", text: "Most losing trades were bought 10:00-11:00 ET: 1 of 1 losing trades, net -$180.00.", trades: 1, enough: false, judge: "Too few trades to judge" },
  { key: "fastStop", title: "Stopped within 5 minutes", text: "The stop was hit within 5 minutes of the buy on 1 of 2 trades (50%).", trades: 2, enough: false, judge: "Too few trades to judge" },
];
const group = (rows) => rows.map(([key, label, trades, wins, pnl]) => ({ key, label, trades, wins, losses: trades - wins,
  winRatePct: trades ? (wins / trades) * 100 : null, pnl, avg: trades ? pnl / trades : null }));

function journalDoc(scope, { review = null, reviewStatus = null, trades = [WON, STOPPED] } = {}) {
  const n = trades.length;
  return {
    scope, label: scope === "all" ? "All tickers" : "NVDA Call", simulated: true, asOf: "2026-11-13T11:20:00-05:00",
    runs: [{ id: "NVDA-CALL", ticker: "NVDA", side: "CALL" }, { id: "SPY-PUT", ticker: "SPY", side: "PUT" }],
    startBalance: scope === "all" ? 15000 : 10000, balance: scope === "all" ? 15290 : 10290, openTrades: 0,
    stats: n ? { n: 2, wins: 1, losses: 1, winRatePct: 50, netPnl: 290, avgWin: 470, avgLoss: -180, profitFactor: 2.61, expectancy: 145, bestTrade: 470, worstTrade: -180, roiPct: 43.9 }
      : { n: 0, wins: 0, losses: 0, winRatePct: null, netPnl: 0, avgWin: null, avgLoss: null, profitFactor: null, expectancy: null, bestTrade: null, worstTrade: null },
    daySummary: { tradingDays: n ? 1 : 0, greenDays: n ? 1 : 0, redDays: 0, bestDay: null, worstDay: null, maxDrawdown: 180 },
    days: n ? [{ day: "2026-11-13", sent: 2, filled: 2, closed: 2, pnl: 290, cum: 290 }] : [],
    journal: { periods: { day: n ? [{ key: "2026-11-13", trades: 2, wins: 1, losses: 1, winRatePct: 50, pnl: 290, calls: 290, puts: 0 }] : [], week: [], month: [] } },
    equity: n ? [{ id: STOPPED.id, at: STOPPED.closedAt, pl: -180, balance: 9820 }, { id: WON.id, at: WON.closedAt, pl: 470, balance: 10290 }] : [],
    bestId: n ? WON.id : null, worstId: n ? STOPPED.id : null,
    breakdowns: n ? {
      side: group([["CALL", "Calls", 2, 1, 290], ["PUT", "Puts", 0, 0, 0]]),
      time: group([["open", "9:30-10:00", 0, 0, 0], ["morning", "10:00-11:00", 2, 1, 290]]),
      outcome: group([["stopped", "Stopped before T1", 1, 0, -180], ["T3", "Reached T3", 1, 1, 470]]),
      hold: group([["under5", "Under 5 min", 1, 0, -180], ["to180", "1-3 hours", 1, 1, 470]]),
      ...(scope === "all" ? { ticker: group([["NVDA", "NVDA", 2, 1, 290]]) } : {}),
    } : {},
    improve: n ? IMPROVE : [], minJudge: 10, trades, noteMax: 2000, thisWeek: "2026-11-09",
    review, reviewStatus, reviewLabel: LABEL,
  };
}

/** A tiny stand-in for the server: the runs, the journal, the notes and the one AI review a day. */
function fakeServer({ reviewReply = { review: { text: REVIEW_TEXT, label: LABEL, asOf: "written 11:20 AM ET from 2 closed trades", trades: 2 }, status: "done", cached: false },
  journal = journalDoc, noteReply } = {}) {
  const notes = {};
  const reviews = {};
  const doc = () => ({ runs: [RUN, SPY], maxRuns: 10, startBalance: 10000, marketOpen: true, asOf: "2026-11-13T11:20:05-05:00", simulated: true });
  const fakeReads = {
    [READ]: () => ({ json: doc() }),
    [JOURNAL]: (read) => {
      const scope = new URLSearchParams(read.search).get("id") || "all";
      const out = journal(scope);
      out.trades = out.trades.map((t) => (notes[t.id] !== undefined ? { ...t, note: notes[t.id] } : t));
      if (reviews[scope]) Object.assign(out, { review: reviews[scope].review, reviewStatus: reviews[scope].status });
      return { json: out };
    },
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM }, [AI_READ]: { json: AI_REPORT }, [MIRROR_READ]: { json: MIRRORS },
  };
  const fakeWrites = {
    [NOTE]: (write) => {
      const { id, text } = JSON.parse(write.body);
      if (noteReply) return noteReply;
      notes[id] = text.trim();
      return { json: { ok: true, note: { id, text: notes[id], at: "2026-11-13T11:21:00-05:00" } } };
    },
    [REVIEW]: (write) => {
      const { id } = JSON.parse(write.body);
      if (reviews[id]) return { json: { ok: true, ...reviews[id], cached: true } };
      reviews[id] = reviewReply;
      return { json: { ok: true, ...reviewReply } };
    },
  };
  return { fakeReads, fakeWrites, notes };
}

const texts = async (locator) => (await locator.allTextContents()).map((s) => s.trim());
const sent = (app, key) => app.writes.filter((w) => `${w.method} ${w.path}` === key).map((w) => JSON.parse(w.body));
const journalReads = (app) => app.reads.filter((r) => `${r.method} ${r.path}` === JOURNAL).map((r) => new URLSearchParams(r.search).get("id"));

e2eTest("the journal for all tickers: tiles, numbers, what to improve, the AI review asked for once, breakdowns", async () => {
  const server = fakeServer();
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const { page } = app;
    const panel = page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator(".desk-tile").first().waitFor({ timeout: 30_000 });
    assert.deepEqual(journalReads(app), ["all"]);
    assert.deepEqual(await texts(journal.locator("[aria-label='Which journal'] button")), ["All tickers", "NVDA Call", "SPY Put"]);
    assert.match(await journal.locator("h3").textContent(), /Journal · All tickers/);
    assert.deepEqual(await texts(journal.locator(".desk-tile-label")), ["Net P&L", "Trade win %", "Profit factor", "Day win %", "Avg win / loss"]);
    assert.equal((await journal.locator("[data-tile='net'] .desk-tile-value").textContent()).trim(), "+$290");
    assert.match(await journal.locator("[data-testid='tj-stat-expectancy']").textContent(), /\+\$145/);
    assert.match(await journal.locator("[data-testid='tj-stat-worst']").textContent(), /−\$180/);
    // The AI review: asked for once, shown with its label.
    const review = journal.locator("[data-testid='tj-review']");
    await review.waitFor({ timeout: 15_000 });
    assert.match(await review.textContent(), new RegExp(LABEL.replace(/[.,]/g, "\\$&")));
    assert.match(await review.textContent(), /reached T3/);
    assert.deepEqual(sent(app, REVIEW), [{ id: "all" }]);
    // What to improve: numbers, trade counts, too few to judge under 10.
    const items = journal.locator("[data-testid='tj-improve-item']");
    assert.equal(await items.count(), 2);
    assert.match(await items.first().textContent(), /10:00-11:00 ET.*1 trade · Too few trades to judge/s);
    // Breakdowns, the equity curve, the daily bars and the calendar.
    for (const key of ["side", "time", "outcome", "hold", "ticker"]) await journal.locator(`[data-testid='tj-breakdown-${key}']`).waitFor();
    assert.equal(await journal.locator("[data-testid='tj-equity'] circle").count(), 2);
    assert.equal(await journal.locator(".desk-bars-svg rect").count(), 1);
    assert.match(await journal.locator("[data-testid='tj-calendar'] [data-day='2026-11-13']").textContent(), /\+\$290.*2 trades/s);
    // The minute's re-read does not ask the AI again.
    await page.waitForTimeout(1500);
    assert.deepEqual(sent(app, REVIEW), [{ id: "all" }]);
    // One run's journal: its own read and its own (one) review ask.
    await journal.locator("[aria-label='Which journal'] button", { hasText: "NVDA Call" }).click();
    await journal.locator("h3", { hasText: "Journal · NVDA Call" }).waitFor({ timeout: 15_000 });
    await journal.locator("[data-testid='tj-review']").waitFor({ timeout: 15_000 });
    assert.deepEqual(journalReads(app).slice(-1), ["NVDA-CALL"]);
    assert.deepEqual(sent(app, REVIEW), [{ id: "all" }, { id: "NVDA-CALL" }]);
    assert.equal(await journal.locator("[data-testid='tj-breakdown-ticker']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a trade opens its card: plan, hits, chart button for that day, and a note saved on the server", async () => {
  const server = fakeServer();
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const { page } = app;
    const panel = page.locator(PANEL);
    // Into the journal from the run itself.
    await panel.locator("[data-testid='tt-row']").first().click({ timeout: 60_000 });
    await panel.locator("[data-testid='tt-journal']").click();
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator("[data-testid='tj-trade']").first().waitFor({ timeout: 30_000 });
    assert.deepEqual(journalReads(app), ["NVDA-CALL"]);
    const rows = journal.locator("[data-testid='tj-trade']");
    assert.match(await rows.nth(0).textContent(), /NVDA 104 Call · best.*Reached T3 · note.*\+\$470/s);
    assert.match(await rows.nth(1).textContent(), /· worst.*Stopped before T1.*−\$180/s);
    await rows.nth(0).click();
    const card = journal.locator("[data-testid='tj-detail']");
    await card.waitFor();
    assert.deepEqual(await texts(card.locator(".tt-level b")), ["Entry", "Stop", "T1 ✓", "T2 ✓", "T3 ✓"]);
    assert.match(await card.locator("[data-testid='tj-level-T1']").textContent(), /102\.00.*hit 10:01 ET · sold 1 at \$1\.80/s);
    assert.equal(await card.locator("[data-testid='tj-note']").inputValue(), "Waited for the pullback.");
    await page.evaluate(() => { window.__tjLinks = []; window.addEventListener("agx:open-link", (e) => window.__tjLinks.push(e.detail), { capture: true }); });
    await card.locator("[data-testid='tj-chart']").click();
    const links = await page.evaluate(() => window.__tjLinks);
    assert.deepEqual(links.map((l) => [l.view, l.ticker, l.day]), [["Charts & OI", "NVDA", "2026-11-13"]]);
  });
  const again = fakeServer();
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: again.fakeReads, fakeWrites: again.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator("[data-testid='tj-trade']").nth(1).click({ timeout: 30_000 });
    const card = journal.locator("[data-testid='tj-detail']");
    assert.equal(await card.locator("[data-testid='tj-note-save']").isDisabled(), true);       // nothing typed yet
    assert.equal(await card.locator("[data-testid='tj-note']").getAttribute("maxlength"), "2000");
    await card.locator("[data-testid='tj-note']").fill("Bought into the open spike. Stop too close.");
    await card.locator("[data-testid='tj-note-save']").click();
    await card.locator("[data-testid='tj-note-saved']").waitFor({ timeout: 15_000 });
    assert.deepEqual(sent(app, NOTE), [{ id: "NVDA-CALL-1-1", text: "Bought into the open spike. Stop too close." }]);
    assert.match(await journal.locator("[data-testid='tj-trade']").nth(1).textContent(), /· note/);
    assert.deepEqual(app.pageErrors, []);
  });
  const refused = fakeServer({ noteReply: { status: 400, json: { ok: false, error: "A note can be at most 2,000 characters." } } });
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: refused.fakeReads, fakeWrites: refused.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator("[data-testid='tj-trade']").first().click({ timeout: 30_000 });
    await journal.locator("[data-testid='tj-note']").fill("x");
    await journal.locator("[data-testid='tj-note-save']").click();
    await journal.locator("[data-testid='tj-note-error']", { hasText: "at most 2,000 characters" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("no AI review box and no error when the AI is down; none asked for with no trades or one already written", async () => {
  const down = fakeServer({ reviewReply: { review: null, status: "skipped", why: "The AI is not answering right now.", cached: false } });
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: down.fakeReads, fakeWrites: down.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator(".desk-tile").first().waitFor({ timeout: 30_000 });
    await journal.locator("[data-testid='tj-review-writing']").waitFor({ state: "detached", timeout: 15_000 });
    assert.equal(await journal.locator("[data-testid='tj-review']").count(), 0);
    assert.equal(await journal.locator("[role='alert']").count(), 0);
    assert.deepEqual(sent(app, REVIEW), [{ id: "all" }]);
  });
  const written = fakeServer({ journal: (scope) => journalDoc(scope, { review: { text: REVIEW_TEXT, label: LABEL, asOf: "written 9:45 AM ET" }, reviewStatus: "done" }) });
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: written.fakeReads, fakeWrites: written.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    await panel.locator("[data-testid='tj-review']").waitFor({ timeout: 30_000 });
    assert.deepEqual(sent(app, REVIEW), []);                      // today's paragraph came with the journal
  });
  const empty = fakeServer({ journal: (scope) => journalDoc(scope, { trades: [] }) });
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: empty.fakeReads, fakeWrites: empty.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator("[data-testid='tj-improve']", { hasText: "No closed trades yet" }).waitFor({ timeout: 30_000 });
    assert.deepEqual(sent(app, REVIEW), []);
    assert.equal(await journal.locator("[data-testid='tj-trade']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the journal and a trade card fit with no sideways scroll", async () => {
  const server = fakeServer();
  await withApp({ view: "Bot", viewport: PHONE, storage: OPEN_ON_IT, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const { page } = app;
    const panel = page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    const journal = panel.locator("[data-testid='tj']");
    await journal.locator("[data-testid='tj-review']").waitFor({ timeout: 30_000 });
    assert.equal(await overflowsSideways(page), false);
    for (const selector of [".desk-tile", "[data-testid='tj-stat-best']", "[data-testid='tj-breakdown-time']", "[data-testid='tj-calendar']",
      "[data-testid='tj-review']", "[data-testid='tj-improve']"]) {
      const box = await journal.locator(selector).first().boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${selector} is cut off on a phone`);
    }
    await journal.locator("[data-testid='tj-trade']").first().click();
    await journal.locator("[data-testid='tj-detail']").waitFor();
    assert.equal(await overflowsSideways(page), false);
    for (const key of ["tj-note", "tj-note-save", "tj-chart"]) {
      const box = await journal.locator(`[data-testid='${key}']`).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${key} is cut off on a phone`);
    }
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused journal read says so in plain words", async () => {
  const server = fakeServer();
  const fakeReads = { ...server.fakeReads, [JOURNAL]: { status: 403, json: { error: "admin only" } } };
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-journal-all']").click({ timeout: 60_000 });
    await panel.locator("[data-testid='tj-error']", { hasText: "Only the owner can use the Ticker trader." }).waitFor({ timeout: 30_000 });
    assert.deepEqual(sent(app, REVIEW), []);
    assert.deepEqual(app.pageErrors, []);
  });
});
