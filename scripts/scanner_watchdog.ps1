$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$artifacts = Join-Path $root "artifacts"
$python = Join-Path $root ".venv\Scripts\python.exe"
$node = "C:\Users\ganes\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
$vite = Join-Path $root "frontend\node_modules\vite\bin\vite.js"
$watchdogLog = Join-Path $artifacts "scanner_watchdog.log"
$backendProcess = $null
$gatewayProcess = $null
$frontendProcess = $null
$previewProcess = $null
$buildWatcherProcess = $null

New-Item -ItemType Directory -Path $artifacts -Force | Out-Null

# Single instance. Two watchdogs racing each other (or a watchdog racing a
# session's manual restart) produced FOUR duplicate api_server pairs on
# 2026-08-10; one of those pairs concurrently refreshed the Schwab trading
# token and revoked it (Schwab rotates the refresh token on every refresh).
# v2 lock: the v1 lock's handle stayed held after a watchdog was killed
# unexpectedly (2026-08-20), and every replacement exited silently for hours.
# A fresh path retires the stale holder; the exit below is also LOGGED now,
# because Write-Host in a hidden window is how the outage stayed invisible.
$lockPath = Join-Path $artifacts "scanner_watchdog.v2.lock"
try {
    $script:lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch {
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath (Join-Path $artifacts "scanner_watchdog.log") -Value "$timestamp DUPLICATE watchdog (pid $PID) exiting: lock held at $lockPath"
    exit 0
}

Add-Content -LiteralPath $watchdogLog -Value ("{0} Watchdog started (pid {1}), supervising gateway :3001 + pipeline :3002." -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $PID)

function Write-WatchdogLog {
    param([string]$Message)
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Add-Content -LiteralPath $watchdogLog -Value "$timestamp $Message"
}

function Test-LocalPort {
    param([int]$Port, [int]$TimeoutMs = 3000)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $connection = $client.ConnectAsync("127.0.0.1", $Port)
        return $connection.Wait($TimeoutMs) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-ApiServerProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*api_server.py*" })
}

function Get-GatewayProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*gateway.py*" })
}

function Start-Backend {
    if ($null -ne $script:backendProcess -and -not $script:backendProcess.HasExited) {
        return
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-WatchdogLog "Backend runtime missing: $python"
        return
    }
    # A1 process split (spec 2026-08-19): the pipeline serves :3002 behind
    # the gateway on :3001. AGX_PORT set here is inherited by Start-Process.
    # A backend started WITHOUT it binds :3001, and Windows SO_REUSEADDR lets
    # two sockets share a port, silently splitting the front door with the
    # gateway - observed live on 2026-08-20.
    $env:AGX_PORT = "3002"
    # Crash forensics (2026-08-21): api_server died SILENTLY at 09:14, 09:46
    # and 10:04 CT under market load - no Python traceback, so a native
    # fault or hard kill. faulthandler dumps every thread's stack to stderr
    # (api_server.err.log) on segfault/abort, so the next death leaves
    # fingerprints instead of a mystery.
    $env:PYTHONFAULTHANDLER = "1"
    # -RedirectStandardOutput TRUNCATES, so every restart destroyed the log of
    # the run before it. That cost a real answer on 2026-08-31: asked whether a
    # CPU fix had landed, the only before/after evidence had been overwritten by
    # the restart that deployed it, and the question was unanswerable. Roll the
    # previous run aside instead; keep the last 6 so a week of restarts cannot
    # fill the disk.
    foreach ($stream in @("out", "err")) {
        $current = Join-Path $artifacts "api_server.$stream.log"
        if (Test-Path $current) {
            try {
                $stampName = "api_server.$stream." + (Get-Date -Format "yyyyMMdd-HHmmss") + ".log"
                Move-Item -LiteralPath $current -Destination (Join-Path $artifacts $stampName) -Force -ErrorAction Stop
                Get-ChildItem -Path $artifacts -Filter "api_server.$stream.2*.log" -ErrorAction SilentlyContinue |
                    Sort-Object LastWriteTime -Descending | Select-Object -Skip 6 |
                    Remove-Item -Force -ErrorAction SilentlyContinue
            } catch {
                # A locked file must never stop the backend from starting.
            }
        }
    }
    $script:backendProcess = Start-Process `
        -FilePath $python `
        -ArgumentList "-u", "api_server.py" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "api_server.out.log") `
        -RedirectStandardError (Join-Path $artifacts "api_server.err.log") `
        -PassThru
    Write-WatchdogLog "Started backend (pipeline) on port 3002."
}

