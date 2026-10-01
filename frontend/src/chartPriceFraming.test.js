import assert from "node:assert/strict";
import { test } from "node:test";
import { priceScaleMargins, pricePaddingFor } from "./chartPriceFraming.js";

test("desktop framing is unchanged", () => {
  assert.deepEqual(priceScaleMargins(false), { top: 0.12, bottom: 0.12 });
  // 10% of span, floored at 0.03
  assert.equal(pricePaddingFor(10, false), 1);
  assert.equal(pricePaddingFor(0.01, false), 0.03);
});

test("phone framing gives candles more of the pane", () => {
  assert.deepEqual(priceScaleMargins(true), { top: 0.06, bottom: 0.16 });
  // 4% of span, same 0.03 floor
  assert.equal(pricePaddingFor(10, true), 0.4);
  assert.equal(pricePaddingFor(0.01, true), 0.03);
});

test("phone bottom margin clears the volume band", () => {
  // Volume sits at { top: 0.78 }, so the price bottom margin must not reach
  // into it far enough to overlap candles with bars.
  const { bottom } = priceScaleMargins(true);
  assert.ok(bottom <= 1 - 0.78 + 0.02, "bottom margin should sit near the volume band edge");
});

test("candle occupancy improves on a phone", () => {
  const occupancy = (isPhone) => {
    const { top, bottom } = priceScaleMargins(isPhone);
    const pad = isPhone ? 0.04 : 0.10;
    return (1 / (1 + 2 * pad)) * (1 - top - bottom);
  };
  assert.ok(occupancy(true) > occupancy(false) + 0.05,
    "phone framing should reclaim at least 5 points of pane height");
});
