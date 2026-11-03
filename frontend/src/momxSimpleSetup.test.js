// The simple Setup column (2026-09-28, "lot of rules its confusing"): only
// GO+, GO, ⭐1, ⚠, ADX, the chart arrows, ⚡, MX A+, Skittles breaks, SQZ fires
// and the RVOL timeframes stay.
import test from "node:test";
import assert from "node:assert/strict";
import { strategyTags, skittlesBreaks, rvolBuilding, setMomxSimpleSetup } from "./momxFilters.js";

const AT = Date.parse("2026-09-28T10:30:00-04:00");
const barAt = (hm) => Date.parse("2026-09-28T" + hm + ":00-04:00") / 1000;

test("RVOL tag lists the painted, live timeframes on the board's side", () => {
  const row = {
    symbol: "NVDA",
    rvol: {
      "5m": { bg: "black", fg: "cyan", barAt: barAt("10:25") },
      "30m": { bg: "cyan", barAt: barAt("10:00") },
      "1h": { bg: "green", barAt: barAt("10:00") },
      "2h": { bg: "magenta", barAt: barAt("10:00") },
      D: { bg: "cyan", barAt: Date.parse("2026-09-25T09:30:00-04:00") / 1000 },   // prior session: dimmed, left out
    },
  };
  assert.equal(rvolBuilding(row, AT).text, "RVOL↑ 30m 1h");
  const bear = { ...row, direction: "bear" };
  assert.equal(rvolBuilding(bear, AT).text, "RVOL↓ 2h");
  assert.equal(rvolBuilding({ symbol: "X", rvol: { "5m": { bg: "black" } } }, AT), null);
});

test("Skittles tag: only crosses SEEN in the last hour that still show", () => {
  const ev = (hm, what) => ({ at: "2026-09-28T" + hm + ":00-04:00", what });
  const row = {
    symbol: "M",
    skittles: { "2h": { bg: "cyan" }, "4h": { bg: "green" }, D: { bg: "lime" }, "2D": { bg: "cyan" }, "3D": { bg: "cyan" }, M: { bg: "green" } },
    gradeFresh: { timeline: [
      ev("10:05", "SKIT 2h bg cyan"),       // fresh
      ev("10:20", "SKIT D bg lime"),        // fresh, newest
      ev("09:10", "SKIT 4h bg green"),      // 80 min ago: not fresh
      ev("10:15", "SKIT 3D bg dark_green"), // counter-trend paint: not a break
    ] },
  };
  const tags = skittlesBreaks(row, AT);
  // 2D / 3D / Mo painted for days: left out. One pill per cross time, newest
  // first, and only the newest carries the flash clock.
  assert.deepEqual(tags.map((x) => x.text), ["SKIT↑ D-Y 10:20", "SKIT↑ 2h-C 10:05"]);
  assert.equal(tags[0].atMs, Date.parse("2026-09-28T10:20:00-04:00"));
  assert.equal(tags[1].atMs, undefined);
  const both = strategyTags(row, Date.parse("2026-09-28T10:22:00-04:00")).filter((x) => x.key === "skit");
  assert.deepEqual(both.map((x) => Boolean(x.fresh)), [true, false], "only the latest blinks");
  const gone = { ...row, skittles: { ...row.skittles, "2h": { bg: "black" }, D: { bg: "black" } } };
  assert.deepEqual(skittlesBreaks(gone, AT), [], "the block no longer shows it");
  assert.deepEqual(skittlesBreaks({ symbol: "M", skittles: { "2D": { bg: "cyan" } } }, AT), [], "painted, never seen crossing");
  // BB 2026-09-28: 4D crossed 09:47, blinked off, back at 11:01 - not fresh (times shifted to the test clock).
  const blink = { symbol: "BB", skittles: { "4D": { bg: "green" } }, gradeFresh: { timeline: [
    { at: "2026-09-28T09:10:00-04:00", what: "SKIT 4D bg green" }, { at: "2026-09-28T10:25:00-04:00", what: "SKIT 4D bg green" }] } };
  assert.deepEqual(skittlesBreaks(blink, AT), []);
});

