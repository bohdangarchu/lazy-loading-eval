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
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, save_figure
from pull_performance.paths import pull_csv_path, pull_chart_path, pull_artifacts_dir
from shared.config import load_config
from shared.registry import prepare_local_registry, clear_registry, registry, image_slug
from shared.services import ensure_buildkit, clear_stargz_cache
from shared.artifacts import clear_artifacts
from shared.model import cleanup_pull_experiment
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
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim", 12),         # ~0.5GB     ~50 MB
    # ("facebook/opt-350m", "docker.io/tensorflow/tensorflow", 12),                # ~1.4 GB     ~700 MB
    # ("Qwen/Qwen2-1.5B", "docker.io/ollama/ollama", 12),                      # ~3.09 GB     ~3.4 GB
    # ("openlm-research/open_llama_3b", "docker.io/ollama/ollama", 12),    # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
# MODES = ["2dfs-stargz"]
PARTITION_PERCENTS = [25, 50, 75, 100]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── helpers ────────────────────────────────────────────────────────


def _run(cmd: str) -> None:
    subprocess.run(cmd, shell=True, check=True, capture_output=not log.VERBOSE)


def _next_container_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


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


def _prepare_mode(
    mode: str, chunk_paths: list[str], base_splits: list[int],
    source_image: str, cfg, model: str, execution_ts: str,
) -> None:
    def art(n: int | None = None) -> str:
        return pull_artifacts_dir(SCRIPT_DIR, execution_ts, model, source_image, mode, n)
    if mode == "base":
        prepare_base(chunk_paths, base_splits, source_image, cfg, artifacts_dir_fn=art)
    elif mode == "stargz":
        prepare_stargz(chunk_paths, source_image, cfg, artifacts_dir=art())
    elif mode == "2dfs":
        prepare_2dfs(chunk_paths, source_image, cfg, artifacts_dir=art())
    elif mode == "2dfs-stargz":
        prepare_2dfs_stargz(chunk_paths, source_image, cfg, artifacts_dir=art())
    elif mode == "2dfs-stargz-zstd":
        prepare_2dfs_stargz_zstd(chunk_paths, source_image, cfg, artifacts_dir=art())
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


