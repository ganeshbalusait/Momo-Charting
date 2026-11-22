import assert from "node:assert/strict";
import test from "node:test";

import {
  MAG7, awayText, barWidth, boardTickers, contractLabel, deltaText, expiryLabel, formatOi, priceText,
  progressText, readAtText, scannerUrl, cleanTicker, searchNote,
  addRecent, contractFacts, moveText, oiChangeText, oiChangeTone, priceAgeText, sinceText, RECENT_MAX,
} from "./leapOi.js";

test("search box: typed text becomes a ticker the server accepts, anything else is refused", () => {
  assert.equal(cleanTicker("nvda"), "NVDA");
  assert.equal(cleanTicker("  $pltr "), "PLTR");
  assert.equal(cleanTicker("brk.b"), "BRK.B");
  for (const bad of ["", "   ", null, undefined, "TOOLONGX", "12AB", "NV DA", "AAPL,MSFT", "<x>", "$"]) {
    assert.equal(cleanTicker(bad), "", String(bad));
  }
});

test("search note: says why a searched ticker shows nothing, on both pages", () => {
  const leap = (line, extra = {}) => ({ tickers: [line], excluded: [], minPrice: 10, ...extra });
  assert.equal(searchNote(null, "NVDA"), "");
  assert.equal(searchNote(leap({ ticker: "NVDA", state: "ready", rows: 12 }), ""), "");
  assert.equal(searchNote(leap({ ticker: "NVDA", state: "ready", rows: 12 }), "NVDA", "LEAP OI Scanner"), "");
  assert.equal(searchNote(leap({ ticker: "NVDA", state: "pending" }), "NVDA"), "");          // the progress line covers it
  assert.equal(searchNote(leap({ ticker: "KITT", state: "error", error: "Schwab has no LEAPs for KITT." }), "KITT"),
    "Schwab has no LEAPs for KITT.");
  assert.match(searchNote(leap({ ticker: "NVDA", state: "ready", rows: 0 }), "NVDA"), /No NVDA strikes match these filters/);
  assert.equal(searchNote({ tickers: [], excluded: [{ ticker: "SPY", reason: "ETF" }] }, "SPY", "LEAP OI Scanner"),
    "SPY is an ETF. LEAP OI Scanner shows stocks only.");
  assert.equal(searchNote({ tickers: [], excluded: [{ ticker: "NIO", reason: "under $10" }], minPrice: 10 }, "NIO", "AGX Flow"),
    "NIO is under $10, so AGX Flow leaves it off.");
  // AGX Flow ticker lines have no "state": an error with nothing read shows; an error after a good read does not.
  assert.equal(searchNote({ tickers: [{ ticker: "LIDR", error: "Schwab has no options 0 to 45 days out for LIDR.", fetchedAt: null }] }, "LIDR"),
    "Schwab has no options 0 to 45 days out for LIDR.");
  assert.equal(searchNote({ tickers: [{ ticker: "TSLA", error: "hiccup", fetchedAt: "2026-11-17T15:00:00-05:00" }] }, "TSLA"), "");
});

test("board tickers: Mag7 and Watchlist are separate boards, no repeats, junk dropped, capped at 400", () => {
  assert.deepEqual(boardTickers("mag7", ["PLTR"]), MAG7);
  // Separate boards (2026-11-17): Watchlist never carries the Mag7 names.
  assert.deepEqual(boardTickers("watchlist", ["pltr", "NVDA", "", "BAD TICKER", "amd", "tsla"]), ["PLTR", "AMD"]);
  const many = Array.from({ length: 40 }, (_, i) => `T${String.fromCharCode(65 + (i % 26))}${i >= 26 ? "X" : ""}`);
  assert.equal(boardTickers("watchlist", many).length, 40);
  const lots = Array.from({ length: 500 }, (_, i) => `${String.fromCharCode(65 + (i % 26))}${String.fromCharCode(65 + Math.floor(i / 26) % 26)}${String.fromCharCode(65 + Math.floor(i / 676))}`);
  assert.equal(boardTickers("watchlist", lots).length, 400);
  assert.deepEqual(boardTickers("watchlist", null), []);
});

