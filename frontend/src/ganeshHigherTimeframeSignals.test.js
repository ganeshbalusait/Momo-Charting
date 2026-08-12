import test from "node:test";
import assert from "node:assert/strict";

import {
  GANESH_HIGHER_TIMEFRAMES,
  GANESH_MACD_TIMEFRAMES,
  __ganeshSignalInternals,
  buildGaneshPrimaryBars,
  calculateGaneshHigherTimeframeSignals,
  ganeshSignalVisualSignature,
  projectGaneshSignalsToChart,
} from "./ganeshHigherTimeframeSignals.js";
import { chartAggregationBucketTime } from "./chartAggregation.js";

const unix = (iso) => Math.floor(new Date(iso).getTime() / 1000);

function fallingDailyHistory(count = 120, start = "2025-09-01") {
  const startDate = new Date(`${start}T00:00:00Z`);
  return Array.from({ length: count }, (_, index) => {
    const date = new Date(startDate.getTime() + index * 86_400_000);
    const dateKey = date.toISOString().slice(0, 10);
    const close = 150 - index;
    return {
      time: Math.floor(date.getTime() / 1000) + 20 * 3600,
      date: dateKey,
      open: close,
      high: close + 1,
      low: close - 1,
      close,
      volume: 100,
    };
  });
}

function flatTradingDailyHistory(count = 2000, end = "2026-07-30", close = 3_000) {
  const sessions = [];
  for (let cursor = new Date(`${end}T00:00:00Z`); sessions.length < count;) {
    if (![0, 6].includes(cursor.getUTCDay())) sessions.unshift(new Date(cursor));
    cursor = new Date(cursor.getTime() - 86_400_000);
  }
  return sessions.map((date) => {
    const dateKey = date.toISOString().slice(0, 10);
    return {
      time: unix(`${dateKey}T20:00:00Z`),
      date: dateKey,
      open: close,
      high: close + 1,
      low: close - 1,
      close,
      volume: 100,
    };
  });
}

function allSignalsOff() {
  const result = {};
  ["ganesh48", "ganesh920", "ganeshMacd"].forEach((family) => {
    GANESH_HIGHER_TIMEFRAMES.forEach(({ key }) => {
      result[`${family}ShowCall${key}`] = false;
      result[`${family}ShowPut${key}`] = false;
    });
  });
  return result;
}

function minuteBars(closes, date = "2026-03-02") {
  return closes.map((close, index) => ({
    time: unix(`${date}T14:${String(30 + index).padStart(2, "0")}:00Z`),
    open: close - 1,
    high: close + 1,
    low: close - 2,
    close,
    volume: 100 + index,
  }));
}

test("uses Eastern trading dates across the UTC-midnight boundary", () => {
  assert.equal(
    __ganeshSignalInternals.dateKeyFromTimestamp(unix("2026-07-31T23:55:00Z")),
    "2026-07-31",
  );
  assert.equal(
    __ganeshSignalInternals.dateKeyFromTimestamp(unix("2026-08-01T00:15:00Z")),
    "2026-07-31",
  );
});

test("uses only the RTH portion of a 4H candle for DAY-or-higher studies", () => {
  const dailyClose = new Map([["2026-07-30", 235.5]]);
  const source = (iso, close) => __ganeshSignalInternals.higherTimeframeSourceBar({
    time: unix(iso),
    open: close,
    high: close,
    low: close,
    close,
  }, dailyClose, 240);

  assert.equal(source("2026-07-30T05:00:00Z", 234), null, "01:00 ET is extended hours");
  assert.equal(source("2026-07-30T13:00:00Z", 238.2).sourceClose, 238.2, "09:00 ET closes before the RTH settlement");
  assert.equal(source("2026-07-30T17:00:00Z", 254.54).sourceClose, 235.5, "13:00 ET must not import the after-hours earnings jump");
  assert.equal(source("2026-07-30T21:00:00Z", 258), null, "17:00 ET is extended hours");
});

test("the literal TOS MACD study evaluates D through Monthly", () => {
  assert.deepEqual(GANESH_MACD_TIMEFRAMES.map(({ key }) => key), ["D", "2D", "3D", "4D", "W", "M"]);
});

test("right-aligns N-day MACD groups over trading sessions, not calendar gaps", () => {
  const sessions = ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"];
  assert.equal(__ganeshSignalInternals.finalizedTimeframeGroupKey("2D", "2026-07-31", sessions), "2D-R0");
  assert.equal(__ganeshSignalInternals.finalizedTimeframeGroupKey("2D", "2026-07-30", sessions), "2D-R0");
  assert.equal(__ganeshSignalInternals.finalizedTimeframeGroupKey("2D", "2026-07-29", sessions), "2D-R1");
  assert.equal(__ganeshSignalInternals.finalizedTimeframeGroupKey("3D", "2026-07-29", sessions), "3D-R0");
  assert.equal(__ganeshSignalInternals.finalizedTimeframeGroupKey("3D", "2026-07-28", sessions), "3D-R1");
});

