import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { test } from "node:test";

import { LEARN_SECTIONS, LEARN_STEPS } from "./learnContent.js";
import { LEARN_SCREENSHOTS, PENDING_CAPTURE, screenshotById } from "./learnScreenshots.js";

const publicDir = new URL("../public/learn/", import.meta.url);

test("manifest ids are unique", () => {
  const ids = LEARN_SCREENSHOTS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length);
});

test("every screenshot referenced by the content exists in the manifest", () => {
  [...LEARN_STEPS, ...LEARN_SECTIONS]
    .map((item) => item.screenshot)
    .filter(Boolean)
    .forEach((id) => {
      assert.ok(screenshotById(id), `content references screenshot "${id}", which is not in the manifest`);
    });
});

// Captured images must be on disk; pending ones must NOT be. That second half is
// the point: it stops "pending" quietly becoming a permanent excuse for an
// image nobody ever took.
test("captured images are on disk and pending ones are honestly absent", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    const onDisk = existsSync(new URL(shot.file, publicDir));
    if (PENDING_CAPTURE.includes(shot.id)) {
      assert.ok(!onDisk, `"${shot.id}" is listed as pending but the file exists - remove it from PENDING_CAPTURE`);
    } else {
      assert.ok(onDisk, `"${shot.id}" is not pending but public/learn/${shot.file} is missing`);
    }
  });
});

test("every image has alt text, because these are the only images on the page", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    assert.ok(shot.alt && shot.alt.trim().length > 20, `"${shot.id}" has weak or missing alt text`);
    assert.ok(shot.file.endsWith(".png"), `"${shot.id}" is not a png`);
  });
});

test("callouts are numbered from 1, positioned on the image, and readable", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    const callouts = shot.callouts || [];
    if (PENDING_CAPTURE.includes(shot.id)) return;
    assert.ok(callouts.length >= 1, `"${shot.id}" has no callouts`);
    assert.ok(callouts.length <= 6, `"${shot.id}" has ${callouts.length} callouts; more than six is unreadable on a phone`);
    callouts.forEach((callout, index) => {
      assert.equal(callout.n, index + 1, `"${shot.id}" callouts must be numbered 1..n in order`);
      assert.ok(callout.x >= 0 && callout.x <= 100, `"${shot.id}" callout ${callout.n} x is off the image`);
      assert.ok(callout.y >= 0 && callout.y <= 100, `"${shot.id}" callout ${callout.n} y is off the image`);
      assert.ok(callout.text && callout.text.trim().length > 10, `"${shot.id}" callout ${callout.n} has stub text`);
    });
  });
});

test("capture dates are real and not in the future", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    if (PENDING_CAPTURE.includes(shot.id)) return;
    const when = new Date(shot.capturedOn);
    assert.ok(!Number.isNaN(when.getTime()), `"${shot.id}" has an unparseable capturedOn`);
    assert.ok(when.getTime() <= Date.now(), `"${shot.id}" was captured in the future`);
  });
});
