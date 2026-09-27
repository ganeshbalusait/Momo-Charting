import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  LEARN_ELSEWHERE,
  LEARN_GLOSSARY,
  LEARN_ICON_NAMES,
  LEARN_SECTIONS,
  LEARN_SETUP_SECTIONS,
  LEARN_STEPS,
} from "./learnContent.js";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

// Lift the real nav labels out of App.jsx. Tolerates \r: Python edits to
// App.jsx flip it to CRLF, and a regex that assumes \n looks like a flake.
export function navLabelsFromAppSource(source) {
  const block = source.match(/const optionNavItems = \[([\s\S]*?)\r?\n\];/);
  assert.ok(block, "optionNavItems not found in App.jsx - did it get renamed?");
  return [...block[1].matchAll(/\blabel:\s*"([^"]+)"/g)].map((m) => m[1]);
}

const NAV_LABELS = navLabelsFromAppSource(appSource);

// The single source of truth for the "no internals" copy rule, checked
// against both the taught-steps text and the full reference copy below.
// These two checks used to keep separate arrays and had already drifted
// apart (one had "api_key", the other had "schwab"/"alpaca"/"tradier" but
// not "api_key") - a banned term added to only one side is a silent gap.
const BANNED_INTERNAL_TERMS = [
  "api key", "api_key", "broker key", "localhost", "endpoint", "/api/",
  "schwab", "alpaca", "tradier",
];

test("App.jsx still exposes a nav list we can check against", () => {
  assert.ok(NAV_LABELS.length >= 10, `only found ${NAV_LABELS.length} nav labels`);
  assert.ok(NAV_LABELS.includes("Charts & OI"));
  assert.ok(NAV_LABELS.includes("Settings"));
});

// THE anti-drift test. Rename a page and this fails, instead of the Learn
// page's buttons silently doing nothing.
test("every Take me there button names a page that actually exists", () => {
  LEARN_STEPS.forEach((step) => {
    assert.ok(
      NAV_LABELS.includes(step.goTo),
      `step "${step.id}" points at "${step.goTo}", which is not in optionNavItems`,
    );
  });
});

test("there are exactly eight steps, numbered 1..8 in order", () => {
  // Step 8 (the Momo Alert, 2026-08-30) joined the original seven.
  assert.equal(LEARN_STEPS.length, 8);
  LEARN_STEPS.forEach((step, index) => assert.equal(step.number, index + 1));
});

test("step ids are unique and nothing is blank", () => {
  const ids = LEARN_STEPS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length, "duplicate step id");
  LEARN_STEPS.forEach((step) => {
    assert.ok(step.id && step.title && step.goToLabel, `step ${step.id} has a blank field`);
    assert.ok(Array.isArray(step.body) && step.body.length > 0, `step ${step.id} has no body`);
    step.body.forEach((p) => assert.ok(p.trim().length > 20, `step ${step.id} has a stub paragraph`));
  });
});

test("the icon allow-list is a non-empty set of unique names", () => {
  assert.ok(LEARN_ICON_NAMES.length > 0);
  assert.equal(new Set(LEARN_ICON_NAMES).size, LEARN_ICON_NAMES.length);
});

// The copy rules from the spec, enforced. These are the three promises the page
// must not break, and prose is exactly the kind of thing that drifts.
test("the copy makes no promise the data cannot keep", () => {
  const allText = LEARN_STEPS.flatMap((s) => [s.title, ...s.body]).join(" ").toLowerCase();
  BANNED_INTERNAL_TERMS.forEach((banned) => {
    assert.ok(!allText.includes(banned), `copy mentions "${banned}", which the spec forbids`);
  });
});

test("all six reference sections exist, in the order the trader named them", () => {
  assert.deepEqual(
    LEARN_SECTIONS.map((s) => s.id),
    ["charts", "indicators", "options", "alerts", "scanner", "momx"],
  );
});

