// MomX Scanner render cost, end to end (2026-11-09, "frozen black"): the app
// shell re-renders about three times a second (clocks, alerts, the Schwab
// countdown). Before the fix every one of those re-ran the whole scanner -
// filters, Setup tags and Confirm levels for every row - and the page sat
// frozen 1-2 s at a time. The panel is memo'd now, so it renders only for its
// own state (board polls, live quotes that actually changed).
//
// React's devtools hook (dev server only) counts commits and panel renders.

import assert from "node:assert/strict";

import { PHONE, e2eTest, withApp } from "./harness.mjs";
import { BOARD_STORAGE, boardReads, boardReady } from "./scanner-board-fixtures.mjs";

const VIEW = "MomX Scanner";

function countRenders() {
  const stats = { commits: 0, panel: 0, on: false };
  window.__renderStats = stats;
  window.__REACT_DEVTOOLS_GLOBAL_HOOK__ = {
    supportsFiber: true, renderers: new Map(), inject(renderer) { this.renderers.set(1, renderer); return 1; },
    onScheduleFiberRoot() {}, onCommitFiberUnmount() {}, onPostCommitFiberRoot() {}, checkDCE() {},
    onCommitFiberRoot(id, root) {
      if (!stats.on) return;
      stats.commits += 1;
      const walk = (fiber) => {
        for (let f = fiber; f; f = f.sibling) {
          if (f.type && f.type.name === "MomxScannerPanel" && f.alternate && (f.flags & 1)) stats.panel += 1;
          walk(f.child);
        }
      };
      walk(root.current.child);
    },
  };
}

for (const [label, viewport] of [["desktop", undefined], ["phone", PHONE]]) {
  e2eTest(`app-shell ticks no longer re-render the whole scanner (${label})`, async () => {
    await withApp({ view: VIEW, storage: BOARD_STORAGE, fakeReads: boardReads(), ...(viewport ? { viewport } : {}) }, async (app) => {
      await app.page.addInitScript(countRenders);
      await app.page.reload({ waitUntil: "domcontentloaded" });
      await boardReady(app.page);
      await app.page.evaluate(() => { window.__renderStats.on = true; });
      await app.page.waitForTimeout(15_000);
      const stats = await app.page.evaluate(() => window.__renderStats);
      assert.ok(stats.commits >= 5, `the app kept rendering (${stats.commits} commits)`);
      assert.ok(stats.panel <= stats.commits / 2,
        `the scanner rendered ${stats.panel} times in ${stats.commits} app commits - it should skip the shell's ticks`);
      assert.ok(await app.page.locator("table.momx-table tbody tr").count() > 0, "the board is still on screen");
      assert.deepEqual(app.pageErrors, []);
    });
  }, { timeout: 240_000 });
}
