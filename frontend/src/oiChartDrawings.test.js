import test from "node:test";
import assert from "node:assert/strict";
import {
  chartDrawingScopeKey,
  drawingHasDistinctPoints,
  drawingMeasurement,
  fibonacciRetracementPrices,
  nearestDrawingMagnetPoint,
  normalizeChartDrawings,
  translateDrawingPoints,
} from "./oiChartDrawings.js";

test("keeps drawings isolated by ticker and timeframe", () => {
  assert.equal(chartDrawingScopeKey(" aapl ", "5m"), "AAPL:5m");
  assert.notEqual(chartDrawingScopeKey("AAPL", "5m"), chartDrawingScopeKey("AAPL", "1h"));
});

test("normalizes supported chart drawings and rejects malformed points", () => {
  const drawings = normalizeChartDrawings([
    { id: "one", type: "trend", points: [{ time: 10, price: 100 }, { time: 20, price: 105 }], color: "#abcdef" },
    { id: "bad", type: "trend", points: [{ time: "no", price: 2 }] },
    { id: "unsupported", type: "ellipse", points: [{ time: 1, price: 1 }, { time: 2, price: 2 }] },
  ]);
  assert.equal(drawings.length, 1);
  assert.equal(drawings[0].id, "one");
  assert.equal(drawings[0].color, "#abcdef");
});

test("preserves every supported TradingView-style drawing tool", () => {
  const twoPoints = [{ time: 1000, price: 100 }, { time: 1300, price: 105 }];
  const drawings = normalizeChartDrawings([
    { id: "trend", type: "trend", points: twoPoints },
    { id: "horizontal", type: "horizontal", points: [twoPoints[0]] },
    { id: "fib", type: "fib", points: twoPoints },
    { id: "rectangle", type: "rectangle", points: twoPoints },
    { id: "brush", type: "brush", points: twoPoints },
    { id: "text", type: "text", points: [twoPoints[0]], text: "Breakout" },
    { id: "measure", type: "measure", points: twoPoints },
  ]);
  assert.deepEqual(drawings.map(({ type }) => type), [
    "trend",
    "horizontal",
    "fib",
    "rectangle",
    "brush",
    "text",
    "measure",
  ]);
  assert.equal(drawings.find(({ type }) => type === "text")?.text, "Breakout");
});

test("calculates TradingView-style price, percent, and bar measurements", () => {
  const measurement = drawingMeasurement([
    { time: 1000, price: 100 },
    { time: 1600, price: 105 },
  ], 5);
  assert.equal(measurement.priceDelta, 5);
  assert.equal(measurement.percentDelta, 5);
  assert.equal(measurement.bars, 2);
});

test("counts displayed candles instead of overnight clock time for measurements", () => {
  const measurement = drawingMeasurement([
    { time: 1000, price: 100 },
    { time: 88_000, price: 102 },
  ], 5, [
    { time: 1000 },
    { time: 1300 },
    { time: 88_000 },
  ]);
  assert.equal(measurement.bars, 2);
  assert.equal(measurement.priceDelta, 2);
});

test("builds Fibonacci retracement prices in both drawing directions", () => {
  assert.deepEqual(
    fibonacciRetracementPrices(
      [{ price: 100 }, { price: 200 }],
      [0, 0.5, 1],
    ),
    [
      { level: 0, price: 100 },
      { level: 0.5, price: 150 },
      { level: 1, price: 200 },
    ],
  );
  assert.deepEqual(
    fibonacciRetracementPrices(
      [{ price: 200 }, { price: 100 }],
      [0, 0.5, 1],
    ),
    [
      { level: 0, price: 200 },
      { level: 0.5, price: 150 },
      { level: 1, price: 100 },
    ],
  );
});

test("recognizes completed two-point drawings and translates every anchor", () => {
  assert.equal(drawingHasDistinctPoints([
    { time: 1000, price: 100 },
    { time: 1000, price: 100 },
  ]), false);
  assert.equal(drawingHasDistinctPoints([
    { time: 1000, price: 100 },
    { time: 1300, price: 105 },
  ]), true);
  assert.deepEqual(translateDrawingPoints([
    { time: 1000, price: 100 },
    { time: 1300, price: 105 },
  ], { time: 1000, price: 100 }, { time: 1600, price: 110 }), [
    { time: 1600, price: 110 },
    { time: 1900, price: 115 },
  ]);
  assert.deepEqual(translateDrawingPoints([
    { time: 1000, price: 100 },
    { time: 1100, price: 101 },
    { time: 1200, price: 102 },
  ], { time: 1000, price: 100 }, { time: 1300, price: 103 }), [
    { time: 1300, price: 103 },
    { time: 1400, price: 104 },
    { time: 1500, price: 105 },
  ]);
});

test("magnet snaps only when the cursor is actually near a candle OHLC point", () => {
  const bars = [
    { time: 1000, open: 100, high: 105, low: 98, close: 103 },
    { time: 1300, open: 103, high: 108, low: 102, close: 107 },
  ];
  const common = {
    bars,
    timeToCoordinate: (time) => (time - 1000) / 10,
    priceToCoordinate: (price) => (110 - price) * 5,
    maxDistancePx: 12,
  };
  assert.deepEqual(nearestDrawingMagnetPoint({
    ...common,
    cursorX: 31,
    cursorY: 16,
  }), {
    time: 1300,
    price: 107,
    distance: Math.sqrt(2),
  });
  assert.equal(nearestDrawingMagnetPoint({
    ...common,
    cursorX: 100,
    cursorY: 16,
  }), null);
  assert.equal(nearestDrawingMagnetPoint({
    ...common,
    cursorX: 31,
    cursorY: 100,
  }), null);
});
