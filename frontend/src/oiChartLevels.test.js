import assert from "node:assert/strict";
import test from "node:test";

import {
  OI_WINDOW_LEVEL_SET_EXPIRY,
  buildAggregateOiLevelSet,
  buildHighOiLevelModel,
  buildHighOiLevelSetFromRows,
  classifyHighOiStrength,
  expandPriceRangeForNearbyOiLevels,
  mergeOiChartLevelSources,
} from "./oiChartLevels.js";

const levelSets = [
  {
    expiry: "2026-08-07",
    daysToExpiration: 9,
    atmStrike: 200,
    callLevels: [{ strike: 210, openInterest: 2_000, volume: 300 }],
    putLevels: [{ strike: 190, openInterest: 1_800, volume: 250 }],
  },
  {
    expiry: "2026-07-31",
    daysToExpiration: 2,
    atmStrike: 200,
    callLevels: [
      { strike: 205, openInterest: 10_000, volume: 900 },
      { strike: 207.5, openInterest: 5_000, volume: 700 },
      { strike: 260, openInterest: 2_000, volume: 100 },
    ],
    putLevels: [
      { strike: 195, openInterest: 9_000, volume: 800 },
      { strike: 205, openInterest: 3_000, volume: 400 },
    ],
  },
];

test("aggregates OI per strike across every expiration for the window set", () => {
  const aggregate = buildAggregateOiLevelSet(levelSets);

  assert.equal(aggregate.expiry, OI_WINDOW_LEVEL_SET_EXPIRY);
  // Nearest expiry (2 DTE) supplies daysToExpiration and the ATM strike.
  assert.equal(aggregate.daysToExpiration, 2);
  assert.equal(aggregate.atmStrike, 200);
  // Calls: 205 (10k) > 207.5 (5k) > 210 + 260 (2k each, lower strike first).
  assert.deepEqual(aggregate.callLevels.map((level) => level.strike), [205, 207.5, 210, 260]);
  assert.deepEqual(aggregate.callLevels.map((level) => level.openInterest), [10_000, 5_000, 2_000, 2_000]);
  // Puts keep per-strike sums and rank ties by higher strike first.
  assert.deepEqual(aggregate.putLevels.map((level) => level.strike), [195, 205, 190]);
  assert.deepEqual(aggregate.putLevels.map((level) => level.openInterest), [9_000, 3_000, 1_800]);
});

test("window set keeps the dominant expiry for duplicate strikes", () => {
  const duplicatedStrikeSets = [
    ...levelSets,
    {
      expiry: "2026-08-14",
      daysToExpiration: 16,
      atmStrike: 200,
      callLevels: [{ strike: 205, openInterest: 4_000, volume: 100 }],
      putLevels: [{ strike: 195, openInterest: 2_500, volume: 100 }],
    },
  ];
  const aggregate = buildAggregateOiLevelSet(duplicatedStrikeSets);
  // 205 calls exist on 07-31 (10k) and 08-14 (4k): the wall is attributed to
  // the dominant 07-31 expiry with its own OI, not a cross-expiry sum.
  assert.equal(aggregate.callLevels[0].strike, 205);
  assert.equal(aggregate.callLevels[0].openInterest, 10_000);
  assert.equal(aggregate.callLevels[0].expiry, "2026-07-31");
  assert.equal(aggregate.putLevels[0].strike, 195);
  assert.equal(aggregate.putLevels[0].openInterest, 9_000);
  assert.equal(aggregate.putLevels[0].expiry, "2026-07-31");

  const model = buildHighOiLevelModel({
    levelSets: [aggregate, ...duplicatedStrikeSets],
    requestedExpiry: OI_WINDOW_LEVEL_SET_EXPIRY,
    underlyingPrice: 200,
  });
  assert.equal(model.expiry, OI_WINDOW_LEVEL_SET_EXPIRY);
  assert.equal(model.callLevels[0].openInterest, 10_000);
  assert.equal(model.callLevels[0].tier, 5);
  // Chart bubbles carry the dominant expiry, e.g. "C S 10K 7/31".
  assert.equal(model.callLevels[0].displayTitle.endsWith(" 7/31"), true);
});

