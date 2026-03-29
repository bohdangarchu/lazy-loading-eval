import csv
import os
import subprocess
import time
from datetime import datetime, timezone

import matplotlib.pyplot as plt

from shared import log
from pull_performance.prepare import prepare
from pull_performance.images import (
    pull_name_2dfs, pull_name_2dfs_stargz,
    pull_name_stargz, pull_name_base,
)

MODEL = "openai-community/gpt2"  # ~500 MB safetensors
NUM_SPLITS = 10
BASE_SPLITS = [2, 4, 6]
IS_LOCAL = True
VERBOSE = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "pull")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "pull")

STARGZ_ROOT = "/var/lib/containerd-stargz-grpc"


# ── helpers ────────────────────────────────────────────────────────


def _run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True, capture_output=not log.VERBOSE)


def clear_cache(image: str | None = None) -> None:
    log.info("Clearing stargz cache...")
    # Remove all images except the registry container
    _run("sudo ctr -n default images ls -q | grep -v 'tdfs-registry' | xargs -r sudo ctr -n default images rm 2>/dev/null")
    # Unmount FUSE mounts while stargz-snapshotter is still running
    _run(f'grep "{STARGZ_ROOT}/snapshotter/snapshots" /proc/mounts | awk \'{{print $2}}\' | xargs -r sudo umount')
    _run("sudo systemctl daemon-reload")
    _run("sudo systemctl stop stargz-snapshotter")
    _run(f"sudo rm -rf {STARGZ_ROOT}/snapshotter")
    _run(f"sudo rm -rf {STARGZ_ROOT}/stargz")
    _run("sudo systemctl start stargz-snapshotter")


def _timed_pull(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=not log.VERBOSE)
    return time.perf_counter() - start


# ── pull functions ─────────────────────────────────────────────────


def pull_base(is_local: bool, num_splits: int) -> float:
    image = pull_name_base(is_local, num_splits)
    log.info(f"Pulling base image: {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  base ({num_splits} splits): {elapsed:.2f}s")
    return elapsed


def pull_stargz(is_local: bool) -> float:
    image = pull_name_stargz(is_local)
    log.info(f"Pulling stargz image: {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  stargz: {elapsed:.2f}s")
    return elapsed


def pull_2dfs(is_local: bool, num_allotments: int) -> float:
    image = pull_name_2dfs(is_local, num_allotments)
    log.info(f"Pulling 2dfs ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  2dfs ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


def pull_2dfs_stargz(is_local: bool, num_allotments: int) -> float:
    image = pull_name_2dfs_stargz(is_local, num_allotments)
    log.info(f"Pulling 2dfs-stargz ({num_allotments} allotments): {image}")
    elapsed = _timed_pull([
        "sudo", "ctr-remote", "images", "rpull", "--plain-http",
        "--use-containerd-labels", image,
    ])
    log.result(f"  2dfs-stargz ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


# ── orchestration ──────────────────────────────────────────────────


def measure_pulls(
    base_splits: list[int], is_local: bool,
) -> tuple[list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]], list[tuple[int, float]]]:
    results_2dfs: list[tuple[int, float]] = []
    results_2dfs_stargz: list[tuple[int, float]] = []
    results_stargz: list[tuple[int, float]] = []
    results_base: list[tuple[int, float]] = []

    prev_image: str | None = None

    for n in base_splits:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        log.info(f"\n[{ts}] === base: {n} splits ===")
        clear_cache(prev_image)
        elapsed = pull_base(is_local, n)
        prev_image = pull_name_base(is_local, n)
        results_base.append((n, elapsed))

        log.info(f"\n[{ts}] === stargz (full image) ===")
        clear_cache(prev_image)
        elapsed = pull_stargz(is_local)
        prev_image = pull_name_stargz(is_local)
        results_stargz.append((n, elapsed))

        log.info(f"\n[{ts}] === 2dfs: {n} allotments ===")
        clear_cache(prev_image)
        elapsed = pull_2dfs(is_local, n)
        prev_image = pull_name_2dfs(is_local, n)
        results_2dfs.append((n, elapsed))

        log.info(f"\n[{ts}] === 2dfs-stargz: {n} allotments ===")
        clear_cache(prev_image)
        elapsed = pull_2dfs_stargz(is_local, n)
        prev_image = pull_name_2dfs_stargz(is_local, n)
        results_2dfs_stargz.append((n, elapsed))

    return results_2dfs, results_2dfs_stargz, results_stargz, results_base


