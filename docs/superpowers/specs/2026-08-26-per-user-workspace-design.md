# Per-user workspace: one data engine, many desks

Date: 2026-08-26
Status: approved, not started
Owner decisions recorded: 2026-08-25/26 (see "Decisions taken")

## The problem

AGX can now sign several people in — Cloudflare Access identity sign-in landed
in `bb36308`. But the app behind that door is single-tenant, and not passively:
the moment a second person uses it, they degrade the owner's workspace.

Verified today on the live app:

| A second user does this | What happens to the owner |
|---|---|
| Saves a chart layout | Overwrites `artifacts/chart_grids.json` (one shared 4,996-byte file, `api_server.py:17780` and `:18152`) and the browser force-reloads the charts page |
| Adds a watchlist symbol | `POST /api/watchlist/add` calls `STATE.add_watchlist_symbol` with no owner check — changes the owner's watchlist, the scanner universe, and stream subscriptions |
| Adds an OI alert symbol | Changes what the alert engine watches for everyone |
| Opens a different option chain | Option tick subscription is a single global "current underlying", wholesale replaced per request (equities are refcounted; options are not) — the other user's option feed is evicted and silently falls back to polling |
| Opens the Journal | Sees the owner's trades. The trades DB has 17 tables and zero user columns |
| Runs a scan | Overwrites the shared `action_message` and scan-result tables the owner is looking at |

The root cause is one line: `_api_gate_passed` (`api_server.py:17462`) resolves
the signed-in user and returns a boolean, discarding them. Every route below it
cannot know who is asking.

## Decisions taken

The owner (a trader, not a developer) chose:

- **Scale:** 2–5 people he knows personally. Not a paid product.
- **Stated motive for per-user keys:** "I don't want to pay for their data."
- **Approach A** — shared market data, private workspace — after being shown
  that per-user keys are only partly achievable and would give users *worse*
  charts (see "Why not per-user keys").
- **New users are seeded from the owner's setup** rather than starting blank.
- **Users must be able to use the app on desktop AND mobile**, not desktop only.
- Look-only. No order placement, no per-user broker accounts.
- **User-facing text never names an individual.** It says "your
  administrator", not "Ganesh" — this is a product other people use, and a
  personal name in an error message reads as a private tool.

## Mobile is a first-class target

Every step below must work on a phone, not just a desktop browser. This is a
requirement, not a nice-to-have, and it changes some of the work:

- **Per-user state must not be browser-local only.** A user with a laptop and a
  phone expects the same watchlist on both. This is the main reason workspace
  state goes in the database rather than `localStorage`. Anything left in
  `localStorage` (drawings, some chart prefs) will silently differ between a
  person's own devices — that is a known limit to state, not to hide.
- **`localStorage` namespacing by user still matters**, because a shared
  household tablet is exactly the case where one person inherits another's
  workspace.
- **Existing mobile hazards apply.** `index.css` has a `max-width: 760px` block
  that is silently outranked by a later unconditional `.charts-workstation`
  block, so new mobile rules must be appended at the end of the file. Touch
  panning depends on a single Lightweight Charts option (`vertTouchDrag`) that
  gates both the candle pane and the price axis.
- **Mobile browser sign-in is CONFIRMED** (2026-08-26, iPhone over 5G, so
  genuinely through the tunnel rather than local wifi). Cloudflare Access
  emailed the code, the app signed in with no password screen, and the
  six-chart grid rendered with live prices, fire markers, OI levels and the
  bottom navigation. Mobile web is a working target, not an aspiration.
- **PWA sign-in is still unverified.** Only the mobile *browser* was tested.
  The app is also installable to the home screen; a standalone PWA usually
  shares the browser's cookie jar, so Access should carry over, but that has
  not been tested.
- **Mobile layout has real clipping today**, visible in the confirmed
  screenshot and unrelated to sign-in: at six charts on a phone the close
  price is cut off mid-value (`$258.8`, `$341.`, `$575.4`), on-chart alert
  labels are clipped at the left edge (`lert @ Auto PUT OI 342.50`), and the
  per-panel toolbar's last control is cut off. Usable for a glance, not for
  reading a number. Fixing it is not part of this design, but "users can use
  mobile" is not honestly met until it is — see [[dense-chart-layout-width-floor]]
  for the desktop precedent (a 320px min-width floor plus a toolbar gutter).
