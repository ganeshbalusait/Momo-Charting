// Clean look, release 1 (2026-11-12): the Dark / Light switch in the top strip.
// Dark is the default and is remembered per device. Only the top strip and the
// left rail have the new look; every other screen, and the chart, must be
// exactly the same in both themes until it is converted in a later release.
// The feature makes no server call, so nothing is faked or allowed here.

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";
import { CARD, waitForTape, withCharts } from "./charts-core.helpers.mjs";

const TOGGLE = "[data-testid='theme-toggle']";
const MENU_ITEM = "[data-testid='theme-toggle-menu']";
const MORE = ".mobile-chart-bottom-nav button[aria-expanded]";
const STRIP = "header.topbar.trading-header";
const STATIC_VIEW = "Learn + Setup";
const NEW_FONT = /^(-apple-system|"?Segoe UI|system-ui)/i;

const theme = (page) => page.evaluate(() => document.documentElement.getAttribute("data-theme"));
const stored = (page) => page.evaluate(() => window.localStorage.getItem("agx.theme"));

// The switch is a round button in the top strip; on a phone it is a row in the "More" sheet.
async function ready(page) {
  // Either one on screen (the strip button exists but is hidden on a phone).
  await page.waitForFunction((selectors) => selectors.some((selector) => [...document.querySelectorAll(selector)]
    .some((node) => node.getBoundingClientRect().width > 0)), [TOGGLE, MORE], { timeout: 60_000 });
}

async function switchTo(page, want) {
  if ((await theme(page)) !== want) {
    if (await page.locator(TOGGLE).first().isVisible()) {
      await page.locator(TOGGLE).first().click();
    } else {
      await page.locator(MORE).click();
      await page.locator(MENU_ITEM).click();
      await page.locator(".mobile-more-sheet > header button[aria-label='Close']").click();
    }
  }
  await page.waitForFunction((value) => document.documentElement.getAttribute("data-theme") === value, want, { timeout: 5_000 });
}

// WCAG contrast of each visible text element against the first solid background behind it.
function readContrasts(page, selector) {
  return page.evaluate((query) => {
    const parse = (value) => {
      const m = String(value).match(/rgba?\(([^)]+)\)/);
      if (!m) return null;
      const [r, g, b, a = "1"] = m[1].split(/[,\s/]+/).filter(Boolean);
      return { r: Number(r), g: Number(g), b: Number(b), a: Number(a) };
    };
    const lum = ({ r, g, b }) => {
      const f = (v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
      return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
    };
    const backdrop = (node) => {
      for (let at = node; at; at = at.parentElement) {
        const bg = parse(getComputedStyle(at).backgroundColor);
        if (bg && bg.a >= 0.9) return bg;
      }
      return parse(getComputedStyle(document.body).backgroundColor) || { r: 0, g: 0, b: 0, a: 1 };
    };
    const out = [];
    for (const node of document.querySelectorAll(query)) {
      const box = node.getBoundingClientRect();
      const ownText = [...node.childNodes].some((child) => child.nodeType === 3 && child.textContent.trim());
      if (!ownText || box.width === 0 || box.height === 0 || getComputedStyle(node).visibility === "hidden") continue;
      const fg = parse(getComputedStyle(node).color);
      const bg = backdrop(node);
      const [hi, lo] = [lum(fg), lum(bg)].sort((a, b) => b - a);
      out.push({ text: node.textContent.trim().slice(0, 30), cls: node.className, ratio: Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100 });
    }
    return out;
  }, selector);
}

// Colour, background and font of the first elements under a root: the look of a screen, as data.
function lookOf(page, rootSelector, limit = 80) {
  return page.evaluate(({ root, max }) => {
    const top = document.querySelector(root);
    if (!top) return null;
    return [top, ...top.querySelectorAll("*")].slice(0, max).map((node) => {
      const style = getComputedStyle(node);
      return `${node.tagName}.${node.className}|${style.color}|${style.backgroundColor}|${style.fontFamily}|${style.borderTopColor}`;
    });
  }, { root: rootSelector, max: limit });
}

const STRIP_TEXT = [
  `${STRIP} .market-open-pill`, `${STRIP} .market-clock`, `${STRIP} .market-clock *`, `${STRIP} .session-tape span`,
  `${STRIP} .session-tape b`, `${STRIP} .last-scan-label`, `${STRIP} .last-scan-label b`, `${STRIP} .connection-state`,
  `${STRIP} .signed-in-user b`, "aside.sidebar .nav-label", "aside.sidebar .brand-copy b",
].join(", ");

e2eTest("the app opens dark, with the switch showing dark", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page, pageErrors }) => {
    await ready(page);
    assert.equal(await theme(page), "dark");
    assert.equal(await page.locator(TOGGLE).first().getAttribute("aria-pressed"), "false");
    assert.match(await page.locator(TOGGLE).first().getAttribute("aria-label"), /Light \(preview\)/);
    assert.deepEqual(pageErrors, []);
  });
});

