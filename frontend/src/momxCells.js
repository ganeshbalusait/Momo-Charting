import { sentimentTone } from "./momxNewsFeed.js";

// Presentation layer for the MomoX watchlist board.
//
// The backend replays the trader's thinkScript studies and emits cells shaped
// { value, bg, fg } where bg/fg are the LOWERCASE thinkScript colour NAMES from
// the source scripts ("cyan", "plum", "downtick", ...). The backend never emits
// hex - by contract - so this module is the single place a colour name becomes
// a CSS colour. Nothing here fetches, and nothing here derives a colour from a
// value: if a cell is the wrong colour the bug is in the thinkScript port, not
// in this file.
//
// Pure functions only, no React, so the whole board's presentation is unit
// testable without a DOM.

// thinkorswim palette. Where thinkScript reuses a standard colour name, the
// standard CSS/X11 value is used, because that is what TOS itself renders:
//   cyan / magenta / red / white / black : the pure sRGB primaries TOS paints.
//   lime / plum / violet / orange / gray / dark_green / dark_red : X11 values,
//     the same ones the TOS colour picker shows for those names.
//   green : TOS's Color.GREEN is a MID green - visibly darker than LIME and
//     brighter than DARK_GREEN - because the skittles ladder needs all three
//     to be distinguishable on a black watchlist row.
//   light_red : the washed-out red TOS uses for the weakest bearish skittle
//     (4x8 cross down inside a 9x20 downtrend).
//   downtick : Color.DOWNTICK is the platform's DOWN-CANDLE red, a slightly
//     softened pure red. It stays distinct from Color.RED on purpose: in the
//     skittles label ladder "MACD up while EMAs are down" prints downtick, and
//     the trader reads that as a warning, not as a plain bearish cell.
export const THINKSCRIPT_COLORS = {
  cyan: "#00ffff",
  magenta: "#ff00ff",
  green: "#00cc00",
  red: "#ff0000",
  light_red: "#ff6e6e",
  plum: "#dda0dd",
  lime: "#00ff00",
  dark_green: "#006400",
  dark_red: "#8b0000",
  violet: "#ee82ee",
  downtick: "#e03c3c",
  orange: "#ffa500",
  white: "#ffffff",
  black: "#000000",
  gray: "#808080",
};

// The board opens the way the TOS scan outputs: % change descending.
export const MOMX_DEFAULT_SORT = { key: "pctChange", direction: "desc" };

// Quote Trend bars occupy 70% of their slot so consecutive bars read as a
// histogram rather than a solid block.
const QUOTE_TREND_BAR_FILL = 0.7;
// A zero bar is still drawn, as a hairline tick on the midline, so a flat print
// is visibly different from "no data at all".
const QUOTE_TREND_FLAT_PX = 1;

function toNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  if (typeof value === "boolean") return null;
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function round2(value) {
  return Math.round(value * 100) / 100;
}

// Colour names arrive from JSON, so tolerate the casing/whitespace drift that
// a hand-edited payload can introduce. Anything unrecognised resolves to null
// and the caller decides the fallback - never throw, a bad colour must not
// blank the board.
function resolveColor(name) {
  if (typeof name !== "string") return null;
  const key = name.trim().toLowerCase();
  return Object.prototype.hasOwnProperty.call(THINKSCRIPT_COLORS, key) ? THINKSCRIPT_COLORS[key] : null;
}

