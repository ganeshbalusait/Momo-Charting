from __future__ import annotations

import argparse
import concurrent.futures
import json
import statistics
import time
import urllib.request


def request_once(url: str, timeout: float) -> tuple[str, bool, float, int, str]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            response.read()
            status = int(response.status)
        return url, status == 200, time.perf_counter() - started, status, ""
    except Exception as exc:  # pragma: no cover - reported by the load runner
        return url, False, time.perf_counter() - started, 0, str(exc)


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(max(round((len(ordered) - 1) * fraction), 0), len(ordered) - 1)
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only local frontend/API load smoke test")
    parser.add_argument("--requests", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()
    targets = [
        "http://127.0.0.1:5173/",
        "http://127.0.0.1:3001/api/health",
        "http://127.0.0.1:3001/api/status",
    ]
    work = [targets[index % len(targets)] for index in range(max(args.requests, 1))]
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(args.concurrency, 1)) as pool:
        futures = [pool.submit(request_once, url, args.timeout) for url in work]
        results = [future.result() for future in concurrent.futures.as_completed(futures)]
    duration = time.perf_counter() - started
    successes = [row for row in results if row[1]]
    failures = [row for row in results if not row[1]]
    latencies_ms = [row[2] * 1000 for row in results]
    by_target = {}
    for target in targets:
        target_rows = [row for row in results if row[0] == target]
        target_latencies = [row[2] * 1000 for row in target_rows]
        by_target[target] = {
            "requests": len(target_rows),
            "failed": sum(1 for row in target_rows if not row[1]),
            "p50Ms": round(percentile(target_latencies, 0.50), 2),
            "p95Ms": round(percentile(target_latencies, 0.95), 2),
            "p99Ms": round(percentile(target_latencies, 0.99), 2),
            "maxMs": round(max(target_latencies), 2) if target_latencies else 0,
        }
    report = {
        "requests": len(results),
        "concurrency": args.concurrency,
        "passed": len(successes),
        "failed": len(failures),
        "durationSeconds": round(duration, 3),
        "requestsPerSecond": round(len(results) / duration, 2) if duration else 0,
        "latencyMs": {
            "mean": round(statistics.fmean(latencies_ms), 2) if latencies_ms else 0,
            "p50": round(percentile(latencies_ms, 0.50), 2),
            "p95": round(percentile(latencies_ms, 0.95), 2),
            "p99": round(percentile(latencies_ms, 0.99), 2),
            "max": round(max(latencies_ms), 2) if latencies_ms else 0,
        },
        "byTarget": by_target,
        "sampleErrors": list(dict.fromkeys(row[4] for row in failures if row[4]))[:5],
    }
    print(json.dumps(report, indent=2))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