e2eTest("choosing light is remembered through a reload, with no dark flash first", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page }) => {
    await page.addInitScript(() => {
      document.addEventListener("DOMContentLoaded", () => {
        window.__themeAtLoad = document.documentElement.getAttribute("data-theme");
      });
    });
    await ready(page);
    await switchTo(page, "light");
    assert.equal(await stored(page), "light");
    assert.equal(await page.locator(TOGGLE).first().getAttribute("aria-pressed"), "true");
    assert.equal(await page.locator('meta[name="theme-color"]').getAttribute("content"), "#f5f5f7");
    await page.reload({ waitUntil: "domcontentloaded" });
    await ready(page);
    assert.equal(await theme(page), "light");
    assert.equal(await page.evaluate(() => window.__themeAtLoad), "light");
    await switchTo(page, "dark");
    assert.equal(await stored(page), "dark");
    assert.equal(await page.locator('meta[name="theme-color"]').getAttribute("content"), "#000000");
  });
});

e2eTest("a stored value that is not a theme opens dark", async () => {
  await withApp({ view: STATIC_VIEW, storage: { "agx.theme": "blue" } }, async ({ page }) => {
    await ready(page);
    assert.equal(await theme(page), "dark");
  });
});

e2eTest("on a phone the switch is on screen, works, and nothing scrolls sideways", async () => {
  await withApp({ view: STATIC_VIEW, viewport: PHONE }, async ({ page }) => {
    await ready(page);
    await page.locator(MORE).click();
    const item = page.locator(MENU_ITEM);
    await item.waitFor({ state: "visible", timeout: 10_000 });
    const box = await item.boundingBox();
    assert.ok(box.x >= 0 && box.x + box.width <= PHONE.width + 1 && box.y + box.height <= PHONE.height + 1, `switch inside the screen: ${JSON.stringify(box)}`);
    assert.ok(box.height >= 44, `a finger-sized row: ${box.height}px`);
    assert.equal((await item.innerText()).trim(), "Light (preview)");
    assert.equal(await item.getAttribute("aria-checked"), "false");
    await item.tap();
    await page.waitForFunction(() => document.documentElement.getAttribute("data-theme") === "light", null, { timeout: 5_000 });
    assert.equal((await item.innerText()).trim(), "Dark");
    assert.equal(await item.getAttribute("aria-checked"), "true");
    assert.equal(await stored(page), "light");
    assert.equal(await overflowsSideways(page), false);
    // The other pages in the sheet are still exactly the ones that were there.
    assert.equal(await page.locator(".mobile-more-grid [role='menuitem']", { hasText: "Settings" }).count(), 1);
  });
});

e2eTest("on a phone the switch is reachable from the chart screen, where the top strip is hidden", async () => {
  await withCharts({ viewport: PHONE, workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async ({ page }) => {
    await ready(page);
    await switchTo(page, "light");
    assert.equal(await stored(page), "light");
    assert.equal(await overflowsSideways(page), false);
  });
});

e2eTest("with storage blocked the phone switch still shows and undoes the theme it set", async () => {
  await withApp({ view: STATIC_VIEW, viewport: PHONE }, async ({ page }) => {
    await ready(page);
    await page.evaluate(() => { Storage.prototype.setItem = () => { throw new Error("blocked"); }; });
    await switchTo(page, "light");
    assert.equal(await stored(page), null);
    await page.locator(MORE).click();
    const item = page.locator(MENU_ITEM);
    assert.equal((await item.innerText()).trim(), "Dark");
    await item.tap();
    await page.waitForFunction(() => document.documentElement.getAttribute("data-theme") === "dark", null, { timeout: 5_000 });
  });
});

e2eTest("the alert bell keeps its alert colours on the restyled strip", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page }) => {
    await ready(page);
    const bell = page.locator(`${STRIP} .global-price-alert-center > .header-icon-button`).first();
    await bell.waitFor({ state: "visible", timeout: 30_000 });
    const colourWith = (cls) => bell.evaluate((node, name) => {
      const before = node.className;
      if (name) node.classList.add(name);
      const colour = getComputedStyle(node).color;
      node.className = before;
      return colour;
    }, cls);
    for (const want of ["dark", "light"]) {
      await switchTo(page, want);
      const plain = await colourWith("");
      assert.notEqual(await colourWith("has-triggered"), plain, `${want}: a fired alert changes the bell's colour`);
      assert.notEqual(await colourWith("is-active"), plain, `${want}: a set alert changes the bell's colour`);
    }
  });
});

