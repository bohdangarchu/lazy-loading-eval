import argparse
import os
import subprocess
import time
from datetime import datetime, timezone

from shared import log
from shared.registry import base_image, tdfs_cmd
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")
REGISTRY = "localhost:5000"
CLEAR_CACHE_LOCAL = "rm -rf ~/.2dfs/blobs/* ~/.2dfs/uncompressed-keys/* ~/.2dfs/index/*"
CLEAR_CACHE_REMOTE = "sudo rm -rf /mydata/.2dfs/blobs/* /mydata/.2dfs/uncompressed-keys/* /mydata/.2dfs/index/*"


def clear_cache(is_local: bool = True) -> None:
    cache_cmd = CLEAR_CACHE_LOCAL if is_local else CLEAR_CACHE_REMOTE
    log.info("=== Clearing 2dfs cache ===")
    subprocess.run(cache_cmd, shell=True, check=True, capture_output=not log.VERBOSE)


def build_only(n: int, is_local: bool = True) -> float:
    target = f"{REGISTRY}/build-perf-stargz:{n}"
    cmd = tdfs_cmd(is_local, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--enable-stargz",
        "--force-http",
        "-f", "2dfs.json",
        base_image(is_local),
        target,
    ]

    log.info(f"=== Building with {n} split(s) (2dfs-stargz) ===")
    start = time.perf_counter()
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR, capture_output=not log.VERBOSE)
    elapsed = time.perf_counter() - start

    log.result(f"Build time for {n} split(s): {elapsed:.2f}s")
    return elapsed


def run_one(model: str, n: int, is_local: bool = True) -> float:
    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing {n} split(s) ===")
    subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True, capture_output=not log.VERBOSE)
    prepare(model, n, is_local)

    elapsed = build_only(n, is_local)

    clear_cache(is_local)
    return elapsed


def run(model: str, max_splits: int, is_local: bool = True) -> list[tuple[int, float]]:
    clear_cache(is_local)
    results = []
    for n in range(1, max_splits + 1):
        elapsed = run_one(model, n, is_local)
        results.append((n, elapsed))
    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark tdfs build --enable-stargz across split counts")
    parser.add_argument("--model", required=True, help="HuggingFace model name")
    parser.add_argument("--max-splits", type=int, required=True, help="Maximum number of splits")
    parser.add_argument("--is-local", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    results = run(args.model, args.max_splits, args.is_local)

    log.result("\n=== Results ===")
    log.result(f"{'splits':>8}  {'seconds':>10}")
    log.result("-" * 22)
    for n, t in results:
        log.result(f"{n:>8}  {t:>10.2f}")


if __name__ == "__main__":
    main()
