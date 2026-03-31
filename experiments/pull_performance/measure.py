import csv
import os
import subprocess
import time
import uuid
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

from shared import log
from shared.registry import prepare_local_registry, registry, image_slug
from pull_performance.prepare import prepare
from pull_performance.images import (
    pull_name_2dfs, pull_name_2dfs_stargz,
    pull_name_stargz, pull_name_base,
)

# MODEL = "openai-community/gpt2"  # ~500 MB safetensors
MODEL = "openai-community/gpt2-medium"
BASE_IMAGE = "docker.io/library/python:3.12-slim" # 41 MB compressed
# BASE_IMAGE = "docker.io/tensorflow/tensorflow" # 588 MB compressed
NUM_SPLITS = 10
BASE_SPLITS = [2, 4, 6, 8, 10]
IS_LOCAL = False
VERBOSE = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "pull")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "pull")

STARGZ_ROOT = "/var/lib/containerd-stargz-grpc"


# ── helpers ────────────────────────────────────────────────────────


def _run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True, capture_output=not log.VERBOSE)


def _next_container_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def clear_cache(is_local: bool = True) -> None:
    log.info("Clearing stargz cache...")
    # if is_local True then we have a local registry which means we have to be careful with clearing cache
    if is_local:
        _run("sudo ctr -n default images ls -q | grep -v 'tdfs-registry' | xargs -r sudo ctr -n default images rm 2>/dev/null")
        _run(f'grep "{STARGZ_ROOT}/snapshotter/snapshots" /proc/mounts | awk \'{{print $2}}\' | xargs -r sudo umount')
        _run("sudo systemctl daemon-reload")
        _run("sudo systemctl stop stargz-snapshotter")
        _run(f"sudo rm -rf {STARGZ_ROOT}/snapshotter")
        _run(f"sudo rm -rf {STARGZ_ROOT}/stargz")
        _run("sudo systemctl start stargz-snapshotter")
    else:
        _run("sudo systemctl stop stargz-snapshotter")
        _run(f"sudo umount -Rl $(readlink -f {STARGZ_ROOT}/snapshotter) 2>/dev/null || true")
        _run(f"sudo rm -rf {STARGZ_ROOT}/*")
        _run("sudo nerdctl image rm -f $(sudo nerdctl images -q) 2>/dev/null || true")
        _run("sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true")
        _run("sudo systemctl start stargz-snapshotter")
        _run("sudo systemctl restart containerd")


def _timed_pull(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=not log.VERBOSE)
    return time.perf_counter() - start


def _timed_run(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=not log.VERBOSE)
    return time.perf_counter() - start


def _run_cmd(n: int) -> list[str]:
    files = " ".join(f"/chunk{i+1}.bin" for i in range(n))
    return ["/bin/sh", "-c", f"cat {files} > /dev/null"]


# ── pull functions ─────────────────────────────────────────────────


def pull_base(source_image: str, is_local: bool, num_splits: int) -> float:
    image = pull_name_base(source_image, is_local, num_splits)
    log.info(f"Pulling base image: {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  base pull ({num_splits} splits): {elapsed:.2f}s")
    return elapsed


def pull_stargz(source_image: str, is_local: bool) -> float:
    image = pull_name_stargz(source_image, is_local)
    log.info(f"Pulling stargz image: {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  stargz pull: {elapsed:.2f}s")
    return elapsed


