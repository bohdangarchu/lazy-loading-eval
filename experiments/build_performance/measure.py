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
    # ("openai-community/gpt2", "docker.io/library/python:3.12-slim"),         # ~0.5GB     ~50 MB
    # ("openai-community/gpt2-medium", "docker.io/tensorflow/tensorflow"),     # ~1.52 GB     ~700 MB
    ("openai-community/gpt2-large", "docker.io/ollama/ollama"),              # ~3.25 GB     ~3.4 GB
    ("openai-community/gpt2-xl", "docker.io/library/python:3.12-slim"),    # ~6.0 GB     ~50 MB
]
MAX_SPLITS = 10
CFG = load_config()
WITH_RESOURCE = True
VERBOSE = True
SLEEP_SECONDS = 5
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
# MODES = ["2dfs-stargz"]

_MODE_COLORS = {
    "2dfs":             "#1f77b4",
    "2dfs-stargz":      "#ff7f0e",
    "2dfs-stargz-zstd": "#9467bd",
    "stargz":           "#2ca02c",
    "base":             "#d62728",
}


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


ResultList = list[tuple[int, BuildResult]]


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


def _run_builds(mode: str, model: str, max_splits: int, cfg, source_image: str) -> ResultList:
    if mode == "2dfs":
        return b2.run(model, max_splits, cfg, source_image)
    elif mode == "2dfs-stargz":
        return b2s.run(model, max_splits, cfg, source_image)
    elif mode == "2dfs-stargz-zstd":
        return b2sz.run(model, max_splits, cfg, source_image)
    elif mode == "stargz":
        return bs.run(model, max_splits, cfg, source_image)
    elif mode == "base":
        return bb.run(model, max_splits, cfg, source_image)
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
) -> dict[str, ResultList]:
    if monitor:
        results: dict[str, ResultList] = {mode: [] for mode in MODES}

        for mode in MODES:
            _clear_cache(mode, cfg)

        for n in range(1, max_splits + 1):
            for i, mode in enumerate(MODES):
                monitor_key = mode.replace("-", "_")
                monitor.set_mode(f"{monitor_key}_splits_{n}")
                log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === {mode}: {n} split(s) ===")
                br = _run_one(mode, model, n, cfg, source_image)
                results[mode].append((n, br))

                if i < len(MODES) - 1 or n < max_splits:
                    monitor.set_mode("idle")
                    log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
                    time.sleep(SLEEP_SECONDS)

        return results

    # Non-monitored path: sequential mode-by-mode execution
    results = {}
    for i, mode in enumerate(MODES):
        log.info(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Running {mode} builds ===")
        results[mode] = _run_builds(mode, model, max_splits, cfg, source_image)
        if i < len(MODES) - 1:
            log.info(f"\nSleeping {SLEEP_SECONDS}s before next mode...")
            time.sleep(SLEEP_SECONDS)

    return results


def save_csv(
    splits: list[int],
    results: dict[str, list[BuildResult]],
    model: str,
    base_image: str,
) -> None:
    os.makedirs(RESULTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = os.path.join(RESULTS_BUILD_DIR, f"{model_slug}_{img_slug}_splits_{len(splits)}_{ts}.csv")

    header = ["splits"]
    for mode in MODES:
        slug = mode.replace("-", "_")
        header.extend([f"{slug}_total_s", f"{slug}_pull_s", f"{slug}_ctx_s", f"{slug}_build_s", f"{slug}_export_s"])

    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        for i, n in enumerate(splits):
            row: list = [n]
            for mode in MODES:
                br = results[mode][i]
                row.extend([f"{br.total_s:.4f}", f"{br.pull_s:.4f}", f"{br.context_transfer_s:.4f}",
                            f"{br.build_s:.4f}", f"{br.export_s:.4f}"])
            writer.writerow(row)
    log.result(f"Results saved to {output_path}")


def plot(
    results: dict[str, ResultList],
    model: str,
    base_image: str,
) -> None:
    splits = [n for n, _ in next(iter(results.values()))]
    os.makedirs(CHARTS_BUILD_DIR, exist_ok=True)
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    # Chart 1: line chart, total
    fig, ax = plt.subplots(figsize=(8, 5))
    for mode, mode_results in results.items():
        ax.plot(splits, [br.total_s for _, br in mode_results], marker="o",
                label=mode, color=_MODE_COLORS[mode])
    ax.set_xlabel("Number of splits")
    ax.set_ylabel("Build time (s)")
    ax.set_title("Build performance (total)")
    ax.set_xticks(splits)
    ax.legend()
    ax.grid(True, linestyle="--", alpha=0.5)
    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")
    fig.tight_layout()
    path1 = os.path.join(CHARTS_BUILD_DIR, f"{model_slug}_{img_slug}_splits_{len(splits)}_{ts}.png")
    fig.savefig(path1, dpi=150)
    plt.close(fig)
    log.result(f"Chart saved to {path1}")

    # Chart 2: stacked bar chart, all stages (total wall clock)
    mode_list = list(results.keys())
    n_modes = len(mode_list)
    n_splits = len(splits)
    bar_width = 0.8 / n_modes
    stage_colors = {"pull": "#4e79a7", "context": "#f28e2b", "build": "#e15759", "export": "#76b7b2"}

    fig, ax = plt.subplots(figsize=(max(8, n_splits * 3), 5))
    for i, (mode, result_list) in enumerate(results.items()):
        for j, (n, br) in enumerate(result_list):
            x = j + i * bar_width
            bottom = 0.0
            for stage, val in [("pull", br.pull_s), ("context", br.context_transfer_s),
                               ("build", br.build_s), ("export", br.export_s)]:
                label = stage if (i == 0 and j == 0) else None
                ax.bar(x, val, bar_width, bottom=bottom, color=stage_colors[stage], label=label,
                       edgecolor="white", linewidth=0.5)
                bottom += val
            # total wall clock line on top
            ax.plot(x + bar_width / 2, br.total_s, marker="_", color="black", markersize=8, markeredgewidth=1.5)

    center_offset = (n_modes - 1) * bar_width / 2
    ax.set_xticks([j + center_offset for j in range(n_splits)])
    ax.set_xticklabels([str(n) for n in splits])
    ax.set_xlabel("Number of splits")
    ax.set_ylabel("Time (s)")
    ax.set_title("Build stages breakdown")

    # Method labels below x-axis
    for j in range(n_splits):
        for i, mode in enumerate(mode_list):
            x = j + i * bar_width + bar_width / 2
            ax.text(x, -0.02, mode, ha="center", va="top", fontsize=6, rotation=45,
                    transform=ax.get_xaxis_transform())

    ax.legend(loc="upper right", fontsize="small")
    ax.grid(True, linestyle="--", alpha=0.5, axis="y")
    fig.text(0.01, 0.01, f"model: {model}\nbase image: {base_image}",
             fontsize=8, verticalalignment="bottom", family="monospace")
    fig.tight_layout()
    fig.subplots_adjust(bottom=0.18)
    path2 = os.path.join(CHARTS_BUILD_DIR, f"{model_slug}_{img_slug}_stages_{len(splits)}_{ts}.png")
    fig.savefig(path2, dpi=150)
    plt.close(fig)
    log.result(f"Chart saved to {path2}")


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
    colors = {mode.replace("-", "_"): _MODE_COLORS[mode] for mode in MODES}
    labels = {mode.replace("-", "_"): mode for mode in MODES}

    # Compute mean CPU and MEM per (base_mode, n_splits), skipping idle
    cpu_by_split: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    mem_by_split: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))

    for _, cpu, mem, mode in samples:
        if mode == "idle":
            continue
        parts = mode.rsplit("_splits_", 1)
        if len(parts) != 2 or not parts[1].isdigit():
            continue
        base = parts[0]
        n = int(parts[1])
        cpu_by_split[n][base].append(cpu)
        mem_by_split[n][base].append(mem)

    split_counts = sorted(cpu_by_split.keys())
    if not split_counts:
        return

    x_labels = [str(n) for n in split_counts]
    x = range(len(split_counts))
    n_modes = len(monitor_keys)
    bar_width = 0.8 / n_modes

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(max(8, len(split_counts) * 2), 8))

    for i, mk in enumerate(monitor_keys):
        cpu_means = []
        mem_means = []
        cpu_stds = []
        mem_stds = []
        for n in split_counts:
            cpu_vals = cpu_by_split[n].get(mk, [])
            mem_vals = mem_by_split[n].get(mk, [])
            cpu_means.append(np.mean(cpu_vals) if cpu_vals else 0)
            mem_means.append(np.mean(mem_vals) if mem_vals else 0)
            cpu_stds.append(np.std(cpu_vals) if cpu_vals else 0)
            mem_stds.append(np.std(mem_vals) if mem_vals else 0)

        offsets = [pos + i * bar_width for pos in x]
        ax_cpu.bar(offsets, cpu_means, bar_width, yerr=cpu_stds, label=labels[mk],
                   color=colors[mk], edgecolor="black", linewidth=0.5, capsize=3)
        ax_mem.bar(offsets, mem_means, bar_width, yerr=mem_stds, label=labels[mk],
                   color=colors[mk], edgecolor="black", linewidth=0.5, capsize=3)

    center_offset = (n_modes - 1) * bar_width / 2
    ax_cpu.set_xticks([pos + center_offset for pos in x])
    ax_cpu.set_xticklabels(x_labels)
    ax_cpu.set_ylabel("CPU Usage (%)")
    ax_cpu.set_title("Resource usage during builds")
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

        splits = [n for n, _ in next(iter(results.values()))]
        brs = {mode: [br for _, br in mode_results] for mode, mode_results in results.items()}

        log.result("\n=== Comparison ===")
        col = 16
        header_modes = "  ".join(f"{m:>{col}}" for m in results)
        log.result(f"{'splits':>8}  {header_modes}")
        log.result("-" * (10 + (col + 2) * len(results)))
        for i, n in enumerate(splits):
            row = "  ".join(f"{brs[m][i].total_s:>{col}.2f}" for m in results)
            log.result(f"{n:>8}  {row}")

        save_csv(splits, brs, model, base_image)
        plot(results, model, base_image)


if __name__ == "__main__":
    main()
