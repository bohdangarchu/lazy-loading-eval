#!/usr/bin/env python3
"""
Query stargz snapshotter metrics from Prometheus on the client node
for a given time window to diagnose lazy-loading run time variance.

Goal: determine whether slow runs are caused by:
  A) more data being fetched (non-deterministic span fetching — prefetch variance,
     request merging differences, or warm/cold span cache)
  B) same data fetched but each fetch takes longer (fetch path latency — network
     latency, registry response time, or client-side decompression overhead)

Usage:
  python3 query_metrics.py <client_host> <start> <end>

Times in RFC3339 or Unix epoch. Get timestamps from the run log
(=== MODE === line = start, next === line or end of log = end).

Example:
  python3 query_metrics.py amd109.utah.cloudlab.us 2026-03-21T04:10:00Z 2026-03-21T04:14:00Z
"""

import sys
import json
import subprocess
from typing import Any

SSH_KEY = "~/.ssh/id_ed25519_cloudlab"
SSH_USER = "bgarchu"
PROM = "http://localhost:9090"


def ssh_curl(host: str, url: str, params: dict[str, str]) -> Any:
    param_str = " ".join(
        f"--data-urlencode '{k}={v}'" for k, v in params.items()
    )
    cmd = f"curl -sG '{url}' {param_str}"
    result = subprocess.run(
        ["ssh", "-i", SSH_KEY,
         "-o", "StrictHostKeyChecking=no",
         "-o", "ConnectTimeout=10",
         f"{SSH_USER}@{host}", cmd],
        capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def instant(host: str, promql: str, time: str) -> list[dict]:
    data = ssh_curl(host, f"{PROM}/api/v1/query", {"query": promql, "time": time})
    return data.get("data", {}).get("result", [])


def fmt_labels(metric: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in metric.items() if k != "__name__")


def print_instant(results: list[dict], unit: str = "") -> None:
    if not results:
        print("  (no data)")
        return
    for r in results:
        label = fmt_labels(r["metric"])
        value = float(r["value"][1])
        suffix = f" {unit}" if unit else ""
        print(f"  [{label}] {value:.2f}{suffix}")


def quantile(host: str, q: float, metric: str, op: str, time: str) -> list[dict]:
    promql = f"histogram_quantile({q}, {metric}{{operation_type='{op}'}})"
    return instant(host, promql, time)


def main() -> None:
    if len(sys.argv) != 4:
        print("Usage: query_metrics.py <client_host> <start> <end>")
        sys.exit(1)

    host, start, end = sys.argv[1], sys.argv[2], sys.argv[3]

    print(f"=== Time window: {start} → {end} ===\n")

    # --- Cause A: is the fetch pattern deterministic? ---

    print("--- [Cause A] Bytes fetched on-demand per layer ---")
    print_instant(instant(host, "stargz_layer_fetched_size", end), "bytes")

    print("\n--- [Cause A] On-demand fetch count per layer ---")
    print_instant(
        instant(host, "stargz_fs_operation_count{operation_type='on_demand_remote_registry_fetch_count'}", end)
    )

    # --- Cause B: is per-fetch latency the bottleneck? ---

    print("\n--- [Cause B] Remote registry fetch latency (ms) ---")
    bucket = "stargz_fs_operation_duration_milliseconds_bucket"
    for q, label in [(0.50, "p50"), (0.90, "p90"), (0.99, "p99")]:
        results = quantile(host, q, bucket, "remote_registry_get", end)
        print(f"  {label}:", end=" ")
        if results:
            print(f"{float(results[0]['value'][1]):.2f} ms")
        else:
            print("(no data)")

    # --- Sanity check: total lazy-load duration vs observed env_load ---

    print("\n--- [Sanity] Time from mount to last on-demand fetch (ms) ---")
    for q, label in [(0.50, "p50"), (0.90, "p90")]:
        results = quantile(host, q, bucket, "mount_layer_to_last_on_demand_fetch", end)
        print(f"  {label}:", end=" ")
        if results:
            print(f"{float(results[0]['value'][1]):.2f} ms")
        else:
            print("(no data)")


if __name__ == "__main__":
    main()
