$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$profileRoot = Join-Path $env:USERPROFILE '.openclaw-agx-bot'
$configPath = Join-Path $profileRoot 'openclaw.json'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'BOT OpenClaw profile is not configured.' }
$existing = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object {
    $_.CommandLine -match 'node_modules[\\/]+openclaw[\\/].*\bgateway\b.*--port\s+18790(?:\s|$)'
})
if ($existing.Count) { Write-Output 'BOT OpenClaw gateway is already starting or running.'; exit 0 }
$runtimeRoot = Join-Path $env:LOCALAPPDATA 'OpenClaw\runtime'
$nodeRuntime = Get-ChildItem -LiteralPath $runtimeRoot -Directory | Sort-Object Name -Descending |
    ForEach-Object { Join-Path $_.FullName 'node.exe' } | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $nodeRuntime) { throw 'Installed OpenClaw Node runtime is unavailable.' }
$entry = Join-Path $env:APPDATA 'npm\node_modules\openclaw\dist\index.js'
if (-not (Test-Path -LiteralPath $entry)) { throw 'Installed native OpenClaw entry is unavailable.' }
$env:OPENCLAW_STATE_DIR = $profileRoot
$env:OPENCLAW_CONFIG_PATH = $configPath
$env:NODE_COMPILE_CACHE = Join-Path $profileRoot 'node-compile-cache'
# Child workers and helpers must use the same supported runtime as the gateway.
$env:PATH = (Split-Path -Parent $nodeRuntime) + ';' + $env:PATH
$artifacts = Join-Path $projectRoot 'artifacts'
$gateway = Start-Process -FilePath $nodeRuntime -ArgumentList '--stack-size=8192', $entry, 'gateway', '--port', '18790' `
    -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $artifacts 'openclaw-bot-profile.out.log') `
    -RedirectStandardError (Join-Path $artifacts 'openclaw-bot-profile.err.log') -PassThru
Write-Output ('Started native BOT OpenClaw gateway: ' + $gateway.Id)