test("4x8 emits the confirmed Daily CALL with the source label and color family", () => {
  const options = allSignalsOff();
  options.ganesh48ShowCallD = true;
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars: minuteBars([150, 180, 210, 250, 1_000_000], "2026-07-31"),
    dailyBars: flatTradingDailyHistory(),
    options,
  });
  assert.deepEqual(signals.map(({ family, timeframe, direction, label }) => ({
    family, timeframe, direction, label,
  })), [{ family: "ganesh48", timeframe: "D", direction: "CALL", label: "CALLD" }]);
});

test("9x20 applies the first-chart-bar gate and the exact Daily ATR multiplier", () => {
  const options = allSignalsOff();
  options.ganesh920ShowCallD = true;
  const bars = minuteBars([250, 40, 260, 1_000_000]);
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars: bars,
    dailyBars: fallingDailyHistory(),
    options,
  });
  assert.equal(signals.length, 1);
  assert.equal(signals[0].time, bars[0].time);
  assert.equal(signals[0].label, "CALLD");
  assert.equal(signals[0].atrMultiplier, 0.2);
});

test("MACD 6/12/8 emits every secondary crossover on the actual premarket 4H candle", () => {
  const options = allSignalsOff();
  GANESH_MACD_TIMEFRAMES.forEach(({ key }) => {
    options[`ganeshMacdShowCall${key}`] = true;
  });
  const dailySessions = [];
  let sessionIndex = 0;
  for (
    let cursor = new Date("2017-01-02T00:00:00Z");
    cursor <= new Date("2026-07-31T00:00:00Z");
    cursor = new Date(cursor.getTime() + 86_400_000)
  ) {
    if ([0, 6].includes(cursor.getUTCDay())) continue;
    const date = cursor.toISOString().slice(0, 10);
    const close = 1_000_000 - (sessionIndex ** 2) * 0.1;
    sessionIndex += 1;
    dailySessions.push({
      time: unix(`${date}T20:00:00Z`),
      date,
      open: close,
      high: close + 1,
      low: close - 1,
      close,
    });
  }
  const warm = dailySessions.slice(0, -1);
  const currentDay = [{
    ...dailySessions.at(-1),
    open: 10_000_000,
    high: 10_000_001,
    low: 9_999_999,
    close: 10_000_000,
  }];
  const sourceBar = (hour, close) => ({
    time: unix(`2026-07-31T${hour}:00:00Z`),
    open: close,
    high: close + 1,
    low: close - 1,
    close,
  });
  const intradayBars = [
    sourceBar("05", 1), // 01:00 ET: secondary MACD remains below its signal.
    sourceBar("09", 10_000_000), // 05:00 ET: all six secondary MACDs cross above.
    sourceBar("13", 10_000_000),
    sourceBar("17", 10_000_000),
  ];
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars,
    dailyBars: [...warm, ...currentDay],
    aggregationMinutes: 240,
    options,
  }).filter(({ family }) => family === "ganeshMacd");

  const expectedLabels = [
    "MACD-D", "MACD-2D", "MACD-3D", "MACD-4D", "MACD-W", "MACD-M",
  ];
  assert.deepEqual(signals.map(({ label }) => label), [...expectedLabels, ...expectedLabels]);
  assert.deepEqual([...new Set(signals.map(({ time }) => time))], [
    sourceBar("05", 1).time,
    sourceBar("09", 10_000_000).time,
  ]);
  assert.ok(signals.every(({ stateSnapshot, sourceEvent }) => !stateSnapshot && sourceEvent));
});

test("back-paints a completed secondary cross and preserves the TOS daily-latch ordering", () => {
  const options = allSignalsOff();
  options.ganeshMacdShowCallD = true;
  const warm = fallingDailyHistory(120, "2024-01-01").map((bar, index) => {
    const close = 1_000 - index;
    return { ...bar, open: close, high: close + 1, low: close - 1, close };
  });
  const dailyBars = [
    ...warm,
    { time: unix("2026-03-02T21:00:00Z"), date: "2026-03-02", open: 100_000, high: 100_001, low: 99_999, close: 100_000 },
    { time: unix("2026-03-03T21:00:00Z"), date: "2026-03-03", open: 100_000, high: 100_001, low: 99_999, close: 100_000 },
  ];
  const bar = (iso, close) => ({
    time: unix(iso),
    open: close,
    high: close + 1,
    low: close - 1,
    close,
    volume: 100,
  });
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars: [
      bar("2026-03-02T06:00:00Z", 100), // 01:00 ET
      bar("2026-03-02T10:00:00Z", 100), // 05:00 ET
      bar("2026-03-02T14:00:00Z", 100),
      bar("2026-03-02T18:00:00Z", 100),
    ],
    dailyBars,
    aggregationMinutes: 240,
    options,
  });

  assert.deepEqual(
    signals.filter(({ family }) => family === "ganeshMacd").map(({ time }) => time),
    [unix("2026-03-02T06:00:00Z"), unix("2026-03-02T10:00:00Z")],
    "the completed cross is true on the whole secondary candle; print reads the prior latch before the new-day reset",
  );
});

