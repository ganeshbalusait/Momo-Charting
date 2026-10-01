# Move AGX to a new laptop

Who this is for: **Ganesh, with no developer**. Every command below was
checked against the running machine on 2026-09-04. Copy-paste them in order.

The one idea to hold on to: **git carries the code, and only the code.**
Your keys, your Schwab logins, and the 1.1 GB trade journal are deliberately
kept OUT of git (they are secrets and data, not code). So a new laptop is
always two moves: *clone the code* + *copy five private things by hand*.

```
 git carries (code)                 you copy by hand (private)
 ------------------                 --------------------------
 api_server.py, momx/, frontend/    .env                       <- every broker/API key
 scripts/, docs/, tests/            database/trades.db         <- journal, users, settings, lists
 requirements.txt, watchlist.txt    artifacts/schwab_token.json
                                    artifacts/schwab_trading_token.json
                                    artifacts/user_credentials.key
                                    artifacts/user_tokens/  (whole folder)
```

Everything under "by hand" except `user_credentials.key` and `user_tokens/`
is already in the nightly **secrets-YYYYMMDD.zip** and **trades-YYYYMMDD.db.zip**
in Google Drive → AGX-Backups (see `docs/RUNBOOK.md`). If the old laptop is
alive, copying straight from it is simpler.

---

## Part A — on the OLD laptop (10 minutes)

1. Make sure every code change is committed:

       cd "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2"
       git status --short

   Lines starting with ` M` are edits not yet committed. Commit them
   (`git add <file>` then `git commit -m "..."`). Lines starting with `??`
   under `artifacts/` are scratch files - ignore them.

2. Put the code on a USB stick as a **git bundle** (one file, the whole
   history, no secrets - the same thing the nightly backup makes):

       git bundle create E:\agx-code.bundle --all

   (`E:` = your USB drive letter.)

3. Copy the five private things to the same stick. In PowerShell:

       $src = "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2"
       New-Item -ItemType Directory -Force E:\agx-private | Out-Null
       Copy-Item "$src\.env"                                  E:\agx-private\
       Copy-Item "$src\database\trades.db"                    E:\agx-private\
       Copy-Item "$src\artifacts\schwab_token.json"           E:\agx-private\
       Copy-Item "$src\artifacts\schwab_trading_token.json"   E:\agx-private\
       Copy-Item "$src\artifacts\user_credentials.key"        E:\agx-private\
       Copy-Item "$src\artifacts\user_tokens"                 E:\agx-private\user_tokens -Recurse
       Copy-Item "$env:USERPROFILE\.cloudflared"              E:\agx-private\cloudflared -Recurse

   The last line is only needed if the new laptop should also serve
   **app.agxtrade.com** (the public address). Skip it for a home-only copy.

   Do this **after market close** so `trades.db` is not being written to
   while it copies. Guard the stick like a bank card - it now holds every key.

---

## Part B — on the NEW laptop (about 30 minutes, most of it waiting)

### B1. Install the two runtimes (once)

- **Python 3.12** - https://www.python.org/downloads/ → the 3.12.x installer.
  Tick **"Add python.exe to PATH"** on the first screen.
- **Node.js 24 LTS** - https://nodejs.org → the LTS installer, defaults are fine.
- **Git for Windows** - https://git-scm.com/download/win, defaults are fine.

Check (open a new PowerShell after installing):

    python --version     # Python 3.12.x
    node --version       # v24.x
    git --version

### B2. Get the code

    New-Item -ItemType Directory -Force "C:\GANESH\AgenticAI-Trading 7" | Out-Null
    cd "C:\GANESH\AgenticAI-Trading 7"
    git clone E:\agx-code.bundle "AgenticAI-Trading 2"
    cd "AgenticAI-Trading 2"
    git checkout main
    git log --oneline -3     # should show the same latest commit as the old laptop

Keep the same folder path. The scheduled tasks in B5 and several scripts
use it, and matching the old machine means every doc still applies.

### B3. Put the private things back

    $dst = "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2"
    New-Item -ItemType Directory -Force "$dst\database", "$dst\artifacts" | Out-Null
    Copy-Item E:\agx-private\.env                         "$dst\.env"
    Copy-Item E:\agx-private\trades.db                    "$dst\database\trades.db"
    Copy-Item E:\agx-private\schwab_token.json            "$dst\artifacts\"
    Copy-Item E:\agx-private\schwab_trading_token.json    "$dst\artifacts\"
    Copy-Item E:\agx-private\user_credentials.key         "$dst\artifacts\"
    Copy-Item E:\agx-private\user_tokens                  "$dst\artifacts\user_tokens" -Recurse

If you restored from Google Drive instead of the stick: unzip
`secrets-YYYYMMDD.zip` into the repo root (it lands `.env`, the two token
files and `watchlist.txt` in the right places) and unzip
`trades-YYYYMMDD.db.zip` to `database\trades.db`.

### B4. Install the app's packages

    cd "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2"
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt

    cd frontend
    npm install
    npm run build
    cd ..

`pip install` takes a few minutes; `npm install` too. `npm run build` is
about a minute and writes `frontend\dist`.

