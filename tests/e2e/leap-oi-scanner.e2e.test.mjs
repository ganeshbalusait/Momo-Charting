// LEAP OI Scanner page (2026-11-17). The board on screen is the REAL server's reply: before the specs run, the
// integration test asks the real /api/leap-oi-scanner on scratch files (Schwab faked) and saves what came back; the
// browser plays it. Checked at desktop and phone width: the two headline cards, the ENTRY list, the whole list with
// delta 0.10 and up only, the filter chips sending the right request, Chart opening the chart, nothing cut off.

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { before } from "node:test";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, marketHoursBlock, overflowsSideways, scrollsToTheEnd, withApp } from "./harness.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const VIEW = "[data-testid='leap-oi-view']";
const WIDTHS = [["desktop", undefined], ["phone width", PHONE]];

let real = null;

before(() => {
  if (marketHoursBlock()) return;
  const out = path.join(mkdtempSync(path.join(tmpdir(), "agx-leap-oi-")), "board.json");
  execFileSync(path.join(REPO, ".venv", "Scripts", "python.exe"),
    ["-m", "pytest", "tests/test_momx_leap_oi_scanner_api.py", "-q", "-p", "no:cacheprovider", "-k", "background_then_shows"],
    { cwd: REPO, encoding: "utf8", timeout: 240_000, env: { ...process.env, LEAP_OI_CAPTURE: out, PYTHONDONTWRITEBYTECODE: "1" } });
  real = JSON.parse(readFileSync(out, "utf8"));
}, { timeout: 260_000 });

