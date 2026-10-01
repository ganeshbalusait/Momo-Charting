"""The lightning bolt stops meaning "weekly options" and starts meaning the fires.

2026-09-02, answering "what should open a ticker's card?": "remove - 'weekly
option', see WFC screenshot i want same".

The bolt on the board was `row.badge` = {on, reasons:["weeklies"], tooltip:
"Weekly options"} - it marked symbols that HAVE weekly options. The bolt on
the card he wants means something else entirely: the signals that fired,
"9x20 D · MACD 2D · 4x8 2D · 4x8 3D · MACD W · MACD Mo".

We already carry exactly that, on every row, in `scanReasons` - NVDA reads
["macd:4h", "ema9x20:4h", "ema4x8:4h", "macd:2D", "sqzfired:D"] right now.
So this is a re-labelling, not new data: firesOf() turns the wire vocabulary
into his, and badgeOf() is retired.

Deliberately NOT a colour decision. Like the momentum strip, this is UI
chrome; nothing here reads THINKSCRIPT_COLORS and nothing here may be cited
as parity.
"""
import io

p = "frontend/src/momxCells.js"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '''// The lightning badge is written by a DIFFERENT agent's backend change and may'''
NEW = '''// ---------------------------------------------------------------------------
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

// The lightning badge is written by a DIFFERENT agent's backend change and may'''
assert s.count(OLD) == 1, "cells anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("momxCells.js: firesOf / fireLabel")
