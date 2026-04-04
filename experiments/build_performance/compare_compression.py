#!/usr/bin/env python3
import os
import subprocess
import time
from datetime import datetime, timezone

from shared import log
from shared.registry import prepare_local_registry, registry, base_image, tdfs_cmd
from shared.build_result import BuildResult
from shared.tdfs_parser import parse_tdfs_output
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")

MODEL = "openai-community/gpt2-medium"
SOURCE_IMAGE = "docker.io/library/python:3.12-slim"
MAX_SPLITS = 10
IS_LOCAL = False

CLEAR_CACHE_CMD = "sudo rm -rf /mydata/.2dfs/blobs/* /mydata/.2dfs/uncompressed-keys/* /mydata/.2dfs/index/*"

LEVELS = [
    ("2dfs (level -1, default)", "-1"),
    ("2dfs (level 1, best-speed)", "1"),
]


def clear_cache() -> None:
    log.info("=== Clearing 2dfs cache ===")
    subprocess.run(CLEAR_CACHE_CMD, shell=True, check=True, capture_output=not log.VERBOSE)


def build_one(n: int, level: str) -> BuildResult:
    target = f"{registry(IS_LOCAL)}/compression-test-level{level.replace('-', 'n')}:{n}"
    cmd = tdfs_cmd(IS_LOCAL, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--force-http",
        "-f", "2dfs.json",
        "--compression-level", level,
        base_image(SOURCE_IMAGE, IS_LOCAL),
        target,
    ]

    env = {**os.environ, "TMPDIR": "/mydata/tmp"}
    start = time.perf_counter()
    result = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True, env=env)
    elapsed = time.perf_counter() - start

    if result.returncode != 0:
        log.info(result.stdout + result.stderr)
        result.check_returncode()

    output = result.stdout + result.stderr
    if log.VERBOSE:
        log.info(output)

    br = parse_tdfs_output(output, elapsed)
    log.result(f"  splits={n} total={elapsed:.2f}s pull={br.pull_s:.2f}s build={br.build_s:.2f}s export={br.export_s:.2f}s")
    return br


def image_ls() -> None:
    log.result("--- tdfs image ls ---")
    cmd = tdfs_cmd(IS_LOCAL, SCRIPT_DIR) + ["image", "ls"]
    subprocess.run(cmd, cwd=SCRIPT_DIR)


def run_level(label: str, level: str) -> list[tuple[int, BuildResult]]:
    log.result(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === {label} ===")
    clear_cache()
    results = []
    for n in range(1, MAX_SPLITS + 1):
        log.info(f"\n=== Preparing {n} split(s) ===")
        subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True, capture_output=not log.VERBOSE)
        prepare(MODEL, n, SOURCE_IMAGE, IS_LOCAL)
        br = build_one(n, level)
        clear_cache()
        results.append((n, br))
    return results


def main():
    log.set_verbose(True)

    prepare_local_registry(SOURCE_IMAGE, registry(IS_LOCAL))

    all_results: list[tuple[str, list[tuple[int, BuildResult]]]] = []
    for label, level in LEVELS:
        results = run_level(label, level)
        image_ls()
        all_results.append((label, results))

    log.result("\n=== Summary ===")
    header = f"{'splits':>8}" + "".join(f"  {label:>28}" for label, _ in all_results)
    log.result(header)
    log.result("-" * (8 + 30 * len(all_results)))
    for i in range(MAX_SPLITS):
        n = i + 1
        row = f"{n:>8}"
        for _, results in all_results:
            _, br = results[i]
            row += f"  {br.total_s:>28.2f}"
        log.result(row)


if __name__ == "__main__":
    main()