test("aggregate window set keeps the full ranking and skips empty input", () => {
  assert.equal(buildAggregateOiLevelSet([]), null);
  assert.equal(buildAggregateOiLevelSet(undefined), null);
  // The aggregate never applies the trader's display cap: the cap belongs to
  // the model's directional pass, which knows which side of spot a strike is
  // on. Every ranked strike stays available here.
  const aggregate = buildAggregateOiLevelSet(levelSets);
  assert.equal(aggregate.callLevels.length, 4);
  // A previously built window set must not aggregate into itself again.
  const doubled = buildAggregateOiLevelSet([aggregate, ...levelSets]);
  assert.equal(doubled.callLevels[0].openInterest, 10_000);
});

test("directional window model keeps above-spot call walls that big below-spot OI would displace", () => {
  // PLTR regression (2026-08-10): spot 172.4, call OI concentrated BELOW spot
  // (170/167.5/165...), moderate real walls above (175/180). With the display
  // cap applied inside the aggregate, below-spot strikes consumed every call
  // slot and the directional chart lost 175 entirely while MomoX showed it.
  const pltrLike = [{
    expiry: "2026-08-21",
    daysToExpiration: 11,
    atmStrike: 172.5,
    callLevels: [
      { strike: 170, openInterest: 8_705, volume: 29_927 },
      { strike: 167.5, openInterest: 8_342, volume: 11_267 },
      { strike: 165, openInterest: 7_111, volume: 14_199 },
      { strike: 160, openInterest: 6_670, volume: 7_402 },
      { strike: 180, openInterest: 5_038, volume: 19_983 },
      { strike: 175, openInterest: 4_541, volume: 15_682 },
      { strike: 162.5, openInterest: 9_639, volume: 2_945 },
      { strike: 157.5, openInterest: 4_176, volume: 870 },
      { strike: 182.5, openInterest: 3_100, volume: 6_104 },
    ],
    putLevels: [{ strike: 150, openInterest: 3_267, volume: 500 }],
  }];
  const aggregate = buildAggregateOiLevelSet(pltrLike);
  const model = buildHighOiLevelModel({
    levelSets: [aggregate, ...pltrLike],
    requestedExpiry: OI_WINDOW_LEVEL_SET_EXPIRY,
    underlyingPrice: 172.4,
    maxPerSide: 8,
    directional: true,
  });
  const callStrikes = model.callLevels.map((level) => level.strike);
  assert.deepEqual(callStrikes, [175, 180, 182.5]);
  assert.equal(model.callLevels.every((level) => level.strike > 172.4), true);
});

test("uses the requested option expiry for chart OI levels", () => {
  const model = buildHighOiLevelModel({
    levelSets,
    requestedExpiry: "2026-08-07",
    underlyingPrice: 200,
  });

  assert.equal(model.expiry, "2026-08-07");
  assert.deepEqual(model.callLevels.map((level) => level.price), [210]);
  assert.deepEqual(model.putLevels.map((level) => level.price), [190]);
});

test("ranks and styles high OI levels in the five TOS tiers", () => {
  const model = buildHighOiLevelModel({
    levelSets,
    requestedExpiry: "2026-07-31",
    underlyingPrice: 200,
    maxDistancePercent: 20,
  });

  assert.equal(model.callLevels[0].strength, "strong");
  assert.equal(model.callLevels[0].tier, 5);
  assert.equal(model.callLevels[0].lineWidth, 4);
  assert.equal(model.callLevels[0].tosLineWeight, 5);
  assert.equal(model.callLevels[0].lineStyle, 3);
  assert.equal(model.callLevels[0].color, "#f23645");
  assert.equal(model.callLevels[1].strength, "moderate");
  assert.equal(model.callLevels[1].tier, 5);
  assert.equal(model.callLevels[1].color, "#f23645");
  assert.equal(model.callLevels[1].lineWidth, 4);
  assert.equal(model.callLevels[1].lineStyle, 3);
  assert.equal(model.callLevels[1].displayTitle, "C M 5K");
  assert.equal(model.callLevels[2].strength, "weak");
  assert.equal(model.callLevels[2].tier, 5);
  assert.equal(model.callLevels[2].color, "#8c2a30");
  assert.equal(model.callLevels[2].lineWidth, 4);
  assert.equal(model.callLevels[2].lineStyle, 2);
  assert.equal(model.callLevels[2].visible, false);
  assert.match(model.callLevels[0].title, /C OI T5 205 · 10K/);
});

