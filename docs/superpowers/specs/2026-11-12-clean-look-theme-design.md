# AGX clean look: dark and light with a switch

Date: 2026-11-12
Status: waiting for Ganesh's approval
Mock-up: https://claude.ai/artifact/YW8nyh8HzY3SqvZysuWd8e (private to Ganesh; sample data)

## Intent

Ganesh wants the app to look "very neat and clean", in the Apple manner: one
calm font, a short colour list, more breathing room. He was shown a dark and a
light mock-up of the Scanner screen and chose **both, with a switch**.

Why: the app grew screen by screen. `frontend/src/index.css` is about 23,700
lines with roughly 1,700 different hand-typed colour values and several
competing fonts (Cascadia Mono as `--terminal-font`, Open Sans for MomoX,
separate scanner fonts). It reads as busy. The data and the behaviour are fine;
only the look changes.

## Goals

- One look across the app: system font, a short named colour list, rounded
  panels, thin dividers, pill buttons and segmented switchers as in the mock-up.
- A dark theme and a light theme, chosen by a switch in the app, remembered on
  that device.
- Delivered one screen at a time, each release safe to trade on.

## Non-goals

- No change to what any screen shows, to column sets, to scanner/BOT rules, or
  to any number.
- No change to navigation structure. The mock-up drew the menu across the top
  for simplicity; the real app keeps its left sidebar (`.sidebar`, 166px,
  collapsible) and its top strip (`header.topbar.trading-header`). They are
  restyled, not moved. (Builder's call, not something Ganesh asked for.)
- No new features riding along.

## Anti-goals (would make this a failure even if it looks good)

- Any chart signal colour changing. Ganesh's standing rules: candles are cyan
  and magenta only, and studies translated from his TOS scripts keep the TOS
  colours, labels and visibility. Arrows, grade circles, OI wall lines, squeeze
  dots, MomoX upper-pane colours stay exactly as they are in dark mode.
- A slower app. No extra re-renders of charts on theme change beyond one
  `applyOptions` call per chart; no layout work added to the tick path.
- A half-converted screen that is unreadable in light mode.
- Losing density on the scanner. Rows may get a little more air, but the
  number of rows visible on his screen must not drop by more than about 10%.
  Row height and paddings are the builder's to tune against that limit.
  Baseline (builder's call): the MomX Scanner main table with the ticker card
  closed, at 1920x1080 and at 375x812, rows fully visible counted in an
  end-to-end spec before the Scanner release changes anything; that count is
  then asserted after.

## Constraints

- Live repo is `AgenticAI-Trading 2`; other sessions commit there. Commit only
  this work's paths.
- AGENTS.md applies: unit + integration + end-to-end tests for every release,
  production build, browser smoke at desktop and phone width, a release-notes
  entry at the top of `frontend/public/release-notes.json` in the same commit.
  No full suites 09:30-16:00 ET.
- `index.css` has a known cascade trap: the 760px mobile block is outranked by
  later rules. New theme rules must be checked at 375px wide.
- Tests that lift source out of `App.jsx` need LF line endings; do not let an
  edit flip the file to CRLF.
- Chart colours for the four `createChart` sites in `App.jsx` (lines near 3110,
  4520, 9093, 19407) and `MiniEventChart.jsx` are set in JavaScript, not CSS.
- The PWA `theme-color` in `frontend/index.html` and
  `frontend/public/manifest.webmanifest` is fixed at `#0c0c0d`.

## Approaches considered

1. **Shared colour list, one screen at a time (chosen).** Add a small set of
   named colours and text sizes with a dark and a light value each, plus the
   switch. Convert each screen's styles to the names in its own release.
   Unconverted screens keep today's look. Slowest, safest, correctable early.
2. **Rewrite the stylesheet in one go.** Consistent soonest, but 23,700 lines
   cannot be verified in one pass and a break would land during trading.
3. **Script that maps each old colour to the nearest new one.** Fast, but light
   mode would be wrong wherever a colour's meaning is not its hue, and the
   result would only look half clean.

Ganesh was shown all three and left the choice to the builder; approach 1.

## The design

### The colour and type list

Defined once at the top of `index.css` (or a new `theme.css` imported first)
as CSS custom properties on `:root`, with the light values under
`:root[data-theme="light"]`. Names are `--agx-*`. Starting values, from the
mock-up:

| Name | Dark | Light | Used for |
|---|---|---|---|
| `--agx-bg` | `#000000` | `#f5f5f7` | page background |
| `--agx-card` | `#1c1c1e` | `#ffffff` | panels |
| `--agx-raised` | `#2c2c2e` | `#ececf0` | inputs, chips, switcher track |
| `--agx-line` | `#38383a` | `#d9d9de` | dividers |
| `--agx-line-soft` | `#2a2a2c` | `#ececf0` | row separators |
| `--agx-text` | `#f5f5f7` | `#1d1d1f` | main text |
| `--agx-text-2` | `#a1a1a6` | `#636366` | secondary text |
| `--agx-accent` | `#0071e3` | `#0071e3` | active chip, primary button (white text on it) |
| `--agx-up` | `#30d158` | `#1b7f37` | positive numbers |
| `--agx-down` | `#ff453a` | `#d70015` | negative numbers |
| `--agx-warn` | `#ffd60a` | `#8a5a00` | cautions |

Font: `-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI Variable",
"Segoe UI", system-ui, sans-serif` as `--agx-font`, always with
`font-variant-numeric: tabular-nums` so price columns do not jitter (that was
the reason for the old monospace font). Text sizes: 12 / 13 / 14 / 15 / 17 / 28.
Radii: 18 panels, 12 inputs, 10 selected rows, 999 pills.

### How a screen opts in (builder's call)

The new look never applies globally. A converted area carries the class
`agx-skin` on its container (release 1: `.sidebar`, the top strip and the page
background; later each screen's root). The new font, the `--agx-*` colours and
the light values take effect only inside `.agx-skin`. Outside it, everything
renders exactly as today in both themes: old font, old dark colours.

The existing `--terminal-font` and the 16 `--terminal-*` colours (47 uses) are
left alone globally. When a screen is converted, its uses are replaced with
`--agx-*` names (or re-pointed inside that screen's `.agx-skin` scope). Column
widths tuned for the monospace font are re-checked in that screen's release.
This keeps release 1 small and means no screen is ever half light, half dark.

### Signal colours outside the chart (builder's call, flagged to Ganesh)

Colours that carry trading meaning outside the chart canvas: cyan/magenta
cells, A+/A/B grades, hot-sector and bolt marks, OI board and heatmap fills,
sector tiles.

- Dark theme: unchanged, exactly today's values. They are not folded into
  up/down/warn.
- Light theme: same hue, a darker shade where the colour is used as text or a
  thin mark on a light panel, so it stays readable (as the mock-up did:
  cyan `#0891b2`, magenta `#c026d3`, amber `#8a5a00`). Solid fills with their
  own text (heatmap tiles, grade pills) keep today's fill.
- Each gets a named `--agx-sig-*` variable when its screen is converted; the
  list grows per screen and is not capped at the table above.

### Contrast rule

Ordinary text/background pairs inside `.agx-skin` must meet 4.5:1. Exempt:
the chart canvas, and dark-theme signal colours (kept as today even where they
fall short). Light-theme signal shades must meet 4.5:1 as text. Up/down also
differ in sign (+/-), never colour alone.

### Component shapes

The mock-up is a direction for the Scanner, not a pixel reference; this
section governs where they differ, and the builder extends the same shapes to
other screens.

- Panel: `--agx-card`, radius 18, padding 24, no border, no shadow in dark;
  24 gap between panels.
- Segmented switcher (Bull/Bear, timeframes): track `--agx-raised`, radius 12,
  3px inset; selected segment radius 9, `#636366` with white text in dark,
  white with a faint shadow in light; unselected is `--agx-text-2`.
- Chip / pill button: radius 999, `--agx-raised` with `--agx-text`; the active
  one is `--agx-accent` with white text, weight 600.
- Primary button: `--agx-accent`, white text, radius 12.
- Search/input: `--agx-raised`, radius 12, no border.
- Table: header 12px `--agx-text-2` over a `--agx-line` rule; rows separated
  by `--agx-line-soft`; selected row `--agx-raised` with radius 10; ticker
  15px weight 600; numbers right-aligned.
- Headings: screen title 28px weight 700, letter-spacing -0.02em; panel title
  17px weight 600.
- Touch targets at phone width at least 44px tall; desktop controls may be
  smaller to protect density.

### The switch

- A two-state control (Dark / Light) in the left sidebar footer, visible on
  every screen; on phone width it lives where the sidebar's items live there.
  Exact placement is the builder's call.
- Sets `data-theme` on `<html>`. Stored in `localStorage` under
  `agx.theme`; applied by a tiny inline script in `index.html` before the app
  mounts so there is no flash of the wrong theme.
- Default for everyone is **dark**, today's behaviour. It does not follow the
  Windows/phone setting. (Builder's call: nobody should find the app changed
  colour by itself.)
- Per device, not per account, in the first release. (Builder's call; simplest,
  and Ganesh may want dark on the desk and light on the phone.)
- Also updates the `theme-color` meta tag so the phone's status bar matches.

### Charts in light mode

- The chart frame (toolbar, timeframe buttons, panels around the chart) follows
  the theme.
- The chart drawing area itself **stays dark in both themes** for now. His TOS
  colours are tuned for a dark background, and yellow, cyan and white marks
  would vanish on white. (Builder's call. The mock-up showed a light chart with
  darker cyan/magenta; a properly light chart is a separate later piece that
  needs its own colour mapping and his sign-off study by study.)
- So the JavaScript chart colours do not change in this project, except the
  chart font, which moves to `--agx-font`'s stack.

### Order of releases

Each is its own commit with tests and a release note, and can stop there
without leaving the app broken:

1. Foundation: the list, the font, the switch, sidebar and top strip, page
   background. Only these carry `agx-skin`. Unconverted screens stay dark with
   today's font even in light mode (a dark panel on a light page; acceptable
   for a few days, and light mode is labelled "preview" in the switch until
   release 6).
2. MomX Scanner (the daily screen): table, chips, filters, ticker card, history.
3. Charts & OI frame: toolbars, option chain, OI board, heatmap panels.
4. BOT, Scorecard, chat dock.
5. Long Term, Sectors, Morning Brief, Learning Center, Release Notes, Journal.
6. Login, settings, dialogs, toasts, error screens; remove the "preview" label.

After each release Ganesh looks at it and can redirect before the next.

### Testing

- Unit (`frontend/src/*.test.js`): the theme helper (read/write/apply, bad or
  missing stored value falls back to dark).
- Integration: the built `index.html` served by the API harness contains the
  pre-mount theme script and the app boots in each theme.
- End to end (`tests/e2e/`): flip the switch, reload, theme persists; at
  desktop and 375px; for each converted screen, no text under 4.5:1 against its
  background in either theme (sampled computed styles), and chart candle
  colours unchanged.
- Existing suites stay green; chart specs prove no chart regression.

## Left to the builder

Exact spacing and row heights within the density limit, switch placement and
wording, whether the list lives in `index.css` or a new file, how to group the
CSS conversion inside each screen's release, and small colour adjustments
needed to pass contrast.
