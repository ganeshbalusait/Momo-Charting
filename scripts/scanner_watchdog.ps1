$ErrorActionPreference = "Stop"

# Some launchers inject both PATH and Path. Windows treats them as the same
# variable, but PowerShell Start-Process rejects the duplicate environment keys.
$processPath = [Environment]::GetEnvironmentVariable(
    "Path",
    [EnvironmentVariableTarget]::Process
)
if ($processPath) {
    [Environment]::SetEnvironmentVariable(
        "PATH",
        $null,
        [EnvironmentVariableTarget]::Process
    )
    [Environment]::SetEnvironmentVariable(
        "Path",
        $processPath,
        [EnvironmentVariableTarget]::Process
    )
}

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$python = Join-Path $root ".venv\Scripts\python.exe"
$backendLauncher = Join-Path $PSScriptRoot "start_api_background.ps1"
$powershell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
$vite = Join-Path $root "frontend\node_modules\vite\bin\vite.js"
$watchdogLog = Join-Path $artifacts "scanner_watchdog.log"
$backendProcess = $null
$frontendProcess = $null
$backendStartedAt = $null
$frontendStartedAt = $null
$backendHealthFailures = 0
$frontendHealthFailures = 0
$startupGraceSeconds = 300
$healthFailureLimit = 4

New-Item -ItemType Directory -Path $artifacts -Force | Out-Null

function Write-WatchdogLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $watchdogLog -Value "$timestamp $Message"
}

function Find-NodeRuntime {
    $candidates = [System.Collections.Generic.List[string]]::new()
    $nodeCommand = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($null -ne $nodeCommand -and $nodeCommand.Source) {
        $candidates.Add($nodeCommand.Source)
    }
    if ($env:ProgramFiles) {
        $candidates.Add((Join-Path $env:ProgramFiles "nodejs\node.exe"))
    }
    $candidates.Add("C:\Users\ganes\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    return $null
}

function Test-LocalHttp {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    }
    catch {
        return $false
    }
}

function Start-Backend {
    if ($null -ne $script:backendProcess -and -not $script:backendProcess.HasExited) {
        return
    }
    if (
        -not (Test-Path -LiteralPath $python) -or
        -not (Test-Path -LiteralPath $backendLauncher) -or
        -not (Test-Path -LiteralPath $powershell)
    ) {
        Write-WatchdogLog "Backend runtime or launcher is missing."
        return
    }
    $script:backendProcess = Start-Process `
        -FilePath $powershell `
        -ArgumentList "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", "`"$backendLauncher`"" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "api_server.out.log") `
        -RedirectStandardError (Join-Path $artifacts "api_server.err.log") `
        -PassThru
    $script:backendStartedAt = Get-Date
    Write-WatchdogLog "Started backend on port 3001."
}

function Start-Frontend {
    if ($null -ne $script:frontendProcess -and -not $script:frontendProcess.HasExited) {
        return
    }
    $node = Find-NodeRuntime
    if ($null -eq $node -or -not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $vite)) {
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
    $script:frontendStartedAt = Get-Date
    Write-WatchdogLog "Started frontend on port 5173."
}

function Restart-OwnedProcess {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$Name
    )
    if ($null -eq $Process -or $Process.HasExited) {
        return
    }
    Write-WatchdogLog "$Name failed repeated health checks; restarting owned process $($Process.Id)."
    Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
    Wait-Process -Id $Process.Id -Timeout 10 -ErrorAction SilentlyContinue
}

Write-WatchdogLog "Scanner watchdog started."
while ($true) {
    try {
        $backendHealthy = Test-LocalHttp -Uri "http://127.0.0.1:3001/api/health"
        if ($backendHealthy) {
            $backendHealthFailures = 0
        }
        else {
            $backendHealthFailures += 1
        }

        if (
            -not $backendHealthy -and
            ($null -eq $backendProcess -or $backendProcess.HasExited)
        ) {
            $backendProcess = $null
            Start-Backend
        }
        elseif (
            -not $backendHealthy -and
            $backendHealthFailures -ge $healthFailureLimit -and
            $null -ne $backendStartedAt -and
            ((Get-Date) - $backendStartedAt).TotalSeconds -ge $startupGraceSeconds
        ) {
            Restart-OwnedProcess -Process $backendProcess -Name "Backend"
            $backendProcess = $null
            $backendHealthFailures = 0
            Start-Backend
        }

        $frontendHealthy = Test-LocalHttp -Uri "http://127.0.0.1:5173/"
        if ($frontendHealthy) {
            $frontendHealthFailures = 0
        }
        else {
            $frontendHealthFailures += 1
        }

        if (
            -not $frontendHealthy -and
            ($null -eq $frontendProcess -or $frontendProcess.HasExited)
        ) {
            $frontendProcess = $null
            Start-Frontend
        }
        elseif (
            -not $frontendHealthy -and
            $frontendHealthFailures -ge $healthFailureLimit -and
            $null -ne $frontendStartedAt -and
            ((Get-Date) - $frontendStartedAt).TotalSeconds -ge $startupGraceSeconds
        ) {
            Restart-OwnedProcess -Process $frontendProcess -Name "Frontend"
            $frontendProcess = $null
            $frontendHealthFailures = 0
            Start-Frontend
        }
    }
    catch {
        Write-WatchdogLog "Watchdog check failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 15
}