test("MACD back-paints a bearish Daily cross onto the first two primary candles", () => {
  const options = allSignalsOff();
  options.ganeshMacdShowPutD = true;
  const dailyBars = fallingDailyHistory(120, "2024-01-01").map((bar, index) => {
    const close = 1_000 + index ** 2;
    return { ...bar, open: close, high: close + 1, low: close - 1, close };
  });
  dailyBars.push({
    time: unix("2026-03-03T21:00:00Z"),
    date: "2026-03-03",
    open: 1,
    high: 2,
    low: 0,
    close: 1,
  });
  const bar = (iso, close) => ({
    time: unix(iso),
    open: close,
    high: close + 1,
    low: close - 1,
    close,
  });
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars: [
      bar("2026-03-03T06:00:00Z", 1_000_000),
      bar("2026-03-03T10:00:00Z", 1),
      bar("2026-03-03T14:00:00Z", 1),
    ],
    dailyBars,
    aggregationMinutes: 240,
    options,
  }).filter(({ family }) => family === "ganeshMacd");

  assert.deepEqual(signals.map(({ direction, label, time }) => ({ direction, label, time })), [
    { direction: "PUT", label: "MACD-D", time: unix("2026-03-03T06:00:00Z") },
    { direction: "PUT", label: "MACD-D", time: unix("2026-03-03T10:00:00Z") },
  ]);
});

test("self-gates an underwarmed study while background history is loading", () => {
  const options = allSignalsOff();
  options.ganeshMacdShowCallD = true;
  const warm = fallingDailyHistory().map((bar) => ({
    ...bar,
    open: 100,
    high: 101,
    low: 99,
    close: 100,
  }));
  const currentDays = [
    { time: unix("2026-03-02T21:00:00Z"), date: "2026-03-02", open: 100, high: 101, low: 99, close: 100 },
    { time: unix("2026-03-03T21:00:00Z"), date: "2026-03-03", open: 100, high: 1_001, low: 99, close: 1_000 },
  ];
  const intradayBars = [
    { time: unix("2026-03-02T06:00:00Z"), open: 100, high: 101, low: 99, close: 100 },
    { time: unix("2026-03-03T06:00:00Z"), open: 1_000, high: 1_001, low: 999, close: 1_000 },
  ];
  const calculate = (dailyBars) => calculateGaneshHigherTimeframeSignals({
    intradayBars,
    dailyBars,
    aggregationMinutes: 240,
    options,
  });

  assert.deepEqual(calculate([...warm.slice(-10), ...currentDays]), []);
  assert.deepEqual(calculate([...warm, ...currentDays]).map(({ label }) => label), ["MACD-D"]);
});

test("projects a source signal to containing candles on 5m, 15m, and 4h views", () => {
  const source = [{
    key: "ganesh920-D-CALL-source",
    family: "ganesh920",
    timeframe: "D",
    direction: "CALL",
    label: "CALLD",
    time: unix("2026-07-31T14:07:00Z"),
    atrMultiplier: 0.2,
  }];
  const five = projectGaneshSignalsToChart(source, [
    { time: unix("2026-07-31T14:05:00Z"), open: 100, high: 102, low: 99, close: 101 },
    { time: unix("2026-07-31T14:10:00Z"), open: 101, high: 103, low: 100, close: 102 },
  ], 5);
  const fifteen = projectGaneshSignalsToChart(source, [
    { time: unix("2026-07-31T14:00:00Z"), open: 100, high: 103, low: 98, close: 102 },
    { time: unix("2026-07-31T14:15:00Z"), open: 102, high: 104, low: 101, close: 103 },
  ], 15);
  const fourHour = projectGaneshSignalsToChart(source, [
    { time: unix("2026-07-31T13:00:00Z"), open: 99, high: 104, low: 97, close: 103 },
    { time: unix("2026-07-31T17:00:00Z"), open: 103, high: 105, low: 102, close: 104 },
  ], 240);
  assert.equal(five[0].time, unix("2026-07-31T14:05:00Z"));
  assert.equal(fifteen[0].time, unix("2026-07-31T14:00:00Z"));
  assert.equal(fourHour[0].time, unix("2026-07-31T13:00:00Z"));
  assert.ok(five[0].price < 99, "CALL bubble is offset below the candle by primary ATR");
});

