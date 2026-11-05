// The 21 / 50 EMA touch lists use his TOS scan script verbatim (2026-11-05):
//     def ema21 = ExpAverage(close, 21);
//     plot scan = low <= ema21 and high >= ema21;
// End to end against the LIVE scanner (reads only): the count the trader sees
// in the WATCHLISTS picker for each EMA list equals the number of Watchlist
// tickers whose current bar straddles the EMA in the scanner's own cache, and
// every cached reading obeys the TOS rule (pass <=> low <= EMA <= high).

import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { BOARD_STORAGE } from "./scanner-board-fixtures.mjs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const PICKER = ".momx-picker[role='dialog'][aria-label='Watchlists']";

function liveTruth() {
  const cache = JSON.parse(readFileSync(path.join(ROOT, "artifacts", "momx_ema_touch.json"), "utf8"));
  // The Watchlist is a built-in seed, not a saved list: ask the scanner's own
  // module for it (read-only).
  const watch = JSON.parse(execFileSync(path.join(ROOT, ".venv", "Scripts", "python.exe"), [
    "-c", "import json; from momx import board; print(json.dumps(board.load_universe('Watchlist')))",
  ], { cwd: ROOT, encoding: "utf8" }));
  let tfs = {};
  try { tfs = JSON.parse(readFileSync(path.join(ROOT, "artifacts", "momx_ema_touch_tf.json"), "utf8")); } catch { /* default */ }
  const out = {};
  for (const n of [21, 50]) {
    const tf = tfs[String(n)] || "Monthly";
    const key = `${n}:${tf}`;
    let members = 0;
    for (const [sym, row] of Object.entries(cache.symbols || {})) {
      const s = row?.[key];
      if (!s) continue;
      const tos = s.low != null && s.high != null && s.low <= s.level && s.high >= s.level;
      assert.equal(Boolean(s.pass), tos, `${sym} ${key}: pass=${s.pass} but low ${s.low} / EMA ${s.level} / high ${s.high}`);
      if (s.pass && watch.includes(sym)) members += 1;
    }
    out[`${n} EMA Touch ${tf}`] = members;
  }
  return out;
}

async function pickerCounts(page) {
  await page.locator(".momx-listbtn[aria-haspopup='true']").waitFor({ timeout: 60_000 });
  await page.locator(".momx-listbtn[aria-haspopup='true']").click();
  const picker = page.locator(PICKER);
  await picker.waitFor({ timeout: 15_000 });
  const counts = {};
  for (const li of await picker.locator(".momx-picker-pick").all()) {
    const name = (await li.locator(".momx-picker-name").textContent()).trim();
    const count = li.locator(".momx-tab-count");
    counts[name] = (await count.count()) ? Number((await count.textContent()).trim()) : null;
  }
  return counts;
}

for (const viewport of [undefined, PHONE]) {
  e2eTest(`EMA touch list counts are the TOS scan (${viewport ? "phone" : "desktop"})`, async () => {
    await withApp({ view: "MomX Scanner", storage: BOARD_STORAGE, viewport }, async (app) => {
      const shown = await pickerCounts(app.page);
      const truth = liveTruth();
      for (const [name, members] of Object.entries(truth)) {
        assert.ok(name in shown, `${name} missing from the picker: ${JSON.stringify(shown)}`);
        assert.equal(shown[name], members, `${name}: picker ${shown[name]} vs TOS rule ${members}`);
      }
      assert.equal(await overflowsSideways(app.page), false);
      assert.deepEqual(app.pageErrors, []);
    });
  });
}