test("combines call and put labels when both have high OI at one strike", () => {
  const model = buildHighOiLevelModel({
    levelSets,
    requestedExpiry: "2026-07-31",
    underlyingPrice: 200,
  });
  const shared = model.visibleLevels.find((level) => level.price === 205);

  assert.deepEqual(shared.sides, ["C", "P"]);
  assert.match(shared.title, /C OI T5/);
  assert.match(shared.title, /P OI T5/);
  assert.equal(shared.color, "#f23645");
});

test("uses the same strong, moderate, and weak thresholds in every OI surface", () => {
  assert.equal(classifyHighOiStrength(660, 1_000), "strong");
  assert.equal(classifyHighOiStrength(659, 1_000), "moderate");
  assert.equal(classifyHighOiStrength(330, 1_000), "moderate");
  assert.equal(classifyHighOiStrength(329, 1_000), "weak");
});

test("keeps option-chain and heatmap models strength-only", () => {
  const model = buildHighOiLevelModel({
    levelSets,
    requestedExpiry: "2026-07-31",
    underlyingPrice: 200,
    presentation: "strength",
  });

  assert.deepEqual(
    model.callLevels.map((level) => level.strength),
    ["strong", "moderate", "weak"],
  );
  assert.deepEqual(
    model.callLevels.map((level) => level.tier),
    [null, null, null],
  );
  assert.deepEqual(
    model.callLevels.map((level) => level.color),
    ["#22c55e", "#84cc16", "#166534"],
  );
  assert.match(model.callLevels[0].title, /C OI STRONG/);
  assert.doesNotMatch(model.callLevels[0].title, /T5/);
});

test("does not substitute another expiry when the requested chart expiry is missing", () => {
  const model = buildHighOiLevelModel({
    levelSets,
    requestedExpiry: "2026-07-29",
    underlyingPrice: 335,
  });

  assert.equal(model.expiry, "");
  assert.deepEqual(model.allLevels, []);
});

test("rebuilds the exact selected-expiry chart levels from full-chain rows", () => {
  const exactLevelSet = buildHighOiLevelSetFromRows({
    requestedExpiry: "2026-07-29",
    atmStrike: 335,
    rows: [
      { expiry: "2026-07-29", side: "CALL", strike: 330, open_interest: 1_995, volume: 3_305, delta: 1 },
      { expiry: "2026-07-29", side: "CALL", strike: 332.5, open_interest: 1_547, volume: 8_164, delta: 0.94 },
      { expiry: "2026-07-29", side: "CALL", strike: 335, open_interest: 2_835, volume: 33_973, delta: 0.72 },
      { expiry: "2026-07-29", side: "CALL", strike: 337.5, open_interest: 926, volume: 27_321, delta: 0.38 },
      { expiry: "2026-07-29", side: "CALL", strike: 340, open_interest: 2_005, volume: 33_326, delta: 0.13 },
      { expiry: "2026-07-29", side: "CALL", strike: 342.5, open_interest: 1_620, volume: 18_016, delta: 0.03 },
      { expiry: "2026-07-29", side: "CALL", strike: 345, open_interest: 2_358, volume: 11_170, delta: 0.01 },
      { expiry: "2026-07-31", side: "CALL", strike: 400, open_interest: 12_245, volume: 100, delta: 0.01 },
    ],
  });
  const model = buildHighOiLevelModel({
    levelSets: [exactLevelSet, ...levelSets],
    requestedExpiry: "2026-07-29",
    underlyingPrice: 335,
  });
  const level342 = model.callLevels.find((level) => level.strike === 342.5);

  assert.deepEqual(model.callLevels.map((level) => level.strike), [335, 345, 340, 330, 342.5, 332.5, 337.5]);
  assert.equal(level342.strength, "moderate");
  assert.equal(level342.tier, 4);
  assert.equal(level342.color, "#f23645");
  assert.equal(level342.displayTitle, "C M 1.6K");
  assert.equal(level342.visible, true);
});

