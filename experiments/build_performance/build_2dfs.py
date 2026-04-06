import os
import argparse
import subprocess
import time
from datetime import datetime, timezone

from shared import log
from shared.build_result import BuildResult
from shared.config import EnvConfig, load_config
from shared.registry import plain_base_image, tdfs_cmd, registry
from shared.tdfs_parser import parse_tdfs_output
from build_performance.prepare import prepare

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNKS_DIR = os.path.join(SCRIPT_DIR, "chunks")


def clear_cache(cfg: EnvConfig) -> None:
    home = cfg.tdfs_home_dir or "~/.2dfs"
    cmd = f"rm -rf {home}/blobs/* {home}/uncompressed-keys/* {home}/index/*"
    if not cfg.tdfs_binary.startswith("./"):
        cmd = "sudo " + cmd
    log.info("=== Clearing 2dfs cache ===")
    subprocess.run(cmd, shell=True, check=True, capture_output=not log.VERBOSE)


def build_only(n: int, cfg: EnvConfig, source_image: str = "") -> BuildResult:
    target = f"{registry(cfg)}/build-perf:{n}"
    cmd = tdfs_cmd(cfg, SCRIPT_DIR) + [
        "build",
        "--platforms", "linux/amd64",
        "--force-http",
        "--compression-level", "1",
        "-f", "2dfs.json",
        plain_base_image(source_image, cfg),
        target,
    ]

    log.info(f"=== Building with {n} split(s) (2dfs) ===")
    start = time.perf_counter()
    env = {**os.environ, "TMPDIR": cfg.tmpdir} if cfg.tmpdir else None
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


def run_one(model: str, n: int, cfg: EnvConfig, source_image: str = "") -> BuildResult:
    log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing {n} split(s) ===")
    subprocess.run(f"rm -rf {CHUNKS_DIR}/*", shell=True, check=True, capture_output=not log.VERBOSE)
    prepare(model, n, source_image, cfg)

    br = build_only(n, cfg, source_image)

    clear_cache(cfg)
    return br


def run(model: str, max_splits: int, cfg: EnvConfig, source_image: str = "") -> list[tuple[int, BuildResult]]:
    clear_cache(cfg)
    results = []
    for n in range(1, max_splits + 1):
        br = run_one(model, n, cfg, source_image)
        results.append((n, br))
    return results


def main():
    parser = argparse.ArgumentParser(description="Benchmark tdfs build across split counts")
    parser.add_argument("--model", required=True, help="HuggingFace model name")
    parser.add_argument("--max-splits", type=int, required=True, help="Maximum number of splits")
    args = parser.parse_args()

    cfg = load_config()
    results = run(args.model, args.max_splits, cfg)

    log.result("\n=== Results ===")
    log.result(f"{'splits':>8}  {'seconds':>10}")
    log.result("-" * 22)
    for n, t in results:
        log.result(f"{n:>8}  {t:>10.2f}")


if __name__ == "__main__":
    main()
