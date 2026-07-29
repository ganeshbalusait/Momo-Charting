$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $repoRoot "artifacts"
$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$lockPath = Join-Path $artifacts "api_server.lock"

New-Item -ItemType Directory -Path $artifacts -Force | Out-Null
if (-not (Test-Path -LiteralPath $python)) {
    throw "Backend runtime is missing: $python"
}

try {
    $lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch [System.IO.IOException] {
    Write-Error "Another supervised backend already owns $lockPath"
    exit 73
}

$exitCode = 1
try {
    Set-Location $repoRoot
    & $python -u api_server.py
    $exitCode = $LASTEXITCODE
}
finally {
    $lockStream.Dispose()
}

exit $exitCode
