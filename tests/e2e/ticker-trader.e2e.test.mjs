// Bot page -> Research -> Ticker trader (2026-11-13): he types a ticker, picks Call or Put, and the
// app trades it from the chart in a simulated $10,000 account. Every route is canned here: the
// harness blocks any write a spec does not answer, and nothing in this feature reaches a broker.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { AI_READ, AI_REPORT, BOT_READ, LEARNING_READ, MIRRORS, MIRROR_READ, TEAM, botAnswer } from "./trading-bot.fixtures.mjs";

const READ = "GET /api/momx-scanner/ticker-trader";
const START = "POST /api/momx-scanner/ticker-trader/start";
const STOP = "POST /api/momx-scanner/ticker-trader/stop";
const RESET = "POST /api/momx-scanner/ticker-trader/reset";
const AGAIN = "POST /api/momx-scanner/ticker-trader/again";
const PANEL = "[data-testid='ticker-trader']";
const OPEN_ON_IT = { "bot.tickerTrader": "1" };

const TRADE = {
  id: "NVDA-CALL-2", day: "2026-11-13", ticker: "NVDA", side: "CALL", contract: "NVDA  261114C00165000", strike: 165, expiry: "2026-11-14",
  qty: 3, held: 2, split: [1, 1, 1], entry: 1.45, entrySpot: 158.4, cost: 435, boughtAt: "2026-11-13T10:05:00-05:00",
  stopStart: 156.9, stopStock: 158.4, stopKind: "break-even", t1: 161, t2: 164.6, t3: 167, bid: 2.0, bidAt: "2026-11-13T10:42:00-05:00",
  targets: [{ label: "T1", stock: 161, reason: "half the expected move" }, { label: "T2", stock: 164.6, reason: "full expected move" },
    { label: "T3", stock: 167, reason: "OI level past the move" }],
  hits: [{ target: "T1", at: "2026-11-13T10:31:00-05:00", stock: 161.02, sold: 1, price: 2.1 }],
  sells: [{ at: "2026-11-13T10:31:00-05:00", qty: 1, price: 2.1, pl: 65 }],
  events: [{ at: "2026-11-13T10:31:00-05:00", text: "T1 hit: sold 1 at $2.10, stop to break-even." }],
  status: "open", pl: 65, why: "You picked Call. Bought 3 at the ask $1.45 with NVDA at 158.40.",
};
const CLOSED = { id: "NVDA-CALL-1", day: "2026-11-12", ticker: "NVDA", side: "CALL", strike: 170, expiry: "2026-11-14", qty: 3, entry: 1.2,
  boughtAt: "2026-11-12T10:00:00-05:00", closedAt: "2026-11-12T10:20:00-05:00", status: "closed", pl: -142,
  reason: "Stop: stock traded through 166.80, sold 3 at $0.73.", balanceAfter: 9858 };
const NVDA = { id: "NVDA-CALL", ticker: "NVDA", side: "CALL", status: "open", note: "In a trade: holding 2 of 3", noteAt: "2026-11-13T10:42:00-05:00",
  balance: 9633, startBalance: 10000, net: -77, openPl: 110, liveNet: 33, tradeCount: 1, wins: 0, stock: 161.2, stockAt: "2026-11-13T10:42:00-05:00",
  open: TRADE, trades: [CLOSED] };
// One account for every ticker (2026-11-16): $10,000, -$142 closed and +$65 banked, 2 contracts held that cost $290
// and are worth $400 at the $2.00 bid.
const ACCOUNT = { startBalance: 10000, cash: 9633, invested: 290, openValue: 400, value: 10033, net: 33, realized: -77, openTrades: 1 };
const fresh = (size) => ({ startBalance: size, cash: size, invested: 0, openValue: 0, value: size, net: 0, realized: 0, openTrades: 0 });
const waiting = (ticker, side) => ({ id: `${ticker}-${side}`, ticker, side, status: "waiting", note: "Market closed: buys at the 9:30 open",
  noteAt: "2026-11-13T08:00:00-05:00", balance: 10000, startBalance: 10000, net: 0, tradeCount: 0, wins: 0, open: null, trades: [] });

