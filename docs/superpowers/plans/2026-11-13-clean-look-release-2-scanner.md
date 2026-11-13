# Clean look release 2: MomX Scanner — implementation plan and record

**Goal:** the MomX Scanner (table, toolbar, chips, Filters, Columns, Setup rules, list menu, News, History, ticker card) in the clean look, dark and light, without changing what it shows or how many rows fit.

**Spec:** `docs/superpowers/specs/2026-11-12-clean-look-theme-design.md` (release order item 2; density anti-goal; signal-colour rule; contrast rule).

**Status (2026-11-13, 09:15 ET):** LIVE for everyone, DARK ONLY. Ganesh dropped the Light theme ("I don't want light"), so the light twins and the Dark / Light switch were removed and the start-up script always picks dark. Sector cards now list every ticker of the sector (his request). The rulings below that mention Light or the preview gate are history. Still open: the mock-up's layout (big title, summary cards, taller rows, phone ticker cards) is not built; this release changes font, colours, shapes and the sector cards only.

## Global constraints (from the spec)

- Colours and font only. Row height, paddings and column sets are not touched.
- Rows that fit on screen may not drop: 30 at 1920x1080 and 8 at 375x812 before (measured on the old look, ticker card closed).
- Dark: signal colours exactly as today. Light: same hue, darker where used as text; solid fills keep their fill.
- Ordinary text 4.5:1 in both themes. Server-named cell colours (RVOL / SQZ / Skittles pills) are thinkScript's and are exempt.
- Chart drawing areas stay dark in both themes (the ticker card's day chart).
- A browser without the skin must see exactly the old scanner in both themes.

## How it is built

1. **Switch** (`frontend/src/theme.js`): `activeSkins(storage)` / `applySkins()` write `data-agx-skin="scanner"` on `<html>` when the browser stored `agx.skin.scanner = "1"` (preview) or the screen is in `LIVE_SKINS`. Unit: `frontend/src/theme.test.js`.
2. **Shared names** (`frontend/src/theme.css`, section "scanner"): the scanner's twelve `--momx-*` variables are re-pointed at `--agx-*`; one font rule for the panel and everything it opens; `--agx-sig-up / -down / -amber`; a few hand rules (industry pill, count badges, faint labels, NEW tag, the page area around the view).
3. **Generated twins** (`scripts/gen_scanner_light_skin.mjs` -> `frontend/src/theme.scanner.light.css`, `theme.scanner.dark.css`, imported by theme.css): every scanner colour rule in index.css gets a twin under `:root[data-theme="light"][data-agx-skin~="scanner"]` (all colours converted) and under `:root:not([data-theme="light"])[data-agx-skin~="scanner"]` (only faint grey text lifted). Unit: `frontend/src/themeScannerSkin.test.js`, which also fails when the written files no longer match index.css.
4. **End to end** (`tests/e2e/scanner-clean-look.e2e.test.mjs`): old look untouched without the preview; with it, font, surfaces, readability of every label on the board and in each dialog, dark signal colours identical to today, rows that fit, ticker card, phone.

## Rulings

- **Skin switch is a word on `<html>`, not an `.agx-skin` class on the screen's element** (the spec's wording). The scanner draws its menus, dialogs and cards outside its panel (portals); a class on the panel would not reach them, and adding classes at six places in `MomxScannerPanel.jsx` would collide with another session's uncommitted edits there. Intent kept: opt-in per screen, nothing else changes. Cost if wrong: two switch mechanisms (strip/rail use the class) until they are unified.
- **Preview gate before going live.** The Vite dev server he trades on shows every saved edit at once, and the release was built in the two hours before the open. The new look therefore stays off unless the browser asks for it. Cost if wrong: one more step (flip to live) before he sees it.
- **Light colours are generated, not hand-typed.** ~550 colour rules; a script converts each by rule (dark neutral -> near-white, pale text -> same hue darkened to 5.6:1, white veils -> black veils) and hand rules cover what a rule cannot know. Cost if wrong: a generated shade he dislikes is changed in the script's rule or by a hand rule in theme.css, not in the generated file.
- **Twins repeat unchanged colours.** A twin outranks its source rule, so a more specific rule left untwinned would lose to the twin of a general rule (found on the purple MX A+ tag). Cost: larger files (about 80 KB + 55 KB before compression).
- **Row height and paddings unchanged** rather than "a little more air": the spec's limit is a floor, and no row-size change is the surest way to keep it. Measured after: 33 rows on the monitor (the list bar got shorter with the new font), 8 on the phone.
- **Dark surfaces keep the scanner's own dark buttons and headers** (slightly blue-black) on the shared card colour; only the twelve shared names, the font and faint grey text change in dark. Cost if wrong: dark scanner is a shade less "neutral" than the strip.
- **Server-named pill colours are not converted** (black pill with a coloured number stays black in light). They are the thinkScript colours he reads; spec: solid fills keep their fill.
- **Built before its tests.** The colour conversion was explored with a contrast-audit script and screenshots first, then pinned by unit and end-to-end tests. The unit tests found a real defect when first run (bluish near-white text classed as "coloured").

## Going live (not done)

1. Outside market hours, run the whole scanner end-to-end group with the preview forced on, and `scanner-clean-look` again.
2. Move `"scanner"` from `PREVIEW_SKINS` to `LIVE_SKINS` in `frontend/src/theme.js`; update the unit test and the "without the preview" end-to-end tests (they then assert a browser cannot switch it off).
3. Release note, production build, commit.

## Review focus

- A scanner state the fixture board never shows (bear board, premarket list, History Find results, news overlay with real headlines, Momo settings, pop-out window) in light.
- Hover / active / focus states in light (the audit reads resting states only).
- Animations: `@keyframes` colours are not converted.
- Colours set in JS (inline styles, the industry pill colour, sparklines).
- Another session changing a scanner colour in index.css without re-running the generator (the unit test fails and names the command).