// { backgroundColor, color } ready to spread into a style prop. "transparent"
// lets the row background through; "inherit" keeps the table's text colour.
// WCAG relative luminance of a #rrggbb colour.
function _luminance(hex) {
  const h = String(hex || "").replace("#", "");
  if (h.length !== 6) return null;
  const chan = (i) => {
    const c = parseInt(h.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * chan(0) + 0.7152 * chan(2) + 0.0722 * chan(4);
}

function _contrast(a, b) {
  const la = _luminance(a);
  const lb = _luminance(b);
  if (la === null || lb === null) return null;
  const hi = Math.max(la, lb);
  const lo = Math.min(la, lb);
  return (hi + 0.05) / (lo + 0.05);
}

// Guarantee the number is legible on its cell. The trader's thinkScript
// sometimes pairs a near-same-hue text and background - e.g. Skittles paints
// VIOLET on PLUM (contrast 1.12), which vanishes (reported 2026-08-28). Keep
// the script's colour whenever it is readable (>= 3:1, so the meaning it
// carries is preserved), and only when it is not, switch to black or white -
// whichever ACTUALLY contrasts. On plum that is black (10:1), not white (2:1),
// so "make it white" alone would not have fixed it.
// Measured contrast of every colour his RVOL ladder paints on black:
//
//     dark_green  #006400   2.82      dark_red  #8b0000   2.10
//     green       #00cc00   9.64      red       #ff0000   5.25
//     cyan        #00ffff  16.75      magenta   #ff00ff   6.70
//     violet on plum        1.12   <- the clash this rescue exists for
//
// At 3.0 the DARK rungs were being rescued to white, and TOS shows them dim on
// purpose: "> 0.5" is the ladder saying "barely worth noticing". He spotted it
// immediately - "i see white rvol number" - because white is the loudest ink
// on the board and it was landing on the least important cells.
//
// 1.6 sits above the 1.12 clash and below the 2.10 dark rung, so the accidental
// case is still corrected and the deliberate one is left exactly as scripted.
// Do not raise this without re-measuring: the gap it threads is narrow.
const _MIN_CONTRAST = 1.6;

// TOS hides a cell by painting the text the same black as its background. His
// RVOL ladder does exactly that for anything at or below 0.5 ("else
// Color.BLACK" on a black bg), so those cells are meant to read as EMPTY --
// the eye should land only on the timeframes that are actually moving.
//
// This has to be decided from what the SCRIPT asked for, not from a contrast
// ratio, because the accidental clash readableInk exists for (violet on plum,
// 1.12) is indistinguishable from the deliberate hide by contrast alone. Only
// black-on-black is the idiom; every other unreadable pairing is still a bug
// worth correcting.
export function isMutedCell(cell) {
  const source = cell && typeof cell === "object" ? cell : {};
  if (source.value === null || source.value === undefined) return false;
  const fg = typeof source.fg === "string" ? source.fg.trim().toLowerCase() : source.fg;
  const bg = typeof source.bg === "string" ? source.bg.trim().toLowerCase() : source.bg;
  // A null/absent background is the row's own dark fill, which black text
  // disappears into just as completely.
  return fg === "black" && (bg === "black" || bg === null || bg === undefined);
}

function readableInk(background, foreground) {
  if (background === null) return foreground; // transparent bg: leave text as-is
  const current = _contrast(background, foreground || "#000000");
  if (current !== null && current >= _MIN_CONTRAST) return foreground;
  const white = _contrast(background, "#ffffff") ?? 0;
  const black = _contrast(background, "#000000") ?? 0;
  return white >= black ? "#ffffff" : "#000000";
}

export function cellStyle(cell) {
  const source = cell && typeof cell === "object" ? cell : {};
  const background = resolveColor(source.bg);
  const foreground = resolveColor(source.fg);
  // A deliberately hidden cell keeps its black ink. Passing it through
  // readableInk would "rescue" it to white and undo the script's intent.
  const ink = isMutedCell(source) ? foreground : readableInk(background, foreground);
  return {
    backgroundColor: background === null ? "transparent" : background,
    color: ink === null ? "inherit" : ink,
  };
}

// RVOL prints one decimal, matching `plot RelVolume = round(relVol, 1)`.
//
// Callers that have the whole cell should prefer formatRvolCell, which also
// honours the script's deliberate black-on-black hide.
export function formatRvol(value) {
  const number = toNumber(value);
  if (number === null) return "";
  const text = number.toFixed(1);
  // -0.04 formats as "-0.0", which reads as a negative reading that is not
  // there. Collapse any rounded zero to the unsigned form.
  return Number(text) === 0 ? (0).toFixed(1) : text;
}

// Skittles prints `Round(value, 0)` of StochasticFast FastD.
export function formatSkittles(value) {
  const number = toNumber(value);
  if (number === null) return "";
  const rounded = Math.round(number);
  return String(rounded === 0 ? 0 : rounded);
}

// % change is signed so a green/red column is still readable in a screenshot.
export function formatPctChange(value) {
  const number = toNumber(value);
  if (number === null) return "";
  const text = number.toFixed(2);
  const rounded = Number(text);
  if (rounded === 0) return `${(0).toFixed(2)}%`;
  return rounded > 0 ? `+${text}%` : `${text}%`;
}

// SVG "d" for the sparkline, scaled to fill the box: highest value at the top
// edge, lowest at the bottom edge.
export function sparklinePath(values, width, height) {
  const boxWidth = toNumber(width);
  const boxHeight = toNumber(height);
  if (boxWidth === null || boxHeight === null || boxWidth <= 0 || boxHeight <= 0) return "";
  if (!Array.isArray(values)) return "";

  const points = values.map(toNumber).filter((point) => point !== null);
  if (points.length < 2) return "";

  const max = Math.max(...points);
  const min = Math.min(...points);
  const span = max - min;
  const step = boxWidth / (points.length - 1);

  return points
    .map((point, index) => {
      const x = round2(index * step);
      // A dead-flat series has span 0; dividing by it would emit NaN and the
      // whole path would silently disappear. Draw it down the middle instead.
      const y = span === 0 ? round2(boxHeight / 2) : round2(((max - point) / span) * boxHeight);
      return `${index === 0 ? "M" : "L"} ${x},${y}`;
    })
    .join(" ");
}

// Rects for the Quote Trend mini histogram. Colour is NOT derived here: each
// rect carries its source cell so the caller runs it through cellStyle, which
// keeps the thinkScript AssignBackgroundColor ladder as the only colour
// authority.
export function quoteTrendBars(cells, width, height) {
  if (!Array.isArray(cells) || cells.length === 0) return [];

  const boxWidth = Math.max(toNumber(width) ?? 0, 0);
  const boxHeight = Math.max(toNumber(height) ?? 0, 0);
  const slot = boxWidth / cells.length;
  const barWidth = round2(slot * QUOTE_TREND_BAR_FILL);
  const inset = round2((slot - barWidth) / 2);
  const flatHeight = Math.min(QUOTE_TREND_FLAT_PX, boxHeight);

  // DIRECTION, not price. His script plots
  //
  //     QuoteTrendScore = if close > close[1] then 1 else if close < close[1] then -1 else 0
  //
  // which has exactly three heights: up, down, flat. This column previously
  // scaled each bar to its close within the window -- a mini price chart,
  // copied from MomoX -- so a run of three down-ticks drew as three DIFFERENT
  // heights and the eye read magnitude that the script never encodes. Measured
  // on closes [100,99,98,97,98] in the real 58x13 box: 13.0, 8.67, 4.33, 1.0
  // and 4.33 px, so the single UP tick drew SHORTER than two of the downs.
  //
  // He chose TOS exactly (2026-09-01). The backend still ships `price` because
  // the % change display reads it; only the geometry ignores it now.
  const midline = round2(boxHeight / 2);

  return cells.map((cell, index) => {
    const source = cell && typeof cell === "object" ? cell : {};
    const x = round2(index * slot + inset);
    const direction = toNumber(source.value) ?? 0;
    if (direction > 0) return { x, y: 0, w: barWidth, h: midline, cell };
    if (direction < 0) return { x, y: midline, w: barWidth, h: round2(boxHeight - midline), cell };
    return { x, y: round2(midline - flatHeight / 2), w: barWidth, h: flatHeight, cell };
  });
}

// Rows whose sort key is missing sort to the END in both directions: a stock
// with no % change is not "the biggest loser", it is unknown, and TOS never
// floats unknowns to the top of the board.
function sortValue(row, key) {
  if (!row || typeof row !== "object") return null;
  const raw = row[key];
  if (raw === null || raw === undefined) return null;
  if (typeof raw === "number") return Number.isFinite(raw) ? raw : null;
  if (typeof raw === "string") return raw;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? parsed : null;
}

// A cell whose VALUE is a string but whose MEANING is a number. The Squeeze
// column is the only one: its cells are the script's label, so they arrive as
// "9", "32", "*1" or "-". Sorting those as text put "9" ABOVE "32" and sank
// the freshly-fired "*1"/"*2" high-compression cells to the bottom -- the
// exact rows worth looking at.
//
// Deliberately STRICT (whole string, optional leading "*" and sign). A loose
// parseFloat would read the Time column's "04:20" as 4 and silently sort by
// the hour, discarding the minutes -- a worse bug than the one being fixed,
// and a silent one.
const NUMERIC_LABEL = /^\*?-?\d+(?:\.\d+)?$/;

function numericLabel(value) {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const text = value.trim();
  if (!NUMERIC_LABEL.test(text)) return null;
  const parsed = Number(text.startsWith("*") ? text.slice(1) : text);
  return Number.isFinite(parsed) ? parsed : null;
}

function compareValues(left, right) {
  if (typeof left === "number" && typeof right === "number") return left - right;
  const leftNumber = numericLabel(left);
  const rightNumber = numericLabel(right);
  if (leftNumber !== null && rightNumber !== null) {
    if (leftNumber !== rightNumber) return leftNumber - rightNumber;
    // Equal counts: the high-compression "*" reading ranks above the plain one,
    // because that is the more notable of two otherwise identical cells.
    const leftStar = typeof left === "string" && left.trim().startsWith("*");
    const rightStar = typeof right === "string" && right.trim().startsWith("*");
    if (leftStar !== rightStar) return leftStar ? 1 : -1;
    return 0;
  }
  const leftText = String(left);
  const rightText = String(right);
  return leftText.localeCompare(rightText, "en", { sensitivity: "base" });
}

export function sortRows(rows, key = MOMX_DEFAULT_SORT.key, direction = MOMX_DEFAULT_SORT.direction) {
  if (!Array.isArray(rows)) return [];
  const sortKey = typeof key === "string" && key ? key : MOMX_DEFAULT_SORT.key;
  const sign = String(direction).toLowerCase() === "asc" ? 1 : -1;

  return [...rows].sort((leftRow, rightRow) => {
    const left = sortValue(leftRow, sortKey);
    const right = sortValue(rightRow, sortKey);
    if (left === null && right === null) return tieBreak(leftRow, rightRow);
    if (left === null) return 1;
    if (right === null) return -1;
    const compared = compareValues(left, right);
    return compared === 0 ? tieBreak(leftRow, rightRow) : compared * sign;
  });
}

// Ties resolve on symbol ascending in BOTH directions so the board does not
// reshuffle rows that are genuinely equal (Array.prototype.sort is stable, but
// the incoming row order is not).
function tieBreak(leftRow, rightRow) {
  const left = sortValue(leftRow, "symbol");
  const right = sortValue(rightRow, "symbol");
  if (left === null && right === null) return 0;
  if (left === null) return 1;
  if (right === null) return -1;
  return compareValues(left, right);
}

// ---------------------------------------------------------------------------
// Industry chips
// ---------------------------------------------------------------------------
//
// The chip row is a UI affordance for filtering, NOT a thinkScript output: TOS
// prints the industry as plain text and colours nothing. So unlike every cell
// on the board, these colours are ours to choose - which is exactly why they
// are kept in their own palette, well away from THINKSCRIPT_COLORS. Nothing
// here may ever be cited as parity.
//
// The colour is DERIVED from the name rather than looked up in a hand-kept map,
// because industries arrive from momx/industries.py and that file is edited by
// hand: a map here would silently give every newly added industry the same
// "unknown" colour until someone noticed.
export const MOMX_INDUSTRY_PALETTE = Object.freeze([
  "#4cc9f0",
  "#f4a261",
  "#b5e48c",
  "#c77dff",
  "#ffd166",
  "#66d9c2",
  "#ff8fab",
  "#8ecae6",
  "#e9c46a",
  "#a0c4ff",
  "#f2779a",
  "#9ae6b4",
]);

// FNV-1a, 32-bit. Chosen over "sum the char codes" because anagram-ish industry
// names ("Semis" / "Miess") would collide under a sum and land on the same chip
// colour. Kept unsigned with >>> 0 so the modulo can never go negative.
function hashName(text) {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

// Same name -> same colour, for the life of the palette. Memoised because the
// board calls this once per ROW (355 of them) on every poll, and the answer for
// a given name can never change.
const INDUSTRY_COLOR_CACHE = new Map();

export function industryColor(name) {
  if (typeof name !== "string") return MOMX_INDUSTRY_PALETTE[0];
  const key = name.trim().toLowerCase();
  if (key === "") return MOMX_INDUSTRY_PALETTE[0];
  const cached = INDUSTRY_COLOR_CACHE.get(key);
  if (cached !== undefined) return cached;
  const color = MOMX_INDUSTRY_PALETTE[hashName(key) % MOMX_INDUSTRY_PALETTE.length];
  INDUSTRY_COLOR_CACHE.set(key, color);
  return color;
}

// One entry per industry present in `rows`, biggest group first, ties broken
// alphabetically so the chip row does not reshuffle itself between polls.
// Rows with no industry are LEFT OUT rather than bucketed into "Other": a chip
// the trader cannot act on is noise, and clicking it would hide every row whose
// industry the backend simply has not been taught yet.
export function industryCounts(rows) {
  if (!Array.isArray(rows)) return [];
  const counts = new Map();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const name = typeof row.industry === "string" ? row.industry.trim() : "";
    if (name === "") continue;
    counts.set(name, (counts.get(name) || 0) + 1);
  }
  return [...counts.entries()]
    .map(([name, count]) => ({ name, count, color: industryColor(name) }))
    .sort((left, right) =>
      right.count === left.count
        ? left.name.localeCompare(right.name, "en", { sensitivity: "base" })
        : right.count - left.count,
    );
}

// Which INDUSTRIES are moving together, not just which tickers moved.
//
// A board of 50 rows sorted by percentage tells you the best single names; it
// does not tell you that every ETF-Lev name on it is up, which is a different
// and often more actionable fact. One stock up 6% is a story about that stock.
// Six of six up is a story about the sector, and it is the second one the
// trader asked to see.
//
// Deliberate choices, each of which the obvious version gets wrong:
//
//  * Ranked by PARTICIPATION first (what share of the group is up), not by the
//    average. Ranking by average lets a single +40% name carry an otherwise
//    flat group to the top of the list, which is exactly the impression this
//    panel exists to correct.
//  * A group needs MIN_MEMBERS. "1 of 1 up" is not a sector rotation, it is a
//    ticker, and it would otherwise always score a perfect 100%.
//  * Groups with no participation are dropped rather than listed at zero: the
//    panel answers "what is moving", and a list of things that are not moving
//    is noise on a screen that is already dense.
// H/L colours drawn the MomoX way (2026-09-25, his screenshot): his script's
// ladder names, but DARK_GREEN (strong, > +0.5) shows as MomoX's pale mint -
// thinkorswim's real dark green is never drawn, on the board or the card.
export const HL_BLOCK_COLORS = Object.freeze({
  dark_green: "#86efac",
  green: "#16a34a",
  red: "#dc2626",
  dark_red: "#991b1b",
  gray: "#6b7280",
});

/** {color, widthPct} for an H/L cell, or null when there is nothing to draw. */
export function hlBlockStyle(cell) {
  // Guard the field before Number(): Number(null) is 0, which would draw a
  // sliver for a cell that has no reading at all.
  const hasValue = cell && typeof cell === "object" && cell.value !== null && cell.value !== undefined && cell.value !== "";
  const raw = hasValue ? Number(cell.value) : NaN;
  const color = cell && typeof cell === "object" ? HL_BLOCK_COLORS[cell.bg] : null;
  if (!Number.isFinite(raw) || !color) return null;
  return { color, widthPct: Math.round(Math.max(0.12, Math.min(1, Math.abs(raw))) * 100) };
}

export const SECTOR_MOVE_PCT = 1.5;
export const SECTOR_MIN_MEMBERS = 2;
export const SECTOR_ROLLUP_LIMIT = 5;

export function sectorRollup(rows, options) {
  const settings = options || {};
  const threshold = Number.isFinite(settings.threshold)
    ? settings.threshold
    : SECTOR_MOVE_PCT;
  const minMembers = Number.isFinite(settings.minMembers)
    ? settings.minMembers
    : SECTOR_MIN_MEMBERS;
  const limit = Number.isFinite(settings.limit) ? settings.limit : SECTOR_ROLLUP_LIMIT;
  if (!Array.isArray(rows)) return [];

  const groups = new Map();
  for (const row of rows) {
    if (!row || typeof row !== "object") continue;
    const name = typeof row.industry === "string" ? row.industry.trim() : "";
    if (name === "") continue;
    // A row with no quote yet is counted in NEITHER total nor movers. Counting
    // it in the total only would report "2 of 5 up" while three of those five
    // simply have not priced, understating the move for no reason.
    //
    // The null check is NOT redundant with isFinite: Number(null) is 0, and so
    // is Number(""), so an unpriced row would sail through as a real 0.0%
    // reading and quietly inflate every denominator on the panel.
    const raw = row.pctChange;
    if (raw === null || raw === undefined || raw === "") continue;
    const change = Number(raw);
    if (!Number.isFinite(change)) continue;
    const group = groups.get(name) || { name, total: 0, up: 0, sum: 0, rotation: null };
    // The scanner's sector-rotation verdict (momx/sectors.py) rides on every
    // row of the group; it is judged on the WHOLE universe, not just the rows
    // on screen, so it is copied, never recomputed here.
    if (!group.rotation && row.sectorRotation && typeof row.sectorRotation === "object") {
      group.rotation = row.sectorRotation;
    }
    group.total += 1;
    group.sum += change;
    if (change >= threshold) group.up += 1;
    groups.set(name, group);
  }

  return [...groups.values()]
    .filter((group) => (group.total >= minMembers && group.up > 0) ||
      Boolean(group.rotation && (group.rotation.hot || group.rotation.late)))
    .map((group) => {
      const rotation = group.rotation;
      const rs3 = rotation && rotation.rs3 !== null && rotation.rs3 !== undefined ? Number(rotation.rs3) : NaN;
      return {
        name: group.name,
        up: group.up,
        total: group.total,
        share: group.up / group.total,
        avg: group.sum / group.total,
        color: industryColor(group.name),
        hot: Boolean(rotation && rotation.hot),
        // After 11:00 ET: the same rotation rule, shown but not traded on.
        late: Boolean(rotation && rotation.late && !rotation.hot),
        rs3: Number.isFinite(rs3) ? rs3 : null,
        volUp: rotation && Number.isFinite(Number(rotation.volUp)) ? Number(rotation.volUp) : null,
        leaders: rotation && Array.isArray(rotation.leaders) ? rotation.leaders : [],
        // The sector's OWN moves (no SPY), the way his Sector ETFs sheet reads.
        ret3: rotation && rotation.ret3 !== null && Number.isFinite(Number(rotation.ret3)) ? Number(rotation.ret3) : null,
        today: rotation && rotation.today !== null && Number.isFinite(Number(rotation.today)) ? Number(rotation.today) : null,
        breadthUp: rotation ? rotation.up : null,
        breadthTotal: rotation ? rotation.total : null,
      };
    })
    .sort((left, right) => {
      // Money flowing IN first (2026-09-24 sector rotation), then participation.
      if (left.hot !== right.hot) return left.hot ? -1 : 1;
      if (left.late !== right.late) return left.late ? -1 : 1;
      if (right.share !== left.share) return right.share - left.share;
      if (right.up !== left.up) return right.up - left.up;      // 6 of 6 over 2 of 2
      if (right.avg !== left.avg) return right.avg - left.avg;
      return left.name.localeCompare(right.name, "en", { sensitivity: "base" });
    })
    .slice(0, Math.max(0, limit));
}

// An empty selection means "no industry filter", never "hide everything".
export function filterByIndustries(rows, selected) {
  if (!Array.isArray(rows)) return [];
  const wanted = selected instanceof Set ? selected : new Set(Array.isArray(selected) ? selected : []);
  if (wanted.size === 0) return rows;
  return rows.filter((row) => {
    if (!row || typeof row !== "object") return false;
    const name = typeof row.industry === "string" ? row.industry.trim() : "";
    return name !== "" && wanted.has(name);
  });
}

// ---------------------------------------------------------------------------
// Fires - what the lightning bolt means
// ---------------------------------------------------------------------------
//
// Until 2026-09-02 the bolt meant "this symbol has weekly options" (row.badge,
// reasons ["weeklies"]). He asked for it to mean what it means on the card he
// works from: the signals that FIRED. That is `row.scanReasons`, which the
// scan already stamps on every matching row -
//
//     ["macd:4h", "ema9x20:4h", "ema4x8:4h", "macd:2D", "sqzfired:D"]
//
// so this is a re-labelling of data we hold, not a new source.
//
// The wire vocabulary is `study:timeframe`. His is "MACD 4h", "9x20 4h",
// "4x8 2D", "SQZ D". Anything unrecognised is passed through with only its
// colon turned into a space: a study added on the server must show up on the
// board as SOMETHING, not vanish because this map had not been updated.

const FIRE_STUDY_LABELS = {
  macd: "MACD",
  ema9x20: "9x20",
  ema4x8: "4x8",
  sqzfired: "SQZ",
  sqz: "SQZ",
  rvol: "RVOL",
};

/** One reason, in his words. "ema9x20:4h" -> "9x20 4h". */
export function fireLabel(reason) {
  const text = String(reason || "").trim();
  if (!text) return "";
  const [rawStudy, ...rest] = text.split(":");
  const study = FIRE_STUDY_LABELS[rawStudy.toLowerCase()] || rawStudy.toUpperCase();
  const timeframe = rest.join(":").trim();
  return timeframe ? study + " " + timeframe : study;
}

/** Every fired signal on a row, in his words, de-duplicated and in order. */
export function firesOf(row) {
  const raw = row && typeof row === "object" ? row.scanReasons : null;
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const out = [];
  for (const reason of raw) {
    const label = fireLabel(reason);
    if (!label || seen.has(label)) continue;
    seen.add(label);
    out.push(label);
  }
  return out;
}

// The lightning badge is written by a DIFFERENT agent's backend change and may
// simply not be in the payload yet. Absent, malformed, or off must all render
// nothing and throw nothing, so the shape check lives here where it is tested
// rather than inline in a row that renders 355 times.
export function badgeOf(row) {
  const badge = row && typeof row === "object" ? row.badge : null;
  if (!badge || typeof badge !== "object") return null;
  if (!badge.on) return null;
  return { tooltip: typeof badge.tooltip === "string" && badge.tooltip ? badge.tooltip : "Momentum badge" };
}

// ---------------------------------------------------------------------------
// Momentum strip
// ---------------------------------------------------------------------------
//
// The strip answers ONE question - "which symbol just started moving?" - so a
// trader who watches the board sees a new event within seconds instead of at
// the next 60s rebuild. Events come from /api/momx-scanner/momentum shaped
// { type: "new_match"|"lost_match"|"rvol_spike", symbol, pctChange?, timeframe?,
// value?, scanReasons?, at }.
//
// Like the industry chips, everything here is UI CHROME: the chip colours are
// ours, chosen in CSS, and are NOT thinkScript colours. Nothing in this section
// reads or extends THINKSCRIPT_COLORS, and nothing here may be cited as parity.
//
// Every function is defensive by contract: the endpoint does not exist yet on
// some deployments, so a malformed or missing field must fall out silently -
// a bad event is dropped, never thrown on.

// The strip shows at most this many chips, newest first; the rest collapse
// into a "+N more" tail so the strip stays one row.
export const MOMX_MOMENTUM_MAX_CHIPS = 12;
// An event this young is "fresh": its chip pops and its table row flashes NEW.
export const MOMX_MOMENTUM_FRESH_MS = 2 * 60 * 1000;
// Past this age a chip fades to background - it is old news, not a trade.
export const MOMX_MOMENTUM_FADE_MS = 15 * 60 * 1000;

const MOMENTUM_EVENT_TYPES = new Set(["new_match", "lost_match", "rvol_spike"]);

// The event's timestamp in epoch MILLISECONDS, or null when unreadable. The
// backend is not built yet, so both ISO strings and epoch numbers are
// tolerated; a number below 1e12 (Sep 2001 in ms) is read as epoch seconds.
export function momentumEventTime(event) {
  if (!event || typeof event !== "object") return null;
  const at = event.at;
  if (typeof at === "number" && Number.isFinite(at)) {
    return at >= 1e12 ? at : at * 1000;
  }
  if (typeof at === "string" && at !== "") {
    const parsed = Date.parse(at);
    return Number.isNaN(parsed) ? null : parsed;
  }
  return null;
}

function momentumSymbol(event) {
  const raw = event && typeof event === "object" ? event.symbol : null;
  if (typeof raw !== "string") return "";
  return raw.trim().toUpperCase();
}

// Chip label parts for one event, or null when the event cannot be rendered.
// `text` is the whole label in reading order - "NEW CRWD +4.20%",
// "RVOL 5m 3.1 MPC", "LOST XYZ" - and tag/symbol/detail are the same pieces
// for the renderer to style individually.
export function momentumChipLabel(event) {
  if (!event || typeof event !== "object") return null;
  if (!MOMENTUM_EVENT_TYPES.has(event.type)) return null;
  const symbol = momentumSymbol(event);
  if (symbol === "") return null;

  if (event.type === "new_match") {
    const detail = formatPctChange(event.pctChange);
    const reasons = Array.isArray(event.scanReasons)
      ? event.scanReasons.filter((reason) => typeof reason === "string" && reason !== "").join("  ")
      : "";
    return {
      kind: "new_match",
      tag: "NEW",
      symbol,
      detail,
      text: ["NEW", symbol, detail].filter(Boolean).join(" "),
      title: reasons !== "" ? reasons : "Newly passed the scan",
    };
  }

  if (event.type === "rvol_spike") {
    const timeframe = typeof event.timeframe === "string" ? event.timeframe.trim() : "";
    const tag = timeframe !== "" ? "RVOL " + timeframe : "RVOL";
    const detail = formatRvol(event.value);
    return {
      kind: "rvol_spike",
      tag,
      symbol,
      detail,
      // The trader's reading order for a spike is measure first, name last:
      // "RVOL 5m 3.1 MPC".
      text: [tag, detail, symbol].filter(Boolean).join(" "),
      title: "Relative volume spike",
    };
  }

  return {
    kind: "lost_match",
    tag: "LOST",
    symbol,
    detail: "",
    text: "LOST " + symbol,
    title: "Dropped off the scan",
  };
}

// Age bucket for the fade: "fresh" (just happened - pop it), "recent" (normal),
// "faded" (past MOMX_MOMENTUM_FADE_MS - old news). An unreadable timestamp is
// "faded", never "fresh": a chip must not flash on data it cannot date. A
// FUTURE timestamp is "fresh" - small clock skew between the backend and the
// browser must not mute a genuinely new event.
export function momentumAgeBucket(event, nowMs) {
  const stamp = momentumEventTime(event);
  if (stamp === null) return "faded";
  const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
  const age = now - stamp;
  if (age >= MOMX_MOMENTUM_FADE_MS) return "faded";
  if (age <= MOMX_MOMENTUM_FRESH_MS) return "fresh";
  return "recent";
}

// Newest-first cap: drops malformed events, dedupes exact repeats (the same
// type+symbol+timestamp arriving on consecutive polls), sorts newest first
// (undated events last), and returns at most `limit` events plus the count
// that fell off for the "+N more" tail.
export function capMomentumEvents(events, limit = MOMX_MOMENTUM_MAX_CHIPS) {
  if (!Array.isArray(events)) return { visible: [], overflow: 0 };
  const seen = new Set();
  const dated = [];
  for (const event of events) {
    const label = momentumChipLabel(event);
    if (!label) continue;
    const at = momentumEventTime(event);
    const key = label.kind + "|" + label.symbol + "|" + (at === null ? "?" : at);
    if (seen.has(key)) continue;
    seen.add(key);
    dated.push({ event, at });
  }
  dated.sort((left, right) => {
    const leftAt = left.at === null ? -Infinity : left.at;
    const rightAt = right.at === null ? -Infinity : right.at;
    return rightAt - leftAt;
  });
  const max = typeof limit === "number" && Number.isFinite(limit) && limit > 0
    ? Math.floor(limit)
    : MOMX_MOMENTUM_MAX_CHIPS;
  const visible = dated.slice(0, max).map((entry) => entry.event);
  return { visible, overflow: Math.max(0, dated.length - visible.length) };
}

// Symbols whose new_match landed inside the window (default ~2 minutes), for
// the table's NEW flash - so the strip and the board tell one story. A future
// timestamp counts as inside the window (clock skew again); an undated event
// never flashes.
export function newMatchSymbols(events, nowMs, windowMs = MOMX_MOMENTUM_FRESH_MS) {
  const fresh = new Set();
  if (!Array.isArray(events)) return fresh;
  const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
  for (const event of events) {
    if (!event || typeof event !== "object" || event.type !== "new_match") continue;
    const symbol = momentumSymbol(event);
    if (symbol === "") continue;
    const at = momentumEventTime(event);
    if (at === null) continue;
    if (now - at <= windowMs) fresh.add(symbol);
  }
  return fresh;
}

// A row's matchedSince stamp counts as NEW for this long. Deliberately wider
// than MOMX_MOMENTUM_FRESH_MS: the event window only exists while a tab is
// polling, but the stamp survives worker restarts and cold starts, so it can
// honestly mark a match the trader has not seen yet.
export const MOMX_MATCHED_NEW_MS = 15 * 60 * 1000;

// Presentation for the worker's row.matchedSince stamp (when this symbol
// ENTERED the matched set). Returns { isNew, label } or null when the stamp is
// absent/unreadable - old cached boards carry no stamp and must render as
// before. isNew: the entry is under MOMX_MATCHED_NEW_MS old (a FUTURE stamp
// clamps to age 0, so backend clock skew reads as new, never as garbage - same
// rule as momentumAgeBucket). label: entered today (ET) -> the ET clock time
// with the AM/PM dropped ("9:47" - the board is dense and a trading-day time
// is unambiguous); an older day -> the short ET weekday ("Fri"). ET via
// America/New_York, exactly like tapeAsOfNote in MomxScannerPanel.jsx.
export function matchedSinceLabel(iso, nowMs) {
  if (typeof iso !== "string" || iso === "") return null;
  const stamp = Date.parse(iso);
  if (!Number.isFinite(stamp)) return null;
  const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
  const age = Math.max(0, now - stamp);
  const isNew = age < MOMX_MATCHED_NEW_MS;
  try {
    const etDay = (ms) => new Date(ms).toLocaleDateString("en-US", { timeZone: "America/New_York" });
    const label =
      etDay(stamp) === etDay(now)
        ? new Date(stamp)
            .toLocaleTimeString("en-US", {
              hour: "numeric",
              minute: "2-digit",
              hour12: true,
              timeZone: "America/New_York",
            })
            .replace(/\s*[AP]M$/i, "")
        : new Date(stamp).toLocaleDateString("en-US", { weekday: "short", timeZone: "America/New_York" });
    return { isNew, label };
  } catch {
    // A runtime without the timezone database: no label beats a wrong one.
    return null;
  }
}

// ---------------------------------------------------------------------------
// Board persistence (browser localStorage)
// ---------------------------------------------------------------------------
//
// THE RULE these functions exist to enforce: NEVER show an empty table when ANY
// previous board exists. The MomX worker keeps each list's board only in RAM,
// so a worker restart wipes it and the next fetch answers "warming" with no
// rows for minutes. On its own the panel would then blank to "Building..." -
// exactly what the trader complained about at the open. So the last GOOD board
// per list is mirrored into localStorage here, read back synchronously on
// mount, and shown while a warming/stale fetch is in flight.
//
// Everything is defensive by contract: localStorage throws outright in some
// privacy modes and on quota exhaustion, and a stored blob can be truncated or
// hand-edited. A read failure degrades to "no cache" (the caller then behaves
// exactly as it did before this feature existed); a write failure is swallowed
// so a full quota never takes the board down. Nothing here ever throws.

// One key per watchlist, so switching tabs shows THAT list's last board, not
// the previously active one. A null/empty list name folds to a single default
// bucket (the server's own active list, before /lists has seated a selection).
export const MOMX_BOARD_CACHE_PREFIX = "momx.board.";
// Rows are the bulk of the blob. A few hundred symbols serialise to well under
// a megabyte, but the universe is trader-editable, so the stored row count is
// capped to keep a giant paste from tripping the ~5MB localStorage quota.
export const MOMX_BOARD_CACHE_MAX_ROWS = 600;

export function momxBoardCacheKey(list) {
  const name = typeof list === "string" && list.trim() !== "" ? list.trim() : "__default__";
  return MOMX_BOARD_CACHE_PREFIX + name;
}

// Resolve the storage backend. An explicit store (a test double) wins; failing
// that, window.localStorage - whose mere ACCESS throws in some privacy modes,
// hence the try/catch around the property read itself.
function resolveStorage(storage) {
  if (storage && typeof storage.getItem === "function" && typeof storage.setItem === "function") {
    return storage;
  }
  try {
    return typeof window !== "undefined" && window.localStorage ? window.localStorage : null;
  } catch {
    return null;
  }
}

function boardUniverseCount(board, fallback) {
  if (!board || typeof board !== "object") return fallback;
  if (Number.isFinite(board.universeCount)) return board.universeCount;
  if (Array.isArray(board.universe)) return board.universe.length;
  return fallback;
}

// The slim, storable shape of a board: rows (capped) plus the two figures the
// toolbar shows for whatever data is on screen. Returns null when the board has
// NO rows - an empty board is never worth caching, and caching it would let a
// warming payload overwrite good rows already on disk.
export function boardCacheEntry(board) {
  if (!board || typeof board !== "object") return null;
  const rows = Array.isArray(board.rows) ? board.rows : [];
  if (rows.length === 0) return null;
  const capped = rows.length > MOMX_BOARD_CACHE_MAX_ROWS ? rows.slice(0, MOMX_BOARD_CACHE_MAX_ROWS) : rows;
  return {
    rows: capped,
    generatedAt: board.generatedAt === undefined ? null : board.generatedAt,
    // tapeAsOf must survive the cache: without it a reloaded weekend tab
    // showed Friday's matches under a fresh Updated stamp with NO "data as
    // of" note - the exact confusion the note exists to prevent (2026-08-30).
    tapeAsOf: board.tapeAsOf === undefined ? null : board.tapeAsOf,
    universeCount: boardUniverseCount(board, capped.length),
    // ~60 bytes a symbol, so the whole 357-name Watchlist costs ~20KB here;
    // not capped, because capping it would put the "where are the other
    // 307?" confusion straight back on the reload path.
    rest: boardRest(board),
  };
}

// Persist the last good board for a list. No-op (returns false) on an empty
// board, an unavailable store, or a quota/serialisation failure - a write that
// cannot happen must never throw and must never blank what is already stored.
export function writeBoardCache(list, board, storage) {
  const store = resolveStorage(storage);
  if (!store) return false;
  const entry = boardCacheEntry(board);
  if (!entry) return false;
  try {
    store.setItem(momxBoardCacheKey(list), JSON.stringify(entry));
    return true;
  } catch {
    return false;
  }
}

// Read a list's last good board back. Returns null - "no cache" - on any of:
// storage unavailable, key absent, unparseable JSON, wrong shape, or zero rows.
// The caller treats null exactly as it did before this cache existed.
export function readBoardCache(list, storage) {
  const store = resolveStorage(storage);
  if (!store) return null;
  let raw;
  try {
    raw = store.getItem(momxBoardCacheKey(list));
  } catch {
    return null;
  }
  if (typeof raw !== "string" || raw === "") return null;
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object") return null;
  const rows = Array.isArray(parsed.rows) ? parsed.rows : null;
  if (!rows || rows.length === 0) return null;
  return {
    rows,
    generatedAt: parsed.generatedAt === undefined ? null : parsed.generatedAt,
    // tapeAsOf and rest are WRITTEN by boardCacheEntry, so they must be READ
    // back here too. Until 2026-09-02 this whitelist dropped both, which meant
    // every reload while the worker was warming showed 50 rows under a
    // "Watchlist 357" header and NEWS 25 instead of 149 - the exact
    // where-are-the-other-307 state the rest cache exists to prevent - and
    // lost the "data as of" note. The round-trip tests all went
    // boardCacheEntry -> decideBoardView and never crossed this hop, so they
    // stayed green through the whole defect. Any future key added to
    // boardCacheEntry must be added HERE in the same edit.
    tapeAsOf: parsed.tapeAsOf === undefined ? null : parsed.tapeAsOf,
    universeCount: Number.isFinite(parsed.universeCount) ? parsed.universeCount : rows.length,
    rest: boardRest(parsed),
  };
}

// THE DECISION, in one pure place: given the live board this fetch returned and
// the cached board read from disk, what does the table show?
//
//   live board HAS rows        -> show it (updating flag = still warming)
//   live empty BUT cache rows  -> show the CACHE, flagged updating (this is the
//                                 whole point: a warming/stale/empty fetch never
//                                 replaces real tickers with "Building...")
//   nothing anywhere           -> empty, and only THEN may the caller say
//                                 "Building..." (the genuine first-ever run)
//
// `generatedAt`/`universeCount` always describe the data actually shown, so the
// UPDATED stamp reads the cache time while cache rows are on screen and never
// says "never" with rows visible.
// A board's `rest` list, or [] for anything that is not a well-formed array.
// Only records with a symbol survive: a bare {} row would render as an empty
// line the trader could neither read nor click.
export function boardRest(board) {
  const rest = board && typeof board === "object" && Array.isArray(board.rest) ? board.rest : [];
  return rest.filter((row) => row && typeof row === "object" && typeof row.symbol === "string" && row.symbol !== "");
}

export function decideBoardView(input) {
  const options = input && typeof input === "object" ? input : {};
  const liveBoard = options.liveBoard;
  const cache = options.cache;
  const warming = Boolean(options.warming);

  const liveRows = liveBoard && Array.isArray(liveBoard.rows) ? liveBoard.rows : [];
  if (liveRows.length > 0) {
    return {
      rows: liveRows,
      // The symbols the 50-row cap cut, as cheap PASS-1 records (symbol,
      // industry, % change, verdict) - what "Scan matches only" OFF shows
      // beneath the full rows. Same whitelisting trap as tapeAsOf below:
      // forget it here and the board silently goes back to 50.
      rest: boardRest(liveBoard),
      generatedAt: liveBoard.generatedAt === undefined ? null : liveBoard.generatedAt,
      // Field whitelisting bit here once: the API carried tapeAsOf but this
      // view-builder dropped it, so the market-closed note NEVER rendered
      // despite its logic being tested (2026-08-30). Carry it explicitly.
      tapeAsOf: liveBoard.tapeAsOf === undefined ? null : liveBoard.tapeAsOf,
      universeCount: boardUniverseCount(liveBoard, liveRows.length),
      source: "live",
      updating: warming,
    };
  }

  const cacheRows = cache && Array.isArray(cache.rows) ? cache.rows : [];
  if (cacheRows.length > 0) {
    return {
      rows: cacheRows,
      rest: boardRest(cache),
      generatedAt: cache.generatedAt === undefined ? null : cache.generatedAt,
      tapeAsOf: cache.tapeAsOf === undefined ? null : cache.tapeAsOf,
      universeCount: Number.isFinite(cache.universeCount) ? cache.universeCount : cacheRows.length,
      // Cache is on screen precisely because the server had nothing fresh, so
      // there is always a rebuild worth signalling.
      source: "cache",
      updating: true,
    };
  }

  return {
    rows: [],
    rest: [],
    // Keep the warming board's universe figure (e.g. "355 tickers") visible
    // while it builds, even before a single row exists.
    generatedAt: liveBoard && liveBoard.generatedAt !== undefined ? liveBoard.generatedAt : null,
    universeCount: boardUniverseCount(liveBoard, 0),
    source: "empty",
    updating: warming,
  };
}

// ---------------------------------------------------------------------------
// News badge (2026-08-30)
// ---------------------------------------------------------------------------
//
// A row may carry `news`: the newest headline for the symbol, shaped
// { headline, source?, url?, publishedAt? } (the worker may name the time
// `at` instead - both are accepted). The badge is BEST-EFFORT by design: the
// worker ships the board without `news` on any fetch failure, and this reader
// returns null for anything malformed, so a bad payload can never break a row.

export const MOMX_NEWS_FRESH_MS = 24 * 60 * 60 * 1000;

// "3h ago" for the popover. Minutes under an hour, hours under a day, days
// beyond that. Unreadable input renders as "" rather than "NaNm ago"; a
// slightly-future timestamp (feed clock skew) clamps to "just now".
export function ageOf(iso, nowMs = Date.now()) {
  if (typeof iso !== "string" || iso === "") return "";
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return "";
  const delta = nowMs - t;
  if (delta < 60 * 1000) return "just now";
  const minutes = Math.floor(delta / (60 * 1000));
  if (minutes < 60) return minutes + "m ago";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h ago";
  return Math.floor(hours / 24) + "d ago";
}

// The one gate for "does this row get the icon". Returns a display-ready
// object or null. Rules:
//   - a non-empty headline is mandatory;
//   - a readable timestamp older than the 24h window hides the badge here
//     even if the worker forgot to filter; a MISSING timestamp is trusted
//     (the worker already applies the window server-side);
//   - only http(s) urls survive, so a junk url can never become a live link.
export function newsOf(row, nowMs = Date.now()) {
  const news = row && typeof row === "object" ? row.news : null;
  if (!news || typeof news !== "object") return null;
  const headline = typeof news.headline === "string" ? news.headline.trim() : "";
  if (headline === "") return null;

  const iso =
    typeof news.publishedAt === "string" && news.publishedAt !== ""
      ? news.publishedAt
      : typeof news.at === "string"
        ? news.at
        : "";
  const t = Date.parse(iso);
  // A STORED headline (the ticker-tagged scraper's store, momxNewsFeed.js) is
  // bounded by the scrape's own lookback server-side and shows past 24h - it
  // is then "Stored", not "Fresh". The worker's best-effort headline keeps the
  // 24h window exactly as before.
  const stored = news.stored === true;
  if (Number.isFinite(t) && nowMs - t > MOMX_NEWS_FRESH_MS && !stored) return null;

  const url = typeof news.url === "string" && /^https?:\/\//i.test(news.url) ? news.url : null;
  return {
    headline,
    source: typeof news.source === "string" ? news.source.trim() : "",
    url,
    age: ageOf(iso, nowMs),
    // Fresh = published inside the 24h window; a stored story older than that
    // is still shown (the store exists so "no news" is never a guess) but
    // labelled as stored. Unreadable time: trusted as fresh, as before.
    fresh: !Number.isFinite(t) || nowMs - t <= MOMX_NEWS_FRESH_MS,
    stored,
    // From the store only: which aggregator carried it, the keyword sentiment
    // and the teaser. Empty for a worker headline.
    via: typeof news.via === "string" ? news.via.trim() : "",
    sentiment: typeof news.sentiment === "string" ? news.sentiment.trim() : "",
    summary: typeof news.summary === "string" ? news.summary.trim() : "",
    // The instant itself, for the NEWS view's Time column and its sort. null
    // when the worker shipped no readable stamp - the badge still shows (the
    // window was applied server-side) but there is no time to print.
    atMs: Number.isFinite(t) ? t : null,
    // IS THIS STORY ABOUT THIS TICKER, or a round-up that merely names it?
    //
    // Stamped by the worker (momx/news.py): an article naming more than three
    // tickers is "market-wide". Before that existed, 120 of 358 rows drew from
    // just 79 distinct headlines - "SanDisk Jumps 10%, Lululemon Crashes 17%"
    // sat on ten rows as though it explained each one. The competitor he
    // compared us to still has the defect (AMD, MRVL and AMDL all showing one
    // identical Nvidia story).
    //
    // Defaults to "specific" when the worker did not stamp it, so an older
    // payload keeps behaving exactly as it did rather than every badge
    // suddenly claiming to be a round-up.
    scope: news.scope === "market-wide" ? "market-wide" : "specific",
    namedCount: Number.isFinite(Number(news.namedCount)) ? Number(news.namedCount) : 0,
  };
}

// What the badge says on hover: source, age, and whether the story is actually
// about this ticker. All three exist in the payload today and none of them
// reached the screen - the title was the bare headline, so a two-day-old
// round-up from a feed he does not rate looked identical to a fresh story
// about his company.
//
// Deliberately NOT a new column: the board is width-locked and he decided on
// 2026-08-30 that news lives as a badge in the Symbol cell.
export function newsBadgeTitle(news) {
  if (!news || typeof news !== "object") return "";
  const bits = [];
  if (news.source) bits.push(news.source);
  if (news.age) bits.push(news.age);
  const lead = bits.length ? bits.join(" · ") + "\n" : "";
  const scopeNote =
    news.scope === "market-wide"
      ? "\n\nMarket-wide story" +
        (news.namedCount > 1 ? " naming " + news.namedCount + " tickers" : "") +
        " - it mentions this symbol, it is not about it."
      : "";
  return lead + String(news.headline || "") + scopeNote;
}

// What the Time column prints while NEWS is pressed (asked for 2026-09-02:
// "News 22, below but I don't see the time? I don't know when the news came").
// Same 24h clock as snapshotTimeLabel ("18:12"), prefixed with the ET weekday
// when the headline is from an earlier ET day ("Tue 18:12") - a 24h window
// straddles midnight, so a bare "22:40" at 01:45 would read as tonight. The
// age rides alongside, compact ("3h"), because on a phone there is no hover
// and a clock time alone still makes him do arithmetic.
export function newsTimeLabel(news, nowMs = Date.now()) {
  if (!news || typeof news !== "object" || !Number.isFinite(news.atMs)) return null;
  const at = news.atMs;
  try {
    const etDay = (ms) => new Date(ms).toLocaleDateString("en-US", { timeZone: "America/New_York" });
    const clock = new Date(at).toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
    const day = new Date(at).toLocaleDateString("en-US", { weekday: "short", timeZone: "America/New_York" });
    const label = etDay(at) === etDay(nowMs) ? clock : day + " " + clock;
    const age = ageOf(new Date(at).toISOString(), nowMs).replace(" ago", "").replace("just now", "now");
    return { label, age };
  } catch {
    return null;
  }
}
// HOW OLD the volume behind an RVOL reading is: "12m", "45m", "1h30", "3h".
//
// This was a clock time ("4:00") until he read it twice and was confused both
// times - once as morning-versus-afternoon, once as a possible duration ("16
// hrs?"). His actual question was never "what time was it" but "is this new",
// and a clock time makes him subtract against the header clock on every row.
// An age answers it directly and cannot be mistaken for a time of day.
//
// `barAt` is the volume-weighted midpoint of the bar, stamped by momx.columns
// from the tape - so this is "the volume behind this number arrived about
// that long ago", not "the bar opened that long ago".
//
// Empty only for a missing/unreadable stamp or one in the future (clock
// skew, same clamp as cellAgeIsFresh). Multi-day ages are real answers here -
// the Skittles block runs out to 4D, Wk and M.
export function cellAgeLabel(cell, nowMs) {
  if (!cell || typeof cell !== "object") return "";
  const seconds = toNumber(cell.barAt);
  if (seconds === null || seconds <= 0) return "";
  const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
  const minutes = Math.floor((now - seconds * 1000) / 60000);
  if (!Number.isFinite(minutes) || minutes < 0) return "";
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest ? `${hours}h${String(rest).padStart(2, "0")}` : `${hours}h`;
  }
  // Days, because the Skittles columns run out to 4D, Wk and M and he asked
  // for those by name: "D, 2D, 3D, 4D, W, M - also be fresh just add it".
  // There was a 24-hour cap here when only RVOL used this; it would have
  // blanked exactly the columns he wanted.
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours ? `${days}d${restHours}` : `${days}d`;
}

// The exact ET clock time behind that age, for the hover tooltip only.
// 24-hour on purpose: "16:00" cannot be read as 4am the way "4:00" can, and
// it matches the Time column on the same row rather than the 12-hour ticker
// label three columns away that means something else entirely.
export function cellAgeClock(cell) {
  if (!cell || typeof cell !== "object") return "";
  const seconds = toNumber(cell.barAt);
  if (seconds === null || seconds <= 0) return "";
  try {
    return new Date(seconds * 1000).toLocaleTimeString("en-US", {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      timeZone: "America/New_York",
    });
  } catch {
    return "";
  }
}
// Did the volume behind this RVOL reading arrive JUST NOW?
//
// "How do I know this is latest rvol or not?" - with the arrival time in the
// cell he could subtract against the header clock, but not while scanning
// fifty rows. This is that subtraction, done for him.
//
// MOMX_MATCHED_NEW_MS is reused deliberately rather than picking a new
// number: it is already what this app means by "new" (the NEW badge on a
// fresh match, and the alert cooldown). One definition, three places.
export function cellAgeIsFresh(cell, nowMs) {
  if (!cell || typeof cell !== "object") return false;
  const seconds = toNumber(cell.barAt);
  if (seconds === null || seconds <= 0) return false;
  const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
  const age = now - seconds * 1000;
  // A stamp from the future is clock skew, not freshness - clamp like
  // matchedSinceLabel does rather than reading it as "0 seconds old".
  return age >= 0 && age < MOMX_MATCHED_NEW_MS;
}
// A live cell: the script painted it AND what is behind it just arrived.
//
// RVOL      the volume landed inside the last 15 minutes.
// SKITTLES  the BAR opened inside the last 15 minutes, so the cross that
//           coloured it fired inside that window too.
//
// "Painted" is read from the colour his script assigned, not from a threshold
// re-derived here. His ladder gives a cell a background only at relVol >= 2 -
// CYAN/GREEN when the bar is bullish, MAGENTA/RED when it is not - and BLACK
// otherwise, which is the script saying "nothing here". So "has a background"
// already IS the threshold, and reading it keeps this in step if he ever
// changes the ladder.
//
// The freshness half is what makes it rare enough to be worth an animation.
// Measured over 520 symbol-moments of the 2026-09-03 session: freshness alone
// would light ~23 cells at once on a thirty-row board, freshness AND painted
// about five.
// "It should be real RVOL" (2026-09-26). The NUMBER stays exactly the TOS
// z-score - parity, and the back-test (scratchpad rvol_open_bt) found no RVOL
// formula change that picked better stocks. Only the DISPLAY changes for two
// readings that are not live buying:
//   "auction"  the volume behind it arrived 15:50-16:10 ET - the closing
//              auction/MOC prints. On 2026-09-25 55% of Watchlist names lit
//              5m RVOL on the 16:00 bar (DHI 22x its normal 5 minutes on
//              +0.9%). No scored rule or phone push reads RVOL after 15:30.
//   "old"      it is from an earlier ET day (Friday's close on a weekend,
//              yesterday's bar before today's first print).
// Returns "" for a live reading.
const _ET_PARTS = (() => {
  try {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "America/New_York", year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", hourCycle: "h23",
    });
  } catch {
    return null;
  }
})();
function _etDayMinute(ms) {
  if (!_ET_PARTS) return null;
  const parts = {};
  for (const part of _ET_PARTS.formatToParts(new Date(ms))) parts[part.type] = part.value;
  return { day: `${parts.year}-${parts.month}-${parts.day}`, minute: Number(parts.hour) * 60 + Number(parts.minute) };
}
export const RVOL_AUCTION_FROM_MIN = 15 * 60 + 50;
export const RVOL_AUCTION_UNTIL_MIN = 16 * 60 + 10;
export function rvolNotLiveReason(cell, nowMs) {
  if (!cell || typeof cell !== "object") return "";
  const seconds = toNumber(cell.barAt);
  if (seconds === null || seconds <= 0) return "";
  const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
  const bar = _etDayMinute(seconds * 1000);
  const today = _etDayMinute(now);
  if (!bar || !today) return "";
  if (bar.minute >= RVOL_AUCTION_FROM_MIN && bar.minute < RVOL_AUCTION_UNTIL_MIN) return "auction";
  if (bar.day < today.day) return "old";
  return "";
}

