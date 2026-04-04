import argparse
import os
import subprocess
import time
from datetime import datetime, timezone

from shared import log
from shared.build_result import BuildResult
from shared.registry import base_image, tdfs_cmd
from shared.tdfs_parser import parse_tdfs_output
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


def build_only(n: int, is_local: bool = True, source_image: str = "") -> BuildResult:
    target = f"{REGISTRY}/build-perf-stargz:{n}"
    cmd = tdfs_cmd(is_local, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--enable-stargz",
        "--stargz-compression-level", "1",
        "--force-http",
        "-f", "2dfs.json",
        base_image(source_image, is_local),
        target,
    ]

    log.info(f"=== Building with {n} split(s) (2dfs-stargz) ===")
    start = time.perf_counter()
    env = {**os.environ, "TMPDIR": "/mydata/tmp"} if not is_local else None
    result = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True, env=env)
    elapsed = time.perf_counter() - start

    if result.returncode != 0:
        log.info(result.stdout + result.stderr)
        result.check_returncode()

    output = result.stdout + result.stderr
    if log.VERBOSE:
        log.info(output)

    br = parse_tdfs_output(output, elapsed)
    log.result(f"Build time for {n} split(s): {elapsed:.2f}s "
               f"(pull={br.pull_s:.2f} build={br.build_s:.2f} export={br.export_s:.2f})")
    return br


def run_one(model: str, n: int, is_local: bool = True, source_image: str = "") -> BuildResult:
    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing {n} split(s) ===")
    subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True, capture_output=not log.VERBOSE)
    prepare(model, n, source_image, is_local)

    br = build_only(n, is_local, source_image)

    clear_cache(is_local)
    return br


def run(model: str, max_splits: int, is_local: bool = True, source_image: str = "") -> list[tuple[int, BuildResult]]:
    clear_cache(is_local)
    results = []
    for n in range(1, max_splits + 1):
        br = run_one(model, n, is_local, source_image)
        results.append((n, br))
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
