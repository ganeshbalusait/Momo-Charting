"""Run only the orphan predicate; never start the watchdog or stop processes."""
import shutil
import subprocess
from pathlib import Path
import pytest


@pytest.mark.skipif(not shutil.which("powershell.exe"), reason="Windows watchdog")
def test_orphan_detection_keeps_live_api_test_and_scanner_children():
    script = r'''
$ErrorActionPreference='Stop'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile(
 (Join-Path (Get-Location) 'scripts/scanner_watchdog.ps1'),[ref]$tokens,[ref]$errors)
if($errors.Count){throw ($errors|Out-String)}
$function=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Test-OrphanedPoolWorker'},$true)
. ([scriptblock]::Create($function.Extent.Text))
$parent=[pscustomobject]@{ProcessId=123;CreationDate=[datetime]'2026-10-29T09:00:00'}
$child=[pscustomobject]@{CommandLine='python spawn_main(parent_pid=123, pipe_handle=4)';CreationDate=[datetime]'2026-10-29T10:00:00'}
if(Test-OrphanedPoolWorker $child @{123=$parent}){throw 'Living parent was treated as orphan'}
if(-not (Test-OrphanedPoolWorker $child @{})){throw 'Abandoned child was retained'}
$parent.CreationDate=[datetime]'2026-10-29T11:00:00'
if(-not (Test-OrphanedPoolWorker $child @{123=$parent})){throw 'Reused parent PID was retained'}
$child.CommandLine='python spawn_main()'
if(Test-OrphanedPoolWorker $child @{}){throw 'Ambiguous ownership must not terminate a process'}
'''
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
