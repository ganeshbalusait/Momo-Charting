import assert from "node:assert/strict";

const { test } = process.env.VITEST ? await import("vitest") : await import("node:test");

import { createColumnDragController } from "./momxColumnDrag.js";

// A fake <th>: data-col, attributes, a class list and a 100px-wide box at `left`.
function fakeTh(key, left) {
  const attrs = new Map([["data-col", key]]);
  const classes = new Set();
  const th = {
    getAttribute: (name) => (attrs.has(name) ? attrs.get(name) : null),
    setAttribute: (name, value) => attrs.set(name, String(value)),
    removeAttribute: (name) => attrs.delete(name),
    hasAttribute: (name) => attrs.has(name),
    classList: { add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c) },
    contains: (other) => other === th,
    getBoundingClientRect: () => ({ left, width: 100, right: left + 100, top: 0, bottom: 20, height: 20 }),
  };
  return th;
}

function fakeEvent(th, clientX = 0) {
  const event = {
    currentTarget: th,
    clientX,
    relatedTarget: null,
    defaultPrevented: false,
    preventDefault() { event.defaultPrevented = true; },
    dataTransfer: { effectAllowed: "", dropEffect: "", setData() {} },
  };
  return event;
}

function setup(order = ["symbol", "setup", "fresh", "pctChange"]) {
  let layout = { version: 1, order, hidden: [] };
  const changes = [];
  const ctl = createColumnDragController({
    getLayout: () => layout,
    onChange: (next) => { changes.push(next); layout = next; },
    defer: (fn) => fn(),
  });
  const ths = Object.fromEntries(order.map((key, i) => [key, fakeTh(key, i * 100)]));
  return { ctl, ths, changes, get layout() { return layout; } };
}

test("dragenter on a new header accepts the drop (preventDefault) so a quick release still drops", () => {
  const { ctl, ths, changes } = setup();
  ctl.onDragStart(fakeEvent(ths.setup, 150));
  ctl.onDragOver(fakeEvent(ths.fresh, 250));
  const enter = fakeEvent(ths.pctChange, 380);
  ctl.onDragEnter(enter);
  assert.equal(enter.defaultPrevented, true, "dragenter must be preventDefault'd or the browser refuses the drop");
  // Released immediately: no dragover on pctChange, straight to drop.
  const drop = fakeEvent(ths.pctChange, 380);
  ctl.onDrop(drop);
  ctl.onDragEnd(fakeEvent(ths.setup));
  assert.equal(changes.length, 1);
  assert.deepEqual(changes[0].order, ["symbol", "fresh", "pctChange", "setup"]);
});

test("drop without any marker on the target still uses the pointer half", () => {
  const { ctl, ths, changes } = setup();
  ctl.onDragStart(fakeEvent(ths.pctChange, 350));
  ctl.onDrop(fakeEvent(ths.setup, 120)); // left half of setup -> before
  assert.deepEqual(changes[0].order, ["symbol", "pctChange", "setup", "fresh"]);
});

test("dragenter sets the insertion marker; dragleave / end clear it", () => {
  const { ctl, ths } = setup();
  ctl.onDragStart(fakeEvent(ths.setup, 150));
  ctl.onDragEnter(fakeEvent(ths.fresh, 210));
  assert.equal(ths.fresh.getAttribute("data-drop"), "before");
  ctl.onDragOver(fakeEvent(ths.fresh, 290));
  assert.equal(ths.fresh.getAttribute("data-drop"), "after");
  assert.equal(ths.setup.classList.contains("is-col-dragging"), true);
  ctl.onDragEnd(fakeEvent(ths.setup));
  assert.equal(ths.fresh.hasAttribute("data-drop"), false);
  assert.equal(ths.setup.classList.contains("is-col-dragging"), false);
});

test("dropping on itself or cancelling changes nothing", () => {
  const { ctl, ths, changes } = setup();
  ctl.onDragStart(fakeEvent(ths.setup, 150));
  ctl.onDragEnter(fakeEvent(ths.setup, 190));
  ctl.onDrop(fakeEvent(ths.setup, 190));
  ctl.onDragStart(fakeEvent(ths.fresh, 250));
  ctl.onDragOver(fakeEvent(ths.symbol, 20));
  ctl.onDragEnd(fakeEvent(ths.fresh)); // Esc / released outside
  assert.equal(changes.length, 0);
});

