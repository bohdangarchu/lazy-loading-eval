import csv
import os
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone

import matplotlib.pyplot as plt
import numpy as np
import psutil

from shared import log
from shared.build_result import BuildResult
from shared.config import load_config
from shared.mode_colors import MODE_COLORS
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_stargz as bs
from build_performance import build_base as bb

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
RESULTS_DIR = os.path.join(SCRIPT_DIR, "results")
RESULTS_BUILD_DIR = os.path.join(RESULTS_DIR, "build")
RESULTS_RESOURCE_DIR = os.path.join(RESULTS_DIR, "resource")
CHARTS_DIR = os.path.join(SCRIPT_DIR, "charts")
CHARTS_BUILD_DIR = os.path.join(CHARTS_DIR, "build")
CHARTS_RESOURCE_DIR = os.path.join(CHARTS_DIR, "resource")

EXPERIMENTS = [
    ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    # ("openai-community/gpt2-medium", "docker.io/tensorflow/tensorflow"),     # ~1.52 GB     ~700 MB
    # ("openai-community/gpt2-large", "docker.io/ollama/ollama"),              # ~3.25 GB     ~3.4 GB
    # ("openai-community/gpt2-xl", "docker.io/library/python:3.12-slim"),    # ~6.0 GB     ~50 MB
]
MAX_SPLITS = 3
N_RUNS = 3
CFG = load_config()
WITH_RESOURCE = True
VERBOSE = True
SLEEP_SECONDS = 5
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
# MODES = ["2dfs-stargz"]


class ResourceMonitor:
    def __init__(self):
        self._samples: list[tuple[int, float, float, str]] = []  # (timestamp_ms, cpu%, mem_mb, mode)
        self._mode = "idle"
        self._stop = threading.Event()

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._poll, daemon=True)
        self._thread.start()

    def stop(self) -> list[tuple[int, float, float, str]]:
        self._stop.set()
        self._thread.join()
        return self._samples

    def _poll(self) -> None:
        while not self._stop.is_set():
            cpu = psutil.cpu_percent(interval=1)
            mem = psutil.virtual_memory().used / (1024 * 1024)  # MB
            ts = int(time.time() * 1000)
            self._samples.append((ts, cpu, mem, self._mode))


def _clear_cache(mode: str, cfg) -> None:
    if mode == "2dfs":
        b2.clear_cache(cfg)
    elif mode == "2dfs-stargz":
        b2s.clear_cache(cfg)
    elif mode == "2dfs-stargz-zstd":
        b2sz.clear_cache(cfg)
    elif mode == "stargz":
        bs.clear_cache()
    elif mode == "base":
        bb.clear_cache()
    else:
        raise ValueError(f"Unknown mode: {mode}")


def _run_one(mode: str, model: str, n: int, cfg, source_image: str) -> BuildResult:
    if mode == "2dfs":
        return b2.run_one(model, n, cfg, source_image)
    elif mode == "2dfs-stargz":
        return b2s.run_one(model, n, cfg, source_image)
    elif mode == "2dfs-stargz-zstd":
        return b2sz.run_one(model, n, cfg, source_image)
    elif mode == "stargz":
        return bs.run_one(model, n, cfg, source_image)
    elif mode == "base":
        return bb.run_one(model, n, cfg, source_image)
    raise ValueError(f"Unknown mode: {mode}")


def measure_builds(
    model: str, max_splits: int, source_image: str, cfg=CFG,
    monitor: ResourceMonitor | None = None,
) -> list[dict]:
    results: list[dict] = []

    for run in range(N_RUNS):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{N_RUNS} ===")
        for n in range(1, max_splits + 1):
            for i, mode in enumerate(MODES):
                monitor_key = mode.replace("-", "_")
                if monitor:
                    monitor.set_mode(f"{monitor_key}_splits_{n}_run_{run}")
                log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === {mode}: {n} split(s) ===")
                _clear_cache(mode, cfg)
                br = _run_one(mode, model, n, cfg, source_image)
                if monitor:
                    monitor.set_mode("idle")
                results.append({
                    "run": run,
                    "splits": n,
                    "mode": mode,
                    "total_s": br.total_s,
                    "pull_s": br.pull_s,
                    "ctx_s": br.context_transfer_s,
                    "build_s": br.build_s,
                    "export_s": br.export_s,
                })

                is_last = (i == len(MODES) - 1) and (n == max_splits) and (run == N_RUNS - 1)
                if not is_last:
                    log.info(f"\nSleeping {SLEEP_SECONDS}s before next...")
                    time.sleep(SLEEP_SECONDS)

    return results


