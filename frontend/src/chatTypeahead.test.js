// Type-ahead for the chat's ask box: his own questions first, then built-in questions filled with his ticker.
import assert from "node:assert/strict";
import test from "node:test";

import { GENERAL_PATTERNS, TICKER_PATTERNS, TYPEAHEAD_MAX, knownTickers, typeahead } from "./chatTypeahead.js";

test("'why tsla' offers 'why TSLA went up today' first, then TSLA's other questions, at most 6", () => {
  const out = typeahead("why tsla");
  assert.equal(out[0], "why TSLA went up today");
  assert.equal(out[1], "why TSLA is down today");
  assert.ok(out.length <= TYPEAHEAD_MAX && out.length === 6);
  assert.ok(out.every((q) => q.includes("TSLA")));
});

test("a bare ticker offers the ticker's question families in order", () => {
  assert.deepEqual(typeahead("tsla", { isOwner: false }), [
    "why TSLA went up today", "why TSLA is down today", "latest news on TSLA", "TSLA chart levels", "TSLA option chain", "TSLA call wall",
  ]);
  const more = typeahead("tsla", { isOwner: false, max: 30 });
  for (const q of ["TSLA today on the scanner", "TSLA at 9:30 on the scanner"]) assert.ok(more.includes(q), q);
});

test("company names map to tickers, two-word names too", () => {
  assert.equal(typeahead("why tesla")[0], "why TSLA went up today");
  assert.equal(typeahead("nvidia news")[0], "latest news on NVDA");
  assert.ok(typeahead("jp morgan chart").includes("JPM chart levels"));
});

test("a half-typed ticker completes from the tickers the app knows; a half-typed word is not a ticker", () => {
  const out = typeahead("why ts", { known: ["TSM"] });
  assert.ok(out.includes("why TSLA went up today"), out.join(" | "));
  assert.ok(out.includes("why TSM went up today"), out.join(" | "));
  assert.deepEqual(typeahead("zeta ch", { known: ["ZETA"] }).slice(0, 2), ["ZETA chart levels", "ZETA option chain"]);
  assert.ok(typeahead("zeta ch", { known: ["ZETA"] }).every((q) => q.includes("ZETA")), "'ch' was read as a ticker");
  assert.ok(typeahead("aapl opt").includes("AAPL option chain"));
});

test("a ticker the app has not seen yet still fills the patterns; everyday words never do", () => {
  assert.equal(typeahead("why zeta")[0], "why ZETA went up today");
  assert.deepEqual(typeahead("why is"), []);
});

test("his own questions come first, most asked first, and only when they match", () => {
  const history = [
    { q: "why TSLA dumped at noon", n: 1 },
    { q: "TSLA call wall", n: 5 },
    { q: "SPY price", n: 9 },
  ];
  const out = typeahead("tsla", { history });
  assert.deepEqual(out.slice(0, 2), ["TSLA call wall", "why TSLA dumped at noon"]);
  assert.equal(out.filter((q) => q.toLowerCase() === "tsla call wall").length, 1, "no repeat of the same question");
  assert.ok(!out.includes("SPY price"));
});

test("a regular user is never offered an owner question", () => {
  const owner = /\b(bot|missed|failed|scorecard|paper|went up\b.*how much|how much it went up)\b/i;
  for (const typed of ["why tsla", "tsla", "score", "what is the", "nvda if", "why was"]) {
    for (const q of typeahead(typed, { isOwner: false, max: 30 })) assert.doesNotMatch(q, /missed|failed|scorecard|the bot|how much it went up/i, `${typed}: ${q}`);
  }
  assert.ok(typeahead("why was tsla", { isOwner: true }).includes("why was TSLA missed"));
  assert.ok(typeahead("score", { isOwner: true }).includes("scorecard"));
  assert.ok(!owner.test("x"));
});

test("no VWAP anywhere, even in his own past questions", () => {
  for (const p of [...TICKER_PATTERNS, ...GENERAL_PATTERNS]) assert.doesNotMatch(p.text, /vwap/i);
  const out = typeahead("spy", { history: [{ q: "SPY vwap on 5m", n: 50 }], max: 30 });
  assert.ok(out.length > 0 && out.every((q) => !/vwap/i.test(q)));
});

test("questions with no ticker match their words; the ticker on screen fills the rest", () => {
  assert.ok(typeahead("which sec").includes("which sector is hot"));
  assert.ok(typeahead("premar").includes("premarket today"));
  assert.ok(typeahead("chart lev", { pageTicker: "AAPL" }).includes("AAPL chart levels"));
});

test("too short or empty gives nothing; it stays fast on every keystroke", () => {
  assert.deepEqual(typeahead(""), []);
  assert.deepEqual(typeahead("t"), []);
  assert.deepEqual(typeahead(null), []);
  const history = Array.from({ length: 30 }, (_, i) => ({ q: `question number ${i} about TSLA and the scanner`, n: i }));
  const known = Array.from({ length: 400 }, (_, i) => `T${i}`);
  const start = performance.now();
  for (let i = 0; i < 200; i += 1) typeahead("why tsla went", { history, known });
  assert.ok((performance.now() - start) / 200 < 5, "one keystroke must take well under 50 ms");
});

test("knownTickers keeps real-looking tickers, upper case, once", () => {
  const t = knownTickers(["zeta", "ZETA", "", "not a ticker", "BRK.B"], "rklb");
  assert.ok(t.includes("ZETA") && t.includes("BRK.B") && t.includes("RKLB") && t.includes("SPY"));
  assert.equal(t.filter((x) => x === "ZETA").length, 1);
  assert.ok(!t.some((x) => x.includes(" ")));
});