export function cellIsLiveSpike(cell, nowMs) {
  if (!cell || typeof cell !== "object") return false;
  if (!hasPaintedBackground(cell)) return false;
  return cellAgeIsFresh(cell, nowMs);
}
// What a saturated RVOL cell cannot say for itself.
//
// A z-score over 50 bars cannot exceed sqrt(49) = 7.0, so once a reading gets
// there the scale stops discriminating: CHPT traded 30.8x its average volume
// and scored 6.97; at 100x it would score the same. The server stamps `xAvg`
// (the plain multiple of the 50-bar average) on cells that reach the ceiling,
// and this turns it into hover text.
//
// Empty for every other cell - the plotted number already says everything
// there, and a tooltip on all of them would be noise.
export function rvolSaturationNote(cell) {
  if (!cell || typeof cell !== "object") return "";
  const multiple = toNumber(cell.xAvg);
  if (multiple === null || multiple <= 0) return "";
  const shown = multiple >= 10 ? Math.round(multiple) : multiple.toFixed(1);
  return `${shown}\u00d7 the 50-bar average volume \u2014 the score is capped at 7.0, so it cannot show how far past it this is`;
}
// Did his script assign this cell a background at all?
//
// Both ladders end in Color.BLACK, which is the script saying "nothing here" -
// below relVol 2 for RVOL, no cross for Skittles. So a painted background is
// the script's own "this matters", and reading it beats re-deriving whichever
// threshold produced it.
export function hasPaintedBackground(cell) {
  if (!cell || typeof cell !== "object") return false;
  const background = typeof cell.bg === "string" ? cell.bg.trim().toLowerCase() : cell.bg;
  return Boolean(background) && background !== "black";
}