test("uses bright and secondary TOS colors instead of dark weak walls", () => {
  const calls = Array.from({ length: 15 }, (_, index) => ({
    strike: 200 + index,
    openInterest: 15_000 - index * 500,
    volume: 1_000 - index,
  }));
  const model = buildHighOiLevelModel({
    levelSets: [{
      expiry: "2026-07-31",
      callLevels: calls,
      putLevels: [],
    }],
    requestedExpiry: "2026-07-31",
    underlyingPrice: 207,
    maxPerSide: 15,
  });

  assert.equal(model.callLevels.length, 15);
  assert.deepEqual(
    [0, 3, 6, 9, 12].map((index) => model.callLevels[index].tier),
    [5, 4, 3, 2, 1],
  );
  assert.deepEqual(
    [0, 3, 6, 9, 12].map((index) => model.callLevels[index].lineWidth),
    // MomoX-bold tier widths: leading walls 4/3, remaining tiers 2.
    [4, 3, 2, 2, 2],
  );
  assert.deepEqual(
    [0, 3, 6, 9, 12].map((index) => model.callLevels[index].lineStyle),
    [3, 3, 3, 2, 2],
  );
  assert.equal(model.callLevels[8].color, "#f23645");
  assert.equal(model.callLevels[9].color, "#8c2a30");
});

test("keeps the nearby 272.5 weak OI wall inside the chart price range", () => {
  const model = buildHighOiLevelModel({
    levelSets: [{
      expiry: "2026-07-31",
      callLevels: [
        { strike: 265, openInterest: 40_679, volume: 66_417 },
        { strike: 280, openInterest: 33_690, volume: 47_743 },
        { strike: 260, openInterest: 26_476, volume: 24_755 },
        { strike: 270, openInterest: 22_667, volume: 99_599 },
        { strike: 275, openInterest: 13_245, volume: 49_087 },
        { strike: 262.5, openInterest: 8_746, volume: 6_172 },
        { strike: 267.5, openInterest: 4_929, volume: 29_629 },
        { strike: 272.5, openInterest: 4_138, volume: 42_472 },
      ],
      putLevels: [],
    }],
    requestedExpiry: "2026-07-31",
    underlyingPrice: 265,
    maxPerSide: 15,
  });
  const weak272 = model.visibleLevels.find((level) => level.price === 272.5);
  const fittedRange = expandPriceRangeForNearbyOiLevels({
    low: 263.5,
    high: 268.25,
    referencePrice: 265,
    levels: model.visibleLevels,
  });

  assert.equal(weak272?.strength, "weak");
  assert.equal(weak272?.lineStyle, 2);
  assert.equal(weak272?.lineWidth, 2);
  assert.ok(fittedRange.includedPrices.includes(272.5));
  assert.ok(fittedRange.high >= 272.5);
  assert.ok(!fittedRange.includedPrices.includes(280));
});

test("keeps a displayed 272.5 OTM wall when the full-chain top list omits it", () => {
  const fullChainModel = buildHighOiLevelModel({
    levelSets: [{
      expiry: "2026-07-31",
      callLevels: [
        { strike: 265, openInterest: 40_679, volume: 66_417 },
        { strike: 270, openInterest: 22_667, volume: 99_599 },
        { strike: 275, openInterest: 13_245, volume: 49_087 },
        { strike: 272.5, openInterest: 4_138, volume: 42_472 },
      ],
      putLevels: [],
    }],
    requestedExpiry: "2026-07-31",
    underlyingPrice: 270.27,
    maxPerSide: 3,
  });
  const displayedOtmModel = buildHighOiLevelModel({
    levelSets: [{
      expiry: "2026-07-31",
      callLevels: [{ strike: 272.5, openInterest: 4_138, volume: 42_472 }],
      putLevels: [],
    }],
    requestedExpiry: "2026-07-31",
    underlyingPrice: 270.27,
  });
  const merged = mergeOiChartLevelSources([
    fullChainModel.visibleLevels,
    displayedOtmModel.visibleLevels,
  ]);

  assert.equal(fullChainModel.visibleLevels.some((level) => level.price === 272.5), false);
  assert.equal(merged.some((level) => level.price === 272.5), true);
});

