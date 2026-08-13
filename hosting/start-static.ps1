# Serves the built frontend (frontend/dist) on 127.0.0.1:4173 for the Cloudflare
# tunnel to route to.
#
# Bound to loopback deliberately: cloudflared runs on this same machine, so this
# server never needs to listen on a public interface. (The `preview` script in
# package.json binds 0.0.0.0 — that is not used here, and is left untouched.)
#
# This does not touch the API on :3001 or the Vite dev server on :5173.

$ErrorActionPreference = "Stop"

$frontend = "C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2\frontend"
$dist = Join-Path $frontend "dist"

if (-not (Test-Path $frontend)) {
    throw "Frontend not found at $frontend"
}

if (-not (Test-Path (Join-Path $dist "index.html"))) {
    throw "No build found at $dist. Run 'npm run build' in $frontend first."
}

# Warn when the build is older than the newest source file, since app.agxtrade.com
# serves dist and would otherwise quietly show stale code.
$newestSrc = Get-ChildItem (Join-Path $frontend "src") -Recurse -File -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
$builtAt = (Get-Item (Join-Path $dist "index.html")).LastWriteTime
if ($newestSrc -and $newestSrc.LastWriteTime -gt $builtAt) {
    Write-Warning "dist is older than src ($($builtAt.ToString('HH:mm')) vs $($newestSrc.LastWriteTime.ToString('HH:mm')))."
    Write-Warning "app.agxtrade.com will serve the OLD build until you run 'npm run build'."
}

Write-Host "Serving $dist on http://127.0.0.1:4173 (loopback only)" -ForegroundColor Cyan

Set-Location $frontend
npx vite preview --host 127.0.0.1 --port 4173 --strictPort
