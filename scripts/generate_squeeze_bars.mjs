// Deterministic SYNTHETIC 5-minute tape for the squeeze-release golden fixture.
//
// Why this exists: the premarket-scanner plan captures a real `studyBars`
// series from a running backend. The environment this was built in had no
// backend, no broker credentials and no cached bar data, so this seeded
// generator stands in. REGENERATE THE FIXTURE FROM A REAL TAPE when one is
// available (plan Task 2 Step 1), then rerun generate_squeeze_fixture.mjs.
//
// Shape: every weekday 04:00-20:00 ET (extended hours included), 5-minute
// bars, spanning the 2026-11-01 DST change so the Eastern/Central-anchored
// bucket clocks are exercised on both offsets. Price is an Ornstein-Uhlenbeck
// walk whose volatility and trend switch between regimes (quiet coils, then
// expansions) so squeezes form and release on 1h, 2h, 4h and D.
//
// Run: node scripts/generate_squeeze_bars.mjs
import { writeFileSync } from "node:fs";

const SEED = 20260820;
const FIRST_DAY = Date.UTC(2026, 8, 21); // 2026-09-21 (Mon)
const WEEKDAYS = 45;
const OUTPUT = "tests/fixtures/squeeze_release_bars.json";

function mulberry32(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const random = mulberry32(SEED);
function gaussian() {
  const u = Math.max(random(), 1e-12);
  const v = random();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

const OFFSET_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  timeZoneName: "shortOffset",
});

// Epoch seconds for an Eastern wall-clock time on a UTC calendar date.
function easternEpoch(utcDateMs, hour, minute) {
  const noon = new Date(utcDateMs + 12 * 3600 * 1000);
  const label = OFFSET_FORMATTER.formatToParts(noon).find((part) => part.type === "timeZoneName").value;
  const offsetHours = Number(label.replace("GMT", "")); // -4 (EDT) or -5 (EST)
  return Math.floor(utcDateMs / 1000) + (hour - offsetHours) * 3600 + minute * 60;
}

const round = (value) => Math.round(value * 100) / 100;

// Regimes are scheduled in whole days so the DAILY candles also coil and
// release; intraday sub-regimes add hourly coils inside each day.
function dailyRegime(dayIndex) {
  // A long daily coil (so 20 daily candles can sit inside the Keltner
  // channel), a multi-day expansion that releases it, then a second coil.
  if (dayIndex < 24) return { sigma: 0.06, pull: 0.02, drift: 0 };       // coil
  if (dayIndex < 28) return { sigma: 0.22, pull: 0.0, drift: 0.02 };     // expansion
  if (dayIndex < 33) return { sigma: 0.12, pull: 0.01, drift: 0 };       // cool-down
  return { sigma: 0.07, pull: 0.02, drift: 0 };                          // coil again
}

const bars = [];
let price = 180;
let anchor = 180;
let dayIndex = 0;
for (let offset = 0; dayIndex < WEEKDAYS; offset += 1) {
  const dateMs = FIRST_DAY + offset * 86400 * 1000;
  const weekday = new Date(dateMs).getUTCDay();
  if (weekday === 0 || weekday === 6) continue;
  const regime = dailyRegime(dayIndex);
  // Overnight gap scaled by regime.
  price += gaussian() * regime.sigma * 4;
  anchor = regime.pull > 0 ? anchor : price;
  let intraday = null;
  for (let minuteOfDay = 4 * 60; minuteOfDay < 20 * 60; minuteOfDay += 5) {
    // Re-roll an intraday sub-regime roughly every 2-6 hours.
    if (!intraday || intraday.left <= 0) {
      const burst = random() < 0.35;
      intraday = {
        left: 24 + Math.floor(random() * 48),
        scale: burst ? 2.5 + random() * 2 : 0.4 + random() * 0.4,
        drift: burst ? (random() < 0.5 ? 1 : -1) * regime.sigma * 0.35 : 0,
      };
    }
    intraday.left -= 1;
    const rth = minuteOfDay >= 9 * 60 + 30 && minuteOfDay < 16 * 60;
    const sigma = regime.sigma * intraday.scale * (rth ? 1 : 0.55);
    const open = price;
    const pullBack = regime.pull * (anchor - price);
    const close = open + regime.drift + intraday.drift + pullBack + gaussian() * sigma;
    const wick = Math.abs(gaussian()) * sigma * 0.6;
    const high = Math.max(open, close) + wick;
    const low = Math.min(open, close) - Math.abs(gaussian()) * sigma * 0.6;
    const baseVolume = rth ? 60000 : 6000;
    bars.push({
      time: easternEpoch(dateMs, Math.floor(minuteOfDay / 60), minuteOfDay % 60),
      open: round(open),
      high: round(high),
      low: round(low),
      close: round(close),
      volume: Math.round(baseVolume * intraday.scale * (0.5 + random())),
    });
    price = close;
  }
  dayIndex += 1;
}

writeFileSync(OUTPUT, `${JSON.stringify(bars)}\n`);
console.log(`${bars.length} bars, ${WEEKDAYS} weekdays -> ${OUTPUT}`);
