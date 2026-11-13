# Clean Look, Release 1 (Foundation) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the dark/light switch, the shared colour and font list, and the restyled left sidebar and top strip, with every other screen looking exactly as it does today.

**Architecture:** A `data-theme` attribute on `<html>`, set before first paint by an inline script and afterwards by a small helper module. All new styling lives in a new `theme.css` and applies only inside elements carrying the class `agx-skin`; nothing outside that class changes in either theme.

**Tech Stack:** React 18 + Vite, plain CSS custom properties, `node --test` for unit/integration, Playwright via `tests/e2e/harness.mjs` for end to end.

**Spec:** `docs/superpowers/specs/2026-11-12-clean-look-theme-design.md` (read it first; this plan covers its release 1 only. Releases 2 to 6 each get their own plan after Ganesh has looked at the one before.)

## Global Constraints

- Repo: `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2`. Other sessions share its index: commit with `git commit -- <paths>` naming only this plan's files. Never `git add -A`.
- One commit for the whole release, at the end (Task 5). The pre-commit hook refuses app-code commits without a new top entry in `frontend/public/release-notes.json`. Do not use `--no-verify`.
- Between 09:30 and 16:00 ET on weekdays run only this plan's focused test files; end-to-end specs skip themselves then. Full suites outside those hours only.
- `frontend/src/App.jsx` must stay LF. After any edit run `git diff --stat -- frontend/src/App.jsx`; if the whole file shows as changed, line endings flipped: restore them.
- Do not restart the backend, the scanner worker or the Vite dev server on :5173. Do not run a Vite dev server from a worktree.
- Outside `.agx-skin` nothing may change: not `:root` colour, not `:root` font, not `--terminal-*` values.
- No JavaScript chart colour changes (`createChart` options, candle colours) in this release.
- Storage key `agx.theme`; values `"dark"` and `"light"`; default `"dark"`; never follows the OS setting.
- Colours, exactly (dark / light): bg `#000000` / `#f5f5f7`; card `#1c1c1e` / `#ffffff`; raised `#2c2c2e` / `#ececf0`; line `#38383a` / `#d9d9de`; line-soft `#2a2a2c` / `#ececf0`; text `#f5f5f7` / `#1d1d1f`; text-2 `#a1a1a6` / `#636366`; accent `#0071e3` / `#0071e3`; up `#30d158` / `#1b7f37`; down `#ff453a` / `#d70015`; warn `#ffd60a` / `#8a5a00`.
- Font stack, exactly: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable", "Segoe UI", system-ui, sans-serif`, always with `font-variant-numeric: tabular-nums`.
- Ordinary text inside `.agx-skin` meets 4.5:1 contrast in both themes.
- The light option is labelled `Light (preview)` until release 6.

## Review Focus

1. **Storage blocked or throwing** (private mode, cleared site data): the app opens dark, the switch still changes the theme for that visit, nothing throws. Pinned in Task 1 and Task 2.
2. **A stored value that is not exactly `dark` or `light`** (`"LIGHT"`, `""`, `"blue"`, `null`): treated as dark. Pinned in Task 1 and Task 2.
3. **Light mode on a screen not yet converted:** its text colour and font must be identical to dark mode (no dark text inherited onto a dark panel). Pinned in Task 4.
4. **Sidebar collapsed, and phone width where the sidebar is hidden:** the switch is still reachable and usable. Pinned in Task 3.
5. **A second window** (a chart pop-out, or the installed app alongside a browser tab): it opens in the stored theme, and follows a change made in the other window. Pinned in Task 1 and Task 3.

---

## File Structure

