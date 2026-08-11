# One-click launcher for the trading dashboard.
#
# The user-facing URL is the Vite dev server on :5173 (user preference,
# 2026-08-09): frontend fixes appear immediately with no rebuild step. The
# backend on :3001 still runs underneath — Vite proxies /api to it — but is
# no longer the browsing URL. The 24/7 watchdog task keeps both alive; this
# script makes sure they are up right now and opens the app window.

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$distIndex = Join-Path $repoRoot "frontend\dist\index.html"
$appUrl = "http://127.0.0.1:5173/"

function Test-ApiUp {
    $c = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
    return $null -ne $c
}

function Test-FrontendUp {
    $c = Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue
    return $null -ne $c
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend runtime is missing: $python"
}

# 1. Production build. api_server.py serves whatever is in frontend/dist, so a
#    missing build would just 404 the app shell.
if (-not (Test-Path -LiteralPath $distIndex)) {
    Write-Host "Building frontend (first run)..."
    Push-Location (Join-Path $repoRoot "frontend")
    try {
        & npx vite build
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    }
    finally { Pop-Location }
}

# 2. Backend. start_api_background.ps1 holds a lock file, so a second launch
#    while the 24/7 task is running is a no-op rather than a port clash.
if (Test-ApiUp) {
    Write-Host "Backend already listening on :3001."
}
else {
    Write-Host "Starting backend..."
    $starter = Join-Path $PSScriptRoot "start_api_background.ps1"
    Start-Process -WindowStyle Hidden -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $starter)

    $deadline = (Get-Date).AddSeconds(60)
    while (-not (Test-ApiUp)) {
        if ((Get-Date) -gt $deadline) { throw "Backend did not come up on :3001 within 60s." }
        Start-Sleep -Milliseconds 500
    }
    Write-Host "Backend is up."
}

# 3. Frontend dev server. The watchdog task owns it long-term; kick the task
#    if :5173 is not listening yet so this launcher works even right after a
#    reboot where the task has not fired.
if (-not (Test-FrontendUp)) {
    Write-Host "Starting frontend via the 24/7 watchdog task..."
    & schtasks.exe /Run /TN "AgenticAI-Trading-24x7" 2>$null | Out-Null
    $deadline = (Get-Date).AddSeconds(90)
    while (-not (Test-FrontendUp)) {
        if ((Get-Date) -gt $deadline) { throw "Frontend did not come up on :5173 within 90s. Check artifacts\scanner_watchdog.log" }
        Start-Sleep -Milliseconds 750
    }
}
Write-Host "Frontend is up on :5173."

# 4. Open in an app window. --app gives a chrome-less window even before the
#    PWA is installed; once installed, the Start-menu shortcut does the same.
$browsers = @(
    "${env:ProgramFiles}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Google\Chrome\Application\chrome.exe",
    "${env:LocalAppData}\Google\Chrome\Application\chrome.exe",
    "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
    "${env:ProgramFiles}\Microsoft\Edge\Application\msedge.exe"
)
$browser = $browsers | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1

if ($browser) {
    Start-Process -FilePath $browser -ArgumentList @("--app=$appUrl")
}
else {
    Write-Warning "Chrome/Edge not found; opening in the default browser."
    Start-Process $appUrl
}
