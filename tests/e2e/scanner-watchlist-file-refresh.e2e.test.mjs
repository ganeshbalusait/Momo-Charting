// A My Watchlist deletion reaches the MomX scanner without a restart
// (2026-11-10, KVUE). The worker now re-reads watchlist.txt when it changes,
// so its /lists count drops on the next poll; the panel must show the new
// count on the next tick without a reload, at desktop and phone width.
// The worker side is covered by test_cov_scanner-board_watchlist_file_refresh_api.py;
// here /lists is faked (no live worker, no live files).

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { boardReads, boardReady, listsFixture } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";
const STORAGE = { "momx.scanner.activeList": "Watchlist", "momx-auto-cards-on": "0" };

function shrinkingWatchlist() {
  let edited = false;
  const lists = () => {
    const base = listsFixture({ active: "Watchlist" });
    return {
      ...base,
      lists: base.lists.map((entry) => (entry.name === "Watchlist" ? { ...entry, count: edited ? 370 : 371 } : entry)),
    };
  };
  return { lists, edit: () => { edited = true; } };
}

for (const viewport of [DESKTOP, PHONE]) {
  e2eTest(`the Watchlist count drops on the next poll after a ticker is removed (${viewport.width}px)`, async () => {
    const watch = shrinkingWatchlist();
    await withApp({ view: VIEW, viewport, storage: STORAGE, fakeReads: boardReads({ lists: watch.lists }) }, async (app) => {
      await boardReady(app.page);
      const count = app.page.locator(".momx-listbtn .momx-tab-count");
      await app.page.waitForFunction(() => document.querySelector(".momx-listbtn .momx-tab-count")?.textContent?.trim() === "371", null, { timeout: 15_000 });
      watch.edit();   // watchlist.txt rewritten without KVUE; the worker re-read it
      await app.page.waitForFunction(() => document.querySelector(".momx-listbtn .momx-tab-count")?.textContent?.trim() === "370", null, { timeout: 30_000 });
      assert.equal((await count.innerText()).trim(), "370");
      assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 120_000 });
}