def pull_2dfs(source_image: str, is_local: bool, num_allotments: int) -> float:
    image = pull_name_2dfs(source_image, is_local, num_allotments)
    log.info(f"Pulling 2dfs ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  2dfs pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


def pull_2dfs_stargz(source_image: str, is_local: bool, num_allotments: int) -> float:
    image = pull_name_2dfs_stargz(source_image, is_local, num_allotments)
    log.info(f"Pulling 2dfs-stargz ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  2dfs-stargz pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


# ── run functions ──────────────────────────────────────────────────


def run_base(image: str, n: int) -> float:
    name = _next_container_name("run-base")
    log.info(f"Running base container: {name} (reading {n} chunks)")
    elapsed = _timed_run([
        "sudo", "ctr", "run", "--rm", image, name, *_run_cmd(n),
    ])
    log.result(f"  base run: {elapsed:.2f}s")
    return elapsed


def run_stargz(image: str, n: int) -> float:
    name = _next_container_name("run-stargz")
    log.info(f"Running stargz container: {name} (reading {n} chunks)")
    elapsed = _timed_run([
        "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
        image, name, *_run_cmd(n),
    ])
    log.result(f"  stargz run: {elapsed:.2f}s")
    return elapsed


def run_2dfs(image: str, n: int) -> float:
    name = _next_container_name("run-2dfs")
    log.info(f"Running 2dfs container: {name} (reading {n} chunks)")
    elapsed = _timed_run([
        "sudo", "ctr", "run", "--rm", image, name, *_run_cmd(n),
    ])
    log.result(f"  2dfs run: {elapsed:.2f}s")
    return elapsed


def run_2dfs_stargz(image: str, n: int) -> float:
    name = _next_container_name("run-2dfs-stargz")
    log.info(f"Running 2dfs-stargz container: {name} (reading {n} chunks)")
    elapsed = _timed_run([
        "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
        image, name, *_run_cmd(n),
    ])
    log.result(f"  2dfs-stargz run: {elapsed:.2f}s")
    return elapsed


# ── orchestration ──────────────────────────────────────────────────


def measure(
    base_splits: list[int], source_image: str, is_local: bool,
) -> tuple[list[tuple[int, float, float]], list[tuple[int, float, float]],
           list[tuple[int, float, float]], list[tuple[int, float, float]]]:
    results_2dfs: list[tuple[int, float, float]] = []
    results_2dfs_stargz: list[tuple[int, float, float]] = []
    results_stargz: list[tuple[int, float, float]] = []
    results_base: list[tuple[int, float, float]] = []

    for n in base_splits:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        log.info(f"\n[{ts}] === base: {n} splits ===")
        clear_cache(is_local)
        pull_t = pull_base(source_image, is_local, n)
        run_t = run_base(pull_name_base(source_image, is_local, n), n)
        results_base.append((n, pull_t, run_t))

        log.info(f"\n[{ts}] === stargz (full image) ===")
        clear_cache(is_local)
        pull_t = pull_stargz(source_image, is_local)
        run_t = run_stargz(pull_name_stargz(source_image, is_local), n)
        results_stargz.append((n, pull_t, run_t))

        log.info(f"\n[{ts}] === 2dfs: {n} allotments ===")
        clear_cache(is_local)
        pull_t = pull_2dfs(source_image, is_local, n)
        run_t = run_2dfs(pull_name_2dfs(source_image, is_local, n), n)
        results_2dfs.append((n, pull_t, run_t))

        log.info(f"\n[{ts}] === 2dfs-stargz: {n} allotments ===")
        clear_cache(is_local)
        pull_t = pull_2dfs_stargz(source_image, is_local, n)
        run_t = run_2dfs_stargz(pull_name_2dfs_stargz(source_image, is_local, n), n)
        results_2dfs_stargz.append((n, pull_t, run_t))

    return results_2dfs, results_2dfs_stargz, results_stargz, results_base


# ── output ─────────────────────────────────────────────────────────


def print_results(
    results_2dfs: list[tuple[int, float, float]],
    results_2dfs_stargz: list[tuple[int, float, float]],
    results_stargz: list[tuple[int, float, float]],
    results_base: list[tuple[int, float, float]],
) -> None:
    splits = [n for n, _, _ in results_base]
    log.result("\n=== Pull + Run Performance Results ===")
    log.result(f"{'splits':>8}  {'2dfs':>18}  {'2dfs+stargz':>18}  {'stargz':>18}  {'base':>18}")
    log.result(f"{'':>8}  {'pull/run/total':>18}  {'pull/run/total':>18}  {'pull/run/total':>18}  {'pull/run/total':>18}")
    log.result("-" * 90)
    for i, n in enumerate(splits):
        def fmt(r: list[tuple[int, float, float]], idx: int) -> str:
            _, p, r_t = r[idx]
            return f"{p:.1f}/{r_t:.1f}/{p+r_t:.1f}"
        log.result(f"{n:>8}  {fmt(results_2dfs, i):>18}  {fmt(results_2dfs_stargz, i):>18}  {fmt(results_stargz, i):>18}  {fmt(results_base, i):>18}")


def save_csv(
    results_2dfs: list[tuple[int, float, float]],
    results_2dfs_stargz: list[tuple[int, float, float]],
    results_stargz: list[tuple[int, float, float]],
    results_base: list[tuple[int, float, float]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    splits = [n for n, _, _ in results_base]
    output_path = os.path.join(RESULTS_DIR, f"{model_slug}_{img_slug}_pull_{len(splits)}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "splits",
            "2dfs_pull_s", "2dfs_run_s", "2dfs_total_s",
            "2dfs_stargz_pull_s", "2dfs_stargz_run_s", "2dfs_stargz_total_s",
            "stargz_pull_s", "stargz_run_s", "stargz_total_s",
            "base_pull_s", "base_run_s", "base_total_s",
        ])
        for i in range(len(splits)):
            def row(r: list[tuple[int, float, float]], idx: int) -> list[str]:
                _, p, r_t = r[idx]
                return [f"{p:.4f}", f"{r_t:.4f}", f"{p+r_t:.4f}"]
            writer.writerow([
                splits[i],
                *row(results_2dfs, i),
                *row(results_2dfs_stargz, i),
                *row(results_stargz, i),
                *row(results_base, i),
            ])
    log.result(f"Results saved to {output_path}")


def plot(
    results_2dfs: list[tuple[int, float, float]],
    results_2dfs_stargz: list[tuple[int, float, float]],
    results_stargz: list[tuple[int, float, float]],
    results_base: list[tuple[int, float, float]],
    model: str,
    base_image: str,
) -> None:
    splits = [n for n, _, _ in results_base]
    x = np.arange(len(splits))
    width = 0.18

    methods = [
        ("2dfs", results_2dfs, "#1f77b4"),
        ("2dfs+stargz", results_2dfs_stargz, "#ff7f0e"),
        ("stargz", results_stargz, "#2ca02c"),
        ("base", results_base, "#d62728"),
    ]

    fig, ax = plt.subplots(figsize=(10, 6))

    for i, (label, results, color) in enumerate(methods):
        pulls = [p for _, p, _ in results]
        runs = [r for _, _, r in results]
        offset = (i - 1.5) * width
        # Pull portion (bottom, hatched)
        ax.bar(x + offset, pulls, width, color=color, alpha=0.5,
               hatch="//", edgecolor=color, linewidth=0.5)
        # Run portion (top, solid)
        ax.bar(x + offset, runs, width, bottom=pulls, color=color,
               edgecolor=color, linewidth=0.5, label=label)

    ax.set_xlabel("Number of splits pulled")
    ax.set_ylabel("Time (s)")
    ax.set_title("Pull + Run Performance")
    ax.set_xticks(x)
    ax.set_xticklabels(splits)
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    # Legend: method colors + pull/run distinction
    method_handles = [mpatches.Patch(facecolor=c, edgecolor=c, label=l)
                      for l, _, c in methods]
    pull_patch = mpatches.Patch(facecolor="gray", alpha=0.5, hatch="//",
                                edgecolor="gray", label="pull")
    run_patch = mpatches.Patch(facecolor="gray", edgecolor="gray", label="run")
    ax.legend(handles=method_handles + [pull_patch, run_patch], loc="upper left")

    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")

    os.makedirs(CHARTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_DIR, f"{model_slug}_{img_slug}_pull_{len(splits)}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    log.result(f"Chart saved to {output_path}")


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    log.info(f"Model: {MODEL}")
    log.info(f"Splits (2dfs/stargz): {NUM_SPLITS}")
    log.info(f"Splits (base): {BASE_SPLITS}")

    prepare_local_registry(BASE_IMAGE, registry(IS_LOCAL))

    prepare(MODEL, NUM_SPLITS, BASE_SPLITS, BASE_IMAGE, IS_LOCAL)

    results_2dfs, results_2dfs_stargz, results_stargz, results_base = measure(
        BASE_SPLITS, BASE_IMAGE, IS_LOCAL,
    )

    print_results(results_2dfs, results_2dfs_stargz, results_stargz, results_base)
    save_csv(results_2dfs, results_2dfs_stargz, results_stargz, results_base, MODEL, BASE_IMAGE)
    plot(results_2dfs, results_2dfs_stargz, results_stargz, results_base, MODEL, BASE_IMAGE)


if __name__ == "__main__":
    main()
