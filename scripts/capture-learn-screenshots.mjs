// Re-capture every Learn page screenshot: `npm run learn:shots` from frontend/.
//
// This script existing is what makes screenshots defensible. The objection to
// screenshots is that they go stale silently; the answer is that refreshing
// them is one command rather than a manual chore nobody does.
//
// It NEVER touches the trader's browser state - Playwright runs an isolated
// context and seeds its own localStorage.

import { createRequire } from "node:module";
import { mkdirSync, statSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import path from "node:path";

// This file lives in scripts/, a sibling of frontend/ - a plain top-level
// `import "playwright"` cannot see frontend/node_modules because Node's ESM
// resolver only walks up ancestor directories of THIS file, and frontend/ is
// not an ancestor of scripts/ (confirmed 2026-08-29: it throws
// ERR_MODULE_NOT_FOUND under `npm run learn:shots` from frontend/). Resolve
// the package explicitly against frontend/'s own node_modules instead, and
// take its "import" condition (index.mjs) rather than the CJS entry - the
// CJS entry's named exports (`chromium`) do not survive dynamic-import
// interop here (also confirmed), so this must not be simplified back to a
// bare specifier or a `require()`.
const FRONTEND_DIR = fileURLToPath(new URL("../frontend/", import.meta.url));
const requireFromFrontend = createRequire(pathToFileURL(path.join(FRONTEND_DIR, "package.json")).href);

// A missing/uninstalled playwright throws Node's raw internal loader stack
// (Module._resolveFilename, node:internal/modules/cjs/loader, ...) which
// tells the person running this months from now nothing actionable. Catch
// only module-resolution/import failures here and give them the one command
// that fixes it; anything else (a genuinely broken playwright install, etc.)
// should still surface as a real error rather than being swallowed.
let chromium;
try {
  const playwrightCjsEntry = requireFromFrontend.resolve("playwright");
  const playwrightEsmEntry = path.join(path.dirname(playwrightCjsEntry), "index.mjs");
  ({ chromium } = await import(pathToFileURL(playwrightEsmEntry).href));
} catch (error) {
  if (error?.code === "MODULE_NOT_FOUND" || error?.code === "ERR_MODULE_NOT_FOUND") {
    console.error("playwright is not installed. Run `corepack pnpm install` from frontend/, then re-run `pnpm run learn:shots`.");
    process.exit(1);
  }
  throw error;
}

const BASE = process.env.LEARN_SHOTS_URL || "http://127.0.0.1:5173";
const OUT = fileURLToPath(new URL("../frontend/public/learn/", import.meta.url));
// Measured, not guessed. The chart-plus-chain board: 594KB at DPR 2, 411KB at
// DPR 1.5, 208KB at DPR 1 - and DPR 1 was checked by eye and reads cleanly,
// because the page caps at 980px wide so a 1357px capture is already being
// downscaled. JPEG is rejected: it comes out LARGER here and softens the axis
// text. Raise `scale` on one shot if it looks soft; do not move the default.
const MAX_BYTES = 250 * 1024;
const SCALE = 1;

// Seeding the view is fine. Seeding the LAYOUT is not: oiFinderChartWorkspace
// takes precedence when present and an initialLayoutId prop overrides both, so
// a seeded context still renders 6-across (verified 2026-08-29). The layout is
// switched by driving the UI instead - see selectSingleChart below.
const CLEAN_STATE = {
  agxLearnSeen: "1",
};

// Hide anything identifying before a pixel is captured.
const PRIVACY_CSS = `
  .signed-in-user, .avatar, .account-value, .portfolio-value { visibility: hidden !important; }
`;

// Switch to the Single layout by clicking, exactly as a user would. The app
// opens 6-across, which is illegible as a teaching image - the trader's
// instruction is one chart and the option chain, never the parallel view.
async function selectSingleChart(page) {
  await page.locator('summary[aria-label="Choose chart layout"]').first().click();
  await page.waitForTimeout(600);
  await page.locator('.oi-chart-layout-menu button[aria-label="Single"]').first().click();
  await page.waitForTimeout(9000);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1200);
}