/** A tiny stand-in for the server: the runs, and what each button does to them. */
function fakeServer(initial = [NVDA], account = ACCOUNT) {
  const state = { runs: initial.map((r) => structuredClone(r)), account: structuredClone(account) };
  const doc = () => ({ runs: state.runs.map((r) => ({ ...r, account: state.account })), account: state.account, maxRuns: 10, startBalance: 10000,
    marketOpen: true, asOf: "2026-11-13T10:42:05-05:00", simulated: true });
  const find = (write) => state.runs.find((r) => r.id === JSON.parse(write.body).id);
  const fakeReads = {
    [READ]: () => ({ json: doc() }),
    [BOT_READ]: (read) => ({ json: botAnswer(read.search) }),
    [LEARNING_READ]: { json: TEAM }, [AI_READ]: { json: AI_REPORT }, [MIRROR_READ]: { json: MIRRORS },
  };
  const fakeWrites = {
    [START]: (write) => {
      const { ticker, side } = JSON.parse(write.body);
      if (ticker === "ZZZZ") return { status: 400, json: { ok: false, error: "No saved option chain for ZZZZ. Open its chart once, then try again." } };
      const run = { ...waiting(ticker, side), balance: state.account.cash, startBalance: state.account.startBalance };
      state.runs.push(run);
      return { json: { ok: true, run } };
    },
    [STOP]: (write) => {
      const run = find(write);
      state.account = { ...fresh(10000), cash: 10033, value: 10033, net: 33, realized: 33 };
      Object.assign(run, { status: "stopped", open: null, note: "Stopped. Tap Trade again for the next one.", tradeCount: 2, wins: 1, balance: 10033, net: 33, openPl: null, liveNet: 33,
        trades: [...run.trades, { ...TRADE, status: "closed", closedAt: "2026-11-13T10:43:00-05:00", pl: 175, balanceAfter: 10033,
          reason: "T1 hit: sold 1 at $2.10, stop to break-even. Stopped by you: sold 2 at the bid $2.00." }] });
      return { json: { ok: true, run } };
    },
    [AGAIN]: (write) => {
      const run = find(write);
      Object.assign(run, { status: "waiting", note: "Buying on the next price update" });
      return { json: { ok: true, run } };
    },
    [RESET]: (write) => {
      // Reset is the whole account: every ticker's trade and history go, with or without a run id.
      const run = find(write) || null;
      const { size } = JSON.parse(write.body);
      state.account = fresh(size);
      for (const each of state.runs) {
        Object.assign(each, { status: "idle", open: null, trades: [], tradeCount: 0, wins: 0, balance: size, startBalance: size, net: 0,
          note: `Reset: fresh $${size.toLocaleString("en-US")}. Tap Trade again to start.` });
      }
      return { json: { ok: true, run, account: state.account } };
    },
  };
  return { fakeReads, fakeWrites, state };
}

const SHOTS = process.env.TT_SHOTS || "";            // a folder: save a picture of each state there
const shot = async (app, name) => { if (SHOTS) await app.page.screenshot({ path: `${SHOTS}/${name}.png`, fullPage: true }); };
const texts = async (locator) => (await locator.allTextContents()).map((s) => s.trim());
const sent = (app, key) => app.writes.filter((w) => `${w.method} ${w.path}` === key).map((w) => JSON.parse(w.body));

