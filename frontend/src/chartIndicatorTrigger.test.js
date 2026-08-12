import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");

test("every chart exposes a visibly labeled Indicators menu trigger", () => {
  const matches = [...appSource.matchAll(
    /<summary([^>]*)aria-label="Add or remove chart indicators"([^>]*)>([\s\S]*?)<\/summary>/g,
  )];

  assert.equal(matches.length, 1);
  const trigger = matches[0][0];
  assert.match(trigger, /<Activity\b/);
  assert.match(trigger, />\s*Indicators\s*</);
  assert.match(trigger, /<ChevronDown\b/);
  assert.doesNotMatch(trigger, /chart-icon-action/);
  assert.match(
    cssSource,
    /\.oi-finder-indicators-menu\[open\]\s*>\s*summary\s+svg:last-child\s*\{[^}]*transform:\s*rotate\(180deg\)/,
  );
});
