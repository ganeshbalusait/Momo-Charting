# A1 cutover: gateway takes :3001, pipeline moves to :3002.
# Spec: docs/superpowers/specs/2026-08-19-process-split-design.md
# Run AFTER the close. Rollback: .\scripts\cutover_a1.ps1 -Rollback
#
# What it deliberately does NOT do:
# - touch cloudflared or vite configs (public topology unchanged, :3001 stays
#   the front door - that is the whole point of the design)
# - edit the watchdog. The running watchdog probes :3001, which the gateway
#   answers, so it stays calm; teaching it to supervise BOTH processes is the
#   follow-up edit in this same file's checklist, done while watching it.
param([switch]$Rollback)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"

function Stop-AgxProcesses {
    $procs = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -like "*api_server.py*" -or $_.CommandLine -like "*gateway.py*" }
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
    }
    Start-Sleep -Seconds 2
}

function Wait-Port200([string]$url, [int]$tries = 90) {
    for ($i = 0; $i -lt $tries; $i++) {
        try {
            $r = Invoke-WebRequest -Uri $url -TimeoutSec 3 -UseBasicParsing
            if ($r.StatusCode -eq 200) { return $true }
        } catch {}
        Start-Sleep -Seconds 2
    }
    return $false
}

Stop-AgxProcesses

if ($Rollback) {
    # Pipeline back on :3001, no gateway. Exactly yesterday's topology.
    $env:AGX_PORT = "3001"
    Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList "-u", "api_server.py" -WorkingDirectory $root
    if (Wait-Port200 "http://127.0.0.1:3001/api/health") {
        Write-Output "ROLLBACK OK: pipeline serving :3001 directly."
    } else {
        Write-Output "ROLLBACK: pipeline did not answer on :3001 - check artifacts/api_server logs."
    }
    exit
}

# 1. pipeline on :3002
$env:AGX_PORT = "3002"
Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList "-u", "api_server.py" -WorkingDirectory $root
if (-not (Wait-Port200 "http://127.0.0.1:3002/api/health")) {
    Write-Output "CUTOVER FAILED: pipeline never answered on :3002. Rolling back."
    Stop-AgxProcesses
    $env:AGX_PORT = "3001"
    Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList "-u", "api_server.py" -WorkingDirectory $root
    exit 1
}

# 2. gateway on :3001 -> :3002
Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList "gateway.py", "--port", "3001", "--pipeline", "http://127.0.0.1:3002" -WorkingDirectory $root
if (Wait-Port200 "http://127.0.0.1:3001/api/gateway-health") {
    Write-Output "CUTOVER OK: gateway :3001 -> pipeline :3002."
    Write-Output "Verify: charts through :3001, SSE in the browser, one alert mutation."
    Write-Output "Then: update scanner_watchdog.ps1 to supervise both, restart watchdog."
} else {
    Write-Output "CUTOVER FAILED: gateway never answered. Rolling back."
    Stop-AgxProcesses
    $env:AGX_PORT = "3001"
    Start-Process -WindowStyle Hidden -FilePath $python -ArgumentList "-u", "api_server.py" -WorkingDirectory $root
    exit 1
}
