# Restore supervision of the app.agxtrade.com tunnel.
#
# Background (2026-08-13): the Cloudflared Windows service is configured to
# read C:\Windows\System32\config\systemprofile\.cloudflared\config.yml, but
# that file did not exist. The service therefore crash-looped (~1 start per
# 20s, 88 starts in 30 minutes) while the tunnel was actually served by a
# MANUALLY started cloudflared that nothing supervises. Because the
# crash-looping instance carries no ingress rules, requests routed to it
# matched only the final "http_status:404" catch-all - which is why
# app.agxtrade.com intermittently returned 404 and 5xx while :5173 was fine.
#
# This script gives the service the real config, starts it, verifies it, and
# only then retires the manual process - so there is no window where the
# tunnel is down. If the service fails to start, the manual tunnel is left
# running and the site stays up.
#
# Run elevated. Safe to re-run.

$ErrorActionPreference = "Stop"

function Say([string]$m) { Write-Host $m }

$userConfig = "C:\Users\ganes\.cloudflared\config.yml"
$systemDir  = "C:\Windows\System32\config\systemprofile\.cloudflared"
$systemConfig = Join-Path $systemDir "config.yml"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Say "ERROR: this window is not elevated. Re-run as Administrator."
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Test-Path -LiteralPath $userConfig)) {
    Say "ERROR: source config not found at $userConfig"
    Read-Host "Press Enter to close"
    exit 1
}

Say "1/5  Copying ingress config to the path the service reads..."
New-Item -ItemType Directory -Force $systemDir | Out-Null
Copy-Item $userConfig $systemConfig -Force
Say "     $systemConfig exists: $(Test-Path -LiteralPath $systemConfig)"

# Record which cloudflared processes exist BEFORE starting the service, so the
# manual one can be retired without guessing at a PID that may have changed.
$before = @(Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
Say "2/5  cloudflared processes before: $($before -join ', ')"

Say "3/5  Enabling and starting the Cloudflared service..."
Set-Service Cloudflared -StartupType Automatic
try { Start-Service Cloudflared } catch { Say "     Start-Service reported: $($_.Exception.Message)" }
Start-Sleep -Seconds 10

$svc = Get-Service Cloudflared
Say "     service status: $($svc.Status)  startup: $((Get-CimInstance Win32_Service -Filter "Name='Cloudflared'").StartMode)"

if ($svc.Status -ne "Running") {
    Say ""
    Say "SERVICE DID NOT START. Leaving the manual tunnel running so the site stays up."
    Say "Nothing was broken - re-run after checking the Application event log."
    Read-Host "Press Enter to close"
    exit 1
}

Say "4/5  Service is running. Retiring the unsupervised manual tunnel..."
$after = @(Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id)
$manual = $before | Where-Object { $after -contains $_ }
foreach ($procId in $manual) {
    try { Stop-Process -Id $procId -Force -ErrorAction Stop; Say "     stopped manual cloudflared pid $procId" }
    catch { Say "     could not stop pid ${procId}: $($_.Exception.Message)" }
}

Start-Sleep -Seconds 5
Say "5/5  Verifying..."
$svc = Get-Service Cloudflared
Say "     service status: $($svc.Status)"
Say "     cloudflared processes now: $((Get-Process cloudflared -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id) -join ', ')"
try {
    $code = (Invoke-WebRequest -Uri "https://app.agxtrade.com/" -UseBasicParsing -TimeoutSec 20 -MaximumRedirection 0 -ErrorAction Stop).StatusCode
} catch { $code = $_.Exception.Response.StatusCode.value__ }
Say "     app.agxtrade.com returned: $code   (302 = Cloudflare Access login, which is CORRECT for a non-browser request)"
Say ""
Say "Done. The tunnel is now supervised by the service and will restart itself."
Read-Host "Press Enter to close"