e2eTest("the rail's icons are readable in both themes", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page }) => {
    await ready(page);
    for (const want of ["dark", "light"]) {
      await switchTo(page, want);
      const ratios = await page.evaluate(() => {
        const parse = (value) => String(value).match(/[\d.]+/g).slice(0, 3).map(Number);
        const lum = ([r, g, b]) => {
          const f = (v) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
          return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
        };
        const rail = lum(parse(getComputedStyle(document.querySelector("aside.sidebar")).backgroundColor));
        return [...document.querySelectorAll("aside.sidebar nav .nav-item:not(.nav-active) .nav-icon")]
          .filter((node) => node.getBoundingClientRect().width > 0)
          .map((node) => {
            const icon = lum(parse(getComputedStyle(node).color));
            const [hi, lo] = [icon, rail].sort((a, b) => b - a);
            return Math.round(((hi + 0.05) / (lo + 0.05)) * 100) / 100;
          });
      });
      assert.ok(ratios.length >= 5, `${want}: found the rail's icons (${ratios.length})`);
      assert.deepEqual(ratios.filter((ratio) => ratio < 4.5), [], `${want}: icons under 4.5:1`);
    }
  });
});

e2eTest("a chart pop-out window keeps its own background in light", async () => {
  const bodyBackground = (storage) => withApp({ path: "/?popout=chart&symbol=AAPL&timeframe=5m", storage }, async ({ page }) => {
    await page.waitForFunction(() => document.body.classList.contains("is-trading-popout"), null, { timeout: 60_000 });
    return page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  });
  assert.equal(await bodyBackground({ "agx.theme": "light" }), await bodyBackground({}));
});

e2eTest("a second window follows the switch", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page, context }) => {
    await ready(page);
    const other = await context.newPage();
    await other.goto(page.url(), { waitUntil: "domcontentloaded" });
    await other.locator(TOGGLE).first().waitFor({ state: "visible", timeout: 60_000 });
    await switchTo(page, "light");
    await other.waitForFunction(() => document.documentElement.getAttribute("data-theme") === "light", null, { timeout: 5_000 });
    assert.equal(await other.locator(TOGGLE).first().getAttribute("aria-pressed"), "true");
  });
});

for (const [name, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  e2eTest(`the top strip and rail use the new font and stay readable in both themes (${name})`, async () => {
    await withApp({ view: STATIC_VIEW, viewport }, async ({ page }) => {
      await ready(page);
      for (const want of ["dark", "light"]) {
        await switchTo(page, want);
        const font = await page.locator(`${STRIP} .market-open-pill`).first().evaluate((node) => getComputedStyle(node).fontFamily);
        assert.match(font, NEW_FONT, `${want}: top strip font is ${font}`);
        const contrasts = await readContrasts(page, STRIP_TEXT);
        assert.ok(contrasts.length >= 3, `${want}: found strip text to check (${contrasts.length})`);
        const weak = contrasts.filter((entry) => entry.ratio < 4.5);
        assert.deepEqual(weak, [], `${want}: text under 4.5:1`);
      }
    });
  });
}

e2eTest("the strip and rail take their colours from the theme", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page }) => {
    await ready(page);
    const bg = (selector) => page.locator(selector).first().evaluate((node) => getComputedStyle(node).backgroundColor);
    assert.equal(await bg(STRIP), "rgb(0, 0, 0)");
    assert.equal(await bg("aside.sidebar"), "rgb(28, 28, 30)");
    await switchTo(page, "light");
    assert.equal(await bg(STRIP), "rgb(245, 245, 247)");
    assert.equal(await bg("aside.sidebar"), "rgb(255, 255, 255)");
    assert.equal(await bg("body"), "rgb(245, 245, 247)");
  });
});

for (const [name, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  e2eTest(`a screen not converted yet looks the same in both themes (${name})`, async () => {
    await withApp({ view: STATIC_VIEW, viewport }, async ({ page }) => {
      await ready(page);
      const root = ".workspace > :not(header)";
      const dark = await lookOf(page, root);
      assert.ok(dark && dark.length > 10, "found the screen under the top strip");
      await switchTo(page, "light");
      assert.deepEqual(await lookOf(page, root), dark);
    });
  });
}

e2eTest("the chart looks the same in both themes", async () => {
  await withCharts({ workspace: { symbols: ["AAPL"], timeframes: ["5m"] } }, async ({ page }) => {
    await ready(page);
    await waitForTape(page, { symbol: "AAPL" });
    const corner = () => page.locator(CARD).first().evaluate((node) => {
      const canvas = node.querySelector("canvas");
      const pixel = canvas?.getContext("2d")?.getImageData(4, 4, 1, 1).data;
      return pixel ? [...pixel].join(",") : null;
    });
    const dark = { look: await lookOf(page, CARD, 60), corner: await corner() };
    assert.ok(dark.corner, "read a pixel of the chart background");
    await switchTo(page, "light");
    await page.waitForTimeout(500);
    assert.deepEqual({ look: await lookOf(page, CARD, 60), corner: await corner() }, dark);
    assert.equal(await overflowsSideways(page), false);
  });
});
