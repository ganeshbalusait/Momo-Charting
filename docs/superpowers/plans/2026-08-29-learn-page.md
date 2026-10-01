# Learn Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one new page, "Learn", that teaches a brand-new AGX user the app — a seven-step first-session path, then a collapsed reference section per feature area, with real screenshots and a glossary.

**Architecture:** Content lives as plain data in `learnContent.js` and `learnScreenshots.js` (no JSX, importable by `node --test`). `LearningCenter.jsx` renders that data and nothing else. Screenshots are captured by a re-runnable Playwright script into `frontend/public/learn/`, with numbered callouts positioned in CSS over the image rather than baked into the PNG. `App.jsx` gains only a nav entry, a header button, a render block, and a first-visit check.

**Tech Stack:** React 19, Vite 7, lucide-react 0.468.0, `node --test` for tests, Playwright 1.62.1 (devDependency, capture only).

**Spec:** `docs/superpowers/specs/2026-08-29-learning-center-design.md` — read it before starting. Its "Content outline" section is the authoritative enumeration of what the copy must cover, and its "Honesty constraints on the copy" section is binding on every word written.

## Global Constraints

- **Working directory is `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2`** — the live app. The outer `AgenticAI-Trading 7` is a mirror with no `node_modules`; edits there do nothing.
- **Tests run as** `npm test` is not defined; use `node --test src/<file>.test.js` from `frontend/`. Full suite: `node --test src/*.test.js`.
- **No `.test.js` file may import JSX.** The runner has no JSX transform. Test data modules only, or lift `App.jsx` as text with `readFileSync`.
- **Never edit `App.jsx` with a Python script.** Python edits flip the file to CRLF and break every test that regexes functions out of its source. Use the Edit tool or `node`.
- **New CSS goes at the very END of `frontend/src/index.css`** (17,461 lines). A `max-width:760px` block partway through is silently outranked by a later unconditional block; appending is the only safe placement.
- **All new CSS classes are prefixed `learn-`.**
- **Copy rules, binding on every user-facing word:** no claim of decimal-exact thinkorswim parity; no instruction to configure personal broker keys; no data-source names, re-auth cadence, cache behaviour, file names, or endpoint names. User-visible timings (9:15 AM ET, 06:00–09:30 ET, 60-second refresh) ARE stated.
- **Icon imports are load-bearing.** A missing lucide import does not fail the build — esbuild compiles it to a reference that throws at render time, producing a green build and a blank page.
- **Commit after every task.** This repo has no remote; commits are the only safety net.

---

### Task 1: First-visit decision module

Pure logic, no dependencies. Decides whether the Learn page opens by itself.

**Files:**
- Create: `frontend/src/learnFirstVisit.js`
- Test: `frontend/src/learnFirstVisit.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `LEARN_SEEN_STORAGE_KEY: string`, `LEARN_CONTENT_VERSION: number`, `shouldAutoOpenLearn({ seenVersion, popoutMode, savedView }): boolean`, `readLearnSeen(storage): number|null`, `markLearnSeen(storage, version): void`. Task 7 consumes all five.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/learnFirstVisit.test.js`:

```js
import assert from "node:assert/strict";
import { test } from "node:test";

import {
  LEARN_CONTENT_VERSION,
  LEARN_SEEN_STORAGE_KEY,
  markLearnSeen,
  readLearnSeen,
  shouldAutoOpenLearn,
} from "./learnFirstVisit.js";

function fakeStorage(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    dump: () => Object.fromEntries(map),
  };
}

const throwingStorage = {
  getItem() { throw new Error("storage disabled"); },
  setItem() { throw new Error("storage disabled"); },
};

test("a brand-new browser is shown the Learn page", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "", savedView: "" }),
    true,
  );
});

test("a browser that has already seen this version is not shown it again", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: LEARN_CONTENT_VERSION, popoutMode: "", savedView: "" }),
    false,
  );
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: LEARN_CONTENT_VERSION + 5, popoutMode: "", savedView: "" }),
    false,
  );
});

test("a rewrite can re-introduce itself to a browser that saw an older version", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: LEARN_CONTENT_VERSION - 1, popoutMode: "", savedView: "" }),
    true,
  );
});

// A pop-out chart window turning itself into the Learn page would be absurd,
// and the trader keeps several open.
test("a pop-out window never becomes the Learn page", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "chart", savedView: "" }),
    false,
  );
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "chain", savedView: "" }),
    false,
  );
});

// A returning browser is mid-workflow. Yanking it to Learn is the failure mode
// this whole gate exists to prevent.
test("a browser with a remembered page is left where it was", () => {
  assert.equal(
    shouldAutoOpenLearn({ seenVersion: null, popoutMode: "", savedView: "MomX Scanner" }),
    false,
  );
});

test("missing and malformed input never throws", () => {
  assert.equal(shouldAutoOpenLearn(undefined), true);
  assert.equal(shouldAutoOpenLearn({}), true);
  assert.equal(shouldAutoOpenLearn({ seenVersion: "not a number" }), true);
});

test("readLearnSeen reads a stored number and rejects junk", () => {
  assert.equal(readLearnSeen(fakeStorage({ [LEARN_SEEN_STORAGE_KEY]: "1" })), 1);
  assert.equal(readLearnSeen(fakeStorage({ [LEARN_SEEN_STORAGE_KEY]: "banana" })), null);
  assert.equal(readLearnSeen(fakeStorage()), null);
});

test("markLearnSeen writes the version", () => {
  const storage = fakeStorage();
  markLearnSeen(storage, 1);
  assert.equal(storage.dump()[LEARN_SEEN_STORAGE_KEY], "1");
});

// localStorage throws outright in some privacy modes. A storage exception must
// never take the app down with it.
test("a throwing storage is swallowed, not propagated", () => {
  assert.doesNotThrow(() => readLearnSeen(throwingStorage));
  assert.equal(readLearnSeen(throwingStorage), null);
  assert.doesNotThrow(() => markLearnSeen(throwingStorage, 1));
  assert.doesNotThrow(() => readLearnSeen(null));
  assert.doesNotThrow(() => markLearnSeen(null, 1));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run from `frontend/`: `node --test src/learnFirstVisit.test.js`
Expected: FAIL — `Cannot find module './learnFirstVisit.js'`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/learnFirstVisit.js`:

```js
// Whether the Learn page opens by itself. All three conditions are load-bearing:
// a pop-out chart window must never become a help page, and a browser that
// already has a remembered page is mid-workflow and must not be yanked away.

export const LEARN_SEEN_STORAGE_KEY = "agxLearnSeen";

// Bump to re-introduce a rewritten Learn page to browsers that saw an older one.
export const LEARN_CONTENT_VERSION = 1;

export function shouldAutoOpenLearn({ seenVersion, popoutMode, savedView } = {}) {
  if (popoutMode) return false;
  if (savedView) return false;
  const seen = Number(seenVersion);
  if (Number.isFinite(seen) && seen >= LEARN_CONTENT_VERSION) return false;
  return true;
}

export function readLearnSeen(storage) {
  try {
    const raw = storage?.getItem(LEARN_SEEN_STORAGE_KEY);
    // Number(null) is 0, and 0 is finite - so testing Number(raw) directly
    // would report a NEVER-WRITTEN key as version 0 instead of "unseen". This
    // codebase has already been bitten by exactly this (see the legacyNumber
    // note in App.jsx, where it pinned a saved split at its minimum).
    if (raw === null || raw === undefined || raw === "") return null;
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

export function markLearnSeen(storage, version = LEARN_CONTENT_VERSION) {
  try {
    storage?.setItem(LEARN_SEEN_STORAGE_KEY, String(version));
  } catch {
    // Storage is blocked. The page simply shows again next time; that is a far
    // better outcome than an exception on mount.
  }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/learnFirstVisit.test.js`
Expected: PASS, 9 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/learnFirstVisit.js frontend/src/learnFirstVisit.test.js
git commit -m "feat(learn): decide once, and only once, whether Learn opens itself"
```

---

### Task 2: Content module shape, the seven steps, and the anti-drift test

The anti-drift test is the most valuable thing in this plan: it makes a page rename break the build instead of leaving a dead button.

**Files:**
- Create: `frontend/src/learnContent.js`
- Test: `frontend/src/learnContent.test.js`

**Interfaces:**
- Consumes: nothing.
- Produces: `LEARN_STEPS`, `LEARN_ICON_NAMES`, and (added in Task 3) `LEARN_SECTIONS`, `LEARN_GLOSSARY`, `LEARN_ELSEWHERE`. Tasks 4 and 5 consume all of them.

Shapes, exactly:

```js
LEARN_STEPS:    { id, number, title, body: [string], goTo, goToLabel, screenshot }
LEARN_SECTIONS: { id, title, icon, goTo, goToLabel, screenshot, concept: [string], blocks: [Block] }
Block:          { heading, body: [string] } | { heading, rows: [{ term, meaning }] } | { heading, chips: [{ label, tone }] }
LEARN_GLOSSARY: { id, term, plain }
LEARN_ELSEWHERE:{ goTo, goToLabel, oneLiner }
```

- [ ] **Step 1: Write the failing test**

Create `frontend/src/learnContent.test.js`:

```js
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { LEARN_ICON_NAMES, LEARN_STEPS } from "./learnContent.js";