def save_csv(results: list[dict], model: str, base_image: str) -> None:
    os.makedirs(RESULTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_BUILD_DIR, f"{model_slug}_{img_slug}_{ts}.csv")

    fieldnames = ["run", "splits", "mode", "total_s", "pull_s", "ctx_s", "build_s", "export_s"]
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({
                **row,
                "total_s": f"{row['total_s']:.4f}",
                "pull_s": f"{row['pull_s']:.4f}",
                "ctx_s": f"{row['ctx_s']:.4f}",
                "build_s": f"{row['build_s']:.4f}",
                "export_s": f"{row['export_s']:.4f}",
            })
    log.result(f"Results saved to {output_path}")


def plot(results: list[dict], model: str, base_image: str) -> None:
    splits = sorted(set(r["splits"] for r in results))
    os.makedirs(CHARTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    n_modes = len(MODES)
    n_splits = len(splits)
    bar_width = 0.8 / n_modes
    stage_colors = {"pull": "#4e79a7", "build": "#e15759"}

    fig, ax = plt.subplots(figsize=(max(8, n_splits * 3), 5))

    for i, mode in enumerate(MODES):
        for j, n in enumerate(splits):
            group = [r for r in results if r["mode"] == mode and r["splits"] == n]
            x = j + i * bar_width
            x_center = x + bar_width / 2

            median_total = float(np.median([r["total_s"] for r in group])) if group else 0.0
            median_pull = float(np.median([r["pull_s"] for r in group])) if group else 0.0
            median_build = median_total - median_pull

            ax.bar(x, median_pull, bar_width, bottom=0.0, color=stage_colors["pull"],
                   label="pull" if (i == 0 and j == 0) else None,
                   edgecolor="white", linewidth=0.5)
            ax.bar(x, median_build, bar_width, bottom=median_pull, color=stage_colors["build"],
                   label="build" if (i == 0 and j == 0) else None,
                   edgecolor="white", linewidth=0.5)

            # overlay individual run dots at total_s
            for k, r in enumerate(group):
                jitter = (k - len(group) / 2) * 0.015
                ax.scatter(x_center + jitter, r["total_s"], color="black", s=12, zorder=4)

    center_offset = (n_modes - 1) * bar_width / 2
    ax.set_xticks([j + center_offset for j in range(n_splits)])
    ax.set_xticklabels([str(n) for n in splits])
    ax.set_xlabel("Number of splits")
    ax.set_ylabel("Time (s)")
    ax.set_title(f"Build stages breakdown (median, n={N_RUNS} runs, dots = individual runs)")

    for j in range(n_splits):
        for i, mode in enumerate(MODES):
            x = j + i * bar_width + bar_width / 2
            ax.text(x, -0.02, mode, ha="center", va="top", fontsize=6, rotation=45,
                    transform=ax.get_xaxis_transform())

    ax.legend(loc="upper right", fontsize="small")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.18)
    path = os.path.join(CHARTS_BUILD_DIR, f"{model_slug}_{img_slug}_stages_{n_splits}_{ts}.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    log.result(f"Chart saved to {path}")


def save_resource_csv(
    samples: list[tuple[int, float, float, str]], model: str, max_splits: int,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_RESOURCE_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_RESOURCE_DIR, f"{model_slug}_{img_slug}_resource_splits_{max_splits}_{ts}.csv")
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "cpu_percent", "mem_mb", "mode"])
        for row in samples:
            writer.writerow(row)
    log.result(f"Resource CSV saved to {output_path}")