test("simple mode keeps his list and drops the rest", () => {
  setMomxSimpleSetup(true);
  const row = {
    symbol: "META",
    grade: { letter: "A+" },
    momoxAPlus: { at: "2026-09-28T09:35:00-04:00", price: 700 },
    rvol: { "30m": { bg: "cyan", barAt: barAt("10:00") } },
    skittles: { "2h": { bg: "cyan" } },
    gradeFresh: { timeline: [{ at: "2026-09-28T10:10:00-04:00", what: "SKIT 2h bg cyan" }] },
    marketTurn: { rank: 1, at: "2026-09-28T10:05:00-04:00", price: 700 },
  };
  const keys = strategyTags(row, AT).map((t) => t.key);
  assert.equal(keys[0], "bolt");
  assert.ok(keys.includes("mxaplus") && keys.includes("rvol") && keys.includes("skit"), keys.join(","));
  assert.ok(!keys.includes("turn"), "market turn is not on his list");
  setMomxSimpleSetup(false);
  assert.ok(strategyTags(row, AT).some((t) => t.key === "turn"), "full mode still shows it");
  setMomxSimpleSetup(true);
});

test("FILTERS: MomoX A+ shows only the rows with an MX A+ stamp (bear rows carry the BEAR book's)", async () => {
  const { filterRows, coerceFilters, DEFAULT_MOMX_FILTERS, strategyOf } = await import("./momxFilters.js");
  const config = coerceFilters({ ...DEFAULT_MOMX_FILTERS, setup: "mxaplus" });
  assert.equal(strategyOf(config), "mxaplus");
  const rows = [
    { symbol: "META", grade: { letter: "A+" }, momoxAPlus: { at: "2026-09-28T09:35:00-04:00" } },
    { symbol: "NVDA", grade: { letter: "A+" } },
    { symbol: "BEAR", direction: "bear", grade: { letter: "A+" }, momoxAPlus: { at: "2026-09-28T09:35:00-04:00" } },
  ];
  assert.deepEqual(filterRows(rows, config, AT).map((r) => r.symbol), ["META", "BEAR"]);
});

test("chart arrows: a redrawn CALL2H shows when it appeared; a repainted one is kept, struck", async () => {
  const { chartArrows } = await import("./momxFilters.js");
  const now = Date.parse("2026-09-28T13:10:00-04:00");
  const row = { symbol: "ABVX", chartSignals: [
    { label: "CALL2H", family: "4x8", timeframe: "2H", at: "2026-09-28T09:00:00-04:00", seenAt: "2026-09-28T13:04:45-04:00" },
    { label: "C4H", family: "4x8", timeframe: "4H", at: "2026-09-28T10:15:00-04:00", seenAt: "2026-09-28T10:20:00-04:00", goneAt: "2026-09-28T13:05:00-04:00" },
  ] };
  const arrows = chartArrows(row, now);
  const call = arrows.find((a) => a.label === "CALL2H");
  assert.equal(call.redrawn, true);
  const c4h = arrows.find((a) => a.label === "C4H");
  assert.ok(c4h && c4h.goneAt);
  setMomxSimpleSetup(true);
  const tags = strategyTags(row, now).filter((t) => t.key.startsWith("chart-"));
  assert.deepEqual(tags.map((t) => t.text).sort(), ["C4H 10:15 ✕13:05", "CALL2H 13:04↺"]);
  assert.equal(tags.find((t) => t.text.startsWith("C4H")).gone, true);
});

test("fading blocks no rule: MX A+ keeps its tag and bolt, with the warning", async () => {
  const { filterRows, coerceFilters, DEFAULT_MOMX_FILTERS } = await import("./momxFilters.js");
  const at = Date.parse("2026-09-28T09:40:00-04:00");
  const fading = { symbol: "F", momoxAPlus: { at: "2026-09-28T09:35:00-04:00", price: 10 }, m5: { state: "fading" } };
  const tags = strategyTags(fading, at);
  assert.equal(tags[0].key, "bolt");
  assert.ok(tags.some((t) => t.key === "mxaplus"));
  assert.ok(tags.some((t) => t.key === "warn" && /fading/.test(t.text)));
  const config = coerceFilters({ ...DEFAULT_MOMX_FILTERS, setup: "mxaplus" });
  assert.deepEqual(filterRows([fading], config, at).map((r) => r.symbol), ["F"]);
});

