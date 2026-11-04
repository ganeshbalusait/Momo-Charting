// Unit (2026-11-04 coverage pass, group charts-studies, feature charts-mobile):
// the phone breakpoint contract. JavaScript decides "is this a phone" with
// isMobileChartWidth (MOBILE_CHART_BREAKPOINT_PX) - no deep-history auto-load, no
// chain under the chart, phone price margins - while index.css decides what a phone
// SEES (the Chart/Chain tab rules, the in-card quick row, the Draw toggle). If the
// two disagree, a width between them gets the phone's data plan with the desktop's
// layout or the reverse. Every CSS block that styles the phone chart tabs must use
// the same breakpoint the JavaScript uses.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  MOBILE_CHART_BREAKPOINT_PX,
  initialChartChainOpen,
  isMobileChartWidth,
  shouldAutoLoadDeepChartHistory,
} from "./mobileChartPerformance.js";

const css = readFileSync(new URL("./index.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

// Every @media block with its query and body, brace-matched (blocks nest rules).
function mediaBlocks(source) {
  const blocks = [];
  const pattern = /@media\s*([^{]+)\{/g;
  let match;
  while ((match = pattern.exec(source))) {
    let depth = 1;
    let index = pattern.lastIndex;
    while (depth > 0 && index < source.length) {
      if (source[index] === "{") depth += 1;
      else if (source[index] === "}") depth -= 1;
      index += 1;
    }
    blocks.push({ query: match[1].trim(), body: source.slice(pattern.lastIndex, index - 1) });
    pattern.lastIndex = index;
  }
  return blocks;
}

// The rules that decide what the phone chart page shows: the Chart/Chain tab
// split, the Draw toggle and the in-card quick-ticker row. (Overlay rules such as
// the price-alert header, which deliberately reach tablet widths, are not part of
// the phone/desktop split.)
const PHONE_CHART_SELECTORS = [".is-options-tab", ".oi-finder-mobile-draw-toggle", ".oi-finder-chart-quick-row"];
const PHONE_CHART_TAB_RULE = /\.is-chart-tab[^{}]*\.charts-oi-(?:tos-chain|full-chart|page-full)/;
const stylesPhoneChart = (body) => PHONE_CHART_SELECTORS.some((selector) => body.includes(selector)) || PHONE_CHART_TAB_RULE.test(body);

test("the JS phone test is inclusive at the breakpoint", () => {
  assert.equal(MOBILE_CHART_BREAKPOINT_PX, 760);
  assert.equal(isMobileChartWidth(375), true);
  assert.equal(isMobileChartWidth(MOBILE_CHART_BREAKPOINT_PX), true);
  assert.equal(isMobileChartWidth(MOBILE_CHART_BREAKPOINT_PX + 1), false);
});

test("every @media block that styles the phone chart uses the JS breakpoint", () => {
  const phoneBlocks = mediaBlocks(css).filter(({ body }) => stylesPhoneChart(body));
  assert.ok(phoneBlocks.length >= 3, `expected the phone chart rules in @media blocks, found ${phoneBlocks.length}`);
  const offenders = phoneBlocks
    .map(({ query }) => query)
    .filter((query) => {
      const widths = [...query.matchAll(/max-width:\s*(\d+)px/g)].map((found) => Number(found[1]));
      return widths.length && !widths.includes(MOBILE_CHART_BREAKPOINT_PX);
    });
  assert.deepEqual([...new Set(offenders)], [], "phone chart rules gated on a different width than isMobileChartWidth");
});

test("the Chart/Chain tab rules exist for both tabs inside the phone breakpoint", () => {
  const phone = mediaBlocks(css).filter(({ query }) => query.includes(`max-width: ${MOBILE_CHART_BREAKPOINT_PX}px`));
  const body = phone.map((block) => block.body).join("\n");
  assert.match(body, /\.is-chart-tab\s+\.charts-oi-page-full\s+\.charts-oi-tos-chain[\s\S]*?display:\s*none\s*!important/,
    "on the Chart tab the chain must be hidden");
  assert.match(body, /\.is-options-tab\s+\.charts-oi-page-full\s+\.charts-oi-full-chart\s*\{[^}]*display:\s*none\s*!important/,
    "on the Chain tab the chart must be hidden (not unmounted)");
});

test("at the breakpoint the phone data plan applies: no chain under the chart, no deep auto-load", () => {
  const phone = isMobileChartWidth(MOBILE_CHART_BREAKPOINT_PX);
  assert.equal(initialChartChainOpen({ isMobile: phone }), false);
  assert.equal(shouldAutoLoadDeepChartHistory({ isMobile: phone }), false);
  const desktop = isMobileChartWidth(MOBILE_CHART_BREAKPOINT_PX + 1);
  assert.equal(initialChartChainOpen({ isMobile: desktop }), true);
  assert.equal(shouldAutoLoadDeepChartHistory({ isMobile: desktop }), true);
});