for (const [label, viewport] of WIDTHS) {
  const phone = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`LEAP OI Scanner at ${label}: headline cards, ENTRY list, whole list, chips, nothing cut off`, async () => {
    const asked = [];
    const reads = {
      "GET /api/leap-oi-scanner": (read) => { asked.push(read.search || ""); return { json: real }; },
      "GET /api/watchlist": { json: { tickers: ["PLTR", "NVDA", "AMD"] } },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.waitFor({ timeout: 90_000 });
      await view.locator(".leapoi-hero").first().waitFor({ timeout: 30_000 });
      const heroes = await view.locator(".leapoi-hero").allInnerTexts();
      assert.match(heroes[0], /Biggest call open interest[\s\S]*NVDA|AAPL/);
      assert.match(heroes[0], /200 Call/);
      assert.match(heroes[1], /140 Put/);
      // 2026-11-17 "who is dominating call or put?": one board bar that names the leader, and one per ticker card.
      const dom = view.locator("[data-testid='dominance']");
      assert.match(await dom.first().innerText(), /Calls vs puts on this board[\s\S]*(Calls lead|Puts lead|About even) · [\s\S]*Calls [\d,.KM]+ · Puts [\d,.KM]+/);
      assert.equal(await dom.count(), 1 + real.byTicker.length);
      // 2026-11-17 "give me mid prize also": every row shows the mid and what one contract costs at it.
      const top = real.rows[0];
      const mid = (Math.round(((top.bid + top.ask) / 2) * 100) / 100).toFixed(2);
      assert.match(heroes[0], new RegExp(`Mid ${mid.replace(".", "\\.")}`));
      const firstRow = await view.locator(phone ? "[data-testid='leap-oi-row-card']" : "[data-testid='leap-oi-row']").first().innerText();
      assert.ok(firstRow.includes(mid), `mid ${mid} missing from: ${firstRow}`);
      assert.match(firstRow, /\$[\d,]+/);
      // Order: the most put-heavy ticker first, then back to A-Z.
      const names = () => view.locator("[data-testid='leap-oi-ticker'] .leapoi-tk").allInnerTexts();
      const putHeavy = [...real.byTicker].sort((a, b) => b.putOi / (b.callOi + b.putOi) - a.putOi / (a.callOi + a.putOi))[0].ticker;
      const order = view.getByRole("button", { name: "Most put-heavy" });
      await (phone ? order.tap() : order.click());
      for (let i = 0; i < 30 && (await names())[0] !== putHeavy; i += 1) await page.waitForTimeout(100);
      const sharePut = (t) => t.putOi / (t.callOi + t.putOi);
      const firstName = (await names())[0];
      const first = real.byTicker.find((t) => t.ticker === firstName);
      assert.ok(real.byTicker.every((t) => sharePut(t) <= sharePut(first) + 1e-9), "first card is not the most put-heavy");
      const az = view.getByRole("button", { name: "A–Z" });
      await (phone ? az.tap() : az.click());
      const rowSel = phone ? "[data-testid='leap-oi-row-card']" : "[data-testid='leap-oi-row']";
      const entrySel = phone ? "[data-testid='leap-oi-entry-row-card']" : "[data-testid='leap-oi-entry-row']";
      assert.equal(await view.locator(rowSel).count(), real.rows.length);
      assert.ok((await view.locator(entrySel).count()) > 0);
      const allText = await view.innerText();
      assert.ok(!allText.includes("250 Call"), "the 0.08-delta lottery strike must not show");
      assert.match(allText, /ENTRY/);
      assert.match(allText, /coin flip/);
      // Separate boards (2026-11-17 "give me separate mag7 and watchlist"): the page opens on Mag7 alone...
      assert.match(decodeURIComponent(asked[0]), /symbols=AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA&from=3&to=12&side=both&min_delta=0\.10/);
      assert.equal(await view.getByRole("button", { name: /Mag7 \+ Watchlist/ }).count(), 0);
      // ...and Watchlist asks for the watchlist without the Mag7 names (NVDA is on the watchlist too).
      const board = view.getByRole("button", { name: "Watchlist", exact: true });
      await (phone ? board.tap() : board.click());
      for (let i = 0; i < 50 && !asked.some((s) => decodeURIComponent(s).includes("symbols=PLTR,AMD&")); i += 1) await page.waitForTimeout(100);
      assert.ok(asked.some((s) => decodeURIComponent(s).includes("symbols=PLTR,AMD&")), asked.join("\n"));
      assert.match(await view.locator(".leapoi-head p").innerText(), /· Watchlist/);
      // Chips change the request.
      const chip = view.getByRole("button", { name: "ENTRY only" });
      await (phone ? chip.tap() : chip.click());
      await page.waitForFunction((n) => window.__leapAsked === undefined || true, asked.length);
      for (let i = 0; i < 50 && !asked.some((s) => s.includes("entry_only=1")); i += 1) await page.waitForTimeout(100);
      assert.ok(asked.some((s) => s.includes("entry_only=1")), asked.join("\n"));
      // By ticker (2026-11-17, "what about other tickers from watchlist"): one card per ticker, searchable.
      const lines = view.locator("[data-testid='leap-oi-ticker']");
      assert.equal(await lines.count(), real.byTicker.length);
      // Every ticker shows at once: no "Show all" tap (2026-11-17 "Still I don't see full loading").
      assert.equal(await view.getByRole("button", { name: /Show all/ }).count(), 0);
      assert.match(await view.innerText(), new RegExp(`All ${real.byTicker.length} tickers on the board`));
      assert.match(await lines.first().innerText(), /AAPL[\s\S]*Most call OI[\s\S]*200 Call[\s\S]*ENTRY call/);
      await view.getByRole("searchbox", { name: "Filter this list" }).fill("nv");
      for (let i = 0; i < 30 && (await lines.count()) !== 1; i += 1) await page.waitForTimeout(100);
      assert.equal(await lines.count(), 1);
      assert.match(await lines.first().innerText(), /NVDA/);
      if (phone) assert.equal(await overflowsSideways(page), false);
      await view.getByRole("searchbox", { name: "Filter this list" }).fill("");
      const end = await scrollsToTheEnd(page, ".leap-oi-page", "[data-testid='leap-oi-ticker']");
      assert.equal(end.moved, true, "the page did not scroll with the wheel");
      assert.ok(end.lastBottom <= end.areaBottom + 1, `last card cut off: ${JSON.stringify(end)}`);
      assert.deepEqual(app.pageErrors, []);
      // Chart opens the chart page for that ticker.
      const chart = view.getByRole("button", { name: "Chart ›" }).first();
      await (phone ? chart.tap() : chart.click());
      await view.waitFor({ state: "detached", timeout: 15_000 });
      assert.deepEqual(app.pageErrors, []);
    });
  });
}