def _splits_for(max_allowed_splits: int) -> list[int]:
    return [max(1, max_allowed_splits * pct // 100) for pct in PARTITION_PERCENTS]


def measure(
    chunk_paths: list[str], max_allowed_splits: int, source_image: str, cfg,
    model: str, execution_ts: str,
) -> dict[str, list[tuple[int, int, float, float]]]:
    # results[mode] = list of (run, pct, pull_t, run_t)
    results: dict[str, list[tuple[int, int, float, float]]] = {m: [] for m in MODES}

    base_splits = _splits_for(max_allowed_splits)

    clear_registry(cfg, preserve_base=True)
    for mode in MODES:
        log.info(f"\n=== Preparing mode: {mode} ===")
        prepare_local_registry(source_image, registry(cfg))
        _prepare_mode(mode, chunk_paths, base_splits, source_image, cfg, model, execution_ts)

        for run in range(CFG.pull_n_runs):
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{CFG.pull_n_runs} ===")
            for pct in PARTITION_PERCENTS:
                n = max(1, max_allowed_splits * pct // 100)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                log.info(f"\n[{ts}] === {mode}: {pct}% ({n} splits) ===")
                clear_stargz_cache()
                _, pull_t, run_t = _measure_one(mode, n, source_image, cfg)
                results[mode].append((run, pct, pull_t, run_t))
                log.info(f"\nSleeping {cfg.pull_cooldown}s before next...")
                time.sleep(cfg.pull_cooldown)

        clear_registry(cfg, preserve_base=True)

    return results


# ── output ─────────────────────────────────────────────────────────


def print_results(results: dict[str, list[tuple[int, int, float, float]]]) -> None:
    pcts = sorted(set(p for entries in results.values() for _, p, _, _ in entries))
    col = 18
    header_modes = "  ".join(f"{m:>{col}}" for m in results)
    subheader = "  ".join(f"{'pull/run/total':>{col}}" for _ in results)
    log.result("\n=== Pull + Run Performance Results (median across runs) ===")
    log.result(f"{'pct':>8}  {header_modes}")
    log.result(f"{'':>8}  {subheader}")
    log.result("-" * (10 + (col + 2) * len(results)))
    for pct in pcts:
        def fmt(entries: list[tuple[int, int, float, float]]) -> str:
            group = [(pull_t, run_t) for _, p_val, pull_t, run_t in entries if p_val == pct]
            if not group:
                return "N/A"
            p = float(np.median([g[0] for g in group]))
            r_t = float(np.median([g[1] for g in group]))
            return f"{p:.1f}/{r_t:.1f}/{p+r_t:.1f}"
        row = "  ".join(f"{fmt(entries):>{col}}" for entries in results.values())
        log.result(f"{pct:>7}%  {row}")


def save_csv(
    results: dict[str, list[tuple[int, int, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    pcts = sorted(set(p for entries in results.values() for _, p, _, _ in entries))
    output_path = pull_csv_path(SCRIPT_DIR, model, base_image, len(pcts), execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        header = ["run", "partition_pct"]
        for mode in results:
            slug = mode.replace("-", "_")
            header += [f"{slug}_pull_s", f"{slug}_run_s", f"{slug}_total_s"]
        writer.writerow(header)
        for run in range(CFG.pull_n_runs):
            for pct in pcts:
                def row_vals(entries: list[tuple[int, int, float, float]]) -> list[str]:
                    match = [(pull_t, run_t) for r, p_val, pull_t, run_t in entries if r == run and p_val == pct]
                    if not match:
                        return ["", "", ""]
                    p, r_t = match[0]
                    return [f"{p:.4f}", f"{r_t:.4f}", f"{p+r_t:.4f}"]
                writer.writerow([run, pct, *(v for entries in results.values() for v in row_vals(entries))])
    log.result(f"Results saved to {output_path}")


def plot(
    results: dict[str, list[tuple[int, int, float, float]]],
    model: str,
    base_image: str,
    execution_ts: str,
) -> None:
    pcts = sorted(set(p for entries in results.values() for _, p, _, _ in entries))
    x = np.arange(len(pcts))
    n_modes = len(results)
    width = min(0.8 / n_modes, 0.15)

    fig, ax = plt.subplots(figsize=(max(10, n_modes * 2), 6))

    for i, (mode, entries) in enumerate(results.items()):
        color = MODE_COLORS[mode]
        offset = (i - (n_modes - 1) / 2) * width
        med_pulls = []
        med_runs = []
        for j, pct in enumerate(pcts):
            group = [(pull_t, run_t) for _, p_val, pull_t, run_t in entries if p_val == pct]
            med_p = float(np.median([g[0] for g in group])) if group else 0.0
            med_r = float(np.median([g[1] for g in group])) if group else 0.0
            med_pulls.append(med_p)
            med_runs.append(med_r)
            x_center = x[j] + offset + width / 2
            add_run_dots(ax, x_center, [g[0] + g[1] for g in group])
        ax.bar(x + offset, med_pulls, width, color=color, alpha=0.5,
               hatch="//", edgecolor=color, linewidth=0.5)
        ax.bar(x + offset, med_runs, width, bottom=med_pulls, color=color,
               edgecolor=color, linewidth=0.5, label=mode)

    ax.set_xlabel("Partition size (%)")
    ax.set_ylabel("Time (s)")
    ax.set_title(f"Pull + Run Performance (median, n={CFG.pull_n_runs} runs, dots = individual runs)")
    ax.set_xticks(x)
    ax.set_xticklabels([f"{p}%" for p in pcts])
    ax.grid(True, linestyle="--", alpha=0.3, axis="y")

    method_handles = [mpatches.Patch(facecolor=MODE_COLORS[m], edgecolor=MODE_COLORS[m], label=m)
                      for m in results]
    pull_patch = mpatches.Patch(facecolor="gray", alpha=0.5, hatch="//",
                                edgecolor="gray", label="pull")
    run_patch = mpatches.Patch(facecolor="gray", edgecolor="gray", label="run")
    ax.legend(handles=method_handles + [pull_patch, run_patch], loc="upper left")

    output_path = pull_chart_path(SCRIPT_DIR, model, base_image, len(pcts), execution_ts)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout()
    figure_footer(fig, model, base_image)
    save_figure(fig, output_path)


# ── main ───────────────────────────────────────────────────────────


def main():
    log.set_verbose(VERBOSE)
    clear_artifacts(SCRIPT_DIR)
    ensure_buildkit()
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log.info(f"Modes: {MODES}")
    log.info(f"Partition percents: {PARTITION_PERCENTS}")
    log.info(f"Runs: {CFG.pull_n_runs}")

    log.info("Pre-run cleanup...")
    for model, _, _ in EXPERIMENTS:
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    for model, base_image, max_allowed_splits in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} (max_splits={max_allowed_splits}) =====")
        chunk_paths = prepare_chunks(model, max_allowed_splits)

        results = measure(chunk_paths, max_allowed_splits, base_image, CFG, model, execution_ts)

        print_results(results)
        save_csv(results, model, base_image, execution_ts)
        plot(results, model, base_image, execution_ts)
        cleanup_pull_experiment(model, SCRIPT_DIR, CFG)

    clear_artifacts(SCRIPT_DIR)


if __name__ == "__main__":
    main()
