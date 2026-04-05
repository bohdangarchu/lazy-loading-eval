import argparse
import os
import subprocess
import time
from datetime import datetime, timezone

from shared import log
from shared.build_result import BuildResult
from shared.buildctl_parser import parse_buildctl_plain
from shared.config import EnvConfig, load_config
from shared.registry import registry
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")


def clear_cache() -> None:
    log.info("=== Pruning buildkit cache ===")
    subprocess.run(["sudo", "buildctl", "prune", "--all"], check=True, capture_output=not log.VERBOSE)


def build_only(n: int, cfg: EnvConfig = None) -> BuildResult:
    target = f"{registry(cfg)}/build-perf-base:{n}"
    cmd = [
        "sudo", "buildctl", "build",
        "--progress=plain",
        "--frontend", "dockerfile.v0",
        "--opt", "filename=Dockerfile.base",
        "--local", f"context={SCRIPT_DIR}",
        "--local", f"dockerfile={SCRIPT_DIR}",
        "--output", f"type=image,name={target},push=false,registry.insecure=true",
    ]

    log.info(f"=== Building with {n} split(s) (base) ===")
    start = time.perf_counter()
    result = subprocess.run(cmd, cwd=SCRIPT_DIR, capture_output=True, text=True)
    elapsed = time.perf_counter() - start

    if result.returncode != 0:
        log.info(result.stderr)
        result.check_returncode()

    if log.VERBOSE:
        log.info(result.stderr)

    br = parse_buildctl_plain(result.stderr, elapsed)
    log.result(f"Build time for {n} split(s): {elapsed:.2f}s "
               f"(pull={br.pull_s:.2f} ctx={br.context_transfer_s:.2f} "
               f"build={br.build_s:.2f} export={br.export_s:.2f})")
    return br


def run_one(model: str, n: int, cfg: EnvConfig = None, source_image: str = "") -> BuildResult:
    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing {n} split(s) ===")
    subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True, capture_output=not log.VERBOSE)
    prepare(model, n, source_image, cfg)

    br = build_only(n, cfg)

    clear_cache()
    return br


def run(model: str, max_splits: int, cfg: EnvConfig = None, source_image: str = "") -> list[tuple[int, BuildResult]]:
    clear_cache()
    results = []
    for n in range(1, max_splits + 1):
        br = run_one(model, n, cfg, source_image)
        results.append((n, br))
    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark base (plain) build across split counts")
    parser.add_argument("--model", required=True, help="HuggingFace model name")
    parser.add_argument("--max-splits", type=int, required=True, help="Maximum number of splits")
    args = parser.parse_args()

    results = run(args.model, args.max_splits, load_config())

    log.result("\n=== Results ===")
    log.result(f"{'splits':>8}  {'seconds':>10}")
    log.result("-" * 22)
    for n, t in results:
        log.result(f"{n:>8}  {t:>10.2f}")


if __name__ == "__main__":
    main()
