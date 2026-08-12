import test from "node:test";
import assert from "node:assert/strict";

import {
  MOMOX_ONCHART_PALETTE_OVERRIDES,
  MOMOX_ONCHART_PALETTE_VERSION,
  TOS_MTF_SIGNAL_VISIBILITY_VERSION,
  migrateMomoxOnChartPalette,
  migrateTosMtfSignalVisibility,
} from "./chartIndicatorMigrations.js";

test("upgrades a v1 profile by enabling every intraday and higher-timeframe signal family", () => {
  assert.deepEqual(
    migrateTosMtfSignalVisibility({
      mtfSignalVisibilityVersion: "tos-mtf-signals-visible-v1",
      signals48: false,
      signals920: false,
      ganesh48HigherSignals: false,
      ganesh920HigherSignals: false,
      ganeshMacdHigherSignals: false,
    }),
    {
      mtfSignalVisibilityVersion: TOS_MTF_SIGNAL_VISIBILITY_VERSION,
      signals48: true,
      signals920: true,
      ganesh48HigherSignals: true,
      ganesh920HigherSignals: true,
      ganeshMacdHigherSignals: true,
    },
  );
});

test("respects signal visibility choices after the v2 profile has been migrated", () => {
  assert.deepEqual(
    migrateTosMtfSignalVisibility({
      mtfSignalVisibilityVersion: TOS_MTF_SIGNAL_VISIBILITY_VERSION,
      ganeshMacdHigherSignals: false,
    }),
    {},
  );
});

test("upgrades legacy and v1 profiles to the ThinkScript-exact MomoX palette", () => {
  const migrated = migrateMomoxOnChartPalette({
    momoxOnChartPaletteVersion: "momox-onchart-palette-v1",
    ema9Color: "#d946ef",
    ema21Color: "#ffc800",
    signal48CallColor: "#00ffff",
    cloudOpacity: 24,
  });
  assert.equal(migrated.momoxOnChartPaletteVersion, MOMOX_ONCHART_PALETTE_VERSION);
  // shared_RelVol_Candles_v324 MA ribbon: 9 magenta, 21 yellow, 200 dark green.
  assert.equal(migrated.ema9Color, "#ff00ff");
  assert.equal(migrated.ema21Color, "#ffff00");
  assert.equal(migrated.sma200Color, "#006400");
  // 4/8 signals are CreateColor(169,255,0)/(255,0,106), not cyan/magenta.
  assert.equal(migrated.signal48CallColor, "#a9ff00");
  assert.equal(migrated.signal48PutColor, "#ff006a");
  assert.equal(migrated.signal920CompactCallColor, "#00afaf");
  // Generated TOS OI study call/put wall colours.
  assert.equal(migrated.oiLevelsCallColor, "#0099cc");
  assert.equal(migrated.oiLevelsPutColor, "#cc0066");
  // MomoX clouds read muted over black: original hues at original opacity.
  assert.equal(migrated.cloudOpacity, 24);
  assert.equal(migrated.cloudBullColor, "#00b8b0");
  assert.equal(migrated.cloudBearColor, "#d90078");
  // Squeeze bands use the pre-dimmed cloudGold, not the script-exact gold: all
  // seven CloudBand timeframes stack, so the composited fill — not one band —
  // is what has to match the single-channel TOS tone.
  assert.equal(migrated.cloudBandsDayMidColor, "#c9b200");
  assert.equal(migrated.cloudBandsDayOpacity, 11);
  assert.equal(migrated.mtfCloudBullColor, "#00d7ff");
  assert.equal(migrated.mtfCloudBearColor, "#ff008c");
  assert.equal(migrated.mtfCloudOpacity, 20);
  // Session-windowed studies follow the trader's 4:00 AM ET premarket start.
  assert.equal(migrated.signalRthStartTime, 400);
  assert.equal(migrated.mtfCloudRthStartTime, 400);
  assert.equal(migrated.cloudBands5mRthStartTime, 400);
  assert.equal(migrated.cloudBandsDayRthStartTime, 400);
  // Prior-period levels: cyan/magenta by side, orange prior-month ceiling.
  assert.equal(migrated.previousDayBullColor, "#00ffff");
  assert.equal(migrated.previousDayBearColor, "#ff00ff");
  assert.equal(migrated.previousMonthBearColor, "#ff5500");
  // Persons pivot ranges: weekly/monthly on, TOS red/green side colours.
  assert.equal(migrated.personsPivotsWeek, true);
  assert.equal(migrated.personsPivotsMonth, true);
  assert.equal(migrated.personsPivotsResistanceColor, "#ff0000");
  assert.equal(migrated.personsPivotsSupportColor, "#00ff00");
});