def plot_resource(
    samples: list[tuple[int, float, float, str]], model: str, max_splits: int,
    base_image: str,
) -> None:
    if not samples:
        return

    monitor_keys = [mode.replace("-", "_") for mode in MODES]
    colors = {mode.replace("-", "_"): MODE_COLORS[mode] for mode in MODES}
    labels = {mode.replace("-", "_"): mode for mode in MODES}

    # Group samples by (base_mode, n_splits, run) → list of cpu/mem values
    cpu_by_split_run: dict[int, dict[str, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    mem_by_split_run: dict[int, dict[str, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for _, cpu, mem, mode in samples:
        if mode == "idle":
            continue
        # mode format: "{base}_splits_{n}_run_{run}"
        run_parts = mode.rsplit("_run_", 1)
        if len(run_parts) != 2 or not run_parts[1].isdigit():
            continue
        run = int(run_parts[1])
        split_parts = run_parts[0].rsplit("_splits_", 1)
        if len(split_parts) != 2 or not split_parts[1].isdigit():
            continue
        base = split_parts[0]
        n = int(split_parts[1])
        cpu_by_split_run[n][base][run].append(cpu)
        mem_by_split_run[n][base][run].append(mem)

    split_counts = sorted(cpu_by_split_run.keys())
    if not split_counts:
        return

    x_labels = [str(n) for n in split_counts]
    x = range(len(split_counts))
    n_modes = len(monitor_keys)
    bar_width = 0.8 / n_modes

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(max(8, len(split_counts) * 2), 8))

    for i, mk in enumerate(monitor_keys):
        cpu_run_medians_by_split = []
        mem_run_medians_by_split = []
        for n in split_counts:
            run_cpu_medians = [
                float(np.median(vals))
                for vals in cpu_by_split_run[n].get(mk, {}).values()
                if vals
            ]
            run_mem_medians = [
                float(np.median(vals))
                for vals in mem_by_split_run[n].get(mk, {}).values()
                if vals
            ]
            cpu_run_medians_by_split.append(run_cpu_medians)
            mem_run_medians_by_split.append(run_mem_medians)

        offsets = [pos + i * bar_width for pos in x]
        cpu_bar_heights = [float(np.median(v)) if v else 0.0 for v in cpu_run_medians_by_split]
        mem_bar_heights = [float(np.median(v)) if v else 0.0 for v in mem_run_medians_by_split]

        ax_cpu.bar(offsets, cpu_bar_heights, bar_width, label=labels[mk],
                   color=colors[mk], edgecolor="black", linewidth=0.5)
        ax_mem.bar(offsets, mem_bar_heights, bar_width, label=labels[mk],
                   color=colors[mk], edgecolor="black", linewidth=0.5)

        # overlay individual run dots
        for off, run_vals_cpu, run_vals_mem in zip(offsets, cpu_run_medians_by_split, mem_run_medians_by_split):
            x_center = off + bar_width / 2
            for k, val in enumerate(run_vals_cpu):
                jitter = (k - len(run_vals_cpu) / 2) * 0.015
                ax_cpu.scatter(x_center + jitter, val, color="black", s=12, zorder=4)
            for k, val in enumerate(run_vals_mem):
                jitter = (k - len(run_vals_mem) / 2) * 0.015
                ax_mem.scatter(x_center + jitter, val, color="black", s=12, zorder=4)

    center_offset = (n_modes - 1) * bar_width / 2
    ax_cpu.set_xticks([pos + center_offset for pos in x])
    ax_cpu.set_xticklabels(x_labels)
    ax_cpu.set_ylabel("CPU Usage (%)")
    ax_cpu.set_title(f"Resource usage during builds (median, n={N_RUNS} runs, dots = individual runs)")
    ax_cpu.legend(fontsize="small")
    ax_cpu.grid(True, linestyle="--", alpha=0.5, axis="y")

    ax_mem.set_xticks([pos + center_offset for pos in x])
    ax_mem.set_xticklabels(x_labels)
    ax_mem.set_xlabel("Number of splits")
    ax_mem.set_ylabel("Memory Usage (MB)")
    ax_mem.legend(fontsize="small")
    ax_mem.grid(True, linestyle="--", alpha=0.5, axis="y")

    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")

    os.makedirs(CHARTS_RESOURCE_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(CHARTS_RESOURCE_DIR, f"{model_slug}_{img_slug}_resource_splits_{max_splits}_{ts}.png")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
    log.result(f"Resource chart saved to {output_path}")


def main():
    log.set_verbose(VERBOSE)

    for model, base_image in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} =====")
        prepare_local_registry(base_image, registry(CFG))

        monitor = None
        if WITH_RESOURCE:
            monitor = ResourceMonitor()
            monitor.start()

        results = measure_builds(model, MAX_SPLITS, base_image, CFG, monitor=monitor)

        if monitor:
            samples = monitor.stop()
            save_resource_csv(samples, model, MAX_SPLITS, base_image)
            plot_resource(samples, model, MAX_SPLITS, base_image)

        splits = sorted(set(r["splits"] for r in results))
        log.result("\n=== Comparison (median across runs) ===")
        col = 16
        header_modes = "  ".join(f"{m:>{col}}" for m in MODES)
        log.result(f"{'splits':>8}  {header_modes}")
        log.result("-" * (10 + (col + 2) * len(MODES)))
        for n in splits:
            row_vals = []
            for m in MODES:
                group = [r["total_s"] for r in results if r["mode"] == m and r["splits"] == n]
                row_vals.append(f"{np.median(group):>{col}.2f}" if group else f"{'N/A':>{col}}")
            log.result(f"{n:>8}  {'  '.join(row_vals)}")

        save_csv(results, model, base_image)
        plot(results, model, base_image)


if __name__ == "__main__":
    main()