function Get-MomxWorkerProcesses {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*momx_worker.py*" })
}

function Start-MomxWorker {
    # The MomX scanner runs in its OWN process on 3010, and api_server proxies
    # to it. Measured 2026-08-28: the same 30-minute fetch took 10s standalone
    # and was still unfinished after 5h19m inside api_server, which is pegged by
    # the chart engine. Sharing that process starved both.
    if ($null -ne $script:momxProcess -and -not $script:momxProcess.HasExited) {
        return
    }
    if ((Get-MomxWorkerProcesses).Count -gt 0) { return }
    if (-not (Test-Path -LiteralPath $python)) { return }
    $script:momxProcess = Start-Process `
        -FilePath $python `
        -ArgumentList "-u", "momx_worker.py" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "momx_worker.out.log") `
        -RedirectStandardError (Join-Path $artifacts "momx_worker.err.log") `
        -PassThru
    Write-WatchdogLog "Started MomX scanner worker on port 3010."
}

function Start-Gateway {
    if ($null -ne $script:gatewayProcess -and -not $script:gatewayProcess.HasExited) {
        return
    }
    # Adopt a gateway some other launcher already started - the exclusive
    # bind means a duplicate would crash-loop, so never blind-start.
    if ((Get-GatewayProcesses).Count -gt 0) {
        return
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-WatchdogLog "Gateway runtime missing: $python"
        return
    }
    $script:gatewayProcess = Start-Process `
        -FilePath $python `
        -ArgumentList "gateway.py", "--port", "3001", "--pipeline", "http://127.0.0.1:3002" `
        -WorkingDirectory $root `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "gateway.out.log") `
        -RedirectStandardError (Join-Path $artifacts "gateway.err.log") `
        -PassThru
    Write-WatchdogLog "Started gateway on port 3001 -> pipeline :3002."
}

