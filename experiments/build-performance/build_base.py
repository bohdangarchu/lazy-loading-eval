import argparse
import os
import subprocess
import time
from datetime import datetime, timezone

from prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")
REGISTRY = "localhost:5000"


def clear_cache() -> None:
    print("=== Pruning buildkit cache ===")
    subprocess.run(["sudo", "buildctl", "prune", "--all"], check=True)


def build_only(n: int) -> float:
    target = f"{REGISTRY}/build-perf-base:{n}"
    cmd = [
        "sudo", "buildctl", "build",
        "--frontend", "dockerfile.v0",
        "--opt", "filename=Dockerfile.base",
        "--local", f"context={SCRIPT_DIR}",
        "--local", f"dockerfile={SCRIPT_DIR}",
        "--output", f"type=image,name={target},push=false,registry.insecure=true",
    ]

    print(f"=== Building with {n} split(s) (base) ===")
    start = time.perf_counter()
    subprocess.run(cmd, check=True, cwd=SCRIPT_DIR)
    elapsed = time.perf_counter() - start

    print(f"Build time for {n} split(s): {elapsed:.2f}s")
    return elapsed


def run_one(model: str, n: int) -> float:
    print(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing {n} split(s) ===")
    subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True)
    prepare(model, n)

    elapsed = build_only(n)

    clear_cache()
    return elapsed


def run(model: str, max_splits: int) -> list[tuple[int, float]]:
    clear_cache()
    results = []
    for n in range(1, max_splits + 1):
        elapsed = run_one(model, n)
        results.append((n, elapsed))
    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark base (plain) build across split counts")
    parser.add_argument("--model", required=True, help="HuggingFace model name")
    parser.add_argument("--max-splits", type=int, required=True, help="Maximum number of splits")
    args = parser.parse_args()

    results = run(args.model, args.max_splits)

    print("\n=== Results ===")
    print(f"{'splits':>8}  {'seconds':>10}")
    print("-" * 22)
    for n, t in results:
        print(f"{n:>8}  {t:>10.2f}")


if __name__ == "__main__":
    main()