### B5. First start and check

    powershell -ExecutionPolicy Bypass -File .\scripts\start_app.ps1

A cold start is slow - up to **3 minutes** before the port opens - the
script waits for it. It then opens http://127.0.0.1:5173/ in the browser.

Check the header lamps: **TOS MARKET** and **TOS TRADING** should be green
with a days-left count, **ALPACA** green during market hours. If a Schwab
lamp is red, open **Settings → Schwab** and re-authenticate that app: the
copied token files are tied to a 7-day login and may simply have run out.

Then run the tests once - they need no market data and prove the copy is
whole:

    .\.venv\Scripts\python.exe -m pytest tests -q -x
    cd frontend; npx vitest run; cd ..

### B6. Make it start by itself (the 24/7 setup)

Three scheduled tasks keep the app alive on the old laptop. Recreate them:

    # 1. The main stack (api_server, gateway, frontend, watchdog) at logon
    powershell -ExecutionPolicy Bypass -File .\scripts\install_24x7_task.ps1

    # 2. The keeper: restarts anything that died, every 10 minutes
    $repo   = "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2"
    $py     = "$repo\.venv\Scripts\python.exe"
    $action = New-ScheduledTaskAction -Execute $py -Argument "-u `"$repo\scripts\agx_keeper.py`"" -WorkingDirectory $repo
    $trig   = @(
        (New-ScheduledTaskTrigger -AtLogOn),
        (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10))
    )
    Register-ScheduledTask -TaskName "AGX-Keeper-24x7" -Action $action -Trigger $trig -Force | Out-Null

    # 3. The nightly backup to Google Drive, 1:30 AM and 3:45 PM local (after close)
    $action = New-ScheduledTaskAction -Execute $py -Argument "`"$repo\scripts\backup_agx.py`"" -WorkingDirectory $repo
    $trig   = @(
        (New-ScheduledTaskTrigger -Daily -At 01:30),
        (New-ScheduledTaskTrigger -Daily -At 15:45)
    )
    Register-ScheduledTask -TaskName "AGX-Nightly-Backup" -Action $action -Trigger $trig -Force | Out-Null

    Get-ScheduledTask AgenticAI-Trading-24x7, AGX-Keeper-24x7, AGX-Nightly-Backup | Select TaskName, State

The backup task also needs **Google Drive for Desktop** installed and signed
in, or it quietly saves to `C:\AGX-Backups` instead (it says so in its log).

Also set Windows so the laptop does not sleep when plugged in:
Settings → System → Power → *Screen, sleep & hibernate timeouts* → sleep
"Never" when plugged in.

### B7. Only if this laptop should serve app.agxtrade.com

1. Install cloudflared: https://github.com/cloudflare/cloudflared/releases
   (the Windows `.msi`).
2. Put the copied tunnel files where the service reads them:

       New-Item -ItemType Directory -Force "$env:USERPROFILE\.cloudflared" | Out-Null
       Copy-Item E:\agx-private\cloudflared\* "$env:USERPROFILE\.cloudflared\" -Recurse -Force

3. Install the Windows service, then let the repo's script give it the real
   config (a freshly installed service reads a config path that does not
   exist yet, so on its own it crash-loops every 20 s - the script fixes
   exactly that). Both need an **Administrator** PowerShell:

       cloudflared service install
       powershell -ExecutionPolicy Bypass -File .\scripts\fix_cloudflared_service.ps1

   The script looks for the config at `C:\Users\ganes\.cloudflared` - if
   the new laptop's Windows username is different, edit line 23 of the
   script first.

4. **Turn the tunnel OFF on the old laptop first** (`Stop-Service cloudflared`
   there, then `sc.exe config cloudflared start= disabled`). Two machines
   running the same tunnel fight each other and the site flickers.

Cloudflare Access (the login gate in front of the site) lives in the
Cloudflare dashboard, not on the laptop - nothing to move.

---

## Part C — what will NOT carry over, and why that is fine

| Thing | What you see | What to do |
|---|---|---|
| Chart / OI / study caches (`artifacts/`, ~1.5 GB) | Charts open slowly the first hour; MomX first build is cold (~3 min) | Nothing - it rebuilds itself |
| Schwab 7-day logins | A red TOS lamp | Settings → re-authenticate (phone on mobile data works if the PC gets "Access Denied") |
| Old laptop's Schwab callback (`https://127.0.0.1/`) | Nothing - it is the same address on every machine | Nothing |
| Phone alerts (ntfy) | Keep working - the topic is in `trades.db`, which you copied | Nothing |
| The AGX desktop icon / installed PWA | Gone | Open http://127.0.0.1:5173/ and install it again from the browser menu |
| The 24/7 tasks on the OLD laptop | Still running there | Fine for a home-only copy. Disable them (`Disable-ScheduledTask`) if the old laptop is being retired, so two servers never both push phone alerts |

## If something is wrong

`docs/RUNBOOK.md` → "If something looks down" is the same on the new machine.
The fastest single check:

    Get-NetTCPConnection -LocalPort 3001,3002,3010,5173,4173 -State Listen | Select LocalPort

Five lines = everything is up. Fewer = read `artifacts\api_server.err.log`
(the last 30 lines say why).
