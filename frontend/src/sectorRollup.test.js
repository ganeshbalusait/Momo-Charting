// Which INDUSTRIES are moving together -- "Biotech 6 of 9 up >=1.5%".
//
// A board sorted by percentage already tells him the best single names. It
// does not tell him that every ETF-Lev name on the board is up, which is a
// different fact and often the more actionable one: one stock up 6% is a story
// about that stock, six of six up is a story about the sector.
//
// Most of these tests pin the RANKING and the exclusions, because the naive
// version of this panel is confidently misleading rather than merely wrong.

import { describe, expect, test } from "vitest";

import { SECTOR_MOVE_PCT, sectorRollup, sortRows } from "./momxCells.js";

const row = (industry, pctChange) => ({ symbol: "X", industry, pctChange });

describe("sectorRollup", () => {
  test("counts how many of a group are up past the threshold", () => {
    const [group] = sectorRollup([
      row("Biotech", 4.0),
      row("Biotech", 2.0),
      row("Biotech", 0.2),
    ]);
    expect(group.name).toBe("Biotech");
    expect(group.up).toBe(2);
    expect(group.total).toBe(3);
  });

  test("the threshold is inclusive, matching the >=1.5% label", () => {
    const [group] = sectorRollup([row("A", SECTOR_MOVE_PCT), row("A", 0)]);
    expect(group.up).toBe(1);
  });

  test("ranks by PARTICIPATION, not by the average", () => {
    // The reason this panel exists. "Solo" has a far better average, but it is
    // one name carrying an otherwise flat group -- exactly the impression a
    // percentage-sorted board already gives and this panel is meant to correct.
    const out = sectorRollup([
      row("Solo", 40),
      row("Solo", 0.1),
      row("Solo", 0.1),
      row("Solo", 0.1),
      row("Broad", 2.0),
      row("Broad", 2.1),
      row("Broad", 2.2),
      row("Broad", 2.3),
    ]);
    expect(out[0].name).toBe("Broad");
    expect(out[0].share).toBe(1);
    expect(out[1].avg).toBeGreaterThan(out[0].avg); // Solo really does average more
  });

  test("a bigger clean sweep outranks a smaller one", () => {
    const out = sectorRollup([
      row("Six", 2), row("Six", 2), row("Six", 2),
      row("Six", 2), row("Six", 2), row("Six", 2),
      row("Two", 9), row("Two", 9),
    ]);
    expect(out.map((g) => g.name)).toEqual(["Six", "Two"]);
  });

  test("a single ticker is not a sector", () => {
    // "1 of 1 up" would otherwise always score a perfect 100% and top the list.
    expect(sectorRollup([row("Lonely", 8)])).toEqual([]);
  });

  test("groups with nothing moving are dropped, not listed at zero", () => {
    const out = sectorRollup([row("Quiet", 0.1), row("Quiet", -2), row("Live", 3), row("Live", 3)]);
    expect(out.map((g) => g.name)).toEqual(["Live"]);
  });

  test("a row with no quote yet is counted in neither total nor movers", () => {
    // Counting it in the total only would report "2 of 5 up" while three of
    // those five simply have not priced, understating the move for no reason.
    const [group] = sectorRollup([
      row("Biotech", 3),
      row("Biotech", 3),
      row("Biotech", null),
      row("Biotech", undefined),
      { symbol: "Z", industry: "Biotech" },
    ]);
    expect(group.up).toBe(2);
    expect(group.total).toBe(2);
    expect(group.share).toBe(1);
  });

  test("the average is over the whole group, not just the movers", () => {
    // "6 of 9 up, avg +3.4%" describes the SECTOR; averaging only the winners
    // would make every group look strong by construction.
    const [group] = sectorRollup([row("A", 4), row("A", 4), row("A", -2), row("A", -2)]);
    expect(group.avg).toBe(1);
  });

  test("rows without an industry are ignored", () => {
    expect(sectorRollup([row("", 5), row("   ", 5), { pctChange: 5 }])).toEqual([]);
  });

  test("the list is capped", () => {
    const rows = [];
    for (let i = 0; i < 12; i += 1) {
      rows.push(row("G" + i, 5), row("G" + i, 5));
    }
    expect(sectorRollup(rows).length).toBe(5);
    expect(sectorRollup(rows, { limit: 2 }).length).toBe(2);
  });

  test("junk in never throws", () => {
    expect(sectorRollup(null)).toEqual([]);
    expect(sectorRollup(undefined)).toEqual([]);
    expect(sectorRollup([null, 7, "x", {}])).toEqual([]);
  });

  test("each group carries its industry colour for the pill", () => {
    const [group] = sectorRollup([row("Biotech", 3), row("Biotech", 3)]);
    expect(typeof group.color).toBe("string");
    expect(group.color.length).toBeGreaterThan(0);
  });
});

// ---------------------------------------------------------------------------
// Numeric-looking LABELS must sort as numbers (the Squeeze column)
// ---------------------------------------------------------------------------
//
// Squeeze cells carry the thinkScript LABEL, so their value is a string: "9",
// "32", "*1", "-". Sorting those as text put "9" above "32" and sank the
// freshly-fired "*1"/"*2" high-compression cells -- the rows worth looking at
// -- to the bottom. It is the only cell column whose value is not a number.

