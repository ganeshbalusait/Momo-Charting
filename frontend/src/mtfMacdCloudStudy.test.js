import assert from "node:assert/strict";
import { describe, it } from "node:test";
import { calculateMtfMacdTrendClouds } from "./mtfMacdCloudStudy.js";

const easternSessionFormatter = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function only15MinuteCloud(options = {}) {
  return {
    mtfMacdCloudLimitRecentSessions: false,
    mtfMacdCloudCurrent: false,
    mtfMacdCloud15m: true,
    mtfMacdCloud30m: false,
    mtfMacdCloud1h: false,
    mtfMacdCloud2h: false,
    mtfMacdCloud4h: false,
    ...options,
  };
}

describe("TOS MTF MACD 6/12/8 trend cloud", () => {
  it("keeps an intrabar higher-timeframe cross on its exact 5-minute candle", () => {
    const start = Date.parse("2026-07-20T04:00:00-04:00") / 1000;
    const bars = Array.from({ length: 300 }, (_, index) => {
      const close = 100 + index * 0.03 + Math.sin(index / 2) * 2;
      return {
        time: start + index * 300,
        open: close - 0.1,
        high: close + 0.3,
        low: close - 0.3,
        close,
        volume: 1_000,
      };
    });

    const clouds = calculateMtfMacdTrendClouds(
      bars,
      5,
      easternSessionFormatter,
      only15MinuteCloud(),
    );
    const secondChildCross = clouds.find((cloud) => cloud.startTime % 900 === 300);

    assert.ok(secondChildCross, "expected a live cross during a forming 15-minute candle");
    assert.equal(secondChildCross.endTime - secondChildCross.startTime, 300);
    assert.equal(secondChildCross.timeframe, "15m");
    assert.equal(secondChildCross.family, "mtf-macd");
  });

  it("uses the partial higher-timeframe low or high as the TOS cloud anchor", () => {
    const start = Date.parse("2026-07-20T04:00:00-04:00") / 1000;
    const bars = Array.from({ length: 300 }, (_, index) => {
      const close = 100 + index * 0.03 + Math.sin(index / 2) * 2;
      return {
        time: start + index * 300,
        open: close - 0.1,
        high: close + 0.3,
        low: close - 0.3,
        close,
        volume: 1_000,
      };
    });
    const clouds = calculateMtfMacdTrendClouds(
      bars,
      5,
      easternSessionFormatter,
      only15MinuteCloud(),
    );
    const cloud = clouds.find((candidate) => candidate.startTime % 900 === 300);
    const bucketStart = Math.floor(cloud.startTime / 900) * 900;
    const partialBars = bars.filter((bar) => bar.time >= bucketStart && bar.time <= cloud.startTime);
    const expectedAnchor = cloud.tone === "bull"
      ? Math.min(...partialBars.map((bar) => bar.low))
      : Math.max(...partialBars.map((bar) => bar.high));

    assert.equal(cloud.anchor, expectedAnchor);
  });
});
