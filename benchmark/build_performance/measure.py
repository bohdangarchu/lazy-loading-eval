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
from build_performance.paths import (
    build_csv_path, build_charts_dir, build_chart_path,
    resource_csv_path, resource_chart_path,
    resource_cpu_charts_run_dir, resource_ram_charts_run_dir,
)
from shared.config import load_config
from shared.charts import MODE_COLORS, figure_footer, add_run_dots, bar_group_xticks, save_figure, write_csv
from shared.registry import prepare_local_registry, registry, image_slug
from build_performance import build_2dfs as b2
from build_performance import build_2dfs_stargz as b2s
from build_performance import build_2dfs_stargz_zstd as b2sz
from build_performance import build_stargz as bs
from build_performance import build_base as bb
from build_performance.prepare import prepare, clear_chunks

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

EXPERIMENTS = [
    ("openai-community/gpt2",        "docker.io/library/python:3.12-slim", 12),  # ~0.5GB     ~50 MB
    # ("facebook/opt-350m",            "docker.io/tensorflow/tensorflow",    12),  # ~1.4 GB     ~700 MB
    # ("Qwen/Qwen2-1.5B",              "docker.io/ollama/ollama",            12),  # ~3.09 GB     ~3.4 GB
    # ("openlm-research/open_llama_3b", "docker.io/ollama/ollama",           12),  # ~6.0 GB     ~3.4 GB
]
CFG = load_config()
VERBOSE = True
MODES = ["2dfs", "2dfs-stargz", "2dfs-stargz-zstd", "stargz", "base"]
CAPACITIES = [0, 25, 50, 75, 100]


def num_layers_for_capacity(capacity: int, max_allowed_splits: int) -> int:
    if capacity <= 0:
        return 1
    return max(1, max_allowed_splits * capacity // 100)


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


def _run_one(mode: str, n: int, cfg, source_image: str) -> BuildResult:
    if mode == "2dfs":
        return b2.run_one(n, cfg, source_image)
    elif mode == "2dfs-stargz":
        return b2s.run_one(n, cfg, source_image)
    elif mode == "2dfs-stargz-zstd":
        return b2sz.run_one(n, cfg, source_image)
    elif mode == "stargz":
        return bs.run_one(n, cfg)
    elif mode == "base":
        return bb.run_one(n, cfg)
    raise ValueError(f"Unknown mode: {mode}")


def measure_builds(
    model: str, max_allowed_splits: int, source_image: str, cfg=CFG,
    monitor: ResourceMonitor | None = None,
) -> list[dict]:
    results: list[dict] = []

    for run in range(cfg.build_n_runs):
        log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Run {run + 1}/{cfg.build_n_runs} ===")
        for cap in CAPACITIES:
            num_layers = num_layers_for_capacity(cap, max_allowed_splits)
            log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === Preparing capacity={cap}% ({num_layers} layer(s)) ===")
            clear_chunks()
            prepare(model, max_allowed_splits, num_layers, source_image, cfg)
            for i, mode in enumerate(MODES):
                monitor_key = mode.replace("-", "_")
                if monitor:
                    monitor.set_mode(f"{monitor_key}_cap_{cap}_run_{run}")
                log.info(f"\n[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}] === {mode}: capacity={cap}% ({num_layers} layer(s)) ===")
                _clear_cache(mode, cfg)
                br = _run_one(mode, num_layers, cfg, source_image)
                if monitor:
                    monitor.set_mode("idle")
                results.append({
                    "run": run,
                    "capacity": cap,
                    "num_layers": num_layers,
                    "mode": mode,
                    "total_s": br.total_s,
                })

                is_last = (i == len(MODES) - 1) and (cap == CAPACITIES[-1]) and (run == cfg.build_n_runs - 1)
                if not is_last:
                    log.info(f"\nSleeping {cfg.build_cooldown}s before next...")
                    time.sleep(cfg.build_cooldown)

    return results


def save_csv(results: list[dict], model: str, base_image: str) -> None:
    output_path = build_csv_path(SCRIPT_DIR, model, base_image)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fieldnames = ["run", "capacity", "num_layers", "mode", "total_s"]
    rows = [{
        **row,
        "total_s": f"{row['total_s']:.4f}",
    } for row in results]
    write_csv(output_path, fieldnames, rows)


def plot(results: list[dict], model: str, base_image: str, max_allowed_splits: int) -> None:
    capacities = sorted(set(r["capacity"] for r in results))
    os.makedirs(build_charts_dir(SCRIPT_DIR), exist_ok=True)

    fig, ax = plt.subplots(figsize=(max(8, len(capacities) * 2), 5))

    for mode in MODES:
        means = []
        stds = []
        for cap in capacities:
            vals = [r["total_s"] for r in results if r["mode"] == mode and r["capacity"] == cap]
            means.append(float(np.mean(vals)) if vals else float("nan"))
            stds.append(float(np.std(vals, ddof=0)) if vals else 0.0)
        ax.errorbar(capacities, means, yerr=stds, label=mode, color=MODE_COLORS[mode],
                    marker="o", capsize=3, linewidth=1.5)

    ax.set_xticks(capacities)
    ax.set_xlabel("Split capacity (%)")
    ax.set_ylabel("Total build time (s)")
    ax.set_title(f"Build performance (mean ± std, n={CFG.build_n_runs} runs)")
    ax.legend(loc="best", fontsize="small")
    ax.grid(True, linestyle="--", alpha=0.5)
    figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)
    fig.tight_layout()
    path = build_chart_path(SCRIPT_DIR, model, base_image)
    save_figure(fig, path)


