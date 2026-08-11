param(
    [string]$GenerateCommand = "",
    [ValidateRange(1, 10)]
    [int]$MaxAttempts = 3,
    [switch]$SkipFrontendBuild,
    [switch]$SkipLiveSmoke
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\python.exe"
$frontend = Join-Path $root "frontend"
$reportRoot = Join-Path $root "artifacts\coding_loop"
$runId = Get-Date -Format "yyyyMMdd-HHmmss"
$runRoot = Join-Path $reportRoot $runId
$latestReport = Join-Path $reportRoot "latest.json"

New-Item -ItemType Directory -Path $runRoot -Force | Out-Null

function Invoke-LoopStep {
    param(
        [string]$Name,
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory
    )
    $started = Get-Date
    $logPath = Join-Path $runRoot "$($script:attempt)-$Name.log"
    Push-Location $WorkingDirectory
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Windows PowerShell wraps any native stderr line as NativeCommandError.
        # Preserve stderr in the log, but judge the step by the real process exit code.
        $ErrorActionPreference = "Continue"
        $output = & $FilePath @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    catch {
        $output = @($_ | Out-String)
        $exitCode = 1
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
        Pop-Location
    }
    @($output) | Set-Content -LiteralPath $logPath -Encoding UTF8
    return [ordered]@{
        name = $Name
        ok = ($exitCode -eq 0)
        exitCode = $exitCode
        startedAt = $started.ToString("o")
        durationSeconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)
        logPath = $logPath
    }
}

function Invoke-LiveSmoke {
    $started = Get-Date
    $logPath = Join-Path $runRoot "$($script:attempt)-live-smoke.log"
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:3001/api/health" -TimeoutSec 45
        $status = Invoke-RestMethod -Uri "http://127.0.0.1:3001/api/status" -TimeoutSec 60
        $frontendResponse = Invoke-WebRequest -Uri "http://127.0.0.1:5173" -UseBasicParsing -TimeoutSec 15
        $accountIds = @($status.accounts | ForEach-Object { $_.id } | Sort-Object)
        $cardIds = @($status.dashboardSummary.accountBooks | ForEach-Object { $_.id } | Sort-Object)
        $idsMatch = (Compare-Object $accountIds $cardIds).Count -eq 0
        $ok = [bool]$health.ok -and $frontendResponse.StatusCode -eq 200 -and $idsMatch
        [ordered]@{
            health = $health
            frontendStatus = $frontendResponse.StatusCode
            configuredAccountIds = $accountIds
            dashboardAccountIds = $cardIds
            accountIdsMatch = $idsMatch
        } | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $logPath -Encoding UTF8
        $exitCode = if ($ok) { 0 } else { 1 }
    }
    catch {
        $_ | Out-String | Set-Content -LiteralPath $logPath -Encoding UTF8
        $exitCode = 1
    }
    return [ordered]@{
        name = "live-smoke"
        ok = ($exitCode -eq 0)
        exitCode = $exitCode
        startedAt = $started.ToString("o")
        durationSeconds = [math]::Round(((Get-Date) - $started).TotalSeconds, 2)
        logPath = $logPath
    }
}

if (-not (Test-Path -LiteralPath $python)) {
    throw "Project Python runtime not found: $python"
}

$attemptReports = @()
$passed = $false
for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
    $env:AGENTIC_LOOP_ATTEMPT = [string]$attempt
    $env:AGENTIC_LOOP_REPORT_PATH = $latestReport
    $steps = @()

    if ($GenerateCommand.Trim()) {
        $steps += Invoke-LoopStep `
            -Name "generate" `
            -FilePath "powershell.exe" `
            -Arguments @("-NoProfile", "-Command", $GenerateCommand) `
            -WorkingDirectory $root
    }

    $steps += Invoke-LoopStep `
        -Name "compile" `
        -FilePath $python `
        -Arguments @("-m", "py_compile", "api_server.py", "scanner.py", "strategy.py", "config.py") `
        -WorkingDirectory $root

    $steps += Invoke-LoopStep `
        -Name "backend-tests" `
        -FilePath $python `
        -Arguments @("-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py") `
        -WorkingDirectory $root

    $steps += Invoke-LoopStep `
        -Name "runtime-contract" `
        -FilePath $python `
        -Arguments @("scripts\verify_runtime_contract.py") `
        -WorkingDirectory $root

    if (-not $SkipFrontendBuild) {
        $steps += Invoke-LoopStep `
            -Name "frontend-build" `
            -FilePath "npm.cmd" `
            -Arguments @("run", "build") `
            -WorkingDirectory $frontend
    }

    if (-not $SkipLiveSmoke) {
        $steps += Invoke-LiveSmoke
    }

    $attemptPassed = @($steps | Where-Object { -not $_.ok }).Count -eq 0
    $attemptReport = [ordered]@{
        attempt = $attempt
        passed = $attemptPassed
        startedBy = $env:USERNAME
        generateCommandConfigured = [bool]$GenerateCommand.Trim()
        steps = $steps
    }
    $attemptReports += $attemptReport
    $report = [ordered]@{
        runId = $runId
        root = $root
        status = if ($attemptPassed) { "passed" } else { "retrying" }
        maxAttempts = $MaxAttempts
        completedAttempts = $attempt
        attempts = $attemptReports
        updatedAt = (Get-Date).ToString("o")
    }
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $latestReport -Encoding UTF8
    $report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $runRoot "report.json") -Encoding UTF8

    if ($attemptPassed) {
        $passed = $true
        break
    }
    if (-not $GenerateCommand.Trim()) {
        break
    }
}

$finalReport = Get-Content -LiteralPath $latestReport -Raw | ConvertFrom-Json
$finalReport.status = if ($passed) { "passed" } else { "failed" }
$finalReport.updatedAt = (Get-Date).ToString("o")
$finalReport | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $latestReport -Encoding UTF8
$finalReport | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $runRoot "report.json") -Encoding UTF8

Write-Host "Agentic coding loop: $($finalReport.status)"
Write-Host "Report: $latestReport"
exit $(if ($passed) { 0 } else { 1 })