// 2026-11-17 "Add search ticker also": type any ticker, the page shows only that one (even off Mag7 and the
// watchlist), says plainly why when there is nothing, refuses junk without asking the server, and "Back to board" returns.
for (const [label, viewport] of WIDTHS) {
  const phone = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`LEAP OI Scanner search any ticker at ${label}`, async () => {
    const asked = [];
    const T = real.byTicker.some((t) => t.ticker === "NVDA") ? "NVDA" : real.byTicker[0].ticker;
    const only = {
      ...real,
      tickers: real.tickers.filter((t) => t.ticker === T), pending: [],
      rows: real.rows.filter((r) => r.ticker === T), entries: real.entries.filter((r) => r.ticker === T),
      byTicker: real.byTicker.filter((t) => t.ticker === T),
      biggestCall: real.rows.find((r) => r.ticker === T && r.side === "CALL") || null,
      biggestPut: real.rows.find((r) => r.ticker === T && r.side === "PUT") || null,
    };
    const kitt = { ...real, tickers: [{ ticker: "KITT", state: "error", error: "Schwab has no LEAP options for KITT." }],
      pending: [], rows: [], entries: [], byTicker: [], biggestCall: null, biggestPut: null, excluded: [] };
    const reads = {
      "GET /api/leap-oi-scanner": (read) => {
        const s = decodeURIComponent(read.search || "");
        asked.push(s);
        if (s.includes(`symbols=${T}&`)) return { json: only };
        if (s.includes("symbols=KITT&")) return { json: kitt };
        return { json: real };
      },
      "GET /api/watchlist": { json: { tickers: ["PLTR", "AMD"] } },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.locator(".leapoi-hero").first().waitFor({ timeout: 90_000 });
      const box = view.getByRole("searchbox", { name: "Search any ticker" });
      const go = view.getByRole("button", { name: "Search", exact: true });
      const tap = (el) => (phone ? el.tap() : el.click());
      const waitAsked = async (text) => {
        for (let i = 0; i < 80 && !asked.some((s) => s.includes(text)); i += 1) await page.waitForTimeout(100);
        assert.ok(asked.some((s) => s.includes(text)), asked.join("\n"));
      };
      // Lower case and Enter work; the request is that ticker alone.
      await box.fill(T.toLowerCase());
      await box.press("Enter");
      await waitAsked(`symbols=${T}&`);
      const cards = view.locator("[data-testid='leap-oi-ticker']");
      for (let i = 0; i < 50 && (await cards.count()) !== 1; i += 1) await page.waitForTimeout(100);
      assert.equal(await cards.count(), 1);
      assert.match(await cards.first().innerText(), new RegExp(T));
      assert.match(await view.locator(".leapoi-head p").innerText(), new RegExp(`· ${T} only`));
      assert.equal(await box.inputValue(), T);
      const boardChips = view.getByRole("group", { name: "Board" }).getByRole("button");
      for (const chip of await boardChips.all()) assert.equal(await chip.getAttribute("aria-pressed"), "false");
      // A ticker with no options says why, in words.
      await box.fill("KITT");
      await tap(go);
      await waitAsked("symbols=KITT&");
      await view.getByTestId("search-note").getByText("Schwab has no LEAP options for KITT.").waitFor({ timeout: 15_000 });
      // Junk is refused on screen and never sent.
      const before = asked.length;
      await box.fill("12ab");
      await tap(go);
      await view.getByRole("alert").getByText("Type a ticker, like NVDA.").waitFor({ timeout: 5_000 });
      await page.waitForTimeout(300);
      assert.ok(asked.slice(before).every((s) => !s.includes("12AB")), asked.join("\n"));
      if (phone) assert.equal(await overflowsSideways(page), false);
      // Back to board: the Mag7 board again, Mag7 pressed, the box empty.
      await tap(view.getByRole("button", { name: "✕ Back to board" }));
      await view.getByRole("button", { name: "Mag7", exact: true }).and(view.locator("[aria-pressed='true']")).waitFor({ timeout: 15_000 });
      assert.ok(asked.at(-1).includes("symbols=AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA&"), asked.at(-1));
      assert.equal(await box.inputValue(), "");
      assert.equal(await view.getByRole("button", { name: "✕ Back to board" }).count(), 0);
      assert.deepEqual(app.pageErrors, []);
      assert.deepEqual(app.writes, []);
    });
  });
}