// ---------------------------------------------------------------------------
// News + Event columns (2026-09-25, his MomoX screenshot: "see MomoX have news
// and earnings column, can we do the same"). MomoX prints the headline's AGE
// ("2h", "43m", "1d") in News and the next earnings date in Event.
// ---------------------------------------------------------------------------

// "43m" / "2h" / "1d": the compact age MomoX prints. "" when unreadable.
export function shortAge(atMs, nowMs = Date.now()) {
  if (!Number.isFinite(atMs)) return "";
  const minutes = Math.max(0, Math.floor((nowMs - atMs) / 60000));
  if (minutes < 60) return minutes + "m";
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return hours + "h";
  return Math.floor(hours / 24) + "d";
}

// The News cell, MomoX3-style (his screenshot 2026-09-26: "2h✦"): the
// headline's age, bright under 3 hours, plus a four-point sparkle on EVERY row
// that has a headline. The sparkle's colour is the AI reader's verdict when
// it judged the story (green good / red bad / purple neutral); otherwise the
// scraper's keyword sentiment (dimmer); otherwise plain. The headline itself,
// publisher and link live in the tooltip, the tap popover and the NEWS list -
// not in the column, which stays one narrow cell.
// null = no headline -> blank cell (the trader's parity choice: no "No" pill).
export function newsColumnCell(row, nowMs = Date.now()) {
  const news = newsOf(row, nowMs);
  if (!news) return null;
  const c = row && row.catalyst;
  const ai = c && typeof c === "object" && c.category && c.category !== "UNKNOWN"
    ? (c.direction === "bullish" ? "up" : c.direction === "bearish" ? "down" : "flat")
    : null;
  const age = news.atMs !== null ? shortAge(news.atMs, nowMs) : "";
  const fresh = news.atMs !== null && nowMs - news.atMs < 3 * 60 * 60 * 1000;
  const aiWord = ai === "up" ? "AI read: good news" : ai === "down" ? "AI read: bad news" : ai ? "AI read: neutral" : "";
  const sentiment = sentimentTone(news.sentiment);
  const tone = ai || sentiment || "none";
  // The plain word the trader asked for (2026-09-27): the cell goes green or
  // red with the story, and the hover SAYS positive or negative instead of
  // leaving him to decode a sparkle colour. The AI verdict wins; the scraper's
  // keyword sentiment is the weaker read and is labelled as such.
  const toneWord = tone === "up" ? "Positive" : tone === "down" ? "Negative" : tone === "flat" ? "Neutral" : "";
  const toneLine = toneWord ? toneWord + " news (" + (ai ? "AI read" : "keyword read") + ")\n" : "";
  const whence = [news.source, news.via && news.via !== news.source ? "via " + news.via : "", news.age]
    .filter(Boolean)
    .join(", ");
  return {
    text: age || "new",
    fresh,
    ai,
    // Sparkle colour: the AI verdict, else the keyword sentiment, else none.
    tone,
    toneWord,
    aiJudged: ai !== null,
    stored: news.stored === true,
    atMs: news.atMs,
    title: toneLine + news.headline + (whence ? " (" + whence + ")" : "")
      + (news.stored ? (news.fresh ? "\nFresh" : "\nStored") + (news.sentiment ? " · " + news.sentiment : "") : "")
      + (aiWord ? "\n" + aiWord + (c.summary ? ": " + c.summary : "") : ""),
  };
}

