// The MomX Scanner's clean-look colours (release 2) are written by
// scripts/gen_scanner_light_skin.mjs from the scanner's rules in index.css.
// These tests pin the rules of that conversion and that the two written files
// match index.css as it is now.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  OUTPUTS, SCOPES, contrast, darkText, generate, isSolidFill, lightBackground, lightLine, lightText,
  parseColour, printColour, readRules, skinRule,
} from "../../scripts/gen_scanner_light_skin.mjs";

const RAISED_LIGHT = { r: 236, g: 236, b: 240 };
const hex = (text) => parseColour(text);
const rule = (selector, body, wrappers = []) => ({ selector, wrappers, declarations: body.split(";").map((d) => d.trim()).filter(Boolean).map((d) => [d.slice(0, d.indexOf(":")).trim(), d.slice(d.indexOf(":") + 1).trim()]) });

test("colours are read in every way index.css writes them", () => {
  assert.deepEqual(parseColour("#fff"), { r: 255, g: 255, b: 255, a: 1 });
  assert.deepEqual(parseColour("#22d3ee"), { r: 34, g: 211, b: 238, a: 1 });
  assert.deepEqual(parseColour("rgba(0, 210, 225, 0.6)"), { r: 0, g: 210, b: 225, a: 0.6 });
  assert.deepEqual(parseColour("rgb(10 20 30 / 50%)"), { r: 10, g: 20, b: 30, a: 0.5 });
  assert.deepEqual(parseColour("black"), { r: 0, g: 0, b: 0, a: 1 });
  assert.equal(parseColour("rgba(var(--x), 0.5)"), null);
  assert.equal(printColour({ r: 29, g: 29, b: 31, a: 1 }), "#1d1d1f");
  assert.equal(printColour({ r: 0, g: 0, b: 0, a: 0.25 }), "rgba(0, 0, 0, 0.25)");
});

test("light: pale text keeps its hue and becomes readable on the greyest light surface", () => {
  for (const pale of ["#9beef5", "#22d3ee", "#fbbf24", "#4ade80", "#f472d0", "#a78bfa", "#ffcc80"]) {
    const next = lightText(hex(pale));
    assert.ok(next, pale + " must change");
    assert.ok(contrast(next, RAISED_LIGHT) >= 5.5, `${pale} -> ${printColour(next)} is ${contrast(next, RAISED_LIGHT).toFixed(1)}:1`);
    assert.equal(next.a, 1);
  }
  // cyan stays on the blue-green side, amber on the yellow-red side
  const cyan = lightText(hex("#22d3ee")); assert.ok(cyan.b > cyan.r);
  const amber = lightText(hex("#fbbf24")); assert.ok(amber.r > amber.b);
});

test("light: grey text becomes one of the two shared text colours; dark text is left alone", () => {
  assert.equal(printColour(lightText(hex("#e7e9f0"))), "#1d1d1f");
  assert.equal(printColour(lightText(hex("#b9bdc9"))), "#1d1d1f");
  assert.equal(printColour(lightText(hex("#8b8f9e"))), "#636366");
  assert.equal(printColour(lightText(hex("rgba(148, 163, 184, 0.62)"))), "#636366");
  assert.equal(lightText(hex("#05131a")), null);
  assert.equal(lightText(hex("#1a1204")), null);
});

test("light: dark surfaces turn near-white, white veils turn into black veils, signal fills stay", () => {
  for (const dark of ["#05070b", "#0a0d13", "#171a22", "#10131a"]) {
    const next = lightBackground(hex(dark));
    assert.ok(Math.min(next.r, next.g, next.b) >= 230, dark + " -> " + printColour(next));
  }
  assert.deepEqual(lightBackground(hex("rgba(255, 255, 255, 0.06)")), { r: 0, g: 0, b: 0, a: 0.06 * 0.7 });
  for (const fill of ["#22d3ee", "#f59e0b", "#a3e635", "#6d28d9", "#16a34a", "#ffffff"]) {
    assert.equal(lightBackground(hex(fill)), null, fill + " is a signal fill");
    assert.equal(isSolidFill(hex(fill)), true);
  }
  assert.equal(lightBackground(hex("rgba(251, 191, 36, 0.2)")), null, "a coloured tint stays");
  assert.equal(isSolidFill(hex("#171a22")), false);
});

test("light: faint light borders become visible dark ones", () => {
  const next = lightLine(hex("rgba(255, 255, 255, 0.16)"));
  assert.deepEqual([next.r, next.g, next.b], [0, 0, 0]);
  const pale = lightLine(hex("#9beef5"));
  assert.ok(contrast(pale, { r: 255, g: 255, b: 255 }) > 2.5);
  assert.equal(lightLine(hex("#f97316")), null, "a signal-coloured border stays");
});