# ── output ─────────────────────────────────────────────────────────


def print_results(
    results_2dfs: list[tuple[int, float]],
    results_2dfs_stargz: list[tuple[int, float]],
    results_stargz: list[tuple[int, float]],
    results_base: list[tuple[int, float]],
) -> None:
    splits = [n for n, _ in results_base]
    log.result("\n=== Pull Performance Results ===")
    log.result(f"{'splits':>8}  {'2dfs (s)':>12}  {'2dfs+stargz (s)':>16}  {'stargz (s)':>12}  {'base (s)':>10}")
    log.result("-" * 68)
    for i, n in enumerate(splits):
        t1 = results_2dfs[i][1]
        t2 = results_2dfs_stargz[i][1]
        t3 = results_stargz[i][1]
        t4 = results_base[i][1]
        log.result(f"{n:>8}  {t1:>12.2f}  {t2:>16.2f}  {t3:>12.2f}  {t4:>10.2f}")


def save_csv(
    results_2dfs: list[tuple[int, float]],
    results_2dfs_stargz: list[tuple[int, float]],
    results_stargz: list[tuple[int, float]],
    results_base: list[tuple[int, float]],
    model: str,
) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    splits = [n for n, _ in results_base]
    output_path = os.path.join(RESULTS_DIR, f"{model_slug}_pull_{len(splits)}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["splits", "2dfs_s", "2dfs_stargz_s", "stargz_s", "base_s"])
        for i in range(len(splits)):
            writer.writerow([
                splits[i],
                f"{results_2dfs[i][1]:.4f}",
                f"{results_2dfs_stargz[i][1]:.4f}",
                f"{results_stargz[i][1]:.4f}",
                f"{results_base[i][1]:.4f}",
            ])
    log.result(f"Results saved to {output_path}")


def plot(
    results_2dfs: list[tuple[int, float]],
    results_2dfs_stargz: list[tuple[int, float]],
    results_stargz: list[tuple[int, float]],
    results_base: list[tuple[int, float]],
    model: str,
) -> None:
    splits = [n for n, _ in results_base]

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(splits, [t for _, t in results_2dfs], marker="o", label="2dfs")
    ax.plot(splits, [t for _, t in results_2dfs_stargz], marker="o", label="2dfs+stargz")
    ax.plot(splits, [t for _, t in results_stargz], marker="o", label="stargz")
    ax.plot(splits, [t for _, t in results_base], marker="o", label="base")
    ax.set_xlabel("Number of splits pulled")
    ax.set_ylabel("Pull time (s)")
    ax.set_title(f"Pull performance — {model}")
    ax.set_xticks(splits)
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)

    os.makedirs(CHARTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_DIR, f"{model_slug}_pull_{len(splits)}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    log.result(f"Chart saved to {output_path}")


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    log.info(f"Model: {MODEL}")
    log.info(f"Splits (2dfs/stargz): {NUM_SPLITS}")
    log.info(f"Splits (base): {BASE_SPLITS}")

    prepare(MODEL, NUM_SPLITS, BASE_SPLITS, IS_LOCAL)

    results_2dfs, results_2dfs_stargz, results_stargz, results_base = measure_pulls(
        BASE_SPLITS, IS_LOCAL,
    )

    print_results(results_2dfs, results_2dfs_stargz, results_stargz, results_base)
    save_csv(results_2dfs, results_2dfs_stargz, results_stargz, results_base, MODEL)
    plot(results_2dfs, results_2dfs_stargz, results_stargz, results_base, MODEL)


if __name__ == "__main__":
    main()
