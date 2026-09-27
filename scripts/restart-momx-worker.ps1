# Restart the MomX scanner worker.
#
# WHY THIS EXISTS. The scanner runs in its own process (momx_worker.py, port
# 3010) rather than inside api_server - that split is deliberate and measured
# (60 symbols in 10s standalone vs still unfinished after 5h19m inside
# api_server). The consequence is that new scanner code needs THAT process
# restarted, and until it is, a new endpoint answers 404 while everything else
# on the board keeps working perfectly. On 2026-09-05 that cost a round trip:
# the FIND search was live in the browser and answering nothing.
#
# scanner_watchdog.ps1 polls port 3010 and respawns the worker when it is down,
# so stopping it IS the restart. This script just stops it and waits for the
# port to come back, so nobody has to remember which python process is which.
#
# Safe to run any time. On a weekday morning prefer before the open: a cold
# worker rebuilds its boards, which takes a couple of minutes on the 358-name
# Watchlist.

$ErrorActionPreference = 'Stop'

function Test-WorkerPort {
    try {
        $client = [System.Net.Sockets.TcpClient]::new()
        $ok = $client.ConnectAsync('127.0.0.1', 3010).Wait(700)
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

Write-Host 'Looking for the MomX scanner worker...'
$workers = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*momx_worker*' }

if (-not $workers) {
    Write-Host 'No worker is running. The watchdog will start one within ~15s.'
} else {
    foreach ($worker in $workers) {
        Write-Host ("  stopping pid {0} (started {1})" -f $worker.ProcessId, $worker.CreationDate)
        Stop-Process -Id $worker.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

Write-Host 'Waiting for the watchdog to bring it back...'
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 3
    if (Test-WorkerPort) {
        $fresh = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
            Where-Object { $_.CommandLine -like '*momx_worker*' } |
            Select-Object -First 1
        if ($fresh) {
            Write-Host ("Worker is back: pid {0}, started {1}" -f $fresh.ProcessId, $fresh.CreationDate) -ForegroundColor Green
        } else {
            Write-Host 'Port 3010 is answering again.' -ForegroundColor Green
        }
        # Prove the NEW code is live rather than assuming the restart took -
        # a health check that only pings the port would pass either way.
        try {
            $probe = Invoke-RestMethod -Uri ('http://127.0.0.1:3010/api/momx-scanner/history-query' +
                '?list=Watchlist&section=rvol&timeframe=1h&min=3&bg=cyan,green') -TimeoutSec 60
            Write-Host ("FIND is live: {0} tickers over {1} days." -f $probe.tickers, $probe.scannedDays) -ForegroundColor Green
        } catch {
            Write-Host 'Port is up but FIND still answers 404 - the worker may have restarted from old code.' -ForegroundColor Yellow
        }
        Write-Host 'The boards rebuild on their own over the next couple of minutes.'
        exit 0
    }
}

Write-Host 'Port 3010 did not come back within 90 seconds.' -ForegroundColor Red
Write-Host 'Check artifacts/scanner_watchdog.log - the watchdog may not be running.'
exit 1