test("dark: only faint grey text is lifted; colours and readable greys stay as typed", () => {
  assert.equal(printColour(darkText(hex("#6d7180"))), "#a1a1a6");
  assert.equal(printColour(darkText(hex("rgba(148, 163, 184, 0.62)"))), "#a1a1a6");
  assert.equal(darkText(hex("#22d3ee")), null);
  assert.equal(darkText(hex("#f472d0")), null);
  assert.equal(darkText(hex("#e7e9f0")), null);
  assert.equal(darkText(hex("#05131a")), null, "dark text on a signal fill is not touched");
});

test("a rule is twinned under the theme's scope, and only its colour lines", () => {
  const twin = skinRule(rule(".momx-scanner-panel .momx-btn", "height: 24px; padding: 0 9px; border: 1px solid var(--momx-line-strong); background: #171a22; color: #9beef5; font-size: 11px"));
  assert.ok(twin.text.startsWith(SCOPES.light + " .momx-scanner-panel .momx-btn {"));
  assert.doesNotMatch(twin.text, /height|padding|font-size/);
  assert.match(twin.text, /border-color: var\(--momx-line-strong\);/);
  assert.doesNotMatch(twin.text, /#171a22|#9beef5/);
  const dark = skinRule(rule(".momx-scanner-panel .momx-footnote", "margin: 0; color: #6d7180; background: #05070b"), "dark");
  assert.equal(dark.text, SCOPES.dark + " .momx-scanner-panel .momx-footnote {\n  color: #a1a1a6;\n}");
});

test("a signal pill keeps its fill AND the text typed for it, and is still written out", () => {
  // Written out unchanged on purpose: its twin must outrank the twin of the
  // plain tag rule, or the purple MX A+ tag takes the plain tag's text colour.
  const twin = skinRule(rule('.momx-strategy-tag[data-testid="momx-strategy-tag-mxaplus"]', "background: #6d28d9; border-color: #facc15; color: #fef9c3"));
  assert.match(twin.text, /background-color: #6d28d9;/);
  assert.match(twin.text, /color: #fef9c3;/);
});

test("selector lists keep only the scanner's parts; :root is merged; !important survives", () => {
  const twin = skinRule(rule(".oi-finder-card .title, .momx-scanner-panel .momx-th", "color: #b9bdc9 !important"));
  assert.equal(twin.text, SCOPES.light + " .momx-scanner-panel .momx-th {\n  color: #1d1d1f !important;\n}");
  assert.equal(skinRule(rule(".oi-finder-card .title", "color: #b9bdc9")), null);
  assert.ok(skinRule(rule(":root .momx-picker", "background: #10131a")).text.startsWith(SCOPES.light + " .momx-picker {"));
  assert.equal(skinRule(rule(".momx-scanner-panel .momx-btn", "height: 24px")), null, "no colours, no twin");
});

test("the dim layer behind a dialog and the card's day chart are left as typed", () => {
  assert.equal(skinRule(rule(".momx-filters-overlay", "background: rgba(0, 0, 0, 0.5)")), null);
  assert.equal(skinRule(rule(".momx-daychart svg", "background: #0b0d12; border: 1px solid #1d2230")), null);
  assert.equal(skinRule(rule(".momx-daychart-line.is-up", "stroke: #00e5ff")), null);
  assert.ok(skinRule(rule(".momx-daychart figcaption", "color: #8a8f9c")), "the caption outside the drawing is converted");
});

test("rules inside @media keep their wrapper; animations are not touched", () => {
  const css = "@media (max-width: 760px) { .momx-scanner-panel .momx-btn { color: #9beef5; } }\n@keyframes momx-pulse { 0% { color: #fff; } }\n/* note { */ .momx-x { color: #e7e9f0 }";
  const rules = readRules(css);
  assert.deepEqual(rules.map((r) => r.selector), [".momx-scanner-panel .momx-btn", ".momx-x"]);
  assert.deepEqual(rules[0].wrappers, ["@media (max-width: 760px)"]);
  const { text, count } = generate(css);
  assert.equal(count, 2);
  assert.match(text, /@media \(max-width: 760px\) \{\n:root\[data-theme="light"\]\[data-agx-skin~="scanner"\] \.momx-scanner-panel \.momx-btn \{/);
});

const indexCss = readFileSync(new URL("./index.css", import.meta.url), "utf8");

for (const theme of ["dark"]) {
  test(`the written ${theme} file matches index.css as it is now`, () => {
    const onDisk = readFileSync(OUTPUTS[theme], "utf8").replace(/\r\n/g, "\n");
    assert.ok(onDisk === generate(indexCss, theme).text,
      "A scanner colour in index.css changed. Run: node scripts/gen_scanner_light_skin.mjs");
  });

  test(`every rule in the written ${theme} file applies only when the scanner skin is on, and sets only colours`, () => {
    const rules = readRules(readFileSync(OUTPUTS[theme], "utf8"));
    assert.ok(rules.length > 300, "rules found: " + rules.length);
    for (const r of rules) {
      for (const part of r.selector.split(/,\s*(?=:root)/)) assert.ok(part.startsWith(SCOPES[theme] + " ") || part.startsWith(SCOPES[theme] + "."), part.slice(0, 120));
      for (const [prop] of r.declarations) {
        // Colour longhands, shadows and gradient backgrounds only: never a
        // shorthand that would also set a width or a line style.
        assert.match(prop, /^(color|fill|stroke|caret-color|-webkit-text-fill-color|text-decoration-color|background-color|background-image|background|border-color|border-(top|right|bottom|left|block|inline)-color|outline-color|column-rule-color|box-shadow|accent-color|text-shadow|scrollbar-color)$/, prop + " in " + r.selector.slice(0, 80));
      }
      for (const [prop, value] of r.declarations) {
        if (prop === "background") assert.match(value, /gradient\(|url\(/, "a plain background must be written as background-color: " + r.selector.slice(0, 80));
      }
    }
  });
}

// ---- review fixes (2026-11-13) ----------------------------------------------

test("a twin sets colour longhands only, so it cannot undo a dashed border or an underline style", () => {
  const twin = skinRule(rule(".momx-scanner-panel .momx-sector", "border: 1px solid rgba(255, 255, 255, 0.16); border-left: 3px solid transparent; outline: 1px solid #a78bfa; background: rgba(255, 255, 255, 0.028); text-decoration: underline dotted #8b8f9e"));
  assert.doesNotMatch(twin.text, /\b(border|border-left|outline|text-decoration|background):/);
  assert.match(twin.text, /border-color: rgba\(0, 0, 0, 0\.14\);/);
  assert.match(twin.text, /border-left-color: transparent;/);
  assert.match(twin.text, /outline-color: /);
  assert.match(twin.text, /background-color: rgba\(0, 0, 0, 0\.02\);/);
  assert.match(twin.text, /text-decoration-color: #636366;/);
  // a border with no colour in it says nothing about colour
  assert.equal(skinRule(rule(".momx-scanner-panel .momx-btn", "border: 0; outline: none")), null);
  // background: none / transparent still has to win over a general twin's fill
  assert.match(skinRule(rule(".momx-scanner-panel .momx-x", "background: transparent")).text, /background-color: transparent;/);
  assert.match(skinRule(rule(".momx-scanner-panel .momx-x", "background: none")).text, /background-color: transparent;/);
  // a gradient is kept as an image, with its colours converted
  assert.match(skinRule(rule(".momx-scanner-panel .momx-x", "background: linear-gradient(rgba(34, 211, 238, 0.10), rgba(34, 211, 238, 0.10)), #0a0d13")).text, /background: linear-gradient\(.*\), #f[0-9a-f]{5};/);
});

test("light: a nearly solid dark panel becomes a nearly solid light panel, not a see-through veil", () => {
  const banner = lightBackground(hex("rgba(20, 12, 2, 0.96)"));
  assert.ok(Math.min(banner.r, banner.g, banner.b) >= 230 && banner.a >= 0.95, printColour(banner));
  const faint = lightBackground(hex("rgba(0, 0, 0, 0.3)"));
  assert.deepEqual([faint.r, faint.g, faint.b], [0, 0, 0]);
  assert.ok(faint.a < 0.1);
});

test("the alert banner is app-wide, not the scanner's: it is never twinned", () => {
  assert.equal(skinRule(rule(".momo-alert-banner", "background: rgba(20, 12, 2, 0.96); color: #fbbf24")), null);
  assert.equal(skinRule(rule(".momo-alert-banner b", "color: #fff7ed"), "dark"), null);
});

test("the History day picker's classes are shared with an older page: twinned only inside the scanner panel", () => {
  const twin = skinRule(rule(".scanner-day-navbtn", "background: #171a22; color: #e7e9f0"));
  assert.ok(twin.text.startsWith(SCOPES.light + " .momx-scanner-panel .scanner-day-navbtn {"), twin.text.slice(0, 120));
});

test("dark: a state that is dim on purpose stays dim", () => {
  for (const selector of [".momx-scanner-panel .momx-setup.is-none .momx-setup-letter", ".momx-scanner-panel td.momx-recordcol.is-few", ".momx-columns-row.is-hidden .momx-columns-name", ".momx-scanner-panel .momx-newscol.is-stored:not(.is-fresh)"]) {
    const twin = skinRule(rule(selector, "color: #6b7080"), "dark");
    assert.match(twin.text, /color: #6b7080;/, selector);
  }
  assert.match(skinRule(rule(".momx-scanner-panel .momx-footnote", "color: #6b7080"), "dark").text, /color: #a1a1a6;/);
});

test("Windows line endings in index.css do not change what is written", () => {
  const css = ".momx-scanner-panel .momx-x {\n  box-shadow: 0 0 0 1px #22d3ee,\n    0 2px 4px rgba(0, 0, 0, 0.5);\n  color: #e7e9f0;\n}\n";
  assert.equal(generate(css.replace(/\n/g, "\r\n")).text, generate(css).text);
});
