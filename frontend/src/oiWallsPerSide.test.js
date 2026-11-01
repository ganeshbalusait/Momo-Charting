import test from "node:test";
import assert from "node:assert/strict";
import { OI_WALLS_PER_SIDE_DEFAULT, OI_WALLS_PER_SIDE_OPTIONS, normalizeOiWallsPerSide } from "./oiWallsPerSide.js";
import { markHighOiWalls } from "./oiChartLevels.js";
import { TosNativeChartPrimitive } from "./tosNativeChartPrimitive.js";

test("walls per side picker offers 3/5/10 and defaults to 10", () => {
  assert.deepEqual([...OI_WALLS_PER_SIDE_OPTIONS], [3, 5, 10]);
  assert.equal(OI_WALLS_PER_SIDE_DEFAULT, 10);
  assert.equal(normalizeOiWallsPerSide("5"), 5);
  assert.equal(normalizeOiWallsPerSide(3), 3);
  assert.equal(normalizeOiWallsPerSide(null), 10);
  assert.equal(normalizeOiWallsPerSide(15), 10);
  assert.equal(normalizeOiWallsPerSide("junk"), 10);
});

test("markHighOiWalls flags only the biggest wall on each side, thicker than the rest", () => {
  const levels = [
    { price: 362.5, side: "C", openInterest: 2100, lineWidth: 3 },
    { price: 365, side: "C", openInterest: 6378, lineWidth: 3 },
    { price: 355, side: "P", openInterest: 2200, lineWidth: 2 },
    { price: 350, side: "P", openInterest: 8700, lineWidth: 3 },
    { price: 352.5, side: "P", openInterest: 471, lineWidth: 1 },
  ];
  const marked = markHighOiWalls(levels);
  const flagged = marked.filter((level) => level.highOi).map((level) => level.price);
  assert.deepEqual(flagged, [365, 350]);
  const widestOther = Math.max(...marked.filter((level) => !level.highOi).map((level) => level.lineWidth));
  marked.filter((level) => level.highOi).forEach((level) => {
    assert.equal(level.flash, true);
    assert.ok(level.lineWidth > widestOther);
  });
  // Input is not mutated.
  assert.equal(levels[1].highOi, undefined);
  assert.deepEqual(markHighOiWalls([]), []);
});

test("a High OI wall does not start a canvas animation loop", () => {
  const primitive = new TosNativeChartPrimitive();
  primitive.model = { ...primitive.model, levelSegments: [{ price: 360, flash: true }] };
  assert.equal(primitive.hasActiveAnimation(), false);
});
