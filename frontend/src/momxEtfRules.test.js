import assert from "node:assert/strict";
import test from "node:test";
import { gapAndGo, earlyOpt, goRvolMacd, newsMomentum, isEtfRow, bestSetupRanks } from "./momxFilters.js";

// "Use only stocks" (2026-09-27): no setup rule fires on a row the worker marked etf.
const now = Date.parse("2026-09-28T10:00:00-04:00");
const goRow = (extra) => ({ symbol: "X", grade: { letter: "A+" }, adx: { "30m": { plus: 30, minus: 10 } },
  m5: { gapGo: { goAt: now / 1000 - 1200, gap: 3 } }, ...extra });

test("GO fires on a stock and not on an ETF", () => {
  assert.ok(gapAndGo(goRow(), now));
  assert.equal(gapAndGo(goRow({ etf: true }), now), null);
  assert.equal(isEtfRow(goRow({ etf: true })), true);
});

test("OPT, GO+RVOL+MACD, news momentum and the stars skip ETFs", () => {
  const etf = goRow({ etf: true, catalyst: { direction: "bullish", confidence: "high", category: "earnings" } });
  assert.equal(earlyOpt(etf, now), null);
  assert.equal(goRvolMacd(etf, now), null);
  assert.equal(newsMomentum(etf), false);
  assert.equal(bestSetupRanks([etf], now).size, 0);
});

// "Yes, open them" (2026-11-05): SPY and QQQ fire every Setup rule like a
// stock (the BOT already traded them since 2026-10-11); every other ETF stays out.
test("SPY and QQQ fire GO like a stock; another ETF still does not", () => {
  for (const symbol of ["SPY", "QQQ", "spy"]) {
    assert.equal(isEtfRow(goRow({ symbol, etf: true })), false, symbol);
    assert.ok(gapAndGo(goRow({ symbol, etf: true }), now), symbol);
  }
  assert.equal(isEtfRow(goRow({ symbol: "IWM", etf: true })), true);
  assert.equal(gapAndGo(goRow({ symbol: "IWM", etf: true }), now), null);
});
