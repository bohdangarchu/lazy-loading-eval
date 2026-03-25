import argparse
import os
import subprocess
import time

from prepare import BASE_IMAGE, prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TDFS = os.path.join(SCRIPT_DIR, "tdfs")
CLEAR_CACHE = "rm -rf ~/.2dfs/blobs/* ~/.2dfs/uncompressed-keys/* ~/.2dfs/index/*"
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")
REGISTRY = "localhost:5000"


def run(model: str, max_splits: int) -> list[tuple[int, float]]:
    results = []

    for n in range(1, max_splits + 1):
        print(f"\n=== Preparing {n} split(s) ===")
        subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True)
        prepare(model, n)

        target = f"{REGISTRY}/build-perf-stargz:{n}"
        cmd = [
            TDFS, "build",
            "--platforms", "linux/amd64",
            "--enable-stargz",
            "--force-http",
            "-f", "2dfs.json",
            BASE_IMAGE,
            target,
        ]

        print(f"=== Building with {n} split(s) (stargz) ===")
        start = time.perf_counter()
        subprocess.run(cmd, check=True, cwd=SCRIPT_DIR)
        elapsed = time.perf_counter() - start

        print(f"Build time for {n} split(s): {elapsed:.2f}s")
        results.append((n, elapsed))

        print(f"=== Clearing 2dfs cache ===")
        subprocess.run(CLEAR_CACHE, shell=True, check=True)

    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark tdfs build --enable-stargz across split counts")
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
