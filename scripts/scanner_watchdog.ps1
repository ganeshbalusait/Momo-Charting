$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$python = Join-Path $root ".venv\Scripts\python.exe"
$node = "C:\Users\ganes\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$vite = Join-Path $root "frontend\node_modules\vite\bin\vite.js"
$watchdogLog = Join-Path $artifacts "scanner_watchdog.log"
$backendProcess = $null
$frontendProcess = $null

New-Item -ItemType Directory -Path $artifacts -Force | Out-Null

# Single instance. Two watchdogs racing each other (or a watchdog racing a
# session's manual restart) produced FOUR duplicate api_server pairs on
# 2026-08-10; one of those pairs concurrently refreshed the Schwab trading
# token and revoked it (Schwab rotates the refresh token on every refresh).
$lockPath = Join-Path $artifacts "scanner_watchdog.lock"
try {
    $script:lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch {
    Write-Host "Another scanner watchdog already holds $lockPath; exiting."
    exit 0
}

function Write-WatchdogLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $watchdogLog -Value "$timestamp $Message"
}

function Test-LocalPort {
    param([int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync("127.0.0.1", $Port)
        return $connection.Wait(750) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-ApiServerProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*api_server.py*" })
}

function Start-Backend {
    if ($null -ne $script:backendProcess -and -not $script:backendProcess.HasExited) {
        return
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-WatchdogLog "Backend runtime missing: $python"
        return
    }
    $script:backendProcess = Start-Process `
        -FilePath $python `
        -ArgumentList "-u", "api_server.py" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "api_server.out.log") `
        -RedirectStandardError (Join-Path $artifacts "api_server.err.log") `
        -PassThru
    Write-WatchdogLog "Started backend on port 3001."
}

function Start-Frontend {
    if ($null -ne $script:frontendProcess -and -not $script:frontendProcess.HasExited) {
        return
    }
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $vite)) {
        Write-WatchdogLog "Frontend runtime or Vite entrypoint is missing."
        return
    }
    $script:frontendProcess = Start-Process `
        -FilePath $node `
        -ArgumentList $vite, "--host", "0.0.0.0", "--port", "5173", "--strictPort" `
        -WorkingDirectory (Join-Path $root "frontend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "frontend.out.log") `
        -RedirectStandardError (Join-Path $artifacts "frontend.err.log") `
        -PassThru
    Write-WatchdogLog "Started frontend on port 5173."
}

# Server startup takes 60-80s on this box, during which port 3001 is closed.
# Restarting whenever the port is closed therefore spawned a SECOND backend
# beside every legitimately starting one. A backend process younger than this
# window is presumed to be booting and is left alone; one older than this
# with the port still closed is wedged and gets replaced.
$backendStartupGraceSeconds = 180

while ($true) {
    try {
        if (-not (Test-LocalPort -Port 3001)) {
            $existing = Get-ApiServerProcesses
            if ($existing.Count -eq 0) {
                Write-WatchdogLog "Port 3001 closed and no api_server process exists; starting backend."
                Start-Backend
            }
            else {
                $newest = ($existing | Sort-Object CreationDate -Descending | Select-Object -First 1)
                $ageSeconds = [Math]::Round(((Get-Date) - $newest.CreationDate).TotalSeconds)
                if ($ageSeconds -lt $backendStartupGraceSeconds) {
                    Write-WatchdogLog "Port 3001 closed but api_server pid $($newest.ProcessId) is ${ageSeconds}s old (starting up); waiting."
                }
                else {
                    Write-WatchdogLog "Port 3001 closed and api_server pid $($newest.ProcessId) is ${ageSeconds}s old (wedged); replacing."
                    $existing | ForEach-Object {
                        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
                    }
                    Start-Sleep -Seconds 2
                    Start-Backend
                }
            }
        }
        if (-not (Test-LocalPort -Port 5173)) {
            Start-Frontend
        }
    }
    catch {
        Write-WatchdogLog "Watchdog check failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 15
}
