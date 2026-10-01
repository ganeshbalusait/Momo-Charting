import test from "node:test";
import assert from "node:assert/strict";
import { optionsGateNote, typedOptionsSentence } from "./momxCells.js";

test("options gate note: nothing held back -> null", () => {
  assert.equal(optionsGateNote(null), null);
  assert.equal(optionsGateNote({ listCount: 10, hidden: [], pending: [] }), null);
});

test("options gate note: counts and the names in the hover text", () => {
  const note = optionsGateNote({ listCount: 13, hidden: ["KITT", "LIDR"], pending: ["ELPW"] });
  assert.equal(note.text, "2 no options · 1 checking");
  assert.match(note.title, /no listed options.*KITT, LIDR/);
  assert.match(note.title, /Checking their option chain.*ELPW/);
});

test("typed tickers sentence", () => {
  assert.equal(typedOptionsSentence({ noOptions: [], optionsChecking: [] }), "");
  assert.equal(typedOptionsSentence({ noOptions: ["JAGX"] }),
    " JAGX has no listed options, so the scanner will not show it.");
  assert.equal(typedOptionsSentence({ noOptions: ["A", "B"], optionsChecking: ["C"] }),
    " A, B have no listed options, so the scanner will not show them. Checking the option chain for C first.");
});
