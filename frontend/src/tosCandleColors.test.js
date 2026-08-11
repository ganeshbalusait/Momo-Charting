import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { calculateTosCandlePaints } from "./tosCandleColors.js";

function bar(time, close, { open = close, high = close + 1, low = close - 1 } = {}) {
  return { time, open, high, low, close, volume: 1_000 };
}

function studyColors(bars) {
  return calculateTosCandlePaints(bars).map(({ color }) => color);
}

describe("shared_CoolCandles_Momo_New24 translation", () => {
  it("uses directional momentum tones when the source is not squeezed", () => {
    const rising = Array.from({ length: 80 }, (_, index) => bar(index, 100 + index));
    const falling = Array.from({ length: 80 }, (_, index) => bar(index, 200 - index));

    assert.equal(studyColors(rising).at(-1), "#00ffff");
    assert.equal(studyColors(falling).at(-1), "#ff00ff");
  });

  it("uses the source's white high-squeeze candle formatting", () => {
    const flat = Array.from({ length: 80 }, (_, index) => bar(index, 100, {
      open: 100,
      high: 100.02,
      low: 99.98,
    }));
    const colors = studyColors(flat);

    assert.equal(colors.at(-1), "#ffffff");
    assert.ok(colors.includes("#ffffff"));
  });

  it("uses the TOS reference's cyan, magenta, teal, and purple momentum tones outside a squeeze", () => {
    const pulse = (slope) => Array.from({ length: 140 }, (_, index) => {
      const close = 100 + Math.sin(index / 2) * 0.01 + index * slope;
      return bar(index, close, { open: close - 0.01, high: close + 0.01, low: close - 0.01 });
    });
    const rising = studyColors(pulse(0.005));
    const falling = studyColors(pulse(-0.005));

    assert.ok(rising.includes("#00ffff"));
    assert.ok(rising.includes("#0d6366"));
    assert.ok(rising.includes("#660066"));
    assert.ok(falling.includes("#ff00ff"));
    assert.ok(falling.includes("#0d6366"));
    assert.ok(!rising.includes("#ffffff"));
    assert.ok(!falling.includes("#ffffff"));
  });

  it("paints body, border, and wick in one colour like the TradingView barcolor()", () => {
    const bullishBodiesInBearTrend = Array.from({ length: 80 }, (_, index) => {
      const close = 200 - index;
      return bar(index, close, { open: close - 0.25, high: close + 0.5, low: close - 0.5 });
    });
    const bearishBodiesInBullTrend = Array.from({ length: 80 }, (_, index) => {
      const close = 100 + index;
      return bar(index, close, { open: close + 0.25, high: close + 0.5, low: close - 0.5 });
    });

    assert.deepEqual(calculateTosCandlePaints(bullishBodiesInBearTrend).at(-1), {
      color: "#ff00ff",
      borderColor: "#ff00ff",
      wickColor: "#ff00ff",
    });
    assert.deepEqual(calculateTosCandlePaints(bearishBodiesInBullTrend).at(-1), {
      color: "#00ffff",
      borderColor: "#00ffff",
      wickColor: "#00ffff",
    });
  });

  it("returns one fully styled candle per source candle", () => {
    const painted = calculateTosCandlePaints([bar(1, 10), bar(2, 11)]);

    assert.deepEqual(painted[0], { color: "#0d6366", borderColor: "#0d6366", wickColor: "#0d6366" });
    assert.equal(painted.length, 2);
  });
});