| File | Responsibility |
|---|---|
| Create `frontend/src/theme.js` | Read, validate, store, apply and subscribe to the theme. No React, no DOM globals at import time. |
| Create `frontend/src/theme.test.js` | Unit tests for `theme.js`. |
| Modify `frontend/index.html` | Inline pre-mount script that sets `data-theme` and the `theme-color` meta. |
| Create `frontend/src/themeBoot.integration.test.js` | Runs the real inline script lifted from `index.html` against stub `document`/`localStorage`; checks it agrees with `theme.js`. |
| Create `frontend/src/theme.css` | `--agx-*` variables for both themes; `.agx-skin` base; sidebar, top strip and switch styles. |
| Create `frontend/src/ThemeSwitch.jsx` | The Dark / Light control. |
| Modify `frontend/src/main.jsx` | `import "./theme.css";` on the line after `import "./index.css";`. |
| Modify `frontend/src/App.jsx` | Add `agx-skin` to `aside.sidebar` (near line 29625) and `header.topbar.trading-header` (near line 29704); mount `<ThemeSwitch />`. |
| Create `tests/e2e/theme-switch.e2e.test.mjs` | End-to-end spec. |
| Modify `frontend/public/release-notes.json`, `tests/FEATURE_TESTS.md` | Release note; feature-test row. |

This feature has no server route. The integration layer is therefore the real `index.html` boot script exercised together with `theme.js`, not an HTTP test; say so in the handoff.

---

### Task 1: Theme helper

**Files:**
- Create: `frontend/src/theme.js`
- Test: `frontend/src/theme.test.js`

**Interfaces:**
- Produces:
  - `THEME_KEY = "agx.theme"`
  - `THEME_COLORS = { dark: "#000000", light: "#f5f5f7" }`
  - `normalizeTheme(value: unknown): "dark" | "light"`: `"light"` only for the exact string `"light"`.
  - `readStoredTheme(storage?: Storage): "dark" | "light"`: never throws.
  - `applyTheme(theme, doc?: Document): "dark" | "light"`: sets `data-theme` on `doc.documentElement` and `content` on `meta[name="theme-color"]` if present; returns the normalized theme.
  - `setTheme(theme, { storage?, doc? }?): "dark" | "light"`: applies, then stores; a throwing `storage.setItem` is swallowed and the theme is still applied.
  - `subscribeTheme(listener: (theme) => void, win?: Window): () => void`: calls `listener` with the normalized new value on a `storage` event whose `key` is `THEME_KEY`; returns an unsubscribe function.
  - Defaults for `storage`, `doc`, `win` resolve inside each function from `globalThis`, and each function tolerates them being undefined.

- [ ] **Step 1: Write the failing tests** in `frontend/src/theme.test.js` (`node:test`, `node:assert/strict`), using plain-object stubs:

```js
const memory = (seed = {}) => { const m = { ...seed }; return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, m }; };
const throwing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
const fakeDoc = () => { const attrs = {}; const meta = { content: "#0c0c0d", setAttribute(k, v) { this[k] = v; } };
  return { documentElement: { setAttribute: (k, v) => { attrs[k] = v; } }, querySelector: (s) => (s === 'meta[name="theme-color"]' ? meta : null), attrs, meta }; };
```

Tests and assertions:
  - `normalizeTheme`: `"light"` → `"light"`; each of `"dark"`, `"LIGHT"`, `""`, `"blue"`, `null`, `undefined`, `0`, `{}` → `"dark"`.
  - `readStoredTheme(memory({ "agx.theme": "light" }))` → `"light"`; `memory()` → `"dark"`; `throwing` → `"dark"`; `undefined` storage → `"dark"`.
  - `applyTheme("light", doc)` sets `doc.attrs["data-theme"] === "light"` and `doc.meta.content === "#f5f5f7"`; `applyTheme("blue", doc)` returns `"dark"`, meta `"#000000"`; a doc whose `querySelector` returns `null` does not throw.
  - `setTheme("light", { storage: s, doc })` → `s.m["agx.theme"] === "light"` and attribute set; with `throwing` storage it returns `"light"`, does not throw, attribute still set.
  - `subscribeTheme`: with a stub `win` that records `addEventListener`/`removeEventListener`, firing `{ key: "agx.theme", newValue: "light" }` calls the listener with `"light"`; `{ key: "other" }` does not call it; `{ key: "agx.theme", newValue: null }` calls it with `"dark"`; after unsubscribe the handler is removed.

- [ ] **Step 2: Run to verify failure**

Run: `node --test frontend/src/theme.test.js`
Expected: FAIL, cannot find module `./theme.js`.

- [ ] **Step 3: Implement `frontend/src/theme.js`** with the exports above. ES module, no imports.