test("promotes the next live OTM resistance and support after price crosses a strike", () => {
  const levelSet = buildHighOiLevelSetFromRows({
    requestedExpiry: "2026-07-31",
    deferLimit: true,
    maxPerSide: 2,
    rows: [
      { expiry: "2026-07-31", side: "CALL", strike: 240, open_interest: 40_000, volume: 4_000 },
      { expiry: "2026-07-31", side: "CALL", strike: 250, open_interest: 50_000, volume: 5_000 },
      { expiry: "2026-07-31", side: "CALL", strike: 265, open_interest: 60_000, volume: 6_000 },
      { expiry: "2026-07-31", side: "CALL", strike: 270, open_interest: 10_000, volume: 1_000 },
      { expiry: "2026-07-31", side: "CALL", strike: 280, open_interest: 8_000, volume: 800 },
      { expiry: "2026-07-31", side: "PUT", strike: 280, open_interest: 60_000, volume: 6_000 },
      { expiry: "2026-07-31", side: "PUT", strike: 270, open_interest: 50_000, volume: 5_000 },
      { expiry: "2026-07-31", side: "PUT", strike: 265, open_interest: 12_000, volume: 1_200 },
      { expiry: "2026-07-31", side: "PUT", strike: 260, open_interest: 9_000, volume: 900 },
    ],
  });
  const model = buildHighOiLevelModel({
    levelSets: [levelSet],
    requestedExpiry: "2026-07-31",
    underlyingPrice: 267.10,
    maxPerSide: 2,
    presentation: "strength",
    directional: true,
  });

  assert.deepEqual(model.callLevels.map((level) => level.strike), [270, 280]);
  assert.deepEqual(model.putLevels.map((level) => level.strike), [265, 260]);
  assert.equal(model.callLevels[0].strength, "strong");
  assert.equal(model.putLevels[0].strength, "strong");
});

test("orders directional resistance and support by nearest strike without changing OI rank", () => {
  const model = buildHighOiLevelModel({
    levelSets: [{
      expiry: "2026-07-31",
      callLevels: [
        { strike: 280, openInterest: 33_690, volume: 22_984 },
        { strike: 270, openInterest: 22_667, volume: 41_205 },
        { strike: 275, openInterest: 13_245, volume: 14_047 },
      ],
      putLevels: [
        { strike: 200, openInterest: 16_191, volume: 1_313 },
        { strike: 220, openInterest: 9_527, volume: 5_672 },
        { strike: 230, openInterest: 8_538, volume: 2_367 },
      ],
    }],
    requestedExpiry: "2026-07-31",
    underlyingPrice: 269.45,
    maxPerSide: 3,
    presentation: "strength",
    directional: true,
  });

  assert.deepEqual(model.callLevels.map((level) => level.strike), [270, 275, 280]);
  assert.deepEqual(model.putLevels.map((level) => level.strike), [230, 220, 200]);
  assert.equal(model.callLevels.find((level) => level.strike === 280)?.rank, 1);
  assert.equal(model.callLevels.find((level) => level.strike === 280)?.strength, "strong");
});

test("keeps a crossed OI wall on the chart while live cards promote the next level", () => {
  const levelSetsForCrossing = [{
    expiry: "2026-07-31",
    callLevels: [
      { strike: 270, openInterest: 30_000, volume: 5_000 },
      { strike: 275, openInterest: 20_000, volume: 4_000 },
    ],
    putLevels: [],
  }];
  const cards = buildHighOiLevelModel({
    levelSets: levelSetsForCrossing,
    requestedExpiry: "2026-07-31",
    underlyingPrice: 271,
    directional: true,
  });
  const chart = buildHighOiLevelModel({
    levelSets: levelSetsForCrossing,
    requestedExpiry: "2026-07-31",
    underlyingPrice: 271,
    directional: false,
  });

  assert.deepEqual(cards.callLevels.map((level) => level.strike), [275]);
  assert.deepEqual(chart.callLevels.map((level) => level.strike), [270, 275]);
  assert.ok(chart.visibleLevels.some((level) => level.strike === 270));
});
