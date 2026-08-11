[CmdletBinding()]
param(
    [ValidateRange(1, 1048576)]
    [int]$ThresholdMB = 100,
    [switch]$Apply,
    [ValidateSet("1.day.ago", "2.weeks.ago", "now")]
    [string]$GitPrune = "1.day.ago"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$cleanupScript = Join-Path $PSScriptRoot "cleanup_safe_storage.py"
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$arguments = @(
    $cleanupScript,
    "--repo-root", $repoRoot,
    "--threshold-mb", $ThresholdMB,
    "--git-prune", $GitPrune
)
if ($Apply) {
    $arguments += "--apply"
}

if (Test-Path -LiteralPath $venvPython) {
    & $venvPython @arguments
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
    & py -3 @arguments
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    & python @arguments
} else {
    throw "Python 3 was not found. Install Python or recreate .venv."
}

exit $LASTEXITCODE
