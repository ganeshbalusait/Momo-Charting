// Ticker story card (2026-11-20): search a ticker on LEAP OI Scanner OR AGX Flow and the same "INTC in simple words"
// card answers what people are betting and whether it looked bought or sold, so the trader does not hop pages
// ("If I see leap oi scanner like INTC I don't know anything like buyers or seller ... I feel little confusion").
// The reply has the server's shape (tests/test_momx_ticker_story_api.py proves the server builds it from both pages).
// Desktop and phone width.

import assert from "node:assert/strict";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const STORY = {
  ticker: "INTC", ready: true, pending: false, spot: 54.6,
  verdict: "Today more money went into Puts on INTC: 91% of it.",
  sections: [
    { title: "Price", lines: [{ mark: "info", text: "One share of INTC costs about 54.60 (at the last read)." }] },
    { title: "Today's bets", lines: [
      { mark: "info", text: "Today about $22 million went into Calls (bets the price goes up) and $209 million into Puts (bets it goes down)." },
      { mark: "yes", text: "Puts got 91% of the money." },
      { mark: "info", text: "Big bet: 95 Put ending Dec 18, 2026, $30 million; the last trade can't tell if a buyer or a seller led." }] },
    { title: "Long bets (3 to 12 months)", lines: [
      { mark: "info", text: "The long Call most people hold: 50 Call ending Feb 19, 2027, 73,640 contracts." }] },
    { title: "New since yesterday", lines: [
      { mark: "yes", text: "43,513 new contracts opened on the 70 Put ending Jun 17, 2027." },
      { mark: "no", text: "2,500 contracts closed on the 80 Put ending Jun 17, 2027." }] },
  ],
  meaning: "A Call is a bet that the price goes UP; a Put is a bet that it goes DOWN. Our test found big open interest guessed the direction no better than a coin flip.",
  notAdvice: "This explains what the numbers show. It is not advice and does not tell you what to do with your money. Practice with paper trading first.",
};
const EMPTY_LEAP = { tickers: [{ ticker: "INTC", state: "ready", spot: 54.6, callOi: 0, putOi: 0, rows: 0 }], pending: [], rows: [],
  entries: [], byTicker: [], excluded: [], side: "both", note: "Facts only, not a pick. coin flip" };
const EMPTY_FLOW = { tickers: [{ ticker: "INTC", spot: 54.6, callPremium: 0, putPremium: 0, callVolume: 0, putVolume: 0,
  unusualCount: 0, cached: true, pending: false, error: null, fetchedAt: "2026-11-19T16:04:00-05:00" }], rows: [], note: "", excluded: [] };

for (const [label, viewport] of [["desktop", undefined], ["phone width", PHONE]]) {
  const phone = Boolean(viewport);
  for (const [view, root] of [["LEAP OI Scanner", "[data-testid='leap-oi-view']"], ["AGX Flow", "[data-testid='agx-flow-view']"]]) {
    e2eTest(`${view} at ${label}: searching INTC shows the one-ticker card in simple words`, async () => {
      const asked = [];
      const reads = {
        "GET /api/ticker-story": (read) => { asked.push(new URLSearchParams(read.search).get("symbol")); return { json: STORY }; },
        "GET /api/leap-oi-scanner": { json: EMPTY_LEAP },
        "GET /api/agx-flow": { json: EMPTY_FLOW },
        "GET /api/watchlist": { json: { tickers: [] } },
        "GET /api/watchlist-quotes": { json: { rows: [] } },
        "GET /api/agx-flow/alert": { json: { on: false, signedIn: true, hasPhone: false } },
      };
      await withApp({ view, ...(viewport ? { viewport } : {}), fakeReads: reads }, async (app) => {
        const { page } = app;
        const pageRoot = page.locator(root);
        await pageRoot.waitFor({ timeout: 90_000 });
        assert.equal(await pageRoot.getByTestId("ticker-story").count(), 0, "no card before a search");
        const box = pageRoot.getByRole("searchbox", { name: "Search any ticker" });
        await box.fill("intc");
        const go = pageRoot.getByRole("button", { name: "Search", exact: true });
        await (phone ? go.tap() : go.click());
        const card = pageRoot.getByTestId("ticker-story");
        await card.getByTestId("ticker-story-verdict").waitFor({ timeout: 20_000 });
        assert.deepEqual(asked.slice(0, 1), ["INTC"]);
        const text = await card.innerText();
        assert.match(text, /INTC in simple words/i);
        assert.match(text, /Today more money went into Puts on INTC: 91% of it\./);
        assert.match(text, /Today's bets[\s\S]*\$22 million went into Calls \(bets the price goes up\)/);
        assert.match(text, /✓ Puts got 91% of the money\./);
        assert.match(text, /Long bets \(3 to 12 months\)[\s\S]*73,640 contracts/);
        assert.match(text, /New since yesterday[\s\S]*✓ 43,513 new contracts[\s\S]*✗ 2,500 contracts closed/);
        assert.match(text, /What this means: A Call is a bet/);
        assert.match(text, /not advice[\s\S]*paper trading/);
        const cardBox = await card.boundingBox();
        assert.ok(cardBox && cardBox.x >= 0 && cardBox.x + cardBox.width <= page.viewportSize().width + 1, "the card is cut off");
        if (phone) assert.equal(await overflowsSideways(page), false);
        // Back to the board: the card goes away.
        const back = pageRoot.getByRole("button", { name: /Back to board/ });
        await (phone ? back.tap() : back.click());
        for (let i = 0; i < 30 && (await pageRoot.getByTestId("ticker-story").count()) > 0; i += 1) await page.waitForTimeout(100);
        assert.equal(await pageRoot.getByTestId("ticker-story").count(), 0);
        assert.deepEqual(app.pageErrors, []);
      });
    }, { timeout: 240_000 });
  }
}
