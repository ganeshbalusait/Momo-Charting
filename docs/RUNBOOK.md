# Running AGX yourself — no developer needed

The app runs entirely on this laptop through Windows scheduled tasks. Nothing
about day-to-day operation depends on Claude or any developer. This page is
everything the operator (Ganesh) needs.

## Starting the app

**Turn the laptop on and log in to Windows. That's it.**

At logon these start on their own:

| what | how it starts | job |
|---|---|---|
| `AgenticAI-Trading-24x7` | scheduled task, at logon | api_server, gateway, frontend, watchdog |
| `AGX-Keeper-24x7` | scheduled task, logon + every 10 min | restarts anything that died (the 10-min repeat IS the restart) |
| `cloudflared` | Windows service, automatic | the tunnel that makes app.agxtrade.com reachable |
| MomX worker + watchdog | spawned by the stack above | the scanner on :3010 |
| `AGX-Nightly-Backup` | scheduled task, 1:30 AM + 4:45 PM ET (after close) | journal + secrets + code → Google Drive, 7-day retention |

Give it ~2 minutes after login, then open **app.agxtrade.com** — header lamps
(TOS MARKET / TOS TRADING / ALPACA) go green during market sessions. Each
Schwab lamp shows the days left on its login; it blinks amber under two days.

## Reading this document

File Explorer -> `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\docs`
-> right-click `RUNBOOK.md` -> Open with -> Notepad.

## If something looks down

**Step 0 - find out what is down (changes nothing).** Click Start, type
`powershell`, press Enter, then paste:

    powershell -ExecutionPolicy Bypass -File "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\scripts\check_health.ps1"

It prints one OK/DOWN line per part (ports, the three scheduled tasks, the
tunnel, backups, both Schwab logins) and, under every DOWN line, the exact
command to run next. Everything green and the app still looks wrong? Reload
the browser tab (Ctrl+F5) - a phone tab left open keeps an old copy.

**Step 1.** One command fixes almost everything. Click Start, type `powershell`, press
Enter, then paste:

    Start-ScheduledTask 'AgenticAI-Trading-24x7'

Wait 2 minutes, reload the app.

**Deepest fallback** - if even the task system is broken, run the supervisor
directly and leave the window open (it is the thing that starts and heals
every process):

    powershell -ExecutionPolicy Bypass -File "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\scripts\scanner_watchdog.ps1" 

## Reading the symptoms

| what you see | what it means | what to do |
|---|---|---|
| "Unable to sign in" on the login page | the backend is down, NOT a password problem | the PowerShell command above |
| Error 1033 at app.agxtrade.com | tunnel disconnected | `Restart-Service cloudflared` in an admin PowerShell |
| Charts load but stop ticking, or a TOS lamp reads EXPIRED | the weekly Schwab login expired | Settings → **Authenticate Market Data** (or Trading) → approve → paste the address back. The lamp blinks from 2 days out and your phone gets a push 1 day out. If the PC gets "Access Denied" from Schwab, log in on the phone over mobile data and paste the address on the PC |
| ALPACA badge red on a weekend | normal — nothing to stream while closed | nothing |
| Board shows "DATA AS OF FRI …" | market is closed; data is from the last session | nothing |
| No phone Momo alerts | check the ntfy app is installed and subscribed to your topic (gear → Phone push) | resubscribe in ntfy |

## The weekly ritual (the only recurring chore)

Roughly once a week Schwab expires its connection: Settings → the Schwab card
→ **Authenticate Market Data** → log in → Allow → copy the 127.0.0.1 address →
paste it into **MARKET DATA CALLBACK URL** → **Complete**. Two minutes.
Full walkthrough with a photograph: the app's **Learn + Setup → Setup** tab.

## How Google Drive and the backups relate

Think of Drive as a shared mailbox in the sky. It works in two directions,
and only one of them is automatic everywhere:

- **Reading the mailbox - any laptop, instantly.** Install Google Drive,
  sign in, and `AGX-Backups` appears with every backup in it. That is
  literally step 1 of recovery on a new machine.
- **Putting new mail in - only the laptop running the app.** The backup is
  not a Google feature; it is the trading app photographing its own journal,
  keys and code twice a day and dropping the copies into the mailbox. A fresh
  laptop with only Drive installed has nothing to photograph yet.

One sentence to remember: **Drive carries the backups; the app creates
them.** Wherever the app lives is where backing-up happens.

New-laptop sequence:

1. Install Google Drive and sign in - all backups appear (the mailbox opens)
2. Restore the app from those files (the section below)
3. From then on the new laptop puts new backups in automatically, same as
   the old one did

Optional extra safety, zero configuration: install Google Drive on any
second computer and it passively holds copies of every backup - one more
place the data survives.

## Disaster recovery (new laptop / dead disk)

Step-by-step commands for a planned move: **`docs/MOVE_TO_NEW_LAPTOP.md`**.

Everything needed lives in **Google Drive → AGX-Backups** (refreshed nightly):

- `code-YYYYMMDD.bundle` — the entire app, every version ever:
  `git clone code-YYYYMMDD.bundle agx` restores the code
- `trades-YYYYMMDD.db.zip` — the trade journal (unzip → `database/trades.db`)
- `secrets-YYYYMMDD.zip` — `.env` with every key, Schwab tokens, watchlist,
  and the Cloudflare tunnel credentials (restore those into the new machine's
  `%USERPROFILE%\.cloudflared\` and app.agxtrade.com points at it again)

Rebuild steps: install Python 3.12 + Node, clone the bundle, restore the two
zips into place, `pip install -r requirements.txt`, `npm install` + `npm run
build` in `frontend/`, then follow `docs/SETUP.md` for the scheduled tasks and
tunnel. A competent IT person (or any AI assistant, given this repo) can do it
from those files alone.

## What stops without a developer

Nothing operational. What you lose is the ability to CHANGE things — new
features, new fixes. The app as it stands keeps scanning, alerting, briefing
and backing up indefinitely. The only external dependencies are the free data
services (Schwab, Alpaca, ntfy, Google) — and if the AI key ever lapses, AI
commentary switches off politely while everything deterministic keeps running.
