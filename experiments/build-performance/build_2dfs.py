import argparse
import os
import subprocess
import time

from prepare import BASE_IMAGE, prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")
REGISTRY = "localhost:5000"
CLEAR_CACHE_LOCAL = "rm -rf ~/.2dfs/blobs/* ~/.2dfs/uncompressed-keys/* ~/.2dfs/index/*"
CLEAR_CACHE_REMOTE = "rm -rf /mydata/.2dfs/blobs/* /mydata/.2dfs/uncompressed-keys/* /mydata/.2dfs/index/*"


def run(model: str, max_splits: int, is_local: bool = True) -> list[tuple[int, float]]:
    results = []

    tdfs_cmd = [os.path.join(SCRIPT_DIR, "tdfs")] if is_local else ["sudo", "tdfs", "--home-dir", "/mydata/.2dfs"]
    clear_cache = CLEAR_CACHE_LOCAL if is_local else CLEAR_CACHE_REMOTE
    run_kwargs: dict = {"cwd": SCRIPT_DIR}

    for n in range(1, max_splits + 1):
        print(f"\n=== Preparing {n} split(s) ===")
        subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True)
        prepare(model, n)

        target = f"{REGISTRY}/build-perf:{n}"
        cmd = tdfs_cmd + [
            "build",
            "--platforms", "linux/amd64",
            "--force-http",
            "-f", "2dfs.json",
            BASE_IMAGE,
            target,
        ]

        print(f"=== Building with {n} split(s) (2dfs) ===")
        start = time.perf_counter()
        subprocess.run(cmd, check=True, **run_kwargs)
        elapsed = time.perf_counter() - start

        print(f"Build time for {n} split(s): {elapsed:.2f}s")
        results.append((n, elapsed))

        print(f"=== Clearing 2dfs cache ===")
        subprocess.run(clear_cache, shell=True, check=True)

    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark tdfs build across split counts")
    parser.add_argument("--model", required=True, help="HuggingFace model name")
    parser.add_argument("--max-splits", type=int, required=True, help="Maximum number of splits")
    parser.add_argument("--is-local", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    results = run(args.model, args.max_splits, args.is_local)

    print("\n=== Results ===")
    print(f"{'splits':>8}  {'seconds':>10}")
    print("-" * 22)
    for n, t in results:
        print(f"{n:>8}  {t:>10.2f}")


if __name__ == "__main__":
    main()