- [ ] **Step 4: Run to verify pass**

Run: `node --test frontend/src/theme.test.js`
Expected: all pass.

---

### Task 2: Set the theme before the app mounts

**Files:**
- Modify: `frontend/index.html` (directly after the `<meta name="theme-color" content="#0c0c0d" />` line)
- Test: `frontend/src/themeBoot.integration.test.js`

**Interfaces:**
- Consumes: `THEME_KEY`, `THEME_COLORS`, `normalizeTheme` from Task 1 (the test compares against them).
- Produces: `<html data-theme="dark|light">` present before any stylesheet rule is evaluated; the script tag carries `data-agx-theme-boot`.

- [ ] **Step 1: Check for a Content-Security-Policy.** Run: `grep -rn "Content-Security-Policy" gateway.py api_server.py frontend/index.html deployment/`. If any policy without `'unsafe-inline'` for scripts is served, do not inline: put the same code in `frontend/public/theme-boot.js` and load it with a plain (not `defer`, not `module`) `<script data-agx-theme-boot src="/theme-boot.js"></script>`, and have the test read that file instead. Otherwise inline as in Step 3.

- [ ] **Step 2: Write the failing test.** It reads `frontend/index.html`, extracts the body of the `<script data-agx-theme-boot>` element (or the file from Step 1), and runs it with `new Function("localStorage", "document", code)` against stubs. Assertions, one test each:
  - stored `"light"` → `data-theme` is `"light"`, theme-color content is `THEME_COLORS.light`.
  - nothing stored → `"dark"`, `THEME_COLORS.dark`.
  - for each of `"LIGHT"`, `""`, `"blue"`: result equals `normalizeTheme(value)` (that is, `"dark"`).
  - `localStorage.getItem` throws → `"dark"`, no throw.
  - no theme-color meta (`querySelector` returns `null`) → attribute still set, no throw.
  - the script text contains the literal `agx.theme` (guards against the key drifting from `THEME_KEY`).
  - the script element appears in the file before the first `<script type="module"`.

- [ ] **Step 3: Run to verify failure**

Run: `node --test frontend/src/themeBoot.integration.test.js`
Expected: FAIL, boot script not found.

- [ ] **Step 4: Add the script** to `frontend/index.html`, with a one-line HTML comment above it saying it must stay ahead of the app bundle to avoid a flash of the wrong theme:

```html
<script data-agx-theme-boot>(function(){var t="dark";try{if(localStorage.getItem("agx.theme")==="light")t="light";}catch(e){}document.documentElement.setAttribute("data-theme",t);var m=document.querySelector('meta[name="theme-color"]');if(m)m.setAttribute("content",t==="light"?"#f5f5f7":"#000000");})();</script>
```

- [ ] **Step 5: Run to verify pass**

Run: `node --test frontend/src/themeBoot.integration.test.js`
Expected: all pass.

---

### Task 3: Colour list and the switch

**Files:**
- Create: `frontend/src/theme.css`, `frontend/src/ThemeSwitch.jsx`
- Modify: `frontend/src/main.jsx`, `frontend/src/App.jsx`
- Test: `tests/e2e/theme-switch.e2e.test.mjs`

**Interfaces:**
- Consumes: `readStoredTheme`, `setTheme`, `subscribeTheme`, `applyTheme` from Task 1.
- Produces:
  - CSS variables `--agx-bg`, `--agx-card`, `--agx-raised`, `--agx-line`, `--agx-line-soft`, `--agx-text`, `--agx-text-2`, `--agx-accent`, `--agx-up`, `--agx-down`, `--agx-warn`, `--agx-font`, defined on `:root` (dark values) and overridden under `:root[data-theme="light"]`. Defining variables on `:root` is allowed; setting `color`, `background` or `font-family` on `:root` or `body` is not.
  - `.agx-skin { font-family: var(--agx-font); font-variant-numeric: tabular-nums; color: var(--agx-text); }`
  - `<ThemeSwitch compact={boolean} />`, default export. Markup: a wrapper `div.agx-theme-switch` with `data-testid="theme-switch"`, `role="group"`, `aria-label="Appearance"`, containing two `<button type="button">` with `data-testid="theme-dark"` / `data-testid="theme-light"`, `aria-pressed`, visible text `Dark` and `Light (preview)`. With `compact`, the visible text is replaced by sun/moon icons from `lucide-react` (already a dependency; `Sun`, `Moon`) and each button keeps its text as `aria-label` and `title`.

