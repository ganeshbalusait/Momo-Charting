import assert from "node:assert/strict";
import test from "node:test";
import {
  guardLightweightChartSeriesTree,
  normalizeLightweightChartSeriesData,
} from "./chartSeriesData.js";

test("normalizes chart points into strictly ascending unique timestamps", () => {
  assert.deepEqual(
    normalizeLightweightChartSeriesData([
      { time: 300, value: 3 },
      { time: 100, value: 1 },
      { time: 300, value: 4 },
      { time: "bad", value: 9 },
      { time: 200.9, value: 2 },
    ]),
    [
      { time: 100, value: 1 },
      { time: 200, value: 2 },
      { time: 300, value: 4 },
    ],
  );
});

test("guards every nested series before it reaches Lightweight Charts", () => {
  const received = [];
  const tree = {
    candle: { setData: (data) => received.push(data) },
    studies: {
      momentum: { setData: (data) => received.push(data) },
    },
  };

  guardLightweightChartSeriesTree(tree);
  tree.candle.setData([{ time: 2, value: 2 }, { time: 1, value: 1 }, { time: 2, value: 3 }]);
  tree.studies.momentum.setData([{ time: 5, value: 5 }, { time: 5, value: 6 }]);

  assert.deepEqual(received, [
    [{ time: 1, value: 1 }, { time: 2, value: 3 }],
    [{ time: 5, value: 6 }],
  ]);
});

test("clears only the rejected series instead of crashing the workspace", () => {
  const received = [];
  const tree = {
    optionalStudy: {
      setData: (data) => {
        received.push(data);
        if (data.length) throw new Error("provider rejected series");
      },
    },
  };
  const previousConsoleError = console.error;
  console.error = () => {};
  try {
    guardLightweightChartSeriesTree(tree);
    assert.doesNotThrow(() => tree.optionalStudy.setData([{ time: 1, value: 2 }]));
  } finally {
    console.error = previousConsoleError;
  }
  assert.deepEqual(received, [[{ time: 1, value: 2 }], []]);
});
