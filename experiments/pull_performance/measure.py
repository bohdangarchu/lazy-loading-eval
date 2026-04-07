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
from shared.charts import MODE_COLORS, figure_footer, save_figure
from shared.config import load_config
from shared.registry import prepare_local_registry, clear_registry, registry, image_slug
from shared.services import ensure_buildkit
from pull_performance.prepare import (
    prepare_chunks,
    prepare_2dfs, prepare_2dfs_stargz, prepare_2dfs_stargz_zstd,
    prepare_stargz, prepare_base,
)
from pull_performance.images import (
    pull_name_2dfs, pull_name_2dfs_stargz, pull_name_2dfs_stargz_zstd,
    pull_name_stargz, pull_name_base,
)

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5 GB     ~50 MB
    # ("openai-community/gpt2-medium", "docker.io/tensorflow/tensorflow"),     # ~1.52 GB    ~700 MB
    # ("openai-community/gpt2-large", "docker.io/ollama/ollama"),            # ~3.25 GB    ~3.4 GB
    # ("openai-community/gpt2-xl", "docker.io/library/python:3.12-slim"),    # ~6.0 GB     ~50 MB
]
NUM_SPLITS = 10
BASE_SPLITS = [2, 4, 6, 8, 10]
CFG = load_config()
VERBOSE = True
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
# MODES = ["2dfs-stargz"]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results", "pull")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts", "pull")

STARGZ_ROOT = "/var/lib/containerd-stargz-grpc"


# ── helpers ────────────────────────────────────────────────────────


def _run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True, capture_output=not log.VERBOSE)


def _next_container_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def clear_cache(cfg) -> None:
    log.info("Clearing stargz cache...")
    if not cfg.full_cache_wipe:
        # Selective cleanup — preserves tdfs-registry images (safe for local dev)
        _run("sudo ctr -n default images ls -q | grep -v 'tdfs-registry' | xargs -r sudo ctr -n default images rm 2>/dev/null")
        _run(f'grep "{STARGZ_ROOT}/snapshotter/snapshots" /proc/mounts | awk \'{{print $2}}\' | xargs -r sudo umount')
        _run("sudo systemctl daemon-reload")
        _run("sudo systemctl stop stargz-snapshotter")
        _run(f"sudo rm -rf {STARGZ_ROOT}/snapshotter")
        _run(f"sudo rm -rf {STARGZ_ROOT}/stargz")
        _run("sudo systemctl start stargz-snapshotter")
    else:
        # Full wipe — removes all images and restarts containerd
        _run("sudo systemctl stop stargz-snapshotter")
        _run("grep 'containerd-stargz-grpc/snapshotter/snapshots' /proc/mounts | awk '{print $2}' | xargs -r sudo umount -l")
        # Use sudo bash -c so glob expands as root (STARGZ_ROOT is not readable by user)
        _run(f"sudo bash -c 'rm -rf {STARGZ_ROOT}/*'")
        _run("sudo nerdctl image rm -f $(sudo nerdctl images -q) 2>/dev/null || true")
        _run("sudo ctr content rm $(sudo ctr content ls -q) 2>/dev/null || true")
        _run("sudo systemctl start stargz-snapshotter")
        _run("sudo systemctl restart containerd")