- [ ] **Step 1: Write the failing end-to-end spec** `tests/e2e/theme-switch.e2e.test.mjs`, following `tests/e2e/chat-dock.e2e.test.mjs` for imports and `withApp`/`e2eTest` usage. No writes are faked or allowed (the feature makes no server calls). Tests:
  - *opens dark by default*: `withApp({ view: "MomX Scanner" })` (use the exact nav label from `visibleNavItems` in `App.jsx`); `html` has `data-theme="dark"`; `theme-dark` has `aria-pressed="true"`.
  - *switching to light persists*: click `theme-light`; `html[data-theme="light"]`; `localStorage["agx.theme"] === "light"`; `meta[name="theme-color"]` content `#f5f5f7`; reload; still light, and `data-theme` was already `light` at `DOMContentLoaded` (read it via `page.addInitScript` recording the attribute on that event).
  - *stored garbage opens dark*: `storage: { "agx.theme": "blue" }` → `data-theme="dark"`.
  - *collapsed sidebar keeps the switch*: click the button with `aria-label="Collapse navigation"`; `theme-light` is visible, has an accessible name, and clicking it switches.
  - *phone width*: `viewport: PHONE`; a `theme-switch` is visible (the sidebar's or the top strip's); tapping `theme-light` switches; `overflowsSideways(page)` is false in both themes.
  - *second window follows*: open a second page in the same context on the same URL; switch in the first; within 2 s the second has `data-theme="light"`.

- [ ] **Step 2: Run to verify failure** (outside market hours; inside them the spec skips, so then verify by reading the skip message and run it after 16:00 ET)

Run: `node --test --test-concurrency=1 tests/e2e/theme-switch.e2e.test.mjs`
Expected: FAIL, `theme-switch` not found.

- [ ] **Step 3: Create `frontend/src/theme.css`** with the variables and `.agx-skin` base from Interfaces, values from Global Constraints, and the switch styles: track `var(--agx-raised)`, radius 12, 3px inset; pressed button radius 9, background `#636366` with `#ffffff` text in dark, `#ffffff` with `box-shadow: 0 1px 3px rgba(0,0,0,.12)` and `var(--agx-text)` in light; unpressed `var(--agx-text-2)`; buttons at least 32px tall on desktop and 44px under `@media (max-width: 760px)`; a visible `:focus-visible` ring in `var(--agx-accent)`.

- [ ] **Step 4: Import it.** In `frontend/src/main.jsx` add `import "./theme.css";` on the line after `import "./index.css";`.

- [ ] **Step 5: Implement `frontend/src/ThemeSwitch.jsx`**: state initialised from `readStoredTheme()`, an effect that calls `applyTheme(state)` and subscribes with `subscribeTheme(setState)`, click handlers calling `setTheme` then `setState`.

- [ ] **Step 6: Mount it in `App.jsx`.** Inside `aside.sidebar`, directly above the element with `className="sidebar-status"` (near line 29680): `<ThemeSwitch compact={sidebarCollapsed} />`. Then load the app at 375px: if `aside.sidebar` is not visible there, also render `<ThemeSwitch compact />` as the last child of `header.topbar.trading-header`, wrapped so CSS shows it only under `@media (max-width: 760px)`; because of the known cascade trap, confirm with computed styles at 375px that it is shown and at 1440px that it is hidden.

- [ ] **Step 7: Run to verify pass**

Run: `node --test --test-concurrency=1 tests/e2e/theme-switch.e2e.test.mjs`
Expected: all pass.

---

### Task 4: Restyle the sidebar and top strip

**Files:**
- Modify: `frontend/src/App.jsx` (two `className` strings), `frontend/src/theme.css`
- Test: `tests/e2e/theme-switch.e2e.test.mjs` (add tests)