const appSource = readFileSync(new URL("./App.jsx", import.meta.url), "utf8");

// Lift the real nav labels out of App.jsx. Tolerates \r: Python edits to
// App.jsx flip it to CRLF, and a regex that assumes \n looks like a flake.
export function navLabelsFromAppSource(source) {
  const block = source.match(/const optionNavItems = \[([\s\S]*?)\r?\n\];/);
  assert.ok(block, "optionNavItems not found in App.jsx - did it get renamed?");
  return [...block[1].matchAll(/\blabel:\s*"([^"]+)"/g)].map((m) => m[1]);
}

const NAV_LABELS = navLabelsFromAppSource(appSource);

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

test("there are exactly seven steps, numbered 1..7 in order", () => {
  assert.equal(LEARN_STEPS.length, 7);
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
  ["api key", "api_key", "broker key", "localhost", "endpoint", "/api/"].forEach((banned) => {
    assert.ok(!allText.includes(banned), `copy mentions "${banned}", which the spec forbids`);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/learnContent.test.js`
Expected: FAIL — `Cannot find module './learnContent.js'`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/learnContent.js`. This is the whole file for this task; Task 3 appends to it.

```js
// The Learn page's words, as data. No JSX and no React here on purpose: this
// module is imported directly by node --test, which has no JSX transform, and
// that is what lets the tests check the copy and the navigation targets.
//
// Every `goTo` MUST be an exact optionNavItems label from App.jsx. A test
// enforces it, so a renamed page breaks the suite instead of leaving a dead
// button on this page.
//
// Copy rules (from the design doc, and binding):
//   - never claim the boards match thinkorswim to the decimal
//   - never tell a user to configure their own broker keys
//   - no data sources, no file names, no endpoint names, no internals

export const LEARN_ICON_NAMES = Object.freeze([
  "chart", "indicators", "options", "alerts", "scanner", "momx",
]);

export const LEARN_STEPS = Object.freeze([
  {
    id: "find-something-moving",
    number: 1,
    title: "Find something moving",
    body: [
      "Trading starts with a shortlist, not a chart. AGX gives you two ways to build one.",
      "The Scanner watches nine large-cap names every trading morning between 6:00 and 9:30 AM ET and tells you which are waking up before the bell. MomX Scanner is the wider net: a few hundred names scored the same way, refreshed every minute, all day.",
      "Start with the Scanner in the morning. Switch to MomX once the market is open.",
    ],
    goTo: "Premarket Scanner",
    goToLabel: "Open the Scanner",
    screenshot: "premarket-scanner",
  },
  {
    id: "put-it-on-a-chart",
    number: 2,
    title: "Put it on a chart",
    body: [
      "Type a ticker at the top of the chart, pick a timeframe, and pick how many charts you want on screen at once.",
      "You can show anything from a single chart up to ten at a time, and there is a MAG7 layout that puts all seven big names side by side. Most people start with one chart on the 5-minute and add more later.",
    ],
    goTo: "Charts & OI",
    goToLabel: "Open the chart",
    screenshot: "chart-workstation",
  },
  {
    id: "read-the-trend",
    number: 3,
    title: "Read the trend",
    body: [
      "Your chart already has five lines on it before you touch anything: three exponential moving averages at 9, 21 and 50 bars, a 200-bar simple moving average, and VWAP, the volume-weighted average price.",
      "The short reading: when the faster lines sit above the slower ones and price sits above VWAP, buyers are in control on that timeframe. When they cross back the other way, that control is changing hands.",
      "Underneath the candles are two more panels showing the same question answered across several timeframes at once, so you can see whether the 5-minute move agrees with the hourly one.",
    ],
    goTo: "Charts & OI",
    goToLabel: "Open the chart",
    screenshot: "chart-workstation",
  },
  {
    id: "see-where-price-stalls",
    number: 4,
    title: "See where price is likely to stall",
    body: [
      "The red and green horizontal bands across the chart are option walls. Red bands sit above price where large numbers of call contracts are open; green bands sit below where large numbers of puts are.",
      "These are the prices where a lot of money has already taken a position, so price often slows down, stalls, or reverses when it reaches one. They are not guarantees. They are the levels worth knowing about before you enter.",
    ],
    goTo: "Charts & OI",
    goToLabel: "Show me the walls",
    screenshot: "chart-oi-walls",
  },
  {
    id: "check-the-option",
    number: 5,
    title: "Check the option itself",
    body: [
      "A good chart setup can still be a bad option. The Options page shows you the contract, not just the stock.",
      "For each strike you get how many days are left, its delta, how much volume and open interest it carries, and how that compares with the at-the-money strike. The expected move tells you roughly how far the stock is priced to travel before expiry.",
      "What you are looking for is a strike with real activity in it, close enough to the money to respond when the stock moves.",
    ],
    goTo: "Quick Options",
    goToLabel: "Open the Options board",
    screenshot: "high-oi-board",
  },
  {
    id: "let-the-app-watch",
    number: 6,
    title: "Let the app watch it for you",
    body: [
      "You cannot stare at nine charts at once, so hand the watching over.",
      "Every trading morning at 9:15 AM ET, AGX builds a ladder of the biggest call strikes above the price and the biggest put strikes below it, then tells you when price actually confirms through one. MAG7 is watched by default, and you can add your own tickers.",
      "You can also draw your own alert on any chart by clicking a price. It snaps to whatever level it lands on, so the alert is named after the thing you cared about.",
    ],
    goTo: "Auto Alert",
    goToLabel: "Open Alerts",
    screenshot: "auto-alert-panel",
  },
  {
    id: "save-your-setup",
    number: 7,
    title: "Save your setup",
    body: [
      "Once a chart looks the way you want it, save it. Your indicator choices can be saved for every ticker on that timeframe at once, so you set up the 5-minute chart once and every symbol you open inherits it.",
      "Your watchlist is shared across the scanners and the chart, so a ticker you save in one place shows up in the others.",
    ],
    goTo: "Watchlist",
    goToLabel: "Open the Watchlist",
    screenshot: null,
  },
]);
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/learnContent.test.js`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/learnContent.js frontend/src/learnContent.test.js
git commit -m "feat(learn): the seven-step first session, and a test that kills dead nav buttons"
```

---

### Task 3: The six reference sections, glossary, and page map

**Files:**
- Modify: `frontend/src/learnContent.js` (append three exports)
- Modify: `frontend/src/learnContent.test.js` (extend)

**Interfaces:**
- Consumes: `LEARN_ICON_NAMES` from Task 2.
- Produces: `LEARN_SECTIONS`, `LEARN_GLOSSARY`, `LEARN_ELSEWHERE`. Task 5 renders all three.

**Content source of truth:** the spec's "Content outline" → "Part 2 — Reference sections" (A–F), "Part 3 — Glossary", "Part 4 — Where the rest lives". Every bullet listed there must appear in the shipped content. Do not invent behaviour; if a bullet is unclear, read the code path named in the spec rather than guessing.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/learnContent.test.js`:

```js
import { LEARN_ELSEWHERE, LEARN_GLOSSARY, LEARN_SECTIONS } from "./learnContent.js";

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
  const covered = new Set([...taught, ...LEARN_ELSEWHERE.map((e) => e.goTo), "Learn"]);
  NAV_LABELS.forEach((label) => {
    assert.ok(covered.has(label), `"${label}" exists in the app but the Learn page never mentions it`);
  });
});

test("the reference copy also obeys the copy rules", () => {
  const allText = LEARN_SECTIONS
    .flatMap((s) => [s.title, ...s.concept, ...s.blocks.flatMap((b) => [
      b.heading, ...(b.body || []), ...(b.rows || []).map((r) => `${r.term} ${r.meaning}`),
    ])])
    .join(" ")
    .toLowerCase();
  ["api key", "broker key", "localhost", "endpoint", "/api/", "schwab", "alpaca", "tradier"].forEach((banned) => {
    assert.ok(!allText.includes(banned), `copy mentions "${banned}", which the spec forbids`);
  });
  // No decimal-parity promise.
  ["exactly matches thinkorswim", "identical to thinkorswim", "matches tos exactly"].forEach((banned) => {
    assert.ok(!allText.includes(banned), `copy promises parity it cannot keep: "${banned}"`);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/learnContent.test.js`
Expected: FAIL — `LEARN_SECTIONS` is not exported.

- [ ] **Step 3: Write the implementation**

Append `LEARN_SECTIONS`, `LEARN_GLOSSARY` and `LEARN_ELSEWHERE` to `frontend/src/learnContent.js`.

Section A is written out in full below as the model for tone, depth, and block shape. Write B–F to the same standard, covering every bullet in the spec's Content outline for that section.

```js
export const LEARN_SECTIONS = Object.freeze([
  {
    id: "charts",
    title: "Charts",
    icon: "chart",
    goTo: "Charts & OI",
    goToLabel: "Open the chart",
    screenshot: "chart-workstation",
    concept: [
      "A chart is one ticker's price history drawn as candles. Each candle covers a fixed slice of time and shows four things: where price opened, where it closed, and the highest and lowest it reached in between.",
      "The same stock tells a different story depending on the slice you choose. A 5-minute chart shows you today's fight; a daily chart shows you the trend that fight sits inside. Reading both is the point of having eleven timeframes rather than one.",
    ],
    blocks: [
      {
        heading: "Timeframes",
        body: [
          "3m, 5m, 10m, 15m, 30m, 1h, 2h, 4h, D (daily), W (weekly) and M (monthly).",
          "A common habit: check the 4-hour and daily for direction, then drop to the 5-minute to time the entry. If those disagree, the trade is a fight, not a trend.",
        ],
      },
      {
        heading: "Charts on screen at once",
        body: [
          "One chart, or up to ten. You can put two side by side, stack them, use a grid, or spread six, seven or eight across a wide monitor. The MAG7 layout puts all seven big names in one row.",
          "Column dividers in the wide layouts can be dragged, and each chart keeps its own ticker and timeframe.",
        ],
      },
      {
        heading: "Drawing on the chart",
        rows: [
          { term: "Crosshair", meaning: "The default. Move the pointer to read any candle's values, and drag to move the chart." },
          { term: "Trend line", meaning: "A sloped line between two points, for drawing the path a move has been following." },
          { term: "Horizontal line", meaning: "A flat line at one price, for marking a level you care about." },
          { term: "Fibonacci retracement", meaning: "Drag across a move to mark the levels a pullback commonly stops at." },
          { term: "Rectangle", meaning: "Box off a zone rather than a single price, such as a consolidation range." },
          { term: "Brush", meaning: "Freehand drawing, for when a shape matters more than precision." },
          { term: "Text note", meaning: "Type a note directly on the chart so you remember why you marked something." },
          { term: "Measure", meaning: "Drag from one point to another to read the distance in both price and time." },
        ],
      },
      {
        heading: "Before the bell and after it",
        body: [
          "Shaded areas mark pre-market and after-hours trading. Candles exist there, but far fewer people are trading, so moves can be larger and less reliable than they look.",
          "Vertical session lines mark the times of day that matter, so you can see at a glance where the open and close fall.",
        ],
      },
      {
        heading: "Saving what you set up",
        body: [
          "Save this timeframe for all tickers applies your indicator choices to every symbol you open on that timeframe, so you configure the 5-minute chart once.",
          "Save chart layout remembers the arrangement of panels. Reset puts the timeframe back to how it arrived.",
        ],
      },
      {
        heading: "Linking and popping out",
        body: [
          "The coloured link buttons tie surfaces together: set two of them to the same colour and changing the ticker in one changes it in the other.",
          "Any chart can be popped out into its own window, which is how you fill a second monitor.",
        ],
      },
    ],
  },
  // ... sections B-F follow, same shape.
]);
```

Sections B–F, each with `concept` then `blocks`, covering the spec's outline:

- `indicators` / icon `indicators` / goTo `Charts & OI` / screenshot `indicators-panel` — concept: an indicator is a second opinion drawn over price, and more is not better. Blocks: **What is already on** (EMA 9 magenta, EMA 21 yellow, EMA 50 cyan, SMA 200 dark green, VWAP white, plus OI levels, EMA clouds, the multi-timeframe clouds, the signal labels, session lines, previous day/week/month levels, and the three lower panels) as a `rows` block; **Trend lines**, **Clouds**, **Signal labels**, **Lower panels**, **Levels** as `rows` blocks naming each study in the spec's grouping; **Turning one on and saving it** as a `body` block; a `chips` block showing the default line colours using tones `ema9`, `ema21`, `ema50`, `sma200`, `vwap`.
- `options` / icon `options` / goTo `Quick Options` / screenshot `high-oi-board` — concept: what open interest is and why a wall acts like a speed bump. Blocks: **Chain or High OI** (`body`); **The ATM banner** (`body`); **What each column means** (`rows`, all fifteen columns from the spec); **Strong, moderate, weak** (`body`); **The colour key** (`chips` using tones `call-wall`, `put-wall`); **Taking the levels elsewhere** (`body`, the thinkorswim/TradingView export).
- `alerts` / icon `alerts` / goTo `Auto Alert` / screenshot `auto-alert-panel` — concept: an alert is the app watching a price line for you. Blocks: **The morning ladder** (`body`, built 9:15 AM ET, MAG7 by default, add your own); **Touched versus confirmed** (`rows` — this is the most important distinction on the page: a wick through a level is an early warning and moves nothing; a completed 5-minute candle closing through it confirms and advances to the next target); **What the message tells you** (`body`); **Alerts you draw yourself** (`body`, snapping to named levels); **Sound and notifications** (`body`).
- `scanner` / icon `scanner` / goTo `Premarket Scanner` / screenshot `premarket-scanner` — concept: which of nine big names is waking up before the bell. Blocks: **The window and the names** (`body`, 6:00–9:30 AM ET, the nine tickers); **What the signals mean** (`rows` — CALL2H and CALL4H are read from the same study drawn on your chart, so the scanner and the chart cannot disagree); **Squeeze fires** (`body`, 1h/2h/4h/Daily, and that 15m and 30m are left out deliberately because they fire almost continuously and would drown the score); **The four numbers at the top** (`rows` — MATCHES, STRONG, LIVE TAPES, FEED; STRONG needs more than three hits); **The morning briefing** (`body`); **Looking back** (`body`, 30 days as table, calendar or list).
- `momx` / icon `momx` / goTo `MomX Scanner` / screenshot `momx-board` — concept: the wide board, a few hundred names scored the same way every minute. Blocks: **What a row has to do to appear** (`body` — at least $3; 4-hour volume momentum up at least 0.5% against two bars ago; 1-hour price up at least 0.3% against two bars ago; and then at least one of a momentum cross, a fired squeeze, or a relative-volume hit); **Reading the board left to right** (`body`, the column order from the spec); **What each column means** (`rows` — Industry, % Chg, the 1D sparkline, RVOL, High/Low, the white separator, Squeeze, Skittles, Quote Trend); **How often it refreshes** (`body`, every 60 seconds, and the last board stays on screen while it refreshes); **Your own lists** (`body`).

Then:

```js
export const LEARN_GLOSSARY = Object.freeze([
  { id: "open-interest", term: "Open interest", plain: "The number of option contracts at one strike that are currently held by someone. High open interest means a lot of money already has a position at that price." },
  { id: "wall", term: "Wall", plain: "A strike carrying so much open interest that price tends to slow down, stall or turn when it gets there. Red walls sit above price, green walls below." },
  { id: "atm-otm-itm", term: "ATM, OTM, ITM", plain: "At the money means the strike is roughly where the stock is trading. Out of the money means it still needs to move for the option to be worth anything at expiry. In the money means it already is." },
  { id: "dte", term: "DTE", plain: "Days to expiration - how many days are left before the option expires. Fewer days means faster moves in the option's price, in both directions." },
  { id: "delta", term: "Delta", plain: "Roughly how much the option's price moves for a one dollar move in the stock. A delta of 0.30 means about thirty cents per dollar." },
  { id: "expected-move", term: "Expected move", plain: "How far the options market is pricing the stock to travel by expiry, up or down. It is a range, not a prediction of direction." },
  { id: "vwap", term: "VWAP", plain: "Volume-weighted average price - the average price paid today, weighted by how much traded at each level. Price above it usually means buyers are in control." },
  { id: "ema-sma", term: "EMA and SMA", plain: "Moving averages, which smooth price into a line. An EMA reacts faster to recent moves than an SMA of the same length." },
  { id: "rvol", term: "RVOL", plain: "Relative volume - how much is trading now compared with what is normal for this stock at this time of day. Above one means busier than usual." },
  { id: "squeeze", term: "Squeeze", plain: "A period where a stock's range tightens and pressure builds. When the squeeze fires, that pressure releases and a larger move often follows." },
  { id: "skittles", term: "Skittles", plain: "A fast momentum reading between 0 and 100, coloured by which way the trend is running. High and green means momentum is stretched upward." },
  { id: "rth-ext", term: "Regular and extended hours", plain: "Regular hours run 9:30 AM to 4:00 PM ET. Extended hours are the trading before and after that, where far fewer people are active and moves are less reliable." },
  { id: "five-minute-close", term: "5-minute close confirmation", plain: "Waiting for a full five-minute candle to finish above or below a level before treating it as broken. It filters out brief spikes that immediately reverse." },
]);

export const LEARN_ELSEWHERE = Object.freeze([
  { goTo: "Watchlist", goToLabel: "Watchlist", oneLiner: "Your saved tickers, shared by the scanners and the chart." },
  { goTo: "News Feed", goToLabel: "News Feed", oneLiner: "Headlines and catalysts for the big names and your watchlist." },
  { goTo: "Earnings Calendar", goToLabel: "Earnings Calendar", oneLiner: "Which of your saved tickers report, and when." },
  { goTo: "Mag7 Scanner", goToLabel: "Mag7 Watchlist", oneLiner: "A dense board of signals and flow for the seven largest names." },
  { goTo: "ROI Calc", goToLabel: "ROI Calc", oneLiner: "A quick expected-move and premium screen when you want to check a number by hand." },
  { goTo: "OI Level Script TOS", goToLabel: "OI Level Scripts", oneLiner: "Export the same option levels the chart draws, to use in thinkorswim or TradingView." },
  { goTo: "Settings", goToLabel: "Settings", oneLiner: "Your account and workspace preferences." },
]);
```

Note the page-map test asserts every nav label is either taught, listed, or `Learn` itself. `Quick Options`, `Charts & OI`, `Auto Alert`, `Premarket Scanner` and `MomX Scanner` are covered by sections; the seven above cover the rest.

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/learnContent.test.js`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/learnContent.js frontend/src/learnContent.test.js
git commit -m "feat(learn): six reference sections, glossary, and a page map that cannot go stale"
```

---

### Task 4: Screenshot manifest

Written before any image exists, so the page and its tests are ready for images to land.

**Files:**
- Create: `frontend/src/learnScreenshots.js`
- Test: `frontend/src/learnScreenshots.test.js`

**Interfaces:**
- Consumes: `LEARN_SECTIONS`, `LEARN_STEPS` from Tasks 2–3.
- Produces: `LEARN_SCREENSHOTS`, `PENDING_CAPTURE`, `screenshotById(id): object|null`, `isScreenshotReady(id): boolean`. Task 5 calls `screenshotById` and `isScreenshotReady`; Task 8's capture script reads the id list; Task 9 fills in callouts.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/learnScreenshots.test.js`:

```js
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { test } from "node:test";

import { LEARN_SECTIONS, LEARN_STEPS } from "./learnContent.js";
import { LEARN_SCREENSHOTS, PENDING_CAPTURE, screenshotById } from "./learnScreenshots.js";

const publicDir = new URL("../public/learn/", import.meta.url);

test("manifest ids are unique", () => {
  const ids = LEARN_SCREENSHOTS.map((s) => s.id);
  assert.equal(new Set(ids).size, ids.length);
});

test("every screenshot referenced by the content exists in the manifest", () => {
  [...LEARN_STEPS, ...LEARN_SECTIONS]
    .map((item) => item.screenshot)
    .filter(Boolean)
    .forEach((id) => {
      assert.ok(screenshotById(id), `content references screenshot "${id}", which is not in the manifest`);
    });
});

// Captured images must be on disk; pending ones must NOT be. That second half is
// the point: it stops "pending" quietly becoming a permanent excuse for an
// image nobody ever took.
test("captured images are on disk and pending ones are honestly absent", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    const onDisk = existsSync(new URL(shot.file, publicDir));
    if (PENDING_CAPTURE.includes(shot.id)) {
      assert.ok(!onDisk, `"${shot.id}" is listed as pending but the file exists - remove it from PENDING_CAPTURE`);
    } else {
      assert.ok(onDisk, `"${shot.id}" is not pending but public/learn/${shot.file} is missing`);
    }
  });
});

test("every image has alt text, because these are the only images on the page", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    assert.ok(shot.alt && shot.alt.trim().length > 20, `"${shot.id}" has weak or missing alt text`);
    assert.ok(shot.file.endsWith(".png"), `"${shot.id}" is not a png`);
  });
});

test("callouts are numbered from 1, positioned on the image, and readable", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    const callouts = shot.callouts || [];
    if (PENDING_CAPTURE.includes(shot.id)) return;
    assert.ok(callouts.length >= 1, `"${shot.id}" has no callouts`);
    assert.ok(callouts.length <= 6, `"${shot.id}" has ${callouts.length} callouts; more than six is unreadable on a phone`);
    callouts.forEach((callout, index) => {
      assert.equal(callout.n, index + 1, `"${shot.id}" callouts must be numbered 1..n in order`);
      assert.ok(callout.x >= 0 && callout.x <= 100, `"${shot.id}" callout ${callout.n} x is off the image`);
      assert.ok(callout.y >= 0 && callout.y <= 100, `"${shot.id}" callout ${callout.n} y is off the image`);
      assert.ok(callout.text && callout.text.trim().length > 10, `"${shot.id}" callout ${callout.n} has stub text`);
    });
  });
});

test("capture dates are real and not in the future", () => {
  LEARN_SCREENSHOTS.forEach((shot) => {
    if (PENDING_CAPTURE.includes(shot.id)) return;
    const when = new Date(shot.capturedOn);
    assert.ok(!Number.isNaN(when.getTime()), `"${shot.id}" has an unparseable capturedOn`);
    assert.ok(when.getTime() <= Date.now(), `"${shot.id}" was captured in the future`);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `node --test src/learnScreenshots.test.js`
Expected: FAIL — `Cannot find module './learnScreenshots.js'`

- [ ] **Step 3: Write the implementation**

Create `frontend/src/learnScreenshots.js` with all eight ids and **every id in `PENDING_CAPTURE`** for now. Task 9 removes ids from that list as images land.

```js
// Screenshots for the Learn page.
//
// Callouts live HERE, not baked into the PNG. Repositioning a marker, fixing
// its wording, or translating it never means re-shooting the image - and the
// text stays real text, so it is selectable and a screen reader can read it.
//
// Every file must be reproducible by `npm run learn:shots`. Never hand-edit an
// image in this folder.

export const LEARN_SCREENSHOTS = Object.freeze([
  { id: "chart-workstation", file: "chart-workstation.png", width: 1200, height: 760,
    capturedOn: "", alt: "A single five-minute chart with candles, three moving averages and VWAP drawn over them.", callouts: [] },
  { id: "chart-oi-walls", file: "chart-oi-walls.png", width: 1200, height: 760,
    capturedOn: "", alt: "The chart's price area with red call-resistance bands above price and green put-support bands below.", callouts: [] },
  { id: "indicators-panel", file: "indicators-panel.png", width: 900, height: 900,
    capturedOn: "", alt: "The indicators and studies panel open, listing the available studies with their toggles.", callouts: [] },
  { id: "high-oi-board", file: "high-oi-board.png", width: 1000, height: 900,
    capturedOn: "", alt: "The High OI board showing the at-the-money banner above rows of call and put strikes.", callouts: [] },
  { id: "auto-alert-panel", file: "auto-alert-panel.png", width: 700, height: 900,
    capturedOn: "", alt: "The Auto Alert panel showing a ticker card with its current call and put targets.", callouts: [] },
  { id: "momx-board", file: "momx-board.png", width: 1400, height: 800,
    capturedOn: "", alt: "The MomX board: rows of tickers with relative volume, squeeze and Skittles columns.", callouts: [] },
  { id: "premarket-scanner", file: "premarket-scanner.png", width: 1400, height: 800,
    capturedOn: "", alt: "The premarket Scanner board showing matched tickers with their momentum signals.", callouts: [] },
  { id: "phone-more-sheet", file: "phone-more-sheet.png", width: 375, height: 812,
    capturedOn: "", alt: "The phone More sheet, with Learn listed at the top.", callouts: [] },
]);

// Ids with no image on disk yet. The test asserts these files are ABSENT, so an
// id cannot sit here forever pretending an image is coming.
//
// premarket-scanner can only be captured on a trading morning between 06:00 and
// 09:30 ET - outside that window the board is empty and the picture teaches
// nothing. momx-board reads best during regular hours.
export const PENDING_CAPTURE = Object.freeze([
  "chart-workstation", "chart-oi-walls", "indicators-panel", "high-oi-board",
  "auto-alert-panel", "momx-board", "premarket-scanner", "phone-more-sheet",
]);

const BY_ID = new Map(LEARN_SCREENSHOTS.map((shot) => [shot.id, shot]));

export function screenshotById(id) {
  return BY_ID.get(id) || null;
}

// A shot is renderable only once it has actually been captured. The Learn page
// falls back to its text treatment for anything still pending, so a missing
// image is a quieter page, never a broken-image icon.
export function isScreenshotReady(id) {
  return Boolean(BY_ID.has(id) && !PENDING_CAPTURE.includes(id));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `node --test src/learnScreenshots.test.js`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/learnScreenshots.js frontend/src/learnScreenshots.test.js
git commit -m "feat(learn): screenshot manifest with callouts in CSS, not baked into the pixels"
```

---

### Task 5: The LearningCenter component

**Files:**
- Create: `frontend/src/LearningCenter.jsx`

**Interfaces:**
- Consumes: everything from Tasks 2–4.
- Produces: `default export LearningCenter({ onNavigate })`. Task 7 mounts it.

There is no test for this task — the runner has no JSX transform, and the repo's convention is to test the data modules and verify components in the browser. Verification is Step 3 and Task 10.

- [ ] **Step 1: Write the component**

Create `frontend/src/LearningCenter.jsx`:

```jsx
import { memo } from "react";
import { Bell, ChartCandlestick, Database, Radar, ScanSearch, Activity } from "lucide-react";

import { LEARN_ELSEWHERE, LEARN_GLOSSARY, LEARN_SECTIONS, LEARN_STEPS } from "./learnContent.js";
import { isScreenshotReady, screenshotById } from "./learnScreenshots.js";

// learnContent.js names icons as plain strings so node --test can import it.
// This map is the only place those names become components. An unknown name
// falls back rather than throwing: a missing lucide import does not fail the
// build here, it throws at render time and blanks the page.
const ICONS = {
  chart: ChartCandlestick,
  indicators: Activity,
  options: Database,
  alerts: Bell,
  scanner: ScanSearch,
  momx: Radar,
};

function SectionIcon({ name }) {
  const Icon = ICONS[name] || Activity;
  return <Icon size={17} strokeWidth={1.8} />;
}

function GoThere({ goTo, label, onNavigate }) {
  if (!goTo) return null;
  return (
    <button className="learn-goto" type="button" onClick={() => onNavigate?.(goTo)}>
      {label}
    </button>
  );
}

function Shot({ id }) {
  if (!id || !isScreenshotReady(id)) return null;
  const shot = screenshotById(id);
  if (!shot) return null;
  const captured = new Date(shot.capturedOn);
  const stamp = Number.isNaN(captured.getTime())
    ? ""
    : captured.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  return (
    <figure className="learn-shot">
      <div className="learn-shot-frame">
        <img
          src={`/learn/${shot.file}`}
          alt={shot.alt}
          width={shot.width}
          height={shot.height}
          loading="lazy"
          decoding="async"
        />
        {(shot.callouts || []).map((callout) => (
          <span
            className="learn-shot-marker"
            key={callout.n}
            style={{ left: `${callout.x}%`, top: `${callout.y}%` }}
            aria-hidden="true"
          >{callout.n}</span>
        ))}
      </div>
      {(shot.callouts || []).length ? (
        <figcaption>
          <ol className="learn-shot-legend">
            {shot.callouts.map((callout) => (
              <li key={callout.n}><b>{callout.n}</b><span>{callout.text}</span></li>
            ))}
          </ol>
          {stamp ? <small className="learn-shot-stamp">Captured {stamp}</small> : null}
        </figcaption>
      ) : null}
    </figure>
  );
}

function Block({ block }) {
  return (
    <div className="learn-block">
      <h4>{block.heading}</h4>
      {block.body ? block.body.map((paragraph, index) => <p key={index}>{paragraph}</p>) : null}
      {block.rows ? (
        <dl className="learn-defs">
          {block.rows.map((row) => (
            <div key={row.term}><dt>{row.term}</dt><dd>{row.meaning}</dd></div>
          ))}
        </dl>
      ) : null}
      {block.chips ? (
        <div className="learn-chips">
          {block.chips.map((chip) => (
            <span className={`learn-chip learn-chip-${chip.tone}`} key={chip.label}>
              <i />{chip.label}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function LearningCenter({ onNavigate }) {
  return (
    <div className="learn-page">
      <header className="learn-hero">
        <h1>Welcome to AGX</h1>
        <p>
          AGX finds stocks that are moving, shows you what the chart and the option chain
          say about them, and watches the levels you care about so you do not have to.
        </p>
        <p className="learn-hero-note">
          Work through the seven steps below on your first morning. Everything after them is
          reference you can come back to.
        </p>
      </header>

      <section className="learn-steps" aria-labelledby="learn-steps-heading">
        <h2 id="learn-steps-heading">Your first 15 minutes</h2>
        {LEARN_STEPS.map((step) => (
          <article className="learn-step" key={step.id}>
            <span className="learn-step-number" aria-hidden="true">{step.number}</span>
            <div>
              <h3>{step.title}</h3>
              {step.body.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
              <Shot id={step.screenshot} />
              <GoThere goTo={step.goTo} label={step.goToLabel} onNavigate={onNavigate} />
            </div>
          </article>
        ))}
      </section>

      <section className="learn-reference" aria-labelledby="learn-reference-heading">
        <h2 id="learn-reference-heading">How each part works</h2>
        {LEARN_SECTIONS.map((section) => (
          <details className="learn-section" key={section.id}>
            <summary>
              <SectionIcon name={section.icon} />
              <span>{section.title}</span>
            </summary>
            <div className="learn-section-body">
              {section.concept.map((paragraph, index) => (
                <p className="learn-concept" key={index}>{paragraph}</p>
              ))}
              <Shot id={section.screenshot} />
              {section.blocks.map((block) => <Block block={block} key={block.heading} />)}
              <GoThere goTo={section.goTo} label={section.goToLabel} onNavigate={onNavigate} />
            </div>
          </details>
        ))}
      </section>

      <section className="learn-glossary" aria-labelledby="learn-glossary-heading">
        <h2 id="learn-glossary-heading">Words you will see</h2>
        <dl className="learn-defs">
          {LEARN_GLOSSARY.map((entry) => (
            <div key={entry.id}><dt>{entry.term}</dt><dd>{entry.plain}</dd></div>
          ))}
        </dl>
      </section>

      <section className="learn-elsewhere" aria-labelledby="learn-elsewhere-heading">
        <h2 id="learn-elsewhere-heading">Everything else</h2>
        <ul>
          {LEARN_ELSEWHERE.map((entry) => (
            <li key={entry.goTo}>
              <button type="button" onClick={() => onNavigate?.(entry.goTo)}>{entry.goToLabel}</button>
              <span>{entry.oneLiner}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

// Static content with one prop. It must never re-render on a dashboard poll.
export default memo(LearningCenter);
```

- [ ] **Step 2: Verify every lucide icon used actually exists**

Run from `frontend/`:

```bash
for icon in bell chart-candlestick database radar scan-search activity; do
  test -f "node_modules/lucide-react/dist/esm/icons/$icon.js" && echo "OK $icon" || echo "MISSING $icon";
done
```

Expected: six `OK` lines. A `MISSING` here would be a blank page at render time with a green build.

- [ ] **Step 3: Confirm it parses**

Run: `npx vite build` from `frontend/`
Expected: build succeeds. (The component is not mounted yet, so this only proves it compiles.)

- [ ] **Step 4: Commit**

```bash
git add frontend/src/LearningCenter.jsx
git commit -m "feat(learn): render the Learn page from content data"
```

---

### Task 6: Styles

**Files:**
- Modify: `frontend/src/index.css` — **append at the very end of the file, after every existing rule.**

- [ ] **Step 1: Confirm you are appending, not inserting**

Run from `frontend/`: `wc -l src/index.css` and note the number. Your additions must start on the line after it.

- [ ] **Step 2: Append the styles**

Add at the end of `frontend/src/index.css`:

```css
/* ---------------------------------------------------------------------------
   Learn page.
   APPENDED AT THE END OF THIS FILE ON PURPOSE. A max-width:760px block earlier
   in this file is silently outranked by later unconditional rules, so anything
   placed mid-file loses on a phone. Keep new blocks below this one.
   Single scroller: this page must never introduce a nested scrolling region.
   --------------------------------------------------------------------------- */
.learn-page {
  max-width: 980px;
  margin: 0 auto;
  padding: 20px 18px 80px;
  color: #d6d6dd;
  line-height: 1.6;
}
.learn-page h1 { font-size: 26px; margin: 0 0 10px; color: #fff; }
.learn-page h2 { font-size: 18px; margin: 34px 0 14px; color: #fff; letter-spacing: .04em; text-transform: uppercase; }
.learn-page h3 { font-size: 17px; margin: 0 0 8px; color: #fff; }
.learn-page h4 { font-size: 14px; margin: 18px 0 6px; color: #8ee9ff; letter-spacing: .03em; }
.learn-page p { margin: 0 0 10px; }
.learn-hero { border-bottom: 1px solid #23232c; padding-bottom: 18px; }
.learn-hero-note { color: #9a9aa6; font-size: 14px; }

.learn-step { display: grid; grid-template-columns: 34px 1fr; gap: 14px; margin: 0 0 26px; }
.learn-step-number {
  width: 30px; height: 30px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #12303a; color: #00d7ff; font-weight: 700; font-size: 15px;
}
.learn-goto {
  margin-top: 6px; padding: 7px 14px; border-radius: 7px; cursor: pointer;
  background: #12303a; color: #8ee9ff; border: 1px solid #1d5566; font-size: 13px;
}
.learn-goto:hover { background: #17414f; }

.learn-section { border: 1px solid #23232c; border-radius: 9px; margin: 0 0 10px; background: #0d0d12; }
.learn-section > summary {
  cursor: pointer; padding: 13px 15px; display: flex; align-items: center; gap: 10px;
  font-size: 15px; color: #fff; list-style: none;
}
.learn-section > summary::-webkit-details-marker { display: none; }
.learn-section[open] > summary { border-bottom: 1px solid #23232c; }
.learn-section-body { padding: 4px 15px 16px; }
.learn-concept { color: #b9b9c4; }

.learn-defs > div { display: grid; grid-template-columns: 190px 1fr; gap: 12px; padding: 7px 0; border-bottom: 1px solid #191920; }
.learn-defs dt { color: #f6bf4a; font-size: 13px; }
.learn-defs dd { margin: 0; font-size: 14px; }

.learn-chips { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0; }
.learn-chip { display: inline-flex; align-items: center; gap: 7px; font-size: 12px; padding: 4px 10px; border-radius: 20px; background: #16161d; }
.learn-chip i { width: 11px; height: 11px; border-radius: 3px; display: inline-block; }
.learn-chip-call-wall i { background: #f23645; }
.learn-chip-put-wall i { background: #2ddf86; }
.learn-chip-bull i { background: #00ffff; }
.learn-chip-bear i { background: #ff00ff; }
.learn-chip-ema9 i { background: #d946ef; }
.learn-chip-ema21 i { background: #f6bf4a; }
.learn-chip-ema50 i { background: #22d3ee; }
.learn-chip-sma200 i { background: #15803d; }
.learn-chip-vwap i { background: #ededee; }

.learn-shot { margin: 14px 0 18px; }
.learn-shot-frame { position: relative; display: block; line-height: 0; }
.learn-shot-frame img { width: 100%; height: auto; border-radius: 8px; border: 1px solid #23232c; }
.learn-shot-marker {
  position: absolute; transform: translate(-50%, -50%);
  width: 22px; height: 22px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  background: #00d7ff; color: #04121a; font-size: 12px; font-weight: 700;
  box-shadow: 0 0 0 2px rgba(0, 0, 0, .55);
}
.learn-shot-legend { margin: 10px 0 4px; padding: 0; list-style: none; }
.learn-shot-legend li { display: grid; grid-template-columns: 22px 1fr; gap: 9px; padding: 3px 0; font-size: 13px; }
.learn-shot-legend b { color: #00d7ff; }
.learn-shot-stamp { color: #6f6f7a; font-size: 11px; }

.learn-elsewhere ul { list-style: none; padding: 0; margin: 0; }
.learn-elsewhere li { display: grid; grid-template-columns: 180px 1fr; gap: 12px; align-items: center; padding: 6px 0; border-bottom: 1px solid #191920; }
.learn-elsewhere button { background: none; border: none; color: #8ee9ff; cursor: pointer; text-align: left; padding: 0; font-size: 14px; }

@media (max-width: 760px) {
  .learn-page { padding: 14px 13px 90px; }
  .learn-page h1 { font-size: 22px; }
  .learn-step { grid-template-columns: 28px 1fr; gap: 10px; }
  .learn-defs > div,
  .learn-elsewhere li { grid-template-columns: 1fr; gap: 2px; }
  .learn-shot-marker { width: 19px; height: 19px; font-size: 11px; }
}
```

- [ ] **Step 3: Verify the build still compiles**

Run: `npx vite build` from `frontend/`
Expected: success.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/index.css
git commit -m "style(learn): Learn page styles, appended below every existing rule"
```

---

### Task 7: Wire the page into the app

**Files:**
- Modify: `frontend/src/App.jsx` (five edits)
- Modify: `frontend/src/mobileOverflowNavigation.test.js` (fixture must learn about the new page)

**Interfaces:**
- Consumes: `LearningCenter` (Task 5), `shouldAutoOpenLearn` / `readLearnSeen` / `markLearnSeen` (Task 1).

**Do not use Python to edit `App.jsx`.** It rewrites the file as CRLF and breaks every source-lifting test.

- [ ] **Step 1: Update the overflow-navigation test fixture first, and watch it fail**

`mobileOverflowNavigation.test.js` hardcodes a copy of the nav list with an exact `deepEqual`. Adding Learn to `App.jsx` would NOT break it — which is worse, because the fixture would quietly stop describing reality. Update it now.

In `frontend/src/mobileOverflowNavigation.test.js`, add to the `visibleNavItems` array immediately after `{ label: "MomX Scanner" }`:

```js
  { label: "Learn" },
```

and add `"Learn"` as the **first** entry of the expected array in the test named `the sheet offers exactly what the bar does not name`:

```js
  assert.deepEqual(labels, [
    "Learn",
    "Watchlist",
    "News Feed",
    "Earnings Calendar",
    "Mag7 Scanner",
    "ROI Calc",
    "OI Level Script TOS",
    "Settings",
  ]);
```

Run: `node --test src/mobileOverflowNavigation.test.js`
Expected: PASS — the fixture is self-contained, so this passes before `App.jsx` changes. It now describes the nav list you are about to create.

- [ ] **Step 2: Add the two icon imports**

In `frontend/src/App.jsx`, the lucide import block is lines 2–50, one name per line. Add `GraduationCap` and `HelpCircle` between `ExternalLink,` (line 20) and `KeyRound,` (line 21):

```js
  ExternalLink,
  GraduationCap,
  HelpCircle,
  KeyRound,
```

- [ ] **Step 3: Add the nav entry**

In `optionNavItems` (≈ line 1091), insert immediately after the `MomX Scanner` entry and before the `// Secondary destinations` comment:

```js
  // Learn: the new-user page. Deliberately absent from BOTTOM_NAV_LABELS, so it
  // lands in the phone "More" sheet - and sits first there, where a new user
  // looking for help will actually find it.
  { label: "Learn", icon: GraduationCap },
```

- [ ] **Step 4: Add the view description**

In `viewDescription` (≈ line 1113), add alongside the others:

```js
  if (view === "Learn") return "How AGX works: charts, indicators, options, alerts, and the two scanners";
```

- [ ] **Step 5: Import the component**

Next to the other local component imports (near line 54, `import MomxScannerPanel from "./MomxScannerPanel.jsx";`):

```js
import LearningCenter from "./LearningCenter.jsx";
import { LEARN_CONTENT_VERSION, markLearnSeen, readLearnSeen, shouldAutoOpenLearn } from "./learnFirstVisit.js";
```

- [ ] **Step 6: Add the header button**

In the header strip, immediately BEFORE the existing Settings gear (≈ line 27734):

```jsx
              <button className="header-icon-button" onClick={() => setActiveView("Learn")} title="Learn how to use AGX" aria-label="Learn how to use AGX" type="button"><HelpCircle size={18} /></button>
```

- [ ] **Step 7: Add the render block**

Alongside the other view blocks — put it immediately after the `MomX Scanner` block (≈ line 29390):

```jsx
        {activeView === "Learn" && (
          <section className="learn-view" data-testid="learn-view">
            <LearningCenter onNavigate={setActiveView} />
          </section>
        )}
```

- [ ] **Step 8: Add the first-visit effect**

Next to the other mount effects in the same component that owns `activeView` (declared ≈ line 24539), add:

```jsx
  // Show a brand-new browser the Learn page, once. Every condition in
  // shouldAutoOpenLearn is load-bearing - a pop-out chart window must never
  // become a help page, and a browser that already remembers a page is
  // mid-workflow and must not be yanked away from it.
  useEffect(() => {
    let savedView = "";
    try {
      savedView = window.localStorage.getItem(ACTIVE_VIEW_STORAGE_KEY) || "";
    } catch {
      savedView = "";
    }
    const storage = (() => { try { return window.localStorage; } catch { return null; } })();
    if (!shouldAutoOpenLearn({
      seenVersion: readLearnSeen(storage),
      popoutMode: popoutConfig.mode,
      savedView,
    })) return;
    markLearnSeen(storage, LEARN_CONTENT_VERSION);
    setActiveView("Learn");
    // Mount only. This must never re-fire on a view change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
```

- [ ] **Step 9: Confirm Learn was NOT added to the heavy payload set**

Run from `frontend/`: `grep -n "heavyDashboardViews" -A 10 src/App.jsx`
Expected: the Set does **not** contain `"Learn"`. That Set is the only trigger for the non-compact dashboard payload; adding Learn would cost a heavy fetch on every visit for nothing.

- [ ] **Step 10: Run the full test suite**

Run from `frontend/`: `node --test src/*.test.js`
Expected: all pass, including the Learn tests and the updated overflow-navigation test.

- [ ] **Step 11: Verify the line endings did not flip**

Run: `git diff --stat src/App.jsx`
Expected: a handful of changed lines, NOT the whole file. A whole-file diff means the line endings flipped and the source-lifting tests are about to break — undo and redo the edit without Python.

- [ ] **Step 12: Build and smoke-test**

Run: `npx vite build`, then open `http://127.0.0.1:5173`, click the `?` in the header, and confirm the Learn page renders with all seven steps, six collapsed sections, the glossary, and the page map. Click three different *Take me there* buttons and confirm each lands on the right page.

- [ ] **Step 13: Commit**

```bash
git add frontend/src/App.jsx frontend/src/mobileOverflowNavigation.test.js
git commit -m "feat(learn): add the Learn page to the app, the More sheet, and the header"
```

---

### Task 8: The screenshot capture script

**Files:**
- Create: `scripts/capture-learn-screenshots.mjs`
- Modify: `frontend/package.json` (devDependency + script)

**Interfaces:**
- Consumes: nothing at runtime; reads its own target list.
- Produces: PNG files in `frontend/public/learn/`, and a printed table of `id → dimensions → size`.

**Verified on 2026-08-29:** Playwright 1.62.1 with Chromium installed drives `http://127.0.0.1:5173`, auto-signs in on loopback, and renders the full workstation with zero page errors. It is currently only resolvable from an npx cache directory, which is not a reproducible build input — hence the pinned devDependency.

- [ ] **Step 1: Add the dependency and the script entry**

In `frontend/package.json`, add to `devDependencies` (keep alphabetical order):

```json
    "playwright": "1.62.1",
```

and to `scripts`:

```json
    "learn:shots": "node ../scripts/capture-learn-screenshots.mjs",
```

Run from `frontend/`: `npm install`
Expected: playwright installs. If it also needs a browser, run `npx playwright install chromium`.

- [ ] **Step 2: Create the capture script**

Create `scripts/capture-learn-screenshots.mjs`:

```js
// Re-capture every Learn page screenshot: `npm run learn:shots` from frontend/.
//
// This script existing is what makes screenshots defensible. The objection to
// screenshots is that they go stale silently; the answer is that refreshing
// them is one command rather than a manual chore nobody does.
//
// It NEVER touches the trader's browser state - Playwright runs an isolated
// context and seeds its own localStorage.

import { mkdirSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { chromium } from "playwright";

const BASE = process.env.LEARN_SHOTS_URL || "http://127.0.0.1:5173";
const OUT = fileURLToPath(new URL("../frontend/public/learn/", import.meta.url));
// Measured, not guessed. The chart-plus-chain board: 594KB at DPR 2, 411KB at
// DPR 1.5, 208KB at DPR 1 - and DPR 1 was checked by eye and reads cleanly,
// because the page caps at 980px wide so a 1357px capture is already being
// downscaled. JPEG is rejected: it comes out LARGER here and softens the axis
// text. Raise `scale` on one shot if it looks soft; do not move the default.
const MAX_BYTES = 250 * 1024;
const SCALE = 1;

// Seeding the view is fine. Seeding the LAYOUT is not: oiFinderChartWorkspace
// takes precedence when present and an initialLayoutId prop overrides both, so
// a seeded context still renders 6-across (verified 2026-08-29). The layout is
// switched by driving the UI instead - see selectSingleChart below.
const CLEAN_STATE = {
  agxLearnSeen: "1",
};

// Hide anything identifying before a pixel is captured.
const PRIVACY_CSS = `
  .signed-in-user, .avatar, .account-value, .portfolio-value { visibility: hidden !important; }
`;

// Switch to the Single layout by clicking, exactly as a user would. The app
// opens 6-across, which is illegible as a teaching image - the trader's
// instruction is one chart and the option chain, never the parallel view.
async function selectSingleChart(page) {
  await page.locator('summary[aria-label="Choose chart layout"]').first().click();
  await page.waitForTimeout(600);
  await page.locator('.oi-chart-layout-menu button[aria-label="Single"]').first().click();
  await page.waitForTimeout(9000);
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1200);
}

// Selectors: the two chart ones are CONFIRMED rendering (2026-08-29). The rest
// are read from source and unverified - if one is wrong the script fails loudly,
// which is the intended outcome. Never loosen a selector to "body" to make it
// pass; find the real class in App.jsx.
const SHOTS = [
  { id: "chart-workstation", view: "Charts & OI", selector: ".charts-oi-page-full", settle: 10000, prepare: selectSingleChart },
  { id: "chart-oi-walls", view: "Charts & OI", selector: ".charts-oi-full-chart", settle: 10000, prepare: selectSingleChart },
  { id: "high-oi-board", view: "Charts & OI", selector: ".charts-oi-tos-chain", settle: 10000, prepare: selectSingleChart },
  { id: "indicators-panel", view: "Charts & OI", selector: ".oi-finder-indicator-catalog", settle: 10000,
    prepare: async (page) => {
      await selectSingleChart(page);
      await page.locator('summary[aria-label="Add or remove chart indicators"]').first().click();
      await page.waitForTimeout(1500);
    } },
  { id: "auto-alert-panel", view: "Auto Alert", selector: ".charts-oi-auto-alerts", settle: 8000 },
  { id: "momx-board", view: "MomX Scanner", selector: ".momx-scanner-view", settle: 14000 },
  { id: "premarket-scanner", view: "Premarket Scanner", selector: ".scanner-overview-grid", settle: 9000 },
  { id: "phone-more-sheet", view: "Charts & OI", selector: "body", settle: 7000, viewport: { width: 375, height: 812 } },
];

const only = process.argv.slice(2).filter((a) => !a.startsWith("-"));
const targets = only.length ? SHOTS.filter((s) => only.includes(s.id)) : SHOTS;
if (!targets.length) {
  console.error(`No matching shot ids. Known: ${SHOTS.map((s) => s.id).join(", ")}`);
  process.exit(1);
}

mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch();
const results = [];
let failures = 0;

for (const shot of targets) {
  const context = await browser.newContext({
    viewport: shot.viewport || { width: 1440, height: 900 },
    deviceScaleFactor: shot.scale || SCALE,
  });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error).slice(0, 200)));

  try {
    await page.addInitScript((state) => {
      for (const [key, value] of Object.entries(state)) {
        try { window.localStorage.setItem(key, value); } catch { /* blocked storage */ }
      }
    }, { ...CLEAN_STATE, agenticActiveView: shot.view });

    await page.goto(BASE, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.addStyleTag({ content: PRIVACY_CSS });
    await page.waitForTimeout(shot.settle);
    if (shot.prepare) await shot.prepare(page);

    const target = page.locator(shot.selector).first();
    await target.waitFor({ state: "visible", timeout: 20000 });
    const file = path.join(OUT, `${shot.id}.png`);
    await target.screenshot({ path: file });

    const bytes = statSync(file).size;
    const box = await target.boundingBox();
    results.push({ id: shot.id, w: Math.round(box?.width || 0), h: Math.round(box?.height || 0), bytes });
    if (bytes > MAX_BYTES) {
      console.error(`  ! ${shot.id} is ${(bytes / 1024).toFixed(0)}KB, over the ${MAX_BYTES / 1024}KB budget - set a lower "scale" on this shot, or crop it tighter`);
      failures += 1;
    }
    if (pageErrors.length) {
      console.error(`  ! ${shot.id} had page errors: ${pageErrors.slice(0, 2).join(" | ")}`);
      failures += 1;
    }
  } catch (error) {
    console.error(`  ! ${shot.id} FAILED: ${String(error).split("\n")[0]}`);
    failures += 1;
  } finally {
    await context.close();
  }
}

await browser.close();

console.log("\n id                      size        bytes");
console.log(" ----------------------- ----------- --------");
results.forEach((r) => {
  console.log(` ${r.id.padEnd(23)} ${`${r.w}x${r.h}`.padEnd(11)} ${(r.bytes / 1024).toFixed(0)}KB`);
});
if (failures) {
  console.error(`\n${failures} problem(s). Fix before committing images.`);
  process.exit(1);
}
console.log("\nNow LOOK at each PNG before committing it.");
```

- [ ] **Step 3: Confirm the dev server is up**

Run: `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:5173/`
Expected: `200`. If not, start it with `npm run dev` from `frontend/` first.

- [ ] **Step 4: Dry-run a single shot**

Run from `frontend/`: `npm run learn:shots -- chart-workstation`
Expected: a PNG appears in `frontend/public/learn/` and the size table prints. If the selector was wrong the script fails loudly — find the real class name in `App.jsx` and fix the `SHOTS` entry rather than loosening the selector to `body`.

**Each shot takes roughly 25 seconds** (the chart needs about ten to settle before it is worth photographing), so a full run of eight is three to four minutes. That is not a hang. If you run this through a tool with a default timeout, raise it past five minutes.

Confirm the result shows **one chart beside the option chain**, not six charts in parallel. If it shows six, `selectSingleChart` did not take — check that the layout picker's `aria-label` is still `Choose chart layout` and the Single button's is still `Single`.

- [ ] **Step 5: Delete the dry-run image**

```bash
rm -f frontend/public/learn/chart-workstation.png
```

This is required, not tidiness. `learnScreenshots.test.js` asserts that every id still listed in `PENDING_CAPTURE` has **no file on disk** — that is what stops "pending" becoming a permanent excuse. Leaving the dry-run PNG behind while the manifest still lists it as pending makes the suite fail. Task 9 captures for real and updates the manifest in the same task.

- [ ] **Step 6: Confirm the suite is green before committing**

Run from `frontend/`: `node --test src/*.test.js`
Expected: all pass. If `learnScreenshots.test.js` fails, a dry-run image is still on disk.

- [ ] **Step 7: Commit the script only, not images yet**

```bash
git add scripts/capture-learn-screenshots.mjs frontend/package.json frontend/package-lock.json
git commit -m "feat(learn): one command to re-capture every Learn screenshot"
```

---

### Task 9: Capture, review, and wire up the images

**Files:**
- Create: `frontend/public/learn/*.png`
- Modify: `frontend/src/learnScreenshots.js` (real dimensions, `capturedOn`, callouts, shrink `PENDING_CAPTURE`)

- [ ] **Step 1: Capture everything capturable**

Run from `frontend/`: `npm run learn:shots`

`premarket-scanner` will produce an empty or warming board outside 06:00–09:30 ET on a trading day, and `momx-board` reads best during regular hours. If a shot is not meaningful, **delete that PNG** and leave the id in `PENDING_CAPTURE`.

- [ ] **Step 2: Look at every image**

Open each PNG. Confirm, for each:
- No signed-in name, email, avatar, or account value is visible.
- The thing the section is teaching is actually the subject of the frame.
- Text is legible when the image is 980px wide (the page's max width) and at 375px.

Delete and re-capture anything that fails. Do not hand-edit an image.

- [ ] **Step 3: Fill in the manifest**

For each captured image, update its entry in `frontend/src/learnScreenshots.js`:
- `width` / `height` from the script's printed table.
- `capturedOn` to today's date in `YYYY-MM-DD`.
- `callouts` — between one and six markers, numbered from 1, with `x`/`y` as percentages of the image. Read the coordinates off the image itself.
- Remove that id from `PENDING_CAPTURE`.

Example, once `chart-workstation.png` exists:

```js
  { id: "chart-workstation", file: "chart-workstation.png", width: 1200, height: 760,
    capturedOn: "2026-08-31",
    alt: "A single five-minute chart with candles, three moving averages and VWAP drawn over them.",
    callouts: [
      { n: 1, x: 12, y: 8, text: "The ticker and timeframe. Change either here." },
      { n: 2, x: 50, y: 45, text: "The three moving averages: 9 magenta, 21 yellow, 50 cyan." },
      { n: 3, x: 78, y: 62, text: "VWAP, in white. Price above it usually means buyers are in control." },
      { n: 4, x: 90, y: 30, text: "The live price, on the right-hand axis." },
    ] },
```

- [ ] **Step 4: Run the manifest test**

Run: `node --test src/learnScreenshots.test.js`
Expected: PASS. It will fail if you left an id in `PENDING_CAPTURE` whose file exists, or removed one whose file does not — that is the test doing its job.

- [ ] **Step 5: Verify in the browser**

Reload `http://127.0.0.1:5173`, open Learn, and confirm the images render with their numbered markers landing on the right things, at both desktop width and 375px. Confirm any still-pending section renders as text with no broken-image icon.

- [ ] **Step 6: Commit**

```bash
git add frontend/public/learn frontend/src/learnScreenshots.js
git commit -m "feat(learn): capture the Learn page screenshots and place their callouts"
```

- [ ] **Step 7: Note what is still outstanding**

If `premarket-scanner` or `momx-board` are still pending, say so explicitly in the handoff, with the instruction: re-run `npm run learn:shots -- premarket-scanner` on a trading morning between 06:00 and 09:30 ET, then repeat Steps 2–6 for it.

---

### Task 10: Full verification

No new files. This task is the `AGENTS.md` checklist applied to this feature.

- [ ] **Step 1: Full frontend suite**

Run from `frontend/`: `node --test src/*.test.js`
Expected: all pass. Record the count.

- [ ] **Step 2: Production build**

Run: `npx vite build`
Expected: success, no warnings about missing modules. A green build does NOT prove the page renders — a missing icon import compiles fine and throws at render.

- [ ] **Step 3: Desktop browser check**

At `http://127.0.0.1:5173`:
- The `?` button in the header opens Learn.
- All seven steps render, in order, numbered 1–7.
- All six sections expand and collapse.
- Every *Take me there* button lands on the right page.
- The console is clean — no errors, no warnings from this page.

- [ ] **Step 4: Phone-width check at 375×812**

- Learn appears in the "More" sheet, first in the list.
- **Settings is still reachable in that sheet.** If it is not, a phone user cannot re-authorise, and this is a blocking regression.
- The page has ONE scroller. Nothing scrolls inside anything else. Four scroll incidents in a single day on this app came from nested mixed-axis scrollers.
- Screenshots and their callout markers are readable.

- [ ] **Step 5: First-visit behaviour**

In a fresh browser profile (or after clearing `agxLearnSeen` and `agenticActiveView`), load the app: Learn opens by itself. Reload: it does not. Set `agenticActiveView` to `MomX Scanner`, clear `agxLearnSeen`, reload: it stays on MomX Scanner.

- [ ] **Step 6: Confirm nothing else regressed**

Open Charts & OI and confirm charts still load, tick, and switch timeframes; open MomX Scanner and confirm the board still populates. The only shared surfaces touched are the nav list and the header strip, but confirm rather than assume.

- [ ] **Step 7: Report**

State plainly: which tests ran and their count, whether the build passed, what was checked in the browser and at what widths, and **which screenshots are still pending and why**. If anything was not verified, say so rather than implying it was.

---

## Notes for whoever executes this

- Read the spec first. Its "Honesty constraints on the copy" section is binding, and the tests in Tasks 2 and 3 enforce part of it — but only part. Judgement covers the rest.
- The content is the bulk of the work, not the code. Tasks 1, 4, 5, 6, 7 are mechanical. Task 3 is where the real effort goes; write it as if explaining to a competent trader who has never seen this app.
- Never claim a screenshot is captured until you have looked at it.
