[CmdletBinding()]
param(
    [string]$TaskName = "AgenticAI-Trading-24x7"
)

$ErrorActionPreference = "Stop"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "Scheduled task '$TaskName' is not installed."
    return
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Scheduled task '$TaskName' was stopped and removed."