**Interfaces:**
- Consumes: the variables and `.agx-skin` from Task 3.
- Produces: `aside.sidebar.agx-skin` and `header.topbar.trading-header.agx-skin`; in light theme `.app-shell` and `body` backgrounds become `var(--agx-bg)` via rules keyed on `:root[data-theme="light"]`.

- [ ] **Step 1: Add failing tests** to the spec. A helper `contrast(page, selector)` evaluates in the page: computed `color` against the nearest ancestor's non-transparent `background-color`, returning the WCAG ratio. Tests:
  - *sidebar and top strip use the new font*: computed `font-family` of `aside.sidebar .nav-label` and of `.market-open-pill` starts with `-apple-system`; in both themes.
  - *readable in both themes*: for every visible element matching `aside.sidebar .nav-label, aside.sidebar .brand-copy b, aside.sidebar .sidebar-status b, header.topbar.trading-header .market-open-pill, header.topbar.trading-header .session-tape *` that has its own text, `contrast >= 4.5`, in dark and in light, at DESKTOP and PHONE.
  - *colours come from the list*: in light, `aside.sidebar` computed background is `rgb(255, 255, 255)` and `body` background `rgb(245, 245, 247)`; in dark, the sidebar is `rgb(28, 28, 30)` and `body` background is whatever it was before this work (record it on the previous commit; the dark page background does not change in this release).
  - *unconverted screen is untouched (Review Focus 3)*: on the Scanner view, record computed `color`, `background-color` and `font-family` of the scanner's root element and of its first table cell in dark; switch to light; all six values are identical.
  - *charts untouched*: on Charts & OI (use the helpers in `tests/e2e/charts-core.helpers.mjs`, `withCharts`/`waitForTape`), the chart container's computed background and a screenshot hash of the chart canvas region are identical in dark and light.
  - *no sideways overflow*: `overflowsSideways(page)` false at PHONE in both themes on Scanner and Charts & OI.

- [ ] **Step 2: Run to verify failure**

Run: `node --test --test-concurrency=1 tests/e2e/theme-switch.e2e.test.mjs`
Expected: the new tests FAIL (old font, old colours); Task 3's tests still pass.

- [ ] **Step 3: Add the class.** In `App.jsx` change `className="sidebar"` to `className="sidebar agx-skin"` and `className="topbar trading-header"` to `className="topbar trading-header agx-skin"`. Then run `grep -rn "className=\\\"sidebar\\\"\|className=\\\"topbar trading-header\\\"\|\\[class=\\\"sidebar\\\"\\]" tests frontend/src` and fix any test that matched the exact string.

- [ ] **Step 4: Write the skin rules in `theme.css`**, every selector prefixed with `.agx-skin` (for example `.sidebar.agx-skin .nav-item`) so they outrank the two existing blocks in `index.css` (`.sidebar` near lines 3166 and 7073, `.topbar.trading-header` near 7331, `.market-open-pill` near 7359) without `!important`. Shapes from the spec's "Component shapes":
  - Sidebar: background `var(--agx-card)`, right edge `1px solid var(--agx-line)`; brand name 17px weight 700, letter-spacing -0.02em; nav items 14px `var(--agx-text-2)`, radius 10, hover `var(--agx-raised)`; `.nav-active` background `var(--agx-raised)`, `var(--agx-text)`, weight 600; keep the existing width, collapse behaviour, unread dot and auto-alert badge colours and positions.
  - Top strip: background `var(--agx-bg)`, bottom edge `1px solid var(--agx-line)`; `.market-open-pill` radius 999, background `var(--agx-card)`, 13px; its status dot uses `var(--agx-up)` / `var(--agx-down)` / `var(--agx-warn)` for the existing `tone-*` classes; session tape and clock text `var(--agx-text-2)` with the active session in `var(--agx-text)`.
  - Page: `:root[data-theme="light"] body, :root[data-theme="light"] .app-shell { background: var(--agx-bg); }`. Dark page background stays as today in this release (changing it would alter unconverted screens).
  - Do not change any height, width, grid template or position in these rules; if a text change makes the top strip wrap at 1440px or 375px where it did not before, reduce padding until it does not.

- [ ] **Step 5: Run to verify pass**