- Each step's test plan includes a phone check, not only two desktop browser
  profiles.

## "Owner" means one specific thing here

The codebase has three overlapping notions — the first row of `app_users`, any
row with `role = 'admin'`, and the account named by `LOCAL_AUTO_LOGIN_EMAIL`.
They happen to be the same account today, which is exactly how an ambiguity
survives until it breaks something.

Throughout this document **owner** means the account the market-data
credentials belong to: the first active admin, as already selected by the query
at `api_server.py:9911` and `:17206`:

```sql
SELECT id FROM app_users WHERE is_active = 1
ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1
```

This matters in two places, and they differ:

- **Owner-first warming** and **seeding a new user** use *the owner* as defined
  above — there is exactly one.
- **Hiding the Journal** uses `role = 'admin'`, not the owner. A second
  administrator should not be shown the owner's trades merely for being an
  admin; the Journal is visible to the owner only.

Any new code needing "the owner" must call one shared helper rather than
repeating that query a third time.

## Why not per-user keys

Recorded so this is not re-litigated from scratch later.

1. **Schwab cannot be split without Schwab's involvement.** The OAuth redirect
   is pinned to `https://127.0.0.1` with the listener bound to the owner's
   machine (`schwab_oauth_callback.py:116`, `config.py:202`). A remote user
   clicking Connect is redirected to their own laptop, where nothing listens.
   Fixing it needs the developer app re-registered with a public callback *and*
   every user obtaining their own Schwab developer approval — Schwab's timeline.
2. **Free Alpaca is worse than what the owner sees.** IEX-only: a thin slice of
   the tape that the codebase itself notes sits cents away from Schwab's price,
   with overnight bars delayed. Users would get visibly different charts.
3. **The data is identical anyway.** AAPL's candles are byte-identical for every
   viewer. Splitting them multiplies the 215 MB chart cache and every broker
   call for pixels that do not differ.
4. **The real cost is speed, not money.** Schwab market data comes with the
   account; Alpaca's tier is free. What another user actually costs the owner is
   background-warmer time — addressed below by owner-first prioritisation.

`data/user_provider_context.py` already exists and is unused. It is the right
seam if per-user keys are ever wanted; this design does not remove it.

## Architecture

### 1. Carry identity past the front door

`_api_gate_passed` returns the resolved user instead of a boolean. Routes that
need identity take it as a parameter. Routes that do not are unchanged.

This is the only change the rest depends on, and it is small. It must land
first and alone, so that a regression in it is unambiguous.

### 2. Where private state lives

One new table in the existing database (`database/trades.db`, which already
holds `app_users`):

```sql
CREATE TABLE IF NOT EXISTS app_user_workspace (
    user_id    TEXT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
    key        TEXT NOT NULL,
    payload    TEXT NOT NULL,          -- JSON
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, key)
);
```

A table rather than per-user files because:

- Seeding a new user from the owner's setup becomes one `INSERT ... SELECT`.
- Two people saving at once cannot corrupt a shared file.
- `ON DELETE CASCADE` means removing a user removes their workspace.

**Performance note.** A prior incident (`app-slowness-root-causes-2026-08-21`)
traced universal slowness to a per-request database hit in the auth gate. This
table must therefore be read **only by the routes that need it**, never in the
gate. Reads are per-endpoint, not per-request.

Keys: `chart_grids`, `watchlist`, `option_watchlist`, `mag7_scanner_symbols`,
`oi_alert_symbols`, `oi_alert_history`, `scanner_config`.

### 3. What stays shared

Unchanged, computed once, read by everyone: the candle tape and its 215 MB
disk cache; option chain snapshots; High-OI wall levels and the Mag7 OI board;
MTF/TOS-parity signal labels (a pure calculation over the shared tape); the
premarket scanner (hard-coded 9 tickers); live price quotes; the equity tick
stream (already refcounted per symbol); earnings, news and macro calendars.

### 4. Subscriptions and prioritisation

Today the watchlist doubles as the stream subscription set and scanner
universe. With per-user watchlists:

- The subscription set becomes the **union** of active users' watchlists.
- The background warmer processes the **owner's symbols first**, then others.
  Without this, another user's 30 symbols make the owner's charts slower — the
  one cost of this design the owner explicitly accepted.
- Scans run once over the union; results are stored per symbol; each user's
  view filters to their own watchlist. This avoids N scan runs for N users.

### 5. Option tick subscription

Equity subscriptions are refcounted and already multi-user-correct. The option
feed is a single global "current underlying" replaced wholesale per request.
It must become refcounted the same way, or two users on different chains will
keep evicting each other.

### 6. Status banner and scan results

`action_message` and the scan-result tables live on the shared `STATE` object.
They become a small in-memory dict keyed by user id. These are transient view
state, not durable workspace, so they do not belong in the table above.

### 7. Journal and positions

Visible to the owner only - not to other administrators (see "Owner means one
specific thing here"). The trades database is structurally single-tenant
(17 tables, no ownership column) and splitting it is out of scope for a
look-only design. **This is a stated limitation, not a solved problem** — it is
recorded here so it is not later mistaken for done.

### 8. Browser-local preferences

Layouts, drawings and price alerts are stored in `localStorage` with no user
attached, and signing out does not clear them — so two people sharing one
computer inherit each other's workspace. Keys become namespaced by user id,
and sign-out clears the namespace. Not requested by the owner; included
because it is the same defect class as the rest and small.

### 9. The "trader is busy" switch

One process-wide timer that pauses background work while the trader is active,
with no notion of *which* trader. With several users it effectively never
releases and the warmers starve — already a documented failure at 272 of 398
caches stale, oldest 206 hours. It becomes per-user, with background work
pausing only for the users actually active.

### 10. Seeding a new user

On first sign-in, a user with no workspace rows gets a copy of the owner's
`chart_grids`, `watchlist`, `option_watchlist` and `oi_alert_symbols`. Alert
*history* is never copied. Seeding happens once and is recorded, so a user who
deliberately empties their watchlist does not get the owner's back.

## Order of work

Each step is independently testable and independently shippable. The owner can
stop after any of them.

1. **Identity past the gate + chart layouts private.** One afternoon. Touches
   no market data, no broker keys, no background threads, so it cannot break
   charts. Migrates the existing `chart_grids.json` to the owner's account.
   *Test:* two browser profiles, save a different grid in each, confirm neither
   disturbs the other and the owner's existing layout survived.
2. **Watchlists private** (stock, option, Mag7) + union subscriptions +
   owner-first warming.
   *Test:* second user adds a symbol; owner's watchlist and scanner universe
   unchanged; the new symbol still streams.
3. **OI alerts private** — watched symbols and fired history.
4. **Scan results and status banner per-user.** Option tick subscription
   refcounted.
5. **Journal hidden from non-owners; `localStorage` namespaced; busy-switch
   per-user.**

## Testing

Every step follows the project's TDD practice. Two constraints inherited from
this codebase:

- **`api_server` cannot be imported in tests** — it boots every scheduler and
  opens the live database (`tests/test_gateway.py` documents this). Logic that
  needs real testing must live in its own importable module, as
  `request_trust.py` and `cf_access_auth.py` now do. Source-lifting assertions
  are for wiring only.
- **Python edits to `App.jsx` flip it to CRLF** and break the tests that regex
  functions out of its source. Edits must preserve line endings.

Multi-user behaviour is verified with two real sessions, not simulated: the
owner in one browser profile, a test user in another.

## Out of scope

- Per-user broker keys (see "Why not per-user keys").
- Order placement, positions, per-user broker accounts.
- Splitting the trades database.
- More than ~5 concurrent users. Measured: one active user already consumes
  more than one of twelve cores, so the practical ceiling is roughly 6–8
  simultaneous users regardless of this work.

## Open risks

- **Union subscriptions raise broker call volume.** With 2–5 users it should be
  comfortable; if it is not, the honest fix is capping per-user watchlist size,
  not silently dropping symbols.
- **No documented Schwab quota** was found anywhere in the codebase, so the
  ceiling on shared-key traffic is unknown until it is hit.
- **The owner remains the single data licensee** serving broker data to other
  people. This design does not change that, and it is a commercial question
  rather than a technical one.