def _timed_pull(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return time.perf_counter() - start


def _timed_run(cmd: list[str]) -> float:
    start = time.perf_counter()
    subprocess.run(cmd, check=True, capture_output=not log.VERBOSE)
    return time.perf_counter() - start


def _run_cmd(n: int) -> list[str]:
    files = " ".join(f"/chunk{i+1}.bin" for i in range(n))
    return ["sh", "-c", f"cat {files} > /dev/null"]


# ── pull functions ─────────────────────────────────────────────────


def pull_base(source_image: str, cfg, num_splits: int) -> float:
    image = pull_name_base(source_image, cfg, num_splits)
    log.info(f"Pulling base image: {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  base pull ({num_splits} splits): {elapsed:.2f}s")
    return elapsed


def pull_stargz(source_image: str, cfg) -> float:
    image = pull_name_stargz(source_image, cfg)
    log.info(f"Pulling stargz image: {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  stargz pull: {elapsed:.2f}s")
    return elapsed


def pull_2dfs(source_image: str, cfg, num_allotments: int) -> float:
    image = pull_name_2dfs(source_image, cfg, num_allotments)
    log.info(f"Pulling 2dfs ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr", "images", "pull", "--plain-http", image])
    log.result(f"  2dfs pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


def pull_2dfs_stargz(source_image: str, cfg, num_allotments: int) -> float:
    image = pull_name_2dfs_stargz(source_image, cfg, num_allotments)
    log.info(f"Pulling 2dfs-stargz ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  2dfs-stargz pull ({num_allotments} allotments): {elapsed:.2f}s")
    return elapsed


def pull_2dfs_stargz_zstd(source_image: str, cfg, num_allotments: int) -> float:
    image = pull_name_2dfs_stargz_zstd(source_image, cfg, num_allotments)
    log.info(f"Pulling 2dfs-stargz-zstd ({num_allotments} allotments): {image}")
    elapsed = _timed_pull(["sudo", "ctr-remote", "images", "rpull", "--plain-http", image])
    log.result(f"  2dfs-stargz-zstd pull ({num_allotments} allotments): {elapsed:.2f}s")
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


def run_2dfs_stargz_zstd(image: str, n: int) -> float:
    name = _next_container_name("run-2dfs-stargz-zstd")
    log.info(f"Running 2dfs-stargz-zstd container: {name} (reading {n} chunks)")
    elapsed = _timed_run([
        "sudo", "ctr-remote", "run", "--rm", "--snapshotter=stargz",
        image, name, *_run_cmd(n),
    ])
    log.result(f"  2dfs-stargz-zstd run: {elapsed:.2f}s")
    return elapsed


# ── orchestration ──────────────────────────────────────────────────


def _prepare_mode(mode: str, chunk_paths: list[str], base_splits: list[int], source_image: str, cfg) -> None:
    if mode == "base":
        prepare_base(chunk_paths, base_splits, source_image, cfg)
    elif mode == "stargz":
        prepare_stargz(chunk_paths, source_image, cfg)
    elif mode == "2dfs":
        prepare_2dfs(chunk_paths, source_image, cfg)
    elif mode == "2dfs-stargz":
        prepare_2dfs_stargz(chunk_paths, source_image, cfg)
    elif mode == "2dfs-stargz-zstd":
        prepare_2dfs_stargz_zstd(chunk_paths, source_image, cfg)
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _measure_one(mode: str, n: int, source_image: str, cfg) -> tuple[int, float, float]:
    if mode == "base":
        pull_t = pull_base(source_image, cfg, n)
        run_t = run_base(pull_name_base(source_image, cfg, n), n)
    elif mode == "stargz":
        pull_t = pull_stargz(source_image, cfg)
        run_t = run_stargz(pull_name_stargz(source_image, cfg), n)
    elif mode == "2dfs":
        pull_t = pull_2dfs(source_image, cfg, n)
        run_t = run_2dfs(pull_name_2dfs(source_image, cfg, n), n)
    elif mode == "2dfs-stargz":
        pull_t = pull_2dfs_stargz(source_image, cfg, n)
        run_t = run_2dfs_stargz(pull_name_2dfs_stargz(source_image, cfg, n), n)
    elif mode == "2dfs-stargz-zstd":
        pull_t = pull_2dfs_stargz_zstd(source_image, cfg, n)
        run_t = run_2dfs_stargz_zstd(pull_name_2dfs_stargz_zstd(source_image, cfg, n), n)
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return (n, pull_t, run_t)


def measure(
    chunk_paths: list[str], base_splits: list[int], source_image: str, cfg,
) -> dict[str, list[tuple[int, float, float]]]:
    results: dict[str, list[tuple[int, float, float]]] = {m: [] for m in MODES}

    clear_registry(cfg, preserve_base=True)
    for mode in MODES:
        log.info(f"\n=== Preparing mode: {mode} ===")
        prepare_local_registry(source_image, registry(cfg))
        _prepare_mode(mode, chunk_paths, base_splits, source_image, cfg)

        for n in base_splits:
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            log.info(f"\n[{ts}] === {mode}: {n} ===")
            clear_cache(cfg)
            results[mode].append(_measure_one(mode, n, source_image, cfg))

        clear_registry(cfg, preserve_base=True)

    return results


# ── output ─────────────────────────────────────────────────────────


def print_results(results: dict[str, list[tuple[int, float, float]]]) -> None:
    splits = [n for n, _, _ in next(iter(results.values()))]
    col = 18
    header_modes = "  ".join(f"{m:>{col}}" for m in results)
    subheader = "  ".join(f"{'pull/run/total':>{col}}" for _ in results)
    log.result("\n=== Pull + Run Performance Results ===")
    log.result(f"{'splits':>8}  {header_modes}")
    log.result(f"{'':>8}  {subheader}")
    log.result("-" * (10 + (col + 2) * len(results)))
    for i, n in enumerate(splits):
        def fmt(r: list[tuple[int, float, float]], idx: int) -> str:
            _, p, r_t = r[idx]
            return f"{p:.1f}/{r_t:.1f}/{p+r_t:.1f}"
        row = "  ".join(f"{fmt(r, i):>{col}}" for r in results.values())
        log.result(f"{n:>8}  {row}")


def save_csv(
    results: dict[str, list[tuple[int, float, float]]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    splits = [n for n, _, _ in next(iter(results.values()))]
    output_path = os.path.join(RESULTS_DIR, f"{model_slug}_{img_slug}_pull_{len(splits)}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["splits"]
        for mode in results:
            slug = mode.replace("-", "_")
            header += [f"{slug}_pull_s", f"{slug}_run_s", f"{slug}_total_s"]
        writer.writerow(header)
        for i in range(len(splits)):
            def row(r: list[tuple[int, float, float]], idx: int) -> list[str]:
                _, p, r_t = r[idx]
                return [f"{p:.4f}", f"{r_t:.4f}", f"{p+r_t:.4f}"]
            writer.writerow([splits[i], *(v for r in results.values() for v in row(r, i))])
    log.result(f"Results saved to {output_path}")


def plot(
    results: dict[str, list[tuple[int, float, float]]],
    model: str,
    base_image: str,
) -> None:
    splits = [n for n, _, _ in next(iter(results.values()))]
    x = np.arange(len(splits))
    n_modes = len(results)
    width = min(0.8 / n_modes, 0.15)

    fig, ax = plt.subplots(figsize=(max(10, n_modes * 2), 6))

    for i, (mode, mode_results) in enumerate(results.items()):
        color = MODE_COLORS[mode]
        pulls = [p for _, p, _ in mode_results]
        runs = [r for _, _, r in mode_results]
        offset = (i - (n_modes - 1) / 2) * width
        ax.bar(x + offset, pulls, width, color=color, alpha=0.5,
               hatch="//", edgecolor=color, linewidth=0.5)
        ax.bar(x + offset, runs, width, bottom=pulls, color=color,
               edgecolor=color, linewidth=0.5, label=mode)

    ax.set_xlabel("Number of splits pulled")
    ax.set_ylabel("Time (s)")
    ax.set_title("Pull + Run Performance")
    ax.set_xticks(x)
    ax.set_xticklabels(splits)
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    method_handles = [mpatches.Patch(facecolor=MODE_COLORS[m], edgecolor=MODE_COLORS[m], label=m)
                      for m in results]
    pull_patch = mpatches.Patch(facecolor="gray", alpha=0.5, hatch="//",
                                edgecolor="gray", label="pull")
    run_patch = mpatches.Patch(facecolor="gray", edgecolor="gray", label="run")
    ax.legend(handles=method_handles + [pull_patch, run_patch], loc="upper left")

    figure_footer(fig, model, base_image)

    os.makedirs(CHARTS_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_DIR, f"{model_slug}_{img_slug}_pull_{len(splits)}_{ts}.png")
    fig.tight_layout()
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    ensure_buildkit()
    log.info(f"Modes: {MODES}")
    log.info(f"Splits (2dfs/stargz): {NUM_SPLITS}")
    log.info(f"Splits (base): {BASE_SPLITS}")

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        chunk_paths = prepare_chunks(model, NUM_SPLITS)

        results = measure(chunk_paths, BASE_SPLITS, base_image, CFG)

        print_results(results)
        save_csv(results, model, base_image)
        plot(results, model, base_image)


if __name__ == "__main__":
    main()
