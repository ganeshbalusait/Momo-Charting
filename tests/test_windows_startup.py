"""Exercise watchdog recovery decisions without starting or stopping services."""
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest


@pytest.mark.skipif(not shutil.which("powershell.exe"), reason="Windows watchdog")
def test_backend_cold_start_and_recovery():
    script = r'''
$ErrorActionPreference = 'Stop'
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    (Join-Path (Get-Location) 'scripts/scanner_watchdog.ps1'), [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw ($errors | Out-String) }
$decision = $ast.Find({param($n)
    $n -is [System.Management.Automation.Language.IfStatementAst] -and
    $n.Extent.Text.StartsWith('if (-not (Test-LocalPort -Port 3002))')
}, $true)
if (-not $decision) { throw 'Backend supervision block missing' }
$block = [scriptblock]::Create($decision.Extent.Text)
function Test-LocalPort { param($Port) return $portUp }
function Get-ApiServerProcesses {
    if ($missing) { return @() }
    return @([pscustomobject]@{ProcessId=123; CreationDate=(Get-Date).AddSeconds(-$age)})
}
function Write-WatchdogLog { param($Message) }
function Stop-Process { param($Id, [switch]$Force, $ErrorAction) $script:stopped++ }
function Start-Sleep { param($Seconds) }
function Start-Backend { $script:started++; $script:backendHasListened=$false }
$backendStartupGraceSeconds=900; $backendMissThreshold=3
# age, previously ready, previous misses, listening, missing, expected starts, expected readiness
$cases = @(
    @(312,$false,14,$false,$false,0,$false),
    @(899,$false,30,$false,$false,0,$false),
    @(901,$false,30,$false,$false,1,$false),
    @(100,$true,1,$false,$false,0,$true),
    @(100,$true,2,$false,$false,1,$false),
    @(100,$false,9,$true,$false,0,$true),
    @(0,$true,0,$false,$true,1,$false)
)
foreach ($case in $cases) {
    $age,$backendHasListened,$backendMisses,$portUp,$missing,$expectedStarts,$expectedReady=$case
    $started=0; $stopped=0
    . $block
    if ($started -ne $expectedStarts -or $backendHasListened -ne $expectedReady) {
        throw "Wrong recovery decision for $case (starts=$started ready=$backendHasListened)"
    }
    if ($stopped -ne $(if ($expectedStarts -and -not $missing) {1} else {0})) {
        throw "Unexpected process termination for $case"
    }
}
'''
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not shutil.which("powershell.exe"), reason="Windows watchdog")
def test_mutex_rejects_duplicate_and_recovers_after_owner_termination(tmp_path):
    source = (Path(__file__).resolve().parents[1] / "scripts/scanner_watchdog.ps1").read_text()
    lock_code = source.split("$script:watchdogMutex =", 1)[1].split(
        'Add-Content -LiteralPath $watchdogLog -Value (', 1
    )[0]
    lock_code = "$script:watchdogMutex =" + lock_code
    lock_code = lock_code.replace("Local\\AgenticAI-Trading-Watchdog", "Local\\AGX-Test-" + uuid.uuid4().hex)
    runner = tmp_path / "mutex.ps1"
    runner.write_text(
        "$ErrorActionPreference='Stop'\n$watchdogLog=Join-Path $PSScriptRoot 'test.log'\n"
        + lock_code + "\n[Console]::WriteLine('OWNED')\n[Console]::ReadLine() | Out-Null\n"
    )
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(runner)]
    owner = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert owner.stdout.readline().strip() == "OWNED"
        duplicate = subprocess.run(command, input="\n", capture_output=True, text=True, timeout=15)
        assert duplicate.returncode == 0, duplicate.stderr
        assert "OWNED" not in duplicate.stdout
        owner.kill()
        owner.wait(timeout=10)
        replacement = subprocess.run(command, input="\n", capture_output=True, text=True, timeout=15)
        assert replacement.returncode == 0, replacement.stderr
        assert "OWNED" in replacement.stdout
    finally:
        if owner.poll() is None:
            owner.kill()
        owner.communicate(timeout=10)
