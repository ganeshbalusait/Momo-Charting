[CmdletBinding()]
param(
    [string]$TaskName = "AgenticAI-Trading-24x7"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$watchdogPath = Join-Path $PSScriptRoot "scanner_watchdog.ps1"
$backendLauncherPath = Join-Path $PSScriptRoot "start_api_background.ps1"
$pythonPath = Join-Path $repoRoot ".venv\Scripts\python.exe"
$vitePath = Join-Path $repoRoot "frontend\node_modules\vite\bin\vite.js"
$powershellPath = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$userId = $identity.Name

foreach ($requiredPath in @($watchdogPath, $backendLauncherPath, $pythonPath, $vitePath, $powershellPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Required runtime file is missing: $requiredPath"
    }
}

$actionArguments = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$watchdogPath`""
$action = New-ScheduledTaskAction `
    -Execute $powershellPath `
    -Argument $actionArguments `
    -WorkingDirectory $repoRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userId
$taskPrincipal = New-ScheduledTaskPrincipal `
    -UserId $userId `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$task = New-ScheduledTask `
    -Action $action `
    -Trigger $trigger `
    -Principal $taskPrincipal `
    -Settings $settings `
    -Description "Keeps AgenticAI Trading running and resumes it when the current user signs in."

Register-ScheduledTask -TaskName $TaskName -InputObject $task -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3

$registered = Get-ScheduledTask -TaskName $TaskName
$info = Get-ScheduledTaskInfo -TaskName $TaskName
[pscustomobject]@{
    TaskName = $registered.TaskName
    State = $registered.State
    LastRunTime = $info.LastRunTime
    LastTaskResult = $info.LastTaskResult
    RunAs = $userId
    Dashboard = "http://127.0.0.1:5173"
    Health = "http://127.0.0.1:3001/api/health"
}