function Start-Frontend {
    if ($null -ne $script:frontendProcess -and -not $script:frontendProcess.HasExited) {
        return
    }
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $vite)) {
        Write-WatchdogLog "Frontend runtime or Vite entrypoint is missing."
        return
    }
    # $vite sits under "AgenticAI-Trading 7\AgenticAI-Trading 2" -- two spaces in
    # the path. Start-Process joins -ArgumentList with spaces and does NOT quote
    # the elements, so the bare path reached node as "C:\GANESH\AgenticAI-Trading"
    # and every start died with MODULE_NOT_FOUND, 3x/minute, silently.
    $script:frontendProcess = Start-Process `
        -FilePath $node `
        -ArgumentList "`"$vite`"", "--host", "0.0.0.0", "--port", "5173", "--strictPort" `
        -WorkingDirectory (Join-Path $root "frontend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "frontend.out.log") `
        -RedirectStandardError (Join-Path $artifacts "frontend.err.log") `
        -PassThru
    Write-WatchdogLog "Started frontend on port 5173."
}

# app.agxtrade.com serves its frontend from :4173 (vite preview over dist/),
# NOT from the :5173 dev server. That port was never supervised, so if the
# preview process died the public URL had no frontend at all and nothing
# restarted it - while :5173 kept working locally and hid the outage.
function Start-Preview {
    if ($null -ne $script:previewProcess -and -not $script:previewProcess.HasExited) {
        return
    }
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $vite)) {
        Write-WatchdogLog "Preview runtime or Vite entrypoint is missing."
        return
    }
    # Same two-spaces-in-the-path trap as Start-Frontend: $vite MUST stay quoted.
    $script:previewProcess = Start-Process `
        -FilePath $node `
        -ArgumentList "`"$vite`"", "preview", "--host", "0.0.0.0", "--port", "4173", "--strictPort" `
        -WorkingDirectory (Join-Path $root "frontend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "preview.out.log") `
        -RedirectStandardError (Join-Path $artifacts "preview.err.log") `
        -PassThru
    Write-WatchdogLog "Started preview (public frontend) on port 4173."
}

# dist/ is a separate artifact from the :5173 dev server, so the public URL
# only ever changed when somebody remembered to run `npm run build`. On
# 2026-08-13 that meant the phone ran a different session's bundle while
# fixes sat in source, and every "works locally, broken on app.agxtrade.com"
# report started here. Rebuild dist automatically on any source change so the
# two origins cannot drift. Watch mode is idle until a file actually changes.
function Start-BuildWatcher {
    if ($null -ne $script:buildWatcherProcess -and -not $script:buildWatcherProcess.HasExited) {
        return
    }
    # A restarted watchdog has no handle on the previous session's watcher,
    # and blind-starting here is exactly how two vite build --watch processes
    # end up racing each other's dist writes (recorded 2026-08-17). Adopt.
    $existingWatcher = @(Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*vite*build*--watch*" })
    if ($existingWatcher.Count -gt 0) {
        return
    }
    if (-not (Test-Path -LiteralPath $node) -or -not (Test-Path -LiteralPath $vite)) {
        Write-WatchdogLog "Build watcher runtime or Vite entrypoint is missing."
        return
    }
    $script:buildWatcherProcess = Start-Process `
        -FilePath $node `
        -ArgumentList "`"$vite`"", "build", "--watch" `
        -WorkingDirectory (Join-Path $root "frontend") `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $artifacts "build-watch.out.log") `
        -RedirectStandardError (Join-Path $artifacts "build-watch.err.log") `
        -PassThru
    Write-WatchdogLog "Started dist build watcher (keeps :4173 in step with :5173)."
}

# The build watcher runs with emptyOutDir:false (see frontend/vite.config.js --
# emptying dist mid-rebuild left app.agxtrade.com with no frontend for the ~45s
# a build takes). The price of keeping the previous build in place is that every
# rebuild adds another content-hashed bundle and never removes the last one: a
# single watch session running since 2026-08-15 had grown dist/assets to 222
# files / 276 MB by 2026-08-17. Prune the superseded ones here instead.
#
# This deliberately does NOT clear dist/assets when the watcher (re)starts:
# that would recreate the very outage emptyOutDir:false was set to avoid, since
# index.html keeps pointing at a deleted bundle until the next build lands. The
# file index.html currently references is never touched, so :4173 always has a
# complete bundle to serve.
function Remove-StaleDistAssets {
    $assets = Join-Path $root "frontend\dist\assets"
    $indexHtml = Join-Path $root "frontend\dist\index.html"
    if (-not (Test-Path -LiteralPath $assets) -or -not (Test-Path -LiteralPath $indexHtml)) {
        return
    }

    $html = Get-Content -LiteralPath $indexHtml -Raw
    $referenced = @([regex]::Matches($html, 'assets/(index-[A-Za-z0-9_-]+\.(?:js|css))') |
        ForEach-Object { $_.Groups[1].Value })
    # A half-written index.html, or one naming neither a script nor a stylesheet,
    # is not a safe basis for deleting anything.
    if ($referenced.Count -lt 2) {
        return
    }

    # A build writes the new bundle BEFORE index.html catches up, so a file that
    # is not referenced yet may be the one that is about to be. Never delete
    # inside that window; builds here measure 16-45s.
    $cutoff = (Get-Date).AddMinutes(-10)

    $stale = @(Get-ChildItem -LiteralPath $assets -File |
        Where-Object {
            $_.Name -match '^index-[A-Za-z0-9_-]+\.(js|css)$' -and
            $referenced -notcontains $_.Name -and
            $_.LastWriteTime -le $cutoff
        })
    if ($stale.Count -eq 0) {
        return
    }

    $freedMb = [Math]::Round((($stale | Measure-Object Length -Sum).Sum / 1MB), 1)
    $removed = 0
    foreach ($file in $stale) {
        # A bundle `vite preview` is streaming to a client right now stays
        # locked; skip it and collect it on a later pass.
        try {
            Remove-Item -LiteralPath $file.FullName -Force -ErrorAction Stop
            $removed++
        }
        catch {}
    }
    if ($removed -gt 0) {
        Write-WatchdogLog "Pruned $removed superseded dist asset(s), freed ~$freedMb MB; kept $($referenced -join ', ')."
    }
}

# Server startup takes 60-80s on this box, during which port 3001 is closed.
# Restarting whenever the port is closed therefore spawned a SECOND backend
# beside every legitimately starting one. A backend process younger than this
# window is presumed to be booting and is left alone; one older than this
# with the port still closed is wedged and gets replaced.
# 300, not 180: right after a Windows reboot the first boot runs on a cold disk
# and took >180s (2026-09-22 23:10 - pid 15500 was killed as "wedged" at 180s;
# its replacement listened at ~140s), so every reboot paid a needless restart.
$backendStartupGraceSeconds = 300

# A healthy backend can still miss a TCP accept: the background study builder
# pegs python near 95% CPU, and under that GIL pressure a connect can exceed the
# probe timeout. Replacing on a SINGLE missed probe therefore killed a working
# server and turned a few seconds of stall into a ~90s outage (2026-08-12
# 23:25 - pid 29004 was replaced after 3.7h, and the frontend showed
# "API is unavailable right now (server error)" until the new process listened).
# Require several consecutive misses, so only a genuinely stuck server is cycled.
$backendMissThreshold = 3
$backendMisses = 0

while ($true) {
    try {
        if (-not (Test-LocalPort -Port 3002)) {
            $backendMisses++
            $existing = Get-ApiServerProcesses
            if ($existing.Count -eq 0) {
                Write-WatchdogLog "Port 3002 closed and no api_server process exists; starting backend."
                Start-Backend
                $backendMisses = 0
            }
            else {
                $newest = ($existing | Sort-Object CreationDate -Descending | Select-Object -First 1)
                $ageSeconds = [Math]::Round(((Get-Date) - $newest.CreationDate).TotalSeconds)
                if ($ageSeconds -lt $backendStartupGraceSeconds) {
                    Write-WatchdogLog "Port 3002 closed but api_server pid $($newest.ProcessId) is ${ageSeconds}s old (starting up); waiting."
                }
                elseif ($backendMisses -lt $backendMissThreshold) {
                    Write-WatchdogLog "Port 3002 did not answer (miss $backendMisses/$backendMissThreshold) but api_server pid $($newest.ProcessId) is alive; likely busy, waiting."
                }
                else {
                    Write-WatchdogLog "Port 3002 closed on $backendMisses consecutive probes and api_server pid $($newest.ProcessId) is ${ageSeconds}s old (wedged); replacing."
                    $existing | ForEach-Object {
                        try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
                    }
                    Start-Sleep -Seconds 2
                    Start-Backend
                    $backendMisses = 0
                }
            }
        }
        else {
            $backendMisses = 0
        }
        # The gateway is stateless and boots in about a second, so recovery
        # is aggressive: port closed -> kill any wedged remnant -> restart.
        # It never touches api_server processes; the pipeline block above is
        # the only authority on those.
        if (-not (Test-LocalPort -Port 3001)) {
            $gatewayProcs = Get-GatewayProcesses
            foreach ($proc in $gatewayProcs) {
                try { Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop } catch {}
            }
            if ($gatewayProcs.Count -gt 0) {
                Write-WatchdogLog "Port 3001 closed with $($gatewayProcs.Count) gateway process(es) alive (wedged); replacing."
                Start-Sleep -Seconds 1
            }
            else {
                Write-WatchdogLog "Port 3001 closed and no gateway process exists; starting gateway."
            }
            Start-Gateway
        }
        # The MomX scanner worker owns port 3010 (its own process on purpose -
        # see Start-MomxWorker). Checked every pass like the others.
        if (-not (Test-LocalPort -Port 3010)) {
            Start-MomxWorker
        }
        # Reap stranded MomX pool children. ProcessPoolExecutor workers do not
        # die with their parent, and a hard kill cannot be trapped in-process,
        # so 8 of them survived every restart on 2026-08-28 and competed with
        # the chart engine at NORMAL priority.
        $momxParents = (Get-MomxWorkerProcesses | ForEach-Object { $_.ProcessId })
        Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
            Where-Object { $_.CommandLine -like "*spawn_main*" } |
            Where-Object { $keep = $false; foreach ($pp in $momxParents) { if ($_.CommandLine -like "*parent_pid=$pp*") { $keep = $true } }; -not $keep } |
            ForEach-Object {
                Write-WatchdogLog "Reaping orphaned MomX pool worker $($_.ProcessId)."
                Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
            }

        if (-not (Test-LocalPort -Port 5173)) {
            Start-Frontend
        }
        # The public frontend and the build that feeds it. Both are checked
        # every pass so app.agxtrade.com cannot quietly fall behind :5173.
        if (-not (Test-LocalPort -Port 4173)) {
            Start-Preview
        }
        Start-BuildWatcher
        # Superseded bundles accumulate for as long as the watcher keeps
        # running, so this has to run every pass, not only when it restarts.
        Remove-StaleDistAssets
    }
    catch {
        Write-WatchdogLog "Watchdog check failed: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds 15
}
