import { describe, test } from "node:test";
import assert from "node:assert/strict";

import { GLOSSARY, glossaryEntry, glossaryKey } from "./longTermGlossary.js";
import { DEEP_HEADING, DEEP_NONE, drawerSections, leapSection } from "./longTerm.js";

// Ganesh 11-07: "I need each name 'I' for information - what is MOS, ROIC, EPS".

describe("glossary entries", () => {
  test("every entry has a term and a plain sentence", () => {
    for (const [key, entry] of Object.entries(GLOSSARY)) {
      assert.ok(entry.term && entry.term.length > 2, `${key} has a term`);
      assert.ok(entry.text && entry.text.length > 40, `${key} explains itself`);
      assert.doesNotMatch(entry.text, /undefined|null|TODO/, `${key} text is finished`);
    }
  });
  test("the three he named are explained", () => {
    assert.match(GLOSSARY.mos.text, /margin/i);
    assert.match(GLOSSARY.roic_now.term, /return on invested capital/i);
    assert.match(GLOSSARY.eps_growth.term, /earnings per share/i);
  });
  test("unknown keys give nothing, never a crash", () => {
    assert.equal(glossaryEntry("nope"), null);
    assert.equal(glossaryEntry(null), null);
    assert.equal(glossaryKey(undefined), null);
    assert.equal(glossaryKey("2025: sales 1B"), null);
  });
});

describe("every drawer line finds its explanation", () => {
  const detail = {
    unit: "USD",
    checks: [
      { key: "roic_now", label: "ROIC now", value: 0.4, threshold: "≥ 15%", passed: true },
      { key: "roic_consistency", label: "ROIC consistency", value: 1, threshold: "≥ 10% in 80% of years", passed: true },
      { key: "sales_growth", label: "Sales growth", value: 0.17, threshold: "≥ 10%/yr", passed: true },
      { key: "eps_growth", label: "EPS growth", value: 0.21, threshold: "≥ 10%/yr", passed: true },
      { key: "equity_growth", label: "Equity growth", value: 0.25, threshold: "≥ 10%/yr", passed: true },
      { key: "fcf_growth", label: "FCF growth", value: 0.16, threshold: "≥ 10%/yr", passed: true },
      { key: "debt", label: "Debt payable", value: 0.6, threshold: "debt ≤ 3× FCF", passed: true },
      { key: "earning", label: "Earning money now", value: 17.92, threshold: "EPS TTM > 0", passed: true },
      { key: "history", label: "History and freshness", value: 10, threshold: "≥ 3", passed: true },
      { key: "eps_not_falling", label: "Earnings not falling", value: 1.07, threshold: "≥ 0.8×", passed: true },
      { key: "sources", label: "Sources agree", value: null, threshold: "EDGAR and Finnhub agree", passed: true },
    ],
    explain: [
      "Sticker EPS $17.9 (TTM $17.9, 3-yr avg $13.6 × 1.5 = $20.4) growing 15%/yr (capped at 15%)",
      "Historical P/E median 45.4 → future P/E 30 (2 × g)",
      "Discounted 15%/yr → sticker $538",
      "Buffett value $751 (owner earnings $21.0/share growing 15%/yr, 10% discount, 3% terminal)",
      "Fair value $538 = lower of sticker and Buffett (sticker)",
      "Safety Buy $269 at 50% margin ($376 at 30%)",
      "LEAPS flag below $430 (80% of sticker)",
      "Payback 7 years (owner earnings growing 15%/yr vs market cap; Town buys at ≤ 8)",
    ],
  };
  const sections = drawerSections(detail);
  const lines = (title) => sections.find((s) => s.title === title).lines;

  test("checklist lines, passed, failed and n/a", () => {
    const expected = ["roic_now", "roic_consistency", "sales_growth", "eps_growth", "equity_growth", "fcf_growth",
      "debt", "earning", "history", "eps_not_falling", "sources"];
    assert.deepEqual(lines("Checklist").map(glossaryKey), expected);
    assert.equal(glossaryKey("✗ ROIC now 9% ≥ 15%"), "roic_now");
    assert.equal(glossaryKey("n/a ROIC consistency — only 2 computable ROIC years"), "roic_consistency");
    assert.equal(glossaryKey("✓ ROE now 18% ≥ 15%"), "roe_now", "banks are scored on ROE");
  });
  test("valuation lines", () => {
    assert.deepEqual(lines("Valuation").map(glossaryKey),
      ["sticker", "pe", "discount", "buffett", "fair", "safety", "leaps_flag", "payback"]);
  });
  test("LEAP box lines, entry and deep", () => {
    const payload = {
      price: 239,
      contract: { kind: "entry", expiry: "2027-09-17", dte: 314, strike: 420, bid: 5.7, ask: 7, mid: 6.35, delta: 0.143,
        openInterest: 6, volume: 34, breakeven: 427, breakevenPct: 0.787, intrinsic: 0, extrinsic: 7, cost: 700,
        wideSpread: true, thin: true },
      deep: { kind: "deep", expiry: "2027-09-17", dte: 314, strike: 200, ask: 70.2, delta: 0.75, openInterest: 0,
        breakeven: 270.2, breakevenPct: 0.13, thin: true },
    };
    const keys = leapSection(payload).map(glossaryKey);
    for (const k of ["entry", "quote", "breakeven", "intrinsic", "oi", "wide", "thin", "deep"]) {
      assert.ok(keys.includes(k), `the LEAP box explains ${k}`);
    }
    assert.equal(glossaryKey(DEEP_HEADING), "deep");
    assert.equal(glossaryKey(DEEP_NONE), "deep");
    assert.equal(glossaryKey("No ENTRY contract: no expiry 3-12 months out · deep $200 call"), "entry");
  });
});
