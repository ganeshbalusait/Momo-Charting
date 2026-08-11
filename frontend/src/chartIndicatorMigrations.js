import { CLOUD_BAND_STUDIES } from "./cloudBandStudy.js";

export const TOS_MTF_SIGNAL_VISIBILITY_VERSION = "tos-mtf-signals-visible-v2";

export function migrateTosMtfSignalVisibility(saved = {}) {
  if (saved.mtfSignalVisibilityVersion === TOS_MTF_SIGNAL_VISIBILITY_VERSION) return {};
  return {
    mtfSignalVisibilityVersion: TOS_MTF_SIGNAL_VISIBILITY_VERSION,
    // The v1 migration covered only the two legacy intraday families. Upgrade
    // every existing browser/layout profile once so the three authoritative
    // D→M backend tapes cannot remain silently hidden after deployment.
    signals48: true,
    signals920: true,
    ganesh48HigherSignals: true,
    ganesh920HigherSignals: true,
    ganeshMacdHigherSignals: true,
  };
}

export const PERSONS_PIVOT_VISIBILITY_VERSION = "persons-pivots-visible-v1";

// Profiles saved before the Persons ranges were MomoX-styled often disabled
// the study; the wPR/mPR lines can never render while it is off, so upgrade
// every stored profile once.
export function migratePersonsPivotVisibility(saved = {}) {
  if (saved.personsPivotVisibilityVersion === PERSONS_PIVOT_VISIBILITY_VERSION) return {};
  return {
    personsPivotVisibilityVersion: PERSONS_PIVOT_VISIBILITY_VERSION,
    personsPivots: true,
  };
}

export const MOMOX_ONCHART_PALETTE_VERSION = "momox-onchart-palette-v11";

// Colours below are transcribed from the trader's actual MomoX ThinkScripts
// (shared_RelVol_Candles_v324, shared_Cloud_Signal_EMA_v20263,
// shared_Signal_MTF_EMA48/920, Ganesh_EMA_4x8_D_2D_3D_4D_W_M) and the
// generated TOS OI-levels study — apple-to-apple, not a reinterpretation.
// The v1 pass wrongly forced everything to cyan/magenta and darkened the
// chart; v2 restores the script-exact hues and instead raises the cloud
// fill opacities, which is what actually makes TOS MomoX look bright.
const MOMOX = Object.freeze({
  cyan: "#00ffff",         // Color.CYAN — bull candles, clouds, 9/20 calls
  magenta: "#ff00ff",      // Color.MAGENTA — bear candles, clouds, 9/20 puts
  white: "#ffffff",        // Color.WHITE — high squeeze, Keltner high
  yellow: "#ffff00",       // Color.YELLOW — EMA 21
  gold: "#fff200",         // squeeze cloud-band gold (app-normalized 255,218,0)
  // TOS draws ONE squeeze channel per chart, so its gold fill sits on black at
  // a single low alpha. The app stacks all seven CloudBand timeframes, and
  // seven 18% gold layers composite to roughly 75% coverage — that is why the
  // script-exact gold read as a flat bright olive slab rather than a cloud.
  // Pre-dimming the fill hue compensates for the stacking so a fully-overlapped
  // region lands near the single-layer TOS tone instead of far above it.
  cloudGold: "#c9b200",
  orange: "#ffc800",       // TOS Color.ORANGE (255,200,0) — Keltner mid
  violet: "#ee82ee",       // Color.VIOLET — Keltner low
  darkGreen: "#006400",    // Color.DARK_GREEN — SMA 200
  lime: "#a9ff00",         // CreateColor(169,255,0) — 4/8 CALL
  pink: "#ff006a",         // CreateColor(255,0,106) — 4/8 PUT
  green: "#00ff00",        // Color.GREEN — MACD higher-TF CALL
  red: "#ff0000",          // Color.RED — MACD higher-TF PUT
  compactCall: "#00afaf",  // CreateColor(0,175,175) — compact 9/20 C bubbles
  compactPut: "#be00be",   // CreateColor(190,0,190) — compact 9/20 P bubbles
});

