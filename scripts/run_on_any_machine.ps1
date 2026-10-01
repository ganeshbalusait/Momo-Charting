$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path .venv)) {
    py -3 -m venv .venv
}

if (Test-Path .venv\Scripts\Activate.ps1) {
    . .venv\Scripts\Activate.ps1
} else {
    . .venv/bin/activate
}

python scripts/bootstrap.py
Write-Host "Setup complete. Copy .env.example to .env and add your credentials if needed."
