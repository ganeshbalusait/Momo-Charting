# Scanner and MAG7 chart candle recovery — October 30, 2026

## Follow-up: remaining recovery wait and redundant startup refresh

The trader asked why chart loading was still open. Initial rendering and
fresh-history recovery were different clocks: the prior SPY observation
painted candles at 3.47s but delivered current history at 16.38s. Its forced
refresh response still held old candles; the next read started about ten
seconds later. There was no evidence that the provider imposed that wait.

The chart now rechecks a confirmed gap independently of further quotes.
After a successful history response, an outstanding gap schedules a stored
tail read in one second. Unchanged history backs off after the first five
checks (2.5s for three checks, then 10s). Advancing delivered history resets
that short burst; a caught-up history clock stops it. Only one chart read is
in flight, queued explicit refresh/deep requests take priority, and the timer
is canceled on ticker change/unmount. These checks do not fabricate bars or
alter study formulas or the trader's viewport.

A second confirmed defect queued unnecessary work during chart startup: the
empty-chart watchdog fired after 1.5s and asked for a broker refresh even
while the initial HTTP read was healthy and still running. That forced a
second request immediately after the first candles painted. Empty recovery
now requests the stored tape; the existing in-flight guard skips a duplicate
instead of queuing a broker rebuild. Genuine empty/canceled reads retain
their recovery retries.

The server bridge also copied all 12,000 retained minute records per symbol
before discarding records outside its two-day recovery window. It now asks
the stream for that window directly. The default archive reader and on-disk
history remain intact. Filtering by timestamp rather than stopping at an old
tail preserves late, out-of-order corrections. A 12,000-row comparison used
2,880 inputs on the recent path and produced identical output. The component
benchmark's wall-time medians were 121.77ms versus 79.15ms under validation
load; this is not an end-to-end navigation-speed claim.

Controlled before/after evidence is separate from live-market measurements:

- One quote exposed a gap, then quotes stopped. Captured provider OHLCV became
  available after 2.5s. Suppressing only the new polling policy in the browser
  reproduced a **29.44s** recovery. With the policy enabled, SPY/TSLA recovered
  in **3.54/3.58s desktop** and **3.42/3.58s phone width**. Polling stopped
  after catch-up. The before case is an explicit counterfactual behavior test,
  not a claim that a complete old release was deployed.
- A saved pre-watchdog-fix production bundle with a 2.5s first response
  reproduced one unnecessary `refresh=true` request. Current-bundle desktop
  and phone checks both produced zero such requests. Both widths passed
  cancellation of gap follow-ups on ticker changes.
- Evidence: `artifacts/chart-gap-silent-*.json`,
  `artifacts/chart-gap-poll-focused.log`, `chart-gap-poll-full-js.log`, and
  `chart-gap-poll-build.log`. Final frontend suite: **1,585 passed**, including
  separate mid-price bot changes made in the shared workspace. Focused
  recovery/initial-history frontend checks: **24 passed**. Focused backend
  checks: **28 passed**. Complete relevant backend suite: **1,768 passed,
  one retired-module skip, 27 warnings** (540.64s under concurrent validation
  load). Source-study formulas were not changed by this follow-up.
- The production matrix passed **37 desktop / 35 phone-width checks** again:
  all 11 timeframes on SPY/AAPL, single/two-chart, big screen, fullscreen,
  zoom/pan/crosshair, gamma options/save/reset/cross-ticker persistence and
  cloud bounds. Controlled continuing-tick recovery still restored 21 missing
  5m bars per symbol with no deep escalation. Repeated REST tails added one
  candle exactly once at both widths.
- Initial cancellation/reload probes stalled on "Loading secure workspace"
  while API responses were busy. Their observations are retained with
  `busy-api` filenames. Separate reruns passed cancellation and SPY/AAPL
  layout/ticker/timeframe restoration with drawing tools hidden at both
  widths. No sign-in requirement was bypassed to obtain a pass.
- The existing watchdog activated the backend change after a verified
  single-process restart. Local API health and auth status both returned
  HTTP 200; the new API PID and source hashes are saved in
  `artifacts/chart-gap-poll-activation.json`. The frontend production build
  passed in 19.50s with the required top ET release entry.