// Upper-pane studies only. The four lower panes (squeeze momentum, MTF ADX,
// MTF squeeze 4-10, MTF cloud labels) intentionally keep their own colours.
// Every key v1 stamped into saved profiles must reappear here so the v2
// migration can move those profiles to the script-exact values.
export const MOMOX_ONCHART_PALETTE_OVERRIDES = Object.freeze({
  // MomoX MA ribbon: 9 magenta, 21 yellow, 50 cyan, 200 dark green.
  ema9Color: MOMOX.magenta,
  ema21Color: MOMOX.yellow,
  ema50Color: MOMOX.cyan,
  sma200Color: MOMOX.darkGreen,
  vwapColor: MOMOX.white,
  // Generated TOS OI study: Call (0,153,204) / Put (204,0,102) + weak tiers.
  oiLevelsCallColor: "#0099cc",
  oiLevelsCallModerateColor: "#0099cc",
  oiLevelsCallWeakColor: "#007299",
  oiLevelsPutColor: "#cc0066",
  oiLevelsPutModerateColor: "#cc0066",
  oiLevelsPutWeakColor: "#9b004c",
  // TOS renders AddCloud fills at a low alpha over the black background, so
  // the MomoX clouds READ as muted dark teal / maroon / olive even though the
  // script colours are bright. The app's original slightly-muted cloud hues at
  // the original low opacities reproduce that exact look; the v2 pass that
  // raised opacity to 30-36 made the chart neon and is deliberately undone.
  cloudBullColor: "#00b8b0",
  cloudBearColor: "#d90078",
  cloudOpacity: 24,
  mtfCloudBullColor: "#00d7ff",
  mtfCloudBearColor: "#ff008c",
  mtfCloudOpacity: 20,
  mtfMacdCloudBullColor: "#00d7ff",
  mtfMacdCloudBearColor: "#ff0050",
  mtfMacdCloudOpacity: 20,
  mtfEma920BullColor: MOMOX.cyan,
  mtfEma920BearColor: MOMOX.magenta,
  mtfEma920Opacity: 20,
  mtfSqueezeBullColor: MOMOX.cyan,
  mtfSqueezeBearColor: MOMOX.magenta,
  mtfSqueezeOpacity: 20,
  relVolBullColor: MOMOX.cyan,
  relVolBearColor: MOMOX.magenta,
  cloudMaxBullColor: MOMOX.cyan,
  cloudMaxBearColor: MOMOX.magenta,
  cloudMaxCloudOpacity: 20,
  // Ichimoku study keeps its original TOS-profile colours.
  ichimokuTenkanColor: "#74bde8",
  ichimokuKijunColor: "#ee82ee",
  ichimokuSpanAColor: "#d1d1d5",
  ichimokuChikouColor: "#0000ff",
  ichimokuSpanBBullColor: MOMOX.cyan,
  ichimokuSpanBBearColor: MOMOX.magenta,
  ichimokuCloudBullColor: "#74bde8",
  ichimokuCloudBearColor: "#ee82ee",
  ichimokuBullArrowColor: "#90ee90",
  ichimokuBearArrowColor: "#ee82ee",
  ichimokuBullLabelColor: "#00b2b2",
  ichimokuBearLabelColor: "#993c99",
  ichimokuCloudOpacity: 20,
  autoFibAbove50Color: MOMOX.cyan,
  autoFibBelow50Color: MOMOX.magenta,
  autoFibAboveGoldColor: "#00a8ff",
  autoFibBelowGoldColor: "#e5005f",
  autoFibCloudBullColor: "#00bfff",
  autoFibCloudBearColor: MOMOX.pink,
  autoFibHighColor: MOMOX.green,
  autoFibLowColor: MOMOX.red,
  autoFibCloudOpacity: 20,
  mtfMaVioletColor: "#ff66ff",
  mtfMaGoldColor: "#e7be00",
  mtfMaAquaColor: MOMOX.cyan,
  mtfMaGreenColor: "#00c531",
  mtfMaBlueColor: "#74bde8",
  mtfMaMintColor: "#8fefbf",
  pivotPointsResistanceColor: MOMOX.red,
  pivotPointsPivotColor: MOMOX.white,
  pivotPointsSupportColor: MOMOX.green,
  // The built-in TOS PivotPoints study shows all seven levels and the full
  // stepped history by default (script: `input showOnlyToday = No;`).
  pivotPointsR3: true,
  pivotPointsS2: true,
  pivotPointsS3: true,
  pivotPointsShowOnlyToday: false,
  // TOS signal scripts default to `sessionsBack = 1` (today's RTH only); a
  // merge-order bug froze upgraded profiles at 5, so re-stamp once here.
  signalSessionsBack: 1,
  // Persons pivot ranges (dPR/wPR/mPR): TOS GetColor(5)/GetColor(6) red and
  // green, flipping per bar by side of price. MomoX keeps the weekly and
  // monthly ranges on — that green dashed wPR line on MSFT.
  personsPivotsResistanceColor: MOMOX.red,
  personsPivotsPivotColor: MOMOX.white,
  personsPivotsSupportColor: MOMOX.green,
  personsPivotsWeek: true,
  personsPivotsMonth: true,
  // Script-exact signal colours: 4/8 lime/pink, 9/20 cyan/magenta with the
  // darker compact bubble pair, higher-TF MACD green/red.
  signal48CallColor: MOMOX.lime,
  signal48PutColor: MOMOX.pink,
  signal920CallColor: MOMOX.cyan,
  signal920PutColor: MOMOX.magenta,
  signal920CompactCallColor: MOMOX.compactCall,
  signal920CompactPutColor: MOMOX.compactPut,
  ganesh48CallColor: MOMOX.lime,
  ganesh48PutColor: MOMOX.pink,
  ganesh920CallColor: MOMOX.cyan,
  ganesh920PutColor: MOMOX.magenta,
  ganeshMacdCallColor: MOMOX.green,
  ganeshMacdPutColor: MOMOX.red,
  // MomoX colours prior-period levels by side of price: below price (price
  // holding above the level) cyan, above price magenta; the prior-month
  // ceiling paints TOS orange-red like the reference pMH line.
  previousDayBullColor: MOMOX.cyan,
  previousDayBearColor: MOMOX.magenta,
  previousDayNeutralColor: "#a0a0ab",
  previousWeekBullColor: MOMOX.cyan,
  previousWeekBearColor: MOMOX.magenta,
  previousWeekNeutralColor: "#a0a0ab",
  previousMonthBullColor: MOMOX.cyan,
  previousMonthBearColor: "#ff5500",
  previousMonthNeutralColor: "#a0a0ab",
  sessionLineColor: "#c0bffe",
  // The trader's TOS profile runs every session-windowed study from the
  // 4:00 AM ET premarket open, not the 5:00 AM script default.
  signalRthStartTime: 400,
  mtfCloudRthStartTime: 400,
  mtfMacdCloudRthStartTime: 400,
  mtfEma920RthStartTime: 400,
  mtfSqueezeRthStartTime: 400,
  autoFibRthStartTime: 400,
  // MomoX paints signals, clouds, and bands across the whole loaded history
  // (scrolling to an older day still shows the full study picture), so the
  // last-N-sessions limits are off everywhere.
  signalLimitRecentSessions: false,
  mtfCloudLimitRecentSessions: false,
  mtfMacdCloudLimitRecentSessions: false,
  mtfEma920LimitRecentSessions: false,
  mtfSqueezeLimitRecentSessions: false,
  ichimokuLimitRecentSessions: false,
  autoFibLimitRecentSessions: false,
  // Squeeze channel bands: white high band over the MomoX gold mid/low fill.
  // 22% then 18% opacity at the script-exact gold both still washed the pane
  // olive, because the brightness comes from seven stacked timeframes rather
  // than from any single band. v11 therefore attacks both terms — the dimmed
  // cloudGold hue and 11% alpha — which lands a seven-deep overlap near the
  // single-layer TOS tone. Raising opacity alone (the rejected 30% pass) moves
  // in exactly the wrong direction.
  ...Object.fromEntries(CLOUD_BAND_STUDIES.flatMap(({ indicatorKey }) => [
    [`${indicatorKey}HighColor`, MOMOX.white],
    [`${indicatorKey}MidColor`, MOMOX.cloudGold],
    [`${indicatorKey}LowColor`, MOMOX.cloudGold],
    [`${indicatorKey}Opacity`, 11],
    [`${indicatorKey}RthStartTime`, 400],
    [`${indicatorKey}LimitRecentSessions`, false],
  ])),
});

export function migrateMomoxOnChartPalette(saved = {}) {
  if (saved.momoxOnChartPaletteVersion === MOMOX_ONCHART_PALETTE_VERSION) return {};
  return {
    momoxOnChartPaletteVersion: MOMOX_ONCHART_PALETTE_VERSION,
    ...MOMOX_ONCHART_PALETTE_OVERRIDES,
  };
}
