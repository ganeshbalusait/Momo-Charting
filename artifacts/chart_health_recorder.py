"""Market-hours chart health recorder (his 2026-10-02 report: "charts loading
very slow" + "very big gap" - "No guess work").

Writes artifacts/chart_health_<date>.jsonl during START_ET-END_ET:
  every 60s, per symbol:
    initialSeconds  - time to the first paint (initial=true)
    newestBar / lagSeconds - newest candle the server serves vs now (the "gap")
    refreshing / tapeStale flags
  every 60s: /api/health latency, api_server pid + start time (restarts)
  every 15 min: a 15s py-spy GIL sample of api_server, top 25 functions
  every 5 min: new lines of api_server.out.log appended to
    artifacts/api_server_out_archive_<date>.log (the watchdog overwrites the
    log on each restart, which erased 2026-10-02's market-hours evidence).
Read-only toward the app: GET requests and a py-spy snapshot only.
"""
import gzip
import json
import subprocess
import time
import urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ART = ROOT / "artifacts"
PY_SPY = ROOT / ".venv" / "Scripts" / "py-spy.exe"
SYMBOLS = ("SPY", "NVDA", "AAPL", "TSLA", "LITE", "CLS", "AMD", "MU", "LASR", "TEAM")
ET = timezone(timedelta(hours=-4))  # EDT
TARGET_DAY = datetime(2026, 10, 5, tzinfo=ET)  # Monday
START_ET, END_ET = (9, 0), (16, 5)


def get(url, timeout=120):
    started = time.time()
    request = urllib.request.Request(url, headers={"Accept-Encoding": "gzip"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return time.time() - started, json.loads(body or b"{}")


def api_server_pid():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -match 'api_server.py' } | "
         "Sort-Object ProcessId | ForEach-Object { \"$($_.ProcessId) $($_.ParentProcessId) $($_.CreationDate.ToString('s'))\" }"],
        capture_output=True, text=True, timeout=60,
    ).stdout.split("\n")
    rows = [line.split() for line in out if line.strip()]
    pids = {row[0] for row in rows}
    # the worker is the child whose parent is also api_server
    child = next((row for row in rows if row[1] in pids), rows[0] if rows else None)
    return (int(child[0]), child[2]) if child else (None, None)


def gil_profile(pid):
    out_file = ART / "chart_health_spy.txt"
    subprocess.run([str(PY_SPY), "record", "--pid", str(pid), "--duration", "15", "--rate", "40",
                    "--format", "raw", "--nonblocking", "--gil", "-o", str(out_file)],
                   capture_output=True, timeout=120)
    total, funcs = 0, Counter()
    for line in out_file.read_text(encoding="utf-8", errors="replace").splitlines():
        stack, _, n = line.rpartition(" ")
        try:
            n = int(n)
        except ValueError:
            continue
        total += n
        seen = set()
        for frame in stack.split(";")[1:]:
            name = frame.split(" (")[0]
            if name not in seen:
                funcs[name] += n
                seen.add(name)
    return {"gilSamples": total, "gilBusyPct": round(total / 600 * 100, 1),
            "top": [(name, round(count * 100 / max(total, 1))) for name, count in funcs.most_common(25)]}


def main():
    day = TARGET_DAY.date()
    out = ART / f"chart_health_{day}.jsonl"
    archive = ART / f"api_server_out_archive_{day}.log"
    start = datetime(day.year, day.month, day.day, *START_ET, tzinfo=ET)
    end = datetime(day.year, day.month, day.day, *END_ET, tzinfo=ET)
    wait = (start - datetime.now(ET)).total_seconds()
    print(f"recording {day} {START_ET}-{END_ET} ET -> {out.name}; sleeping {wait:.0f}s", flush=True)
    if wait > 0:
        time.sleep(wait)
    log_offset, last_spy, last_log = 0, 0.0, 0.0
    with out.open("a", encoding="utf-8") as fh:
        while datetime.now(ET) < end:
            loop_started = time.time()
            pid, started_at = api_server_pid()
            try:
                health_seconds, _ = get("http://127.0.0.1:3002/api/health", timeout=60)
            except Exception as exc:
                health_seconds = f"ERR {exc}"
            fh.write(json.dumps({"kind": "server", "at": loop_started, "pid": pid, "startedAt": started_at,
                                 "healthSeconds": health_seconds}) + "\n")
            for symbol in SYMBOLS:
                row = {"kind": "chart", "at": time.time(), "symbol": symbol}
                try:
                    seconds, payload = get(f"http://127.0.0.1:3001/api/oi-finder-chart?symbol={symbol}&timeframe=5m&initial=true")
                    bars = payload.get("bars") or []
                    newest = int(bars[-1]["time"]) if bars else 0
                    row.update({"initialSeconds": round(seconds, 2), "newestBar": newest,
                                "lagSeconds": round(time.time() - newest - 60, 1) if newest else None,
                                "refreshing": payload.get("refreshing"), "tapeStale": payload.get("tapeStale"),
                                "historyLoading": payload.get("historyLoading")})
                except Exception as exc:
                    row["error"] = str(exc)
                fh.write(json.dumps(row) + "\n")
            if pid and time.time() - last_spy >= 900:
                last_spy = time.time()
                try:
                    fh.write(json.dumps({"kind": "gil", "at": last_spy, "pid": pid, **gil_profile(pid)}) + "\n")
                except Exception as exc:
                    fh.write(json.dumps({"kind": "gil", "at": last_spy, "error": str(exc)}) + "\n")
            if time.time() - last_log >= 300:
                last_log = time.time()
                log = ART / "api_server.out.log"
                try:
                    data = log.read_bytes()
                    if len(data) < log_offset:  # overwritten by a restart
                        log_offset = 0
                        with archive.open("ab") as arc:
                            arc.write(f"\n===== log restarted, seen at {datetime.now(ET):%H:%M:%S} ET =====\n".encode())
                    with archive.open("ab") as arc:
                        arc.write(data[log_offset:])
                    log_offset = len(data)
                except Exception:
                    pass
            fh.flush()
            time.sleep(max(1.0, 60 - (time.time() - loop_started)))
    print("done", flush=True)


if __name__ == "__main__":
    main()
