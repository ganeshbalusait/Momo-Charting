// Bot page -> Paper (2026-11-27, frontend/src/BotAccounts.jsx): his "See Webull paper trade page mobile I want
// same how reset button pnl", "Use this journal for all" and "when I reset new money old journal save with money".
// One account screen for Paper 3, Paper 5, Research and Ticker: value, day / open / total P&L, money for the
// automatic trades, Reset, each account's own journal (the Scorecard's tiles and month calendar) and its past runs.
//
// The one read (GET /api/momx-scanner/bot-accounts) is canned and the reset is answered by the spec, so nothing
// reaches a server that changes anything. How the spec reaches the page: left-rail view "Bot", then the "Paper"
// button in the panel row ([data-testid='bh-panel-accounts']); the page is [data-testid='bot-accounts'].

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const READ = "GET /api/momx-scanner/bot-accounts";
const RESET = "POST /api/momx-scanner/bot-accounts/reset";
const MONEY = "POST /api/momx-scanner/bot-accounts/money";
const PAGE = "[data-testid='bot-view'] [data-testid='bot-accounts']";
const tid = (name) => `[data-testid='${name}']`;
const texts = async (locator) => (await locator.allInnerTexts()).map((s) => s.replace(/\s+/g, " ").trim());
const sent = (app, key) => app.writes.filter((w) => `${w.method} ${w.path}` === key).map((w) => JSON.parse(w.body));

const tiles = (netPnl, closed, wins, losses, more = {}) => ({ netPnl, closed, wins, losses, winPct: closed ? (wins / closed) * 100 : null, profitFactor: null,
  greenDays: 0, redDays: 0, dayWinPct: null, avgWin: null, avgLoss: null, ...more });
const account = (key, label, kind, more = {}) => ({ key, label, kind, note: "", run: { n: 1, money: 10000, perTrade: 300, startedAt: "2026-11-13T09:30:00-05:00", capped: false },
  value: 10000, free: 10000, invested: 0, dayPnl: 0, openPnl: 0, totalPnl: 0, positions: [], orders: [], journal: { tiles: tiles(0, 0, 0, 0), days: [] },
  skippedNoMoney: 0, pastRuns: [], resets: 0, canTradeTicker: key === "ticker", error: null, ...more });

const RESEARCH = account("research", "Research", "virtual", {
  note: "The virtual bot: every Level 1 / 2 / 3 signal, counted once, priced at the middle of the bid and ask. No order is sent.",
  run: { n: 2, money: 3000, perTrade: 300, startedAt: "2026-11-19T09:30:00-05:00", capped: true },
  value: 3474, free: 2932, invested: 542, dayPnl: -184, openPnl: 62, totalPnl: 474, skippedNoMoney: 2, resets: 1,
  positions: [{ symbol: "NVDA", contract: "150C · Dec 4", qty: 3, entry: 0.62, mark: 0.71, pnl: 27, pct: 14.5, at: "2026-11-25T10:42:00-05:00" }],
  orders: [{ symbol: "AMD", contract: "165C · Dec 4", qty: 1, status: "filled", price: 0.48, at: "2026-11-25T14:05:00-05:00" }],
  journal: { tiles: tiles(412, 14, 8, 6, { profitFactor: 1.62, greenDays: 3, redDays: 2, dayWinPct: 60, avgWin: 134, avgLoss: -111 }),
    days: [{ day: "2026-11-19", pnl: 210, trades: 3 }, { day: "2026-11-20", pnl: -96, trades: 2 }, { day: "2026-11-23", pnl: 188, trades: 4 },
      { day: "2026-11-24", pnl: -126, trades: 2 }, { day: "2026-11-25", pnl: 236, trades: 3 }] },
  pastRuns: [{ n: 1, money: 2000, perTrade: 300, startedAt: "2026-11-13T09:30:00-05:00", endedAt: "2026-11-19T09:30:00-05:00", endValue: 409, pnl: -1591, pnlPct: -79.55,
    trades: 8, tiles: tiles(-1591, 8, 0, 8, { redDays: 3 }), days: [{ day: "2026-11-13", pnl: -1300, trades: 4 }, { day: "2026-11-16", pnl: -291, trades: 4 }] }],
});
const DOC = { ok: true, asOf: "2026-11-27T01:10:00-05:00", order: ["paper3", "paper5", "research", "ticker"], accounts: {
  paper3: account("paper3", "Paper 3", "paper", { note: "Rules bot. Real paper fills at Alpaca.", value: 8409, free: 8409, totalPnl: -1591,
    journal: { tiles: tiles(-1591, 8, 0, 8, { redDays: 3 }), days: [{ day: "2026-11-13", pnl: -1300, trades: 4 }] } }),
  paper5: account("paper5", "Paper 5", "paper", { error: "Paper 5 could not be read just now. The other accounts are not affected." }),
  research: RESEARCH,
  ticker: account("ticker", "Ticker", "simulated"),
} };

