import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

const lift = (pattern, what) => {
  const match = pattern.exec(appSource);
  assert.ok(match, `could not lift ${what} out of App.jsx`);
  return match[0];
};

// buildMtfMaLevelsStudy is not exported - App.jsx is one 31k-line module - so
// the study is lifted out with the two helpers it needs and run for real. That
// exercises shipped code instead of asserting on its source text.
const liftMtfMaLevelsStudy = () => new Function([
  lift(/const MTF_MA_LEVEL_DEFINITIONS = Object\.freeze\(\[[\s\S]*?\n\]\);/, "MTF_MA_LEVEL_DEFINITIONS"),
  lift(/const MTF_MA_LEVEL_TIMEFRAMES = Object\.freeze\(\[[\s\S]*?\n\]\);/, "MTF_MA_LEVEL_TIMEFRAMES"),
  lift(/const MTF_MA_MIN_AVERAGE_SAMPLES = \d+;/, "MTF_MA_MIN_AVERAGE_SAMPLES"),
  lift(/function calculateNumericEma\([\s\S]*?\n}/, "calculateNumericEma"),
  // Every fixture bar carries an explicit YYYY-MM-DD `date`, which dateKeyFor
  // prefers, so the Intl-backed key helper is never reached.
  'function easternDateKey() { throw new Error("unexpected easternDateKey call"); }',
  lift(/function buildMtfMaLevelsStudy\([\s\S]*?\n}\n/, "buildMtfMaLevelsStudy"),
  "return buildMtfMaLevelsStudy;",
].join("\n\n"))();

// Twelve years of dailies wobbling inside a 1% band, so every average lands
// well inside the proximity window and nothing is filtered out on distance.
const dailyBars = Array.from({ length: 3000 }, (unused, index) => {
  const day = new Date(Date.UTC(2014, 0, 1) + index * 86400000);
  const close = 100 + (index % 20) * 0.05;
  return {
    date: day.toISOString().slice(0, 10),
    time: Math.floor(day.getTime() / 1000),
    open: close, high: close + 0.2, low: close - 0.2, close, volume: 1000,
  };
});
const chartBars = [{
  date: dailyBars.at(-1).date,
  time: dailyBars.at(-1).time,
  open: 100.5, high: 100.9, low: 100.1, close: 100.5, volume: 500,
}];

// Proximity has no in-function default (`Number(options.mtfMaProximity) || 0`),
// so a real profile always supplies it - at 0 a level draws only when price
// sits exactly on it. Every case here starts from the shipped 10%.
const runStudy = (options) => liftMtfMaLevelsStudy()(chartBars, dailyBars, null, { mtfMaProximity: 10, ...options });
const emaLevels = (levels) => levels.filter((level) => /^\d+e/.test(level.label));
const smaLevels = (levels) => levels.filter((level) => /^\d+s/.test(level.label));

test("a profile with neither family key still draws both averages", () => {
  // Every profile saved before these toggles existed lacks both keys, so the
  // `!== false` reads are what stand in for a migration.
  const levels = runStudy({});
  assert.ok(emaLevels(levels).length > 0);
  assert.ok(smaLevels(levels).length > 0);
});

test("switching EMA off clears every EMA line and leaves the SMAs alone", () => {
  const both = runStudy({});
  assert.deepEqual(runStudy({ mtfMaShowEma: false }), smaLevels(both));
});

test("switching SMA off clears every SMA line and leaves the EMAs alone", () => {
  const both = runStudy({});
  assert.deepEqual(runStudy({ mtfMaShowSma: false }), emaLevels(both));
});

test("the two families partition the ribbon", () => {
  const both = runStudy({});
  // Nothing is dropped or drawn twice when the ribbon is split down the middle.
  assert.equal(runStudy({ mtfMaShowEma: false }).length + runStudy({ mtfMaShowSma: false }).length, both.length);
  assert.deepEqual(runStudy({ mtfMaShowEma: false, mtfMaShowSma: false }), []);
});

test("family toggles compose with the daily/weekly/monthly toggles", () => {
  const levels = runStudy({ mtfMaShowSma: false, mtfMaShowWeekly: false, mtfMaShowMonthly: false });
  assert.ok(levels.length > 0);
  assert.ok(levels.every((level) => /^\d+eD$/.test(level.label)));
});

test("MTF MA Levels opens with both average families visible", () => {
  assert.match(
    appSource,
    /mtfMaShowMonthly: true,\n\s*mtfMaShowEma: true,\n\s*mtfMaShowSma: true,/,
  );
});

test("the settings panel toggles EMA and SMA alongside the timeframe boxes", () => {
  const match = /\{key === "mtfMaLevels" \? <>([\s\S]*?)<\/> : null\}/.exec(appSource);
  assert.ok(match, "the MTF MA Levels settings panel is no longer in App.jsx");
  const panel = match[1];
  assert.match(
    panel,
    /<label><input type="checkbox" checked=\{indicatorOptions\.mtfMaShowEma !== false\} onChange=\{\(\) => setIndicatorOption\("mtfMaShowEma", indicatorOptions\.mtfMaShowEma === false\)\} \/><span>Show EMA<\/span><\/label>/,
  );
  assert.match(
    panel,
    /<label><input type="checkbox" checked=\{indicatorOptions\.mtfMaShowSma !== false\} onChange=\{\(\) => setIndicatorOption\("mtfMaShowSma", indicatorOptions\.mtfMaShowSma === false\)\} \/><span>Show SMA<\/span><\/label>/,
  );
  // Grouped with the other visibility switches - after the timeframes, ahead of
  // the name-bubble box and the six colour pickers.
  const order = ["Show monthly", "Show EMA", "Show SMA", "Display names", "EMA 1 violet"]
    .map((label) => panel.indexOf(label));
  assert.ok(order.every((index) => index >= 0), "a MTF MA Levels setting went missing");
  assert.deepEqual(order, [...order].sort((left, right) => left - right));
});
