# Navigation rebuild, 2026-08-27

Fourteen views were deleted and seven were brought back the same day. This file
exists because the deletion is not findable from `git log`: it landed inside
commit `fcc9de4`, whose message is entirely about an alerts date-stamp
(`fix(alerts): date the levels stamp...`). A sibling session committed a working
tree it did not author. `git revert fcc9de4` to undo the alerts change would
silently restore all fourteen views; reverting the deletion would undo the
alerts fix. Neither commit says so.

## What the app has now

Sidebar / `optionNavItems`, in order:

| View key | Shown as | In the phone bottom bar? |
|---|---|---|
| `Charts & OI` | Charts & OI | yes — "Chart" (landing page) |
| `Quick Options` | Options | yes — "Options" |
| `Auto Alert` | Alerts | yes — "Alerts" |
| `Premarket Scanner` | Scanner | yes — "Scanner" |
| `MomX Scanner` | MomX Scanner | yes — "MomX" |
| `Watchlist` | Watchlist | no — behind "More" |
| `News Feed` | News Feed | no — behind "More" |
| `Earnings Calendar` | Earnings Calendar | no — behind "More" |
| `Mag7 Scanner` | Mag7 Watchlist | no — behind "More" |
| `ROI Calc` | ROI Calc | no — behind "More" |
| `OI Level Script TOS` | OI Level Scripts | no — behind "More" |
| `Settings` | Settings | no — behind "More" |

The phone bar is six columns: Chart, Options, Alerts, Scanner, MomX, More.
`BOTTOM_NAV_LABELS` in `mobileOverflowNavigation.js` names the five the bar
reaches directly; everything else in `optionNavItems` falls into the sheet
automatically. **Adding a nav entry without adding it to `BOTTOM_NAV_LABELS`
puts it in the sheet — that is the intended way to add a secondary page.**

**Settings is reachable ONLY through the "More" sheet on a phone.** The header
gear is hidden at phone width (`.market-strip-right > .header-icon-button` is
`display:none` under 760px), and the chart tab hides the whole top bar. Since
Settings is the only route to Schwab re-authorisation — which expires roughly
weekly — a change that drops it out of the sheet locks the trader out from
mobile. There is a test pinning this.

## Deleted and NOT restored

`OI Scanner`, `Scanner` (stock momentum), `Learning Lab`, `Backtesting`,
`OI Finder` (the Chart + Chain board, removed again on second look - its candles
duplicated the Chart tab),
`Journal`, `Option Journal`, `Memory`. Their state, handlers, column arrays and
CSS were removed in `perf(workspace): stop paying every render...`.

Restoring any of them means recovering the render block from `a52a0da`, the last
commit that still had all fourteen, **plus** its supporting declarations — those
are gone now, unlike during the first restore when they were still present as
dead code.

## Traps this cost us, worth not repeating

1. **`heavyDashboardViews` is the only trigger for the non-compact dashboard
   payload**, and that payload carries `catalysts`. It named only deleted views
   for several hours, so the News panel on the landing page was blank with an
   empty state that blamed the data. Any change to that Set is a change to
   whether news works at all.

2. **A missing icon import does not fail the build.** `Newspaper` had been
   dropped as unused while the News Feed block still referenced it. esbuild
   compiles the missing component to a reference that throws at render time —
   green build, blank page. Babel scope analysis catches it; the bundler cannot.

3. **CSS classes are assembled mid-template.** `oi-metric-*` and
   `news-sentiment-*` are emitted as `` `oi-metric-chip oi-metric-${tone}` `` and
   look dead to any exact-name search. Never delete CSS by line range, and honour
   `prefix-${` anywhere in a template literal, not just at its start.

4. **Views are remembered in `localStorage` (`agenticActiveView`).** A deleted
   view name persists there and is re-entered on every reload.
   `AppErrorBoundary` offers "Reset to the default page" for exactly that.

## Repository hazards (unchanged by this work)

- **This repo has no git remote.** `git remote -v` is empty. Every commit here
  exists on one disk only.
- **Never run `git clean -fd` here.** `agents/` and the MomX scanner work are
  untracked; it would delete both.
- Several sessions edit this working tree at once. Commit promptly — the reason
  `fcc9de4` is mislabelled is that a sibling session swept up someone else's
  uncommitted work.