test("FILTERS: the bot's rules on both boards (MX A+ bear, turn, sector, ZS, short arrows)", async () => {
  const { filterRows, coerceFilters, DEFAULT_MOMX_FILTERS } = await import("./momxFilters.js");
  const at = Date.parse("2026-09-29T10:30:00-04:00");
  const cfg = (setup) => coerceFilters({ ...DEFAULT_MOMX_FILTERS, setup });
  const bearRow = (sym, extra = {}) => ({ symbol: sym, direction: "bear", industry: "Crypto", pctChange: -5, last: 9, m5: { vwap: 10 }, ...extra });
  const rows = [
    bearRow("RIOT", { momoxAPlus: { at: "2026-09-29T09:50:00-04:00" }, marketTurn: { rank: 1, at: "2026-09-29T10:10:00-04:00" } }),
    bearRow("CIFR"), bearRow("IREN"), bearRow("BTBT", { pctChange: -4 }),
    { symbol: "UP", direction: "bear", industry: "Pharma", pctChange: 1, last: 11, m5: { vwap: 10 } },
  ];
  assert.deepEqual(filterRows(rows, cfg("mxaplus"), at).map((r) => r.symbol), ["RIOT"]);
  // "turn" left the FILTERS dropdown 2026-10-10: a saved one reads as Any.
  assert.equal(cfg("turn").setup, "any");
  // "sector" and "zs" left the dropdown 2026-10-10 - ZS is now the column's own
  // ZS tag (tagZs) and the bear board has no SECTOR tag.
  assert.equal(cfg("sector").setup, "any");
  assert.equal(cfg("zs").setup, "any");
  const arrow = bearRow("P", { chartSignals: [{ label: "P2H", family: "4x8", timeframe: "2H", at: "2026-09-29T10:00:00-04:00", seenAt: "2026-09-29T10:05:00-04:00" }] });
  assert.deepEqual(filterRows([arrow, bearRow("Q")], cfg("chartShort"), at).map((r) => r.symbol), ["P"]);
});

test("History judges the SKIT tag as of the snapshot, not now (MRNA 10-09)", async () => {
  const fs = await import("node:fs");
  const row = {
    symbol: "MRNA", direction: "bull", skittles: { "2h": { value: 62, bg: "green" } },
    gradeFresh: { timeline: [{ at: "2026-10-09T09:30:16-04:00", what: "SKIT 2h bg green" }] },
  };
  const snap = Date.parse("2026-10-09T09:31:04-04:00");
  assert.equal(skittlesBreaks(row, snap)[0].text, "SKIT↑ 2h-G 09:30");
  assert.deepEqual(skittlesBreaks(row, Date.parse("2026-10-10T08:00:00-04:00")), [], "a later 'now' drops it");
  const src = fs.readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  assert.match(src, /gradeCell\(column, row, onOpenGrade, base, Number\(entry\.atMs\) \|\| undefined\)/);
});

test("Setup column hides the plain MOM↑ and keeps the strong (cyan-filled) one", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  assert.match(src, /\.filter\(\(tag\) => !\(tag\.key === "sqzmom" && !tag\.strong\)\)/);
});

test("every Setup dropdown strategy has an explanation, bull and bear (no 'undefined')", async () => {
  const f = await import("./momxFilters.js");
  for (const dir of ["bull", "bear"]) {
    const rules = f.strategyRules(dir);
    for (const v of f.FILTER_STRATEGY_VALUES) assert.ok(typeof rules[v] === "string" && rules[v].length > 20, dir + " " + v);
  }
});

test("Setup-column tags as filters pass exactly the rows showing that tag", async () => {
  const f = await import("./momxFilters.js");
  const at = Date.parse("2026-10-09T09:31:04-04:00");
  const mrna = {
    symbol: "MRNA", direction: "bull", skittles: { "2h": { value: 62, bg: "green" } },
    gradeFresh: { timeline: [{ at: "2026-10-09T09:30:16-04:00", what: "SKIT 2h bg green" }] },
  };
  const plain = { symbol: "CVX", direction: "bull", skittles: {} };
  const pass = (row, setup) => f.filterRows([row], { ...f.DEFAULT_FILTER_CONFIG, setup }, at).length === 1;
  assert.equal(pass(mrna, "tagSkit"), true);
  assert.equal(pass(plain, "tagSkit"), false);
  assert.equal(pass(mrna, "tagSqz"), false);
});

test("tapping a Setup tag opens its explanation box", async () => {
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  assert.equal((src.match(/onClick=\{\(event\) => explainTag\(event, tag, row\)\}/g) || []).length, 2);
  assert.match(src, /<MomxTagExplain \/>/);
});

