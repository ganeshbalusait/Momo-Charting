import assert from "node:assert/strict";
import test from "node:test";

import { reconcileLiveMtfSignals } from "./mtfLiveSignalState.js";

const context = (overrides = {}) => ({
  family: "4x8",
  color: "yellow",
  timeframe: "1H",
  confirmationTimeframe: "2H",
  signalMinutes: 60,
  confirmationMinutes: 120,
  fastLength: 4,
  slowLength: 8,
  candleTimestamp: 1_785_509_400,
  confirmationCandleTimestamp: 1_785_509_400,
  baseFastEma: 99,
  baseSlowEma: 100,
  higherBaseFastEma: 99,
  higherBaseSlowEma: 100,
  aggregationAnchor: "TOS_START_DAY_ET",
  ...overrides,
});

test("creates CALL and PUT from the current incomplete candle", () => {
  const call = reconcileLiveMtfSignals([], [context({ higherBaseFastEma: 100, higherBaseSlowEma: 99 })], {
    time: 1_785_510_000,
    price: 110,
  });
  const put = reconcileLiveMtfSignals([], [context({
    baseFastEma: 101,
    baseSlowEma: 100,
    higherBaseFastEma: 99,
    higherBaseSlowEma: 100,
  })], {
    time: 1_785_510_000,
    price: 90,
  });

  assert.equal(call[0].label, "CALL1H");
  assert.equal(put[0].label, "PUT1H");
  assert.equal(call[0].isLiveSignal, true);
});

test("relabels C to CALL and P to PUT without moving the marker", () => {
  const cContext = context({ higherBaseFastEma: 90, higherBaseSlowEma: 100 });
  const compactCall = reconcileLiveMtfSignals([], [cContext], { time: 1_785_510_000, price: 110 });
  const confirmedCall = reconcileLiveMtfSignals(compactCall, [{
    ...cContext,
    higherBaseFastEma: 100,
    higherBaseSlowEma: 99,
  }], { time: 1_785_510_060, price: 110 });

  const pContext = context({
    baseFastEma: 101,
    baseSlowEma: 100,
    higherBaseFastEma: 110,
    higherBaseSlowEma: 100,
  });
  const compactPut = reconcileLiveMtfSignals([], [pContext], { time: 1_785_510_000, price: 90 });
  const confirmedPut = reconcileLiveMtfSignals(compactPut, [{
    ...pContext,
    higherBaseFastEma: 99,
    higherBaseSlowEma: 100,
  }], { time: 1_785_510_060, price: 90 });

  assert.equal(compactCall[0].label, "C1H");
  assert.equal(confirmedCall[0].label, "CALL1H");
  assert.equal(compactCall[0].time, confirmedCall[0].time);
  assert.equal(compactPut[0].label, "P1H");
  assert.equal(confirmedPut[0].label, "PUT1H");
  assert.equal(compactPut[0].time, confirmedPut[0].time);
});

test("removes a live marker when the forming crossover reverses", () => {
  const active = reconcileLiveMtfSignals([], [context()], { time: 1_785_510_000, price: 110 });
  const reversed = reconcileLiveMtfSignals(active, [context()], { time: 1_785_510_060, price: 99 });

  assert.equal(active.length, 1);
  assert.deepEqual(reversed, []);
});

test("does not duplicate repeated updates for one secondary candle", () => {
  const first = reconcileLiveMtfSignals([], [context()], { time: 1_785_510_000, price: 110 });
  const second = reconcileLiveMtfSignals(first, [context()], { time: 1_785_510_060, price: 111 });
  const third = reconcileLiveMtfSignals(second, [context()], { time: 1_785_510_120, price: 112 });

  assert.equal(third.length, 1);
  assert.equal(third[0].candleTimestamp, context().candleTimestamp);
});

test("finalizes the last live state at candle close and evaluates the next candle", () => {
  const current = reconcileLiveMtfSignals([], [context()], { time: 1_785_510_000, price: 110 });
  const afterClose = reconcileLiveMtfSignals(current, [context()], {
    time: context().candleTimestamp + 60 * 60,
    price: 110,
  });
  const nextContext = context({
    candleTimestamp: context().candleTimestamp + 60 * 60,
    baseFastEma: 101,
    baseSlowEma: 100,
    higherBaseFastEma: 99,
    higherBaseSlowEma: 100,
  });
  const nextCandle = reconcileLiveMtfSignals(afterClose, [nextContext], {
    time: nextContext.candleTimestamp + 60,
    price: 90,
  });

  assert.equal(afterClose[0].isCandleClosed, true);
  assert.deepEqual(nextCandle.map((signal) => signal.label), ["CALL1H", "PUT1H"]);
});

test("preserves the REST-managed price EMA reclaim trial between snapshots", () => {
  const reclaim = {
    family: "4x8",
    timeframe: "1H",
    candleTimestamp: context().candleTimestamp,
    time: context().candleTimestamp,
    label: "CALL1H",
    direction: "CALL",
    isLiveSignal: true,
    streamManaged: false,
    signalBasis: "price_ema_reclaim",
  };

  const reconciled = reconcileLiveMtfSignals([reclaim], [context()], {
    time: context().candleTimestamp + 120,
    price: 99,
  });

  assert.equal(reconciled.length, 1);
  assert.equal(reconciled[0].signalBasis, "price_ema_reclaim");
});