Live checks after activation passed without runtime/console errors or
wrong-ticker frames. They **do not close all cold desktop loading work**:
the first desktop run at 14:08–14:09 ET showed scanner first candles in
4.70–9.03s (fresh-history checks 5.37–9.45s), while the following phone-width
run showed 0.50–1.33s (0.80–1.60s current history). Default MAG7/SPY switches
were 0.39–4.24s desktop and 0.35–1.73s phone width. A later fresh desktop
context at 14:13 ET, against the warmer server, showed scanner first candles
in 0.57–2.04s and default switches in 0.38–2.43s. These are observations,
not a controlled cold-versus-warm benchmark. The artificial recovery wait and
false startup refresh are fixed; cold initial loading remains variable.

Final evidence: `artifacts/chart-loading-gap-poll-final-{desktop,phone}.json`
and `artifacts/chart-loading-gap-poll-trace-desktop.json`. The latter retains
all request issue/finish times for continued cold-load investigation.
The separate intermittent `Value is null` incident documented below is not
claimed diagnosed by this performance change.

An earlier timing run was interrupted by an independently applied API
restart, and other readings coincided with test load and post-restart warm-up.
They are not presented as clean before/after speed comparisons. The watchdog
log and startup stack inspection showed the recorded-history archive being
read during startup (221.6 MiB decoded JSON); that cold-start work is separate
from the repaired in-chart retry delay and is not claimed eliminated here.

The reported TSLA screenshot showed an old candle cluster followed by a new
live-price candle with missing intervening history. The trader confirmed the
problem affected scanner tickers generally and asked for fast scanner entry
and default MAG7 chart switching.

## Confirmed causes and changes

- The chart refresh FIFO had more than 100 queued jobs in the running log.
  Scanner/new-match and hot-set background producers could submit more work
  than the two tail workers completed. Background tail submission now stops
  at two waiting jobs. Explicitly requested charts remain eligible, and the
  existing per-symbol guard still deduplicates their work. Full rebuilds and
  unknown queue-depth fallbacks keep their existing paths.
- Recent broker CHART minute records already contained the intervening
  TSLA/AAPL/SPY/AMZN/CBOE candles. The serve path now joins the selected
  symbol's recorded OHLCV directly from memory to its REST snapshot, before
  slimming or returning a delta. This avoids waiting for study replay or
  another provider request to display candles already received. A cold
  chart can also paint this real recorded seed while REST warms; initial
  responses retain the 900-minute-bar cap.
- The join preserves existing closed REST minutes, fills available missing
  timestamps, extends the tail and merges the current forming minute's
  extrema/volume. It rejects malformed, future, non-minute and quote-only
  records, and never interpolates no-trade/halt intervals. Its request work
  considers only the recent two-day recorded bridge. It does not recompute
  or fabricate the backend's signal history.
- Gap recovery compared each new tick to the preceding live tick. After one
  jump, later ticks appeared adjacent and could suppress repair even while
  downloaded history stayed old. The chart now tracks delivered history
  separately and retries against that clock. Its third gap attempt asks for
  a bounded broker refresh; subsequent plain reads collect the result.
- Quote-updated shared caches could advance the delta cutoff across missing
  history. A delivered-history watermark now survives quote updates and
  advances only on delivered candle data. The next delta includes the gap.
- Queuing a broker-tail repair behind an in-flight request implicitly turned
  it into a deep archive download. These are separate requests now.
- Default MAG7/quick tickers now share scanner-style compact first loading.
  Intraday history expands when panning left; D/W/M explicitly request the
  daily archive. Chart snapshot/delta/archive and history-status requests are
  canceled when their last visible pane leaves the ticker, including while
  reading their bodies. Linked panes still using the same symbol retain
  their shared request. Cancellation preserves an already loaded same-symbol
  seed in the client cache.
- Corrected browser traces showed GOOGL spending 24.3s before request start
  and only 227ms waiting for response headers afterward. Pending-request
  capture identified automatic synchronous news POST scrapes and obsolete
  grades/summary reads occupying HTTP connections during ticker changes.
  Automatic ticker news now uses the existing stored-news GET alongside the
  selected ticker's asynchronous `/api/news-feed/refresh` request. The latter
  acknowledges one background scrape (or reports an already running job),
  instead of keeping the browser connection throughout a scrape. These reads
  have an 8s deadline and cancel on navigation. Explicit manual POST refresh
  behavior is retained. Old grade
  and ticker-summary reads cancel on cleanup, and best-effort timing reports
  have a 5s deadline and low priority.
