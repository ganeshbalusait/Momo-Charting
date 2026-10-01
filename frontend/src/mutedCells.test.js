// TOS hides a weak reading by painting it black on black. We must too.
//
// He compared CVS side by side: his TOS showed values in a few columns and the
// rest looked blank, while our board printed a number in every one. His RVOL
// ladder ends
//
//     if relVol > 0.5 then Color.DARK_GREEN else Color.BLACK
//
// on a background that is also black below 2 -- so TOS computes the value and
// then paints it invisibly, ON PURPOSE. It is a scannability device: the eye
// lands only on the timeframes that are actually moving.
//
// We were undoing it. readableInk() exists for a real bug (Skittles paints
// VIOLET on PLUM, contrast 1.12, and the number vanished on 2026-08-28) and it
// rescues any unreadable pairing to white or black. Black-on-black is
// unreadable, so it got rescued to white and every hidden value came back.
//
// The two cases are indistinguishable by contrast and opposite in intent, so
// the tests below pin BOTH: the deliberate hide is obeyed, and the accidental
// clash is still corrected.

import { describe, expect, test } from "vitest";

import { cellStyle, isMutedCell } from "./momxCells.js";

describe("isMutedCell", () => {
  test("black on black is the deliberate hide", () => {
    expect(isMutedCell({ value: 0.3, bg: "black", fg: "black" })).toBe(true);
  });

  test("black text on the row's own dark fill counts too", () => {
    // A null background is the row fill, which black text disappears into
    // just as completely as an explicit black one.
    expect(isMutedCell({ value: 0.3, bg: null, fg: "black" })).toBe(true);
    expect(isMutedCell({ value: 0.3, fg: "black" })).toBe(true);
  });

  test("black text on a COLOURED background is not hidden", () => {
    // The ladder paints black on cyan/green/red at the top rungs; those are
    // the most important cells on the board, not the least.
    for (const bg of ["cyan", "green", "red", "magenta"]) {
      expect(isMutedCell({ value: 3.4, bg, fg: "black" })).toBe(false);
    }
  });

  test("a coloured foreground is never hidden", () => {
    expect(isMutedCell({ value: 1.2, bg: "black", fg: "green" })).toBe(false);
    expect(isMutedCell({ value: 1.7, bg: "black", fg: "cyan" })).toBe(false);
    expect(isMutedCell({ value: 0.8, bg: "black", fg: "dark_green" })).toBe(false);
  });

  test("an empty cell is not 'muted' - there is nothing to hide", () => {
    // Muted means "a value exists and the script chose not to show it".
    // Conflating the two would lose the distinction between a warm-up cell
    // and a genuinely weak reading.
    expect(isMutedCell({ value: null, bg: "black", fg: "black" })).toBe(false);
    expect(isMutedCell({ value: undefined, bg: null, fg: null })).toBe(false);
  });

  test("case and stray whitespace do not defeat it", () => {
    expect(isMutedCell({ value: 0.1, bg: "BLACK", fg: " Black " })).toBe(true);
  });

  test("junk in never throws", () => {
    expect(isMutedCell(null)).toBe(false);
    expect(isMutedCell(undefined)).toBe(false);
    expect(isMutedCell("x")).toBe(false);
    expect(isMutedCell(7)).toBe(false);
  });
});

describe("cellStyle", () => {
  test("a hidden cell keeps its black ink instead of being rescued", () => {
    const style = cellStyle({ value: 0.3, bg: "black", fg: "black" });
    expect(style.color).toBe("#000000");
    expect(style.backgroundColor).toBe("#000000");
  });

  test("the violet-on-plum clash is STILL corrected", () => {
    // The regression readableInk was written for. This must not be lost while
    // teaching the code to respect black-on-black.
    const style = cellStyle({ value: 42, bg: "plum", fg: "violet" });
    expect(style.color).not.toBe("#ee82ee");           // not left as violet
    expect(["#000000", "#ffffff"]).toContain(style.color);
  });

  test("the DIM rungs keep their script colour instead of turning white", () => {
    // "> 0.5" is his ladder saying "barely worth noticing", and TOS paints it
    // dark green/dark red on black - dim on purpose. The rescue threshold used
    // to sit at 3.0, above dark_green's 2.82 and dark_red's 2.10, so both were
    // promoted to WHITE: the loudest ink on the board landing on its least
    // important cells. He spotted it at a glance ("i see white rvol number").
    for (const fg of ["dark_green", "dark_red"]) {
      const style = cellStyle({ value: 0.8, bg: "black", fg });
      expect(style.color).not.toBe("#ffffff");
      expect(style.color).toBe(fg === "dark_green" ? "#006400" : "#8b0000");
    }
  });

  test("a readable script colour is left exactly as the script asked", () => {
    const style = cellStyle({ value: 3.4, bg: "black", fg: "cyan" });
    expect(style.backgroundColor).toBe("#000000");
    expect(style.color).not.toBe("#000000");
    expect(style.color).not.toBe("#ffffff");
  });
});