test("every section points at a page that exists and uses an allowed icon", () => {
  LEARN_SECTIONS.forEach((section) => {
    assert.ok(NAV_LABELS.includes(section.goTo), `section "${section.id}" -> unknown page "${section.goTo}"`);
    assert.ok(LEARN_ICON_NAMES.includes(section.icon), `section "${section.id}" -> unknown icon "${section.icon}"`);
  });
});

test("every section explains the concept before naming a control", () => {
  LEARN_SECTIONS.forEach((section) => {
    assert.ok(Array.isArray(section.concept) && section.concept.length > 0, `${section.id} has no concept`);
    section.concept.forEach((p) => assert.ok(p.trim().length > 40, `${section.id} concept is a stub`));
    assert.ok(section.blocks.length > 0, `${section.id} has no blocks`);
  });
});

test("no block is empty or malformed", () => {
  LEARN_SECTIONS.flatMap((s) => s.blocks.map((b) => [s.id, b])).forEach(([id, block]) => {
    assert.ok(block.heading && block.heading.trim(), `${id} has a block with no heading`);
    const filled = [block.body, block.rows, block.chips].filter((v) => Array.isArray(v) && v.length > 0);
    assert.equal(filled.length, 1, `${id} block "${block.heading}" must have exactly one of body/rows/chips`);
    (block.rows || []).forEach((row) => {
      assert.ok(row.term && row.term.trim(), `${id} row missing term`);
      assert.ok(row.meaning && row.meaning.trim().length > 10, `${id} row "${row.term}" has a stub meaning`);
    });
  });
});

test("the glossary is unique and defined", () => {
  const terms = LEARN_GLOSSARY.map((g) => g.term.toLowerCase());
  assert.equal(new Set(terms).size, terms.length, "duplicate glossary term");
  LEARN_GLOSSARY.forEach((entry) => {
    assert.ok(entry.id && entry.term, "glossary entry missing id or term");
    assert.ok(entry.plain.trim().length > 25, `glossary "${entry.term}" is a stub`);
  });
  // The terms a new user will actually trip over.
  ["open interest", "delta", "rvol", "vwap", "expected move", "squeeze", "skittles"].forEach((needed) => {
    assert.ok(terms.some((t) => t.includes(needed)), `glossary is missing "${needed}"`);
  });
});

test("the page map covers every page not already taught, and nothing that is", () => {
  const taught = new Set(LEARN_SECTIONS.map((s) => s.goTo));
  LEARN_ELSEWHERE.forEach((entry) => {
    assert.ok(NAV_LABELS.includes(entry.goTo), `page map -> unknown page "${entry.goTo}"`);
    assert.ok(!taught.has(entry.goTo), `"${entry.goTo}" is both taught and listed as elsewhere`);
    assert.ok(entry.oneLiner.trim().length > 15, `page map "${entry.goTo}" is a stub`);
  });
  const covered = new Set([...taught, ...LEARN_ELSEWHERE.map((e) => e.goTo), "Learn + Setup"]);
  NAV_LABELS.forEach((label) => {
    assert.ok(covered.has(label), `"${label}" exists in the app but the Learn page never mentions it`);
  });
});

test("the reference copy also obeys the copy rules", () => {
  // Everything a reader can SEE, not just the prose. Chip labels, the button
  // captions, the glossary and the page map were all outside this join once,
  // which meant a banned data-source name could ship in the glossary silently.
  const allText = [
    ...LEARN_SECTIONS.flatMap((s) => [s.title, s.goToLabel, ...s.concept, ...s.blocks.flatMap((b) => [
      b.heading,
      ...(b.body || []),
      ...(b.rows || []).map((r) => `${r.term} ${r.meaning}`),
      ...(b.chips || []).map((c) => c.label),
    ])]),
    ...LEARN_GLOSSARY.map((g) => `${g.term} ${g.plain}`),
    ...LEARN_ELSEWHERE.map((e) => `${e.goToLabel} ${e.oneLiner}`),
  ]
    .join(" ")
    .toLowerCase();
  BANNED_INTERNAL_TERMS.forEach((banned) => {
    assert.ok(!allText.includes(banned), `copy mentions "${banned}", which the spec forbids`);
  });
  // No decimal-parity promise.
  ["exactly matches thinkorswim", "identical to thinkorswim", "matches tos exactly"].forEach((banned) => {
    assert.ok(!allText.includes(banned), `copy promises parity it cannot keep: "${banned}"`);
  });
});