test("scanner url carries range, delta, side and the entry-only switch", () => {
  const url = scannerUrl({ tickers: ["NVDA", "AAPL"], range: "leap", delta: "min", side: "both" });
  assert.equal(url, "/api/leap-oi-scanner?symbols=NVDA%2CAAPL&from=3&to=12&side=both&min_delta=0.10");
  const entry = scannerUrl({ tickers: ["NVDA"], range: "near", delta: "entry", side: "calls" });
  assert.match(entry, /from=1&to=3&side=calls&min_delta=0\.10&entry_only=1$/);
  assert.match(scannerUrl({ tickers: ["NVDA"], range: "x", delta: "mid", side: "sideways" }), /from=3&to=12&side=both&min_delta=0\.20$/);
});

test("contract, delta, expiry and number text", () => {
  assert.equal(contractLabel({ strike: 172.5, side: "CALL" }), "172.5 Call");
  assert.equal(contractLabel({ strike: 140, side: "PUT" }), "140 Put");
  assert.equal(deltaText(0.198), "0.198");
  assert.equal(deltaText(-0.196), "-0.196");
  assert.equal(deltaText(0.2), "0.20");
  assert.equal(deltaText(0.1), "0.10");
  assert.equal(deltaText(null), "—");
  assert.equal(expiryLabel("2027-01-15"), "Jan 15 '27");
  assert.equal(expiryLabel(""), "");
  assert.equal(formatOi(123456), "123,456");
  assert.equal(priceText(null), "—");
  assert.equal(priceText(0), "0.00");
  assert.equal(awayText(12.34), "+12.3%");
  assert.equal(awayText(-5), "-5.0%");
  assert.equal(awayText(null), "");
});

test("bar width is a share of the top row, never vanishing", () => {
  assert.equal(barWidth(50, 100), 50);
  assert.equal(barWidth(1, 100000), 4);
  assert.equal(barWidth(10, 0), 4);
  assert.equal(barWidth(200, 100), 100);
});

test("progress text only while tickers are still being read", () => {
  assert.equal(progressText({ tickers: [{ state: "ready" }, { state: "pending" }, { state: "error" }] }), "Reading Schwab… 2 of 3 tickers ready");
  assert.equal(progressText({ tickers: [{ state: "ready" }] }), "");
  assert.equal(progressText(null), "");
});

test("read time shows the oldest ready read in ET", () => {
  const text = readAtText({ tickers: [
    { state: "ready", readAt: "2026-11-17T08:31:00-05:00" },
    { state: "ready", readAt: "2026-11-17T08:45:00-05:00" },
    { state: "pending" },
  ] });
  assert.equal(text, "Read from Schwab 8:31 AM ET");
  assert.equal(readAtText({ tickers: [] }), "");
});

test("by-ticker search: starts-with match, preview of 40 unless show all", async () => {
  const { filterByTicker } = await import("./leapOi.js");
  const list = Array.from({ length: 100 }, (_, i) => ({ ticker: `T${String(i).padStart(3, "0")}` })).concat([{ ticker: "PLTR" }, { ticker: "PL" }]);
  assert.deepEqual(filterByTicker(list, "pl").shown.map((l) => l.ticker), ["PLTR", "PL"]);
  assert.equal(filterByTicker(list, "").shown.length, 102);                    // every ticker, no 40-ticker preview
  assert.equal(filterByTicker(list, "").total, 102);
  assert.deepEqual(filterByTicker(null, "x"), { shown: [], total: 0 });
});

test("progress on a whole-watchlist read says roughly how long is left", () => {
  const tickers = [...Array(20).fill({ state: "ready" }), ...Array(340).fill({ state: "pending" })];
  assert.equal(progressText({ tickers }), "Reading Schwab… 20 of 360 tickers ready · about 20 min left, once a day");
});

test("left-out line counts ETFs and stocks under $10 (2026-11-17 'No need spy, qqq and small stocks and etf')", async () => {
  const { leftOutText } = await import("./leapOi.js");
  assert.equal(leftOutText([{ ticker: "SPY", reason: "ETF" }, { ticker: "QQQ", reason: "ETF" }, { ticker: "NIO", reason: "under $10" }], 10),
    "Left out: 2 ETFs · 1 stock under $10");
  assert.equal(leftOutText([{ ticker: "SPY", reason: "ETF" }]), "Left out: 1 ETF");
  assert.equal(leftOutText([]), "");
  assert.equal(leftOutText(undefined), "");
});

