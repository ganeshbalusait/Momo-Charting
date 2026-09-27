import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

// sameTickerDayMoves decides whether the rail re-renders. Lifted and run for
// real rather than asserted on as source text.
const sameTickerDayMoves = new Function([
  lift(/function sameTickerDayMoves\([\s\S]*?\n}/, "sameTickerDayMoves"),
  "return sameTickerDayMoves;",
].join("\n\n"))();

test("an unchanged set of moves is treated as the same", () => {
  assert.equal(sameTickerDayMoves({ AAPL: 1.01, TSLA: -1.55 }, { AAPL: 1.01, TSLA: -1.55 }), true);
});

test("a wobble below display precision does not re-render", () => {
  // THE POINT. The raw percent moves on nearly every poll; the rail shows two
  // decimals. Re-rendering for a difference nobody can see re-renders every
  // memoised thing below it, on a page that has measurable render problems.
  assert.equal(sameTickerDayMoves({ AAPL: 1.0101 }, { AAPL: 1.0149 }), true);
});

test("a change at display precision does re-render", () => {
  assert.equal(sameTickerDayMoves({ AAPL: 1.01 }, { AAPL: 1.02 }), false);
  assert.equal(sameTickerDayMoves({ AAPL: 1.01 }, { AAPL: -1.01 }), false);
});

test("a ticker appearing or disappearing re-renders", () => {
  assert.equal(sameTickerDayMoves({ AAPL: 1.01 }, { AAPL: 1.01, TSLA: 0.5 }), false);
  assert.equal(sameTickerDayMoves({ AAPL: 1.01, TSLA: 0.5 }), false);
  assert.equal(sameTickerDayMoves({}, {}), true);
});

test("a swapped ticker of the same size is not mistaken for no change", () => {
  // Equal key COUNTS with different key NAMES must not compare equal, or a
  // rail whose symbols changed would keep showing the old ticker's move.
  assert.equal(sameTickerDayMoves({ AAPL: 1.01 }, { TSLA: 1.01 }), false);
});

test("missing and zero are different states", () => {
  // A reported 0.00% is a real reading; an absent one means no data and must
  // render no badge at all. They must not compare equal.
  assert.equal(sameTickerDayMoves({ AAPL: 0 }, {}), false);
});

test("the rail polls on its own slow timer, not the 1s chart poller", () => {
  // Subscribing the rail to the 1s quote poller would add every rail ticker to
  // the 1s fetch set and re-render the rail once a second for a badge that
  // only needs to be roughly current.
  const interval = /const TICKER_DAY_MOVE_POLL_MS = ([0-9_]+);/.exec(appSource);
  assert.ok(interval, "TICKER_DAY_MOVE_POLL_MS not found");
  assert.ok(
    Number(interval[1].replace(/_/g, "")) >= 10_000,
    "the rail badge must not poll faster than 10s",
  );
  assert.ok(
    !/useTickerDayMoves[\s\S]{0,600}subscribeLiveChartQuoteFallback/.test(appSource),
    "the rail must not join the 1s chart quote poller",
  );
});
