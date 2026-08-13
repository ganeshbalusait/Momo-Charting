# app.agxtrade.com — tunnel + static server

Serves the trading app at `https://app.agxtrade.com` without changing `api_server.py`
and without restarting the backend.

## Why it is shaped this way

`:3001` is **API-only** and binds to `127.0.0.1` — it serves no frontend (`/`,
`/index.html` and `/dist` all return 404), so it cannot be tunnelled on its own.
`:5173` is the Vite **dev** server and is not a stable front door.

So a small static server publishes `frontend/dist`, and the tunnel joins the two
onto one hostname by path:

| Request | Goes to | Notes |
|---|---|---|
| `app.agxtrade.com/api/*` | `127.0.0.1:3001` | existing API, unchanged, stays loopback-only |
| `app.agxtrade.com/*` | `127.0.0.1:4173` | built frontend |
| `:5173` | not exposed | dev server, local only |

One origin means **no CORS changes and no backend restart**.

## Prerequisites

- your domain active in your Cloudflare account
- `cloudflared` installed (`winget install --id Cloudflare.cloudflared`)
- A current build: `npm run build` in `AgenticAI-Trading 2/frontend`

## Setup

Run once, in order. Steps 1–3 need your Cloudflare credentials.

```bash
cloudflared tunnel login
```

```bash
cloudflared tunnel create trading-app
```

That prints a tunnel ID and a credentials file path. Put that path into
`credentials-file` in `config.yml`, replacing `REPLACE-WITH-TUNNEL-ID.json`.

```bash
cloudflared tunnel route dns trading-app app.agxtrade.com
```

## Run

Two processes. Start the static server first:

```bash
powershell -ExecutionPolicy Bypass -File hosting/start-static.ps1
```

Then the tunnel:

```bash
cloudflared tunnel --config hosting/config.yml run trading-app
```

Verify locally before trusting the tunnel:

```bash
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:4173/
```

## Make it permanent

Once the tunnel works interactively, install it as a Windows service so it
starts on boot:

```bash
cloudflared --config hosting/config.yml service install
```

The static server needs the same treatment — Task Scheduler with "run at
startup", or NSSM — otherwise `app.agxtrade.com` returns 502 after a reboot while
the API keeps working.

## Add the Access gate

This is the part that produces the login screen, and the allow-list is what
decides who gets past it — adding someone's email here is exactly how another
operator would grant you access to their own app.

Do this **before** DNS propagates if you can. Until the policy exists, the
hostname is open to anyone who finds it.

### 1. Turn on Google sign-in (do this first)

**Zero Trust → Settings → Authentication → Login methods → Add new → Google.**

Without this, the gate only offers Cloudflare login and one-time email codes.
Adding Google is what puts a **"Continue with Google"** button on the login
screen. It is a dashboard setting — no code, and nothing in the app changes.

Cloudflare walks you through creating a Google OAuth client; the redirect URI it
asks you to paste into Google is shown on that same screen.

### 2. Create the application

**Zero Trust → Access → Applications → Add an application → Self-hosted.**

| Field | Value |
|---|---|
| Application name | `AGX` — this becomes the "Log in to …" text on the gate |
| Session duration | 30 days |
| Subdomain / domain | `app` / `agxtrade.com` |

### 3. Add the allow-list policy

| Field | Value |
|---|---|
| Policy name | `Owner` |
| Action | **Allow** |
| Include → selector | **Emails** |
| Value | your Gmail, plus any address you want to let in |

Each address you add here is one person who can reach the app. Removing an
address revokes them immediately — no app change, no redeploy.

### 4. Check it

Open `https://app.agxtrade.com` in a private window. You should be redirected to
`<your-team>.cloudflareaccess.com` and see the login screen with the Google
button. An address that is *not* on the list must be refused — verify that too,
because a policy that admits everyone looks identical to a working one until
someone tries.

## Known behaviour

- **Stale builds.** `app.agxtrade.com` serves `dist`, so changes appear only after
  `npm run build`. `npx vite build --watch` keeps it current. `start-static.ps1`
  warns when `dist` is older than `src`.
- **SSE.** `/api/live-market-stream` is `text/event-stream`. `config.yml` keeps
  chunked encoding on and raises keep-alives so the stream is not buffered or
  recycled. If events stop arriving but normal requests work, look here first.
- **Concurrency.** The watchlist opens up to 7 EventSources. Over local HTTP/1.1
  that bumps the ~6-connection-per-origin browser cap; through Cloudflare the
  browser gets HTTP/2 multiplexing, so this is one case where the tunnelled app
  behaves *better* than `localhost`.