- The left drawing rail now starts hidden at both widths and closes on a
  ticker change. Its collapsed state reserves no plot gutter. The Draw
  button opens/closes it explicitly; saved drawings are unchanged.

The complete historical archives, user settings and trade records remain in
place. Translated trading-study formulas were not changed. Source timestamps,
recorded candles and last-price quote updates are distinct observations.

## Checks completed

- Initial focused frontend recovery checks: 60 passed. Final focused
  ownership/delta/candle/timing checks: **50 passed**.
- Complete frontend suite after all changes: **1,579 passed**.
- Targeted new backend checks: **9 passed**; initial broader focused backend
  checks: 61 passed before the last additions.
- Complete relevant backend suite: **1,758 passed, one retired-module skip,
  25 warnings**. Includes API/chart/stream/aggregation/TOS/premarket/MomX
  tests. Results: `artifacts/chart-gap-full-backend.log`.
- Controlled desktop/phone replay recovered 21 missing 5m bars for TSLA and
  SPY while quotes continued. The final replay additionally holds a plain
  read open to verify queued recovery does not request `deep=true`.
- Production build and top ET release notes are recorded in
  `artifacts/chart-gap-production-build-final.log` and
  `frontend/public/release-notes.json`.

- Production chart matrix: **37 desktop / 35 phone-width checks passed**,
  including all 11 timeframes for SPY/AAPL, single chart, desktop two-chart
  and big screen, fullscreen, zoom/pan/crosshair, expiry/basis/empty state,
  indicator controls/save/reset and compact gamma-band bounds. Evidence:
  `artifacts/chart-gap-matrix-desktop.json` and
  `artifacts/chart-gap-matrix-phone.json`.
- Controlled TSLA/SPY quote replay recovered **21 missing 5m candles each**
  at both widths; one forced tail refresh each, no `deep=true` escalation.
  REST reconciliation also appended one real-shaped candle without
  duplicating it on repeated reads. These are fixture regressions, separate
  from actual-market observations.
- Focused drawing/layout checks passed at both widths: SPY/AAPL switching,
  explicit Draw open/hide, save and reload with the rail still hidden, and
  retained ticker/timeframe. Desktop saves the complete workspace; phone
  saves its timeframe layout through the indicator editor. The initial
  additional reload harness mistakenly awaited a workspace POST from the
  per-timeframe layout control; the corrected focused harness uses each
  actual save path. Evidence: `artifacts/chart-toolbar-layout-*.json`.

Final actual-market timing checks at both widths completed without
runtime/console errors, wrong-symbol frames, unrequested deep downloads or
synchronous automatic news POSTs. All eight selected tickers requested their
asynchronous news refresh. An additional production phone-width stress run
completed 35 scanner/ticker openings (three scanner entries plus four rounds
of eight default tickers) without errors.

One preceding phone timing run did emit an intermittent Lightweight Charts
`Value is null` error. The original harness retained its message without a
stack. The next full phone run and the 35-opening production stress run did
not reproduce it. It has **not** been diagnosed or claimed fixed. The failing
observation remains in `artifacts/chart-loading-intermittent-error-phone.json`.
The diagnostic harness now retains stacks. A slower development-mode
diagnostic was stopped; its timings are not production results.

## Actual-market browser timings

Fresh isolated browser contexts against the running production build and
warm server, 12:54–12:57 ET. No chart, quote or market-data responses were
replaced. Only saved profiles/layout endpoints were isolated to preserve
trader settings. "Current history" requires a delivered-history watermark
within 180s of browser time, separately from streaming last-price updates.

| Path | Desktop first candles | Phone-width first candles | Current history |
| --- | --- | --- | --- |
| Scanner TSLA/AAPL/AMZN (3 each) | 0.74–1.60s | 0.87–2.44s | desktop 1.29–2.41s; phone 1.46–3.00s |
| Default MAG7 + SPY (8 each) | 0.60–3.47s; median 1.71s | 0.42–3.54s; median 2.05s | desktop 0.84–16.38s; phone 0.55–3.73s |