describe("sortRows on string labels", () => {
  const rows = [
    { symbol: "DLTR", "sqz.D": "9" },
    { symbol: "SBUX", "sqz.D": "32" },
    { symbol: "FRESH", "sqz.D": "*2" },
    { symbol: "NONE", "sqz.D": "-" },
  ];

  test("descending puts 32 above 9, not the other way round", () => {
    const out = sortRows(rows, "sqz.D", "desc").map((r) => r.symbol);
    expect(out.slice(0, 2)).toEqual(["SBUX", "DLTR"]);
  });

  test("the starred high-compression label sorts on its number", () => {
    const out = sortRows(rows, "sqz.D", "asc").map((r) => r["sqz.D"]);
    expect(out).toEqual(["-", "*2", "9", "32"]);
  });

  test("equal counts put the starred one higher", () => {
    const tied = [{ symbol: "A", "sqz.D": "2" }, { symbol: "B", "sqz.D": "*2" }];
    expect(sortRows(tied, "sqz.D", "desc").map((r) => r.symbol)).toEqual(["B", "A"]);
  });

  test("a TIME label is NOT parsed as a number", () => {
    // The trap this guards: parseFloat("04:20") is 4 and parseFloat("11:05") is
    // 11, so a loose numeric parse would sort times by the hour and silently
    // discard the minutes -- a worse and quieter bug than the one being fixed.
    const times = [
      { symbol: "A", time: "04:55" },
      { symbol: "B", time: "04:20" },
    ];
    expect(sortRows(times, "time", "asc").map((r) => r.time)).toEqual(["04:20", "04:55"]);
  });
});

describe("sector rotation (hot sectors from the scanner)", () => {
  const hot = { hot: true, rs3: 5.5, up: 9, total: 10 };
  const rot = (industry, pctChange, sectorRotation) => ({ symbol: "X", industry, pctChange, sectorRotation });

  test("a hot sector sorts first and carries its 3-day lead", () => {
    const groups = sectorRollup([
      rot("Biotech", 4.0, { hot: false }), rot("Biotech", 3.0, { hot: false }),
      rot("Semis", 1.6, hot), rot("Semis", 0.4, hot),
    ]);
    expect(groups[0].name).toBe("Semis");
    expect(groups[0].hot).toBe(true);
    expect(groups[0].rs3).toBe(5.5);
    expect(groups[0].breadthUp).toBe(9);
  });

  test("a hot sector shows even when none of its rows on screen is up 1.5%", () => {
    const groups = sectorRollup([rot("Semis", 0.8, hot)]);
    expect(groups.map((g) => g.name)).toEqual(["Semis"]);
  });

  test("rows without the scanner's verdict behave exactly as before", () => {
    const [group] = sectorRollup([rot("A", 2.0, undefined), rot("A", 2.0, undefined)]);
    expect(group.hot).toBe(false);
    expect(group.rs3).toBe(null);
  });
});

describe("late rotation (after 11:00)", () => {
  test("a late sector shows after the hot ones and is not hot", () => {
    const groups = sectorRollup([
      { symbol: "A", industry: "Space", pctChange: 0.9, sectorRotation: { hot: false, late: true, rs3: 2.5, up: 7, total: 9 } },
      { symbol: "B", industry: "Quantum", pctChange: 0.8, sectorRotation: { hot: true, late: false, rs3: 3.9, up: 5, total: 6 } },
    ]);
    expect(groups.map((g) => [g.name, g.hot, g.late])).toEqual([["Quantum", true, false], ["Space", false, true]]);
  });
});

test("hot box carries its stocks and the unusual-volume count", () => {
  const [g] = sectorRollup([{ symbol: "IONQ", industry: "Quantum", pctChange: 5.7,
    sectorRotation: { hot: true, rs3: 3.9, up: 5, total: 6, volUp: 4, leaders: [{ symbol: "IONQ", pct: 5.74, vol: true }] } }]);
  expect(g.volUp).toBe(4);
  expect(g.leaders[0].symbol).toBe("IONQ");
});

import { hlBlockStyle, HL_BLOCK_COLORS } from "./momxCells.js";

describe("H/L block (MomoX style)", () => {
  test("strong reads pale mint, never thinkorswim's dark green", () => {
    expect(hlBlockStyle({ value: 0.95, bg: "dark_green" })).toEqual({ color: HL_BLOCK_COLORS.dark_green, widthPct: 95 });
    expect(HL_BLOCK_COLORS.dark_green).not.toBe("#006400");
  });
  test("small readings keep a visible sliver; missing data draws nothing", () => {
    expect(hlBlockStyle({ value: -0.05, bg: "red" }).widthPct).toBe(12);
    expect(hlBlockStyle({ value: null, bg: "red" })).toBe(null);
    expect(hlBlockStyle({ value: 0.4, bg: null })).toBe(null);
  });
});

test("sector boxes carry the sector's own today / 3-day move (no SPY)", () => {
  const [g] = sectorRollup([{ symbol: "IONQ", industry: "Quantum", pctChange: 3.0,
    sectorRotation: { hot: false, late: true, rs3: 3.9, ret3: 4.7, today: 3.2, up: 5, total: 6 } }]);
  expect([g.late, g.today, g.ret3]).toEqual([true, 3.2, 4.7]);
});
