import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");
const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");

// The trigger was deliberately made ICON-ONLY on 2026-08-18 at the trader's
// request ("alert, indicators only symbol"), so the label assertion this test
// used to make is gone. The icon, the chevron and the accessible name still
// have to be there - dropping the text must not drop the meaning.
test("every chart exposes an icon-only Indicators menu trigger", () => {
  const matches = [...appSource.matchAll(
    /<summary([^>]*)aria-label="Add or remove chart indicators"([^>]*)>([\s\S]*?)<\/summary>/g,
  )];

  assert.equal(matches.length, 1);
  const trigger = matches[0][0];
  assert.match(trigger, /<Activity\b/);
  // Icon only: no visible text label inside the summary.
  assert.doesNotMatch(trigger, />\s*Indicators\s*</);
  // The accessible name is what carries it now.
  assert.match(trigger, /aria-label="Add or remove chart indicators"/);
  assert.match(trigger, /title="Indicators and studies"/);
  assert.match(trigger, /<ChevronDown\b/);
  assert.doesNotMatch(trigger, /chart-icon-action/);
  assert.match(
    cssSource,
    /\.oi-finder-indicators-menu\[open\]\s*>\s*summary\s+svg:last-child\s*\{[^}]*transform:\s*rotate\(180deg\)/,
  );
});