test("who leads, calls or puts: 55% or more leads, closer is about even (2026-11-17 'who is dominating call or put?')", async () => {
  const { dominance, boardTotals } = await import("./leapOi.js");
  assert.deepEqual(dominance(620, 380), { callPct: 62, putPct: 38, leader: "calls", text: "Calls lead · 62%" });
  assert.deepEqual(dominance(300, 700), { callPct: 30, putPct: 70, leader: "puts", text: "Puts lead · 70%" });
  assert.equal(dominance(52, 48).text, "About even · calls 52% / puts 48%");
  assert.equal(dominance(55, 45).leader, "calls");
  assert.equal(dominance(0, 0).leader, "none");
  assert.equal(dominance(null, "x").text, "No calls or puts yet");
  assert.equal(dominance(10, 0).text, "Calls lead · 100%");
  assert.deepEqual(boardTotals([{ callOi: 10, putOi: 4 }, { callOi: 5 }, null]), { calls: 15, puts: 4 });
  assert.deepEqual(boardTotals(undefined), { calls: 0, puts: 0 });
});

test("mid price, cost of one contract and spread (2026-11-17 'give me mid prize also')", async () => {
  const { midPrice, midText, costText, spreadText } = await import("./leapOi.js");
  assert.equal(midPrice(2.7, 2.85), 2.78);                  // 2.775 to the cent
  assert.equal(midPrice(0, 0.06), 0.03);
  assert.equal(midPrice(1, 0), null);                       // no ask: no real market
  assert.equal(midPrice(null, 2), null);
  assert.equal(midPrice("x", 2), null);
  assert.equal(midText({ bid: 2.4, ask: 2.5 }), "2.45");
  assert.equal(midText({ bid: null, ask: 2.5 }), "—");
  assert.equal(costText({ bid: 2.4, ask: 2.5 }), "$245");
  assert.equal(costText({ bid: 12.3, ask: 12.5 }), "$1,240");
  assert.equal(costText({}), "—");
  assert.equal(spreadText({ bid: 2.47, ask: 2.75 }), "10.7%");
  assert.equal(spreadText({ bid: null, ask: 1 }), "—");
});

test("by-ticker order: A-Z, most call-heavy, most put-heavy; nothing open goes last", async () => {
  const { sortByTicker } = await import("./leapOi.js");
  const list = [
    { ticker: "MU", callOi: 63, putOi: 37 },
    { ticker: "AAA", callOi: 0, putOi: 0 },
    { ticker: "TSM", callOi: 28, putOi: 72 },
    { ticker: "CVX", callOi: 100, putOi: 0 },
  ];
  assert.deepEqual(sortByTicker(list, "az").map((l) => l.ticker), ["AAA", "CVX", "MU", "TSM"]);
  assert.deepEqual(sortByTicker(list, "calls").map((l) => l.ticker), ["CVX", "MU", "TSM", "AAA"]);
  assert.deepEqual(sortByTicker(list, "puts").map((l) => l.ticker), ["TSM", "MU", "CVX", "AAA"]);
  assert.deepEqual(sortByTicker(null, "calls"), []);
  assert.equal(list[0].ticker, "MU");                        // the board's own list is not reordered
});

test("open interest change: signed, with thousands, nothing when there is no earlier day (2026-11-17 'All six')", () => {
  assert.equal(oiChangeText(12400), "+12,400");
  assert.equal(oiChangeText(-3100), "−3,100");
  assert.equal(oiChangeText(0), "no change");
  assert.equal(oiChangeText(null), "");
  assert.equal(oiChangeText(undefined), "");
  assert.equal(oiChangeTone(5), "is-up");
  assert.equal(oiChangeTone(-5), "is-down");
  assert.equal(oiChangeTone(0), "is-flat");
  assert.equal(oiChangeTone(null), "is-flat");
  assert.equal(sinceText("2026-11-16T20:00:00-05:00"), "since Nov 16");
  assert.equal(sinceText(null), "since yesterday");
});

test("contract facts: break-even and the stock move to reach it, call and put", () => {
  const call = contractFacts({ side: "CALL", strike: 120, bid: 1.06, ask: 1.21, spot: 100 });
  assert.deepEqual(call, { mid: 1.14, breakeven: 121.14, move: 21.1, cost: 114 });
  const put = contractFacts({ side: "PUT", strike: 85, bid: 20.25, ask: 21.05, spot: 100 });
  assert.equal(put.breakeven, 64.35);
  assert.equal(put.move, -35.7);
  assert.deepEqual(contractFacts({ side: "CALL", strike: 100, bid: null, ask: 1 }), { mid: null, breakeven: null, move: null, cost: null });
  assert.equal(contractFacts({ side: "CALL", strike: 100, bid: 1, ask: 1.2 }).move, null);   // no stock price
  assert.equal(moveText(21.1), "+21.1%");
  assert.equal(moveText(-35.7), "−35.7%");
  assert.equal(moveText(null), "—");
});