def save_resource_csv(
    samples: list[tuple[int, float, float, str]], model: str, base_image: str,
) -> None:
    output_path = resource_csv_path(SCRIPT_DIR, model, base_image)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["timestamp_ms", "cpu_percent", "mem_mb", "mode"])
        for row in samples:
            writer.writerow(row)
    log.result(f"Resource CSV saved to {output_path}")


def plot_resource(
    samples: list[tuple[int, float, float, str]], model: str, base_image: str,
    max_allowed_splits: int,
) -> None:
    if not samples:
        return

    monitor_keys = [mode.replace("-", "_") for mode in MODES]
    colors = {mode.replace("-", "_"): MODE_COLORS[mode] for mode in MODES}
    labels = {mode.replace("-", "_"): mode for mode in MODES}

    cpu_by_cap_run: dict[int, dict[str, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    mem_by_cap_run: dict[int, dict[str, dict[int, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for _, cpu, mem, mode in samples:
        if mode == "idle":
            continue
        # mode format: "{base}_cap_{cap}_run_{run}"
        run_parts = mode.rsplit("_run_", 1)
        if len(run_parts) != 2 or not run_parts[1].isdigit():
            continue
        run = int(run_parts[1])
        cap_parts = run_parts[0].rsplit("_cap_", 1)
        if len(cap_parts) != 2 or not cap_parts[1].isdigit():
            continue
        base = cap_parts[0]
        cap = int(cap_parts[1])
        cpu_by_cap_run[cap][base][run].append(cpu)
        mem_by_cap_run[cap][base][run].append(mem)

    capacities = sorted(cpu_by_cap_run.keys())
    if not capacities:
        return

    x_labels = [f"{c}" for c in capacities]
    x = range(len(capacities))
    n_modes = len(monitor_keys)
    bar_width = 0.8 / n_modes

    fig, (ax_cpu, ax_mem) = plt.subplots(2, 1, figsize=(max(8, len(capacities) * 2), 8))

    for i, mk in enumerate(monitor_keys):
        cpu_run_medians_by_cap = []
        mem_run_medians_by_cap = []
        for cap in capacities:
            run_cpu_medians = [
                float(np.median(vals))
                for vals in cpu_by_cap_run[cap].get(mk, {}).values()
                if vals
            ]
            run_mem_medians = [
                float(np.median(vals))
                for vals in mem_by_cap_run[cap].get(mk, {}).values()
                if vals
            ]
            cpu_run_medians_by_cap.append(run_cpu_medians)
            mem_run_medians_by_cap.append(run_mem_medians)

        offsets = [pos + i * bar_width for pos in x]
        cpu_bar_heights = [float(np.median(v)) if v else 0.0 for v in cpu_run_medians_by_cap]
        mem_bar_heights = [float(np.median(v)) if v else 0.0 for v in mem_run_medians_by_cap]

        ax_cpu.bar(offsets, cpu_bar_heights, bar_width, label=labels[mk],
                   color=colors[mk], edgecolor="black", linewidth=0.5)
        ax_mem.bar(offsets, mem_bar_heights, bar_width, label=labels[mk],
                   color=colors[mk], edgecolor="black", linewidth=0.5)

        for off, run_vals_cpu, run_vals_mem in zip(offsets, cpu_run_medians_by_cap, mem_run_medians_by_cap):
            x_center = off + bar_width / 2
            add_run_dots(ax_cpu, x_center, run_vals_cpu)
            add_run_dots(ax_mem, x_center, run_vals_mem)

    bar_group_xticks(ax_cpu, len(capacities), n_modes, bar_width, x_labels)
    ax_cpu.set_ylabel("CPU Usage (%)")
    ax_cpu.set_title(f"Resource usage during builds (median, n={CFG.build_n_runs} runs, dots = individual runs)")
    ax_cpu.legend(fontsize="small")
    ax_cpu.grid(True, linestyle="--", alpha=0.5, axis="y")

    bar_group_xticks(ax_mem, len(capacities), n_modes, bar_width, x_labels)
    ax_mem.set_xlabel("Split capacity (%)")
    ax_mem.set_ylabel("Memory Usage (MB)")
    ax_mem.legend(fontsize="small")
    ax_mem.grid(True, linestyle="--", alpha=0.5, axis="y")

    figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)

    output_path = resource_chart_path(SCRIPT_DIR, model, base_image)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    fig.tight_layout()
    save_figure(fig, output_path)


def plot_resource_individual(
    samples: list[tuple[int, float, float, str]], model: str, base_image: str,
    execution_ts: str, max_allowed_splits: int,
) -> None:
    if not samples:
        return

    series: dict[tuple[str, int, int], list[tuple[int, float, float]]] = defaultdict(list)

    for ts_ms, cpu, mem, mode in samples:
        if mode == "idle":
            continue
        run_parts = mode.rsplit("_run_", 1)
        if len(run_parts) != 2 or not run_parts[1].isdigit():
            continue
        run = int(run_parts[1])
        cap_parts = run_parts[0].rsplit("_cap_", 1)
        if len(cap_parts) != 2 or not cap_parts[1].isdigit():
            continue
        base = cap_parts[0]
        cap = int(cap_parts[1])
        series[(base, cap, run)].append((ts_ms, cpu, mem))

    mode_label = {mode.replace("-", "_"): mode for mode in MODES}
    model_slug = model.replace("/", "--")
    img_slug = image_slug(base_image)
    cpu_dir = resource_cpu_charts_run_dir(SCRIPT_DIR, execution_ts)
    ram_dir = resource_ram_charts_run_dir(SCRIPT_DIR, execution_ts)
    os.makedirs(cpu_dir, exist_ok=True)
    os.makedirs(ram_dir, exist_ok=True)

    for (mk, cap, run), points in sorted(series.items()):
        points.sort(key=lambda p: p[0])
        t0 = points[0][0]
        t_sec = [(p[0] - t0) / 1000.0 for p in points]
        cpu_vals = [p[1] for p in points]
        mem_vals = [p[2] for p in points]

        mode_name = mode_label.get(mk, mk)
        file_stem = f"{model_slug}_{img_slug}_{mk}_run{run + 1}_cap{cap}"

        def _add_run_footer(fig) -> None:
            figure_footer(fig, model, base_image, max_allowed_splits=max_allowed_splits)
            fig.text(
                0.99, 0.01,
                f"mode: {mode_name}  |  run: {run + 1}  |  capacity: {cap}%",
                fontsize=8,
                verticalalignment="bottom",
                horizontalalignment="right",
                family="monospace",
            )

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(t_sec, cpu_vals, color=MODE_COLORS.get(mode_name, "#888888"), linewidth=1)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("CPU (%)")
        ax.set_title("CPU usage over time")
        ax.grid(True, linestyle="--", alpha=0.5)
        _add_run_footer(fig)
        fig.tight_layout()
        fig.subplots_adjust(bottom=0.18)
        save_figure(fig, os.path.join(cpu_dir, f"{file_stem}.png"))

        fig, ax = plt.subplots(figsize=(8, 3))
        ax.plot(t_sec, mem_vals, color=MODE_COLORS.get(mode_name, "#888888"), linewidth=1)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Memory (MB)")
        ax.set_title("RAM usage over time")
        ax.grid(True, linestyle="--", alpha=0.5)
        _add_run_footer(fig)
        fig.tight_layout()
        fig.subplots_adjust(bottom=0.18)
        save_figure(fig, os.path.join(ram_dir, f"{file_stem}.png"))


def main():
    log.set_verbose(VERBOSE)
    execution_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    for model, base_image, max_allowed_splits in EXPERIMENTS:
        log.result(f"\n===== Experiment: {model} / {base_image} (max_allowed_splits={max_allowed_splits}) =====")
        prepare_local_registry(base_image, registry(CFG))

        monitor = None
        if CFG.build_with_resource:
            monitor = ResourceMonitor()
            monitor.start()

        results = measure_builds(model, max_allowed_splits, base_image, CFG, monitor=monitor)

        if monitor:
            samples = monitor.stop()
            save_resource_csv(samples, model, base_image)
            plot_resource(samples, model, base_image, max_allowed_splits)
            plot_resource_individual(samples, model, base_image, execution_ts, max_allowed_splits)

        capacities = sorted(set(r["capacity"] for r in results))
        log.result("\n=== Comparison (median across runs) ===")
        col = 16
        header_modes = "  ".join(f"{m:>{col}}" for m in MODES)
        log.result(f"{'capacity':>10}  {header_modes}")
        log.result("-" * (12 + (col + 2) * len(MODES)))
        for cap in capacities:
            row_vals = []
            for m in MODES:
                group = [r["total_s"] for r in results if r["mode"] == m and r["capacity"] == cap]
                row_vals.append(f"{np.median(group):>{col}.2f}" if group else f"{'N/A':>{col}}")
            log.result(f"{cap:>9}%  {'  '.join(row_vals)}")

        save_csv(results, model, base_image)
        plot(results, model, base_image, max_allowed_splits)


if __name__ == "__main__":
    main()