async function openPaper(app) {
  const { page } = app;
  await page.locator(`[data-testid='bot-view'] ${tid("bh-panel-accounts")}`).click({ timeout: 90_000 });
  const root = page.locator(PAGE);
  await root.locator(tid("ba-value-number")).waitFor({ timeout: 60_000 });
  return root;
}

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  const size = viewport ? { viewport } : {};

  e2eTest(`${label}: Paper shows each account's value, P&L and money; one account's trouble does not hide the others`, async () => {
    await withApp({ view: "Bot", storage: { "bot.view": "home" }, ...size, fakeReads: { [READ]: { json: DOC } } }, async (app) => {
      const { page } = app;
      const root = await openPaper(app);
      const panels = page.locator(`[data-testid='bot-view'] ${tid("bh-panels")}`);
      assert.deepEqual(await texts(panels.locator("button")), ["Bot", "Paper", "Ticker", "Scorecard", "Research", "Ask"]);
      assert.deepEqual(await texts(panels.locator("button[aria-pressed='true']")), ["Paper"]);
      assert.deepEqual(await panels.locator("button").evaluateAll((els) => els.filter((el) => el.getBoundingClientRect().width < 44
        || el.getBoundingClientRect().height < 44 || el.scrollWidth > el.clientWidth + 1).map((el) => el.textContent)), [], "every page button fits");
      assert.deepEqual(await texts(root.locator(".ba-accounts button")), ["Paper 3", "Paper 5", "Research", "Ticker"]);
      // Paper 3 first: never reset, so no money limit yet and it says so.
      assert.equal((await root.locator(tid("ba-value-number")).innerText()).trim(), "$8,409.00");
      assert.equal((await root.locator(tid("ba-started")).innerText()).trim(), "Started with $10,000 on Nov 13 · run 1");
      assert.match(await root.locator(tid("ba-money")).innerText(), /no limit/);
      assert.equal(await root.locator(tid("ba-money-change")).count(), 0);
      // Paper 5 could not be read: the page says so in words and still shows the account.
      await root.locator(tid("ba-account-paper5")).click();
      await root.locator(".ba-error", { hasText: "Paper 5 could not be read just now" }).waitFor({ timeout: 15_000 });
      // Research: its own money, its own numbers.
      await root.locator(tid("ba-account-research")).click();
      await root.locator(tid("ba-value-number"), { hasText: "$3,474.00" }).waitFor({ timeout: 15_000 });
      // The page says what Research counts: Level signals only (his answer 2026-11-27).
      await root.locator(".ba-dim", { hasText: "every Level 1 / 2 / 3 signal, counted once" }).first().waitFor({ timeout: 15_000 });
      assert.equal((await root.locator(tid("ba-started")).innerText()).trim(), "Started with $3,000 on Nov 19 · run 2");
      assert.deepEqual(await texts(root.locator(`${tid("ba-value")} .ba-well`)), ["Day P&L −$184", "Open P&L +$62", "Total P&L +$474"]);
      const money = await root.locator(tid("ba-money")).innerText();
      assert.match(money, /\$300/);
      assert.match(money, /\$2,932/);
      assert.match(money, /2 trades were left out/);
      assert.match(await root.locator("section[aria-label='Positions']").innerText(), /NVDA[\s\S]*3 contracts · bought 0\.62 · now 0\.71[\s\S]*\+\$27/);
      await root.locator(tid("ba-tab-orders")).click();
      assert.match(await root.locator("section[aria-label='Orders']").innerText(), /AMD[\s\S]*1 contract · 0\.48 · Nov 25 2:05 PM[\s\S]*filled/);
      // The account and the list are remembered.
      await page.reload();
      await page.locator(`[data-testid='bot-view'] ${tid("bh-panel-accounts")}`).click({ timeout: 90_000 });
      await page.locator(`${PAGE} ${tid("ba-value-number")}`, { hasText: "$3,474.00" }).waitFor({ timeout: 60_000 });
      await page.locator(`${PAGE} section[aria-label='Orders']`).waitFor({ timeout: 15_000 });
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.writes, []);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`${label}: each account has its own journal, and a past run opens with its own tiles and calendar`, async () => {
    await withApp({ view: "Bot", storage: { "bot.view": "home", "bot.account": "research", "bot.accountTab": "journal" }, ...size, fakeReads: { [READ]: { json: DOC } } }, async (app) => {
      const { page } = app;
      const root = await openPaper(app);
      const journal = root.locator(tid("ba-journal"));
      await journal.waitFor({ timeout: 15_000 });
      const tile = (scope, key) => scope.locator(`.desk-tile[data-tile='${key}'] .desk-tile-value`).innerText();
      assert.equal((await tile(journal, "net")).trim(), "+$412");
      assert.equal((await tile(journal, "factor")).trim(), "1.62");
      assert.deepEqual(await texts(journal.locator(".desk-tile-label")), ["Net P&L", "Trade win %", "Profit factor", "Day win %", "Avg win / loss"]);
      assert.equal((await journal.locator(tid("ba-cal-total")).innerText()).replace(/\s+/g, " ").trim(), "Month: +$412 · 5 days traded");
      assert.match(await journal.locator("[data-day='2026-11-19']").innerText(), /\+\$210[\s\S]*3 trades/);
      assert.equal((await journal.locator("[data-day='2026-11-13'] .desk-cal-pnl").count()), 0, "the run before traded Nov 13; this run did not");
      // Paper 3 has a different journal.
      await root.locator(tid("ba-account-paper3")).click();
      await root.locator(`${tid("ba-journal")} .desk-tile[data-tile='net'] .desk-tile-value`, { hasText: "−$1,591" }).waitFor({ timeout: 15_000 });
      assert.match(await root.locator(`${tid("ba-journal")} [data-day='2026-11-13']`).innerText(), /−\$1\.3k/);
      // Past runs: how many resets, the money of each, and the saved journal of the run before.
      await root.locator(tid("ba-account-research")).click();
      await root.locator(tid("ba-tab-runs")).click();
      const runs = root.locator(tid("ba-runs"));
      assert.equal((await runs.locator(tid("ba-resets")).innerText()).trim(), "Research · reset 1 time");
      assert.deepEqual(await texts(runs.locator(".ba-run > span:first-child b")), ["Run 2 · started with $3,000", "Run 1 · started with $2,000"]);
      assert.match(await runs.locator(tid("ba-run-1")).innerText(), /Nov 13 to Nov 19 · 8 closed trades[\s\S]*−\$1,591[\s\S]*ended \$409/);
      await runs.locator(tid("ba-run-1")).click();
      const past = root.locator(tid("ba-past-journal"));
      await past.waitFor({ timeout: 15_000 });
      assert.equal((await tile(past, "net")).trim(), "−$1,591");
      assert.match(await past.locator("[data-day='2026-11-13']").innerText(), /−\$1\.3k[\s\S]*4 trades/);
      assert.equal(await past.locator("[data-day='2026-11-19'] .desk-cal-pnl").count(), 0, "the new run's days are not in the old run's calendar");
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.writes, []);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`${label}: Reset asks for the new money, refuses money that is not an account, and sends one request`, async () => {
    const fakeWrites = { [RESET]: { json: { ok: true, account: RESEARCH } }, [MONEY]: { json: { ok: true, account: RESEARCH } } };
    await withApp({ view: "Bot", storage: { "bot.view": "home", "bot.account": "research", "bot.accountTab": "positions" }, ...size, fakeReads: { [READ]: { json: DOC } }, fakeWrites }, async (app) => {
      const { page } = app;
      const root = await openPaper(app);
      await root.locator(tid("ba-reset")).click();
      const sheet = root.locator(tid("ba-reset-sheet"));
      await sheet.waitFor({ timeout: 15_000 });
      assert.match(await sheet.innerText(), /This run ends and is saved[\s\S]*Started with\s*\$3,000[\s\S]*Closed trades\s*14[\s\S]*\+\$474/);
      const go = sheet.locator(tid("ba-reset-go"));
      await sheet.locator("#ba-money").fill("29");                       // his "5k, 29, 3k": 29 dollars is not an account
      await sheet.locator(".ba-error", { hasText: "at least $500" }).waitFor({ timeout: 15_000 });
      assert.equal(await go.isDisabled(), true);
      await sheet.locator(".ba-presets button", { hasText: "$5k" }).click();
      assert.equal((await go.innerText()).trim(), "Reset and start run 3 with $5,000");
      assert.match(await sheet.innerText(), /Run 2 moves to Past runs with its money and its P&L\. Nothing is deleted\./);
      assert.deepEqual(app.writes, [], "nothing is sent before the button");
      await go.click();
      await root.locator(tid("ba-value-number")).waitFor({ timeout: 30_000 });
      assert.deepEqual(sent(app, RESET), [{ account: "research", money: 5000, perTrade: 300 }]);
      // Money per trade can change without a reset.
      await root.locator(tid("ba-money-change")).click();
      const form = root.locator(tid("ba-money-form"));
      await form.locator("#ba-per-only").fill("150");
      await form.locator("button[type='submit']").click();
      await root.locator(tid("ba-money-change")).waitFor({ timeout: 30_000 });
      assert.deepEqual(sent(app, MONEY), [{ account: "research", perTrade: 150 }]);
      assert.equal(app.writes.length, 2);
      // Trade a ticker opens the Ticker trader.
      await root.locator(tid("ba-trade-ticker")).click();
      await page.locator(`[data-testid='bot-view'] ${tid("bh-panel-ticker")}[aria-pressed='true']`).waitFor({ timeout: 60_000 });
      assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });

  e2eTest(`${label}: a reset the server refuses changes nothing and says why`, async () => {
    const fakeWrites = { [RESET]: { status: 409, json: { ok: false, error: "Paper 3 still has open trades. Close them or wait for them to close, then reset." } } };
    await withApp({ view: "Bot", storage: { "bot.view": "home", "bot.account": "paper3" }, ...size, fakeReads: { [READ]: { json: DOC } }, fakeWrites }, async (app) => {
      const root = await openPaper(app);
      await root.locator(tid("ba-reset")).click();
      const sheet = root.locator(tid("ba-reset-sheet"));
      await sheet.locator(".ba-presets button", { hasText: "$3k" }).click();
      assert.match(await sheet.innerText(), /The Alpaca paper account itself is not changed/);
      await sheet.locator(tid("ba-reset-go")).click();
      await sheet.locator(".ba-error[role='alert']", { hasText: "Paper 3 still has open trades" }).waitFor({ timeout: 30_000 });
      assert.equal(await sheet.isVisible(), true, "the sheet stays open so he can try again");
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