test("recent searches: newest first, no repeats, junk dropped, at most 5", () => {
  let list = [];
  for (const t of ["nvda", "PLTR", "hood", "NVDA", "12ab", "amd", "tsla", "coin"]) list = addRecent(list, t);
  assert.deepEqual(list, ["COIN", "TSLA", "AMD", "NVDA", "HOOD"]);
  assert.equal(list.length, RECENT_MAX);
  assert.deepEqual(addRecent(["nvda", "NVDA", "bad ticker"], ""), ["NVDA"]);
  assert.deepEqual(addRecent(null, "pltr"), ["PLTR"]);
});

// 2026-11-22 "can we add sector also which one have more leaps option? Sector can expand also and sector have
// top [OI] with ticker?": sectors ranked by total LEAP open interest, each with its tickers ranked the same way.
test("by sector: sectors ranked by open interest, tickers inside ranked, each with its biggest contract", async () => {
  const { sectorGroups } = await import("./leapOi.js");
  const line = (ticker, sector, callOi, putOi, callStrike, putStrike) => ({
    ticker, sector, callOi, putOi,
    call: { ticker, side: "CALL", strike: callStrike, oi: callOi / 2 },
    put: { ticker, side: "PUT", strike: putStrike, oi: putOi / 2 },
  });
  const groups = sectorGroups([
    line("PLTR", "Software", 300, 100, 200, 150),
    line("NVDA", "Semis", 5000, 2000, 200, 140),
    line("AMD", "Semis", 900, 1200, 250, 150),
    line("SOFI", "Fintech", 50, 10, 30, 20),
    { ticker: "ZZZQ", callOi: 5, putOi: 0, call: null, put: null },
  ]);
  assert.deepEqual(groups.map((g) => g.sector), ["Semis", "Software", "Fintech", "Other"]);
  const semis = groups[0];
  assert.equal(semis.oi, 9100);
  assert.equal(semis.callOi, 5900);
  assert.equal(semis.putOi, 3200);
  assert.deepEqual(semis.tickers.map((t) => t.ticker), ["NVDA", "AMD"]);
  assert.equal(semis.tickers[0].top.side, "CALL", "NVDA's biggest contract is its call");
  assert.equal(semis.tickers[1].top.side, "PUT", "AMD's biggest contract is its put");
  assert.equal(groups[3].tickers[0].top, null, "a ticker with no contract still shows");
  assert.deepEqual(sectorGroups(null), []);
});

// 2026-11-22 "Recent I don't see the clear option": Clear empties the list and
// it stays empty after the page is opened again.
test("recent searches: clearing empties the saved list for good", async () => {
  const { clearRecent, readRecent, saveRecent } = await import("./leapOi.js");
  const store = new Map();
  globalThis.window = { localStorage: { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, String(v)) } };
  try {
    saveRecent(["SMCI", "NVDA"]);
    assert.deepEqual(readRecent(), ["SMCI", "NVDA"]);
    assert.deepEqual(clearRecent(), []);
    assert.deepEqual(readRecent(), [], "still empty when read back");
  } finally {
    delete globalThis.window;
  }
});


// 2026-11-20: the NVDA card at 5:39 PM ET showed Mid 1.95 from a 12:53 PM read. A read older than 15 minutes says its time.
test("priceAgeText names the read time once the prices are more than 15 minutes old", () => {
  const read = "2026-11-20T12:53:00-05:00";
  const at = (iso) => new Date(iso).getTime();
  assert.equal(priceAgeText({ readAt: read }, at("2026-11-20T13:05:00-05:00")), "");
  assert.equal(priceAgeText({ readAt: read }, at("2026-11-20T17:39:00-05:00")), "Prices from 12:53 PM ET");
  assert.equal(priceAgeText({ readAt: "2026-11-20T11:53:00-06:00" }, at("2026-11-20T17:39:00-05:00")), "Prices from 12:53 PM ET");
  assert.equal(priceAgeText({ readAt: "2026-11-19T15:58:00-05:00" }, at("2026-11-20T09:40:00-05:00")), "Prices from Thu 3:58 PM ET");
  assert.equal(priceAgeText({}, at("2026-11-20T17:39:00-05:00")), "");
  assert.equal(priceAgeText({ readAt: "not a time" }), "");
});
