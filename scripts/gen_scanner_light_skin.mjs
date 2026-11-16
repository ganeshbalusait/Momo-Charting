// Writes the MomX Scanner's clean-look colours (release 2):
//   frontend/src/theme.scanner.light.css   the whole light theme of the scanner
//   frontend/src/theme.scanner.dark.css    dark: only faint grey text is lifted
//
// The scanner's ~550 colour rules in index.css were typed for a dark screen.
// Instead of retyping each one by hand, this reads them and writes a twin of
// every colour rule under
//   :root[data-theme="light"][data-agx-skin~="scanner"]            (light)
//   :root:not([data-theme="light"])[data-agx-skin~="scanner"]      (dark)
// so nothing applies unless the scanner skin is on (theme.js).
//
// Light (colours only: never a size, a padding or a width):
//   - dark neutral backgrounds        -> near-white
//   - white see-through layers        -> black see-through layers
//   - light text that would vanish    -> the same hue, darkened until readable
//   - pale borders                    -> darker borders
// Kept in light: solid signal fills (cyan / magenta / orange / green pills)
// with the text typed for them, dark text, the dim layer behind a dialog, and
// the ticker card's small day chart (chart drawing areas stay dark).
// Dark: signal colours and surfaces stay as typed; only grey text under 4.5:1
// becomes the shared second text colour (a state that is dim on purpose: the
// quieter dim ink).
//
// Run after changing a scanner colour in index.css:
//   node scripts/gen_scanner_light_skin.mjs
// (frontend/src/themeScannerSkin.test.js fails when the files are out of date.)