// Selectors: the two chart ones are CONFIRMED rendering (2026-08-29). The rest
// are read from source and unverified - if one is wrong the script fails loudly,
// which is the intended outcome. Never loosen a selector to "body" to make it
// pass; find the real class in App.jsx.
const SHOTS = [
  { id: "chart-workstation", view: "Charts & OI", selector: ".charts-oi-page-full", settle: 10000, prepare: selectSingleChart },
  { id: "chart-oi-walls", view: "Charts & OI", selector: ".charts-oi-full-chart", settle: 10000, prepare: selectSingleChart },
  // The chain column is `align-self: stretch`, so it is as tall as the chart
  // beside it (844px) no matter how few strike rows it holds - the first
  // capture was 29% empty black below the last put row, and the Learn column
  // upscales it 2.5x, so that emptiness cost two and a half screens of
  // scrolling (measured 2026-08-29). `clipTo` trims the frame to the bottom of
  // the board's own table rather than the stretched container. It is measured
  // from the live DOM, not a hardcoded pixel height, so a longer board still
  // photographs in full.
  { id: "high-oi-board", view: "Charts & OI", selector: ".charts-oi-tos-chain", settle: 10000, prepare: selectSingleChart,
    clipTo: { selector: ".high-oi-scroll table", pad: 12 } },
  // The popover, not the ".oi-finder-indicator-catalog" chip list inside it: the
  // catalog alone is a 594x223 strip of study NAMES with no checkboxes, and this
  // section teaches how a study is switched ON (fixed 2026-08-29). The popover
  // self-limits to max-height 78vh with its own scroll, so the frame is the
  // panel exactly as a trader sees it.
  { id: "indicators-panel", view: "Charts & OI", selector: ".oi-finder-indicators-popover", settle: 10000,
    prepare: async (page) => {
      await selectSingleChart(page);
      await page.locator('summary[aria-label="Add or remove chart indicators"]').first().click();
      await page.waitForTimeout(1500);
    } },
  // ".charts-oi-auto-alerts" is not a class that exists anywhere in App.jsx, so
  // this shot timed out on its first real run (2026-08-29). The Auto Alert page
  // renders <OiAutoAlertDrawer>, whose root carries this testid.
  { id: "auto-alert-panel", view: "Auto Alert", selector: "[data-testid=\"oi-auto-alert-drawer\"]", settle: 8000 },
  { id: "momx-board", view: "MomX Scanner", selector: ".momx-scanner-view", settle: 14000 },
  { id: "premarket-scanner", view: "Premarket Scanner", selector: ".scanner-overview-grid", settle: 9000 },
  // The sheet must be OPENED before it can be photographed: with selector "body"
  // this shot came back as the phone chart with a More button in the tab bar and
  // no sheet at all (2026-08-29). Tap More, then frame the sheet itself.
  { id: "phone-more-sheet", view: "Charts & OI", selector: ".mobile-more-sheet", settle: 7000, viewport: { width: 375, height: 812 },
    prepare: async (page) => {
      await page.locator('[data-testid="mobile-primary-more"]').first().click();
      await page.waitForTimeout(1200);
    } },
  // Setup-tab shots (2026-08-30): the kid-simple setup steps point at exact
  // boxes and buttons, so each gets a photograph with numbered markers.
  { id: "settings-schwab-keys", view: "Settings", selector: ".schwab-settings-card", settle: 8000 },
  { id: "settings-alpaca-keys", view: "Settings", selector: ".personal-api-keys-card", settle: 8000 },
  { id: "momo-settings-modal", view: "MomX Scanner", selector: ".momo-settings", settle: 12000,
    prepare: async (page) => {
      await page.locator('[aria-label="Momo Alert settings"]').first().click();
      await page.waitForTimeout(1500);
      // The topic input shows the signed-in trader's REAL ntfy channel - the
      // secret itself (first capture shipped it, caught on review 2026-08-30).
      // Overwrite the DISPLAYED value only: setting .value directly bypasses
      // React's onChange, so nothing is posted or saved - purely cosmetic.
      await page.evaluate(() => {
        const input = document.querySelector(".momo-settings-push input");
        if (input) input.value = "agx-momo-your-name";
      });
      await page.waitForTimeout(300);
    } },
];

