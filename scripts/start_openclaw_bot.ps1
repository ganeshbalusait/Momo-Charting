$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$profileRoot = Join-Path $env:USERPROFILE '.openclaw-agx-bot'
$configPath = Join-Path $profileRoot 'openclaw.json'
if (-not (Test-Path -LiteralPath $configPath)) { throw 'BOT OpenClaw profile is not configured.' }
$existing = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" | Where-Object {
    $_.CommandLine -match 'node_modules[\\/]+openclaw[\\/].*\bgateway\b.*--port\s+18790(?:\s|$)'
})
if ($existing.Count) {
    $listener = @(Get-NetTCPConnection -State Listen -LocalPort 18790 -ErrorAction SilentlyContinue)
    if ($listener.Count) { Write-Output 'BOT OpenClaw listener is running; agent readiness is checked by requests.'; exit 0 }
    $expired = @($existing | Where-Object { $_.CreationDate -lt (Get-Date).AddMinutes(-20) })
    if ($expired.Count -ne $existing.Count) { Write-Output 'BOT OpenClaw is still starting; listener not ready.'; exit 0 }
    # Recover only this dedicated BOT gateway, never unrelated OpenClaw sessions.
    $expired | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop }
    Write-Output 'Restarting dedicated BOT gateway after a stalled 20-minute startup.'
}
# OpenClaw was installed from inside the Codex app, and Windows redirected its
# %LOCALAPPDATA% writes into Codex's package LocalCache (2026-11-02: the plain
# path did not exist, so every start outside Codex failed). Look in both.
$runtimeRoots = @(
    (Join-Path $env:LOCALAPPDATA 'OpenClaw\runtime'),
    (Join-Path $env:LOCALAPPDATA 'Packages\OpenAI.Codex_2p2nqsd0c76g0\LocalCache\Local\OpenClaw\runtime')
) | Where-Object { Test-Path -LiteralPath $_ }
$nodeRuntime = $runtimeRoots | ForEach-Object { Get-ChildItem -LiteralPath $_ -Directory } | Sort-Object Name -Descending |
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
