import assert from "node:assert/strict";
import test from "node:test";
import {
  formatTickerStripChange,
  formatTickerStripPercent,
  formatTickerStripPrice,
  mergeTickerStripLivePrice,
  normalizeTickerStripPayload,
  tickerStripBias,
} from "./tickerIdentityStrip.js";

test("bias follows the sign of the day change", () => {
  assert.deepEqual(tickerStripBias(-0.03), { label: "BEAR", tone: "bear", arrow: "↓" });
  assert.deepEqual(tickerStripBias(1.2), { label: "BULL", tone: "bull", arrow: "↑" });
  assert.deepEqual(tickerStripBias(0), { label: "FLAT", tone: "flat", arrow: "→" });
  assert.deepEqual(tickerStripBias(null), { label: "FLAT", tone: "flat", arrow: "→" });
  assert.deepEqual(tickerStripBias("bad"), { label: "FLAT", tone: "flat", arrow: "→" });
});

test("today's dollar move keeps an explicit sign", () => {
  assert.equal(formatTickerStripChange(11.244), "+11.24");
  assert.equal(formatTickerStripChange(-0.9), "-0.90");
  assert.equal(formatTickerStripChange(0), "0.00");
  assert.equal(formatTickerStripChange(1234.5), "+1,234.50");
  assert.equal(formatTickerStripChange(null), "--");
  assert.equal(formatTickerStripChange("bad"), "--");
});

test("price formats with two decimals and thousands separators", () => {
  assert.equal(formatTickerStripPrice(771.12), "771.12");
  assert.equal(formatTickerStripPrice(1234.5), "1,234.50");
  assert.equal(formatTickerStripPrice(null), "--");
  assert.equal(formatTickerStripPrice("bad"), "--");
});

test("percent formats with explicit sign", () => {
  assert.equal(formatTickerStripPercent(-0.03), "-0.03%");
  assert.equal(formatTickerStripPercent(0.412), "+0.41%");
  assert.equal(formatTickerStripPercent(0), "0.00%");
  assert.equal(formatTickerStripPercent(undefined), "--%");
});

test("normalizes API payloads and rejects junk numbers", () => {
  assert.deepEqual(
    normalizeTickerStripPayload(
      {
        symbol: "spy",
        name: " State Street SPDR S&P 500 ETF Trust ",
        lastPrice: "771.12",
        change: -0.23,
        changePct: -0.03,
        closePrice: 771.35,
      },
      "SPY",
    ),
    {
      symbol: "SPY",
      name: "State Street SPDR S&P 500 ETF Trust",
      lastPrice: 771.12,
      change: -0.23,
      changePct: -0.03,
      closePrice: 771.35,
    },
  );
  const empty = normalizeTickerStripPayload({}, "qqq");
  assert.deepEqual(empty, {
    symbol: "QQQ",
    name: "",
    lastPrice: null,
    change: null,
    changePct: null,
    closePrice: null,
  });
});

test("live price merge recomputes the day change against the close", () => {
  const strip = normalizeTickerStripPayload({
    symbol: "SPY",
    name: "State Street SPDR S&P 500 ETF Trust",
    lastPrice: 771.12,
    change: -0.23,
    changePct: -0.03,
    closePrice: 771.35,
  });
  const merged = mergeTickerStripLivePrice(strip, 772.5);
  assert.equal(merged.lastPrice, 772.5);
  assert.equal(merged.change, 1.15);
  assert.ok(Math.abs(merged.changePct - 0.1491) < 0.001);
});

test("live price merge without a close keeps the last known percent", () => {
  const strip = normalizeTickerStripPayload({ symbol: "SPY", lastPrice: 771.12, changePct: -0.03 });
  const merged = mergeTickerStripLivePrice(strip, 772.5);
  assert.equal(merged.lastPrice, 772.5);
  assert.equal(merged.changePct, -0.03);
});

test("live price merge ignores junk ticks and no-op updates", () => {
  const strip = normalizeTickerStripPayload({ symbol: "SPY", lastPrice: 771.12, closePrice: 771.35 });
  assert.equal(mergeTickerStripLivePrice(strip, null), strip);
  assert.equal(mergeTickerStripLivePrice(strip, 0), strip);
  assert.equal(mergeTickerStripLivePrice(strip, -5), strip);
  const same = mergeTickerStripLivePrice(strip, 771.12);
  assert.equal(mergeTickerStripLivePrice(same, 771.12), same);
  assert.equal(mergeTickerStripLivePrice(null, 771.12), null);
});

test("a down day never renders a bullish label", () => {
  assert.equal(tickerStripBias(-3.16).label, "BEAR");
  assert.equal(tickerStripBias(-0.01).label, "BEAR");
  assert.equal(tickerStripBias(-3.16).tone, "bear");
});