Run: `node --test --test-concurrency=1 tests/e2e/theme-switch.e2e.test.mjs`
Expected: all pass.

- [ ] **Step 6: Line-ending check**

Run: `git diff --stat -- frontend/src/App.jsx`
Expected: a handful of changed lines, not the whole file.

---

### Task 5: Release note, verification, commit

**Files:**
- Modify: `frontend/public/release-notes.json`, `tests/FEATURE_TESTS.md`

- [ ] **Step 1: Release note.** Add at the TOP of `releases`, `at` = the current ET time in the file's existing format, heading `New clean look begins: Dark / Light switch`, items written for the trader:
  - `New: a Dark / Light switch at the bottom of the left menu (on the phone, at the end of the top strip). Dark is still what you get unless you change it. Your choice is remembered on each device.`
  - `The left menu and the top strip have the new clean look: one simpler font and calmer colours.`
  - `Light is marked "preview": the other screens and the chart stay dark for now and will be converted one at a time.`
  - `Nothing about the scanner, charts, BOT or any number has changed.`

  Adjust the first item's wording to match where Task 3 Step 6 actually put the phone switch.

- [ ] **Step 2: Feature-test row.** Add a row at the top of the table in `tests/FEATURE_TESTS.md`: feature `Clean look release 1: Dark / Light switch, colour list, sidebar and top strip (2026-11-12)`; unit `frontend/src/theme.test.js`; integration `frontend/src/themeBoot.integration.test.js` (the real index.html boot script with theme.js; the feature has no server route); end to end `tests/e2e/theme-switch.e2e.test.mjs`.

- [ ] **Step 3: Focused tests**

Run: `node --test frontend/src/theme.test.js frontend/src/themeBoot.integration.test.js`
Expected: all pass.

- [ ] **Step 4: Production build**

Run: `npm --prefix frontend run build`
Expected: build succeeds. Then confirm the built page kept the script: `grep -c "data-agx-theme-boot" frontend/dist/index.html` prints `1`.

- [ ] **Step 5: Full suites (outside 09:30-16:00 ET only)**

Run: `node --test "frontend/src/*.test.js"` then `node --test --test-concurrency=1 "tests/e2e/*.e2e.test.mjs"`
Expected: no new failures. Eleven end-to-end failures were already open on 2026-11-11 and are not regressions; compare failing names against a run on the commit before this work and report any name that is new.

- [ ] **Step 6: Look at it.** In the browser at 1440 wide and at 375x812, in both themes: Scanner, Charts & OI (single chart, a multi-chart grid, big-screen and fullscreen), BOT. Confirm the switch works, no flash of the wrong theme on reload, no clipped or wrapped top strip, no console errors, candles still ticking. Record what was and was not checked for the handoff.

- [ ] **Step 7: Commit only this work's paths**

```bash
git add frontend/src/theme.js frontend/src/theme.test.js frontend/src/themeBoot.integration.test.js frontend/src/theme.css frontend/src/ThemeSwitch.jsx tests/e2e/theme-switch.e2e.test.mjs docs/superpowers/specs/2026-11-12-clean-look-theme-design.md docs/superpowers/plans/2026-11-12-clean-look-release-1-foundation.md
git commit -m "Clean look release 1: Dark / Light switch, colour list, sidebar and top strip" -- frontend/src/theme.js frontend/src/theme.test.js frontend/src/themeBoot.integration.test.js frontend/src/theme.css frontend/src/ThemeSwitch.jsx frontend/src/main.jsx frontend/src/App.jsx frontend/index.html frontend/public/release-notes.json tests/e2e/theme-switch.e2e.test.mjs tests/FEATURE_TESTS.md docs/superpowers/specs/2026-11-12-clean-look-theme-design.md docs/superpowers/plans/2026-11-12-clean-look-release-1-foundation.md
```

If `frontend/public/theme-boot.js` was created in Task 2 Step 1, add it to both commands. End the commit message with the session's attribution line. Do not push; Ganesh decides when it goes to the public app.

- [ ] **Step 8: Handoff** names the three layers that ran, any that could not (for example end to end skipped in market hours), what was looked at in the browser, and that light mode is a preview with only the menu and top strip converted.