test("projects one backend source event onto every supported chart timeframe", () => {
  const sourceTime = unix("2026-07-31T17:07:00Z"); // 13:07 ET
  const source = [{
    key: `ganeshMacd-D-CALL-${sourceTime}`,
    family: "ganeshMacd",
    timeframe: "D",
    direction: "CALL",
    label: "MACD-D",
    time: sourceTime,
    atrMultiplier: 0,
    stateSnapshot: true,
  }];

  [3, 5, 10, 15, 30, 60, 120, 240, 1440, 10080, 43200].forEach((minutes) => {
    const bucketTime = chartAggregationBucketTime(sourceTime, minutes);
    const projected = projectGaneshSignalsToChart(source, [{
      time: bucketTime,
      open: 270,
      high: 272,
      low: 269,
      close: 271,
      volume: 100,
    }], minutes);

    assert.equal(projected.length, 1, `${minutes}m view must keep the backend event`);
    assert.equal(projected[0].sourceTime, sourceTime);
    assert.equal(projected[0].time, bucketTime);
    assert.equal(projected[0].label, "MACD-D");
  });
});

test("does not spill a missing-session source event backward onto the previous candle", () => {
  const source = [{
    family: "ganeshMacd",
    timeframe: "D",
    direction: "CALL",
    label: "MACD-D",
    time: unix("2026-08-01T14:07:00Z"), // Saturday
  }];
  const projected = projectGaneshSignalsToChart(source, [
    { time: unix("2026-07-31T14:05:00Z"), open: 100, high: 102, low: 99, close: 101 },
  ], 5);
  assert.deepEqual(projected, []);
});

test("historical Ganesh changes invalidate the live-render signature", () => {
  const latestTime = unix("2026-07-31T21:00:00Z");
  const historical = {
    key: "ganeshMacd-D-CALL-historical",
    family: "ganeshMacd",
    timeframe: "D",
    direction: "CALL",
    label: "MACD-D",
    sourceTime: unix("2026-07-31T13:00:00Z"),
    time: unix("2026-07-31T13:00:00Z"),
    price: 250.25,
    position: "belowBar",
  };
  const latest = {
    ...historical,
    key: "ganeshMacd-W-CALL-latest",
    timeframe: "W",
    label: "MACD-W",
    sourceTime: latestTime,
    time: latestTime,
    price: 270.5,
  };

  const latestOnly = ganeshSignalVisualSignature([latest]);
  const historyArrived = ganeshSignalVisualSignature([latest, historical]);
  assert.notEqual(historyArrived, latestOnly, "an older signal must force a full overlay render");
  assert.equal(
    ganeshSignalVisualSignature([historical, latest]),
    historyArrived,
    "payload order must not create a false invalidation",
  );
});

test("builds Ganesh signals from selected 4H primary bars without mixed 5m/1m overlap", () => {
  const studyBars = [
    { time: unix("2026-07-30T12:55:00Z"), open: 90, high: 91, low: 89, close: 90, volume: 50 },
    { time: unix("2026-07-31T13:00:00Z"), open: 100, high: 120, low: 99, close: 120, volume: 500 },
  ];
  const liveBars = [
    { time: unix("2026-07-31T13:00:00Z"), open: 100, high: 101, low: 99, close: 100, volume: 10 },
    { time: unix("2026-07-31T14:00:00Z"), open: 100, high: 141, low: 99, close: 140, volume: 20 },
    { time: unix("2026-07-31T16:59:00Z"), open: 140, high: 141, low: 79, close: 80, volume: 30 },
  ];
  const primary = buildGaneshPrimaryBars({ studyBars, liveBars, aggregationMinutes: 240 });
  assert.deepEqual(primary.map(({ time, open, high, low, close, volume }) => ({
    time, open, high, low, close, volume,
  })), [
    {
      time: unix("2026-07-30T09:00:00Z"),
      open: 90,
      high: 91,
      low: 89,
      close: 90,
      volume: 50,
    },
    {
      time: unix("2026-07-31T13:00:00Z"),
      open: 100,
      high: 141,
      low: 79,
      close: 80,
      volume: 60,
    },
  ]);
});

test("removes a 4x8 cross that reverses before the selected primary source candle settles", () => {
  const options = allSignalsOff();
  options.ganesh48ShowCallD = true;
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars: minuteBars([300, 10]),
    dailyBars: fallingDailyHistory(),
    options,
  });
  assert.deepEqual(signals, []);
});

test("source visibility options suppress only the selected study timeframe", () => {
  const options = allSignalsOff();
  const signals = calculateGaneshHigherTimeframeSignals({
    intradayBars: minuteBars([150, 180, 210, 250, 300]),
    dailyBars: fallingDailyHistory(),
    options,
  });
  assert.deepEqual(signals, []);
});