const only = process.argv.slice(2).filter((a) => !a.startsWith("-"));
const targets = only.length ? SHOTS.filter((s) => only.includes(s.id)) : SHOTS;
if (!targets.length) {
  console.error(`No matching shot ids. Known: ${SHOTS.map((s) => s.id).join(", ")}`);
  process.exit(1);
}

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const results = [];
let failures = 0;

for (const shot of targets) {
  const context = await browser.newContext({
    viewport: shot.viewport || { width: 1440, height: 900 },
    deviceScaleFactor: shot.scale || SCALE,
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error).slice(0, 200)));

  try {
    await page.addInitScript((state) => {
      for (const [key, value] of Object.entries(state)) {
        try { window.localStorage.setItem(key, value); } catch { /* blocked storage */ }
      }
    }, { ...CLEAN_STATE, agenticActiveView: shot.view });

    await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.addStyleTag({ content: PRIVACY_CSS });
    await page.waitForTimeout(shot.settle);
    if (shot.prepare) await shot.prepare(page);

    const target = page.locator(shot.selector).first();
    await target.waitFor({ state: "visible", timeout: 20000 });
    const file = path.join(OUT, `${shot.id}.png`);
    const box = await target.boundingBox();

    // A stretched container photographs its empty tail as well as its content.
    // `clipTo` names an inner element whose bottom is the real end of the
    // picture; the frame keeps the container's own x/width and stops there.
    // locator.screenshot() has no clip option, so this goes through
    // page.screenshot() with page coordinates.
    let clipped = null;
    if (shot.clipTo && box) {
      const inner = page.locator(shot.clipTo.selector).first();
      const innerBox = await inner.boundingBox();
      if (!innerBox) throw new Error(`clipTo selector "${shot.clipTo.selector}" matched nothing`);
      const height = Math.min(box.height, Math.ceil(innerBox.y + innerBox.height + (shot.clipTo.pad || 0) - box.y));
      if (height < 40) throw new Error(`clipTo produced a ${height}px frame for ${shot.id}`);
      clipped = { x: box.x, y: box.y, width: box.width, height };
      await page.screenshot({ path: file, clip: clipped });
    } else {
      await target.screenshot({ path: file });
    }

    const bytes = statSync(file).size;
    const frame = clipped || box;
    results.push({ id: shot.id, w: Math.round(frame?.width || 0), h: Math.round(frame?.height || 0), bytes });
    if (bytes > MAX_BYTES) {
      console.error(`  ! ${shot.id} is ${(bytes / 1024).toFixed(0)}KB, over the ${MAX_BYTES / 1024}KB budget - set a lower "scale" on this shot, or crop it tighter`);
      failures += 1;
    }
    if (pageErrors.length) {
      console.error(`  ! ${shot.id} had page errors: ${pageErrors.slice(0, 2).join(" | ")}`);
      failures += 1;
    }
  } catch (error) {
    console.error(`  ! ${shot.id} FAILED: ${String(error).split("\n")[0]}`);
    failures += 1;
  } finally {
    await context.close();
  }
}

await browser.close();

console.log("\n id                      size        bytes");
console.log(" ----------------------- ----------- --------");
results.forEach((r) => {
  console.log(` ${r.id.padEnd(23)} ${`${r.w}x${r.h}`.padEnd(11)} ${(r.bytes / 1024).toFixed(0)}KB`);
});
if (failures) {
  console.error(`\n${failures} problem(s). Fix before committing images.`);
  process.exit(1);
}
console.log("\nNow LOOK at each PNG before committing it.");