test("a foreign drag (file/text) is ignored: no preventDefault on enter or over", () => {
  const { ctl, ths } = setup();
  const enter = fakeEvent(ths.fresh, 250);
  ctl.onDragEnter(enter);
  const over = fakeEvent(ths.fresh, 250);
  ctl.onDragOver(over);
  assert.equal(enter.defaultPrevented, false);
  assert.equal(over.defaultPrevented, false);
});

test("a header outside the layout (Time) refuses the drop", () => {
  const { ctl, ths, changes } = setup();
  const time = fakeTh("time", 900);
  ctl.onDragStart(fakeEvent(ths.setup, 150));
  const enter = fakeEvent(time, 950);
  ctl.onDragEnter(enter);
  assert.equal(enter.defaultPrevented, false);
  ctl.onDrop(fakeEvent(time, 950));
  assert.equal(changes.length, 0);
});

test("justDragged is true during a drag and briefly after it", () => {
  let now = 1000;
  let layout = { version: 1, order: ["symbol", "setup"], hidden: [] };
  const ctl = createColumnDragController({ getLayout: () => layout, onChange: () => {}, defer: (fn) => fn(), now: () => now });
  const th = fakeTh("setup", 100);
  assert.equal(ctl.justDragged(), false);
  ctl.onDragStart(fakeEvent(th, 150));
  assert.equal(ctl.justDragged(), true);
  ctl.onDragEnd(fakeEvent(th));
  now += 100;
  assert.equal(ctl.justDragged(), true);
  now += 1000;
  assert.equal(ctl.justDragged(), false);
});

// Headers in a row, linked like the DOM: nextElementSibling / previousElementSibling.
function linkedRow(keys) {
  const ths = keys.map((key, i) => fakeTh(key, i * 100));
  ths.forEach((th, i) => {
    th.nextElementSibling = ths[i + 1] || null;
    th.previousElementSibling = ths[i - 1] || null;
  });
  return Object.fromEntries(keys.map((key, i) => [key, ths[i]]));
}

test("'after' on a header the Time column follows draws the line after Time, where the column lands", () => {
  // Live: Symbol | Setup | Time | Fresh | % Chg - Time is not in the layout.
  let layout = { version: 1, order: ["symbol", "setup", "fresh", "pctChange"], hidden: [] };
  const changes = [];
  const ctl = createColumnDragController({
    getLayout: () => layout,
    onChange: (next) => { changes.push(next); layout = next; },
    defer: (fn) => fn(),
  });
  const ths = linkedRow(["symbol", "setup", "matchedSince", "fresh", "pctChange"]);
  ctl.onDragStart(fakeEvent(ths.pctChange, 450));
  ctl.onDragOver(fakeEvent(ths.setup, 190)); // right half of Setup
  assert.equal(ths.setup.hasAttribute("data-drop"), false, "no line between Setup and Time");
  assert.equal(ths.matchedSince.getAttribute("data-drop"), "after", "line drawn after Time");
  // Leaving Setup clears the line that was drawn on Time.
  ctl.onDragLeave(fakeEvent(ths.setup, 199));
  assert.equal(ths.matchedSince.hasAttribute("data-drop"), false);
  // Back over the right half and drop: moved to after Setup, i.e. after Time.
  ctl.onDragOver(fakeEvent(ths.setup, 190));
  ctl.onDrop(fakeEvent(ths.setup, 190));
  assert.deepEqual(changes[0].order, ["symbol", "setup", "pctChange", "fresh"]);
  assert.equal(ths.matchedSince.hasAttribute("data-drop"), false);
  // 'before' halves are untouched.
  ctl.onDragStart(fakeEvent(ths.fresh, 350));
  ctl.onDragOver(fakeEvent(ths.setup, 110));
  assert.equal(ths.setup.getAttribute("data-drop"), "before");
});

test("drag data is a private MIME type only - never text/plain that a text box would paste", () => {
  const { ctl, ths } = setup();
  const types = [];
  const start = fakeEvent(ths.setup, 150);
  start.dataTransfer.setData = (type, value) => types.push([type, value]);
  ctl.onDragStart(start);
  assert.ok(types.length >= 1, "Firefox needs some drag data");
  assert.ok(types.every(([type]) => type !== "text/plain" && type !== "text" && type !== "Text"));
  assert.ok(types.every(([type]) => type.startsWith("application/")));
});
