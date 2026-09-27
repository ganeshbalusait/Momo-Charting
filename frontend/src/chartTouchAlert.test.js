import test from "node:test";
import assert from "node:assert/strict";

import {
  CHART_TOUCH_ALERT_HOLD_MS,
  CHART_TOUCH_ALERT_MOVE_TOLERANCE_PX,
  touchAlertGestureShouldCancel,
} from "./chartTouchAlert.js";

test("a steady mobile hold remains eligible to open the chart alert editor", () => {
  assert.equal(CHART_TOUCH_ALERT_HOLD_MS, 550);
  assert.equal(CHART_TOUCH_ALERT_MOVE_TOLERANCE_PX, 10);
  assert.equal(touchAlertGestureShouldCancel(100, 200, 106, 207), false);
});

test("a mobile chart drag or malformed gesture cancels the alert hold", () => {
  assert.equal(touchAlertGestureShouldCancel(100, 200, 111, 200), true);
  assert.equal(touchAlertGestureShouldCancel(100, 200, 108, 208), true);
  assert.equal(touchAlertGestureShouldCancel(100, 200, Number.NaN, 200), true);
});