// /api/earnings-calendar payload -> Map SYMBOL -> {date, daysUntil, timingCode, timing}.
export function earningsMapFrom(payload) {
  const out = new Map();
  const rows = payload && Array.isArray(payload.rows) ? payload.rows : [];
  for (const r of rows) {
    if (!r || typeof r.symbol !== "string" || typeof r.date !== "string") continue;
    const days = Number(r.daysUntil);
    if (!Number.isFinite(days) || days < 0) continue;
    const held = out.get(r.symbol);
    if (held && held.daysUntil <= days) continue;
    out.set(r.symbol, {
      date: r.date,
      daysUntil: days,
      timingCode: typeof r.timingCode === "string" ? r.timingCode.toLowerCase() : "tbd",
      timing: typeof r.timing === "string" ? r.timing : "",
    });
  }
  return out;
}

// The Event cell: "10/28" plus ☀ (before the open) or ☾ (after the close).
// Amber when earnings are within 7 days - an option held through it carries
// the earnings jump and the volatility crush.
export function eventColumnCell(earnings) {
  if (!earnings || typeof earnings !== "object" || typeof earnings.date !== "string") return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(earnings.date);
  if (!m) return null;
  const days = Number(earnings.daysUntil);
  const icon = earnings.timingCode === "bmo" ? "☀" : earnings.timingCode === "amc" ? "☾" : "";
  const when = days === 0 ? "today" : days === 1 ? "tomorrow" : "in " + days + " days";
  return {
    text: Number(m[2]) + "/" + Number(m[3]),
    icon,
    soon: Number.isFinite(days) && days <= 7,
    days: Number.isFinite(days) ? days : null,
    title: "Earnings " + when + " (" + earnings.date + ")" + (earnings.timing ? " - " + earnings.timing : ""),
  };
}

