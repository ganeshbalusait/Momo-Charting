import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { calculateRelativeVolumeCandleStudy } from "./relativeVolumeStudy.js";

function bar(time, volume, { high = 10, low = 0, close = 8 } = {}) {
  return { time, volume, open: 5, high, low, close };
}

describe("shared_RelVol_Candles_v324 translation", () => {
  it("keeps a sub-threshold candle normal and does not print an RVOL value", () => {
    const bars = [bar(1, 100), bar(2, 100), bar(3, 110)];
    const result = calculateRelativeVolumeCandleStudy(bars, {
      relVolLength: 3,
      relVolNumDev: 1.5,
      relVolHighlightAverage: false,
      relVolHighlightRelative: false,
    });

    assert.deepEqual(result.candleStyles[2], {});
    assert.deepEqual(result.markers, []);
  });

  it("border-highlights an above-average-volume candle like the TOS AddChart overlays", () => {
    const bars = [bar(1, 100), bar(2, 100), bar(3, 110)];
    const result = calculateRelativeVolumeCandleStudy(bars, {
      relVolLength: 3,
      relVolNumDev: 1.5,
    });

    // volume 110 >= its rolling average, buying dominant -> cyan border/wick.
    assert.deepEqual(result.candleStyles[2], { borderColor: "#00ffff", wickColor: "#00ffff" });
    assert.deepEqual(result.markers, []);
  });

  it("border-highlights a bearish RelVol-threshold candle in magenta", () => {
    const bars = [bar(1, 100), bar(2, 100), bar(3, 100), bar(4, 100), bar(5, 400, { close: 2 })];
    const result = calculateRelativeVolumeCandleStudy(bars, {
      relVolLength: 5,
      relVolNumDev: 1.5,
      relVolHighlightAverage: false,
    });

    assert.deepEqual(result.candleStyles[4], { borderColor: "#ff00ff", wickColor: "#ff00ff" });
  });

  it("places a cyan one-decimal value below a bullish threshold candle", () => {
    const bars = [bar(1, 100), bar(2, 100), bar(3, 100), bar(4, 100), bar(5, 400)];
    const result = calculateRelativeVolumeCandleStudy(bars, {
      relVolLength: 5,
      relVolNumDev: 1.5,
    });

    assert.equal(result.markers.length, 1);
    assert.equal(result.markers[0].position, "belowBar");
    assert.equal(result.markers[0].color, "#00ffff");
    assert.equal(result.markers[0].text, "2.0");
  });

  it("places a magenta value above a bearish threshold candle", () => {
    const bars = [bar(1, 100), bar(2, 100), bar(3, 100), bar(4, 100), bar(5, 400, { close: 2 })];
    const result = calculateRelativeVolumeCandleStudy(bars, {
      relVolLength: 5,
      relVolNumDev: 1.5,
    });

    assert.equal(result.markers[0].position, "aboveBar");
    assert.equal(result.markers[0].color, "#ff00ff");
    assert.equal(result.markers[0].text, "2.0");
  });

  it("keeps value plots active without restyling the candle", () => {
    const bars = [bar(1, 100), bar(2, 100), bar(3, 100), bar(4, 100), bar(5, 400)];
    const result = calculateRelativeVolumeCandleStudy(bars, {
      relVolLength: 5,
      relVolNumDev: 1.5,
      relVolHighlightAverage: false,
      relVolHighlightRelative: false,
    });

    assert.deepEqual(result.candleStyles[4], {});
    assert.equal(result.markers.length, 1);
  });
});
