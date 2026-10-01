# Windows and macOS Startup Guide

The trading app does not require a ChatGPT subscription to run. The broker connections, backend, and local dashboard operate independently.

## Windows

### Normal startup

After starting Windows:

1. Sign in to your Windows account.
2. Wait approximately 30-60 seconds. The `AgenticAI-Trading-24x7` task should start automatically.
3. Open the dashboard at [http://127.0.0.1:5173](http://127.0.0.1:5173).

### Start it manually

Open PowerShell and run:

```powershell
Set-Location 'C:\GANESH\AgenticAI-Trading 2\AgenticAI-Trading 2'
Start-ScheduledTask -TaskName 'AgenticAI-Trading-24x7'
```

Check backend health:

```powershell
Invoke-RestMethod 'http://127.0.0.1:3001/api/health'
```

Look for `"ok": true` and `"status": "Healthy"`.

### If the scheduled task is unavailable

Run the watchdog directly:

```powershell
Set-Location 'C:\GANESH\AgenticAI-Trading 2\AgenticAI-Trading 2'
powershell -NoProfile -ExecutionPolicy Bypass -File '.\scripts\scanner_watchdog.ps1'
```

Keep that PowerShell window open. It starts and monitors:

- Dashboard: [http://127.0.0.1:5173](http://127.0.0.1:5173)
- Backend: [http://127.0.0.1:3001](http://127.0.0.1:3001)
- Backend health: [http://127.0.0.1:3001/api/health](http://127.0.0.1:3001/api/health)

### First-time recovery setup

If the virtual environment or frontend dependencies are missing:

```powershell
Set-Location 'C:\GANESH\AgenticAI-Trading 2\AgenticAI-Trading 2'
.\scripts\run_on_any_machine.ps1
.\scripts\install_24x7_task.ps1
```

Your broker/API credentials must remain in `.env`. For safety, keep:

```env
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
```

A ChatGPT subscription and OpenAI API billing are separate. The app does not need either unless an external OpenAI-powered feature is explicitly enabled.

## macOS

### One-time MacBook setup

Install Python 3 and Node.js, then open Terminal and go to the project:

```bash
cd "/path/to/AgenticAI-Trading 2"

python3 -m venv .venv
source .venv/bin/activate
python scripts/bootstrap.py
```

Update `.env` with the required broker credentials. Keep paper-trading safety enabled unless live trading has been deliberately reviewed and authorized:

```env
EXECUTION_MODE=paper
ALLOW_LIVE_TRADING=false
```

Do not reuse the Windows `.venv` or `frontend/node_modules` folders on macOS. Recreate them because Python and Node dependencies can contain operating-system-specific files.

### Start the app manually

Open the first Terminal window and start the backend:

```bash
cd "/path/to/AgenticAI-Trading 2"
source .venv/bin/activate
python -u api_server.py
```

Open a second Terminal window and start the frontend:

```bash
cd "/path/to/AgenticAI-Trading 2/frontend"
npm install
npm run dev
```

Open the dashboard:

- Dashboard: [http://127.0.0.1:5173](http://127.0.0.1:5173)
- Backend health: [http://127.0.0.1:3001/api/health](http://127.0.0.1:3001/api/health)

Keep both Terminal windows open while the app is running.

### Prevent the MacBook from sleeping

When starting the backend, use `caffeinate` to keep the Mac awake:

```bash
cd "/path/to/AgenticAI-Trading 2"
caffeinate -dimsu .venv/bin/python -u api_server.py
```

The MacBook must remain powered, connected to the internet, and awake for continuous operation.

### Automatic startup

The project includes a production `launchd` LaunchAgent. It serves the built React frontend and Python API from one supervised process, starts after macOS login, restarts after crashes, and prevents system sleep while connected to AC power.

Install it once:

```bash
cd "/path/to/AgenticAI-Trading 2/frontend"
npm run build
cd ..
./scripts/install_macos_service.sh
```

Then use [http://127.0.0.1:3001](http://127.0.0.1:3001). Bookmark this address. Port `5173` is only the Vite development server and is not supervised or intended as the 24/7 production URL.

For the one-time setup, manual start/restart commands, foreground debugging, and log locations, see [RUN_24_7.md](RUN_24_7.md).

Check the service and health:

```bash
launchctl print "gui/$(id -u)/com.agenticai.trading"
curl http://127.0.0.1:3001/api/health
```

Logs are written to `artifacts/logs/production.out.log` and `artifacts/logs/production.error.log`.

This local service runs only while the Mac is powered on and the user is logged in. A truly public 24/7 site that survives Mac shutdowns requires deployment to an always-on cloud host with HTTPS and persistent storage.