test("covers every option key the v1 palette pass stamped into profiles", () => {
  const v1Keys = [
    "ema9Color", "ema21Color", "ema50Color", "sma200Color", "vwapColor",
    "oiLevelsCallColor", "oiLevelsCallModerateColor", "oiLevelsCallWeakColor",
    "oiLevelsPutColor", "oiLevelsPutModerateColor", "oiLevelsPutWeakColor",
    "cloudBullColor", "cloudBearColor", "mtfCloudBullColor", "mtfCloudBearColor",
    "mtfMacdCloudBullColor", "mtfMacdCloudBearColor", "mtfEma920BullColor",
    "mtfEma920BearColor", "mtfSqueezeBullColor", "mtfSqueezeBearColor",
    "relVolBullColor", "relVolBearColor", "cloudMaxBullColor", "cloudMaxBearColor",
    "ichimokuTenkanColor", "ichimokuKijunColor", "ichimokuSpanAColor",
    "ichimokuChikouColor", "ichimokuSpanBBullColor", "ichimokuSpanBBearColor",
    "ichimokuCloudBullColor", "ichimokuCloudBearColor", "ichimokuBullArrowColor",
    "ichimokuBearArrowColor", "ichimokuBullLabelColor", "ichimokuBearLabelColor",
    "autoFibAbove50Color", "autoFibBelow50Color", "autoFibAboveGoldColor",
    "autoFibBelowGoldColor", "autoFibCloudBullColor", "autoFibCloudBearColor",
    "autoFibHighColor", "autoFibLowColor", "mtfMaVioletColor", "mtfMaGoldColor",
    "mtfMaAquaColor", "mtfMaGreenColor", "mtfMaBlueColor", "mtfMaMintColor",
    "pivotPointsResistanceColor", "pivotPointsPivotColor", "pivotPointsSupportColor",
    "personsPivotsResistanceColor", "personsPivotsPivotColor", "personsPivotsSupportColor",
    "signal48CallColor", "signal48PutColor", "signal920CallColor", "signal920PutColor",
    "signal920CompactCallColor", "signal920CompactPutColor", "ganesh48CallColor",
    "ganesh48PutColor", "ganesh920CallColor", "ganesh920PutColor",
    "ganeshMacdCallColor", "ganeshMacdPutColor", "previousDayBullColor",
    "previousDayBearColor", "previousDayNeutralColor", "previousWeekBullColor",
    "previousWeekBearColor", "previousWeekNeutralColor", "previousMonthBullColor",
    "previousMonthBearColor", "previousMonthNeutralColor", "sessionLineColor",
    "cloudBands5mHighColor", "cloudBands5mMidColor", "cloudBands5mLowColor",
    "cloudBandsDayHighColor", "cloudBandsDayMidColor", "cloudBandsDayLowColor",
  ];
  v1Keys.forEach((key) => {
    assert.ok(
      Object.prototype.hasOwnProperty.call(MOMOX_ONCHART_PALETTE_OVERRIDES, key),
      `v2 must re-stamp ${key} so v1 profiles are corrected`,
    );
  });
});

test("does not touch lower-pane study colours", () => {
  const lowerPrefixes = ["squeezeMomentum", "mtfAdx", "mtfSqueeze410", "mtfCloudLabel"];
  Object.keys(MOMOX_ONCHART_PALETTE_OVERRIDES).forEach((key) => {
    assert.ok(
      !lowerPrefixes.some((prefix) => key.startsWith(prefix)),
      `${key} belongs to a lower-pane study`,
    );
  });
});

test("respects saved colour edits once the MomoX palette version is stored", () => {
  assert.deepEqual(
    migrateMomoxOnChartPalette({
      momoxOnChartPaletteVersion: MOMOX_ONCHART_PALETTE_VERSION,
      ema9Color: "#123456",
    }),
    {},
  );
});