// The Setup tab (2026-08-30). It is deliberately NOT joined into the
// banned-terms checks above: the trader reversed the shared-keys-only decision
// for this tab alone, so its copy is allowed to name brokers and keys. The
// Learn-tab copy keeps the old rule.
test("the Setup tab has well-formed sections", () => {
  assert.ok(LEARN_SETUP_SECTIONS.length > 0, "Setup tab is empty");
  const ids = LEARN_SETUP_SECTIONS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length, "duplicate setup section id");
  LEARN_SETUP_SECTIONS.forEach((section) => {
    assert.ok(section.id && section.title && section.title.trim(), `setup section ${section.id} missing id or title`);
    assert.ok(LEARN_ICON_NAMES.includes(section.icon), `setup section "${section.id}" -> unknown icon "${section.icon}"`);
    assert.ok(Array.isArray(section.blocks) && section.blocks.length > 0, `setup section ${section.id} has no blocks`);
    section.blocks.forEach((block) => {
      assert.ok(block.heading && block.heading.trim(), `${section.id} has a block with no heading`);
      const filled = [block.body, block.rows].filter((v) => Array.isArray(v) && v.length > 0);
      assert.equal(filled.length, 1, `${section.id} block "${block.heading}" must have exactly one of body/rows`);
      (block.rows || []).forEach((row) => {
        assert.ok(row.term && row.term.trim(), `${section.id} row missing term`);
        assert.ok(row.meaning && row.meaning.trim().length > 10, `${section.id} row "${row.term}" has a stub meaning`);
      });
    });
  });
});

// The Momo walkthrough MOVED to Setup; the momx chapter keeps only a pointer.
// If the rows ever get copied back, the same steps render twice.
test("the Momo setup steps live on the Setup tab, not in the momx chapter", () => {
  const momx = LEARN_SECTIONS.find((s) => s.id === "momx");
  const momxHeadings = momx.blocks.map((b) => b.heading);
  assert.ok(!momxHeadings.some((h) => h.startsWith("Set it up")), "Momo setup steps duplicated in the momx chapter");
  const setupHeadings = LEARN_SETUP_SECTIONS.flatMap((s) => s.blocks.map((b) => b.heading));
  // Renamed in the 2026-08-30 kid-simple rewrite; the guard's point is
  // unchanged - the on-screen steps and the phone steps live HERE, once.
  assert.ok(setupHeadings.includes("Turn it on"));
  assert.ok(setupHeadings.includes("Get it on your phone - locked, in your pocket"));
});

// The blocking defect from final review: .learn-view had NO CSS rule at all,
// so real scroll input did nothing - the app shell pins .workspace to
// overflow:hidden, and every view must supply its own scroll container. This
// pins that fix so a future edit to index.css cannot silently drop the rule
// (or its overflow) and reintroduce an unscrollable Learn page.
test("index.css still gives .learn-view a scroll rule", () => {
  const cssSource = readFileSync(new URL("./index.css", import.meta.url), "utf8");
  // Tolerates \r: see the App.jsx note above this file's other source lift.
  const block = cssSource.match(/\.learn-view\s*\{([\s\S]*?)\r?\n\}/);
  assert.ok(
    block,
    ".learn-view has no rule in index.css - without one, the .workspace shell's overflow:hidden makes the Learn page unscrollable by real input",
  );
  assert.ok(
    /overflow\s*:/.test(block[1]),
    ".learn-view exists but sets no overflow - it must be its own scroll container inside the overflow:hidden .workspace shell",
  );
});
