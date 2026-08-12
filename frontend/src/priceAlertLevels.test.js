import test from "node:test";
import assert from "node:assert/strict";

import {
  findPriceAlertLevel,
  priceAlertChipText,
  priceAlertLevelTitle,
} from "./priceAlertLevels.js";

// Prices sit 1.00 apart and the projection is 10px per dollar, so a pointer at
// 105 is 5px from 222.00 and 5px from 223.00 in coordinate space.
const priceToCoordinate = (price) => (230 - Number(price)) * 10;

const DEFINITIONS = [
  { key: "previous-day-high", title: "pDH", price: 222.27 },
  { key: "oi-wall-3-217.5", title: "85k 10/16", price: 217.5 },
  { key: "live-price", title: "LIVE NVDA 222.30", price: 222.3 },
  { key: "price-alert-abc", title: "ALERT >= 222.24", price: 222.24 },
];

test("snaps to a named level and inherits its exact price", () => {
  const level = findPriceAlertLevel(DEFINITIONS, priceToCoordinate(222.27) + 3, priceToCoordinate);
  assert.deepEqual({ label: level.label, price: level.price }, { label: "pDH", price: 222.27 });
});

test("renames OI walls to the MomoX High OI chip instead of the size/expiry axis title", () => {
  const level = findPriceAlertLevel(DEFINITIONS, priceToCoordinate(217.5), priceToCoordinate);
  assert.deepEqual({ label: level.label, price: level.price }, { label: "High OI", price: 217.5 });
  assert.equal(priceAlertLevelTitle({ key: "oi-wall-0-100", title: "12k 8/7" }), "High OI");
});

test("returns null in open space so a free-price alert stays exactly where clicked", () => {
  assert.equal(findPriceAlertLevel(DEFINITIONS, priceToCoordinate(225), priceToCoordinate), null);
});

test("never snaps to the live price or to an existing alert", () => {
  // Both sit within the tolerance of this pointer; only they are nearby, so a
  // leaked snap target would surface here as a non-null result.
  assert.equal(findPriceAlertLevel(
    [DEFINITIONS[2], DEFINITIONS[3]],
    priceToCoordinate(222.27),
    priceToCoordinate,
  ), null);
});

test("prefers the closest level when two are inside the tolerance", () => {
  const crowded = [
    { key: "a", title: "pDH", price: 222.2 },
    { key: "b", title: "pWH", price: 222.3 },
  ];
  assert.equal(findPriceAlertLevel(crowded, priceToCoordinate(222.28), priceToCoordinate).label, "pWH");
});

test("survives a series that cannot project a price", () => {
  assert.equal(findPriceAlertLevel(DEFINITIONS, 10, () => null), null);
  assert.equal(findPriceAlertLevel(DEFINITIONS, Number.NaN, priceToCoordinate), null);
  assert.equal(findPriceAlertLevel(DEFINITIONS, 10, undefined), null);
});

test("writes MomoX chip text with and without a level name", () => {
  assert.equal(priceAlertChipText({ price: 222.24, enabled: true }), "Alert @ 222.24");
  assert.equal(
    priceAlertChipText({ price: 222.27, levelLabel: "pDH", enabled: true }),
    "Alert @ pDH 222.27",
  );
  assert.equal(
    priceAlertChipText({ price: 217.5, levelLabel: "High OI", enabled: true }),
    "Alert @ High OI 217.50",
  );
});

test("keeps the chip shape for paused and triggered alerts", () => {
  assert.equal(priceAlertChipText({ price: 222.24, enabled: false }), "Paused @ 222.24");
  assert.equal(
    priceAlertChipText({ price: 222.24, status: "triggered", enabled: false }),
    "Triggered @ 222.24",
  );
});