// 2026-11-17 "All six": open interest change since the previous day, tap a contract for its facts card (Chart and
// Options page), and the last searches as one-tap buttons. Desktop and phone width.
for (const [label, viewport] of WIDTHS) {
  const phone = viewport === PHONE;
  const size = viewport ? { viewport } : {};

  e2eTest(`LEAP OI Scanner OI change, contract card and recent searches at ${label}`, async () => {
    const asked = [];
    const since = "2026-11-16T20:00:00-05:00";
    const withChange = (r, i) => ({ ...r, oiChange: i === 0 ? 1234 : i === 1 ? -560 : null, oiChangeSince: i < 2 ? since : null });
    const board = {
      ...real,
      rows: real.rows.map(withChange),
      biggestCall: real.biggestCall ? { ...real.biggestCall, oiChange: 1234, oiChangeSince: since } : null,
    };
    const reads = {
      "GET /api/leap-oi-scanner": (read) => { asked.push(decodeURIComponent(read.search || "")); return { json: board }; },
      "GET /api/watchlist": { json: { tickers: ["PLTR", "AMD"] } },
    };
    await withApp({ view: "LEAP OI Scanner", ...size, fakeReads: reads }, async (app) => {
      const { page } = app;
      const view = page.locator(VIEW);
      await view.locator(".leapoi-hero").first().waitFor({ timeout: 90_000 });
      const tap = (el) => (phone ? el.tap() : el.click());
      // OI change: green +, red −, and the note says what it is.
      const rowSel = phone ? "[data-testid='leap-oi-row-card']" : "[data-testid='leap-oi-row']";
      const rows = view.locator(rowSel);
      assert.match(await rows.nth(0).innerText(), /\+1,234/);
      assert.match(await rows.nth(1).innerText(), /−560/);
      assert.equal(await rows.nth(0).locator(".leapoi-chg.is-up").count(), 1);
      assert.equal(await rows.nth(1).locator(".leapoi-chg.is-down").count(), 1);
      assert.match(await view.locator(".leapoi-hero").first().innerText(), /\+1,234 since Nov 16/);
      assert.match(await view.getByTestId("oi-change-note").innerText(), /change since the previous day's read/);
      // Tap a contract: the facts card.
      const top = board.rows[0];
      await tap(rows.nth(0).locator(phone ? ".leapoi-card-grid" : "td").nth(phone ? 0 : 2));
      const sheet = page.getByTestId("contract-sheet");
      await sheet.waitFor({ timeout: 10_000 });
      const text = await sheet.innerText();
      assert.match(text, new RegExp(top.ticker));
      assert.match(text, /Break-even at expiry/);
      assert.match(text, /Stock move to break-even/);
      assert.match(text, /\+1,234/);
      assert.match(text, /Facts only, not a pick/);
      if (Number(top.days) > 31) assert.match(text, /up to 31 days out/);
      if (phone) {
        assert.equal(await overflowsSideways(page), false);
        const box = await sheet.boundingBox();
        assert.ok(box && box.x >= 0 && box.x + box.width <= PHONE.width + 1, "the card fits the phone width");
      }
      await tap(sheet.getByRole("button", { name: "Close" }));
      await sheet.waitFor({ state: "detached", timeout: 5_000 });
      // The Chart button on a row still opens the chart, not the card.
      assert.equal(await page.getByTestId("contract-sheet").count(), 0);
      // Recent searches: search, go back, the ticker is one tap away.
      const box = view.getByRole("searchbox", { name: "Search any ticker" });
      await box.fill("pltr");
      await box.press("Enter");
      await tap(view.getByRole("button", { name: "✕ Back to board" }));
      const recent = view.getByRole("group", { name: "Recent searches" });
      await recent.waitFor({ timeout: 5_000 });
      assert.deepEqual(await recent.getByRole("button").allInnerTexts(), ["PLTR"]);
      const before = asked.length;
      await tap(recent.getByRole("button", { name: "PLTR" }));
      for (let i = 0; i < 50 && !asked.slice(before).some((s) => s.includes("symbols=PLTR&")); i += 1) await page.waitForTimeout(100);
      assert.ok(asked.slice(before).some((s) => s.includes("symbols=PLTR&")), asked.join("\n"));
      // Options page from the card leaves for the Options page.
      await tap(view.getByRole("button", { name: "✕ Back to board" }));
      await view.locator(rowSel).first().waitFor({ timeout: 15_000 });
      await tap(view.locator(rowSel).first().locator(phone ? ".leapoi-card-grid" : "td").nth(phone ? 0 : 2));
      await page.getByTestId("contract-sheet").getByRole("button", { name: "Options page ›" }).click();
      await view.waitFor({ state: "detached", timeout: 15_000 });
      assert.deepEqual(app.pageErrors, []);
      // The Options page asks for its own news refresh when it opens on a ticker; nothing else may be sent.
      assert.deepEqual(app.writes.filter((w) => w.path !== "/api/news-feed/refresh"), []);
    });
  });
}