e2eTest("Research has a Ticker trader button beside All trades; typing a ticker with Put starts a run", async () => {
  const server = fakeServer();
  await withApp({ view: "Bot", fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const { page } = app;
    const which = page.locator("[role='group'][aria-label='Which bot']");
    await which.waitFor({ timeout: 60_000 });
    assert.deepEqual(await texts(which.locator("button")), ["All trades", "Ticker trader"]);
    await which.locator("button", { hasText: "Ticker trader" }).click();
    const panel = page.locator(PANEL);
    await panel.locator("[data-testid='tt-row']").first().waitFor({ timeout: 30_000 });
    assert.match(await panel.locator("[data-testid='tt-simulated']").textContent(), /Simulated inside the app\. No order goes to any broker\./);
    assert.equal(await panel.locator("[aria-label='Call or Put'] button[aria-pressed='true']").textContent(), "Call");
    assert.match(await panel.locator("[data-testid='tt-row']").first().textContent(), /NVDA · Call.*In a trade: holding 2 of 3 · open \+\$110 \(\+38%\).*\+\$33/s);   // closed −$77 + $110 open (2026-11-18)
    // "show me latest trade top with date and time and expiry, con[tract]" (2026-11-18).
    assert.deepEqual(await texts(panel.locator("[data-testid='tt-row']").first().locator("[data-testid='tt-row-trade'] em")),
      ["165 Call · exp Nov 14", "Bought Nov 13, 10:05 ET"]);
    // "Make it a dashboard" (2026-11-18): four tiles over the list. 1 closed (-$142, a loss), 1 open worth +$175 at the
    // bid with T1's $65 banked; asOf is Nov 13, the closed trade was Nov 12, so today is the open trade alone.
    assert.deepEqual(await texts(panel.locator("[data-testid='tt-dash'] .tt-tile b")), ["+$175", "0%", "2", "NVDA +$33"]);
    // "I don't see percentage": +$33 on the $360 + $435 the two trades cost = +4%, under the money.
    assert.equal(await panel.locator("[data-testid='tt-row'] [data-testid='tt-row-pct']").first().textContent(), "+4%");

    // A ticker that cannot be sent is refused on the screen, with no request.
    await panel.locator("#tt-ticker").fill("nv1da");
    await panel.locator("[data-testid='tt-trade']").click();
    await panel.locator("[data-testid='tt-error']", { hasText: "letters only" }).waitFor();
    assert.deepEqual(sent(app, START), []);
    // The server's refusal is shown in its own words.
    await panel.locator("#tt-ticker").fill("zzzz");
    await panel.locator("[data-testid='tt-trade']").click();
    await panel.locator("[data-testid='tt-error']", { hasText: "No saved option chain for ZZZZ" }).waitFor();

    // One account for every ticker (2026-11-16): what it is worth now, the cash left, up or down.
    const account = panel.locator("[data-testid='tt-account']");
    assert.deepEqual(await texts(account.locator(".tt-tile b")), ["$10,033", "$9,633", "+$33"]);
    assert.match(await account.textContent(), /One account for every ticker.*from \$10,000.*\$290 in 1 open trade/s);
    // The start form has no account size any more: the size belongs to the account (Reset account).
    assert.equal(await panel.locator("[data-testid='tt-size']").count(), 0);

    await panel.locator("[aria-label='Call or Put'] button", { hasText: "Put" }).click();
    await panel.locator("[aria-label='Quick tickers'] button", { hasText: "SPY" }).click();
    await panel.locator("[data-testid='tt-trade']").click();
    await panel.locator("[data-testid='tt-run']").waitFor({ timeout: 15_000 });
    assert.deepEqual(sent(app, START).at(-1), { ticker: "SPY", side: "PUT" });
    assert.equal((await panel.locator("[data-testid='tt-status']").textContent()).trim(), "Waiting to buy");
    assert.match(await panel.locator("[data-testid='tt-note']").textContent(), /Market closed: buys at the 9:30 open/);
    // The new ticker shows the SAME account: its cash and its starting size, not a second $10,000.
    assert.equal((await panel.locator("[data-testid='tt-balance']").textContent()).trim(), "$9,633");
    assert.equal((await panel.locator("[data-testid='tt-from']").textContent()).trim(), "from $10,000");
    assert.match(await panel.locator("[data-testid='tt-account-line']").textContent(), /Account now \$10,033 \(\+\$33 from \$10,000\).*shared by every ticker/s);

    await panel.locator("button", { hasText: "‹ Ticker trader" }).click();
    assert.equal(await panel.locator("[data-testid='tt-row']").count(), 2);
    // Reset account from the list page: the size picker, an amount out of range refused, then the whole account fresh.
    await account.locator("[data-testid='tt-account-reset']").click();
    const picker = account.locator("[data-testid='tt-reset-panel']");
    await picker.waitFor();
    assert.deepEqual(await texts(picker.locator("[aria-label='Account size'] button")), ["$2K", "$5K", "$10K", "$100K"]);
    assert.equal(await picker.locator("[data-testid='tt-size-input']").inputValue(), "10000");
    await picker.locator("[data-testid='tt-size-input']").fill("100");
    await picker.locator("[data-testid='tt-reset-go']").click();
    await picker.locator("[data-testid='tt-reset-error']", { hasText: "at least $500" }).waitFor();
    assert.deepEqual(sent(app, RESET), []);
    await picker.locator("[data-testid='tt-size-2000']").click();
    await picker.locator("[data-testid='tt-reset-go']").click();
    await account.locator("[data-testid='tt-account-value']", { hasText: "$2,000" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(sent(app, RESET), [{ size: 2000 }]);
    assert.deepEqual(await texts(account.locator(".tt-tile b")), ["$2,000", "$2,000", "$0"]);
    assert.match(await account.textContent(), /from \$2,000.*nothing in trades/s);
    assert.equal(await account.locator("[data-testid='tt-reset-panel']").count(), 0);
    // Back to the All trades view, untouched.
    await page.locator("[role='group'][aria-label='Which bot'] button", { hasText: "All trades" }).click();
    await page.locator("section.bot-kpis[aria-label='Totals']").waitFor({ timeout: 60_000 });
    assert.equal(await page.locator(PANEL).count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

// The "Trade a ticker" box at the top of Research (2026-11-13, his "I dnt see research trade box"):
// it only opens the Ticker trader with the ticker and side filled in; Trade is still his own tap.
for (const [name, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  e2eTest(`${name}: the Trade a ticker box on Research opens the Ticker trader with the ticker and Put filled in`, async () => {
    const server = fakeServer();
    await withApp({ view: "Bot", viewport, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
      const { page } = app;
      const box = page.locator("[data-testid='bot-quick-trade']");
      await box.waitFor({ timeout: 60_000 });
      const go = box.locator("button[type='submit']");
      assert.equal(await go.isDisabled(), true, "Open is off until a ticker is typed");
      // It sits near the top, right under All trades | Ticker trader, not below the page's settings.
      const top = await box.boundingBox();
      assert.ok(top && top.y < 600, `the box is too far down: ${JSON.stringify(top)}`);
      assert.equal(await box.locator("[aria-label='Call or Put'] button[aria-pressed='true']").textContent(), "Call");
      await box.locator("input[aria-label='Ticker to trade']").fill("nvda");
      await box.locator("[aria-label='Call or Put'] button", { hasText: "Put" }).click();
      await go.click();
      const panel = page.locator(PANEL);
      await panel.locator("#tt-ticker").waitFor({ timeout: 30_000 });
      assert.equal(await panel.locator("#tt-ticker").inputValue(), "NVDA");
      assert.equal(await panel.locator("[aria-label='Call or Put'] button[aria-pressed='true']").textContent(), "Put");
      assert.deepEqual(sent(app, START), [], "opening the Ticker trader must not trade by itself");
      if (viewport) assert.equal(await overflowsSideways(page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

e2eTest("a run shows the chart's ladder, the trades with reasons, and Stop / Trade again / Reset / the chart button", async () => {
  const server = fakeServer();
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const { page } = app;
    const panel = page.locator(PANEL);
    await panel.locator("[data-testid='tt-row']").first().click({ timeout: 60_000 });
    const run = panel.locator("[data-testid='tt-run']");
    await run.waitFor();
    assert.equal((await run.locator("[data-testid='tt-status']").textContent()).trim(), "In a trade · CALL");
    assert.deepEqual(await texts(run.locator(".tt-tile b")), ["$9,633", "−$77", "0 of 1"]);
    assert.deepEqual(await texts(run.locator(".tt-level b")), ["T3", "T2", "T1 ✓", "Entry", "Stop"]);
    assert.match(await run.locator("[data-testid='tt-level-T1']").textContent(), /161\.00.*hit 10:31 ET · sold 1/s);
    assert.match(await run.locator("[data-testid='tt-level-Entry']").textContent(), /158\.40 · ask \$1\.45.*3 contracts/s);
    assert.match(await run.locator("[data-testid='tt-level-Stop']").textContent(), /158\.40.*moved to break-even/s);
    assert.match(await run.locator("[data-testid='tt-open']").textContent(), /NVDA 165 Call · Nov 14.*\+38%.*Why: You picked Call/s);
    assert.match(await run.locator("[data-testid='tt-trade']").first().textContent(), /Nov 12 · 170 Call.*−\$142.*Stop: stock traded through 166\.80/s);

    await shot(app, "tt-desktop-run");
    // The chart button hands the app a link to that ticker's chart.
    await page.evaluate(() => { window.__ttLinks = []; window.addEventListener("agx:open-link", (e) => window.__ttLinks.push(e.detail), { capture: true }); });
    await run.locator("[data-testid='tt-chart']").click();
    const links = await page.evaluate(() => window.__ttLinks);
    assert.deepEqual(links.map((l) => [l.view, l.ticker]), [["Charts & OI", "NVDA"]]);
  });
  const again = fakeServer();
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads: again.fakeReads, fakeWrites: again.fakeWrites }, async (app) => {
    const panel = app.page.locator(PANEL);
    await panel.locator("[data-testid='tt-row']").first().click({ timeout: 60_000 });
    const run = panel.locator("[data-testid='tt-run']");
    await run.locator("button", { hasText: /^Stop$/ }).click();
    await run.locator("[data-testid='tt-status']", { hasText: "Stopped" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(sent(app, STOP), [{ id: "NVDA-CALL" }]);
    assert.match(await run.locator("[data-testid='tt-trade']").first().textContent(), /\+\$175.*Stopped by you: sold 2 at the bid \$2\.00/s);
    await run.locator("[data-testid='tt-again']").click();
    await run.locator("[data-testid='tt-status']", { hasText: "Waiting to buy" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(sent(app, AGAIN), [{ id: "NVDA-CALL" }]);
    // Reset opens the size picker filled with the run's starting size; Cancel sends nothing.
    await run.locator("[data-testid='tt-reset']").click();
    const picker = run.locator("[data-testid='tt-reset-panel']");
    await picker.waitFor();
    assert.equal(await picker.locator("[data-testid='tt-size-input']").inputValue(), "10000");
    assert.equal((await picker.locator("[data-testid='tt-reset-go']").textContent()).trim(), "Reset to $10,000");
    await picker.locator("[data-testid='tt-reset-cancel']").click();
    assert.equal(await run.locator("[data-testid='tt-reset-panel']").count(), 0);
    assert.deepEqual(sent(app, RESET), []);
    // An amount out of range is explained and not sent.
    await run.locator("[data-testid='tt-reset']").click();
    await picker.locator("[data-testid='tt-size-input']").fill("2,000,000");
    await picker.locator("[data-testid='tt-reset-go']").click();
    await picker.locator("[data-testid='tt-reset-error']", { hasText: "at most $1,000,000" }).waitFor();
    assert.deepEqual(sent(app, RESET), []);
    await picker.locator("[data-testid='tt-size-2000']").click();
    assert.equal((await picker.locator("[data-testid='tt-reset-go']").textContent()).trim(), "Reset to $2,000");
    await picker.locator("[data-testid='tt-reset-go']").click();
    await run.locator("[data-testid='tt-note']", { hasText: "Reset: fresh $2,000" }).waitFor({ timeout: 15_000 });
    assert.deepEqual(sent(app, RESET), [{ id: "NVDA-CALL", size: 2000 }]);
    assert.equal(await run.locator("[data-testid='tt-reset-panel']").count(), 0);
    assert.equal(await run.locator("[data-testid='tt-trade']").count(), 0);
    assert.equal((await run.locator("[data-testid='tt-balance']").textContent()).trim(), "$2,000");
    assert.equal((await run.locator("[data-testid='tt-from']").textContent()).trim(), "from $2,000");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("phone width: the Ticker trader list and a run fit with no sideways scroll", async () => {
  const server = fakeServer([NVDA, waiting("SPY", "PUT")]);
  await withApp({ view: "Bot", viewport: PHONE, storage: OPEN_ON_IT, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
    const { page } = app;
    const panel = page.locator(PANEL);
    await panel.locator("[data-testid='tt-row']").first().waitFor({ timeout: 60_000 });
    await shot(app, "tt-phone-list");
    assert.equal(await overflowsSideways(page), false);
    for (const selector of ["#tt-ticker", "[data-testid='tt-trade']", "[data-testid='tt-row']", "[data-testid='tt-account']",
      "[data-testid='tt-account-reset']", "[data-testid='tt-account-value']", "[data-testid='tt-account-cash']", "[data-testid='tt-account-net']"]) {
      const box = await panel.locator(selector).first().boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${selector} is cut off on a phone`);
    }
    const which = await texts(page.locator("[role='group'][aria-label='Which bot'] button"));
    assert.deepEqual(which, ["All trades", "Ticker trader"]);
    await panel.locator("[data-testid='tt-row']").first().click();
    await panel.locator("[data-testid='tt-run']").waitFor();
    assert.equal(await overflowsSideways(page), false);
    for (const key of ["T3", "T2", "T1", "Entry", "Stop"]) {
      const box = await panel.locator(`[data-testid='tt-level-${key}']`).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${key} is cut off on a phone`);
    }
    await shot(app, "tt-phone-run");
    // The Reset size picker fits a phone too.
    await panel.locator("[data-testid='tt-reset']").click();
    await panel.locator("[data-testid='tt-reset-panel']").waitFor();
    assert.equal(await overflowsSideways(page), false);
    for (const key of ["tt-size-input", "tt-size-100000", "tt-reset-go", "tt-reset-cancel"]) {
      const box = await panel.locator(`[data-testid='tt-reset-panel'] [data-testid='${key}']`).boundingBox();
      assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, `${key} is cut off on a phone`);
    }
    await shot(app, "tt-phone-reset");
    await panel.locator("[data-testid='tt-reset-cancel']").click();
    await panel.locator("[data-testid='tt-chart']").scrollIntoViewIfNeeded();
    assert.ok(await panel.locator("[data-testid='tt-chart']").isVisible(), "the page does not scroll to the chart button");
    assert.deepEqual(app.pageErrors, []);
  });
});

e2eTest("a refused read says so in plain words", async () => {
  const server = fakeServer();
  const fakeReads = { ...server.fakeReads, [READ]: { status: 403, json: { error: "admin only" } } };
  await withApp({ view: "Bot", storage: OPEN_ON_IT, fakeReads }, async (app) => {
    await app.page.locator(`${PANEL} [role='alert']`, { hasText: "Only the owner can use the Ticker trader." }).waitFor({ timeout: 60_000 });
    assert.equal(await app.page.locator("[data-testid='tt-row']").count(), 0);
    assert.deepEqual(app.pageErrors, []);
  });
});

// 2026-11-16: a contract that costs more than 10% of the account is bought once (LLY at $440 on his $2,000 account).
const ONE = { ...TRADE, id: "LLY-CALL-1", ticker: "LLY", contract: "LLY   261120C01100000", strike: 1100, expiry: "2026-11-20", qty: 1, held: 1,
  split: [0, 1, 0], entry: 4.4, entrySpot: 1052.3, cost: 440, stopStart: 1040, stopStock: 1040, stopKind: "stop", t1: 1062, t2: 1071, t3: 1080,
  bid: 4.6, hits: [], sells: [], events: [], pl: 0,
  targets: [{ label: "T1", stock: 1062, reason: "half the expected move" }, { label: "T2", stock: 1071, reason: "full expected move" },
    { label: "T3", stock: 1080, reason: "OI level past the move" }],
  why: "You picked Call. Bought 1 at the ask $4.40 with LLY at 1052.30. 1 not 3: one contract costs more than 10% of the account, so the bot bought just one." };
const LLY = { id: "LLY-CALL", ticker: "LLY", side: "CALL", status: "open", note: "In a trade: 1 x 1100 Call", noteAt: "2026-11-13T10:42:00-05:00",
  balance: 1560, startBalance: 2000, net: 0, tradeCount: 0, wins: 0, stock: 1053.1, stockAt: "2026-11-13T10:42:00-05:00", open: ONE, trades: [] };
const LLY_ACCOUNT = { startBalance: 2000, cash: 1560, invested: 440, openValue: 460, value: 2020, net: 20, realized: 0, openTrades: 1 };

for (const [name, viewport] of [["desktop", undefined], ["phone", PHONE]]) {
  e2eTest(`${name}: a contract over 10% of a $2,000 account is shown bought once, with the reason`, async () => {
    const server = fakeServer([LLY], LLY_ACCOUNT);
    await withApp({ view: "Bot", viewport, storage: OPEN_ON_IT, fakeReads: server.fakeReads, fakeWrites: server.fakeWrites }, async (app) => {
      const panel = app.page.locator(PANEL);
      // The rule on the list page says so before he starts.
      await panel.locator(".tt-rules", { hasText: "still bought, one only, when the account can pay for it" }).waitFor({ timeout: 60_000 });
      // The buy took $440 out of the $2,000: the account card says so before he opens the ticker.
      // (The rules are on the page before the first read lands; the card comes with the read.)
      await panel.locator("[data-testid='tt-account-value']").waitFor({ timeout: 60_000 });
      assert.deepEqual(await texts(panel.locator("[data-testid='tt-account'] .tt-tile b")), ["$2,020", "$1,560", "+$20"]);
      assert.match(await panel.locator("[data-testid='tt-account']").textContent(), /from \$2,000.*\$440 in 1 open trade/s);
      await panel.locator("[data-testid='tt-row']").first().click();
      const run = panel.locator("[data-testid='tt-run']");
      await run.waitFor();
      assert.equal((await run.locator("[data-testid='tt-status']").textContent()).trim(), "In a trade · CALL");
      assert.equal((await run.locator("[data-testid='tt-balance']").textContent()).trim(), "$1,560");
      assert.equal((await run.locator("[data-testid='tt-from']").textContent()).trim(), "from $2,000");
      assert.match(await run.locator("[data-testid='tt-level-Entry']").textContent(), /ask \$4\.40.*1 contract(?!s)/s);
      assert.match(await run.locator("[data-testid='tt-open']").textContent(), /LLY 1100 Call.*1 not 3: one contract costs more than 10% of the account/s);
      assert.equal(await run.getByText("Not bought").count(), 0);
      assert.equal(await overflowsSideways(app.page), false);
      await shot(app, `tt-${name}-one-contract`);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
