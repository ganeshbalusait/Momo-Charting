// The app is dark only (2026-11-13: Ganesh dropped the Light theme). The
// top strip and the left rail have the clean look (Inter / system font, calm
// colours); there is no Dark / Light switch anywhere, and a Light choice made
// with the old switch is forgotten. The feature makes no server call.

import assert from "node:assert/strict";

import { DESKTOP, PHONE, e2eTest, overflowsSideways, withApp } from "./harness.mjs";

const MORE = ".mobile-chart-bottom-nav button[aria-expanded]";
const STRIP = "header.topbar.trading-header";
const STATIC_VIEW = "Learn + Setup";
const NEW_FONT = /^(-apple-system|"?Segoe UI|system-ui|"?Inter)/i;
const SWITCH = "[data-testid='theme-toggle'], [data-testid='theme-toggle-menu']";

const theme = (page) => page.evaluate(() => document.documentElement.getAttribute("data-theme"));

async function ready(page, viewport) {
  await page.locator(viewport === PHONE ? MORE : STRIP).first().waitFor({ state: "visible", timeout: 60_000 });
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

for (const [name, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  e2eTest(`the app opens dark and has no Dark / Light switch (${name})`, async () => {
    await withApp({ view: STATIC_VIEW, viewport }, async ({ page, pageErrors }) => {
      await ready(page, viewport);
      assert.equal(await theme(page), "dark");
      assert.equal(await page.locator('meta[name="theme-color"]').getAttribute("content"), "#000000");
      assert.equal(await page.locator(SWITCH).count(), 0);
      if (viewport === PHONE) {
        await page.locator(MORE).click();
        await page.locator(".mobile-more-sheet").waitFor({ state: "visible", timeout: 10_000 });
        assert.equal(await page.locator(SWITCH).count(), 0, "no switch in the More menu");
        assert.equal(await overflowsSideways(page), false);
      }
      assert.deepEqual(pageErrors, []);
    });
  });
}

e2eTest("a Light choice made with the old switch opens dark and is forgotten", async () => {
  await withApp({ view: STATIC_VIEW, storage: { "agx.theme": "light" } }, async ({ page }) => {
    await ready(page, DESKTOP);
    assert.equal(await theme(page), "dark");
    assert.equal(await page.evaluate(() => window.localStorage.getItem("agx.theme")), null);
    assert.equal(await page.evaluate(() => getComputedStyle(document.body).backgroundColor), await page.evaluate(() => {
      document.documentElement.setAttribute("data-theme", "dark");
      return getComputedStyle(document.body).backgroundColor;
    }));
  });
});

for (const [name, viewport] of [["desktop", DESKTOP], ["phone", PHONE]]) {
  e2eTest(`the top strip and rail use the new font and are readable (${name})`, async () => {
    await withApp({ view: STATIC_VIEW, viewport }, async ({ page }) => {
      await ready(page, viewport);
      const fonts = await page.locator(`${STRIP}, aside.sidebar`).evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).fontFamily));
      for (const font of fonts) assert.match(font, NEW_FONT);
      const low = (await readContrasts(page, STRIP_TEXT)).filter((item) => item.ratio < 4.5);
      assert.deepEqual(low, []);
    });
  });
}

e2eTest("the strip and rail have the dark clean-look colours", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page }) => {
    await ready(page, DESKTOP);
    const bg = (selector) => page.locator(selector).first().evaluate((node) => getComputedStyle(node).backgroundColor);
    assert.equal(await bg(STRIP), "rgb(0, 0, 0)");
    assert.equal(await bg("aside.sidebar"), "rgb(28, 28, 30)");
  });
});

e2eTest("the alert bell keeps its alert colours on the restyled strip", async () => {
  await withApp({ view: STATIC_VIEW }, async ({ page }) => {
    await ready(page, DESKTOP);
    const bell = page.locator(`${STRIP} .global-price-alert-center > .header-icon-button`).first();
    await bell.waitFor({ state: "visible", timeout: 30_000 });
    const colourWith = (cls) => bell.evaluate((node, name) => {
      const before = node.className;
      if (name) node.classList.add(name);
      const colour = getComputedStyle(node).color;
      node.className = before;
      return colour;
    }, cls);
    const plain = await colourWith("");
    assert.notEqual(await colourWith("has-triggered"), plain, "a fired alert changes the bell's colour");
    assert.notEqual(await colourWith("is-active"), plain, "a set alert changes the bell's colour");
  });
});
