# AGX health check - read-only, safe to run any time, prints plain language.
#
# Right-click -> "Run with PowerShell", or from a PowerShell window:
#   powershell -ExecutionPolicy Bypass -File "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\scripts\check_health.ps1"
#
# It changes nothing. It tells you WHICH part is down and WHAT to run next.

$ErrorActionPreference = "SilentlyContinue"
$repo = Split-Path -Parent $PSScriptRoot

function Line($ok, $what, $next) {
    $mark = if ($ok) { "OK  " } else { "DOWN" }
    Write-Host ("  [{0}] {1}" -f $mark, $what)
    if (-not $ok -and $next) { Write-Host ("         -> {0}" -f $next) -ForegroundColor Yellow }
}

Write-Host ""
Write-Host "AGX health  $(Get-Date -Format 'ddd yyyy-MM-dd HH:mm') (this PC's clock)"
Write-Host "-------------------------------------------------------------"

# 1. Ports: what is listening.
$ports = @{
    3001 = "gateway (the address the browser and the tunnel talk to)"
    3002 = "app server (charts, data, alerts)"
    3010 = "MomX scanner worker"
    5173 = "web app - development server (the one you browse)"
    4173 = "web app - built copy (what app.agxtrade.com serves)"
}
$listening = Get-NetTCPConnection -State Listen | Select-Object -ExpandProperty LocalPort -Unique
$anyDown = $false
foreach ($p in 3001, 3002, 3010, 5173, 4173) {
    $up = $listening -contains $p
    if (-not $up) { $anyDown = $true }
    Line $up ("port {0}  {1}" -f $p, $ports[$p]) $null
}
if ($anyDown) {
    Write-Host "         -> Run:  Start-ScheduledTask 'AgenticAI-Trading-24x7'   then wait 2-3 minutes and run this check again." -ForegroundColor Yellow
}

# 2. The three scheduled tasks that keep it alive.
Write-Host ""
foreach ($name in "AgenticAI-Trading-24x7", "AGX-Keeper-24x7", "AGX-Nightly-Backup") {
    $task = Get-ScheduledTask -TaskName $name
    if ($null -eq $task) {
        Line $false ("scheduled task {0} is MISSING" -f $name) "See docs\MOVE_TO_NEW_LAPTOP.md part B6 to recreate it."
        continue
    }
    $info = Get-ScheduledTaskInfo -TaskName $name
    $ok = $task.State -ne "Disabled"
    $when = if ($info.LastRunTime -and $info.LastRunTime.Year -gt 2000) { $info.LastRunTime.ToString("MM-dd HH:mm") } else { "never" }
    $result = switch ($info.LastTaskResult) { 0 { "finished fine" } 267009 { "still running (normal for the 24x7 task)" } default { "exit code $_" } }
    Line $ok ("task {0}: {1}, last run {2}, {3}" -f $name, $task.State, $when, $result) ("Run:  Enable-ScheduledTask '{0}'" -f $name)
}

# 3. The public tunnel.
Write-Host ""
$svc = Get-Service cloudflared
if ($null -eq $svc) {
    Line $false "cloudflared service not installed (app.agxtrade.com will not work; the PC address still does)" "docs\MOVE_TO_NEW_LAPTOP.md part B7"
} else {
    Line ($svc.Status -eq "Running") ("cloudflared tunnel service: {0}" -f $svc.Status) "In an ADMIN PowerShell run:  Restart-Service cloudflared"
}

# 4. Backups.
Write-Host ""
$drive = Get-Process GoogleDriveFS
Line ($null -ne $drive) "Google Drive for Desktop is running" "Start Google Drive from the Start menu (backups go to C:\AGX-Backups until it is back)."
$backupDir = @("G:\My Drive\AGX-Backups", "$env:USERPROFILE\My Drive\AGX-Backups", "C:\AGX-Backups") | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($backupDir) {
    $newest = Get-ChildItem $backupDir -Filter "trades-*.db.zip" | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $age = if ($newest) { [int]((Get-Date) - $newest.LastWriteTime).TotalHours } else { 9999 }
    Line ($age -le 36) ("newest journal backup in {0}: {1} ({2} hours old)" -f $backupDir, $(if ($newest) { $newest.Name } else { "none" }), $age) "Run:  Start-ScheduledTask 'AGX-Nightly-Backup'   then check again in 5 minutes."
} else {
    Line $false "no backup folder found" "Install Google Drive for Desktop and sign in."
}

# 5. Schwab logins - the weekly chore.
Write-Host ""
try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:3002/api/schwab/status" -TimeoutSec 5
    foreach ($pair in @(@("marketData", "TOS MARKET (charts, quotes, chains)"), @("trading", "TOS TRADING (live ticks)"))) {
        $s = $status.($pair[0])
        if ($null -eq $s) { continue }
        $hours = [math]::Floor($s.refreshTokenRemainingSeconds / 3600)
        $ok = [bool]$s.refreshTokenValid -and $hours -gt 24
        $msg = if ([bool]$s.refreshTokenValid) { "{0}: login valid, {1} days {2} hours left" -f $pair[1], [math]::Floor($hours / 24), ($hours % 24) } else { "{0}: login EXPIRED" -f $pair[1] }
        Line $ok $msg "Open the app -> Settings -> Schwab -> Authenticate. (Phone on mobile data if the PC gets 'Access Denied'.)"
    }
} catch {
    Line $false "could not ask the app server about Schwab (it is down or still starting)" "Fix the ports above first."
}

# 6. Last errors, so you can read (or paste) them.
Write-Host ""
$err = Join-Path $repo "artifacts\api_server.err.log"
if (Test-Path $err) {
    Write-Host "Last 15 lines of the app server's error log ($err):"
    Get-Content $err -Tail 15 | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor DarkGray }
}
Write-Host ""
Write-Host "Nothing above was changed. Full instructions: docs\RUNBOOK.md"
Write-Host ""