/** The options-only gate (momx/optionable.py) in words, or null when nothing
 *  is held back. payload.optionsGate = {listCount, hidden: [...], pending: [...]}:
 *  hidden = its real option chain lists no options; pending = not checked yet
 *  (held out until its chain is read, usually under a minute). */
export function optionsGateNote(gate) {
  if (!gate || typeof gate !== "object") return null;
  const hidden = Array.isArray(gate.hidden) ? gate.hidden : [];
  const pending = Array.isArray(gate.pending) ? gate.pending : [];
  if (!hidden.length && !pending.length) return null;
  const parts = [];
  if (hidden.length) parts.push(hidden.length + " no options");
  if (pending.length) parts.push(pending.length + " checking");
  const lines = [];
  if (hidden.length) {
    lines.push("Not scanned - no listed options (from their real option chain): " + hidden.join(", ") + ".");
  }
  if (pending.length) {
    lines.push("Checking their option chain first, then scanned if they have options: " + pending.join(", ") + ".");
  }
  return { text: parts.join(" · "), title: lines.join(" ") };
}

/** One sentence for the Tickers box after a save/add, or "" when every typed
 *  ticker has options. reply = service add/set reply (noOptions, optionsChecking). */
export function typedOptionsSentence(reply) {
  if (!reply || typeof reply !== "object") return "";
  const none = Array.isArray(reply.noOptions) ? reply.noOptions : [];
  const checking = Array.isArray(reply.optionsChecking) ? reply.optionsChecking : [];
  let out = "";
  if (none.length) {
    out += " " + none.join(", ") + (none.length === 1 ? " has" : " have") +
      " no listed options, so the scanner will not show " + (none.length === 1 ? "it" : "them") + ".";
  }
  if (checking.length) {
    out += " Checking the option chain for " + checking.join(", ") + " first.";
  }
  return out;
}