import { readFileSync, writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

const SRC = fileURLToPath(new URL("../frontend/src/index.css", import.meta.url));
export const OUTPUTS = {
  light: fileURLToPath(new URL("../frontend/src/theme.scanner.light.css", import.meta.url)),
  dark: fileURLToPath(new URL("../frontend/src/theme.scanner.dark.css", import.meta.url)),
};
export const SCOPES = {
  light: ':root[data-theme="light"][data-agx-skin~="scanner"]',
  dark: ':root:not([data-theme="light"])[data-agx-skin~="scanner"]',
};
// The scanner's classes and its History day picker.
const OWNED = /\.mom[xo]-|\.scanner-day-/;
const TEXT_PROPS = /^(color|fill|stroke|caret-color|-webkit-text-fill-color|text-decoration-color|text-decoration)$/;
const BG_PROPS = /^(background|background-color|background-image)$/;
const LINE_PROPS = /^(border|border-(top|right|bottom|left|color|block|inline)(-color)?|outline|outline-color|box-shadow|accent-color|text-shadow|column-rule|scrollbar-color)$/;
const COLOUR = /#[0-9a-fA-F]{3,8}\b|rgba?\([^()]*\)|\b(white|black)\b/g;
// The darkest light surface, and the lightest dark one, text must read on
// (--agx-raised in each theme).
const LIGHT_BG = { r: 236, g: 236, b: 240 };
const DARK_BG = { r: 44, g: 44, b: 46 };
// Rules left exactly as typed, in both themes.
// (.momo-alert-banner is drawn on every screen, so it is not the scanner's.)
const KEEP_RULES = [/overlay$/, /backdrop/, /\.momx-daychart( svg|-ext|-line|-wall|-em|-mark)/, /\.momo-alert-banner/];
// States that are dim on purpose: dark does not lift their grey text to the
// shared second colour, only as far as darkDimText() below.
const DIM_STATES = /\.is-(none|stored|few|hidden|off|gone|muted|stale|dim|disabled|placeholder)\b/;
// Classes shared with an older page outside the scanner: twinned only inside
// the scanner panel.
const SHARED = /^\.scanner-day-/;

export function parseColour(text) {
  if (text === "white") return { r: 255, g: 255, b: 255, a: 1 };
  if (text === "black") return { r: 0, g: 0, b: 0, a: 1 };
  if (text[0] === "#") {
    let h = text.slice(1);
    if (h.length <= 4) h = [...h].map((c) => c + c).join("");
    if (h.length !== 6 && h.length !== 8) return null;
    const n = (i) => parseInt(h.slice(i, i + 2), 16);
    return { r: n(0), g: n(2), b: n(4), a: h.length === 8 ? n(6) / 255 : 1 };
  }
  const parts = text.slice(text.indexOf("(") + 1, -1).split(/[\s,/]+/).filter(Boolean);
  if (parts.length < 3 || parts.some((p) => /[a-z(]/i.test(p.replace("%", "")))) return null;
  const channel = (p) => (p.endsWith("%") ? (parseFloat(p) * 255) / 100 : parseFloat(p));
  const alpha = parts[3] === undefined ? 1 : parts[3].endsWith("%") ? parseFloat(parts[3]) / 100 : parseFloat(parts[3]);
  return { r: channel(parts[0]), g: channel(parts[1]), b: channel(parts[2]), a: alpha };
}

function toHsl({ r, g, b }) {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b); const min = Math.min(r, g, b); const l = (max + min) / 2; const d = max - min;
  if (!d) return { h: 0, s: 0, l };
  const s = d / (1 - Math.abs(2 * l - 1));
  const h = max === r ? ((g - b) / d + 6) % 6 : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return { h: h * 60, s, l };
}

function fromHsl({ h, s, l }) {
  const c = (1 - Math.abs(2 * l - 1)) * s; const x = c * (1 - Math.abs(((h / 60) % 2) - 1)); const m = l - c / 2;
  const [r, g, b] = h < 60 ? [c, x, 0] : h < 120 ? [x, c, 0] : h < 180 ? [0, c, x] : h < 240 ? [0, x, c] : h < 300 ? [x, 0, c] : [c, 0, x];
  return { r: (r + m) * 255, g: (g + m) * 255, b: (b + m) * 255 };
}

const luminance = ({ r, g, b }) => {
  const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
};
export const contrast = (a, b) => {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
};

// Grey = little difference between the channels. (HSL saturation cannot be used
// for this: it blows up near white, so the bluish near-white #e7e9f0 looked
// "coloured" and came out blue instead of as plain text.)
const isGrey = ({ r, g, b }) => (Math.max(r, g, b) - Math.min(r, g, b)) / 255 < 0.16;

export function printColour({ r, g, b, a = 1 }) {
  const n = (v) => Math.max(0, Math.min(255, Math.round(v)));
  if (a >= 0.995) return "#" + [r, g, b].map((v) => n(v).toString(16).padStart(2, "0")).join("");
  return `rgba(${n(r)}, ${n(g)}, ${n(b)}, ${Math.round(a * 100) / 100})`;
}

// A fill that carries meaning by itself (a cyan / orange / green pill) or is
// already light: kept, together with the text typed for it.
export const isSolidFill = (c) => {
  const { s, l } = toHsl(c);
  return c.a >= 0.5 && (l > 0.78 || (s >= 0.35 && l >= 0.25));
};

// Each converter returns the new colour, or null to keep what index.css says.
// 5.6:1 against the greyest light surface leaves room for tinted buttons.
export function lightText(c) {
  const { h, s, l } = toHsl(c);
  if (contrast(c, LIGHT_BG) >= 5.6 && c.a >= 0.85) return null;
  if (isGrey(c)) return l >= 0.7 ? { r: 29, g: 29, b: 31, a: 1 } : { r: 99, g: 99, b: 102, a: 1 };
  let next = { h, s: Math.min(s, 0.9), l: Math.min(l, 0.5) };
  while (contrast(fromHsl(next), LIGHT_BG) < 5.6 && next.l > 0.05) next = { ...next, l: next.l - 0.01 };
  return { ...fromHsl(next), a: 1 };
}

export function lightBackground(c) {
  const { h, s, l } = toHsl(c);
  if (c.a < 0.995) {
    if (isGrey(c) && l > 0.6) return { r: 0, g: 0, b: 0, a: c.a * 0.7 };
    // A nearly solid dark panel (a floating banner, a "to top" button) is a
    // panel: it becomes a nearly solid light one. A faint dark veil is a shade.
    if (l < 0.25 && c.a >= 0.5) return { ...fromHsl({ h, s: Math.min(s, 0.3) * 0.35, l: Math.min(0.985, 0.925 + l * 0.4) }), a: c.a };
    if (isGrey(c) && l < 0.25) return { r: 0, g: 0, b: 0, a: c.a * 0.12 };
    if (l < 0.25) return { ...fromHsl({ h, s, l: 0.45 }), a: c.a * 0.6 };
    return null;
  }
  if (l >= 0.3) return null;
  if (isGrey(c)) return { ...fromHsl({ h, s: Math.min(s, 0.3) * 0.35, l: Math.min(0.985, 0.925 + l * 0.4) }), a: 1 };
  return { ...fromHsl({ h, s: s * 0.7, l: 0.91 }), a: 1 };
}

export function lightLine(c) {
  const { h, s, l } = toHsl(c);
  if (c.a < 0.995) {
    if (isGrey(c) && l > 0.6) return { r: 0, g: 0, b: 0, a: Math.min(1, c.a * 0.9) };
    if (isGrey(c) && l < 0.25) return { r: 0, g: 0, b: 0, a: c.a * 0.22 };
    return l > 0.6 ? { ...fromHsl({ h, s: Math.min(s, 0.9), l: 0.36 }), a: c.a } : null;
  }
  if (isGrey(c)) return { ...fromHsl({ h, s: Math.min(s, 0.2), l: Math.max(0.45, Math.min(0.88, 0.95 - l * 0.62)) }), a: 1 };
  return l > 0.6 ? { ...fromHsl({ h, s: Math.min(s, 0.9), l: 0.36 }), a: 1 } : null;
}

export function darkText(c) {
  const { l } = toHsl(c);
  if (!isGrey(c) || l < 0.3 || l > 0.8) return null;
  if (contrast(c, DARK_BG) >= 4.6 && c.a >= 0.85) return null;
  return { r: 161, g: 161, b: 166, a: 1 };
}

// A state that is dim on purpose (DIM_STATES) stays quieter than the #a1a1a6
// above, but must still be readable. 2026-11-14: the age of an older stored
// headline in the News column ("10h", #6b7280) read 3.8:1 on a board row and
// 3.5:1 on the hovered row, and the name of a hidden column in the Columns
// dialog (#6b7080) 3.3:1; the look's rule is 4.5:1 for every label. A dim grey
// that already reads 4.5:1 on the hovered row (--momx-row-hover #1c1c1f in
// theme.css, the lightest surface a board cell sits on) is kept as typed;
// any other becomes the look's dim ink, --momx-ink-dim #8e8e93:
// 5.6:1 on a row (#141416), 5.2:1 hovered, 5.1:1 on a hidden column's line.
const DARK_ROW_HOVER = { r: 28, g: 28, b: 31 };
export function darkDimText(c) {
  const { l } = toHsl(c);
  if (!isGrey(c) || l < 0.3 || l > 0.8) return null;
  if (contrast(c, DARK_ROW_HOVER) >= 4.5 && c.a >= 0.85) return null;
  return { r: 142, g: 142, b: 147, a: 1 };
}

// css text -> [{ selector, wrappers: ["@media ..."], declarations: [[prop, value]] }]
export function readRules(css) {
  const text = css.replace(/\r\n/g, "\n").replace(/\/\*[\s\S]*?\*\//g, "");
  const rules = []; const stack = []; let buffer = "";
  for (let i = 0; i < text.length; i += 1) {
    const ch = text[i];
    if (ch === "{") {
      stack.push({ head: buffer.trim().replace(/\s+/g, " "), body: "" }); buffer = "";
    } else if (ch === "}") {
      const block = stack.pop(); if (!block) continue;
      block.body += buffer; buffer = "";
      if (block.head.startsWith("@")) continue;
      const wrappers = stack.map((s) => s.head);
      if (wrappers.some((w) => /^@(-webkit-)?keyframes|^@font-face/.test(w))) continue;
      const declarations = block.body.split(";").map((d) => d.trim()).filter(Boolean).map((d) => {
        const at = d.indexOf(":");
        return [d.slice(0, at).trim(), d.slice(at + 1).trim()];
      });
      rules.push({ selector: block.head, wrappers, declarations });
    } else if (ch === ";" && stack.length) {
      stack[stack.length - 1].body += buffer + ";"; buffer = "";
    } else buffer += ch;
  }
  return rules;
}

function splitSelectors(selector) {
  const parts = []; let depth = 0; let current = "";
  for (const ch of selector) {
    if (ch === "(") depth += 1; else if (ch === ")") depth -= 1;
    if (ch === "," && depth === 0) { parts.push(current.trim()); current = ""; } else current += ch;
  }
  if (current.trim()) parts.push(current.trim());
  return parts;
}

const scoped = (part, scope) => {
  if (/^(:root|html)\b/.test(part)) return part.replace(/^(:root|html)/, scope);
  return SHARED.test(part) ? `${scope} .momx-scanner-panel ${part}` : `${scope} ${part}`;
};

function converterFor(prop, theme, ownFill, dimOnPurpose = false) {
  if (TEXT_PROPS.test(prop)) return { convert: ownFill ? null : theme === "dark" ? (dimOnPurpose ? darkDimText : darkText) : lightText };
  if (theme === "dark") return null;
  if (BG_PROPS.test(prop)) return { convert: lightBackground };
  if (LINE_PROPS.test(prop)) return { convert: lightLine };
  return null;
}

// A twin may set COLOUR only. "border: 1px solid #x" is therefore written as
// "border-color", never as the shorthand: a twin outranks every plain rule, so
// a shorthand would also reset the style and width that a more specific rule
// set (a dashed border came out solid, a dotted underline came out plain).
const WIDTH_OR_STYLE = /^(-?[\d.]+[a-z%]*|thin|medium|thick|none|hidden|dotted|dashed|solid|double|groove|ridge|inset|outset|underline|overline|line-through|wavy|auto|from-font)$/;
function tokens(value) {
  const out = []; let depth = 0; let current = "";
  for (const ch of value) {
    if (ch === "(") depth += 1; else if (ch === ")") depth -= 1;
    if (/\s/.test(ch) && depth === 0) { if (current) out.push(current); current = ""; } else current += ch;
  }
  if (current) out.push(current);
  return out;
}
const COLOUR_LONGHAND = {
  border: "border-color", "border-top": "border-top-color", "border-right": "border-right-color",
  "border-bottom": "border-bottom-color", "border-left": "border-left-color", "border-block": "border-block-color",
  "border-inline": "border-inline-color", outline: "outline-color", "column-rule": "column-rule-color",
  "text-decoration": "text-decoration-color",
};
// [property, value] to write for one declaration, or null when it carries no colour.
export function colourOnly(prop, value) {
  if (COLOUR_LONGHAND[prop]) {
    const colour = tokens(value).filter((token) => !WIDTH_OR_STYLE.test(token)).join(" ");
    return colour ? [COLOUR_LONGHAND[prop], colour] : null;
  }
  if (prop === "background") {
    if (/gradient\(|url\(/.test(value)) return [prop, value];
    return ["background-color", value === "none" ? "transparent" : value];
  }
  return [prop, value];
}

// The twin of one index.css rule for a theme, or null when it has no colours.
export function skinRule(rule, theme = "light") {
  const parts = splitSelectors(rule.selector).filter((part) => OWNED.test(part));
  if (!parts.length || parts.every((part) => KEEP_RULES.some((re) => re.test(part)))) return null;
  const ownFill = rule.declarations.some(([prop, value]) => BG_PROPS.test(prop)
    && (value.match(COLOUR) || []).map(parseColour).some((c) => c && isSolidFill(c)));
  const dimOnPurpose = theme === "dark" && parts.every((part) => DIM_STATES.test(part));
  const out = [];
  for (const [prop, rawValue] of rule.declarations) {
    if (prop.startsWith("--")) continue;
    const kind = converterFor(prop, theme, ownFill, dimOnPurpose);
    if (!kind) continue;
    const important = /!important\s*$/.test(rawValue);
    const value = rawValue.replace(/\s*!important\s*$/, "").replace(/\s+/g, " ");
    // Every colour declaration is written out, changed or not. A twin outranks
    // the rule it came from, so a kept colour left out here would lose to the
    // twin of a more general rule (the purple MX A+ tag took the plain tag's
    // text colour).
    const next = !kind.convert ? value : value.replace(COLOUR, (literal) => {
      const colour = parseColour(literal); if (!colour) return literal;
      const converted = kind.convert(colour);
      return converted ? printColour(converted) : literal;
    });
    const written = colourOnly(prop, next);
    if (written) out.push(`  ${written[0]}: ${written[1]}${important ? " !important" : ""};`);
  }
  if (!out.length) return null;
  return { wrappers: rule.wrappers, text: `${parts.map((part) => scoped(part, SCOPES[theme])).join(",\n")} {\n${out.join("\n")}\n}` };
}

export function generate(css, theme = "light") {
  const blocks = readRules(css).map((rule) => skinRule(rule, theme)).filter(Boolean);
  const lines = [
    "/* GENERATED by scripts/gen_scanner_light_skin.mjs from the scanner rules in",
    "   index.css. Do not edit by hand: change the colour in index.css (or a rule in",
    "   the script) and run  node scripts/gen_scanner_light_skin.mjs",
    theme === "dark"
      ? "   Dark theme of the MomX Scanner: text colours only; faint grey text is lifted."
      : "   Light theme of the MomX Scanner. Colours only: no sizes, paddings or widths.",
    "   Applies only when the scanner skin is on. */",
    "",
  ];
  for (const block of blocks) {
    const open = block.wrappers.map((w) => `${w} {`).join(" ");
    lines.push(open ? `${open}\n${block.text}\n${"}".repeat(block.wrappers.length)}` : block.text, "");
  }
  return { text: lines.join("\n"), count: blocks.length };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const css = readFileSync(SRC, "utf8");
  // 2026-11-13: dark only (Ganesh dropped the Light theme), so only the dark
  // file is written. The light rules stay below until they are removed.
  for (const theme of ["dark"]) {
    const { text, count } = generate(css, theme);
    writeFileSync(OUTPUTS[theme], text);
    console.log(`wrote ${count} ${theme} rules`);
  }
}
