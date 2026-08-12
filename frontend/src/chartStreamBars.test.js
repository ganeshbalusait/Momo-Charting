import assert from "node:assert/strict";
import test from "node:test";

import {
  isSchwabTosChartPacket,
  mergeLatestStreamBar,
  reconcileRestBarsWithLiveTail,
  shouldUseEquityTradeForChart,
} from "./chartStreamBars.js";

test("REST reconciliation heals a corrupt same-minute live candle", () => {
  const rest = [{
    time: 120,
    open: 357.39,
    high: 357.39,
    low: 357.30,
    close: 357.35,
    volume: 698,
  }];
  const live = [{
    time: 120,
    open: 335.21,
    high: 357.39,
    low: 335.21,
    close: 357.38,
    volume: 0,
  }];

  assert.deepEqual(reconcileRestBarsWithLiveTail(rest, live), [{
    time: 120,
    open: 357.39,
    high: 357.39,
    low: 357.30,
    close: 357.38,
    volume: 698,
  }]);
});

test("REST reconciliation keeps a strictly newer live candle", () => {
  const rest = [{ time: 120, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
  const newer = { time: 180, open: 12, high: 13, low: 11, close: 12.5, volume: 25 };

  assert.deepEqual(reconcileRestBarsWithLiveTail(rest, [newer]), [...rest, newer]);
});

test("updates the forming candle without copying the full history array", () => {
  const bars = [{ time: 60, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
  const result = mergeLatestStreamBar(bars, { time: 95, close: 12 }, true);

  assert.equal(result.changed, true);
  assert.equal(result.appended, false);
  assert.equal(result.bars, bars);
  assert.deepEqual(result.bars[0], {
    time: 60,
    open: 10,
    high: 12,
    low: 9,
    close: 12,
    volume: 100,
  });
});

test("allocates a new history array only when a new minute is appended", () => {
  const bars = [{ time: 60, open: 10, high: 11, low: 9, close: 10, volume: 100 }];
  const result = mergeLatestStreamBar(bars, {
    time: 120,
    open: 12,
    high: 13,
    low: 11,
    close: 12.5,
    volume: 25,
  });

  assert.equal(result.changed, true);
  assert.equal(result.appended, true);
  assert.notEqual(result.bars, bars);
  assert.equal(bars.length, 1);
  assert.equal(result.bars.length, 2);
});

test("ignores delayed packets older than the newest candle", () => {
  const bars = [{ time: 120, open: 12, high: 13, low: 11, close: 12.5, volume: 25 }];
  const result = mergeLatestStreamBar(bars, { time: 60, close: 9 }, true);

  assert.equal(result.changed, false);
  assert.equal(result.bars, bars);
  assert.equal(result.bars[0].close, 12.5);
});

test("does not let a delayed chart snapshot replace a newer trade close", () => {
  const bars = [{ time: 60, open: 10, high: 12, low: 9, close: 12, volume: 100 }];
  const result = mergeLatestStreamBar(bars, {
    time: 95,
    open: 10,
    high: 15,
    low: 7,
    close: 9.75,
    volume: 140,
  }, false, true);

  assert.equal(result.changed, true);
  assert.equal(result.bars, bars);
  assert.deepEqual(result.bars[0], {
    time: 60,
    open: 10,
    high: 12,
    low: 9,
    close: 12,
    volume: 140,
  });
});

test("uses a Level-1 trade in the same minute as CHART_EQUITY", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 125,
    latestBarTime: 120,
  }), true);
});

test("uses a newer Level-1 minute instead of waiting for a delayed chart packet", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 185,
    latestBarTime: 120,
  }), true);
});

test("rejects an old Level-1 trade timestamp after a newer candle is visible", () => {
  assert.equal(shouldUseEquityTradeForChart({
    equityTime: 119,
    latestBarTime: 120,
  }), false);
});

test("accepts only Schwab/TOS packets as visible chart writers", () => {
  assert.equal(isSchwabTosChartPacket({ data: { source: "schwab" } }), true);
  assert.equal(isSchwabTosChartPacket({ data: { source: "schwab-rest-1s" } }), true);
  assert.equal(isSchwabTosChartPacket({ data: {} }), true);
  assert.equal(isSchwabTosChartPacket({ data: { source: "alpaca:iex" } }), false);
  assert.equal(isSchwabTosChartPacket({ data: { source: "another-provider" } }), false);
});