test("🔥 / 🔸 SECTOR tag: a leader of a hot or moving sector, shown in the Setup column", async () => {
  const f = await import("./momxFilters.js");
  const rot = (o) => ({ symbol: "ABBV", direction: "bull", industry: "Pharma", sectorRotation: { leaders: [{ symbol: "ABBV" }], ...o } });
  assert.equal(f.sectorTag(rot({ hot: true })).text, "🔥 SECTOR");
  assert.equal(f.sectorTag(rot({ hot: false, late: true })).text, "🔸 SECTOR");
  assert.equal(f.sectorTag(rot({ hot: false, late: false })), null);
  assert.equal(f.sectorTag({ ...rot({ hot: true }), symbol: "OTHER" }), null);
  assert.ok(f.strategyTags(rot({ hot: true }), Date.now()).some((t) => t.key === "sector"));
});

test("removed FILTERS choices (SOLO, TURN, V2 ...) are gone from the dropdown and a saved one resets to Any", async () => {
  const f = await import("./momxFilters.js");
  const fs = await import("node:fs");
  const src = fs.readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  for (const v of ["turn", "hotLead", "solo", "v2", "v3", "daily2", "opt", "tagMom", "tagJev"]) {
    assert.equal(src.includes('["' + v + '", '), false, v);
  }
  assert.doesNotMatch(src, /\["g", "Strategy G/);
  assert.equal(f.coerceFilters({ ...f.defaultFilters ? f.defaultFilters() : {}, version: 4, setup: "solo" }).setup, "any");
});

test("FILTERS = the Setup column: one choice per tag, in the column's order", async () => {
  const fs = await import("node:fs");
  const f = await import("./momxFilters.js");
  const src = fs.readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  const block = src.slice(src.indexOf('group: "Setup column (same order)"'), src.indexOf('group: "Record column"'));
  const values = [...block.matchAll(/\["(\w+)", /g)].map((m) => m[1]);
  assert.deepEqual(values, ["tagBolt", "go", "goRvolMacd", "mxaplus", "tagStar1", "tagSector", "dayLine", "dayLine100",
    "dayReclaim", "tagZs", "tagAbvx", "tagMomStrong", "tagFlip", "tagRvol", "tagSkit", "tagSqz", "tagChartSqz", "tagAdx", "chart",
    "chartShort", "tagWarn", "tagExplosive", "tagSteady"]);
  for (const dir of ["bull", "bear"]) for (const v of values) assert.ok((f.strategyRules(dir)[v] || "").length > 20, dir + " " + v);
  // a row showing ZS↑ in the column passes the ZS filter, one without it does not
  const at = Date.parse("2026-10-09T10:00:00-04:00");
  const withZs = { symbol: "Z", direction: "bull", dayLines: { zs: "2026-10-09T09:58:00-04:00" } };
  const tagged = f.strategyTags(withZs, at).some((t) => t.key === "zsrule");
  assert.equal(f.filterRows([withZs], { setup: "tagZs" }, at).length === 1, tagged);
});

test("every dropdown choice is a registered filter value (picking it really filters)", async () => {
  const fs = await import("node:fs");
  const f = await import("./momxFilters.js");
  const src = fs.readFileSync(new URL("./MomxScannerPanel.jsx", import.meta.url), "utf8");
  const block = src.slice(src.indexOf('group: "Setup column (same order)"'), src.indexOf('{gateSelect("momentum"'));
  for (const [, v] of block.matchAll(/\["(\w+)", /g)) {
    assert.ok(f.FILTER_SETUP_VALUES.includes(v), v + " in FILTER_SETUP_VALUES");
    if (!["any", "aAndUp", "aPlus"].includes(v)) assert.ok(f.FILTER_STRATEGY_VALUES.includes(v), v + " in FILTER_STRATEGY_VALUES");
    assert.equal(f.coerceFilters({ ...f.DEFAULT_MOMX_FILTERS, setup: v }).setup, v, v + " survives a reload");
  }
});

test("Record column keeps matching histories separate instead of choosing the best ROI", async () => {
  const f = await import("./momxFilters.js");
  const at = Date.parse("2026-10-09T09:50:00-04:00");
  const sample = { n: 45, seen: 50, wins: 32, losses: 13, breakEven: 0, winRate: 71.1, roi: 25.4,
    netPnl: 100, avgNet: 2.22, avgWin: 10, avgLoss: -5, coveragePct: 90, excludedTotal: 5, excluded: {late_exit_quote:5} };
  f.setRuleRecord({ version: 2, days: 10, asOf: (at-1000)/1000, builtAt: "2026-10-09T09:49:59-04:00",
    ranges: {bear: {days:10,from:"2026-09-25",through:"2026-10-08"}}, roundTripFeeEstimate:1.3,
    maxEntryDelaySeconds:600,maxExitQuoteDelaySeconds:120,
    bear: {dayLineEarly: sample, explosive: {...sample,n:3,seen:3,wins:3,losses:0,winRate:100,roi:90,coveragePct:100,excludedTotal:0,excluded:{}}} });
  try {
    const row = { symbol: "X", direction: "bear", dayLines: { line: "2026-10-09T09:45:00-04:00" }, m5: { pattern: "explosive" } };
    const rec = f.recordCell(row, at);
    // Every rule shows its own win % (2026-11-02), still never pooled.
    assert.equal(rec.text, "LINE↓ 71.1% · Explosive 100%");
    assert.equal(rec.detail, "n=45 · n=3 · limited");
    assert.equal(rec.best, null);
    assert.match(rec.lines.join(" | "), /32 wins \/ 13 losses/);
    assert.match(rec.lines.join(" | "), /LIMITED EVIDENCE/);
    assert.match(rec.lines.join(" | "), /late exit quote 5/);
    assert.equal(f.recordCell({ symbol: "Y", direction: "bear" }, at), null);
    assert.equal(f.recordCell(row, at-60000).text,"History unavailable");
    f.setRuleRecord({days:10,bear:{dayLineEarly:sample}});
    assert.equal(f.recordCell(row,at).text,"History pending");
  } finally { f.setRuleRecord(null); }
});

test("SKIT tag carries the box's real colour; different colours at one time = two pills", async () => {
  const f = await import("./momxFilters.js");
  const at = Date.parse("2026-10-09T09:31:04-04:00");
  const row = { symbol: "MRNA", direction: "bull",
    skittles: { "2h": { value: 62, bg: "green" }, "4h": { value: 70, bg: "cyan" } },
    gradeFresh: { timeline: [{ at: "2026-10-09T09:30:16-04:00", what: "SKIT 2h bg green" },
      { at: "2026-10-09T09:30:16-04:00", what: "SKIT 4h bg cyan" }] } };
  const tags = f.skittlesBreaks(row, at);
  assert.deepEqual(tags.map((t) => [t.text, t.color]).sort(), [["SKIT↑ 2h-G 09:30", "green"], ["SKIT↑ 4h-C 09:30", "cyan"]]);
  assert.match(tags.find((t) => t.color === "green").title, /green \(MACD cross up/);
});

test("chart squeeze flames show in the Setup column: one pill per candle, one candle long", async () => {
  const f = await import("./momxFilters.js");
  const row = { symbol: "MRNA", direction: "bull", chartSqueeze: [
    { timeframe: "1h", at: "2026-10-09T09:00:00-04:00", seenAt: "2026-10-09T09:05:00-04:00" },
    { timeframe: "2h", at: "2026-10-09T09:00:00-04:00", seenAt: "2026-10-09T09:05:00-04:00" },
  ] };
  const at = (hm) => Date.parse("2026-10-09T" + hm + ":00-04:00");
  assert.deepEqual(f.chartSqueezeTags(row, at("09:31")).map((t) => t.text), ["🔥1h 2h 09:00"]);
  assert.deepEqual(f.chartSqueezeTags(row, at("10:30")).map((t) => t.text), ["🔥2h 09:00"]);   // 1h life over
  assert.deepEqual(f.chartSqueezeTags(row, at("11:10")), []);                                  // 2h life over
  assert.ok(f.strategyTags(row, at("09:31")).some((t) => t.key === "chartsqz"));
  assert.equal(f.filterRows([row], { setup: "tagChartSqz" }, at("09:31")).length, 1);
  const gone = { ...row, chartSqueeze: [{ timeframe: "2h", at: "2026-10-09T09:00:00-04:00", seenAt: "2026-10-09T09:05:00-04:00", goneAt: "2026-10-09T09:20:00-04:00" }] };
  assert.equal(f.chartSqueezeTags(gone, at("09:31"))[0].text, "🔥2h 09:00 ✕09:20");
  assert.equal(f.filterRows([gone], { setup: "tagChartSqz" }, at("09:31")).length, 0);
});