Desktop pre-news-fix vs final first-candle observations were GOOGL
13.09→1.83s, META 5.47→1.58s and MSFT 17.43→2.52s. These are sequential
real-market observations, not a controlled same-instant benchmark. The
phone final MSFT result was 3.54s (earlier corrected run: 16.44s).
SPY's desktop first history response was older than the freshness threshold;
recovery delivered current history after 16.38s (first candles at 3.47s).
Thus the fixes remove
confirmed queue/gap defects, but do not establish instantaneous loading or
zero broker/host latency. Delivered minute timestamps were 51–121s old at
sampling (minute-start timestamps, distinct from live quote time).

Evidence for these prior observations: `artifacts/chart-loading-before-gap-poll-desktop.json`,
`artifacts/chart-loading-before-gap-poll-phone.json`, and
`artifacts/chart-loading-before-news-fix-desktop.log`.

## Measurement and source limitations

`artifacts/chart-gap-20261030/` preserves before/after API payloads and the
pre-restart log. Before repair, the actual 10:47 ET initial reads were 18m
old for AAPL, 47m for AMZN and 25m for CBOE, while TSLA/SPY had newer stored
minutes. The later API check used recorded history and returned recent
minute data without waiting for a study rebuild. It retained a real gap
across a process restart until REST recovered those minutes; it did not
manufacture candles to hide that interval.

The first browser timing attempt constructed a date formatter for every
historical bar in its response handler, adding test-driver overhead. Those
reports are explicitly preserved as instrumentation-overhead runs and must
not be used as controlled speed comparisons. The corrected harness uses
numeric session bounds and captures network timings. Fresh-browser tests
against a warm server do not establish a cold-server or physical iPhone/5G
performance guarantee.

Backend test collection now uses a scratch stream-history path instead of
parsing the live account's large archive. The running service's default path
is unchanged. This is test isolation, not a simulated live-market result.

## Sign-in interruption and recovery

During a host/tool-session interruption, the manually launched watchdog and
API were no longer running. The existing Windows scheduled task
`AgenticAI-Trading-24x7` was started to restore supervision outside that tool
session. Local API/gateway auth checks returned actual status replies in
52/53ms. A browser reached the public site's normal Cloudflare Access gate;
anonymous automation could not pass that private gate. The trader subsequently
confirmed successful app login. No credentials were reset or changed.

The one-time earlier scanner audit automation was already removed; no new
scheduled automation or trading task was created for these repairs.

## Mobile password-manager sign-in follow-up, 14:25 ET

The trader reported the mobile app's "Invalid email or password" message.
Local health/auth endpoints were healthy. A read-only check found the owner
account active with a structurally valid stored credential and no recent
password-record change. No password, session, device or permission was reset.

The existing React-controlled form sent its state values, rather than
necessarily the values visible in the inputs. A production-browser fixture
reproduced three cases: native autofill without input/change events submitted
empty values; replacing earlier typed values through a password manager sent
old values; owner-setup autofill had the same problem. Manual typing sent the
expected values. A first FormData-only fix exposed an additional blur/rerender
case where controlled fields could overwrite a sibling's autofilled value.

Credential fields are now native/uncontrolled and named. Submission captures
FormData before changing submission UI state. Email declares username
autocomplete and disables mobile autocapitalization/autocorrection. Native
validation, exact password contents, server authentication and device approval
remain authoritative.

Verification: all four cases passed at desktop and 390px phone width against
the final production build; card bounds fit both viewports. These tests use
dummy credentials and reject every mocked login, so they verify submission
and error handling without creating a real session or bypassing authentication.
The complete frontend suite passed 1,585 tests; the complete selected auth,
Cloudflare and OAuth-context suites passed 51 tests. The production build
passed in 21.63s with the new top ET release note. Artifacts are
`signin-autofill-{desktop,phone}-{before,after}.json`,
`signin-autofill-full-js.log`, `signin-autofill-auth-tests.log` and
`signin-autofill-build.log`. The before fixture was run at phone width.

The actual phone/Keychain and the trader's real password cannot be verified by
the isolated browser fixture. The trader was asked to open the updated page
and retry; actual-account sign-in confirmation is pending.
