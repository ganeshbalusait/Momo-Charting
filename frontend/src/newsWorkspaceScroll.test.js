// News Feed and Earnings Calendar own their scroll container inside the
// overflow:hidden .workspace shell (2026-11-14). The news table card is a
// .data-card (overflow: hidden), so without grid-auto-rows: max-content the
// fixed-height grid shrank its row to the screen and clipped the headlines
// below it: the wheel had nothing to scroll. This pins both halves of the rule.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const css = readFileSync(new URL("./index.css", import.meta.url), "utf8");

function scrollRule() {
  const match = css.match(/\.app-shell \.workspace > \.news-workspace,\s*\.app-shell \.workspace > \.earnings-workspace\s*\{([^}]*)\}/);
  assert.ok(match, "the News Feed / Earnings Calendar scroll rule is missing from index.css");
  return match[1];
}

test("News Feed and Earnings Calendar are their own vertical scrollers", () => {
  const body = scrollRule();
  assert.match(body, /overflow-y\s*:\s*auto/);
  assert.match(body, /min-height\s*:\s*0/);
  assert.match(body, /flex\s*:\s*1/);
});

test("their grid rows keep their full height instead of shrinking to the screen", () => {
  assert.match(scrollRule(), /grid-auto-rows\s*:\s*max-content/);
});
