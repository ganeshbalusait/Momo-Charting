# First-time setup (owner)

Who this is for: **the person running the AGX server** — the market-data keys
are configured ONCE, here, and every user of the app shares them. Regular
users never set up broker keys (that is a deliberate product decision from
2026-08-26: Schwab's OAuth is pinned to a loopback redirect so remote users
cannot complete it, and free per-user Alpaca keys would give everyone worse
charts than the shared feed). The only per-user setup is phone alerts, and
that lives in the app itself — Learn → MomX Scanner → "Set it up" steps.

Everything below lands in `.env` in the repo root (copy `.env.example` to
`.env` if it does not exist), followed by a backend restart.

---

## 1. TOS / Schwab market data (charts, live stream, option chains)

Schwab is the app's consolidated real-time source: the active chart stream,
option chains, and the MomX fastlane quotes all ride one Schwab connection.

1. Create a developer account at **developer.schwab.com** (free; uses your
   Schwab brokerage login) and register an app requesting the
   **Market Data** and **Accounts and Trading** products.
   - Callback URL: `https://127.0.0.1/` — must match `SCHWAB_REDIRECT_URI`.
   - App approval is not instant; Schwab typically takes a few days.
2. When approved, copy the **App Key** and **Secret** into `.env`:

       SCHWAB_CLIENT_ID=<app key>
       SCHWAB_CLIENT_SECRET=<secret>
       SCHWAB_REDIRECT_URI=https://127.0.0.1/

3. One-time authorization (mints the refresh token). In a browser, open:

       https://api.schwabapi.com/v1/oauth/authorize?client_id=<app key>&redirect_uri=https://127.0.0.1/

   Log in, approve, and the browser lands on a `https://127.0.0.1/?code=...`
   error page — that is expected. Copy the `code` value from the address bar
   (URL-decode it: `%40` is `@`), then exchange it:

       curl -X POST https://api.schwabapi.com/v1/oauth/token \
         -u "<app key>:<secret>" \
         -d "grant_type=authorization_code&code=<code>&redirect_uri=https://127.0.0.1/"

   Put the returned `refresh_token` into `.env` as `SCHWAB_REFRESH_TOKEN`.
   The server refreshes access tokens itself from then on.
4. Restart the backend. The header badges (`TOS DATA` / `TOS STREAM`) go
   green when the stream serves.

**Known gotcha:** Schwab refresh tokens expire roughly every 7 days. When
charts load but stop ticking, the token has lapsed — repeat step 3. The
market-data and trading profiles hold separate tokens; one can be healthy
while the other is expired.

## 2. Alpaca market data (scanner bars, watchlist stream, overnight candles)

Alpaca supplies what Schwab cannot: bulk multi-symbol history for the
357-name scan, and the overnight session (8 PM–4 AM) that TOS-parity 2H/4H
signals require. A **free** account is enough.

1. Sign up at **alpaca.markets** → open the (paper) dashboard → generate an
   API key pair.
2. In `.env`:

       ALPACA_PAPER_KEY_ID=<key id>
       ALPACA_PAPER_SECRET_KEY=<secret>

   Additional trading profiles (`ALPACA_PROFILE_PAPER2_*`, …) are optional
   and only matter for the paper-trading accounts, not for market data.
3. Restart. The `ALPACA` badge goes green during sessions with data flowing
   (it is red on weekends by design — nothing to stream).

**Known limits of the free tier (accepted trade-offs, do not "fix" them):**
SIP data is ~16 min delayed, so the scanner merges an IEX live tail
(~1–3 min); the newest bars carry thin IEX-only volume until SIP catches up.

## 3. Phone alerts (ntfy) — per user, in the app

No server setup. Each user: install the free **ntfy** app → in AGX open
MomX → gear → Phone push → **Generate** → subscribe to that exact name in
the ntfy app. The scanner pushes each account's alerts to its own topic.
The topic name is the secret; the Generate button makes unguessable ones.

## 4. After any `.env` change

Restart the backend (`Start-ScheduledTask 'AgenticAI-Trading-24x7'` or the
watchdog restarts it). Verify: header badges green, a chart ticking, the
MomX board building, and `/api/schwab/status` showing valid tokens.
